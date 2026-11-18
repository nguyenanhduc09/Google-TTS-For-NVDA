"""Security tests for the updater module.

Covers:
  1. SHA256 hash validation rejects malformed hashes.
  2. Size validation rejects zero/negative/oversized values.
  3. Path traversal prevention in file names.
  4. HTTPS-only enforcement for download URLs.
  5. Manifest parsing rejects invalid input.
  6. Update size limits are enforced.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.test_support import ROOT, FakeHttpResponse, load_module_from_path

# Load updater module directly to avoid NVDA dependency chain.
_UPDATER_PATH = ROOT / "googleTtsForNvda" / "globalPlugins" / "googleTtsForNvda" / "updater.py"
_updater = load_module_from_path("test_updater_security._updater", _UPDATER_PATH)


def _make_update_info(**kwargs: object) -> _updater.UpdateInfo:
    defaults: dict[str, object] = {
        "version": "1.0",
        "url": "https://example.com/update.nvda-addon",
        "size": 1024,
        "sha256": "a" * 64,
        "minimumNVDAVersion": "2024.1",
        "lastTestedNVDAVersion": "2026.2",
        "releaseNotes": "Test update",
    }
    defaults.update(kwargs)
    return _updater.UpdateInfo(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Test 1: SHA256 hash validation
# ---------------------------------------------------------------------------


class Sha256ValidationTests(unittest.TestCase):
    """Verify _sha256() rejects malformed hashes."""

    def _make_manifest(self, sha256: str = "a" * 64) -> dict:
        return {"sha256": sha256}

    def test_valid_lowercase_sha256(self) -> None:
        hash_val = "a" * 64
        result = _updater._sha256(self._make_manifest(sha256=hash_val))
        self.assertEqual(hash_val, result)

    def test_valid_uppercase_sha256_is_normalized(self) -> None:
        hash_val = "A" * 64
        result = _updater._sha256(self._make_manifest(sha256=hash_val))
        self.assertEqual("a" * 64, result)

    def test_sha256_prefix_is_stripped(self) -> None:
        hash_val = "a" * 64
        result = _updater._sha256(self._make_manifest(sha256=f"sha256:{hash_val}"))
        self.assertEqual(hash_val, result)

    def test_rejects_too_short_hash(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._sha256(self._make_manifest(sha256="a" * 32))

    def test_rejects_too_long_hash(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._sha256(self._make_manifest(sha256="a" * 128))

    def test_rejects_non_hex_characters(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._sha256(self._make_manifest(sha256="g" * 64))

    def test_rejects_empty_string(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._sha256(self._make_manifest(sha256=""))

    def test_rejects_missing_sha256(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._sha256({})


# ---------------------------------------------------------------------------
# Test 2: Size validation
# ---------------------------------------------------------------------------


class SizeValidationTests(unittest.TestCase):
    """Verify _required_size() rejects invalid sizes."""

    def test_valid_positive_size(self) -> None:
        result = _updater._required_size({"size": 1024})
        self.assertEqual(1024, result)

    def test_rejects_zero_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({"size": 0})

    def test_rejects_negative_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({"size": -1})

    def test_rejects_none_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({"size": None})

    def test_rejects_string_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({"size": "abc"})

    def test_rejects_boolean_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({"size": True})

    def test_rejects_missing_size(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_size({})

    def test_large_size_is_valid(self) -> None:
        result = _updater._required_size({"size": 512 * 1024 * 1024})
        self.assertEqual(512 * 1024 * 1024, result)


# ---------------------------------------------------------------------------
# Test 3: Path traversal prevention
# ---------------------------------------------------------------------------


class PathTraversalTests(unittest.TestCase):
    """Verify _safe_update_file_name() prevents path traversal."""

    def test_normal_filename_is_accepted(self) -> None:
        result = _updater._safe_update_file_name("googleTtsForNvda-1.0.nvda-addon")
        self.assertEqual("googleTtsForNvda-1.0.nvda-addon", result)

    def test_rejects_forward_slash(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("../evil.nvda-addon")

    def test_rejects_backslash(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("..\\evil.nvda-addon")

    def test_rejects_dot(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name(".")

    def test_rejects_dotdot(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("..")

    def test_rejects_empty_filename(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("")

    def test_rejects_whitespace_only(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("   ")

    def test_rejects_non_addon_extension(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("evil.exe")

    def test_rejects_addon_extension_with_slash(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._safe_update_file_name("sub/evil.nvda-addon")

    def test_rejects_colon_control_chars_trailing_dot_and_reserved_device_names(self) -> None:
        for bad_name in (
            "C:evil.nvda-addon",
            "evil:stream.nvda-addon",
            "evil\x00.nvda-addon",
            "evil .nvda-addon",
            "evil..nvda-addon",
            ".nvda-addon",
            "CON.nvda-addon",
            "nul.1.nvda-addon",
            "COM1.nvda-addon",
            "LPT9.nvda-addon",
        ):
            with self.subTest(bad_name=bad_name), self.assertRaises(_updater.UpdateError):
                _updater._safe_update_file_name(bad_name)


# ---------------------------------------------------------------------------
# Test 4: HTTPS-only enforcement
# ---------------------------------------------------------------------------


class HttpsOnlyTests(unittest.TestCase):
    """Verify download_update() and redirect handling enforce HTTPS."""

    def test_https_url_passes_validation_and_downloads(self) -> None:
        payload = b"valid-addon-archive-bytes"
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        update = _make_update_info(
            url="https://example.com/update.nvda-addon",
            size=len(payload),
            sha256=expected_sha256,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir)
            with (
                mock.patch.object(_updater, "_update_download_dir", return_value=dest_dir),
                mock.patch.object(
                    _updater.urllib.request,
                    "urlopen",
                    return_value=FakeHttpResponse(payload),
                ) as urlopen_mock,
            ):
                downloaded = _updater.download_update(update)
            self.assertTrue(urlopen_mock.called)
            self.assertEqual("https://example.com/update.nvda-addon", urlopen_mock.call_args.args[0].full_url)
            self.assertTrue(downloaded.path.is_file())
            self.assertEqual(payload, downloaded.path.read_bytes())

    def test_non_https_urls_are_rejected(self) -> None:
        for url in (
            "http://example.com/update.nvda-addon",
            "ftp://example.com/update.nvda-addon",
            "data:text/html,<script>alert(1)</script>",
        ):
            with self.subTest(url=url):
                update = _make_update_info(url=url)
                with self.assertRaises(_updater.UpdateError):
                    _updater.download_update(update)

    def test_https_redirect_handler_blocks_downgrade_to_http(self) -> None:
        handler = _updater._HttpsOnlyRedirectHandler()
        req = urllib.request.Request("https://example.com/update.nvda-addon")
        with self.assertRaises(_updater.UpdateError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},  # type: ignore[arg-type]
                "http://example.com/downgrade.nvda-addon",
            )


# ---------------------------------------------------------------------------
# Test 5: Manifest parsing
# ---------------------------------------------------------------------------


class ManifestParsingTests(unittest.TestCase):
    """Verify _parse_update_info() rejects invalid manifest input."""

    def _valid_manifest(self) -> dict:
        return {
            "addonId": "googleTtsForNvda",
            "version": "2.0",
            "url": "https://example.com/update.nvda-addon",
            "size": 1024,
            "sha256": "a" * 64,
            "minimumNVDAVersion": "2024.1",
            "lastTestedNVDAVersion": "2026.2",
            "releaseNotes": "Test",
        }

    def test_valid_manifest_parses(self) -> None:
        info = _updater._parse_update_info(self._valid_manifest(), "en")
        self.assertEqual("2.0", info.version)
        self.assertEqual("a" * 64, info.sha256)

    def test_rejects_wrong_addon_id(self) -> None:
        manifest = self._valid_manifest()
        manifest["addonId"] = "evil-addon"
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_rejects_non_stable_channel(self) -> None:
        manifest = self._valid_manifest()
        manifest["channel"] = "beta"
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_rejects_missing_version(self) -> None:
        manifest = self._valid_manifest()
        del manifest["version"]
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_rejects_missing_url(self) -> None:
        manifest = self._valid_manifest()
        del manifest["url"]
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_rejects_missing_sha256(self) -> None:
        manifest = self._valid_manifest()
        del manifest["sha256"]
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_rejects_missing_size(self) -> None:
        manifest = self._valid_manifest()
        del manifest["size"]
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")

    def test_negative_update_build_is_rejected(self) -> None:
        manifest = self._valid_manifest()
        manifest["updateBuild"] = -1
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info(manifest, "en")


# ---------------------------------------------------------------------------
# Test 6: Update size limits
# ---------------------------------------------------------------------------


class UpdateSizeLimitTests(unittest.TestCase):
    """Verify MAX_UPDATE_PACKAGE_BYTES is enforced."""

    def test_max_package_size_constant(self) -> None:
        self.assertEqual(512 * 1024 * 1024, _updater.MAX_UPDATE_PACKAGE_BYTES)

    def test_max_manifest_size_constant(self) -> None:
        self.assertEqual(256 * 1024, _updater.MAX_UPDATE_MANIFEST_BYTES)

    def test_oversized_package_is_rejected(self) -> None:
        update = _make_update_info(size=_updater.MAX_UPDATE_PACKAGE_BYTES + 1)
        with self.assertRaises(_updater.UpdateError) as ctx:
            _updater.download_update(update)
        self.assertIn("too large", str(ctx.exception))


# ---------------------------------------------------------------------------
# Test 7: Version comparison
# ---------------------------------------------------------------------------


class VersionComparisonTests(unittest.TestCase):
    """Verify _is_newer_version() correctly compares versions."""

    def test_newer_version_detected(self) -> None:
        self.assertTrue(_updater._is_newer_version("2.0", "1.0"))

    def test_same_version_not_newer(self) -> None:
        self.assertFalse(_updater._is_newer_version("1.0", "1.0"))

    def test_older_version_not_newer(self) -> None:
        self.assertFalse(_updater._is_newer_version("1.0", "2.0"))

    def test_three_part_version(self) -> None:
        self.assertTrue(_updater._is_newer_version("1.0.1", "1.0.0"))

    def test_patch_level_difference(self) -> None:
        self.assertTrue(_updater._is_newer_version("1.0.2", "1.0.1"))

    def test_major_version_difference(self) -> None:
        self.assertTrue(_updater._is_newer_version("2.0.0", "1.9.9"))


# ---------------------------------------------------------------------------
# Test 8: Manifest value helpers
# ---------------------------------------------------------------------------


class StripManifestValueTests(unittest.TestCase):
    """Verify _strip_manifest_value removes surrounding quotes."""

    def test_double_quoted(self) -> None:
        self.assertEqual("hello", _updater._strip_manifest_value('"hello"'))

    def test_single_quoted(self) -> None:
        self.assertEqual("hello", _updater._strip_manifest_value("'hello'"))

    def test_no_quotes(self) -> None:
        self.assertEqual("hello", _updater._strip_manifest_value("hello"))

    def test_whitespace_trimmed(self) -> None:
        self.assertEqual("hello", _updater._strip_manifest_value("  hello  "))

    def test_empty_string(self) -> None:
        self.assertEqual("", _updater._strip_manifest_value(""))


# ---------------------------------------------------------------------------
# Test 9: Version parts
# ---------------------------------------------------------------------------


class VersionPartsTests(unittest.TestCase):
    """Verify _version_parts extracts numeric components."""

    def test_simple_version(self) -> None:
        self.assertEqual((1, 0), _updater._version_parts("1.0"))

    def test_three_part_version(self) -> None:
        self.assertEqual((1, 2, 3), _updater._version_parts("1.2.3"))

    def test_version_with_non_numeric(self) -> None:
        self.assertEqual((1, 0), _updater._version_parts("1.0-beta"))

    def test_no_numeric_parts_raises(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._version_parts("abc")


# ---------------------------------------------------------------------------
# Test 10: Update availability
# ---------------------------------------------------------------------------


class UpdateAvailabilityTests(unittest.TestCase):
    """Verify _is_update_available compares versions and build numbers."""

    def test_newer_version_is_available(self) -> None:
        update = _make_update_info(version="2.0")
        self.assertTrue(_updater._is_update_available(update, "1.0", 0))

    def test_same_version_no_update(self) -> None:
        update = _make_update_info(version="1.0")
        self.assertFalse(_updater._is_update_available(update, "1.0", 0))

    def test_same_version_higher_build_is_available(self) -> None:
        update = _make_update_info(version="1.0", updateBuild=5)
        self.assertTrue(_updater._is_update_available(update, "1.0", 3))

    def test_same_version_same_build_no_update(self) -> None:
        update = _make_update_info(version="1.0", updateBuild=3)
        self.assertFalse(_updater._is_update_available(update, "1.0", 3))

    def test_older_version_no_update(self) -> None:
        update = _make_update_info(version="1.0")
        self.assertFalse(_updater._is_update_available(update, "2.0", 0))


# ---------------------------------------------------------------------------
# Test 11: Required / optional string helpers
# ---------------------------------------------------------------------------


class RequiredStringTests(unittest.TestCase):
    """Verify _required_string rejects missing or empty values."""

    def test_valid_string(self) -> None:
        self.assertEqual("hello", _updater._required_string({"key": "hello"}, "key"))

    def test_missing_key_raises(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_string({}, "key")

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_string({"key": ""}, "key")

    def test_non_string_raises(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._required_string({"key": 123}, "key")


class OptionalStringTests(unittest.TestCase):
    """Verify _optional_string returns empty for None, value for str."""

    def test_present_string(self) -> None:
        self.assertEqual("hello", _updater._optional_string({"key": "hello"}, "key"))

    def test_missing_key_returns_empty(self) -> None:
        self.assertEqual("", _updater._optional_string({}, "key"))

    def test_none_returns_empty(self) -> None:
        self.assertEqual("", _updater._optional_string({"key": None}, "key"))

    def test_non_string_raises(self) -> None:
        with self.assertRaises(_updater.UpdateError):
            _updater._optional_string({"key": 123}, "key")


# ---------------------------------------------------------------------------
# Test 12: Locale key and release notes
# ---------------------------------------------------------------------------


class LocaleKeyTests(unittest.TestCase):
    """Verify _locale_key normalizes locale strings."""

    def test_dash_to_underscore(self) -> None:
        self.assertEqual("en_US", _updater._locale_key("en-US"))

    def test_none_returns_empty(self) -> None:
        self.assertEqual("", _updater._locale_key(None))

    def test_whitespace_trimmed(self) -> None:
        self.assertEqual("fr_FR", _updater._locale_key("  fr-FR  "))


class ReleaseNotesTests(unittest.TestCase):
    """Verify _release_notes selects locale-specific or fallback notes."""

    def test_locale_specific_notes(self) -> None:
        data = {"releaseNotesByLocale": {"uk_UA": "Нотатки"}, "releaseNotes": "English"}
        self.assertEqual("Нотатки", _updater._release_notes(data, "uk-UA"))

    def test_fallback_to_default(self) -> None:
        data = {"releaseNotesByLocale": {}, "releaseNotes": "English"}
        self.assertEqual("English", _updater._release_notes(data, "uk-UA"))

    def test_no_release_notes(self) -> None:
        self.assertEqual("", _updater._release_notes({}, None))


# ---------------------------------------------------------------------------
# Test 13: Update file name
# ---------------------------------------------------------------------------


class UpdateFileNameTests(unittest.TestCase):
    """Verify _update_file_name constructs and validates file names."""

    def test_custom_file_name(self) -> None:
        data = {"fileName": "custom-1.0.nvda-addon"}
        result = _updater._update_file_name(data, "1.0")
        self.assertEqual("custom-1.0.nvda-addon", result)

    def test_default_file_name(self) -> None:
        result = _updater._update_file_name({}, "2.0")
        self.assertEqual("googleTtsForNvda-2.0.nvda-addon", result)

    def test_rejects_non_addon_extension(self) -> None:
        data = {"fileName": "evil.exe"}
        with self.assertRaises(_updater.UpdateError):
            _updater._update_file_name(data, "1.0")

    def test_missing_path_is_not_classified_as_reparse_point(self) -> None:
        missing = Path(tempfile.gettempdir()) / "nonexistent-googleTts-reparse-probe-xyz"
        self.assertFalse(missing.exists())
        self.assertFalse(_updater._is_reparse_or_symlink(missing))

    def test_parse_update_info_rejects_empty_channel_and_non_https_url(self) -> None:
        base = {
            "version": "1.2",
            "url": "https://example.com/googleTtsForNvda-1.2.nvda-addon",
            "size": 1024,
            "sha256": "a" * 64,
            "minimumNVDAVersion": "2024.1",
            "lastTestedNVDAVersion": "2026.2",
        }
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info({**base, "channel": ""}, "en")
        with self.assertRaises(_updater.UpdateError):
            _updater._parse_update_info({**base, "url": "http://example.com/googleTtsForNvda-1.2.nvda-addon"}, "en")


if __name__ == "__main__":
    unittest.main()

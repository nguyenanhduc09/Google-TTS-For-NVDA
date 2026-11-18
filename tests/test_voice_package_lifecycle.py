"""Integration tests for the voice package lifecycle.

Tests the complete flow: catalog loading → package verification →
download → install → verify installed → remove → verify removed.

Since these tests run without NVDA, they use the pure driver modules
and a temporary voice directory.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.test_support import FakeHttpResponse, load_driver_module

voice_store = load_driver_module("voice_store")
catalog_module = load_driver_module("catalog")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_fake_zvoice(path: Path, content: bytes = b"fake zvoice data") -> None:
    """Create a fake .zvoice file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256_of_bytes(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _isolated_voice_store() -> Iterator[Path]:
    """Yield a temporary voices directory with isolated data_root and verification caches."""
    with tempfile.TemporaryDirectory(prefix="google_tts_test_") as td:
        data_dir = Path(td)
        voices_dir = data_dir / "voices"
        voices_dir.mkdir()
        with (
            patch.object(voice_store, "voice_dir", return_value=voices_dir),
            patch.object(voice_store, "data_root", return_value=data_dir),
            patch.dict(voice_store._verifiedPackageCache, {}, clear=True),
            patch.object(voice_store, "_persistentVerifiedPackageCache", None),
        ):
            yield voices_dir


class _FakePackage:
    """Minimal stand-in for VoicePackage."""

    def __init__(
        self,
        package_id: str = "en-us-x-multi",
        file_name: str | None = None,
        sha256: str = "",
        compressed_size: int = 0,
        url: str = "https://example.com/fake.zvoice",
    ) -> None:
        self.id = package_id
        self.fileName = file_name or f"{package_id}.zvoice"
        self.sha256Checksum = sha256
        self.compressedSize = compressed_size
        self.url = url
        self.dependentVoiceId = ""


def _make_package_with_file(
    target_dir: Path,
    content: bytes,
    *,
    file_name: str | None = None,
    sha256: str | None = None,
    compressed_size: int | None = None,
) -> tuple[_FakePackage, Path]:
    """Create a _FakePackage and write a matching payload file in *target_dir*."""
    pkg = _FakePackage(
        sha256=_sha256_of_bytes(content) if sha256 is None else sha256,
        compressed_size=len(content) if compressed_size is None else compressed_size,
    )
    target = target_dir / (file_name or pkg.fileName)
    _create_fake_zvoice(target, content)
    return pkg, target


# ---------------------------------------------------------------------------
# Test 1: Catalog loading
# ---------------------------------------------------------------------------


class CatalogLoadingTests(unittest.TestCase):
    """Verify VoiceCatalog loads correctly from JSON."""

    @staticmethod
    def _load_temp_catalog(catalog_data: list[dict[str, Any]]) -> Any:
        with tempfile.TemporaryDirectory() as td:
            catalog_path = Path(td) / "voices.json"
            catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")
            return catalog_module.VoiceCatalog.load(catalog_path)

    def test_load_catalog_from_json(self) -> None:
        """Load a catalog from a temporary JSON file."""
        catalog_data = [
            {
                "id": "en-us-x-multi",
                "fileId": "en-us-x-multi-r46",
                "url": "https://example.com/en-us-x-multi-r46.zvoice",
                "sha256Checksum": "a" * 64,
                "compressedSize": 1024,
                "remote": True,
                "speakers": [
                    {"speaker": "ena", "name": "English 1", "gender": "female"},
                    {"speaker": "enb", "name": "English 2", "gender": "male"},
                ],
            },
            {
                "id": "vi-vn-x-multi",
                "fileId": "vi-vn-x-multi-r46",
                "url": "https://example.com/vi-vn-x-multi-r46.zvoice",
                "sha256Checksum": "b" * 64,
                "compressedSize": 2048,
                "remote": True,
                "speakers": [
                    {"speaker": "vna", "name": "Vietnamese 1", "gender": "female"},
                ],
            },
        ]
        cat = self._load_temp_catalog(catalog_data)
        self.assertEqual(2, len(cat.packages))
        self.assertEqual(3, len(cat.speakers))
        self.assertIn("en-us-x-multi", cat._packageById)
        self.assertIn("vi-vn-x-multi", cat._packageById)

    def test_catalog_sorted_by_language_and_id(self) -> None:
        """Packages are sorted by language then ID."""
        catalog_data = [
            {
                "id": "vi-vn-x-multi",
                "fileId": "f1",
                "url": "",
                "sha256Checksum": "",
                "compressedSize": 0,
                "remote": True,
                "speakers": [{"speaker": "a", "name": "A", "gender": "f"}],
            },
            {
                "id": "en-us-x-multi",
                "fileId": "f2",
                "url": "",
                "sha256Checksum": "",
                "compressedSize": 0,
                "remote": True,
                "speakers": [{"speaker": "b", "name": "B", "gender": "m"}],
            },
        ]
        cat = self._load_temp_catalog(catalog_data)
        self.assertEqual("en-us-x-multi", cat.packages[0].id)
        self.assertEqual("vi-vn-x-multi", cat.packages[1].id)

    def test_package_id_to_language(self) -> None:
        """package_id_to_language extracts language from package ID."""
        self.assertEqual("en-US", catalog_module.package_id_to_language("en-us-x-multi"))
        self.assertEqual("vi-VN", catalog_module.package_id_to_language("vi-vn-x-multi"))
        self.assertEqual("zh-CN", catalog_module.package_id_to_language("zh-cn-x-multi"))

    def test_is_package_supported_by_engine(self) -> None:
        """Unsupported package families (locomel, darwinnrio) are detected while CPU lemonbalm is allowed."""

        def make_pkg(pkgId: str, dependentVoiceId: str = "") -> Any:
            return catalog_module.VoicePackage(
                id=pkgId,
                fileId="f",
                url="",
                sha256Checksum="",
                compressedSize=0,
                remote=True,
                speakers=(),
                dependentVoiceId=dependentVoiceId,
            )

        for supportedId in (
            "en-us-x-multi",
            "en-us-x-news-lemonbalm",
            "en-us-x-blueginger-lemonbalm",
            "fr-fr-x-multi-lemonbalm",
        ):
            with self.subTest(supportedId=supportedId):
                self.assertTrue(catalog_module.is_package_supported_by_engine(make_pkg(supportedId)))

        for unsupportedId in (
            "locomel-en-us",
            "en-gb-x-multi-locomel",
            "de-de-x-news-darwinnrio-lemonbalm",
            "en-us-x-news-darwinnrio-lemonbalm",
        ):
            with self.subTest(unsupportedId=unsupportedId):
                self.assertFalse(catalog_module.is_package_supported_by_engine(make_pkg(unsupportedId)))

    def test_bundled_catalog_lemonbalm_support_and_dependencies(self) -> None:
        """Bundled catalog allows CPU lemonbalm packages with base multi deps and blocks locomel/darwinnrio."""
        catalog = catalog_module.VoiceCatalog.load()
        supportedCpuLemonbalm = [
            pkg
            for pkg in catalog.packages
            if "lemonbalm" in pkg.id and catalog_module.is_package_supported_by_engine(pkg)
        ]
        blocked = [pkg for pkg in catalog.packages if not catalog_module.is_package_supported_by_engine(pkg)]

        self.assertEqual(8, len(supportedCpuLemonbalm))
        self.assertEqual(12, len(blocked))
        for pkg in supportedCpuLemonbalm:
            baseId = f"{pkg.id.split('-x-')[0]}-x-multi"
            self.assertEqual(baseId, pkg.dependentVoiceId)
            basePkg = catalog.package_by_id(pkg.dependentVoiceId)
            self.assertTrue(catalog_module.is_package_supported_by_engine(basePkg))

        usableWithoutBase = voice_store.usable_installed_packages([supportedCpuLemonbalm[0]])
        self.assertEqual([], usableWithoutBase)
        basePkg = catalog.package_by_id(supportedCpuLemonbalm[0].dependentVoiceId)
        usableWithBase = voice_store.usable_installed_packages([basePkg, supportedCpuLemonbalm[0]])
        self.assertEqual({basePkg.id, supportedCpuLemonbalm[0].id}, {pkg.id for pkg in usableWithBase})


# ---------------------------------------------------------------------------
# Test 2: Package verification
# ---------------------------------------------------------------------------


class PackageVerificationTests(unittest.TestCase):
    """Verify package file verification with SHA256."""

    def test_is_package_installed_returns_false_for_missing_file(self) -> None:
        """is_package_installed returns False when file doesn't exist."""
        with _isolated_voice_store():
            pkg = _FakePackage()
            self.assertFalse(voice_store.is_package_installed(pkg))

    def test_is_package_installed_returns_true_for_valid_file(self) -> None:
        """is_package_installed returns True for file with correct size and hash."""
        with _isolated_voice_store() as voices_dir:
            pkg, _ = _make_package_with_file(voices_dir, b"test voice data content")
            self.assertTrue(voice_store.is_package_installed(pkg))

    def test_is_package_installed_returns_false_for_wrong_hash(self) -> None:
        """is_package_installed returns False when SHA256 doesn't match."""
        with _isolated_voice_store() as voices_dir:
            pkg, _ = _make_package_with_file(voices_dir, b"test voice data content", sha256="f" * 64)
            self.assertFalse(voice_store.is_package_installed(pkg))

    def test_is_package_installed_returns_false_for_wrong_size(self) -> None:
        """is_package_installed returns False when file size doesn't match."""
        content = b"test voice data content"
        with _isolated_voice_store() as voices_dir:
            pkg, _ = _make_package_with_file(voices_dir, content, compressed_size=len(content) + 100)
            self.assertFalse(voice_store.is_package_installed(pkg))

    def test_is_package_installed_ignores_hash_when_empty(self) -> None:
        """is_package_installed ignores SHA256 when checksum is empty."""
        with _isolated_voice_store() as voices_dir:
            pkg, _ = _make_package_with_file(voices_dir, b"test voice data content", sha256="")
            self.assertTrue(voice_store.is_package_installed(pkg))


# ---------------------------------------------------------------------------
# Test 3: Package removal
# ---------------------------------------------------------------------------


class PackageRemovalTests(unittest.TestCase):
    """Verify package removal cleans up files and cache."""

    def test_remove_package_deletes_file(self) -> None:
        """remove_package deletes the .zvoice file."""
        with _isolated_voice_store() as voices_dir:
            content = b"test voice data content"
            pkg = _FakePackage(sha256=_sha256_of_bytes(content))
            pkg_path = voices_dir / pkg.fileName
            _create_fake_zvoice(pkg_path, content)
            self.assertTrue(pkg_path.exists())

            voice_store.remove_package(pkg)
            self.assertFalse(pkg_path.exists())

    def test_remove_package_handles_missing_file(self) -> None:
        """remove_package doesn't raise when file is already gone."""
        with _isolated_voice_store():
            pkg = _FakePackage()
            # Should not raise
            voice_store.remove_package(pkg)


# ---------------------------------------------------------------------------
# Test 4: Package copy (import)
# ---------------------------------------------------------------------------


class PackageCopyTests(unittest.TestCase):
    """Verify copy_existing_package for import flow."""

    def test_copy_installs_package(self) -> None:
        """copy_existing_package copies file to voice dir."""
        content = b"imported voice data"
        with _isolated_voice_store() as voices_dir:
            pkg, source = _make_package_with_file(voices_dir, content, file_name="source.zvoice")
            result = voice_store.copy_existing_package(source, pkg)
            self.assertTrue(result.exists())
            self.assertEqual(content, result.read_bytes())

    def test_copy_verifies_size(self) -> None:
        """copy_existing_package rejects wrong file size."""
        content = b"imported voice data"
        with _isolated_voice_store() as voices_dir:
            pkg, source = _make_package_with_file(
                voices_dir,
                content,
                file_name="source.zvoice",
                compressed_size=len(content) + 100,
            )
            with self.assertRaises(RuntimeError):
                voice_store.copy_existing_package(source, pkg)

    def test_copy_verifies_sha256(self) -> None:
        """copy_existing_package rejects wrong SHA256."""
        with _isolated_voice_store() as voices_dir:
            pkg, source = _make_package_with_file(
                voices_dir,
                b"imported voice data",
                file_name="source.zvoice",
                sha256="f" * 64,
            )
            with self.assertRaises(RuntimeError):
                voice_store.copy_existing_package(source, pkg)


# ---------------------------------------------------------------------------
# Test 4b: Package download guards
# ---------------------------------------------------------------------------


class PackageDownloadGuardTests(unittest.TestCase):
    """Verify download_package enforces HTTPS, the size limit, and package integrity."""

    def test_rejects_non_https_download_url(self) -> None:
        """Insecure download links are refused before any network access."""
        with _isolated_voice_store():
            pkg = _FakePackage(url="http://example.com/fake.zvoice")
            with self.assertRaises(RuntimeError) as context:
                voice_store.download_package(pkg)
            self.assertIn("HTTPS", str(context.exception))

    def test_https_redirect_handler_blocks_downgrade_to_http(self) -> None:
        req = voice_store.urllib.request.Request("https://example.com/voice.zvoice")
        handler = voice_store._HttpsOnlyRedirectHandler()
        with self.assertRaises(RuntimeError) as context:
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.com/voice.zvoice")
        self.assertIn("HTTPS", str(context.exception))

        https_req = handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.example.com/voice.zvoice")
        self.assertIsNotNone(https_req)
        self.assertEqual("https://cdn.example.com/voice.zvoice", https_req.full_url)

    def test_rejects_declared_size_over_download_limit(self) -> None:
        """A package whose catalog size exceeds the limit is refused before downloading."""
        with _isolated_voice_store():
            pkg = _FakePackage(compressed_size=voice_store.VOICE_PACKAGE_MAX_BYTES + 1)
            with self.assertRaises(RuntimeError) as context:
                voice_store.download_package(pkg)
            self.assertIn("larger", str(context.exception))

    def test_rejects_response_larger_than_limit(self) -> None:
        """A Content-Length above the limit aborts the download and removes the partial file."""
        with _isolated_voice_store() as voices_dir:
            pkg = _FakePackage()
            response = FakeHttpResponse(b"partial", content_length=voice_store.VOICE_PACKAGE_MAX_BYTES + 1)
            with (
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / pkg.fileName).exists())
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())

    def test_rejects_stream_over_limit_without_content_length(self) -> None:
        """Streamed bytes over the limit are refused even when Content-Length is unusable."""
        with _isolated_voice_store() as voices_dir:
            pkg = _FakePackage()
            response = FakeHttpResponse(b"x" * 64, content_length=0)
            with (
                patch.object(voice_store, "VOICE_PACKAGE_MAX_BYTES", 8),
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())

    def test_download_verifies_and_installs_package(self) -> None:
        """A verified download is installed into the voice directory."""
        with _isolated_voice_store():
            content = b"downloaded voice data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            response = FakeHttpResponse(content)
            with patch.object(voice_store.urllib.request, "urlopen", return_value=response):
                result = voice_store.download_package(pkg)
                self.assertEqual(content, result.read_bytes())
                self.assertTrue(voice_store.is_package_installed(pkg))

    def test_download_rejects_checksum_mismatch(self) -> None:
        """A payload with the right size but the wrong checksum is discarded."""
        with _isolated_voice_store() as voices_dir:
            content = b"tampered voice data"
            pkg = _FakePackage(
                sha256="f" * 64,
                compressed_size=len(content),
            )
            response = FakeHttpResponse(content)
            with (
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / pkg.fileName).exists())
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())


# ---------------------------------------------------------------------------
# Test 5: End-to-end lifecycle
# ---------------------------------------------------------------------------


class VoicePackageLifecycleTests(unittest.TestCase):
    """Test the complete lifecycle: install → verify → remove → verify."""

    def test_full_lifecycle(self) -> None:
        """Install a package, verify it, remove it, verify removal."""
        with _isolated_voice_store() as voices_dir:
            content = b"complete lifecycle test data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            source = voices_dir / "source.zvoice"
            _create_fake_zvoice(source, content)

            # Step 1: Verify not installed
            self.assertFalse(voice_store.is_package_installed(pkg))

            # Step 2: Install via copy
            result = voice_store.copy_existing_package(source, pkg)
            self.assertTrue(result.exists())

            # Step 3: Verify installed
            self.assertTrue(voice_store.is_package_installed(pkg))

            # Step 4: Remove
            voice_store.remove_package(pkg)

            # Step 5: Verify removed
            self.assertFalse(voice_store.is_package_installed(pkg))
            self.assertFalse((voices_dir / pkg.fileName).exists())

    def test_cache_invalidation_after_removal(self) -> None:
        """Verification cache is invalidated after package removal."""
        with _isolated_voice_store() as voices_dir:
            content = b"cache invalidation test"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            # Install and verify
            self.assertTrue(voice_store.is_package_installed(pkg))

            # Remove
            voice_store.remove_package(pkg)

            # Cache should be invalidated — re-creating the file
            # with different content should NOT be detected as installed
            new_content = b"different content"
            _create_fake_zvoice(voices_dir / pkg.fileName, new_content)

            # Should still return False (different content)
            self.assertFalse(voice_store.is_package_installed(pkg))


class CatalogValidationTests(unittest.TestCase):
    @staticmethod
    def _catalog_package(
        package_id: str = "en-us-x-sfg",
        *,
        file_id: str = "f1",
        url: str = "https://example.com/en.zvoice",
        sha256: str = "a" * 64,
        compressed_size: int = 100,
        speakers: tuple[dict[str, Any], ...] = ({"id": "en-1", "speaker": "s1", "language": "en-US"},),
    ) -> Any:
        return catalog_module.VoicePackage(
            id=package_id,
            fileId=file_id,
            url=url,
            sha256Checksum=sha256,
            compressedSize=compressed_size,
            remote=False,
            speakers=speakers,
        )

    def test_valid_multiple_packages(self) -> None:
        pkg1 = self._catalog_package()
        pkg2 = self._catalog_package(
            "vi-vn-x-gda",
            file_id="f2",
            url="https://example.com/vi.zvoice",
            sha256="b" * 64,
            compressed_size=200,
            speakers=({"id": "vi-1", "speaker": "s2", "language": "vi-VN"},),
        )
        catalog = catalog_module.VoiceCatalog([pkg1, pkg2])
        warnings = voice_store.validate_package_catalog(catalog)
        self.assertEqual(warnings, [])

    def test_warnings_collected_from_later_packages(self) -> None:
        """Verify warnings in package 2 are not skipped by early return."""
        pkg1 = self._catalog_package()
        pkg2 = self._catalog_package(
            "vi-vn-x-gda",
            file_id="f2",
            url="",
            sha256="b" * 64,
            compressed_size=200,
            speakers=(),
        )
        catalog = catalog_module.VoiceCatalog([pkg1, pkg2])
        warnings = voice_store.validate_package_catalog(catalog)
        self.assertTrue(any("vi-vn-x-gda" in w for w in warnings))

    def test_accepts_speaker_key_from_real_voices_json_schema(self) -> None:
        """validate_package_catalog accepts raw voices.json dicts using 'speaker' without 'id'."""
        pkg = self._catalog_package(
            speakers=({"speaker": "s1", "name": "Voice 1", "gender": "female", "language": "en-US"},),
        )
        catalog = catalog_module.VoiceCatalog([pkg])
        warnings = voice_store.validate_package_catalog(catalog)
        self.assertEqual(warnings, [])

    def test_package_id_to_language_preserves_script_subtag(self) -> None:
        self.assertEqual("zh-Hans-CN", catalog_module.package_id_to_language("zh-hans-cn-x-multi"))
        self.assertEqual("sr-Latn-RS", catalog_module.package_id_to_language("sr-latn-rs-x-multi"))
        self.assertEqual("en-US", catalog_module.package_id_to_language("en-us-x-sfg"))

    def test_from_json_rejects_empty_and_dot_package_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog_path = Path(tmpdir) / "voices.json"
            for badId in ("", "   ", ".", ".."):
                with self.subTest(badId=badId):
                    catalog_path.write_text(
                        json.dumps([{"id": badId, "speakers": [{"speaker": "s1"}]}]),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        catalog_module.VoiceCatalog.load(catalog_path)


if __name__ == "__main__":
    unittest.main()

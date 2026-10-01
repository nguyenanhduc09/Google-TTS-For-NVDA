"""Integration tests for the voice package lifecycle.

Tests the complete flow: catalog loading → package verification →
download → install → verify installed → remove → verify removed.

Since these tests run without NVDA, they use the pure driver modules
and a temporary voice directory.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from tests.test_support import load_driver_module

voice_store = load_driver_module("voice_store")
catalog_module = load_driver_module("catalog")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_voices_dir() -> Path:
    """Create a temporary directory for voice packages."""
    tmpdir = Path(tempfile.mkdtemp(prefix="google_tts_test_"))
    voices_dir = tmpdir / "voices"
    voices_dir.mkdir()
    return voices_dir


def _create_fake_zvoice(path: Path, content: bytes = b"fake zvoice data") -> None:
    """Create a fake .zvoice file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha256_of_bytes(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


class _FakeHttpResponse:
    """Minimal urlopen() context manager so download tests never touch the network."""

    def __init__(self, payload: bytes, content_length: int | None = None) -> None:
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload) if content_length is None else content_length)}

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


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


# ---------------------------------------------------------------------------
# Test 1: Catalog loading
# ---------------------------------------------------------------------------


class CatalogLoadingTests(unittest.TestCase):
    """Verify VoiceCatalog loads correctly from JSON."""

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
        tmpdir = Path(tempfile.mkdtemp())
        catalog_path = tmpdir / "voices.json"
        catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

        try:
            cat = catalog_module.VoiceCatalog.load(catalog_path)
            self.assertEqual(2, len(cat.packages))
            self.assertEqual(3, len(cat.speakers))
            self.assertIn("en-us-x-multi", cat._packageById)
            self.assertIn("vi-vn-x-multi", cat._packageById)
        finally:
            import shutil

            shutil.rmtree(tmpdir)

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
        tmpdir = Path(tempfile.mkdtemp())
        catalog_path = tmpdir / "voices.json"
        catalog_path.write_text(json.dumps(catalog_data), encoding="utf-8")

        try:
            cat = catalog_module.VoiceCatalog.load(catalog_path)
            self.assertEqual("en-us-x-multi", cat.packages[0].id)
            self.assertEqual("vi-vn-x-multi", cat.packages[1].id)
        finally:
            import shutil

            shutil.rmtree(tmpdir)

    def test_package_id_to_language(self) -> None:
        """package_id_to_language extracts language from package ID."""
        self.assertEqual("en-US", catalog_module.package_id_to_language("en-us-x-multi"))
        self.assertEqual("vi-VN", catalog_module.package_id_to_language("vi-vn-x-multi"))
        self.assertEqual("zh-CN", catalog_module.package_id_to_language("zh-cn-x-multi"))

    def test_is_package_supported_by_engine(self) -> None:
        """Unsupported package families are detected."""
        normal = catalog_module.VoicePackage(
            id="en-us-x-multi",
            fileId="f",
            url="",
            sha256Checksum="",
            compressedSize=0,
            remote=True,
            speakers=(),
        )
        unsupported = catalog_module.VoicePackage(
            id="locomel-en-us",
            fileId="f",
            url="",
            sha256Checksum="",
            compressedSize=0,
            remote=True,
            speakers=(),
        )
        self.assertTrue(catalog_module.is_package_supported_by_engine(normal))
        self.assertFalse(catalog_module.is_package_supported_by_engine(unsupported))


# ---------------------------------------------------------------------------
# Test 2: Package verification
# ---------------------------------------------------------------------------


class PackageVerificationTests(unittest.TestCase):
    """Verify package file verification with SHA256."""

    def test_is_package_installed_returns_false_for_missing_file(self) -> None:
        """is_package_installed returns False when file doesn't exist."""
        voices_dir = _make_temp_voices_dir()
        try:
            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                pkg = _FakePackage()
                self.assertFalse(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_is_package_installed_returns_true_for_valid_file(self) -> None:
        """is_package_installed returns True for file with correct size and hash."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"test voice data content"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                self.assertTrue(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_is_package_installed_returns_false_for_wrong_hash(self) -> None:
        """is_package_installed returns False when SHA256 doesn't match."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"test voice data content"
            pkg = _FakePackage(
                sha256="wrong" * 13,  # wrong hash
                compressed_size=len(content),
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                self.assertFalse(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_is_package_installed_returns_false_for_wrong_size(self) -> None:
        """is_package_installed returns False when file size doesn't match."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"test voice data content"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content) + 100,  # wrong size
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                self.assertFalse(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_is_package_installed_ignores_hash_when_empty(self) -> None:
        """is_package_installed ignores SHA256 when checksum is empty."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"test voice data content"
            pkg = _FakePackage(
                sha256="",  # no hash check
                compressed_size=len(content),
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                self.assertTrue(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)


# ---------------------------------------------------------------------------
# Test 3: Package removal
# ---------------------------------------------------------------------------


class PackageRemovalTests(unittest.TestCase):
    """Verify package removal cleans up files and cache."""

    def test_remove_package_deletes_file(self) -> None:
        """remove_package deletes the .zvoice file."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"test voice data content"
            pkg = _FakePackage(sha256=_sha256_of_bytes(content))
            pkg_path = voices_dir / pkg.fileName
            _create_fake_zvoice(pkg_path, content)
            self.assertTrue(pkg_path.exists())

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                voice_store.remove_package(pkg)
                self.assertFalse(pkg_path.exists())
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_remove_package_handles_missing_file(self) -> None:
        """remove_package doesn't raise when file is already gone."""
        voices_dir = _make_temp_voices_dir()
        try:
            pkg = _FakePackage()

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                # Should not raise
                voice_store.remove_package(pkg)
        finally:
            import shutil

            shutil.rmtree(voices_dir)


# ---------------------------------------------------------------------------
# Test 4: Package copy (import)
# ---------------------------------------------------------------------------


class PackageCopyTests(unittest.TestCase):
    """Verify copy_existing_package for import flow."""

    def test_copy_installs_package(self) -> None:
        """copy_existing_package copies file to voice dir."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"imported voice data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            source = voices_dir / "source.zvoice"
            _create_fake_zvoice(source, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
                result = voice_store.copy_existing_package(source, pkg)
                self.assertTrue(result.exists())
                self.assertEqual(content, result.read_bytes())
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_copy_verifies_size(self) -> None:
        """copy_existing_package rejects wrong file size."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"imported voice data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content) + 100,  # wrong size
            )
            source = voices_dir / "source.zvoice"
            _create_fake_zvoice(source, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir), self.assertRaises(RuntimeError):
                voice_store.copy_existing_package(source, pkg)
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_copy_verifies_sha256(self) -> None:
        """copy_existing_package rejects wrong SHA256."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"imported voice data"
            pkg = _FakePackage(
                sha256="wrong" * 13,  # wrong hash
                compressed_size=len(content),
            )
            source = voices_dir / "source.zvoice"
            _create_fake_zvoice(source, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir), self.assertRaises(RuntimeError):
                voice_store.copy_existing_package(source, pkg)
        finally:
            import shutil

            shutil.rmtree(voices_dir)


# ---------------------------------------------------------------------------
# Test 4b: Package download guards
# ---------------------------------------------------------------------------


class PackageDownloadGuardTests(unittest.TestCase):
    """Verify download_package enforces HTTPS, the size limit, and package integrity."""

    def test_rejects_non_https_download_url(self) -> None:
        """Insecure download links are refused before any network access."""
        voices_dir = _make_temp_voices_dir()
        try:
            pkg = _FakePackage(url="http://example.com/fake.zvoice")
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                self.assertRaises(RuntimeError) as context,
            ):
                voice_store.download_package(pkg)
            self.assertIn("HTTPS", str(context.exception))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_rejects_declared_size_over_download_limit(self) -> None:
        """A package whose catalog size exceeds the limit is refused before downloading."""
        voices_dir = _make_temp_voices_dir()
        try:
            pkg = _FakePackage(compressed_size=voice_store.VOICE_PACKAGE_MAX_BYTES + 1)
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                self.assertRaises(RuntimeError) as context,
            ):
                voice_store.download_package(pkg)
            self.assertIn("larger", str(context.exception))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_rejects_response_larger_than_limit(self) -> None:
        """A Content-Length above the limit aborts the download and removes the partial file."""
        voices_dir = _make_temp_voices_dir()
        try:
            pkg = _FakePackage()
            response = _FakeHttpResponse(b"partial", content_length=voice_store.VOICE_PACKAGE_MAX_BYTES + 1)
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / pkg.fileName).exists())
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_rejects_stream_over_limit_without_content_length(self) -> None:
        """Streamed bytes over the limit are refused even when Content-Length is unusable."""
        voices_dir = _make_temp_voices_dir()
        try:
            pkg = _FakePackage()
            response = _FakeHttpResponse(b"x" * 64, content_length=0)
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                patch.object(voice_store, "VOICE_PACKAGE_MAX_BYTES", 8),
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_download_verifies_and_installs_package(self) -> None:
        """A verified download is installed into the voice directory."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"downloaded voice data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            response = _FakeHttpResponse(content)
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
            ):
                result = voice_store.download_package(pkg)
                self.assertEqual(content, result.read_bytes())
                self.assertTrue(voice_store.is_package_installed(pkg))
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_download_rejects_checksum_mismatch(self) -> None:
        """A payload with the right size but the wrong checksum is discarded."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"tampered voice data"
            pkg = _FakePackage(
                sha256="wrong" * 13,
                compressed_size=len(content),
            )
            response = _FakeHttpResponse(content)
            with (
                patch.object(voice_store, "voice_dir", return_value=voices_dir),
                patch.object(voice_store.urllib.request, "urlopen", return_value=response),
                self.assertRaises(RuntimeError),
            ):
                voice_store.download_package(pkg)
            self.assertFalse((voices_dir / pkg.fileName).exists())
            self.assertFalse((voices_dir / f"{pkg.id}.download").exists())
        finally:
            import shutil

            shutil.rmtree(voices_dir)


# ---------------------------------------------------------------------------
# Test 5: End-to-end lifecycle
# ---------------------------------------------------------------------------


class VoicePackageLifecycleTests(unittest.TestCase):
    """Test the complete lifecycle: install → verify → remove → verify."""

    def test_full_lifecycle(self) -> None:
        """Install a package, verify it, remove it, verify removal."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"complete lifecycle test data"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            source = voices_dir / "source.zvoice"
            _create_fake_zvoice(source, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
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
        finally:
            import shutil

            shutil.rmtree(voices_dir)

    def test_cache_invalidation_after_removal(self) -> None:
        """Verification cache is invalidated after package removal."""
        voices_dir = _make_temp_voices_dir()
        try:
            content = b"cache invalidation test"
            pkg = _FakePackage(
                sha256=_sha256_of_bytes(content),
                compressed_size=len(content),
            )
            _create_fake_zvoice(voices_dir / pkg.fileName, content)

            with patch.object(voice_store, "voice_dir", return_value=voices_dir):
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
        finally:
            import shutil

            shutil.rmtree(voices_dir)


class CatalogValidationTests(unittest.TestCase):
    def test_valid_multiple_packages(self) -> None:
        pkg1 = catalog_module.VoicePackage(
            id="en-us-x-sfg",
            fileId="f1",
            url="https://example.com/en.zvoice",
            sha256Checksum="a" * 64,
            compressedSize=100,
            remote=False,
            speakers=({"id": "en-1", "speaker": "s1", "language": "en-US"},),
        )
        pkg2 = catalog_module.VoicePackage(
            id="vi-vn-x-gda",
            fileId="f2",
            url="https://example.com/vi.zvoice",
            sha256Checksum="b" * 64,
            compressedSize=200,
            remote=False,
            speakers=({"id": "vi-1", "speaker": "s2", "language": "vi-VN"},),
        )
        catalog = catalog_module.VoiceCatalog([pkg1, pkg2])
        warnings = voice_store.validate_package_catalog(catalog)
        self.assertEqual(warnings, [])

    def test_warnings_collected_from_later_packages(self) -> None:
        """Verify warnings in package 2 are not skipped by early return."""
        pkg1 = catalog_module.VoicePackage(
            id="en-us-x-sfg",
            fileId="f1",
            url="https://example.com/en.zvoice",
            sha256Checksum="a" * 64,
            compressedSize=100,
            remote=False,
            speakers=({"id": "en-1", "speaker": "s1", "language": "en-US"},),
        )
        pkg2 = catalog_module.VoicePackage(
            id="vi-vn-x-gda",
            fileId="f2",
            url="",
            sha256Checksum="b" * 64,
            compressedSize=200,
            remote=False,
            speakers=(),
        )
        catalog = catalog_module.VoiceCatalog([pkg1, pkg2])
        warnings = voice_store.validate_package_catalog(catalog)
        self.assertTrue(any("vi-vn-x-gda" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()

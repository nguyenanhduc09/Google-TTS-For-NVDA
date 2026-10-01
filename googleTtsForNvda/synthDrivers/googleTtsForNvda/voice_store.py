from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat as stat_module
import tempfile
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .catalog import VoiceCatalog, VoicePackage, is_package_supported_by_engine

try:
    import addonHandler

    addonHandler.initTranslation()
except Exception:

    def _(message: str) -> str:
        return message


ProgressCallback = Callable[[int | None, str], None]

VOICE_PACKAGE_MAX_BYTES = 256 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 1024 * 256

_verifiedPackageCache: dict[str, tuple[int, int]] = {}
_persistentVerifiedPackageCache: dict[str, dict[str, object]] | None = None
_verificationCacheLock = threading.RLock()
_dataRootCache: Path | None = None
_voiceDirCache: Path | None = None
_VERIFICATION_CACHE_VERSION = 1
_VERIFICATION_CACHE_FILE = "verified_voices.json"


def _default_config_path() -> Path:
    try:
        import globalVars  # type: ignore

        configPath = getattr(getattr(globalVars, "appArgs", None), "configPath", None)
        if configPath:
            return Path(configPath)
    except Exception:
        pass
    return Path(tempfile.gettempdir()) / "googleTtsForNvda"


def data_root() -> Path:
    global _dataRootCache
    with _verificationCacheLock:
        if _dataRootCache is None:
            _dataRootCache = _default_config_path() / "googleTtsForNvda"
        root = _dataRootCache
    root.mkdir(parents=True, exist_ok=True)
    return root


def _verification_cache_path() -> Path:
    return data_root() / _VERIFICATION_CACHE_FILE


def voice_dir() -> Path:
    global _voiceDirCache
    with _verificationCacheLock:
        if _voiceDirCache is None:
            _voiceDirCache = data_root() / "voices"
        path = _voiceDirCache
    path.mkdir(parents=True, exist_ok=True)
    return path


def package_file(package: VoicePackage) -> Path:
    return voice_dir() / package.fileName


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_persistent_verification_cache() -> dict[str, dict[str, object]]:
    global _persistentVerifiedPackageCache
    with _verificationCacheLock:
        if _persistentVerifiedPackageCache is not None:
            return _persistentVerifiedPackageCache
        cachePath = _verification_cache_path()
        try:
            raw = json.loads(cachePath.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _persistentVerifiedPackageCache = {}
            return _persistentVerifiedPackageCache
        except OSError:
            _persistentVerifiedPackageCache = {}
            return _persistentVerifiedPackageCache
        except json.JSONDecodeError:
            _persistentVerifiedPackageCache = {}
            _save_persistent_verification_cache()
            return _persistentVerifiedPackageCache
        if not isinstance(raw, dict) or raw.get("version") != _VERIFICATION_CACHE_VERSION:
            _persistentVerifiedPackageCache = {}
            _save_persistent_verification_cache()
            return _persistentVerifiedPackageCache
        packages = raw.get("packages")
        _persistentVerifiedPackageCache = packages if isinstance(packages, dict) else {}
        if not isinstance(packages, dict):
            _save_persistent_verification_cache()
        return _persistentVerifiedPackageCache


def _save_persistent_verification_cache() -> None:
    with _verificationCacheLock:
        if _persistentVerifiedPackageCache is None:
            return
        cachePath = _verification_cache_path()
        cachePath.parent.mkdir(parents=True, exist_ok=True)
        tmp = cachePath.with_suffix(".tmp")
        payload = {
            "version": _VERIFICATION_CACHE_VERSION,
            "packages": _persistentVerifiedPackageCache,
        }
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, cachePath)
        except OSError:
            pass


def _persistent_cache_matches(package: VoicePackage, stat: os.stat_result) -> bool:
    if not package.sha256Checksum:
        return False
    cache = _load_persistent_verification_cache()
    entry = cache.get(package.id)
    if not isinstance(entry, dict):
        return False
    expectedHash = package.sha256Checksum.lower()
    return (
        entry.get("fileName") == package.fileName
        and entry.get("size") == stat.st_size
        and entry.get("mtimeNs") == stat.st_mtime_ns
        and str(entry.get("expectedSha256") or "").lower() == expectedHash
        and str(entry.get("verifiedSha256") or "").lower() == expectedHash
    )


def _remember_verified_package(
    package: VoicePackage,
    stat: os.stat_result,
    actualHash: str | None = None,
    savePersistent: bool = True,
) -> bool:
    cacheKey = (stat.st_size, stat.st_mtime_ns)
    with _verificationCacheLock:
        _verifiedPackageCache[package.id] = cacheKey
    if not package.sha256Checksum or actualHash is None:
        return False
    cache = _load_persistent_verification_cache()
    cache[package.id] = {
        "fileName": package.fileName,
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "expectedSha256": package.sha256Checksum.lower(),
        "verifiedSha256": actualHash.lower(),
    }
    if savePersistent:
        _save_persistent_verification_cache()
    return True


def _forget_verified_package(packageId: str, savePersistent: bool = True) -> bool:
    with _verificationCacheLock:
        _verifiedPackageCache.pop(packageId, None)
        cache = _load_persistent_verification_cache()
        if packageId not in cache:
            return False
        cache.pop(packageId, None)
    if savePersistent:
        _save_persistent_verification_cache()
    return True


def _check_package_file_installed(
    package: VoicePackage,
    path: Path,
    stat: os.stat_result | None = None,
    savePersistent: bool = True,
) -> tuple[bool, bool]:
    if stat is None:
        if not path.is_file():
            return False, _forget_verified_package(package.id, savePersistent=savePersistent)
        stat = path.stat()
    cacheKey = (stat.st_size, stat.st_mtime_ns)
    if package.compressedSize and stat.st_size != package.compressedSize:
        return False, _forget_verified_package(package.id, savePersistent=savePersistent)
    with _verificationCacheLock:
        if _verifiedPackageCache.get(package.id) == cacheKey:
            return True, False
    if _persistent_cache_matches(package, stat):
        with _verificationCacheLock:
            _verifiedPackageCache[package.id] = cacheKey
        return True, False
    actualHash = sha256(path).lower() if package.sha256Checksum else None
    if actualHash is not None and actualHash != package.sha256Checksum.lower():
        return False, _forget_verified_package(package.id, savePersistent=savePersistent)
    return True, _remember_verified_package(package, stat, actualHash, savePersistent=savePersistent)


def is_package_installed(package: VoicePackage) -> bool:
    installed, _cacheUpdated = _check_package_file_installed(package, package_file(package))
    return installed


def _voice_files_by_name() -> dict[str, tuple[Path, os.stat_result]]:
    dirPath = voice_dir()
    try:
        entries = os.scandir(dirPath)
    except OSError:
        return {}
    files: dict[str, tuple[Path, os.stat_result]] = {}
    with entries:
        for entry in entries:
            try:
                stat = entry.stat()
            except OSError:
                continue
            if not stat_module.S_ISREG(stat.st_mode):
                continue
            files[entry.name] = (Path(entry.path), stat)
    return files


def physically_installed_packages(catalog: VoiceCatalog) -> list[VoicePackage]:
    filesByName = _voice_files_by_name()
    installed: list[VoicePackage] = []
    verificationCacheUpdated = False
    # Load persistent cache once for the entire batch to avoid redundant I/O.
    with _verificationCacheLock:
        persistentCache = _load_persistent_verification_cache()
    for package in catalog.packages:
        fileInfo = filesByName.get(package.fileName)
        if fileInfo is None:
            with _verificationCacheLock:
                _verifiedPackageCache.pop(package.id, None)
                if package.id in persistentCache:
                    persistentCache.pop(package.id, None)
                    verificationCacheUpdated = True
            continue
        path, stat = fileInfo
        cacheKey = (stat.st_size, stat.st_mtime_ns)
        if package.compressedSize and stat.st_size != package.compressedSize:
            with _verificationCacheLock:
                _verifiedPackageCache.pop(package.id, None)
                if package.id in persistentCache:
                    persistentCache.pop(package.id, None)
                    verificationCacheUpdated = True
            continue
        with _verificationCacheLock:
            if _verifiedPackageCache.get(package.id) == cacheKey:
                installed.append(package)
                continue
        # Check persistent cache without re-reading from disk.
        persistentMatch = False
        if package.sha256Checksum and package.id in persistentCache:
            entry = persistentCache[package.id]
            if isinstance(entry, dict):
                expectedHash = package.sha256Checksum.lower()
                persistentMatch = (
                    entry.get("fileName") == package.fileName
                    and entry.get("size") == stat.st_size
                    and entry.get("mtimeNs") == stat.st_mtime_ns
                    and str(entry.get("expectedSha256") or "").lower() == expectedHash
                    and str(entry.get("verifiedSha256") or "").lower() == expectedHash
                )
        if persistentMatch:
            with _verificationCacheLock:
                _verifiedPackageCache[package.id] = cacheKey
            installed.append(package)
            continue
        # Last resort: compute SHA256.
        actualHash = sha256(path).lower() if package.sha256Checksum else None
        if actualHash is not None and actualHash != package.sha256Checksum.lower():
            with _verificationCacheLock:
                _verifiedPackageCache.pop(package.id, None)
                if package.id in persistentCache:
                    persistentCache.pop(package.id, None)
                    verificationCacheUpdated = True
            continue
        installed.append(package)
        with _verificationCacheLock:
            _verifiedPackageCache[package.id] = cacheKey
        if package.sha256Checksum and actualHash is not None:
            persistentCache[package.id] = {
                "fileName": package.fileName,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
                "expectedSha256": package.sha256Checksum.lower(),
                "verifiedSha256": actualHash.lower(),
            }
            verificationCacheUpdated = True
    if verificationCacheUpdated:
        _save_persistent_verification_cache()
    return installed


def usable_installed_packages(packages: list[VoicePackage]) -> list[VoicePackage]:
    usableIds = {package.id for package in packages if is_package_supported_by_engine(package)}
    while True:
        nextUsableIds = {
            package.id
            for package in packages
            if package.id in usableIds and (not package.dependentVoiceId or package.dependentVoiceId in usableIds)
        }
        if nextUsableIds == usableIds:
            break
        usableIds = nextUsableIds
    return [package for package in packages if package.id in usableIds]


def installed_packages(catalog: VoiceCatalog) -> list[VoicePackage]:
    return usable_installed_packages(physically_installed_packages(catalog))


def validate_package_catalog(catalog: VoiceCatalog) -> list[str]:
    """Validate catalog integrity for all installed packages.

    Returns a list of warning messages for any issues found.
    """
    warnings: list[str] = []
    allSpeakerIds: set[str] = set()
    for package in catalog.packages:
        if not package.id:
            warnings.append("Package with empty id found.")
            continue
        if not package.speakers:
            warnings.append(f"Package '{package.id}' has no speakers defined.")
            continue
        if not package.url:
            warnings.append(f"Package '{package.id}' has no download URL.")
        for speakerDict in package.speakers:
            speakerId = speakerDict.get("id", "")
            if not speakerId:
                warnings.append(f"Package '{package.id}' has a speaker with empty id.")
                continue
            if speakerId in allSpeakerIds:
                warnings.append(f"Duplicate speaker id '{speakerId}' in package '{package.id}'.")
            allSpeakerIds.add(speakerId)
            lang = speakerDict.get("language", "")
            if not lang:
                warnings.append(f"Speaker '{speakerId}' in package '{package.id}' has no language.")
    return warnings


def remove_package(package: VoicePackage) -> None:
    _forget_verified_package(package.id)
    path = package_file(package)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def download_package(package: VoicePackage, progress: ProgressCallback | None = None) -> Path:
    if is_package_installed(package):
        if progress:
            progress(100, _("{package} is already installed.").format(package=package.id))
        return package_file(package)
    if not package.url:
        raise RuntimeError(_("No download link is available for voice package {package}.").format(package=package.id))
    if urllib.parse.urlparse(package.url).scheme.lower() != "https":
        raise RuntimeError(f"Voice package {package.id} must be downloaded over a secure HTTPS connection.")
    if package.compressedSize > VOICE_PACKAGE_MAX_BYTES:
        raise RuntimeError(f"Voice package {package.id} is larger than the supported download limit.")
    target = package_file(package)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".download")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    if progress:
        progress(0, _("Downloading {package}.").format(package=package.id))
    request = urllib.request.Request(package.url, headers={"User-Agent": "NVDA Google TTS"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as output:
            total = int(response.headers.get("Content-Length") or package.compressedSize or 0)
            if total > VOICE_PACKAGE_MAX_BYTES:
                raise RuntimeError(f"Voice package {package.id} is larger than the supported download limit.")
            downloaded = 0
            lastPercent = -1
            for chunk in iter(lambda: response.read(DOWNLOAD_CHUNK_SIZE), b""):
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded > VOICE_PACKAGE_MAX_BYTES:
                    raise RuntimeError(f"Voice package {package.id} is larger than the supported download limit.")
                if progress and total:
                    percent = min(99, int(downloaded * 100 / total))
                    if percent != lastPercent:
                        lastPercent = percent
                        progress(percent, _("Downloading {package}.").format(package=package.id))
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    if package.compressedSize and tmp.stat().st_size != package.compressedSize:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            _(
                "Voice package {package} did not pass verification after download. Please try downloading it again."
            ).format(package=package.id)
        )
    if package.sha256Checksum:
        actualHash = sha256(tmp)
        if actualHash.lower() != package.sha256Checksum.lower():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                _(
                    "Voice package {package} did not pass verification after download. Please try downloading it again."
                ).format(package=package.id)
            )
    else:
        actualHash = None
    os.replace(tmp, target)
    _remember_verified_package(package, target.stat(), actualHash)
    if progress:
        progress(100, _("Installed {package}.").format(package=package.id))
    return target


def copy_existing_package(source: Path, package: VoicePackage) -> Path:
    target = package_file(package)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".import_tmp")
    try:
        tmp.unlink(missing_ok=True)
        shutil.copy2(source, tmp)
        if package.compressedSize and tmp.stat().st_size != package.compressedSize:
            raise RuntimeError(
                _("Voice package {package} did not pass verification after import.").format(package=package.id)
            )
        actualHash = sha256(tmp) if package.sha256Checksum else None
        if package.sha256Checksum and actualHash is not None:
            if actualHash.lower() != package.sha256Checksum.lower():
                raise RuntimeError(
                    _("Voice package {package} did not pass verification after import.").format(package=package.id)
                )
        _forget_verified_package(package.id)
        os.replace(tmp, target)
        _remember_verified_package(package, target.stat(), actualHash)
        return target
    finally:
        tmp.unlink(missing_ok=True)

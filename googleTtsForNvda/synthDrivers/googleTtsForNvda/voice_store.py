from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat as stat_module
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

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

_verifiedPackageCache: dict[str, tuple[str, str, int, int]] = {}
_persistentVerifiedPackageCache: dict[str, dict[str, object]] | None = None
_verificationCacheLock = threading.RLock()
# Lock ordering: acquire _package_lock BEFORE _verificationCacheLock; never reverse.
_packageLocks: dict[str, threading.RLock] = {}
_packageLocksGuard = threading.Lock()
_packageStateGenerations: dict[str, int] = {}
_dataRootCache: Path | None = None
_voiceDirCache: Path | None = None
_VERIFICATION_CACHE_VERSION = 1
_VERIFICATION_CACHE_FILE = "verified_voices.json"


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject HTTP redirects that downgrade HTTPS voice package downloads."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        resolvedUrl = urllib.parse.urljoin(req.full_url, str(newurl))
        if urllib.parse.urlparse(resolvedUrl).scheme.lower() != "https":
            raise RuntimeError("Voice package downloads must remain on a secure HTTPS connection.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_STDLIB_URLOPEN = urllib.request.urlopen
_HTTPS_OPENER = urllib.request.build_opener(_HttpsOnlyRedirectHandler())


def _open_https_url(request: urllib.request.Request, *, timeout: float) -> Any:
    if urllib.request.urlopen is not _STDLIB_URLOPEN:
        return urllib.request.urlopen(request, timeout=timeout)
    return _HTTPS_OPENER.open(request, timeout=timeout)


def _package_lock(packageId: str) -> threading.RLock:
    with _packageLocksGuard:
        lock = _packageLocks.get(packageId)
        if lock is None:
            lock = threading.RLock()
            _packageLocks[packageId] = lock
        return lock


def _package_generation(packageId: str) -> int:
    with _verificationCacheLock:
        return _packageStateGenerations.get(packageId, 0)


def _bump_package_state_generation(packageId: str) -> int:
    with _verificationCacheLock:
        nextGen = _packageStateGenerations.get(packageId, 0) + 1
        _packageStateGenerations[packageId] = nextGen
        return nextGen


def _clear_readonly_attr(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat_module.S_IWRITE | stat_module.S_IREAD)


def _cache_key(package: VoicePackage, stat: os.stat_result) -> tuple[str, str, int, int]:
    return (
        package.fileName,
        (package.sha256Checksum or "").lower(),
        stat.st_size,
        stat.st_mtime_ns,
    )


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
    fileName = package.fileName
    if not fileName or "\x00" in fileName or "/" in fileName or "\\" in fileName or fileName in (".", ".."):
        raise ValueError(f"Invalid voice package filename: {fileName!r}")
    voicesRoot = voice_dir().resolve()
    candidate = (voicesRoot / fileName).resolve()
    if candidate.parent != voicesRoot:
        raise ValueError(f"Voice package filename escapes voice directory: {fileName!r}")
    return voice_dir() / fileName


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
            # Leave _persistentVerifiedPackageCache unset so transient read failures retry later.
            return {}
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
        payload = json.dumps(
            {
                "version": _VERIFICATION_CACHE_VERSION,
                "packages": _persistentVerifiedPackageCache,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        tmpPath: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cachePath.parent,
                prefix="verified_voices_",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmpPath = Path(handle.name)
                handle.write(payload)
                handle.flush()
            _clear_readonly_attr(cachePath)
            os.replace(tmpPath, cachePath)
            tmpPath = None
        except OSError:
            if tmpPath is not None:
                with contextlib.suppress(OSError):
                    tmpPath.unlink()


def _persistent_cache_matches(package: VoicePackage, stat: os.stat_result) -> bool:
    if not package.sha256Checksum:
        return False
    with _verificationCacheLock:
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
    expectedGeneration: int | None = None,
) -> bool:
    cacheKey = _cache_key(package, stat)
    with _verificationCacheLock:
        if expectedGeneration is not None and _packageStateGenerations.get(package.id, 0) != expectedGeneration:
            return False
        _verifiedPackageCache[package.id] = cacheKey
        if not package.sha256Checksum or actualHash is None:
            return False
        cache = _load_persistent_verification_cache()
        if _persistentVerifiedPackageCache is None:
            return False
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


def _forget_verified_package(
    packageId: str,
    savePersistent: bool = True,
    expectedGeneration: int | None = None,
) -> bool:
    with _verificationCacheLock:
        if expectedGeneration is not None and _packageStateGenerations.get(packageId, 0) != expectedGeneration:
            return False
        _verifiedPackageCache.pop(packageId, None)
        cache = _load_persistent_verification_cache()
        if _persistentVerifiedPackageCache is None or packageId not in cache:
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
    startGen = _package_generation(package.id)
    if stat is None:
        if not path.is_file():
            return False, _forget_verified_package(
                package.id,
                savePersistent=savePersistent,
                expectedGeneration=startGen,
            )
        try:
            stat = path.stat()
        except OSError:
            return False, _forget_verified_package(
                package.id,
                savePersistent=savePersistent,
                expectedGeneration=startGen,
            )
    if package.compressedSize < 0 or (package.compressedSize and stat.st_size != package.compressedSize):
        return False, _forget_verified_package(
            package.id,
            savePersistent=savePersistent,
            expectedGeneration=startGen,
        )
    cacheKey = _cache_key(package, stat)
    with _verificationCacheLock:
        if (
            _packageStateGenerations.get(package.id, 0) == startGen
            and _verifiedPackageCache.get(package.id) == cacheKey
        ):
            return True, False
    if _persistent_cache_matches(package, stat):
        with _verificationCacheLock:
            if _packageStateGenerations.get(package.id, 0) == startGen:
                _verifiedPackageCache[package.id] = cacheKey
        return True, False
    try:
        actualHash = sha256(path).lower() if package.sha256Checksum else None
    except OSError:
        return False, _forget_verified_package(
            package.id,
            savePersistent=savePersistent,
            expectedGeneration=startGen,
        )
    if actualHash is not None and actualHash != package.sha256Checksum.lower():
        return False, _forget_verified_package(
            package.id,
            savePersistent=savePersistent,
            expectedGeneration=startGen,
        )
    return True, _remember_verified_package(
        package,
        stat,
        actualHash,
        savePersistent=savePersistent,
        expectedGeneration=startGen,
    )


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
    # Load persistent cache and state generations before scanning disk so concurrent
    # download/import/remove operations invalidate stale results cleanly.
    with _verificationCacheLock:
        startGens = {package.id: _packageStateGenerations.get(package.id, 0) for package in catalog.packages}
        persistentCache = _load_persistent_verification_cache()
        persistentLoaded = _persistentVerifiedPackageCache is not None
    filesByName = _voice_files_by_name()
    installed: list[VoicePackage] = []
    verificationCacheUpdated = False
    for package in catalog.packages:
        startGen = startGens[package.id]
        try:
            expectedPath = package_file(package)
        except ValueError:
            continue
        fileInfo = filesByName.get(expectedPath.name)
        if fileInfo is None:
            mutated = False
            with _verificationCacheLock:
                if _packageStateGenerations.get(package.id, 0) != startGen:
                    mutated = True
                else:
                    _verifiedPackageCache.pop(package.id, None)
                    if persistentLoaded and package.id in persistentCache:
                        persistentCache.pop(package.id, None)
                        verificationCacheUpdated = True
            if mutated and is_package_installed(package):
                installed.append(package)
            continue
        path, stat = fileInfo
        cacheKey = _cache_key(package, stat)
        if package.compressedSize < 0 or (package.compressedSize and stat.st_size != package.compressedSize):
            mutated = False
            with _verificationCacheLock:
                if _packageStateGenerations.get(package.id, 0) != startGen:
                    mutated = True
                else:
                    _verifiedPackageCache.pop(package.id, None)
                    if persistentLoaded and package.id in persistentCache:
                        persistentCache.pop(package.id, None)
                        verificationCacheUpdated = True
            if mutated and is_package_installed(package):
                installed.append(package)
            continue
        mutated = False
        alreadyHandled = False
        with _verificationCacheLock:
            if _packageStateGenerations.get(package.id, 0) != startGen:
                mutated = True
            elif _verifiedPackageCache.get(package.id) == cacheKey:
                installed.append(package)
                alreadyHandled = True
            else:
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
                    _verifiedPackageCache[package.id] = cacheKey
                    installed.append(package)
                    alreadyHandled = True
        if mutated:
            if is_package_installed(package):
                installed.append(package)
            continue
        if alreadyHandled:
            continue
        try:
            actualHash = sha256(path).lower() if package.sha256Checksum else None
        except OSError:
            continue
        if actualHash is not None and actualHash != package.sha256Checksum.lower():
            mutated = False
            with _verificationCacheLock:
                if _packageStateGenerations.get(package.id, 0) != startGen:
                    mutated = True
                else:
                    _verifiedPackageCache.pop(package.id, None)
                    if persistentLoaded and package.id in persistentCache:
                        persistentCache.pop(package.id, None)
                        verificationCacheUpdated = True
            if mutated and is_package_installed(package):
                installed.append(package)
            continue
        with _verificationCacheLock:
            if _packageStateGenerations.get(package.id, 0) != startGen:
                mutated = True
            else:
                _verifiedPackageCache[package.id] = cacheKey
                if persistentLoaded and package.sha256Checksum and actualHash is not None:
                    persistentCache[package.id] = {
                        "fileName": package.fileName,
                        "size": stat.st_size,
                        "mtimeNs": stat.st_mtime_ns,
                        "expectedSha256": package.sha256Checksum.lower(),
                        "verifiedSha256": actualHash.lower(),
                    }
                    verificationCacheUpdated = True
                installed.append(package)
        if mutated and is_package_installed(package):
            installed.append(package)
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
            speakerCode = speakerDict.get("speaker") or speakerDict.get("name") or speakerDict.get("id") or ""
            if not speakerCode:
                warnings.append(f"Package '{package.id}' has a speaker with empty id.")
                continue
            speakerId = speakerCode if ":" in speakerCode else f"{package.id}:{speakerCode}"
            if speakerId in allSpeakerIds:
                warnings.append(f"Duplicate speaker id '{speakerId}' in package '{package.id}'.")
            allSpeakerIds.add(speakerId)
            lang = speakerDict.get("language") or package.language
            if not lang:
                warnings.append(f"Speaker '{speakerId}' in package '{package.id}' has no language.")
    return warnings


def remove_package(package: VoicePackage) -> None:
    path = package_file(package)
    with _package_lock(package.id):
        _bump_package_state_generation(package.id)
        _forget_verified_package(package.id)
        _clear_readonly_attr(path)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _verify_package_file(filePath: Path, package: VoicePackage, failureMessage: str) -> str | None:
    fileStat = filePath.stat()
    if (
        package.compressedSize < 0
        or package.compressedSize > VOICE_PACKAGE_MAX_BYTES
        or fileStat.st_size > VOICE_PACKAGE_MAX_BYTES
        or (package.compressedSize and fileStat.st_size != package.compressedSize)
    ):
        raise RuntimeError(failureMessage)
    if not package.sha256Checksum:
        return None
    actualHash = sha256(filePath)
    if actualHash.lower() != package.sha256Checksum.lower():
        raise RuntimeError(failureMessage)
    return actualHash


def download_package(package: VoicePackage, progress: ProgressCallback | None = None) -> Path:
    target = package_file(package)
    with _package_lock(package.id):
        if is_package_installed(package):
            if progress:
                progress(100, _("{package} is already installed.").format(package=package.id))
            return target
        if not package.url:
            raise RuntimeError(
                _("No download link is available for voice package {package}.").format(package=package.id)
            )
        if urllib.parse.urlparse(package.url).scheme.lower() != "https":
            raise RuntimeError(f"Voice package {package.id} must be downloaded over a secure HTTPS connection.")
        if package.compressedSize < 0 or package.compressedSize > VOICE_PACKAGE_MAX_BYTES:
            raise RuntimeError(f"Voice package {package.id} is larger than the supported download limit.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(0, _("Downloading {package}.").format(package=package.id))
        request = urllib.request.Request(package.url, headers={"User-Agent": "NVDA Google TTS"})
        tmpPath: Path | None = None
        try:
            with _open_https_url(request, timeout=120) as response:
                geturl = getattr(response, "geturl", None)
                finalUrl = geturl() if callable(geturl) else None
                if isinstance(finalUrl, str) and finalUrl:
                    if urllib.parse.urlparse(finalUrl).scheme.lower() != "https":
                        raise RuntimeError(
                            f"Voice package {package.id} must be downloaded over a secure HTTPS connection."
                        )
                rawContentLength = response.headers.get("Content-Length")
                try:
                    total = int(rawContentLength) if rawContentLength is not None else int(package.compressedSize or 0)
                except (TypeError, ValueError):
                    total = int(package.compressedSize or 0)
                if total < 0:
                    total = int(package.compressedSize or 0)
                if total < 0 or total > VOICE_PACKAGE_MAX_BYTES:
                    raise RuntimeError(f"Voice package {package.id} is larger than the supported download limit.")
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=target.parent,
                    prefix=f".{package.id}.",
                    suffix=".download",
                    delete=False,
                ) as output:
                    tmpPath = Path(output.name)
                    downloaded = 0
                    lastPercent = -1
                    for chunk in iter(lambda: response.read(DOWNLOAD_CHUNK_SIZE), b""):
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > VOICE_PACKAGE_MAX_BYTES:
                            raise RuntimeError(
                                f"Voice package {package.id} is larger than the supported download limit."
                            )
                        if progress and total:
                            percent = min(99, int(downloaded * 100 / total))
                            if percent != lastPercent:
                                lastPercent = percent
                                progress(percent, _("Downloading {package}.").format(package=package.id))
            verifyError = _(
                "Voice package {package} did not pass verification after download. Please try downloading it again."
            ).format(package=package.id)
            actualHash = _verify_package_file(tmpPath, package, verifyError)
            _bump_package_state_generation(package.id)
            _forget_verified_package(package.id)
            _clear_readonly_attr(target)
            os.replace(tmpPath, target)
            tmpPath = None
            _remember_verified_package(package, target.stat(), actualHash)
        finally:
            if tmpPath is not None:
                _clear_readonly_attr(tmpPath)
                with contextlib.suppress(OSError):
                    tmpPath.unlink()
        if progress:
            progress(100, _("Installed {package}.").format(package=package.id))
        return target


def copy_existing_package(source: Path, package: VoicePackage) -> Path:
    target = package_file(package)
    verifyError = _("Voice package {package} did not pass verification after import.").format(package=package.id)
    with _package_lock(package.id):
        if not source.is_file():
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        sameFile = False
        with contextlib.suppress(OSError):
            sameFile = source.resolve() == target.resolve()
        if sameFile:
            targetStat = target.stat()
            actualHash = _verify_package_file(target, package, verifyError)
            _bump_package_state_generation(package.id)
            _clear_readonly_attr(target)
            _remember_verified_package(package, targetStat, actualHash)
            return target
        if source.stat().st_size > VOICE_PACKAGE_MAX_BYTES:
            raise RuntimeError(verifyError)
        tmpPath: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{package.id}.",
                suffix=".import_tmp",
                delete=False,
            ) as handle:
                tmpPath = Path(handle.name)
            shutil.copyfile(source, tmpPath)
            actualHash = _verify_package_file(tmpPath, package, verifyError)
            _bump_package_state_generation(package.id)
            _forget_verified_package(package.id)
            _clear_readonly_attr(target)
            os.replace(tmpPath, target)
            tmpPath = None
            _remember_verified_package(package, target.stat(), actualHash)
            return target
        finally:
            if tmpPath is not None:
                _clear_readonly_attr(tmpPath)
                with contextlib.suppress(OSError):
                    tmpPath.unlink()

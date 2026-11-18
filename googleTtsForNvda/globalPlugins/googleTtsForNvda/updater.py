from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

ADDON_ID = "googleTtsForNvda"
BUILD_INFO_FILE_NAME = "buildInfo.json"
UPDATE_CHANNEL = "stable"
UPDATE_MANIFEST_URL = "https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json"
MAX_UPDATE_MANIFEST_BYTES = 256 * 1024
MAX_UPDATE_PACKAGE_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 64 * 1024


class UpdateError(Exception):
    """Raised when the stable update manifest cannot be used."""


class UpdateCancelled(UpdateError):
    """Raised when the user cancels an update download."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str
    minimumNVDAVersion: str
    lastTestedNVDAVersion: str
    releaseNotes: str
    baseVersion: str = ""
    updateBuild: int = 0
    fileName: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    currentVersion: str
    update: UpdateInfo
    available: bool
    manifestPath: Path
    currentUpdateBuild: int = 0


@dataclass(frozen=True)
class DownloadedUpdate:
    update: UpdateInfo
    path: Path


def _addon_manifest_path() -> Path:
    return _addon_root_path() / "manifest.ini"


def _addon_root_path() -> Path:
    return Path(__file__).resolve().parents[2]


def _build_info_path() -> Path:
    return _addon_root_path() / BUILD_INFO_FILE_NAME


def _strip_manifest_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in ("'", '"'):
        quote = value[0]
        escaped = False
        chars: list[str] = []
        for idx in range(1, len(value)):
            ch = value[idx]
            if escaped:
                chars.append(ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == quote:
                trailing = value[idx + 1 :].lstrip()
                if not trailing or trailing.startswith("#"):
                    return "".join(chars).strip()
                break
            chars.append(ch)
    if "#" in value:
        return value.split("#", 1)[0].strip()
    return value


def current_version() -> str:
    try:
        text = _addon_manifest_path().read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UpdateError("Could not read the installed add-on manifest.") from exc
    match = re.search(r"(?m)^\s*version\s*=\s*(.+?)\s*$", text)
    if not match:
        raise UpdateError("The installed add-on manifest does not contain a version.")
    return _strip_manifest_value(match.group(1))


def _non_negative_int(value: Any, key: str, source: str) -> int:
    if isinstance(value, (bool, float)):
        raise UpdateError(f"The {source} has an invalid {key}.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise UpdateError(f"The {source} has an invalid {key}.") from exc
    if number < 0:
        raise UpdateError(f"The {source} has an invalid {key}.")
    return number


def _read_build_info_json() -> dict[str, Any]:
    try:
        text = _build_info_path().read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise UpdateError("Could not read the installed add-on build information.") from exc
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateError("The installed add-on build information is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise UpdateError("The installed add-on build information is not a JSON object.")
    schema = data.get("schema")
    if schema is not None and (isinstance(schema, bool) or schema != 1):
        raise UpdateError("The installed add-on build information has an unsupported schema.")
    return data


def current_update_build(currentVersion: str | None = None) -> int:
    if currentVersion is None:
        currentVersion = current_version()
    data = _read_build_info_json()
    if not data:
        return 0
    baseVersion = data.get("baseVersion")
    if baseVersion is not None:
        if not isinstance(baseVersion, str) or not baseVersion.strip():
            raise UpdateError("The installed add-on build information has an invalid baseVersion.")
        strippedBaseVersion = baseVersion.strip()
        if strippedBaseVersion != currentVersion:
            try:
                if _is_newer_version(strippedBaseVersion, currentVersion) or _is_newer_version(
                    currentVersion,
                    strippedBaseVersion,
                ):
                    return 0
            except UpdateError:
                return 0
    value = data.get("updateBuild", 0)
    return _non_negative_int(value, "updateBuild", "installed add-on build information")


def _version_parts(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", version))
    if not parts:
        raise UpdateError(f"Version has no numeric parts: {version}")
    return parts


def _version_comparison_key(version: str) -> tuple[tuple[int, ...], int, tuple[tuple[int, int | str], ...]]:
    stripped = version.strip()
    base = stripped.split("+", 1)[0]
    mainPart, sep, prePart = base.partition("-")
    if not sep:
        match = re.match(r"^\s*v?(\d+(?:\.\d+)*)(.*)$", base, flags=re.IGNORECASE)
        if match and match.group(2).strip("._ "):
            mainPart = match.group(1)
            prePart = match.group(2).strip("._ ")
    numericParts = _version_parts(mainPart)
    normalizedPre = prePart.strip()
    if not normalizedPre:
        return (numericParts, 1, ())
    preTokens: list[tuple[int, int | str]] = []
    for token in re.findall(r"\d+|[A-Za-z]+", normalizedPre):
        if token.isdigit():
            preTokens.append((0, int(token)))
        else:
            preTokens.append((1, token.casefold()))
    return (numericParts, 0, tuple(preTokens))


def _is_newer_version(latestVersion: str, currentVersion: str) -> bool:
    latestParts, latestIsRelease, latestPre = _version_comparison_key(latestVersion)
    currentParts, currentIsRelease, currentPre = _version_comparison_key(currentVersion)
    maxLength = max(len(latestParts), len(currentParts))
    latestParts += (0,) * (maxLength - len(latestParts))
    currentParts += (0,) * (maxLength - len(currentParts))
    return (latestParts, latestIsRelease, latestPre) > (currentParts, currentIsRelease, currentPre)


def _is_update_available(update: UpdateInfo, currentVersion: str, currentUpdateBuild: int) -> bool:
    baseVersion = update.baseVersion or update.version
    if _is_newer_version(baseVersion, currentVersion):
        return True
    if _is_newer_version(currentVersion, baseVersion):
        return False
    return update.updateBuild > currentUpdateBuild


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(f"The update manifest is missing {key}.")
    return value.strip()


def _optional_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(f"The update manifest has an invalid {key}.")
    return value.strip()


def _required_size(data: dict[str, Any]) -> int:
    value = data.get("size")
    if isinstance(value, (bool, float)):
        raise UpdateError("The update manifest has an invalid size.")
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise UpdateError("The update manifest has an invalid size.") from exc
    if size <= 0:
        raise UpdateError("The update manifest has an invalid size.")
    return size


def _sha256(data: dict[str, Any]) -> str:
    value = _required_string(data, "sha256").lower()
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise UpdateError("The update manifest has an invalid SHA256 hash.")
    return value


_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _safe_update_file_name(fileName: str) -> str:
    fileName = fileName.strip()
    if (
        not fileName
        or "/" in fileName
        or "\\" in fileName
        or ":" in fileName
        or any(ch in '<>"|?*' for ch in fileName)
        or fileName in (".", "..")
        or any(ord(ch) < 32 for ch in fileName)
    ):
        raise UpdateError("The update manifest has an invalid fileName.")
    if not fileName.lower().endswith(".nvda-addon"):
        raise UpdateError("The update manifest has an invalid fileName.")
    stem = fileName[: -len(".nvda-addon")]
    if not stem or stem.endswith((".", " ")) or stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise UpdateError("The update manifest has an invalid fileName.")
    return fileName


def _update_file_name(data: dict[str, Any], version: str) -> str:
    fileName = _optional_string(data, "fileName")
    if not fileName:
        fileName = f"{ADDON_ID}-{_safe_version_for_file_name(version)}.nvda-addon"
    return _safe_update_file_name(fileName)


def _locale_key(locale: str | None) -> str:
    return str(locale or "").strip().replace("-", "_")


def _release_notes(data: dict[str, Any], locale: str | None) -> str:
    localeKey = _locale_key(locale)
    byLocale = data.get("releaseNotesByLocale")
    if localeKey and isinstance(byLocale, dict):
        value = byLocale.get(localeKey)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = data.get("releaseNotes")
    return value.strip() if isinstance(value, str) else ""


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        reparseFlag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparseFlag:
            st = os.lstat(path)
            if getattr(st, "st_file_attributes", 0) & reparseFlag:
                return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def _ensure_safe_download_dir() -> Path:
    downloadDir = _update_download_dir()
    tempRoot = Path(tempfile.gettempdir())
    try:
        resolvedTempRoot = tempRoot.resolve()
    except OSError:
        resolvedTempRoot = tempRoot
    current = downloadDir
    while current != current.parent:
        if current in (tempRoot, resolvedTempRoot):
            break
        if _is_reparse_or_symlink(current):
            raise UpdateError("Could not use the update download folder because it is a symbolic link or junction.")
        with contextlib.suppress(OSError):
            if current.resolve() == resolvedTempRoot:
                break
        current = current.parent
    downloadDir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_symlink(downloadDir):
        raise UpdateError("Could not use the update download folder because it is a symbolic link or junction.")
    return downloadDir


def _parse_update_info(data: dict[str, Any], locale: str | None) -> UpdateInfo:
    addonId = _required_string(data, "addonId")
    if addonId != ADDON_ID:
        raise UpdateError("The update manifest is for another add-on.")
    channel = data.get("channel")
    if channel is not None and (not isinstance(channel, str) or channel != UPDATE_CHANNEL):
        raise UpdateError("The update manifest is not for the stable channel.")
    url = _required_string(data, "url")
    if urlparse(url).scheme.lower() != "https":
        raise UpdateError("The update download URL must use HTTPS.")
    version = _required_string(data, "version")
    baseVersion = _optional_string(data, "baseVersion") or version
    displayVersion = _optional_string(data, "displayVersion") or version
    return UpdateInfo(
        version=displayVersion,
        url=url,
        size=_required_size(data),
        sha256=_sha256(data),
        minimumNVDAVersion=_required_string(data, "minimumNVDAVersion"),
        lastTestedNVDAVersion=_required_string(data, "lastTestedNVDAVersion"),
        releaseNotes=_release_notes(data, locale),
        baseVersion=baseVersion,
        updateBuild=_non_negative_int(data.get("updateBuild", 0), "updateBuild", "update manifest"),
        fileName=_update_file_name(data, version),
    )


def _response_uses_https(response: Any, fallbackUrl: str) -> bool:
    finalUrl = getattr(response, "geturl", lambda: fallbackUrl)()
    if not isinstance(finalUrl, str) or not finalUrl:
        finalUrl = fallbackUrl
    return urlparse(finalUrl).scheme.lower() == "https"


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject HTTP redirects that downgrade HTTPS update manifest or package URLs."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        resolvedUrl = urljoin(req.full_url, str(newurl))
        if urlparse(resolvedUrl).scheme.lower() != "https":
            raise UpdateError("The update URL must use HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_STDLIB_URLOPEN = urllib.request.urlopen
_HTTPS_OPENER = urllib.request.build_opener(_HttpsOnlyRedirectHandler())


def _open_https_url(request: urllib.request.Request, *, timeout: float) -> Any:
    if urllib.request.urlopen is not _STDLIB_URLOPEN:
        return urllib.request.urlopen(request, timeout=timeout)
    return _HTTPS_OPENER.open(request, timeout=timeout)


def fetch_update_manifest(
    manifestUrl: str = UPDATE_MANIFEST_URL,
    timeout: int = 20,
) -> tuple[dict[str, Any], Path]:
    if urlparse(manifestUrl).scheme.lower() != "https":
        raise UpdateError("The update manifest URL must use HTTPS.")
    manifestPath = _update_manifest_path()
    if not _is_reparse_or_symlink(manifestPath.parent) and not _is_reparse_or_symlink(manifestPath):
        with contextlib.suppress(OSError):
            _remove_file_if_present(manifestPath)
    request = urllib.request.Request(
        manifestUrl,
        headers={
            "Accept": "application/json",
            "User-Agent": f"{ADDON_ID}/{current_version()}",
        },
    )
    try:
        with _open_https_url(request, timeout=timeout) as response:
            if not _response_uses_https(response, manifestUrl):
                raise UpdateError("The update manifest URL must use HTTPS.")
            rawData = response.read(MAX_UPDATE_MANIFEST_BYTES + 1)
    except UpdateError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise UpdateError("Could not download the update manifest.") from exc
    if len(rawData) > MAX_UPDATE_MANIFEST_BYTES:
        raise UpdateError("The update manifest is too large.")
    try:
        data = json.loads(rawData.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("The update manifest is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise UpdateError("The update manifest is not a JSON object.")
    tempPath: Path | None = None
    try:
        downloadDir = _ensure_safe_download_dir()
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=downloadDir,
            prefix=f"{manifestPath.name}.",
            suffix=".tmp",
            delete=False,
        ) as outFile:
            tempPath = Path(outFile.name)
            outFile.write(rawData)
        os.replace(os.fspath(tempPath), os.fspath(manifestPath))
        tempPath = None
    except OSError as exc:
        raise UpdateError("Could not save the update manifest.") from exc
    finally:
        if tempPath is not None:
            with contextlib.suppress(OSError):
                _remove_file_if_present(tempPath)
    return data, manifestPath


def check_for_update(locale: str | None, manifestUrl: str = UPDATE_MANIFEST_URL) -> UpdateCheckResult:
    currentVersion = current_version()
    currentUpdateBuild = current_update_build(currentVersion)
    manifest, manifestPath = fetch_update_manifest(manifestUrl)
    try:
        update = _parse_update_info(manifest, locale)
        available = _is_update_available(update, currentVersion, currentUpdateBuild)
    except Exception:
        with contextlib.suppress(OSError):
            _remove_file_if_present(manifestPath)
        raise
    return UpdateCheckResult(
        currentVersion=currentVersion,
        update=update,
        available=available,
        manifestPath=manifestPath,
        currentUpdateBuild=currentUpdateBuild,
    )


def _safe_version_for_file_name(version: str) -> str:
    safeVersion = re.sub(r"[^A-Za-z0-9._-]+", "_", version).strip("._-")
    return safeVersion or "update"


def _update_download_dir() -> Path:
    return Path(tempfile.gettempdir()) / f"{ADDON_ID}-updates"


def _update_manifest_path() -> Path:
    return _update_download_dir() / "stable.json"


def _download_path_for_update(update: UpdateInfo) -> Path:
    fileName = update.fileName or f"{ADDON_ID}-{_safe_version_for_file_name(update.version)}.nvda-addon"
    return _update_download_dir() / _safe_update_file_name(fileName)


def _remove_file_if_present(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _remove_update_dir_if_empty() -> None:
    try:
        _update_download_dir().rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _raise_if_cancelled(cancel_requested: Callable[[], bool] | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise UpdateCancelled("The update download was cancelled.")


def download_update(
    update: UpdateInfo,
    progress: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    timeout: int = 60,
    on_response: Callable[[Any], None] | None = None,
) -> DownloadedUpdate:
    _raise_if_cancelled(cancel_requested)
    if update.size > MAX_UPDATE_PACKAGE_BYTES:
        raise UpdateError("The update package is too large.")
    parsedUrl = urlparse(update.url)
    if parsedUrl.scheme.lower() != "https":
        raise UpdateError("The update download URL must use HTTPS.")
    try:
        downloadDir = _ensure_safe_download_dir()
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError("Could not create the update download folder.") from exc
    targetPath = _download_path_for_update(update)
    if _is_reparse_or_symlink(targetPath):
        raise UpdateError("Could not prepare the update download file.")
    try:
        _remove_file_if_present(targetPath)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=downloadDir,
            prefix=f"{targetPath.name}.",
            suffix=".download",
            delete=False,
        ) as tempFile:
            partialPath = Path(tempFile.name)
    except OSError as exc:
        raise UpdateError("Could not prepare the update download file.") from exc
    request = urllib.request.Request(
        update.url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"{ADDON_ID}/{current_version()}",
        },
    )
    received = 0
    digest = hashlib.sha256()
    try:
        _raise_if_cancelled(cancel_requested)
        with _open_https_url(request, timeout=timeout) as response, partialPath.open("wb") as outFile:
            if on_response is not None:
                with contextlib.suppress(Exception):
                    on_response(response)
            if not _response_uses_https(response, update.url):
                raise UpdateError("The update download URL must use HTTPS.")
            while True:
                _raise_if_cancelled(cancel_requested)
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                _raise_if_cancelled(cancel_requested)
                if not chunk:
                    break
                received += len(chunk)
                if received > update.size:
                    raise UpdateError("The downloaded update package is larger than expected.")
                outFile.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(received, update.size)
                _raise_if_cancelled(cancel_requested)
    except UpdateError:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        _raise_if_cancelled(cancel_requested)
        raise UpdateError("Could not download the update package.") from exc
    except Exception:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        _raise_if_cancelled(cancel_requested)
        raise
    finally:
        if on_response is not None:
            with contextlib.suppress(Exception):
                on_response(None)
    if received != update.size:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise UpdateError("The downloaded update package size does not match the update manifest.")
    try:
        _raise_if_cancelled(cancel_requested)
    except UpdateCancelled:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise
    if digest.hexdigest().casefold() != update.sha256.casefold():
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise UpdateError("The downloaded update package checksum does not match the update manifest.")
    try:
        _raise_if_cancelled(cancel_requested)
    except UpdateCancelled:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise
    try:
        os.replace(os.fspath(partialPath), os.fspath(targetPath))
    except OSError as exc:
        with contextlib.suppress(OSError):
            _remove_file_if_present(partialPath)
        raise UpdateError("Could not finalize the downloaded update package.") from exc
    try:
        _raise_if_cancelled(cancel_requested)
    except UpdateCancelled:
        with contextlib.suppress(OSError):
            _remove_file_if_present(targetPath)
        raise
    return DownloadedUpdate(update=update, path=targetPath)


def remove_downloaded_update(downloadedUpdate: DownloadedUpdate) -> None:
    with contextlib.suppress(OSError):
        _remove_file_if_present(downloadedUpdate.path)
    _remove_update_dir_if_empty()


def remove_update_manifest(updateCheckResult: UpdateCheckResult) -> None:
    with contextlib.suppress(OSError):
        _remove_file_if_present(updateCheckResult.manifestPath)
    _remove_update_dir_if_empty()


def cleanup_update_files() -> None:
    downloadDir = _update_download_dir()
    try:
        if _is_reparse_or_symlink(downloadDir):
            if os.path.isdir(downloadDir):
                os.rmdir(downloadDir)
            else:
                _remove_file_if_present(downloadDir)
        elif downloadDir.is_dir():
            for entry in list(downloadDir.iterdir()):
                with contextlib.suppress(OSError):
                    if _is_reparse_or_symlink(entry):
                        if os.path.isdir(entry):
                            os.rmdir(entry)
                        else:
                            _remove_file_if_present(entry)
                    elif entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        _remove_file_if_present(entry)
            with contextlib.suppress(OSError):
                downloadDir.rmdir()
        else:
            _remove_file_if_present(downloadDir)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def format_size(size: int) -> str:
    from .uiUtils import format_size_auto

    return format_size_auto(size)

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse
import urllib.request


ADDON_ID = "googleTtsForNvda"
UPDATE_CHANNEL = "stable"
UPDATE_MANIFEST_URL = (
	"https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/releases/latest/download/stable.json"
)
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


@dataclass(frozen=True)
class UpdateCheckResult:
	currentVersion: str
	update: UpdateInfo
	available: bool
	manifestPath: Path


@dataclass(frozen=True)
class DownloadedUpdate:
	update: UpdateInfo
	path: Path


def _addon_manifest_path() -> Path:
	return Path(__file__).resolve().parents[2] / "manifest.ini"


def _strip_manifest_value(value: str) -> str:
	value = value.strip()
	if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
		return value[1:-1].strip()
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


def _version_parts(version: str) -> tuple[int, ...]:
	parts = tuple(int(part) for part in re.findall(r"\d+", version))
	if not parts:
		raise UpdateError(f"Version has no numeric parts: {version}")
	return parts


def _is_newer_version(latestVersion: str, currentVersion: str) -> bool:
	latestParts = _version_parts(latestVersion)
	currentParts = _version_parts(currentVersion)
	maxLength = max(len(latestParts), len(currentParts))
	latestParts += (0,) * (maxLength - len(latestParts))
	currentParts += (0,) * (maxLength - len(currentParts))
	return latestParts > currentParts


def _required_string(data: dict[str, Any], key: str) -> str:
	value = data.get(key)
	if not isinstance(value, str) or not value.strip():
		raise UpdateError(f"The update manifest is missing {key}.")
	return value.strip()


def _required_size(data: dict[str, Any]) -> int:
	value = data.get("size")
	if isinstance(value, bool):
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


def _parse_update_info(data: dict[str, Any], locale: str | None) -> UpdateInfo:
	addonId = _required_string(data, "addonId")
	if addonId != ADDON_ID:
		raise UpdateError("The update manifest is for another add-on.")
	channel = data.get("channel")
	if isinstance(channel, str) and channel and channel != UPDATE_CHANNEL:
		raise UpdateError("The update manifest is not for the stable channel.")
	version = _required_string(data, "version")
	return UpdateInfo(
		version=version,
		url=_required_string(data, "url"),
		size=_required_size(data),
		sha256=_sha256(data),
		minimumNVDAVersion=_required_string(data, "minimumNVDAVersion"),
		lastTestedNVDAVersion=_required_string(data, "lastTestedNVDAVersion"),
		releaseNotes=_release_notes(data, locale),
	)


def fetch_update_manifest(
	manifestUrl: str = UPDATE_MANIFEST_URL,
	timeout: int = 20,
) -> tuple[dict[str, Any], Path]:
	manifestPath = _update_manifest_path()
	try:
		_remove_file_if_present(manifestPath)
	except OSError:
		pass
	request = urllib.request.Request(
		manifestUrl,
		headers={
			"Accept": "application/json",
			"User-Agent": f"{ADDON_ID}/{current_version()}",
		},
	)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			rawData = response.read(MAX_UPDATE_MANIFEST_BYTES + 1)
	except OSError as exc:
		raise UpdateError("Could not download the update manifest.") from exc
	if len(rawData) > MAX_UPDATE_MANIFEST_BYTES:
		raise UpdateError("The update manifest is too large.")
	try:
		data = json.loads(rawData.decode("utf-8-sig"))
	except (UnicodeDecodeError, json.JSONDecodeError) as exc:
		raise UpdateError("The update manifest is not valid JSON.") from exc
	if not isinstance(data, dict):
		raise UpdateError("The update manifest is not a JSON object.")
	try:
		_update_download_dir().mkdir(parents=True, exist_ok=True)
		partialPath = manifestPath.with_name(f"{manifestPath.name}.download")
		_remove_file_if_present(partialPath)
		partialPath.write_bytes(rawData)
		os.replace(os.fspath(partialPath), os.fspath(manifestPath))
	except OSError as exc:
		raise UpdateError("Could not save the update manifest.") from exc
	return data, manifestPath


def check_for_update(locale: str | None, manifestUrl: str = UPDATE_MANIFEST_URL) -> UpdateCheckResult:
	currentVersion = current_version()
	manifest, manifestPath = fetch_update_manifest(manifestUrl)
	try:
		update = _parse_update_info(manifest, locale)
		available = _is_newer_version(update.version, currentVersion)
	except Exception:
		try:
			_remove_file_if_present(manifestPath)
		except OSError:
			pass
		raise
	return UpdateCheckResult(
		currentVersion=currentVersion,
		update=update,
		available=available,
		manifestPath=manifestPath,
	)


def _safe_version_for_file_name(version: str) -> str:
	safeVersion = re.sub(r"[^A-Za-z0-9._-]+", "_", version).strip("._-")
	return safeVersion or "update"


def _update_download_dir() -> Path:
	return Path(tempfile.gettempdir()) / f"{ADDON_ID}-updates"


def _update_manifest_path() -> Path:
	return _update_download_dir() / "stable.json"


def _download_path_for_update(update: UpdateInfo) -> Path:
	return _update_download_dir() / f"{ADDON_ID}-{_safe_version_for_file_name(update.version)}.nvda-addon"


def _remove_file_if_present(path: Path) -> None:
	try:
		path.unlink()
	except FileNotFoundError:
		pass


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
) -> DownloadedUpdate:
	_raise_if_cancelled(cancel_requested)
	if update.size > MAX_UPDATE_PACKAGE_BYTES:
		raise UpdateError("The update package is too large.")
	parsedUrl = urlparse(update.url)
	if parsedUrl.scheme.lower() != "https":
		raise UpdateError("The update download URL must use HTTPS.")
	downloadDir = _update_download_dir()
	try:
		downloadDir.mkdir(parents=True, exist_ok=True)
	except OSError as exc:
		raise UpdateError("Could not create the update download folder.") from exc
	targetPath = _download_path_for_update(update)
	partialPath = targetPath.with_name(f"{targetPath.name}.download")
	try:
		_remove_file_if_present(partialPath)
		_remove_file_if_present(targetPath)
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
		with urllib.request.urlopen(request, timeout=timeout) as response:
			with partialPath.open("wb") as outFile:
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
	except UpdateCancelled:
		try:
			_remove_file_if_present(partialPath)
		except OSError:
			pass
		raise
	except OSError as exc:
		_remove_file_if_present(partialPath)
		raise UpdateError("Could not download the update package.") from exc
	except Exception:
		_remove_file_if_present(partialPath)
		raise
	if received != update.size:
		_remove_file_if_present(partialPath)
		raise UpdateError("The downloaded update package size does not match the update manifest.")
	_raise_if_cancelled(cancel_requested)
	if digest.hexdigest().casefold() != update.sha256.casefold():
		_remove_file_if_present(partialPath)
		raise UpdateError("The downloaded update package checksum does not match the update manifest.")
	_raise_if_cancelled(cancel_requested)
	try:
		os.replace(os.fspath(partialPath), os.fspath(targetPath))
	except OSError as exc:
		_remove_file_if_present(partialPath)
		raise UpdateError("Could not finalize the downloaded update package.") from exc
	try:
		_raise_if_cancelled(cancel_requested)
	except UpdateCancelled:
		try:
			_remove_file_if_present(targetPath)
		except OSError:
			pass
		raise
	return DownloadedUpdate(update=update, path=targetPath)


def remove_downloaded_update(downloadedUpdate: DownloadedUpdate) -> None:
	try:
		_remove_file_if_present(downloadedUpdate.path)
	except OSError:
		pass
	_remove_update_dir_if_empty()


def remove_update_manifest(updateCheckResult: UpdateCheckResult) -> None:
	try:
		_remove_file_if_present(updateCheckResult.manifestPath)
	except OSError:
		pass
	_remove_update_dir_if_empty()


def cleanup_update_files() -> None:
	downloadDir = _update_download_dir()
	if not downloadDir.exists():
		return
	for path in downloadDir.iterdir():
		try:
			if path.is_dir():
				continue
			_remove_file_if_present(path)
		except OSError:
			continue
	_remove_update_dir_if_empty()


def format_size(size: int) -> str:
	if size >= 1024 * 1024:
		return f"{size / (1024 * 1024):.1f} MB"
	if size >= 1024:
		return f"{size / 1024:.1f} KB"
	return f"{size} bytes"

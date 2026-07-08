# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import urllib.request

from .catalog import VoiceCatalog, VoicePackage


ProgressCallback = Callable[[int | None, str], None]

_verifiedPackageCache: dict[str, tuple[int, int]] = {}
_persistentVerifiedPackageCache: dict[str, dict[str, object]] | None = None
_verificationCacheLock = threading.RLock()
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
	root = _default_config_path() / "googleTtsForNvda"
	root.mkdir(parents=True, exist_ok=True)
	return root


def _verification_cache_path() -> Path:
	return data_root() / _VERIFICATION_CACHE_FILE


def voice_dir() -> Path:
	path = data_root() / "voices"
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
		except (OSError, json.JSONDecodeError):
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


def _remember_verified_package(package: VoicePackage, stat: os.stat_result, actualHash: str | None = None) -> None:
	cacheKey = (stat.st_size, stat.st_mtime_ns)
	with _verificationCacheLock:
		_verifiedPackageCache[package.id] = cacheKey
	if not package.sha256Checksum or actualHash is None:
		return
	cache = _load_persistent_verification_cache()
	cache[package.id] = {
		"fileName": package.fileName,
		"size": stat.st_size,
		"mtimeNs": stat.st_mtime_ns,
		"expectedSha256": package.sha256Checksum.lower(),
		"verifiedSha256": actualHash.lower(),
	}
	_save_persistent_verification_cache()


def _forget_verified_package(packageId: str) -> None:
	with _verificationCacheLock:
		_verifiedPackageCache.pop(packageId, None)
		cache = _load_persistent_verification_cache()
		if packageId not in cache:
			return
		cache.pop(packageId, None)
	_save_persistent_verification_cache()


def is_package_installed(package: VoicePackage) -> bool:
	path = package_file(package)
	if not path.is_file():
		_forget_verified_package(package.id)
		return False
	stat = path.stat()
	cacheKey = (stat.st_size, stat.st_mtime_ns)
	if package.compressedSize and stat.st_size != package.compressedSize:
		_forget_verified_package(package.id)
		return False
	with _verificationCacheLock:
		if _verifiedPackageCache.get(package.id) == cacheKey:
			return True
	if _persistent_cache_matches(package, stat):
		with _verificationCacheLock:
			_verifiedPackageCache[package.id] = cacheKey
		return True
	actualHash = sha256(path).lower() if package.sha256Checksum else None
	if actualHash is not None and actualHash != package.sha256Checksum.lower():
		_forget_verified_package(package.id)
		return False
	_remember_verified_package(package, stat, actualHash)
	return True


def physically_installed_packages(catalog: VoiceCatalog) -> list[VoicePackage]:
	return [package for package in catalog.packages if is_package_installed(package)]


def installed_packages(catalog: VoiceCatalog) -> list[VoicePackage]:
	installed = physically_installed_packages(catalog)
	installedIds = {package.id for package in installed}
	return [
		package
		for package in installed
		if not package.dependentVoiceId or package.dependentVoiceId in installedIds
	]


def remove_package(package: VoicePackage) -> None:
	_forget_verified_package(package.id)
	path = package_file(package)
	try:
		path.unlink()
	except FileNotFoundError:
		pass


def download_package(package: VoicePackage, progress: ProgressCallback | None = None) -> Path:
	if is_package_installed(package):
		if progress:
			progress(100, f"{package.id} is already installed.")
		return package_file(package)
	if not package.url:
		raise RuntimeError(f"No download URL is available for {package.id}.")
	target = package_file(package)
	target.parent.mkdir(parents=True, exist_ok=True)
	tmp = target.with_suffix(".download")
	try:
		tmp.unlink()
	except FileNotFoundError:
		pass
	if progress:
		progress(0, f"Downloading {package.id}.")
	request = urllib.request.Request(package.url, headers={"User-Agent": "NVDA Google TTS"})
	with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as output:
		total = int(response.headers.get("Content-Length") or package.compressedSize or 0)
		downloaded = 0
		for chunk in iter(lambda: response.read(1024 * 256), b""):
			if not chunk:
				break
			output.write(chunk)
			downloaded += len(chunk)
			if progress and total:
				progress(min(99, int(downloaded * 100 / total)), f"Downloading {package.id}.")
	if package.compressedSize and tmp.stat().st_size != package.compressedSize:
		tmp.unlink(missing_ok=True)
		raise RuntimeError(f"Downloaded size mismatch for {package.id}.")
	if package.sha256Checksum:
		actualHash = sha256(tmp)
		if actualHash.lower() != package.sha256Checksum.lower():
			tmp.unlink(missing_ok=True)
			raise RuntimeError(f"Downloaded checksum mismatch for {package.id}.")
	else:
		actualHash = None
	os.replace(tmp, target)
	_remember_verified_package(package, target.stat(), actualHash)
	if progress:
		progress(100, f"Installed {package.id}.")
	return target


def copy_existing_package(source: Path, package: VoicePackage) -> Path:
	target = package_file(package)
	target.parent.mkdir(parents=True, exist_ok=True)
	shutil.copy2(source, target)
	_forget_verified_package(package.id)
	if not is_package_installed(package):
		target.unlink(missing_ok=True)
		raise RuntimeError(f"Copied package did not pass verification: {package.id}.")
	return target

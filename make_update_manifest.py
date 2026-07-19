#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


ADDON_ID = "googleTtsForNvda"
DEFAULT_CHANNEL = "stable"
DEFAULT_OUTPUT = "stable.json"
DEFAULT_URL_TEMPLATE = (
	"https://github.com/nguyenanhduc09/Google-TTS-For-NVDA/"
	"releases/download/v{version}/googleTtsForNvda-{version}.nvda-addon"
)
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
TRANSLATED_MANIFEST_RE = re.compile(r"^locale/([^/]+)/manifest\.ini$")
IGNORED_SEARCH_DIRS = {
	".git",
	".hg",
	".svn",
	"__pycache__",
	".mypy_cache",
	".pytest_cache",
	"node_modules",
}


class ManifestError(RuntimeError):
	pass


def _unquote(value: str) -> str:
	value = value.strip()
	if len(value) >= 2 and value[0] == value[-1] == '"':
		return value[1:-1]
	return value


def _parse_manifest(text: str) -> dict[str, str]:
	values: dict[str, str] = {}
	lines = text.splitlines()
	index = 0
	while index < len(lines):
		rawLine = lines[index]
		line = rawLine.strip()
		index += 1
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, rawValue = line.split("=", 1)
		key = key.strip()
		rawValue = rawValue.strip()
		if not key:
			continue
		if rawValue.startswith('"""'):
			valuePart = rawValue[3:]
			parts: list[str] = []
			end = valuePart.find('"""')
			if end >= 0:
				parts.append(valuePart[:end])
			else:
				parts.append(valuePart)
				while index < len(lines):
					nextLine = lines[index]
					index += 1
					end = nextLine.find('"""')
					if end >= 0:
						parts.append(nextLine[:end])
						break
					parts.append(nextLine)
				else:
					raise ManifestError(f"Unterminated triple-quoted value for {key}.")
			values[key] = "\n".join(parts).strip()
			continue
		values[key] = _unquote(rawValue)
	return values


def _read_manifest_from_archive(archive: zipfile.ZipFile, memberName: str) -> dict[str, str]:
	with archive.open(memberName, "r") as manifestFile:
		text = manifestFile.read().decode("utf-8-sig")
	return _parse_manifest(text)


def _read_addon_manifest(addonPath: Path) -> dict[str, str]:
	try:
		with zipfile.ZipFile(addonPath, "r") as archive:
			return _read_manifest_from_archive(archive, "manifest.ini")
	except KeyError as exc:
		raise ManifestError(f"{addonPath} does not contain manifest.ini.") from exc
	except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
		raise ManifestError(f"Could not read {addonPath} as an NVDA add-on package: {exc}") from exc


def _read_release_notes_by_locale(addonPath: Path) -> dict[str, str]:
	notesByLocale: dict[str, str] = {}
	try:
		with zipfile.ZipFile(addonPath, "r") as archive:
			for memberName in sorted(archive.namelist()):
				normalizedName = memberName.replace("\\", "/")
				match = TRANSLATED_MANIFEST_RE.match(normalizedName)
				if match is None:
					continue
				language = match.group(1).strip()
				if not language:
					continue
				try:
					manifest = _read_manifest_from_archive(archive, memberName)
				except (KeyError, UnicodeDecodeError, ManifestError) as exc:
					raise ManifestError(f"Could not read translated manifest {normalizedName}: {exc}") from exc
				changelog = str(manifest.get("changelog") or "").strip()
				if changelog:
					notesByLocale[language] = changelog
	except (OSError, zipfile.BadZipFile) as exc:
		raise ManifestError(f"Could not read translated manifests from {addonPath}: {exc}") from exc
	return notesByLocale


def _sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as stream:
		for chunk in iter(lambda: stream.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
	key: list[tuple[int, int | str]] = []
	for token in re.findall(r"\d+|[A-Za-z]+", version):
		if token.isdigit():
			key.append((0, int(token)))
		else:
			key.append((1, token.lower()))
	return tuple(key)


def _iter_addon_packages(searchDir: Path) -> list[Path]:
	packages: list[Path] = []
	for path in searchDir.rglob("*.nvda-addon"):
		if any(part in IGNORED_SEARCH_DIRS for part in path.parts):
			continue
		packages.append(path)
	return sorted(packages)


def _find_addon_package(searchDir: Path, allowNameMismatch: bool = False) -> Path:
	searchDir = searchDir.resolve()
	if not searchDir.exists():
		raise ManifestError(f"Search directory was not found: {searchDir}")
	if not searchDir.is_dir():
		raise ManifestError(f"Search path is not a directory: {searchDir}")
	candidates: list[tuple[tuple[tuple[int, int | str], ...], float, Path]] = []
	mismatchedNames: list[str] = []
	for packagePath in _iter_addon_packages(searchDir):
		try:
			manifest = _read_addon_manifest(packagePath)
			addonId = _require_manifest_value(manifest, "name")
			version = _require_manifest_value(manifest, "version")
		except ManifestError:
			continue
		if addonId != ADDON_ID or not VERSION_RE.match(version):
			continue
		expectedFileName = f"{ADDON_ID}-{version}.nvda-addon"
		if packagePath.name != expectedFileName and not allowNameMismatch:
			mismatchedNames.append(f"{packagePath} should be named {expectedFileName}")
			continue
		try:
			modifiedTime = packagePath.stat().st_mtime
		except OSError:
			modifiedTime = 0
		candidates.append((_version_sort_key(version), modifiedTime, packagePath))
	if not candidates:
		detail = ""
		if mismatchedNames:
			detail = " Matching packages with unexpected names: " + "; ".join(mismatchedNames)
		raise ManifestError(f"No valid {ADDON_ID} .nvda-addon package was found under {searchDir}.{detail}")
	candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
	return candidates[-1][2]


def _require_manifest_value(manifest: dict[str, str], key: str) -> str:
	value = str(manifest.get(key) or "").strip()
	if not value:
		raise ManifestError(f"manifest.ini is missing {key}.")
	return value


def build_update_manifest(
	addonPath: Path,
	urlTemplate: str,
	channel: str,
	allowNameMismatch: bool = False,
) -> dict[str, object]:
	addonPath = addonPath.resolve()
	if not addonPath.is_file():
		raise ManifestError(f"Add-on package was not found: {addonPath}")
	manifest = _read_addon_manifest(addonPath)
	addonId = _require_manifest_value(manifest, "name")
	if addonId != ADDON_ID:
		raise ManifestError(f"Expected add-on id {ADDON_ID}, found {addonId}.")
	version = _require_manifest_value(manifest, "version")
	if not VERSION_RE.match(version):
		raise ManifestError(f"Version is not safe for a release URL: {version}")
	expectedFileName = f"{ADDON_ID}-{version}.nvda-addon"
	if addonPath.name != expectedFileName and not allowNameMismatch:
		raise ManifestError(
			f"Package file name should be {expectedFileName}, found {addonPath.name}. "
			"Rename the package or pass --allow-name-mismatch."
		)
	try:
		url = urlTemplate.format(version=version, addonId=ADDON_ID, fileName=expectedFileName)
	except (KeyError, IndexError, ValueError) as exc:
		raise ManifestError(f"Invalid URL template: {exc}") from exc
	updateManifest: dict[str, object] = {
		"schema": 1,
		"addonId": addonId,
		"channel": channel,
		"version": version,
		"fileName": expectedFileName,
		"minimumNVDAVersion": _require_manifest_value(manifest, "minimumNVDAVersion"),
		"lastTestedNVDAVersion": _require_manifest_value(manifest, "lastTestedNVDAVersion"),
		"url": url,
		"sha256": _sha256(addonPath),
		"size": addonPath.stat().st_size,
		"releaseNotes": str(manifest.get("changelog") or "").strip(),
	}
	notesByLocale = _read_release_notes_by_locale(addonPath)
	if notesByLocale:
		updateManifest["releaseNotesByLocale"] = notesByLocale
	return updateManifest


def _parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Create stable.json for the Google TTS For NVDA private updater from a built .nvda-addon package."
		)
	)
	parser.add_argument(
		"addon",
		nargs="?",
		type=Path,
		help=(
			"Optional path to the built .nvda-addon package. "
			"If omitted, the script auto-detects the highest-version package under --search-dir."
		),
	)
	parser.add_argument(
		"--search-dir",
		"--dist-dir",
		dest="search_dir",
		default=Path("."),
		type=Path,
		help="Directory to scan recursively when addon is omitted. Default: current directory",
	)
	parser.add_argument(
		"--output",
		default=Path(DEFAULT_OUTPUT),
		type=Path,
		help=f"Output update manifest path. Default: {DEFAULT_OUTPUT}",
	)
	parser.add_argument(
		"--channel",
		default=DEFAULT_CHANNEL,
		help=f"Update channel written to the manifest. Default: {DEFAULT_CHANNEL}",
	)
	parser.add_argument(
		"--url-template",
		default=DEFAULT_URL_TEMPLATE,
		help=(
			"Download URL template. Available fields: {version}, {addonId}, {fileName}. "
			f"Default: {DEFAULT_URL_TEMPLATE}"
		),
	)
	parser.add_argument(
		"--allow-name-mismatch",
		action="store_true",
		help="Do not fail if the package file name does not match googleTtsForNvda-{version}.nvda-addon.",
	)
	return parser.parse_args(argv)


def main(argv: list[str]) -> int:
	args = _parse_args(argv)
	try:
		addonPath = args.addon or _find_addon_package(args.search_dir, args.allow_name_mismatch)
		updateManifest = build_update_manifest(
			addonPath=addonPath,
			urlTemplate=args.url_template,
			channel=args.channel,
			allowNameMismatch=args.allow_name_mismatch,
		)
		outputPath = args.output.resolve()
		outputPath.parent.mkdir(parents=True, exist_ok=True)
		outputPath.write_text(
			json.dumps(updateManifest, ensure_ascii=False, indent=2) + "\n",
			encoding="utf-8",
		)
	except (ManifestError, OSError) as exc:
		print(f"[ERROR] {exc}", file=sys.stderr)
		return 1
	print(f"Created {outputPath}")
	print(f"Package: {addonPath.resolve()}")
	print(f"Version: {updateManifest['version']}")
	print(f"Size: {updateManifest['size']} bytes")
	print(f"SHA256: {updateManifest['sha256']}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv[1:]))

# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
ENGINE_VERSION = "20260709.1"
ENGINE_ROOT = BASE_DIR / "WasmTtsEngine"
ENGINE_DIR = ENGINE_ROOT / ENGINE_VERSION
CATALOG_PATH = ENGINE_DIR / "voices.json"
REQUIRED_ENGINE_FILES = (
	"bindings_main.js",
	"bindings_main.wasm",
	"manifest.json",
	"offscreen_compiled.js",
	"streaming_worklet_processor.js",
	"voices.json",
)
# The bundled engine reports these package families as unavailable even when their .zvoice files verify.
UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS = ("locomel", "lemonbalm")


class EngineLibraryError(RuntimeError):
	def __init__(
		self,
		kind: str,
		*,
		supportedVersion: str = ENGINE_VERSION,
		foundVersions: tuple[str, ...] = (),
		missingFiles: tuple[str, ...] = (),
		technicalDetail: str = "",
	) -> None:
		super().__init__(technicalDetail or kind)
		self.kind = kind
		self.supportedVersion = supportedVersion
		self.foundVersions = foundVersions
		self.missingFiles = missingFiles
		self.technicalDetail = technicalDetail or kind


@dataclass(frozen=True)
class VoicePackage:
	id: str
	fileId: str
	url: str
	sha256Checksum: str
	compressedSize: int
	remote: bool
	speakers: tuple[dict[str, str], ...]
	dependentVoiceId: str = ""

	@property
	def fileName(self) -> str:
		return f"{self.id}.zvoice"

	@property
	def language(self) -> str:
		return package_id_to_language(self.id)

	@property
	def displayName(self) -> str:
		return f"{self.language} ({len(self.speakers)} voices)"


@dataclass(frozen=True)
class Speaker:
	id: str
	name: str
	language: str
	packageId: str
	speaker: str
	gender: str


def package_id_to_language(packageId: str) -> str:
	match = re.match(r"^([a-z]{2,3})-([a-z]{2})(?:-|$)", packageId, re.I)
	if not match:
		return packageId
	return f"{match.group(1).lower()}-{match.group(2).upper()}"


def is_package_supported_by_engine(package: "VoicePackage") -> bool:
	packageId = package.id.lower()
	return not any(part in packageId for part in UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS)


def _safe_str(value: Any, default: str = "") -> str:
	if value is None:
		return default
	return str(value)


def inspect_engine_library() -> None:
	if not ENGINE_ROOT.is_dir():
		raise EngineLibraryError(
			"missing",
			technicalDetail=f"WASM TTS Engine folder was not found: {ENGINE_ROOT}",
		)
	foundVersions = tuple(sorted(child.name for child in ENGINE_ROOT.iterdir() if child.is_dir()))
	if not foundVersions:
		raise EngineLibraryError(
			"missing",
			technicalDetail=f"WASM TTS Engine folder is empty: {ENGINE_ROOT}",
		)
	if not ENGINE_DIR.is_dir():
		raise EngineLibraryError(
			"unsupportedVersion",
			foundVersions=foundVersions,
			technicalDetail=(
				f"Supported WASM TTS Engine version {ENGINE_VERSION} was not found. "
				f"Found versions: {', '.join(foundVersions)}"
			),
		)
	missingFiles = tuple(name for name in REQUIRED_ENGINE_FILES if not (ENGINE_DIR / name).is_file())
	if missingFiles:
		raise EngineLibraryError(
			"incomplete",
			foundVersions=foundVersions,
			missingFiles=missingFiles,
			technicalDetail=f"WASM TTS Engine is missing files: {', '.join(missingFiles)}",
		)
	try:
		manifest = json.loads((ENGINE_DIR / "manifest.json").read_text(encoding="utf-8"))
		manifestVersion = str(manifest.get("version") or "").strip() if isinstance(manifest, dict) else ""
	except (OSError, json.JSONDecodeError) as exc:
		raise EngineLibraryError(
			"incomplete",
			foundVersions=foundVersions,
			technicalDetail=f"WASM TTS Engine manifest could not be read: {exc}",
		) from exc
	if manifestVersion != ENGINE_VERSION:
		raise EngineLibraryError(
			"unsupportedVersion",
			foundVersions=(manifestVersion or "unknown",),
			technicalDetail=(
				f"WASM TTS Engine manifest version {manifestVersion or 'unknown'} "
				f"does not match supported version {ENGINE_VERSION}."
			),
		)


class VoiceCatalog:
	def __init__(self, packages: list[VoicePackage]) -> None:
		self.packages = sorted(packages, key=lambda pkg: (pkg.language.lower(), pkg.id.lower()))
		self._packageById = {package.id: package for package in self.packages}
		self.speakers = self._build_speakers()
		self._speakerById = {speaker.id: speaker for speaker in self.speakers}

	@classmethod
	def load(cls, path: Path | None = None) -> "VoiceCatalog":
		catalogPath = path or CATALOG_PATH
		if path is None:
			inspect_engine_library()
		try:
			raw = json.loads(catalogPath.read_text(encoding="utf-8"))
		except OSError as exc:
			if path is None:
				raise EngineLibraryError(
					"invalidCatalog",
					technicalDetail=f"WASM TTS Engine voice catalog could not be opened: {exc}",
				) from exc
			raise
		except json.JSONDecodeError as exc:
			if path is None:
				raise EngineLibraryError(
					"invalidCatalog",
					technicalDetail=f"WASM TTS Engine voice catalog could not be read: {exc}",
				) from exc
			raise
		packages: list[VoicePackage] = []
		for item in raw:
			if not isinstance(item, dict):
				continue
			speakers = item.get("speakers")
			if not isinstance(speakers, list):
				speakers = []
			packages.append(
				VoicePackage(
					id=_safe_str(item.get("id")),
					fileId=_safe_str(item.get("fileId")),
					url=_safe_str(item.get("url")),
					sha256Checksum=_safe_str(item.get("sha256Checksum")),
					compressedSize=int(item.get("compressedSize") or 0),
					remote=bool(item.get("remote", True)),
					speakers=tuple(s for s in speakers if isinstance(s, dict)),
					dependentVoiceId=_safe_str(item.get("dependentVoiceId")),
				),
			)
		return cls(packages)

	def _build_speakers(self) -> list[Speaker]:
		speakers: list[Speaker] = []
		seen: set[str] = set()
		for package in self.packages:
			for rawSpeaker in package.speakers:
				speakerCode = _safe_str(rawSpeaker.get("speaker"))
				name = _safe_str(rawSpeaker.get("name"), speakerCode or package.id)
				gender = _safe_str(rawSpeaker.get("gender"))
				speakerId = f"{package.id}:{speakerCode or name}"
				if speakerId in seen:
					continue
				seen.add(speakerId)
				speakers.append(
					Speaker(
						id=speakerId,
						name=name,
						language=package.language,
						packageId=package.id,
						speaker=speakerCode,
						gender=gender,
					),
				)
		return speakers

	def package_for_voice(self, voiceId: str) -> VoicePackage:
		speaker = self._speakerById[voiceId]
		return self._packageById[speaker.packageId]

	def speaker_for_voice(self, voiceId: str) -> Speaker:
		return self._speakerById[voiceId]

	def package_by_id(self, packageId: str) -> VoicePackage:
		return self._packageById[packageId]

	def language_for_voice(self, voiceId: str) -> str:
		return self._speakerById[voiceId].language

	def to_runtime_json(self) -> str:
		runtimePackages: list[dict[str, Any]] = []
		for package in self.packages:
			runtimePackages.append(
				{
					"id": package.id,
					"fileId": package.fileId,
					"url": f"/{package.fileName}",
					"sha256Checksum": package.sha256Checksum,
					"compressedSize": package.compressedSize,
					"speakers": list(package.speakers),
					"remote": False,
				},
			)
			if package.dependentVoiceId:
				runtimePackages[-1]["dependentVoiceId"] = package.dependentVoiceId
		return json.dumps(runtimePackages, ensure_ascii=False)

	def voices_by_language(self) -> "OrderedDict[str, list[Speaker]]":
		grouped: OrderedDict[str, list[Speaker]] = OrderedDict()
		for speaker in self.speakers:
			grouped.setdefault(speaker.language, []).append(speaker)
		return grouped

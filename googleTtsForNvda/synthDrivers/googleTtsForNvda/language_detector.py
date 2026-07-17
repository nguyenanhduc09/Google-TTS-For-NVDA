# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import threading

try:
	from logHandler import log
except Exception:  # pragma: no cover - NVDA is not available in local unit checks.
	log = None


_DLL_DIR = Path(__file__).with_name("cld2")
_DLL_NAMES = ("cld2_x64.dll", "cld2.dll") if ctypes.sizeof(ctypes.c_void_p) == 8 else ("cld2_x86.dll", "cld2.dll")
_MIN_RELIABLE_PERCENT = 50


@dataclass(frozen=True)
class DetectionResult:
	language: str
	percent: int
	textBytes: int
	isReliable: bool


class _Cld2Detector:
	def __init__(self) -> None:
		self._lock = threading.RLock()
		self._library: ctypes.CDLL | None = None
		self._loadAttempted = False
		self._loadErrorLogged = False

	def detect(self, text: str) -> DetectionResult | None:
		if not text:
			return None
		library = self._load_library()
		if library is None:
			return None
		encodedText = text.encode("utf-8", "replace")
		if not encodedText:
			return None
		languageCode = ctypes.create_string_buffer(16)
		percent = ctypes.c_int()
		textBytes = ctypes.c_int()
		isReliable = ctypes.c_int()
		try:
			result = library.cld2_detect_language(
				encodedText,
				len(encodedText),
				languageCode,
				len(languageCode),
				ctypes.byref(percent),
				ctypes.byref(textBytes),
				ctypes.byref(isReliable),
			)
		except Exception:
			if log is not None:
				log.debug("Could not detect language with CLD2.", exc_info=True)
			return None
		if not result:
			return None
		language = languageCode.value.decode("ascii", "replace").strip()
		if not language:
			return None
		return DetectionResult(
			language=language,
			percent=max(0, min(100, int(percent.value))),
			textBytes=max(0, int(textBytes.value)),
			isReliable=bool(isReliable.value),
		)

	def _load_library(self) -> ctypes.CDLL | None:
		with self._lock:
			if self._library is not None:
				return self._library
			if self._loadAttempted:
				return None
			self._loadAttempted = True
			try:
				for dllName in _DLL_NAMES:
					dllPath = _DLL_DIR / dllName
					try:
						library = ctypes.CDLL(str(dllPath))
						library.cld2_detect_language.argtypes = [
							ctypes.c_char_p,
							ctypes.c_int,
							ctypes.c_char_p,
							ctypes.c_int,
							ctypes.POINTER(ctypes.c_int),
							ctypes.POINTER(ctypes.c_int),
							ctypes.POINTER(ctypes.c_int),
						]
						library.cld2_detect_language.restype = ctypes.c_int
						self._library = library
						return self._library
					except Exception:
						if log is not None and not self._loadErrorLogged:
							log.debug("Could not load CLD2 language detector from %s.", dllPath, exc_info=True)
				return None
			finally:
				if self._library is None:
					self._loadErrorLogged = True


_detector = _Cld2Detector()


def detect_language(text: str, candidateLanguages: list[str]) -> str | None:
	result = _detector.detect(text)
	if result is None:
		return None
	if not result.isReliable or result.percent < _MIN_RELIABLE_PERCENT:
		return None
	return _candidate_for_language(result.language, candidateLanguages)


def _candidate_for_language(language: str, candidateLanguages: list[str]) -> str | None:
	languageKey = _normalize_language(language)
	if not languageKey:
		return None
	for candidate in candidateLanguages:
		if _normalize_language(candidate) == languageKey:
			return candidate
	languageRoot = languageKey.split("-", 1)[0]
	for candidate in candidateLanguages:
		if _normalize_language(candidate).split("-", 1)[0] == languageRoot:
			return candidate
	return None


def _normalize_language(language: str | None) -> str:
	return str(language or "").strip().replace("_", "-").lower()

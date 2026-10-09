from __future__ import annotations

import ctypes
import functools
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:
    from logHandler import log
except Exception:  # pragma: no cover - NVDA is not available in local unit checks.
    log = None

# Shared sentinel and attribute name used by both the synth driver and the
# global plugin to attach / retrieve a Google TTS language tag on NVDA
# LangChangeCommand objects.  Defined once here so both modules use the
# same object identity for ``is`` checks.
GOOGLE_TTS_LANG_CHANGE_ATTR: str = "googleTtsForNvdaLanguage"
MISSING_GOOGLE_TTS_LANGUAGE: object = object()


_DLL_DIR = Path(__file__).with_name("cld2")
_DLL_NAMES = ("cld2_x64.dll", "cld2.dll") if ctypes.sizeof(ctypes.c_void_p) == 8 else ("cld2_x86.dll", "cld2.dll")
_MIN_RELIABLE_PERCENT = 30
_LANGUAGE_ALIASES = {
    "ar": {"ar-xa"},
    "ar-xa": {"ar"},
    "cmn": {"zh"},
    "cmn-cn": {"zh-cn", "zh-hans"},
    "cmn-tw": {"zh-hant", "zh-tw"},
    "fil": {"tl", "fil-ph"},
    "fil-ph": {"fil", "tl"},
    "gom": {"kok", "kok-in"},
    "he": {"iw", "he-il"},
    "he-il": {"he", "iw"},
    "id": {"in", "id-id"},
    "in": {"id", "id-id"},
    "iw": {"he", "he-il"},
    "jv": {"jw", "jv-id"},
    "jv-id": {"jv", "jw"},
    "jw": {"jv", "jv-id"},
    "kok": {"gom", "kok-in"},
    "kok-in": {"kok", "gom"},
    "mni": {"mni-in", "mni-mtei"},
    "mni-mtei": {"mni", "mni-in"},
    "nb": {"no", "nn", "nb-no"},
    "nb-no": {"nb", "no", "nn"},
    "nn": {"nb", "nb-no", "no"},
    "no": {"nb", "nb-no", "nn"},
    "or": {"ory", "or-in"},
    "ory": {"or", "or-in"},
    "pa": {"pan", "pa-in"},
    "pan": {"pa", "pa-in"},
    "sat": {"sat-in", "sat-olck"},
    "sat-olck": {"sat", "sat-in"},
    "sr": {"sr-rs", "sr-cyrl", "sr-latn"},
    "sr-cyrl": {"sr", "sr-rs"},
    "sr-latn": {"sr", "sr-rs"},
    "tl": {"fil", "fil-ph"},
    "yue": {"yue-hk"},
    "yue-hk": {"yue", "zh-hant", "zh-hk"},
    "zh": {"cmn-cn", "cmn-tw", "yue-hk"},
    "zh-cn": {"cmn-cn", "zh-hans"},
    "zh-hans": {"cmn-cn", "zh-cn"},
    "zh-hant": {"cmn-tw", "yue-hk", "zh-hk", "zh-tw"},
    "zh-hk": {"yue-hk", "zh-hant"},
    "zh-tw": {"cmn-tw", "zh-hant"},
}
_CHINESE_LANGUAGE_ROOTS = {"cmn", "yue", "zh"}
# Specific dialect → best supported dialect redirects.
# Only dialects that are commonly requested but not always available need entries.
_LANGUAGE_REDIRECTS: dict[str, str] = {
    "fr-ca": "fr-fr",
    "fr-be": "fr-fr",
    "fr-ch": "fr-fr",
    "fr-lu": "fr-fr",
    "fr-mc": "fr-fr",
    "pt-pt": "pt-br",
    "pt-ao": "pt-br",
    "pt-mz": "pt-br",
    "es-es": "es-mx",
    "es-ar": "es-mx",
    "es-co": "es-mx",
    "es-cl": "es-mx",
    "es-ve": "es-mx",
    "es-pe": "es-mx",
    "es-ec": "es-mx",
    "de-at": "de-de",
    "de-ch": "de-de",
    "en-gb": "en-us",
    "en-au": "en-us",
    "en-in": "en-us",
    "en-za": "en-us",
    "en-nz": "en-us",
    "it-ch": "it-it",
}


def redirect_language(language: str | None, available_languages: set[str]) -> str | None:
    """Redirect an unsupported locale to the best available alternative.

    When a specific locale is not available, return the most appropriate
    supported locale.
    Returns None if no redirect is needed (language is already available)
    or no suitable redirect exists.
    """
    if not language:
        return None
    key = language.strip().replace("_", "-").lower()
    if key in available_languages:
        return None
    # Check explicit redirects first.
    redirect = _LANGUAGE_REDIRECTS.get(key)
    if redirect and redirect in available_languages:
        return redirect
    # Try root language match (e.g., fr-CA → fr-FR).
    root = key.split("-", 1)[0]
    for available in available_languages:
        if available.split("-", 1)[0] == root:
            return available
    return None


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
        if not text.isascii():
            text = unicodedata.normalize("NFC", text)
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


def detect_language(
    text: str,
    candidateLanguages: list[str],
    preferredLanguage: str | None = None,
) -> str | None:
    """Detect language for text restricted by candidate language hints and preferred language.

    Applies Mathematical Alphanumeric normalization, candidate language prior hints,
    preferred language routing for single characters and spelling clauses, and Unicode script/diacritic analysis.
    """
    if not text:
        return None
    from .language_profiles import (
        _LATIN_NON_ASCII_EXEMPLARS,
        get_character_script,
        has_language_words,
        is_single_grapheme_token,
        is_spelling_only_text,
        language_script_signal,
        latin_exemplar_signal,
        normalize_mathematical_alphanumeric,
    )
    from .unicode_data import SUPPORTED_LANGUAGE_SCRIPTS

    normalizedText = normalize_mathematical_alphanumeric(text)
    stripped = normalizedText.strip()
    if not stripped:
        return None

    candidateRoots = list(dict.fromkeys(_language_root(c) for c in candidateLanguages))

    # Single character reading and alphabet spelling clauses route to preferred language
    if preferredLanguage:
        if is_single_grapheme_token(stripped):
            scriptSignal = language_script_signal(stripped, candidateRoots)
            prefRoot = _language_root(preferredLanguage)
            if scriptSignal and scriptSignal != prefRoot:
                return _candidate_for_language(scriptSignal, candidateLanguages)
            if not stripped.isascii():
                exemplarSignal = latin_exemplar_signal(stripped, candidateRoots)
                if (
                    exemplarSignal
                    and exemplarSignal != prefRoot
                    and stripped.lower() not in _LATIN_NON_ASCII_EXEMPLARS.get(prefRoot, frozenset())
                ):
                    return _candidate_for_language(exemplarSignal, candidateLanguages)
            return _candidate_for_language(preferredLanguage, candidateLanguages) or preferredLanguage
        if is_spelling_only_text(stripped):
            return _candidate_for_language(preferredLanguage, candidateLanguages) or preferredLanguage

    # Text without language words (e.g. pure numbers, units, currencies, times, symbols, or emojis)
    # has no intrinsic language signal: return preferredLanguage fallback if provided, or None.
    if not has_language_words(stripped):
        if preferredLanguage:
            return _candidate_for_language(preferredLanguage, candidateLanguages) or preferredLanguage
        return None

    # CLD2 statistical detection
    result = _detector.detect(normalizedText)
    if result is not None:
        candidate = _candidate_for_language(result.language, candidateLanguages)
        if candidate is not None and (result.isReliable or result.percent >= _MIN_RELIABLE_PERCENT):
            return candidate
        # Language hint recovery: CLD2 predicted a language outside the candidate list
        # (e.g. ceb, gl, id, ms, la, pt on short Latin text without diacritics).
        # Restrict the prior to the candidate language space.
        cldRoot = _language_root(result.language)
        hasLatinChars = any(get_character_script(ord(c)) == "Latin" for c in normalizedText if c.isalpha())
        if hasLatinChars or "Latin" in SUPPORTED_LANGUAGE_SCRIPTS.get(cldRoot, ()):
            exemplarWinner = latin_exemplar_signal(normalizedText, candidateRoots)
            if exemplarWinner:
                return _candidate_for_language(exemplarWinner, candidateLanguages)
            if "en" in candidateRoots and any(c.isalpha() and ord(c) < 128 for c in normalizedText):
                return _candidate_for_language("en", candidateLanguages)
            latinCands = [
                c for c in candidateLanguages if "Latin" in SUPPORTED_LANGUAGE_SCRIPTS.get(_language_root(c), ())
            ]
            if len(latinCands) == 1:
                return latinCands[0]

    # Non-Latin script fallback via Unicode script ranges
    scriptSignal = language_script_signal(normalizedText, candidateRoots)
    if scriptSignal:
        return _candidate_for_language(scriptSignal, candidateLanguages)

    # Latin exemplar & plain Latin heuristics derived from CLDR exemplar sets
    exemplarWinner = latin_exemplar_signal(normalizedText, candidateRoots)
    if exemplarWinner:
        return _candidate_for_language(exemplarWinner, candidateLanguages)
    if "en" in candidateRoots and any(c.isalpha() and ord(c) < 128 for c in normalizedText):
        return _candidate_for_language("en", candidateLanguages)

    latinCandidates = [
        c for c in candidateLanguages if "Latin" in SUPPORTED_LANGUAGE_SCRIPTS.get(_language_root(c), ())
    ]
    if len(latinCandidates) == 1:
        return latinCandidates[0]

    if preferredLanguage:
        return _candidate_for_language(preferredLanguage, candidateLanguages) or preferredLanguage

    return None


def _candidate_for_language(language: str, candidateLanguages: list[str]) -> str | None:
    languageKey = _normalize_language(language)
    if not languageKey:
        return None
    for candidate in candidateLanguages:
        if _normalize_language(candidate) == languageKey:
            return candidate
    languageAliases = _language_aliases(languageKey)
    for candidate in candidateLanguages:
        if _normalize_language(candidate) in languageAliases:
            return candidate
    languageRoot = _language_root(languageKey)
    for candidate in candidateLanguages:
        if _language_root(candidate) == languageRoot:
            return candidate
    if _language_family(languageKey) == "zh":
        for candidate in candidateLanguages:
            if _language_family(candidate) == "zh":
                return candidate
    return None


@functools.lru_cache(maxsize=128)
def _language_match_keys_cached(languageKey: str) -> frozenset[str]:
    if not languageKey:
        return frozenset()
    aliases = {languageKey}
    aliases.update(_LANGUAGE_ALIASES.get(languageKey, set()))
    root = languageKey.split("-", 1)[0]
    aliases.add(root)
    aliases.update(_LANGUAGE_ALIASES.get(root, set()))
    if root in _CHINESE_LANGUAGE_ROOTS:
        aliases.update(_CHINESE_LANGUAGE_ROOTS)
    return frozenset(aliases)


def language_match_keys(language: str | None) -> set[str]:
    languageKey = _normalize_language(language)
    if not languageKey:
        return set()
    return set(_language_match_keys_cached(languageKey))


def _language_aliases(languageKey: str) -> set[str]:
    return set(_LANGUAGE_ALIASES.get(languageKey, set()))


def _language_family(language: str | None) -> str:
    root = _language_root(language)
    if root in _CHINESE_LANGUAGE_ROOTS:
        return "zh"
    return root


def _language_root(language: str | None) -> str:
    return _normalize_language(language).split("-", 1)[0]


def _normalize_language(language: str | None) -> str:
    return str(language or "").strip().replace("_", "-").lower()


def language_matches(left: str | None, right: str | None) -> bool:
    """Return True when two language tags refer to the same language.

    Uses aliases, root-language matching, and Chinese-family merging so that
    regional variants (fr-FR, fr-CA) and CLDR aliases (fil, tl) are recognised
    as the same language when appropriate.
    """
    leftKeys = language_match_keys(left)
    rightKeys = language_match_keys(right)
    return bool(leftKeys and rightKeys and leftKeys.intersection(rightKeys))

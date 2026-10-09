"""Language and locale normalization helpers for Google TTS For NVDA."""

from __future__ import annotations

import functools
import os

SPECIAL_NVDA_LOCALES: dict[str, str] = {
    "cmn-CN": "zh_CN",
    "cmn-TW": "zh_TW",
    "yue-HK": "zh_HK",
    "ar-XA": "ar",
    "fil-PH": "tl",
}
_SPECIAL_NVDA_LOCALES_LOWER: dict[str, str] = {k.lower(): v for k, v in SPECIAL_NVDA_LOCALES.items()}


def normalize_language(language: str | None) -> str:
    """Normalize a language tag to lower-case with hyphens (e.g. 'vi_vn' -> 'vi-vn')."""
    return str(language or "").strip().replace("_", "-").lower()


def normalize_language_code(language: str | None) -> str:
    """Normalize a language tag preserving case with hyphens (e.g. 'vi_VN' -> 'vi-VN')."""
    return str(language or "").strip().replace("_", "-")


def normalize_language_key(language: str | None) -> str:
    """Alias for normalize_language for dictionary keys."""
    return normalize_language(language)


def get_nvda_locale_for_language(lang_code: str | None) -> str:
    """Convert a language tag into a valid NVDA locale code (e.g. 'cmn-CN' -> 'zh_CN')."""
    if not lang_code:
        return ""
    languageText = str(lang_code).strip()
    if languageText in SPECIAL_NVDA_LOCALES:
        return SPECIAL_NVDA_LOCALES[languageText]
    lowerLanguage = languageText.replace("_", "-").lower()
    if lowerLanguage in _SPECIAL_NVDA_LOCALES_LOWER:
        return _SPECIAL_NVDA_LOCALES_LOWER[lowerLanguage]
    if lowerLanguage.startswith(("cmn-tw", "cmn-hant")):
        return "zh_TW"
    if lowerLanguage.startswith("cmn"):
        return "zh_CN"
    if lowerLanguage.startswith("yue"):
        return "zh_HK"
    try:
        import languageHandler

        normalized = languageHandler.normalizeLanguage(languageText)
    except Exception:
        normalized = languageText.replace("-", "_")
    return str(normalized or "").strip()


@functools.lru_cache(maxsize=64)
def nvda_locale_exists(locale: str) -> bool:
    """Return True if the given locale exists in NVDA's installation directory."""
    try:
        import globalVars

        return os.path.isdir(os.path.join(globalVars.appDir, "locale", locale))
    except Exception:
        return False


def resolve_nvda_locale(language: str | None) -> str:
    """Resolve the effective NVDA locale for speech messages or fall back to 'en'."""
    nvdaLocale = get_nvda_locale_for_language(language)
    if not nvdaLocale:
        return "en"
    if nvda_locale_exists(nvdaLocale):
        return nvdaLocale
    rootLocale = nvdaLocale.split("_", 1)[0]
    if rootLocale != nvdaLocale and nvda_locale_exists(rootLocale):
        return rootLocale
    return "en"


@functools.lru_cache(maxsize=128)
def _language_display_candidates(lang_code: str) -> tuple[str, ...]:
    nvdaLocale = get_nvda_locale_for_language(lang_code)
    candidates: list[str] = []
    for candidate in (nvdaLocale, nvdaLocale.split("_", 1)[0] if "_" in nvdaLocale else "", lang_code):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def get_language_display_name(
    lang_code: str,
    custom_language_names: dict[str, str] | None = None,
) -> str:
    """Return a human-readable language description."""
    if custom_language_names:
        normalized = str(lang_code or "").strip().replace("_", "-").lower()
        for code, name in custom_language_names.items():
            if code.lower() == normalized:
                return name
    for candidate in _language_display_candidates(lang_code):
        try:
            import languageHandler

            description = languageHandler.getLanguageDescription(candidate)
        except Exception:
            description = None
        if description:
            return description
    return lang_code

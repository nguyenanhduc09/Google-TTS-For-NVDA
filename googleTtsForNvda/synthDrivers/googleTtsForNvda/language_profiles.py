"""Pure helpers for Unicode-script language-profile fallback.

This module intentionally has no NVDA imports so the same production logic can
be exercised by the standalone test suite.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection

from .unicode_data import (
    LANGUAGE_SCRIPT_RANGES,
    NORMALIZATION_TABLE,
    SCRIPT_RANGES,
    SUPPORTED_LANGUAGE_SCRIPTS,
)

ScriptRanges = tuple[tuple[int, int], ...]

LANGUAGE_WORD_RE = re.compile(r"[^\W\d_]+(?:['’_-][^\W\d_]+)?", re.UNICODE)
VIETNAMESE_LETTERS = set("ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
VIETNAMESE_WORDS = {
    "anh",
    "ban",
    "bạn",
    "bao",
    "bi",
    "bị",
    "bo",
    "bỏ",
    "cai",
    "cái",
    "cac",
    "các",
    "can",
    "cần",
    "cau",
    "câu",
    "cho",
    "co",
    "có",
    "con",
    "cua",
    "của",
    "cung",
    "cùng",
    "dang",
    "đang",
    "de",
    "để",
    "den",
    "đến",
    "di",
    "đi",
    "do",
    "đó",
    "duoc",
    "được",
    "hay",
    "hon",
    "hơn",
    "khi",
    "khong",
    "không",
    "la",
    "là",
    "lam",
    "làm",
    "len",
    "lên",
    "mot",
    "một",
    "nay",
    "này",
    "neu",
    "nếu",
    "nguoi",
    "người",
    "nhung",
    "những",
    "o",
    "ở",
    "qua",
    "ra",
    "rang",
    "rằng",
    "roi",
    "rồi",
    "sau",
    "se",
    "sẽ",
    "thi",
    "thì",
    "toi",
    "tôi",
    "trong",
    "tu",
    "từ",
    "va",
    "và",
    "vao",
    "vào",
    "ve",
    "về",
    "vi",
    "vì",
    "voi",
    "với",
}
ENGLISH_WORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "between",
    "brave",
    "browser",
    "but",
    "by",
    "can",
    "chrome",
    "click",
    "could",
    "did",
    "do",
    "does",
    "download",
    "edge",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "install",
    "is",
    "it",
    "language",
    "more",
    "not",
    "of",
    "on",
    "open",
    "or",
    "package",
    "press",
    "runtime",
    "select",
    "settings",
    "speech",
    "than",
    "that",
    "the",
    "then",
    "there",
    "this",
    "to",
    "use",
    "voice",
    "was",
    "were",
    "when",
    "will",
    "with",
    "you",
    "your",
}


def script_ranges_for_language_root(root: str) -> ScriptRanges:
    return LANGUAGE_SCRIPT_RANGES.get(root, ())


def token_has_character_in_ranges(token: str, ranges: ScriptRanges) -> bool:
    return any(start <= ord(character) <= end for character in token for start, end in ranges)


def language_script_signal(token: str, candidateRoots: Collection[str]) -> str | None:
    """Return the only candidate root whose generated script ranges match."""
    matchingRoots = {
        root
        for root in candidateRoots
        if (ranges := script_ranges_for_language_root(root)) and token_has_character_in_ranges(token, ranges)
    }
    if len(matchingRoots) == 1:
        return next(iter(matchingRoots))
    return None


def language_token_signal(
    token: str,
    candidateRoots: Collection[str],
    is_url_token: Callable[[str], bool] | None = None,
) -> tuple[str | None, int]:
    normalized = token.strip("'’_-").casefold()
    if not normalized or (is_url_token is not None and is_url_token(normalized)):
        return None, 0
    scriptRoot = language_script_signal(normalized, candidateRoots)
    if scriptRoot is not None:
        return scriptRoot, 2
    if "vi" in candidateRoots and any(character in VIETNAMESE_LETTERS for character in normalized):
        return "vi", 2
    viScore = 1 if "vi" in candidateRoots and normalized in VIETNAMESE_WORDS else 0
    enScore = 1 if "en" in candidateRoots and normalized in ENGLISH_WORDS else 0
    if viScore > enScore:
        return "vi", viScore
    if enScore > viScore:
        return "en", enScore
    return None, 0


_MATH_ALPHANUMERIC_TRANSLATION_TABLE: dict[int, str] = NORMALIZATION_TABLE


def normalize_mathematical_alphanumeric(text: str) -> str:
    """Normalize Mathematical Alphanumeric Symbols (U+1D400..U+1D7FF),
    Arabic Mathematical Alphabetic Symbols (U+1EE00..U+1EEFF),
    Fullwidth/Halfwidth forms (U+FF01..U+FFEF), Ideographic Space (U+3000),
    Letterlike symbols (U+2100..U+214F), Enclosed Alphanumerics & Supplements,
    Phonetic Small Capitals, Superscripts, Subscripts, Invisible controls,
    and official UCD 17.0 / CLDR 48.2 compatibility characters
    to standard readable script characters.
    """
    if not text or text.isascii():
        return text
    return text.translate(_MATH_ALPHANUMERIC_TRANSLATION_TABLE)


def has_vietnamese_diacritics(text: str) -> bool:
    """Return True if text contains any Vietnamese distinctive diacritics."""
    return any(ch in VIETNAMESE_LETTERS for ch in text.casefold())


# Unicode Emoji Sequences: Keycaps, Flags, ZWJ sequences, Fitzpatrick skin tones, Pictographs
_EMOJI_BASE = (
    r"(?:"
    r"[0-9#*]\uFE0F?\u20E3"  # Keycap sequence (1️⃣, 2️⃣, #️⃣, *️⃣)
    r"|[\U0001F1E6-\U0001F1FF]{2}"  # Regional Indicator pairs (Flags: 🇻🇳, 🇺🇸...)
    r"|[\U0001F300-\U0001FAFF\u2600-\u27BF\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA"
    r"\u24C2\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299]"
    r")"
)
_EMOJI_MODIFIERS = r"(?:[\uFE0E\uFE0F]|\U0001F3FB-\U0001F3FF)*"
_EMOJI_RUN_PATTERN = rf"(?:{_EMOJI_BASE}{_EMOJI_MODIFIERS}(?:\u200D{_EMOJI_BASE}{_EMOJI_MODIFIERS})*)+"
_EMOJI_TOKEN_RE = re.compile(rf"^{_EMOJI_RUN_PATTERN}$", re.UNICODE)


def is_emoji_token(token: str) -> bool:
    """Return True if token is an intact Unicode emoji or emoji sequence."""
    return bool(_EMOJI_TOKEN_RE.match(token))


# Unicode 17.0 Category Sc (all 64 official currency symbols across BMP and SMP)
_UCD_CURRENCY_SYMBOLS = r"[\$¢£¤¥֏؋߾߿৲৳৻૱௹฿៛\u20a0-\u20c1꠸﷼﹩＄￠￡￥￦\U00011FDD-\U00011FE0\U0001E2FF\U0001ECB0]"

# Active ISO 4217 Currency Codes from CLDR 48.2 (178 active 3-letter codes)
_ISO_CURRENCY_CODES = (
    r"AED|AFN|ALL|AMD|AOA|ARS|AUD|AWG|AZN|BAM|BBD|BDT|BHD|BIF|BMD|BND|BOB|BOV|BRL|BSD|"
    r"BTN|BWP|BYN|BZD|CAD|CDF|CHE|CHF|CHW|CLF|CLP|CNH|CNY|COP|COU|CRC|CUP|CVE|CZK|DJF|"
    r"DKK|DOP|DZD|EGP|ERN|ETB|EUR|FJD|FKP|GBP|GEL|GHS|GIP|GMD|GNF|GTQ|GYD|HKD|HNL|HTG|"
    r"HUF|IDR|ILS|INR|IQD|IRR|ISK|JMD|JOD|JPY|KES|KGS|KHR|KMF|KPW|KRW|KWD|KYD|KZT|LAK|"
    r"LBP|LKR|LRD|LSL|LYD|MAD|MDL|MGA|MKD|MMK|MNT|MOP|MRU|MUR|MVR|MWK|MXN|MXV|MYR|MZN|"
    r"NAD|NGN|NIO|NOK|NPR|NZD|OMR|PAB|PEN|PGK|PHP|PKR|PLN|PYG|QAR|RON|RSD|RUB|RWF|SAR|"
    r"SBD|SCR|SDG|SEK|SGD|SHP|SLE|SOS|SRD|SSP|STN|SYP|SZL|THB|TJS|TMT|TND|TOP|TRY|TTD|"
    r"TWD|TZS|UAH|UGX|USD|USN|UYI|UYU|UYW|UZS|VED|VES|VND|VUV|WST|XAD|XAF|XAG|XAU|XBA|"
    r"XBB|XBC|XBD|XCD|XCG|XDR|XOF|XPD|XPF|XPT|XSU|XTS|XUA|XXX|YER|ZAR|ZMW|ZWG"
)
_CURRENCY_PATTERN = rf"(?:{_UCD_CURRENCY_SYMBOLS}|(?:{_ISO_CURRENCY_CODES})\b)"

# International measurement, computing, physical, and scientific units from CLDR 48.2 and ISO 80000
_INTERNATIONAL_UNITS = [
    # Speeds and composite
    "km/h",
    "m/s",
    "mph",
    "knot",
    "L/100km",
    "L/km",
    # Area & Volume
    "km2",
    "km²",
    "m2",
    "m²",
    "dm2",
    "cm2",
    "mm2",
    "km3",
    "km³",
    "m3",
    "m³",
    "dm3",
    "cm3",
    "mm3",
    # Distance / Length
    "km",
    "dm",
    "cm",
    "mm",
    "μm",
    "um",
    "nm",
    "pm",
    "ha",
    # Mass
    "kg",
    "mg",
    "dag",
    "hg",
    "g",
    "μg",
    "ug",
    "t",
    "oz",
    "lb",
    "lbs",
    # Liquid Volume
    "ml",
    "cl",
    "dl",
    "cc",
    "l",
    "L",
    # Digital storage and data transfer
    "Kbps",
    "Mbps",
    "Gbps",
    "Tbps",
    "bps",
    "KB",
    "MB",
    "GB",
    "TB",
    "PB",
    "EB",
    "RAM",
    "dpi",
    "ppi",
    "fps",
    "rpm",
    "px",
    "pt",
    "em",
    "rem",
    "vh",
    "vw",
    # Frequency
    "THz",
    "GHz",
    "MHz",
    "kHz",
    "Hz",
    # Time
    "hrs",
    "hr",
    "mins",
    "min",
    "sec",
    "ms",
    "μs",
    "us",
    "ns",
    "s",
    "h",
    # Temperature & Angles
    "°C",
    "℃",
    "°F",
    "℉",
    "K",
    "K",
    "°",
    # Electrical, Energy, Force, Pressure
    "mV",
    "kV",
    "V",
    "mA",
    "A",
    "kW",
    "MW",
    "GW",
    "W",
    "Wh",
    "kWh",
    "MWh",
    "GWh",
    "kJ",
    "MJ",
    "GJ",
    "J",
    "cal",
    "kcal",
    "hPa",
    "kPa",
    "MPa",
    "GPa",
    "Pa",
    "bar",
    "mbar",
    "atm",
    "dB",
    "lx",
    "lm",
    "kN",
    "N",
    "Ω",
    "ohm",
    # Ratios, percentages, parts
    "%",
    "‰",
    "‱",
    "ppm",
    # Single letter SI length
    "m",
]
_INTERNATIONAL_UNITS.sort(key=len, reverse=True)
_UNITS_PATTERN = rf"(?:{'|'.join(re.escape(u) for u in _INTERNATIONAL_UNITS)})(?!\w)"

# Time format: standard 24h/12h with optional AM/PM (hh:mm[:ss]), or colloquial hh'h'mm
_TIME_PATTERN = (
    r"(?:"
    r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|am|pm))?"
    r"|\d{1,2}[h:]\d{2}(?:[a-zA-Z]+)?"
    r")"
)
_NUM_CORE = r"(?:\d+(?:[.,/:]\d+)*)"
_NUM_RANGE_OP = r"[–—~×xX*+±/\-]"

_NUMBER_CHUNK_PATTERN = (
    rf"(?:[+\-±~]?\s*(?:{_CURRENCY_PATTERN}\s*)?"
    rf"(?:{_TIME_PATTERN}|{_NUM_CORE})"
    rf"(?:\s*(?:{_CURRENCY_PATTERN}|{_UNITS_PATTERN}))?"
    rf"(?:\s*{_NUM_RANGE_OP}\s*"
    rf"(?:[+\-±~]?\s*(?:{_CURRENCY_PATTERN}\s*)?(?:{_TIME_PATTERN}|{_NUM_CORE})(?:\s*(?:{_CURRENCY_PATTERN}|{_UNITS_PATTERN}))?)"
    rf")*)"
)

_NUMBER_TOKEN_RE = re.compile(rf"^{_NUMBER_CHUNK_PATTERN}$", re.IGNORECASE | re.UNICODE)


def is_number_token(token: str) -> bool:
    """Check if token is numeric (digits, decimals, percentages, currency, units, time, ranges)."""
    return bool(_NUMBER_TOKEN_RE.match(token))


def is_symbol_or_emoji_token(token: str) -> bool:
    """Check if token is composed entirely of symbols, emojis, or punctuation."""
    return is_emoji_token(token) or (
        bool(token) and all(not unicodedata.category(c).startswith("L") and not c.isdigit() for c in token)
    )


def _build_script_to_candidate_roots() -> dict[str, tuple[str, ...]]:
    """Invert official SUPPORTED_LANGUAGE_SCRIPTS mapping scripts to valid language roots.

    Excludes universal Latin and shared Han scripts (which are disambiguated
    contextually in ``segment_mixed_text``).
    """
    byScript: dict[str, list[str]] = {}
    for root, scripts in SUPPORTED_LANGUAGE_SCRIPTS.items():
        for script in scripts:
            if script in ("Latin", "Han"):
                continue
            byScript.setdefault(script, []).append(root)
    if "Hebrew" in byScript and "iw" not in byScript["Hebrew"]:
        byScript["Hebrew"].append("iw")
    return {script: tuple(sorted(roots)) for script, roots in sorted(byScript.items())}


_SCRIPT_TO_CANDIDATE_ROOTS: dict[str, tuple[str, ...]] = _build_script_to_candidate_roots()


def get_character_script(cp: int) -> str | None:
    """Resolve character codepoint to its official script name."""
    for script, ranges in SCRIPT_RANGES.items():
        for start, end in ranges:
            if start <= cp <= end:
                return script
    return None


_MIXED_TOKENIZER_RE = re.compile(
    rf"\s+"
    rf"|{_EMOJI_RUN_PATTERN}"
    rf"|{_NUMBER_CHUNK_PATTERN}"
    rf"|[^\w\s]+"
    rf"|[^\W\d_]+",
    re.IGNORECASE | re.UNICODE,
)


def segment_mixed_text(
    text: str,
    candidateLanguages: list[str],
    preferredLanguage: str,
) -> list[tuple[str, str]]:
    """Segment a mixed-language sentence into contiguous homogeneous sub-segments.

    Guarantees:
    - Mathematical alphanumeric symbols and fullwidth characters are normalized.
    - Numbers, math tokens, units, times, currencies, and symbols/emojis strictly use ``preferredLanguage``.
    - Non-Latin script blocks (CJK, Cyrillic, Arabic, Greek, Hebrew, Thai, etc.)
      are routed to matching candidate languages according to user-configured priority.
    - Latin text without Vietnamese diacritics is assigned to English or candidate
      languages, while Vietnamese diacritics keep the clause in Vietnamese.
    - Neutral punctuation and whitespace attach seamlessly to neighboring text.
    - The concatenation of all segment texts exactly equals the normalized text:
      ``"".join(s for s, _ in segments) == normalize_mathematical_alphanumeric(text)``.
    """
    if not text:
        return []
    normalizedText = normalize_mathematical_alphanumeric(text)
    if len(candidateLanguages) <= 1 or not normalizedText.strip():
        lang = candidateLanguages[0] if candidateLanguages else preferredLanguage
        return [(normalizedText, lang)]

    candidateRoots = {c.split("-", 1)[0].lower(): c for c in candidateLanguages}

    chunks: list[tuple[str, str]] = []
    # Tokenize preserving exact whitespace, emojis, numbers with units/currency/time, words, and punctuation
    rawTokens = _MIXED_TOKENIZER_RE.findall(normalizedText)

    for tok in rawTokens:
        if tok.isspace():
            chunks.append((tok, "WHITE"))
        elif is_emoji_token(tok):
            chunks.append((tok, "SYM"))
        elif is_number_token(tok):
            chunks.append((tok, "NUM"))
        elif is_symbol_or_emoji_token(tok):
            if any(unicodedata.category(c) in ("So", "Sk") or ord(c) > 0x2000 for c in tok):
                chunks.append((tok, "SYM"))
            else:
                chunks.append((tok, "PUNCT"))
        else:
            scripts = [get_character_script(ord(c)) for c in tok if unicodedata.category(c).startswith("L")]
            dominantScript = scripts[0] if scripts else "Latin"
            chunks.append((tok, f"SCRIPT_{dominantScript}"))

    # Initial language classification for chunks
    classifiedChunks: list[tuple[str, str | None]] = []
    for val, cat in chunks:
        if cat in ("NUM", "SYM"):
            classifiedChunks.append((val, preferredLanguage))
        elif cat in ("WHITE", "PUNCT"):
            classifiedChunks.append((val, None))
        elif cat.startswith("SCRIPT_"):
            scriptName = cat.split("_", 1)[1]
            if scriptName == "Han":
                # Disambiguate Han (Kanji/Hanzi):
                if "ja" in candidateRoots:
                    classifiedChunks.append((val, candidateRoots["ja"]))
                elif any(zh in candidateRoots for zh in ("zh", "cmn", "yue")):
                    zhCand = next(candidateRoots[zh] for zh in ("zh", "cmn", "yue") if zh in candidateRoots)
                    classifiedChunks.append((val, zhCand))
                elif "ko" in candidateRoots:
                    classifiedChunks.append((val, candidateRoots["ko"]))
                else:
                    classifiedChunks.append((val, preferredLanguage))
            elif scriptName in _SCRIPT_TO_CANDIDATE_ROOTS:
                # Check preferredLanguage first if it supports this script
                prefRoot = preferredLanguage.split("-", 1)[0].lower()
                validRoots = _SCRIPT_TO_CANDIDATE_ROOTS[scriptName]
                if prefRoot in validRoots:
                    classifiedChunks.append((val, preferredLanguage))
                else:
                    # Match according to user-configured priority in candidateLanguages
                    for cand in candidateLanguages:
                        candRoot = cand.split("-", 1)[0].lower()
                        if candRoot in validRoots:
                            classifiedChunks.append((val, cand))
                            break
                    else:
                        classifiedChunks.append((val, preferredLanguage))
            else:
                # Latin script
                classifiedChunks.append((val, "LATIN"))

    # Resolve LATIN chunks
    latinCandidates = [
        candidateRoots[r] for r, c in candidateRoots.items() if "Latin" in SUPPORTED_LANGUAGE_SCRIPTS.get(r, ())
    ]
    if len(latinCandidates) <= 1:
        singleLatin = latinCandidates[0] if latinCandidates else preferredLanguage
        classifiedChunks = [(v, singleLatin if lang == "LATIN" else lang) for v, lang in classifiedChunks]
    else:
        # Multiple Latin candidates (e.g. vi + en):
        i = 0
        while i < len(classifiedChunks):
            if classifiedChunks[i][1] == "LATIN":
                start = i
                end = i
                hasVi = False
                while end < len(classifiedChunks) and (classifiedChunks[end][1] in ("LATIN", None)):
                    if classifiedChunks[end][1] == "LATIN":
                        if has_vietnamese_diacritics(classifiedChunks[end][0]):
                            hasVi = True
                    end += 1

                if "vi" in candidateRoots and hasVi:
                    # Clause has Vietnamese diacritics
                    for k in range(start, end):
                        if classifiedChunks[k][1] == "LATIN":
                            word = classifiedChunks[k][0]
                            if has_vietnamese_diacritics(word):
                                classifiedChunks[k] = (word, candidateRoots["vi"])
                            else:
                                isDistinctForeign = any(ch in "wzjfwZJF" for ch in word) or (
                                    len(word) >= 4 and word[0].isupper() and "en" in candidateRoots
                                )
                                if isDistinctForeign and "en" in candidateRoots:
                                    classifiedChunks[k] = (word, candidateRoots["en"])
                                else:
                                    classifiedChunks[k] = (word, candidateRoots["vi"])
                else:
                    latinTarget = candidateRoots.get("en") or latinCandidates[0]
                    for k in range(start, end):
                        if classifiedChunks[k][1] == "LATIN":
                            classifiedChunks[k] = (classifiedChunks[k][0], latinTarget)
                i = end
            else:
                i += 1

    # Attach neutral tokens (spaces, punctuation)
    resolved: list[tuple[str, str]] = []
    currentLang = preferredLanguage
    for val, lang in classifiedChunks:
        if lang is not None:
            currentLang = lang
            resolved.append((val, lang))
        else:
            resolved.append((val, currentLang))

    # Coalesce adjacent chunks sharing the same language
    merged: list[tuple[str, str]] = []
    for val, lang in resolved:
        if merged and merged[-1][1] == lang:
            merged[-1] = (merged[-1][0] + val, lang)
        else:
            merged.append((val, lang))

    return merged

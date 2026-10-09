"""Pure helpers for Unicode-script language-profile fallback.

This module intentionally has no NVDA imports so the same production logic can
be exercised by the standalone test suite.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Callable, Collection

from .unicode_data import (
    CLDR_ACTIVE_CURRENCIES,
    CLDR_UNIT_SYMBOLS,
    COMBINING_MARK_RANGES,
    CURRENCY_SYMBOL_RANGES,
    EMOJI_PICTOGRAPHIC_RANGES,
    LANGUAGE_SCRIPT_RANGES,
    LATIN_LANGUAGE_EXEMPLARS,
    NORMALIZATION_TABLE,
    SCRIPT_RANGES,
    SENTENCE_TERMINAL_CODEPOINTS,
    SUPPORTED_LANGUAGE_SCRIPTS,
)

ScriptRanges = tuple[tuple[int, int], ...]


def _ranges_to_regex_class(ranges: ScriptRanges) -> str:
    parts: list[str] = []
    for start, end in ranges:
        if start == end:
            parts.append(re.escape(chr(start)))
        else:
            parts.append(f"{re.escape(chr(start))}-{re.escape(chr(end))}")
    return f"[{''.join(parts)}]"


_COMBINING_MARK_STARTS: tuple[int, ...] = tuple(start for start, _ in COMBINING_MARK_RANGES)
_COMBINING_MARK_CLASS = _ranges_to_regex_class(COMBINING_MARK_RANGES)
_WORD_CORE_PATTERN = (
    rf"(?:[^\W\d_]+{_COMBINING_MARK_CLASS}*)+"
    rf"(?:[\u200c\u200d](?:[^\W\d_]+{_COMBINING_MARK_CLASS}*)+)*"
)

_WORD_CONNECTOR_CHARS = "'’_\\-\u2010\u2011\u05be\u05f3\u05f4\u00b7\u22c5"
_WORD_INTERNAL_CONNECTOR_CLASS = r"['’\u2010\u2011\u05be\u05f3\u05f4\u00b7\u22c5]"
_WORD_TOKEN_PATTERN = (
    rf"{_WORD_CORE_PATTERN}"
    rf"(?:{_WORD_INTERNAL_CONNECTOR_CLASS}{_WORD_CORE_PATTERN})*"
    r"\u05f3?"
)

LANGUAGE_WORD_RE = re.compile(
    rf"{_WORD_CORE_PATTERN}(?:[{_WORD_CONNECTOR_CHARS}]{_WORD_CORE_PATTERN})*\u05f3?",
    re.UNICODE,
)
_LATIN_NON_ASCII_EXEMPLARS: dict[str, frozenset[str]] = {
    root: frozenset(ch for ch in chars if not ch.isascii()) for root, chars in LATIN_LANGUAGE_EXEMPLARS.items()
}
VIETNAMESE_LETTERS: frozenset[str] = _LATIN_NON_ASCII_EXEMPLARS.get("vi", frozenset())


_FIRST_COMBINING_MARK_CODEPOINT = 0x0300
_UNIQUE_EXEMPLAR_WEIGHT = 3
_SHARED_EXEMPLAR_WEIGHT = 1
_STRONG_LANGUAGE_SIGNAL = 2
_WEAK_LANGUAGE_SIGNAL = 1
_HAN_CANDIDATE_ROOTS: frozenset[str] = frozenset({"cmn", "yue", "zh", "ja", "ko"})


def is_combining_mark(cp: int) -> bool:
    """Return True if codepoint is a UCD 17.0 combining mark (Mn, Mc, Me)."""
    if cp < _FIRST_COMBINING_MARK_CODEPOINT:
        return False
    idx = bisect.bisect_right(_COMBINING_MARK_STARTS, cp) - 1
    if idx >= 0:
        start, end = COMBINING_MARK_RANGES[idx]
        if start <= cp <= end:
            return True
    return unicodedata.category(chr(cp)).startswith("M")


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


def latin_exemplar_signal(text: str, candidateRoots: Collection[str]) -> str | None:
    """Return the winning Latin candidate root based on CLDR non-ASCII exemplar characters."""
    if not text or text.isascii():
        return None
    latinRoots = [root for root in candidateRoots if root in _LATIN_NON_ASCII_EXEMPLARS]
    if not latinRoots:
        return None
    normalized = unicodedata.normalize("NFC", text)
    nonAsciiChars: list[str] = []
    for char in normalized:
        if char.isascii() or not char.isalpha():
            continue
        folded = char.lower()
        nonAsciiChars.append(folded if len(folded) == 1 else char)
    if not nonAsciiChars:
        return None
    scores: dict[str, int] = {root: 0 for root in latinRoots}
    for char in nonAsciiChars:
        matching = [root for root in latinRoots if char in _LATIN_NON_ASCII_EXEMPLARS[root]]
        if len(matching) == 1:
            scores[matching[0]] += _UNIQUE_EXEMPLAR_WEIGHT
        elif len(matching) > 1:
            for root in matching:
                scores[root] += _SHARED_EXEMPLAR_WEIGHT
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if ranked[0][1] <= 0:
        return None
    if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
        return ranked[0][0]
    return None


def language_token_signal(
    token: str,
    candidateRoots: Collection[str],
    is_url_token: Callable[[str], bool] | None = None,
) -> tuple[str | None, int]:
    normalized = normalize_mathematical_alphanumeric(token).strip(_WORD_CONNECTOR_CHARS)
    if not normalized or (is_url_token is not None and is_url_token(normalized)):
        return None, 0
    scriptRoot = language_script_signal(normalized, candidateRoots)
    if scriptRoot is not None:
        return scriptRoot, _STRONG_LANGUAGE_SIGNAL
    exemplarRoot = latin_exemplar_signal(normalized, candidateRoots)
    if exemplarRoot is not None:
        return exemplarRoot, _STRONG_LANGUAGE_SIGNAL
    if "en" in candidateRoots and normalized.isascii() and any(character.isalpha() for character in normalized):
        return "en", _WEAK_LANGUAGE_SIGNAL
    return None, 0


_MATH_ALPHANUMERIC_TRANSLATION_TABLE: dict[int, str] = NORMALIZATION_TABLE
_NFC_PRESERVED_PUNCT_RE = re.compile(r"([\u037e\u0387]+)")


def normalize_mathematical_alphanumeric(text: str) -> str:
    """Normalize Mathematical Alphanumeric Symbols (U+1D400..U+1D7FF),
    Arabic Mathematical Alphabetic Symbols (U+1EE00..U+1EEFF),
    Fullwidth/Halfwidth alphanumeric forms, Ideographic Space (U+3000),
    Letterlike symbols (U+2100..U+214F), Enclosed Alphanumerics & Supplements,
    Phonetic Small Capitals, Superscripts, Subscripts, Invisible controls,
    decomposed combining sequences (NFD -> NFC), and UCD 17.0 / CLDR 48.2
    compatibility characters to standard readable precomposed script characters.
    """
    if not text or text.isascii():
        return text
    translated = text.translate(_MATH_ALPHANUMERIC_TRANSLATION_TABLE)
    if "\u037e" in translated or "\u0387" in translated:
        return "".join(
            part if part[0] in "\u037e\u0387" else unicodedata.normalize("NFC", part)
            for part in _NFC_PRESERVED_PUNCT_RE.split(translated)
            if part
        )
    return unicodedata.normalize("NFC", translated)


def is_single_grapheme_token(text: str) -> bool:
    """Return True if text (ignoring outer whitespace) is a single character or single base character with combining marks."""
    if not text:
        return False
    stripped = normalize_mathematical_alphanumeric(text).strip()
    if not stripped:
        return False
    if len(stripped) == 1:
        return True
    return all(is_combining_mark(ord(ch)) for ch in stripped[1:])


def has_vietnamese_diacritics(text: str) -> bool:
    """Return True if text contains any Vietnamese distinctive diacritics (NFC or NFD)."""
    if not text or text.isascii():
        return False
    normalized = unicodedata.normalize("NFC", text).casefold()
    return any(ch in VIETNAMESE_LETTERS for ch in normalized)


# Unicode Emoji Sequences: Keycaps, Flags, ZWJ sequences, Fitzpatrick skin tones, Pictographs (UCD 17.0)
_EMOJI_PICTOGRAPH_CLASS = _ranges_to_regex_class(EMOJI_PICTOGRAPHIC_RANGES)
_EMOJI_BASE = (
    r"(?:"
    r"[0-9#*]\uFE0F?\u20E3"  # Keycap sequence (1️⃣, 2️⃣, #️⃣, *️⃣)
    r"|[\U0001F1E6-\U0001F1FF]{2}"  # Regional Indicator pairs (Flags: 🇻🇳, 🇺🇸...)
    rf"|{_EMOJI_PICTOGRAPH_CLASS}"
    r")"
)
_EMOJI_MODIFIERS = r"(?:[\uFE0E\uFE0F\u20E3\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F])*"
_EMOJI_RUN_PATTERN = rf"(?:{_EMOJI_BASE}{_EMOJI_MODIFIERS}(?:\u200D{_EMOJI_BASE}{_EMOJI_MODIFIERS})*)+"
_EMOJI_TOKEN_RE = re.compile(rf"^{_EMOJI_RUN_PATTERN}$", re.UNICODE)


def is_emoji_token(token: str) -> bool:
    """Return True if token is an intact Unicode emoji or emoji sequence."""
    return bool(_EMOJI_TOKEN_RE.match(token))


# Unicode 17.0 Category Sc (currency symbols across BMP and SMP)
_UCD_CURRENCY_SYMBOLS = _ranges_to_regex_class(CURRENCY_SYMBOL_RANGES)

# Active ISO 4217 Currency Codes from CLDR 48.2 (matched case-sensitively)
_ISO_CURRENCY_CODES = "(?-i:" + "|".join((*CLDR_ACTIVE_CURRENCIES, "XXX")) + ")"
_CURRENCY_PATTERN = rf"(?:{_UCD_CURRENCY_SYMBOLS}|(?:{_ISO_CURRENCY_CODES})\b)"
_PERCENT_PATTERN = r"[%\u066a\u0609\u060a\ufe6a\uff05\u2030\u2031]"

# International measurement, computing, physical, and scientific units from CLDR 48.2 (matched case-sensitively)
_INTERNATIONAL_UNITS = sorted(CLDR_UNIT_SYMBOLS, key=len, reverse=True)
_UNITS_PATTERN = rf"(?-i:(?:{'|'.join(re.escape(u) for u in _INTERNATIONAL_UNITS)}))(?!\w)"

# Time format: standard 24h/12h with optional AM/PM (hh:mm[:ss]), or colloquial hh'h'mm
_TIME_PATTERN = (
    r"(?:"
    r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM|am|pm))?"
    r"|\d{1,2}[h:]\d{2}(?:[a-zA-Z]+)?"
    r")"
)
_NUM_CORE = r"(?:\d+(?:[.,/:\u066b\u066c\u060d]\d+)*)"
_NUM_RANGE_OP = r"[–—~×xX*+±/\-]"

_NUMBER_CHUNK_PATTERN = (
    rf"(?:(?:[±~]\s*|[+\-])?(?:(?:{_CURRENCY_PATTERN}|{_PERCENT_PATTERN})\s*)?"
    rf"(?:{_TIME_PATTERN}|{_NUM_CORE})"
    rf"(?:\s*(?:{_CURRENCY_PATTERN}|{_PERCENT_PATTERN}|{_UNITS_PATTERN}))?"
    rf"(?:\s*{_NUM_RANGE_OP}\s*"
    rf"(?:(?:[±~]\s*|[+\-])?(?:(?:{_CURRENCY_PATTERN}|{_PERCENT_PATTERN})\s*)?(?:{_TIME_PATTERN}|{_NUM_CORE})(?:\s*(?:{_CURRENCY_PATTERN}|{_PERCENT_PATTERN}|{_UNITS_PATTERN}))?)"
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
    """Invert SUPPORTED_LANGUAGE_SCRIPTS mapping scripts to valid language roots.

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
_FLATTENED_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = tuple(
    sorted((start, end, script) for script, ranges in SCRIPT_RANGES.items() for start, end in ranges)
)
_FLATTENED_SCRIPT_STARTS: tuple[int, ...] = tuple(start for start, _, _ in _FLATTENED_SCRIPT_RANGES)


def get_character_script(cp: int) -> str | None:
    """Resolve character codepoint to its script name."""
    if 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A:
        return "Latin"
    idx = bisect.bisect_right(_FLATTENED_SCRIPT_STARTS, cp) - 1
    if idx >= 0:
        start, end, script = _FLATTENED_SCRIPT_RANGES[idx]
        if start <= cp <= end:
            return script
    return None


_URL_EMAIL_DOMAIN_PATTERN = (
    r"(?:"
    r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<>\"')\]}]*[A-Za-z0-9/_=#%&+\-~]"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"|(?-i:[A-Za-z0-9_]+(?:\.[a-z0-9_]+)*\.[a-z]{2,})"
    r")"
)

_MIXED_TOKENIZER_RE = re.compile(
    rf"\s+"
    rf"|{_EMOJI_RUN_PATTERN}"
    rf"|{_NUMBER_CHUNK_PATTERN}"
    rf"|{_URL_EMAIL_DOMAIN_PATTERN}"
    rf"|{_WORD_TOKEN_PATTERN}"
    rf"|(?:[^\w\s]|_)+",
    re.IGNORECASE | re.UNICODE,
)

_CLAUSE_BREAK_PUNCTUATION: frozenset[str] = frozenset(
    {
        *(chr(cp) for cp in SENTENCE_TERMINAL_CODEPOINTS),
        ",",
        ";",
        ":",
        "—",
        "–",
        "―",
        "⸺",
        "⸻",
        "|",
        "¦",
        "‖",
        ".",
        "!",
        "?",
        "…",
        "⋯",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        '"',
        "'",
        "“",
        "”",
        "‘",
        "’",
        "‚",
        "‛",
        "„",
        "‟",
        "«",
        "»",
        "‹",
        "›",
        "⹂",
        "、",
        "。",
        "，",
        "；",
        "：",
        "！",
        "？",
        "．",
        "「",
        "」",
        "『",
        "』",
        "（",
        "）",
        "［",
        "］",
        "｛",
        "｝",
        "｟",
        "｠",
        "⦅",
        "⦆",
        "〈",
        "〉",
        "《",
        "》",
        "〔",
        "〕",
        "〖",
        "〗",
        "〘",
        "〙",
        "〚",
        "〛",
        "【",
        "】",
        "〝",
        "〞",
        "〟",
        "〜",
        "〰",
        "・",
        "｡",
        "｢",
        "｣",
        "､",
        "\u00b7",
        "\u037e",
        "\u0387",
        "\u055b",
        "\u055c",
        "\u055d",
        "\u055e",
        "\u0589",
        "\u05c0",
        "\u05c3",
        "\u060c",
        "\u061b",
        "\u061e",
        "\u061f",
        "\u06d4",
        "\u0df4",
        "\u0e5a",
        "\u0e5b",
        "\u0f0b",
        "\u0f0c",
        "\u0f0d",
        "\u0f0e",
        "\u104a",
        "\u104b",
        "\u10fb",
        "\u1361",
        "\u1362",
        "\u1363",
        "\u1364",
        "\u1365",
        "\u1366",
        "\u1367",
        "\u1368",
        "\u17d4",
        "\u17d5",
        "\u17d6",
        "\u17d8",
        "\ua9c7",
        "\ua9c8",
        "\ua9c9",
        "\ufd3e",
        "\ufd3f",
        "\n",
        "\r",
        "\u0085",
        "\u2028",
        "\u2029",
    }
)


def _is_clause_break_chunk(chunks: list[tuple[str, str]], idx: int) -> bool:
    """Return True when chunk at `idx` acts as a clause separator."""
    chunkVal, chunkCat = chunks[idx]
    if chunkCat in ("PUNCT", "SYM") and any(
        ch in _CLAUSE_BREAK_PUNCTUATION or unicodedata.category(ch) in ("Ps", "Pe", "Pi", "Pf") for ch in chunkVal
    ):
        return True
    if chunkCat == "PUNCT" and any(ch in "-/\\" for ch in chunkVal):
        hasPrevSpace = idx > 0 and chunks[idx - 1][1] == "WHITE"
        hasNextSpace = idx + 1 < len(chunks) and chunks[idx + 1][1] == "WHITE"
        if hasPrevSpace or hasNextSpace:
            return True
    return False


def has_language_words(text: str) -> bool:
    """Return True if text contains at least one word token (excluding numbers, units, currencies, symbols, and emojis)."""
    normalizedText = normalize_mathematical_alphanumeric(text)
    stripped = normalizedText.strip()
    if not stripped:
        return False
    tokens = [tok for tok in _MIXED_TOKENIZER_RE.findall(stripped) if not tok.isspace()]
    return any(not (is_number_token(tok) or is_symbol_or_emoji_token(tok)) for tok in tokens)


def is_spelling_only_text(text: str) -> bool:
    """Return True if text consists solely of single-letter basic Latin tokens and optional clause punctuation."""
    normalizedText = normalize_mathematical_alphanumeric(text)
    stripped = normalizedText.strip()
    if not stripped:
        return False
    tokens = [tok for tok in _MIXED_TOKENIZER_RE.findall(stripped) if not tok.isspace()]
    if not tokens:
        return False
    hasLetters = False
    for tok in tokens:
        if is_number_token(tok) or is_emoji_token(tok):
            return False
        if is_symbol_or_emoji_token(tok):
            if not all(unicodedata.category(c).startswith("P") or c.isspace() for c in tok):
                return False
            continue
        if len(tok) == 1 and tok.isascii() and tok.isalpha() and get_character_script(ord(tok)) == "Latin":
            hasLetters = True
        else:
            return False
    return hasLetters


def _candidate_roots_map(candidateLanguages: list[str]) -> dict[str, str]:
    """Map language roots to their highest-priority candidate locale."""
    roots: dict[str, str] = {}
    for cand in candidateLanguages:
        roots.setdefault(cand.split("-", 1)[0].lower(), cand)
    return roots


def segment_mixed_text(
    text: str,
    candidateLanguages: list[str],
    preferredLanguage: str,
    defaultLanguage: str | None = None,
) -> list[tuple[str, str]]:
    """Segment a mixed-language sentence into contiguous homogeneous sub-segments.

    Guarantees:
    - Mathematical alphanumeric symbols and fullwidth characters are normalized.
    - Single characters and single-letter spelling clauses use ``preferredLanguage``
      (unless a single character belongs to a distinct non-preferred candidate script
      or exemplar set).
    - Numbers, math tokens, units, times, currencies, symbols, emojis, punctuation,
      and whitespace follow the language currently being spoken in their clause.
    - Non-Latin script blocks (CJK, Cyrillic, Arabic, Greek, Hebrew, Thai, etc.)
      are routed to matching candidate languages according to user-configured priority.
    - Latin clauses are disambiguated using CLDR language exemplar sets and CLD2.
    - The concatenation of all segment texts exactly equals the normalized text:
      ``"".join(s for s, _ in segments) == normalize_mathematical_alphanumeric(text)``.
    """
    if not text:
        return []
    normalizedText = normalize_mathematical_alphanumeric(text)
    stripped = normalizedText.strip()
    if not stripped:
        lang = defaultLanguage or preferredLanguage or (candidateLanguages[0] if candidateLanguages else "")
        return [(normalizedText, lang)]
    if len(candidateLanguages) <= 1:
        lang = candidateLanguages[0] if candidateLanguages else (defaultLanguage or preferredLanguage)
        return [(normalizedText, lang)]

    candidateRoots = _candidate_roots_map(candidateLanguages)
    prefRoot = preferredLanguage.split("-", 1)[0].lower()

    # Single character reading (when not part of a multi-item utterance with an active defaultLanguage):
    # route directly to preferred language (or candidate language if matching a non-Latin script
    # or a non-preferred Latin exemplar character).
    if is_single_grapheme_token(stripped) and defaultLanguage is None:
        scriptSignal = language_script_signal(stripped, candidateRoots)
        if scriptSignal and scriptSignal != prefRoot and scriptSignal in candidateRoots:
            return [(normalizedText, candidateRoots[scriptSignal])]
        if not stripped.isascii():
            exemplarSignal = latin_exemplar_signal(stripped, candidateRoots)
            if (
                exemplarSignal
                and exemplarSignal != prefRoot
                and exemplarSignal in candidateRoots
                and stripped.lower() not in _LATIN_NON_ASCII_EXEMPLARS.get(prefRoot, frozenset())
            ):
                return [(normalizedText, candidateRoots[exemplarSignal])]
        return [(normalizedText, preferredLanguage)]

    spellingOnlyUtterance = is_spelling_only_text(normalizedText)

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
            if all(unicodedata.category(c).startswith("P") or c.isspace() for c in tok):
                chunks.append((tok, "PUNCT"))
            else:
                chunks.append((tok, "SYM"))
        else:
            scripts = [get_character_script(ord(c)) for c in tok if unicodedata.category(c).startswith("L")]
            dominantScript = next((s for s in scripts if s is not None), "Latin")
            chunks.append((tok, f"SCRIPT_{dominantScript}"))

    hasKana = any(cat in ("SCRIPT_Hiragana", "SCRIPT_Katakana") for _, cat in chunks)
    hanCandidates = [cand for cand in candidateLanguages if cand.split("-", 1)[0].lower() in _HAN_CANDIDATE_ROOTS]

    # Initial language classification for chunks:
    # Whitespace, punctuation, symbols/emojis, and numbers/units/currencies/times are language-neutral (None)
    # and inherit the language of the clause being spoken.
    classifiedChunks: list[tuple[str, str | None]] = []
    for val, cat in chunks:
        if cat in ("WHITE", "PUNCT", "SYM", "NUM"):
            classifiedChunks.append((val, None))
        elif cat.startswith("SCRIPT_"):
            scriptName = cat.split("_", 1)[1]
            if scriptName == "Han":
                if hasKana and "ja" in candidateRoots:
                    classifiedChunks.append((val, candidateRoots["ja"]))
                elif len(hanCandidates) == 1:
                    classifiedChunks.append((val, hanCandidates[0]))
                elif len(hanCandidates) > 1:
                    from . import language_detector

                    detectedHan = language_detector.detect_language(
                        val,
                        hanCandidates,
                        preferredLanguage=preferredLanguage if prefRoot in _HAN_CANDIDATE_ROOTS else None,
                    )
                    if detectedHan:
                        classifiedChunks.append((val, detectedHan))
                    elif prefRoot in _HAN_CANDIDATE_ROOTS:
                        classifiedChunks.append((val, preferredLanguage))
                    else:
                        classifiedChunks.append((val, hanCandidates[0]))
                else:
                    classifiedChunks.append((val, preferredLanguage))
            elif scriptName in _SCRIPT_TO_CANDIDATE_ROOTS:
                # Check preferredLanguage first if it supports this script
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
    latinCandidateRoots = [r for r in candidateRoots if "Latin" in SUPPORTED_LANGUAGE_SCRIPTS.get(r, ())]
    latinCandidates = [cand for cand in candidateLanguages if cand.split("-", 1)[0].lower() in latinCandidateRoots]
    if len(latinCandidates) <= 1:
        singleLatin = latinCandidates[0] if latinCandidates else preferredLanguage
        classifiedChunks = [(v, singleLatin if lang == "LATIN" else lang) for v, lang in classifiedChunks]
    else:
        nonEnViLatin = any(r not in ("en", "vi") for r in latinCandidateRoots) or len(latinCandidates) > len(
            latinCandidateRoots
        )
        i = 0
        while i < len(classifiedChunks):
            if classifiedChunks[i][1] == "LATIN":
                start = i
                end = i
                clauseWords: list[str] = []
                while end < len(classifiedChunks):
                    chunkVal, chunkLang = classifiedChunks[end]
                    if chunkLang not in ("LATIN", None):
                        break
                    # Break clause at punctuation or separator boundaries (e.g. comma, semicolon, pipe, spaced dash, period, etc.)
                    if _is_clause_break_chunk(chunks, end):
                        break
                    if chunkLang == "LATIN":
                        clauseWords.append(chunkVal)
                    end += 1

                clauseText = " ".join(clauseWords)
                exemplarWinner = latin_exemplar_signal(clauseText, latinCandidateRoots)
                if exemplarWinner and exemplarWinner in candidateRoots:
                    latinTarget = candidateRoots[exemplarWinner]
                elif spellingOnlyUtterance and prefRoot in candidateRoots:
                    latinTarget = candidateRoots[prefRoot]
                elif nonEnViLatin:
                    from . import language_detector

                    detectedLatin = language_detector.detect_language(
                        clauseText,
                        latinCandidates,
                        preferredLanguage=None,
                    )
                    latinTarget = detectedLatin or candidateRoots.get("en") or latinCandidates[0]
                else:
                    latinTarget = candidateRoots.get("en") or latinCandidates[0]
                for k in range(start, end):
                    if classifiedChunks[k][1] == "LATIN":
                        classifiedChunks[k] = (classifiedChunks[k][0], latinTarget)
                i = max(end, i + 1)
            else:
                i += 1

    # Attach neutral tokens (spaces, punctuation, symbols, numbers) to the active clause language.
    # Leading neutral tokens attach to the first non-None word language (or defaultLanguage / preferredLanguage).
    firstLang = defaultLanguage or preferredLanguage
    for _val, lang in classifiedChunks:
        if lang is not None:
            firstLang = lang
            break

    resolved: list[tuple[str, str]] = []
    currentLang = firstLang
    awaitingClauseLang = False
    for idx, (val, lang) in enumerate(classifiedChunks):
        cat = chunks[idx][1]
        if lang is not None:
            currentLang = lang
            awaitingClauseLang = False
            resolved.append((val, lang))
        else:
            if awaitingClauseLang and cat in ("NUM", "SYM"):
                for nextIdx in range(idx + 1, len(classifiedChunks)):
                    _nextVal, nextLang = classifiedChunks[nextIdx]
                    if nextLang is not None:
                        currentLang = nextLang
                        break
                    if _is_clause_break_chunk(chunks, nextIdx):
                        break
                awaitingClauseLang = False
            resolved.append((val, currentLang))
            if _is_clause_break_chunk(chunks, idx):
                awaitingClauseLang = True

    # Coalesce adjacent chunks sharing the same language
    merged: list[tuple[str, str]] = []
    for val, lang in resolved:
        if merged and merged[-1][1] == lang:
            merged[-1] = (merged[-1][0] + val, lang)
        else:
            merged.append((val, lang))

    return merged

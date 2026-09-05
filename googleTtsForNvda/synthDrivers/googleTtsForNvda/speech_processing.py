"""Pure-Python text segmentation and PCM processing helpers.

This module deliberately has no NVDA imports so its behavior can be exercised by
the regular Python test suite. Runtime-specific orchestration remains in the
synth driver.
"""

from __future__ import annotations

import bisect
import unicodedata
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Any

from .unicode_data import SENTENCE_TERMINAL_CODEPOINTS

PAUSE_MODE_DO_NOT_SHORTEN = "0"
PAUSE_MODE_SHORTEN_END_ONLY = "1"
PAUSE_MODE_SHORTEN_ALL = "2"
SHORTENED_SILENCE_KEEP_MS = 35
SHORTENED_ALL_PAUSES_KEEP_MS = 25
SILENCE_SAMPLE_THRESHOLD = 48
PCM_BYTES_PER_SAMPLE = 2
LIVE_MULTI_SEGMENT_LEAD_MS = 80
SHORT_CACHE_MAX_CHARS = 5000
SHORT_CACHE_MAX_HIDDEN_SEGMENTS = 24
SHORT_AUDIO_CACHE_OPTION_FIELDS = (
    "voiceId",
    "rate",
    "pitch",
    "postPitch",
    "volume",
    "outputGain",
    "artificialRate",
    "nvdaRate",
)

FAST_FIRST_SEGMENT_MIN_CHARS = 30
REGULAR_SEGMENT_MIN_CHARS = 110
FAST_FIRST_SEGMENT_MAX_CHARS = 64
FAST_FIRST_SEGMENT_TRIGGER_CHARS = 90
REGULAR_SEGMENT_MAX_CHARS = 240
SEAMLESS_UTTERANCE_MAX_CHARS = 900
FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS = 30
FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS = 90
FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD = 40
SOFT_PHRASE_SEGMENT_MIN_CHARS = 100
SOFT_PHRASE_SEGMENT_MAX_CHARS = 240
SOFT_PHRASE_SEGMENT_LOOKAHEAD = 55
URL_TOKEN_SEGMENT_MAX_CHARS = 220
FORCED_SEGMENT_MIN_CHARS = 32
FORCED_SEGMENT_FORWARD_LOOKAHEAD = 24
FORCED_SEGMENT_HARD_MAX_CHARS = 320
NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS = 12
NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO = 0.55
NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD = 8
MIN_ORPHAN_SPACE_CHARS = 24
MIN_ORPHAN_NO_SPACE_CHARS = 16
FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS = 115
FAST_FIRST_PUNCTUATION_FREE_NO_SPACE_TRIGGER_CHARS = 96
FAST_FIRST_PREFERRED_SOFT_CHARS = 55
FAST_FIRST_PREFERRED_WHITESPACE_CHARS = 68

SOFT_BREAK_CHARS = (
    ",;:\uff0c\u3001\uff1b\uff1a\u2014\u2013"
    "\u0387"
    "\u060c\u061b"
    "\u055d"
    "\u0f0b\u0f0c"
    "\u1363\u1364\u1365\u1366"
    "\u17d6"
    "\u104a"
    "\ua9c8"
)
ASCII_SENTENCE_TERMINATORS = ".!?"
SENTENCE_TRAILING_CLOSERS = "'\")]}”’」』）》〉»\u2018-\u201f\u3009\u300b\u300d\u300f\u3011\uff09\uff3d\uff5d"
# UCD Sentence_Terminal deliberately excludes some locale-specific or ambiguous
# sentence endings. Keep only supported-language/common tailoring here.
TAILORED_SENTENCE_TERMINATORS = set("\u037e\u0df4\u0e5a\u0e5b\u2026\u22ef")
UNICODE_SOFT_BREAK_NAME_PARTS = (
    "COMMA",
    "SEMICOLON",
    "COLON",
    "PHRASE",
    "CLAUSE",
    "PADA LINGSA",
    "PUNCTUATION CHEIKHAN",
    "PUNCTUATION BINDU",
)
UNICODE_INITIAL_PUNCTUATION_NAME_PARTS = (
    "INVERTED QUESTION MARK",
    "INVERTED EXCLAMATION MARK",
    "INITIAL QUESTION MARK",
    "INITIAL EXCLAMATION MARK",
)
NON_BREAKING_SOFT_PUNCTUATION = set("'\"`´’ʼʻʹʺ_-#@&/\\\u00b7\u05f3\u05f4\u2010\u2011\u2027\u30fb\uff65")
NON_BREAKING_SOFT_PUNCTUATION_NAME_PARTS = (
    "APOSTROPHE",
    "QUOTATION MARK",
    "QUOTE",
    "HYPHEN",
    "SOLIDUS",
    "SLASH",
    "MIDDLE DOT",
)
NO_SPACE_SCRIPT_PROFILES = (
    (
        (
            (0x3100, 0x312F),
            (0x31A0, 0x31BF),
            (0x3400, 0x4DBF),
            (0x4E00, 0x9FFF),
            (0xF900, 0xFAFF),
            (0x20000, 0x2A6DF),
            (0x2A700, 0x2B73F),
            (0x2B740, 0x2B81F),
            (0x2B820, 0x2CEAF),
            (0x2CEB0, 0x2EBEF),
            (0x30000, 0x3134F),
        ),
        80,
    ),
    (
        (
            (0x3040, 0x30FF),
            (0x31F0, 0x31FF),
            (0x1AFF0, 0x1AFFF),
            (0x1B000, 0x1B16F),
            (0xFF66, 0xFF9F),
        ),
        80,
    ),
    (((0x0E00, 0x0E7F),), 70),
    (((0x0E80, 0x0EFF),), 70),
    (((0x1900, 0x194F),), 70),
    (((0x1950, 0x197F),), 70),
    (((0x1980, 0x19DF),), 70),
    (((0x1A00, 0x1A1F),), 70),
    (((0x1A20, 0x1AAF),), 70),
    (((0x1780, 0x17FF),), 70),
    (((0x1000, 0x109F), (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F)), 70),
    (((0x0F00, 0x0FFF),), 70),
    (((0x1700, 0x171F),), 70),
    (((0x1720, 0x173F),), 70),
    (((0x1740, 0x175F),), 70),
    (((0x1760, 0x177F),), 70),
    (((0x1B00, 0x1B7F),), 70),
    (((0x1B80, 0x1BBF),), 70),
    (((0x1BC0, 0x1BFF),), 70),
    (((0x1C00, 0x1C4F),), 70),
    (((0xA000, 0xA48F),), 70),
    (((0xA930, 0xA95F),), 70),
    (((0xA980, 0xA9DF),), 70),
    (((0xAA00, 0xAA5F),), 70),
    (((0xAA80, 0xAADF),), 70),
)
_FLATTENED_NO_SPACE_RANGES: tuple[tuple[int, int, int], ...] = tuple(
    sorted(
        ((start, end, limit) for ranges, limit in NO_SPACE_SCRIPT_PROFILES for start, end in ranges),
        key=lambda item: item[0],
    )
)
_FLATTENED_NO_SPACE_STARTS: tuple[int, ...] = tuple(start for start, _end, _limit in _FLATTENED_NO_SPACE_RANGES)


def _find_no_space_range(codepoint: int) -> tuple[int, int, int] | None:
    idx = bisect.bisect_right(_FLATTENED_NO_SPACE_STARTS, codepoint) - 1
    if idx >= 0:
        entry = _FLATTENED_NO_SPACE_RANGES[idx]
        if entry[0] <= codepoint <= entry[1]:
            return entry
    return None


COMMON_ABBREVIATIONS = {
    # English
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "rev",
    "gen",
    "col",
    "maj",
    "capt",
    "lt",
    "sgt",
    "hon",
    "gov",
    "sen",
    "rep",
    "esq",
    "vs",
    "etc",
    "inc",
    "ltd",
    "co",
    "corp",
    "no",
    "fig",
    "eq",
    "vol",
    "ch",
    "p",
    "pp",
    "sec",
    "min",
    "max",
    "approx",
    "est",
    "dept",
    "dist",
    "ave",
    "blvd",
    "rd",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
    "ph",
    "phd",
    "md",
    "ba",
    "ma",
    "bsc",
    "msc",
    "jd",
    "llb",
    "llm",
    # German
    "usw",
    "bzw",
    "ca",
    "evtl",
    "ggf",
    "inkl",
    "nr",
    "ing",
    "mag",
    # French
    "mme",
    "mlle",
    "mgr",
    "ex",
    "p.",
    "n.b.",
    "c.-à-d.",
    # Spanish / Portuguese
    "sra",
    "srta",
    "dra",
    "profa",
    "num",
    "pag",
    "cap",
    "ej",
    "av",
    "eng",
    "exc",
    # Vietnamese
    "tp",
    "ths",
    "ts",
    "gs",
    "pgs",
    "bs",
    "ks",
    "cn",
    "tx",
    "tt",
    "qd",
    "nd",
    # Italian
    "dott",
    "avv",
    "cav",
    "ecc",
    # Polish
    "inż",  # Turkish
    "doç",
    "yrd",
    "vb",
    "müh",
    # Malay / Indonesian
    "drg",
    "dll",
    "dsb",
    # Hungarian
    "stb",
    # Czech / Slovak
    "doc",  # Romanian
    "cond",  # Russian / Cyrillic
    "ул",
    "им",
    "обл",
    "рис",
    "см",
    "стр",
    "тд",
    "тп",
    "пр",
    "руб",
    "коп",
    "тыс",
    "млн",
    "млрд",
    "др",
    "г",
    "гор",
    "пер",
    "пл",
    "просп",
    "проф",
    "канд",
    "доц",
    # Devanagari (hi, mr, ne, sa, brx, doi, kok, mai)
    "डॉ",
}


def pcm_bytes_for_milliseconds(milliseconds: int, sampleRate: int, bytesPerSample: int = PCM_BYTES_PER_SAMPLE) -> int:
    frames = max(0, int(sampleRate * milliseconds / 1000))
    return frames * bytesPerSample


def align_pcm_bytes(byteCount: int, bytesPerSample: int = PCM_BYTES_PER_SAMPLE) -> int:
    return max(0, int(byteCount) - (int(byteCount) % bytesPerSample))


def pcm_has_audible_sample(
    pcm: bytes,
    *,
    noiseFloor: int = SILENCE_SAMPLE_THRESHOLD,
    bytesPerSample: int = PCM_BYTES_PER_SAMPLE,
) -> bool:
    if bytesPerSample != 2:
        raise ValueError("Only signed 16-bit PCM is supported")
    pcmLength = align_pcm_bytes(len(pcm), bytesPerSample)
    if pcmLength <= 0:
        return False
    samples = memoryview(pcm)[:pcmLength].cast("h")
    return any(sample < -noiseFloor or sample > noiseFloor for sample in samples)


class PcmSilenceShortener:
    """Shorten final or all silent PCM runs with a streaming block fast path."""

    def __init__(
        self,
        *,
        sampleRate: int,
        shortenAllPauses: bool,
        keepSilenceMs: int = SHORTENED_SILENCE_KEEP_MS,
        noiseFloor: int = SILENCE_SAMPLE_THRESHOLD,
        bytesPerSample: int = PCM_BYTES_PER_SAMPLE,
    ) -> None:
        if bytesPerSample != 2:
            raise ValueError("Only signed 16-bit PCM is supported")
        self._shortenAllPauses = bool(shortenAllPauses)
        self._noiseFloor = max(0, int(noiseFloor))
        self._bytesPerSample = bytesPerSample
        self._heldSilence = bytearray()
        self._partialSample = bytearray()
        self._pendingBlock = bytearray()
        self._keepSilenceBytes = pcm_bytes_for_milliseconds(keepSilenceMs, sampleRate, bytesPerSample)
        self._blockSizeSamples = max(1, sampleRate // 200)  # 5ms blocks
        self._blockSizeBytes = self._blockSizeSamples * bytesPerSample

    def _release_held_silence(self, *, final: bool) -> bytes:
        if not self._heldSilence:
            return b""
        if final or self._shortenAllPauses:
            output = bytes(self._heldSilence[: self._keepSilenceBytes])
        else:
            output = bytes(self._heldSilence)
        self._heldSilence.clear()
        return output

    def _hold_silence(self, pcm: bytes) -> None:
        if not pcm:
            return
        if not self._shortenAllPauses:
            self._heldSilence.extend(pcm)
            return
        bytesNeeded = self._keepSilenceBytes - len(self._heldSilence)
        if bytesNeeded > 0:
            self._heldSilence.extend(pcm[:bytesNeeded])

    def _process_block(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        samples = memoryview(pcm).cast("h")
        floor = self._noiseFloor
        if not any(s < -floor or s > floor for s in samples):
            self._hold_silence(pcm)
            return b""

        # Mixed blocks contain a silence/audio transition. Preserve sample-accurate
        # boundaries here while uniform silence blocks use the cheaper fast path above.
        output = bytearray()
        runStart = 0
        runIsSilence = -self._noiseFloor <= samples[0] <= self._noiseFloor
        for sampleIndex in range(1, len(samples)):
            isSilence = -self._noiseFloor <= samples[sampleIndex] <= self._noiseFloor
            if isSilence == runIsSilence:
                continue
            run = pcm[runStart * self._bytesPerSample : sampleIndex * self._bytesPerSample]
            if runIsSilence:
                self._hold_silence(run)
            else:
                output.extend(self._release_held_silence(final=False))
                output.extend(run)
            runStart = sampleIndex
            runIsSilence = isSilence
        run = pcm[runStart * self._bytesPerSample :]
        if runIsSilence:
            self._hold_silence(run)
        else:
            output.extend(self._release_held_silence(final=False))
            output.extend(run)
        return bytes(output)

    def feed(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        if self._partialSample:
            pcm = bytes(self._partialSample) + pcm
            self._partialSample.clear()
        pcmLength = align_pcm_bytes(len(pcm), self._bytesPerSample)
        if pcmLength < len(pcm):
            self._partialSample.extend(pcm[pcmLength:])
        if pcmLength <= 0:
            return b""
        pcm = pcm[:pcmLength]
        if self._pendingBlock:
            pcm = bytes(self._pendingBlock) + pcm
            self._pendingBlock.clear()
        completeLength = len(pcm) - (len(pcm) % self._blockSizeBytes)
        if completeLength < len(pcm):
            self._pendingBlock.extend(pcm[completeLength:])
        output = bytearray()
        for blockStart in range(0, completeLength, self._blockSizeBytes):
            blockEnd = blockStart + self._blockSizeBytes
            output.extend(self._process_block(pcm[blockStart:blockEnd]))
        return bytes(output)

    def _flush(self, *, shortenPause: bool) -> bytes:
        # A partial 16-bit sample is malformed input and cannot be sent to WavePlayer.
        self._partialSample.clear()
        output = bytearray()
        if self._pendingBlock:
            output.extend(self._process_block(bytes(self._pendingBlock)))
            self._pendingBlock.clear()
        output.extend(self._release_held_silence(final=shortenPause))
        return bytes(output)

    def flush_boundary(self, *, shortenPause: bool) -> bytes:
        """Flush a hidden-segment boundary without treating it as the end of all text."""
        return self._flush(shortenPause=shortenPause)

    def finish(self) -> bytes:
        return self._flush(shortenPause=True)


class PcmLeadBuffer:
    """Hold a small initial PCM lead, then pass subsequent packets through."""

    def __init__(self, *, sampleRate: int, leadMs: int = LIVE_MULTI_SEGMENT_LEAD_MS) -> None:
        self._leadBytes = pcm_bytes_for_milliseconds(leadMs, sampleRate)
        self._buffer = bytearray()
        self._started = self._leadBytes <= 0

    def feed(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        if self._started:
            return pcm
        self._buffer.extend(pcm)
        if len(self._buffer) < self._leadBytes:
            return b""
        self._started = True
        output = bytes(self._buffer)
        self._buffer.clear()
        return output

    def finish(self) -> bytes:
        self._started = True
        if not self._buffer:
            return b""
        output = bytes(self._buffer)
        self._buffer.clear()
        return output


def create_pcm_silence_shortener(
    pauseMode: str, sampleRate: int, keepSilenceMs: int | None = None
) -> PcmSilenceShortener | None:
    if pauseMode == PAUSE_MODE_DO_NOT_SHORTEN:
        return None
    if pauseMode == PAUSE_MODE_SHORTEN_END_ONLY:
        return PcmSilenceShortener(
            sampleRate=sampleRate,
            shortenAllPauses=False,
            keepSilenceMs=keepSilenceMs if keepSilenceMs is not None else SHORTENED_SILENCE_KEEP_MS,
        )
    if pauseMode == PAUSE_MODE_SHORTEN_ALL:
        return PcmSilenceShortener(
            sampleRate=sampleRate,
            shortenAllPauses=True,
            keepSilenceMs=keepSilenceMs if keepSilenceMs is not None else SHORTENED_ALL_PAUSES_KEEP_MS,
        )
    raise ValueError(f"Unknown pause mode: {pauseMode!r}")


def short_audio_cache_key(
    text: str,
    options: dict[str, Any],
    hiddenSegments: Sequence[str] | None = None,
    pauseShorteningMode: str = PAUSE_MODE_DO_NOT_SHORTEN,
    *,
    maxChars: int = SHORT_CACHE_MAX_CHARS,
    maxHiddenSegments: int = SHORT_CACHE_MAX_HIDDEN_SEGMENTS,
) -> tuple[Any, ...] | None:
    if len(text) > maxChars:
        return None
    if hiddenSegments:
        if len(hiddenSegments) > maxHiddenSegments:
            return None
        # Hidden segments are alternate boundaries over the same spoken text, so do
        # not count their characters a second time against the content limit.
        if max(len(text), sum(len(segment) for segment in hiddenSegments)) > maxChars:
            return None
    return (
        text,
        tuple(hiddenSegments or ()),
        *(options.get(field) for field in SHORT_AUDIO_CACHE_OPTION_FIELDS),
        pauseShorteningMode,
    )


def segment_audio_cache_key(
    text: str,
    options: dict[str, Any],
    pauseShorteningMode: str,
    *,
    hasPreviousSegment: bool,
    hasNextSegment: bool,
    maxChars: int = SHORT_CACHE_MAX_CHARS,
) -> tuple[Any, ...] | None:
    """Build a cache key for PCM whose hidden-boundary context is complete."""
    if not text or len(text) > maxChars:
        return None
    # Tempo and post-pitch processors carry overlap state across hidden segment
    # boundaries. Their PCM cannot be safely reused as an independent prefix.
    try:
        artificialRate = float(options.get("artificialRate", 1))
        postPitch = float(options.get("postPitch", 1))
    except (TypeError, ValueError):
        return None
    if abs(artificialRate - 1) >= 0.001 or abs(postPitch - 1) >= 0.001:
        return None
    return (
        "segment-v1",
        text,
        *(options.get(field) for field in SHORT_AUDIO_CACHE_OPTION_FIELDS),
        pauseShorteningMode,
        bool(hasPreviousSegment),
        bool(hasNextSegment),
    )


def is_complete_speech_result(result: Any, *, expectedSegmentEnds: int = 0) -> bool:
    """Return whether a bridge result is structurally complete for PCM caching."""
    if expectedSegmentEnds < 0 or not isinstance(result, dict):
        return False
    try:
        segmentEnds = int(result.get("segmentEnds", 0))
    except (TypeError, ValueError):
        return False
    return (
        result.get("success") is True
        and result.get("done") is True
        and not result.get("cancelled")
        and segmentEnds == expectedSegmentEnds
    )


@lru_cache(maxsize=4096)
def _unicode_name(character: str) -> str:
    return unicodedata.name(character, "")


@lru_cache(maxsize=4096)
def _is_sentence_terminator_character(character: str) -> bool:
    return ord(character) in SENTENCE_TERMINAL_CODEPOINTS or character in TAILORED_SENTENCE_TERMINATORS


def is_sentence_terminator_character(character: str) -> bool:
    """Return True if *character* is a Unicode sentence terminator.

    Public wrapper that covers both UCD Sentence_Terminal codepoints and
    the add-on's tailored tailoring (e.g. ellipsis, Thai Angular punctuation,
    Greek question mark).
    """
    return _is_sentence_terminator_character(character)


@lru_cache(maxsize=4096)
def _is_soft_break_character(character: str) -> bool:
    if character in SOFT_BREAK_CHARS:
        return True
    if character in ASCII_SENTENCE_TERMINATORS:
        return False
    if _is_sentence_terminator_character(character):
        return True
    category = unicodedata.category(character)
    if character in NON_BREAKING_SOFT_PUNCTUATION:
        return False
    name = _unicode_name(character)
    if any(part in name for part in UNICODE_INITIAL_PUNCTUATION_NAME_PARTS):
        return False
    if any(part in name for part in NON_BREAKING_SOFT_PUNCTUATION_NAME_PARTS):
        return False
    if category in {"Pd", "Po"}:
        return True
    return any(part in name for part in UNICODE_SOFT_BREAK_NAME_PARTS)


@lru_cache(maxsize=4096)
def _is_colon_like_character(character: str) -> bool:
    return character in ":：" or "COLON" in _unicode_name(character)


@lru_cache(maxsize=4096)
def _is_dash_like_character(character: str) -> bool:
    return character in "\u2013\u2014" or "DASH" in _unicode_name(character)


@lru_cache(maxsize=1024)
def _is_sentence_trailing_closer(character: str) -> bool:
    return (
        character in SENTENCE_TRAILING_CLOSERS
        or "\u2018" <= character <= "\u201f"
        or unicodedata.category(character) in {"Pe", "Pf"}
    )


def _is_no_space_script_character(character: str) -> bool:
    if character.isascii():
        return False
    codepoint = ord(character)
    category = unicodedata.category(character)
    if not (category.startswith("L") or category.startswith("M")):
        return False
    return _find_no_space_range(codepoint) is not None


class TextSegmenter:
    """Unicode-aware latency segmenter independent of NVDA speech commands."""

    def iter_indexed_text_segments(
        self,
        text: str,
        indexes: list[tuple[Any, int]],
        fastFirstSegment: bool,
    ) -> Iterator[tuple[str, list[tuple[Any, int]]]]:
        if not indexes:
            for segment in self.iter_text_segments_for_latency(text, fastFirstSegment):
                yield segment, []
            return
        segments: list[tuple[str, int, int]] = []
        searchStart = 0
        for segment in self.iter_text_segments_for_latency(text, fastFirstSegment):
            segmentStart = text.find(segment, searchStart)
            if segmentStart < 0:
                segmentStart = searchStart
            segmentEnd = segmentStart + len(segment)
            segments.append((segment, segmentStart, segmentEnd))
            searchStart = segmentEnd
        if not segments:
            return
        indexPosition = 0
        for segmentPosition, (segment, segmentStart, segmentEnd) in enumerate(segments):
            segmentIndexes: list[tuple[Any, int]] = []
            while indexPosition < len(indexes) and indexes[indexPosition][1] <= segmentEnd:
                index, charOffset = indexes[indexPosition]
                segmentIndexes.append((index, max(0, min(len(segment), charOffset - segmentStart))))
                indexPosition += 1
            if segmentPosition == len(segments) - 1:
                while indexPosition < len(indexes):
                    index, _charOffset = indexes[indexPosition]
                    segmentIndexes.append((index, len(segment)))
                    indexPosition += 1
            yield segment, segmentIndexes

    def split_text_for_latency(self, text: str) -> list[str]:
        return list(self.iter_text_segments_for_latency(text, False))

    def sanitize_speech_text(self, text: str) -> str:
        if not text:
            return text
        return "".join(" " if unicodedata.category(character) == "Co" else character for character in text)

    def spoken_bridge_segments(self, segments: list[str]) -> list[str]:
        spokenSegments: list[str] = []
        for segment in segments:
            if (
                spokenSegments
                and spokenSegments[-1]
                and segment
                and self._needs_spoken_segment_space(spokenSegments[-1][-1], segment[0])
            ):
                spokenSegments[-1] += " "
            spokenSegments.append(segment)
        return spokenSegments

    def _needs_spoken_segment_space(self, previousCharacter: str, nextCharacter: str) -> bool:
        if not previousCharacter.isalnum() or not nextCharacter.isalnum():
            return False
        return not (_is_no_space_script_character(previousCharacter) or _is_no_space_script_character(nextCharacter))

    def find_sentence_splits(self, text: str) -> list[int]:
        splits: list[int] = []
        index = 0
        while index < len(text):
            terminatorStart = index
            terminator = text[index]
            if not _is_sentence_terminator_character(terminator):
                index += 1
                continue
            index += 1
            while index < len(text) and _is_sentence_terminator_character(text[index]):
                index += 1
            terminatorEnd = index
            while index < len(text) and _is_sentence_trailing_closer(text[index]):
                index += 1
            whitespaceStart = index
            while index < len(text) and text[index].isspace():
                index += 1
            trailingWhitespace = text[whitespaceStart:index]
            if index == len(text):
                continue
            if self._sentence_terminator_stays_with_token(text, terminatorStart, terminatorEnd, terminator):
                continue
            if terminator in ASCII_SENTENCE_TERMINATORS + ";":
                if not trailingWhitespace:
                    continue
            else:
                splits.append(index)
                continue
            splits.append(index)
        return splits

    def _sentence_terminator_stays_with_token(
        self,
        text: str,
        terminatorStart: int,
        terminatorEnd: int,
        terminator: str,
    ) -> bool:
        before = text[terminatorStart - 1] if terminatorStart > 0 else ""
        after = text[terminatorEnd] if terminatorEnd < len(text) else ""
        if before.isdigit() and after.isdigit():
            return True
        if terminator == ".":
            return self._period_stays_with_previous_token(text, terminatorStart)
        return False

    def _period_stays_with_previous_token(self, text: str, periodIndex: int) -> bool:
        if self._period_is_numeric_separator(text, periodIndex):
            return True
        wordStart = periodIndex - 1
        while wordStart >= 0 and text[wordStart].isalnum():
            wordStart -= 1
        wordBefore = text[wordStart + 1 : periodIndex].lower()
        if len(wordBefore) == 1 and wordBefore.isalpha() and wordBefore.isascii():
            return True
        if wordBefore in COMMON_ABBREVIATIONS:
            return True
        return wordBefore.isalpha() and wordStart >= 0 and text[wordStart] == "."

    def _period_is_numeric_separator(self, text: str, periodIndex: int) -> bool:
        before = text[periodIndex - 1] if periodIndex > 0 else ""
        after = text[periodIndex + 1] if periodIndex + 1 < len(text) else ""
        return before.isdigit() and after.isdigit()

    def iter_text_segments_for_latency(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
        if not text.strip():
            return
        splits = self.find_sentence_splits(text)
        chunkStart = 0
        firstYield = True
        for endIndex in splits + [len(text)]:
            candidate = text[chunkStart:endIndex].strip()
            if not candidate:
                continue
            targetLength = (
                FAST_FIRST_SEGMENT_MIN_CHARS if (firstYield and fastFirstSegment) else REGULAR_SEGMENT_MIN_CHARS
            )
            if len(candidate) >= targetLength or endIndex == len(text):
                for segment in self._iter_forced_latency_segments(
                    candidate,
                    firstYield and fastFirstSegment,
                ):
                    yield segment
                    firstYield = False
                chunkStart = endIndex

    def _iter_forced_latency_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
        remaining = text.strip()
        if len(remaining) <= SEAMLESS_UTTERANCE_MAX_CHARS:
            yield from self._iter_soft_phrase_segments(remaining, fastFirstSegment)
            return
        firstYield = fastFirstSegment
        while remaining:
            if self.looks_like_url_token(remaining):
                maxLength = min(URL_TOKEN_SEGMENT_MAX_CHARS, FORCED_SEGMENT_HARD_MAX_CHARS)
            else:
                maxLength = FAST_FIRST_SEGMENT_MAX_CHARS if firstYield else REGULAR_SEGMENT_MAX_CHARS
            noSpaceLimit = self._no_space_script_segment_limit(remaining, maxLength)
            if noSpaceLimit is not None:
                maxLength = min(maxLength, noSpaceLimit)
            if len(remaining) <= maxLength:
                yield remaining
                return
            cut = self._find_forced_latency_cut(remaining, maxLength)
            segment = remaining[:cut].strip()
            if segment:
                yield segment
            remaining = remaining[cut:].strip()
            firstYield = False

    def _min_orphan_chars(self, text: str) -> int:
        if self._no_space_script_segment_limit(text, SOFT_PHRASE_SEGMENT_MAX_CHARS) is not None:
            return MIN_ORPHAN_NO_SPACE_CHARS
        return MIN_ORPHAN_SPACE_CHARS

    def _iter_soft_phrase_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
        remaining = text.strip()
        firstSegment = fastFirstSegment
        noSpaceSegmentation = (
            len(remaining) > SOFT_PHRASE_SEGMENT_MAX_CHARS
            and self._no_space_script_segment_limit(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS) is not None
        )
        while (
            len(remaining) > SOFT_PHRASE_SEGMENT_MAX_CHARS
            or noSpaceSegmentation
            or (
                firstSegment
                and len(remaining) > FAST_FIRST_SEGMENT_TRIGGER_CHARS
                and not self.looks_like_url_token(remaining)
            )
        ):
            noSpaceLimit = (
                self._no_space_script_segment_limit(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS)
                if noSpaceSegmentation
                else None
            )
            if noSpaceLimit is None:
                noSpaceSegmentation = False
                if len(remaining) <= SOFT_PHRASE_SEGMENT_MAX_CHARS and not (
                    firstSegment
                    and len(remaining) > FAST_FIRST_SEGMENT_TRIGGER_CHARS
                    and not self.looks_like_url_token(remaining)
                ):
                    break
            if noSpaceLimit is not None and len(remaining) <= noSpaceLimit:
                break
            isNoSpace = (
                noSpaceLimit is not None
                or self._no_space_script_segment_limit(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS) is not None
            )
            minOrphan = MIN_ORPHAN_NO_SPACE_CHARS if isNoSpace else MIN_ORPHAN_SPACE_CHARS
            puncFreeTrigger = (
                FAST_FIRST_PUNCTUATION_FREE_NO_SPACE_TRIGGER_CHARS
                if isNoSpace
                else FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS
            )
            fastFirstCut = (
                firstSegment
                and len(remaining) > FAST_FIRST_SEGMENT_TRIGGER_CHARS
                and not self.looks_like_url_token(remaining)
            )
            if noSpaceLimit is not None:
                cut = self._find_no_space_script_cut(remaining, noSpaceLimit, minOrphanChars=minOrphan)
            elif fastFirstCut:
                cut = self._find_soft_phrase_cut(remaining, True, minOrphanChars=minOrphan)
                if cut is None:
                    if len(remaining) <= puncFreeTrigger:
                        break
                    cut = self._find_whitespace_cut(
                        remaining,
                        FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS,
                        FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS,
                        FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD,
                        minOrphanChars=minOrphan,
                        preferredTarget=FAST_FIRST_PREFERRED_WHITESPACE_CHARS,
                    )
                if cut is None and len(remaining) > puncFreeTrigger:
                    cut = self._find_forced_latency_cut(remaining, FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
            else:
                cut = self._find_soft_phrase_cut(remaining, False, minOrphanChars=minOrphan)
                if cut is None:
                    cut = self._find_whitespace_cut(
                        remaining,
                        SOFT_PHRASE_SEGMENT_MIN_CHARS,
                        SOFT_PHRASE_SEGMENT_MAX_CHARS,
                        SOFT_PHRASE_SEGMENT_LOOKAHEAD,
                        minOrphanChars=minOrphan,
                    )
                if cut is None and len(remaining) > SOFT_PHRASE_SEGMENT_MAX_CHARS:
                    cut = self._find_forced_latency_cut(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS)
            if cut is None:
                cut = min(len(remaining), FORCED_SEGMENT_HARD_MAX_CHARS)
            segment = remaining[:cut].strip()
            if segment:
                yield segment
            nextRemaining = remaining[cut:].strip()
            if nextRemaining == remaining:
                yield nextRemaining
                return
            remaining = nextRemaining
            firstSegment = False
        if remaining:
            yield remaining

    def _find_soft_phrase_cut(
        self,
        text: str,
        fastFirstSegment: bool = False,
        minOrphanChars: int = 0,
    ) -> int | None:
        if fastFirstSegment:
            minLength = min(len(text), FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS)
            maxLength = min(len(text), FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
            preferred = min(len(text), FAST_FIRST_PREFERRED_SOFT_CHARS)
            lookahead = FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD
            if preferred > minLength:
                for index in range(preferred, minLength - 1, -1):
                    if (
                        not minOrphanChars or len(text) - index >= minOrphanChars
                    ) and self._is_contextual_soft_phrase_cut(text, index):
                        return index
            for index in range(maxLength, preferred, -1):
                if (not minOrphanChars or len(text) - index >= minOrphanChars) and self._is_contextual_soft_phrase_cut(
                    text, index
                ):
                    return index
            lookaheadEnd = min(len(text), maxLength + lookahead)
            for index in range(maxLength, lookaheadEnd):
                candidate = index + 1
                if (
                    not minOrphanChars or len(text) - candidate >= minOrphanChars
                ) and self._is_contextual_soft_phrase_cut(text, candidate):
                    return candidate
            return None
        minLength = min(len(text), SOFT_PHRASE_SEGMENT_MIN_CHARS)
        maxLength = min(len(text), SOFT_PHRASE_SEGMENT_MAX_CHARS)
        lookahead = SOFT_PHRASE_SEGMENT_LOOKAHEAD
        for index in range(maxLength, minLength - 1, -1):
            if (not minOrphanChars or len(text) - index >= minOrphanChars) and self._is_contextual_soft_phrase_cut(
                text, index
            ):
                return index
        lookaheadEnd = min(len(text), maxLength + lookahead)
        for index in range(maxLength, lookaheadEnd):
            candidate = index + 1
            if (not minOrphanChars or len(text) - candidate >= minOrphanChars) and self._is_contextual_soft_phrase_cut(
                text, candidate
            ):
                return candidate
        return None

    def _find_whitespace_cut(
        self,
        text: str,
        minLength: int,
        maxLength: int,
        lookahead: int,
        minOrphanChars: int = 0,
        preferredTarget: int | None = None,
    ) -> int | None:
        minLength = min(len(text), minLength)
        maxLength = min(len(text), maxLength)
        target = min(maxLength, preferredTarget) if preferredTarget is not None else maxLength
        for index in range(target, minLength - 1, -1):
            if text[index - 1].isspace() and (not minOrphanChars or len(text) - index >= minOrphanChars):
                return index
        if target < maxLength:
            for index in range(target + 1, maxLength + 1):
                if text[index - 1].isspace() and (not minOrphanChars or len(text) - index >= minOrphanChars):
                    return index
        lookaheadEnd = min(len(text), maxLength + lookahead)
        for index in range(maxLength, lookaheadEnd):
            if text[index].isspace() and (not minOrphanChars or len(text) - (index + 1) >= minOrphanChars):
                return index
        return self._find_no_space_script_cut(text, maxLength, minOrphanChars=minOrphanChars)

    def _find_forced_latency_cut(self, text: str, maxLength: int) -> int:
        if len(text) <= maxLength:
            return len(text)
        minLength = min(maxLength, max(FORCED_SEGMENT_MIN_CHARS, int(maxLength * 0.55)))
        for index in range(maxLength, minLength - 1, -1):
            if self._is_contextual_soft_phrase_cut(text, index):
                return index
        for index in range(maxLength, minLength - 1, -1):
            if text[index - 1].isspace():
                return index
        lookaheadEnd = min(len(text), maxLength + FORCED_SEGMENT_FORWARD_LOOKAHEAD)
        for index in range(maxLength, lookaheadEnd):
            if text[index].isspace():
                return index
        noSpaceCut = self._find_no_space_script_cut(text, maxLength)
        if noSpaceCut is not None:
            return noSpaceCut
        urlBreakCharacters = "/\\?&=#%._-~:"
        for index in range(maxLength, minLength - 1, -1):
            if text[index - 1] in urlBreakCharacters:
                return index
        for index in range(maxLength, lookaheadEnd):
            if text[index] in urlBreakCharacters:
                return index + 1
        if text[maxLength - 1].isalnum() and text[maxLength].isalnum():
            wordEnd = min(len(text), FORCED_SEGMENT_HARD_MAX_CHARS)
            for index in range(maxLength, wordEnd):
                if not text[index].isalnum():
                    return index
        return maxLength

    def _find_no_space_script_cut(
        self,
        text: str,
        maxLength: int,
        minOrphanChars: int = 0,
    ) -> int | None:
        segmentLimit = self._no_space_script_segment_limit(text, maxLength)
        if segmentLimit is None:
            return None
        target = min(len(text), maxLength, max(FORCED_SEGMENT_MIN_CHARS, segmentLimit))
        for index in range(target, FORCED_SEGMENT_MIN_CHARS - 1, -1):
            if (not minOrphanChars or len(text) - index >= minOrphanChars) and self._is_contextual_soft_phrase_cut(
                text, index
            ):
                return index
        cut = self._extend_cut_over_combining_marks(
            text,
            target,
            min(len(text), maxLength + NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD),
        )
        if minOrphanChars and len(text) - cut < minOrphanChars and len(text) <= maxLength:
            return None
        return cut

    def _no_space_script_segment_limit(self, text: str, maxLength: int) -> int | None:
        sample = text[: min(len(text), maxLength)]
        if not sample or sample.isascii():
            return None
        signalCharacters = 0
        noSpaceCharacters = 0
        segmentLimit: int | None = None
        for character in sample:
            category = unicodedata.category(character)
            if category.startswith("L") or category.startswith("M"):
                signalCharacters += 1
                codepoint = ord(character)
                rangeEntry = _find_no_space_range(codepoint)
                if rangeEntry is not None:
                    noSpaceCharacters += 1
                    limit = rangeEntry[2]
                    segmentLimit = limit if segmentLimit is None else min(segmentLimit, limit)
        if not signalCharacters:
            return None
        if noSpaceCharacters < NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS:
            return None
        if noSpaceCharacters / signalCharacters < NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO:
            return None
        return segmentLimit

    def _extend_cut_over_combining_marks(self, text: str, cut: int, maxCut: int) -> int:
        while cut < maxCut and unicodedata.category(text[cut]).startswith("M"):
            cut += 1
        return cut

    def looks_like_url_token(self, text: str) -> bool:
        if any(character.isspace() for character in text):
            return False
        return "://" in text or "/" in text or "\\" in text

    def _is_forced_soft_break(self, text: str, index: int) -> bool:
        character = text[index - 1]
        before = text[index - 2] if index >= 2 else ""
        after = text[index] if index < len(text) else ""
        if before.isdigit() and after.isdigit():
            return False
        if _is_colon_like_character(character) and character == ":" and after in "/\\":
            if index == 2 and text[0].isalpha():
                return False
            schemeStart = index - 2
            while schemeStart >= 0 and (text[schemeStart].isalnum() or text[schemeStart] in "+-."):
                schemeStart -= 1
            scheme = text[schemeStart + 1 : index - 1]
            if scheme and scheme[0].isalpha() and text[index : index + 2] == "//":
                return False
        return not (_is_dash_like_character(character) and before.isalnum() and after.isalnum())

    def _is_contextual_soft_phrase_cut(self, text: str, index: int) -> bool:
        if index <= 0 or index > len(text):
            return False
        return _is_soft_break_character(text[index - 1]) and self._is_forced_soft_break(text, index)

    def should_pause_after_segment(self, segment: str) -> bool:
        stripped = segment.rstrip()
        while stripped and _is_sentence_trailing_closer(stripped[-1]):
            stripped = stripped[:-1].rstrip()
        return bool(stripped) and _is_sentence_terminator_character(stripped[-1])


DEFAULT_TEXT_SEGMENTER = TextSegmenter()

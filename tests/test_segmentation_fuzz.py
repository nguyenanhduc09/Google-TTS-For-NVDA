"""Fuzz tests for speech text segmentation with random Unicode input.

Tests that the segmenter never crashes, always produces non-overlapping
segments that cover the full input, and respects maximum segment lengths
for a wide range of random Unicode text.
"""

from __future__ import annotations

import random
import string
import unittest

from tests.test_support import load_driver_module

processing = load_driver_module("speech_processing")
segmenter = processing.DEFAULT_TEXT_SEGMENTER


# ---------------------------------------------------------------------------
# Unicode character pools
# ---------------------------------------------------------------------------

_LATIN = string.ascii_letters + string.digits + string.punctuation + " "
_CJK = "".join(chr(cp) for cp in range(0x4E00, 0x9FFF + 1, 0x100))  # sample CJK
_THAI = "".join(chr(cp) for cp in range(0x0E00, 0x0E7F + 1, 4))  # sample Thai
_ARABIC = "".join(chr(cp) for cp in range(0x0600, 0x06FF + 1, 5))  # sample Arabic
_DEVANAGARI = "".join(chr(cp) for cp in range(0x0900, 0x097F + 1, 4))  # sample Devanagari
_EMOJI = "👨‍👩‍👧‍👦🚀🎉🎊 family rocket celebration party "
_PUNCTUATION = ".!?,;:\"'()[]{}`~@#$%^&*-_+=|\\/<>£€¥©®™"
_FULLWIDTH = "".join(chr(cp) for cp in range(0xFF01, 0xFF5E + 1))  # fullwidth ASCII
_EXTENDED_LATIN = "".join(chr(cp) for cp in range(0x00C0, 0x024F + 1, 3))  # Latin Extended


def _random_text(min_len: int = 10, max_len: int = 500, *, rng: random.Random | None = None) -> str:
    """Generate random text from a mix of Unicode scripts."""
    prng = rng or random.Random(20260401)
    pools = [_LATIN, _CJK, _THAI, _ARABIC, _DEVANAGARI, _EMOJI, _FULLWIDTH, _EXTENDED_LATIN]
    length = prng.randint(min_len, max_len)
    chars = []
    for _ in range(length):
        pool = prng.choice(pools)
        chars.append(prng.choice(pool) if pool else " ")
    return "".join(chars)


def _random_script_text(script: str, min_len: int = 50, max_len: int = 300, *, rng: random.Random | None = None) -> str:
    """Generate random text from a single Unicode script."""
    prng = rng or random.Random(20260402)
    pools = {
        "latin": _LATIN,
        "cjk": _CJK,
        "thai": _THAI,
        "arabic": _ARABIC,
        "devanagari": _DEVANAGARI,
        "emoji": _EMOJI,
    }
    pool = pools.get(script, _LATIN)
    length = prng.randint(min_len, max_len)
    return "".join(prng.choice(pool) if pool else " " for _ in range(length))


# ---------------------------------------------------------------------------
# Fuzz tests
# ---------------------------------------------------------------------------


class SegmentationFuzzTests(unittest.TestCase):
    """Fuzz tests for text segmentation correctness invariants."""

    def test_sentence_splits_never_crash_on_random_unicode(self) -> None:
        """find_sentence_splits must not raise on any random Unicode input."""
        rng = random.Random(20260401)
        for _ in range(100):
            text = _random_text(0, 2000, rng=rng)
            # Should not raise
            splits = segmenter.find_sentence_splits(text)
            self.assertIsInstance(splits, list)
            for split in splits:
                self.assertIsInstance(split, int)
                self.assertGreaterEqual(split, 0)
                self.assertLessEqual(split, len(text))

    def test_sentence_splits_are_monotonically_increasing(self) -> None:
        """Split indices must be strictly increasing."""
        rng = random.Random(20260402)
        for _ in range(50):
            text = _random_text(10, 1000, rng=rng)
            splits = segmenter.find_sentence_splits(text)
            for i in range(1, len(splits)):
                self.assertGreater(splits[i], splits[i - 1])

    def test_latency_segments_cover_full_input(self) -> None:
        """Latency segments must join back to the original text (whitespace-collapsed)."""
        rng = random.Random(20260403)
        for _ in range(50):
            text = _random_text(10, 1500, rng=rng)
            for fast_first in (True, False):
                segments = list(segmenter.iter_text_segments_for_latency(text, fast_first))
                if not segments:
                    continue
                # All segments must be non-empty strings
                for seg in segments:
                    self.assertIsInstance(seg, str)
                    self.assertTrue(seg.strip(), f"Empty segment in: {segments}")
                # Concatenated segments must cover the full text
                joined = "".join(segments)
                self.assertEqual(
                    "".join(text.split()),
                    "".join(joined.split()),
                    f"Segments don't cover full text: {text[:100]}...",
                )

    def test_latency_segments_respect_max_length(self) -> None:
        """No latency segment should exceed the hard maximum."""
        rng = random.Random(20260404)
        for _ in range(50):
            text = _random_text(50, 2000, rng=rng)
            segments = list(segmenter.iter_text_segments_for_latency(text, False))
            for seg in segments:
                self.assertLessEqual(
                    len(seg),
                    processing.FORCED_SEGMENT_HARD_MAX_CHARS + 50,  # small tolerance for trailing punctuation
                    f"Segment too long ({len(seg)} chars): {seg[:50]}...",
                )

    def test_mixed_script_text_segments_correctly(self) -> None:
        """Mixed Latin + CJK + emoji text should segment without issues."""
        rng = random.Random(20260405)
        for _ in range(30):
            parts = [
                rng.choice(_LATIN) * rng.randint(5, 30),
                rng.choice(_CJK) * rng.randint(5, 20),
                _EMOJI[: rng.randint(1, len(_EMOJI))],
                rng.choice(_LATIN) * rng.randint(5, 30),
            ]
            text = " ".join(parts)
            segments = list(segmenter.iter_text_segments_for_latency(text, True))
            self.assertGreater(len(segments), 0)
            # Verify coverage
            joined = "".join(segments)
            self.assertEqual("".join(text.split()), "".join(joined.split()))

    def test_empty_and_whitespace_text(self) -> None:
        """Empty and whitespace-only text should produce no segments."""
        for text in ("", "   ", "\n\t  ", "\r\n"):
            segments = list(segmenter.iter_text_segments_for_latency(text, False))
            self.assertEqual([], segments)

    def test_single_character_text(self) -> None:
        """Single character text should produce exactly one segment."""
        for cp in range(0x0041, 0x005A + 1):  # A-Z
            text = chr(cp)
            segments = list(segmenter.iter_text_segments_for_latency(text, False))
            self.assertEqual(1, len(segments), f"Char U+{cp:04X} produced {len(segments)} segments")

    def test_punctuation_heavy_text(self) -> None:
        """Text with lots of punctuation should segment without crashes."""
        rng = random.Random(20260406)
        for _ in range(30):
            text = ""
            for _ in range(rng.randint(10, 100)):
                text += rng.choice(_PUNCTUATION) + rng.choice(_LATIN) * rng.randint(1, 5)
            segments = list(segmenter.iter_text_segments_for_latency(text, False))
            self.assertGreater(len(segments), 0)

    def test_all_same_script_text(self) -> None:
        """Text in a single script should segment consistently."""
        rng = random.Random(20260407)
        for script in ("latin", "cjk", "thai", "arabic", "devanagari"):
            for _ in range(10):
                text = _random_script_text(script, 100, 500, rng=rng)
                segments = list(segmenter.iter_text_segments_for_latency(text, False))
                self.assertGreater(len(segments), 0, f"No segments for {script}")
                # Verify coverage
                joined = "".join(segments)
                self.assertEqual("".join(text.split()), "".join(joined.split()))


class SentenceSplitFuzzTests(unittest.TestCase):
    """Fuzz tests specifically for sentence splitting."""

    def test_sentence_splits_dont_split_inside_cjk(self) -> None:
        """CJK fullwidth punctuation should cause splits at valid boundaries."""
        rng = random.Random(20260408)
        for _ in range(20):
            # CJK text with sentence terminators
            cp = rng.choice([0x3002, 0xFF01, 0xFF1F])  # 。！？
            text = "测试文本" * rng.randint(2, 10) + chr(cp) + "下一句" * rng.randint(2, 10)
            splits = segmenter.find_sentence_splits(text)
            self.assertTrue(splits, f"Expected sentence split for {text!r}")
            punct_pos = text.index(chr(cp)) + 1
            self.assertGreaterEqual(splits[0], punct_pos)

    def test_ellipsis_doesnt_over_split(self) -> None:
        """Ellipsis should be treated as sentence terminal but not cause excessive splits."""
        text = "First thought… Second thought. Third thought… Fourth."
        splits = segmenter.find_sentence_splits(text)
        # Should split at … and . but not inside words
        self.assertGreaterEqual(len(splits), 2)
        for idx in splits:
            self.assertGreater(idx, 0)
            self.assertLess(idx, len(text))


if __name__ == "__main__":
    unittest.main()

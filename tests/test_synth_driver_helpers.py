"""Tests for pure helper functions in SynthDriver and audio_math.

These helpers are tested standalone without importing NVDA; live SynthDriver and
GlobalPlugin integration hooks are exercised directly in test_nvda_integration_shims.py.
"""

from __future__ import annotations

import unittest

from tests.test_support import load_driver_module

_audio_math = load_driver_module("audio_math")
_language_profiles = load_driver_module("language_profiles")
_LANGUAGE_WORD_RE = _language_profiles.LANGUAGE_WORD_RE

_BREAK_RATE_TABLE = _audio_math.BREAK_RATE_TABLE
_BREAK_RATE_FACTOR_MIN = _audio_math.BREAK_RATE_FACTOR_MIN
_BREAK_RATE_FACTOR_MAX = _audio_math.BREAK_RATE_FACTOR_MAX
_END_OF_UTTERANCE_RATE_FACTOR_MIN = _audio_math.END_OF_UTTERANCE_RATE_FACTOR_MIN
_END_OF_UTTERANCE_RATE_FACTOR_MAX = _audio_math.END_OF_UTTERANCE_RATE_FACTOR_MAX
_interpolate_rate_factor = _audio_math.interpolate_rate_factor
_break_rate_factor = _audio_math.break_rate_factor
_end_of_utterance_rate_factor = _audio_math.end_of_utterance_rate_factor


# ---------------------------------------------------------------------------
# _interpolate_rate_factor
# ---------------------------------------------------------------------------


class InterpolateRateFactorTests(unittest.TestCase):
    """Verify linear interpolation over (rate, factor) lookup tables."""

    def test_below_table_minimum(self) -> None:
        """Rate below the first entry returns the first factor."""
        result = _interpolate_rate_factor(0, _BREAK_RATE_TABLE)
        self.assertEqual(1.8, result)

    def test_at_table_minimum(self) -> None:
        """Rate equal to the first entry returns the first factor."""
        result = _interpolate_rate_factor(10, _BREAK_RATE_TABLE)
        self.assertEqual(1.8, result)

    def test_above_table_maximum(self) -> None:
        """Rate above the last entry returns the last factor."""
        result = _interpolate_rate_factor(100, _BREAK_RATE_TABLE)
        self.assertEqual(0.4, result)

    def test_at_table_maximum(self) -> None:
        """Rate equal to the last entry returns the last factor."""
        result = _interpolate_rate_factor(85, _BREAK_RATE_TABLE)
        self.assertEqual(0.4, result)

    def test_midpoint_interpolation(self) -> None:
        """Rate between two entries is linearly interpolated."""
        # Between (60, 1.0) and (75, 0.7): rate 67.5 should give 0.85
        result = _interpolate_rate_factor(67, _BREAK_RATE_TABLE)
        self.assertAlmostEqual(0.86, result, places=2)

    def test_custom_table(self) -> None:
        """Works with arbitrary tables."""
        table = ((0, 0.0), (100, 1.0))
        self.assertEqual(0.0, _interpolate_rate_factor(-10, table))
        self.assertEqual(0.5, _interpolate_rate_factor(50, table))
        self.assertEqual(1.0, _interpolate_rate_factor(200, table))


# ---------------------------------------------------------------------------
# _break_rate_factor
# ---------------------------------------------------------------------------


class BreakRateFactorTests(unittest.TestCase):
    """Verify _break_rate_factor clamps output to [MIN, MAX]."""

    def test_low_rate_returns_high_factor(self) -> None:
        """Low rates (slow speech) should get longer break factors."""
        result = _break_rate_factor(10)
        self.assertGreaterEqual(result, _BREAK_RATE_FACTOR_MIN)
        self.assertGreaterEqual(result, 1.0)

    def test_high_rate_returns_low_factor(self) -> None:
        """High rates (fast speech) should get shorter break factors."""
        result = _break_rate_factor(85)
        self.assertLessEqual(result, _BREAK_RATE_FACTOR_MAX)
        self.assertLessEqual(result, 1.0)

    def test_neutral_rate(self) -> None:
        """Rate 60 (neutral) should return factor 1.0."""
        result = _break_rate_factor(60)
        self.assertAlmostEqual(1.0, result, places=2)

    def test_clamped_at_table_boundaries(self) -> None:
        """Extreme rates clamp to BREAK_RATE_FACTOR_MAX (slow) and BREAK_RATE_FACTOR_MIN (fast)."""
        self.assertEqual(_BREAK_RATE_FACTOR_MAX, _break_rate_factor(0))
        self.assertEqual(_BREAK_RATE_FACTOR_MIN, _break_rate_factor(200))


# ---------------------------------------------------------------------------
# _end_of_utterance_rate_factor
# ---------------------------------------------------------------------------


class EndOfUtteranceRateFactorTests(unittest.TestCase):
    """Verify _end_of_utterance_rate_factor clamps output to [MIN, MAX]."""

    def test_low_rate(self) -> None:
        self.assertEqual(_END_OF_UTTERANCE_RATE_FACTOR_MAX, _end_of_utterance_rate_factor(10))

    def test_high_rate(self) -> None:
        self.assertEqual(_END_OF_UTTERANCE_RATE_FACTOR_MIN, _end_of_utterance_rate_factor(85))

    def test_neutral_rate(self) -> None:
        result = _end_of_utterance_rate_factor(60)
        self.assertAlmostEqual(1.0, result, places=2)


# ---------------------------------------------------------------------------
# _LANGUAGE_WORD_RE
# ---------------------------------------------------------------------------


class LanguageWordRegexTests(unittest.TestCase):
    """Verify the language word regex matches expected patterns."""

    def test_matches_simple_word(self) -> None:
        self.assertIsNotNone(_LANGUAGE_WORD_RE.match("hello"))

    def test_matches_word_with_apostrophe(self) -> None:
        self.assertIsNotNone(_LANGUAGE_WORD_RE.match("don't"))

    def test_matches_word_with_hyphen(self) -> None:
        self.assertIsNotNone(_LANGUAGE_WORD_RE.match("self-aware"))

    def test_matches_non_ascii_word(self) -> None:
        self.assertIsNotNone(_LANGUAGE_WORD_RE.match("Tiếng"))

    def test_no_match_for_digits_only(self) -> None:
        self.assertIsNone(_LANGUAGE_WORD_RE.match("123"))


if __name__ == "__main__":
    unittest.main()

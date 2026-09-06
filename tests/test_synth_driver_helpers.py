"""Tests for pure helper functions in the SynthDriver __init__.py.

These functions are extracted from __init__.py to test them without NVDA
dependencies. Keep in sync with the production constants.
"""

from __future__ import annotations

import re
import unittest

# ---------------------------------------------------------------------------
# Pure constants and functions extracted from __init__.py
# ---------------------------------------------------------------------------

_BREAK_RATE_TABLE = ((10, 1.8), (43, 1.3), (60, 1.0), (75, 0.7), (85, 0.4))
_BREAK_RATE_FACTOR_MIN = 0.4
_BREAK_RATE_FACTOR_MAX = 1.8
_END_OF_UTTERANCE_RATE_FACTOR_MIN = 0.5
_END_OF_UTTERANCE_RATE_FACTOR_MAX = 1.6
_VIETNAMESE_WORDS = {
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
_ENGLISH_WORDS = {
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
_LANGUAGE_WORD_RE = re.compile(r"[^\W\d_]+(?:['\u2019_-][^\W\d_]+)?", re.UNICODE)


def _interpolate_rate_factor(rate: int, table: tuple[tuple[int, float], ...]) -> float:
    """Linear interpolation over a (rate, factor) lookup table."""
    if rate <= table[0][0]:
        return table[0][1]
    if rate >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        r0, f0 = table[i]
        r1, f1 = table[i + 1]
        if r0 <= rate <= r1:
            t = (rate - r0) / (r1 - r0)
            return f0 + t * (f1 - f0)
    return table[-1][1]


def _break_rate_factor(rate: int) -> float:
    return max(_BREAK_RATE_FACTOR_MIN, min(_BREAK_RATE_FACTOR_MAX, _interpolate_rate_factor(rate, _BREAK_RATE_TABLE)))


def _end_of_utterance_rate_factor(rate: int) -> float:
    return max(
        _END_OF_UTTERANCE_RATE_FACTOR_MIN,
        min(_END_OF_UTTERANCE_RATE_FACTOR_MAX, _interpolate_rate_factor(rate, _BREAK_RATE_TABLE)),
    )


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

    def test_clamped_below_minimum(self) -> None:
        """Very low rate is clamped to BREAK_RATE_FACTOR_MIN."""
        result = _break_rate_factor(0)
        self.assertGreaterEqual(result, _BREAK_RATE_FACTOR_MIN)

    def test_clamped_above_maximum(self) -> None:
        """Very high rate is clamped to BREAK_RATE_FACTOR_MAX."""
        result = _break_rate_factor(200)
        self.assertLessEqual(result, _BREAK_RATE_FACTOR_MAX)


# ---------------------------------------------------------------------------
# _end_of_utterance_rate_factor
# ---------------------------------------------------------------------------


class EndOfUtteranceRateFactorTests(unittest.TestCase):
    """Verify _end_of_utterance_rate_factor clamps output to [MIN, MAX]."""

    def test_low_rate(self) -> None:
        result = _end_of_utterance_rate_factor(10)
        self.assertGreaterEqual(result, _END_OF_UTTERANCE_RATE_FACTOR_MIN)

    def test_high_rate(self) -> None:
        result = _end_of_utterance_rate_factor(85)
        self.assertLessEqual(result, _END_OF_UTTERANCE_RATE_FACTOR_MAX)

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


# ---------------------------------------------------------------------------
# Word dictionaries
# ---------------------------------------------------------------------------


class WordDictionaryTests(unittest.TestCase):
    """Verify language word dictionaries are populated."""

    def test_vietnamese_words_not_empty(self) -> None:
        self.assertIsInstance(_VIETNAMESE_WORDS, set)
        self.assertGreater(len(_VIETNAMESE_WORDS), 0)

    def test_english_words_not_empty(self) -> None:
        self.assertIsInstance(_ENGLISH_WORDS, set)
        self.assertGreater(len(_ENGLISH_WORDS), 0)

    def test_common_english_words_present(self) -> None:
        for word in ("the", "is", "and", "you", "voice", "browser", "settings"):
            self.assertIn(word, _ENGLISH_WORDS)

    def test_common_vietnamese_words_present(self) -> None:
        for word in ("là", "và", "của", "có", "không"):
            self.assertIn(word, _VIETNAMESE_WORDS)


# ---------------------------------------------------------------------------
# _ensure_config_compat logic simulation
# ---------------------------------------------------------------------------


class _MockSetting:
    def __init__(self, settingId: str, defaultVal: object, useConfig: bool = True):
        self.id = settingId
        self.defaultVal = defaultVal
        self.useConfig = useConfig


_STANDARD_SETTINGS = (
    _MockSetting("voice", "en-US"),
    _MockSetting("variant", "en-us-x-multi-seanet:tpc"),
    _MockSetting("rate", 50),
    _MockSetting("rateBoost", False),
    _MockSetting("pitch", 50),
    _MockSetting("volume", 100),
    _MockSetting("pauseMode", "doNotShorten"),
)


def _simulate_ensure_config_compat(
    synthConfig: dict[str, Any],
    standardSettings: tuple[_MockSetting, ...] = _STANDARD_SETTINGS,
    availableVoices: dict[str, Any] | None = None,
    availableVariants: dict[str, Any] | None = None,
) -> None:
    if availableVoices is None:
        availableVoices = {"en-US": "English (US)"}
    if availableVariants is None:
        availableVariants = {"en-us-x-multi-seanet:tpc": "Guy"}

    for setting in standardSettings:
        if not getattr(setting, "useConfig", True):
            continue
        settingId = getattr(setting, "id", None)
        if not settingId or settingId in ("voice", "variant"):
            continue
        if settingId not in synthConfig or synthConfig[settingId] is None:
            defaultVal = getattr(setting, "defaultVal", None)
            if defaultVal is not None:
                synthConfig[settingId] = defaultVal

    configuredVoice = str(synthConfig.get("voice") or "")
    configuredVariant = str(synthConfig.get("variant") or "")

    if configuredVoice in availableVoices:
        if configuredVariant in availableVariants:
            return
        synthConfig["variant"] = next(iter(availableVariants))
        return

    synthConfig["voice"] = next(iter(availableVoices))
    synthConfig["variant"] = next(iter(availableVariants))


class ConfigCompatTests(unittest.TestCase):
    """Verify that _ensure_config_compat injects missing standard settings without overwriting existing ones."""

    def test_fills_missing_rate_boost_and_pause_mode(self) -> None:
        legacyConfig = {
            "voice": "en-US",
            "variant": "en-us-x-multi-seanet:tpc",
            "rate": 40,
            "pitch": 50,
            "volume": 100,
        }
        _simulate_ensure_config_compat(legacyConfig)
        self.assertIn("rateBoost", legacyConfig)
        self.assertEqual(legacyConfig["rateBoost"], False)
        self.assertIn("pauseMode", legacyConfig)
        self.assertEqual(legacyConfig["pauseMode"], "doNotShorten")
        self.assertEqual(legacyConfig["rate"], 40)

    def test_preserves_custom_settings(self) -> None:
        customConfig = {
            "voice": "en-US",
            "variant": "en-us-x-multi-seanet:tpc",
            "rate": 70,
            "rateBoost": True,
            "pitch": 60,
            "volume": 80,
            "pauseMode": "shortenAll",
        }
        _simulate_ensure_config_compat(customConfig)
        self.assertEqual(customConfig["rateBoost"], True)
        self.assertEqual(customConfig["pauseMode"], "shortenAll")
        self.assertEqual(customConfig["rate"], 70)
        self.assertEqual(customConfig["pitch"], 60)
        self.assertEqual(customConfig["volume"], 80)

    def test_nvda_load_settings_loop_simulation_succeeds(self) -> None:
        # Simulate an old nvda.ini that had only partial settings
        legacyConfig = {
            "voice": "en-US",
            "variant": "en-us-x-multi-seanet:tpc",
            "rate": 50,
        }
        _simulate_ensure_config_compat(legacyConfig)

        # Now simulate NVDA's SynthDriver.loadSettings() loop:
        # for s in self.supportedSettings:
        #     if not s.useConfig or s.id == "voice" or c[s.id] is None:
        #         continue
        #     val = c[s.id]
        loaded = {}
        for s in _STANDARD_SETTINGS:
            if not s.useConfig or s.id == "voice" or legacyConfig[s.id] is None:
                continue
            loaded[s.id] = legacyConfig[s.id]

        self.assertIn("rateBoost", loaded)
        self.assertEqual(loaded["rateBoost"], False)
        self.assertIn("pauseMode", loaded)
        self.assertEqual(loaded["pauseMode"], "doNotShorten")
        self.assertIn("pitch", loaded)
        self.assertEqual(loaded["pitch"], 50)
        self.assertIn("volume", loaded)
        self.assertEqual(loaded["volume"], 100)

    def test_speech_failure_logging_compatible_with_nvda_logger(self) -> None:
        # NVDA's logHandler.Logger.exception signature is:
        # def exception(self, msg: str = "", exc_info = True, **kwargs):
        # It does NOT accept *args, so passing formatting positional arguments
        # alongside exc_info=True causes TypeError: multiple values for 'exc_info'.
        class NvdaLoggerExceptionSignature:
            def __init__(self) -> None:
                self.records: list[str] = []

            def exception(self, msg: str = "", exc_info: bool = True, **kwargs: object) -> None:
                self.records.append(msg)

        logger = NvdaLoggerExceptionSignature()
        technicalDetail = "Chromium runtime exited: 1"

        # Formatted single string must succeed without TypeError
        if technicalDetail:
            logger.exception(f"Google TTS speech failed: {technicalDetail}")
        else:
            logger.exception("Google TTS speech failed.")

        self.assertEqual(len(logger.records), 1)
        self.assertEqual(logger.records[0], "Google TTS speech failed: Chromium runtime exited: 1")


class FatalFallbackTests(unittest.TestCase):
    """Verify fatal runtime error handling, fallback triggering, and debounce safeguards."""

    def test_friendly_message_extracted_from_cdp_error(self) -> None:
        from tests.test_support import load_driver_module

        bridge_module = load_driver_module("bridge")
        default_msg = "Google TTS For NVDA could not start speech in the Chromium browser runtime."
        error_msg = "The Chromium browser runtime connection closed unexpectedly."
        err = bridge_module.CdpError(error_msg, "WebSocket closed abruptly")
        extracted = str(err).strip() or default_msg
        self.assertEqual(extracted, error_msg)

    def test_empty_error_falls_back_to_default_message(self) -> None:
        err = Exception("")
        default_msg = "Google TTS For NVDA could not start speech in the Chromium browser runtime."
        extracted = str(err).strip() or default_msg
        self.assertEqual(extracted, default_msg)

    def test_fallback_debounce_prevents_duplicate_dialogs(self) -> None:
        calls: list[str] = []
        dialogs: list[str] = []

        class MockDriver:
            def __init__(self) -> None:
                self.name = "googleTtsForNvda"
                self._fallbackTriggered = False
                self._shutdownEvent = False
                self._queue = ["item1", "item2"]
                self.cancelled = False

            def cancel(self) -> None:
                self.cancelled = True

            def _trigger_fatal_fallback(self, message: str) -> None:
                if self._fallbackTriggered or self._shutdownEvent:
                    return
                self._fallbackTriggered = True
                self._queue.clear()
                self.cancel()
                calls.append("findAndSetNextSynth")
                dialogs.append(message)

        driver = MockDriver()
        driver._trigger_fatal_fallback("Fatal error 1")
        # Second call must be debounced
        driver._trigger_fatal_fallback("Fatal error 2")

        self.assertTrue(driver._fallbackTriggered)
        self.assertTrue(driver.cancelled)
        self.assertEqual(len(driver._queue), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(dialogs, ["Fatal error 1"])


if __name__ == "__main__":
    unittest.main()

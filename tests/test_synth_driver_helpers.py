"""Tests for pure helper functions in the SynthDriver __init__.py.

These functions are extracted from __init__.py to test them without NVDA
dependencies. Keep in sync with the production constants.
"""

from __future__ import annotations

import re
import unittest
from typing import Any

# ---------------------------------------------------------------------------
# Pure constants and functions extracted from __init__.py
# ---------------------------------------------------------------------------

_BREAK_RATE_TABLE = ((10, 1.8), (43, 1.3), (60, 1.0), (75, 0.7), (85, 0.4))
_BREAK_RATE_FACTOR_MIN = 0.4
_BREAK_RATE_FACTOR_MAX = 1.8
_END_OF_UTTERANCE_RATE_FACTOR_MIN = 0.5
_END_OF_UTTERANCE_RATE_FACTOR_MAX = 1.6
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


class SpeechLoopResilienceTests(unittest.TestCase):
    """Verify that _speech_loop catches worker exceptions and triggers fatal fallback without crashing."""

    def test_speech_loop_catches_worker_crash_and_triggers_fallback(self) -> None:
        import collections
        import threading

        class MockDriver:
            def __init__(self) -> None:
                self._shutdownEvent = threading.Event()
                self._speechCondition = threading.Condition()
                self._speechQueue: collections.deque[tuple] = collections.deque()
                self._activeCancelEvent: threading.Event | None = None
                self._fallbackTriggered = False
                self.fallbackCalls: list[str] = []
                self.workerCalls = 0

            def _speak_worker(self, *args: object) -> None:
                self.workerCalls += 1
                raise AttributeError("Simulated unexpected AttributeError in worker")

            def _trigger_fatal_fallback(self, message: str) -> None:
                self.fallbackCalls.append(message)
                self._shutdownEvent.set()

            def _speech_loop(self) -> None:
                while not self._shutdownEvent.is_set():
                    with self._speechCondition:
                        while not self._speechQueue and not self._shutdownEvent.is_set():
                            self._speechCondition.wait()
                        if self._shutdownEvent.is_set():
                            return
                        request = self._speechQueue.popleft()
                        self._activeCancelEvent = request[-1]
                    try:
                        self._speak_worker(*request)
                    except Exception:
                        if not self._shutdownEvent.is_set() and not self._fallbackTriggered:
                            fallbackMsg = "Simulated fallback message"
                            self._trigger_fatal_fallback(fallbackMsg)
                    finally:
                        with self._speechCondition:
                            if self._activeCancelEvent is request[-1]:
                                self._activeCancelEvent = None

        driver = MockDriver()
        cancelEvent = threading.Event()
        driver._speechQueue.append(("mock_text", cancelEvent))

        t = threading.Thread(target=driver._speech_loop)
        t.start()
        t.join(timeout=2.0)

        self.assertFalse(t.is_alive(), "Speech loop should exit after fallback sets shutdown")
        self.assertEqual(driver.workerCalls, 1)
        self.assertEqual(len(driver.fallbackCalls), 1)
        self.assertIsNone(driver._activeCancelEvent)


class SynthDriverIsSupportedTests(unittest.TestCase):
    """Verify that SynthDriver.isSupported("voice") always returns True.

    This ensures NVDA's loadSettings() initializes the active voice via
    synthDriverHandler.changeVoice(self, voice) instead of changeVoice(self, None)
    when Automatic Language Profiles is enabled.
    """

    def test_synth_driver_ast_defines_is_supported_override(self) -> None:
        import ast
        from pathlib import Path

        init_path = (
            Path(__file__).resolve().parents[1]
            / "googleTtsForNvda"
            / "synthDrivers"
            / "googleTtsForNvda"
            / "__init__.py"
        )
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
        synth_driver_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "SynthDriver":
                synth_driver_node = node
                break
        self.assertIsNotNone(synth_driver_node, "SynthDriver class must be defined in __init__.py")
        assert synth_driver_node is not None

        method_node = None
        for item in synth_driver_node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "isSupported":
                method_node = item
                break
        self.assertIsNotNone(method_node, "SynthDriver must override isSupported")
        assert method_node is not None

        param_names = [arg.arg for arg in method_node.args.args]
        self.assertEqual(param_names, ["self", "settingID"])

    def test_is_supported_behavior_when_auto_language_profiles_enabled_and_disabled(self) -> None:
        auto_notice_setting = _MockSetting("notice", "notice", useConfig=False)
        pause_setting = _MockSetting("pauseMode", "doNotShorten")

        class BaseAutoSettings:
            def __init__(self, supported_settings: tuple[_MockSetting, ...]) -> None:
                self.supportedSettings = supported_settings

            def isSupported(self, settingID: str) -> bool:
                return any(s.id == settingID for s in self.supportedSettings)

        class SimulatedSynthDriver(BaseAutoSettings):
            def __init__(self, auto_language_enabled: bool) -> None:
                self.auto_language_enabled = auto_language_enabled
                settings = (auto_notice_setting, pause_setting) if auto_language_enabled else _STANDARD_SETTINGS
                super().__init__(settings)

            def isSupported(self, settingID: str) -> bool:
                if settingID == "voice":
                    return True
                return super().isSupported(settingID)

        # 1. Profiles enabled: voice is hidden from supportedSettings but isSupported("voice") is True
        driver_auto = SimulatedSynthDriver(auto_language_enabled=True)
        self.assertNotIn("voice", [s.id for s in driver_auto.supportedSettings])
        self.assertTrue(driver_auto.isSupported("voice"))
        self.assertTrue(driver_auto.isSupported("pauseMode"))
        self.assertTrue(driver_auto.isSupported("notice"))
        self.assertFalse(driver_auto.isSupported("rate"))
        self.assertFalse(driver_auto.isSupported("pitch"))
        self.assertFalse(driver_auto.isSupported("nonexistent"))

        # 2. Profiles disabled: voice is in supportedSettings and isSupported("voice") is True
        driver_manual = SimulatedSynthDriver(auto_language_enabled=False)
        self.assertIn("voice", [s.id for s in driver_manual.supportedSettings])
        self.assertTrue(driver_manual.isSupported("voice"))
        self.assertTrue(driver_manual.isSupported("rate"))
        self.assertTrue(driver_manual.isSupported("pitch"))
        self.assertTrue(driver_manual.isSupported("pauseMode"))
        self.assertFalse(driver_manual.isSupported("notice"))

    def test_nvda_load_settings_initializes_valid_voice_id(self) -> None:
        """Simulate NVDA's loadSettings() voice initialization behavior.

        Verifies that when isSupported("voice") is True, changeVoice receives a valid
        voice ID instead of None.
        """
        config_voice = "vi"
        available_voices = {"vi": "Vietnamese", "en-US": "English (US)"}

        recorded_voice_id: str | None = "initial"

        def simulated_change_voice(synth: object, voice_id: str | None) -> None:
            nonlocal recorded_voice_id
            recorded_voice_id = voice_id

        def simulated_nvda_load_settings(driver_is_supported_voice: bool) -> None:
            if driver_is_supported_voice:
                voice = config_voice
                simulated_change_voice("googleTtsForNvda", voice)
            else:
                simulated_change_voice("googleTtsForNvda", None)

        # When isSupported("voice") is True even when profiles are enabled
        simulated_nvda_load_settings(driver_is_supported_voice=True)
        self.assertEqual(recorded_voice_id, "vi")
        self.assertIn(recorded_voice_id, available_voices)

        # When isSupported("voice") was False
        simulated_nvda_load_settings(driver_is_supported_voice=False)
        self.assertIsNone(recorded_voice_id)
        self.assertNotIn(recorded_voice_id, available_voices)


class AutoLanguageGlobalPluginHooksTests(unittest.TestCase):
    """Verify global plugin hooks for LangChangeCommand preservation and voice dictionary caching."""

    def test_global_plugin_ast_defines_lang_change_and_voice_dict_cache_hooks(self) -> None:
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        plugin_path = repo_root / "googleTtsForNvda" / "globalPlugins" / "googleTtsForNvda" / "__init__.py"
        driver_path = repo_root / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "__init__.py"
        tree = ast.parse(plugin_path.read_text(encoding="utf-8-sig"))
        top_level_funcs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("_resolve_nvda_voice_speech_dict", top_level_funcs)
        self.assertIn("_file_stat_signature", top_level_funcs)
        self.assertIn("_load_voice_dictionary_for_voice", top_level_funcs)
        self.assertIn("_auto_language_for_spelling_text", top_level_funcs)
        self.assertIn("_google_auto_language_multiple_profiles_active", top_level_funcs)
        self.assertIn("_strip_external_lang_changes_and_coalesce", top_level_funcs)
        self.assertIn("_patch_auto_language_voice_dictionary", top_level_funcs)

        patch_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_patch_auto_language_voice_dictionary"
        )
        nested_funcs = {node.name for node in patch_node.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("speak_with_auto_profile", nested_funcs)
        self.assertIn("should_make_lang_change_command_with_auto_profile", nested_funcs)
        self.assertIn("should_switch_voice_with_auto_profile", nested_funcs)

        driver_tree = ast.parse(driver_path.read_text(encoding="utf-8-sig"))
        synth_driver_node = next(
            node for node in driver_tree.body if isinstance(node, ast.ClassDef) and node.name == "SynthDriver"
        )
        driver_methods = {node.name for node in synth_driver_node.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("languageIsSupported", driver_methods)

    def test_single_profile_and_speak_depth_lang_change_gating_and_coalescing(self) -> None:
        from tests.test_support import load_driver_module

        language_profiles = load_driver_module("language_profiles")
        language_detector = load_driver_module("language_detector")
        google_attr = language_detector.GOOGLE_TTS_LANG_CHANGE_ATTR
        missing_lang = language_detector.MISSING_GOOGLE_TTS_LANGUAGE

        class FakeLangChangeCommand:
            def __init__(self, lang: str | None) -> None:
                self.lang = lang

            def __repr__(self) -> str:
                return f"LangChangeCommand({self.lang!r})"

        class FakeCharacterModeCommand:
            def __init__(self, state: bool) -> None:
                self.state = state

        def make_google_lang_change(lang: str) -> FakeLangChangeCommand:
            cmd = FakeLangChangeCommand(lang.replace("-", "_"))
            setattr(cmd, google_attr, lang)
            return cmd

        def strip_external_and_coalesce(seq: list[Any]) -> list[Any]:
            normalized_seq: list[Any] = []
            merge_next_str = False
            for item in seq:
                if isinstance(item, FakeLangChangeCommand):
                    if getattr(item, google_attr, missing_lang) is missing_lang:
                        if normalized_seq and isinstance(normalized_seq[-1], str):
                            merge_next_str = True
                        continue
                if merge_next_str and isinstance(item, str) and normalized_seq and isinstance(normalized_seq[-1], str):
                    normalized_seq[-1] = normalized_seq[-1] + item
                    merge_next_str = False
                    continue
                merge_next_str = False
                normalized_seq.append(item)
            return normalized_seq

        def filter_sequence(seq: list[Any], candidates: list[str], preferred: str) -> list[Any]:
            seq = strip_external_and_coalesce(seq)
            if len(candidates) <= 1:
                out: list[Any] = []
                for item in seq:
                    if isinstance(item, FakeLangChangeCommand):
                        continue
                    if isinstance(item, str) and item:
                        out.append(language_profiles.normalize_mathematical_alphanumeric(item))
                    else:
                        out.append(item)
                return out

            filtered: list[Any] = []
            current_lang: str | None = None
            in_char_mode = False
            in_spelling_seq = False
            non_empty_str_count = sum(1 for x in seq if isinstance(x, str) and x.strip())
            for item in seq:
                if isinstance(item, FakeCharacterModeCommand):
                    in_char_mode = item.state
                    filtered.append(item)
                    continue
                if isinstance(item, FakeLangChangeCommand):
                    current_lang = getattr(item, google_attr, None)
                    filtered.append(item)
                    continue
                if isinstance(item, str) and item:
                    stripped = item.strip()
                    if not stripped:
                        filtered.append(item)
                        continue
                    if in_char_mode and not in_spelling_seq and non_empty_str_count > 1 and current_lang is not None:
                        filtered.append(language_profiles.normalize_mathematical_alphanumeric(item))
                        continue
                    segments = language_profiles.segment_mixed_text(
                        item,
                        candidates,
                        preferred,
                        defaultLanguage=current_lang,
                    )
                    for seg_text, seg_lang in segments:
                        if not language_detector.language_matches(current_lang, seg_lang):
                            filtered.append(make_google_lang_change(seg_lang))
                            current_lang = seg_lang
                        filtered.append(seg_text)
                    continue
                filtered.append(item)
            return filtered

        # 1. UIA-split Vietnamese sentence with 1 profile enabled: coalesced & NO LangChangeCommand
        uia_split_sentence = [
            FakeLangChangeCommand("en_US"),
            "Đây là máy tính model th",
            FakeLangChangeCommand("vi_VN"),
            "ế",
            FakeLangChangeCommand("en_US"),
            " h",
            FakeLangChangeCommand("vi_VN"),
            "ệ",
            FakeLangChangeCommand("en_US"),
            " 11",
        ]
        single_profile_result = filter_sequence(uia_split_sentence, ["vi-VN"], "vi-VN")
        self.assertEqual(["Đây là máy tính model thế hệ 11"], single_profile_result)

        # 2. UIA-split Vietnamese sentence with 2 profiles enabled: coalesced & single vi_VN LangChangeCommand
        multi_profile_result = filter_sequence(uia_split_sentence, ["en-US", "vi-VN"], "vi-VN")
        self.assertEqual(2, len(multi_profile_result))
        self.assertIsInstance(multi_profile_result[0], FakeLangChangeCommand)
        self.assertEqual("vi_VN", multi_profile_result[0].lang)
        self.assertEqual("Đây là máy tính model thế hệ 11", multi_profile_result[1])

        # 3. UIA-split single word "thế " during word navigation: coalesced into "thế "
        uia_split_word = [
            FakeLangChangeCommand("en_US"),
            "th",
            FakeLangChangeCommand("vi_VN"),
            "ế",
            FakeLangChangeCommand("en_US"),
            " ",
        ]
        word_result = filter_sequence(uia_split_word, ["en-US", "vi-VN"], "vi-VN")
        self.assertEqual(2, len(word_result))
        self.assertEqual("vi_VN", word_result[0].lang)
        self.assertEqual("thế ", word_result[1])

        # 4. UI control with access key announcement: keeps en_US without switching to vi_VN on single character
        ui_shortcut_seq = [
            "Use automatic language profiles",
            "check box",
            "not checked",
            "Key+",
            FakeCharacterModeCommand(True),
            "u",
            FakeCharacterModeCommand(False),
        ]
        shortcut_result = filter_sequence(ui_shortcut_seq, ["en-US", "vi-VN"], "vi-VN")
        lang_changes = [x for x in shortcut_result if isinstance(x, FakeLangChangeCommand)]
        self.assertEqual(1, len(lang_changes))
        self.assertEqual("en_US", lang_changes[0].lang)

        # 5. shouldMakeLangChangeCommand gating by speak depth and candidate count
        def should_make_lang_change(auto_active: bool, speak_depth: int, candidate_count: int) -> bool:
            if auto_active:
                return speak_depth > 0 and candidate_count > 1
            return False

        self.assertFalse(should_make_lang_change(True, speak_depth=0, candidate_count=2))
        self.assertFalse(should_make_lang_change(True, speak_depth=1, candidate_count=1))
        self.assertTrue(should_make_lang_change(True, speak_depth=1, candidate_count=2))

        # 6. Decomposed NFD Vietnamese sentence is composed to NFC before symbol processing
        nfd_sentence = "Đây la\u0300 ma\u0301y ti\u0301nh model thê\u0301 hê\u0323 11"
        self.assertEqual(
            ["Đây là máy tính model thế hệ 11"],
            filter_sequence([nfd_sentence], ["vi-VN"], "vi-VN"),
        )

    def test_voice_dictionary_cache_avoids_repeated_disk_loads_until_file_changes(self) -> None:
        import tempfile
        from pathlib import Path

        class FakeSpeechDict(list):
            fileName: str | None = None

        voice_dict = FakeSpeechDict()
        disk_load_calls: list[str] = []
        cache: dict[str, tuple[str | None, tuple[int, int] | None, list[Any]]] = {}
        active_variant: str | None = None

        def file_stat_sig(file_path: str | None) -> tuple[int, int] | None:
            if not file_path:
                return None
            try:
                st = Path(file_path).stat()
                return (int(st.st_mtime_ns), int(st.st_size))
            except OSError:
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            vi_path = str(Path(tmp_dir) / "googleTtsForNvda-vi.dic")
            en_path = str(Path(tmp_dir) / "googleTtsForNvda-en.dic")
            Path(en_path).write_text("hello\txin chao\t0\t0\n", encoding="utf-8")

            def fake_nvda_load_voice_dict(voice_id: str) -> None:
                disk_load_calls.append(voice_id)
                target_path = vi_path if voice_id == "vi-voice" else en_path
                voice_dict.fileName = target_path
                voice_dict.clear()
                if Path(target_path).is_file():
                    voice_dict.append(f"entry:{voice_id}:{Path(target_path).stat().st_size}")

            def load_for_voice(voice_id: str) -> bool:
                nonlocal active_variant
                cached = cache.get(voice_id)
                if cached is not None:
                    cached_file, cached_stat, cached_entries = cached
                    if file_stat_sig(cached_file) == cached_stat:
                        if active_variant != voice_id or voice_dict.fileName != cached_file:
                            voice_dict[:] = cached_entries
                            voice_dict.fileName = cached_file
                            active_variant = voice_id
                        return True
                fake_nvda_load_voice_dict(voice_id)
                active_variant = voice_id
                cache[voice_id] = (
                    voice_dict.fileName,
                    file_stat_sig(voice_dict.fileName),
                    list(voice_dict),
                )
                return True

            # Initial loads hit disk once per voice
            self.assertTrue(load_for_voice("vi-voice"))
            self.assertTrue(load_for_voice("en-voice"))
            self.assertEqual(disk_load_calls, ["vi-voice", "en-voice"])
            self.assertEqual(len(voice_dict), 1)

            # Repeated alternating switches use in-memory cache without disk loads
            for _ in range(10):
                load_for_voice("vi-voice")
                self.assertEqual(voice_dict, [])
                self.assertEqual(voice_dict.fileName, vi_path)
                load_for_voice("en-voice")
                self.assertEqual(len(voice_dict), 1)
                self.assertEqual(voice_dict.fileName, en_path)
            self.assertEqual(disk_load_calls, ["vi-voice", "en-voice"])

            # Creating or modifying the .dic file invalidates the cache entry automatically
            Path(vi_path).write_text("test\tthử\t0\t0\n", encoding="utf-8")
            load_for_voice("vi-voice")
            self.assertEqual(disk_load_calls, ["vi-voice", "en-voice", "vi-voice"])
            self.assertEqual(len(voice_dict), 1)

    def test_synth_language_and_unicode_normalization_integration(self) -> None:
        import unicodedata

        from tests.test_support import load_driver_module

        language_profiles = load_driver_module("language_profiles")
        language_utils = load_driver_module("language_utils")

        # 1. SynthDriver._get_language resolution when Automatic Language Profiles is enabled vs disabled
        def simulated_get_language(
            auto_enabled: bool,
            candidates: list[str],
            preferred: str | None,
            current_voice_lang: str,
        ) -> str | None:
            if auto_enabled:
                if len(candidates) == 1:
                    return language_utils.get_nvda_locale_for_language(candidates[0])
                if preferred:
                    return language_utils.get_nvda_locale_for_language(preferred)
            return language_utils.get_nvda_locale_for_language(current_voice_lang)

        self.assertEqual("vi_VN", simulated_get_language(True, ["vi-VN"], "vi-VN", "en-US"))
        self.assertEqual("vi_VN", simulated_get_language(True, ["en-US", "vi-VN"], "vi-VN", "en-US"))
        self.assertEqual("en_US", simulated_get_language(False, ["vi-VN"], "vi-VN", "en-US"))

        # 2. processText normalizes NFD and compatibility characters before symbol processing even when auto-language is off
        def simulated_process_text(text: str, auto_enabled: bool) -> str:
            text = language_profiles.normalize_mathematical_alphanumeric(text)
            if not auto_enabled:
                return text
            return text

        self.assertEqual("thế hệ", simulated_process_text("thê\u0301 hê\u0323", auto_enabled=False))
        self.assertEqual("thế hệ", simulated_process_text("thê\u0301 hê\u0323", auto_enabled=True))
        self.assertEqual(
            "Hello", simulated_process_text("\U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428", auto_enabled=False)
        )

        # 3. getSpellingSpeech respects NVDA unicodeNormalization when enabled (preserving NFKC difference for NVDA reporting)
        #    and normalizes when NVDA unicodeNormalization is disabled or for supplementary UCD 17.0 symbols
        def simulated_spelling_text(text: str, nvda_unicode_norm_enabled: bool) -> str:
            if not nvda_unicode_norm_enabled or unicodedata.normalize("NFKC", text) == text:
                return language_profiles.normalize_mathematical_alphanumeric(text)
            return text

        # When NVDA unicodeNormalization is disabled (default in NVDA 2024.4.2 / 2025.3.3), NFD is normalized to NFC
        self.assertEqual("ế", simulated_spelling_text("e\u0302\u0301", nvda_unicode_norm_enabled=False))
        # When NVDA unicodeNormalization is enabled (default in NVDA 2026.2), raw NFD is passed so NVDA's unicodeNormalize
        # and reportNormalizedForCharacterNavigation handle it natively
        self.assertEqual("e\u0302\u0301", simulated_spelling_text("e\u0302\u0301", nvda_unicode_norm_enabled=True))
        # UCD 17.0 symbols not altered by NFKC (such as negative circled letters) are normalized in both modes
        self.assertEqual("A", simulated_spelling_text("\U0001f150", nvda_unicode_norm_enabled=True))
        self.assertEqual("A", simulated_spelling_text("\U0001f150", nvda_unicode_norm_enabled=False))

    def test_say_all_speech_without_pauses_is_patched_by_auto_profile(self) -> None:
        from pathlib import Path

        plugin_path = (
            Path(__file__).resolve().parents[1]
            / "googleTtsForNvda"
            / "globalPlugins"
            / "googleTtsForNvda"
            / "__init__.py"
        )
        plugin_source = plugin_path.read_text(encoding="utf-8")
        self.assertIn("from speech import sayAll as sayAllModule", plugin_source)
        self.assertIn("sayAllSpeechWithoutPauses.speak = speak_with_auto_profile", plugin_source)
        self.assertIn("sayAllSpeechWithoutPauses.speak = _originalSayAllSpeak", plugin_source)
        self.assertIn(
            "sayAllSpeechWithoutPauses.speakWithoutPauses = speak_without_pauses_with_auto_profile",
            plugin_source,
        )
        self.assertIn(
            "sayAllSpeechWithoutPauses.speakWithoutPauses = _originalSayAllSpeakWithoutPauses",
            plugin_source,
        )

        synth_path = (
            Path(__file__).resolve().parents[1]
            / "googleTtsForNvda"
            / "synthDrivers"
            / "googleTtsForNvda"
            / "__init__.py"
        )
        synth_source = synth_path.read_text(encoding="utf-8")
        self.assertIn("self._isPaused = bool(switch)", synth_source)
        self.assertIn("chromeRate = effective_chrome_rate(options)", synth_source)
        self.assertIn("keepSilenceMs = keep_silence_ms_for_rate(pauseShorteningMode, chromeRate)", synth_source)
        eou_idx = synth_source.index("_END_OF_UTTERANCE_PAUSE_MS * _end_of_utterance_rate_factor(lastEffectiveRate)")
        finish_idx = synth_source.index("self._finish_request_audio()", eou_idx)
        self.assertLess(eou_idx, finish_idx)

    def test_pause_mode_and_utterance_end_resolution(self) -> None:
        from tests.test_support import load_driver_module

        processing = load_driver_module("speech_processing")
        segmenter = processing.DEFAULT_TEXT_SEGMENTER
        mode_0 = processing.PAUSE_MODE_DO_NOT_SHORTEN
        mode_1 = processing.PAUSE_MODE_SHORTEN_END_ONLY
        mode_2 = processing.PAUSE_MODE_SHORTEN_ALL

        def resolve_flush(pause_mode: str, is_utterance_end: bool, segment_text: str) -> tuple[str, bool]:
            ends_with_punct = segmenter.ends_with_pause_punctuation(segment_text)
            if pause_mode == mode_2:
                shortening_mode = mode_2
            elif is_utterance_end:
                shortening_mode = mode_1 if pause_mode == mode_1 else mode_0
            else:
                shortening_mode = mode_0 if ends_with_punct else mode_1
            emit_end_pause = is_utterance_end and pause_mode == mode_1 and ends_with_punct
            return shortening_mode, emit_end_pause

        # 1. Mode "0" (Do not shorten):
        #    - Utterance end never shortens or emits synthetic end_pause
        self.assertEqual((mode_0, False), resolve_flush(mode_0, True, "OK button"))
        self.assertEqual((mode_0, False), resolve_flush(mode_0, True, "Hello world."))
        #    - Mid-utterance flush without punctuation shortens trailing engine silence
        self.assertEqual((mode_1, False), resolve_flush(mode_0, False, "Hello"))
        self.assertEqual((mode_0, False), resolve_flush(mode_0, False, "Hello,"))

        # 2. Mode "1" (Shorten at end of text only):
        #    - Utterance end without punctuation shortens trailing silence without extra end_pause
        self.assertEqual((mode_1, False), resolve_flush(mode_1, True, "OK button"))
        self.assertEqual((mode_1, False), resolve_flush(mode_1, True, 'He said "Hello"'))
        #    - Utterance end with punctuation preserves sentence-end pause via end_pause
        self.assertEqual((mode_1, True), resolve_flush(mode_1, True, "Hello world."))
        self.assertEqual((mode_1, True), resolve_flush(mode_1, True, 'He said "Hello."'))
        self.assertEqual((mode_1, True), resolve_flush(mode_1, True, "Downloads (5)"))
        #    - Mid-utterance flush preserves punctuation pause, shortens unpunctuated flush
        self.assertEqual((mode_1, False), resolve_flush(mode_1, False, "Hello"))
        self.assertEqual((mode_0, False), resolve_flush(mode_1, False, "Hello,"))

        # 3. Mode "2" (Shorten all pauses):
        #    - Shortens all pauses and never adds extra end_pause
        self.assertEqual((mode_2, False), resolve_flush(mode_2, True, "OK button"))
        self.assertEqual((mode_2, False), resolve_flush(mode_2, True, "Hello world."))
        self.assertEqual((mode_2, False), resolve_flush(mode_2, False, "Hello,"))


if __name__ == "__main__":
    unittest.main()

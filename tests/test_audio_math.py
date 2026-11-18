from __future__ import annotations

import unittest
from typing import Any

from tests.test_support import load_driver_module

audio_math = load_driver_module("audio_math")


class AudioMathTests(unittest.TestCase):
    @staticmethod
    def _seanet_options(**overrides: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "speaker_id": "speaker_seanet",
            "speaker_name": "SeaNet Voice",
            "lang": "und",
            "package_id": "dummy-package-seanet",
            "rate": 50,
            "pitch": 50,
            "volume": 100,
            "rateBoost": False,
        }
        params.update(overrides)
        return dict(audio_math.build_speech_options(**params))

    def test_rate_to_chrome_mapping(self) -> None:
        self.assertAlmostEqual(audio_math.rate_to_chrome(0), 0.35, places=3)
        self.assertAlmostEqual(audio_math.rate_to_chrome(50), 1.175, places=3)
        self.assertAlmostEqual(audio_math.rate_to_chrome(100), 2.0, places=3)

    def test_rate_to_chrome_with_rate_boost(self) -> None:
        self.assertAlmostEqual(audio_math.rate_to_chrome(0, rateBoost=True), 0.70, places=3)
        self.assertAlmostEqual(audio_math.rate_to_chrome(50, rateBoost=True), 2.35, places=3)
        self.assertAlmostEqual(audio_math.rate_to_chrome(100, rateBoost=True), 4.0, places=3)

    def test_pitch_to_chrome_mapping(self) -> None:
        self.assertAlmostEqual(audio_math.pitch_to_chrome(0), 0.4, places=3)
        self.assertAlmostEqual(audio_math.pitch_to_chrome(50), 1.0, places=3)
        self.assertAlmostEqual(audio_math.pitch_to_chrome(100), 1.6, places=3)

    def test_uses_protected_engine_rate_detection(self) -> None:
        for protectedId in (
            "dummy-model-seanet",
            "test-voice-seanet",
            "en-us-x-news-lemonbalm",
            "en-us-x-blueginger-lemonbalm",
            "fr-fr-x-multi-lemonbalm",
        ):
            with self.subTest(protectedId=protectedId):
                self.assertTrue(audio_math.uses_protected_engine_rate(protectedId))
        for standardId in ("dummy-model", "test-voice", "en-us-x-multi"):
            with self.subTest(standardId=standardId):
                self.assertFalse(audio_math.uses_protected_engine_rate(standardId))

    def test_build_speech_options_standard_package(self) -> None:
        options = audio_math.build_speech_options(
            speaker_id="speaker_1",
            speaker_name="Voice 1",
            lang="und",
            package_id="dummy-standard-package",
            rate=50,
            pitch=50,
            volume=100,
            rateBoost=False,
        )
        self.assertEqual(options["voiceId"], "speaker_1")
        self.assertEqual(options["voiceName"], "Voice 1")
        self.assertEqual(options["lang"], "und")
        self.assertAlmostEqual(options["rate"], 1.175, places=3)
        self.assertAlmostEqual(options["artificialRate"], 1.0, places=3)
        self.assertAlmostEqual(options["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options["postPitch"], 1.0, places=3)
        self.assertAlmostEqual(options["volume"], 1.0, places=4)
        self.assertAlmostEqual(options["outputGain"], 1.70, places=4)

    def test_build_speech_options_seanet_high_rate(self) -> None:
        options = self._seanet_options(rate=100, volume=50)
        self.assertEqual(options["voiceId"], "speaker_seanet")
        self.assertAlmostEqual(options["rate"], audio_math.PROTECTED_ENGINE_RATE, places=3)
        self.assertGreater(options["artificialRate"], 1.0)
        self.assertAlmostEqual(options["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options["postPitch"], 1.0, places=3)
        self.assertAlmostEqual(options["volume"], 0.5, places=4)
        self.assertAlmostEqual(options["outputGain"], 0.85, places=4)

    def test_build_speech_options_seanet_rate_boost_soft_knee(self) -> None:
        options_100 = self._seanet_options(rate=100)
        options_boost_62 = self._seanet_options(rate=62, rateBoost=True)
        options_boost_100 = self._seanet_options(rate=100, rateBoost=True)
        self.assertAlmostEqual(options_100["artificialRate"], 1.6, places=3)
        self.assertGreater(options_boost_62["artificialRate"], options_100["artificialRate"])
        self.assertLess(options_boost_62["artificialRate"], options_boost_100["artificialRate"])
        self.assertAlmostEqual(options_boost_100["artificialRate"], audio_math.MAX_ARTIFICIAL_RATE, places=3)

    def test_build_speech_options_seanet_pitch_scaling(self) -> None:
        options_low = self._seanet_options(pitch=0)
        self.assertAlmostEqual(options_low["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options_low["postPitch"], 0.4, places=3)

        options_high = self._seanet_options(pitch=100)
        self.assertAlmostEqual(options_high["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options_high["postPitch"], 1.6, places=3)

    def test_build_speech_options_seanet_rate_and_pitch_are_independent(self) -> None:
        """Rate/rateBoost and pitch must remain completely independent on SeaNet and Lemonbalm voices."""
        for package_id in ("dummy-package-seanet", "en-us-x-news-lemonbalm", "en-us-x-blueginger-lemonbalm"):
            for rateBoost in (False, True):
                speeds = []
                for rate in (0, 25, 50, 60, 75, 90, 100):
                    baseline = self._seanet_options(
                        speaker_id="speaker_protected",
                        speaker_name="Protected Voice",
                        package_id=package_id,
                        rate=rate,
                        pitch=50,
                        rateBoost=rateBoost,
                    )
                    speeds.append(baseline["rate"] * baseline["artificialRate"])
                    for pitch in (0, 25, 50, 75, 100):
                        opts = self._seanet_options(
                            speaker_id="speaker_protected",
                            speaker_name="Protected Voice",
                            package_id=package_id,
                            rate=rate,
                            pitch=pitch,
                            rateBoost=rateBoost,
                        )
                        self.assertEqual(opts["rate"], baseline["rate"])
                        self.assertEqual(opts["artificialRate"], baseline["artificialRate"])
                        self.assertAlmostEqual(opts["pitch"], 1.0, places=3)
                        self.assertAlmostEqual(opts["postPitch"], audio_math.pitch_to_chrome(pitch), places=3)
                self.assertEqual(speeds, sorted(speeds))

            # Changing rate/rateBoost must never change postPitch for a given pitch setting.
            for pitch in (0, 25, 50, 75, 100):
                expected_post = audio_math.pitch_to_chrome(pitch)
                for rate in (0, 50, 100):
                    for rateBoost in (False, True):
                        opts = self._seanet_options(
                            speaker_id="speaker_protected",
                            speaker_name="Protected Voice",
                            package_id=package_id,
                            rate=rate,
                            pitch=pitch,
                            rateBoost=rateBoost,
                        )
                        self.assertAlmostEqual(opts["postPitch"], expected_post, places=3)

    def test_build_speech_options_standard_pitch_scaling(self) -> None:
        options_low = audio_math.build_speech_options(
            speaker_id="speaker_standard",
            speaker_name="Standard Voice",
            lang="und",
            package_id="dummy-standard-package",
            rate=50,
            pitch=0,
            volume=100,
            rateBoost=False,
        )
        self.assertAlmostEqual(options_low["pitch"], 0.4, places=3)
        self.assertAlmostEqual(options_low["postPitch"], 1.0, places=3)

        options_high = audio_math.build_speech_options(
            speaker_id="speaker_standard",
            speaker_name="Standard Voice",
            lang="und",
            package_id="dummy-standard-package",
            rate=50,
            pitch=100,
            volume=100,
            rateBoost=False,
        )
        self.assertAlmostEqual(options_high["pitch"], 1.6, places=3)
        self.assertAlmostEqual(options_high["postPitch"], 1.0, places=3)

    def test_coercion_and_invalid_inputs(self) -> None:
        self.assertAlmostEqual(audio_math.rate_to_chrome("50.4", rateBoost="false"), 1.175, places=3)
        self.assertAlmostEqual(audio_math.rate_to_chrome(float("nan")), 1.175, places=3)
        self.assertAlmostEqual(audio_math.pitch_to_chrome(None), 1.0, places=3)
        options = audio_math.build_speech_options(
            speaker_id="speaker_1",
            speaker_name="Voice 1",
            lang="und",
            package_id="  dummy-model-seanet  ",
            rate=True,
            pitch=float("inf"),
            volume="invalid",
            rateBoost="0",
        )
        self.assertEqual(options["nvdaRate"], 50)
        self.assertFalse(options["rateBoost"])
        self.assertAlmostEqual(options["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options["postPitch"], 1.0, places=3)
        self.assertAlmostEqual(options["volume"], 1.0, places=4)


if __name__ == "__main__":
    unittest.main()

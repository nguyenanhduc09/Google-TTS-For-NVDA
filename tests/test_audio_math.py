from __future__ import annotations

import unittest

from tests.test_support import load_driver_module

audio_math = load_driver_module("audio_math")


class AudioMathTests(unittest.TestCase):
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
        self.assertTrue(audio_math.uses_protected_engine_rate("dummy-model-seanet"))
        self.assertTrue(audio_math.uses_protected_engine_rate("test-voice-seanet"))
        self.assertFalse(audio_math.uses_protected_engine_rate("dummy-model"))
        self.assertFalse(audio_math.uses_protected_engine_rate("test-voice"))

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
        options = audio_math.build_speech_options(
            speaker_id="speaker_seanet",
            speaker_name="SeaNet Voice",
            lang="und",
            package_id="dummy-package-seanet",
            rate=100,
            pitch=50,
            volume=50,
            rateBoost=False,
        )
        self.assertEqual(options["voiceId"], "speaker_seanet")
        self.assertAlmostEqual(options["rate"], audio_math.PROTECTED_ENGINE_RATE, places=3)
        self.assertGreater(options["artificialRate"], 1.0)
        self.assertAlmostEqual(options["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options["postPitch"], 1.0, places=3)
        self.assertAlmostEqual(options["volume"], 0.5, places=4)
        self.assertAlmostEqual(options["outputGain"], 0.85, places=4)

    def test_build_speech_options_seanet_pitch_scaling(self) -> None:
        options_low = audio_math.build_speech_options(
            speaker_id="speaker_seanet",
            speaker_name="SeaNet Voice",
            lang="und",
            package_id="dummy-package-seanet",
            rate=50,
            pitch=0,
            volume=100,
            rateBoost=False,
        )
        self.assertAlmostEqual(options_low["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options_low["postPitch"], 0.4, places=3)

        options_high = audio_math.build_speech_options(
            speaker_id="speaker_seanet",
            speaker_name="SeaNet Voice",
            lang="und",
            package_id="dummy-package-seanet",
            rate=50,
            pitch=100,
            volume=100,
            rateBoost=False,
        )
        self.assertAlmostEqual(options_high["pitch"], 1.0, places=3)
        self.assertAlmostEqual(options_high["postPitch"], 1.6, places=3)

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


if __name__ == "__main__":
    unittest.main()

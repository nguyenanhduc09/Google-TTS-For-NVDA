"""Pure audio mathematics and speech option calculation helpers for Google TTS For NVDA.

This module intentionally has no NVDA imports so the same production logic can
be exercised by standalone regression test suites.
"""

from __future__ import annotations

from typing import Any

OUTPUT_GAIN_MAKEUP: float = 1.70
PROTECTED_ENGINE_RATE: float = 1.25
MIN_ARTIFICIAL_RATE: float = 0.5
MAX_ARTIFICIAL_RATE: float = 2.8


def rate_to_chrome(value: int, rateBoost: bool = False) -> float:
    """Convert an NVDA rate integer (0..100) into a Chromium TTS rate multiplier."""
    percent = max(0, min(100, int(value))) / 100.0
    rate = 0.35 + (2.0 - 0.35) * percent
    if rateBoost:
        rate *= 2
    return round(max(0.1, min(10.0, rate)), 3)


def pitch_to_chrome(pitch: int) -> float:
    """Convert an NVDA pitch integer (0..100) into a Chromium TTS pitch multiplier."""
    pitchSemitones = -12.0 + 24.0 * max(0, min(100, int(pitch))) / 100.0
    return round(max(0.1, min(3.0, 1.0 + pitchSemitones / 20.0)), 3)


def uses_protected_engine_rate(package_id: str) -> bool:
    """Return True if the voice package is a SeaNet model that requires post-rate scaling."""
    return str(package_id or "").lower().endswith("-seanet")


def build_speech_options(
    speaker_id: str,
    speaker_name: str,
    lang: str,
    package_id: str,
    rate: int,
    pitch: int,
    volume: int,
    rateBoost: bool = False,
) -> dict[str, Any]:
    """Calculate the full speech options dictionary for the WASM TTS engine and tempo processor."""
    volumeLevel = max(0.0, min(1.0, int(volume) / 100.0))
    outputGain = max(0.0, min(OUTPUT_GAIN_MAKEUP, volumeLevel * OUTPUT_GAIN_MAKEUP))
    desiredRate = rate_to_chrome(rate, rateBoost)
    engineRate = desiredRate
    artificialRate = 1.0
    isProtected = uses_protected_engine_rate(package_id)
    pitchValue = pitch_to_chrome(pitch)
    enginePitch = 1.0 if isProtected else pitchValue
    postPitch = pitchValue if isProtected else 1.0
    if isProtected and desiredRate > PROTECTED_ENGINE_RATE:
        engineRate = PROTECTED_ENGINE_RATE
        artificialRate = max(MIN_ARTIFICIAL_RATE, min(MAX_ARTIFICIAL_RATE, desiredRate / engineRate))
    return {
        "voiceId": speaker_id,
        "voiceName": speaker_name,
        "lang": lang,
        "rate": round(engineRate, 3),
        "artificialRate": round(artificialRate, 3),
        "pitch": round(enginePitch, 3),
        "postPitch": round(postPitch, 3),
        "volume": round(volumeLevel, 4),
        "outputGain": round(outputGain, 4),
        "nvdaRate": max(0, min(100, int(rate))),
        "rateBoost": bool(rateBoost),
    }

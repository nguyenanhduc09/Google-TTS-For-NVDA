"""Pure audio mathematics and speech option calculation helpers for Google TTS For NVDA.

This module intentionally has no NVDA imports so the same production logic can
be exercised by standalone regression test suites.
"""

from __future__ import annotations

import math
import re
from typing import Any

_SEANET_PACKAGE_RE = re.compile(r"(?:^|-)(?:seanet|lemonbalm)(?:-|$)")

OUTPUT_GAIN_MAKEUP: float = 1.70
PROTECTED_ENGINE_RATE: float = 1.25
MIN_ARTIFICIAL_RATE: float = 0.5
MAX_ARTIFICIAL_RATE: float = 1.85
_SOFT_KNEE_ARTIFICIAL_RATE: float = 1.60
_MAX_RAW_PROTECTED_RATIO: float = 4.0 / PROTECTED_ENGINE_RATE

# 5-point rate factor interpolation over NVDA 0..100 rate slider values.
BREAK_RATE_TABLE: tuple[tuple[int, float], ...] = ((10, 1.8), (43, 1.3), (60, 1.0), (75, 0.7), (85, 0.4))
BREAK_RATE_FACTOR_MIN: float = 0.4
BREAK_RATE_FACTOR_MAX: float = 1.8
END_OF_UTTERANCE_RATE_FACTOR_MIN: float = 0.5
END_OF_UTTERANCE_RATE_FACTOR_MAX: float = 1.6


def interpolate_rate_factor(rate: int, table: tuple[tuple[int, float], ...] = BREAK_RATE_TABLE) -> float:
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


def break_rate_factor(rate: int) -> float:
    """Return the pause scaling factor for explicit breaks and sentence pauses."""
    return max(BREAK_RATE_FACTOR_MIN, min(BREAK_RATE_FACTOR_MAX, interpolate_rate_factor(rate, BREAK_RATE_TABLE)))


def end_of_utterance_rate_factor(rate: int) -> float:
    """Return the pause scaling factor for end-of-utterance tail silence."""
    return max(
        END_OF_UTTERANCE_RATE_FACTOR_MIN,
        min(END_OF_UTTERANCE_RATE_FACTOR_MAX, interpolate_rate_factor(rate, BREAK_RATE_TABLE)),
    )


def clamp_int(value: Any, default: int = 50, minimum: int = 0, maximum: int = 100) -> int:
    """Safely coerce a numeric/config value to a bounded integer, tolerating floats, strings, NaN, and infinity."""
    try:
        fallback = max(minimum, min(maximum, int(default)))
    except (TypeError, ValueError, OverflowError):
        fallback = minimum
    if isinstance(value, bool) or value is None:
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return max(minimum, min(maximum, int(round(numeric))))


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Safely coerce a boolean, string flag, or None config value to bool."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _clamp_percent(value: Any, default: int = 50) -> int:
    """Safely coerce an NVDA 0..100 slider value to a clamped integer."""
    return clamp_int(value, default=default, minimum=0, maximum=100)


def _coerce_bool(value: Any) -> bool:
    """Safely coerce a boolean or string flag to bool."""
    return coerce_bool(value, default=False)


def rate_to_chrome(value: int, rateBoost: bool = False) -> float:
    """Convert an NVDA rate integer (0..100) into a Chromium TTS rate multiplier."""
    percent = _clamp_percent(value, default=50) / 100.0
    rate = 0.35 + (2.0 - 0.35) * percent
    if _coerce_bool(rateBoost):
        rate *= 2
    return round(max(0.1, min(10.0, rate)), 3)


def pitch_to_chrome(pitch: int) -> float:
    """Convert an NVDA pitch integer (0..100) into a Chromium TTS pitch multiplier."""
    pitchSemitones = -12.0 + 24.0 * _clamp_percent(pitch, default=50) / 100.0
    return round(max(0.1, min(3.0, 1.0 + pitchSemitones / 20.0)), 3)


def uses_protected_engine_rate(package_id: str) -> bool:
    """Return True if the voice package uses SeaNet enhancement and requires post-rate scaling."""
    return bool(_SEANET_PACKAGE_RE.search(str(package_id or "").strip().lower()))


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
    clampedRate = _clamp_percent(rate, default=50)
    clampedPitch = _clamp_percent(pitch, default=50)
    clampedVolume = _clamp_percent(volume, default=100)
    rateBoostEnabled = _coerce_bool(rateBoost)
    volumeLevel = max(0.0, min(1.0, clampedVolume / 100.0))
    outputGain = max(0.0, min(OUTPUT_GAIN_MAKEUP, volumeLevel * OUTPUT_GAIN_MAKEUP))
    desiredRate = rate_to_chrome(clampedRate, rateBoostEnabled)
    engineRate = desiredRate
    artificialRate = 1.0
    isProtected = uses_protected_engine_rate(package_id)
    pitchValue = pitch_to_chrome(clampedPitch)
    enginePitch = 1.0 if isProtected else pitchValue
    postPitch = pitchValue if isProtected else 1.0
    if isProtected and desiredRate > PROTECTED_ENGINE_RATE:
        engineRate = PROTECTED_ENGINE_RATE
        rawRatio = desiredRate / engineRate
        if rawRatio <= _SOFT_KNEE_ARTIFICIAL_RATE:
            scaledRatio = rawRatio
        else:
            kneeSpan = max(0.001, _MAX_RAW_PROTECTED_RATIO - _SOFT_KNEE_ARTIFICIAL_RATE)
            kneeProgress = min(1.0, (rawRatio - _SOFT_KNEE_ARTIFICIAL_RATE) / kneeSpan)
            scaledRatio = _SOFT_KNEE_ARTIFICIAL_RATE + (MAX_ARTIFICIAL_RATE - _SOFT_KNEE_ARTIFICIAL_RATE) * kneeProgress
        # Keep speed completely independent of post-pitch shifting on SeaNet/Lemonbalm voices.
        artificialRate = max(MIN_ARTIFICIAL_RATE, min(MAX_ARTIFICIAL_RATE, scaledRatio))
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
        "nvdaRate": clampedRate,
        "rateBoost": rateBoostEnabled,
    }

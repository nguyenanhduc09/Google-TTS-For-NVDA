"""Performance and benchmark tests for Google TTS For NVDA speech processing.

These tests verify key streaming/processing constants and throughput benchmarks:

- Segment flush threshold constant
- PCM lead buffer timing
- Pause mode timing constants
- Adaptive audio packet sizing constants in bridgeHarness.js
- Multilingual segmentation throughput benchmarks
- PCM audio processing throughput benchmarks
"""

from __future__ import annotations

import ast
import re
import time
import unittest
from collections.abc import Callable

from tests.test_support import ROOT, load_driver_module
from tests.test_support import pcm_bytes as _pcm

processing = load_driver_module("speech_processing")
_SEGMENTER = processing.DEFAULT_TEXT_SEGMENTER


def _read_driver_constant(name: str) -> object:
    """Read a module-level literal constant from __init__.py without importing it."""
    path = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*=\s*(.+)$", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Constant {name!r} not found in __init__.py")
    return ast.literal_eval(match.group(1).strip())


class SegmentFlushThresholdTests(unittest.TestCase):
    """Verify the segment flush threshold constant (live flush behavior is tested on real SynthDriver)."""

    def test_threshold_constant_value(self) -> None:
        """Verify the flush threshold constant is set correctly."""
        threshold = _read_driver_constant("_FLUSH_GROUP_CHARS_THRESHOLD")
        self.assertEqual(120, threshold)


class PcmLeadBufferPerformanceTests(unittest.TestCase):
    """Verify PCM lead buffer timing configuration."""

    processing = processing

    def test_lead_buffer_80ms_setting(self) -> None:
        """Verify LIVE_MULTI_SEGMENT_LEAD_MS is set to 80ms."""
        self.assertEqual(80, self.processing.LIVE_MULTI_SEGMENT_LEAD_MS)

    def test_lead_buffer_byte_budget(self) -> None:
        """80ms lead buffer should require fewer bytes than 120ms at same sample rate."""
        sample_rate = 24000
        bytes_per_sample = self.processing.PCM_BYTES_PER_SAMPLE

        lead_80ms = self.processing.pcm_bytes_for_milliseconds(80, sample_rate, bytes_per_sample)
        lead_120ms = self.processing.pcm_bytes_for_milliseconds(120, sample_rate, bytes_per_sample)

        self.assertLess(lead_80ms, lead_120ms)
        # 80ms at 24kHz 16-bit mono = 3840 bytes
        self.assertEqual(3840, lead_80ms)
        # 120ms at 24kHz 16-bit mono = 5760 bytes
        self.assertEqual(5760, lead_120ms)


class PauseModePerformanceTests(unittest.TestCase):
    """Verify pause mode timing constants."""

    processing = processing

    def test_sentence_break_ms_constants(self) -> None:
        """Verify sentence break constants."""
        self.assertEqual(45, _read_driver_constant("_NORMAL_SENTENCE_BREAK_MS"))
        self.assertEqual(15, _read_driver_constant("_SHORTENED_SENTENCE_BREAK_MS"))

    def test_preload_resume_delay(self) -> None:
        """Verify preload resume delay constant."""
        self.assertEqual(0.15, _read_driver_constant("_PRELOAD_RESUME_DELAY_SECONDS"))


def _read_harness_constant(name: str) -> int:
    """Read a packet sizing constant from bridgeHarness.js without running JS."""
    path = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "web" / "bridgeHarness.js"
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"\bconst\s+{name}\s*=\s*(\d+);", text)
    if match is None:
        raise AssertionError(f"Constant {name!r} not found in bridgeHarness.js")
    return int(match.group(1))


class AdaptiveAudioPacketSizingTests(unittest.TestCase):
    """Verify adaptive audio packet sizing constants in bridgeHarness.js."""

    def test_audio_packet_sizing_constants(self) -> None:
        """Verify adaptive audio packet constants balance initial latency and steady IPC."""
        first_samples = _read_harness_constant("firstAudioPacketSamples")
        early_samples = _read_harness_constant("earlyAudioPacketSamples")
        steady_samples = _read_harness_constant("steadyAudioPacketSamples")
        long_samples = _read_harness_constant("longStreamAudioPacketSamples")
        early_count = _read_harness_constant("earlyAudioPacketCount")
        steady_count = _read_harness_constant("steadyAudioPacketCount")

        # First packet: 120 samples (5ms at 24kHz) for initial response
        self.assertEqual(120, first_samples)
        # Early packets: 1200 samples (50ms at 24kHz) for initial 3 packets
        self.assertEqual(1200, early_samples)
        self.assertEqual(3, early_count)
        # Steady packets: 2400 samples (100ms at 24kHz) for packets 4-8
        self.assertEqual(2400, steady_samples)
        self.assertEqual(8, steady_count)
        # Long-stream packets: 3600 samples (150ms at 24kHz) for packets 9+
        self.assertEqual(3600, long_samples)


class SegmentationPerformanceTests(unittest.TestCase):
    """Verify segmentation performance for long multilingual text."""

    processing = processing
    segmenter = _SEGMENTER

    def _measure(self, fn: Callable[[], object], iterations: int = 10) -> float:
        """Measure average elapsed seconds for a callable over multiple iterations."""
        fn()
        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            fn()
            end = time.perf_counter()
            times.append(end - start)
        return sum(times) / len(times)

    def test_latin_1000_chars_sentence_splits(self) -> None:
        """1000 chars of Latin text with punctuation should split in <5ms."""
        text = "This is a test sentence. " * 40  # 1000 chars
        avg_ms = self._measure(lambda: self.segmenter.find_sentence_splits(text)) * 1000
        self.assertLess(avg_ms, 5.0, f"Sentence splits took {avg_ms:.1f}ms for {len(text)} Latin chars")

    def test_latin_5000_chars_sentence_splits(self) -> None:
        """~5000 chars of Latin text should split in <20ms."""
        text = "This is a test sentence with multiple words. " * 110  # 4950 chars
        avg_ms = self._measure(lambda: self.segmenter.find_sentence_splits(text)) * 1000
        self.assertLess(avg_ms, 20.0, f"Sentence splits took {avg_ms:.1f}ms for {len(text)} Latin chars")

    def test_cjk_latency_segments(self) -> None:
        """500 chars of CJK text (no spaces) should segment in <10ms."""
        text = "这是用于测试没有空格的长文本分段并保持语音尽快开始" * 20  # 500 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, False))) * 1000
        self.assertLess(avg_ms, 10.0, f"Latency segments took {avg_ms:.1f}ms for {len(text)} CJK chars")

    def test_thai_latency_segments(self) -> None:
        """~928 chars of Thai text (no spaces) should segment in <30ms."""
        text = "ข้อความภาษาไทยสำหรับทดสอบการแบ่งข้อความยาวโดยไม่มีช่องว่าง" * 16  # 928 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, False))) * 1000
        self.assertLess(avg_ms, 30.0, f"Latency segments took {avg_ms:.1f}ms for {len(text)} Thai chars")

    def test_arabic_sentence_splits(self) -> None:
        """~1120 chars of Arabic text should split in <10ms."""
        text = "هذه جملة اختبار للتقسيم الطويل. " * 35  # 1120 chars
        avg_ms = self._measure(lambda: self.segmenter.find_sentence_splits(text)) * 1000
        self.assertLess(avg_ms, 10.0, f"Sentence splits took {avg_ms:.1f}ms for {len(text)} Arabic chars")

    def test_hindi_latency_segments(self) -> None:
        """~1140 chars of Hindi text should segment in <25ms."""
        text = "यह एक बहुत लंबा वाक्य है जिसमें बहुत सारे शब्द हैं और इसे पढ़ने में समय लगता है " * 15  # 1140 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, False))) * 1000
        self.assertLess(avg_ms, 25.0, f"Latency segments took {avg_ms:.1f}ms for {len(text)} Hindi chars")

    def test_mixed_script_sentence_splits(self) -> None:
        """~2880 chars of mixed script text should split in <15ms."""
        text = ("Hello world.这是一个测试。مرحبا بالعالم।Привет мир. ") * 60  # 2880 chars
        avg_ms = self._measure(lambda: self.segmenter.find_sentence_splits(text)) * 1000
        self.assertLess(avg_ms, 15.0, f"Sentence splits took {avg_ms:.1f}ms for {len(text)} mixed chars")

    def test_emoji_heavy_latency_segments(self) -> None:
        """Long text with heavy emoji usage should segment in <15ms."""
        text = "family 👨‍👩‍👧‍👦 rocket 🚀 celebration 🎉 party 🎊 " * 40  # 1840 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, False))) * 1000
        self.assertLess(avg_ms, 15.0, f"Latency segments took {avg_ms:.1f}ms for {len(text)} emoji chars")

    def test_url_heavy_latency_segments(self) -> None:
        """~1000 chars with many URLs should segment in <10ms."""
        text = "Visit https://example.com/docs/v1.2/index.html?scale=1.5 now. " * 17  # 1054 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, False))) * 1000
        self.assertLess(avg_ms, 10.0, f"Latency segments took {avg_ms:.1f}ms for {len(text)} URL chars")

    def test_fast_first_segment_performance(self) -> None:
        """Fast first segmentation of ~1000 chars should complete in <15ms."""
        text = "This medium length announcement deliberately contains no punctuation " * 15  # 1005 chars
        avg_ms = self._measure(lambda: list(self.segmenter.iter_text_segments_for_latency(text, True))) * 1000
        self.assertLess(avg_ms, 15.0, f"Fast first segments took {avg_ms:.1f}ms for {len(text)} chars")

    def test_segmentation_scales_linearly(self) -> None:
        """Segmentation time should scale roughly linearly with text length."""
        short_text = "This is a test sentence. " * 4  # 100 chars
        long_text = "This is a test sentence. " * 40  # 1000 chars

        short_ms = self._measure(lambda: self.segmenter.find_sentence_splits(short_text), iterations=30) * 1000
        long_ms = self._measure(lambda: self.segmenter.find_sentence_splits(long_text), iterations=30) * 1000

        effective_short_ms = max(short_ms, 0.05)
        ratio = long_ms / effective_short_ms
        self.assertLess(ratio, 20.0, f"Non-linear scaling: {ratio:.1f}x for 10x text length")


class PcmProcessingThroughputTests(unittest.TestCase):
    """Verify audio processing throughput meets real-time requirements."""

    processing = processing

    def test_pcm_silence_shortener_throughput(self) -> None:
        """PCM silence shortener should process audio within real-time budget."""
        processing = self.processing
        sample_rate = 24000
        pcm = _pcm(*([1000] * 100), *([0] * 200), *([800] * 100), *([0] * 200))
        iterations = 100

        start = time.perf_counter()
        for _ in range(iterations):
            s = processing.create_pcm_silence_shortener(
                processing.PAUSE_MODE_SHORTEN_ALL,
                sample_rate,
            )
            s.feed(pcm)
            s.finish()
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.1, f"PCM processing took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()

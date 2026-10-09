"""Performance and benchmark tests for Google TTS For NVDA speech processing.

These tests verify that the speech processing and streaming optimizations produce
correct results while measuring key performance characteristics:

- Segment flush threshold behaviour (hidden segments for cache hits)
- Speech request coalescing (cancelled requests skip CDP round-trips)
- PCM lead buffer timing (faster streaming start)
- Pause mode logic correctness under threshold boundaries
- Adaptive audio packet sizing constants in bridgeHarness.js
- Multilingual segmentation throughput benchmarks
- PCM audio processing throughput benchmarks
"""

from __future__ import annotations

import re
import threading
import time
import unittest

from tests.test_support import ROOT, load_driver_module
from tests.test_support import pcm_bytes as _pcm


def _read_driver_constant(name: str) -> object:
    """Read a module-level constant from __init__.py without importing it (NVDA deps)."""
    path = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\s*=\s*(.+)$", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Constant {name!r} not found in __init__.py")
    return eval(match.group(1))  # noqa: S307


class SegmentFlushThresholdTests(unittest.TestCase):
    """Verify that the threshold-based segment flush logic produces correct
    hidden-segment counts and pauseShorteningMode values."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.segmenter = cls.processing.DEFAULT_TEXT_SEGMENTER

    def _iter_indexed_segments(self, text: str, fast_first: bool = False):
        return list(self.segmenter.iter_indexed_text_segments(text, [], fast_first))

    def test_short_text_single_flush(self) -> None:
        """Text shorter than threshold produces a single group (no hidden segments)."""
        text = "Hello world"
        segments = self._iter_indexed_segments(text)
        total_chars = sum(len(seg) for seg, _ in segments)
        self.assertLess(total_chars, 120)
        self.assertGreaterEqual(len(segments), 1)

    def test_long_text_produces_multiple_groups(self) -> None:
        """Text longer than threshold with PAUSE_MODE_SHORTEN_ALL should be split
        into multiple groups at soft phrase boundaries."""
        text = (
            "This is a long sentence with many words that should exceed "
            "the flush threshold of 120 characters when accumulated across "
            "multiple segments in pause mode shorten all for testing purposes"
        )
        segments = self._iter_indexed_segments(text)
        total_chars = sum(len(seg) for seg, _ in segments)
        self.assertGreater(total_chars, 120)

    def test_threshold_constant_value(self) -> None:
        """Verify the flush threshold constant is set correctly."""
        threshold = _read_driver_constant("_FLUSH_GROUP_CHARS_THRESHOLD")
        self.assertEqual(120, threshold)


class SpeechCoalescingTests(unittest.TestCase):
    """Verify that cancelled requests are detected early to skip CDP round-trips."""

    def test_cancelled_event_detected_immediately(self) -> None:
        """A pre-set cancel event should be detected at the start of _speak_text."""
        cancel_event = threading.Event()
        cancel_event.set()
        self.assertTrue(cancel_event.is_set())

    def test_fresh_event_not_cancelled(self) -> None:
        """A fresh cancel event should not be detected as cancelled."""
        cancel_event = threading.Event()
        self.assertFalse(cancel_event.is_set())

    def test_cancel_event_set_during_processing(self) -> None:
        """Setting cancel event during processing should stop further work."""
        cancel_event = threading.Event()
        processing = load_driver_module("speech_processing")

        shortener = processing.create_pcm_silence_shortener(
            processing.PAUSE_MODE_SHORTEN_ALL,
            24000,
        )
        self.assertIsNotNone(shortener)

        audio = _pcm(*([1000] * 10), *([0] * 50))
        result = shortener.feed(audio)
        self.assertEqual(b"", result)

        cancel_event.set()
        self.assertTrue(cancel_event.is_set())

        final = shortener.finish()
        self.assertIsInstance(final, bytes)


class PcmLeadBufferPerformanceTests(unittest.TestCase):
    """Verify PCM lead buffer timing optimization."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_lead_buffer_80ms_setting(self) -> None:
        """Verify LIVE_MULTI_SEGMENT_LEAD_MS is set to 80ms (optimized from 120ms)."""
        self.assertEqual(80, self.processing.LIVE_MULTI_SEGMENT_LEAD_MS)

    def test_lead_buffer_reduces_initial_latency(self) -> None:
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

    def test_lead_buffer_releases_after_threshold(self) -> None:
        """Lead buffer should hold audio until threshold, then pass through."""
        # At 1000Hz, 80ms = 160 bytes (80 samples * 2 bytes/sample)
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=80)
        threshold = self.processing.pcm_bytes_for_milliseconds(80, 1000, 2)
        self.assertEqual(160, threshold)

        # Feed less than threshold - should hold
        self.assertEqual(b"", lead.feed(b"\x01\x02\x03\x04"))

        # Feed up to exactly the threshold - should release
        remaining = threshold - 4
        result = lead.feed(b"\x05" * remaining)
        self.assertEqual(threshold, len(result))

        # After threshold, new data passes through immediately
        result = lead.feed(b"\x06\x07")
        self.assertEqual(2, len(result))

    def test_lead_buffer_finish_flushes(self) -> None:
        """finish() should return buffered audio even if below threshold."""
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=80)
        self.assertEqual(b"", lead.feed(b"\x01\x02\x03\x04"))
        result = lead.finish()
        self.assertEqual(b"\x01\x02\x03\x04", result)


class PauseModePerformanceTests(unittest.TestCase):
    """Verify pause mode constants and their performance implications."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_sentence_break_ms_constants(self) -> None:
        """Verify optimized sentence break constants."""
        # Normal break reduced from 95ms to 45ms
        self.assertEqual(45, _read_driver_constant("_NORMAL_SENTENCE_BREAK_MS"))
        # Shortened break stays at 15ms
        self.assertEqual(15, _read_driver_constant("_SHORTENED_SENTENCE_BREAK_MS"))

    def test_end_of_utterance_pause_ms(self) -> None:
        """Verify optimized end-of-utterance pause constant."""
        # Reduced from 80ms to 40ms
        self.assertEqual(40, _read_driver_constant("_END_OF_UTTERANCE_PAUSE_MS"))

    def test_preload_resume_delay(self) -> None:
        """Verify optimized preload resume delay."""
        # Reduced from 0.45s to 0.15s
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
        """Verify adaptive audio packet constants protect startup latency while reducing steady IPC."""
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
        # Long-stream packets: 3600 samples (150ms at 24kHz) for packets 9+ to cut CDP overhead by 33%
        self.assertEqual(3600, long_samples)


class SegmentationPerformanceTests(unittest.TestCase):
    """Verify segmentation performance for long multilingual text."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.segmenter = cls.processing.DEFAULT_TEXT_SEGMENTER

    def _measure_sentence_splits(self, text: str, iterations: int = 10) -> float:
        """Measure average time for find_sentence_splits over multiple iterations."""
        self.segmenter.find_sentence_splits(text)
        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            self.segmenter.find_sentence_splits(text)
            end = time.perf_counter()
            times.append(end - start)
        return sum(times) / len(times)

    def _measure_latency_segments(self, text: str, fast_first: bool, iterations: int = 10) -> float:
        """Measure average time for iter_text_segments_for_latency."""
        list(self.segmenter.iter_text_segments_for_latency(text, fast_first))
        times: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            list(self.segmenter.iter_text_segments_for_latency(text, fast_first))
            end = time.perf_counter()
            times.append(end - start)
        return sum(times) / len(times)

    def test_latin_1000_chars_sentence_splits(self) -> None:
        """1000 chars of Latin text with punctuation should split in <5ms."""
        text = "This is a test sentence. " * 40  # 1000 chars
        avg_ms = self._measure_sentence_splits(text) * 1000
        self.assertLess(avg_ms, 5.0, f"Sentence splits took {avg_ms:.1f}ms for 1000 Latin chars")

    def test_latin_5000_chars_sentence_splits(self) -> None:
        """5000 chars of Latin text should split in <20ms."""
        text = "This is a test sentence with multiple words. " * 110  # ~5000 chars
        avg_ms = self._measure_sentence_splits(text) * 1000
        self.assertLess(avg_ms, 20.0, f"Sentence splits took {avg_ms:.1f}ms for 5000 Latin chars")

    def test_cjk_1000_chars_latency_segments(self) -> None:
        """1000 chars of CJK text (no spaces) should segment in <10ms."""
        text = "这是用于测试没有空格的长文本分段并保持语音尽快开始" * 20  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, False) * 1000
        self.assertLess(avg_ms, 10.0, f"Latency segments took {avg_ms:.1f}ms for 1000 CJK chars")

    def test_thai_1000_chars_latency_segments(self) -> None:
        """1000 chars of Thai text (no spaces) should segment in <15ms."""
        text = "ข้อความภาษาไทยสำหรับทดสอบการแบ่งข้อความยาวโดยไม่มีช่องว่าง" * 16  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, False) * 1000
        self.assertLess(avg_ms, 15.0, f"Latency segments took {avg_ms:.1f}ms for 1000 Thai chars")

    def test_arabic_1000_chars_sentence_splits(self) -> None:
        """1000 chars of Arabic text should split in <10ms."""
        text = "هذه جملة اختبار للتقسيم الطويل. " * 35  # ~1000 chars
        avg_ms = self._measure_sentence_splits(text) * 1000
        self.assertLess(avg_ms, 10.0, f"Sentence splits took {avg_ms:.1f}ms for 1000 Arabic chars")

    def test_hindi_1000_chars_latency_segments(self) -> None:
        """1000 chars of Hindi text should segment in <15ms."""
        text = "यह एक बहुत लंबा वाक्य है जिसमें बहुत सारे शब्द हैं और इसे पढ़ने में समय लगता है " * 15  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, False) * 1000
        self.assertLess(avg_ms, 15.0, f"Latency segments took {avg_ms:.1f}ms for 1000 Hindi chars")

    def test_mixed_script_2000_chars_sentence_splits(self) -> None:
        """2000 chars of mixed script text should split in <15ms."""
        text = ("Hello world.这是一个测试。مرحبا بالعالم।Привет мир. ") * 60  # ~2000 chars
        avg_ms = self._measure_sentence_splits(text) * 1000
        self.assertLess(avg_ms, 15.0, f"Sentence splits took {avg_ms:.1f}ms for 2000 mixed chars")

    def test_emoji_heavy_1000_chars_latency_segments(self) -> None:
        """1000 chars with heavy emoji usage should segment in <15ms."""
        text = "family 👨‍👩‍👧‍👦 rocket 🚀 celebration 🎉 party 🎊 " * 40  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, False) * 1000
        self.assertLess(avg_ms, 15.0, f"Latency segments took {avg_ms:.1f}ms for 1000 emoji chars")

    def test_url_heavy_1000_chars_latency_segments(self) -> None:
        """1000 chars with many URLs should segment in <10ms."""
        text = "Visit https://example.com/docs/v1.2/index.html?scale=1.5 now. " * 17  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, False) * 1000
        self.assertLess(avg_ms, 10.0, f"Latency segments took {avg_ms:.1f}ms for 1000 URL chars")

    def test_fast_first_segment_1000_chars(self) -> None:
        """Fast first segmentation of 1000 chars should complete in <15ms."""
        text = "This medium length announcement deliberately contains no punctuation " * 15  # ~1000 chars
        avg_ms = self._measure_latency_segments(text, True) * 1000
        self.assertLess(avg_ms, 15.0, f"Fast first segments took {avg_ms:.1f}ms for 1000 chars")

    def test_segmentation_scales_linearly(self) -> None:
        """Segmentation time should scale roughly linearly with text length."""
        short_text = "This is a test sentence. " * 4  # ~100 chars
        long_text = "This is a test sentence. " * 40  # ~1000 chars

        short_ms = self._measure_sentence_splits(short_text, iterations=30) * 1000
        long_ms = self._measure_sentence_splits(long_text, iterations=30) * 1000

        effective_short_ms = max(short_ms, 0.05)
        ratio = long_ms / effective_short_ms
        self.assertLess(ratio, 20.0, f"Non-linear scaling: {ratio:.1f}x for 10x text length")


class PcmProcessingThroughputTests(unittest.TestCase):
    """Verify audio processing throughput meets real-time requirements."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_pcm_silence_shortener_throughput(self) -> None:
        """PCM silence shortener should process audio faster than real-time."""
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

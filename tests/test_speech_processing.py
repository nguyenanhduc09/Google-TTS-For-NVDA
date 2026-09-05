from __future__ import annotations

import json
import unicodedata
import unittest

from tests.test_support import ROOT, load_driver_module
from tests.test_support import pcm_bytes as _pcm
from tests.test_support import pcm_samples as _samples

CORPUS_PATH = ROOT / "tests" / "segmentation_corpus.json"
SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_OPERATIONS = {"sentenceUnits", "latencySegments"}
REQUIRED_CATEGORIES = {
    "locale-punctuation",
    "abbreviation",
    "url",
    "emoji",
    "cjk",
    "thai",
    "long-sentence",
}


def _materialize_text(case: dict[str, object]) -> str:
    text = case.get("text")
    if isinstance(text, str):
        return text
    builder = case.get("textBuilder")
    if not isinstance(builder, dict):
        raise AssertionError(f"{case['id']}: text or textBuilder is required")
    pattern = str(builder.get("pattern", ""))
    repeat = int(builder.get("repeat", 1))
    separator = str(builder.get("separator", ""))
    return str(builder.get("prefix", "")) + separator.join([pattern] * repeat) + str(builder.get("suffix", ""))


def _sentence_units(segmenter: object, text: str) -> list[str]:
    starts = [0, *segmenter.find_sentence_splits(text)]
    ends = [*starts[1:], len(text)]
    return [text[start:end].strip() for start, end in zip(starts, ends, strict=False) if text[start:end].strip()]


class PcmSilenceShortenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def _process(self, mode: str, pcm: bytes, chunks: list[int] | None = None) -> bytes:
        shortener = self.processing.create_pcm_silence_shortener(mode, 1000)
        if shortener is None:
            return pcm
        output = bytearray()
        if chunks is None:
            chunks = [len(pcm)]
        offset = 0
        for length in chunks:
            output.extend(shortener.feed(pcm[offset : offset + length]))
            offset += length
        output.extend(shortener.feed(pcm[offset:]))
        output.extend(shortener.finish())
        return bytes(output)

    def _process_segments(
        self,
        mode: str,
        segments: list[bytes],
        chunks: list[list[int]] | None = None,
    ) -> bytes:
        shortener = self.processing.create_pcm_silence_shortener(mode, 1000)
        if shortener is None:
            return b"".join(segments)
        output = bytearray()
        chunks = chunks or [[] for _segment in segments]
        for segment_index, segment in enumerate(segments):
            offset = 0
            for length in chunks[segment_index]:
                output.extend(shortener.feed(segment[offset : offset + length]))
                offset += length
            output.extend(shortener.feed(segment[offset:]))
            if segment_index < len(segments) - 1:
                output.extend(
                    shortener.flush_boundary(
                        shortenPause=mode == self.processing.PAUSE_MODE_SHORTEN_ALL,
                    )
                )
            else:
                output.extend(shortener.finish())
        return bytes(output)

    def test_three_pause_modes(self) -> None:
        pcm = _pcm(*([1000] * 4), *([0] * 100), *([1200] * 4), *([0] * 100))
        do_not_shorten = self._process(self.processing.PAUSE_MODE_DO_NOT_SHORTEN, pcm)
        end_only = _samples(self._process(self.processing.PAUSE_MODE_SHORTEN_END_ONLY, pcm))
        shorten_all = _samples(self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm))

        self.assertEqual(pcm, do_not_shorten)
        self.assertEqual((*([1000] * 4), *([0] * 100), *([1200] * 4), *([0] * 35)), end_only)
        self.assertEqual((*([1000] * 4), *([0] * 25), *([1200] * 4), *([0] * 25)), shorten_all)

    def test_noise_floor_is_inclusive(self) -> None:
        self.assertFalse(self.processing.pcm_has_audible_sample(_pcm(-48, 0, 48)))
        self.assertTrue(self.processing.pcm_has_audible_sample(_pcm(-49)))
        self.assertTrue(self.processing.pcm_has_audible_sample(_pcm(49)))

        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        output = shortener.feed(_pcm(*([48] * 60), 49, -49, *([0] * 60))) + shortener.finish()
        self.assertEqual((*([48] * 25), 49, -49, *([0] * 25)), _samples(output))

    def test_pcm_chunk_boundaries_do_not_change_output(self) -> None:
        pcm = _pcm(*([0] * 60), *([800] * 7), *([0] * 60), *([-900] * 7), *([0] * 60))
        whole = self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm)
        for split_offset in range(len(pcm) + 1):
            with self.subTest(single_split=split_offset):
                self.assertEqual(
                    whole,
                    self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm, chunks=[split_offset]),
                )
        for chunk_size in (1, 2, 3, 5, 17, 64):
            with self.subTest(chunk_size=chunk_size):
                chunks = [chunk_size] * (len(pcm) // chunk_size)
                self.assertEqual(
                    whole,
                    self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm, chunks=chunks),
                )

    def test_incomplete_detection_block_is_flushed_at_finish(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        pcm = _pcm(700, 700, 0, 0)
        self.assertEqual(b"", shortener.feed(pcm))
        self.assertEqual(pcm, shortener.finish())

    def test_incomplete_audible_block_is_flushed_at_hidden_boundary(self) -> None:
        first = _pcm(700, 800, 900, 1000)
        second = _pcm(-700, -800, -900, -1000)
        for mode in (
            self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
        ):
            with self.subTest(mode=mode):
                shortener = self.processing.create_pcm_silence_shortener(mode, 1000)
                self.assertIsNotNone(shortener)
                self.assertEqual(b"", shortener.feed(first))
                self.assertEqual(
                    first,
                    shortener.flush_boundary(
                        shortenPause=mode == self.processing.PAUSE_MODE_SHORTEN_ALL,
                    ),
                )
                self.assertEqual(b"", shortener.feed(second))
                self.assertEqual(second, shortener.finish())

    def test_hidden_boundary_chunking_does_not_change_output(self) -> None:
        segments = [
            _pcm(*([0] * 7), *([700] * 4), *([0] * 43), 900),
            _pcm(1000, *([0] * 41), *([-800] * 4), *([0] * 52)),
        ]
        for mode in (
            self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
        ):
            whole = self._process_segments(mode, segments)
            for segment_index, segment in enumerate(segments):
                for split_offset in range(len(segment) + 1):
                    with self.subTest(mode=mode, segment=segment_index, single_split=split_offset):
                        chunks = [[], []]
                        chunks[segment_index] = [split_offset]
                        self.assertEqual(whole, self._process_segments(mode, segments, chunks))
            for chunk_size in (1, 2, 3, 5, 17, 64):
                with self.subTest(mode=mode, chunk_size=chunk_size):
                    chunks = [[chunk_size] * (len(segment) // chunk_size) for segment in segments]
                    self.assertEqual(whole, self._process_segments(mode, segments, chunks))

    def test_end_only_preserves_hidden_boundary_and_shortens_final_end(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
            1000,
        )
        self.assertIsNotNone(shortener)
        first = shortener.feed(_pcm(*([700] * 3), *([0] * 90))) + shortener.flush_boundary(
            shortenPause=False,
        )
        second = shortener.feed(_pcm(*([900] * 2), *([0] * 80))) + shortener.finish()
        self.assertEqual((*([700] * 3), *([0] * 90)), _samples(first))
        self.assertEqual((*([900] * 2), *([0] * 35)), _samples(second))

    def test_shorten_all_shortens_hidden_boundary_and_final_end(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        self.assertIsNotNone(shortener)
        first = shortener.feed(_pcm(*([700] * 3), *([0] * 90))) + shortener.flush_boundary(
            shortenPause=True,
        )
        second = shortener.feed(_pcm(*([900] * 2), *([0] * 80))) + shortener.finish()
        self.assertEqual((*([700] * 3), *([0] * 25)), _samples(first))
        self.assertEqual((*([900] * 2), *([0] * 25)), _samples(second))

    def test_unknown_pause_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.processing.create_pcm_silence_shortener("unknown", 24000)


class PcmLeadBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_lead_is_released_once_and_later_pcm_passes_through(self) -> None:
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=3)
        self.assertEqual(b"", lead.feed(b"\x01\x02"))
        self.assertEqual(b"\x01\x02\x03\x04\x05\x06", lead.feed(b"\x03\x04\x05\x06"))
        self.assertEqual(b"\x07\x08", lead.feed(b"\x07\x08"))
        self.assertEqual(b"", lead.finish())

    def test_short_audio_flushes_without_loss(self) -> None:
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=10)
        self.assertEqual(b"", lead.feed(b"\x01\x02\x03\x04"))
        self.assertEqual(b"\x01\x02\x03\x04", lead.finish())
        self.assertEqual(b"\x05\x06", lead.feed(b"\x05\x06"))


class TextSegmenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.segmenter = cls.processing.DEFAULT_TEXT_SEGMENTER
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    def test_medium_fast_first_segmentation_is_opt_in(self) -> None:
        text = (
            "This medium announcement deliberately has no punctuation and remains below the regular "
            "phrase limit while still being long enough to benefit from a fast first segment"
        )

        regular = list(self.segmenter.iter_text_segments_for_latency(text, False))
        fast_first = list(self.segmenter.iter_text_segments_for_latency(text, True))

        self.assertEqual([text], regular)
        self.assertGreaterEqual(len(fast_first), 2)
        self.assertLessEqual(len(fast_first[0]), self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
        self.assertEqual("".join(text.split()), "".join("".join(fast_first).split()))

    def test_fast_first_limit_applies_only_to_first_segment(self) -> None:
        text = " ".join(["latency"] * 100)

        segments = list(self.segmenter.iter_text_segments_for_latency(text, True))

        self.assertGreaterEqual(len(segments), 3)
        self.assertLessEqual(len(segments[0]), self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
        self.assertTrue(
            any(len(segment) > self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS for segment in segments[1:])
        )

    def test_fast_first_punctuation_free_intact_ceiling(self) -> None:
        """Verify sentences under the intact ceiling remain a single segment."""
        text_93 = "Một nghiên cứu gần đây cho thấy rằng việc tối ưu hóa hiệu năng mang lại kết quả rất khả quan."
        segments = list(self.segmenter.iter_text_segments_for_latency(text_93, True))
        self.assertEqual([text_93], segments)

        en_text = (
            "This ordinary instructional sentence intentionally has no punctuation and stays whole for smooth prosody"
        )
        en_segments = list(self.segmenter.iter_text_segments_for_latency(en_text, True))
        self.assertEqual([en_text], en_segments)

    def test_trailing_orphan_protection_across_scripts(self) -> None:
        """Verify that segments never leave an orphan remainder below minimum threshold."""
        long_text = (
            "Khi chúng tôi tiến hành thử nghiệm cấu hình âm thanh mới thì toàn bộ kết quả "
            "đo đạc thực tế trên hệ thống đều phản hồi rất tích cực và hoạt động vô cùng ổn định."
        )
        segments = list(self.segmenter.iter_text_segments_for_latency(long_text, True))
        self.assertGreaterEqual(len(segments), 2)
        for segment in segments:
            self.assertGreaterEqual(len(segment), self.processing.MIN_ORPHAN_SPACE_CHARS)

    def test_fast_first_early_soft_break_preference(self) -> None:
        """Verify that an early comma produces a faster, natural first segment."""
        text = "Khi chúng tôi tiến hành thử nghiệm cấu hình âm thanh mới, kết quả đo đạc thực tế phản hồi rất tích cực."
        segments = list(self.segmenter.iter_text_segments_for_latency(text, True))
        self.assertEqual(2, len(segments))
        self.assertTrue(segments[0].endswith(","))
        self.assertLessEqual(len(segments[0]), self.processing.FAST_FIRST_PREFERRED_SOFT_CHARS + 5)
        self.assertGreaterEqual(len(segments[1]), self.processing.MIN_ORPHAN_SPACE_CHARS)

    def test_cjk_punctuation_free_intact_ceiling(self) -> None:
        """Verify CJK text under the intact ceiling stays whole without orphan tail."""
        cjk_text = (
            "这是一段专门用于测试无标点符号文本在自适应分段算法下保持完整朗读而不产生孤立片段的中文句子内容并且测试语句长度"
            * 2
        )
        cjk_85 = cjk_text[:85]
        segments = list(self.segmenter.iter_text_segments_for_latency(cjk_85, True))
        self.assertEqual([cjk_85], segments)

    def test_corpus_schema(self) -> None:
        self.assertEqual(SUPPORTED_SCHEMA_VERSION, self.corpus.get("schemaVersion"))
        self.assertIsInstance(self.corpus.get("source"), dict)
        cases = self.corpus.get("cases")
        self.assertIsInstance(cases, list)
        self.assertTrue(cases)
        seen_ids: set[str] = set()
        for case in cases:
            self.assertIsInstance(case, dict)
            case_id = case.get("id")
            self.assertIsInstance(case_id, str)
            self.assertTrue(case_id)
            self.assertNotIn(case_id, seen_ids, f"Duplicate corpus case ID: {case_id}")
            seen_ids.add(case_id)
            self.assertIn(case.get("category"), REQUIRED_CATEGORIES, case_id)
            operation = case.get("operation")
            self.assertIn(operation, SUPPORTED_OPERATIONS, case_id)
            self.assertNotEqual("text" in case, "textBuilder" in case, case_id)
            _materialize_text(case)
            if operation == "sentenceUnits":
                expected = case.get("expected")
                self.assertIsInstance(expected, list, case_id)
                self.assertTrue(all(isinstance(segment, str) and segment for segment in expected), case_id)
            else:
                self.assertIsInstance(case.get("assert"), dict, case_id)

    def test_corpus_cases(self) -> None:
        for case in self.corpus["cases"]:
            with self.subTest(case=case["id"]):
                text = _materialize_text(case)
                operation = case["operation"]
                if operation == "sentenceUnits":
                    self.assertEqual(case["expected"], _sentence_units(self.segmenter, text))
                elif operation == "latencySegments":
                    segments = list(
                        self.segmenter.iter_text_segments_for_latency(text, bool(case.get("fastFirstSegment", False)))
                    )
                    self._assert_latency_segments(case, text, segments)
                else:
                    self.fail(f"Unknown corpus operation: {operation}")

    def _assert_latency_segments(self, case: dict[str, object], text: str, segments: list[str]) -> None:
        assertions = case["assert"]
        self.assertGreaterEqual(len(segments), assertions.get("minSegmentCount", 1))
        if "maxSegmentLength" in assertions:
            self.assertLessEqual(max(map(len, segments)), assertions["maxSegmentLength"])
        if "firstMaxLength" in assertions:
            self.assertLessEqual(len(segments[0]), assertions["firstMaxLength"])
        if assertions.get("preservesNonWhitespace"):

            def compact(value):
                return "".join(value.split())

            self.assertEqual(compact(text), compact("".join(segments)))
        forbidden_starts = tuple(assertions.get("forbidSegmentStartCharacters", []))
        forbidden_ends = tuple(assertions.get("forbidSegmentEndCharacters", []))
        for segment in segments:
            self.assertTrue(segment)
            if forbidden_starts:
                self.assertNotIn(segment[0], forbidden_starts)
            if forbidden_ends:
                self.assertNotIn(segment[-1], forbidden_ends)
            if assertions.get("noLeadingCombiningMark"):
                self.assertFalse(unicodedata.category(segment[0]).startswith("M"))

    def test_corpus_covers_requested_categories(self) -> None:
        categories = {case["category"] for case in self.corpus["cases"]}
        self.assertTrue(categories >= REQUIRED_CATEGORIES)

    def test_no_space_script_segment_limit_ascii_fast_path(self) -> None:
        """Verify ASCII strings return None immediately via fast path, while CJK/Thai return correct limits."""
        # Pure ASCII text returns None without scanning
        self.assertIsNone(self.segmenter._no_space_script_segment_limit("Hello world pure ascii text", 100))
        self.assertIsNone(self.segmenter._no_space_script_segment_limit("1234567890 !@#$%^&*()_+", 100))
        self.assertIsNone(self.segmenter._no_space_script_segment_limit("https://example.com/path?q=1", 100))

        # CJK text returns 80 chars limit
        cjk_text = "这是用于测试没有空格的长文本分段" * 5
        self.assertEqual(80, self.segmenter._no_space_script_segment_limit(cjk_text, 240))

        # Thai text returns 70 chars limit
        thai_text = "ข้อความภาษาไทยสำหรับทดสอบการแบ่งข้อความยาว" * 3
        self.assertEqual(70, self.segmenter._no_space_script_segment_limit(thai_text, 240))

    def test_is_no_space_script_character_ascii_fast_path(self) -> None:
        """Verify _is_no_space_script_character fast path for ASCII and accurate recognition for scripts."""
        is_no_space = self.processing._is_no_space_script_character
        # ASCII characters return False immediately
        for ch in ("a", "Z", "0", "9", " ", ".", "-", "\n"):
            self.assertFalse(is_no_space(ch))

        # CJK / Thai / Khmer characters return True
        self.assertTrue(is_no_space("中"))
        self.assertTrue(is_no_space("文"))
        self.assertTrue(is_no_space("ก"))
        self.assertTrue(is_no_space("ข"))


class ShortAudioCacheKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.expected_option_fields = (
            "voiceId",
            "rate",
            "pitch",
            "postPitch",
            "volume",
            "outputGain",
            "artificialRate",
            "nvdaRate",
        )
        cls.options = {
            "voiceId": "vi-vn-x-multi:gft",
            "rate": 1.0,
            "pitch": 0.0,
            "postPitch": 1.0,
            "volume": 1.0,
            "outputGain": 1.70,
            "artificialRate": 1.0,
            "nvdaRate": 50,
        }

    def test_cache_key_covers_audio_and_segmentation_inputs(self) -> None:
        self.assertEqual(self.expected_option_fields, self.processing.SHORT_AUDIO_CACHE_OPTION_FIELDS)
        baseline = self.processing.short_audio_cache_key("hello", self.options)
        pause_mode_keys = {
            self.processing.short_audio_cache_key(
                "hello",
                self.options,
                pauseShorteningMode=pause_mode,
            )
            for pause_mode in (
                self.processing.PAUSE_MODE_DO_NOT_SHORTEN,
                self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
                self.processing.PAUSE_MODE_SHORTEN_ALL,
            )
        }
        changed_hidden_segments = self.processing.short_audio_cache_key("hello", self.options, ["hel", "lo"])
        changed_pitch_options = dict(self.options, postPitch=1.1)
        changed_pitch = self.processing.short_audio_cache_key("hello", changed_pitch_options)

        self.assertIsNotNone(baseline)
        self.assertEqual(3, len(pause_mode_keys))
        self.assertNotEqual(baseline, changed_hidden_segments)
        self.assertNotEqual(baseline, changed_pitch)
        for option_name in self.options:
            with self.subTest(option=option_name):
                changed_options = dict(self.options)
                changed_options[option_name] = f"changed-{option_name}"
                self.assertNotEqual(
                    baseline,
                    self.processing.short_audio_cache_key("hello", changed_options),
                )

    def test_cache_key_rejects_oversized_text_or_hidden_segments(self) -> None:
        self.assertIsNone(self.processing.short_audio_cache_key("x" * 5001, self.options))
        self.assertIsNone(self.processing.short_audio_cache_key("x", self.options, ["x"] * 25))
        self.assertIsNotNone(self.processing.short_audio_cache_key("x" * 4999, self.options, ["x" * 4999]))
        self.assertIsNone(self.processing.short_audio_cache_key("x", self.options, ["x" * 5001]))

    def test_segment_cache_key_covers_boundary_context(self) -> None:
        baseline = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=False,
            hasNextSegment=True,
        )
        changed_previous = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=True,
            hasNextSegment=True,
        )
        changed_next = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=False,
            hasNextSegment=False,
        )

        self.assertIsNotNone(baseline)
        self.assertNotEqual(baseline, changed_previous)
        self.assertNotEqual(baseline, changed_next)
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "hello",
                dict(self.options, artificialRate=1.2),
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=True,
            )
        )
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "hello",
                dict(self.options, postPitch=1.1),
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=True,
            )
        )
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "",
                self.options,
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=False,
            )
        )

    def test_complete_speech_result_requires_all_boundaries(self) -> None:
        complete = {"success": True, "done": True, "cancelled": False, "segmentEnds": 2}

        self.assertTrue(self.processing.is_complete_speech_result(complete, expectedSegmentEnds=2))
        self.assertFalse(self.processing.is_complete_speech_result(complete, expectedSegmentEnds=1))
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, cancelled=True),
                expectedSegmentEnds=2,
            )
        )
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, done=False),
                expectedSegmentEnds=2,
            )
        )
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, segmentEnds="invalid"),
                expectedSegmentEnds=2,
            )
        )
        self.assertTrue(
            self.processing.is_complete_speech_result(
                {"success": True, "done": True},
                expectedSegmentEnds=0,
            )
        )


class SingleLetterAbbreviationGuardTests(unittest.TestCase):
    """Verify the isascii() guard prevents non-Latin single-letter words from blocking splits."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.segmenter = cls.processing.DEFAULT_TEXT_SEGMENTER

    def test_kannada_single_letter_before_period_splits(self) -> None:
        splits = self.segmenter.find_sentence_splits("\u0caf. \u0caf.")
        self.assertEqual(1, len(splits))

    def test_oriya_single_letter_before_period_splits(self) -> None:
        splits = self.segmenter.find_sentence_splits("\u0b5f. \u0b5f.")
        self.assertEqual(1, len(splits))

    def test_latin_single_letter_still_stays_with_period(self) -> None:
        splits = self.segmenter.find_sentence_splits("U.S.A. is big.")
        self.assertEqual(0, len(splits))


class UnicodeSentenceTerminatorTests(unittest.TestCase):
    """Ensure the public is_sentence_terminator_character covers every script."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_ascii_sentence_terminals(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        for char in (".", "!", "?"):
            with self.subTest(char=char):
                self.assertTrue(is_term(char))
        for char in (",", ";", ":", "-", "("):
            with self.subTest(char=char):
                self.assertFalse(is_term(char))

    def test_cjk_fullwidth_terminals(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        # Chinese/Japanese fullwidth period, exclamation, question
        self.assertTrue(is_term(chr(0x3002)))  # 。
        self.assertTrue(is_term(chr(0xFF01)))  # ！
        self.assertTrue(is_term(chr(0xFF1F)))  # ？

    def test_arabic_sentence_terminals(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0x061E)))  # ؞
        self.assertTrue(is_term(chr(0x061F)))  # ؟
        self.assertTrue(is_term(chr(0x06D4)))  # ۔

    def test_devanagari_danda(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0x0964)))  # ।
        self.assertTrue(is_term(chr(0x0965)))  # ॥

    def test_thai_angular_punctuation(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0x0E5A)))  # ๚
        self.assertTrue(is_term(chr(0x0E5B)))  # ๛

    def test_meetei_mayek_section_marker(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0xAAF0)))  # ꯰
        self.assertTrue(is_term(chr(0xAAF1)))  # ꯱

    def test_tailored_ellipsis_is_sentence_terminal(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0x2026)))  # …

    def test_greek_question_mark(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        self.assertTrue(is_term(chr(0x037E)))  # ;

    def test_non_terminal_punctuation_is_not_sentence_terminal(self) -> None:
        is_term = self.processing.is_sentence_terminator_character
        non_terminals = (
            chr(0x060C),  # Arabic comma
            chr(0x0964 - 1),  # Just before danda
            chr(0xFF0C),  # Fullwidth comma
            chr(0x3001),  # Ideographic comma
            chr(0x0E2B),  # Thai character (not punctuation)
            chr(0x2014),  # Em dash
            chr(0x2013),  # En dash
        )
        for char in non_terminals:
            with self.subTest(char=f"U+{ord(char):04X}"):
                self.assertFalse(is_term(char))


if __name__ == "__main__":
    unittest.main()

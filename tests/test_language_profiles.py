"""Tests for language utilities, detection redirects/matching, and language profiles.

Covers:
  1. Language tag normalization, locale mapping, and localized display names (language_utils.py)
  2. Language dialect redirects and consolidated language matching helpers (language_detector.py)
  3. Mathematical alphanumeric normalization, neutral token classification, script candidate mapping,
     sub-sentence mixed text segmentation, number/currency/unit/time clustering, and emoji preservation (language_profiles.py)
"""

from __future__ import annotations

import unittest

from tests.test_support import load_driver_module

# ---------------------------------------------------------------------------
# LanguageUtilsTests
# ---------------------------------------------------------------------------


class LanguageUtilsTests(unittest.TestCase):
    """Verify language tag normalization, NVDA special locale mappings, and display names."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.language_utils = load_driver_module("language_utils")

    def test_normalize_language(self) -> None:
        self.assertEqual(self.language_utils.normalize_language("en_US"), "en-us")
        self.assertEqual(self.language_utils.normalize_language("vi-VN"), "vi-vn")
        self.assertEqual(self.language_utils.normalize_language("  ZH_cn  "), "zh-cn")
        self.assertEqual(self.language_utils.normalize_language(None), "")

    def test_normalize_language_code(self) -> None:
        self.assertEqual(self.language_utils.normalize_language_code("en_US"), "en-US")
        self.assertEqual(self.language_utils.normalize_language_code("vi-VN"), "vi-VN")
        self.assertEqual(self.language_utils.normalize_language_code(None), "")

    def test_get_nvda_locale_special_cases(self) -> None:
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("cmn-CN"), "zh_CN")
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("cmn-TW"), "zh_TW")
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("yue-HK"), "zh_HK")
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("ar-XA"), "ar")
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("fil-PH"), "tl")

    def test_get_nvda_locale_prefixes(self) -> None:
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("cmn-Hans-CN"), "zh_CN")
        self.assertEqual(self.language_utils.get_nvda_locale_for_language("yue-Hant-HK"), "zh_HK")

    def test_resolve_nvda_locale_fallback_to_en(self) -> None:
        self.assertEqual(self.language_utils.resolve_nvda_locale(None), "en")
        self.assertEqual(self.language_utils.resolve_nvda_locale(""), "en")

    def test_get_language_display_name_with_custom_dict(self) -> None:
        custom_names = {"vi-VN": "Tiếng Việt", "en-US": "English (US)"}
        self.assertEqual(self.language_utils.get_language_display_name("vi-VN", custom_names), "Tiếng Việt")
        self.assertEqual(self.language_utils.get_language_display_name("en_us", custom_names), "English (US)")
        self.assertEqual(self.language_utils.get_language_display_name("fr-FR", custom_names), "fr-FR")


# ---------------------------------------------------------------------------
# LanguageRedirectTests
# ---------------------------------------------------------------------------


class LanguageRedirectTests(unittest.TestCase):
    """Verify redirect_language() redirects unsupported locales to the best
    available alternative."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ld = load_driver_module("language_detector")

    def test_no_redirect_when_language_already_available(self) -> None:
        available = {"en-us", "fr-fr"}
        self.assertIsNone(self.ld.redirect_language("en-us", available))
        self.assertIsNone(self.ld.redirect_language("fr-fr", available))

    def test_no_redirect_for_none_language(self) -> None:
        self.assertIsNone(self.ld.redirect_language(None, {"en-us"}))
        self.assertIsNone(self.ld.redirect_language("", {"en-us"}))

    def test_underscore_is_normalised(self) -> None:
        available = {"fr-fr"}
        self.assertIsNone(self.ld.redirect_language("fr_fr", available))

    def test_underscore_redirect_when_needed(self) -> None:
        available = {"fr-fr"}
        self.assertEqual("fr-fr", self.ld.redirect_language("fr_ca", available))

    # --- explicit redirect map entries -----------------------------------

    def test_french_canadian_redirects_to_fr_fr(self) -> None:
        available = {"fr-fr"}
        self.assertEqual("fr-fr", self.ld.redirect_language("fr-ca", available))

    def test_portuguese_european_redirects_to_pt_br(self) -> None:
        available = {"pt-br"}
        self.assertEqual("pt-br", self.ld.redirect_language("pt-pt", available))

    def test_spanish_spain_redirects_to_es_mx(self) -> None:
        available = {"es-mx"}
        self.assertEqual("es-mx", self.ld.redirect_language("es-es", available))

    def test_german_austrian_redirects_to_de_de(self) -> None:
        available = {"de-de"}
        self.assertEqual("de-de", self.ld.redirect_language("de-at", available))

    def test_german_swiss_redirects_to_de_de(self) -> None:
        available = {"de-de"}
        self.assertEqual("de-de", self.ld.redirect_language("de-ch", available))

    def test_english_gb_redirects_to_en_us(self) -> None:
        available = {"en-us"}
        self.assertEqual("en-us", self.ld.redirect_language("en-gb", available))

    def test_english_australian_redirects_to_en_us(self) -> None:
        available = {"en-us"}
        self.assertEqual("en-us", self.ld.redirect_language("en-au", available))

    def test_italian_swiss_redirects_to_it_it(self) -> None:
        available = {"it-it"}
        self.assertEqual("it-it", self.ld.redirect_language("it-ch", available))

    def test_redirect_prefers_explicit_over_root(self) -> None:
        available = {"fr-fr", "fr-ca"}
        self.assertIsNone(self.ld.redirect_language("fr-ca", available))

    # --- root-language fallback -----------------------------------------

    def test_root_language_fallback_when_no_explicit_redirect(self) -> None:
        available = {"ko-kr"}
        self.assertEqual("ko-kr", self.ld.redirect_language("ko-kp", available))

    def test_root_fallback_returns_first_available(self) -> None:
        available = {"ja-jp", "ja"}
        result = self.ld.redirect_language("ja-xx", available)
        self.assertIn(result, available)

    # --- no redirect possible ------------------------------------------

    def test_no_redirect_when_no_available_language_matches(self) -> None:
        available = {"en-us", "fr-fr"}
        self.assertIsNone(self.ld.redirect_language("xx-xx", available))

    def test_no_redirect_when_available_is_empty(self) -> None:
        self.assertIsNone(self.ld.redirect_language("en-us", set()))


# ---------------------------------------------------------------------------
# LanguageMatchesTests
# ---------------------------------------------------------------------------


class LanguageMatchesTests(unittest.TestCase):
    """Verify the consolidated language_matches() helper."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ld = load_driver_module("language_detector")

    def test_exact_match(self) -> None:
        self.assertTrue(self.ld.language_matches("en-us", "en-us"))

    def test_root_match(self) -> None:
        self.assertTrue(self.ld.language_matches("fr-fr", "fr-ca"))

    def test_alias_match_fil_tl(self) -> None:
        self.assertTrue(self.ld.language_matches("fil", "tl"))
        self.assertTrue(self.ld.language_matches("fil-ph", "tl"))

    def test_chinese_family_match(self) -> None:
        self.assertTrue(self.ld.language_matches("cmn-cn", "zh-hans"))
        self.assertTrue(self.ld.language_matches("zh", "cmn-tw"))
        self.assertTrue(self.ld.language_matches("yue-hk", "zh-hant"))

    def test_no_match_across_families(self) -> None:
        self.assertFalse(self.ld.language_matches("en-us", "fr-fr"))

    def test_none_returns_false(self) -> None:
        self.assertFalse(self.ld.language_matches(None, "en-us"))
        self.assertFalse(self.ld.language_matches("en-us", None))
        self.assertFalse(self.ld.language_matches(None, None))

    def test_empty_string_returns_false(self) -> None:
        self.assertFalse(self.ld.language_matches("", "en-us"))
        self.assertFalse(self.ld.language_matches("en-us", ""))

    def test_normalisation_underscore(self) -> None:
        self.assertTrue(self.ld.language_matches("en_US", "en-us"))

    def test_hebrew_aliases(self) -> None:
        self.assertTrue(self.ld.language_matches("he", "iw"))
        self.assertTrue(self.ld.language_matches("he-il", "iw"))


# ---------------------------------------------------------------------------
# LanguageProfilesTests
# ---------------------------------------------------------------------------


class LanguageProfilesTests(unittest.TestCase):
    """Verify mathematical alphanumeric normalization, neutral token classification,
    sub-sentence mixed text segmentation, clustering, and Unicode integration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.language_profiles = load_driver_module("language_profiles")
        cls.language_detector = load_driver_module("language_detector")
        cls.language_utils = load_driver_module("language_utils")
        cls.speech_processing = load_driver_module("speech_processing")
        cls.unicode_data = load_driver_module("unicode_data")

    def test_normalize_mathematical_alphanumerics_and_letterlike_symbols(self) -> None:
        """Mathematical alphanumeric symbols, Greek/Arabic/Hebrew math, and letterlike constants normalize cleanly."""
        samples = [
            ("\U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428", "Hello"),  # Bold
            ("\U0001d4b3\U0001d4b4\U0001d4b5", "XYZ"),  # Italic
            ("\U0001d5d9\U0001d5ee\U0001d600\U0001d601", "Fast"),  # Sans-Serif Bold
            ("\U0001d7d9\U0001d7da\U0001d7db", "123"),  # Double-Struck Numbers
            ("\U0001d6c2", "α"),  # Greek math alpha
            ("\U0001ee00", "ا"),  # Arabic math alef
            ("\u2135", "א"),  # Hebrew letterlike alef
            ("\u212a = 273.15, \u212f = 2.718", "K = 273.15, e = 2.718"),  # Kelvin & Euler e
            ("\u2115, \u211d, \u2124, \u2102, \u211a", "N, R, Z, C, Q"),  # Blackboard bold
            (
                "\uff26\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48\u3000\uff11\uff12\uff13",
                "Fullwidth 123",
            ),  # Fullwidth ASCII & ideographic space
        ]
        for raw, expected in samples:
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.language_profiles.normalize_mathematical_alphanumeric(raw))

    def test_normalize_enclosed_small_capitals_and_compat_forms(self) -> None:
        """Phonetic small capitals, enclosed/squared letters, CLDR fallbacks, fractions, and ligatures normalize cleanly."""
        samples = [
            ("\u029c\u1d07\u029f\u029f\u1d0f \u029f\u1d00\u0274\u0262", "hello lang"),  # Phonetic Small Capitals
            ("ʜᴇʟʟᴏ ᴡᴏʀʟᴅ", "hello world"),  # Latin Small Capitals
            ("\u249cpple", "apple"),  # Parenthesized small letters (no parentheses for speech)
            ("⒜ ⒝ ⒞", "a b c"),
            ("\U0001f157\U0001f154\U0001f15b\U0001f15b\U0001f15e", "HELLO"),  # Negative circled letters
            ("🅐 🅑 🅒 🅰 🅱 🆎 🄰 🄱 🄲 🆗", "A B C A B AB A B C OK"),  # Circled, squared, and squared words
            ("\u00a9 2026, \u22125°C", "(C) 2026, -5°C"),  # CLDR copyright and minus sign
            ("\u00bd cup, x\u00b2", "1⁄2 cup, x2"),  # Vulgar fraction & superscript
            ("E=mc² H₂O x³ 10⁴", "E=mc2 H2O x3 104"),  # Superscripts & subscripts
            ("Ⅰ Ⅱ Ⅲ Ⅳ Ⅴ", "I II III IV V"),  # Roman numerals
            ("ﬁrst ﬂight", "first flight"),  # Latin ligatures
            ("ﾜｰﾙﾄﾞ", "ワールド"),  # Halfwidth Katakana
            ("Xin\u00a0chào\u202fthế\u3000giới\ufeff", "Xin chào thế giới"),  # NBSP, NNBSP, ideographic space, BOM
        ]
        for raw, expected in samples:
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.language_profiles.normalize_mathematical_alphanumeric(raw))

    def test_token_classifiers_numbers_currencies_units_and_symbols(self) -> None:
        """Number tokens (with UCD currencies, CLDR units, time, ranges) and symbol/emoji tokens are correctly classified."""
        for token in ("123", "+45.6%", "25/12/2026", "10-20%"):
            with self.subTest(num=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # Official UCD 17.0 Category Sc currency symbols
        for token in (
            "$100",
            "100.000₫",
            "€50",
            "£25.50",
            "¥1000",
            "₹500",
            "₽300",
            "₩50000",
            "100฿",
            "50₪",
            "10₿",
            "500₺",
        ):
            with self.subTest(curr=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # CLDR active ISO 4217 currencies
        for token in ("500 USD", "100 EUR", "500000 VND"):
            with self.subTest(iso=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # International SI / CLDR measurement units
        for token in ("50kg", "100km/h", "10 - 20 km", "37 °C", "16 GB", "1 TB", "24px", "3.5 GHz", "500 kWh"):
            with self.subTest(unit=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # International time tokens
        for token in ("08:30", "08:30 AM", "14:30:15", "8h30", "10s", "15min", "2h"):
            with self.subTest(time=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # Math expressions and dimensions
        for token in ("1920x1080", "5 × 10", "10 ± 2"):
            with self.subTest(math=token):
                self.assertTrue(self.language_profiles.is_number_token(token))

        # Negative checks for non-numbers
        for token in ("Hello", "Word123", "E=mc2"):
            with self.subTest(not_num=token):
                self.assertFalse(self.language_profiles.is_number_token(token))

        # Symbol and emoji tokens
        for token in ("😊", "🎉🚀", "@#$%^&*", "1️⃣", "🇻🇳", "👨‍👩‍👧‍👦"):
            with self.subTest(sym=token):
                self.assertTrue(self.language_profiles.is_symbol_or_emoji_token(token))

        self.assertFalse(self.language_profiles.is_symbol_or_emoji_token("Word123"))

    def test_detect_language_numbers_and_symbols_use_preferred_language(self) -> None:
        """Pure numbers, currencies, and symbols route strictly to preferredLanguage."""
        candidates = ["vi-VN", "en-US", "zh-CN"]
        detected_num = self.language_detector.detect_language(
            "123,456.78",
            candidates,
            preferredLanguage="vi-VN",
        )
        self.assertEqual("vi-VN", detected_num)

        detected_sym = self.language_detector.detect_language(
            "🎉🚀🔥",
            candidates,
            preferredLanguage="vi-VN",
        )
        self.assertEqual("vi-VN", detected_sym)

    def test_detect_language_with_language_hint_recovery(self) -> None:
        """Short phrases that CLD2 mispredicts outside candidates recover to candidate space."""
        candidates = ["vi-VN", "en-US", "ja-JP"]
        detected = self.language_detector.detect_language(
            "Peter Parker",
            candidates,
            preferredLanguage="vi-VN",
        )
        self.assertEqual("en-US", detected)

        detected_vi = self.language_detector.detect_language(
            "Cảm ơn",
            candidates,
            preferredLanguage="vi-VN",
        )
        self.assertEqual("vi-VN", detected_vi)

        bold_english = (
            "\U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428 \U0001d430\U0001d428\U0001d42b\U0001d425\U0001d41d"
        )
        detected_math = self.language_detector.detect_language(
            bold_english,
            candidates,
            preferredLanguage="vi-VN",
        )
        self.assertEqual("en-US", detected_math)

    def test_cld2_to_candidate_language_matching(self) -> None:
        """CLD2 language codes and aliases map symmetrically to candidate voices."""
        candidates = [
            "cmn-CN",
            "cmn-TW",
            "yue-HK",
            "ar-XA",
            "fil-PH",
            "he-IL",
            "jv-ID",
            "nb-NO",
            "vi-VN",
            "en-US",
            "ru-RU",
        ]
        self.assertEqual("cmn-CN", self.language_detector._candidate_for_language("zh", candidates))
        self.assertEqual("cmn-TW", self.language_detector._candidate_for_language("zh-Hant", candidates))
        self.assertEqual("ar-XA", self.language_detector._candidate_for_language("ar", candidates))
        self.assertEqual("fil-PH", self.language_detector._candidate_for_language("tl", candidates))
        self.assertEqual("he-IL", self.language_detector._candidate_for_language("iw", candidates))
        self.assertEqual("he-IL", self.language_detector._candidate_for_language("he", candidates))
        self.assertEqual("jv-ID", self.language_detector._candidate_for_language("jw", candidates))
        self.assertEqual("nb-NO", self.language_detector._candidate_for_language("no", candidates))
        self.assertEqual("vi-VN", self.language_detector._candidate_for_language("vi", candidates))
        self.assertEqual("en-US", self.language_detector._candidate_for_language("en", candidates))
        self.assertEqual("ru-RU", self.language_detector._candidate_for_language("ru", candidates))

    def test_segment_mixed_text_preserves_full_content(self) -> None:
        """segment_mixed_text preserves exact normalized string invariant."""
        candidates = ["vi-VN", "en-US", "zh-CN", "ja-JP", "ru-RU"]
        test_strings = [
            "Xin chào Peter Parker, hôm nay là 25/12/2026. Chúc mừng năm mới!",
            "Chào bạn, hãy xem video trên YouTube và ghé thăm Tokyo 東京 nhé.",
            "Tập hợp số tự nhiên là ℕ và số thực là ℝ.",
            "123 + 456 = 579. Kết quả rất tốt!",
            "Здравствуйте! Hello world! Chào buổi sáng!",
            "This is a pure English sentence without any other languages.",
            "Đây là một câu thuần tiếng Việt không có từ ngoại lai nào.",
            "🚀 Bắn tên lửa lên vũ trụ vào lúc 08:30 sáng.",
        ]
        for text in test_strings:
            with self.subTest(text=text):
                segments = self.language_profiles.segment_mixed_text(
                    text,
                    candidates,
                    preferredLanguage="vi-VN",
                )
                reconstructed = "".join(segText for segText, _ in segments)
                expected = self.language_profiles.normalize_mathematical_alphanumeric(text)
                self.assertEqual(expected, reconstructed)
                self.assertTrue(all(segLang in candidates for _, segLang in segments))

    def test_segment_mixed_text_single_candidate(self) -> None:
        """Single candidate language returns single normalized segment."""
        text = "Hello \U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428 123"
        segments = self.language_profiles.segment_mixed_text(
            text,
            ["en-US"],
            preferredLanguage="en-US",
        )
        self.assertEqual([("Hello Hello 123", "en-US")], segments)

    def test_segment_mixed_text_respects_user_candidate_languages_order_without_bias(self) -> None:
        """Sub-sentence segmentation routes multi-language scripts by user priority without hardcoded bias."""
        # 1. Cyrillic: Ukrainian prioritized when user places uk-UA before ru-RU
        cyrillic_text = "Привіт thế giới!"
        uk_first = self.language_profiles.segment_mixed_text(
            cyrillic_text,
            ["vi-VN", "uk-UA", "ru-RU"],
            preferredLanguage="vi-VN",
        )
        self.assertTrue(any(lang == "uk-UA" for _, lang in uk_first))
        self.assertFalse(any(lang == "ru-RU" for _, lang in uk_first))

        # 2. Cyrillic: Russian prioritized when user places ru-RU before uk-UA
        ru_first = self.language_profiles.segment_mixed_text(
            cyrillic_text,
            ["vi-VN", "ru-RU", "uk-UA"],
            preferredLanguage="vi-VN",
        )
        self.assertTrue(any(lang == "ru-RU" for _, lang in ru_first))
        self.assertFalse(any(lang == "uk-UA" for _, lang in ru_first))

        # 3. Cyrillic: preferredLanguage is chosen when preferredLanguage itself matches the script
        uk_preferred = self.language_profiles.segment_mixed_text(
            cyrillic_text,
            ["uk-UA", "ru-RU"],
            preferredLanguage="uk-UA",
        )
        self.assertTrue(all(lang == "uk-UA" for _, lang in uk_preferred))

        # 4. Devanagari: Marathi prioritized when user places mr-IN before hi-IN
        devanagari_text = "नमस्ते thế giới!"
        mr_first = self.language_profiles.segment_mixed_text(
            devanagari_text,
            ["vi-VN", "mr-IN", "hi-IN"],
            preferredLanguage="vi-VN",
        )
        self.assertTrue(any(lang == "mr-IN" for _, lang in mr_first))
        self.assertFalse(any(lang == "hi-IN" for _, lang in mr_first))

    def test_segment_mixed_text_numbers_currencies_units_and_time_clustering(self) -> None:
        """Numbers clustered with currency, measurement units, time, and ranges are routed to preferredLanguage."""
        candidates = ["vi-VN", "en-US"]
        test_cases = [
            ("Hello world 12345 🎉 have a nice day", ["12345", "🎉"]),
            ("Tôi có 50kg gạo giá 100.000 ₫ lúc 8h30.", ["50kg", "100.000 ₫", "8h30"]),
            ("Chạy xe 100km/h từ 10 - 20 km mất 15 phút, nhiệt độ 37 °C.", ["100km/h", "10 - 20 km", "37 °C"]),
            ("Máy tính có 16 GB RAM, ổ cứng 1 TB, màn hình 24px, chip 3.5 GHz.", ["16 GB", "1 TB", "24px", "3.5 GHz"]),
            ("Giảm giá 10-20% cho đơn hàng trên 500 USD hoặc 2.000.000 ₫.", ["10-20%", "500 USD", "2.000.000 ₫"]),
            ("Màn hình 1920x1080 tiêu thụ 500 kWh điện lúc 14:30:15.", ["1920x1080", "500 kWh", "14:30:15"]),
        ]
        for sentence, expected_clusters in test_cases:
            with self.subTest(sentence=sentence):
                segments = self.language_profiles.segment_mixed_text(
                    sentence,
                    candidates,
                    preferredLanguage="vi-VN",
                )
                reconstructed = "".join(segText for segText, _ in segments)
                self.assertEqual(sentence, reconstructed)
                for cluster in expected_clusters:
                    matching_segs = [(text, lang) for text, lang in segments if cluster in text]
                    self.assertTrue(bool(matching_segs), f"Cluster {cluster} not found in segments")
                    self.assertEqual("vi-VN", matching_segs[0][1], f"Cluster {cluster} not assigned to vi-VN")

    def test_intact_emoji_sequences_and_keycaps_preservation(self) -> None:
        """Emoji keycaps, ZWJ sequences, skin tone modifiers, and flag pairs are preserved intact."""
        candidates = ["vi-VN", "en-US"]
        emoji_samples = [
            "Biểu tượng 1️⃣ và 2️⃣ rất đẹp.",
            "Gia đình 👨‍👩‍👧‍👦 đi chơi cùng bạn nữ 👩‍💻.",
            "Cờ Việt Nam 🇻🇳 và Mỹ 🇺🇸 bay phấp phới.",
            "Xin chào 👍🏽 chúc mừng sinh nhật 🎉 🥳 🚀.",
        ]
        for sentence in emoji_samples:
            with self.subTest(sentence=sentence):
                segments = self.language_profiles.segment_mixed_text(
                    sentence,
                    candidates,
                    preferredLanguage="vi-VN",
                )
                reconstructed = "".join(segText for segText, _ in segments)
                self.assertEqual(sentence, reconstructed)
                for keycap in ["1️⃣", "2️⃣", "👨‍👩‍👧‍👦", "👩‍💻", "🇻🇳", "🇺🇸", "👍🏽"]:
                    if keycap in sentence:
                        matching_segs = [text for text, _ in segments if keycap in text]
                        self.assertTrue(bool(matching_segs), f"Emoji {keycap} was broken during segmentation")

    def test_speech_sanitize_table_inherits_unicode_normalization_table(self) -> None:
        """_SPEECH_SANITIZE_TABLE inherits whitespace from official Unicode NORMALIZATION_TABLE."""
        sanitize_table = self.speech_processing._SPEECH_SANITIZE_TABLE
        norm_table = self.unicode_data.NORMALIZATION_TABLE

        self.assertTrue(all(len(val) == 1 for val in sanitize_table.values()))

        for cp, val in norm_table.items():
            if val in (" ", "\n"):
                self.assertIn(cp, sanitize_table)
                self.assertEqual(" ", sanitize_table[cp])

        for pua in (0xE000, 0xE500, 0xF8FF):
            self.assertIn(pua, sanitize_table)
            self.assertEqual(" ", sanitize_table[pua])

        sample = "Hello\u00a0world\u2003test\u3000CJK\ue001icon"
        sanitized = self.speech_processing.DEFAULT_TEXT_SEGMENTER.sanitize_speech_text(sample)
        self.assertEqual(len(sample), len(sanitized))
        self.assertEqual("Hello world test CJK icon", sanitized)

    def test_unicode_data_structural_integration(self) -> None:
        """NO_SPACE_SCRIPT_PROFILES reuses SCRIPT_RANGES and _SCRIPT_TO_CANDIDATE_ROOTS derives from SUPPORTED_LANGUAGE_SCRIPTS."""
        profiles = self.speech_processing.NO_SPACE_SCRIPT_PROFILES
        script_ranges = self.unicode_data.SCRIPT_RANGES

        han_ranges = profiles[0][0]
        self.assertEqual(80, profiles[0][1])
        for r in script_ranges.get("Han", ()):
            self.assertIn(r, han_ranges)

        kana_ranges = profiles[1][0]
        self.assertEqual(80, profiles[1][1])
        for r in script_ranges.get("Hiragana", ()):
            self.assertIn(r, kana_ranges)
        for r in script_ranges.get("Katakana", ()):
            self.assertIn(r, kana_ranges)

        thai_ranges = profiles[2][0]
        self.assertEqual(70, profiles[2][1])
        for r in script_ranges.get("Thai", ()):
            self.assertIn(r, thai_ranges)

        mapping = self.language_profiles._SCRIPT_TO_CANDIDATE_ROOTS
        supported = self.unicode_data.SUPPORTED_LANGUAGE_SCRIPTS
        self.assertNotIn("Latin", mapping)
        self.assertNotIn("Han", mapping)
        for script in ("Cyrillic", "Arabic", "Devanagari", "Hangul", "Hiragana", "Katakana", "Thai", "Hebrew", "Khmer"):
            self.assertIn(script, mapping)
        self.assertIn("he", mapping["Hebrew"])
        self.assertIn("iw", mapping["Hebrew"])
        for script, roots in mapping.items():
            for root in roots:
                if root == "iw":
                    continue
                self.assertIn(root, supported)
                self.assertIn(script, supported[root])

    def test_segmenter_preserves_emoji_zwj_and_modifiers(self) -> None:
        """TextSegmenter._extend_cut_over_combining_marks preserves ZWJ, keycaps, and modifiers."""
        segmenter = self.speech_processing.DEFAULT_TEXT_SEGMENTER
        text = "Hello 👨‍👩‍👧‍👦 world"
        zwj_index = text.find("\u200d")
        self.assertGreater(zwj_index, 0)
        extended = segmenter._extend_cut_over_combining_marks(text, zwj_index, len(text))
        self.assertGreater(extended, zwj_index)


if __name__ == "__main__":
    unittest.main()

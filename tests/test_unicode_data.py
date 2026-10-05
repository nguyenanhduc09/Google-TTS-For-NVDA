"""Tests for Unicode data: runtime script ranges, normalization tables, and generator helpers."""

from __future__ import annotations

import json
import re
import runpy
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

import generate_unicode_data
from tests.test_support import UNICODE_DATA_PATH, load_driver_module

EXPECTED_LANGUAGE_SCRIPT_GROUPS = {
    ("Arabic",): ("ar", "ur"),
    ("Arabic", "Devanagari"): ("ks", "sd"),
    ("Bengali",): ("as", "bn"),
    ("Bengali", "Meetei_Mayek"): ("mni",),
    ("Cyrillic",): ("bg", "ru", "uk"),
    ("Cyrillic", "Latin"): ("sr",),
    ("Devanagari",): ("brx", "doi", "hi", "kok", "mai", "mr", "ne", "sa"),
    ("Greek",): ("el",),
    ("Gujarati",): ("gu",),
    ("Gurmukhi",): ("pa",),
    ("Han",): ("cmn", "yue"),
    ("Han", "Hangul"): ("ko",),
    ("Han", "Hiragana", "Katakana"): ("ja",),
    ("Hebrew",): ("he",),
    ("Kannada",): ("kn",),
    ("Khmer",): ("km",),
    ("Latin",): (
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "en",
        "es",
        "et",
        "fi",
        "fil",
        "fr",
        "hr",
        "hu",
        "id",
        "is",
        "it",
        "jv",
        "lt",
        "lv",
        "ms",
        "nb",
        "nl",
        "pl",
        "pt",
        "ro",
        "sk",
        "sl",
        "sq",
        "su",
        "sv",
        "sw",
        "tr",
        "vi",
    ),
    ("Malayalam",): ("ml",),
    ("Ol_Chiki",): ("sat",),
    ("Oriya",): ("or",),
    ("Sinhala",): ("si",),
    ("Tamil",): ("ta",),
    ("Telugu",): ("te",),
    ("Thai",): ("th",),
}
EXPECTED_LANGUAGE_SCRIPTS = {
    root: scripts for scripts, roots in EXPECTED_LANGUAGE_SCRIPT_GROUPS.items() for root in roots
}


def _contains(ranges: tuple[tuple[int, int], ...], codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in ranges)


def _first_letter_codepoint(ranges: tuple[tuple[int, int], ...]) -> int:
    for start, end in ranges:
        for codepoint in range(start, end + 1):
            if unicodedata.category(chr(codepoint)).startswith("L"):
                return codepoint
    raise AssertionError("Script has no letter recognized by the test Python runtime")


class UnicodeDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = runpy.run_path(str(UNICODE_DATA_PATH))
        catalog = load_driver_module("catalog")
        if not catalog.CATALOG_PATH.is_file():
            raise AssertionError(f"No bundled voices.json was found at {catalog.CATALOG_PATH}")
        packages = json.loads(catalog.CATALOG_PATH.read_text(encoding="utf-8"))
        cls.processing = load_driver_module("speech_processing")
        cls.language_profiles = load_driver_module("language_profiles")
        cls.supported_roots = set()
        for package in packages:
            parts = str(package.get("id", "")).split("-")
            if len(parts) >= 2 and re.fullmatch(r"[a-z]{2,3}", parts[0]) and re.fullmatch(r"[a-z]{2}", parts[1]):
                cls.supported_roots.add(parts[0])

    def test_generated_versions_are_pinned(self) -> None:
        self.assertEqual("17.0.0", self.data["UNICODE_VERSION"])
        self.assertEqual("48.2", self.data["CLDR_VERSION"])

    def test_every_bundled_language_root_has_official_script_data(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        self.assertEqual(self.supported_roots, set(language_scripts))
        self.assertEqual(self.supported_roots, set(language_ranges))
        self.assertEqual(EXPECTED_LANGUAGE_SCRIPTS, language_scripts)
        for root in sorted(self.supported_roots):
            with self.subTest(root=root):
                self.assertTrue(language_scripts[root])
                self.assertTrue(language_ranges[root])

    def test_language_ranges_are_exactly_composed_from_mapped_scripts(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        self.assertEqual(
            {script for scripts in language_scripts.values() for script in scripts},
            set(script_ranges),
        )
        for root, scripts in language_scripts.items():
            with self.subTest(root=root):
                expected_ranges = tuple(span for script in scripts for span in script_ranges[script])
                self.assertEqual(expected_ranges, language_ranges[root])
                self.assertEqual(
                    expected_ranges,
                    self.language_profiles.script_ranges_for_language_root(root),
                )

    def test_script_ranges_are_sorted_and_non_overlapping(self) -> None:
        for script, ranges in self.data["SCRIPT_RANGES"].items():
            with self.subTest(script=script):
                previous_end = -1
                for start, end in ranges:
                    self.assertLessEqual(start, end)
                    self.assertGreater(start, previous_end)
                    previous_end = end

    def test_unicode_17_script_ranges_outside_old_blocks_are_present(self) -> None:
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        cases = {
            "ar": (0x0870, 0x10EC2),
            "cmn": (0x20000, 0x31350, 0x323B0),
            "en": (0xA7D0, 0x10780),
            "ja": (0x1AFF0, 0x1B120, 0x20000),
            "ko": (0xA960, 0xD7B0),
            "mni": (0xABC0,),
            "ru": (0x1C80, 0x2DE0),
            "sat": (0x1C5A,),
        }
        for root, codepoints in cases.items():
            for codepoint in codepoints:
                with self.subTest(root=root, codepoint=f"U+{codepoint:04X}"):
                    self.assertTrue(_contains(language_ranges[root], codepoint))

    def test_automatic_language_profile_fallback_uses_generated_ranges(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        for root, scripts in language_scripts.items():
            alternative_root = next(
                other_root
                for other_root, other_scripts in language_scripts.items()
                if root != other_root and set(scripts).isdisjoint(other_scripts)
            )
            for script in scripts:
                codepoint = _first_letter_codepoint(script_ranges[script])
                with self.subTest(root=root, script=script, codepoint=f"U+{codepoint:04X}"):
                    self.assertEqual(
                        root,
                        self.language_profiles.language_script_signal(
                            chr(codepoint),
                            {root, alternative_root, "unsupported"},
                        ),
                    )

    def test_shared_scripts_remain_ambiguous_between_language_profiles(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        roots = sorted(language_scripts)
        for index, left_root in enumerate(roots):
            for right_root in roots[index + 1 :]:
                shared_scripts = set(language_scripts[left_root]).intersection(language_scripts[right_root])
                for script in shared_scripts:
                    codepoint = _first_letter_codepoint(script_ranges[script])
                    with self.subTest(left=left_root, right=right_root, script=script):
                        self.assertIsNone(
                            self.language_profiles.language_script_signal(
                                chr(codepoint),
                                {left_root, right_root},
                            )
                        )

    def test_language_profile_fallback_rejects_missing_or_nonmatching_scripts(self) -> None:
        self.assertEqual((), self.language_profiles.script_ranges_for_language_root("unsupported"))
        self.assertIsNone(self.language_profiles.language_script_signal("", self.supported_roots))
        self.assertIsNone(self.language_profiles.language_script_signal("🙂", self.supported_roots))
        self.assertFalse(self.language_profiles.token_has_character_in_ranges("🙂", ((0x0041, 0x005A),)))

    def test_official_sentence_terminal_property_is_complete(self) -> None:
        terminals = self.data["SENTENCE_TERMINAL_CODEPOINTS"]
        self.assertEqual(170, len(terminals))
        for codepoint in (0x002E, 0x003F, 0x061E, 0x061F, 0x0964, 0x1B4E, 0x113D4, 0x16D6E):
            with self.subTest(codepoint=f"U+{codepoint:04X}"):
                self.assertIn(codepoint, terminals)

    def test_sentence_terminal_tailoring_is_minimal_and_disjoint(self) -> None:
        tailored = self.processing.TAILORED_SENTENCE_TERMINATORS
        self.assertEqual({0x037E, 0x0DF4, 0x0E5A, 0x0E5B, 0x2026, 0x22EF}, {ord(value) for value in tailored})
        self.assertTrue({ord(value) for value in tailored}.isdisjoint(self.data["SENTENCE_TERMINAL_CODEPOINTS"]))

    def test_normalization_table_is_present_and_consistent(self) -> None:
        norm_table = self.data["NORMALIZATION_TABLE"]
        self.assertGreaterEqual(len(norm_table), 3900)
        self.assertIs(
            self.language_profiles._MATH_ALPHANUMERIC_TRANSLATION_TABLE,
            self.language_profiles.NORMALIZATION_TABLE,
        )
        self.assertEqual(norm_table, self.language_profiles.NORMALIZATION_TABLE)

    def test_normalization_table_covers_spaces_and_controls(self) -> None:
        norm_table = self.data["NORMALIZATION_TABLE"]
        self.assertEqual(" ", norm_table[0x00A0])  # NBSP
        self.assertEqual(" ", norm_table[0x200B])  # ZWSP
        self.assertEqual("", norm_table[0xFEFF])  # BOM
        self.assertEqual("", norm_table[0x200E])  # LRM
        self.assertEqual("", norm_table[0x200F])  # RLM
        self.assertEqual("\n", norm_table[0x2028])  # Line Separator
        self.assertEqual("\n", norm_table[0x2029])  # Paragraph Separator

    def test_normalization_table_covers_stylized_and_compat_letters(self) -> None:
        norm_table = self.data["NORMALIZATION_TABLE"]
        # Small capitals
        self.assertEqual("a", norm_table[0x1D00])
        self.assertEqual("b", norm_table[0x0299])
        self.assertEqual("g", norm_table[0x0262])
        self.assertEqual("f", norm_table[0xA730])
        # Negative circled & squared
        self.assertEqual("A", norm_table[0x1F150])
        self.assertEqual("A", norm_table[0x1F170])
        # Squared words
        self.assertEqual("COOL", norm_table[0x1F192])
        self.assertEqual("OK", norm_table[0x1F197])
        # Parenthesized letters without parentheses for TTS
        self.assertEqual("a", norm_table[0x249C])
        self.assertEqual("z", norm_table[0x24B5])
        self.assertEqual("A", norm_table[0x1F110])
        self.assertEqual("Z", norm_table[0x1F129])
        # CLDR fallbacks
        self.assertEqual("(C)", norm_table[0x00A9])
        self.assertEqual("(R)", norm_table[0x00AE])
        self.assertEqual("-", norm_table[0x2212])
        # Composed Hangul syllable
        self.assertEqual("가", norm_table[0x326E])

    def test_normalization_table_preserves_standard_precomposed_letters(self) -> None:
        norm_table = self.data["NORMALIZATION_TABLE"]
        # Standard accented letters must not be stripped or decomposed
        for char in "áàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụđñüöä":
            self.assertNotIn(ord(char), norm_table)


def _write_engine(root: Path, version: str, *, voices: bool = True) -> Path:
    versionDir = root / version
    versionDir.mkdir(parents=True, exist_ok=True)
    if voices:
        (versionDir / "voices.json").write_text("[]", encoding="utf-8")
    return versionDir


def _printed(printMock: mock.MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in printMock.call_args_list if call.args)


class ParseUcdRecordsTests(unittest.TestCase):
    """Verify _parse_ucd_records parses UCD semicolon-delimited files."""

    def _write_ucd(self, tmpdir: Path, content: str) -> Path:
        path = tmpdir / "Scripts.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_single_codepoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_ucd(Path(td), "# Comment\n0041 ; Lc # Latin\n")
            records = generate_unicode_data._parse_ucd_records(path)
            self.assertEqual([(0x0041, 0x0041, "Lc")], records)

    def test_range(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_ucd(Path(td), "0041..005A ; L # Latin\n")
            records = generate_unicode_data._parse_ucd_records(path)
            self.assertEqual([(0x0041, 0x005A, "L")], records)

    def test_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_ucd(Path(td), "# Only comments\n")
            records = generate_unicode_data._parse_ucd_records(path)
            self.assertEqual([], records)

    def test_multiple_records(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_ucd(
                Path(td),
                "0041..005A ; L # Latin\n0030..0039 ; Nd # Number\n",
            )
            records = generate_unicode_data._parse_ucd_records(path)
            self.assertEqual(2, len(records))


class MergeRangesTests(unittest.TestCase):
    """Verify _merge_ranges merges overlapping and adjacent ranges."""

    def test_non_overlapping(self) -> None:
        result = generate_unicode_data._merge_ranges([(1, 5), (10, 15)])
        self.assertEqual(((1, 5), (10, 15)), result)

    def test_overlapping(self) -> None:
        result = generate_unicode_data._merge_ranges([(1, 5), (3, 10)])
        self.assertEqual(((1, 10),), result)

    def test_adjacent(self) -> None:
        result = generate_unicode_data._merge_ranges([(1, 5), (6, 10)])
        self.assertEqual(((1, 10),), result)

    def test_empty_input(self) -> None:
        result = generate_unicode_data._merge_ranges([])
        self.assertEqual((), result)

    def test_single_range(self) -> None:
        result = generate_unicode_data._merge_ranges([(1, 5)])
        self.assertEqual(((1, 5),), result)

    def test_unsorted_input(self) -> None:
        result = generate_unicode_data._merge_ranges([(10, 15), (1, 5)])
        self.assertEqual(((1, 5), (10, 15)), result)

    def test_three_ranges_merge(self) -> None:
        result = generate_unicode_data._merge_ranges([(1, 3), (2, 5), (6, 10)])
        self.assertEqual(((1, 10),), result)


class ScriptAliasesTests(unittest.TestCase):
    """Verify _script_aliases parses PropertyValueAliases.txt."""

    def _write_aliases(self, tmpdir: Path, content: str) -> Path:
        path = tmpdir / "PropertyValueAliases.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_sc_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_aliases(
                Path(td),
                "# Comment\nsc ; Arab ; Arabic\nsc ; Latn ; Latin\n",
            )
            aliases = generate_unicode_data._script_aliases(path)
            self.assertEqual("Arabic", aliases["Arab"])
            self.assertEqual("Latin", aliases["Latn"])

    def test_hans_hant_always_alias_to_han(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = self._write_aliases(Path(td), "sc ; Arab ; Arabic\n")
            aliases = generate_unicode_data._script_aliases(path)
            self.assertEqual("Han", aliases["Hans"])
            self.assertEqual("Han", aliases["Hant"])


class FormatRangesTests(unittest.TestCase):
    """Verify _format_ranges renders codepoint ranges as C-style tuples."""

    def test_single_range(self) -> None:
        result = generate_unicode_data._format_ranges([(0x0041, 0x005A)])
        self.assertIn("0x0041", result)
        self.assertIn("0x005A", result)

    def test_multiple_ranges(self) -> None:
        result = generate_unicode_data._format_ranges([(1, 5), (10, 15)])
        self.assertIn("0x0001", result)
        self.assertIn("0x000A", result)


class FormatCodepointsTests(unittest.TestCase):
    """Verify _format_codepoints renders sorted hex codepoints."""

    def test_sorted_output(self) -> None:
        result = generate_unicode_data._format_codepoints([0x003F, 0x002E, 0x2026])
        lines = result.strip().split("\n")
        all_text = " ".join(lines)
        pos_2e = all_text.index("0x002E")
        pos_3f = all_text.index("0x003F")
        pos_2026 = all_text.index("0x2026")
        self.assertLess(pos_2e, pos_3f)
        self.assertLess(pos_3f, pos_2026)


class FormatNormalizationTableTests(unittest.TestCase):
    """Verify _format_normalization_table renders sorted hex key-value pairs."""

    def test_sorted_output(self) -> None:
        table = {0x00A0: " ", 0x00A9: "(C)", 0x0041: "A"}
        result = generate_unicode_data._format_normalization_table(table)
        lines = result.strip().split("\n")
        self.assertEqual(3, len(lines))
        self.assertIn("0x0041: 'A'", lines[0])
        self.assertIn("0x00A0: ' '", lines[1])
        self.assertIn("0x00A9: '(C)'", lines[2])


class BuildNormalizationTableTests(unittest.TestCase):
    """Verify _build_normalization_table parses UCD and CLDR data."""

    def test_builds_normalization_table(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            (tmpdir / "PropList.txt").write_text(
                "00A0 ; White_Space # Zs\n200E ; Bidi_Control # Cf\n",
                encoding="utf-8",
            )
            (tmpdir / "UnicodeData.txt").write_text(
                "00BC;VULGAR FRACTION ONE QUARTER;No;0;ON;<fraction> 0031 2044 0034;;;1/4;N;;;;;\n"
                "1F150;NEGATIVE CIRCLED LATIN CAPITAL LETTER A;So;0;ON;;;;;N;;;;;\n",
                encoding="utf-8",
            )
            cldrCommon = tmpdir / "common"
            cldrSupp = cldrCommon / "supplemental"
            cldrSupp.mkdir(parents=True)
            (cldrSupp / "characters.xml").write_text(
                "<supplementalData><characters><character-fallback>"
                '<character value="©"><substitute>(C)</substitute></character>'
                "</character-fallback></characters></supplementalData>",
                encoding="utf-8",
            )
            table = generate_unicode_data._build_normalization_table(tmpdir, cldrCommon)
            self.assertEqual(" ", table[0x00A0])
            self.assertEqual("", table[0x200E])
            self.assertEqual("1⁄4", table[0x00BC])
            self.assertEqual("A", table[0x1F150])
            self.assertEqual("(C)", table[0x00A9])


class RenderModuleTests(unittest.TestCase):
    """Verify _render_module produces valid Python source."""

    def test_output_contains_version(self) -> None:
        result = generate_unicode_data._render_module(
            ucdVersion="17.0.0",
            cldrVersion="48.2",
            languageScripts={"en": ("Latin",)},
            scriptRanges={"Latin": ((0x0041, 0x005A),)},
            sentenceTerminals={0x002E, 0x003F},
        )
        self.assertIn('UNICODE_VERSION = "17.0.0"', result)
        self.assertIn('CLDR_VERSION = "48.2"', result)
        self.assertIn('"en":', result)
        self.assertIn("Latin", result)
        self.assertIn("SENTENCE_TERMINAL_CODEPOINTS", result)
        self.assertNotIn("NORMALIZATION_TABLE", result)

    def test_output_contains_normalization_table_when_provided(self) -> None:
        result = generate_unicode_data._render_module(
            ucdVersion="17.0.0",
            cldrVersion="48.2",
            languageScripts={"en": ("Latin",)},
            scriptRanges={"Latin": ((0x0041, 0x005A),)},
            sentenceTerminals={0x002E, 0x003F},
            normalizationTable={0x00A0: " ", 0x00A9: "(C)"},
        )
        self.assertIn("NORMALIZATION_TABLE: dict[int, str] = {", result)
        self.assertIn("0x00A0: ' '", result)
        self.assertIn("0x00A9: '(C)'", result)


class ConfiguredVoicesJsonTests(unittest.TestCase):
    """Verify the bundled catalog gate fails closed on any unverified engine version."""

    def test_reads_the_version_pinned_by_the_catalog_module(self) -> None:
        self.assertRegex(generate_unicode_data._configured_engine_version(), r"^\d{8}\.\d+$")

    def test_repository_engine_tree_resolves_to_the_pinned_version(self) -> None:
        with mock.patch("builtins.print"):
            voicesJson = generate_unicode_data._configured_voices_json()
        self.assertTrue(voicesJson.is_file(), voicesJson)
        self.assertEqual(generate_unicode_data._configured_engine_version(), voicesJson.parent.name)

    def test_returns_the_catalog_when_versions_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            versionDir = _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_unicode_data, "_configured_engine_version", return_value="20260820.1"),
                mock.patch("builtins.print") as printMock,
            ):
                selected = generate_unicode_data._configured_voices_json([Path(tmp)])
            self.assertEqual(versionDir / "voices.json", selected)
            printed = _printed(printMock)
            self.assertIn("Target engine catalog", printed)
            self.assertNotIn("ABORT", printed)

    def test_aborts_when_the_tree_is_ahead_of_the_catalog_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_unicode_data, "_configured_engine_version", return_value="20250101.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_unicode_data._configured_voices_json([Path(tmp)])
            self.assertIn("Bump ENGINE_VERSION", _printed(printMock))

    def test_aborts_when_the_catalog_module_points_at_an_unbundled_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_unicode_data, "_configured_engine_version", return_value="20260920.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_unicode_data._configured_voices_json([Path(tmp)])
            self.assertIn("no bundled engine directory", _printed(printMock))

    def test_aborts_when_the_engine_version_cannot_be_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_unicode_data, "_configured_engine_version", return_value=""),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_unicode_data._configured_voices_json([Path(tmp)])
            self.assertIn("Could not read ENGINE_VERSION", _printed(printMock))

    def test_aborts_when_the_newest_engine_has_no_voices_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1", voices=False)
            with (
                mock.patch.object(generate_unicode_data, "_configured_engine_version", return_value="20260820.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_unicode_data._configured_voices_json([Path(tmp)])
            self.assertIn("has no voices.json", _printed(printMock))

    def test_aborts_when_no_version_directory_exists(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("builtins.print"),
            self.assertRaises(SystemExit),
        ):
            generate_unicode_data._configured_voices_json([Path(tmp)])


if __name__ == "__main__":
    unittest.main()

"""Tests for pure helper functions in generate_unicode_data.py.

Covers UCD record parsing, script alias resolution, range merging, module rendering
helpers, and the fail-closed engine version policy.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import generate_unicode_data


def _write_engine(root: Path, version: str, *, voices: bool = True) -> Path:
    versionDir = root / version
    versionDir.mkdir(parents=True, exist_ok=True)
    if voices:
        (versionDir / "voices.json").write_text("[]", encoding="utf-8")
    return versionDir


def _printed(printMock: mock.MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in printMock.call_args_list if call.args)


# ---------------------------------------------------------------------------
# _parse_ucd_records
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _merge_ranges
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _script_aliases
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _format_ranges
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _format_codepoints
# ---------------------------------------------------------------------------


class FormatCodepointsTests(unittest.TestCase):
    """Verify _format_codepoints renders sorted hex codepoints."""

    def test_sorted_output(self) -> None:
        result = generate_unicode_data._format_codepoints([0x003F, 0x002E, 0x2026])
        lines = result.strip().split("\n")
        all_text = " ".join(lines)
        # 0x002E should come before 0x003F which comes before 0x2026
        pos_2e = all_text.index("0x002E")
        pos_3f = all_text.index("0x003F")
        pos_2026 = all_text.index("0x2026")
        self.assertLess(pos_2e, pos_3f)
        self.assertLess(pos_3f, pos_2026)


# ---------------------------------------------------------------------------
# _render_module
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _version_sort_key / newest_bundled_engine_dir / check_engine_version_alignment
# ---------------------------------------------------------------------------


class VersionSortKeyTests(unittest.TestCase):
    """Verify _version_sort_key orders engine versions numerically."""

    def test_newest_engine_versions_sort_last(self) -> None:
        versions = ["20260625.1", "20260820.1", "20251001.2", "20260820.2"]
        self.assertEqual(
            sorted(versions, key=generate_unicode_data._version_sort_key),
            ["20251001.2", "20260625.1", "20260820.1", "20260820.2"],
        )

    def test_numeric_tokens_are_compared_as_numbers(self) -> None:
        self.assertLess(
            generate_unicode_data._version_sort_key("20260820.1"),
            generate_unicode_data._version_sort_key("20260820.10"),
        )


class NewestBundledEngineDirTests(unittest.TestCase):
    """Verify dynamic discovery of the newest bundled engine version directory."""

    def test_picks_the_newest_version_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine(root, "20260625.1")
            _write_engine(root, "20260820.1")
            (root / "notes").mkdir()
            (root / "readme.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(root / "20260820.1", generate_unicode_data.newest_bundled_engine_dir([root]))

    def test_version_directory_without_a_catalog_is_still_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine(root, "20260625.1")
            _write_engine(root, "20260820.1", voices=False)
            self.assertEqual(root / "20260820.1", generate_unicode_data.newest_bundled_engine_dir([root]))

    def test_returns_none_when_no_version_directory_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            self.assertIsNone(generate_unicode_data.newest_bundled_engine_dir([root]))
            self.assertIsNone(generate_unicode_data.newest_bundled_engine_dir([root / "missing"]))

    def test_repository_engine_tree_is_discovered(self) -> None:
        engineDir = generate_unicode_data.newest_bundled_engine_dir()
        self.assertIsNotNone(engineDir)
        assert engineDir is not None
        self.assertTrue(engineDir.is_dir(), engineDir)
        self.assertTrue((engineDir / "voices.json").is_file(), engineDir)


class EngineVersionAlignmentTests(unittest.TestCase):
    """Verify the shared engine version alignment messages."""

    def test_matching_versions_are_aligned(self) -> None:
        self.assertIsNone(generate_unicode_data.check_engine_version_alignment("20260820.1", "20260820.1"))

    def test_newer_bundled_engine_requires_a_version_bump(self) -> None:
        message = generate_unicode_data.check_engine_version_alignment("20260820.1", "20260625.1")
        assert message is not None
        self.assertIn("20260820.1", message)
        self.assertIn("Bump ENGINE_VERSION", message)

    def test_newer_configured_engine_requires_a_bundle(self) -> None:
        message = generate_unicode_data.check_engine_version_alignment("20260820.1", "20260920.1")
        assert message is not None
        self.assertIn("20260920.1", message)
        self.assertIn("no bundled engine directory", message)

    def test_unreadable_engine_version_is_reported(self) -> None:
        message = generate_unicode_data.check_engine_version_alignment("20260820.1", "")
        assert message is not None
        self.assertIn("Could not read ENGINE_VERSION", message)

    def test_missing_bundled_engine_is_reported(self) -> None:
        message = generate_unicode_data.check_engine_version_alignment("", "20260820.1")
        assert message is not None
        self.assertIn("No bundled WasmTtsEngine version directory", message)


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

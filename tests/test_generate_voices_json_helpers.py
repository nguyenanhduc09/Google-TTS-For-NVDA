"""Standalone tests for the engine version helpers in generate_voices_json.py.

The catalog generator is an online developer tool, but its engine version policy is
pure filesystem logic: the newest bundled engine is discovered from the tree, must
match the production catalog module, and must contain its upstream ``voices.json``.
Any unreadable or disagreeing version aborts instead of writing a catalog that the
add-on would not serve.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import generate_voices_json


def _write_engine(root: Path, version: str, *, voices: bool = True) -> Path:
    versionDir = root / version
    versionDir.mkdir(parents=True, exist_ok=True)
    if voices:
        (versionDir / "voices.json").write_text("[]", encoding="utf-8")
    return versionDir


def _printed(printMock: mock.MagicMock) -> str:
    return " ".join(str(call.args[0]) for call in printMock.call_args_list if call.args)


class VersionSortKeyTests(unittest.TestCase):
    def test_newest_engine_versions_sort_last(self) -> None:
        versions = ["20260625.1", "20260820.1", "20251001.2", "20260820.2"]
        self.assertEqual(
            sorted(versions, key=generate_voices_json._version_sort_key),
            ["20251001.2", "20260625.1", "20260820.1", "20260820.2"],
        )

    def test_numeric_tokens_are_compared_as_numbers(self) -> None:
        self.assertLess(
            generate_voices_json._version_sort_key("20260625.9"),
            generate_voices_json._version_sort_key("20260820.1"),
        )
        self.assertLess(
            generate_voices_json._version_sort_key("20260820.1"),
            generate_voices_json._version_sort_key("20260820.10"),
        )


class NewestBundledEngineDirTests(unittest.TestCase):
    def test_picks_the_newest_engine_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine(root, "20260625.1")
            _write_engine(root, "20260820.1")
            (root / "notes").mkdir()
            (root / "readme.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(
                root / "20260820.1",
                generate_voices_json.newest_bundled_engine_dir([root]),
            )

    def test_version_directory_without_a_catalog_is_still_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_engine(root, "20260625.1")
            _write_engine(root, "20260820.1", voices=False)
            self.assertEqual(
                root / "20260820.1",
                generate_voices_json.newest_bundled_engine_dir([root]),
            )

    def test_returns_none_when_no_version_directory_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            self.assertIsNone(generate_voices_json.newest_bundled_engine_dir([root]))
            self.assertIsNone(generate_voices_json.newest_bundled_engine_dir([root / "missing"]))

    def test_repository_engine_tree_is_discovered(self) -> None:
        engineDir = generate_voices_json.newest_bundled_engine_dir()
        self.assertIsNotNone(engineDir)
        assert engineDir is not None
        self.assertTrue(engineDir.is_dir(), engineDir)
        self.assertEqual("voices.json", (engineDir / "voices.json").name)
        self.assertTrue((engineDir / "voices.json").is_file(), engineDir)


class LatestEngineVoicesJsonTests(unittest.TestCase):
    def test_picks_the_newest_engine_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ("20260625.1", "20260820.1"):
                versionDir = root / version
                versionDir.mkdir()
                (versionDir / "voices.json").write_text("[]", encoding="utf-8")
            (root / "notes").mkdir()
            (root / "readme.txt").write_text("ignored", encoding="utf-8")
            self.assertEqual(
                root / "20260820.1" / "voices.json",
                generate_voices_json.latest_engine_voices_json(root),
            )

    def test_returns_none_when_no_catalog_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "20260820.1").mkdir()
            self.assertIsNone(generate_voices_json.latest_engine_voices_json(root))
            self.assertIsNone(generate_voices_json.latest_engine_voices_json(root / "missing"))

    def test_repository_engine_tree_is_discovered(self) -> None:
        voicesJson = generate_voices_json.latest_engine_voices_json()
        self.assertIsNotNone(voicesJson)
        assert voicesJson is not None
        self.assertTrue(voicesJson.is_file(), voicesJson)
        self.assertEqual("voices.json", voicesJson.name)


class CatalogEngineVersionTests(unittest.TestCase):
    def test_reads_the_version_pinned_by_the_catalog_module(self) -> None:
        self.assertRegex(generate_voices_json.catalog_engine_version(), r"^\d{8}\.\d+$")

    def test_missing_catalog_module_returns_empty_string(self) -> None:
        with mock.patch.object(generate_voices_json, "CATALOG_MODULE_CANDIDATES", [Path("does-not-exist.py")]):
            self.assertEqual("", generate_voices_json.catalog_engine_version())


class EngineVersionAlignmentTests(unittest.TestCase):
    def test_matching_versions_are_aligned(self) -> None:
        self.assertIsNone(generate_voices_json.check_engine_version_alignment("20260820.1", "20260820.1"))

    def test_newer_bundled_engine_requires_a_version_bump(self) -> None:
        message = generate_voices_json.check_engine_version_alignment("20260820.1", "20260625.1")
        assert message is not None
        self.assertIn("20260820.1", message)
        self.assertIn("20260625.1", message)
        self.assertIn("Bump ENGINE_VERSION", message)

    def test_newer_configured_engine_requires_a_bundle(self) -> None:
        message = generate_voices_json.check_engine_version_alignment("20260820.1", "20260920.1")
        assert message is not None
        self.assertIn("20260920.1", message)
        self.assertIn("no bundled engine directory", message)

    def test_unreadable_engine_version_is_reported(self) -> None:
        message = generate_voices_json.check_engine_version_alignment("20260820.1", "")
        assert message is not None
        self.assertIn("Could not read ENGINE_VERSION", message)

    def test_missing_bundled_engine_is_reported(self) -> None:
        message = generate_voices_json.check_engine_version_alignment("", "20260820.1")
        assert message is not None
        self.assertIn("No bundled WasmTtsEngine version directory", message)


class SelectEngineVoicesJsonTests(unittest.TestCase):
    def test_aborts_when_no_engine_catalog_exists(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("builtins.print"),
            self.assertRaises(SystemExit),
        ):
            generate_voices_json.select_engine_voices_json([Path(tmp)])

    def test_aborts_when_the_newest_engine_has_no_voices_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1", voices=False)
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value="20260820.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_voices_json.select_engine_voices_json([Path(tmp)])
            self.assertIn("has no voices.json", _printed(printMock))

    def test_aborts_when_the_tree_is_ahead_of_the_catalog_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value="20250101.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_voices_json.select_engine_voices_json([Path(tmp)])
            self.assertIn("Bump ENGINE_VERSION", _printed(printMock))

    def test_aborts_when_the_catalog_module_points_at_an_unbundled_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value="20260920.1"),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_voices_json.select_engine_voices_json([Path(tmp)])
            self.assertIn("no bundled engine directory", _printed(printMock))

    def test_aborts_when_the_engine_version_cannot_be_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value=""),
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_voices_json.select_engine_voices_json([Path(tmp)])
            self.assertIn("Could not read ENGINE_VERSION", _printed(printMock))

    def test_succeeds_when_versions_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            versionDir = _write_engine(Path(tmp), "20260820.1")
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value="20260820.1"),
                mock.patch("builtins.print") as printMock,
            ):
                selected = generate_voices_json.select_engine_voices_json([Path(tmp)])
            self.assertEqual(versionDir / "voices.json", selected)
            printed = _printed(printMock)
            self.assertIn("Target engine catalog", printed)
            self.assertNotIn("ABORT", printed)

    def test_repository_engine_tree_is_selectable(self) -> None:
        with mock.patch("builtins.print"):
            selected = generate_voices_json.select_engine_voices_json()
        self.assertTrue(selected.is_file(), selected)
        self.assertEqual(generate_voices_json.catalog_engine_version(), selected.parent.name)

    def test_explicit_catalog_skips_the_version_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "voices.json"
            explicit.write_text("[]", encoding="utf-8")
            with (
                mock.patch.object(generate_voices_json, "catalog_engine_version", return_value="20250101.1"),
                mock.patch("builtins.print") as printMock,
            ):
                selected = generate_voices_json.select_engine_voices_json(None, explicit)
            self.assertEqual(explicit, selected)
            self.assertIn("skipped", _printed(printMock))

    def test_explicit_catalog_aborts_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("builtins.print") as printMock,
                self.assertRaises(SystemExit),
            ):
                generate_voices_json.select_engine_voices_json(None, Path(tmp) / "missing.json")
            self.assertIn("was not found", _printed(printMock))


class ParseArgsTests(unittest.TestCase):
    def test_defaults_to_no_explicit_catalog(self) -> None:
        self.assertIsNone(generate_voices_json._parse_args([]).voices_json)

    def test_explicit_catalog_is_a_path(self) -> None:
        args = generate_voices_json._parse_args(["--voices-json", "engine/voices.json"])
        self.assertEqual(Path("engine") / "voices.json", args.voices_json)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import build_i18n
from tests.test_support import interactive_options_for, patched_i18n_cli, temporary_doc_tree


def _fake_msgmerge(merged: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        output_index = arguments.index("--output-file") + 1
        Path(arguments[output_index]).write_text(merged, encoding="utf-8")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    return fake_run


@contextmanager
def _locale_paths_patch(root: Path, pot_path: Path) -> Iterator[None]:
    with (
        mock.patch.object(build_i18n, "POT_PATH", pot_path),
        mock.patch.object(build_i18n, "LOCALE_DIR", root / "locale"),
        mock.patch.object(build_i18n, "ADDON_DIR", root),
    ):
        yield


def _interactive_options_for(
    inputs: list[str],
    *,
    languages: list[str] | None = None,
    doc_languages: list[str] | None = None,
) -> tuple[object, ...]:
    return interactive_options_for(build_i18n, inputs, languages=languages, doc_languages=doc_languages)


class TranslationTemplateUpdateTests(unittest.TestCase):
    def test_update_command_accepts_all_and_multiple_languages(self) -> None:
        language_dirs = [build_i18n.LOCALE_DIR / "ru", build_i18n.LOCALE_DIR / "uk"]
        cases = (
            (["--all-languages"], None),
            (["--language", "ru", "--language", "uk"], ["ru", "uk"]),
        )
        for selection_arguments, expected_selection in cases:
            with (
                self.subTest(selection_arguments=selection_arguments),
                patched_i18n_cli(build_i18n, ["--update-po", *selection_arguments]),
                mock.patch.object(build_i18n, "_translatable_source_messages", return_value={"New": []}),
                mock.patch.object(build_i18n, "_write_pot", return_value=build_i18n.POT_PATH),
                mock.patch.object(
                    build_i18n,
                    "_language_dirs",
                    return_value=(language_dirs, []),
                ) as language_dirs_mock,
                mock.patch.object(build_i18n, "_find_msgmerge", return_value=Path("msgmerge")),
                mock.patch.object(
                    build_i18n,
                    "_update_po_from_template",
                    return_value=(1, 1, 1),
                ) as update_mock,
            ):
                result = build_i18n.main()

            self.assertEqual(0, result)
            language_dirs_mock.assert_called_once_with(expected_selection, allow_create=True)
            self.assertEqual(2, update_mock.call_count)

    def test_update_menu_can_select_one_or_all_locales(self) -> None:
        all_options = _interactive_options_for(["3", "1"], languages=["ru", "uk"])
        one_options = _interactive_options_for(["3", "3"], languages=["ru", "uk"])

        self.assertIsNone(all_options[0])
        self.assertTrue(all_options[4])
        self.assertEqual(["uk"], one_options[0])
        self.assertTrue(one_options[4])

    def test_pot_project_version_comes_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest_path = root / "manifest.ini"
            pot_path = root / "nvda.pot"
            manifest_path.write_text("version = 9.8.7\n", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest_path),
                mock.patch.object(build_i18n, "POT_PATH", pot_path),
            ):
                build_i18n._write_pot({})
            text = pot_path.read_text(encoding="utf-8")
            self.assertIn("Project-Id-Version: Google TTS For NVDA 9.8.7", text)

    def test_update_adds_empty_strings_and_removes_obsolete_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            language_dir = root / "uk"
            messages_dir = language_dir / "LC_MESSAGES"
            messages_dir.mkdir(parents=True)
            po_path = messages_dir / "nvda.po"
            pot_path = root / "nvda.pot"
            original = """msgid ""
msgstr ""
"Language: uk\\n"
"Last-Translator: Translator\\n"

msgid "Keep"
msgstr "Зберегти"

msgid "Old active"
msgstr "Старий"
"""
            po_path.write_text(original, encoding="utf-8")
            pot_path.write_text("template", encoding="utf-8")
            merged = """msgid ""
msgstr ""
"Language: uk\\n"
"Last-Translator: Translator\\n"

#: current.py:1
msgid "Keep"
msgstr "Зберегти"

#: current.py:2
msgid "New"
msgstr ""

#~ msgid "Old active"
#~ msgstr "Старий"

#~ msgid "Older obsolete"
#~ msgstr "Давній"
"""

            with (
                mock.patch.object(build_i18n, "POT_PATH", pot_path),
                mock.patch.object(build_i18n.subprocess, "run", side_effect=_fake_msgmerge(merged)),
            ):
                preserved, added, removed = build_i18n._update_po_from_template(
                    language_dir,
                    Path("msgmerge"),
                    {"Keep": ["current.py:1"], "New": ["current.py:2"]},
                )

            self.assertEqual((1, 1, 2), (preserved, added, removed))
            catalog = build_i18n._parse_po(po_path, include_untranslated=True)
            self.assertEqual("Зберегти", catalog["Keep"])
            self.assertEqual("", catalog["New"])
            self.assertNotIn("Old active", catalog)
            self.assertIn("Last-Translator: Translator", catalog[""])
            self.assertNotIn("#~", po_path.read_text(encoding="utf-8"))

    def test_update_rejects_nonempty_translation_for_new_string(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            language_dir = root / "uk"
            messages_dir = language_dir / "LC_MESSAGES"
            messages_dir.mkdir(parents=True)
            po_path = messages_dir / "nvda.po"
            pot_path = root / "nvda.pot"
            original = 'msgid ""\nmsgstr "Language: uk\\n"\n'
            po_path.write_text(original, encoding="utf-8")
            pot_path.write_text("template", encoding="utf-8")
            merged = """msgid ""
msgstr "Language: uk\\n"

msgid "New"
msgstr "Guessed translation"
"""

            with (
                mock.patch.object(build_i18n, "POT_PATH", pot_path),
                mock.patch.object(build_i18n.subprocess, "run", side_effect=_fake_msgmerge(merged)),
                self.assertRaisesRegex(RuntimeError, "new source strings received non-empty translations"),
            ):
                build_i18n._update_po_from_template(
                    language_dir,
                    Path("msgmerge"),
                    {"New": ["current.py:1"]},
                )
            self.assertEqual(original, po_path.read_text(encoding="utf-8"))

    def test_update_po_from_template_initial_creation_counts_new_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "uk"
            pot_path = root / "nvda.pot"
            required = {"Alpha": ["a.py:1"], "Beta": ["b.py:2"]}
            with _locale_paths_patch(root, pot_path):
                preserved, added, removed = build_i18n._update_po_from_template(lang_dir, None, required)
                self.assertEqual((0, 2, 0), (preserved, added, removed))

                fresh_lang = root / "fr"
                with (
                    mock.patch.object(build_i18n, "_verify_merged_po", side_effect=RuntimeError("boom")),
                    self.assertRaises(RuntimeError),
                ):
                    build_i18n._update_po_from_template(fresh_lang, None, required)
                self.assertFalse((fresh_lang / "LC_MESSAGES" / "nvda.po").exists())

    def test_translatable_source_messages_skips_empty_manifest_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src_dir = Path(td) / "src"
            src_dir.mkdir()
            (src_dir / "mod.py").write_text('_("Hello")\n', encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "TRANSLATABLE_SOURCE_DIRS", (src_dir,)),
                mock.patch.object(build_i18n, "_manifest_values", return_value={"summary": "", "description": "Desc"}),
            ):
                messages = build_i18n._translatable_source_messages()
            self.assertNotIn("", messages)
            self.assertIn("Desc", messages)
            self.assertIn("Hello", messages)

    def test_check_catalog_skips_manifest_when_disabled(self) -> None:
        with mock.patch.object(build_i18n, "_manifest_values", side_effect=AssertionError("should not read")):
            errors = build_i18n._check_catalog(
                Path("vi"),
                {"Hello": "Xin chào"},
                checks={build_i18n.CHECK_UI},
                required_messages={"Hello": ["a.py:1"]},
            )
        self.assertEqual([], errors)


class I18nBuildAndCheckTests(unittest.TestCase):
    def test_interactive_options_task_4_build_docs(self) -> None:
        options = _interactive_options_for(["4", "1", "1", "2"], doc_languages=["ru", "uk"])
        self.assertIsNone(options[0])
        self.assertEqual(set(build_i18n.DEFAULT_DOC_CHECKS), options[1])
        self.assertFalse(options[2])
        self.assertFalse(options[3])
        self.assertFalse(options[4])
        self.assertTrue(options[5])
        self.assertFalse(options[8])

    def test_interactive_options_task_4_check_docs(self) -> None:
        options = _interactive_options_for(["4", "1", "1", "1"], doc_languages=["ru", "uk"])
        self.assertIsNone(options[0])
        self.assertEqual(set(build_i18n.DEFAULT_DOC_CHECKS), options[1])
        self.assertFalse(options[2])
        self.assertFalse(options[3])
        self.assertFalse(options[4])
        self.assertFalse(options[5])
        self.assertTrue(options[8])

    def test_interactive_options_task_4_custom_doc_check(self) -> None:
        options = _interactive_options_for(["4", "1", "6", "1"], doc_languages=["ru"])
        self.assertEqual({build_i18n.CHECK_FUZZY}, options[1])
        self.assertTrue(options[8])

    def test_interactive_options_fuzzy_check(self) -> None:
        options = _interactive_options_for(["1", "1", "9", "1"], languages=["ru"])
        self.assertEqual({build_i18n.CHECK_FUZZY}, options[1])
        self.assertTrue(options[2])  # check_only is True

    def test_main_build_docs_flag(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--build-docs", "--language", "ru"]),
            mock.patch.object(build_i18n, "_addon_languages", return_value=["ru"]),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]),
            mock.patch.object(
                build_i18n, "_build_doc_for_language", return_value=(True, "Generated doc")
            ) as build_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        build_mock.assert_called_once_with("ru")

    def test_main_build_docs_aborts_on_check_errors(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--build-docs", "--language", "ru"]),
            mock.patch.object(build_i18n, "_addon_languages", return_value=["ru"]),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=["ru: tag mismatch"]),
            mock.patch.object(
                build_i18n, "_build_doc_for_language", return_value=(True, "Generated doc")
            ) as build_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(1, result)
        build_mock.assert_not_called()

    def test_interactive_options_task_5_extract_doc_template(self) -> None:
        options = _interactive_options_for(["5"], languages=["ru", "uk"])
        self.assertIsNone(options[0])
        self.assertFalse(options[2])  # check_only is False for template extraction
        self.assertFalse(options[3])
        self.assertFalse(options[4])
        self.assertFalse(options[5])
        self.assertTrue(options[6])  # extract_doc_template
        self.assertFalse(options[7])
        self.assertFalse(options[8])

    def test_interactive_options_task_6_update_doc_po(self) -> None:
        options = _interactive_options_for(["6", "1"], doc_languages=["ru", "uk"])
        self.assertIsNone(options[0])
        self.assertFalse(options[2])
        self.assertFalse(options[3])
        self.assertFalse(options[4])
        self.assertFalse(options[5])
        self.assertFalse(options[6])

    def test_interactive_options_task_2_extract_template(self) -> None:
        options = _interactive_options_for(["2"], languages=["ru", "uk"])
        self.assertIsNone(options[0])
        self.assertFalse(options[2])  # check_only is False
        self.assertTrue(options[3])  # extract_template
        self.assertFalse(options[4])
        self.assertFalse(options[5])
        self.assertFalse(options[6])
        self.assertFalse(options[7])
        self.assertFalse(options[8])

    def test_main_interactive_menu_task_2_succeeds(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--menu"]),
            mock.patch("builtins.input", side_effect=["2"]),
            mock.patch.object(build_i18n, "_write_pot", return_value=build_i18n.POT_PATH) as write_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        write_mock.assert_called_once()

    def test_main_interactive_menu_task_5_succeeds(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--menu"]),
            mock.patch("builtins.input", side_effect=["5"]),
            mock.patch.object(build_i18n, "_write_doc_pot", return_value=build_i18n.DOC_POT_PATH) as write_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        write_mock.assert_called_once()

    def test_interactive_menu_exit_with_zero(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--menu"]),
            mock.patch("builtins.input", side_effect=["0"]),
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)

    def test_interactive_menu_sub_menus_go_back_with_zero(self) -> None:
        inputs = [
            "1",
            "0",  # Task 1 -> Lang '0' (Go back) -> back to main menu
            "1",
            "1",
            "0",  # Task 1 -> Lang '1' -> Checks '0' (Go back) -> back to main menu
            "1",
            "1",
            "1",
            "0",  # Task 1 -> Lang '1' -> Checks '1' -> Action '0' (Go back) -> back to main menu
            "4",
            "0",  # Task 4 -> Lang '0' (Go back) -> back to main menu
            "4",
            "1",
            "0",  # Task 4 -> Lang '1' -> Checks '0' (Go back) -> back to main menu
            "4",
            "1",
            "1",
            "0",  # Task 4 -> Lang '1' -> Checks '1' -> Action '0' (Go back) -> back to main menu
            "3",
            "0",  # Task 3 -> Lang '0' (Go back) -> back to main menu
            "6",
            "0",  # Task 6 -> Lang '0' (Go back) -> back to main menu
            "7",
            "0",  # Task 7 -> Lang '0' (Go back) -> back to main menu
            "8",
            "0",  # Task 8 -> Lang '0' (Go back) -> back to main menu
            "0",  # Main menu -> '0' (Exit)
        ]
        with (
            patched_i18n_cli(build_i18n, ["--menu"]),
            mock.patch.object(build_i18n, "_addon_languages", return_value=["vi", "ru"]),
            mock.patch.object(build_i18n, "_addon_doc_languages", return_value=["vi", "ru"]),
            mock.patch("builtins.input", side_effect=inputs),
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)

    def test_prompt_language_code_allows_back_with_zero(self) -> None:
        with (
            mock.patch("builtins.input", side_effect=["0"]),
            mock.patch("builtins.print"),
        ):
            res = build_i18n._prompt_language_code(allow_back=True)
        self.assertIsNone(res)

    def test_main_build_docs_succeeds_on_existing_html_without_po(self) -> None:
        with (
            temporary_doc_tree(
                build_i18n,
                en_html="<!DOCTYPE html><html lang='en'><body><p>Hello</p></body></html>",
                lang="vi",
                html_text="<!DOCTYPE html><html lang='vi'><body><p>Xin chao</p></body></html>",
                patch_doc_pot=True,
            ),
            patched_i18n_cli(build_i18n, ["--build-docs", "-l", "vi"]),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]),
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)

    def test_main_build_ui_alias_flag(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--build", "-l", "vi"]),
            mock.patch.object(build_i18n, "_check_language_files", return_value=[]),
            mock.patch.object(build_i18n, "_check_catalog", return_value=[]),
            mock.patch.object(build_i18n, "_compile_mo"),
            mock.patch.object(build_i18n, "_compile_mo_file"),
            mock.patch.object(build_i18n, "_write_translated_manifest"),
            mock.patch.object(build_i18n, "_write_pot", return_value=build_i18n.POT_PATH),
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)

    def test_main_check_docs_flag(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--check-docs", "-l", "vi"]),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]) as check_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        check_mock.assert_called_once_with("vi", supported_languages=mock.ANY, checks=mock.ANY, check_only=True)

    def test_check_doc_language_flags_unsupported_language(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            lang="fake_lang",
            po_text='msgid ""\nmsgstr ""\n',
            patch_en_path=False,
        ):
            errors = build_i18n._check_doc_language("fake_lang", supported_languages={"en", "vi"})
            self.assertTrue(any("language code is not present in the NVDA locale folder" in e for e in errors))

    def test_check_doc_language_flags_html_tag_issues(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            en_html='<!DOCTYPE html><html lang="en"><body><p>Press <kbd>Ctrl+S</kbd></p></body></html>',
            lang="vi",
            po_text=(
                'msgid ""\nmsgstr ""\n\n'
                'msgid "Press <kbd>Ctrl+S</kbd>"\n'
                'msgstr "Nhấn <kbd>Ctrl+S"\n'  # missing closing </kbd>
            ),
        ):
            errors = build_i18n._check_doc_language("vi")
            self.assertTrue(any("HTML tag issue" in e for e in errors))

    def test_check_doc_language_flags_missing_obsolete_fuzzy(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            en_html='<!DOCTYPE html><html lang="en"><body><h1>Header</h1><p>Active</p></body></html>',
            lang="vi",
            po_text=(
                'msgid ""\nmsgstr ""\n\n'
                '#, fuzzy\nmsgid "Header"\nmsgstr "Tiêu đề cũ"\n\n'
                'msgid "Obsolete"\nmsgstr "Thừa"\n\n'
                'msgid "Active"\nmsgstr ""\n'
            ),
        ):
            errors = build_i18n._check_doc_language("vi")
            self.assertTrue(any("fuzzy documentation string" in e for e in errors))
            self.assertTrue(any("obsolete documentation string" in e for e in errors))
            self.assertTrue(any("untranslated documentation string" in e for e in errors))

    def test_main_extract_doc_template_flag(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--extract-doc-template"]),
            mock.patch.object(build_i18n, "_write_doc_pot", return_value=build_i18n.DOC_POT_PATH) as pot_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        pot_mock.assert_called_once()

    def test_main_update_doc_po_flag(self) -> None:
        with (
            patched_i18n_cli(build_i18n, ["--update-doc-po", "-l", "vi"]),
            mock.patch.object(build_i18n, "_write_doc_pot", return_value=build_i18n.DOC_POT_PATH),
            mock.patch.object(build_i18n, "_find_msgmerge", return_value=None),
            mock.patch.object(build_i18n, "_update_doc_po_from_template", return_value=(10, 0, 0)) as update_mock,
        ):
            result = build_i18n.main()
        self.assertEqual(0, result)
        update_mock.assert_called_once_with("vi", None)

    def test_doc_cli_flag_aliases(self) -> None:
        aliases = [
            (["--check-doc", "-l", "vi"]),
            (["--build-doc", "-l", "vi"]),
            (["--extract-docs-template"]),
            (["--update-docs-po", "-l", "vi"]),
        ]
        for flags in aliases:
            with (
                self.subTest(flags=flags),
                patched_i18n_cli(build_i18n, flags),
                mock.patch.object(build_i18n, "_write_doc_pot", return_value=build_i18n.DOC_POT_PATH),
                mock.patch.object(build_i18n, "_check_doc_language", return_value=[]),
                mock.patch.object(build_i18n, "_build_doc_for_language", return_value=(True, "OK")),
                mock.patch.object(build_i18n, "_update_doc_po_from_template", return_value=(1, 0, 0)),
                mock.patch.object(build_i18n, "_find_msgmerge", return_value=None),
            ):
                result = build_i18n.main()
                self.assertEqual(0, result)

    def test_check_catalog_detects_fuzzy_msgids(self) -> None:
        errors = build_i18n._check_catalog(
            Path("fake/ru"),
            {"Hello": "Привет"},
            {build_i18n.CHECK_FUZZY},
            fuzzy_msgids={"Hello"},
        )
        self.assertTrue(any("fuzzy translation requires review" in err for err in errors))

    def test_check_language_files_flags_rtl_missing_dir_rtl(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            lang="ar",
            html_text="<html><body>No RTL</body></html>",
            locale_po_text='msgid ""\nmsgstr ""\n',
            manifest_text="summary = Test\n",
            patch_en_path=False,
        ) as env:
            errors = build_i18n._check_language_files(
                env.locale_lang_dir,
                supported_languages={"ar"},
                checks={build_i18n.CHECK_DOCS},
                check_only=True,
            )
            self.assertTrue(any('RTL documentation missing dir="rtl"' in err for err in errors))

    def test_check_language_files_defaults_do_not_require_doc(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            lang="de",
            create_lang_dir=False,
            locale_po_text='msgid ""\nmsgstr ""\n',
            manifest_text="summary = Test\n",
            patch_en_path=False,
        ) as env:
            errors = build_i18n._check_language_files(
                env.locale_lang_dir,
                supported_languages={"de"},
                checks=set(build_i18n.DEFAULT_UI_CHECKS),
                check_only=True,
            )
            self.assertEqual([], errors)

    def test_update_doc_po_rejects_nonempty_translation_for_new_string(self) -> None:
        merged = """msgid ""
msgstr "Language: vi\\n"

msgid "New doc"
msgstr "Guessed translation"
"""
        with temporary_doc_tree(
            build_i18n,
            lang="vi",
            po_text='msgid ""\nmsgstr "Language: vi\\n"\n',
            patch_en_path=False,
            patch_addon_dir=False,
            patch_doc_pot=True,
        ) as env:
            env.doc_pot.write_text('msgid ""\nmsgstr ""\n\nmsgid "New doc"\nmsgstr ""\n', encoding="utf-8")
            with (
                mock.patch.object(build_i18n.subprocess, "run", side_effect=_fake_msgmerge(merged)),
                self.assertRaisesRegex(RuntimeError, "new documentation strings received non-empty translations"),
            ):
                build_i18n._update_doc_po_from_template("vi", Path("msgmerge"))

    def test_update_po_without_msgmerge_uses_internal_merger(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "locale" / "vi"
            messages_dir = lang_dir / "LC_MESSAGES"
            messages_dir.mkdir(parents=True)
            po_path = messages_dir / "nvda.po"
            pot_path = root / "locale" / "nvda.pot"
            original = """msgid ""
msgstr ""
"Language: vi\\n"

#, fuzzy
msgid "Old fuzzy"
msgstr "Mờ cũ"

msgid "Retain"
msgstr "Giữ lại"

msgid "Obsolete string"
msgstr "Thừa"
"""
            po_path.write_text(original, encoding="utf-8")
            pot_path.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Retain"\nmsgstr ""\n\nmsgid "Old fuzzy"\nmsgstr ""\n\nmsgid "New string"\nmsgstr ""\n',
                encoding="utf-8",
            )
            with _locale_paths_patch(root, pot_path):
                preserved, added, removed = build_i18n._update_po_from_template(
                    lang_dir,
                    None,
                    {"Retain": ["file.py:1"], "Old fuzzy": ["file.py:2"], "New string": ["file.py:3"]},
                )
            self.assertEqual(2, preserved)
            self.assertEqual(1, added)
            self.assertEqual(1, removed)
            catalog = build_i18n._parse_po(po_path, include_untranslated=True)
            self.assertEqual("Giữ lại", catalog.get("Retain"))
            self.assertEqual("Mờ cũ", catalog.get("Old fuzzy"))
            self.assertEqual("", catalog.get("New string"))
            self.assertNotIn("Obsolete string", catalog)
            fuzzy_ids = build_i18n._po_fuzzy_msgids(po_path)
            self.assertIn("Old fuzzy", fuzzy_ids)

    def test_check_doc_language_without_po_checks_html(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            en_html='<!DOCTYPE html><html lang="en"><body><p>Text with <kbd>Ctrl+S</kbd></p></body></html>',
            lang="vi",
            html_text='<!DOCTYPE html><html lang="vi"><body><p>Text with <kbd>Ctrl+S</kbd></p></body></html>',
        ) as env:
            errors = build_i18n._check_doc_language("vi")
            self.assertEqual([], errors)

            # Now corrupt the HTML in readme.html
            assert env.html_file is not None
            env.html_file.write_text(
                '<!DOCTYPE html><html lang="vi"><body><p>Text with <kbd>Ctrl+S</p></body></html>',
                encoding="utf-8",
            )
            errors_bad = build_i18n._check_doc_language("vi")
            self.assertTrue(any("HTML tag issue in readme.html" in e for e in errors_bad))

    def test_update_po_creates_missing_language_folder_and_po(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "locale" / "ja"
            pot_path = root / "locale" / "nvda.pot"
            pot_path.parent.mkdir(parents=True)
            pot_path.write_text('msgid ""\nmsgstr ""\n"Language: \\n"\n\nmsgid "Hello"\nmsgstr ""\n', encoding="utf-8")
            with _locale_paths_patch(root, pot_path):
                dirs, errs = build_i18n._language_dirs(["ja"], allow_create=True)
                self.assertEqual([], errs)
                self.assertTrue(lang_dir.is_dir())
                preserved, added, removed = build_i18n._update_po_from_template(
                    lang_dir,
                    None,
                    {"Hello": ["file.py:1"]},
                )
                po_path = lang_dir / "LC_MESSAGES" / "nvda.po"
                self.assertTrue(po_path.is_file())
                self.assertIn("Language: ja", po_path.read_text(encoding="utf-8"))

    def test_md_to_html_all_languages_skips_missing_markdown(self) -> None:
        with temporary_doc_tree(
            build_i18n,
            lang="fr",
            html_text="<p>bonjour</p>",
            patch_en_path=False,
        ) as env:
            (env.doc_root / "vi").mkdir(parents=True)
            (env.doc_root / "vi" / "readme.md").write_text("# Tiêu đề\n\nXin chào", encoding="utf-8")
            with patched_i18n_cli(build_i18n, ["--md-to-html", "--all-languages"]):
                result = build_i18n.main()
                self.assertEqual(0, result)
                # vi readme.html should be created from readme.md
                self.assertTrue((env.doc_root / "vi" / "readme.html").is_file())

    def test_html_to_md_all_languages_skips_missing_html(self) -> None:
        with (
            temporary_doc_tree(
                build_i18n,
                lang="es",
                po_text='msgid ""\nmsgstr ""\n',
                patch_en_path=False,
            ),
            patched_i18n_cli(build_i18n, ["--html-to-md", "--all-languages"]),
        ):
            result = build_i18n.main()
            self.assertEqual(0, result)


if __name__ == "__main__":
    unittest.main()

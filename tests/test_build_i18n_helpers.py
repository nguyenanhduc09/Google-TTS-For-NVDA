"""Tests for pure helper functions in build_i18n.py.

Covers PO file parsing, MO compilation, language code normalization,
string formatting utilities, and manifest value extraction.
"""

from __future__ import annotations

import html
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_i18n

# ---------------------------------------------------------------------------
# _parse_po
# ---------------------------------------------------------------------------


class ParsePoTests(unittest.TestCase):
    """Verify PO file parsing handles standard and edge-case files."""

    def _write_po(self, tmpdir: Path, content: str) -> Path:
        po_path = tmpdir / "test.po"
        po_path.write_text(content, encoding="utf-8")
        return po_path

    def test_simple_po(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = self._write_po(
                Path(td),
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "Привіт"\n',
            )
            catalog = build_i18n._parse_po(po_path)
            self.assertIn("Hello", catalog)
            self.assertTrue(catalog["Hello"])

    def test_multiline_msgstr(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = self._write_po(
                Path(td),
                'msgid ""\nmsgstr ""\n\nmsgid "Multi"\nmsgstr ""\n"line1\\n"\n"line2"\n',
            )
            catalog = build_i18n._parse_po(po_path)
            self.assertEqual("line1\nline2", catalog["Multi"])

    def test_empty_msgstr_not_included(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = self._write_po(
                Path(td),
                'msgid ""\nmsgstr ""\n\nmsgid "Untranslated"\nmsgstr ""\n',
            )
            catalog = build_i18n._parse_po(po_path)
            self.assertNotIn("Untranslated", catalog)

    def test_include_untranslated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = self._write_po(
                Path(td),
                'msgid ""\nmsgstr ""\n\nmsgid "Untranslated"\nmsgstr ""\n',
            )
            catalog = build_i18n._parse_po(po_path, include_untranslated=True)
            self.assertIn("Untranslated", catalog)

    def test_msgctxt_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = self._write_po(
                Path(td),
                'msgid ""\nmsgstr ""\n\nmsgctxt "ctx"\nmsgid "Key"\nmsgstr "Val"\n',
            )
            catalog = build_i18n._parse_po(po_path)
            self.assertEqual("Val", catalog["Key"])


# ---------------------------------------------------------------------------
# _format_set
# ---------------------------------------------------------------------------


class FormatSetTests(unittest.TestCase):
    """Verify _format_set extracts placeholders from format strings."""

    def test_python_brace_placeholders(self) -> None:
        result = build_i18n._format_set("Hello {name}, you have {count} items")
        self.assertEqual({"{name}", "{count}"}, result)

    def test_no_placeholders(self) -> None:
        result = build_i18n._format_set("No placeholders here")
        self.assertEqual(set(), result)

    def test_empty_string(self) -> None:
        result = build_i18n._format_set("")
        self.assertEqual(set(), result)

    def test_repeated_placeholder(self) -> None:
        result = build_i18n._format_set("{x} and {x}")
        self.assertEqual({"{x}"}, result)


# ---------------------------------------------------------------------------
# _normalize_language_code
# ---------------------------------------------------------------------------


class NormalizeLanguageCodeTests(unittest.TestCase):
    """Verify _normalize_language_code normalizes language codes."""

    def test_simple_code(self) -> None:
        self.assertEqual("en", build_i18n._normalize_language_code("en"))

    def test_locale_with_dash(self) -> None:
        self.assertEqual("en_US", build_i18n._normalize_language_code("en-us"))

    def test_locale_with_underscore(self) -> None:
        self.assertEqual("en_US", build_i18n._normalize_language_code("en_US"))

    def test_whitespace_trimmed(self) -> None:
        self.assertEqual("fr_FR", build_i18n._normalize_language_code("  fr-fr  "))

    def test_empty_string(self) -> None:
        self.assertEqual("", build_i18n._normalize_language_code(""))


# ---------------------------------------------------------------------------
# _po_escape and _po_quoted_lines
# ---------------------------------------------------------------------------


class PoEscapeTests(unittest.TestCase):
    """Verify PO string escaping and quoting."""

    def test_backslash_escaped(self) -> None:
        result = build_i18n._po_escape("path\\to")
        self.assertEqual("path\\\\to", result)

    def test_quote_escaped(self) -> None:
        result = build_i18n._po_escape('say "hello"')
        self.assertEqual('say \\"hello\\"', result)

    def test_newline_escaped(self) -> None:
        result = build_i18n._po_escape("line1\nline2")
        self.assertEqual("line1\\nline2", result)

    def test_tab_escaped(self) -> None:
        result = build_i18n._po_escape("col1\tcol2")
        self.assertEqual("col1\\tcol2", result)

    def test_quoted_lines_single_line(self) -> None:
        result = build_i18n._po_quoted_lines("hello")
        self.assertEqual(['"hello"'], result)

    def test_quoted_lines_empty(self) -> None:
        result = build_i18n._po_quoted_lines("")
        self.assertEqual(['""'], result)


# ---------------------------------------------------------------------------
# _purge_obsolete_po_entries
# ---------------------------------------------------------------------------


class PurgeObsoleteTests(unittest.TestCase):
    """Verify _purge_obsolete_po_entries removes obsolete blocks."""

    def test_removes_obsolete_entries(self) -> None:
        text = (
            '# Header\n\nmsgid ""\nmsgstr "Language: uk\\n"\n\n'
            'msgid "Current"\nmsgstr "Поточний"\n\n'
            '#~ msgid "Old"\n#~ msgstr "Старий"\n'
        )
        updated, removed = build_i18n._purge_obsolete_po_entries(text)
        self.assertEqual(1, removed)
        self.assertIn('msgid "Current"', updated)
        self.assertNotIn("Old", updated)

    def test_no_obsolete(self) -> None:
        text = 'msgid ""\nmsgstr ""\n\nmsgid "Key"\nmsgstr "Val"\n'
        updated, removed = build_i18n._purge_obsolete_po_entries(text)
        self.assertEqual(0, removed)
        self.assertIn('msgid "Key"', updated)


# ---------------------------------------------------------------------------
# _manifest_values
# ---------------------------------------------------------------------------


class ManifestValuesTests(unittest.TestCase):
    """Verify _manifest_values reads triple-quoted fields."""

    def test_reads_summary_and_description(self) -> None:
        values = build_i18n._manifest_values()
        self.assertIn("summary", values)
        self.assertIn("description", values)
        self.assertTrue(values["summary"])
        self.assertTrue(values["description"])


# ---------------------------------------------------------------------------
# _message_preview
# ---------------------------------------------------------------------------


class MessagePreviewTests(unittest.TestCase):
    """Verify _message_preview truncates long messages."""

    def test_short_message(self) -> None:
        result = build_i18n._message_preview("Hello")
        self.assertEqual("Hello", result)

    def test_long_message_truncated(self) -> None:
        result = build_i18n._message_preview("A" * 200, limit=50)
        self.assertEqual(50, len(result))
        self.assertTrue(result.endswith("..."))

    def test_newline_replaced(self) -> None:
        result = build_i18n._message_preview("line1\nline2")
        self.assertIn("\\n", result)
        self.assertNotIn("\n", result)


# ---------------------------------------------------------------------------
# _get_interpolations and _check_format_interpolations
# ---------------------------------------------------------------------------


class FormatInterpolationTests(unittest.TestCase):
    """Verify format string and interpolation validation."""

    def test_unnamed_percent_matching(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Found %d items in %s.",
            "Найдено %d элементов в %s.",
        )
        self.assertEqual([], alerts)

    def test_unnamed_percent_count_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Found %d items in %s.",
            "Найдено %d элементов.",
        )
        self.assertTrue(any("unnamed percent interpolations differ" in a for a in alerts))

    def test_unnamed_percent_type_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Count: %d",
            "Count: %s",
        )
        self.assertTrue(any("unnamed percent interpolations differ" in a for a in alerts))

    def test_unnamed_percent_escaped_percent(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Completed 100%% of %d tasks.",
            "100%% hoàn thành trong %d tác vụ.",
        )
        self.assertEqual([], alerts)

    def test_named_percent_matching_any_order(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "%(name)s has %(count)d items",
            "%(count)d items for %(name)s",
        )
        self.assertEqual([], alerts)

    def test_named_percent_key_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Hello %(name)s",
            "Bonjour %(nom)s",
        )
        self.assertTrue(any("missing named percent interpolation" in a for a in alerts))
        self.assertTrue(any("extra named percent interpolation" in a for a in alerts))

    def test_named_percent_type_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Items: %(count)d",
            "Items: %(count)s",
        )
        self.assertTrue(any("missing named percent interpolation" in a for a in alerts))
        self.assertTrue(any("extra named percent interpolation" in a for a in alerts))

    def test_brace_format_matching(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Hello {name}, score {score}",
            "Xin chào {name}, điểm {score}",
        )
        self.assertEqual([], alerts)

    def test_brace_format_key_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Hello {name}",
            "Xin chào {ten}",
        )
        self.assertTrue(any("missing brace format interpolation" in a for a in alerts))
        self.assertTrue(any("extra brace format interpolation" in a for a in alerts))

    def test_mixed_interpolation_alert(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Hello {name}",
            "Xin chào %s",
        )
        self.assertTrue(len(alerts) >= 2)

    def test_unnumbered_brace_format_count_mismatch(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Value is {} and {}",
            "Giá trị là {}",
        )
        self.assertTrue(any("unnumbered brace format count mismatch" in a for a in alerts))

    def test_unexpected_unnumbered_brace_format(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Hello {name}",
            "Xin chào {name} {}",
        )
        self.assertTrue(any("unexpected presence of unnumbered brace format" in a for a in alerts))

    def test_unnumbered_brace_format_matching(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "{} + {} = {}",
            "{} cộng {} bằng {}",
        )
        self.assertEqual([], alerts)

    def test_formatted_unnumbered_brace_matching(self) -> None:
        alerts = build_i18n._check_format_interpolations(
            "Code {:04d}",
            "Mã {:04d}",
        )
        self.assertEqual([], alerts)


# ---------------------------------------------------------------------------
# Fuzzy PO entries handling
# ---------------------------------------------------------------------------


class FuzzyPoTests(unittest.TestCase):
    """Verify handling of fuzzy (#, fuzzy) translation entries."""

    def _write_po(self, tmpdir: Path, content: str) -> Path:
        po_path = tmpdir / "test.po"
        po_path.write_text(content, encoding="utf-8")
        return po_path

    def test_fuzzy_excluded_when_flag_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            content = (
                'msgid ""\nmsgstr ""\n\n'
                'msgid "Accepted"\nmsgstr "Прийнято"\n\n'
                '#, fuzzy\nmsgid "Draft"\nmsgstr "Чернетка"\n'
            )
            po_path = self._write_po(Path(td), content)
            catalog = build_i18n._parse_po(po_path, include_fuzzy=False)
            self.assertIn("Accepted", catalog)
            self.assertNotIn("Draft", catalog)

    def test_fuzzy_included_when_flag_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            content = 'msgid ""\nmsgstr ""\n\n#, fuzzy\nmsgid "Draft"\nmsgstr "Чернетка"\n'
            po_path = self._write_po(Path(td), content)
            catalog = build_i18n._parse_po(po_path, include_fuzzy=True)
            self.assertIn("Draft", catalog)
            self.assertEqual("Чернетка", catalog["Draft"])

    def test_po_fuzzy_msgids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            content = (
                'msgid ""\nmsgstr ""\n\n'
                'msgid "Normal"\nmsgstr "Звичайний"\n\n'
                '#, fuzzy, python-format\nmsgid "Fuzzy %s"\nmsgstr "Нечіткий %s"\n'
            )
            po_path = self._write_po(Path(td), content)
            fuzzy_ids = build_i18n._po_fuzzy_msgids(po_path)
            self.assertEqual({"Fuzzy %s"}, fuzzy_ids)


# ---------------------------------------------------------------------------
# RTL and documentation compilation
# ---------------------------------------------------------------------------


class DocumentationAndRtlTests(unittest.TestCase):
    """Verify RTL language detection and PO-to-HTML documentation compilation."""

    def test_is_rtl_language(self) -> None:
        for code in (
            "ar",
            "fa",
            "he",
            "ur",
            "ar_SA",
            "ar-SA",
            "  fa-IR  ",
            "fa_IR",
            "he_IL",
            "he-IL",
            "ckb",
            "ckb-IQ",
            "ug",
            "yi",
        ):
            self.assertTrue(build_i18n._is_rtl_language(code), f"Expected RTL for {code}")
        for code in ("en", "vi", "fr", "de", "ru", "uk", "zh_CN", "zh_TW", "ja"):
            self.assertFalse(build_i18n._is_rtl_language(code), f"Expected LTR for {code}")

    def test_check_doc_language_rtl_check_only_vs_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "ar"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "مرحبا"\n',
                encoding="utf-8",
            )
            # Stale html without dir="rtl"
            html_file = doc_dir / "readme.html"
            html_file.write_text(
                "<html><head><title>Old</title></head><body><p>Old</p></body></html>", encoding="utf-8"
            )
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Hello</p>", encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                # When check_only=True, reports error about stale HTML
                errors_check = build_i18n._check_doc_language("ar", check_only=True)
                self.assertTrue(any('missing dir="rtl"' in e for e in errors_check))

                # When check_only=False (build mode), stale HTML is ignored because po_file exists and will overwrite it
                errors_build = build_i18n._check_doc_language("ar", check_only=False)
                self.assertFalse(any('missing dir="rtl"' in e for e in errors_build))

    def test_build_doc_from_po_handles_malformed_po_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Hello</p>", encoding="utf-8")
            bad_po = root / "bad.po"
            bad_po.write_text('msgid "unterminated\n', encoding="utf-8")
            out_html = root / "out.html"
            with (
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                success, msg = build_i18n._build_doc_from_po("vi", bad_po, out_html)
                self.assertFalse(success)
                self.assertIn("could not parse", msg)

    def test_build_doc_for_language_creates_html_with_rtl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_root = Path(td)
            en_doc = doc_root / "en"
            en_doc.mkdir(parents=True)
            (en_doc / "readme.html").write_text(
                '<!DOCTYPE html><html lang="en"><head><title>Guide</title></head><body><p>Content</p></body></html>',
                encoding="utf-8",
            )
            ar_doc = doc_root / "ar"
            ar_doc.mkdir(parents=True)
            (ar_doc / "readme.po").write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Guide"\nmsgstr "دليل المستخدم"\n\n'
                'msgid "Content"\nmsgstr "محتوى التجربة."\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(build_i18n, "DOC_DIR", doc_root),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_doc / "readme.html"),
                mock.patch.object(build_i18n, "ADDON_DIR", doc_root),
            ):
                success, msg = build_i18n._build_doc_for_language("ar")
                self.assertTrue(success)
                self.assertIn("Generated", msg)
                html_file = ar_doc / "readme.html"
                self.assertTrue(html_file.is_file())
                content = html_file.read_text(encoding="utf-8")
                self.assertIn('dir="rtl"', content)
                self.assertIn('lang="ar"', content)
                self.assertIn("<title>دليل المستخدم</title>", content)

    def test_build_doc_for_language_cleans_stale_mo_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_root = Path(td)
            en_doc = doc_root / "en"
            en_doc.mkdir(parents=True)
            (en_doc / "readme.html").write_text(
                '<!DOCTYPE html><html lang="en"><head><title>Title</title></head><body><p>Text</p></body></html>',
                encoding="utf-8",
            )
            vi_doc = doc_root / "vi"
            vi_doc.mkdir(parents=True)
            stray_mo = vi_doc / "readme.mo"
            stray_mo.write_bytes(b"\xde\x12\x04\x95")
            (vi_doc / "readme.po").write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Title"\nmsgstr "Tiêu đề"\n',
                encoding="utf-8",
            )
            with (
                mock.patch.object(build_i18n, "DOC_DIR", doc_root),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_doc / "readme.html"),
                mock.patch.object(build_i18n, "ADDON_DIR", doc_root),
            ):
                success, _ = build_i18n._build_doc_for_language("vi")
                self.assertTrue(success)
                self.assertFalse(stray_mo.exists())

    def test_doc_html_extractor_and_rebuilder_roundtrip(self) -> None:
        sample_html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '\t<meta charset="UTF-8">\n'
            "\t<title>Add-on Title</title>\n"
            "</head>\n"
            "<body>\n"
            "\t<h1>Main Heading</h1>\n"
            '\t<p>Welcome to <strong>Google TTS</strong>. Visit <a href="https://example.com">link</a>.</p>\n'
            "\t<ul>\n"
            "\t\t<li>Parent item\n"
            "\t\t\t<ul>\n"
            "\t\t\t\t<li>Nested sub-item</li>\n"
            "\t\t\t</ul>\n"
            "\t\t</li>\n"
            "\t</ul>\n"
            "</body>\n"
            "</html>\n"
        )
        extractor = build_i18n._DocHtmlExtractor()
        extractor.feed(sample_html)
        segments = [seg for _, seg in extractor.segments]
        self.assertIn("Add-on Title", segments)
        self.assertIn("Main Heading", segments)
        self.assertIn('Welcome to <strong>Google TTS</strong>. Visit <a href="https://example.com">link</a>.', segments)
        self.assertIn("Parent item", segments)
        self.assertIn("Nested sub-item", segments)

        catalog = {
            "Add-on Title": "Tiêu đề Add-on",
            "Main Heading": "Tiêu đề chính",
            'Welcome to <strong>Google TTS</strong>. Visit <a href="https://example.com">link</a>.': (
                'Chào mừng đến với <strong>Google TTS</strong>. Ghé thăm <a href="https://example.com">liên kết</a>.'
            ),
            "Parent item": "Mục cha",
            "Nested sub-item": "Mục con",
        }
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="vi", is_rtl=False)
        rebuilder.feed(sample_html)
        rebuilt = "".join(rebuilder.output)

        self.assertIn('<html lang="vi">', rebuilt)
        self.assertIn("<title>Tiêu đề Add-on</title>", rebuilt)
        self.assertIn("<h1>Tiêu đề chính</h1>", rebuilt)
        self.assertIn("Chào mừng đến với", rebuilt)
        self.assertIn("Mục cha", rebuilt)
        self.assertIn("Mục con", rebuilt)

    def test_doc_html_rebuilder_applies_rtl_direction(self) -> None:
        sample_html = '<!DOCTYPE html><html lang="en"><head><title>Title</title></head><body><p>Text</p></body></html>'
        catalog = {"Title": "عنوان", "Text": "نص"}
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="ar", is_rtl=True)
        rebuilder.feed(sample_html)
        rebuilt = "".join(rebuilder.output)
        self.assertIn('lang="ar"', rebuilt)
        self.assertIn('dir="rtl"', rebuilt)
        self.assertIn("عنوان", rebuilt)

    def test_write_doc_pot_generates_valid_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_en = root / "doc" / "en" / "readme.html"
            doc_en.parent.mkdir(parents=True)
            doc_en.write_text(
                '<!DOCTYPE html><html lang="en"><head><title>Guide</title></head>'
                "<body><h1>Header</h1><p>Doc content.</p></body></html>",
                encoding="utf-8",
            )
            pot_file = root / "doc" / "readme.pot"
            with (
                mock.patch.object(build_i18n, "DOC_EN_PATH", doc_en),
                mock.patch.object(build_i18n, "DOC_POT_PATH", pot_file),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                result_path = build_i18n._write_doc_pot(pot_file)
                self.assertTrue(result_path.is_file())
                content = result_path.read_text(encoding="utf-8")
                self.assertIn('msgid "Guide"', content)
                self.assertIn('msgid "Header"', content)
                self.assertIn('msgid "Doc content."', content)

    def test_build_doc_from_po_compiles_html(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_en = root / "doc" / "en" / "readme.html"
            doc_en.parent.mkdir(parents=True)
            doc_en.write_text(
                '<!DOCTYPE html><html lang="en"><head><title>Hello</title></head><body><p>Content</p></body></html>',
                encoding="utf-8",
            )
            po_file = root / "doc" / "vi" / "readme.po"
            po_file.parent.mkdir(parents=True)
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "Xin chào"\n\nmsgid "Content"\nmsgstr "Nội dung"\n',
                encoding="utf-8",
            )
            out_html = root / "doc" / "vi" / "readme.html"
            with (
                mock.patch.object(build_i18n, "DOC_EN_PATH", doc_en),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                success, msg = build_i18n._build_doc_from_po("zh_CN", po_file, out_html)
                self.assertTrue(success)
                self.assertTrue(out_html.is_file())
                self.assertTrue(po_file.exists())
                content = out_html.read_text(encoding="utf-8")
                self.assertIn("<title>Xin chào</title>", content)
                self.assertIn("<p>Nội dung</p>", content)
                self.assertIn('lang="zh-CN"', content)

    def test_update_doc_po_from_template_prepopulates_from_existing_html(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_en = root / "doc" / "en" / "readme.html"
            doc_en.parent.mkdir(parents=True)
            doc_en.write_text(
                '<!DOCTYPE html><html lang="en"><head><title>Greeting</title></head>'
                "<body><p>Sentence one.</p></body></html>",
                encoding="utf-8",
            )
            doc_pot = root / "doc" / "readme.pot"
            doc_vi_html = root / "doc" / "vi" / "readme.html"
            doc_vi_html.parent.mkdir(parents=True)
            doc_vi_html.write_text(
                '<!DOCTYPE html><html lang="vi"><head><title>Chào hỏi</title></head>'
                "<body><p>Câu một.</p></body></html>",
                encoding="utf-8",
            )
            with (
                mock.patch.object(build_i18n, "DOC_EN_PATH", doc_en),
                mock.patch.object(build_i18n, "DOC_POT_PATH", doc_pot),
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                build_i18n._write_doc_pot(doc_pot)
                preserved, added, removed = build_i18n._update_doc_po_from_template("vi", msgmerge_path=None)
                po_path = root / "doc" / "vi" / "readme.po"
                self.assertTrue(po_path.is_file())
                catalog = build_i18n._parse_po(po_path)
                self.assertEqual("Chào hỏi", catalog.get("Greeting"))
                self.assertEqual("Câu một.", catalog.get("Sentence one."))
                self.assertEqual(2, preserved)
                self.assertEqual(0, added)
                self.assertEqual(0, removed)


# ---------------------------------------------------------------------------
# _check_html_tag_interpolations
# ---------------------------------------------------------------------------


class CheckHtmlTagInterpolationsTests(unittest.TestCase):
    """Verify HTML inline tag validation for doc translations."""

    def test_matching_tags_pass(self) -> None:
        msgid = 'Press <kbd>Ctrl+S</kbd> to <a href="https://example.com">save</a>.'
        msgstr = 'Nhấn <kbd>Ctrl+S</kbd> để <a href="https://example.com">lưu</a>.'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertEqual([], errors)

    def test_unbalanced_tag_detected(self) -> None:
        msgid = "<strong>Title</strong>"
        msgstr = "<strong>Tiêu đề"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unbalanced <strong>" in e for e in errors))

    def test_missing_tag_detected(self) -> None:
        msgid = "Press <kbd>NVDA+Ctrl+S</kbd>"
        msgstr = "Nhấn NVDA+Ctrl+S"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("missing HTML tag: ['kbd']" in e for e in errors))

    def test_tag_count_mismatch_detected(self) -> None:
        msgid = "Use <code>foo</code> and <code>bar</code>"
        msgstr = "Dùng <code>foo</code> và bar"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("<code> tag count mismatch" in e for e in errors))

    def test_href_mismatch_detected(self) -> None:
        msgid = '<a href="https://example.com/a">link</a>'
        msgstr = '<a href="https://example.com/b">liên kết</a>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("<a> href mismatch" in e for e in errors))

    def test_empty_msgstr_returns_no_errors(self) -> None:
        msgid = "<strong>Text</strong>"
        self.assertEqual([], build_i18n._check_html_tag_interpolations(msgid, ""))

    def test_disallowed_dangerous_tag_detected(self) -> None:
        msgid = "Run the application"
        msgstr = "Chạy ứng dụng <script>alert(1)</script>"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("disallowed HTML tag: <script>" in e for e in errors))

    def test_disallowed_inline_event_handler_detected(self) -> None:
        msgid = "Click <b>here</b>"
        msgstr = 'Nhấn <b onclick="evil()">đây</b>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("disallowed inline event handler in HTML tag" in e for e in errors))

    def test_mismatched_nesting_order_detected(self) -> None:
        msgid = "<kbd><code>Ctrl+C</code></kbd>"
        msgstr = "<kbd><code>Ctrl+C</kbd></code>"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("mismatched closing tag: expected </code>, got </kbd>" in e for e in errors))

    def test_closing_tag_without_opening_tag_detected(self) -> None:
        msgid = "Plain text"
        msgstr = "Văn bản</kbd>"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("closing </kbd> tag without matching opening tag" in e for e in errors))

    def test_unexpected_closing_for_void_element_detected(self) -> None:
        msgid = "Line 1<br>Line 2"
        msgstr = "Dòng 1</br>Dòng 2"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unexpected closing tag for void element </br>" in e for e in errors))

    def test_unexpected_extra_tag_detected(self) -> None:
        msgid = "Plain message without tags"
        msgstr = "Thông điệp <b>đậm</b>"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unexpected HTML tag: ['b']" in e for e in errors))

    def test_unsafe_href_scheme_detected(self) -> None:
        msgid = '<a href="javascript:alert(1)">Click</a>'
        msgstr = '<a href="javascript:alert(1)">Nhấn</a>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unsafe <a> href scheme" in e for e in errors))

    def test_closing_tag_with_whitespace_passes(self) -> None:
        msgid = "<code>sample</code>"
        msgstr = "<code>mẫu</code >"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertEqual([], errors)

    def test_void_tags_with_slash_pass(self) -> None:
        msgid = "Line 1<br>Line 2"
        msgstr = "Dòng 1<br/>Dòng 2"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertEqual([], errors)

    def test_format_html_attrs(self) -> None:
        attrs = [("href", "https://example.com?a=1&b=2"), ("title", 'say "hello"'), ("disabled", None)]
        formatted = build_i18n._format_html_attrs(attrs)
        self.assertEqual(' href="https://example.com?a=1&amp;b=2" title="say &quot;hello&quot;" disabled', formatted)

    def test_unsafe_href_with_html_entity_detected(self) -> None:
        msgid = '<a href="https://example.com">Click</a>'
        msgstr = '<a href="jav&#x61;script:alert(1)">Nhấn</a>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unsafe <a> href scheme" in e for e in errors))

        msgstr2 = '<a href="javascript&colon;alert(1)">Nhấn</a>'
        errors2 = build_i18n._check_html_tag_interpolations(msgid, msgstr2)
        self.assertTrue(any("unsafe <a> href scheme" in e for e in errors2))

    def test_href_with_spaces_and_unquoted(self) -> None:
        msgid = '<a href="https://example.com">Click</a>'
        msgstr1 = '<a href = "https://example.com">Nhấn</a>'
        self.assertEqual([], build_i18n._check_html_tag_interpolations(msgid, msgstr1))

        msgstr2 = "<a href=https://example.com>Nhấn</a>"
        self.assertEqual([], build_i18n._check_html_tag_interpolations(msgid, msgstr2))

    def test_unbalanced_comments_detected(self) -> None:
        msgid = "Plain text"
        msgstr = "Văn bản <!-- bình luận không đóng"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unbalanced HTML comment" in e for e in errors))

    def test_entity_encoded_event_handler_detected(self) -> None:
        msgid = "<b>Bold</b>"
        msgstr = '<b on&#99;lick="alert(1)">Đậm</b>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("disallowed inline event handler" in e for e in errors))

    def test_extract_po_from_doc_html_preserves_translations_with_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            en_html = root / "en.html"
            vi_html = root / "vi.html"
            out_po = root / "readme.po"

            # en has 3 paragraphs; vi was translated when en had only p1 and p3 (p2 is newly added to en)
            en_html.write_text(
                "<!DOCTYPE html><html><body><p>First paragraph with <kbd>Ctrl+1</kbd>.</p><p>Added middle paragraph.</p><p>Third paragraph with <kbd>Ctrl+3</kbd>.</p></body></html>",
                encoding="utf-8",
            )
            vi_html.write_text(
                "<!DOCTYPE html><html><body><p>Đoạn thứ nhất với <kbd>Ctrl+1</kbd>.</p><p>Đoạn thứ ba với <kbd>Ctrl+3</kbd>.</p></body></html>",
                encoding="utf-8",
            )
            with mock.patch.object(build_i18n, "ADDON_DIR", root):
                success = build_i18n._extract_po_from_doc_html(en_html, vi_html, "vi", out_po)
            self.assertTrue(success)
            catalog = build_i18n._parse_po(out_po, include_untranslated=True)
            self.assertEqual(
                "Đoạn thứ nhất với <kbd>Ctrl+1</kbd>.", catalog.get("First paragraph with <kbd>Ctrl+1</kbd>.")
            )
            self.assertEqual(
                "Đoạn thứ ba với <kbd>Ctrl+3</kbd>.", catalog.get("Third paragraph with <kbd>Ctrl+3</kbd>.")
            )
            self.assertEqual("", catalog.get("Added middle paragraph."))

    def test_write_translated_manifest_falls_back_on_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_source = root / "manifest.ini"
            manifest_source.write_text(
                'summary = "English summary"\ndescription = """English desc"""\n', encoding="utf-8"
            )
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest_source):
                # Pass catalog where summary is empty string
                build_i18n._write_translated_manifest(lang_dir, {"English summary": "", "English desc": "Mô tả"})
                result = (lang_dir / "manifest.ini").read_text(encoding="utf-8")
                self.assertIn('summary = "English summary"', result)
                self.assertIn('description = """Mô tả"""', result)

    def test_inline_style_attribute_detected(self) -> None:
        msgid = "<b>Bold</b>"
        msgstr = '<b style="color:red">Đậm</b>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("disallowed inline style attribute" in e for e in errors))

    def test_entity_encoded_style_attribute_detected(self) -> None:
        msgid = "<b>Bold</b>"
        msgstr = '<b st&#121;le="display:none">Đậm</b>'
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("disallowed inline style attribute" in e for e in errors))

    def test_slash_delimited_event_handler_and_href(self) -> None:
        msgid = '<a href="https://example.com">Link</a>'
        msgstr_bad = "<a/onclick=alert(1)>Link</a>"
        errors = build_i18n._check_html_tag_interpolations(msgid, msgstr_bad)
        self.assertTrue(any("disallowed inline event handler" in e for e in errors))

        msgstr_good = '<a/href="https://example.com">Link</a>'
        self.assertEqual([], build_i18n._check_html_tag_interpolations(msgid, msgstr_good))

    def test_new_dangerous_media_tags_detected(self) -> None:
        msgid = "<p>Text</p>"
        for tag in ("audio", "video", "source"):
            msgstr = f"<{tag}>Text</{tag}>"
            errors = build_i18n._check_html_tag_interpolations(msgid, msgstr)
            self.assertTrue(any(f"disallowed HTML tag: <{tag}>" in e for e in errors))

    def test_doc_target_languages_validation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "doc" / "vi").mkdir(parents=True)
            (root / "doc" / "en").mkdir(parents=True)
            (root / "locale" / "vi").mkdir(parents=True)
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "LOCALE_DIR", root / "locale"),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                # Empty code error
                targets, errs = build_i18n._doc_target_languages(["", "vi"])
                self.assertIn("empty language code.", errs)
                self.assertEqual(["vi"], targets)

                # "en" without include_en
                targets, errs = build_i18n._doc_target_languages(["en"])
                self.assertTrue(any("English is the documentation source" in e for e in errs))

                # Non-existent language without allow_create
                targets, errs = build_i18n._doc_target_languages(["fr"], allow_create=False)
                self.assertTrue(any("documentation folder is missing" in e for e in errs))

                # Non-existent language with allow_create
                targets, errs = build_i18n._doc_target_languages(["fr"], allow_create=True)
                self.assertEqual([], errs)
                self.assertEqual(["fr"], targets)
                self.assertTrue((root / "doc" / "fr").is_dir())

    def test_check_catalog_detects_html_issues_in_ui(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            cat = {
                "Hello <kbd>Ctrl+C</kbd>": "Xin chào <kbd>Ctrl+C",  # unclosed kbd
            }
            errors = build_i18n._check_catalog(
                lang_dir,
                cat,
                checks={build_i18n.CHECK_PLACEHOLDERS},
            )
            self.assertTrue(any("HTML tag issue" in e for e in errors))

    def test_check_doc_language_detects_placeholders_and_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Version %s"\nmsgstr "Phiên bản"\n',
                encoding="utf-8",
            )
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Version %s</p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                errors = build_i18n._check_doc_language("vi")
                self.assertTrue(any("placeholder mismatch in readme.po" in e for e in errors))

            # Missing en source file
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", root / "nonexistent" / "readme.html"),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                errors = build_i18n._check_doc_language("vi")
                self.assertTrue(any("English source document missing" in e for e in errors))

    def test_check_catalog_detects_injected_html_tags_in_plain_msgid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            cat = {
                "Plain English without tags": "Bản dịch có <script>alert(1)</script>",
            }
            errors = build_i18n._check_catalog(
                lang_dir,
                cat,
                checks={build_i18n.CHECK_PLACEHOLDERS},
            )
            self.assertTrue(any("HTML tag issue" in e for e in errors))
            self.assertTrue(any("disallowed HTML tag: <script>" in e for e in errors))

    def test_language_dirs_deduplicates_repeated_locales(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            locale_root = root / "locale"
            (locale_root / "vi").mkdir(parents=True)
            with mock.patch.object(build_i18n, "LOCALE_DIR", locale_root):
                dirs, errs = build_i18n._language_dirs(["vi", "vi", "VI"])
                self.assertEqual(len(dirs), 1)
                self.assertEqual(dirs[0].name, "vi")
                self.assertEqual(errs, [])

    def test_doc_html_extractor_and_rebuilder_void_elements(self) -> None:
        html_input = "<p>Line 1<br/>Line 2<br>Line 3<img src='test.png'/>End</p>"
        extractor = build_i18n._DocHtmlExtractor()
        extractor.feed(html_input)
        self.assertEqual(len(extractor.segments), 1)
        segment = extractor.segments[0][1]
        self.assertNotIn("</br>", segment)
        self.assertNotIn("</img>", segment)
        tag_errors = build_i18n._check_html_tag_interpolations(segment, segment)
        self.assertEqual(tag_errors, [])

        catalog = {segment: "Dòng 1<br/>Dòng 2<br>Dòng 3<img src='test.png'/>Hết"}
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="vi", is_rtl=False)
        rebuilder.feed(html_input)
        output = "".join(rebuilder.output)
        self.assertNotIn("</br>", output)
        self.assertNotIn("</img>", output)
        self.assertIn("<br>", output)
        self.assertIn("Hết", output)

    def test_check_doc_language_separates_docs_and_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            # Valid translated string but with bad HTML tag
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "Xin chào <script>alert(1)</script>"\n',
                encoding="utf-8",
            )
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Hello</p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                # When checking only CHECK_DOCS (translation completeness), placeholder/tag check is not run
                errors_docs_only = build_i18n._check_doc_language("vi", checks={build_i18n.CHECK_DOCS})
                self.assertEqual(errors_docs_only, [])

                # When checking CHECK_PLACEHOLDERS, the bad HTML tag is detected
                errors_placeholders = build_i18n._check_doc_language("vi", checks={build_i18n.CHECK_PLACEHOLDERS})
                self.assertTrue(any("HTML tag issue" in e for e in errors_placeholders))

    def test_check_catalog_formats_long_msgid_with_preview(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            long_msgid = "Start of message " + ("very long text " * 20) + "{code} end"
            bad_msgstr = "Bắt đầu thông điệp " + ("rất dài " * 20) + "không có biến"
            cat = {long_msgid: bad_msgstr}
            errors = build_i18n._check_catalog(
                lang_dir,
                cat,
                checks={build_i18n.CHECK_PLACEHOLDERS},
            )
            self.assertTrue(any("placeholder mismatch for 'Start of message" in e for e in errors))
            self.assertTrue(any("..." in e for e in errors))

    def test_describe_checks_empty_returns_none(self) -> None:
        self.assertEqual(build_i18n._describe_checks(set()), "none")
        self.assertEqual(build_i18n._describe_doc_checks(set()), "none")

    def test_obsolete_and_context_do_not_leak_fuzzy_in_parse_po(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_file = Path(td) / "test.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\n'
                'msgid "prev"\nmsgstr "trước"\n\n'
                "#, fuzzy\n"
                '#~ msgid "obsolete"\n'
                '#~ msgstr "cũ"\n\n'
                'msgctxt "button"\n'
                '"label"\n'
                'msgid "valid"\n'
                'msgstr "hợp lệ"\n',
                encoding="utf-8",
            )
            # Fuzzy should NOT include 'valid' because fuzzy belonged to the obsolete entry
            non_fuzzy_catalog = build_i18n._parse_po(po_file, include_fuzzy=False)
            self.assertEqual(non_fuzzy_catalog.get("prev"), "trước")
            self.assertEqual(non_fuzzy_catalog.get("valid"), "hợp lệ")

            fuzzy_ids = build_i18n._po_fuzzy_msgids(po_file)
            self.assertNotIn("valid", fuzzy_ids)

    def test_po_escape_and_quoted_lines_with_crlf_and_cr(self) -> None:
        escaped = build_i18n._po_escape('line1\r\nline2\tline3\\"')
        self.assertIn("\\r", escaped)
        self.assertIn("\\n", escaped)
        self.assertIn("\\t", escaped)

        quoted = build_i18n._po_quoted_lines("first\r\nsecond\r\n")
        self.assertEqual(quoted, ['""', '"first\\n"', '"second\\n"'])

        empty_quoted = build_i18n._po_quoted_lines("")
        self.assertEqual(empty_quoted, ['""'])

    def test_quote_manifest_value_and_write_translated_manifest_edge_cases(self) -> None:
        # Quote manifest value strips newlines
        quoted = build_i18n._quote_manifest_value("line1\r\nline2")
        self.assertNotIn("\n", quoted)
        self.assertNotIn("\r", quoted)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            manifest_file = root / "manifest.ini"
            manifest_file.write_text(
                'name = googleTts\nsummary = "Add-on Summary"\ndescription = """Add-on Description"""\n',
                encoding="utf-8",
            )
            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest_file):
                # Translation description ends with quote "
                cat = {
                    "Add-on Summary": 'Mô tả "ngắn"',
                    "Add-on Description": 'Chi tiết với "ngoặc kép"',
                }
                build_i18n._write_translated_manifest(lang_dir, cat)
                written = (lang_dir / "manifest.ini").read_text(encoding="utf-8")
                self.assertIn('summary = "Mô tả \\"ngắn\\""', written)
                # Must not end with 4 quotes """"
                self.assertNotIn('""""', written)

    def test_language_dirs_rejects_en_and_filters_addon_languages(self) -> None:
        dirs, errors = build_i18n._language_dirs(["en"])
        self.assertEqual(dirs, [])
        self.assertTrue(any("en: English is the source language" in e for e in errors))

        with tempfile.TemporaryDirectory() as td:
            locale_dir = Path(td) / "locale"
            (locale_dir / "en").mkdir(parents=True)
            (locale_dir / "vi").mkdir(parents=True)
            with mock.patch.object(build_i18n, "LOCALE_DIR", locale_dir):
                all_langs = build_i18n._addon_languages()
                self.assertNotIn("en", all_langs)
                self.assertIn("vi", all_langs)

                auto_dirs, auto_errors = build_i18n._language_dirs(None)
                self.assertEqual([d.name for d in auto_dirs], ["vi"])
                self.assertEqual(auto_errors, [])

    def test_supported_nvda_languages_always_includes_en(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            loc = Path(td) / "locale"
            (loc / "vi").mkdir(parents=True)
            supported, found = build_i18n._supported_nvda_languages_from_dirs([loc])
            self.assertIsNotNone(supported)
            assert supported is not None
            self.assertIn("en", supported)
            self.assertIn("vi", supported)

    def test_doc_html_nested_translatable_tags(self) -> None:
        html = "<blockquote>\n  <p>First paragraph in quote</p>\n  <p>Second paragraph in quote</p>\n</blockquote>\n"
        extractor = build_i18n._DocHtmlExtractor()
        extractor.feed(html)
        segs = [s for _, s in extractor.segments]
        self.assertEqual(segs, ["First paragraph in quote", "Second paragraph in quote"])

        catalog = {
            "First paragraph in quote": "Đoạn 1 trong trích dẫn",
            "Second paragraph in quote": "Đoạn 2 trong trích dẫn",
        }
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="vi", is_rtl=False)
        rebuilder.feed(html)
        rebuilt = "".join(rebuilder.output)
        self.assertIn("<p>Đoạn 1 trong trích dẫn</p>", rebuilt)
        self.assertIn("<p>Đoạn 2 trong trích dẫn</p>", rebuilt)

    def test_doc_segment_signature_and_extract_missing_files(self) -> None:
        sig = build_i18n._doc_segment_signature('<a href="url">link</a> with 123 numbers')
        self.assertEqual(sig, (("a",), ("123",)))

        # Missing files returns False
        success = build_i18n._extract_po_from_doc_html(
            Path("nonexistent1.html"),
            Path("nonexistent2.html"),
            "vi",
            Path("out.po"),
        )
        self.assertFalse(success)

    def test_get_interpolations_escaped_percent_and_braces(self) -> None:
        # %%s should not be counted as %s
        unnamed, named, braces, unnumbered = build_i18n._get_interpolations("100%% sure and %d count")
        self.assertEqual(unnamed, ["%d"])
        self.assertEqual(named, set())

        # %%(name)s should not be counted as named percent
        unnamed2, named2, _, _ = build_i18n._get_interpolations("100%%(name)s vs %(real)s")
        self.assertEqual(named2, {"%(real)s"})

        # {{escaped}} should not be counted as format field
        _, _, braces3, unnumbered3 = build_i18n._get_interpolations("{{literal}} and {name} and {}")
        self.assertEqual(braces3, {"{name}"})
        self.assertEqual(unnumbered3, 1)

    def test_purge_obsolete_po_entries_empty_and_all_obsolete(self) -> None:
        cleaned, count = build_i18n._purge_obsolete_po_entries("")
        self.assertEqual(cleaned, "")
        self.assertEqual(count, 0)

        all_obs = '#~ msgid "foo"\n#~ msgstr "bar"\n'
        cleaned2, count2 = build_i18n._purge_obsolete_po_entries(all_obs)
        self.assertEqual(cleaned2, "")
        self.assertEqual(count2, 1)

    def test_re_named_percent_does_not_eat_closing_parentheses(self) -> None:
        matches = build_i18n.RE_NAMED_PERCENT.findall("%(first)s and some text)s")
        self.assertEqual(matches, ["%(first)s"])

        flags_match = build_i18n.RE_NAMED_PERCENT.findall("%(count)02d and %(name)-10s")
        self.assertEqual(flags_match, ["%(count)02d", "%(name)-10s"])

    def test_write_pot_custom_pot_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            custom_path = Path(td) / "custom.pot"
            with mock.patch.object(build_i18n, "_manifest_version", return_value="1.0"):
                result = build_i18n._write_pot({"Test string": ["file.py:10"]}, pot_path=custom_path)
            self.assertEqual(result, custom_path)
            self.assertTrue(custom_path.is_file())
            content = custom_path.read_text(encoding="utf-8")
            self.assertIn('msgid "Test string"', content)

    def test_update_po_and_doc_po_reject_en_source_language(self) -> None:
        with self.assertRaisesRegex(ValueError, "English is the source language"):
            build_i18n._update_po_from_template(Path("some/path/en"), None, {})

        with self.assertRaisesRegex(ValueError, "English is the documentation source"):
            build_i18n._update_doc_po_from_template("en", None)

        built, msg = build_i18n._build_doc_from_po("en", Path("fake.po"), Path("fake.html"))
        self.assertFalse(built)
        self.assertIn("cannot build English documentation from PO", msg)

        built_for_lang, msg_for_lang = build_i18n._build_doc_for_language("en")
        self.assertFalse(built_for_lang)
        self.assertIn("English source document", msg_for_lang)

        success = build_i18n._extract_po_from_doc_html(Path("en.html"), Path("en.html"), "en", Path("out.po"))
        self.assertFalse(success)

    def test_check_doc_language_flags_stale_mo_and_en_readme_po(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_en = root / "doc" / "en"
            doc_en.mkdir(parents=True)
            (doc_en / "readme.html").write_text("<html>English</html>", encoding="utf-8")
            (doc_en / "readme.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
            (doc_en / "stale.mo").write_bytes(b"\x00")

            doc_vi = root / "doc" / "vi"
            doc_vi.mkdir(parents=True)
            (doc_vi / "readme.html").write_text("<html>Tieng Viet</html>", encoding="utf-8")
            (doc_vi / "stale.mo").write_bytes(b"\x00")

            locale_vi = root / "locale" / "vi"
            (locale_vi / "LC_MESSAGES").mkdir(parents=True)
            (locale_vi / "LC_MESSAGES" / "nvda.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", doc_en / "readme.html"),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                # en check should flag unexpected readme.po and unexpected .mo
                en_errors = build_i18n._check_doc_language("en", supported_languages={"en"})
                self.assertTrue(any("unexpected readme.po" in err for err in en_errors))
                self.assertTrue(any("unexpected .mo file" in err for err in en_errors))

                # vi check should flag unexpected .mo
                vi_doc_errors = build_i18n._check_doc_language("vi", supported_languages={"vi"})
                self.assertTrue(any("unexpected .mo file" in err for err in vi_doc_errors))

                # UI check with CHECK_DOCS should also flag unexpected .mo in doc folder
                vi_ui_errors = build_i18n._check_language_files(
                    locale_vi,
                    supported_languages={"vi"},
                    checks={build_i18n.CHECK_DOCS},
                )
                self.assertTrue(any("unexpected .mo file in documentation folder" in err for err in vi_ui_errors))

    def test_check_language_files_and_catalog_optional_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            (lang_dir / "LC_MESSAGES").mkdir(parents=True)
            (lang_dir / "LC_MESSAGES" / "nvda.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
            (lang_dir / "manifest.ini").write_text("summary = Test\n", encoding="utf-8")

            # _check_language_files with default args
            errors = build_i18n._check_language_files(lang_dir)
            self.assertIsInstance(errors, list)

            # _check_catalog with default args
            cat_errors = build_i18n._check_catalog(lang_dir, {"Hello": "Xin chào"})
            self.assertIsInstance(cat_errors, list)

    def test_manifest_values_single_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.ini"
            manifest.write_text(
                "summary = 'Single Quoted Summary'\n"
                "description = '''Triple single quoted\ndescription.'''\n"
                "changelog = '''Single changelog.'''\n",
                encoding="utf-8",
            )
            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest):
                values = build_i18n._manifest_values()
                self.assertEqual("Single Quoted Summary", values.get("summary"))
                self.assertEqual("Triple single quoted\ndescription.", values.get("description"))
                self.assertEqual("Single changelog.", values.get("changelog"))

    def test_write_translated_manifest_creates_directory_and_normalizes_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest_src = Path(td) / "source_manifest.ini"
            manifest_src.write_text(
                'summary = "Summary"\ndescription = """Desc"""\nchangelog = """Change"""\n',
                encoding="utf-8",
            )
            non_existent_dir = Path(td) / "new_lang"
            catalog = {
                "Summary": "Resumen",
                "Desc": "Línea 1\r\nLínea 2",
                "Change": "Notas\r\nVersión 1.0",
            }
            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest_src):
                build_i18n._write_translated_manifest(non_existent_dir, catalog)
                self.assertTrue(non_existent_dir.is_dir())
                written = (non_existent_dir / "manifest.ini").read_text(encoding="utf-8")
                self.assertIn('summary = "Resumen"', written)
                self.assertNotIn("\r\n", written)
                self.assertIn("Línea 1\nLínea 2", written)

    def test_comma_separated_language_requests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            locale_dir = root / "locale"
            (locale_dir / "es" / "LC_MESSAGES").mkdir(parents=True)
            (locale_dir / "vi" / "LC_MESSAGES").mkdir(parents=True)
            doc_dir = root / "doc"
            (doc_dir / "es").mkdir(parents=True)
            (doc_dir / "vi").mkdir(parents=True)

            with (
                mock.patch.object(build_i18n, "LOCALE_DIR", locale_dir),
                mock.patch.object(build_i18n, "DOC_DIR", doc_dir),
            ):
                dirs, errors = build_i18n._language_dirs(["es,vi"])
                self.assertEqual([], errors)
                self.assertEqual([locale_dir / "es", locale_dir / "vi"], dirs)

                targets, doc_errors = build_i18n._doc_target_languages(["es,vi"])
                self.assertEqual([], doc_errors)
                self.assertEqual(["es", "vi"], targets)

    def test_doc_segment_signature_case_insensitive(self) -> None:
        sig_upper = build_i18n._doc_segment_signature("Press <CODE>Ctrl+F</CODE> in 2026")
        sig_lower = build_i18n._doc_segment_signature("Press <code>Ctrl+F</code> in 2026")
        self.assertEqual(sig_upper, sig_lower)

    def test_update_doc_po_fallback_when_html_extract_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc"
            doc_en = doc_dir / "en"
            doc_en.mkdir(parents=True)
            (doc_en / "readme.html").write_text(
                "<!DOCTYPE html><html><body><p>Hello world</p></body></html>", encoding="utf-8"
            )

            doc_vi = doc_dir / "vi"
            doc_vi.mkdir(parents=True)
            # Create a broken html file
            (doc_vi / "readme.html").write_text("<not-closed>", encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", doc_dir),
                mock.patch.object(build_i18n, "DOC_POT_PATH", doc_dir / "readme.pot"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", doc_en / "readme.html"),
                mock.patch.object(build_i18n, "_extract_po_from_doc_html", return_value=False),
            ):
                preserved, added, removed = build_i18n._update_doc_po_from_template("vi", None)
                self.assertTrue((doc_vi / "readme.po").is_file())
                po_content = (doc_vi / "readme.po").read_text(encoding="utf-8")
                self.assertIn("Language: vi", po_content)
                self.assertIn("Hello world", po_content)

    def test_build_doc_from_po_cannot_overwrite_english_doc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            en_doc = Path(td) / "readme.html"
            en_doc.write_text("<html>English</html>", encoding="utf-8")
            fake_po = Path(td) / "fake.po"
            fake_po.write_text('msgid "test"\nmsgstr "test"\n', encoding="utf-8")
            with mock.patch.object(build_i18n, "DOC_EN_PATH", en_doc):
                success, msg = build_i18n._build_doc_from_po("vi", fake_po, en_doc)
                self.assertFalse(success)
                self.assertIn("cannot overwrite English source document", msg)

    def test_extract_po_from_doc_html_same_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "readme.html"
            doc.write_text("<html>Hello</html>", encoding="utf-8")
            out_po = Path(td) / "out.po"
            success = build_i18n._extract_po_from_doc_html(doc, doc, "vi", out_po)
            self.assertFalse(success)
            self.assertFalse(out_po.exists())

    def test_decode_po_string_error_handling(self) -> None:
        self.assertEqual("test", build_i18n._decode_po_string('"test"'))
        with self.assertRaises(ValueError) as ctx:
            build_i18n._decode_po_string("unquoted")
        self.assertIn("malformed PO string", str(ctx.exception))

    def test_extract_hrefs_with_data_attributes_and_whitespace(self) -> None:
        text = '<a data-href="bad" href="https://example.com/test?a=1&amp;b=2">Link</a>'
        hrefs = build_i18n._extract_hrefs(text)
        self.assertEqual(["https://example.com/test?a=1&amp;b=2"], hrefs)

    def test_check_html_tag_interpolations_entities_and_safe_data_attributes(self) -> None:
        msgid = '<a href="https://example.com?a=1&amp;b=2">Link</a>'
        msgstr = '<a href="https://example.com?a=1&b=2">Liên kết</a>'
        alerts = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertEqual([], alerts)

        # Ensure data-style and data-onclick are not flagged
        safe_str = '<span data-style="bold" data-onclick="noop">Text</span>'
        safe_id = "<span>Text</span>"
        alerts_safe = build_i18n._check_html_tag_interpolations(safe_id, safe_str)
        self.assertEqual([], alerts_safe)

    def test_check_html_tag_interpolations_unsafe_schemes_obfuscated(self) -> None:
        msgid = "<a>Link</a>"
        msgstr = '<a href="jav&#97;script:alert(1)">Link</a>'
        alerts = build_i18n._check_html_tag_interpolations(msgid, msgstr)
        self.assertTrue(any("unsafe <a> href scheme" in a for a in alerts))

    def test_doc_html_extractor_and_rebuilder_close_flushes_data(self) -> None:
        ext = build_i18n._DocHtmlExtractor()
        ext.feed("<p>Unclosed paragraph")
        ext.close()
        self.assertEqual(1, len(ext.segments))
        self.assertEqual("Unclosed paragraph", ext.segments[0][1])

        rebuilder = build_i18n._DocHtmlRebuilder({"Unclosed paragraph": "Đoạn chưa đóng"}, "vi", is_rtl=False)
        rebuilder.feed("<p>Unclosed paragraph")
        rebuilder.close()
        output = "".join(rebuilder.output)
        self.assertIn("Đoạn chưa đóng", output)

    def test_language_dirs_and_doc_target_languages_oserror(self) -> None:
        with mock.patch("pathlib.Path.mkdir", side_effect=OSError("Access denied")):
            dirs, errs = build_i18n._language_dirs(["new_lang"], allow_create=True)
            self.assertEqual([], dirs)
            self.assertTrue(any("could not create translation folder" in e for e in errs))

            targets, doc_errs = build_i18n._doc_target_languages(["new_lang"], allow_create=True)
            self.assertEqual([], targets)
            self.assertTrue(any("could not create documentation folder" in e for e in doc_errs))

    def test_check_doc_language_flags_missing_en_doc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", root / "doc" / "en" / "readme.html"),
            ):
                errs = build_i18n._check_doc_language("en")
                self.assertTrue(any("missing English source document" in e for e in errs))

    def test_check_doc_and_files_rtl_read_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ar_dir = root / "locale" / "ar"
            (ar_dir / "LC_MESSAGES").mkdir(parents=True)
            (ar_dir / "LC_MESSAGES" / "nvda.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
            (ar_dir / "manifest.ini").write_text("summary = Test\n", encoding="utf-8")
            doc_ar = root / "doc" / "ar"
            doc_ar.mkdir(parents=True)
            (doc_ar / "readme.html").write_text("placeholder", encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
                mock.patch("pathlib.Path.read_text", side_effect=OSError("File locked")),
            ):
                doc_errs = build_i18n._check_doc_language(
                    "ar",
                    checks={build_i18n.CHECK_DOCS},
                    check_only=True,
                )
                self.assertTrue(any("could not read" in e for e in doc_errs))

                file_errs = build_i18n._check_language_files(
                    ar_dir,
                    checks={build_i18n.CHECK_DOCS},
                    check_only=True,
                )
                self.assertTrue(any("could not read" in e for e in file_errs))

    def test_atomic_write_text_and_bytes_creates_target_and_parents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            text_target = root / "nested" / "dir" / "out.txt"
            build_i18n._atomic_write_text(text_target, "test content\n")
            self.assertTrue(text_target.is_file())
            self.assertEqual("test content\n", text_target.read_text(encoding="utf-8"))

            bin_target = root / "nested" / "bin" / "out.bin"
            build_i18n._atomic_write_bytes(bin_target, b"\x00\x01\x02")
            self.assertTrue(bin_target.is_file())
            self.assertEqual(b"\x00\x01\x02", bin_target.read_bytes())

    def test_atomic_write_cleans_up_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            text_target = root / "fail.txt"
            with (
                mock.patch("pathlib.Path.replace", side_effect=OSError("Disk full")),
                self.assertRaises(OSError),
            ):
                build_i18n._atomic_write_text(text_target, "data")
            # Ensure no orphaned temp files remained
            tmp_files = list(root.glob("*.tmp"))
            self.assertEqual([], tmp_files)

    def test_compile_mo_and_manifest_atomic_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            mo_path = lang_dir / "LC_MESSAGES" / "nvda.mo"
            catalog = {"Hello": "Xin chào"}

            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", root / "manifest.ini"):
                (root / "manifest.ini").write_text('summary = "Test"\ndescription = """Desc"""\n', encoding="utf-8")
                build_i18n._compile_mo(catalog, mo_path)
                build_i18n._write_translated_manifest(lang_dir, catalog)

            self.assertTrue(mo_path.is_file())
            self.assertGreater(mo_path.stat().st_size, 28)
            manifest_path = lang_dir / "manifest.ini"
            self.assertTrue(manifest_path.is_file())
            content = manifest_path.read_text(encoding="utf-8")
            self.assertIn("summary =", content)

    def test_main_build_docs_catches_write_doc_pot_error(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--build-docs", "-l", "vi"]),
            mock.patch.object(build_i18n, "DOC_EN_PATH", Path("non_existent/readme.html")),
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch.object(build_i18n, "_write_doc_pot", side_effect=RuntimeError("Doc POT error")),
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(1, res)
            print_mock.assert_any_call("[ERROR] Doc POT error")

    def test_main_build_ui_catches_write_pot_error(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--build", "-l", "vi"]),
            mock.patch.object(build_i18n, "_language_dirs", return_value=([Path("locale/vi")], [])),
            mock.patch.object(build_i18n, "_check_language_files", return_value=[]),
            mock.patch.object(build_i18n, "_check_catalog", return_value=[]),
            mock.patch.object(build_i18n, "_write_pot", side_effect=OSError("Cannot write POT")),
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(1, res)
            print_mock.assert_any_call("[ERROR] Cannot write POT")

    def test_main_build_ui_catches_compile_mo_error(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--build", "-l", "vi"]),
            mock.patch.object(build_i18n, "_language_dirs", return_value=([Path("locale/vi")], [])),
            mock.patch.object(build_i18n, "_check_language_files", return_value=[]),
            mock.patch.object(build_i18n, "_check_catalog", return_value=[]),
            mock.patch.object(build_i18n, "_write_pot", return_value=build_i18n.POT_PATH),
            mock.patch.object(build_i18n, "_parse_po", return_value={"test": "dịch"}),
            mock.patch.object(build_i18n, "_po_fuzzy_msgids", return_value=set()),
            mock.patch.object(build_i18n, "_compile_mo", side_effect=OSError("Permission denied")),
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(1, res)
            print_mock.assert_any_call("[ERROR] vi: failed to write generated files: Permission denied")

    def test_check_doc_language_en_detects_html_issues(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_en = root / "doc" / "en"
            doc_en.mkdir(parents=True)
            bad_html = doc_en / "readme.html"
            bad_html.write_text("<p>Hello <script>alert(1)</script></p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", bad_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                errs = build_i18n._check_doc_language("en", checks={build_i18n.CHECK_PLACEHOLDERS})
                self.assertTrue(any("disallowed HTML tag: <script>" in e for e in errs))

    def test_main_check_docs_with_en(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--check-docs", "-l", "en"]),
            mock.patch.object(build_i18n, "DOC_EN_PATH", Path("doc/en/readme.html")),
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]) as check_mock,
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(0, res)
            check_mock.assert_called_once_with("en", supported_languages=mock.ANY, checks=mock.ANY, check_only=True)
            print_mock.assert_any_call("  Passed: English source document")

    def test_main_check_docs_all_includes_en(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--check-docs"]),
            mock.patch.object(build_i18n, "_addon_doc_languages", return_value=["vi"]),
            mock.patch.object(build_i18n, "DOC_EN_PATH", Path("doc/en/readme.html")),
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]),
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(0, res)
            print_mock.assert_any_call("  Passed: English source document")

    def test_main_build_docs_catches_build_error(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--build-docs", "-l", "vi"]),
            mock.patch.object(build_i18n, "DOC_EN_PATH", Path("doc/en/readme.html")),
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch.object(build_i18n, "_write_doc_pot", return_value=build_i18n.DOC_POT_PATH),
            mock.patch.object(build_i18n, "_check_doc_language", return_value=[]),
            mock.patch.object(build_i18n, "_build_doc_for_language", side_effect=OSError("Disk full")),
            mock.patch("builtins.print") as print_mock,
        ):
            res = build_i18n.main()
            self.assertEqual(1, res)
            print_mock.assert_any_call("[ERROR] vi: failed to build documentation: Disk full")

    def test_translatable_source_messages_wraps_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_dir = root / "src"
            src_dir.mkdir()
            (src_dir / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "TRANSLATABLE_SOURCE_DIRS", (src_dir,)),
                mock.patch.object(build_i18n, "_manifest_values", return_value={}),
            ):
                with self.assertRaises(ValueError) as ctx:
                    build_i18n._translatable_source_messages()
                self.assertIn("Could not parse", str(ctx.exception))


# ---------------------------------------------------------------------------
# Markdown <-> HTML conversion tests
# ---------------------------------------------------------------------------


class DocHtmlToMarkdownTests(unittest.TestCase):
    def test_headings_conversion(self) -> None:
        html_input = "<h1>Title</h1><h2>Section</h2><h3>Sub</h3>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("# Title", md)
        self.assertIn("## Section", md)
        self.assertIn("### Sub", md)

    def test_paragraphs_and_inline_formatting(self) -> None:
        html_input = (
            "<p>A paragraph with <strong>bold</strong>, <em>italic</em>, "
            '<code>code</code>, <kbd>Ctrl+S</kbd>, and <a href="https://example.com">link</a>.</p>'
        )
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("**bold**", md)
        self.assertIn("*italic*", md)
        self.assertIn("`code`", md)
        self.assertIn("<kbd>Ctrl+S</kbd>", md)
        self.assertIn("[link](https://example.com)", md)

    def test_lists_and_nested_lists(self) -> None:
        html_input = "<ul><li>Item 1</li><li>Item 2<ul><li>Sub 2.1</li><li>Sub 2.2</li></ul></li></ul>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("* Item 1", md)
        self.assertIn("* Item 2", md)
        self.assertIn("  * Sub 2.1", md)
        self.assertIn("  * Sub 2.2", md)

    def test_ordered_list(self) -> None:
        html_input = "<ol><li>First</li><li>Second</li></ol>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("1. First", md)
        self.assertIn("2. Second", md)

    def test_blockquote_and_hr(self) -> None:
        html_input = "<blockquote><p>Quote text</p></blockquote><hr><p>After hr</p>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("> Quote text", md)
        self.assertIn("---", md)
        self.assertIn("After hr", md)

    def test_code_fence(self) -> None:
        html_input = "<pre><code>def foo():\n    return 42\n</code></pre>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("```\ndef foo():\n    return 42\n```", md)

    def test_entities_preserved(self) -> None:
        html_input = "<p>Menu &rarr; Settings &copy; 2026</p>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("&rarr;", md)
        self.assertIn("&copy;", md)


class SanitizeHrefTests(unittest.TestCase):
    def test_safe_urls(self) -> None:
        self.assertEqual("https://example.com/test", build_i18n._sanitize_href("https://example.com/test"))
        self.assertEqual("doc/readme.html", build_i18n._sanitize_href("doc/readme.html"))
        self.assertEqual("#section-1", build_i18n._sanitize_href("#section-1"))

    def test_malicious_javascript_urls(self) -> None:
        self.assertEqual("#", build_i18n._sanitize_href("javascript:alert(1)"))
        self.assertEqual("#", build_i18n._sanitize_href("JAVASCRIPT:alert(1)"))
        self.assertEqual("#", build_i18n._sanitize_href("  javascript:alert(1)  "))
        self.assertEqual("#", build_i18n._sanitize_href("java\x00script:alert(1)"))
        self.assertEqual("#", build_i18n._sanitize_href("java\tscript:alert(1)"))

    def test_malicious_data_and_vbscript(self) -> None:
        self.assertEqual("#", build_i18n._sanitize_href("data:text/html,<script>alert(1)</script>"))
        self.assertEqual("#", build_i18n._sanitize_href("vbscript:msgbox(1)"))


class FormatInlineMarkdownTests(unittest.TestCase):
    def test_bold_italic_and_code(self) -> None:
        text = "Text with **bold**, *italic*, and `code`."
        formatted = build_i18n._format_inline_markdown(text)
        self.assertEqual("Text with <strong>bold</strong>, <em>italic</em>, and <code>code</code>.", formatted)

    def test_snake_case_not_italicized(self) -> None:
        text = "Variable `my_var_name` and plain text_variable_name should be preserved."
        formatted = build_i18n._format_inline_markdown(text)
        self.assertIn("<code>my_var_name</code>", formatted)
        self.assertIn("text_variable_name", formatted)
        self.assertNotIn("<em>variable</em>", formatted)

    def test_safe_kbd_tag(self) -> None:
        text = "Press <kbd>Ctrl+S</kbd> to save."
        formatted = build_i18n._format_inline_markdown(text)
        self.assertEqual("Press <kbd>Ctrl+S</kbd> to save.", formatted)

    def test_dangerous_html_tags_are_escaped(self) -> None:
        text = 'Malicious <script>alert(1)</script> and <img src=x onerror=alert(1)> and <iframe src="foo"></iframe>.'
        formatted = build_i18n._format_inline_markdown(text)
        self.assertNotIn("<script>", formatted)
        self.assertNotIn("<img", formatted)
        self.assertNotIn("<iframe", formatted)
        self.assertIn("&lt;script&gt;", formatted)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", formatted)
        self.assertIn('&lt;iframe src="foo"&gt;', formatted)

    def test_kbd_with_events_is_escaped(self) -> None:
        text = 'Malicious <kbd onclick="bad()">Key</kbd>.'
        formatted = build_i18n._format_inline_markdown(text)
        self.assertNotIn('<kbd onclick="bad()">', formatted)
        self.assertIn('&lt;kbd onclick="bad()"&gt;', formatted)

    def test_link_sanitization(self) -> None:
        text = "Good [Link](https://google.com) and bad [XSS](javascript:alert(1))."
        formatted = build_i18n._format_inline_markdown(text)
        self.assertIn('<a href="https://google.com">Link</a>', formatted)
        self.assertIn('<a href="#">XSS</a>', formatted)


class MarkdownToDocHtmlTests(unittest.TestCase):
    def test_ltr_html_generation(self) -> None:
        md = "# Heading 1\n\nParagraph text.\n\n* List item 1\n* List item 2\n"
        doc = build_i18n._markdown_to_doc_html(md, language="vi", is_rtl=False)
        self.assertIn("<!DOCTYPE html>", doc)
        self.assertIn('<html lang="vi">', doc)
        self.assertNotIn('dir="rtl"', doc)
        self.assertIn("<title>Heading 1</title>", doc)
        self.assertIn("Heading 1</h1>", doc)
        self.assertIn("<p>Paragraph text.</p>", doc)
        self.assertIn("<li>List item 1</li>", doc)

    def test_rtl_html_generation(self) -> None:
        md = "# Arabic Title\n\nArabic text.\n"
        doc = build_i18n._markdown_to_doc_html(md, language="ar", is_rtl=True)
        self.assertIn('<html lang="ar" dir="rtl">', doc)
        self.assertIn("<title>Arabic Title</title>", doc)

    def test_markdown_sanitization_removes_xss(self) -> None:
        md = "# Title\n\n<script>alert('xss')</script>\n\n<img src=x onerror=alert(1)>\n\n[Bad link](javascript:alert(1))"
        doc = build_i18n._markdown_to_doc_html(md, language="en", is_rtl=False)
        self.assertNotIn("<script>", doc)
        self.assertNotIn("onerror", doc)
        self.assertNotIn("javascript:alert", doc)

    def test_markdown_allows_kbd_and_mathml(self) -> None:
        md = "# Keys\n\nPress <kbd>NVDA+Ctrl+S</kbd> to open settings.\n\n<math><mrow><mi>x</mi></mrow></math>"
        doc = build_i18n._markdown_to_doc_html(md, language="en", is_rtl=False)
        self.assertIn("<kbd>NVDA+Ctrl+S</kbd>", doc)
        self.assertIn("<math>", doc)

    def test_markdownlint_comments_stripped(self) -> None:
        md = "# Title <!-- markdownlint-disable -->\n\nContent <!-- markdownlint-capture -->\n"
        doc = build_i18n._markdown_to_doc_html(md, language="en", is_rtl=False)
        self.assertNotIn("markdownlint", doc)

    def test_doc_title_from_heading_or_kc_title(self) -> None:
        md1 = "<!-- KC:title: Custom Key Commands Title -->\n\n# Body Heading\n"
        self.assertEqual("Custom Key Commands Title", build_i18n._get_title_from_markdown(md1))

        md2 = "# My Accessible Addon\n\nSome text\n"
        self.assertEqual("My Accessible Addon", build_i18n._get_title_from_markdown(md2))


class ConvertHtmlAndMdFilesTests(unittest.TestCase):
    def test_table_html_to_md_and_back(self) -> None:
        html_input = (
            "<h1>Shortcuts</h1>\n"
            "<table>\n"
            "<thead><tr><th>Key</th><th>Description</th></tr></thead>\n"
            "<tbody>\n"
            "<tr><td><kbd>NVDA+S</kbd></td><td>Synthesizer dialog</td></tr>\n"
            "<tr><td><kbd>NVDA+V</kbd></td><td>Voice settings</td></tr>\n"
            "</tbody>\n"
            "</table>"
        )
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_input)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("| Key | Description |", md)
        self.assertIn("| --- | --- |", md)
        self.assertIn("| <kbd>NVDA+S</kbd> | Synthesizer dialog |", md)
        self.assertIn("| <kbd>NVDA+V</kbd> | Voice settings |", md)

        # Roundtrip to HTML
        html_out = build_i18n._markdown_to_doc_html(md, language="en", is_rtl=False)
        self.assertIn("<table>", html_out)
        self.assertIn("<th>Key</th>", html_out)
        self.assertIn("<td><kbd>NVDA+S</kbd></td>", html_out)

    def test_roundtrip_english_doc(self) -> None:
        en_html_path = build_i18n.DOC_EN_PATH
        if not en_html_path.is_file():
            self.skipTest("English readme.html not available")
        with tempfile.TemporaryDirectory() as td:
            temp_dir = Path(td)
            md_path = temp_dir / "readme.md"
            rebuilt_html_path = temp_dir / "readme.html"

            ok1, _ = build_i18n._convert_html_file_to_md(en_html_path, md_path)
            self.assertTrue(ok1)
            self.assertTrue(md_path.is_file())

            ok2, _ = build_i18n._convert_md_file_to_html(md_path, rebuilt_html_path, "en")
            self.assertTrue(ok2)
            self.assertTrue(rebuilt_html_path.is_file())

            ext1 = build_i18n._DocHtmlExtractor()
            ext1.feed(en_html_path.read_text(encoding="utf-8-sig"))
            ext1.close()

            ext2 = build_i18n._DocHtmlExtractor()
            ext2.feed(rebuilt_html_path.read_text(encoding="utf-8-sig"))
            ext2.close()

            self.assertEqual(len(ext1.segments), len(ext2.segments))
            for i, ((_, seg1), (_, seg2)) in enumerate(zip(ext1.segments, ext2.segments, strict=True)):
                self.assertEqual(html.unescape(seg1), html.unescape(seg2), f"Segment {i} mismatch")

    def test_missing_files_error_handling(self) -> None:
        ok1, msg1 = build_i18n._convert_html_file_to_md(Path("nonexistent.html"), Path("out.md"))
        self.assertFalse(ok1)
        self.assertIn("HTML file not found", msg1)

        ok2, msg2 = build_i18n._convert_md_file_to_html(Path("nonexistent.md"), Path("out.html"), "en")
        self.assertFalse(ok2)
        self.assertIn("Markdown file not found", msg2)

    def test_doc_html_to_markdown_flushes_unclosed_buffer_at_eof(self) -> None:
        p = build_i18n._DocHtmlToMarkdown()
        p.feed("<p>Unclosed paragraph")
        p.close()
        md = p.get_markdown()
        self.assertIn("Unclosed paragraph", md)

    def test_format_inline_markdown_supports_image(self) -> None:
        text = "Here is an image: ![Diagram](https://example.com/pic_1.png)"
        formatted = build_i18n._format_inline_markdown(text)
        self.assertIn('<img src="https://example.com/pic_1.png" alt="Diagram">', formatted)
        self.assertNotIn("!<a", formatted)

    def test_render_table_html_handles_uneven_row_columns(self) -> None:
        headers = ["Col 1", "Col 2"]
        alignments = ["left", "right"]
        rows = [["A", "B", "Extra"]]
        table_html = build_i18n._render_table_html(headers, alignments, rows)
        self.assertIn("<td>Extra</td>", table_html)

    def test_doc_html_to_markdown_preserves_table_alignments(self) -> None:
        html_input = (
            "<table>\n"
            '<thead><tr><th align="left">Left</th><th align="center">Center</th><th align="right">Right</th></tr></thead>\n'
            "<tbody><tr><td>1</td><td>2</td><td>3</td></tr></tbody>\n"
            "</table>"
        )
        converter = build_i18n._DocHtmlToMarkdown()
        converter.feed(html_input)
        converter.close()
        md = converter.get_markdown()
        self.assertIn("| :--- | :---: | ---: |", md)

    def test_fallback_markdown_to_html_body_flushes_unclosed_code_block(self) -> None:
        md = "# Heading\n\n```python\nprint('hello')\n"
        html_body = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn('<pre><code class="language-python">print(&#x27;hello&#x27;)</code></pre>', html_body)

        md_no_lang = "# Heading\n\n```\nprint('hello')\n"
        html_no_lang = build_i18n._fallback_markdown_to_html_body(md_no_lang)
        self.assertIn("<pre><code>print(&#x27;hello&#x27;)</code></pre>", html_no_lang)


class BuildDocForLanguageMarkdownTests(unittest.TestCase):
    def test_priority_1_compile_from_po_when_po_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_dir = Path(td) / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            po_file.write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
            md_file = doc_dir / "readme.md"
            md_file.write_text("# Tiêu đề\n", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", Path(td) / "doc"),
                mock.patch.object(build_i18n, "_build_doc_from_po", return_value=(True, "from po")) as po_mock,
            ):
                ok, msg = build_i18n._build_doc_for_language("vi")
                self.assertTrue(ok)
                self.assertEqual("from po", msg)
                po_mock.assert_called_once()

    def test_priority_2_compile_from_md_when_no_po(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_dir = Path(td) / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            md_file = doc_dir / "readme.md"
            md_file.write_text("# Tiêu đề\n\nNội dung đoạn văn.\n", encoding="utf-8")
            html_file = doc_dir / "readme.html"
            with mock.patch.object(build_i18n, "DOC_DIR", Path(td) / "doc"):
                ok, msg = build_i18n._build_doc_for_language("vi")
                self.assertTrue(ok)
                self.assertTrue(html_file.is_file())
                self.assertIn("readme.md", msg)
                content = html_file.read_text(encoding="utf-8")
                self.assertIn("<title>Tiêu đề</title>", content)
                self.assertIn("Nội dung đoạn văn.", content)

    def test_priority_3_keep_html_when_no_po_and_no_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_dir = Path(td) / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            html_file = doc_dir / "readme.html"
            html_file.write_text("<!DOCTYPE html><html><body>Existing</body></html>", encoding="utf-8")
            with mock.patch.object(build_i18n, "DOC_DIR", Path(td) / "doc"):
                ok, msg = build_i18n._build_doc_for_language("vi")
                self.assertFalse(ok)
                self.assertIn("already exists", msg)

    def test_priority_4_fail_when_no_files_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            doc_dir = Path(td) / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            with mock.patch.object(build_i18n, "DOC_DIR", Path(td) / "doc"):
                ok, msg = build_i18n._build_doc_for_language("vi")
                self.assertFalse(ok)
                self.assertIn("no readme.po, readme.md, or readme.html found", msg)

    def test_english_cannot_be_built(self) -> None:
        ok, msg = build_i18n._build_doc_for_language("en")
        self.assertFalse(ok)
        self.assertIn("English source document", msg)


class CheckDocLanguageMarkdownTests(unittest.TestCase):
    def test_check_doc_language_recognizes_readme_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            en_doc_dir = root / "doc" / "en"
            en_doc_dir.mkdir(parents=True)
            en_html = en_doc_dir / "readme.html"
            en_html.write_text(
                "<!DOCTYPE html><html><head><title>Title</title></head><body><h1>Title</h1><p>Content</p></body></html>",
                encoding="utf-8",
            )

            vi_doc_dir = root / "doc" / "vi"
            vi_doc_dir.mkdir(parents=True)
            vi_md = vi_doc_dir / "readme.md"
            vi_md.write_text("# Tiêu đề\n\nNội dung\n", encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
            ):
                errors = build_i18n._check_doc_language("vi", check_only=True)
                self.assertEqual([], errors)

    def test_check_language_files_recognizes_readme_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            locale_vi = root / "locale" / "vi"
            (locale_vi / "LC_MESSAGES").mkdir(parents=True)
            (locale_vi / "LC_MESSAGES" / "nvda.po").write_text('msgid ""\nmsgstr ""\n', encoding="utf-8")
            (locale_vi / "manifest.ini").write_text("summary = Test\n", encoding="utf-8")

            vi_doc_dir = root / "doc" / "vi"
            vi_doc_dir.mkdir(parents=True)
            (vi_doc_dir / "readme.md").write_text("# Tiêu đề\n", encoding="utf-8")

            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "LOCALE_DIR", root / "locale"),
            ):
                errors = build_i18n._check_language_files(locale_vi, check_only=True)
                self.assertEqual([], errors)


class InteractiveOptionsMarkdownTests(unittest.TestCase):
    def test_interactive_task_7_html_to_md(self) -> None:
        with (
            mock.patch.object(build_i18n, "_addon_doc_languages", return_value=["vi"]),
            mock.patch("builtins.input", side_effect=["7", "1"]),
            mock.patch("builtins.print"),
        ):
            options = build_i18n._interactive_options(build_i18n.DEFAULT_CHECKS)
            self.assertIsNone(options[0])  # all locales
            self.assertTrue(options[9])  # html_to_md
            self.assertFalse(options[10])  # md_to_html

    def test_interactive_task_8_md_to_html(self) -> None:
        with (
            mock.patch.object(build_i18n, "_addon_doc_languages", return_value=["vi"]),
            mock.patch("builtins.input", side_effect=["8", "1"]),
            mock.patch("builtins.print"),
        ):
            options = build_i18n._interactive_options(build_i18n.DEFAULT_CHECKS)
            self.assertIsNone(options[0])  # all locales
            self.assertFalse(options[9])  # html_to_md
            self.assertTrue(options[10])  # md_to_html


class MainCliMarkdownTests(unittest.TestCase):
    def test_html_to_md_flag(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--html-to-md", "-l", "vi"]),
            mock.patch.object(build_i18n, "_doc_target_languages", return_value=(["vi"], [])),
            mock.patch.object(build_i18n, "_convert_html_file_to_md", return_value=(True, "Converted")) as conv_mock,
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch("builtins.print"),
        ):
            res = build_i18n.main()
            self.assertEqual(0, res)
            conv_mock.assert_called_once()

    def test_md_to_html_flag(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--md-to-html", "-l", "vi"]),
            mock.patch.object(build_i18n, "_doc_target_languages", return_value=(["vi"], [])),
            mock.patch.object(build_i18n, "_convert_md_file_to_html", return_value=(True, "Converted")) as conv_mock,
            mock.patch.object(build_i18n.Path, "is_file", return_value=True),
            mock.patch("builtins.print"),
        ):
            res = build_i18n.main()
            self.assertEqual(0, res)
            conv_mock.assert_called_once()

    def test_conflicting_flags_rejected(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--html-to-md", "--md-to-html", "-l", "vi"]),
            mock.patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                build_i18n.main()
            self.assertEqual(2, ctx.exception.code)

    def test_html_to_md_requires_language_or_all(self) -> None:
        with (
            mock.patch.object(build_i18n.sys, "argv", ["build_i18n.py", "--html-to-md"]),
            mock.patch("builtins.print"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                build_i18n.main()
            self.assertEqual(2, ctx.exception.code)


class DocBuildIntegrationTests(unittest.TestCase):
    def test_find_msgfmt_custom_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake_exe = Path(td) / "msgfmt.exe"
            fake_exe.write_text("placeholder", encoding="utf-8")
            self.assertEqual(fake_exe.resolve(), build_i18n._find_msgfmt(fake_exe))
            non_existent = Path(td) / "missing_msgfmt.exe"
            self.assertIsNone(build_i18n._find_msgfmt(non_existent))

    def test_check_po_syntax_with_msgfmt_edge_cases(self) -> None:
        # None or missing msgfmt returns empty errors
        self.assertEqual([], build_i18n._check_po_syntax_with_msgfmt(Path("missing.po"), msgfmt_path=None))

        with tempfile.TemporaryDirectory() as td:
            # Headerless PO file (no charset) skips msgfmt
            po_file = Path(td) / "no_charset.po"
            po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "A"\nmsgstr "B"\n', encoding="utf-8")
            self.assertEqual([], build_i18n._check_po_syntax_with_msgfmt(po_file))

            # Valid PO with UTF-8 charset header
            valid_po = Path(td) / "valid.po"
            valid_po.write_text(
                'msgid ""\nmsgstr ""\n'
                '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                'msgid "Hello"\nmsgstr "Xin chào"\n',
                encoding="utf-8",
            )
            msgfmt_path = build_i18n._find_msgfmt()
            if msgfmt_path is not None:
                errs = build_i18n._check_po_syntax_with_msgfmt(valid_po, msgfmt_path=msgfmt_path)
                self.assertEqual([], errs)

                # Invalid PO with unclosed quote
                bad_po = Path(td) / "bad.po"
                bad_po.write_text(
                    'msgid ""\nmsgstr ""\n'
                    '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                    'msgid "Hello\nmsgstr "Xin chào"\n',
                    encoding="utf-8",
                )
                errs_bad = build_i18n._check_po_syntax_with_msgfmt(bad_po, msgfmt_path=msgfmt_path)
                self.assertTrue(len(errs_bad) > 0)
                self.assertTrue(any("msgfmt syntax error" in e for e in errs_bad))

    def test_compile_mo_file_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_path = Path(td) / "nvda.po"
            po_path.write_text(
                'msgid ""\nmsgstr ""\n'
                '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                'msgid "Google TTS"\nmsgstr "Google TTS VN"\n',
                encoding="utf-8",
            )
            mo_path = Path(td) / "nvda.mo"
            catalog = {"": "Content-Type: text/plain; charset=UTF-8\n", "Google TTS": "Google TTS VN"}
            build_i18n._compile_mo_file(po_path, mo_path, catalog)
            self.assertTrue(mo_path.is_file())
            self.assertGreater(mo_path.stat().st_size, 0)

            import gettext

            with open(mo_path, "rb") as fp:
                trans = gettext.GNUTranslations(fp)
            self.assertEqual("Google TTS VN", trans.gettext("Google TTS"))

    def test_check_html_content_with_lxml(self) -> None:
        if not build_i18n._LXML_AVAILABLE:
            self.assertEqual([], build_i18n._check_html_content_with_lxml("<p>test</p>"))
            return

        valid_html = "<!DOCTYPE html><html><head><title>Test</title></head><body><p>Valid</p></body></html>"
        self.assertEqual([], build_i18n._check_html_content_with_lxml(valid_html))

        mismatched_html = "<!DOCTYPE html><html><body><p><b>Hello</i></p></body></html>"
        errors = build_i18n._check_html_content_with_lxml(mismatched_html, "test.html")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("HTML syntax error" in e for e in errors))

    def test_convert_html_file_to_md_rejects_broken_html(self) -> None:
        if not build_i18n._LXML_AVAILABLE:
            return
        with tempfile.TemporaryDirectory() as td:
            bad_html = Path(td) / "bad.html"
            bad_html.write_text("<!DOCTYPE html><html><body><p><b>Hello</i></p></body></html>", encoding="utf-8")
            target_md = Path(td) / "out.md"
            success, msg = build_i18n._convert_html_file_to_md(bad_html, target_md)
            self.assertFalse(success)
            self.assertIn("HTML file has syntax errors", msg)


class FallbackMarkdownTableTests(unittest.TestCase):
    def test_parse_table_separator_valid_and_invalid(self) -> None:
        self.assertEqual(
            ["left", "center", "right", None], build_i18n._parse_table_separator("| :--- | :---: | ---: | --- |")
        )
        self.assertEqual([None, None], build_i18n._parse_table_separator("--- | ---"))
        self.assertIsNone(build_i18n._parse_table_separator("not a separator"))
        self.assertIsNone(build_i18n._parse_table_separator("| --- | abc |"))
        self.assertIsNone(build_i18n._parse_table_separator(""))

    def test_split_table_row_with_escaped_pipe(self) -> None:
        row = r"| Key | Symbol \| Description |"
        cells = build_i18n._split_table_row(row)
        self.assertEqual(["Key", "Symbol | Description"], cells)

    def test_table_parsing_with_alignments_and_formatting(self) -> None:
        md = (
            "| Command | Key | Description |\n"
            "| :--- | :---: | ---: |\n"
            "| **Save** | <kbd>Ctrl+S</kbd> | Save current *document* |\n"
            "| **Open** | <kbd>Ctrl+O</kbd> | Open `file` |\n"
        )
        html_out = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn("<table>", html_out)
        self.assertIn("<thead>", html_out)
        self.assertIn('<td align="left"><strong>Save</strong></td>', html_out)
        self.assertIn('<th align="left">Command</th>', html_out)
        self.assertIn('<th align="center">Key</th>', html_out)
        self.assertIn('<th align="right">Description</th>', html_out)
        self.assertIn("<tbody>", html_out)
        self.assertIn('<td align="center"><kbd>Ctrl+S</kbd></td>', html_out)
        self.assertIn('<td align="right">Save current <em>document</em></td>', html_out)
        self.assertIn('<td align="right">Open <code>file</code></td>', html_out)


class DefinitionListTests(unittest.TestCase):
    def test_fallback_markdown_definition_list(self) -> None:
        md = "Term 1\n:   Definition 1\n\nTerm 2\n:   Definition 2\n"
        html_out = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn("<dl>", html_out)
        self.assertIn("<dt>Term 1</dt>", html_out)
        self.assertIn("<dd>Definition 1</dd>", html_out)
        self.assertIn("<dt>Term 2</dt>", html_out)
        self.assertIn("<dd>Definition 2</dd>", html_out)

    def test_fallback_markdown_multiple_definitions_and_multiline(self) -> None:
        md = "Apple\n:   A pomaceous fruit\n    grown on trees.\n:   Red or green fruit.\n"
        html_out = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn("<dl>", html_out)
        self.assertIn("<dt>Apple</dt>", html_out)
        self.assertIn("<dd>A pomaceous fruit grown on trees.</dd>", html_out)
        self.assertIn("<dd>Red or green fruit.</dd>", html_out)

    def test_doc_html_to_markdown_definition_list(self) -> None:
        html_in = "<dl>\n<dt>Term 1</dt>\n<dd>Definition 1</dd>\n<dt>Term 2</dt>\n<dd><p>Definition 2</p></dd>\n</dl>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_in)
        conv.close()
        md = conv.get_markdown()
        self.assertIn("Term 1\n:   Definition 1", md)
        self.assertIn("Term 2\n:   Definition 2", md)


class PoSyntaxFallbackTests(unittest.TestCase):
    def test_check_po_syntax_fallback_detects_duplicate_msgid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_file = Path(td) / "duplicate.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "Xin chào"\n\nmsgid "Hello"\nmsgstr "Chào bạn"\n',
                encoding="utf-8",
            )
            errs = build_i18n._check_po_syntax_fallback(po_file)
            self.assertTrue(len(errs) > 0)
            self.assertTrue(any("duplicate message definition" in e for e in errs))
            self.assertTrue(any("Hello" in e for e in errs))

    def test_check_po_syntax_fallback_valid_po_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_file = Path(td) / "valid.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "One"\nmsgstr "Một"\n\nmsgid "Two"\nmsgstr "Hai"\n',
                encoding="utf-8",
            )
            errs = build_i18n._check_po_syntax_fallback(po_file)
            self.assertEqual([], errs)

    def test_check_language_files_detects_duplicate_msgid_without_msgfmt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lang_dir = root / "vi"
            (lang_dir / "LC_MESSAGES").mkdir(parents=True)
            po_file = lang_dir / "LC_MESSAGES" / "nvda.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Same"\nmsgstr "A"\n\nmsgid "Same"\nmsgstr "B"\n',
                encoding="utf-8",
            )
            with mock.patch.object(build_i18n, "_find_msgfmt", return_value=None):
                errs = build_i18n._check_language_files(lang_dir, check_only=False, msgfmt_path=None)
                self.assertTrue(any("duplicate message definition" in e for e in errs))

    def test_check_doc_language_detects_duplicate_msgid_without_msgfmt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Same"\nmsgstr "A"\n\nmsgid "Same"\nmsgstr "B"\n',
                encoding="utf-8",
            )
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Same</p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
                mock.patch.object(build_i18n, "_find_msgfmt", return_value=None),
            ):
                errs = build_i18n._check_doc_language("vi", msgfmt_path=None)
                self.assertTrue(any("duplicate message definition" in e for e in errs))


class DocListAndCheckImprovementsTests(unittest.TestCase):
    def test_doc_html_to_markdown_loose_lists_with_p(self) -> None:
        html_in = "<ul><li><p>Item 1</p></li><li><p>Item 2</p></li></ul>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_in)
        conv.close()
        md = conv.get_markdown()
        self.assertEqual("* Item 1\n* Item 2\n", md)

    def test_doc_html_to_markdown_ordered_loose_list(self) -> None:
        html_in = "<ol><li><p>First</p></li><li><p>Second</p></li></ol>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_in)
        conv.close()
        md = conv.get_markdown()
        self.assertEqual("1. First\n2. Second\n", md)

    def test_doc_html_to_markdown_nested_list_with_p(self) -> None:
        html_in = "<ul><li><p>Parent</p><ul><li>Child</li></ul></li></ul>"
        conv = build_i18n._DocHtmlToMarkdown()
        conv.feed(html_in)
        conv.close()
        md = conv.get_markdown()
        self.assertEqual("* Parent\n  * Child\n", md)
        self.assertNotIn("* \n", md)

    def test_fallback_markdown_to_html_body_nested_lists(self) -> None:
        md = "* A\n  * B\n  * C\n* D\n"
        html_out = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn("<ul>", html_out)
        self.assertIn("<li>A", html_out)
        self.assertIn("<li>B</li>", html_out)
        self.assertIn("<li>C</li>", html_out)
        self.assertIn("<li>D</li>", html_out)
        # Verify valid syntax with lxml
        errs = build_i18n._check_html_content_with_lxml(f"<html><body>{html_out}</body></html>")
        self.assertEqual([], errs)

    def test_fallback_markdown_to_html_body_mixed_sublist(self) -> None:
        md = "* A\n  1. B\n  2. C\n* D\n"
        html_out = build_i18n._fallback_markdown_to_html_body(md)
        self.assertIn("<ul>", html_out)
        self.assertIn("<ol>", html_out)
        errs = build_i18n._check_html_content_with_lxml(f"<html><body>{html_out}</body></html>")
        self.assertEqual([], errs)

    def test_check_doc_language_prioritizes_markdown_over_html(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            # readme.md has new content, readme.html has stale content
            (doc_dir / "readme.md").write_text("# Tiêu đề\n\nNội dung mới", encoding="utf-8")
            (doc_dir / "readme.html").write_text("<p>Nội dung cũ</p>", encoding="utf-8")
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<h1>Title</h1>\n<p>New content</p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
            ):
                errs = build_i18n._check_doc_language("vi", checks={"docs"})
                # Should not raise missing/syntax error for readme.md
                self.assertFalse(any("could not validate" in e for e in errs))

    def test_check_doc_language_detects_whitespace_untranslated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            doc_dir = root / "doc" / "vi"
            doc_dir.mkdir(parents=True)
            po_file = doc_dir / "readme.po"
            po_file.write_text(
                'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr "   "\n',
                encoding="utf-8",
            )
            en_html = root / "doc" / "en" / "readme.html"
            en_html.parent.mkdir(parents=True)
            en_html.write_text("<p>Hello</p>", encoding="utf-8")
            with (
                mock.patch.object(build_i18n, "DOC_DIR", root / "doc"),
                mock.patch.object(build_i18n, "DOC_EN_PATH", en_html),
                mock.patch.object(build_i18n, "ADDON_DIR", root),
                mock.patch.object(build_i18n, "_find_msgfmt", return_value=None),
            ):
                errs = build_i18n._check_doc_language("vi", checks={"docs"})
                self.assertTrue(any("untranslated documentation string" in e for e in errs))

    def test_doc_html_rebuilder_whitespace_translation_fallbacks_to_source(self) -> None:
        catalog = {"Hello": "   "}
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="vi", is_rtl=False)
        rebuilder.feed("<p>Hello</p>")
        rebuilder.close()
        out = "".join(rebuilder.output)
        self.assertIn("<p>Hello</p>", out)

    def test_merge_po_header_default_comments(self) -> None:
        lines = build_i18n._merge_po_header("Language: vi\n", language="vi", comments=None)
        header_text = "\n".join(lines)
        self.assertIn("# Google TTS For NVDA translation.", header_text)
        self.assertIn('"Language: vi\\n"', header_text)

    def test_iter_po_entries_with_tabs_and_extra_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            po_file = Path(td) / "test.po"
            po_file.write_text(
                'msgid\t""\nmsgstr\t""\n\n'
                'msgctxt\t"ctx"\n'
                'msgid\t"TabKey"\n'
                'msgstr\t"TabVal"\n\n'
                'msgid   "SpaceKey"\n'
                'msgstr   "SpaceVal"\n\n'
                "msgid\n"
                '"NewlineKey"\n'
                "msgstr\n"
                '"NewlineVal"\n',
                encoding="utf-8",
            )
            entries = build_i18n._iter_po_entries(po_file)
            entries_dict = {m: s for m, s, _ in entries}
            self.assertEqual("TabVal", entries_dict.get("TabKey"))
            self.assertEqual("SpaceVal", entries_dict.get("SpaceKey"))
            self.assertEqual("NewlineVal", entries_dict.get("NewlineKey"))

    def test_purge_obsolete_po_entries_with_tabs(self) -> None:
        content = (
            'msgid ""\nmsgstr ""\n\n'
            'msgid "Active"\nmsgstr "Đang dùng"\n\n'
            '#~\tmsgid "OldTab"\n#~\tmsgstr "Cũ"\n\n'
            '#~   msgid "OldSpace"\n#~   msgstr "Cũ 2"\n'
        )
        purged, count = build_i18n._purge_obsolete_po_entries(content)
        self.assertEqual(2, count)
        self.assertIn("Active", purged)
        self.assertNotIn("OldTab", purged)
        self.assertNotIn("OldSpace", purged)

    def test_write_translated_manifest_falls_back_on_whitespace_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_source = root / "manifest.ini"
            manifest_source.write_text(
                'summary = "English summary"\ndescription = """English desc"""\nchangelog = """English changes"""\n',
                encoding="utf-8",
            )
            lang_dir = root / "vi"
            lang_dir.mkdir(parents=True)
            with mock.patch.object(build_i18n, "MANIFEST_SOURCE", manifest_source):
                build_i18n._write_translated_manifest(
                    lang_dir,
                    {
                        "English summary": "   \t  ",
                        "English desc": "\n  \n",
                        "English changes": "  ",
                    },
                )
                result = (lang_dir / "manifest.ini").read_text(encoding="utf-8")
                self.assertIn('summary = "English summary"', result)
                self.assertIn('description = """English desc"""', result)
                self.assertIn('changelog = """English changes"""', result)

    def test_merge_po_header_with_doc_default_comments(self) -> None:
        doc_comments = [
            "# Google TTS For NVDA documentation translation.",
            "# Copyright (C) 2026 Google TTS For NVDA contributors",
            "# This file is distributed under the same license as the add-on.",
            "#",
        ]
        lines = build_i18n._merge_po_header(
            "Language: vi\n",
            language="vi",
            comments=None,
            default_comments=doc_comments,
        )
        header_text = "\n".join(lines)
        self.assertIn("# Google TTS For NVDA documentation translation.", header_text)
        self.assertIn('"Language: vi\\n"', header_text)

    def test_compile_mo_file_cleans_up_temp_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            po_file = root / "test.po"
            po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "Hi"\nmsgstr "Chào"\n', encoding="utf-8")
            mo_file = root / "test.mo"
            fake_msgfmt = root / "fake_msgfmt.exe"
            fake_msgfmt.touch()

            def raise_error(*args: object, **kwargs: object) -> object:
                tmp_file = mo_file.with_name(f".tmp-{mo_file.name}")
                tmp_file.touch()
                raise OSError("Simulated subprocess failure")

            with mock.patch("subprocess.run", side_effect=raise_error):
                build_i18n._compile_mo_file(po_file, mo_file, {"Hi": "Chào"}, msgfmt_path=fake_msgfmt)

            tmp_file = mo_file.with_name(f".tmp-{mo_file.name}")
            self.assertFalse(tmp_file.is_file())
            self.assertTrue(mo_file.is_file())

    def test_rtl_language_codes_contains_ps_and_sd(self) -> None:
        self.assertTrue(build_i18n._is_rtl_language("ps"))
        self.assertTrue(build_i18n._is_rtl_language("sd"))
        self.assertTrue(build_i18n._is_rtl_language("sd_PK"))
        self.assertFalse(build_i18n._is_rtl_language("en"))

    def test_main_rejects_nonexistent_msgfmt_path(self) -> None:
        with (
            mock.patch.object(
                build_i18n.sys,
                "argv",
                ["build_i18n.py", "--check", "--msgfmt", "C:/nonexistent/msgfmt.exe"],
            ),
            self.assertRaises(SystemExit) as cm,
        ):
            build_i18n.main()
        self.assertEqual(2, cm.exception.code)

    def test_main_rejects_nonexistent_msgmerge_path(self) -> None:
        with (
            mock.patch.object(
                build_i18n.sys,
                "argv",
                ["build_i18n.py", "--update-po", "-l", "vi", "--msgmerge", "C:/nonexistent/msgmerge.exe"],
            ),
            self.assertRaises(SystemExit) as cm,
        ):
            build_i18n.main()
        self.assertEqual(2, cm.exception.code)

    def test_check_format_interpolations_whitespace_only_msgstr(self) -> None:
        alerts = build_i18n._check_format_interpolations("Hello {name}, count: %d", "   \t\n  ")
        self.assertEqual([], alerts)

    def test_check_html_tag_interpolations_whitespace_only_msgstr(self) -> None:
        alerts = build_i18n._check_html_tag_interpolations('Click <a href="#">here</a>', "   ")
        self.assertEqual([], alerts)

    def test_doc_html_extractor_and_rebuilder_nested_structural_containers(self) -> None:
        html_input = (
            "<dl>\n"
            "<dt>Term</dt>\n"
            "<dd>Text before\n"
            "<ul>\n"
            "<li>Item 1</li>\n"
            "<li>Item 2</li>\n"
            "</ul>\n"
            "Text after</dd>\n"
            "</dl>\n"
        )
        segments = build_i18n._extract_doc_segments(html_input)
        seg_texts = [s for _, s in segments]
        self.assertEqual(["Term", "Text before", "Item 1", "Item 2", "Text after"], seg_texts)

        catalog = {
            "Term": "Thuật ngữ",
            "Text before": "Văn bản trước",
            "Item 1": "Mục 1",
            "Item 2": "Mục 2",
            "Text after": "Văn bản sau",
        }
        rebuilder = build_i18n._DocHtmlRebuilder(catalog, target_lang="vi", is_rtl=False)
        rebuilder.feed(html_input)
        rebuilder.close()
        rebuilt = "".join(rebuilder.output)

        self.assertIn("<dt>Thuật ngữ</dt>", rebuilt)
        self.assertIn("<dd>Văn bản trước", rebuilt)
        self.assertIn("<li>Mục 1</li>", rebuilt)
        self.assertIn("<li>Mục 2</li>", rebuilt)
        self.assertIn("Văn bản sau</dd>", rebuilt)

    def test_code_block_language_roundtrip(self) -> None:
        md_input = "```python\ndef greet():\n    return 'hello'\n```"
        html_body = build_i18n._fallback_markdown_to_html_body(md_input)
        self.assertIn('<pre><code class="language-python">', html_body)

        converter = build_i18n._DocHtmlToMarkdown()
        converter.feed(html_body)
        converter.close()
        md_output = converter.get_markdown()
        self.assertIn("```python", md_output)
        self.assertIn("def greet():", md_output)

    def test_extract_po_from_doc_html_ignores_whitespace_segments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            en_html = root / "en.html"
            en_html.write_text("<p>Hello world</p>", encoding="utf-8")
            lang_html = root / "lang.html"
            lang_html.write_text("<p>   </p>", encoding="utf-8")
            out_po = root / "out.po"

            success = build_i18n._extract_po_from_doc_html(en_html, lang_html, "vi", out_po)
            self.assertTrue(success)
            self.assertTrue(out_po.is_file())
            content = out_po.read_text(encoding="utf-8")
            self.assertIn('msgid "Hello world"', content)
            self.assertIn('msgstr ""', content)

    def test_get_active_doc_markdown_extensions_caching(self) -> None:
        first_call = build_i18n._get_active_doc_markdown_extensions()
        second_call = build_i18n._get_active_doc_markdown_extensions()
        self.assertEqual(len(first_call), len(second_call))
        self.assertIsNot(first_call, second_call)
        self.assertIsNotNone(build_i18n._RESOLVED_DOC_MARKDOWN_EXTENSIONS)
        self.assertEqual(build_i18n._RESOLVED_DOC_MARKDOWN_EXTENSIONS, [e for e in first_call if isinstance(e, str)])

    def test_check_po_with_msgfmt_passes_utf8_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            po_file = root / "test.po"
            po_file.write_text(
                'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "a"\nmsgstr "b"\n',
                encoding="utf-8",
            )
            fake_msgfmt = root / "fake_msgfmt.exe"
            fake_msgfmt.touch()

            captured_kwargs: dict[str, object] = {}

            def fake_run(*args: object, **kwargs: object) -> object:
                captured_kwargs.update(kwargs)
                mock_res = mock.MagicMock()
                mock_res.returncode = 0
                mock_res.stderr = ""
                return mock_res

            with mock.patch("subprocess.run", side_effect=fake_run):
                errors = build_i18n._check_po_syntax_with_msgfmt(po_file, msgfmt_path=fake_msgfmt)
                self.assertEqual(errors, [])
                self.assertEqual(captured_kwargs.get("encoding"), "utf-8")
                self.assertEqual(captured_kwargs.get("errors"), "replace")
                self.assertTrue(captured_kwargs.get("text"))


if __name__ == "__main__":
    unittest.main()

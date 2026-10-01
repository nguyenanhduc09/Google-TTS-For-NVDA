from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import difflib
import html
import json
import os
import re
import shutil
import string
import struct
import subprocess
import sys
import tempfile
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import markdown

    _MARKDOWN_AVAILABLE = True
except ImportError:
    markdown = None  # type: ignore[assignment]
    _MARKDOWN_AVAILABLE = False

try:
    import nh3

    _NH3_AVAILABLE = True
except ImportError:
    nh3 = None  # type: ignore[assignment]
    _NH3_AVAILABLE = False

try:
    import lxml.etree
    import lxml.html

    _LXML_AVAILABLE = True
except ImportError:
    lxml = None  # type: ignore[assignment]
    _LXML_AVAILABLE = False

ADDON_DIR = Path(__file__).resolve().parent / "googleTtsForNvda"
LOCALE_DIR = ADDON_DIR / "locale"
DOC_DIR = ADDON_DIR / "doc"
MANIFEST_SOURCE = ADDON_DIR / "manifest.ini"
POT_PATH = LOCALE_DIR / "nvda.pot"
DOC_POT_PATH = DOC_DIR / "readme.pot"
DOC_EN_PATH = DOC_DIR / "en" / "readme.html"
TRANSLATABLE_DOC_TAGS = frozenset(
    {"title", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "th", "td", "dt", "dd", "caption", "blockquote", "summary"}
)
DOC_STRUCTURAL_CONTAINER_TAGS = frozenset({"ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr"})


def _relative_to_addon(path: Path) -> Path:
    try:
        return path.relative_to(ADDON_DIR)
    except ValueError:
        return path


DEFAULT_NVDA_LOCALE_DIRS = (
    Path(r"C:\Program Files\NVDA\locale"),
    Path(r"C:\Program Files (x86)\NVDA\locale"),
)
POEDIT_MSGMERGE_RELATIVE_PATH = Path("Poedit") / "GettextTools" / "bin" / "msgmerge.exe"
POEDIT_MSGFMT_RELATIVE_PATH = Path("Poedit") / "GettextTools" / "bin" / "msgfmt.exe"
MANIFEST_KEYS = ("summary", "description", "changelog")
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
# Unnamed percent interpolations (e.g. %s, %d, %10.2f, %-5s, but not %%)
RE_UNNAMED_PERCENT = re.compile(
    r"""
    %
    [#0+-]*
    \d*
    (?:\.\d+)?
    [hlL]?
    [diouxXeEfFgGcrsa]
    """,
    flags=re.VERBOSE,
)
# Named percent interpolations (e.g. %(name)s, %(count)02d, %(name)-10s)
RE_NAMED_PERCENT = re.compile(r"%\([^()]+\)[#0+\- ]*\d*(?:\.\d+)?[a-zA-Z]")
# Brace format interpolations (e.g. {name}, {name:format}, {0})
RE_FORMAT = re.compile(r"{([^{}:]*):?[^{}]*}")
VOID_HTML_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link"})
DANGEROUS_HTML_TAGS = frozenset(
    {
        "script",
        "iframe",
        "object",
        "embed",
        "style",
        "form",
        "input",
        "button",
        "svg",
        "base",
        "link",
        "meta",
        "applet",
        "audio",
        "video",
        "source",
    }
)
OPEN_HTML_TAG_RE = re.compile(r"<([a-zA-Z0-9]+)(?:[\s/][^>]*)?>")
CLOSE_HTML_TAG_RE = re.compile(r"</([a-zA-Z0-9]+)\s*>")
HTML_TAG_TOKEN_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)(?:[\s/][^>]*)?>")
HREF_ATTR_RE = re.compile(
    r"""<a[\s/][^>]*?(?<![\w-])href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)
EVENT_HANDLER_ATTR_RE = re.compile(r"""<[a-zA-Z0-9]+[\s/][^>]*?(?<![\w-])on[a-zA-Z]+\s*=""", re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r"""<[a-zA-Z0-9]+[\s/][^>]*?(?<![\w-])style\s*=""", re.IGNORECASE)


def _extract_hrefs(text: str) -> list[str]:
    hrefs: list[str] = []
    for m in HREF_ATTR_RE.finditer(text):
        val = m.group(1) or m.group(2) or m.group(3) or ""
        hrefs.append(val)
    return hrefs


TRANSLATABLE_SOURCE_DIRS = (
    ADDON_DIR / "globalPlugins" / "googleTtsForNvda",
    ADDON_DIR / "synthDrivers" / "googleTtsForNvda",
)
SKIPPED_SOURCE_PARTS = {"WasmTtsEngine", "websocketClientRepo", "__pycache__"}
CHECK_LANGUAGE = "language"
CHECK_MANIFEST = "manifest"
CHECK_DOCS = "docs"
CHECK_UI = "ui"
CHECK_PLACEHOLDERS = "placeholders"
CHECK_SORT = "sort"
CHECK_OBSOLETE = "obsolete"
CHECK_FUZZY = "fuzzy"
CHECK_ORDER = (
    CHECK_LANGUAGE,
    CHECK_MANIFEST,
    CHECK_DOCS,
    CHECK_UI,
    CHECK_PLACEHOLDERS,
    CHECK_SORT,
    CHECK_OBSOLETE,
    CHECK_FUZZY,
)
CHECK_LABELS = {
    CHECK_LANGUAGE: "NVDA language code",
    CHECK_MANIFEST: "manifest",
    CHECK_DOCS: "documentation",
    CHECK_UI: "UI strings",
    CHECK_PLACEHOLDERS: "placeholders and format specifiers",
    CHECK_SORT: "language sorting",
    CHECK_OBSOLETE: "obsolete source strings",
    CHECK_FUZZY: "fuzzy translations",
}
DEFAULT_UI_CHECKS = frozenset(
    {
        CHECK_LANGUAGE,
        CHECK_MANIFEST,
        CHECK_UI,
        CHECK_PLACEHOLDERS,
        CHECK_SORT,
        CHECK_OBSOLETE,
        CHECK_FUZZY,
    }
)
DEFAULT_DOC_CHECKS = frozenset(
    {
        CHECK_LANGUAGE,
        CHECK_DOCS,
        CHECK_PLACEHOLDERS,
        CHECK_OBSOLETE,
        CHECK_FUZZY,
    }
)
DEFAULT_CHECKS = set(DEFAULT_UI_CHECKS)
ALL_CHECKS = set(DEFAULT_UI_CHECKS) | set(DEFAULT_DOC_CHECKS)
RTL_LANG_CODES = frozenset({"ar", "ckb", "fa", "he", "ps", "sd", "ug", "ur", "yi"})


def _atomic_write_bytes(target_path: Path, content: bytes, prefix: str = ".tmp-") -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_path.parent,
            prefix=prefix,
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        temp_path.replace(target_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _atomic_write_text(target_path: Path, content: str, prefix: str = ".tmp-") -> None:
    _atomic_write_bytes(target_path, content.encode("utf-8"), prefix=prefix)


def _decode_po_string(token: str) -> str:
    try:
        val = ast.literal_eval(token)
        return val if isinstance(val, str) else str(val)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"malformed PO string {token!r}: {exc}") from exc


def _iter_po_entries(path: Path) -> list[tuple[str, str, bool]]:
    entries: list[tuple[str, str, bool]] = []
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []
    active: str | None = None
    has_msgid = False
    next_fuzzy = False
    is_fuzzy = False

    def commit() -> None:
        nonlocal msgid_parts, msgstr_parts, active, has_msgid, is_fuzzy
        if has_msgid:
            msgid = "".join(msgid_parts)
            msgstr = "".join(msgstr_parts)
            entries.append((msgid, msgstr, is_fuzzy))
        msgid_parts = []
        msgstr_parts = []
        active = None
        has_msgid = False
        is_fuzzy = False

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            commit()
            continue
        if line.startswith("#~"):
            commit()
            next_fuzzy = False
            continue
        if line.startswith("#,") and re.search(r"\bfuzzy\b", line):
            next_fuzzy = True
            continue
        if line.startswith("#"):
            continue
        if (line.startswith("msgctxt") and line[7:8] in (" ", "\t")) or line == "msgctxt":
            commit()
            active = "msgctxt"
            continue
        if line.startswith('"') and active == "msgctxt":
            continue
        if (line.startswith("msgid_plural") and line[12:13] in (" ", "\t")) or line == "msgid_plural":
            active = "msgid_plural"
            continue
        if line.startswith('"') and active == "msgid_plural":
            continue
        if line.startswith("msgstr["):
            active = "msgstr_plural"
            continue
        if line.startswith('"') and active == "msgstr_plural":
            continue
        if (line.startswith("msgid") and line[5:6] in (" ", "\t")) or line == "msgid":
            commit()
            active = "msgid"
            has_msgid = True
            is_fuzzy = next_fuzzy
            next_fuzzy = False
            rest = line[5:].strip()
            if rest:
                msgid_parts.append(_decode_po_string(rest))
            continue
        if (line.startswith("msgstr") and line[6:7] in (" ", "\t")) or line == "msgstr":
            active = "msgstr"
            rest = line[6:].strip()
            if rest:
                msgstr_parts.append(_decode_po_string(rest))
            continue
        if line.startswith('"') and active == "msgid":
            msgid_parts.append(_decode_po_string(line))
            continue
        if line.startswith('"') and active == "msgstr":
            msgstr_parts.append(_decode_po_string(line))
            continue
    commit()
    return entries


def _parse_po(
    path: Path,
    *,
    include_untranslated: bool = False,
    include_fuzzy: bool = True,
) -> dict[str, str]:
    entries: dict[str, str] = {}
    for msgid, msgstr, is_fuzzy in _iter_po_entries(path):
        if is_fuzzy and not include_fuzzy:
            if include_untranslated:
                entries[msgid] = ""
        elif msgstr or include_untranslated:
            entries[msgid] = msgstr
    return entries


def _po_fuzzy_msgids(path: Path) -> set[str]:
    return {msgid for msgid, _msgstr, is_fuzzy in _iter_po_entries(path) if is_fuzzy and msgid}


def _function_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _function_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _translatable_source_messages() -> dict[str, list[str]]:
    messages: dict[str, list[str]] = {}
    for key, value in _manifest_values().items():
        messages.setdefault(value, []).append(f"manifest.ini:{key}")
    for source_dir in TRANSLATABLE_SOURCE_DIRS:
        for path in sorted(source_dir.rglob("*.py")):
            if any(part in SKIPPED_SOURCE_PARTS for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except SyntaxError as exc:
                raise ValueError(f"Could not parse {_relative_to_addon(path)}: {exc}") from exc
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _function_name(node.func) != "_":
                    continue
                if (
                    not node.args
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    continue
                message = node.args[0].value
                if not message:
                    continue
                location = _relative_to_addon(path).as_posix()
                messages.setdefault(message, []).append(f"{location}:{node.lineno}")
    return messages


def _manifest_version() -> str:
    text = MANIFEST_SOURCE.read_text(encoding="utf-8-sig")
    match = re.search(r"^version\s*=\s*([^\r\n#]+)", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"Manifest version could not be read from {_relative_to_addon(MANIFEST_SOURCE)}")
    version = match.group(1).strip().strip("\"'")
    if not version:
        raise ValueError(f"Manifest version is empty in {_relative_to_addon(MANIFEST_SOURCE)}")
    return version


def _po_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
    )


def _po_quoted_lines(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ['""']
    parts = normalized.splitlines(keepends=True)
    if len(parts) == 1:
        return [f'"{_po_escape(normalized)}"']
    return ['""'] + [f'"{_po_escape(part)}"' for part in parts]


def _append_po_string(lines: list[str], keyword: str, value: str) -> None:
    quoted_lines = _po_quoted_lines(value)
    lines.append(f"{keyword} {quoted_lines[0]}")
    lines.extend(quoted_lines[1:])


def _build_po_header(
    comments: list[str] | None = None,
    language: str = "",
) -> list[str]:
    version = _manifest_version()
    lines: list[str] = []
    if comments:
        lines.extend(comments)
    lines.extend(
        [
            'msgid ""',
            'msgstr ""',
            f'"Project-Id-Version: Google TTS For NVDA {_po_escape(version)}\\n"',
            '"Report-Msgid-Bugs-To: \\n"',
            '"POT-Creation-Date: YEAR-MO-DA HO:MI+ZONE\\n"',
            '"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"',
            '"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"',
            '"Language-Team: LANGUAGE <LL@li.org>\\n"',
            f'"Language: {language}\\n"',
            '"MIME-Version: 1.0\\n"',
            '"Content-Type: text/plain; charset=UTF-8\\n"',
            '"Content-Transfer-Encoding: 8bit\\n"',
        ]
    )
    return lines


def _extract_po_header_comments(po_path: Path) -> list[str]:
    comments: list[str] = []
    if not po_path.is_file():
        return comments
    try:
        for raw_line in po_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                comments.append(line)
            else:
                break
    except Exception:
        pass
    return comments


def _merge_po_header(
    old_header_str: str,
    language: str,
    comments: list[str] | None = None,
    default_comments: list[str] | None = None,
) -> list[str]:
    version = _manifest_version()
    lines: list[str] = []
    fallback_comments = default_comments or [
        "# Google TTS For NVDA translation.",
        "# Copyright (C) 2026 Google TTS For NVDA contributors",
        "# This file is distributed under the same license as the add-on.",
        "#",
    ]
    if comments:
        lines.extend(comments)
    else:
        lines.extend(fallback_comments)
    lines.extend(
        [
            'msgid ""',
            'msgstr ""',
        ]
    )
    if not old_header_str:
        return _build_po_header(comments=comments or fallback_comments, language=language)

    header_dict: dict[str, str] = {}
    for hline in old_header_str.splitlines():
        if ":" in hline:
            k, v = hline.split(":", 1)
            header_dict[k.strip()] = v.strip()

    header_dict["Project-Id-Version"] = f"Google TTS For NVDA {_po_escape(version)}"
    header_dict.setdefault("Report-Msgid-Bugs-To", "")
    header_dict.setdefault("POT-Creation-Date", "YEAR-MO-DA HO:MI+ZONE")
    header_dict.setdefault("PO-Revision-Date", "YEAR-MO-DA HO:MI+ZONE")
    header_dict.setdefault("Last-Translator", "FULL NAME <EMAIL@ADDRESS>")
    header_dict.setdefault("Language-Team", "LANGUAGE <LL@li.org>")
    header_dict["Language"] = language
    header_dict["MIME-Version"] = "1.0"
    header_dict["Content-Type"] = "text/plain; charset=UTF-8"
    header_dict["Content-Transfer-Encoding"] = "8bit"

    ordered_keys = [
        "Project-Id-Version",
        "Report-Msgid-Bugs-To",
        "POT-Creation-Date",
        "PO-Revision-Date",
        "Last-Translator",
        "Language-Team",
        "Language",
        "MIME-Version",
        "Content-Type",
        "Content-Transfer-Encoding",
        "Plural-Forms",
    ]
    for k in ordered_keys:
        if k in header_dict:
            lines.append(f'"{k}: {header_dict[k]}\\n"')
    for k, v in header_dict.items():
        if k not in ordered_keys:
            lines.append(f'"{k}: {v}\\n"')
    return lines


def _write_pot(messages: dict[str, list[str]], pot_path: Path | None = None) -> Path:
    target_pot = POT_PATH if pot_path is None else pot_path
    lines = _build_po_header(
        comments=[
            "# Google TTS For NVDA translation template.",
            "# Copyright (C) 2026 Google TTS For NVDA contributors",
            "# This file is distributed under the same license as the add-on.",
            "#",
        ]
    )
    for msgid, locations in sorted(messages.items(), key=lambda item: item[0].lower()):
        lines.append("")
        for location in sorted(locations):
            lines.append(f"#: {location}")
        _append_po_string(lines, "msgid", msgid)
        lines.append('msgstr ""')
    _atomic_write_text(target_pot, "\n".join(lines) + "\n", prefix=".nvda-pot-")
    return target_pot


def _find_gettext_tool(
    name: str,
    poedit_rel_path: Path,
    configured_path: Path | None = None,
) -> Path | None:
    if configured_path is not None:
        path = configured_path.expanduser().resolve()
        return path if path.is_file() else None
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    candidates: list[Path] = []
    for variable_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(variable_name)
        if root:
            candidates.append(Path(root) / poedit_rel_path)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / poedit_rel_path)
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def _find_msgmerge(configured_path: Path | None = None) -> Path | None:
    return _find_gettext_tool("msgmerge", POEDIT_MSGMERGE_RELATIVE_PATH, configured_path)


def _find_msgfmt(configured_path: Path | None = None) -> Path | None:
    return _find_gettext_tool("msgfmt", POEDIT_MSGFMT_RELATIVE_PATH, configured_path)


def _check_po_syntax_with_msgfmt(po_path: Path, msgfmt_path: Path | None = None) -> list[str]:
    """Check the syntax of a PO file using msgfmt."""
    actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
    if actual_msgfmt is None or not actual_msgfmt.is_file() or not po_path.is_file():
        return []
    try:
        content = po_path.read_text(encoding="utf-8-sig", errors="replace")
        if "charset=" not in content.lower():
            return []
    except Exception:
        return []
    try:
        result = subprocess.run(
            [str(actual_msgfmt), "-o", os.devnull, str(po_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            output = result.stderr.strip().replace("\r\n", "\n")
            if output:
                error_lines = [
                    line.strip()
                    for line in output.splitlines()
                    if line.strip() and not line.strip().lower().startswith(("cảnh báo: ", "warning: "))
                ]
                if not error_lines:
                    error_lines = [line.strip() for line in output.splitlines() if line.strip()]
                return [f"{po_path.name}: msgfmt syntax error: {line}" for line in error_lines]
            return [f"{po_path.name}: msgfmt reported syntax error (exit code {result.returncode})"]
        return []
    except Exception as exc:
        return [f"{po_path.name}: could not run msgfmt: {exc}"]


def _check_po_syntax_fallback(po_path: Path) -> list[str]:
    """Pure-Python PO syntax validation when msgfmt is unavailable."""
    if not po_path.is_file():
        return []
    errors: list[str] = []
    seen_msgids: set[str] = set()
    try:
        for msgid, _msgstr, _fuzzy in _iter_po_entries(po_path):
            if not msgid:
                continue
            if msgid in seen_msgids:
                errors.append(f"{po_path.name}: duplicate message definition for {_message_preview(msgid)!r}")
            else:
                seen_msgids.add(msgid)
    except Exception as exc:
        errors.append(f"{po_path.name}: PO syntax error: {exc}")
    return errors


def _purge_obsolete_po_entries(text: str) -> tuple[str, int]:
    if not text.strip():
        return "", 0
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n{2,}", normalized.strip())
    kept: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        if any(
            line.startswith("#~")
            and (line.startswith(("#~ msgctxt", "#~ msgid")) or bool(re.match(r"^#~\s*(?:msgctxt|msgid)\b", line)))
            for line in lines
        ):
            removed += 1
            continue
        kept.append(paragraph)
    if not kept:
        return "", removed
    return "\n\n".join(kept).rstrip() + "\n", removed


def _run_msgmerge(
    msgmerge_path: Path,
    po_path: Path,
    pot_path: Path,
    output_path: Path,
    label: str,
) -> int:
    result = subprocess.run(
        [
            str(msgmerge_path),
            "--no-fuzzy-matching",
            "--output-file",
            str(output_path),
            str(po_path),
            str(pot_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"msgmerge failed for {label}: {detail}")
    merged_text = output_path.read_text(encoding="utf-8-sig")
    merged_text, removed_count = _purge_obsolete_po_entries(merged_text)
    output_path.write_text(merged_text, encoding="utf-8", newline="\n")
    return removed_count


def _verify_merged_po(
    output_path: Path,
    required_msgids: set[str],
    old_msgids: set[str],
    entity_name: str,
    category: str = "catalog",
    string_type: str = "source",
) -> tuple[int, int]:
    merged_catalog = _parse_po(output_path, include_untranslated=True)
    actual_msgids = set(merged_catalog) - {""}
    if actual_msgids != required_msgids:
        missing = sorted(required_msgids - actual_msgids)
        unexpected = sorted(actual_msgids - required_msgids)
        raise RuntimeError(
            f"Merged {category} verification failed for {entity_name}: "
            f"missing={len(missing)}, unexpected={len(unexpected)}"
        )
    new_msgids = required_msgids - old_msgids
    nonempty_new = sorted(msgid for msgid in new_msgids if merged_catalog.get(msgid, ""))
    if nonempty_new:
        raise RuntimeError(
            f"Merged {category} verification failed for {entity_name}: "
            f"{len(nonempty_new)} new {string_type} strings received non-empty translations"
        )
    preserved = len(required_msgids & old_msgids)
    return preserved, len(new_msgids)


def _update_po_from_template(
    language_dir: Path,
    msgmerge_path: Path | None,
    required_messages: dict[str, list[str]],
) -> tuple[int, int, int]:
    if language_dir.name == "en":
        raise ValueError("en: English is the source language; cannot update PO file.")
    po_path = language_dir / "LC_MESSAGES" / "nvda.po"
    if not po_path.is_file():
        (language_dir / "LC_MESSAGES").mkdir(parents=True, exist_ok=True)
        if not POT_PATH.is_file():
            _write_pot(required_messages)
        pot_text = POT_PATH.read_text(encoding="utf-8-sig")
        po_text = pot_text.replace('"Language: \\n"', f'"Language: {language_dir.name}\\n"')
        _atomic_write_text(po_path, po_text, prefix=".nvda-po-init-")

    old_catalog = _parse_po(po_path, include_untranslated=True, include_fuzzy=True)
    if not POT_PATH.is_file():
        _write_pot(required_messages)
    old_msgids = set(old_catalog) - {""}
    required_msgids = set(required_messages)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=po_path.parent,
            prefix=".nvda-po-update-",
            suffix=".tmp",
            delete=False,
        ) as temporary_stream:
            temporary_path = Path(temporary_stream.name)

        removed_count = 0
        if msgmerge_path is not None:
            removed_count = _run_msgmerge(msgmerge_path, po_path, POT_PATH, temporary_path, language_dir.name)
        else:
            old_comments = _extract_po_header_comments(po_path)
            lines = _merge_po_header(old_catalog.get("", ""), language=language_dir.name, comments=old_comments)
            old_fuzzy_msgids = _po_fuzzy_msgids(po_path)
            for msgid, locations in sorted(required_messages.items(), key=lambda item: item[0].lower()):
                if not msgid:
                    continue
                lines.append("")
                if msgid in old_fuzzy_msgids:
                    lines.append("#, fuzzy")
                for loc in sorted(locations):
                    lines.append(f"#: {loc}")
                _append_po_string(lines, "msgid", msgid)
                msgstr = old_catalog.get(msgid, "")
                _append_po_string(lines, "msgstr", msgstr)
            temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            removed_count = len(old_msgids - required_msgids)

        preserved_count, new_count = _verify_merged_po(
            temporary_path,
            required_msgids,
            old_msgids,
            language_dir.name,
            category="catalog",
            string_type="source",
        )
        temporary_path.replace(po_path)
        temporary_path = None
        return preserved_count, new_count, removed_count
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _format_set(message: str) -> set[str]:
    fields: set[str] = set()
    try:
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(message):
            if field_name:
                fields.add("{" + field_name.split(".", 1)[0].split("[", 1)[0] + "}")
    except ValueError:
        return set(PLACEHOLDER_RE.findall(message))
    return fields


def _get_interpolations(text: str) -> tuple[list[str], set[str], set[str], int]:
    cleaned_percent = text.replace("%%", "")
    unnamed_percent = RE_UNNAMED_PERCENT.findall(cleaned_percent)
    named_percent = set(RE_NAMED_PERCENT.findall(cleaned_percent))
    brace_formats: set[str] = set()
    unnumbered_braces = 0
    cleaned_braces = text.replace("{{", "").replace("}}", "")
    for m in RE_FORMAT.finditer(cleaned_braces):
        field_name = m.group(1).split(".", 1)[0].split("[", 1)[0]
        if field_name:
            brace_formats.add("{" + field_name + "}")
        else:
            unnumbered_braces += 1
    return unnamed_percent, named_percent, brace_formats, unnumbered_braces


def _check_format_interpolations(msgid: str, msgstr: str) -> list[str]:
    if not msgstr.strip():
        return []
    id_unnamed, id_named, id_braces, id_empty = _get_interpolations(msgid)
    str_unnamed, str_named, str_braces, str_empty = _get_interpolations(msgstr)
    alerts: list[str] = []

    if id_unnamed != str_unnamed:
        if id_unnamed:
            alerts.append(f"unnamed percent interpolations differ: expected {id_unnamed}, got {str_unnamed}")
        else:
            alerts.append(f"unexpected presence of unnamed percent interpolations: {str_unnamed}")

    missing_named = id_named - str_named
    if missing_named:
        alerts.append(f"missing named percent interpolation: {sorted(missing_named)}")
    extra_named = str_named - id_named
    if extra_named:
        alerts.append(f"extra named percent interpolation: {sorted(extra_named)}")

    missing_braces = id_braces - str_braces
    if missing_braces:
        alerts.append(f"missing brace format interpolation: {sorted(missing_braces)}")
    extra_braces = str_braces - id_braces
    if extra_braces:
        alerts.append(f"extra brace format interpolation: {sorted(extra_braces)}")

    if id_empty != str_empty:
        if id_empty:
            alerts.append(f"unnumbered brace format count mismatch: expected {id_empty}, got {str_empty}")
        else:
            alerts.append(f"unexpected presence of unnumbered brace format: got {str_empty}")

    return alerts


def _check_html_tag_interpolations(msgid: str, msgstr: str) -> list[str]:
    if not msgstr.strip():
        return []
    alerts: list[str] = []

    open_tags = OPEN_HTML_TAG_RE.findall(msgstr)
    close_tags = CLOSE_HTML_TAG_RE.findall(msgstr)

    # 1. Disallowed dangerous tags and event handlers check
    for tag in open_tags + close_tags:
        if tag.lower() in DANGEROUS_HTML_TAGS:
            alerts.append(f"disallowed HTML tag: <{tag.lower()}>")

    if EVENT_HANDLER_ATTR_RE.search(msgstr) or EVENT_HANDLER_ATTR_RE.search(html.unescape(msgstr)):
        alerts.append("disallowed inline event handler in HTML tag")

    if STYLE_ATTR_RE.search(msgstr) or STYLE_ATTR_RE.search(html.unescape(msgstr)):
        alerts.append("disallowed inline style attribute in HTML tag")

    open_comments = len(re.findall(r"<!--", msgstr))
    close_comments = len(re.findall(r"-->", msgstr))
    if open_comments != close_comments:
        alerts.append(f"unbalanced HTML comment: {open_comments} open vs {close_comments} close")

    # 2. Balanced count check for each tag
    all_tag_names = set(t.lower() for t in open_tags + close_tags) - VOID_HTML_TAGS
    for tag in sorted(all_tag_names):
        o = sum(1 for t in open_tags if t.lower() == tag)
        c = sum(1 for t in close_tags if t.lower() == tag)
        if o != c:
            alerts.append(f"unbalanced <{tag}> tag: {o} open vs {c} close")

    # 3. Stack-based nesting order check
    stack: list[str] = []
    for m in HTML_TAG_TOKEN_RE.finditer(msgstr):
        is_close = bool(m.group(1))
        tag_name = m.group(2).lower()
        if tag_name in VOID_HTML_TAGS:
            if is_close:
                alerts.append(f"unexpected closing tag for void element </{tag_name}>")
            continue
        if not is_close:
            stack.append(tag_name)
        else:
            if not stack:
                alerts.append(f"closing </{tag_name}> tag without matching opening tag")
            else:
                top = stack.pop()
                if top != tag_name:
                    alerts.append(f"mismatched closing tag: expected </{top}>, got </{tag_name}>")

    id_open = [t.lower() for t in OPEN_HTML_TAG_RE.findall(msgid) if t.lower() not in VOID_HTML_TAGS]
    str_open = [t.lower() for t in open_tags if t.lower() not in VOID_HTML_TAGS]

    # 4. Missing tags and unexpected extra tags
    missing_tags = set(id_open) - set(str_open)
    if missing_tags:
        alerts.append(f"missing HTML tag: {sorted(missing_tags)}")

    extra_tags = set(str_open) - set(id_open)
    if extra_tags:
        alerts.append(f"unexpected HTML tag: {sorted(extra_tags)}")

    # 5. Strict tags count check
    for strict_tag in ("a", "code", "kbd"):
        id_cnt = id_open.count(strict_tag)
        str_cnt = str_open.count(strict_tag)
        if id_cnt != str_cnt:
            alerts.append(f"<{strict_tag}> tag count mismatch: expected {id_cnt}, got {str_cnt}")

    for other_tag in ("strong", "em", "b", "i"):
        id_cnt = id_open.count(other_tag)
        str_cnt = str_open.count(other_tag)
        if str_cnt < id_cnt:
            alerts.append(f"missing <{other_tag}> tag: expected at least {id_cnt}, got {str_cnt}")

    # 6. Href check and scheme safety check
    id_hrefs = sorted(html.unescape(h).strip() for h in _extract_hrefs(msgid))
    str_hrefs = sorted(html.unescape(h).strip() for h in _extract_hrefs(msgstr))
    if id_hrefs != str_hrefs:
        alerts.append(f"<a> href mismatch: expected {id_hrefs}, got {str_hrefs}")

    for href in str_hrefs:
        unescaped_href = html.unescape(href)
        cleaned_href = re.sub(r"[\s\x00-\x1f]", "", unescaped_href).lower()
        if any(cleaned_href.startswith(prefix) for prefix in ("javascript:", "data:", "vbscript:")):
            alerts.append(f"unsafe <a> href scheme: {href!r}")

    return alerts


def _message_preview(message: str, limit: int = 120) -> str:
    preview = message.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if len(preview) > limit:
        preview = preview[: limit - 3] + "..."
    return preview


def _check_string_interpolations(
    msgid: str,
    msgstr: str,
    target_label: str,
    lang_name: str,
) -> list[str]:
    alerts: list[str] = []
    for err in _check_format_interpolations(msgid, msgstr):
        alerts.append(f"{lang_name}: placeholder mismatch for {target_label}: {err}")
    if "<" in msgid or "<" in msgstr:
        for err in _check_html_tag_interpolations(msgid, msgstr):
            alerts.append(f"{lang_name}: HTML tag issue for {target_label}: {err}")
    return alerts


def _check_catalog(
    language_dir: Path,
    catalog: dict[str, str],
    checks: set[str] | None = None,
    required_messages: dict[str, list[str]] | None = None,
    fuzzy_msgids: set[str] | None = None,
) -> list[str]:
    active_checks = DEFAULT_UI_CHECKS if checks is None else checks
    errors: list[str] = []
    manifest_values = _manifest_values()
    checked_msgids: set[str] = set()
    if CHECK_MANIFEST in active_checks:
        for key in MANIFEST_KEYS:
            msgid = manifest_values.get(key)
            if not msgid:
                errors.append(f"{language_dir.name}: manifest field {key!r} could not be read.")
                continue
            msgstr = catalog.get(msgid, "")
            if not msgstr.strip():
                errors.append(
                    f"{language_dir.name}: missing translation for manifest {key}: {_message_preview(msgid)!r}"
                )
                continue
            if CHECK_PLACEHOLDERS in active_checks:
                errors.extend(_check_string_interpolations(msgid, msgstr, f"manifest {key}", language_dir.name))
                checked_msgids.add(msgid)
    if CHECK_PLACEHOLDERS in active_checks:
        for msgid, msgstr in catalog.items():
            if msgid and msgid not in checked_msgids:
                errors.extend(
                    _check_string_interpolations(msgid, msgstr, repr(_message_preview(msgid)), language_dir.name)
                )
    if CHECK_UI in active_checks and required_messages is not None:
        for msgid, locations in sorted(required_messages.items()):
            if not catalog.get(msgid, "").strip():
                errors.append(
                    f"{language_dir.name}: missing translation for {_message_preview(msgid)!r} "
                    f"at {', '.join(locations[:3])}"
                )
    if CHECK_OBSOLETE in active_checks and required_messages is not None:
        current_msgids = set(required_messages)
        for msgid in sorted(catalog):
            if not msgid or msgid in current_msgids:
                continue
            errors.append(f"{language_dir.name}: obsolete source string in nvda.po: {_message_preview(msgid)!r}")
    if CHECK_FUZZY in active_checks and fuzzy_msgids:
        for msgid in sorted(fuzzy_msgids):
            errors.append(f"{language_dir.name}: fuzzy translation requires review for {_message_preview(msgid)!r}")
    return errors


def _compile_mo(catalog: dict[str, str], output_path: Path) -> None:
    keys = sorted(catalog)
    ids = [key.encode("utf-8") for key in keys]
    strs = [catalog[key].encode("utf-8") for key in keys]

    key_table_offset = 7 * 4
    value_table_offset = key_table_offset + len(keys) * 8
    string_offset = value_table_offset + len(keys) * 8

    key_offsets: list[tuple[int, int]] = []
    current_offset = string_offset
    for value in ids:
        key_offsets.append((len(value), current_offset))
        current_offset += len(value) + 1

    value_offsets: list[tuple[int, int]] = []
    for value in strs:
        value_offsets.append((len(value), current_offset))
        current_offset += len(value) + 1

    data = bytearray()
    data.extend(
        struct.pack(
            "<Iiiiiii",
            0x950412DE,
            0,
            len(keys),
            key_table_offset,
            value_table_offset,
            0,
            0,
        )
    )
    for length, offset in key_offsets:
        data.extend(struct.pack("<ii", length, offset))
    for length, offset in value_offsets:
        data.extend(struct.pack("<ii", length, offset))
    for value in ids:
        data.extend(value + b"\0")
    for value in strs:
        data.extend(value + b"\0")

    _atomic_write_bytes(output_path, bytes(data), prefix=".nvda-mo-")


def _compile_mo_file(
    po_path: Path,
    output_mo_path: Path,
    catalog: dict[str, str],
    msgfmt_path: Path | None = None,
) -> None:
    """Compile a PO file to MO, preferring msgfmt if available with pure-Python fallback."""
    actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
    if actual_msgfmt is not None and actual_msgfmt.is_file() and po_path.is_file():
        tmp_mo: Path | None = None
        try:
            output_mo_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_mo = output_mo_path.with_name(f".tmp-{output_mo_path.name}")
            res = subprocess.run(
                [str(actual_msgfmt), "-o", str(tmp_mo), str(po_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if res.returncode == 0 and tmp_mo.is_file():
                tmp_mo.replace(output_mo_path)
                tmp_mo = None
                return
        except Exception:
            pass
        finally:
            if tmp_mo is not None:
                tmp_mo.unlink(missing_ok=True)
    _compile_mo(catalog, output_mo_path)


def _manifest_values() -> dict[str, str]:
    text = MANIFEST_SOURCE.read_text(encoding="utf-8-sig")
    values: dict[str, str] = {}
    for key in MANIFEST_KEYS:
        match = re.search(
            rf'^{re.escape(key)}\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')',
            text,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            values[key] = match.group(1) if match.group(1) is not None else match.group(2)
        else:
            match_single = re.search(rf'^{re.escape(key)}\s*=\s*(?:"(.*?)"|\'(.*?)\')', text, re.MULTILINE)
            if match_single:
                values[key] = match_single.group(1) if match_single.group(1) is not None else match_single.group(2)
            else:
                match_unquoted = re.search(rf"^{re.escape(key)}\s*=\s*([^\r\n#]+)", text, re.MULTILINE)
                if match_unquoted:
                    values[key] = match_unquoted.group(1).strip()
    return values


def _quote_manifest_value(value: str) -> str:
    cleaned = value.replace("\r", "").replace("\n", " ")
    return '"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_translated_manifest(language_dir: Path, catalog: dict[str, str]) -> None:
    values = _manifest_values()
    lines: list[str] = []
    if "summary" in values:
        summary = catalog.get(values["summary"])
        if not summary or not summary.strip():
            summary = values["summary"]
        lines.append(f"summary = {_quote_manifest_value(summary)}\n")
    if "description" in values:
        description = catalog.get(values["description"])
        if not description or not description.strip():
            description = values["description"]
        description = description.replace("\r\n", "\n").replace("\r", "\n")
        description = description.replace('"""', r"\"\"\"")
        if description.endswith('"'):
            description += "\n"
        lines.append(f'description = """{description}"""\n')
    if "changelog" in values:
        changelog = catalog.get(values["changelog"])
        if not changelog or not changelog.strip():
            changelog = values["changelog"]
        changelog = changelog.replace("\r\n", "\n").replace("\r", "\n")
        changelog = changelog.replace('"""', r"\"\"\"")
        if changelog.endswith('"'):
            changelog += "\n"
        lines.append(f'changelog = """{changelog}"""\n')
    _atomic_write_text(language_dir / "manifest.ini", "".join(lines), prefix=".manifest-")


def _supported_nvda_languages(locale_dir: Path) -> set[str] | None:
    if not locale_dir.is_dir():
        return None
    return {path.name for path in locale_dir.iterdir() if path.is_dir()}


def _supported_nvda_languages_from_dirs(locale_dirs: list[Path]) -> tuple[set[str] | None, list[Path]]:
    supported: set[str] = set()
    found_dirs: list[Path] = []
    for locale_dir in locale_dirs:
        languages = _supported_nvda_languages(locale_dir)
        if languages is None:
            continue
        found_dirs.append(locale_dir)
        supported.update(languages)
    if not found_dirs:
        return None, []
    supported.add("en")
    return supported, found_dirs


def _normalize_language_code(language: str) -> str:
    parts = language.strip().replace("-", "_").split("_", 1)
    if len(parts) == 1:
        return parts[0].lower()
    return f"{parts[0].lower()}_{parts[1].upper()}"


def _resolve_target_directories(
    requested_languages: list[str] | None,
    base_dir: Path,
    folder_type_name: str,
    en_source_msg: str,
    allow_create: bool = False,
    include_en: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if requested_languages:
        target: list[str] = []
        for raw in requested_languages:
            codes = [c.strip() for c in raw.split(",")] if "," in raw else [raw]
            for language in codes:
                norm = _normalize_language_code(language)
                if not norm:
                    errors.append("empty language code.")
                    continue
                if norm == "en":
                    if include_en:
                        if "en" not in target:
                            target.append("en")
                    else:
                        errors.append(f"en: {en_source_msg}")
                    continue
                target_dir = base_dir / norm
                if not target_dir.is_dir():
                    if allow_create:
                        try:
                            target_dir.mkdir(parents=True, exist_ok=True)
                        except OSError as exc:
                            errors.append(f"{norm}: could not create {folder_type_name} folder: {exc}")
                            continue
                    else:
                        errors.append(f"{norm}: {folder_type_name} folder is missing: {_relative_to_addon(target_dir)}")
                        continue
                if norm not in target:
                    target.append(norm)
        return target, errors

    all_langs = _child_directories(base_dir)
    if include_en:
        return ["en"] + [lang for lang in all_langs if lang != "en"], []
    return [lang for lang in all_langs if lang != "en"], []


def _child_directories(parent: Path) -> list[str]:
    if not parent.is_dir():
        return []
    return sorted(path.name for path in parent.iterdir() if path.is_dir() and path.name != "en")


def _language_dirs(
    requested_languages: list[str] | None,
    allow_create: bool = False,
) -> tuple[list[Path], list[str]]:
    names, errors = _resolve_target_directories(
        requested_languages,
        base_dir=LOCALE_DIR,
        folder_type_name="translation",
        en_source_msg="English is the source language; choose another locale to translate.",
        allow_create=allow_create,
        include_en=False,
    )
    return [LOCALE_DIR / name for name in names], errors


def _addon_languages() -> list[str]:
    return _child_directories(LOCALE_DIR)


def _addon_doc_languages() -> list[str]:
    return _child_directories(DOC_DIR)


def _all_doc_languages() -> list[str]:
    return _addon_doc_languages()


def _doc_target_languages(
    requested_languages: list[str] | None,
    allow_create: bool = False,
    include_en: bool = False,
) -> tuple[list[str], list[str]]:
    return _resolve_target_directories(
        requested_languages,
        base_dir=DOC_DIR,
        folder_type_name="documentation",
        en_source_msg="English is the documentation source; choose another locale to translate.",
        allow_create=allow_create,
        include_en=include_en,
    )


def _is_rtl_language(language: str) -> bool:
    base = language.strip().replace("-", "_").split("_")[0].lower()
    return base in RTL_LANG_CODES


def _format_html_attrs(attrs: list[tuple[str, str | None]] | dict[str, str | None]) -> str:
    items = attrs.items() if isinstance(attrs, dict) else attrs
    parts: list[str] = []
    for k, v in items:
        if v is None:
            parts.append(f" {k}")
        else:
            escaped_v = html.escape(v, quote=True)
            parts.append(f' {k}="{escaped_v}"')
    return "".join(parts)


class _DocHtmlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.segments: list[tuple[int, str]] = []
        self.stack: list[str] = []
        self.current_data: list[str] = []
        self.current_start_line = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in DOC_STRUCTURAL_CONTAINER_TAGS:
            if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
                text = "".join(self.current_data).strip()
                if text:
                    self.segments.append((self.current_start_line, text))
                self.current_data = []
            self.stack.append(tag_lower)
        elif tag_lower in TRANSLATABLE_DOC_TAGS:
            if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
                text = "".join(self.current_data).strip()
                if text:
                    self.segments.append((self.current_start_line, text))
                self.current_data = []
            self.stack.append(tag_lower)
            self.current_start_line = self.getpos()[0]
        elif self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            attr_str = _format_html_attrs(attrs)
            self.current_data.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_str = _format_html_attrs(attrs)
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            if tag.lower() in VOID_HTML_TAGS:
                self.current_data.append(f"<{tag}{attr_str}>")
            else:
                self.current_data.append(f"<{tag}{attr_str}></{tag}>")
        else:
            self.handle_starttag(tag, attrs)
            if tag.lower() not in VOID_HTML_TAGS:
                self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in VOID_HTML_TAGS:
            return
        if self.stack and self.stack[-1] == tag_lower:
            if tag_lower in TRANSLATABLE_DOC_TAGS:
                text = "".join(self.current_data).strip()
                if text:
                    self.segments.append((self.current_start_line, text))
                self.current_data = []
            self.stack.pop()
            if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
                self.current_start_line = self.getpos()[0]
        elif self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"<!--{data}-->")

    def close(self) -> None:
        if self.current_data and self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            text = "".join(self.current_data).strip()
            if text:
                self.segments.append((self.current_start_line, text))
            self.current_data = []
        super().close()


def _extract_doc_segments(html_text: str) -> list[tuple[int, str]]:
    extractor = _DocHtmlExtractor()
    extractor.feed(html_text)
    extractor.close()
    return extractor.segments


class _DocHtmlRebuilder(HTMLParser):
    def __init__(self, catalog: dict[str, str], target_lang: str, is_rtl: bool) -> None:
        super().__init__(convert_charrefs=False)
        self.catalog = catalog
        self.target_lang = target_lang
        self.is_rtl = is_rtl
        self.output: list[str] = []
        self.stack: list[str] = []
        self.current_data: list[str] = []

    def _flush_translatable(self) -> None:
        raw = "".join(self.current_data)
        self.current_data = []
        stripped = raw.strip()
        if not stripped:
            self.output.append(raw)
            return

        l_ws = raw[: len(raw) - len(raw.lstrip())]
        r_ws = raw[len(raw.rstrip()) :]

        cand = self.catalog.get(stripped)
        translated = cand if (cand is not None and cand.strip()) else stripped
        self.output.append(f"{l_ws}{translated}{r_ws}")

    def handle_decl(self, decl: str) -> None:
        self.output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.output.append(f"<?{data}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "html":
            attr_dict = dict(attrs)
            attr_dict["lang"] = self.target_lang.replace("_", "-")
            if self.is_rtl:
                attr_dict["dir"] = "rtl"
            elif "dir" in attr_dict:
                del attr_dict["dir"]
            attr_str = _format_html_attrs(attr_dict)
            self.output.append(f"<{tag}{attr_str}>")
            return

        if tag_lower in DOC_STRUCTURAL_CONTAINER_TAGS:
            if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
                self._flush_translatable()
            self.stack.append(tag_lower)
            attr_str = _format_html_attrs(attrs)
            self.output.append(f"<{tag}{attr_str}>")
            return

        if tag_lower in TRANSLATABLE_DOC_TAGS:
            if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
                self._flush_translatable()
            self.stack.append(tag_lower)
            attr_str = _format_html_attrs(attrs)
            self.output.append(f"<{tag}{attr_str}>")
        elif self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            attr_str = _format_html_attrs(attrs)
            self.current_data.append(f"<{tag}{attr_str}>")
        else:
            attr_str = _format_html_attrs(attrs)
            self.output.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_str = _format_html_attrs(attrs)
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            if tag.lower() in VOID_HTML_TAGS:
                self.current_data.append(f"<{tag}{attr_str}>")
            else:
                self.current_data.append(f"<{tag}{attr_str}></{tag}>")
        else:
            if tag.lower() in VOID_HTML_TAGS:
                self.output.append(f"<{tag}{attr_str}>")
            else:
                self.output.append(f"<{tag}{attr_str}></{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in VOID_HTML_TAGS:
            return
        if self.stack and self.stack[-1] == tag_lower:
            if tag_lower in TRANSLATABLE_DOC_TAGS:
                self._flush_translatable()
            self.stack.pop()
            self.output.append(f"</{tag}>")
        elif self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"</{tag}>")
        else:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(data)
        else:
            self.output.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"&{name};")
        else:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"&#{name};")
        else:
            self.output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS:
            self.current_data.append(f"<!--{data}-->")
        else:
            self.output.append(f"<!--{data}-->")

    def close(self) -> None:
        if self.current_data:
            self._flush_translatable()
        super().close()


def _write_doc_pot(pot_path: Path | None = None) -> Path:
    target_pot = DOC_POT_PATH if pot_path is None else pot_path
    if not DOC_EN_PATH.is_file():
        raise RuntimeError(f"English source document not found: {_relative_to_addon(DOC_EN_PATH)}")
    html_text = DOC_EN_PATH.read_text(encoding="utf-8-sig")
    try:
        segments = _extract_doc_segments(html_text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse {_relative_to_addon(DOC_EN_PATH)}: {exc}") from exc

    aggregated: dict[str, list[int]] = {}
    for line_no, segment in segments:
        aggregated.setdefault(segment, []).append(line_no)

    lines = _build_po_header(
        comments=[
            "# Google TTS For NVDA documentation translation template.",
            "# Copyright (C) 2026 Google TTS For NVDA contributors",
            "# This file is distributed under the same license as the add-on.",
            "#",
        ]
    )

    en_rel = _relative_to_addon(DOC_EN_PATH).as_posix()
    for msgid, line_numbers in aggregated.items():
        lines.append("")
        loc_str = " ".join(f"{en_rel}:{ln}" for ln in line_numbers)
        lines.append(f"#: {loc_str}")
        _append_po_string(lines, "msgid", msgid)
        lines.append('msgstr ""')

    _atomic_write_text(target_pot, "\n".join(lines) + "\n", prefix=".doc-pot-")
    return target_pot


def _doc_segment_signature(s: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tags = tuple(t.lower() for t in OPEN_HTML_TAG_RE.findall(s))
    nums = tuple(re.findall(r"\b\d+(?:\.\d+)*\b", s))
    return (tags, nums)


def _match_doc_segments(en_segs: list[str], lang_segs: list[str]) -> list[tuple[str, str]]:
    if len(en_segs) == len(lang_segs):
        return list(zip(en_segs, lang_segs, strict=True))
    pairs: list[tuple[str, str]] = []
    en_sigs = [_doc_segment_signature(s) for s in en_segs]
    lang_sigs = [_doc_segment_signature(s) for s in lang_segs]
    matcher = difflib.SequenceMatcher(None, en_sigs, lang_sigs)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2), strict=True):
                pairs.append((en_segs[i], lang_segs[j]))
    return pairs


def _check_doc_segments(
    language: str,
    en_segs: list[str],
    lang_segs: list[str],
    doc_name: str,
    active_checks: set[str],
) -> list[str]:
    errors: list[str] = []
    if len(en_segs) != len(lang_segs) and CHECK_DOCS in active_checks:
        errors.append(
            f"{language}: segment count mismatch in {doc_name}: expected {len(en_segs)}, found {len(lang_segs)}"
        )
    if CHECK_PLACEHOLDERS in active_checks:
        for en_seg, lang_seg in _match_doc_segments(en_segs, lang_segs):
            for tag_err in _check_html_tag_interpolations(en_seg, lang_seg):
                errors.append(f"{language}: HTML tag issue in {doc_name} for {_message_preview(en_seg)!r}: {tag_err}")
            for interp_err in _check_format_interpolations(en_seg, lang_seg):
                errors.append(
                    f"{language}: placeholder mismatch in {doc_name} for {_message_preview(en_seg)!r}: {interp_err}"
                )
    return errors


def _extract_po_from_doc_html(en_html_path: Path, lang_html_path: Path, language: str, output_po_path: Path) -> bool:
    if language == "en" or not en_html_path.is_file() or not lang_html_path.is_file():
        return False
    try:
        if en_html_path.resolve() == lang_html_path.resolve():
            return False
    except Exception:
        pass
    try:
        en_ext_segments = _extract_doc_segments(en_html_path.read_text(encoding="utf-8-sig"))
        lang_ext_segments = _extract_doc_segments(lang_html_path.read_text(encoding="utf-8-sig"))

        en_segs = [s for _, s in en_ext_segments]
        lang_segs = [s for _, s in lang_ext_segments]

        catalog: dict[str, str] = {en: "" for en in en_segs}
        for en, lang in _match_doc_segments(en_segs, lang_segs):
            if not catalog[en] and lang.strip():
                catalog[en] = lang

        lines = _build_po_header(
            comments=[
                "# Google TTS For NVDA documentation translation.",
                "# Copyright (C) 2026 Google TTS For NVDA contributors",
                "# This file is distributed under the same license as the add-on.",
                "#",
            ],
            language=language,
        )

        en_rel = _relative_to_addon(en_html_path).as_posix()
        aggregated: dict[str, list[int]] = {}
        for line_no, segment in en_ext_segments:
            aggregated.setdefault(segment, []).append(line_no)

        for msgid, line_numbers in aggregated.items():
            lines.append("")
            loc_str = " ".join(f"{en_rel}:{ln}" for ln in line_numbers)
            lines.append(f"#: {loc_str}")
            _append_po_string(lines, "msgid", msgid)
            msgstr = catalog.get(msgid, "")
            _append_po_string(lines, "msgstr", msgstr)

        _atomic_write_text(output_po_path, "\n".join(lines) + "\n", prefix=".extract-po-")
        return True
    except Exception:
        output_po_path.unlink(missing_ok=True)
        return False


def _build_doc_from_po(language: str, po_path: Path, output_html_path: Path) -> tuple[bool, str]:
    if language == "en":
        return False, f"{language}: cannot build English documentation from PO"
    if not DOC_EN_PATH.is_file():
        return False, f"{language}: English source document missing: {_relative_to_addon(DOC_EN_PATH)}"
    try:
        if output_html_path.resolve() == DOC_EN_PATH.resolve():
            return False, f"{language}: cannot overwrite English source document"
    except Exception:
        pass
    try:
        catalog = _parse_po(po_path, include_fuzzy=False)
    except Exception as exc:
        return False, f"{language}: could not parse {_relative_to_addon(po_path)}: {exc}"
    try:
        en_html = DOC_EN_PATH.read_text(encoding="utf-8-sig")
        is_rtl = _is_rtl_language(language)
        rebuilder = _DocHtmlRebuilder(catalog, target_lang=language, is_rtl=is_rtl)
        rebuilder.feed(en_html)
        rebuilder.close()
        rebuilt_html = "".join(rebuilder.output)
        lxml_errors = _check_html_content_with_lxml(rebuilt_html, str(_relative_to_addon(output_html_path)))
        if lxml_errors:
            return False, f"{language}: rebuilt documentation HTML has syntax errors: {'; '.join(lxml_errors)}"
        _atomic_write_text(output_html_path, rebuilt_html, prefix=".build-doc-")
    except Exception as exc:
        return False, f"{language}: failed to rebuild documentation: {exc}"
    direction = "rtl" if is_rtl else "ltr"
    return (
        True,
        f"Generated {_relative_to_addon(output_html_path)} from readme.po (lang={language}, dir={direction})",
    )


def _update_doc_po_from_template(
    language: str,
    msgmerge_path: Path | None,
) -> tuple[int, int, int]:
    if language == "en":
        raise ValueError("en: English is the documentation source; cannot update PO file.")
    doc_dir = DOC_DIR / language
    if doc_dir.is_dir():
        for stale_mo in doc_dir.glob("*.mo"):
            stale_mo.unlink(missing_ok=True)
    po_path = doc_dir / "readme.po"
    html_path = doc_dir / "readme.html"

    if not po_path.is_file():
        extracted = False
        if (doc_dir / "readme.md").is_file() and language != "en":
            _convert_md_file_to_html(doc_dir / "readme.md", html_path, language)
        if html_path.is_file() and language != "en":
            extracted = _extract_po_from_doc_html(DOC_EN_PATH, html_path, language, po_path)
        if not extracted or not po_path.is_file():
            doc_dir.mkdir(parents=True, exist_ok=True)
            if not DOC_POT_PATH.is_file():
                _write_doc_pot()
            pot_text = DOC_POT_PATH.read_text(encoding="utf-8-sig")
            po_text = pot_text.replace('"Language: \\n"', f'"Language: {language}\\n"')
            _atomic_write_text(po_path, po_text, prefix=".readme-po-init-")

    old_catalog = _parse_po(po_path, include_untranslated=True, include_fuzzy=True)

    if not DOC_POT_PATH.is_file():
        _write_doc_pot()
    pot_catalog = _parse_po(DOC_POT_PATH, include_untranslated=True)
    required_msgids = set(pot_catalog.keys()) - {""}
    old_msgids = set(old_catalog.keys()) - {""}
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=po_path.parent,
            prefix=".readme-po-update-",
            suffix=".tmp",
            delete=False,
        ) as temporary_stream:
            temporary_path = Path(temporary_stream.name)

        removed_obsolete = 0
        if msgmerge_path is not None:
            removed_obsolete = _run_msgmerge(msgmerge_path, po_path, DOC_POT_PATH, temporary_path, language)
        else:
            old_comments = _extract_po_header_comments(po_path)
            lines = _merge_po_header(
                old_catalog.get("", ""),
                language=language,
                comments=old_comments,
                default_comments=[
                    "# Google TTS For NVDA documentation translation.",
                    "# Copyright (C) 2026 Google TTS For NVDA contributors",
                    "# This file is distributed under the same license as the add-on.",
                    "#",
                ],
            )
            en_rel = _relative_to_addon(DOC_EN_PATH).as_posix()
            old_fuzzy_msgids = _po_fuzzy_msgids(po_path)
            doc_segments: list[tuple[int, str]] = []
            if DOC_EN_PATH.is_file():
                with contextlib.suppress(Exception):
                    doc_segments = _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))
            doc_locations: dict[str, list[int]] = {}
            for line_no, segment in doc_segments:
                doc_locations.setdefault(segment, []).append(line_no)

            for msgid in pot_catalog:
                if not msgid:
                    continue
                lines.append("")
                if msgid in old_fuzzy_msgids:
                    lines.append("#, fuzzy")
                lns = doc_locations.get(msgid)
                if lns:
                    loc_str = " ".join(f"{en_rel}:{ln}" for ln in lns)
                    lines.append(f"#: {loc_str}")
                else:
                    lines.append(f"#: {en_rel}")
                _append_po_string(lines, "msgid", msgid)
                msgstr = old_catalog.get(msgid, "")
                _append_po_string(lines, "msgstr", msgstr)
            temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            removed_obsolete = len(old_msgids - required_msgids)

        preserved, added = _verify_merged_po(
            temporary_path,
            required_msgids,
            old_msgids,
            language,
            category="doc catalog",
            string_type="documentation",
        )
        temporary_path.replace(po_path)
        temporary_path = None
        return preserved, added, removed_obsolete
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Markdown <-> HTML documentation conversion
# ---------------------------------------------------------------------------

_DOC_MARKDOWN_EXTENSIONS: tuple[str, ...] = (
    # Supports tables, HTML mixed with markdown, code blocks, custom attributes and more
    "markdown.extensions.extra",
    # Used to preserve tabs in code blocks
    "pymdownx.superfences",
    # Allows TOC with [TOC]
    "markdown.extensions.toc",
    # Makes list behaviour better, including 2 space indents by default
    "mdx_truly_sane_lists",
    # Adds links to GitHub authors, issues and PRs
    "mdx_gh_links",
)

_DOC_MARKDOWN_EXTENSIONS_CONFIG: dict[str, dict[str, object]] = {
    "mdx_gh_links": {
        "user": "nguyenanhduc09",
        "repo": "Google-TTS-For-NVDA",
    },
    "pymdownx.superfences": {
        "preserve_tabs": True,
    },
}


def _create_doc_attribute_filter() -> dict[str, set[str]]:
    """Create attribute filter exceptions for HTML sanitization."""
    if not _NH3_AVAILABLE or nh3 is None:
        return {}
    allowed_attributes: dict[str, set[str]] = copy.deepcopy(nh3.ALLOWED_ATTRIBUTES)

    attributes_with_anchors = {"h1", "h2", "h3", "h4", "h5", "h6", "td"}
    attributes_with_class = {"div", "span", "a", "th", "td"}

    # Allow IDs for anchors
    for attr in attributes_with_anchors:
        allowed_attributes.setdefault(attr, set()).add("id")

    # Allow class for styling
    for attr in attributes_with_class:
        allowed_attributes.setdefault(attr, set()).add("class")

    # Allow rel and target on anchor tags
    allowed_attributes.setdefault("a", set()).update({"rel", "target"})

    allowed_attributes.setdefault("math", set()).add("display")
    return allowed_attributes


def _doc_attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Filter HTML attributes during sanitization."""
    if tag == "math" and attr == "display":
        return value if value == "block" else None
    return value


def _create_doc_tag_filter() -> set[str]:
    """Allowed HTML tags for documentation, explicitly supporting MathML, <kbd>, and document structure."""
    if not _NH3_AVAILABLE or nh3 is None:
        return set()
    return nh3.ALLOWED_TAGS | {
        "math",
        "mrow",
        "mfrac",
        "mi",
        "mn",
        "mo",
        "msub",
        "kbd",
        "html",
        "head",
        "body",
        "title",
        "meta",
    }


def _check_html_content_with_lxml(html_content: str, source_name: str = "HTML") -> list[str]:
    """Validate HTML content syntax using lxml if available."""
    if not _LXML_AVAILABLE or lxml is None:
        return []
    try:
        parser = lxml.etree.HTMLParser()
        lxml.html.fromstring(html_content, parser=parser)
        errors: list[str] = []
        for entry in parser.error_log:
            if entry.level_name in ("ERROR", "FATAL"):
                errors.append(
                    f"{source_name}:{entry.line}:{entry.column}: HTML syntax error ({entry.type_name}): {entry.message}"
                )
        return errors
    except Exception as exc:
        return [f"{source_name}: lxml failed to parse HTML: {exc}"]


RE_INLINE_MARKDOWNLINT_COMMENT = re.compile(r"^(.*?)(?:\s*<!--\s*markdownlint.*-->)(\s*)$")


def _preprocess_markdown_text(md_text: str) -> str:
    """Preprocess markdown lines such as removing inline markdownlint comments."""
    cleaned_lines = []
    for line in md_text.splitlines():
        cleaned_lines.append(RE_INLINE_MARKDOWNLINT_COMMENT.sub(r"\1\2", line))
    return "\n".join(cleaned_lines)


def _get_title_from_markdown(md_text: str) -> str:
    """Extract document title from markdown heading or KC:title comment."""
    kc_re = re.compile(r"^<!--\s*(?:KC:)?title:\s*(.+?)\s*-->$", re.IGNORECASE)
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = kc_re.match(stripped)
        if m:
            return m.group(1).strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


_RESOLVED_DOC_MARKDOWN_EXTENSIONS: list[str] | None = None


def _get_active_doc_markdown_extensions() -> list[object]:
    """Return active markdown extensions, validating available string extensions once and caching them."""
    global _RESOLVED_DOC_MARKDOWN_EXTENSIONS
    if _RESOLVED_DOC_MARKDOWN_EXTENSIONS is None:
        resolved: list[str] = []
        if _MARKDOWN_AVAILABLE and markdown is not None:
            for ext_name in _DOC_MARKDOWN_EXTENSIONS:
                try:
                    markdown.markdown("", extensions=[ext_name])
                    resolved.append(ext_name)
                except Exception:
                    continue
        _RESOLVED_DOC_MARKDOWN_EXTENSIONS = resolved

    active_extensions: list[object] = list(_RESOLVED_DOC_MARKDOWN_EXTENSIONS)
    try:
        from l2m4m import LaTeX2MathMLExtension

        active_extensions.append(LaTeX2MathMLExtension())
    except ImportError:
        pass
    return active_extensions


def _generate_doc_html_body(md: str) -> str:
    """Convert markdown text to sanitized HTML using markdown + nh3 pipeline."""
    if not _MARKDOWN_AVAILABLE or markdown is None:
        return _fallback_markdown_to_html_body(md)

    active_extensions = _get_active_doc_markdown_extensions()

    html_output = markdown.markdown(
        text=md,
        extensions=active_extensions,
        extension_configs=_DOC_MARKDOWN_EXTENSIONS_CONFIG,
    )

    if _NH3_AVAILABLE and nh3 is not None:
        allowed_attrs = _create_doc_attribute_filter()
        allowed_tags = _create_doc_tag_filter()
        html_output = nh3.clean(
            html_output,
            tags=allowed_tags,
            attributes=allowed_attrs,
            attribute_filter=_doc_attribute_filter,
            link_rel=None,
            strip_comments=False,
        )

    return html_output


class _DocHtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.stack: list[str] = []
        self.list_stack: list[dict[str, Any]] = []
        self.current_buf: list[str] = []
        self.link_stack: list[str] = []
        self.title: str = ""
        self.in_pre: bool = False
        self.pre_lang: str = ""
        self.in_table: bool = False
        self.table_rows: list[list[str]] = []
        self.table_alignments: list[str | None] = []
        self.current_table_row: list[str] = []

    def _flush_buf(self) -> str:
        text = "".join(self.current_buf)
        self.current_buf = []
        return text

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self.stack.append(tag_lower)
        attr_dict = dict(attrs)

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "dt", "dd", "title"):
            self.current_buf = []
        elif tag_lower in ("ul", "ol"):
            if self.stack and len(self.stack) >= 2 and self.stack[-2] == "li":
                text = self._flush_buf().strip()
                if text and self.list_stack:
                    info = self.list_stack[-1]
                    indent = "  " * (len(self.list_stack) - 1)
                    if not info.get("li_bullet_emitted", False):
                        if info.get("type") == "ol":
                            prefix = f"{indent}{info['index']}. "
                            info["index"] = int(str(info["index"])) + 1
                        else:
                            prefix = f"{indent}* "
                        self.output.append(f"{prefix}{text}\n")
                        info["li_bullet_emitted"] = True
                    else:
                        self.output.append(f"{indent}  {text}\n")
            self.list_stack.append({"type": tag_lower, "index": 1, "li_bullet_emitted": False})
        elif tag_lower == "li":
            self.current_buf = []
            if self.list_stack:
                self.list_stack[-1]["li_bullet_emitted"] = False
        elif tag_lower == "table":
            self.in_table = True
            self.table_rows = []
            self.table_alignments = []
        elif tag_lower == "tr":
            self.current_table_row = []
        elif tag_lower in ("th", "td"):
            self.current_buf = []
            if len(self.table_rows) == 0:
                align = (attr_dict.get("align") or "").lower()
                if not align:
                    style = attr_dict.get("style") or ""
                    m = re.search(r"text-align:\s*(left|center|right)", style, re.IGNORECASE)
                    if m:
                        align = m.group(1).lower()
                self.table_alignments.append(align if align in ("left", "center", "right") else None)
        elif tag_lower in ("strong", "b"):
            self.current_buf.append("**")
        elif tag_lower in ("em", "i"):
            self.current_buf.append("*")
        elif tag_lower == "code":
            if not self.in_pre:
                self.current_buf.append("`")
            else:
                cls = attr_dict.get("class") or ""
                m = re.search(r"\b(?:language-|lang-)(\w+)\b", cls)
                if m:
                    self.pre_lang = m.group(1)
        elif tag_lower == "pre":
            self.in_pre = True
            self.pre_lang = ""
            self.current_buf = []
        elif tag_lower == "kbd":
            self.current_buf.append("<kbd>")
        elif tag_lower == "a":
            href = attr_dict.get("href") or ""
            self.link_stack.append(href)
            self.current_buf.append("[")
        elif tag_lower == "hr":
            self.output.append("\n\n---\n\n")
        elif tag_lower == "br":
            self.current_buf.append("  \n")
        elif tag_lower == "img":
            alt = attr_dict.get("alt") or ""
            src = attr_dict.get("src") or ""
            self.current_buf.append(f"![{alt}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self.stack and self.stack[-1] == tag_lower:
            self.stack.pop()

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            text = self._flush_buf().strip()
            if text:
                self.output.append(f"\n\n{'#' * level} {text}\n\n")
        elif tag_lower == "p":
            text = self._flush_buf().strip()
            if text:
                if "blockquote" in self.stack:
                    lines = [f"> {line}" for line in text.splitlines()]
                    self.output.append("\n\n" + "\n".join(lines) + "\n\n")
                elif "dd" in self.stack:
                    self.output.append(f":   {text}\n\n")
                elif "dt" in self.stack:
                    self.output.append(f"\n\n{text}\n")
                elif "li" in self.stack and self.list_stack:
                    info = self.list_stack[-1]
                    indent = "  " * (len(self.list_stack) - 1)
                    if not info.get("li_bullet_emitted", False):
                        if info.get("type") == "ol":
                            prefix = f"{indent}{info['index']}. "
                            info["index"] = int(str(info["index"])) + 1
                        else:
                            prefix = f"{indent}* "
                        self.output.append(f"{prefix}{text}\n")
                        info["li_bullet_emitted"] = True
                    else:
                        self.output.append(f"\n{indent}  {text}\n")
                else:
                    self.output.append(f"\n\n{text}\n\n")
        elif tag_lower == "dt":
            text = self._flush_buf().strip()
            if text:
                self.output.append(f"\n\n{text}\n")
        elif tag_lower == "dd":
            text = self._flush_buf().strip()
            if text:
                self.output.append(f":   {text}\n\n")
        elif tag_lower == "li":
            text = self._flush_buf().strip()
            if text and self.list_stack:
                info = self.list_stack[-1]
                indent = "  " * (len(self.list_stack) - 1)
                if not info.get("li_bullet_emitted", False):
                    if info.get("type") == "ol":
                        prefix = f"{indent}{info['index']}. "
                        info["index"] = int(str(info["index"])) + 1
                    else:
                        prefix = f"{indent}* "
                    self.output.append(f"{prefix}{text}\n")
                    info["li_bullet_emitted"] = True
                else:
                    self.output.append(f"{indent}  {text}\n")
        elif tag_lower in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self.output.append("\n")
        elif tag_lower in ("th", "td"):
            cell_text = self._flush_buf().strip().replace("\r", "").replace("\n", " ").replace("|", r"\|")
            self.current_table_row.append(cell_text)
        elif tag_lower == "tr":
            if self.current_table_row:
                self.table_rows.append(self.current_table_row)
            self.current_table_row = []
        elif tag_lower == "table":
            self.in_table = False
            if self.table_rows:
                max_cols = max(len(row) for row in self.table_rows)
                if max_cols > 0:
                    lines = []
                    header = self.table_rows[0]
                    padded_header = header + [""] * (max_cols - len(header))
                    lines.append("| " + " | ".join(padded_header) + " |")
                    sep_cells: list[str] = []
                    for c_idx in range(max_cols):
                        c_align = self.table_alignments[c_idx] if c_idx < len(self.table_alignments) else None
                        if c_align == "center":
                            sep_cells.append(":---:")
                        elif c_align == "right":
                            sep_cells.append("---:")
                        elif c_align == "left":
                            sep_cells.append(":---")
                        else:
                            sep_cells.append("---")
                    lines.append("| " + " | ".join(sep_cells) + " |")
                    for row in self.table_rows[1:]:
                        padded_row = row + [""] * (max_cols - len(row))
                        lines.append("| " + " | ".join(padded_row) + " |")
                    self.output.append("\n\n" + "\n".join(lines) + "\n\n")
            self.table_rows = []
            self.table_alignments = []
        elif tag_lower == "blockquote":
            text = self._flush_buf().strip()
            if text:
                lines = [f"> {line}" for line in text.splitlines()]
                self.output.append("\n\n" + "\n".join(lines) + "\n\n")
        elif tag_lower in ("strong", "b"):
            self.current_buf.append("**")
        elif tag_lower in ("em", "i"):
            self.current_buf.append("*")
        elif tag_lower == "code":
            if not self.in_pre:
                self.current_buf.append("`")
        elif tag_lower == "pre":
            self.in_pre = False
            code_text = self._flush_buf().strip("\r\n")
            lang = self.pre_lang
            self.output.append(f"\n\n```{lang}\n{code_text}\n```\n\n")
            self.pre_lang = ""
        elif tag_lower == "kbd":
            self.current_buf.append("</kbd>")
        elif tag_lower == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            self.current_buf.append(f"]({href})")
        elif tag_lower == "title":
            self.title = self._flush_buf().strip()

    def handle_data(self, data: str) -> None:
        self.current_buf.append(data)

    def handle_entityref(self, name: str) -> None:
        if name in ("rarr", "larr", "uarr", "darr", "copy", "nbsp", "trade"):
            self.current_buf.append(f"&{name};")
        else:
            self.current_buf.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.current_buf.append(html.unescape(f"&#{name};"))

    def close(self) -> None:
        if self.in_pre:
            self.in_pre = False
            code_text = self._flush_buf().strip("\r\n")
            lang = self.pre_lang
            self.output.append(f"\n\n```{lang}\n{code_text}\n```\n\n")
            self.pre_lang = ""
        elif self.in_table:
            self.in_table = False
            if self.current_table_row:
                self.table_rows.append(self.current_table_row)
                self.current_table_row = []
            if self.table_rows:
                max_cols = max(len(row) for row in self.table_rows)
                if max_cols > 0:
                    lines = []
                    header = self.table_rows[0]
                    padded_header = header + [""] * (max_cols - len(header))
                    lines.append("| " + " | ".join(padded_header) + " |")
                    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
                    for row in self.table_rows[1:]:
                        padded_row = row + [""] * (max_cols - len(row))
                        lines.append("| " + " | ".join(padded_row) + " |")
                    self.output.append("\n\n" + "\n".join(lines) + "\n\n")
            self.table_rows = []
        elif self.current_buf:
            text = self._flush_buf().strip()
            if text:
                if self.list_stack:
                    info = self.list_stack[-1]
                    indent = "  " * (len(self.list_stack) - 1)
                    if not info.get("li_bullet_emitted", False):
                        prefix = f"{indent}{info['index']}. " if info.get("type") == "ol" else f"{indent}* "
                        self.output.append(f"{prefix}{text}\n")
                    else:
                        self.output.append(f"{indent}  {text}\n")
                else:
                    self.output.append(f"\n\n{text}\n\n")
        super().close()

    def get_markdown(self) -> str:
        text = "".join(self.output)
        text = re.sub(r"\n{3,}", "\n\n", text)
        result = text.strip() + "\n"
        if self.title:
            first_h = ""
            for line in result.splitlines():
                s = line.strip()
                if s.startswith("#"):
                    first_h = s.lstrip("#").strip()
                    break
            if not first_h or first_h != self.title:
                result = f"<!-- title: {self.title} -->\n\n" + result
        return result


def _sanitize_href(url: str) -> str:
    cleaned = re.sub(r"[\s\x00-\x1f]", "", html.unescape(url)).lower()
    if any(cleaned.startswith(p) for p in ("javascript:", "data:", "vbscript:")):
        return "#"
    return html.escape(url, quote=True)


def _format_inline_markdown(text: str) -> str:
    code_tokens: list[str] = []

    def save_code(m: re.Match[str]) -> str:
        code_content = html.escape(m.group(1), quote=False)
        code_tokens.append(f"<code>{code_content}</code>")
        return f"\x00CODE{len(code_tokens) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_code, text)

    entity_tokens: list[str] = []

    def save_entity(m: re.Match[str]) -> str:
        entity_tokens.append(m.group(0))
        return f"\x00ENT{len(entity_tokens) - 1}\x00"

    text = re.sub(r"&[a-zA-Z0-9#]+;", save_entity, text)

    kbd_tokens: list[str] = []

    def save_kbd(m: re.Match[str]) -> str:
        kbd_content = html.escape(m.group(1), quote=False)
        kbd_tokens.append(f"<kbd>{kbd_content}</kbd>")
        return f"\x00KBD{len(kbd_tokens) - 1}\x00"

    text = re.sub(r"<kbd>(.*?)</kbd>", save_kbd, text, flags=re.IGNORECASE | re.DOTALL)

    text = html.escape(text, quote=False)

    img_tokens: list[str] = []

    def replace_image(m: re.Match[str]) -> str:
        alt_text = m.group(1).replace('"', "&quot;")
        img_url = _sanitize_href(m.group(2))
        img_tokens.append(f'<img src="{img_url}" alt="{alt_text}">')
        return f"\x00IMG{len(img_tokens) - 1}\x00"

    text = re.sub(r"!\[([^\[\]]*)\]\(((?:[^()\s]|\([^()\s]*\))*)\)", replace_image, text)

    link_tokens: list[str] = []

    def replace_link(m: re.Match[str]) -> str:
        link_text = m.group(1)
        link_url = _sanitize_href(m.group(2))
        link_tokens.append(link_url)
        return f'<a href="\x00URL{len(link_tokens) - 1}\x00">{link_text}</a>'

    text = re.sub(r"\[([^\[\]]+)\]\(((?:[^()\s]|\([^()\s]*\))*)\)", replace_link, text)

    text = re.sub(r"\*\*\*([^*]+)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", text)

    for i, tok in enumerate(img_tokens):
        text = text.replace(f"\x00IMG{i}\x00", tok)
    for i, url in enumerate(link_tokens):
        text = text.replace(f"\x00URL{i}\x00", url)
    for i, tok in enumerate(kbd_tokens):
        text = text.replace(f"\x00KBD{i}\x00", tok)
    for i, tok in enumerate(code_tokens):
        text = text.replace(f"\x00CODE{i}\x00", tok)
    for i, tok in enumerate(entity_tokens):
        text = text.replace(f"\x00ENT{i}\x00", tok)

    return text


def _split_table_row(line: str) -> list[str]:
    cleaned = line.strip()
    if cleaned.startswith("|"):
        cleaned = cleaned[1:]
    if cleaned.endswith("|") and not cleaned.endswith(r"\|"):
        cleaned = cleaned[:-1]
    protected = cleaned.replace(r"\|", "\x00PIPE\x00")
    return [cell.replace("\x00PIPE\x00", "|").strip() for cell in protected.split("|")]


def _parse_table_separator(line: str) -> list[str | None] | None:
    cleaned = line.strip()
    if not cleaned or "|" not in cleaned:
        return None
    if cleaned.startswith("|"):
        cleaned = cleaned[1:]
    if cleaned.endswith("|") and not cleaned.endswith(r"\|"):
        cleaned = cleaned[:-1]
    cells = [c.strip() for c in cleaned.split("|")]
    if not cells:
        return None
    alignments: list[str | None] = []
    sep_pattern = re.compile(r"^(:?)-{1,}(:?)$")
    for cell in cells:
        m = sep_pattern.match(cell)
        if not m:
            return None
        left, right = m.groups()
        if left and right:
            alignments.append("center")
        elif right:
            alignments.append("right")
        elif left:
            alignments.append("left")
        else:
            alignments.append(None)
    return alignments


def _render_table_html(
    headers: list[str],
    alignments: list[str | None],
    rows: list[list[str]],
) -> str:
    max_row_cols = max((len(r) for r in rows), default=0)
    num_cols = max(len(headers), len(alignments), max_row_cols)
    parts: list[str] = ["\t<table>\n", "\t\t<thead>\n", "\t\t\t<tr>\n"]
    for i in range(num_cols):
        h_text = headers[i] if i < len(headers) else ""
        align = alignments[i] if i < len(alignments) else None
        align_attr = f' align="{align}"' if align else ""
        formatted = _format_inline_markdown(h_text)
        parts.append(f"\t\t\t\t<th{align_attr}>{formatted}</th>\n")
    parts.extend(["\t\t\t</tr>\n", "\t\t</thead>\n"])
    if rows:
        parts.append("\t\t<tbody>\n")
        for row in rows:
            parts.append("\t\t\t<tr>\n")
            for i in range(num_cols):
                c_text = row[i] if i < len(row) else ""
                align = alignments[i] if i < len(alignments) else None
                align_attr = f' align="{align}"' if align else ""
                formatted = _format_inline_markdown(c_text)
                parts.append(f"\t\t\t\t<td{align_attr}>{formatted}</td>\n")
            parts.append("\t\t\t</tr>\n")
        parts.append("\t\t</tbody>\n")
    parts.append("\t</table>\n")
    return "".join(parts)


def _fallback_markdown_to_html_body(md_text: str) -> str:
    """Fallback pure-Python markdown body generator used when markdown library is absent."""
    lines = md_text.splitlines()
    body_parts: list[str] = []
    in_code_block = False
    code_block_lines: list[str] = []
    code_block_lang = ""
    in_quote = False
    quote_lines: list[str] = []
    in_list = False
    list_items: list[tuple[int, str, str]] = []
    in_dl = False
    dl_items: list[tuple[str, list[str]]] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            content = " ".join(line.strip() for line in paragraph_lines)
            if content:
                formatted = _format_inline_markdown(content)
                body_parts.append(f"\t<p>{formatted}</p>\n")
            paragraph_lines = []

    def flush_quote() -> None:
        nonlocal in_quote, quote_lines
        if in_quote and quote_lines:
            content = " ".join(line.strip() for line in quote_lines)
            formatted = _format_inline_markdown(content)
            body_parts.append(f"\t<blockquote>\n\t\t<p>{formatted}</p>\n\t</blockquote>\n")
            quote_lines = []
            in_quote = False

    def flush_list() -> None:
        nonlocal in_list, list_items
        if in_list and list_items:
            stack: list[tuple[int, str]] = []
            for indent, tag, item_text in list_items:
                formatted = _format_inline_markdown(item_text)
                if not stack:
                    body_parts.append(f"\t<{tag}>\n\t\t<li>{formatted}")
                    stack.append((indent, tag))
                elif indent > stack[-1][0]:
                    tabs = "\t" * (len(stack) + 1)
                    body_parts.append(f"\n{tabs}<{tag}>\n{tabs}\t<li>{formatted}")
                    stack.append((indent, tag))
                else:
                    while len(stack) > 1 and indent < stack[-1][0]:
                        _top_indent, top_tag = stack.pop()
                        tabs = "\t" * (len(stack) + 1)
                        body_parts.append(f"</li>\n{tabs}</{top_tag}>\n")
                    if stack and stack[-1][1] != tag and indent == stack[-1][0]:
                        _top_indent, top_tag = stack.pop()
                        tabs = "\t" * (len(stack) + 1)
                        body_parts.append(f"</li>\n{tabs}</{top_tag}>\n")
                        tabs = "\t" * (len(stack) + 1)
                        body_parts.append(f"{tabs}<{tag}>\n{tabs}\t<li>{formatted}")
                        stack.append((indent, tag))
                    else:
                        tabs = "\t" * (len(stack) + 1)
                        body_parts.append(f"</li>\n{tabs}<li>{formatted}")
            while stack:
                _top_indent, top_tag = stack.pop()
                tabs = "\t" * (len(stack) + 1)
                body_parts.append(f"</li>\n{tabs}</{top_tag}>\n")
            list_items = []
            in_list = False

    def flush_dl() -> None:
        nonlocal in_dl, dl_items
        if in_dl and dl_items:
            body_parts.append("\t<dl>\n")
            for term, defs in dl_items:
                term_fmt = _format_inline_markdown(term)
                body_parts.append(f"\t\t<dt>{term_fmt}</dt>\n")
                for definition in defs:
                    def_fmt = _format_inline_markdown(definition)
                    body_parts.append(f"\t\t<dd>{def_fmt}</dd>\n")
            body_parts.append("\t</dl>\n")
            dl_items = []
            in_dl = False

    def flush_all() -> None:
        nonlocal in_code_block, code_block_lines, code_block_lang
        if in_code_block:
            escaped_code = html.escape("\n".join(code_block_lines))
            class_attr = f' class="language-{code_block_lang}"' if code_block_lang else ""
            body_parts.append(f"\t<pre><code{class_attr}>{escaped_code}</code></pre>\n")
            code_block_lines = []
            code_block_lang = ""
            in_code_block = False
        flush_paragraph()
        flush_quote()
        flush_list()
        flush_dl()

    idx = 0
    num_lines = len(lines)
    while idx < num_lines:
        line = lines[idx]
        stripped = line.strip()

        if not in_code_block and idx + 1 < num_lines and "|" in line:
            alignments = _parse_table_separator(lines[idx + 1])
            if alignments is not None:
                flush_all()
                headers = _split_table_row(line)
                idx += 2
                table_rows: list[list[str]] = []
                while idx < num_lines:
                    r_line = lines[idx]
                    r_stripped = r_line.strip()
                    if not r_stripped or "|" not in r_line:
                        break
                    if r_stripped.startswith("```") or re.match(r"^#{1,6}\s+", r_stripped):
                        break
                    table_rows.append(_split_table_row(r_line))
                    idx += 1
                body_parts.append(_render_table_html(headers, alignments, table_rows))
                continue

        idx += 1

        if stripped.startswith("```"):
            if not in_code_block:
                flush_all()
                in_code_block = True
                code_block_lines = []
                code_block_lang = stripped[3:].strip()
            else:
                in_code_block = False
                escaped_code = html.escape("\n".join(code_block_lines))
                class_attr = f' class="language-{code_block_lang}"' if code_block_lang else ""
                body_parts.append(f"\t<pre><code{class_attr}>{escaped_code}</code></pre>\n")
                code_block_lines = []
                code_block_lang = ""
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        if not stripped:
            flush_all()
            continue

        if re.match(r"^(?:---|\*\*\*|___)$", stripped):
            flush_all()
            body_parts.append("\t<hr>\n")
            continue

        h_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h_match:
            flush_all()
            level = len(h_match.group(1))
            h_text = h_match.group(2).strip()
            formatted_h = _format_inline_markdown(h_text)
            body_parts.append(f"\t<h{level}>{formatted_h}</h{level}>\n")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            flush_dl()
            in_quote = True
            quote_lines.append(stripped.lstrip(">").strip())
            continue

        ul_match = re.match(r"^(\s*)[*+-]\s+(.*)$", line)
        if ul_match:
            flush_paragraph()
            flush_quote()
            flush_dl()
            in_list = True
            indent = len(ul_match.group(1))
            list_items.append((indent, "ul", ul_match.group(2).strip()))
            continue

        ol_match = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ol_match:
            flush_paragraph()
            flush_quote()
            flush_dl()
            in_list = True
            indent = len(ol_match.group(1))
            list_items.append((indent, "ol", ol_match.group(2).strip()))
            continue

        dl_match = re.match(r"^:\s+(.*)$", stripped)
        if dl_match:
            if paragraph_lines:
                flush_quote()
                flush_list()
                term = paragraph_lines.pop()
                flush_paragraph()
                in_dl = True
                dl_items.append((term.strip(), [dl_match.group(1).strip()]))
                continue
            elif in_dl and dl_items:
                dl_items[-1][1].append(dl_match.group(1).strip())
                continue

        if in_dl and dl_items and (line.startswith("   ") or line.startswith("\t")):
            dl_items[-1][1][-1] = f"{dl_items[-1][1][-1]} {stripped}"
            continue

        if in_list and (line.startswith("   ") or line.startswith("\t")):
            if list_items:
                prev_lvl, prev_type, prev_txt = list_items[-1]
                list_items[-1] = (prev_lvl, prev_type, f"{prev_txt} {stripped}")
            continue

        if in_quote:
            quote_lines.append(stripped)
            continue

        paragraph_lines.append(line)

    flush_all()
    return "".join(body_parts)


def _markdown_to_doc_html(
    md_text: str,
    language: str = "en",
    is_rtl: bool | None = None,
    title: str | None = None,
) -> str:
    """Convert Markdown to complete accessible documentation HTML."""
    if is_rtl is None:
        is_rtl = _is_rtl_language(language)

    md_cleaned = _preprocess_markdown_text(md_text)
    doc_title = title or _get_title_from_markdown(md_cleaned) or "Google TTS For NVDA"

    if _MARKDOWN_AVAILABLE and markdown is not None:
        html_body = _generate_doc_html_body(md_cleaned)
    else:
        html_body = _fallback_markdown_to_html_body(md_cleaned)

    lang_attr = language.replace("_", "-")
    dir_attr = ' dir="rtl"' if is_rtl else ""
    title_escaped = html.escape(doc_title)

    html_lines = [
        "<!DOCTYPE html>",
        f'<html lang="{lang_attr}"{dir_attr}>',
        "<head>",
        '\t<meta charset="UTF-8">',
        '\t<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"\t<title>{title_escaped}</title>",
        "</head>",
        "<body>",
        html_body.rstrip(),
        "</body>",
        "</html>\n",
    ]
    return "\n".join(html_lines)


def _convert_html_file_to_md(html_path: Path, md_path: Path) -> tuple[bool, str]:
    if not html_path.is_file():
        return False, f"HTML file not found: {_relative_to_addon(html_path)}"
    try:
        raw_html = html_path.read_text(encoding="utf-8-sig")
        lxml_errors = _check_html_content_with_lxml(raw_html, str(_relative_to_addon(html_path)))
        if lxml_errors:
            return False, f"HTML file has syntax errors: {'; '.join(lxml_errors)}"
        if _NH3_AVAILABLE and nh3 is not None:
            raw_html = nh3.clean(
                raw_html,
                tags=_create_doc_tag_filter(),
                attributes=_create_doc_attribute_filter(),
                attribute_filter=_doc_attribute_filter,
                link_rel=None,
                strip_comments=False,
            )
        converter = _DocHtmlToMarkdown()
        converter.feed(raw_html)
        converter.close()
        md_text = converter.get_markdown()
        _atomic_write_text(md_path, md_text, prefix=".convert-md-")
        return True, f"Generated {_relative_to_addon(md_path)} from {_relative_to_addon(html_path)}"
    except Exception as exc:
        return False, f"Failed to convert {_relative_to_addon(html_path)} to Markdown: {exc}"


def _convert_md_file_to_html(md_path: Path, html_path: Path, language: str) -> tuple[bool, str]:
    if not md_path.is_file():
        return False, f"Markdown file not found: {_relative_to_addon(md_path)}"
    try:
        md_text = md_path.read_text(encoding="utf-8-sig")
        is_rtl = _is_rtl_language(language)
        html_text = _markdown_to_doc_html(md_text, language=language, is_rtl=is_rtl)
        lxml_errors = _check_html_content_with_lxml(html_text, str(_relative_to_addon(html_path)))
        if lxml_errors:
            return False, f"Generated HTML has syntax errors: {'; '.join(lxml_errors)}"
        _atomic_write_text(html_path, html_text, prefix=".convert-html-")
        direction = "rtl" if is_rtl else "ltr"
        return (
            True,
            f"Generated {_relative_to_addon(html_path)} from {_relative_to_addon(md_path)} (lang={language}, dir={direction})",
        )
    except Exception as exc:
        return False, f"Failed to convert {_relative_to_addon(md_path)} to HTML: {exc}"


def _build_doc_for_language(language: str) -> tuple[bool, str]:
    """Build doc/<language>/readme.html from readme.po or readme.md."""
    if language == "en":
        return False, f"{language}: readme.html is the English source document"
    doc_dir = DOC_DIR / language
    if doc_dir.is_dir():
        for stale_mo in doc_dir.glob("*.mo"):
            stale_mo.unlink(missing_ok=True)
    po_path = doc_dir / "readme.po"
    md_path = doc_dir / "readme.md"
    html_path = doc_dir / "readme.html"

    # Priority 1: Compile from PO file
    if po_path.is_file():
        return _build_doc_from_po(language, po_path, html_path)

    # Priority 2: Compile from Markdown file
    if md_path.is_file():
        return _convert_md_file_to_html(md_path, html_path, language)

    # Priority 3: Keep existing HTML file
    if html_path.is_file():
        return False, f"{language}: readme.html already exists (no readme.po or readme.md source to compile)"

    return False, f"{language}: no readme.po, readme.md, or readme.html found"


def _check_doc_folder_health(language: str, check_only: bool) -> list[str]:
    errors: list[str] = []
    doc_dir = DOC_DIR / language
    if doc_dir.is_dir():
        for stale_mo in sorted(doc_dir.glob("*.mo")):
            errors.append(f"{language}: unexpected .mo file in documentation folder: {_relative_to_addon(stale_mo)}")
    html_path = doc_dir / "readme.html"
    po_path = doc_dir / "readme.po"
    md_path = doc_dir / "readme.md"
    if not html_path.is_file() and not po_path.is_file() and not md_path.is_file():
        errors.append(f"{language}: missing documentation file: {_relative_to_addon(html_path)}")
    elif html_path.is_file() and _is_rtl_language(language):
        if not (not check_only and (po_path.is_file() or md_path.is_file())):
            try:
                content = html_path.read_text(encoding="utf-8-sig", errors="replace")
                if 'dir="rtl"' not in content and "dir='rtl'" not in content:
                    errors.append(
                        f'{language}: RTL documentation missing dir="rtl" attribute in {_relative_to_addon(html_path)}'
                    )
            except OSError as exc:
                errors.append(f"{language}: could not read {_relative_to_addon(html_path)}: {exc}")
    return errors


def _check_doc_language(
    language: str,
    supported_languages: set[str] | None = None,
    checks: set[str] | None = None,
    check_only: bool = True,
    msgfmt_path: Path | None = None,
) -> list[str]:
    """Check documentation translation for a specific language (missing, obsolete, fuzzy, RTL, language code, HTML tags, placeholders)."""
    active_checks = DEFAULT_DOC_CHECKS if checks is None else checks
    doc_dir = DOC_DIR / language
    po_path = doc_dir / "readme.po"
    md_path = doc_dir / "readme.md"
    html_path = doc_dir / "readme.html"
    errors: list[str] = []

    if language == "en":
        if not html_path.is_file():
            errors.append(f"en: missing English source document: {_relative_to_addon(html_path)}")
        if po_path.is_file():
            errors.append(
                f"en: unexpected readme.po in English source documentation folder: {_relative_to_addon(po_path)}"
            )
        if (CHECK_DOCS in active_checks) and doc_dir.is_dir():
            for stale_mo in sorted(doc_dir.glob("*.mo")):
                errors.append(f"en: unexpected .mo file in documentation folder: {_relative_to_addon(stale_mo)}")
        if html_path.is_file():
            try:
                en_html_text = html_path.read_text(encoding="utf-8-sig")
                errors.extend(
                    _check_html_content_with_lxml(
                        en_html_text,
                        str(_relative_to_addon(html_path)),
                    )
                )
                if CHECK_PLACEHOLDERS in active_checks:
                    for _line_no, seg in _extract_doc_segments(en_html_text):
                        for tag_err in _check_html_tag_interpolations(seg, seg):
                            errors.append(
                                f"en: HTML tag issue in English source document for {_message_preview(seg)!r}: {tag_err}"
                            )
            except Exception as exc:
                errors.append(f"en: could not parse English source document: {exc}")
        return errors

    if CHECK_LANGUAGE in active_checks and supported_languages is not None and language not in supported_languages:
        errors.append(f"{language}: language code is not present in the NVDA locale folder.")
        return errors

    if CHECK_DOCS in active_checks:
        health_errors = _check_doc_folder_health(language, check_only)
        errors.extend(health_errors)
        if any("missing documentation file" in e for e in health_errors):
            return errors

    if not DOC_EN_PATH.is_file():
        errors.append(f"{language}: English source document missing: {_relative_to_addon(DOC_EN_PATH)}")
        return errors

    if po_path.is_file():
        actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
        msgfmt_errors = _check_po_syntax_with_msgfmt(po_path, actual_msgfmt)
        if msgfmt_errors:
            errors.extend(msgfmt_errors)
        elif actual_msgfmt is None:
            errors.extend(_check_po_syntax_fallback(po_path))
        fuzzy_msgids: set[str] = set()
        try:
            catalog = _parse_po(po_path, include_untranslated=True, include_fuzzy=True)
            if CHECK_FUZZY in active_checks:
                fuzzy_msgids = _po_fuzzy_msgids(po_path)
        except Exception as exc:
            errors.append(f"{language}: could not parse {_relative_to_addon(po_path)}: {exc}")
            return errors

        if CHECK_FUZZY in active_checks:
            for f_id in sorted(fuzzy_msgids):
                errors.append(
                    f"{language}: fuzzy documentation string in readme.po requires review: {_message_preview(f_id)!r}"
                )

        try:
            expected_msgids = {seg for _, seg in _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))}
        except Exception as exc:
            errors.append(f"en: could not parse English source document: {exc}")
            return errors

        po_msgids = set(catalog.keys()) - {""}

        if CHECK_DOCS in active_checks:
            for m in sorted(expected_msgids - po_msgids):
                errors.append(f"{language}: missing documentation string in readme.po: {_message_preview(m)!r}")

        for m in sorted(expected_msgids & po_msgids):
            msgstr = catalog.get(m, "")
            if (CHECK_DOCS in active_checks) and not msgstr.strip():
                errors.append(f"{language}: untranslated documentation string in readme.po: {_message_preview(m)!r}")
            if msgstr and (CHECK_PLACEHOLDERS in active_checks):
                for tag_err in _check_html_tag_interpolations(m, msgstr):
                    errors.append(f"{language}: HTML tag issue in readme.po for {_message_preview(m)!r}: {tag_err}")
                for interp_err in _check_format_interpolations(m, msgstr):
                    errors.append(
                        f"{language}: placeholder mismatch in readme.po for {_message_preview(m)!r}: {interp_err}"
                    )

        if CHECK_OBSOLETE in active_checks:
            for m in sorted(po_msgids - expected_msgids):
                errors.append(f"{language}: obsolete documentation string in readme.po: {_message_preview(m)!r}")

    elif md_path.is_file() and language != "en":
        try:
            is_rtl = _is_rtl_language(language)
            md_html = _markdown_to_doc_html(md_path.read_text(encoding="utf-8-sig"), language=language, is_rtl=is_rtl)
            errors.extend(_check_html_content_with_lxml(md_html, str(_relative_to_addon(md_path))))
            en_segs = [s for _, s in _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))]
            lang_segs = [s for _, s in _extract_doc_segments(md_html)]
            errors.extend(_check_doc_segments(language, en_segs, lang_segs, "readme.md", active_checks))
        except Exception as exc:
            errors.append(f"{language}: could not validate {_relative_to_addon(md_path)}: {exc}")

    elif html_path.is_file() and language != "en":
        try:
            lang_html_text = html_path.read_text(encoding="utf-8-sig")
            errors.extend(
                _check_html_content_with_lxml(
                    lang_html_text,
                    str(_relative_to_addon(html_path)),
                )
            )
            en_segs = [s for _, s in _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))]
            lang_segs = [s for _, s in _extract_doc_segments(lang_html_text)]
            errors.extend(_check_doc_segments(language, en_segs, lang_segs, "readme.html", active_checks))
        except Exception as exc:
            errors.append(f"{language}: could not validate {_relative_to_addon(html_path)}: {exc}")

    return errors


def _check_language_files(
    language_dir: Path,
    supported_languages: set[str] | None = None,
    checks: set[str] | None = None,
    check_only: bool = True,
    msgfmt_path: Path | None = None,
) -> list[str]:
    active_checks = DEFAULT_UI_CHECKS if checks is None else checks
    language = language_dir.name
    errors: list[str] = []
    if CHECK_LANGUAGE in active_checks and supported_languages is not None and language not in supported_languages:
        errors.append(f"{language}: language code is not present in the NVDA locale folder.")
        return errors
    manifest_path = language_dir / "manifest.ini"
    if check_only and CHECK_MANIFEST in active_checks and not manifest_path.is_file():
        errors.append(f"{language}: missing localized manifest file: {_relative_to_addon(manifest_path)}")
    po_path = language_dir / "LC_MESSAGES" / "nvda.po"
    if not po_path.is_file():
        errors.append(f"{language}: missing translation file: {_relative_to_addon(po_path)}")
    else:
        actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
        msgfmt_errors = _check_po_syntax_with_msgfmt(po_path, actual_msgfmt)
        if msgfmt_errors:
            errors.extend(msgfmt_errors)
        elif actual_msgfmt is None:
            errors.extend(_check_po_syntax_fallback(po_path))
    if CHECK_DOCS in active_checks:
        errors.extend(_check_doc_folder_health(language, check_only))
    if CHECK_SORT in active_checks:
        errors.extend(_check_language_sort_file(language_dir))
    return errors


def _check_language_sort_file(language_dir: Path) -> list[str]:
    path = language_dir / "languageSort.json"
    if not path.exists():
        return []
    language = language_dir.name
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [f"{language}: languageSort.json could not be parsed: {exc}"]
    if not isinstance(data, dict):
        return [f"{language}: languageSort.json must contain a JSON object."]
    letter_order = data.get("letterOrder")
    if not isinstance(letter_order, list) or not letter_order:
        errors.append(f"{language}: languageSort.json field 'letterOrder' must be a non-empty list.")
    else:
        seen_letters: set[str] = set()
        for index, item in enumerate(letter_order):
            if not isinstance(item, str) or not item:
                errors.append(f"{language}: languageSort.json letterOrder[{index}] must be a non-empty string.")
                continue
            normalized_item = unicodedata.normalize("NFC", item.casefold())
            if normalized_item in seen_letters:
                errors.append(f"{language}: languageSort.json repeats letter {item!r} in letterOrder.")
            seen_letters.add(normalized_item)
    strip_prefixes = data.get("stripPrefixes", [])
    if not isinstance(strip_prefixes, list) or any(not isinstance(item, str) or not item for item in strip_prefixes):
        errors.append(f"{language}: languageSort.json field 'stripPrefixes' must be a list of non-empty strings.")
    ignored_marks = data.get("ignoreCombiningMarks", [])
    if not isinstance(ignored_marks, list):
        errors.append(
            f"{language}: languageSort.json field 'ignoreCombiningMarks' must be a list of combining mark names."
        )
    else:
        for item in ignored_marks:
            if not isinstance(item, str) or not item:
                errors.append(f"{language}: languageSort.json combining mark names must be non-empty strings.")
                continue
            if _combining_mark_from_name(item) is None:
                errors.append(f"{language}: languageSort.json has unknown combining mark name: {item!r}.")
    allowed_fields = {"stripPrefixes", "letterOrder", "ignoreCombiningMarks"}
    for key in data:
        if key not in allowed_fields:
            errors.append(f"{language}: languageSort.json has unknown field {key!r}.")
    return errors


def _combining_mark_from_name(name: str) -> str | None:
    normalized_name = name.upper()
    for candidate in (f"COMBINING {normalized_name}", f"COMBINING {normalized_name} ACCENT"):
        try:
            return unicodedata.lookup(candidate)
        except KeyError:
            continue
    return None


def _describe_checks_generic(checks: set[str], order: tuple[str, ...], labels: dict[str, str]) -> str:
    active = [labels[name] for name in order if name in checks]
    return ", ".join(active) if active else "none"


def _describe_checks(checks: set[str]) -> str:
    return _describe_checks_generic(checks, CHECK_ORDER, CHECK_LABELS)


def _format_run_summary(
    languages_str: str,
    described_checks: str,
    check_only: bool,
    found_locale_dirs: list[Path],
    has_language_check: bool,
) -> None:
    print("")
    print(f"Selected locales: {languages_str}")
    print(f"Checks: {described_checks}")
    print(f"Action: {'check only' if check_only else 'build generated files'}")
    if has_language_check:
        if found_locale_dirs:
            locale_paths = "; ".join(str(path) for path in found_locale_dirs)
            print(f"NVDA locale source: {locale_paths}")
        else:
            print("NVDA locale source: not found; language-code support check will be skipped")
    print("")


def _print_run_summary(
    language_dirs: list[Path],
    checks: set[str],
    check_only: bool,
    found_locale_dirs: list[Path],
) -> None:
    languages = ", ".join(path.name for path in language_dirs) if language_dirs else "none"
    _format_run_summary(languages, _describe_checks(checks), check_only, found_locale_dirs, CHECK_LANGUAGE in checks)


DOC_CHECK_ORDER = (
    CHECK_LANGUAGE,
    CHECK_DOCS,
    CHECK_PLACEHOLDERS,
    CHECK_OBSOLETE,
    CHECK_FUZZY,
)
DOC_CHECK_LABELS = {
    CHECK_LANGUAGE: "NVDA language code",
    CHECK_DOCS: "documentation and RTL direction",
    CHECK_PLACEHOLDERS: "HTML tags, links, and placeholders",
    CHECK_OBSOLETE: "obsolete documentation strings",
    CHECK_FUZZY: "fuzzy translations",
}


def _describe_doc_checks(checks: set[str]) -> str:
    return _describe_checks_generic(checks, DOC_CHECK_ORDER, DOC_CHECK_LABELS)


def _print_doc_run_summary(
    languages: list[str],
    checks: set[str],
    check_only: bool,
    found_locale_dirs: list[Path],
) -> None:
    lang_str = ", ".join(languages) if languages else "none"
    _format_run_summary(lang_str, _describe_doc_checks(checks), check_only, found_locale_dirs, CHECK_LANGUAGE in checks)


def _parse_checks(
    values: list[str] | None,
    strict: bool,
    default_checks: set[str] | None = None,
) -> set[str]:
    checks = set(default_checks if default_checks is not None else DEFAULT_CHECKS)
    if strict:
        checks.add(CHECK_UI)
    if not values:
        return checks
    checks = set()
    valid_checks = ALL_CHECKS | {"all"}
    for raw_value in values:
        for item in raw_value.split(","):
            name = item.strip().lower()
            if not name:
                continue
            if name not in valid_checks:
                raise ValueError(f"unknown check {name!r}; choose from {', '.join(sorted(valid_checks))}")
            if name == "all":
                checks.update(ALL_CHECKS)
            else:
                checks.add(name)
    return checks


def _prompt_number(prompt: str, minimum: int, maximum: int) -> int:
    while True:
        raw_value = input(prompt).strip()
        try:
            value = int(raw_value)
        except ValueError:
            print(f"Enter a number from {minimum} to {maximum}.")
            continue
        if minimum <= value <= maximum:
            return value
        print(f"Enter a number from {minimum} to {maximum}.")


def _prompt_language_code() -> str:
    while True:
        language = _normalize_language_code(input("Language code: ").strip())
        if language:
            return language
        print("Enter a language code.")


def _prompt_languages(languages: list[str]) -> list[str] | None:
    print("")
    print("Locales:")
    print("  1. All addon locales")
    for index, language in enumerate(languages, start=2):
        print(f"  {index}. {language}")
    manual_choice = len(languages) + 2
    print(f"  {manual_choice}. Enter language code manually")
    choice = _prompt_number(f"Choose 1-{manual_choice}: ", 1, manual_choice)
    if choice == 1:
        return None
    if choice == manual_choice:
        return [_prompt_language_code()]
    return [languages[choice - 2]]


def _prompt_checks_generic(
    default_checks: set[str],
    default_label: str,
    numbered_checks: list[tuple[str, str]],
    available_checks_str: str,
) -> set[str]:
    print("")
    print("Check mode:")
    print(f"  1. {default_label}")
    for index, (_check, label) in enumerate(numbered_checks, start=2):
        print(f"  {index}. {label}")
    custom_choice = len(numbered_checks) + 2
    print(f"  {custom_choice}. Custom checks")
    mode = _prompt_number(f"Choose 1-{custom_choice}: ", 1, custom_choice)
    if mode == 1:
        return set(default_checks)
    if mode == custom_choice:
        print(f"Available checks: {available_checks_str}")
        raw_checks = input("Checks, separated by commas: ")
        return _parse_checks([raw_checks], strict=False, default_checks=default_checks)
    return {numbered_checks[mode - 2][0]}


def _prompt_checks(default_checks: set[str]) -> set[str]:
    return _prompt_checks_generic(
        default_checks=default_checks,
        default_label="Default checks (all categories)",
        numbered_checks=[
            (CHECK_LANGUAGE, "NVDA language code only"),
            (CHECK_MANIFEST, "Manifest only"),
            (CHECK_DOCS, "Documentation only"),
            (CHECK_UI, "UI strings only"),
            (CHECK_PLACEHOLDERS, "Placeholders and format specifiers only"),
            (CHECK_SORT, "Language sorting only"),
            (CHECK_OBSOLETE, "Obsolete source strings only"),
            (CHECK_FUZZY, "Fuzzy translations only"),
        ],
        available_checks_str="language, manifest, docs, ui, placeholders, sort, obsolete, fuzzy, all",
    )


def _prompt_doc_checks(default_checks: set[str] | None = None) -> set[str]:
    base_checks = set(default_checks) if default_checks is not None else set(DEFAULT_DOC_CHECKS)
    return _prompt_checks_generic(
        default_checks=base_checks,
        default_label="Default checks (all doc categories)",
        numbered_checks=[
            (CHECK_LANGUAGE, "NVDA language code only"),
            (CHECK_DOCS, "Documentation and RTL direction only"),
            (CHECK_PLACEHOLDERS, "HTML tags, links, and placeholders only"),
            (CHECK_OBSOLETE, "Obsolete documentation strings only"),
            (CHECK_FUZZY, "Fuzzy translations only"),
        ],
        available_checks_str="language, docs, placeholders, obsolete, fuzzy, all",
    )


def _interactive_options(
    default_checks: set[str],
) -> tuple[list[str] | None, set[str], bool, bool, bool, bool, bool, bool, bool, bool, bool]:
    languages = _addon_languages()
    doc_languages = _addon_doc_languages()
    print("Google TTS For NVDA translation tools")
    print("")
    print("Task:")
    print("  1. Check or build UI translations (nvda.mo, manifest.ini)")
    print("  2. Generate UI string template (locale\\nvda.pot)")
    print("  3. Update UI PO files from the template (locale\\<lang>\\LC_MESSAGES\\nvda.po)")
    print("  4. Check or build documentation translations (doc\\<lang>\\readme.html)")
    print("  5. Generate documentation string template (doc\\readme.pot)")
    print("  6. Update documentation PO files from the template (doc\\<lang>\\readme.po)")
    print("  7. Convert documentation HTML to Markdown (doc\\<lang>\\readme.html -> readme.md)")
    print("  8. Convert documentation Markdown to HTML (doc\\<lang>\\readme.md -> readme.html)")
    task = _prompt_number("Choose 1-8: ", 1, 8)
    if task == 2:
        return None, set(default_checks), False, True, False, False, False, False, False, False, False
    if task == 3:
        selected_languages = _prompt_languages(languages) if languages else [_prompt_language_code()]
        return selected_languages, set(default_checks), False, False, True, False, False, False, False, False, False
    if task == 4:
        selected_languages = _prompt_languages(doc_languages) if doc_languages else [_prompt_language_code()]
        checks = _prompt_doc_checks(DEFAULT_DOC_CHECKS)
        print("")
        print("Action:")
        print("  1. Check only")
        print("  2. Build generated files")
        action = _prompt_number("Choose 1 or 2: ", 1, 2)
        if action == 1:
            return selected_languages, checks, False, False, False, False, False, False, True, False, False
        return selected_languages, checks, False, False, False, True, False, False, False, False, False
    if task == 5:
        return None, set(default_checks), False, False, False, False, True, False, False, False, False
    if task == 6:
        selected_languages = _prompt_languages(doc_languages) if doc_languages else [_prompt_language_code()]
        return selected_languages, set(default_checks), False, False, False, False, False, True, False, False, False
    if task == 7:
        conv_languages = (["en"] if (DOC_DIR / "en").is_dir() else []) + [
            lang for lang in doc_languages if lang != "en"
        ]
        selected_languages = _prompt_languages(conv_languages) if conv_languages else [_prompt_language_code()]
        return selected_languages, set(default_checks), False, False, False, False, False, False, False, True, False
    if task == 8:
        conv_languages = (["en"] if (DOC_DIR / "en").is_dir() else []) + [
            lang for lang in doc_languages if lang != "en"
        ]
        selected_languages = _prompt_languages(conv_languages) if conv_languages else [_prompt_language_code()]
        return selected_languages, set(default_checks), False, False, False, False, False, False, False, False, True

    selected_languages = _prompt_languages(languages) if languages else [_prompt_language_code()]
    checks = _prompt_checks(default_checks)

    print("")
    print("Action:")
    print("  1. Check only")
    print("  2. Build generated files")
    action = _prompt_number("Choose 1 or 2: ", 1, 2)
    return selected_languages, checks, action == 1, False, False, False, False, False, False, False, False


def _print_errors(errors: list[str]) -> int:
    for error in errors:
        print(f"[ERROR] {error}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Update, build, and check Google TTS For NVDA translations.")
    parser.add_argument("--menu", action="store_true", help="Show an interactive numbered menu.")
    parser.add_argument(
        "--check",
        "--check-ui",
        dest="check",
        action="store_true",
        help="Only check UI translations and packaging; do not write generated files.",
    )
    parser.add_argument(
        "--extract-template",
        "--extract-ui-template",
        dest="extract_template",
        action="store_true",
        help="Generate locale\\nvda.pot with the current English UI strings and exit.",
    )
    parser.add_argument(
        "--update-po",
        "--update-ui-po",
        dest="update_po",
        action="store_true",
        help="Regenerate locale\\nvda.pot and merge it into selected or all locale UI PO files.",
    )
    parser.add_argument(
        "--build-ui",
        "--build",
        dest="build_ui",
        action="store_true",
        help="Build UI translations (nvda.mo, manifest.ini) for selected or all languages.",
    )
    parser.add_argument(
        "--check-docs",
        "--check-doc",
        dest="check_docs",
        action="store_true",
        help="Only check documentation translations; do not write generated files.",
    )
    parser.add_argument(
        "--extract-doc-template",
        "--extract-docs-template",
        dest="extract_doc_template",
        action="store_true",
        help="Generate doc\\readme.pot with the current English documentation strings and exit.",
    )
    parser.add_argument(
        "--update-doc-po",
        "--update-docs-po",
        dest="update_doc_po",
        action="store_true",
        help="Regenerate doc\\readme.pot and merge it into selected or all locale documentation PO files.",
    )
    parser.add_argument(
        "--build-docs",
        "--build-doc",
        dest="build_docs",
        action="store_true",
        help="Build doc\\<lang>\\readme.html from readme.po for selected or all languages.",
    )
    parser.add_argument(
        "--html-to-md",
        "--html2md",
        "--doc-to-md",
        dest="html_to_md",
        action="store_true",
        help="Convert documentation readme.html to readme.md for selected or all languages.",
    )
    parser.add_argument(
        "--md-to-html",
        "--md2html",
        "--doc-from-md",
        dest="md_to_html",
        action="store_true",
        help="Convert documentation readme.md to readme.html for selected or all languages.",
    )
    parser.add_argument(
        "--msgmerge",
        type=Path,
        help="Path to msgmerge for --update-po and --update-doc-po. Defaults to PATH or a standard Poedit installation.",
    )
    parser.add_argument(
        "--msgfmt",
        type=Path,
        help="Path to msgfmt for PO syntax checking and MO compilation. Defaults to PATH or a standard Poedit installation.",
    )
    parser.add_argument(
        "-l",
        "--language",
        action="append",
        help="Only update, build, or check this language code. Can be used more than once.",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="Update, build, or check every add-on locale without opening the interactive menu.",
    )
    parser.add_argument(
        "--nvda-locale-dir",
        type=Path,
        action="append",
        help=(
            "NVDA locale folder used to validate supported language codes. "
            "Can be used more than once. Defaults to Program Files and Program Files (x86)."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Compatibility alias; UI strings are already checked by default.",
    )
    parser.add_argument(
        "--checks",
        action="append",
        help=(
            "Comma-separated checks to run: all, language, manifest, docs, ui, placeholders, sort, obsolete, fuzzy. "
            "Defaults to all checks."
        ),
    )
    args = parser.parse_args()
    if args.language and args.all_languages:
        parser.error("--language and --all-languages cannot be used together.")
    try:
        checks = _parse_checks(args.checks, args.strict)
    except ValueError as exc:
        parser.error(str(exc))
    selected_languages = None if args.all_languages else args.language
    check_only = args.check
    extract_template = args.extract_template
    update_po = args.update_po
    build_ui = args.build_ui
    build_docs = args.build_docs
    extract_doc_template = args.extract_doc_template
    update_doc_po = args.update_doc_po
    check_docs = args.check_docs
    html_to_md = args.html_to_md
    md_to_html = args.md_to_html
    using_menu = args.menu or len(sys.argv) == 1
    if using_menu:
        (
            selected_languages,
            checks,
            check_only,
            extract_template,
            update_po,
            build_docs,
            extract_doc_template,
            update_doc_po,
            check_docs,
            html_to_md,
            md_to_html,
        ) = _interactive_options(checks)
        if not check_only and not any(
            (
                extract_template,
                update_po,
                build_docs,
                extract_doc_template,
                update_doc_po,
                check_docs,
                html_to_md,
                md_to_html,
            )
        ):
            build_ui = True
    elif not args.checks:
        checks = set(DEFAULT_DOC_CHECKS) if check_docs or build_docs else set(DEFAULT_UI_CHECKS)

    action_flags = [
        ("--check", check_only),
        ("--extract-template", extract_template),
        ("--update-po", update_po),
        ("--build-ui", build_ui),
        ("--check-docs", check_docs),
        ("--extract-doc-template", extract_doc_template),
        ("--update-doc-po", update_doc_po),
        ("--build-docs", build_docs),
        ("--html-to-md", html_to_md),
        ("--md-to-html", md_to_html),
    ]
    chosen_actions = [flag for flag, enabled in action_flags if enabled]
    if len(chosen_actions) > 1:
        parser.error(f"{chosen_actions[0]} cannot be combined with {chosen_actions[1]}.")

    if args.msgmerge is not None and not update_po and not update_doc_po:
        parser.error("--msgmerge can only be used with --update-po or --update-doc-po.")

    msgfmt_path = _find_msgfmt(args.msgfmt)
    if args.msgfmt is not None and msgfmt_path is None:
        parser.error(f"--msgfmt path does not exist or is not a valid file: {args.msgfmt}")

    if args.msgmerge is not None and _find_msgmerge(args.msgmerge) is None:
        parser.error(f"--msgmerge path does not exist or is not a valid file: {args.msgmerge}")

    if check_docs:
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=False, include_en=True
        )
        if not DOC_EN_PATH.is_file():
            print(f"[ERROR] English source document missing: {_relative_to_addon(DOC_EN_PATH)}")
            return 1
        locale_dirs = args.nvda_locale_dir or list(DEFAULT_NVDA_LOCALE_DIRS)
        supported_languages, found_locale_dirs = _supported_nvda_languages_from_dirs(locale_dirs)
        if CHECK_LANGUAGE in checks and supported_languages is None:
            print("[WARN] NVDA locale folder was not found; language-code support check is skipped.")
        _print_doc_run_summary(target_languages, checks, check_only=True, found_locale_dirs=found_locale_dirs)
        all_errors: list[str] = list(selection_errors)
        for lang in target_languages:
            doc_dir = DOC_DIR / lang
            kwargs = {"msgfmt_path": msgfmt_path} if args.msgfmt is not None else {}
            errors = _check_doc_language(
                lang,
                supported_languages=supported_languages,
                checks=checks,
                check_only=True,
                **kwargs,
            )
            if errors:
                all_errors.extend(errors)
                continue
            if lang == "en":
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print("  Passed: English source document")
                continue
            print(f"Checked {_relative_to_addon(doc_dir)}")
            print(f"  Passed: {_describe_doc_checks(checks)}")
        return _print_errors(all_errors)

    if extract_doc_template:
        try:
            pot_path = _write_doc_pot()
            print(f"Updated documentation string template: {_relative_to_addon(pot_path)}")
            return 0
        except (RuntimeError, ValueError, OSError) as exc:
            return _print_errors([str(exc)])

    if update_doc_po:
        if not using_menu and not args.all_languages and selected_languages is None:
            parser.error("--update-doc-po requires --language or --all-languages.")
        try:
            pot_path = _write_doc_pot()
        except (RuntimeError, ValueError, OSError) as exc:
            return _print_errors([str(exc)])
        print(f"Updated documentation string template: {_relative_to_addon(pot_path)}")
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=True, include_en=False
        )
        if selection_errors:
            return _print_errors(selection_errors)
        msgmerge_path = _find_msgmerge(args.msgmerge)
        if msgmerge_path:
            print(f"msgmerge: {msgmerge_path}")
        else:
            print("[INFO] msgmerge was not found; using internal PO merger.")
        print("")
        all_errors = []
        for lang in target_languages:
            try:
                preserved, added, removed = _update_doc_po_from_template(lang, msgmerge_path)
            except (OSError, RuntimeError, ValueError) as exc:
                all_errors.append(str(exc))
                continue
            doc_po = DOC_DIR / lang / "readme.po"
            print(f"Updated {_relative_to_addon(doc_po)}")
            print(f"  Preserved current translations: {preserved}")
            print(f"  Added untranslated doc strings: {added}")
            print(f"  Removed obsolete entries: {removed}")
        return _print_errors(all_errors)

    if build_docs:
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=False, include_en=True
        )
        if not DOC_EN_PATH.is_file():
            print(f"[ERROR] English source document missing: {_relative_to_addon(DOC_EN_PATH)}")
            return 1
        locale_dirs = args.nvda_locale_dir or list(DEFAULT_NVDA_LOCALE_DIRS)
        supported_languages, found_locale_dirs = _supported_nvda_languages_from_dirs(locale_dirs)
        if CHECK_LANGUAGE in checks and supported_languages is None:
            print("[WARN] NVDA locale folder was not found; language-code support check is skipped.")
        try:
            pot_path = _write_doc_pot()
            print(f"Updated documentation string template: {_relative_to_addon(pot_path)}")
        except (RuntimeError, ValueError, OSError) as exc:
            return _print_errors([str(exc)])
        _print_doc_run_summary(target_languages, checks, check_only=False, found_locale_dirs=found_locale_dirs)

        all_errors = list(selection_errors)
        for lang in target_languages:
            doc_dir = DOC_DIR / lang
            html_path = doc_dir / "readme.html"
            kwargs = {"msgfmt_path": msgfmt_path} if args.msgfmt is not None else {}
            errors = _check_doc_language(
                lang,
                supported_languages=supported_languages,
                checks=checks,
                check_only=False,
                **kwargs,
            )
            if errors:
                all_errors.extend(errors)
                continue
            if lang == "en":
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print("  Passed: English source document")
                continue

            try:
                built, msg = _build_doc_for_language(lang)
            except (OSError, RuntimeError, ValueError) as exc:
                all_errors.append(f"{lang}: failed to build documentation: {exc}")
                continue
            if built:
                print(f"Updated {_relative_to_addon(doc_dir)}")
                print(f"  Passed: {_describe_doc_checks(checks)}")
                print("  Generated: readme.html")
            elif html_path.is_file():
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print(f"  Passed: {_describe_doc_checks(checks)}")
            else:
                all_errors.append(msg)
        return _print_errors(all_errors)

    if html_to_md:
        if not using_menu and not args.all_languages and selected_languages is None:
            parser.error("--html-to-md requires --language or --all-languages.")
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=False, include_en=True
        )
        if selection_errors:
            return _print_errors(selection_errors)
        all_errors = []
        converted_count = 0
        for lang in target_languages:
            doc_dir = DOC_DIR / lang
            html_file = doc_dir / "readme.html"
            md_file = doc_dir / "readme.md"
            if not html_file.is_file():
                if selected_languages is not None:
                    all_errors.append(f"{lang}: documentation HTML not found: {_relative_to_addon(html_file)}")
                continue
            success, msg = _convert_html_file_to_md(html_file, md_file)
            if success:
                print(f"Converted {_relative_to_addon(html_file)} -> {_relative_to_addon(md_file)}")
                converted_count += 1
            else:
                all_errors.append(msg)
        if selected_languages is None and converted_count == 0 and not all_errors:
            print("[INFO] No documentation HTML files found to convert to Markdown.")
        return _print_errors(all_errors)

    if md_to_html:
        if not using_menu and not args.all_languages and selected_languages is None:
            parser.error("--md-to-html requires --language or --all-languages.")
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=True, include_en=True
        )
        if selection_errors:
            return _print_errors(selection_errors)
        all_errors = []
        converted_count = 0
        for lang in target_languages:
            doc_dir = DOC_DIR / lang
            md_file = doc_dir / "readme.md"
            html_file = doc_dir / "readme.html"
            if not md_file.is_file():
                if selected_languages is not None:
                    all_errors.append(f"{lang}: documentation Markdown not found: {_relative_to_addon(md_file)}")
                continue
            success, msg = _convert_md_file_to_html(md_file, html_file, lang)
            if success:
                print(f"Converted {_relative_to_addon(md_file)} -> {_relative_to_addon(html_file)}")
                converted_count += 1
            else:
                all_errors.append(msg)
        if selected_languages is None and converted_count == 0 and not all_errors:
            print("[INFO] No documentation Markdown files found to convert to HTML.")
        return _print_errors(all_errors)

    if update_po:
        if not using_menu and not args.all_languages and selected_languages is None:
            parser.error("--update-po requires --language or --all-languages.")

    if extract_template:
        try:
            template_path = _write_pot(_translatable_source_messages())
            print(f"Updated source string template: {_relative_to_addon(template_path)}")
            return 0
        except (ValueError, OSError) as exc:
            return _print_errors([str(exc)])
    if update_po:
        try:
            required_messages = _translatable_source_messages()
            template_path = _write_pot(required_messages)
        except (ValueError, OSError) as exc:
            return _print_errors([str(exc)])
        print(f"Updated source string template: {_relative_to_addon(template_path)}")
        language_dirs, selection_errors = _language_dirs(selected_languages, allow_create=True)
        if selection_errors:
            return _print_errors(selection_errors)
        msgmerge_path = _find_msgmerge(args.msgmerge)
        if msgmerge_path:
            print(f"msgmerge: {msgmerge_path}")
        else:
            print("[INFO] msgmerge was not found; using internal PO merger.")
        print("")
        all_errors = []
        for language_dir in language_dirs:
            try:
                preserved, added, removed = _update_po_from_template(
                    language_dir,
                    msgmerge_path,
                    required_messages,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                all_errors.append(str(exc))
                continue
            po_path = language_dir / "LC_MESSAGES" / "nvda.po"
            print(f"Updated {_relative_to_addon(po_path)}")
            print(f"  Preserved current source strings: {preserved}")
            print(f"  Added untranslated source strings: {added}")
            print(f"  Removed obsolete entries: {removed}")
        return _print_errors(all_errors)

    locale_dirs = args.nvda_locale_dir or list(DEFAULT_NVDA_LOCALE_DIRS)
    language_dirs, selection_errors = _language_dirs(selected_languages)
    supported_languages, found_locale_dirs = _supported_nvda_languages_from_dirs(locale_dirs)
    if CHECK_LANGUAGE in checks and supported_languages is None:
        print("[WARN] NVDA locale folder was not found; language-code support check is skipped.")
    try:
        source_messages = (
            _translatable_source_messages()
            if CHECK_UI in checks or CHECK_OBSOLETE in checks or not check_only
            else None
        )
    except (ValueError, OSError) as exc:
        return _print_errors([str(exc)])
    all_errors = list(selection_errors)
    if not check_only:
        try:
            template_path = _write_pot(source_messages or _translatable_source_messages())
            print(f"Updated source string template: {_relative_to_addon(template_path)}")
        except (ValueError, OSError) as exc:
            return _print_errors([str(exc)])
    _print_run_summary(language_dirs, checks, check_only, found_locale_dirs)
    for language_dir in language_dirs:
        kwargs = {"msgfmt_path": msgfmt_path} if args.msgfmt is not None else {}
        file_errors = _check_language_files(
            language_dir,
            supported_languages,
            checks,
            check_only,
            **kwargs,
        )
        if file_errors:
            all_errors.extend(file_errors)
            continue
        po_path = language_dir / "LC_MESSAGES" / "nvda.po"
        try:
            check_catalog = _parse_po(po_path, include_untranslated=True)
            fuzzy_msgids = _po_fuzzy_msgids(po_path) if CHECK_FUZZY in checks else None
        except Exception as exc:
            all_errors.append(f"{language_dir.name}: could not parse {_relative_to_addon(po_path)}: {exc}")
            continue
        errors = _check_catalog(language_dir, check_catalog, checks, source_messages, fuzzy_msgids)
        if errors:
            all_errors.extend(errors)
            continue
        if not check_only:
            try:
                catalog = _parse_po(po_path, include_fuzzy=False)
                _compile_mo_file(
                    po_path,
                    language_dir / "LC_MESSAGES" / "nvda.mo",
                    catalog,
                    msgfmt_path=msgfmt_path,
                )
                _write_translated_manifest(language_dir, catalog)
                generated_items = ["LC_MESSAGES\\nvda.mo", "manifest.ini"]
                print(f"Updated {_relative_to_addon(language_dir)}")
                print(f"  Passed: {_describe_checks(checks)}")
                print(f"  Generated: {', '.join(generated_items)}")
            except (OSError, ValueError) as exc:
                all_errors.append(f"{language_dir.name}: failed to write generated files: {exc}")
        else:
            print(f"Checked {_relative_to_addon(language_dir)}")
            print(f"  Passed: {_describe_checks(checks)}")
    return _print_errors(all_errors)


if __name__ == "__main__":
    raise SystemExit(main())

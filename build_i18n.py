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
from collections.abc import Callable
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
DOC_STRUCTURAL_CONTAINER_TAGS = frozenset(
    {
        "ul",
        "ol",
        "dl",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "div",
        "section",
        "article",
        "aside",
        "details",
        "figure",
        "figcaption",
        "header",
        "footer",
        "nav",
        "main",
    }
)


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
# Unnamed percent interpolations (e.g. %s, %d, %10.2f, %-5s, but not %% or 50%+a)
RE_UNNAMED_PERCENT = re.compile(
    r"""
    (?:(?<!\d)%|%(?![+-]))
    [#0+-]*
    \d*
    (?:\.\d+)?
    [hlL]?
    [diouxXeEfFgGcrsa]
    (?![A-Za-z])
    """,
    flags=re.VERBOSE,
)
# Named percent interpolations (e.g. %(name)s, %(count)02d, %(name)-10s)
RE_NAMED_PERCENT = re.compile(r"%\([^()]+\)[#0+\- ]*\d*(?:\.\d+)?[a-zA-Z]")
# Brace format interpolations (e.g. {name}, {name!r}, {name:format}, {0})
RE_FORMAT = re.compile(r"\{([^{}:!]*)(?:![^{}:]*)?(?::[^{}]*)?\}")
VOID_HTML_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link"})
ALL_VOID_HTML_TAGS = VOID_HTML_TAGS | {"area", "base", "col", "embed", "param", "source", "track", "wbr"}
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
_TAG_ATTRS_PATTERN = r"""(?:[\s/](?:[^>"']|"[^"]*"|'[^']*')*)?"""
OPEN_HTML_TAG_RE = re.compile(rf"<([a-zA-Z][a-zA-Z0-9]*){_TAG_ATTRS_PATTERN}>")
_FULL_OPEN_TAG_RE = re.compile(rf"<([a-zA-Z][a-zA-Z0-9]*)({_TAG_ATTRS_PATTERN})>")
CLOSE_HTML_TAG_RE = re.compile(r"</([a-zA-Z][a-zA-Z0-9]*)\s*>")
HTML_TAG_TOKEN_RE = re.compile(rf"<(/?)([a-zA-Z][a-zA-Z0-9]*){_TAG_ATTRS_PATTERN}>")
_TAG_ATTR_PAIR_RE = re.compile(r"""([^\s/>="'<]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']*)))?""")
_EVENT_HANDLER_NAME_RE = re.compile(r"^on[a-z]+$")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _iter_tag_attributes(attrs_raw: str) -> list[tuple[str, str]]:
    attrs: list[tuple[str, str]] = []
    for match in _TAG_ATTR_PAIR_RE.finditer(attrs_raw):
        name = html.unescape(match.group(1)).strip().lower()
        val = (
            match.group(2)
            if match.group(2) is not None
            else match.group(3)
            if match.group(3) is not None
            else (match.group(4) or "")
        )
        attrs.append((name, val))
    return attrs


def _extract_hrefs(text: str) -> list[str]:
    cleaned = HTML_COMMENT_RE.sub("", text)
    hrefs: list[str] = []
    for match in _FULL_OPEN_TAG_RE.finditer(cleaned):
        if match.group(1).lower() != "a":
            continue
        for attr_name, attr_val in _iter_tag_attributes(match.group(2)):
            if attr_name == "href":
                hrefs.append(attr_val)
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


def _split_lf(text: str) -> list[str]:
    """Split text on CR/LF newlines only, preserving Unicode line separators (e.g. U+2028) inside lines."""
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
        if not normalized:
            return [""]
    return normalized.split("\n")


def _decode_po_string(token: str) -> str:
    if not (token.startswith('"') and token.endswith('"')) or token.startswith('"""'):
        raise ValueError(f"malformed PO string {token!r}: expected double-quoted string")
    try:
        val = ast.literal_eval(token)
        if not isinstance(val, str):
            raise ValueError(f"expected str literal, got {type(val).__name__}")
        return val
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"malformed PO string {token!r}: {exc}") from exc


def _match_po_keyword(line: str, keyword: str) -> str | None:
    prefix_len = len(keyword)
    if line == keyword:
        return ""
    if line.startswith(keyword) and line[prefix_len : prefix_len + 1] in (" ", "\t", '"'):
        return line[prefix_len:].strip()
    return None


def _iter_po_records(path: Path) -> list[tuple[str | None, str, str, bool]]:
    entries: list[tuple[str | None, str, str, bool]] = []
    msgctxt_parts: list[str] = []
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []
    plural_parts: list[str] = []
    has_msgctxt = False
    has_msgid = False
    has_msgstr = False
    has_msgstr_zero = False
    active: str | None = None
    next_fuzzy = False
    is_fuzzy = False

    def commit(*, reset_next_fuzzy: bool = False) -> None:
        nonlocal msgctxt_parts, msgid_parts, msgstr_parts, plural_parts
        nonlocal has_msgctxt, has_msgid, has_msgstr, has_msgstr_zero, active, is_fuzzy, next_fuzzy
        if has_msgid:
            if not has_msgstr:
                msgid_preview = "".join(msgid_parts)[:60]
                raise ValueError(f"missing msgstr for msgid {msgid_preview!r} in PO file {path.name}")
            ctx = "".join(msgctxt_parts) if has_msgctxt else None
            msgid = "".join(msgid_parts)
            msgstr = "".join(msgstr_parts) if msgstr_parts else "".join(plural_parts)
            entries.append((ctx, msgid, msgstr, is_fuzzy))
        elif has_msgctxt:
            raise ValueError(f"msgctxt without msgid in PO file {path.name}")
        msgctxt_parts = []
        msgid_parts = []
        msgstr_parts = []
        plural_parts = []
        has_msgctxt = False
        has_msgid = False
        has_msgstr = False
        has_msgstr_zero = False
        active = None
        is_fuzzy = False
        if reset_next_fuzzy:
            next_fuzzy = False

    for raw_line in _split_lf(path.read_text(encoding="utf-8-sig")):
        line = raw_line.strip()
        if not line:
            commit(reset_next_fuzzy=True)
            continue
        if line.startswith("#~"):
            commit(reset_next_fuzzy=True)
            continue
        if line.startswith("#,") and re.search(r"\bfuzzy\b", line):
            next_fuzzy = True
            continue
        if line.startswith("#"):
            continue
        if (rest := _match_po_keyword(line, "msgctxt")) is not None:
            commit()
            active = "msgctxt"
            has_msgctxt = True
            if rest:
                msgctxt_parts.append(_decode_po_string(rest))
            continue
        if line.startswith('"') and active == "msgctxt":
            msgctxt_parts.append(_decode_po_string(line))
            continue
        if (rest := _match_po_keyword(line, "msgid_plural")) is not None:
            if not has_msgid:
                raise ValueError(f"msgid_plural without msgid in PO file {path.name}: {line!r}")
            active = "msgid_plural"
            if rest:
                _decode_po_string(rest)
            continue
        if line.startswith('"') and active == "msgid_plural":
            _decode_po_string(line)
            continue
        plural_match = re.match(r"^msgstr\[(\d+)\](?:\s*(.*))?$", line)
        if plural_match:
            if not has_msgid:
                raise ValueError(f"msgstr[...] without msgid in PO file {path.name}: {line!r}")
            plural_idx = int(plural_match.group(1))
            active = "msgstr_0" if plural_idx == 0 else "msgstr_plural"
            has_msgstr = True
            rest = (plural_match.group(2) or "").strip()
            if rest:
                decoded = _decode_po_string(rest)
                if plural_idx == 0 and not has_msgstr_zero:
                    plural_parts.append(decoded)
            if plural_idx == 0:
                has_msgstr_zero = True
            continue
        if line.startswith('"') and active in ("msgstr_0", "msgstr_plural"):
            decoded = _decode_po_string(line)
            if active == "msgstr_0":
                plural_parts.append(decoded)
            continue
        if (rest := _match_po_keyword(line, "msgid")) is not None:
            if has_msgid:
                commit()
            active = "msgid"
            has_msgid = True
            is_fuzzy = next_fuzzy
            next_fuzzy = False
            if rest:
                msgid_parts.append(_decode_po_string(rest))
            continue
        if (rest := _match_po_keyword(line, "msgstr")) is not None:
            if not has_msgid:
                raise ValueError(f"msgstr without preceding msgid in PO file {path.name}: {line!r}")
            active = "msgstr"
            has_msgstr = True
            if rest:
                msgstr_parts.append(_decode_po_string(rest))
            continue
        if line.startswith('"') and active == "msgid":
            msgid_parts.append(_decode_po_string(line))
            continue
        if line.startswith('"') and active == "msgstr":
            msgstr_parts.append(_decode_po_string(line))
            continue
        raise ValueError(f"unexpected line in PO file {path.name}: {line!r}")
    commit(reset_next_fuzzy=True)
    return entries


def _iter_po_entries(path: Path) -> list[tuple[str, str, bool]]:
    return [(msgid, msgstr, is_fuzzy) for _ctx, msgid, msgstr, is_fuzzy in _iter_po_records(path)]


def _parse_po(
    path: Path,
    *,
    include_untranslated: bool = False,
    include_fuzzy: bool = True,
) -> dict[str, str]:
    entries: dict[str, str] = {}
    for msgid, msgstr, is_fuzzy in _iter_po_entries(path):
        if is_fuzzy and not include_fuzzy and msgid != "":
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
        if not value:
            continue
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
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\a", "\\a")
        .replace("\b", "\\b")
        .replace("\f", "\\f")
        .replace("\v", "\\v")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _po_quoted_lines(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ['""']
    parts = re.findall(r"[^\n]*\n|[^\n]+", normalized)
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
            f'"Language: {_po_escape(language)}\\n"',
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
        for raw_line in _split_lf(po_path.read_text(encoding="utf-8-sig")):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                if not line.startswith(("#:", "#,", "#|", "#~")):
                    comments.append(line)
            else:
                if not re.match(r'^msgid(?:\s+""|"")$', line):
                    return []
                break
    except Exception:
        pass
    return comments


def _default_po_comments(description: str = "translation") -> list[str]:
    return [
        f"# Google TTS For NVDA {description}.",
        "# Copyright (C) 2026 Google TTS For NVDA contributors",
        "# This file is distributed under the same license as the add-on.",
        "#",
    ]


def _merge_po_header(
    old_header_str: str,
    language: str,
    comments: list[str] | None = None,
    default_comments: list[str] | None = None,
) -> list[str]:
    version = _manifest_version()
    lines: list[str] = []
    fallback_comments = default_comments or _default_po_comments("translation")
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
    for hline in _split_lf(old_header_str):
        if ":" in hline:
            k, v = hline.split(":", 1)
            header_dict[k.strip()] = v.strip()

    header_dict["Project-Id-Version"] = f"Google TTS For NVDA {version}"
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
            lines.append(f'"{_po_escape(k)}: {_po_escape(header_dict[k])}\\n"')
    for k, v in header_dict.items():
        if k not in ordered_keys:
            lines.append(f'"{_po_escape(k)}: {_po_escape(v)}\\n"')
    return lines


def _write_pot(messages: dict[str, list[str]], pot_path: Path | None = None) -> Path:
    target_pot = POT_PATH if pot_path is None else pot_path
    lines = _build_po_header(comments=_default_po_comments("translation template"))
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


def _gettext_c_env() -> dict[str, str]:
    return {**os.environ, "LANG": "C", "LANGUAGE": "C", "LC_ALL": "C", "LC_MESSAGES": "C"}


def _run_msgfmt_command(msgfmt_path: Path, output_path: Path, po_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(msgfmt_path), "-o", str(output_path), str(po_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_gettext_c_env(),
        check=False,
    )


def _po_has_charset(po_path: Path) -> bool:
    try:
        content = po_path.read_text(encoding="utf-8-sig", errors="replace")
        return "charset=" in content.lower()
    except Exception:
        return False


def _check_po_syntax_with_msgfmt(
    po_path: Path,
    msgfmt_path: Path | None = None,
    *,
    _verified_charset: bool = False,
) -> list[str]:
    """Check the syntax of a PO file using msgfmt."""
    actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
    if actual_msgfmt is None or not actual_msgfmt.is_file() or not po_path.is_file():
        return []
    if not _verified_charset and not _po_has_charset(po_path):
        return []
    try:
        result = _run_msgfmt_command(actual_msgfmt, Path(os.devnull), po_path)
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


def _check_po_syntax(po_path: Path, msgfmt_path: Path | None = None) -> list[str]:
    """Check PO syntax using msgfmt when usable, falling back to pure-Python validation."""
    if not po_path.is_file():
        return []
    actual_msgfmt = msgfmt_path if msgfmt_path is not None else _find_msgfmt()
    if actual_msgfmt is not None and actual_msgfmt.is_file() and _po_has_charset(po_path):
        return _check_po_syntax_with_msgfmt(po_path, actual_msgfmt, _verified_charset=True)
    return _check_po_syntax_fallback(po_path)


def _check_po_syntax_fallback(po_path: Path) -> list[str]:
    """Pure-Python PO syntax validation when msgfmt is unavailable."""
    if not po_path.is_file():
        return []
    errors: list[str] = []
    seen_keys: set[tuple[str | None, str]] = set()
    try:
        for ctx, msgid, _msgstr, _fuzzy in _iter_po_records(po_path):
            if not msgid:
                continue
            key = (ctx, msgid)
            if key in seen_keys:
                errors.append(f"{po_path.name}: duplicate message definition for {_message_preview(msgid)!r}")
            else:
                seen_keys.add(key)
    except Exception as exc:
        errors.append(f"{po_path.name}: PO syntax error: {exc}")
    return errors


_OBSOLETE_START_RE = re.compile(r"^#~\s*(?:msgctxt|msgid)\b")


def _purge_obsolete_po_entries(text: str) -> tuple[str, int]:
    if not text.strip():
        return "", 0
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n{2,}", normalized.strip())
    kept: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        lines = paragraph.split("\n")
        obs_start_indices = [
            idx for idx, line in enumerate(lines) if line.startswith("#~") and _OBSOLETE_START_RE.match(line)
        ]
        if obs_start_indices:
            has_active_entry = any(
                not line.startswith("#") and line.strip() != "" for line in lines[: obs_start_indices[0]]
            )
            if has_active_entry:
                cut_idx = obs_start_indices[0]
                while cut_idx > 0 and lines[cut_idx - 1].startswith(("#~", "#,", "#.")):
                    cut_idx -= 1
                kept.append("\n".join(lines[:cut_idx]))
                removed += len(obs_start_indices)
            else:
                removed += max(1, len(obs_start_indices))
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
        env=_gettext_c_env(),
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


_CONTROL_WS_RE = re.compile(r"[\s\x00-\x1f]+")
_UNSAFE_HREF_SCHEMES = ("javascript:", "data:", "vbscript:")


def _is_unsafe_href(url: str, *, unescape_first: bool = False) -> bool:
    candidate = html.unescape(url) if unescape_first else url
    return _CONTROL_WS_RE.sub("", candidate).lower().startswith(_UNSAFE_HREF_SCHEMES)


def _update_po_from_template(
    language_dir: Path,
    msgmerge_path: Path | None,
    required_messages: dict[str, list[str]],
) -> tuple[int, int, int]:
    if language_dir.name == "en":
        raise ValueError("en: English is the source language; cannot update PO file.")
    po_path = language_dir / "LC_MESSAGES" / "nvda.po"

    def _build_ui_entries() -> list[tuple[str, list[str]]]:
        return [
            (msgid, [f"#: {loc}" for loc in sorted(locations)])
            for msgid, locations in sorted(required_messages.items(), key=lambda item: item[0].lower())
            if msgid
        ]

    return _merge_po_from_template(
        po_path=po_path,
        pot_path=POT_PATH,
        language=language_dir.name,
        msgmerge_path=msgmerge_path,
        ensure_pot=lambda: _write_pot(required_messages),
        build_entries=_build_ui_entries,
        default_comments=_default_po_comments("translation"),
        init_prefix=".nvda-po-init-",
        update_prefix=".nvda-po-update-",
        category="catalog",
        string_type="source",
    )


def _merge_po_from_template(
    *,
    po_path: Path,
    pot_path: Path,
    language: str,
    msgmerge_path: Path | None,
    ensure_pot: Callable[[], Path],
    build_entries: Callable[[], list[tuple[str, list[str]]]],
    default_comments: list[str],
    init_prefix: str,
    update_prefix: str,
    category: str,
    string_type: str,
    pre_seed_cb: Callable[[], None] | None = None,
) -> tuple[int, int, int]:
    seeded_blank_po = False
    if not po_path.is_file():
        if pre_seed_cb is not None:
            pre_seed_cb()
        if not po_path.is_file():
            po_path.parent.mkdir(parents=True, exist_ok=True)
            if not pot_path.is_file():
                ensure_pot()
            pot_text = pot_path.read_text(encoding="utf-8-sig")
            po_text = pot_text.replace('"Language: \\n"', f'"Language: {_po_escape(language)}\\n"')
            _atomic_write_text(po_path, po_text, prefix=init_prefix)
            seeded_blank_po = True

    temporary_path: Path | None = None
    try:
        old_catalog = _parse_po(po_path, include_untranslated=True, include_fuzzy=True)
        if not pot_path.is_file():
            ensure_pot()
        entry_specs = build_entries()
        required_msgids = {msgid for msgid, _ in entry_specs if msgid}
        old_msgids = set() if seeded_blank_po else (set(old_catalog) - {""})
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=po_path.parent,
            prefix=update_prefix,
            suffix=".tmp",
            delete=False,
        ) as temporary_stream:
            temporary_path = Path(temporary_stream.name)

        if msgmerge_path is not None:
            removed_count = _run_msgmerge(msgmerge_path, po_path, pot_path, temporary_path, language)
        else:
            old_comments = _extract_po_header_comments(po_path)
            lines = _merge_po_header(
                old_catalog.get("", ""),
                language=language,
                comments=old_comments,
                default_comments=default_comments,
            )
            old_fuzzy_msgids = _po_fuzzy_msgids(po_path)
            for msgid, comment_lines in entry_specs:
                if not msgid:
                    continue
                lines.append("")
                if msgid in old_fuzzy_msgids:
                    lines.append("#, fuzzy")
                lines.extend(comment_lines)
                _append_po_string(lines, "msgid", msgid)
                _append_po_string(lines, "msgstr", old_catalog.get(msgid, ""))
            temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            removed_count = len(old_msgids - required_msgids)

        preserved_count, new_count = _verify_merged_po(
            temporary_path,
            required_msgids,
            old_msgids,
            language,
            category=category,
            string_type=string_type,
        )
        temporary_path.replace(po_path)
        temporary_path = None
        return preserved_count, new_count, removed_count
    except Exception:
        if seeded_blank_po:
            po_path.unlink(missing_ok=True)
        raise
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _root_brace_field(field_name: str) -> str:
    return field_name.split(".", 1)[0].split("[", 1)[0]


def _format_set(message: str) -> set[str]:
    fields: set[str] = set()
    try:
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(message):
            if field_name:
                fields.add("{" + _root_brace_field(field_name) + "}")
    except ValueError:
        return set(PLACEHOLDER_RE.findall(message))
    return fields


def _collect_brace_fields(text: str, brace_formats: set[str], unnumbered: list[int]) -> None:
    for _literal, field_name, format_spec, _conversion in string.Formatter().parse(text):
        if field_name is None:
            continue
        root_name = _root_brace_field(field_name)
        if root_name:
            brace_formats.add("{" + root_name + "}")
        else:
            unnumbered[0] += 1
        if format_spec:
            _collect_brace_fields(format_spec, brace_formats, unnumbered)


def _get_interpolations(text: str) -> tuple[list[str], set[str], set[str], int]:
    cleaned_percent = re.sub(
        r"(?:https?|ftps?)://(?:[^\s<>\"'()]|\([^()\s]*\))+|mailto:[^\s<>\"')]+",
        lambda m: re.sub(r"%[0-9A-Fa-f]{2}", "", m.group(0)),
        text.replace("%%", ""),
    )
    unnamed_percent = RE_UNNAMED_PERCENT.findall(cleaned_percent)
    named_percent = set(RE_NAMED_PERCENT.findall(cleaned_percent))
    brace_formats: set[str] = set()
    unnumbered_count = [0]
    try:
        _collect_brace_fields(text, brace_formats, unnumbered_count)
    except ValueError:
        brace_formats.clear()
        unnumbered_count[0] = 0
        cleaned_braces = text.replace("{{", "").replace("}}", "")
        for m in RE_FORMAT.finditer(cleaned_braces):
            field_name = _root_brace_field(m.group(1))
            if field_name:
                brace_formats.add("{" + field_name + "}")
            else:
                unnumbered_count[0] += 1
    return unnamed_percent, named_percent, brace_formats, unnumbered_count[0]


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

    open_comments = len(re.findall(r"<!--", msgstr))
    close_comments = len(re.findall(r"-->", msgstr))
    if open_comments != close_comments:
        alerts.append(f"unbalanced HTML comment: {open_comments} open vs {close_comments} close")

    msgid_clean = HTML_COMMENT_RE.sub("", msgid)
    msgstr_clean = HTML_COMMENT_RE.sub("", msgstr)

    open_tags = OPEN_HTML_TAG_RE.findall(msgstr_clean)
    close_tags = CLOSE_HTML_TAG_RE.findall(msgstr_clean)

    # 1. Disallowed dangerous tags and event handlers check
    for tag in open_tags + close_tags:
        if tag.lower() in DANGEROUS_HTML_TAGS:
            alerts.append(f"disallowed HTML tag: <{tag.lower()}>")

    found_event_handler = False
    found_inline_style = False
    for match in _FULL_OPEN_TAG_RE.finditer(msgstr_clean):
        for attr_name, _attr_val in _iter_tag_attributes(match.group(2)):
            cleaned_attr = _CONTROL_WS_RE.sub("", attr_name)
            if _EVENT_HANDLER_NAME_RE.match(cleaned_attr):
                found_event_handler = True
            elif cleaned_attr == "style":
                found_inline_style = True
    if found_event_handler:
        alerts.append("disallowed inline event handler in HTML tag")
    if found_inline_style:
        alerts.append("disallowed inline style attribute in HTML tag")

    # 2. Balanced count check for each tag
    all_tag_names = set(t.lower() for t in open_tags + close_tags) - ALL_VOID_HTML_TAGS
    for tag in sorted(all_tag_names):
        o = sum(1 for t in open_tags if t.lower() == tag)
        c = sum(1 for t in close_tags if t.lower() == tag)
        if o != c:
            alerts.append(f"unbalanced <{tag}> tag: {o} open vs {c} close")

    # 3. Stack-based nesting order check
    stack: list[str] = []
    for m in HTML_TAG_TOKEN_RE.finditer(msgstr_clean):
        is_close = bool(m.group(1))
        tag_name = m.group(2).lower()
        if tag_name in ALL_VOID_HTML_TAGS:
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

    id_open = [t.lower() for t in OPEN_HTML_TAG_RE.findall(msgid_clean)]
    str_open = [t.lower() for t in open_tags]

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
    id_hrefs = sorted(html.unescape(h).strip() for h in _extract_hrefs(msgid_clean))
    str_hrefs = sorted(html.unescape(h).strip() for h in _extract_hrefs(msgstr_clean))
    if id_hrefs != str_hrefs:
        alerts.append(f"<a> href mismatch: expected {id_hrefs}, got {str_hrefs}")

    for href in str_hrefs:
        if _is_unsafe_href(href):
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
    *,
    context_phrase: str | None = None,
    always_check_html: bool = False,
) -> list[str]:
    alerts: list[str] = []
    scope = f"in {context_phrase} " if context_phrase else ""
    for err in _check_format_interpolations(msgid, msgstr):
        alerts.append(f"{lang_name}: placeholder mismatch {scope}for {target_label}: {err}")
    if always_check_html or "<" in msgid or "<" in msgstr:
        for err in _check_html_tag_interpolations(msgid, msgstr):
            alerts.append(f"{lang_name}: HTML tag issue {scope}for {target_label}: {err}")
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
    checked_msgids: set[str] = set()
    if CHECK_MANIFEST in active_checks:
        manifest_values = _manifest_values()
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
    mo_catalog = dict(catalog)
    header = mo_catalog.get("", "")
    if "charset=" not in header.lower():
        if header and not header.endswith("\n"):
            header += "\n"
        mo_catalog[""] = header + "Content-Type: text/plain; charset=UTF-8\n"

    encoded_entries = sorted((key.encode("utf-8"), mo_catalog[key].encode("utf-8")) for key in mo_catalog)
    ids = [k for k, _ in encoded_entries]
    strs = [v for _, v in encoded_entries]

    key_table_offset = 7 * 4
    value_table_offset = key_table_offset + len(encoded_entries) * 8
    string_offset = value_table_offset + len(encoded_entries) * 8

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
            "<IIIIIII",
            0x950412DE,
            0,
            len(encoded_entries),
            key_table_offset,
            value_table_offset,
            0,
            0,
        )
    )
    for length, offset in key_offsets:
        data.extend(struct.pack("<II", length, offset))
    for length, offset in value_offsets:
        data.extend(struct.pack("<II", length, offset))
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
            res = _run_msgfmt_command(actual_msgfmt, tmp_mo, po_path)
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


def _unescape_manifest_value(value: str, *, multiline: bool = False) -> str:
    escapes = {
        '"': '"',
        "'": "'",
        "\\": "\\",
    }
    if not multiline:
        escapes.update({"n": "\n", "r": "\r", "t": "\t"})
    return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(0)), value)


def _manifest_values() -> dict[str, str]:
    text = MANIFEST_SOURCE.read_text(encoding="utf-8-sig")
    values: dict[str, str] = {}
    for key in MANIFEST_KEYS:
        match = re.search(
            rf'^{re.escape(key)}[ \t]*=[ \t]*(?:"""((?:\\.|(?!"\"\").)*?)"""|\'\'\'((?:\\.|(?!\'\'\').)*?)\'\'\')',
            text,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            raw = match.group(1) if match.group(1) is not None else match.group(2)
            values[key] = _unescape_manifest_value(raw, multiline=True)
        else:
            match_single = re.search(
                rf'^{re.escape(key)}[ \t]*=[ \t]*(?:"((?:\\.|[^"\r\n\\])*)"|\'((?:\\.|[^\'\r\n\\])*)\')',
                text,
                re.MULTILINE,
            )
            if match_single:
                raw = match_single.group(1) if match_single.group(1) is not None else match_single.group(2)
                values[key] = _unescape_manifest_value(raw, multiline=False)
            else:
                match_unquoted = re.search(rf"^{re.escape(key)}[ \t]*=[ \t]*([^\s\r\n#][^\r\n#]*)", text, re.MULTILINE)
                if match_unquoted:
                    values[key] = match_unquoted.group(1).strip()
    return values


def _quote_manifest_value(value: str) -> str:
    cleaned = value.replace("\r", "").replace("\n", " ")
    return '"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_multiline_manifest_value(value: str) -> str:
    formatted = value.replace("\r\n", "\n").replace("\r", "\n")
    formatted = formatted.replace("\\", "\\\\").replace('"""', r"\"\"\"")
    if formatted.endswith('"'):
        formatted += "\n"
    return f'"""{formatted}"""'


def _write_translated_manifest(language_dir: Path, catalog: dict[str, str]) -> None:
    values = _manifest_values()
    lines: list[str] = []
    if "summary" in values:
        summary = catalog.get(values["summary"])
        if not summary or not summary.strip():
            summary = values["summary"]
        lines.append(f"summary = {_quote_manifest_value(summary)}\n")
    for key in ("description", "changelog"):
        if key in values:
            translated = catalog.get(values[key])
            if not translated or not translated.strip():
                translated = values[key]
            lines.append(f"{key} = {_format_multiline_manifest_value(translated)}\n")
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


_WINDOWS_RESERVED_DIR_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_INVALID_LOCALE_DIR_CHARS_RE = re.compile(r'[\s\x00-\x1f<>:"/\\|?*]')


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
        dirs_to_create: list[tuple[str, Path]] = []
        for raw in requested_languages:
            codes = [c.strip() for c in raw.split(",")] if "," in raw else [raw]
            for language in codes:
                norm = _normalize_language_code(language)
                if not norm:
                    errors.append("empty language code.")
                    continue
                if (
                    norm in (".", "..")
                    or _INVALID_LOCALE_DIR_CHARS_RE.search(norm)
                    or norm.rstrip(".").upper() in _WINDOWS_RESERVED_DIR_NAMES
                ):
                    errors.append(f"{norm}: invalid language code.")
                    continue
                if norm == "en":
                    if include_en:
                        if "en" not in target:
                            target.append("en")
                    else:
                        errors.append(f"en: {en_source_msg}")
                    continue
                target_dir = base_dir / norm
                try:
                    resolved_parent = target_dir.resolve().parent
                    resolved_base = base_dir.resolve()
                except OSError:
                    errors.append(f"{norm}: invalid language code.")
                    continue
                if resolved_parent != resolved_base:
                    errors.append(f"{norm}: invalid language code.")
                    continue
                if not target_dir.is_dir():
                    if allow_create:
                        dirs_to_create.append((norm, target_dir))
                    else:
                        errors.append(f"{norm}: {folder_type_name} folder is missing: {_relative_to_addon(target_dir)}")
                        continue
                if norm not in target:
                    target.append(norm)
        # Validate every requested code before creating missing directories on disk.
        if allow_create and not errors:
            for norm, target_dir in dirs_to_create:
                if target_dir.is_dir():
                    continue
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    errors.append(f"{norm}: could not create {folder_type_name} folder: {exc}")
                    target = [lang for lang in target if lang != norm]
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


_OPTIONAL_DOC_CLOSE_TRIGGERS: dict[str, frozenset[str]] = {
    "p": frozenset(
        {
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "ul",
            "ol",
            "dl",
            "table",
            "blockquote",
            "pre",
            "hr",
            "div",
            "section",
            "article",
            "aside",
            "details",
            "figure",
            "header",
            "footer",
            "nav",
            "main",
        }
    ),
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "th": frozenset({"th", "td", "tr", "thead", "tbody", "tfoot"}),
    "td": frozenset({"th", "td", "tr", "thead", "tbody", "tfoot"}),
    "tr": frozenset({"tr", "thead", "tbody", "tfoot"}),
}


def _close_optional_doc_tags(stack: list[str], tag_lower: str, flush_cb: Callable[[], None]) -> None:
    while stack:
        top = stack[-1]
        triggers = _OPTIONAL_DOC_CLOSE_TRIGGERS.get(top)
        if triggers is None or tag_lower not in triggers:
            break
        if top in TRANSLATABLE_DOC_TAGS:
            flush_cb()
        stack.pop()


class _BaseDocHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.current_data: list[str] = []

    def _in_translatable(self) -> bool:
        return bool(self.stack and self.stack[-1] in TRANSLATABLE_DOC_TAGS)

    def _emit_raw(self, text: str) -> None:
        pass

    def _flush_segment(self) -> None:
        pass

    def _on_enter_translatable(self) -> None:
        pass

    def _on_return_to_translatable(self) -> None:
        pass

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        _close_optional_doc_tags(self.stack, tag_lower, self._flush_segment)
        if tag_lower in DOC_STRUCTURAL_CONTAINER_TAGS:
            if self._in_translatable():
                self._flush_segment()
            self.stack.append(tag_lower)
            self._emit_raw(f"<{tag}{_format_html_attrs(attrs)}>")
        elif tag_lower in TRANSLATABLE_DOC_TAGS:
            if self._in_translatable():
                self._flush_segment()
            self.stack.append(tag_lower)
            self._on_enter_translatable()
            self._emit_raw(f"<{tag}{_format_html_attrs(attrs)}>")
        else:
            rendered = f"<{tag}{_format_html_attrs(attrs)}>"
            if self._in_translatable():
                self.current_data.append(rendered)
            else:
                self._emit_raw(rendered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_str = _format_html_attrs(attrs)
        rendered = f"<{tag}{attr_str}>" if tag_lower in ALL_VOID_HTML_TAGS else f"<{tag}{attr_str}></{tag}>"
        if self._in_translatable():
            self.current_data.append(rendered)
        else:
            self._emit_raw(rendered)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ALL_VOID_HTML_TAGS:
            return
        if tag_lower in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top in TRANSLATABLE_DOC_TAGS:
                    self._flush_segment()
                if top == tag_lower:
                    self._emit_raw(f"</{tag}>")
                    break
            if self._in_translatable():
                self._on_return_to_translatable()
        elif self._in_translatable():
            self.current_data.append(f"</{tag}>")
        else:
            self._emit_raw(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._in_translatable():
            self.current_data.append(data)
        else:
            self._emit_raw(data)

    def handle_entityref(self, name: str) -> None:
        entity = f"&{name};"
        if self._in_translatable():
            self.current_data.append(entity)
        else:
            self._emit_raw(entity)

    def handle_charref(self, name: str) -> None:
        charref = f"&#{name};"
        if self._in_translatable():
            self.current_data.append(charref)
        else:
            self._emit_raw(charref)

    def handle_comment(self, data: str) -> None:
        comment = f"<!--{data}-->"
        if self._in_translatable():
            self.current_data.append(comment)
        else:
            self._emit_raw(comment)


class _DocHtmlExtractor(_BaseDocHtmlParser):
    def __init__(self) -> None:
        super().__init__()
        self.segments: list[tuple[int, str]] = []
        self.current_start_line = 0

    def _flush_segment(self) -> None:
        text = "".join(self.current_data).strip()
        if text:
            self.segments.append((self.current_start_line, text))
        self.current_data = []

    def _on_enter_translatable(self) -> None:
        self.current_start_line = self.getpos()[0]

    def _on_return_to_translatable(self) -> None:
        self.current_start_line = self.getpos()[0]

    def close(self) -> None:
        if self.current_data and self._in_translatable():
            self._flush_segment()
        super().close()


def _extract_doc_segments(html_text: str) -> list[tuple[int, str]]:
    extractor = _DocHtmlExtractor()
    extractor.feed(html_text)
    extractor.close()
    return extractor.segments


class _DocHtmlRebuilder(_BaseDocHtmlParser):
    def __init__(self, catalog: dict[str, str], target_lang: str, is_rtl: bool) -> None:
        super().__init__()
        self.catalog = catalog
        self.target_lang = target_lang
        self.is_rtl = is_rtl
        self.output: list[str] = []

    def _emit_raw(self, text: str) -> None:
        self.output.append(text)

    def _flush_segment(self) -> None:
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
        if tag.lower() == "html":
            attr_dict = dict(attrs)
            attr_dict["lang"] = self.target_lang.replace("_", "-")
            if self.is_rtl:
                attr_dict["dir"] = "rtl"
            elif "dir" in attr_dict:
                del attr_dict["dir"]
            self.output.append(f"<{tag}{_format_html_attrs(attr_dict)}>")
            return
        super().handle_starttag(tag, attrs)

    def close(self) -> None:
        if self.current_data:
            self._flush_segment()
        super().close()


def _doc_segment_locations(segments: list[tuple[int, str]], html_path: Path) -> tuple[str, dict[str, list[int]]]:
    en_rel = _relative_to_addon(html_path).as_posix()
    aggregated: dict[str, list[int]] = {}
    for line_no, segment in segments:
        aggregated.setdefault(segment, []).append(line_no)
    return en_rel, aggregated


def _format_doc_location(en_rel: str, line_numbers: list[int] | None) -> str:
    if not line_numbers:
        return en_rel
    return " ".join(f"{en_rel}:{ln}" for ln in line_numbers)


def _write_doc_pot(pot_path: Path | None = None) -> Path:
    target_pot = DOC_POT_PATH if pot_path is None else pot_path
    if not DOC_EN_PATH.is_file():
        raise RuntimeError(f"English source document not found: {_relative_to_addon(DOC_EN_PATH)}")
    html_text = DOC_EN_PATH.read_text(encoding="utf-8-sig")
    try:
        segments = _extract_doc_segments(html_text)
    except Exception as exc:
        raise RuntimeError(f"Could not parse {_relative_to_addon(DOC_EN_PATH)}: {exc}") from exc

    en_rel, aggregated = _doc_segment_locations(segments, DOC_EN_PATH)
    lines = _build_po_header(comments=_default_po_comments("documentation translation template"))

    for msgid, line_numbers in aggregated.items():
        lines.append("")
        lines.append(f"#: {_format_doc_location(en_rel, line_numbers)}")
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
            errors.extend(
                _check_string_interpolations(
                    en_seg,
                    lang_seg,
                    repr(_message_preview(en_seg)),
                    language,
                    context_phrase=doc_name,
                    always_check_html=True,
                )
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

        lines = _build_po_header(comments=_default_po_comments("documentation translation"), language=language)
        en_rel, aggregated = _doc_segment_locations(en_ext_segments, en_html_path)

        for msgid, line_numbers in aggregated.items():
            lines.append("")
            lines.append(f"#: {_format_doc_location(en_rel, line_numbers)}")
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


def _remove_stale_doc_mo_files(doc_dir: Path) -> None:
    if doc_dir.is_dir():
        for stale_mo in doc_dir.glob("*.mo"):
            stale_mo.unlink(missing_ok=True)


def _update_doc_po_from_template(
    language: str,
    msgmerge_path: Path | None,
) -> tuple[int, int, int]:
    if language == "en":
        raise ValueError("en: English is the documentation source; cannot update PO file.")
    doc_dir = DOC_DIR / language
    _remove_stale_doc_mo_files(doc_dir)
    po_path = doc_dir / "readme.po"
    html_path = doc_dir / "readme.html"
    md_path = doc_dir / "readme.md"

    def _pre_seed_doc_po() -> None:
        md_ok = True
        if md_path.is_file() and language != "en":
            md_ok, _ = _convert_md_file_to_html(md_path, html_path, language)
        if md_ok and html_path.is_file() and language != "en":
            _extract_po_from_doc_html(DOC_EN_PATH, html_path, language, po_path)

    def _build_doc_entries() -> list[tuple[str, list[str]]]:
        pot_catalog = _parse_po(DOC_POT_PATH, include_untranslated=True)
        doc_segments: list[tuple[int, str]] = []
        if DOC_EN_PATH.is_file():
            with contextlib.suppress(Exception):
                doc_segments = _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))
        en_rel, doc_locations = _doc_segment_locations(doc_segments, DOC_EN_PATH)
        return [
            (msgid, [f"#: {_format_doc_location(en_rel, doc_locations.get(msgid))}"]) for msgid in pot_catalog if msgid
        ]

    return _merge_po_from_template(
        po_path=po_path,
        pot_path=DOC_POT_PATH,
        language=language,
        msgmerge_path=msgmerge_path,
        ensure_pot=_write_doc_pot,
        build_entries=_build_doc_entries,
        default_comments=_default_po_comments("documentation translation"),
        init_prefix=".readme-po-init-",
        update_prefix=".readme-po-update-",
        category="doc catalog",
        string_type="documentation",
        pre_seed_cb=_pre_seed_doc_po,
    )


# ---------------------------------------------------------------------------
# Markdown <-> HTML documentation conversion
# ---------------------------------------------------------------------------

_DOC_MARKDOWN_EXTENSIONS: tuple[str, ...] = (
    "markdown.extensions.extra",
    "pymdownx.superfences",
    "markdown.extensions.toc",
    "mdx_truly_sane_lists",
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
    attributes_with_class = {"div", "span", "a", "th", "td", "code", "pre"}

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
        "msup",
        "msqrt",
        "mroot",
        "msubsup",
        "mover",
        "munder",
        "munderover",
        "mtext",
        "mspace",
        "mtable",
        "mtr",
        "mtd",
        "mfenced",
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
    """Preprocess markdown lines such as removing inline linter comments."""
    cleaned_lines = []
    for line in md_text.splitlines():
        cleaned_lines.append(RE_INLINE_MARKDOWNLINT_COMMENT.sub(r"\1\2", line))
    return "\n".join(cleaned_lines)


_FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")


def _update_code_fence(stripped: str, fence_char: str, fence_len: int) -> tuple[str, str, int, str]:
    """Track fenced code block transitions ('open', 'close', or 'none')."""
    fence_match = _FENCE_LINE_RE.match(stripped)
    if not fence_match:
        return "none", fence_char, fence_len, ""
    marker, info_rest = fence_match.groups()
    if not fence_char:
        if marker[0] == "`" and "`" in info_rest:
            return "none", "", 0, ""
        return "open", marker[0], len(marker), info_rest.strip()
    if marker[0] == fence_char and len(marker) >= fence_len and not info_rest.strip():
        return "close", "", 0, ""
    return "none", fence_char, fence_len, ""


def _get_title_from_markdown(md_text: str) -> str:
    """Extract document title from markdown heading or title comment."""
    kc_re = re.compile(r"^<!--\s*(?:KC:)?title:\s*(.+?)\s*-->$", re.IGNORECASE)
    fence_char = ""
    fence_len = 0
    for line in md_text.splitlines():
        stripped = line.strip()
        event, fence_char, fence_len, _ = _update_code_fence(stripped, fence_char, fence_len)
        if event in ("open", "close") or fence_char:
            continue
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


_TABLE_CELL_STYLE_ALIGN_RE = re.compile(
    r'<(th|td)\b([^>]*?)\s+style=(["\'])\s*text-align:\s*(left|center|right)\s*;?\s*\3([^>]*)>',
    re.IGNORECASE,
)


def _rewrite_table_cell_align_styles(html_text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        tag, before, _quote, align, after = match.groups()
        attrs = before + after
        if re.search(r"\balign\s*=", attrs, re.IGNORECASE):
            return f"<{tag}{attrs}>"
        return f'<{tag}{attrs} align="{align.lower()}">'

    return _TABLE_CELL_STYLE_ALIGN_RE.sub(_replace, html_text)


def _sanitize_doc_html(html_text: str) -> str:
    return nh3.clean(
        html_text,
        tags=_create_doc_tag_filter(),
        attributes=_create_doc_attribute_filter(),
        attribute_filter=_doc_attribute_filter,
        link_rel=None,
        strip_comments=False,
    )


def _generate_doc_html_body(md: str) -> str:
    """Convert markdown text to sanitized HTML using markdown + nh3 pipeline."""
    if not (_MARKDOWN_AVAILABLE and markdown is not None and _NH3_AVAILABLE and nh3 is not None):
        return _fallback_markdown_to_html_body(md)

    active_extensions = _get_active_doc_markdown_extensions()

    html_output = markdown.markdown(
        text=md,
        extensions=active_extensions,
        extension_configs=_DOC_MARKDOWN_EXTENSIONS_CONFIG,
    )
    html_output = _rewrite_table_cell_align_styles(html_output)
    return _sanitize_doc_html(html_output)


def _scan_backtick_span(text: str, start: int) -> tuple[int, int]:
    """Scan a backtick span starting at `start`; return (tick_end, close_start or -1)."""
    length = len(text)
    tick_end = start
    while tick_end < length and text[tick_end] == "`":
        tick_end += 1
    fence_len = tick_end - start
    search = tick_end
    while search < length:
        if text[search] == "`":
            run_end = search
            while run_end < length and text[run_end] == "`":
                run_end += 1
            if run_end - search == fence_len:
                return tick_end, search
            search = run_end
        else:
            search += 1
    return tick_end, -1


def _format_inline_code_span(code_text: str) -> str:
    max_ticks = max((len(m.group(0)) for m in re.finditer(r"`+", code_text)), default=0)
    fence = "`" * (max_ticks + 1)
    if code_text and (code_text.startswith("`") or code_text.endswith("`")):
        code_text = f" {code_text} "
    return f"{fence}{code_text}{fence}"


def _escape_table_cell_pipes(text: str) -> str:
    parts: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        if text[pos] == "`":
            tick_end, close_start = _scan_backtick_span(text, pos)
            if close_start != -1:
                close_end = close_start + (tick_end - pos)
                parts.append(text[pos:close_end])
                pos = close_end
                continue
            parts.append(text[pos:tick_end])
            pos = tick_end
            continue
        parts.append(r"\|" if text[pos] == "|" else text[pos])
        pos += 1
    return "".join(parts)


class _DocHtmlToMarkdown(HTMLParser):
    _BLOCK_CONTAINER_START_TAGS = frozenset(
        {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dt", "dd", "ul", "ol", "dl", "table", "pre", "hr"}
    )
    _END_TAG_IMPLICIT_CHILDREN: dict[str, tuple[str, ...]] = {
        **{container: ("p",) for container in DOC_STRUCTURAL_CONTAINER_TAGS},
        "li": ("p",),
        "dd": ("p",),
        "dt": ("p",),
        "blockquote": ("p",),
        "ul": ("p", "li"),
        "ol": ("p", "li"),
        "dl": ("p", "dt", "dd"),
        "tr": ("p", "td", "th"),
        "thead": ("p", "td", "th", "tr"),
        "tbody": ("p", "td", "th", "tr"),
        "tfoot": ("p", "td", "th", "tr"),
        "table": ("p", "td", "th", "tr"),
    }
    _PRESERVED_PROSE_ENTITIES = frozenset(
        {"rarr", "larr", "uarr", "darr", "copy", "nbsp", "trade", "lt", "gt", "amp", "quot"}
    )
    _CHARREF_PROSE_ESCAPES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.stack: list[str] = []
        self.list_stack: list[dict[str, Any]] = []
        self.current_buf: list[str] = []
        self.link_stack: list[str | None] = []
        self.title: str = ""
        self.in_pre: bool = False
        self.pre_lang: str = ""
        self.pre_buf: list[str] = []
        self.in_inline_code: bool = False
        self.inline_code_buf: list[str] = []
        self.in_table: bool = False
        self.table_depth: int = 0
        self.table_rows: list[list[str]] = []
        self.table_alignments: list[str | None] = []
        self.current_table_row: list[str] = []
        self.in_caption: bool = False

    def _in_cell(self) -> bool:
        return "td" in self.stack or "th" in self.stack

    def _quote_prefix(self) -> str:
        return "> " if "blockquote" in self.stack else ""

    def _flush_buf(self) -> str:
        text = "".join(self.current_buf)
        self.current_buf = []
        return text

    def _ensure_buf_space(self) -> None:
        if (
            self.current_buf
            and "".join(self.current_buf).strip()
            and not self.current_buf[-1].endswith((" ", "\t", "\n"))
        ):
            self.current_buf.append(" ")

    def _close_inline_code(self) -> None:
        if not self.in_inline_code:
            return
        self.in_inline_code = False
        code_text = "".join(self.inline_code_buf)
        self.inline_code_buf = []
        self.current_buf.append(_format_inline_code_span(code_text))

    def _consume_li_bullet(self, info: dict[str, Any], q_prefix: str, indent: str) -> str:
        if info.get("type") == "ol":
            bullet = f"{q_prefix}{indent}{info['index']}. "
            info["index"] = int(str(info["index"])) + 1
        else:
            bullet = f"{q_prefix}{indent}* "
        info["li_bullet_emitted"] = True
        return bullet

    def _emit_li_line(self, text: str, *, leading_newline: bool = False) -> None:
        if not text:
            return
        q_prefix = self._quote_prefix()
        if self.list_stack:
            info = self.list_stack[-1]
            indent = "  " * (len(self.list_stack) - 1)
            if not info.get("li_bullet_emitted", False):
                prefix = self._consume_li_bullet(info, q_prefix, indent)
                self.output.append(f"{prefix}{text}\n")
            else:
                nl = f"{q_prefix.rstrip()}\n" if leading_newline else ""
                self.output.append(f"{nl}{q_prefix}{indent}  {text}\n")
        else:
            self.output.append(f"\n\n{q_prefix}{text}\n\n")

    def _flush_pending_li_text(self) -> None:
        text = self._flush_buf().strip()
        if text:
            self._emit_li_line(text)

    def _emit_blockquote_lines(self, text: str) -> None:
        lines = [f"> {line}" for line in text.splitlines()]
        if "li" in self.stack and self.list_stack:
            for line in lines:
                self._emit_li_line(line)
        else:
            self.output.append("\n\n" + "\n".join(lines) + "\n\n")

    def _flush_blockquote_text(self) -> None:
        text = self._flush_buf().strip()
        if text:
            self._emit_blockquote_lines(text)

    def _emit_pre_block(self) -> None:
        self.in_pre = False
        code_text = "".join(self.pre_buf).strip("\r\n")
        self.pre_buf = []
        lang = self.pre_lang
        self.pre_lang = ""
        if self._in_cell():
            inline_text = re.sub(r"[\r\n]+", " ", code_text).strip()
            if inline_text:
                self._ensure_buf_space()
                self.current_buf.append(_format_inline_code_span(inline_text))
            return

        max_ticks = max((len(m.group(0)) for m in re.finditer(r"`+", code_text)), default=0)
        fence = "`" * max(3, max_ticks + 1)
        q_prefix = self._quote_prefix()
        if self.list_stack:
            info = self.list_stack[-1]
            indent = "  " * (len(self.list_stack) - 1)
            if not info.get("li_bullet_emitted", False):
                bullet = self._consume_li_bullet(info, q_prefix, indent)
                self.output.append(f"{bullet}\n")
            block_indent = f"{q_prefix}{indent}  "
            lines = [
                f"{block_indent}{line}" if line else q_prefix.rstrip()
                for line in f"{fence}{lang}\n{code_text}\n{fence}".split("\n")
            ]
            self.output.append("\n" + "\n".join(lines) + "\n\n")
            return
        if q_prefix:
            lines = [
                f"{q_prefix}{line}" if line else ">" for line in f"{fence}{lang}\n{code_text}\n{fence}".split("\n")
            ]
            self.output.append("\n\n" + "\n".join(lines) + "\n\n")
            return
        self.output.append(f"\n\n{fence}{lang}\n{code_text}\n{fence}\n\n")

    def _emit_markdown_table(self) -> None:
        if self._in_cell() or "td" in self.stack or "th" in self.stack:
            for open_cell in ("td", "th"):
                if open_cell in self.stack:
                    self.handle_endtag(open_cell)
        if self.current_table_row:
            self.table_rows.append(self.current_table_row)
            self.current_table_row = []
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

    def _close_optional_tags_md(self, tag_lower: str) -> None:
        while self.stack:
            top = self.stack[-1]
            triggers = _OPTIONAL_DOC_CLOSE_TRIGGERS.get(top)
            if triggers is None or tag_lower not in triggers:
                break
            self.handle_endtag(top)

    def _close_open_children_before(self, parent_tag: str, child_tags: tuple[str, ...]) -> None:
        while parent_tag in self.stack:
            parent_idx = len(self.stack) - 1 - self.stack[::-1].index(parent_tag)
            top = self.stack[-1]
            if top not in child_tags or (len(self.stack) - 1) <= parent_idx:
                break
            self.handle_endtag(top)

    def _pop_stack_to(self, tag_lower: str) -> bool:
        if tag_lower not in self.stack:
            return False
        idx = len(self.stack) - 1 - self.stack[::-1].index(tag_lower)
        popped = self.stack[idx:]
        del self.stack[idx:]
        for popped_tag in popped:
            if popped_tag == "a" and self.link_stack:
                self.link_stack.pop()
        return True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = dict(attrs)
        if self.in_pre:
            if tag_lower == "code":
                cls = attr_dict.get("class") or ""
                m = re.search(r"(?:^|\s)(?:language-|lang-)([A-Za-z0-9_+#.-]+)(?=\s|$)", cls)
                if m:
                    self.pre_lang = m.group(1)
            elif tag_lower == "br":
                self.pre_buf.append("\n")
            return

        if not self._in_cell() and tag_lower in self._BLOCK_CONTAINER_START_TAGS:
            self._close_optional_tags_md(tag_lower)

        parent_tag = self.stack[-1] if self.stack else None
        in_cell = self._in_cell()
        if in_cell and tag_lower in ("th", "td", "tr"):
            self._close_optional_tags_md(tag_lower)
            parent_tag = self.stack[-1] if self.stack else None
            in_cell = self._in_cell()

        if self.in_inline_code and tag_lower in self._BLOCK_CONTAINER_START_TAGS:
            self._close_inline_code()

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "dt", "dd", "title", "caption"):
            if in_cell:
                self._ensure_buf_space()
            elif parent_tag == "blockquote" and tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"):
                self._flush_blockquote_text()
            elif "li" in self.stack and tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"):
                self._flush_pending_li_text()
            elif parent_tag == "dd" and tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"):
                lead = self._flush_buf().strip()
                if lead:
                    self.output.append(f":   {lead}\n\n")
            else:
                pending = self._flush_buf().strip()
                if pending:
                    self.output.append(f"\n\n{pending}\n\n")
            if tag_lower == "caption":
                self.in_caption = True
            if tag_lower not in ALL_VOID_HTML_TAGS:
                self.stack.append(tag_lower)
            return

        if tag_lower in ("ul", "ol"):
            if not in_cell:
                if parent_tag == "blockquote":
                    self._flush_blockquote_text()
                elif "li" in self.stack:
                    self._flush_pending_li_text()
                self.list_stack.append({"type": tag_lower, "index": 1, "li_bullet_emitted": False})
            self.stack.append(tag_lower)
            return

        if tag_lower == "li":
            if in_cell:
                self._ensure_buf_space()
            else:
                self.current_buf = []
                if self.list_stack:
                    self.list_stack[-1]["li_bullet_emitted"] = False
            self.stack.append(tag_lower)
            return

        if tag_lower == "table":
            if self.in_table:
                self.table_depth += 1
            else:
                self.in_table = True
                self.table_depth = 1
                self.table_rows = []
                self.table_alignments = []
                self.current_table_row = []
            self.stack.append(tag_lower)
            return

        if tag_lower == "tr":
            if not in_cell and self.table_depth <= 1:
                self.current_table_row = []
            self.stack.append(tag_lower)
            return

        if tag_lower in ("th", "td"):
            if self.table_depth <= 1:
                self.current_buf = []
                if len(self.table_rows) == 0:
                    align = (attr_dict.get("align") or "").lower()
                    if not align:
                        style = attr_dict.get("style") or ""
                        m = re.search(r"text-align:\s*(left|center|right)", style, re.IGNORECASE)
                        if m:
                            align = m.group(1).lower()
                    self.table_alignments.append(align if align in ("left", "center", "right") else None)
            else:
                self._ensure_buf_space()
            self.stack.append(tag_lower)
            return

        if tag_lower == "pre":
            if not in_cell:
                if parent_tag == "blockquote":
                    self._flush_blockquote_text()
                elif "li" in self.stack:
                    self._flush_pending_li_text()
            self.in_pre = True
            self.pre_lang = ""
            self.pre_buf = []
            self.stack.append(tag_lower)
            return

        if tag_lower not in ALL_VOID_HTML_TAGS:
            self.stack.append(tag_lower)

        if self.in_inline_code:
            return

        if tag_lower in ("strong", "b"):
            self.current_buf.append("**")
        elif tag_lower in ("em", "i"):
            self.current_buf.append("*")
        elif tag_lower == "code":
            self.in_inline_code = True
            self.inline_code_buf = []
        elif tag_lower == "kbd":
            self.current_buf.append("<kbd>")
        elif tag_lower == "a":
            href = attr_dict.get("href")
            self.link_stack.append(href)
            if href is not None:
                self.current_buf.append("[")
        elif tag_lower == "hr":
            if not in_cell:
                if "li" in self.stack and self.list_stack:
                    self._flush_pending_li_text()
                else:
                    pending = self._flush_buf().strip()
                    if pending:
                        self.output.append(f"\n\n{pending}\n\n")
                self.output.append("\n\n---\n\n")
        elif tag_lower == "br":
            self.current_buf.append(" " if in_cell else "  \n")
        elif tag_lower == "img":
            alt = attr_dict.get("alt") or ""
            src = attr_dict.get("src") or ""
            self.current_buf.append(f"![{alt}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in ALL_VOID_HTML_TAGS:
            return

        if self.in_pre:
            if tag_lower == "pre":
                self._pop_stack_to("pre")
                self._emit_pre_block()
            return

        if tag_lower != "code" and self.in_inline_code and tag_lower in self._BLOCK_CONTAINER_START_TAGS:
            self._close_inline_code()

        if tag_lower == "a":
            href = self.link_stack.pop() if self.link_stack else None
            self._pop_stack_to("a")
            if href is not None and not self.in_inline_code:
                self.current_buf.append(f"]({href})")
            return

        implicit_children = self._END_TAG_IMPLICIT_CHILDREN.get(tag_lower)
        if implicit_children:
            self._close_open_children_before(tag_lower, implicit_children)

        self._pop_stack_to(tag_lower)
        in_cell = self._in_cell()

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if in_cell:
                return
            level = int(tag_lower[1])
            text = self._flush_buf().strip()
            if text:
                q_prefix = self._quote_prefix()
                heading = f"{'#' * level} {text}"
                if "li" in self.stack and self.list_stack:
                    self._emit_li_line(heading, leading_newline=True)
                else:
                    self.output.append(f"\n\n{q_prefix}{heading}\n\n")
        elif tag_lower == "p":
            if in_cell:
                return
            text = self._flush_buf().strip()
            if text:
                if "blockquote" in self.stack:
                    self._emit_blockquote_lines(text)
                elif "dd" in self.stack:
                    self.output.append(f":   {text}\n\n")
                elif "dt" in self.stack:
                    self.output.append(f"\n\n{text}\n")
                elif "li" in self.stack and self.list_stack:
                    self._emit_li_line(text, leading_newline=True)
                else:
                    self.output.append(f"\n\n{text}\n\n")
        elif tag_lower == "dt":
            if in_cell:
                return
            text = self._flush_buf().strip()
            if text:
                self.output.append(f"\n\n{text}\n")
        elif tag_lower == "dd":
            if in_cell:
                return
            text = self._flush_buf().strip()
            if text:
                self.output.append(f":   {text}\n\n")
        elif tag_lower == "li":
            if in_cell:
                return
            self._flush_pending_li_text()
        elif tag_lower in ("ul", "ol"):
            if in_cell:
                return
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self.output.append("\n")
        elif tag_lower == "caption":
            self.in_caption = False
            if in_cell:
                return
            text = self._flush_buf().strip()
            if text:
                self.output.append(f"\n\n{text}\n\n")
        elif tag_lower in ("th", "td"):
            if self.table_depth <= 1:
                if self.in_inline_code:
                    self._close_inline_code()
                raw_cell = self._flush_buf().strip().replace("\r", "").replace("\n", " ")
                self.current_table_row.append(_escape_table_cell_pipes(raw_cell))
        elif tag_lower == "tr":
            if not in_cell and self.table_depth <= 1:
                if self.current_table_row:
                    self.table_rows.append(self.current_table_row)
                self.current_table_row = []
        elif tag_lower == "table":
            if self.table_depth > 1:
                self.table_depth -= 1
            else:
                self.table_depth = 0
                self._emit_markdown_table()
        elif tag_lower == "blockquote":
            if in_cell:
                return
            self._flush_blockquote_text()
        elif tag_lower == "code":
            self._close_inline_code()
        elif tag_lower == "pre":
            self._emit_pre_block()
        elif tag_lower == "title":
            self.title = self._flush_buf().strip()
        elif not self.in_inline_code:
            if tag_lower in ("strong", "b"):
                self.current_buf.append("**")
            elif tag_lower in ("em", "i"):
                self.current_buf.append("*")
            elif tag_lower == "kbd":
                self.current_buf.append("</kbd>")

    def handle_data(self, data: str) -> None:
        if self.in_pre:
            self.pre_buf.append(data)
        elif self.in_inline_code:
            self.inline_code_buf.append(data)
        else:
            self.current_buf.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.in_pre:
            self.pre_buf.append(html.unescape(f"&{name};"))
        elif self.in_inline_code or "code" in self.stack:
            self.inline_code_buf.append(html.unescape(f"&{name};"))
        elif name in self._PRESERVED_PROSE_ENTITIES:
            self.current_buf.append(f"&{name};")
        else:
            self.current_buf.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        ch = html.unescape(f"&#{name};")
        if self.in_pre:
            self.pre_buf.append(ch)
        elif self.in_inline_code or "code" in self.stack:
            self.inline_code_buf.append(ch)
        else:
            self.current_buf.append(self._CHARREF_PROSE_ESCAPES.get(ch, ch))

    def close(self) -> None:
        if self.in_inline_code:
            self._close_inline_code()
        if self.in_pre:
            self._emit_pre_block()
        if self.in_table:
            self._emit_markdown_table()
        if self.current_buf:
            text = self._flush_buf().strip()
            if text:
                if self.list_stack:
                    self._emit_li_line(text)
                else:
                    q_prefix = self._quote_prefix()
                    self.output.append(f"\n\n{q_prefix}{text}\n\n")
        super().close()

    def get_markdown(self) -> str:
        text = "".join(self.output).replace("\r\n", "\n").replace("\r", "\n")
        out_lines: list[str] = []
        blank_run = 0
        fence_len = 0
        first_h = ""
        for line in text.split("\n"):
            if not fence_len:
                m = re.match(r"^(`{3,})(.*)$", line)
                if m and "`" not in m.group(2):
                    blank_run = 0
                    fence_len = len(m.group(1))
                    out_lines.append(line)
                    continue
                if not line:
                    blank_run += 1
                    if blank_run <= 1:
                        out_lines.append(line)
                else:
                    blank_run = 0
                    out_lines.append(line)
                    stripped = line.strip()
                    if not first_h and stripped.startswith("#"):
                        first_h = stripped.lstrip("#").strip()
            else:
                out_lines.append(line)
                if re.match(rf"^`{{{fence_len},}}[ \t]*$", line):
                    fence_len = 0
        result = "\n".join(out_lines).strip() + "\n"
        if self.title and first_h != self.title:
            safe_title = re.sub(r"\s+", " ", self.title).strip().replace("-->", "--&gt;")
            result = f"<!-- title: {safe_title} -->\n\n" + result
        return result


_EXPLICIT_HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
_INLINE_LINK_TARGET_PAT = r'(?:<([^<>\r\n]+)>|((?:[^()\s]|\([^()\s]*\))*))(?:\s+(?:"([^"]*)"|\'([^\']*)\'))?'
_INLINE_IMAGE_RE = re.compile(rf"!\[([^\[\]]*)\]\({_INLINE_LINK_TARGET_PAT}\)")
_INLINE_LINK_RE = re.compile(rf"\[([^\[\]]+)\]\({_INLINE_LINK_TARGET_PAT}\)")


def _unescape_explicit_html_entities(text: str) -> str:
    return _EXPLICIT_HTML_ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)


def _mask_html_entities(text: str, token_prefix: str) -> tuple[str, list[str]]:
    entity_tokens: list[str] = []

    def _save_entity(m: re.Match[str]) -> str:
        raw = m.group(0)
        if html.unescape(raw) == raw:
            return raw
        entity_tokens.append(raw)
        return f"\x00{token_prefix}{len(entity_tokens) - 1}\x00"

    return _EXPLICIT_HTML_ENTITY_RE.sub(_save_entity, text), entity_tokens


def _unmask_tokens(text: str, token_prefix: str, tokens: list[str]) -> str:
    for idx, tok in enumerate(tokens):
        text = text.replace(f"\x00{token_prefix}{idx}\x00", tok)
    return text


def _escape_html_preserving_entities(text: str, *, quote: bool = False) -> str:
    protected, entity_tokens = _mask_html_entities(text, "TITLEENT")
    return _unmask_tokens(html.escape(protected, quote=quote), "TITLEENT", entity_tokens)


def _sanitize_href(url: str) -> str:
    if _is_unsafe_href(url, unescape_first=True):
        return "#"
    return html.escape(_unescape_explicit_html_entities(url), quote=True)


def _protect_code_and_kbd(text: str) -> tuple[str, list[str], list[str]]:
    code_tokens: list[str] = []
    kbd_tokens: list[str] = []
    out: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        if text[pos] == "`":
            tick_end, close_start = _scan_backtick_span(text, pos)
            if close_start != -1:
                inner = text[tick_end:close_start]
                if len(inner) >= 2 and inner[0] == " " and inner[-1] == " " and inner.strip(" "):
                    inner = inner[1:-1]
                code_tokens.append(f"<code>{html.escape(inner, quote=False)}</code>")
                out.append(f"\x00CODE{len(code_tokens) - 1}\x00")
                pos = close_start + (tick_end - pos)
                continue
            out.append(text[pos:tick_end])
            pos = tick_end
            continue

        if text[pos : pos + 5].lower() == "<kbd>":
            close_match = re.search(r"</kbd>", text[pos + 5 :], re.IGNORECASE)
            if close_match is not None:
                kbd_raw = text[pos + 5 : pos + 5 + close_match.start()]
                kbd_tokens.append(f"<kbd>{_escape_html_preserving_entities(kbd_raw)}</kbd>")
                out.append(f"\x00KBD{len(kbd_tokens) - 1}\x00")
                pos = pos + 5 + close_match.end()
                continue

        out.append(text[pos])
        pos += 1
    return "".join(out), code_tokens, kbd_tokens


def _apply_balanced_em(pattern: str, text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.count("<strong>") != inner.count("</strong>"):
            return match.group(0)
        return f"<em>{inner}</em>"

    return re.sub(pattern, _replace, text)


def _format_plain_inline(text: str) -> str:
    protected, entity_tokens = _mask_html_entities(text, "ENT")
    text = html.escape(protected, quote=False)

    text = re.sub(
        r"(?<!\*)\*\*\*(?!\s)((?:[^*]|\*(?!\*\*))+?)(?<!\s)\*\*\*(?!\*)",
        r"<strong><em>\1</em></strong>",
        text,
    )
    text = re.sub(
        r"(?<![\w_])___(?!\s)((?:[^_]|_(?!__))+?)(?<!\s)___(?![\w_])",
        r"<strong><em>\1</em></strong>",
        text,
    )
    text = re.sub(
        r"(?<!\*)\*\*(?!\s|\*)((?:[^*]|\*(?!\*))+?)(?<!\s|\*)\*\*(?!\*)",
        r"<strong>\1</strong>",
        text,
    )
    text = re.sub(
        r"(?<![\w_])__(?!\s|_)((?:[^_]|_(?!_))+?)(?<!\s|_)__(?![\w_])",
        r"<strong>\1</strong>",
        text,
    )
    text = _apply_balanced_em(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)", text)
    text = _apply_balanced_em(r"(?<![\w_])_(?!\s)([^_]+?)(?<!\s)_(?![\w_])", text)

    return _unmask_tokens(text, "ENT", entity_tokens)


def _extract_markdown_target_and_title(match: re.Match[str]) -> tuple[str, str]:
    raw_url = match.group(2) if match.group(2) is not None else (match.group(3) or "")
    safe_url = _sanitize_href(raw_url)
    raw_title = match.group(4) if match.group(4) is not None else match.group(5)
    title_attr = f' title="{_escape_html_preserving_entities(raw_title, quote=True)}"' if raw_title else ""
    return safe_url, title_attr


def _format_inline_markdown(text: str) -> str:
    text, code_tokens, kbd_tokens = _protect_code_and_kbd(text)

    img_tokens: list[str] = []

    def replace_image(m: re.Match[str]) -> str:
        alt_text = _escape_html_preserving_entities(m.group(1), quote=True)
        img_url, title_attr = _extract_markdown_target_and_title(m)
        img_tokens.append(f'<img src="{img_url}" alt="{alt_text}"{title_attr}>')
        return f"\x00IMG{len(img_tokens) - 1}\x00"

    text = _INLINE_IMAGE_RE.sub(replace_image, text)

    link_tokens: list[str] = []

    def replace_link(m: re.Match[str]) -> str:
        link_text = _format_plain_inline(m.group(1))
        link_url, title_attr = _extract_markdown_target_and_title(m)
        link_tokens.append(f'<a href="{link_url}"{title_attr}>{link_text}</a>')
        return f"\x00LINK{len(link_tokens) - 1}\x00"

    text = _INLINE_LINK_RE.sub(replace_link, text)
    text = _format_plain_inline(text)

    text = _unmask_tokens(text, "LINK", link_tokens)
    text = _unmask_tokens(text, "IMG", img_tokens)
    text = _unmask_tokens(text, "KBD", kbd_tokens)
    return _unmask_tokens(text, "CODE", code_tokens)


def _strip_outer_table_pipes(line: str) -> str:
    cleaned = line.strip()
    if cleaned.startswith("|"):
        cleaned = cleaned[1:]
    if cleaned.endswith("|"):
        backslashes = 0
        idx = len(cleaned) - 2
        while idx >= 0 and cleaned[idx] == "\\":
            backslashes += 1
            idx -= 1
        if backslashes % 2 == 0:
            cleaned = cleaned[:-1]
    return cleaned


def _split_table_row(line: str) -> list[str]:
    cleaned = _strip_outer_table_pipes(line)
    cells: list[str] = []
    buf: list[str] = []
    idx = 0
    length = len(cleaned)
    while idx < length:
        ch = cleaned[idx]
        if ch == "`":
            tick_end, close_start = _scan_backtick_span(cleaned, idx)
            if close_start != -1:
                close_end = close_start + (tick_end - idx)
                buf.append(cleaned[idx:close_end])
                idx = close_end
                continue
            buf.append(cleaned[idx:tick_end])
            idx = tick_end
            continue
        if ch == "\\" and idx + 1 < length:
            nxt = cleaned[idx + 1]
            if nxt == "|":
                buf.append("|")
                idx += 2
                continue
            if nxt == "\\":
                buf.append("\\\\")
                idx += 2
                continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        idx += 1
    cells.append("".join(buf).strip())
    return cells


def _parse_table_separator(line: str) -> list[str | None] | None:
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return None
    cleaned = _strip_outer_table_pipes(stripped)
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


_HR_LINE_RE = re.compile(r"^(?:-\s*){3,}$|^(?:\*\s*){3,}$|^(?:_\s*){3,}$")


def _next_nonblank_line(lines: list[str], start_idx: int) -> str | None:
    for probe in range(start_idx, len(lines)):
        if lines[probe].strip():
            return lines[probe]
    return None


_MD_LIST_LINE_RE = re.compile(r"^(?:\s*[*+-]\s+|\s*\d+\.\s+)")
_MD_UL_RE = re.compile(r"^(\s*)[*+-]\s+(.*)$")
_MD_OL_RE = re.compile(r"^(\s*)\d+\.\s+(.*)$")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_DL_DEF_RE = re.compile(r"^:\s+(.*)$")
_MD_TABLE_STOP_PREFIX_RE = re.compile(r"^(?:`{3,}|~{3,}|#{1,6}\s+)")


def _fallback_markdown_to_html_body(md_text: str) -> str:
    """Fallback pure-Python markdown body generator used when markdown library is absent."""
    lines = md_text.splitlines()
    body_parts: list[str] = []
    in_code_block = False
    code_fence_char = ""
    code_fence_len = 0
    code_block_lines: list[str] = []
    code_block_lang = ""
    in_html_comment = False
    in_quote = False
    quote_lines: list[str] = []
    in_list = False
    list_items: list[tuple[int, str, str]] = []
    in_dl = False
    dl_items: list[tuple[str, list[str]]] = []
    paragraph_lines: list[str] = []

    def emit_code_block() -> None:
        nonlocal in_code_block, code_fence_char, code_fence_len, code_block_lines, code_block_lang
        if in_code_block:
            escaped_code = html.escape("\n".join(code_block_lines))
            escaped_lang = html.escape(code_block_lang, quote=True)
            class_attr = f' class="language-{escaped_lang}"' if code_block_lang else ""
            body_parts.append(f"\t<pre><code{class_attr}>{escaped_code}</code></pre>\n")
            code_block_lines = []
            code_block_lang = ""
            code_fence_char = ""
            code_fence_len = 0
            in_code_block = False

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
        if in_quote:
            quote_paras: list[list[str]] = []
            current_para: list[str] = []
            for q_line in quote_lines:
                if not q_line:
                    if current_para:
                        quote_paras.append(current_para)
                        current_para = []
                else:
                    current_para.append(q_line)
            if current_para:
                quote_paras.append(current_para)
            if quote_paras:
                body_parts.append("\t<blockquote>\n")
                for q_para in quote_paras:
                    formatted = _format_inline_markdown(" ".join(q_para))
                    body_parts.append(f"\t\t<p>{formatted}</p>\n")
                body_parts.append("\t</blockquote>\n")
            quote_lines = []
            in_quote = False

    def flush_list() -> None:
        nonlocal in_list, list_items
        if in_list and list_items:
            stack: list[tuple[int, str]] = []

            def _open_list(tag: str, formatted: str, indent: int, *, nested: bool) -> None:
                tabs = "\t" * (len(stack) + 1)
                prefix = "\n" if nested else ""
                body_parts.append(f"{prefix}{tabs}<{tag}>\n{tabs}\t<li>{formatted}")
                stack.append((indent, tag))

            def _close_list() -> None:
                _top_indent, top_tag = stack.pop()
                tabs = "\t" * (len(stack) + 1)
                body_parts.append(f"</li>\n{tabs}</{top_tag}>\n")

            for indent, tag, item_text in list_items:
                formatted = _format_inline_markdown(item_text)
                if not stack:
                    _open_list(tag, formatted, indent, nested=False)
                elif indent > stack[-1][0]:
                    _open_list(tag, formatted, indent, nested=True)
                else:
                    while len(stack) > 1 and indent < stack[-1][0]:
                        _close_list()
                    if indent > stack[-1][0]:
                        _open_list(tag, formatted, indent, nested=True)
                    elif stack[-1][1] != tag and (
                        indent == stack[-1][0] or (len(stack) == 1 and indent < stack[-1][0])
                    ):
                        _close_list()
                        _open_list(tag, formatted, indent, nested=False)
                    else:
                        tabs = "\t" * (len(stack) + 1)
                        body_parts.append(f"</li>\n{tabs}<li>{formatted}")
            while stack:
                _close_list()
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
        emit_code_block()
        flush_quote()
        flush_list()
        flush_dl()
        flush_paragraph()

    idx = 0
    num_lines = len(lines)
    while idx < num_lines:
        line = lines[idx]
        stripped = line.strip()

        if in_html_comment:
            idx += 1
            if "-->" in stripped:
                in_html_comment = False
            continue

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
                    if (
                        _MD_TABLE_STOP_PREFIX_RE.match(r_stripped)
                        or r_stripped.startswith(">")
                        or _MD_LIST_LINE_RE.match(r_line)
                    ):
                        break
                    table_rows.append(_split_table_row(r_line))
                    idx += 1
                body_parts.append(_render_table_html(headers, alignments, table_rows))
                continue

        idx += 1

        fence_event, new_char, new_len, fence_info = _update_code_fence(stripped, code_fence_char, code_fence_len)
        if fence_event == "open":
            flush_all()
            in_code_block = True
            code_fence_char = new_char
            code_fence_len = new_len
            code_block_lines = []
            code_block_lang = fence_info
            continue
        if fence_event == "close":
            emit_code_block()
            continue

        if in_code_block:
            code_block_lines.append(line)
            continue

        if (
            stripped.startswith("<!--")
            and (not paragraph_lines)
            and (stripped.endswith("-->") or "-->" not in stripped)
        ):
            flush_all()
            if "-->" not in stripped:
                in_html_comment = True
            continue

        if not stripped:
            nxt = _next_nonblank_line(lines, idx)
            if in_quote and nxt is not None and nxt.lstrip().startswith(">"):
                quote_lines.append("")
                continue
            if in_list and nxt is not None:
                nxt_stripped = nxt.strip()
                is_next_list_item = bool(_MD_LIST_LINE_RE.match(nxt))
                is_indented_cont = (
                    nxt.startswith(("  ", "\t"))
                    and not re.match(r"^(?:`{3,}|~{3,}|#{1,6}\s+|:\s+|>\s?)", nxt_stripped)
                    and not _HR_LINE_RE.match(nxt_stripped)
                )
                if is_next_list_item or is_indented_cont:
                    continue
            if in_dl and nxt is not None and _MD_DL_DEF_RE.match(nxt.strip()):
                continue
            flush_all()
            continue

        if _HR_LINE_RE.match(stripped):
            flush_all()
            body_parts.append("\t<hr>\n")
            continue

        h_match = _MD_HEADING_RE.match(stripped)
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

        ul_match = _MD_UL_RE.match(line)
        if ul_match:
            flush_paragraph()
            flush_quote()
            flush_dl()
            in_list = True
            indent = len(ul_match.group(1))
            list_items.append((indent, "ul", ul_match.group(2).strip()))
            continue

        ol_match = _MD_OL_RE.match(line)
        if ol_match:
            flush_paragraph()
            flush_quote()
            flush_dl()
            in_list = True
            indent = len(ol_match.group(1))
            list_items.append((indent, "ol", ol_match.group(2).strip()))
            continue

        dl_match = _MD_DL_DEF_RE.match(stripped)
        if dl_match:
            if paragraph_lines:
                flush_quote()
                flush_list()
                term = paragraph_lines.pop()
                if paragraph_lines:
                    flush_dl()
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

        if in_list and (line.startswith("  ") or line.startswith("\t")):
            if list_items:
                prev_lvl, prev_type, prev_txt = list_items[-1]
                list_items[-1] = (prev_lvl, prev_type, f"{prev_txt} {stripped}")
            continue

        if in_quote:
            if idx < num_lines and _MD_DL_DEF_RE.match(lines[idx].strip()):
                flush_quote()
            else:
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

    if _MARKDOWN_AVAILABLE and markdown is not None and _NH3_AVAILABLE and nh3 is not None:
        html_body = _generate_doc_html_body(md_cleaned)
    else:
        html_body = _fallback_markdown_to_html_body(md_cleaned)

    lang_attr = html.escape(language.replace("_", "-"), quote=True)
    dir_attr = ' dir="rtl"' if is_rtl else ""
    title_escaped = _escape_html_preserving_entities(doc_title)

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
            raw_html = _sanitize_doc_html(raw_html)
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
    _remove_stale_doc_mo_files(doc_dir)
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
            if check_only:
                errors.append(
                    f"{language}: unexpected .mo file in documentation folder: {_relative_to_addon(stale_mo)}"
                )
            else:
                stale_mo.unlink(missing_ok=True)
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


def _validate_doc_html_against_en(
    language: str,
    source_path: Path,
    doc_name: str,
    active_checks: set[str],
    render_html: Callable[[str], str] | None = None,
) -> list[str]:
    try:
        raw_text = source_path.read_text(encoding="utf-8-sig")
        html_text = render_html(raw_text) if render_html is not None else raw_text
        errors = _check_html_content_with_lxml(html_text, str(_relative_to_addon(source_path)))
        en_segs = [s for _, s in _extract_doc_segments(DOC_EN_PATH.read_text(encoding="utf-8-sig"))]
        lang_segs = [s for _, s in _extract_doc_segments(html_text)]
        errors.extend(_check_doc_segments(language, en_segs, lang_segs, doc_name, active_checks))
        return errors
    except Exception as exc:
        return [f"{language}: could not validate {_relative_to_addon(source_path)}: {exc}"]


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
        syntax_errors = _check_po_syntax(po_path, msgfmt_path)
        if syntax_errors:
            errors.extend(syntax_errors)
            return errors
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
                errors.extend(
                    _check_string_interpolations(
                        m,
                        msgstr,
                        repr(_message_preview(m)),
                        language,
                        context_phrase="readme.po",
                        always_check_html=True,
                    )
                )

        if CHECK_OBSOLETE in active_checks:
            for m in sorted(po_msgids - expected_msgids):
                errors.append(f"{language}: obsolete documentation string in readme.po: {_message_preview(m)!r}")

    elif md_path.is_file() and language != "en":
        errors.extend(
            _validate_doc_html_against_en(
                language,
                md_path,
                "readme.md",
                active_checks,
                render_html=lambda text: _markdown_to_doc_html(
                    text, language=language, is_rtl=_is_rtl_language(language)
                ),
            )
        )

    elif html_path.is_file() and language != "en":
        errors.extend(_validate_doc_html_against_en(language, html_path, "readme.html", active_checks))

    elif CHECK_DOCS not in active_checks and (active_checks & {CHECK_OBSOLETE, CHECK_FUZZY, CHECK_PLACEHOLDERS}):
        errors.append(f"{language}: missing documentation file: {_relative_to_addon(html_path)}")

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
        errors.extend(_check_po_syntax(po_path, msgfmt_path))
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
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return [f"{language}: languageSort.json could not be parsed: {exc}"]
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return [f"{language}: languageSort.json must be UTF-8 without BOM."]
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
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
    valid_checks: set[str] | frozenset[str] | None = None,
) -> set[str]:
    checks = set(default_checks if default_checks is not None else DEFAULT_CHECKS)
    if strict and (valid_checks is None or CHECK_UI in valid_checks):
        checks.add(CHECK_UI)
    if not values:
        return checks
    checks = set()
    allowed_pool = ALL_CHECKS if valid_checks is None else valid_checks
    allowed_names = set(allowed_pool) | {"all"}
    for raw_value in values:
        for item in raw_value.split(","):
            name = item.strip().lower()
            if not name:
                continue
            if name not in allowed_names:
                raise ValueError(f"unknown check {name!r}; choose from {', '.join(sorted(allowed_names))}")
            if name == "all":
                checks.update(allowed_pool)
            else:
                checks.add(name)
    if not checks:
        raise ValueError(f"at least one check must be specified; choose from {', '.join(sorted(allowed_names))}")
    return checks


class _MenuBack:
    pass


_MENU_BACK = _MenuBack()


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


def _prompt_language_code(allow_back: bool = False) -> str | None:
    prompt = "Language code (0 to go back): " if allow_back else "Language code: "
    while True:
        raw = input(prompt).strip()
        if allow_back and raw == "0":
            return None
        language = _normalize_language_code(raw)
        if language:
            return language
        print("Enter a language code.")


def _prompt_languages(languages: list[str], allow_back: bool = True) -> list[str] | None | _MenuBack:
    print("")
    print("Locales:")
    print("  1. All addon locales")
    for index, language in enumerate(languages, start=2):
        print(f"  {index}. {language}")
    manual_choice = len(languages) + 2
    print(f"  {manual_choice}. Enter language code manually")
    if allow_back:
        print("  0. Go back")
        min_choice = 0
    else:
        min_choice = 1
    choice = _prompt_number(f"Choose {min_choice}-{manual_choice}: ", min_choice, manual_choice)
    if choice == 0:
        return _MENU_BACK
    if choice == 1:
        return None
    if choice == manual_choice:
        code = _prompt_language_code(allow_back=allow_back)
        if code is None:
            return _MENU_BACK
        return [code]
    return [languages[choice - 2]]


def _prompt_checks_generic(
    default_checks: set[str],
    default_label: str,
    numbered_checks: list[tuple[str, str]],
    available_checks_str: str,
    allow_back: bool = True,
    valid_checks: set[str] | frozenset[str] | None = None,
) -> set[str] | _MenuBack:
    print("")
    print("Check mode:")
    print(f"  1. {default_label}")
    for index, (_check, label) in enumerate(numbered_checks, start=2):
        print(f"  {index}. {label}")
    custom_choice = len(numbered_checks) + 2
    print(f"  {custom_choice}. Custom checks")
    if allow_back:
        print("  0. Go back")
        min_choice = 0
    else:
        min_choice = 1
    mode = _prompt_number(f"Choose {min_choice}-{custom_choice}: ", min_choice, custom_choice)
    if mode == 0:
        return _MENU_BACK
    if mode == 1:
        return set(default_checks)
    if mode == custom_choice:
        print(f"Available checks: {available_checks_str}")
        if allow_back:
            print("Enter 0 to go back.")
        while True:
            raw_checks = input("Checks, separated by commas: ").strip()
            if allow_back and raw_checks == "0":
                return _MENU_BACK
            try:
                return _parse_checks(
                    [raw_checks],
                    strict=False,
                    default_checks=default_checks,
                    valid_checks=valid_checks,
                )
            except ValueError as exc:
                print(f"Error: {exc}")
                print(f"Enter valid checks ({available_checks_str}) or 0 to go back.")
    return {numbered_checks[mode - 2][0]}


def _prompt_checks(default_checks: set[str], allow_back: bool = True) -> set[str] | _MenuBack:
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
        allow_back=allow_back,
    )


def _prompt_doc_checks(default_checks: set[str] | None = None, allow_back: bool = True) -> set[str] | _MenuBack:
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
        allow_back=allow_back,
        valid_checks=DEFAULT_DOC_CHECKS,
    )


def _prompt_locale_choice(pool: list[str]) -> list[str] | None | _MenuBack:
    if pool:
        return _prompt_languages(pool, allow_back=True)
    code = _prompt_language_code(allow_back=True)
    return [code] if code else _MENU_BACK


def _prompt_build_or_check_action() -> int:
    print("")
    print("Action:")
    print("  1. Check only")
    print("  2. Build generated files")
    print("  0. Go back")
    return _prompt_number("Choose 0-2: ", 0, 2)


def _interactive_options(
    default_checks: set[str],
) -> tuple[list[str] | None, set[str], bool, bool, bool, bool, bool, bool, bool, bool, bool] | None:
    while True:
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
        print("  0. Exit")
        task = _prompt_number("Choose 0-8: ", 0, 8)
        if task == 0:
            return None
        if task == 2:
            return None, set(default_checks), False, True, False, False, False, False, False, False, False
        if task == 3:
            selected_languages = _prompt_locale_choice(languages)
            if isinstance(selected_languages, _MenuBack):
                continue
            return selected_languages, set(default_checks), False, False, True, False, False, False, False, False, False
        if task == 4:
            selected_languages = _prompt_locale_choice(doc_languages)
            if isinstance(selected_languages, _MenuBack):
                continue
            checks = _prompt_doc_checks(DEFAULT_DOC_CHECKS, allow_back=True)
            if isinstance(checks, _MenuBack):
                continue
            action = _prompt_build_or_check_action()
            if action == 0:
                continue
            return selected_languages, checks, False, False, False, action != 1, False, False, action == 1, False, False
        if task == 5:
            return None, set(default_checks), False, False, False, False, True, False, False, False, False
        if task == 6:
            selected_languages = _prompt_locale_choice(doc_languages)
            if isinstance(selected_languages, _MenuBack):
                continue
            return selected_languages, set(default_checks), False, False, False, False, False, True, False, False, False
        if task in (7, 8):
            conv_languages = (["en"] if (DOC_DIR / "en").is_dir() else []) + doc_languages
            selected_languages = _prompt_locale_choice(conv_languages)
            if isinstance(selected_languages, _MenuBack):
                continue
            return (
                selected_languages,
                set(default_checks),
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                task == 7,
                task == 8,
            )

        selected_languages = _prompt_locale_choice(languages)
        if isinstance(selected_languages, _MenuBack):
            continue
        checks = _prompt_checks(default_checks, allow_back=True)
        if isinstance(checks, _MenuBack):
            continue
        action = _prompt_build_or_check_action()
        if action == 0:
            continue
        return selected_languages, checks, action == 1, False, False, False, False, False, False, False, False


def _print_errors(errors: list[str]) -> int:
    for error in errors:
        print(f"[ERROR] {error}")
    return 1 if errors else 0


def _resolve_nvda_locale_support(
    nvda_locale_dirs: list[Path] | None,
    checks: set[str],
) -> tuple[set[str] | None, list[Path]]:
    locale_dirs = nvda_locale_dirs or list(DEFAULT_NVDA_LOCALE_DIRS)
    supported_languages, found_locale_dirs = _supported_nvda_languages_from_dirs(locale_dirs)
    if CHECK_LANGUAGE in checks and supported_languages is None:
        print("[WARN] NVDA locale folder was not found; language-code support check is skipped.")
    return supported_languages, found_locale_dirs


def _print_msgmerge_discovery(configured_msgmerge: Path | None) -> Path | None:
    msgmerge_path = _find_msgmerge(configured_msgmerge)
    if msgmerge_path:
        print(f"msgmerge: {msgmerge_path}")
    else:
        print("[INFO] msgmerge was not found; using internal PO merger.")
    print("")
    return msgmerge_path


def _run_doc_conversion(selected_languages: list[str] | None, *, to_markdown: bool) -> int:
    target_languages, selection_errors = _doc_target_languages(selected_languages, allow_create=False, include_en=True)
    if selection_errors:
        return _print_errors(selection_errors)
    all_errors: list[str] = []
    converted_count = 0
    for lang in target_languages:
        doc_dir = DOC_DIR / lang
        html_file = doc_dir / "readme.html"
        md_file = doc_dir / "readme.md"
        src_file, dst_file = (html_file, md_file) if to_markdown else (md_file, html_file)
        if not src_file.is_file():
            if selected_languages is not None:
                kind = "HTML" if to_markdown else "Markdown"
                all_errors.append(f"{lang}: documentation {kind} not found: {_relative_to_addon(src_file)}")
            continue
        success, msg = (
            _convert_html_file_to_md(html_file, md_file)
            if to_markdown
            else _convert_md_file_to_html(md_file, html_file, lang)
        )
        if success:
            print(f"Converted {_relative_to_addon(src_file)} -> {_relative_to_addon(dst_file)}")
            converted_count += 1
        else:
            all_errors.append(msg)
    if selected_languages is None and converted_count == 0 and not all_errors:
        src_kind = "HTML" if to_markdown else "Markdown"
        dst_kind = "Markdown" if to_markdown else "HTML"
        print(f"[INFO] No documentation {src_kind} files found to convert to {dst_kind}.")
    return _print_errors(all_errors)


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
    msgfmt_path = _find_msgfmt(args.msgfmt)
    if args.msgfmt is not None and msgfmt_path is None:
        parser.error(f"--msgfmt path does not exist or is not a valid file: {args.msgfmt}")

    if args.msgmerge is not None and _find_msgmerge(args.msgmerge) is None:
        parser.error(f"--msgmerge path does not exist or is not a valid file: {args.msgmerge}")

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
        try:
            checks = _parse_checks(args.checks, args.strict)
        except ValueError as exc:
            parser.error(str(exc))
        build_ui = False
        menu_options = _interactive_options(checks)
        if menu_options is None:
            return 0
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
        ) = menu_options
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
    else:
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
        is_doc_check_mode = check_docs or build_docs
        try:
            checks = _parse_checks(
                args.checks,
                args.strict,
                default_checks=DEFAULT_DOC_CHECKS if is_doc_check_mode else DEFAULT_UI_CHECKS,
                valid_checks=DEFAULT_DOC_CHECKS if is_doc_check_mode else None,
            )
        except ValueError as exc:
            parser.error(str(exc))

    if args.msgmerge is not None and not update_po and not update_doc_po:
        parser.error("--msgmerge can only be used with --update-po or --update-doc-po.")

    msgfmt_kwargs: dict[str, Path] = (
        {"msgfmt_path": msgfmt_path} if args.msgfmt is not None and msgfmt_path is not None else {}
    )

    if check_docs or build_docs:
        doc_check_only = bool(check_docs)
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=False, include_en=True
        )
        if not doc_check_only and selection_errors:
            return _print_errors(selection_errors)
        if not DOC_EN_PATH.is_file():
            print(f"[ERROR] English source document missing: {_relative_to_addon(DOC_EN_PATH)}")
            return 1
        supported_languages, found_locale_dirs = _resolve_nvda_locale_support(args.nvda_locale_dir, checks)
        if not doc_check_only:
            try:
                pot_path = _write_doc_pot()
                print(f"Updated documentation string template: {_relative_to_addon(pot_path)}")
            except (RuntimeError, ValueError, OSError) as exc:
                return _print_errors([str(exc)])
        _print_doc_run_summary(target_languages, checks, check_only=doc_check_only, found_locale_dirs=found_locale_dirs)
        all_errors = list(selection_errors) if doc_check_only else []
        for lang in target_languages:
            doc_dir = DOC_DIR / lang
            html_path = doc_dir / "readme.html"
            errors = _check_doc_language(
                lang,
                supported_languages=supported_languages,
                checks=checks,
                check_only=doc_check_only,
                **msgfmt_kwargs,
            )
            if errors:
                all_errors.extend(errors)
                continue
            if lang == "en":
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print("  Passed: English source document")
                continue
            if doc_check_only:
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print(f"  Passed: {_describe_doc_checks(checks)}")
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
            elif (
                html_path.is_file() and not (doc_dir / "readme.po").is_file() and not (doc_dir / "readme.md").is_file()
            ):
                print(f"Checked {_relative_to_addon(doc_dir)}")
                print(f"  Passed: {_describe_doc_checks(checks)}")
            else:
                all_errors.append(msg)
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
        target_languages, selection_errors = _doc_target_languages(
            selected_languages, allow_create=True, include_en=False
        )
        if selection_errors:
            return _print_errors(selection_errors)
        try:
            pot_path = _write_doc_pot()
        except (RuntimeError, ValueError, OSError) as exc:
            return _print_errors([str(exc)])
        print(f"Updated documentation string template: {_relative_to_addon(pot_path)}")
        msgmerge_path = _print_msgmerge_discovery(args.msgmerge)
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

    if html_to_md or md_to_html:
        if not using_menu and not args.all_languages and selected_languages is None:
            flag_name = "--html-to-md" if html_to_md else "--md-to-html"
            parser.error(f"{flag_name} requires --language or --all-languages.")
        return _run_doc_conversion(selected_languages, to_markdown=bool(html_to_md))

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
        language_dirs, selection_errors = _language_dirs(selected_languages, allow_create=True)
        if selection_errors:
            return _print_errors(selection_errors)
        try:
            required_messages = _translatable_source_messages()
            template_path = _write_pot(required_messages)
        except (ValueError, OSError) as exc:
            return _print_errors([str(exc)])
        print(f"Updated source string template: {_relative_to_addon(template_path)}")
        msgmerge_path = _print_msgmerge_discovery(args.msgmerge)
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

    language_dirs, selection_errors = _language_dirs(selected_languages)
    if not check_only and selection_errors:
        return _print_errors(selection_errors)
    supported_languages, found_locale_dirs = _resolve_nvda_locale_support(args.nvda_locale_dir, checks)
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
        file_errors = _check_language_files(
            language_dir,
            supported_languages,
            checks,
            check_only,
            **msgfmt_kwargs,
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

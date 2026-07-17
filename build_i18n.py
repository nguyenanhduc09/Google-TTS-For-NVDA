# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import re
import struct
import string
import sys
import unicodedata
from pathlib import Path


ADDON_DIR = Path(__file__).resolve().parent / "googleTtsForNvda"
LOCALE_DIR = ADDON_DIR / "locale"
DOC_DIR = ADDON_DIR / "doc"
MANIFEST_SOURCE = ADDON_DIR / "manifest.ini"
POT_PATH = LOCALE_DIR / "nvda.pot"
DEFAULT_NVDA_LOCALE_DIRS = (
	Path(r"C:\Program Files\NVDA\locale"),
	Path(r"C:\Program Files (x86)\NVDA\locale"),
)
MANIFEST_KEYS = ("summary", "description", "changelog")
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
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
CHECK_ORDER = (
	CHECK_LANGUAGE,
	CHECK_MANIFEST,
	CHECK_DOCS,
	CHECK_UI,
	CHECK_PLACEHOLDERS,
	CHECK_SORT,
	CHECK_OBSOLETE,
)
CHECK_LABELS = {
	CHECK_LANGUAGE: "NVDA language code",
	CHECK_MANIFEST: "manifest",
	CHECK_DOCS: "documentation",
	CHECK_UI: "UI strings",
	CHECK_PLACEHOLDERS: "placeholders",
	CHECK_SORT: "language sorting",
	CHECK_OBSOLETE: "obsolete source strings",
}
DEFAULT_CHECKS = {
	CHECK_LANGUAGE,
	CHECK_MANIFEST,
	CHECK_DOCS,
	CHECK_UI,
	CHECK_PLACEHOLDERS,
	CHECK_SORT,
	CHECK_OBSOLETE,
}
ALL_CHECKS = set(DEFAULT_CHECKS)


def _decode_po_string(token: str) -> str:
	return ast.literal_eval(token)


def _parse_po(path: Path, *, include_untranslated: bool = False) -> dict[str, str]:
	entries: dict[str, str] = {}
	msgid_parts: list[str] = []
	msgstr_parts: list[str] = []
	active: str | None = None
	has_msgid = False

	def commit() -> None:
		nonlocal msgid_parts, msgstr_parts, active, has_msgid
		if has_msgid:
			msgid = "".join(msgid_parts)
			msgstr = "".join(msgstr_parts)
			if msgstr or include_untranslated:
				entries[msgid] = msgstr
		msgid_parts = []
		msgstr_parts = []
		active = None
		has_msgid = False

	for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#"):
			continue
		if line.startswith("msgctxt "):
			continue
		if line.startswith("msgid "):
			commit()
			active = "msgid"
			has_msgid = True
			msgid_parts.append(_decode_po_string(line[6:].strip()))
			continue
		if line.startswith("msgstr "):
			active = "msgstr"
			msgstr_parts.append(_decode_po_string(line[7:].strip()))
			continue
		if line.startswith('"') and active == "msgid":
			msgid_parts.append(_decode_po_string(line))
			continue
		if line.startswith('"') and active == "msgstr":
			msgstr_parts.append(_decode_po_string(line))
			continue
	commit()
	return entries


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
			tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call) or _function_name(node.func) != "_":
					continue
				if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
					continue
				message = node.args[0].value
				if not message:
					continue
				location = path.relative_to(ADDON_DIR).as_posix()
				messages.setdefault(message, []).append(f"{location}:{node.lineno}")
	return messages


def _po_escape(value: str) -> str:
	return value.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n")


def _po_quoted_lines(value: str) -> list[str]:
	if not value:
		return ['""']
	parts = value.splitlines(keepends=True)
	if len(parts) == 1:
		return [f'"{_po_escape(value)}"']
	return ['""'] + [f'"{_po_escape(part)}"' for part in parts]


def _append_po_string(lines: list[str], keyword: str, value: str) -> None:
	quoted_lines = _po_quoted_lines(value)
	lines.append(f"{keyword} {quoted_lines[0]}")
	lines.extend(quoted_lines[1:])


def _write_pot(messages: dict[str, list[str]]) -> Path:
	POT_PATH.parent.mkdir(parents=True, exist_ok=True)
	lines = [
		"# Google TTS For NVDA translation template.",
		"# Copyright (C) 2026 Google TTS For NVDA contributors",
		"# This file is distributed under the same license as the add-on.",
		"#",
		'msgid ""',
		'msgstr ""',
		'"Project-Id-Version: Google TTS For NVDA 0.3\\n"',
		'"Report-Msgid-Bugs-To: \\n"',
		'"POT-Creation-Date: YEAR-MO-DA HO:MI+ZONE\\n"',
		'"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\\n"',
		'"Last-Translator: FULL NAME <EMAIL@ADDRESS>\\n"',
		'"Language-Team: LANGUAGE <LL@li.org>\\n"',
		'"Language: \\n"',
		'"MIME-Version: 1.0\\n"',
		'"Content-Type: text/plain; charset=UTF-8\\n"',
		'"Content-Transfer-Encoding: 8bit\\n"',
	]
	for msgid, locations in sorted(messages.items(), key=lambda item: item[0].lower()):
		lines.append("")
		for location in sorted(locations):
			lines.append(f"#: {location}")
		_append_po_string(lines, "msgid", msgid)
		lines.append('msgstr ""')
	with POT_PATH.open("w", encoding="utf-8", newline="\n") as stream:
		stream.write("\n".join(lines) + "\n")
	return POT_PATH


def _format_set(message: str) -> set[str]:
	fields: set[str] = set()
	try:
		for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(message):
			if field_name:
				fields.add("{" + field_name.split(".", 1)[0].split("[", 1)[0] + "}")
	except ValueError:
		return set(PLACEHOLDER_RE.findall(message))
	return fields


def _message_preview(message: str, limit: int = 120) -> str:
	preview = message.replace("\n", "\\n")
	if len(preview) > limit:
		preview = preview[: limit - 3] + "..."
	return preview


def _check_catalog(
	language_dir: Path,
	catalog: dict[str, str],
	checks: set[str],
	required_messages: dict[str, list[str]] | None = None,
) -> list[str]:
	errors: list[str] = []
	manifest_values = _manifest_values()
	if CHECK_MANIFEST in checks:
		for key in MANIFEST_KEYS:
			msgid = manifest_values.get(key)
			if not msgid:
				errors.append(f"{language_dir.name}: manifest field {key!r} could not be read.")
				continue
			msgstr = catalog.get(msgid, "")
			if not msgstr:
				errors.append(f"{language_dir.name}: missing translation for manifest {key}: {msgid!r}")
				continue
			if CHECK_PLACEHOLDERS in checks and _format_set(msgid) != _format_set(msgstr):
				errors.append(
					f"{language_dir.name}: placeholder mismatch for manifest {key}: "
					f"{sorted(_format_set(msgid))} != {sorted(_format_set(msgstr))}"
				)
	if CHECK_PLACEHOLDERS in checks:
		for msgid, msgstr in catalog.items():
			if not msgid:
				continue
			if _format_set(msgid) != _format_set(msgstr):
				errors.append(
					f"{language_dir.name}: placeholder mismatch for {msgid!r}: "
					f"{sorted(_format_set(msgid))} != {sorted(_format_set(msgstr))}"
				)
	if CHECK_UI in checks and required_messages is not None:
		for msgid, locations in sorted(required_messages.items()):
			if not catalog.get(msgid, ""):
				errors.append(
					f"{language_dir.name}: missing translation for {_message_preview(msgid)!r} "
					f"at {', '.join(locations[:3])}"
				)
	if CHECK_OBSOLETE in checks and required_messages is not None:
		current_msgids = set(required_messages)
		for msgid in sorted(catalog):
			if not msgid or msgid in current_msgids:
				continue
			errors.append(
				f"{language_dir.name}: obsolete source string in nvda.po: "
				f"{_message_preview(msgid)!r}"
			)
	return errors


def _compile_mo(catalog: dict[str, str], output_path: Path) -> None:
	output_path.parent.mkdir(parents=True, exist_ok=True)
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

	with output_path.open("wb") as stream:
		stream.write(
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
			stream.write(struct.pack("<ii", length, offset))
		for length, offset in value_offsets:
			stream.write(struct.pack("<ii", length, offset))
		for value in ids:
			stream.write(value + b"\0")
		for value in strs:
			stream.write(value + b"\0")


def _manifest_values() -> dict[str, str]:
	text = MANIFEST_SOURCE.read_text(encoding="utf-8")
	values: dict[str, str] = {}
	for key in MANIFEST_KEYS:
		match = re.search(rf'^{re.escape(key)}\s*=\s*"""(.*?)"""', text, re.MULTILINE | re.DOTALL)
		if match:
			values[key] = match.group(1)
		else:
			match_single = re.search(rf'^{re.escape(key)}\s*=\s*"(.*?)"', text, re.MULTILINE)
			if match_single:
				values[key] = match_single.group(1)
	return values


def _quote_manifest_value(value: str) -> str:
	return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_translated_manifest(language_dir: Path, catalog: dict[str, str]) -> None:
	values = _manifest_values()
	summary = catalog.get(values["summary"], values["summary"])
	description = catalog.get(values["description"], values["description"])
	description = description.replace('"""', r"\"\"\"")
	lines = [
		f"summary = {_quote_manifest_value(summary)}\n",
		f'description = """{description}"""\n',
	]
	if "changelog" in values:
		changelog = catalog.get(values["changelog"], values["changelog"])
		changelog = changelog.replace('"""', r"\"\"\"")
		lines.append(f'changelog = """{changelog}"""\n')
	with (language_dir / "manifest.ini").open("w", encoding="utf-8", newline="\n") as stream:
		stream.write("".join(lines))


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
	return supported, found_dirs


def _normalize_language_code(language: str) -> str:
	parts = language.strip().replace("-", "_").split("_", 1)
	if len(parts) == 1:
		return parts[0].lower()
	return f"{parts[0].lower()}_{parts[1].upper()}"


def _language_dirs(requested_languages: list[str] | None) -> tuple[list[Path], list[str]]:
	errors: list[str] = []
	if requested_languages:
		language_dirs = []
		for language in requested_languages:
			normalized_language = _normalize_language_code(language)
			if not normalized_language:
				errors.append("empty language code.")
				continue
			language_dir = LOCALE_DIR / normalized_language
			if not language_dir.is_dir():
				errors.append(f"{normalized_language}: translation folder is missing: {language_dir.relative_to(ADDON_DIR)}")
				continue
			language_dirs.append(language_dir)
		return language_dirs, errors
	if not LOCALE_DIR.is_dir():
		return [], []
	return sorted(path for path in LOCALE_DIR.iterdir() if path.is_dir()), []


def _addon_languages() -> list[str]:
	if not LOCALE_DIR.is_dir():
		return []
	return sorted(path.name for path in LOCALE_DIR.iterdir() if path.is_dir())


def _check_language_files(
	language_dir: Path,
	supported_languages: set[str] | None,
	checks: set[str],
	check_only: bool,
) -> list[str]:
	language = language_dir.name
	errors: list[str] = []
	if CHECK_LANGUAGE in checks and supported_languages is not None and language not in supported_languages:
		errors.append(f"{language}: language code is not present in the NVDA locale folder.")
		return errors
	manifest_path = language_dir / "manifest.ini"
	if check_only and CHECK_MANIFEST in checks and not manifest_path.is_file():
		errors.append(f"{language}: missing localized manifest file: {manifest_path.relative_to(ADDON_DIR)}")
	po_path = language_dir / "LC_MESSAGES" / "nvda.po"
	if not po_path.is_file():
		errors.append(f"{language}: missing translation file: {po_path.relative_to(ADDON_DIR)}")
	doc_path = DOC_DIR / language / "readme.html"
	if CHECK_DOCS in checks and not doc_path.is_file():
		errors.append(f"{language}: missing documentation file: {doc_path.relative_to(ADDON_DIR)}")
	if CHECK_SORT in checks:
		errors.extend(_check_language_sort_file(language_dir))
	return errors


def _check_language_sort_file(language_dir: Path) -> list[str]:
	path = language_dir / "languageSort.json"
	if not path.exists():
		return []
	language = language_dir.name
	errors: list[str] = []
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
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
		errors.append(f"{language}: languageSort.json field 'ignoreCombiningMarks' must be a list of combining mark names.")
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


def _describe_checks(checks: set[str]) -> str:
	return ", ".join(CHECK_LABELS[name] for name in CHECK_ORDER if name in checks)


def _print_run_summary(
	language_dirs: list[Path],
	checks: set[str],
	check_only: bool,
	found_locale_dirs: list[Path],
) -> None:
	print("")
	if language_dirs:
		languages = ", ".join(path.name for path in language_dirs)
	else:
		languages = "none"
	print(f"Selected locales: {languages}")
	print(f"Checks: {_describe_checks(checks)}")
	print(f"Action: {'check only' if check_only else 'build generated files'}")
	if CHECK_LANGUAGE in checks:
		if found_locale_dirs:
			locale_paths = "; ".join(str(path) for path in found_locale_dirs)
			print(f"NVDA locale source: {locale_paths}")
		else:
			print("NVDA locale source: not found; language-code support check will be skipped")
	print("")


def _parse_checks(values: list[str] | None, strict: bool) -> set[str]:
	checks = set(DEFAULT_CHECKS)
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


def _prompt_checks(default_checks: set[str]) -> set[str]:
	print("")
	print("Check mode:")
	print("  1. Default checks (all categories)")
	numbered_checks = [
		(CHECK_LANGUAGE, "NVDA language code only"),
		(CHECK_MANIFEST, "Manifest only"),
		(CHECK_DOCS, "Documentation only"),
		(CHECK_UI, "UI strings only"),
		(CHECK_PLACEHOLDERS, "Placeholders only"),
		(CHECK_SORT, "Language sorting only"),
		(CHECK_OBSOLETE, "Obsolete source strings only"),
	]
	for index, (_check, label) in enumerate(numbered_checks, start=2):
		print(f"  {index}. {label}")
	custom_choice = len(numbered_checks) + 2
	print(f"  {custom_choice}. Custom checks")
	mode = _prompt_number(f"Choose 1-{custom_choice}: ", 1, custom_choice)
	if mode == 1:
		return set(default_checks)
	if mode == custom_choice:
		print("Available checks: language, manifest, docs, ui, placeholders, sort, obsolete, all")
		raw_checks = input("Checks, separated by commas: ")
		return _parse_checks([raw_checks], strict=False)
	return {numbered_checks[mode - 2][0]}


def _interactive_options(default_checks: set[str]) -> tuple[list[str] | None, set[str], bool, bool]:
	languages = _addon_languages()
	print("Google TTS For NVDA translation checker")
	print("")
	print("Task:")
	print("  1. Check or build translations")
	print("  2. Generate source string template")
	task = _prompt_number("Choose 1 or 2: ", 1, 2)
	if task == 2:
		return None, set(default_checks), True, True

	selected_languages = _prompt_languages(languages) if languages else [_prompt_language_code()]
	checks = _prompt_checks(default_checks)

	print("")
	print("Action:")
	print("  1. Check only")
	print("  2. Build generated files")
	action = _prompt_number("Choose 1 or 2: ", 1, 2)
	return selected_languages, checks, action == 1, False


def main() -> int:
	parser = argparse.ArgumentParser(description="Build and check Google TTS For NVDA translations.")
	parser.add_argument("--menu", action="store_true", help="Show an interactive numbered menu.")
	parser.add_argument("--check", action="store_true", help="Only check translations; do not write generated files.")
	parser.add_argument(
		"--extract-template",
		action="store_true",
		help="Generate locale\\nvda.pot with the current English source strings and exit.",
	)
	parser.add_argument(
		"-l",
		"--language",
		action="append",
		help="Only build or check this language code. Can be used more than once.",
	)
	parser.add_argument(
		"--all-languages",
		action="store_true",
		help="Build or check every add-on locale without opening the interactive menu.",
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
			"Comma-separated checks to run: all, language, manifest, docs, ui, placeholders, sort, obsolete. "
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
	if args.menu or len(sys.argv) == 1:
		selected_languages, checks, check_only, extract_template = _interactive_options(checks)

	if extract_template:
		template_path = _write_pot(_translatable_source_messages())
		print(f"Updated source string template: {template_path.relative_to(ADDON_DIR)}")
		return 0

	locale_dirs = args.nvda_locale_dir or list(DEFAULT_NVDA_LOCALE_DIRS)
	language_dirs, selection_errors = _language_dirs(selected_languages)
	supported_languages, found_locale_dirs = _supported_nvda_languages_from_dirs(locale_dirs)
	if CHECK_LANGUAGE in checks and supported_languages is None:
		print("[WARN] NVDA locale folder was not found; language-code support check is skipped.")
	source_messages = (
		_translatable_source_messages()
		if CHECK_UI in checks or CHECK_OBSOLETE in checks or not check_only
		else None
	)
	all_errors: list[str] = list(selection_errors)
	if not check_only:
		template_path = _write_pot(source_messages or _translatable_source_messages())
		print(f"Updated source string template: {template_path.relative_to(ADDON_DIR)}")
	_print_run_summary(language_dirs, checks, check_only, found_locale_dirs)
	for language_dir in language_dirs:
		file_errors = _check_language_files(language_dir, supported_languages, checks, check_only)
		if file_errors:
			all_errors.extend(file_errors)
			continue
		po_path = language_dir / "LC_MESSAGES" / "nvda.po"
		check_catalog = _parse_po(po_path, include_untranslated=True)
		errors = _check_catalog(language_dir, check_catalog, checks, source_messages)
		if errors:
			all_errors.extend(errors)
			continue
		if not check_only:
			catalog = _parse_po(po_path)
			_compile_mo(catalog, language_dir / "LC_MESSAGES" / "nvda.mo")
			_write_translated_manifest(language_dir, catalog)
			print(f"Updated {language_dir.relative_to(ADDON_DIR)}")
			print(f"  Passed: {_describe_checks(checks)}")
			print("  Generated: LC_MESSAGES\\nvda.mo, manifest.ini")
		else:
			print(f"Checked {language_dir.relative_to(ADDON_DIR)}")
			print(f"  Passed: {_describe_checks(checks)}")
	if all_errors:
		for error in all_errors:
			print(f"[ERROR] {error}")
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

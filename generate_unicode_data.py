#!/usr/bin/env python3
"""Generate compact runtime Unicode tables from official UCD and CLDR files."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_ENGINE_ROOT = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "WasmTtsEngine"
DEFAULT_OUTPUT = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "unicode_data.py"
DEFAULT_CATALOG_MODULE = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "catalog.py"

ENGINE_ROOT_CANDIDATES = [
    ROOT / "Google-TTS-For-NVDA" / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "WasmTtsEngine",
    DEFAULT_ENGINE_ROOT,
]

CATALOG_MODULE_CANDIDATES = [
    ROOT / "Google-TTS-For-NVDA" / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda" / "catalog.py",
    DEFAULT_CATALOG_MODULE,
]

ENGINE_VERSION_PATTERN = re.compile(r"^\d{8}(?:\.\d+)*$")

_CLDR_LANGUAGE_FALLBACKS = {
    # CLDR treats Mandarin as a legacy alias of Chinese in likely-subtag data.
    "cmn": "zh",
}
_CLDR_COMPOSITE_SCRIPTS = {
    "Hanb": ("Han", "Bopomofo"),
    "Jpan": ("Han", "Hiragana", "Katakana"),
    "Kore": ("Hangul", "Han"),
}
_SUPPORTED_ALTERNATE_SCRIPTS = {
    # These alternates are already accepted by the add-on for the corresponding
    # voice language and complement CLDR's single most-likely script.
    "ks": ("Devanagari",),
    "mni": ("Meetei_Mayek",),
    "sd": ("Devanagari",),
    "sr": ("Latin",),
}


def _parse_ucd_records(path: Path) -> list[tuple[int, int, str]]:
    records: list[tuple[int, int, str]] = []
    for rawLine in path.read_text(encoding="utf-8").splitlines():
        line = rawLine.split("#", 1)[0].strip()
        if not line:
            continue
        span, value = (part.strip() for part in line.split(";", 1))
        if ".." in span:
            startText, endText = span.split("..", 1)
            start, end = int(startText, 16), int(endText, 16)
        else:
            start = end = int(span, 16)
        records.append((start, end, value))
    return records


def _ucd_version(path: Path) -> str:
    match = re.search(r"(?m)^# Scripts-([0-9.]+)\.txt$", path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Could not read the UCD version from {path}")
    return match.group(1)


def _script_aliases(path: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for rawLine in path.read_text(encoding="utf-8").splitlines():
        line = rawLine.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(";")]
        if parts[0] == "sc":
            aliases[parts[1]] = parts[2]
    aliases.update({"Hans": "Han", "Hant": "Han"})
    return aliases


def _likely_scripts(path: Path) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for element in ET.parse(path).findall(".//likelySubtag"):
        target = element.attrib["to"].split("_")
        if len(target) >= 2:
            mappings[element.attrib["from"]] = target[1]
    return mappings


def _supported_locales(voicesJsonPath: Path) -> set[str]:
    packages = json.loads(voicesJsonPath.read_text(encoding="utf-8"))
    locales: set[str] = set()
    for package in packages:
        parts = str(package.get("id", "")).split("-")
        if len(parts) >= 2 and re.fullmatch(r"[a-z]{2,3}", parts[0]) and re.fullmatch(r"[a-z]{2}", parts[1]):
            locales.add(f"{parts[0]}_{parts[1]}")
    return locales


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Build a numeric-aware sort key so 20260820.1 sorts after 20260625.1."""
    key: list[tuple[int, int | str]] = []
    for token in re.findall(r"\d+|[A-Za-z]+", version):
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token.lower()))
    return tuple(key)


def _resolved_engine_roots(engineRoots: Iterable[Path] | None) -> list[Path]:
    return list(engineRoots) if engineRoots is not None else list(ENGINE_ROOT_CANDIDATES)


def newest_bundled_engine_dir(engineRoots: Iterable[Path] | None = None) -> Path | None:
    """Return the newest bundled engine version directory discovered under the engine roots."""
    candidates: list[tuple[tuple[tuple[int, int | str], ...], str, Path]] = []
    for engineRoot in _resolved_engine_roots(engineRoots):
        if not engineRoot.is_dir():
            continue
        for directory in engineRoot.iterdir():
            if directory.is_dir() and ENGINE_VERSION_PATTERN.match(directory.name):
                candidates.append((_version_sort_key(directory.name), directory.name, directory))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2])))
    return candidates[-1][2]


def check_engine_version_alignment(bundledVersion: str, configuredVersion: str) -> str | None:
    """Explain why the newest bundled engine and the production catalog module disagree, if they do."""
    if not configuredVersion:
        locations = ", ".join(str(candidate) for candidate in CATALOG_MODULE_CANDIDATES)
        return f"Could not read ENGINE_VERSION from the production catalog module ({locations})."
    if not bundledVersion:
        return "No bundled WasmTtsEngine version directory was found."
    if bundledVersion == configuredVersion:
        return None
    if _version_sort_key(bundledVersion) > _version_sort_key(configuredVersion):
        return (
            f"The newest bundled engine version ({bundledVersion}) is newer than catalog.py "
            f"ENGINE_VERSION ({configuredVersion}). Bump ENGINE_VERSION to {bundledVersion} "
            "before regenerating."
        )
    return (
        f"catalog.py ENGINE_VERSION ({configuredVersion}) has no bundled engine directory: "
        f"the newest bundled version is {bundledVersion}. Add the {configuredVersion} engine "
        "bundle or fix ENGINE_VERSION."
    )


def _configured_engine_version() -> str:
    """Read ENGINE_VERSION from the production catalog module."""
    for candidate in CATALOG_MODULE_CANDIDATES:
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8-sig"), filename=str(candidate))
        except OSError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "ENGINE_VERSION" for target in targets):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            break
    return ""


def _configured_voices_json(engineRoots: Iterable[Path] | None = None) -> Path:
    """Verify the newest bundled engine against the production catalog module, then return its voices.json."""
    engineDir = newest_bundled_engine_dir(engineRoots)
    if engineDir is None:
        roots = ", ".join(str(candidate) for candidate in _resolved_engine_roots(engineRoots))
        print(
            f"[ABORT] Error: no WasmTtsEngine <version> directory was found under: {roots}",
            file=sys.stderr,
        )
        sys.exit(1)

    voicesJsonPath = engineDir / "voices.json"
    if not voicesJsonPath.is_file():
        print(
            f"[ABORT] Error: the newest bundled engine ({engineDir.name}) has no voices.json. "
            "Add the upstream catalog to the engine bundle before regenerating.",
            file=sys.stderr,
        )
        sys.exit(1)

    problem = check_engine_version_alignment(engineDir.name, _configured_engine_version())
    if problem is not None:
        print(f"[ABORT] Error: {problem}", file=sys.stderr)
        sys.exit(1)

    print(f"Target engine catalog: {voicesJsonPath}")
    return voicesJsonPath


def _supported_language_scripts(
    locales: Iterable[str],
    likelyScripts: dict[str, str],
    scriptAliases: dict[str, str],
) -> dict[str, tuple[str, ...]]:
    byLanguage: dict[str, set[str]] = defaultdict(set)
    for locale in locales:
        language = locale.split("_", 1)[0]
        fallbackLanguage = _CLDR_LANGUAGE_FALLBACKS.get(language, language)
        scriptCode = likelyScripts.get(locale) or likelyScripts.get(language) or likelyScripts.get(fallbackLanguage)
        if scriptCode is None:
            raise ValueError(f"CLDR has no likely script for supported locale {locale}")
        if scriptCode in _CLDR_COMPOSITE_SCRIPTS:
            scripts = _CLDR_COMPOSITE_SCRIPTS[scriptCode]
        else:
            try:
                scripts = (scriptAliases[scriptCode],)
            except KeyError as error:
                raise ValueError(f"UCD has no Script alias for CLDR code {scriptCode}") from error
        byLanguage[language].update(scripts)
    for language, scripts in _SUPPORTED_ALTERNATE_SCRIPTS.items():
        if language in byLanguage:
            byLanguage[language].update(scripts)
    return {language: tuple(sorted(scripts)) for language, scripts in sorted(byLanguage.items())}


def _merge_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _format_ranges(ranges: Iterable[tuple[int, int]], indent: str = "\t\t") -> str:
    return "\n".join(f"{indent}(0x{start:04X}, 0x{end:04X})," for start, end in ranges)


def _format_codepoints(codepoints: Iterable[int], indent: str = "\t") -> str:
    values = [f"0x{codepoint:04X}" for codepoint in sorted(codepoints)]
    lines = []
    for index in range(0, len(values), 10):
        lines.append(indent + ", ".join(values[index : index + 10]) + ",")
    return "\n".join(lines)


def _render_module(
    *,
    ucdVersion: str,
    cldrVersion: str,
    languageScripts: dict[str, tuple[str, ...]],
    scriptRanges: dict[str, tuple[tuple[int, int], ...]],
    sentenceTerminals: set[int],
) -> str:
    lines = [
        '"""Generated Unicode data used by language detection and segmentation.',
        "",
        "Generated by generate_unicode_data.py. Do not edit this file manually.",
        f"UCD source: https://www.unicode.org/Public/{ucdVersion}/ucd/",
        f"CLDR source: https://www.unicode.org/Public/cldr/{cldrVersion}/core.zip",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'UNICODE_VERSION = "{ucdVersion}"',
        f'CLDR_VERSION = "{cldrVersion}"',
        "",
        "SUPPORTED_LANGUAGE_SCRIPTS = {",
    ]
    for language, scripts in languageScripts.items():
        tupleText = ", ".join(repr(script) for script in scripts)
        if len(scripts) == 1:
            tupleText += ","
        lines.append(f'\t"{language}": ({tupleText}),')
    lines.extend(("}", "", "SCRIPT_RANGES = {"))
    for script, ranges in scriptRanges.items():
        lines.append(f'\t"{script}": (')
        lines.append(_format_ranges(ranges))
        lines.append("\t),")
    lines.extend(
        (
            "}",
            "",
            "LANGUAGE_SCRIPT_RANGES = {",
            "\tlanguage: tuple(span for script in scripts for span in SCRIPT_RANGES[script])",
            "\tfor language, scripts in SUPPORTED_LANGUAGE_SCRIPTS.items()",
            "}",
            "",
            "SENTENCE_TERMINAL_CODEPOINTS = frozenset((",
            _format_codepoints(sentenceTerminals),
            "))",
            "",
        )
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ucd-dir", type=Path, required=True)
    parser.add_argument("--likely-subtags", type=Path, required=True)
    parser.add_argument("--cldr-version", required=True)
    parser.add_argument(
        "--voices-json",
        type=Path,
        help="Explicit engine voices.json to read; skips the engine version verification.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    scriptsPath = args.ucd_dir / "Scripts.txt"
    aliasesPath = args.ucd_dir / "PropertyValueAliases.txt"
    propListPath = args.ucd_dir / "PropList.txt"
    ucdVersion = _ucd_version(scriptsPath)
    scriptAliases = _script_aliases(aliasesPath)
    if args.voices_json is not None:
        if not args.voices_json.is_file():
            print(
                f"[ABORT] Error: the explicit engine catalog was not found at {args.voices_json}",
                file=sys.stderr,
            )
            return 1
        print(f"Target engine catalog (explicit): {args.voices_json}")
        print(
            "[WARN] Engine version verification was skipped for an explicit --voices-json target.",
            file=sys.stderr,
        )
        voicesJsonPath = args.voices_json
    else:
        voicesJsonPath = _configured_voices_json()
    languageScripts = _supported_language_scripts(
        _supported_locales(voicesJsonPath),
        _likely_scripts(args.likely_subtags),
        scriptAliases,
    )
    requiredScripts = {script for scripts in languageScripts.values() for script in scripts}
    rangesByScript: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for start, end, script in _parse_ucd_records(scriptsPath):
        if script in requiredScripts:
            rangesByScript[script].append((start, end))
    missingScripts = requiredScripts.difference(rangesByScript)
    if missingScripts:
        raise ValueError(f"UCD is missing required scripts: {sorted(missingScripts)}")
    scriptRanges = {script: _merge_ranges(rangesByScript[script]) for script in sorted(requiredScripts)}
    sentenceTerminals: set[int] = set()
    for start, end, propertyName in _parse_ucd_records(propListPath):
        if propertyName == "Sentence_Terminal":
            sentenceTerminals.update(range(start, end + 1))
    if not sentenceTerminals:
        raise ValueError("UCD PropList.txt contains no Sentence_Terminal data")

    output = _render_module(
        ucdVersion=ucdVersion,
        cldrVersion=args.cldr_version,
        languageScripts=languageScripts,
        scriptRanges=scriptRanges,
        sentenceTerminals=sentenceTerminals,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8", newline="\n")
    print(
        f"Wrote {args.output} for {len(languageScripts)} language roots, "
        f"{len(scriptRanges)} scripts, and {len(sentenceTerminals)} sentence terminals."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

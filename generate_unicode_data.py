#!/usr/bin/env python3
"""Generate compact runtime Unicode tables from official UCD and CLDR files."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
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


def _format_normalization_table(table: dict[int, str], indent: str = "\t") -> str:
    lines = []
    for codepoint in sorted(table):
        lines.append(f"{indent}0x{codepoint:04X}: {repr(table[codepoint])},")
    return "\n".join(lines)


def _build_normalization_table(
    ucdDir: Path,
    cldrCommonDir: Path | None = None,
) -> dict[int, str]:
    table: dict[int, str] = {}
    propListPath = ucdDir / "PropList.txt"
    if propListPath.is_file():
        for start, end, prop in _parse_ucd_records(propListPath):
            if prop == "White_Space":
                for cp in range(start, end + 1):
                    if cp < 0x80:
                        continue
                    if cp in (0x2028, 0x2029):
                        table[cp] = "\n"
                    else:
                        table[cp] = " "
            elif prop == "Bidi_Control":
                for cp in range(start, end + 1):
                    table[cp] = ""

    table[0x200B] = " "
    table[0xFEFF] = ""
    for cp in (0x000B, 0x000C, 0x001C, 0x001D, 0x001E, 0x001F):
        table[cp] = " "

    unicodeDataPath = ucdDir / "UnicodeData.txt"
    rawDecomps: dict[int, str] = {}
    acceptedTags = {
        "font",
        "compat",
        "wide",
        "narrow",
        "circle",
        "square",
        "super",
        "sub",
        "fraction",
        "noBreak",
        "initial",
        "medial",
        "final",
        "isolated",
        "small",
        "vertical",
    }
    if unicodeDataPath.is_file():
        for rawLine in unicodeDataPath.read_text(encoding="utf-8").splitlines():
            line = rawLine.strip()
            if not line:
                continue
            fields = line.split(";")
            cp = int(fields[0], 16)
            name = fields[1]
            decomp = fields[5].strip()
            if decomp.startswith("<"):
                tagName, *codepointHexes = decomp.split()
                tag = tagName[1:-1]
                if tag in acceptedTags:
                    rawDecomps[cp] = "".join(chr(int(h, 16)) for h in codepointHexes)
            elif decomp and (0x2100 <= cp < 0x2150 or 0xFB1D <= cp < 0xFB50):
                rawDecomps[cp] = "".join(chr(int(h, 16)) for h in decomp.split())

            if name.startswith(
                (
                    "NEGATIVE CIRCLED LATIN CAPITAL LETTER ",
                    "NEGATIVE SQUARED LATIN CAPITAL LETTER ",
                    "SQUARED LATIN CAPITAL LETTER ",
                )
            ):
                table[cp] = name.rsplit(" ", 1)[-1]

    for cp, chars in rawDecomps.items():
        current = chars
        for _ in range(10):
            nextStr = "".join(rawDecomps.get(ord(c), c) for c in current)
            if nextStr == current:
                break
            current = nextStr
        current = unicodedata.normalize("NFC", current)
        if 0x249C <= cp <= 0x24B5:
            table[cp] = chr(ord("a") + (cp - 0x249C))
        elif 0x1F110 <= cp <= 0x1F129:
            table[cp] = chr(ord("A") + (cp - 0x1F110))
        elif cp not in table:
            table[cp] = current

    smallCapitals = {
        0x0262: "g",
        0x026A: "i",
        0x0274: "n",
        0x0276: "oe",
        0x0280: "r",
        0x028F: "y",
        0x0299: "b",
        0x029C: "h",
        0x029F: "l",
        0x1D00: "a",
        0x1D01: "ae",
        0x1D04: "c",
        0x1D05: "d",
        0x1D07: "e",
        0x1D0A: "j",
        0x1D0B: "k",
        0x1D0C: "l",
        0x1D0D: "m",
        0x1D0E: "n",
        0x1D0F: "o",
        0x1D18: "p",
        0x1D1B: "t",
        0x1D1C: "u",
        0x1D20: "v",
        0x1D21: "w",
        0x1D22: "z",
        0xA730: "f",
        0xA731: "s",
        0xA7AF: "q",
    }
    table.update(smallCapitals)

    squaredWords = {
        0x1F18E: "AB",
        0x1F18F: "WC",
        0x1F191: "CL",
        0x1F192: "COOL",
        0x1F193: "FREE",
        0x1F194: "ID",
        0x1F195: "NEW",
        0x1F196: "NG",
        0x1F197: "OK",
        0x1F198: "SOS",
        0x1F199: "UP",
        0x1F19A: "VS",
        0x1F19B: "3D",
    }
    table.update(squaredWords)

    if cldrCommonDir is not None:
        charsXml = cldrCommonDir / "supplemental" / "characters.xml"
        if charsXml.is_file():
            tree = ET.parse(charsXml)
            for charElem in tree.findall(".//character"):
                val = charElem.attrib.get("value")
                if not val or len(val) != 1:
                    continue
                cp = ord(val)
                if cp in (0x00A9, 0x00AE, 0x2212, 0x2044, 0x2215):
                    subs = [s.text for s in charElem.findall("substitute") if s.text]
                    if subs:
                        table[cp] = subs[0].strip()

    return table


def _render_module(
    *,
    ucdVersion: str,
    cldrVersion: str,
    languageScripts: dict[str, tuple[str, ...]],
    scriptRanges: dict[str, tuple[tuple[int, int], ...]],
    sentenceTerminals: set[int],
    normalizationTable: dict[int, str] | None = None,
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
        )
    )
    if normalizationTable is not None:
        lines.extend(
            (
                "",
                "NORMALIZATION_TABLE: dict[int, str] = {",
                _format_normalization_table(normalizationTable),
                "}",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ucd-dir", type=Path, required=True)
    parser.add_argument("--likely-subtags", type=Path, required=True)
    parser.add_argument("--cldr-version", required=True)
    parser.add_argument(
        "--cldr-common-dir",
        type=Path,
        help="Optional path to CLDR common directory for supplemental characters data.",
    )
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

    cldrCommonDir = args.cldr_common_dir
    if cldrCommonDir is None and args.likely_subtags.parent.name == "supplemental":
        cldrCommonDir = args.likely_subtags.parent.parent

    normalizationTable = _build_normalization_table(args.ucd_dir, cldrCommonDir)

    output = _render_module(
        ucdVersion=ucdVersion,
        cldrVersion=args.cldr_version,
        languageScripts=languageScripts,
        scriptRanges=scriptRanges,
        sentenceTerminals=sentenceTerminals,
        normalizationTable=normalizationTable,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8", newline="\n")
    print(
        f"Wrote {args.output} for {len(languageScripts)} language roots, "
        f"{len(scriptRanges)} scripts, {len(sentenceTerminals)} sentence terminals, "
        f"and {len(normalizationTable)} normalization codepoints."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

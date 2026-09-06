"""Statically check the add-on's NVDA API contracts against local NVDA trees.

The checker deliberately does not import NVDA. Importing a source checkout on a
different Python/Windows runtime would execute platform code and would make the
result less reproducible than inspecting the source syntax.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NVDA_ROOT = REPO_ROOT.parent / "NVDA source code"
ADDON_ROOT = REPO_ROOT / "googleTtsForNvda"


@dataclass
class CategoryResult:
    name: str
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)


class SourceTree:
    def __init__(self, root: Path):
        self.root = root
        self.source = root / "source"
        self._trees: dict[Path, ast.Module] = {}

    def module_file(self, module: str) -> Path | None:
        base = self.source.joinpath(*module.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def tree(self, module: str) -> ast.Module | None:
        path = self.module_file(module)
        if path is None:
            return None
        if path not in self._trees:
            try:
                self._trees[path] = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (OSError, SyntaxError, UnicodeError):
                return None
        return self._trees[path]

    def node(self, module: str, dotted_name: str) -> ast.AST | None:
        current: ast.AST | None = self.tree(module)
        for part in dotted_name.split("."):
            if current is None:
                return None
            current = _direct_member(current, part)
        return current

    def has(self, module: str, dotted_name: str) -> bool:
        return self.node(module, dotted_name) is not None

    def text(self, module: str) -> str:
        path = self.module_file(module)
        if path is None:
            return ""
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return ""


def _assigned_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {child.id for target in targets for child in ast.walk(target) if isinstance(child, ast.Name)}
    return set()


def _direct_member(parent: ast.AST, name: str) -> ast.AST | None:
    body = getattr(parent, "body", ())
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if name in _assigned_names(node):
            return node
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == name:
                    return node
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    return node
    return None


def _qualified_member(parent: ast.AST, dottedName: str) -> ast.AST | None:
    current: ast.AST | None = parent
    for part in dottedName.split("."):
        if current is None:
            return None
        current = _direct_member(current, part)
    return current


def _has_literal_string_sequence(node: ast.AST | None, expected: tuple[str, ...]) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if not isinstance(child, (ast.Tuple, ast.List)):
            continue
        values = tuple(
            element.value
            for element in child.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
        if len(values) == len(child.elts) and values == expected:
            return True
    return False


def _calls_name(node: ast.AST | None, expectedName: str) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id == expectedName:
            return True
        if isinstance(child.func, ast.Attribute) and child.func.attr == expectedName:
            return True
    return False


def _signature(node: ast.AST | None) -> str:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "<missing>"
    a = node.args
    pos = [arg.arg for arg in (*a.posonlyargs, *a.args)]
    kw = [arg.arg for arg in a.kwonlyargs]
    parts = pos[:]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif kw:
        parts.append("*")
    parts.extend(kw)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _parameters(node: ast.AST | None) -> set[str]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    a = node.args
    return {arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)}


def _require_symbols(result: CategoryResult, tree: SourceTree, specs: Iterable[tuple[str, str]]) -> None:
    for module, symbol in specs:
        result.require(tree.has(module, symbol), f"Missing {module}.{symbol}")


def _category(name: str, tree: SourceTree, specs: Iterable[tuple[str, str]]) -> CategoryResult:
    result = CategoryResult(name)
    _require_symbols(result, tree, specs)
    return result


def _general_categories(tree: SourceTree) -> list[CategoryResult]:
    return [
        _category(
            "Synth driver",
            tree,
            [
                ("synthDriverHandler", item)
                for item in (
                    "SynthDriver",
                    "SynthDriver.VoiceSetting",
                    "SynthDriver.VariantSetting",
                    "SynthDriver.RateSetting",
                    "SynthDriver.RateBoostSetting",
                    "SynthDriver.PitchSetting",
                    "SynthDriver.VolumeSetting",
                    "SynthDriver.loadSettings",
                    "VoiceInfo",
                    "synthIndexReached",
                    "synthDoneSpeaking",
                    "getSynth",
                )
            ]
            + [
                ("autoSettingsUtils.driverSetting", item)
                for item in ("DriverSetting", "BooleanDriverSetting", "NumericDriverSetting")
            ]
            + [("autoSettingsUtils.utils", "StringParameterInfo")]
            + [
                ("speech.commands", item)
                for item in (
                    "BreakCommand",
                    "IndexCommand",
                    "LangChangeCommand",
                    "PitchCommand",
                    "RateCommand",
                    "VolumeCommand",
                )
            ],
        ),
        _category(
            "Global plugin",
            tree,
            [
                ("globalPluginHandler", "GlobalPlugin"),
                ("globalPluginHandler", "GlobalPlugin.terminate"),
                ("core", "postNvdaStartup"),
                ("gui", "mainFrame"),
                ("gui", "messageBox"),
            ],
        ),
        _category(
            "Speech hooks and language profiles",
            tree,
            [
                ("speech.extensions", "filter_speechSequence"),
                ("speech.speech", "processText"),
                ("speech.speech", "getSpellingSpeech"),
                ("speech.shortcutKeys", "shouldUseSpellingFunctionality"),
                ("speechDictHandler", "loadVoiceDict"),
                ("speech.commands", "LangChangeCommand"),
            ],
        ),
        _category(
            "Settings category",
            tree,
            [
                ("gui.settingsDialogs", item)
                for item in (
                    "SettingsPanel",
                    "SettingsPanel.makeSettings",
                    "SettingsPanel.onSave",
                    "AutoSettingsMixin",
                    "AutoSettingsMixin._getSettingMaker",
                    "AutoSettingsMixin._updateValueForControl",
                    "AutoSettingsMixin.onDiscard",
                    "AutoSettingsMixin.refreshGui",
                    "VoiceSettingsPanel",
                    "VoiceSettingsPanel.makeSettings",
                    "NVDASettingsDialog",
                    "NVDASettingsDialog.categoryClasses",
                )
            ]
            + [("gui.guiHelper", item) for item in ("BoxSizerHelper", "LabeledControlHelper", "BORDER_FOR_DIALOGS")]
            + [("gui.nvdaControls", "SelectOnFocusSpinCtrl")],
        ),
        _category(
            "Voice Manager",
            tree,
            [
                ("gui.nvdaControls", "DPIScaledDialog"),
                ("languageHandler", "normalizeLanguage"),
                ("languageHandler", "getLanguageDescription"),
                ("languageHandler", "getLanguage"),
                ("synthDriverHandler", "getSynth"),
                ("ui", "message"),
            ],
        ),
        _category(
            "Updater",
            tree,
            [
                ("gui.addonGui", "installAddon"),
                ("gui.addonGui", "promptUserForRestart"),
                ("ui", "message"),
                ("addonHandler", "initTranslation"),
            ],
        ),
        _category(
            "Browser runtime and standby",
            tree,
            [
                ("addonHandler", "initTranslation"),
                ("config", "conf"),
                ("globalVars", "appArgs"),
                ("globalVars", "appDir"),
                ("logHandler", "log"),
            ],
        ),
        _category(
            "Shared NVDA state",
            tree,
            [
                ("addonHandler", "initTranslation"),
                ("config", "conf"),
                ("globalVars", "appArgs"),
                ("globalVars", "appDir"),
                ("languageHandler", "getLanguage"),
            ],
        ),
    ]


def _audio_category(tree: SourceTree) -> CategoryResult:
    result = CategoryResult("Audio output")
    nvwave = tree.tree("nvwave")
    result.require(nvwave is not None, "Missing nvwave module")
    exports = [name for name in ("WavePlayer", "WinmmWavePlayer", "WasapiWavePlayer") if tree.has("nvwave", name)]
    players = [name for name in exports if isinstance(tree.node("nvwave", name), ast.ClassDef)]
    result.require("WavePlayer" in exports, "nvwave does not export WavePlayer")
    result.require(bool(players), "No concrete supported WavePlayer class found")
    for player in players:
        for method in ("feed", "sync", "idle", "stop", "pause", "close"):
            result.require(tree.has("nvwave", f"{player}.{method}"), f"Missing nvwave.{player}.{method}")
    init = tree.node("nvwave", f"{players[0]}.__init__") if players else None
    params = _parameters(init)
    for param in ("channels", "samplesPerSec", "bitsPerSample", "outputDevice"):
        result.require(param in params, f"{players[0] if players else 'WavePlayer'}.__init__ lacks {param}")
    result.require(tree.has("nvwave", "isInError"), "Missing nvwave.isInError")
    result.details.append(f"exports={', '.join(exports) or '<none>'}; concrete={', '.join(players) or '<none>'}")
    result.details.append(f"constructor={_signature(init)}")
    result.details.append(
        "error API=isInError; legacy audioDeviceError="
        + ("present" if tree.has("nvwave", "audioDeviceError") else "absent")
    )
    config_text = tree.text("config.configSpec")
    sections = []
    current_section = ""
    for line in config_text.splitlines():
        section_match = re.match(r"^\[([^][]+)\]$", line.strip())
        if section_match:
            current_section = section_match.group(1)
        elif re.match(r"^\s*outputDevice\s*=", line) and current_section in {"audio", "speech"}:
            sections.append(current_section)
    # Newer configSpec files build sections as Python dictionaries rather than INI text.
    if not sections and re.search(r"(?m)^\s*outputDevice\s*=", config_text):
        sections.append("audio" if '"audio"' in config_text or "'audio'" in config_text else "speech")
    result.require(bool(sections), "Could not locate outputDevice in configSpec")
    result.details.append(f"outputDevice section={','.join(sections) or '<unknown>'}")
    return result


def _high_risk_category(tree: SourceTree) -> CategoryResult:
    result = CategoryResult("High-risk signatures")
    set_synth = tree.node("synthDriverHandler", "setSynth")
    result.require(set_synth is not None, "Missing synthDriverHandler.setSynth")
    params = _parameters(set_synth)
    result.require(bool(params & {"name", "synthName"}), "setSynth has no recognized synth-name parameter")
    result.require("isFallback" in params, "setSynth lacks isFallback")
    unknown = params - {"name", "synthName", "isFallback", "_leftToTry"}
    result.require(not unknown, f"setSynth has unreviewed parameters: {sorted(unknown)}")
    refresh = tree.node("gui.settingsDialogs", "AutoSettingsMixin.refreshGui")
    result.require(refresh is not None, "Missing AutoSettingsMixin.refreshGui")
    result.require(_parameters(refresh) == {"self"}, f"Unexpected AutoSettingsMixin.refreshGui{_signature(refresh)}")
    result.details.extend((f"setSynth{_signature(set_synth)}", f"AutoSettingsMixin.refreshGui{_signature(refresh)}"))
    return result


def _addon_guard_category() -> CategoryResult:
    result = CategoryResult("Add-on compatibility guards")
    driver_path = ADDON_ROOT / "synthDrivers" / "googleTtsForNvda" / "__init__.py"
    plugin_path = ADDON_ROOT / "globalPlugins" / "googleTtsForNvda" / "__init__.py"
    settings_path = ADDON_ROOT / "globalPlugins" / "googleTtsForNvda" / "settings.py"
    modules = {
        path: ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for path in (driver_path, plugin_path, settings_path)
    }
    current_output_device = _qualified_member(modules[driver_path], "SynthDriver._current_output_device")
    result.require(
        _has_literal_string_sequence(current_output_device, ("audio", "speech")),
        "SynthDriver._current_output_device does not probe audio then speech",
    )
    audio_device_error = _qualified_member(modules[driver_path], "SynthDriver._audio_device_error")
    result.require(
        _has_literal_string_sequence(audio_device_error, ("isInError", "audioDeviceError")),
        "SynthDriver._audio_device_error does not prefer isInError with a guarded legacy fallback",
    )
    language_token_signal = _qualified_member(modules[driver_path], "SynthDriver._language_token_signal")
    result.require(
        _calls_name(language_token_signal, "language_script_signal")
        or _calls_name(language_token_signal, "_language_token_signal")
        or _calls_name(language_token_signal, "language_token_signal"),
        "SynthDriver._language_token_signal does not call the pure language_profiles fallback",
    )
    entry_points = {
        driver_path: (
            "SynthDriver.terminate",
            "SynthDriver.speak",
            "SynthDriver.cancel",
            "SynthDriver.pause",
            "SynthDriver.loadSettings",
        ),
        plugin_path: (
            "_set_synth_with_google_tts_voice_prompt",
            "_patch_read_only_text_setting._get_setting_maker",
            "_patch_read_only_text_setting._update_value_for_control",
            "_patch_read_only_text_setting._on_discard",
            "_patch_read_only_text_setting._refresh_gui",
            "_patch_read_only_text_setting._voice_make_settings",
            "_filter_auto_language_speech_sequence",
            "_patch_auto_language_voice_dictionary.process_text_with_auto_voice_dictionary",
            "_patch_auto_language_voice_dictionary.get_spelling_speech_with_auto_profile",
            "_patch_auto_language_voice_dictionary.should_use_spelling_functionality_with_auto_profile",
            "GlobalPlugin.terminate",
            "GlobalPlugin.on_open_voice_manager",
            "GlobalPlugin.script_openVoiceManager",
            "GlobalPlugin.script_openSettings",
        ),
        settings_path: ("GoogleTtsSettingsPanel.makeSettings", "GoogleTtsSettingsPanel.onSave"),
    }
    checked = 0
    for path, qualifiedNames in entry_points.items():
        module = modules[path]
        displayPath = path.relative_to(REPO_ROOT)
        for qualifiedName in qualifiedNames:
            node = _qualified_member(module, qualifiedName)
            result.require(node is not None, f"Missing add-on NVDA entry point {displayPath}:{qualifiedName}")
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            checked += 1
            result.require(
                node.args.vararg is not None and node.args.kwarg is not None,
                f"{displayPath}:{qualifiedName}{_signature(node)} must preserve *args and **kwargs",
            )
    result.details.append(f"extensible add-on entry points checked={checked}")
    return result


def discover_trees(paths: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for path in paths:
        path = path.resolve()
        if (path / "source" / "synthDriverHandler.py").is_file():
            found.add(path)
        elif path.is_dir():
            for marker in path.glob("*/source/synthDriverHandler.py"):
                found.add(marker.parent.parent)
    return sorted(found, key=lambda item: item.name.casefold())


def check_tree(root: Path) -> tuple[list[CategoryResult], int]:
    tree = SourceTree(root)
    results = _general_categories(tree)
    results.insert(1, _audio_category(tree))
    results.append(_high_risk_category(tree))
    results.append(_addon_guard_category())
    errors = sum(len(result.errors) for result in results)
    return results, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[DEFAULT_NVDA_ROOT],
        help="NVDA checkout or directory containing checkouts",
    )
    args = parser.parse_args()
    trees = discover_trees(args.paths)
    if not trees:
        print("No NVDA source trees found.", file=sys.stderr)
        return 2
    total_errors = 0
    for root in trees:
        print(f"\n{root.name}: {root}")
        results, errors = check_tree(root)
        total_errors += errors
        for result in results:
            print(f"  [{'PASS' if not result.errors else 'FAIL'}] {result.name}")
            for detail in result.details:
                print(f"         {detail}")
            for error in result.errors:
                print(f"         ERROR: {error}")
    print(f"\nChecked {len(trees)} NVDA tree(s); {total_errors} error(s).")
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import array
import ast
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_support import DRIVER_DIR, ROOT, load_driver_module

VENDORED_WEBSOCKET_ROOT = DRIVER_DIR / "websocketClientRepo" / "websocket"
GLOBAL_PLUGIN_DIR = ROOT / "googleTtsForNvda" / "globalPlugins" / "googleTtsForNvda"
DRIVER_INTERNAL_MODULES = {
    "audio_math",
    "bridge",
    "catalog",
    "language_detector",
    "language_profiles",
    "language_utils",
    "speech_processing",
    "standby",
    "unicode_data",
    "voice_store",
    "watcher",
    "websocketClientRepo",
}

GLOBAL_PLUGIN_INTERNAL_MODULES = {
    "settings",
    "updater",
    "updateGui",
    "uiUtils",
    "voiceManager",
}
NVDA_ONLY_MODULES = (
    "addonHandler",
    "config",
    "globalVars",
    "languageHandler",
    "nvwave",
    "synthDriverHandler",
    "wx",
)


class BundledDependencyIsolationTests(unittest.TestCase):
    def test_bridge_ignores_foreign_top_level_websocket_module(self) -> None:
        foreignWebSocket = ModuleType("websocket")
        foreignWebSocket.__file__ = "foreign-addon/websocket/__init__.py"
        previousWebSocket = sys.modules.get("websocket")
        sys.modules["websocket"] = foreignWebSocket
        try:
            bridge = load_driver_module("bridge")
        finally:
            if previousWebSocket is None:
                sys.modules.pop("websocket", None)
            else:
                sys.modules["websocket"] = previousWebSocket

        vendoredRoot = VENDORED_WEBSOCKET_ROOT.parent.resolve()
        loadedPath = Path(bridge.websocket.__file__).resolve()
        self.assertIsNot(bridge.websocket, foreignWebSocket)
        self.assertTrue(loadedPath.is_relative_to(vendoredRoot), loadedPath)
        self.assertTrue(bridge.websocket.__name__.endswith(".websocketClientRepo.websocket"))

        websocketHttp = sys.modules[f"{bridge.websocket.__name__}._http"]
        self.assertFalse(websocketHttp.HAVE_PYTHON_SOCKS)

    def test_vendored_websocket_has_no_third_party_absolute_imports(self) -> None:
        for path in VENDORED_WEBSOCKET_ROOT.rglob("*.py"):
            with self.subTest(path=path.relative_to(DRIVER_DIR)):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        moduleRoots = {alias.name.partition(".")[0] for alias in node.names}
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        moduleRoots = {node.module.partition(".")[0]}
                    else:
                        continue
                    for moduleRoot in moduleRoots:
                        self.assertIn(moduleRoot, sys.stdlib_module_names)

    def test_bundled_websocket_fallbacks_preserve_core_behavior(self) -> None:
        bridge = load_driver_module("bridge")
        websocketPrefix = bridge.websocket.__name__
        websocketAbnf = sys.modules[f"{websocketPrefix}._abnf"]
        websocketUtils = sys.modules[f"{websocketPrefix}._utils"]
        mask = array.array("B", (0x01, 0x02, 0x03, 0x04))
        payload = array.array("B", (0x10, 0x20, 0x30, 0x40, 0x50))
        self.assertEqual(bytes((0x11, 0x22, 0x33, 0x44, 0x51)), websocketAbnf._mask(mask, payload))
        self.assertTrue(websocketUtils.validate_utf8("Tiếng Việt".encode()))
        self.assertFalse(websocketUtils.validate_utf8(b"\xff"))

    def test_driver_internal_modules_use_package_relative_imports(self) -> None:
        self._assert_internal_modules_use_relative_imports(DRIVER_DIR, DRIVER_INTERNAL_MODULES)

    def test_global_plugin_internal_modules_use_package_relative_imports(self) -> None:
        self._assert_internal_modules_use_relative_imports(GLOBAL_PLUGIN_DIR, GLOBAL_PLUGIN_INTERNAL_MODULES)

    def test_pure_driver_modules_have_no_nvda_dependencies(self) -> None:
        for driverModuleName in ("language_profiles", "speech_processing"):
            module = load_driver_module(driverModuleName)
            for moduleName in NVDA_ONLY_MODULES:
                with self.subTest(driverModule=driverModuleName, dependency=moduleName):
                    self.assertNotIn(moduleName, module.__dict__)

    def _assert_internal_modules_use_relative_imports(
        self,
        moduleDirectory: Path,
        internalModuleNames: set[str],
    ) -> None:
        for path in moduleDirectory.glob("*.py"):
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        moduleRoots = {alias.name.partition(".")[0] for alias in node.names}
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        moduleRoots = {node.module.partition(".")[0]}
                    else:
                        continue
                    self.assertTrue(
                        internalModuleNames.isdisjoint(moduleRoots),
                        f"{path.name} imports an internal module through the shared top-level namespace: {moduleRoots}",
                    )

    def test_cld2_candidates_are_anchored_to_the_driver_directory(self) -> None:
        languageDetector = load_driver_module("language_detector")
        expectedRoot = (DRIVER_DIR / "cld2").resolve()
        self.assertEqual(expectedRoot, languageDetector._DLL_DIR.resolve())
        for dllName in languageDetector._DLL_NAMES:
            with self.subTest(dllName=dllName):
                self.assertTrue((languageDetector._DLL_DIR / dllName).resolve().is_relative_to(expectedRoot))

    def test_browser_assets_are_anchored_to_the_driver_directory(self) -> None:
        catalog = load_driver_module("catalog")
        bridge = load_driver_module("bridge")
        self.assertEqual((DRIVER_DIR / "WasmTtsEngine").resolve(), catalog.ENGINE_ROOT.resolve())
        self.assertTrue(catalog.ENGINE_DIR.resolve().is_relative_to(catalog.ENGINE_ROOT.resolve()))
        self.assertEqual((DRIVER_DIR / "web").resolve(), bridge.WEB_DIR.resolve())
        for fileName in catalog.REQUIRED_ENGINE_FILES:
            with self.subTest(fileName=fileName):
                self.assertTrue((catalog.ENGINE_DIR / fileName).is_file())

    def test_bundled_engine_files_are_fully_declared(self) -> None:
        """Every bundled engine file must be declared, and every declaration must exist."""
        catalog = load_driver_module("catalog")
        bundledFiles = sorted(
            path.relative_to(catalog.ENGINE_DIR).as_posix() for path in catalog.ENGINE_DIR.rglob("*") if path.is_file()
        )
        self.assertEqual(sorted(catalog.REQUIRED_ENGINE_FILES), bundledFiles)

    def test_global_plugin_imports_speech_module_alias(self) -> None:
        """Global plugin __init__.py must import speech as speechModule for auto voice dictionary patching."""
        pluginInit = GLOBAL_PLUGIN_DIR / "__init__.py"
        tree = ast.parse(pluginInit.read_text(encoding="utf-8-sig"), filename=str(pluginInit))
        importedAliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertIn("speechModule", importedAliases)


if __name__ == "__main__":
    unittest.main()

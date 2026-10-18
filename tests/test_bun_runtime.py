"""Tests for the bundled Bun speech runtime.

Covers three things the existing suite cannot:

1. ``bun_bridge.classify_expression`` -- the single translation point between the
   CDP ``Runtime.evaluate`` expressions ``WasmTtsEngineBridge`` builds and the
   line protocol ``bunRuntime/worker.js`` speaks.
2. ``bun_bootstrap`` -- generation of the Bun entry points from Google's pristine
   engine sources, including discovery of the minified engine global, which
   changes with every engine build.
3. The duck-typing contract that lets ``ChromeTtsBridge`` and the whole of
   ``WasmTtsEngineBridge`` be shared by both runtimes: every attribute
   ``bridge.py`` reaches for on ``_cdp_client``/``_cdp``/``_process_manager`` must
   exist on ``BunClient``/``BunProcessManager``.

Plus ``fetch_bun_runtime``, which the build scripts and the CI workflow use to put
``bun.exe`` inside the add-on package.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import fetch_bun_runtime
from tests.test_support import DRIVER_DIR, ROOT, bridge_module, load_driver_module

bun_bridge = load_driver_module("bun_bridge")
bun_bootstrap = load_driver_module("bun_bootstrap")
bridge = bridge_module

BUN_RUNTIME_DIR = DRIVER_DIR / "bunRuntime"
SHIM_JS = BUN_RUNTIME_DIR / "shim.js"
WORKER_JS = BUN_RUNTIME_DIR / "worker.js"
HARNESS_JS = DRIVER_DIR / "web" / "bridgeHarness.js"
OFFSCREEN_JS = bun_bootstrap.ENGINE_DIR / "offscreen_compiled.js"


def _attributes_used_on(source: Path, receiverAttrs: tuple[str, ...]) -> set[str]:
    """Collect ``self.<receiver>.<attribute>`` names used anywhere in ``source``."""
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner, ast.Attribute) or inner.attr not in receiverAttrs:
            continue
        if isinstance(inner.value, ast.Name) and inner.value.id == "self":
            used.add(node.attr)
    return used


def _fake_downloader(archive: Path):
    """Stand in for fetch_bun_runtime.download by materialising the cached archive."""

    def download(url: str, destination: Path, *, timeout: int = 300) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, destination)
        return destination

    return download


def _make_fake_zip(root: Path, members: dict[str, bytes]) -> Path:
    archive = root / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zipFile:
        for name, data in members.items():
            zipFile.writestr(name, data)
    return archive


class ClassifyExpressionTests(unittest.TestCase):
    """The CDP-expression -> worker-operation translation."""

    def test_speak_expression_yields_speak_operation_and_payload(self) -> None:
        payload = {"sessionId": "1", "text": "Hello", "voiceName": "Chrome OS US English 1", "rate": 1.175}
        expression = f"window.googleTtsForNvdaSpeak({json.dumps(payload, ensure_ascii=False)})"
        operation, parsed = bun_bridge.classify_expression(expression)
        self.assertEqual("speak", operation)
        self.assertEqual(payload, parsed)

    def test_preload_expression_yields_preload_operation(self) -> None:
        payload = {"sessionId": "preload-1", "voiceName": "Chrome OS US English 1", "lang": "en-US", "text": " "}
        expression = f"window.googleTtsForNvdaPreload({json.dumps(payload)})"
        operation, parsed = bun_bridge.classify_expression(expression)
        self.assertEqual("preload", operation)
        self.assertEqual(payload, parsed)

    def test_stop_expression_is_recognised(self) -> None:
        operation, parsed = bun_bridge.classify_expression(bridge.STOP_EXPRESSION)
        self.assertEqual("stop", operation)
        self.assertIsNone(parsed)

    def test_bare_stop_call_is_recognised(self) -> None:
        operation, _ = bun_bridge.classify_expression("window.googleTtsForNvdaStop()")
        self.assertEqual("stop", operation)

    def test_readiness_expression_is_recognised_before_call_parsing(self) -> None:
        """wait_until_ready() probes with an expression that mentions every entry point."""
        expression = """
\t\ttypeof window.googleTtsForNvdaSpeak === "function"
\t\t&& typeof window.googleTtsForNvdaPreload === "function"
\t\t&& typeof window.googleTtsForNvdaBridge === "function"
\t\t&& typeof window.googleTtsForNvdaReady === "function"
\t\t&& window.googleTtsForNvdaReady() === true
\t\t"""
        operation, parsed = bun_bridge.classify_expression(expression)
        self.assertEqual("ready", operation)
        self.assertIsNone(parsed)

    def test_speak_payload_preserves_unicode(self) -> None:
        expression = 'window.googleTtsForNvdaSpeak({"sessionId": "1", "text": "Tiếng Việt 中文 🎉"})'
        _, parsed = bun_bridge.classify_expression(expression)
        assert parsed is not None
        self.assertEqual("Tiếng Việt 中文 🎉", parsed["text"])

    def test_expression_surrounding_whitespace_is_tolerated(self) -> None:
        operation, _ = bun_bridge.classify_expression('  window.googleTtsForNvdaSpeak({"sessionId":"1"})  \n')
        self.assertEqual("speak", operation)

    def test_malformed_payload_raises_cdp_error(self) -> None:
        with self.assertRaises(bridge.CdpError):
            bun_bridge.classify_expression("window.googleTtsForNvdaSpeak({not json})")

    def test_non_object_payload_raises_cdp_error(self) -> None:
        with self.assertRaises(bridge.CdpError):
            bun_bridge.classify_expression("window.googleTtsForNvdaSpeak([1,2,3])")

    def test_unsupported_expression_raises_cdp_error(self) -> None:
        with self.assertRaises(bridge.CdpError):
            bun_bridge.classify_expression("document.title")

    def test_error_carries_a_technical_detail(self) -> None:
        with self.assertRaises(bridge.CdpError) as caught:
            bun_bridge.classify_expression("1 + 1")
        self.assertTrue(caught.exception.technicalDetail)


class AdapterContractTests(unittest.TestCase):
    """bridge.py must be able to treat BunClient/BunProcessManager like their CDP twins."""

    def test_bun_client_implements_every_cdp_client_attribute_bridge_uses(self) -> None:
        required = _attributes_used_on(DRIVER_DIR / "bridge.py", ("_cdp_client", "_cdp"))
        self.assertTrue(required, "no _cdp_client attributes discovered; the AST scan broke")
        missing = sorted(name for name in required if not hasattr(bun_bridge.BunClient, name))
        self.assertEqual([], missing)

    def test_bun_process_manager_implements_every_attribute_bridge_uses(self) -> None:
        required = _attributes_used_on(DRIVER_DIR / "bridge.py", ("_process_manager",))
        self.assertTrue(required, "no _process_manager attributes discovered; the AST scan broke")
        missing = sorted(name for name in required if not hasattr(bun_bridge.BunProcessManager, name))
        self.assertEqual([], missing)

    def test_bun_client_request_signature_matches_cdp_client(self) -> None:
        """WasmTtsEngineBridge calls request() with keyword arguments."""

        def requestParameters(fileName: str, className: str) -> set[str]:
            tree = ast.parse((DRIVER_DIR / fileName).read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == className:
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == "request":
                            return {argument.arg for argument in item.args.args if argument.arg != "self"}
            return set()

        expected = requestParameters("bridge.py", "CdpClient")
        actual = requestParameters("bun_bridge.py", "BunClient")
        self.assertTrue(expected)
        self.assertEqual(expected, actual)

    def test_fallback_capability_flags(self) -> None:
        """BrowserProcessManager can retry with another candidate; the bundled one cannot."""
        self.assertTrue(bridge.BrowserProcessManager.supports_runtime_fallback)
        self.assertFalse(bun_bridge.BunProcessManager.supports_runtime_fallback)

    def test_fallback_capability_defaults_to_true_for_unknown_managers(self) -> None:
        """Instances built without __init__ (the test fakes) must keep browser fallback."""

        class _Bare:
            pass

        self.assertTrue(bridge._process_manager_supports_fallback(_Bare()))
        self.assertFalse(
            bridge._process_manager_supports_fallback(
                bun_bridge.BunProcessManager.__new__(bun_bridge.BunProcessManager)
            )
        )

    def test_ensure_connection_retries_for_a_browser_manager(self) -> None:
        """Deterministic counterpart to the cancellation race in test_bridge_concurrency."""
        attempts = self._run_ensure_connection(supportsFallback=True)
        self.assertEqual(2, len(attempts), attempts)

    def test_ensure_connection_does_not_retry_for_the_bundled_runtime(self) -> None:
        attempts = self._run_ensure_connection(supportsFallback=False)
        self.assertEqual(1, len(attempts), attempts)

    def _run_ensure_connection(self, *, supportsFallback: bool) -> list[str]:
        from tests.test_support import FakeCdpClient, FakeProcessManager, make_fake_bridge

        manager = FakeProcessManager(urls=["ws://first:1", "ws://second:1"])
        manager.supports_runtime_fallback = supportsFallback
        client = FakeCdpClient()
        bridgeInstance = make_fake_bridge(cdp_client=client, process_manager=manager)
        attempts: list[str] = []

        def alwaysFails(wsUrl: str) -> None:
            attempts.append(wsUrl)
            raise bridge.CdpError("refused", "refused")

        client.connect = alwaysFails
        allChoices = (
            ("chrome", "C:/chrome/chrome.exe"),
            ("edge", "C:/edge/msedge.exe"),
            ("brave", "C:/brave/brave.exe"),
        )

        def fakeChoices(runtime: str | None = None, skipRuntimes: set[str] | None = None) -> tuple:
            # Honour skipRuntimes like the real helper, otherwise the retry loop
            # never exhausts its candidates and spins forever.
            skipped = skipRuntimes or set()
            return tuple(choice for choice in allChoices if choice[0] not in skipped)

        with (
            mock.patch.object(bridge, "_browser_choices", side_effect=fakeChoices),
            self.assertRaises(bridge.CdpError),
        ):
            bridgeInstance.ensure_connection()
        return attempts

    def test_start_and_get_websocket_url_accepts_the_keyword_arguments_bridge_passes(self) -> None:
        import inspect

        signature = inspect.signature(bun_bridge.BunProcessManager.start_and_get_websocket_url)
        for name in ("cancelEvent", "skipRuntimes"):
            self.assertIn(name, signature.parameters)
            self.assertIsNotNone(signature.parameters[name].default is not inspect.Parameter.empty)


class BunDiscoveryTests(unittest.TestCase):
    """Executable discovery, including the GTTS_BUN_PATH override."""

    def setUp(self) -> None:
        self._saved = os.environ.get(bun_bridge.BUN_PATH_ENV_VAR)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop(bun_bridge.BUN_PATH_ENV_VAR, None)
        else:
            os.environ[bun_bridge.BUN_PATH_ENV_VAR] = self._saved

    def test_environment_override_is_the_first_candidate(self) -> None:
        os.environ[bun_bridge.BUN_PATH_ENV_VAR] = "/custom/bun.exe"
        self.assertEqual("/custom/bun.exe", bun_bridge.bun_candidates()[0])

    def test_quoted_environment_override_is_trimmed(self) -> None:
        os.environ[bun_bridge.BUN_PATH_ENV_VAR] = '"C:\\Program Files\\bun\\bun.exe"'
        self.assertEqual("C:\\Program Files\\bun\\bun.exe", bun_bridge.bun_candidates()[0])

    def test_bundled_runtime_directory_is_a_candidate(self) -> None:
        os.environ.pop(bun_bridge.BUN_PATH_ENV_VAR, None)
        candidates = bun_bridge.bun_candidates()
        self.assertIn(str(bun_bridge.RUNTIME_DIR / "bun.exe"), candidates)

    def test_find_bun_returns_none_when_nothing_exists(self) -> None:
        os.environ[bun_bridge.BUN_PATH_ENV_VAR] = str(Path(tempfile.gettempdir()) / "definitely-missing-bun")
        missingRuntime = Path(tempfile.gettempdir()) / "no-such-runtime-dir"
        with (
            mock.patch.object(bun_bridge, "_which", return_value=None),
            mock.patch.object(bun_bridge, "RUNTIME_DIR", missingRuntime),
        ):
            self.assertIsNone(bun_bridge.find_bun())

    def test_find_bun_prefers_the_override_over_the_bundled_copy(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            override = Path(staging) / "override-bun"
            override.write_bytes(b"MZ\x90\x00")
            os.environ[bun_bridge.BUN_PATH_ENV_VAR] = str(override)
            with mock.patch.object(bun_bridge, "_which", return_value=None):
                self.assertEqual(str(override), bun_bridge.find_bun())

    def test_availability_follows_discovery(self) -> None:
        with mock.patch.object(bun_bridge, "find_bun", return_value="/x/bun.exe"):
            self.assertTrue(bun_bridge.bun_runtime_available())
        with mock.patch.object(bun_bridge, "find_bun", return_value=None):
            self.assertFalse(bun_bridge.bun_runtime_available())

    def test_runtime_dir_matches_the_fetcher_destination(self) -> None:
        """The downloader and the runtime must agree on where bun.exe lives."""
        self.assertEqual(fetch_bun_runtime.default_destination().resolve(), bun_bridge.RUNTIME_DIR.resolve())


class UnifiedRuntimeSelectionTests(unittest.TestCase):
    """The bundled runtime is the fourth entry in the existing runtime setting.

    There is deliberately no separate "speech runtime" config key: ``browserRuntime``
    accepts ``bun`` alongside ``chrome``/``edge``/``brave``, so the settings panel
    shows one dropdown with four entries and the standby signature, config
    normalisation and fallback order all keep working unchanged.
    """

    def test_four_runtimes_are_selectable(self) -> None:
        self.assertEqual(("chrome", "edge", "brave", "bun"), bridge.BROWSER_RUNTIMES)
        self.assertEqual(4, len(bridge._runtime_fallback_order("brave")))

    def test_bundled_runtime_is_the_default(self) -> None:
        self.assertEqual("bun", bridge.DEFAULT_BROWSER_RUNTIME)
        with mock.patch.object(bridge, "config", None):
            self.assertEqual("bun", bridge.configured_browser_runtime())

    def test_labels_cover_every_runtime(self) -> None:
        self.assertEqual(set(bridge.BROWSER_RUNTIMES), set(bridge.BROWSER_RUNTIME_LABELS))
        for label in bridge.BROWSER_RUNTIME_LABELS.values():
            self.assertTrue(label)
        self.assertIn("Bundled", bridge.BROWSER_RUNTIME_LABELS["bun"])

    def test_chromium_subset_excludes_the_bundled_runtime(self) -> None:
        self.assertEqual(("chrome", "edge", "brave"), bridge.CHROMIUM_RUNTIMES)
        self.assertNotIn("bun", bridge.CHROMIUM_RUNTIMES)

    def test_browser_choices_never_offers_the_bundled_runtime(self) -> None:
        """BrowserProcessManager must never try to launch bun.exe with --headless."""
        with (
            mock.patch.object(bridge, "browser_path_for_runtime", return_value="/somewhere/executable"),
            mock.patch.object(bridge, "edge_webview2_available", return_value=True),
        ):
            for selected in ("bun", "chrome", "edge", "brave", None):
                with self.subTest(selected=selected):
                    choices = bridge._browser_choices(selected)
                    self.assertTrue(choices)
                    for runtime, _path in choices:
                        self.assertIn(runtime, bridge.CHROMIUM_RUNTIMES)
                        self.assertNotEqual("bun", runtime)

    def test_browser_candidates_for_bun_honour_the_env_override_and_bundled_copy(self) -> None:
        with mock.patch.dict(os.environ, {bridge.BUN_PATH_ENV_VAR: "/custom/bun.exe"}, clear=False):
            candidates = bridge._browser_candidates("bun")
        self.assertEqual("/custom/bun.exe", candidates[0])
        self.assertIn(str(bridge.BASE_DIR / "runtime" / "bun.exe"), candidates)

    def test_browser_candidates_for_bun_never_returns_a_browser(self) -> None:
        candidates = bridge._browser_candidates("bun")
        for candidate in candidates:
            self.assertNotIn("chrome", candidate.lower())
            self.assertNotIn("msedge", candidate.lower())
            self.assertNotIn("brave", candidate.lower())

    def test_path_for_runtime_resolves_bun(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            real = Path(staging) / "bun.exe"
            real.write_bytes(b"MZ")
            with mock.patch.object(bridge, "_browser_candidates", return_value=["/does/not/exist", str(real)]):
                self.assertEqual(str(real), bridge.browser_path_for_runtime("bun"))

    def test_path_for_runtime_returns_none_when_bun_is_absent(self) -> None:
        with mock.patch.object(bridge, "_browser_candidates", return_value=["/does/not/exist"]):
            self.assertIsNone(bridge.browser_path_for_runtime("bun"))

    def test_availability_reports_the_bundled_runtime(self) -> None:
        availability = bridge.browser_availability()
        self.assertEqual(set(bridge.BROWSER_RUNTIMES), set(availability))
        for runtime, available in availability.items():
            with self.subTest(runtime=runtime):
                self.assertIsInstance(available, bool)

    def test_browser_runtime_available_counts_the_bundled_runtime(self) -> None:
        with (
            mock.patch.object(bridge, "bun_runtime_available", return_value=True),
            mock.patch.object(bridge, "find_browser", return_value=None),
        ):
            self.assertTrue(bridge.browser_runtime_available())
        with (
            mock.patch.object(bridge, "bun_runtime_available", return_value=False),
            mock.patch.object(bridge, "find_browser", return_value=None),
        ):
            self.assertFalse(bridge.browser_runtime_available())

    def test_browser_runtime_available_for_bun_needs_no_webview2(self) -> None:
        with (
            mock.patch.object(bridge, "browser_executable_available", return_value=True),
            mock.patch.object(bridge, "edge_webview2_available", return_value=False),
        ):
            self.assertTrue(bridge.browser_runtime_available("bun"))

    def test_snapshot_reports_bun_alongside_the_browsers(self) -> None:
        snapshot = bridge.browser_runtime_snapshot("bun")
        for key in ("selectedRuntime", "paths", "executableAvailability", "availability", "effectiveRuntime"):
            self.assertIn(key, snapshot)
        self.assertEqual("bun", snapshot["selectedRuntime"])
        self.assertEqual(set(bridge.BROWSER_RUNTIMES), set(snapshot["paths"]))
        self.assertEqual(set(bridge.BROWSER_RUNTIMES), set(snapshot["availability"]))

    def test_effective_runtime_prefers_bun_when_it_is_installed(self) -> None:
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "bun_runtime_available", return_value=True),
        ):
            self.assertEqual("bun", bridge.effective_speech_runtime())

    def test_effective_runtime_falls_back_to_a_browser_when_bun_is_missing(self) -> None:
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "bun_runtime_available", return_value=False),
            mock.patch.object(bridge, "_find_browser_choice", return_value=("chrome", "/x/chrome.exe")),
        ):
            self.assertEqual("chrome", bridge.effective_speech_runtime())

    def test_effective_runtime_stays_bun_when_nothing_is_installed(self) -> None:
        """So the user gets the bundled-runtime error, not a confusing browser one."""
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "bun_runtime_available", return_value=False),
            mock.patch.object(bridge, "_find_browser_choice", return_value=None),
        ):
            self.assertEqual("bun", bridge.effective_speech_runtime())

    def test_effective_runtime_falls_back_to_bun_when_no_browser_exists(self) -> None:
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="chrome"),
            mock.patch.object(bridge, "browser_runtime_available", return_value=False),
            mock.patch.object(bridge, "_find_browser_choice", return_value=None),
            mock.patch.object(bridge, "bun_runtime_available", return_value=True),
        ):
            self.assertEqual("bun", bridge.effective_speech_runtime())

    def test_effective_runtime_keeps_a_usable_selected_browser(self) -> None:
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="edge"),
            mock.patch.object(bridge, "browser_runtime_available", return_value=True),
        ):
            self.assertEqual("edge", bridge.effective_speech_runtime())

    def test_speech_runtime_executable_returns_the_bun_path(self) -> None:
        with (
            mock.patch.object(bridge, "effective_speech_runtime", return_value="bun"),
            mock.patch.object(bridge, "bun_executable_path", return_value="/opt/bun.exe"),
        ):
            self.assertEqual("/opt/bun.exe", bridge.speech_runtime_executable())

    def test_speech_runtime_executable_returns_the_browser_path(self) -> None:
        with (
            mock.patch.object(bridge, "effective_speech_runtime", return_value="chrome"),
            mock.patch.object(bridge, "browser_path_for_runtime", return_value="/opt/chrome.exe") as pathMock,
        ):
            self.assertEqual("/opt/chrome.exe", bridge.speech_runtime_executable())
            pathMock.assert_called_once_with("chrome")

    def test_webview2_does_not_block_the_bundled_runtime(self) -> None:
        """A missing WebView2 Runtime must not stop a user who never asked for Edge."""
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "edge_webview2_available", return_value=False),
            mock.patch.object(bridge, "bun_runtime_available", return_value=True),
        ):
            self.assertFalse(bridge.edge_webview2_blocks_effective_runtime())

    def test_set_configured_runtime_persists_bun(self) -> None:
        with mock.patch.object(bridge, "config", None):
            self.assertEqual("bun", bridge.set_configured_browser_runtime("BUN"))
            self.assertEqual("edge", bridge.set_configured_browser_runtime("edge"))

    def test_bun_availability_swallows_a_broken_import(self) -> None:
        with mock.patch.dict("sys.modules", {"_google_tts_for_nvda_test_driver.bun_bridge": None}):
            self.assertIsInstance(bridge.bun_runtime_available(), bool)
            self.assertIsNone(bridge.bun_executable_path())


class EngineGlobalDiscoveryTests(unittest.TestCase):
    """find_engine_global must survive a re-minified engine build."""

    def setUp(self) -> None:
        self.source = OFFSCREEN_JS.read_text(encoding="utf-8")

    def test_finds_the_global_in_the_bundled_engine(self) -> None:
        name = bun_bootstrap.find_engine_global(self.source)
        self.assertTrue(name.isidentifier(), name)
        # The discovered name must really be the engine: `new <name>` never appears,
        # but `<name>.init(` / the instance is registered with chrome.runtime.
        self.assertIn(f"var{name}=new", self.source.replace(" ", ""))

    def test_prefers_the_declaration_before_the_message_listener(self) -> None:
        anchor = "chrome.runtime.onMessage.addListener"
        self.assertIn(anchor, self.source)
        name = bun_bootstrap.find_engine_global(self.source)
        self.assertLess(self.source.index(f"var {name}=new"), self.source.index(anchor))

    def test_falls_back_to_the_last_candidate_without_the_anchor(self) -> None:
        withoutAnchor = self.source.replace("chrome.runtime.onMessage.addListener", "someOtherRegistration")
        self.assertTrue(bun_bootstrap.find_engine_global(withoutAnchor).isidentifier())

    def test_raises_a_clear_error_when_nothing_matches(self) -> None:
        with self.assertRaises(bun_bootstrap.BunBootstrapError):
            bun_bootstrap.find_engine_global("var unrelated = 1;")

    def test_harness_probe_names_are_read_from_the_real_harness(self) -> None:
        probes = bun_bootstrap.harness_probe_names()
        self.assertTrue(probes)
        for probe in probes:
            self.assertTrue(probe.isidentifier(), probe)
            self.assertIn(f'"{probe}"', HARNESS_JS.read_text(encoding="utf-8"))

    def test_harness_probe_names_tolerate_a_missing_harness(self) -> None:
        with mock.patch.object(bun_bootstrap, "BASE_DIR", Path(tempfile.gettempdir()) / "no-such-dir"):
            self.assertEqual((), bun_bootstrap.harness_probe_names())


class BootstrapGenerationTests(unittest.TestCase):
    """Generating the Bun entry points from the pristine engine sources."""

    def test_generate_writes_both_entries_and_a_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            output = Path(staging)
            paths = bun_bootstrap.generate(outputDir=output)
            self.assertEqual({"main", "pthread", "worker"}, set(paths))
            self.assertTrue(paths["main"].is_file())
            self.assertTrue(paths["pthread"].is_file())
            self.assertTrue((output / bun_bootstrap.MANIFEST_NAME).is_file())

    def test_main_entry_contains_the_engine_and_publishes_its_global(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            main = bun_bootstrap.generate(outputDir=Path(staging))["main"].read_text(encoding="utf-8")
        self.assertIn("loadWasmTtsBindings", main)
        self.assertIn("chrome.runtime.onMessage.addListener", main)
        engineGlobal = bun_bootstrap.find_engine_global(OFFSCREEN_JS.read_text(encoding="utf-8"))
        self.assertIn(f"globalThis.__gttsEngine = {engineGlobal};", main)
        # Published under a name bridgeHarness.js probes first, so getTtsEngine()
        # does not have to scan globalThis.
        self.assertIn(f"globalThis.{bun_bootstrap.harness_probe_names()[0]} = {engineGlobal};", main)

    def test_pthread_entry_boots_the_factory_but_omits_the_engine(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            paths = bun_bootstrap.generate(outputDir=Path(staging))
            pthread = paths["pthread"].read_text(encoding="utf-8")
            main = paths["main"].read_text(encoding="utf-8")
        self.assertIn('workerData === "em-pthread"', pthread)
        self.assertIn("loadWasmTtsBindings();", pthread)
        # Workers must not build the engine, only host a pthread.
        self.assertNotIn("chrome.runtime.onMessage.addListener", pthread)
        self.assertLess(len(pthread), len(main))

    def test_entries_load_the_shim_and_bind_the_engine_directory(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            paths = bun_bootstrap.generate(outputDir=Path(staging))
            for key in ("main", "pthread"):
                source = paths[key].read_text(encoding="utf-8")
                self.assertIn("shim.js", source)
                self.assertIn(bun_bootstrap.ENGINE_DIR.as_posix(), source)

    def test_generation_is_cached_until_an_input_changes(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            output = Path(staging)
            first = bun_bootstrap.generate(outputDir=output)
            manifest = output / bun_bootstrap.MANIFEST_NAME
            firstStamp = manifest.stat().st_mtime_ns
            second = bun_bootstrap.generate(outputDir=output)
            self.assertEqual(first, second)
            self.assertEqual(firstStamp, manifest.stat().st_mtime_ns, "cached generation rewrote the manifest")
            manifest.write_text("{}", encoding="utf-8")
            bun_bootstrap.generate(outputDir=output)
            self.assertNotEqual(firstStamp, manifest.stat().st_mtime_ns)

    def test_generated_javascript_is_syntactically_valid(self) -> None:
        """Guard against a malformed wrapper; skipped when node is unavailable."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as staging:
            paths = bun_bootstrap.generate(outputDir=Path(staging))
            for key in ("main", "pthread"):
                with self.subTest(entry=key):
                    completed = subprocess.run(
                        [node, "--check", str(paths[key])],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr[-800:])

    def test_missing_engine_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            empty = Path(staging) / "engine"
            empty.mkdir()
            with self.assertRaises(bun_bootstrap.BunBootstrapError):
                bun_bootstrap.generate(engineDir=empty, outputDir=Path(staging) / "out")

    def test_engine_directory_is_left_untouched(self) -> None:
        """Generated output must never land in WasmTtsEngine/, which the packaging test audits."""
        before = sorted(path.name for path in bun_bootstrap.ENGINE_DIR.iterdir())
        with tempfile.TemporaryDirectory() as staging:
            bun_bootstrap.generate(outputDir=Path(staging))
        self.assertEqual(before, sorted(path.name for path in bun_bootstrap.ENGINE_DIR.iterdir()))


class RuntimeFileTests(unittest.TestCase):
    """The shipped JavaScript, and the guarantee that the harness is reused not copied."""

    def test_runtime_files_exist(self) -> None:
        for path in (SHIM_JS, WORKER_JS):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())

    def test_worker_reuses_the_browser_harness_instead_of_duplicating_it(self) -> None:
        """Audio behaviour must stay identical across both runtimes."""
        source = WORKER_JS.read_text(encoding="utf-8")
        self.assertIn("bridgeHarness.js", source)
        for entryPoint in ("googleTtsForNvdaSpeak", "googleTtsForNvdaPreload", "googleTtsForNvdaStop"):
            self.assertIn(entryPoint, source)
        # The DSP lives only in the harness; a copy here would drift.
        for duplicated in ("processTempoSamples", "limitSample", "buffersToPcmBase64"):
            self.assertNotIn(duplicated, source)

    def test_shim_bridges_the_parent_port_message_gap(self) -> None:
        """Bun does not deliver worker messages to globalThis.onmessage; the shim must."""
        source = SHIM_JS.read_text(encoding="utf-8")
        self.assertIn('parentPort.on("message"', source)
        self.assertIn("globalThis.onmessage", source)

    def test_shim_forces_the_worker_threads_worker_and_worker_data(self) -> None:
        source = SHIM_JS.read_text(encoding="utf-8")
        self.assertIn("workerData", source)
        self.assertIn("em-pthread", source)
        self.assertIn("worker_threads", source)

    def test_shim_stubs_indexeddb_and_relative_fetch(self) -> None:
        source = SHIM_JS.read_text(encoding="utf-8")
        self.assertIn("indexedDB", source)
        self.assertIn("voices.json", source)
        self.assertIn(".zvoice", source)

    def test_shim_does_not_redefine_chrome_or_audiocontext(self) -> None:
        """bridgeHarness.js owns those; redefining them would fork the behaviour."""
        source = SHIM_JS.read_text(encoding="utf-8")
        self.assertNotIn("globalThis.chrome =", source)
        self.assertNotIn("globalThis.AudioContext =", source)
        self.assertNotIn("globalThis.AudioWorkletNode =", source)

    def test_harness_is_unmodified_by_the_bun_runtime(self) -> None:
        """The browser path must keep working, so the harness stays browser-only code."""
        source = HARNESS_JS.read_text(encoding="utf-8")
        self.assertIn("window.googleTtsForNvdaBridge", source)
        self.assertNotIn("process.stdin", source)
        self.assertNotIn("node:worker_threads", source)


class FetchBunRuntimeTests(unittest.TestCase):
    """The downloader shared by build.bat, build.sh and the CI workflow."""

    def test_default_url_is_the_latest_windows_x64_release(self) -> None:
        self.assertEqual(
            "https://github.com/oven-sh/bun/releases/latest/download/bun-windows-x64.zip",
            fetch_bun_runtime.DEFAULT_URL,
        )

    def test_finds_the_executable_nested_in_a_platform_folder(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            nested = root / "bun-windows-x64"
            nested.mkdir()
            (nested / "bun.exe").write_bytes(b"MZ" + b"\x00" * 64)
            found = fetch_bun_runtime.find_bun_executable(root)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual("bun.exe", found.name)

    def test_prefers_the_shallowest_match(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            (root / "deep" / "deeper").mkdir(parents=True)
            (root / "deep" / "deeper" / "bun.exe").write_bytes(b"MZ")
            (root / "bun.exe").write_bytes(b"MZ")
            found = fetch_bun_runtime.find_bun_executable(root)
            assert found is not None
            self.assertEqual(root / "bun.exe", found)

    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            (Path(staging) / "readme.txt").write_text("nothing here", encoding="utf-8")
            self.assertIsNone(fetch_bun_runtime.find_bun_executable(Path(staging)))
            self.assertIsNone(fetch_bun_runtime.find_bun_executable(Path(staging) / "missing"))

    def test_pe_detection(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            good = Path(staging) / "good.exe"
            good.write_bytes(b"MZ\x90\x00\x03\x00")
            bad = Path(staging) / "bad.exe"
            bad.write_text("<html>404</html>", encoding="utf-8")
            self.assertTrue(fetch_bun_runtime.is_pe_executable(good))
            self.assertFalse(fetch_bun_runtime.is_pe_executable(bad))
            self.assertFalse(fetch_bun_runtime.is_pe_executable(Path(staging) / "missing.exe"))

    def test_extract_archive_places_members(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            archive = _make_fake_zip(root, {"bun-windows-x64/bun.exe": b"MZdata", "readme.txt": b"hi"})
            target = fetch_bun_runtime.extract_archive(archive, root / "out")
            self.assertTrue((target / "bun-windows-x64" / "bun.exe").is_file())
            self.assertEqual(b"hi", (target / "readme.txt").read_bytes())

    def test_extract_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            archive = _make_fake_zip(root, {"../escape.txt": b"nope"})
            with self.assertRaises(RuntimeError):
                fetch_bun_runtime.extract_archive(archive, root / "out")
            self.assertFalse((root / "escape.txt").exists())

    def test_sha256_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            path = Path(staging) / "blob"
            path.write_bytes(b"google tts for nvda")
            self.assertEqual(64, len(fetch_bun_runtime.sha256(path)))
            self.assertEqual(fetch_bun_runtime.sha256(path), fetch_bun_runtime.sha256(path))

    def test_install_rejects_a_too_small_executable(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            archive = _make_fake_zip(root, {"bun-windows-x64/bun.exe": b"MZtiny"})
            with (
                mock.patch.object(fetch_bun_runtime, "download", side_effect=_fake_downloader(archive)),
                self.assertRaises(RuntimeError),
            ):
                fetch_bun_runtime.install(destination=root / "runtime", cacheDir=root / "cache", quiet=True)

    def test_install_rejects_a_non_pe_payload(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            payload = b"<!doctype html>rate limited</html>" + b"\x00" * (32 * 1024 * 1024)
            archive = _make_fake_zip(root, {"bun-windows-x64/bun.exe": payload})
            with (
                mock.patch.object(fetch_bun_runtime, "download", side_effect=_fake_downloader(archive)),
                self.assertRaises(RuntimeError),
            ):
                fetch_bun_runtime.install(destination=root / "runtime", cacheDir=root / "cache", quiet=True)

    def test_install_places_and_verifies_a_valid_executable(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            payload = b"MZ" + b"\x00" * (24 * 1024 * 1024)
            archive = _make_fake_zip(root, {"bun-windows-x64/bun.exe": payload})
            destination = root / "runtime"
            with (
                mock.patch.object(fetch_bun_runtime, "download", side_effect=_fake_downloader(archive)),
                mock.patch.object(fetch_bun_runtime, "probe_version", return_value="1.4.3"),
            ):
                installed = fetch_bun_runtime.install(destination=destination, cacheDir=root / "cache", quiet=True)
            self.assertEqual(destination / "bun.exe", installed)
            self.assertTrue(fetch_bun_runtime.is_pe_executable(installed))
            metadata = json.loads((destination / fetch_bun_runtime.VERSION_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual("1.4.3", metadata["version"])
            self.assertEqual(64, len(metadata["executableSha256"]))
            self.assertEqual(len(payload), metadata["executableBytes"])

    def test_install_is_idempotent_when_already_present(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            destination = root / "runtime"
            destination.mkdir()
            existing = destination / "bun.exe"
            existing.write_bytes(b"MZ" + b"\x00" * (24 * 1024 * 1024))
            stamp = existing.stat().st_mtime_ns
            with mock.patch.object(fetch_bun_runtime, "download", side_effect=AssertionError("must not download")):
                installed = fetch_bun_runtime.install(destination=destination, cacheDir=root / "cache", quiet=True)
            self.assertEqual(existing, installed)
            self.assertEqual(stamp, existing.stat().st_mtime_ns)

    def test_install_reports_a_useful_error_when_the_archive_layout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            root = Path(staging)
            archive = _make_fake_zip(root, {"bun-windows-x64/bun": b"MZ" + b"\x00" * (24 * 1024 * 1024)})
            with (
                mock.patch.object(fetch_bun_runtime, "download", side_effect=_fake_downloader(archive)),
                self.assertRaises(RuntimeError) as caught,
            ):
                fetch_bun_runtime.install(destination=root / "runtime", cacheDir=root / "cache", quiet=True)
            self.assertIn("bun.exe", str(caught.exception))

    def test_print_dest_reports_the_packaged_location(self) -> None:
        import contextlib
        import io

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.assertEqual(0, fetch_bun_runtime.main(["--print-dest"]))
        self.assertEqual(
            str(fetch_bun_runtime.default_destination()),
            captured.getvalue().strip(),
        )

    def test_runtime_directory_is_not_committed(self) -> None:
        """bun.exe is a build input, never a tracked source file."""
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("runtime/", gitignore)
        self.assertIn("bun.exe", gitignore)


class InterruptAndAccountingTests(unittest.TestCase):
    """The truncation-on-interrupt defect and the instrumentation that localises it.

    Two compounding defects cut the tail off an utterance that follows an interrupt:
    BunClient.send_command_async() wrote {"op": "stop"} with no id and worker.js
    dropped id-less messages, so send_fast_stop() never reached the engine; and
    web/bridgeHarness.js's speak() success tail fences everything with sessionToken
    except its final `await stopActiveSynthesis()`, which bumps the token, resets the
    audio queue and stops the engine. A superseded invocation unwinding after the next
    session began therefore truncated the *new* utterance.
    """

    def setUp(self) -> None:
        self.workerSource = WORKER_JS.read_text(encoding="utf-8")

    def test_worker_runs_commands_that_carry_no_id(self) -> None:
        """send_fast_stop() is fire-and-forget, so id-less messages must not be dropped."""
        dispatch = self.workerSource[self.workerSource.index("function dispatch(line)") :]
        dispatch = dispatch[: dispatch.index("let pending")]
        self.assertIn("runOp(null, message)", dispatch)
        # the old guard returned before reaching runOp
        self.assertNotIn("if (id === undefined || id === null) {\n\t\treturn;\n\t}", dispatch)

    def test_worker_serialises_concurrent_speak(self) -> None:
        """Two harness invocations must never overlap, or the unfenced tail races."""
        self.assertIn("async function speakExclusive(payload)", self.workerSource)
        self.assertIn("activeSpeak", self.workerSource)
        self.assertIn("googleTtsForNvdaStop", self.workerSource)
        # the in-flight promise is awaited (with a bound) before the next one starts
        self.assertIn("settleWithBound(previous", self.workerSource)
        self.assertIn('if (op === "speak") {\n\t\treturn await speakExclusive(payload);', self.workerSource)

    def test_worker_reports_what_it_emitted(self) -> None:
        self.assertIn("jsAudioPackets", self.workerSource)
        self.assertIn("jsAudioSamples", self.workerSource)

    def test_delivered_audio_is_counted(self) -> None:
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 0
        client._deliveredAudioSamples = 0
        payload = json.dumps({"type": "audio", "sampleRate": 24000, "data": "AAAAAA=="})
        client._count_delivered_audio(payload)
        self.assertEqual(1, client._deliveredAudioPackets)
        self.assertEqual(2, client._deliveredAudioSamples)  # 4 bytes of base64 -> 2 samples

    def test_non_audio_and_malformed_payloads_are_ignored(self) -> None:
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 0
        client._deliveredAudioSamples = 0
        for payload in (None, 42, "not json", json.dumps({"type": "mark", "charIndex": 3})):
            client._count_delivered_audio(payload)
        self.assertEqual(0, client._deliveredAudioPackets)
        self.assertEqual(0, client._deliveredAudioSamples)

    def test_accounting_mismatch_is_reported_as_a_warning(self) -> None:
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 5
        client._deliveredAudioSamples = 1200
        with self.assertLogs(bun_bridge.log, level="WARNING") as captured:
            client._report_audio_accounting({"jsAudioPackets": 9, "jsAudioSamples": 2400})
        self.assertTrue(any("mismatch" in line for line in captured.output))

    def test_accounting_agreement_is_not_a_warning(self) -> None:
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 9
        client._deliveredAudioSamples = 2400
        with self.assertLogs(bun_bridge.log, level="DEBUG") as captured:
            client._report_audio_accounting({"jsAudioPackets": 9, "jsAudioSamples": 2400})
        self.assertFalse(any("mismatch" in line for line in captured.output))

    def test_accounting_is_skipped_when_the_worker_reports_nothing(self) -> None:
        """Older generated workers do not report counts; that must not warn."""
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 3
        client._deliveredAudioSamples = 900
        client._report_audio_accounting({"success": True})
        client._report_audio_accounting(None)


class _WindowsArch:
    """Combined patch for os.name and the PROCESSOR_ARCHITECTURE* environment."""

    def __init__(self, environ: dict[str, str]) -> None:
        self._environ = environ
        self._name = mock.patch.object(bridge.os, "name", "nt")
        self._env = mock.patch.dict(os.environ, environ, clear=True)

    def __enter__(self) -> _WindowsArch:
        self._name.start()
        self._env.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._env.stop()
        self._name.stop()


class ThirtyTwoBitWindowsTests(unittest.TestCase):
    """Bun publishes no 32-bit Windows build, so the bundled runtime must not be
    offered there -- and must not be *reported* available, or the browser fallback
    never engages and speech fails at spawn time with no recovery."""

    def _with_arch(self, architecture: str | None, wow64: str | None = None):
        """Patch the environment AND os.name; without the latter the non-Windows
        early return makes every case pass vacuously."""
        environ = {"PROCESSOR_ARCHITECTURE": architecture} if architecture else {}
        if wow64 is not None:
            environ["PROCESSOR_ARCHITEW6432"] = wow64
        return _WindowsArch(environ)

    def test_64_bit_windows_is_detected(self) -> None:
        with self._with_arch("AMD64"):
            self.assertTrue(bridge._windows_is_64bit())

    def test_32_bit_windows_is_detected(self) -> None:
        with self._with_arch("x86"):
            self.assertFalse(bridge._windows_is_64bit())

    def test_32_bit_process_on_64_bit_windows_is_detected_as_64_bit(self) -> None:
        """NVDA is 32-bit Python, so PROCESSOR_ARCHITEW6432 is the signal that matters."""
        with self._with_arch("x86", wow64="AMD64"):
            self.assertTrue(bridge._windows_is_64bit())

    def test_arm64_windows_counts_as_64_bit(self) -> None:
        with self._with_arch("ARM64"):
            self.assertTrue(bridge._windows_is_64bit())

    def test_missing_architecture_is_treated_as_32_bit_on_windows(self) -> None:
        with self._with_arch(None), mock.patch.object(bridge.os, "name", "nt"):
            self.assertFalse(bridge._windows_is_64bit())

    def test_non_windows_is_not_gated(self) -> None:
        """Development and CI run the checker on Linux, where the OS bitness is not the issue."""
        with mock.patch.object(bridge.os, "name", "posix"):
            self.assertTrue(bridge._windows_is_64bit())

    def test_bun_offers_no_candidates_on_32_bit_windows(self) -> None:
        with (
            mock.patch.object(bridge, "_windows_is_64bit", return_value=False),
            mock.patch.dict(os.environ, {bridge.BUN_PATH_ENV_VAR: "/custom/bun.exe"}, clear=False),
        ):
            self.assertEqual([], bridge._browser_candidates("bun"))

    def test_bun_offers_candidates_on_64_bit_windows(self) -> None:
        with mock.patch.object(bridge, "_windows_is_64bit", return_value=True):
            self.assertTrue(bridge._browser_candidates("bun"))

    def test_bun_reports_unavailable_on_32_bit_windows(self) -> None:
        with mock.patch.object(bridge, "_windows_is_64bit", return_value=False):
            self.assertFalse(bridge.browser_runtime_available("bun"))
            self.assertFalse(bridge.browser_availability()["bun"])
            self.assertIsNone(bridge.browser_path_for_runtime("bun"))

    def test_find_bun_returns_none_on_32_bit_windows_even_when_the_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as staging:
            fake = Path(staging) / "bun.exe"
            fake.write_bytes(b"MZ")
            with (
                # bun_bridge did `from .bridge import _windows_is_64bit`, so its own
                # namespace holds the reference and that is the one to patch.
                mock.patch.object(bun_bridge, "_windows_is_64bit", return_value=False),
                mock.patch.dict(os.environ, {bun_bridge.BUN_PATH_ENV_VAR: str(fake)}, clear=False),
            ):
                self.assertIsNone(bun_bridge.find_bun())
                self.assertFalse(bun_bridge.bun_runtime_available())

    def test_effective_runtime_chooses_a_browser_on_32_bit_windows(self) -> None:
        """The whole point: a 32-bit Windows user still gets speech."""
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "_windows_is_64bit", return_value=False),
            mock.patch.object(bridge, "_find_browser_choice", return_value=("chrome", "C:/chrome.exe")),
            mock.patch.object(bridge, "browser_path_for_runtime", return_value="C:/chrome.exe"),
        ):
            self.assertEqual("chrome", bridge.effective_speech_runtime())
            self.assertEqual("C:/chrome.exe", bridge.speech_runtime_executable())

    def test_effective_runtime_still_prefers_bun_on_64_bit_windows(self) -> None:
        with (
            mock.patch.object(bridge, "configured_browser_runtime", return_value="bun"),
            mock.patch.object(bridge, "_windows_is_64bit", return_value=True),
            mock.patch.object(bun_bridge, "find_bun", return_value="C:/runtime/bun.exe"),
        ):
            self.assertEqual("bun", bridge.effective_speech_runtime())

    def test_settings_panel_shows_the_bundled_runtime_unavailable_on_32_bit_windows(self) -> None:
        with mock.patch.object(bridge, "_windows_is_64bit", return_value=False):
            snapshot = bridge.browser_runtime_snapshot("bun")
        self.assertFalse(snapshot["availability"]["bun"])
        self.assertNotEqual("bun", snapshot["effectiveRuntime"])


class DrainIdleOverrideTests(unittest.TestCase):
    """Truncated speech: the harness treats an 80 ms pump stall as end-of-utterance.

    waitForSynthesisComplete()'s endHasSettled branch is gated on `lastChunkAt <= 0`,
    i.e. no audio at all, so for any utterance that produced sound, `audioHasDrained`
    (an idle gap of synthesisFinishedIdleMs) is the ONLY completion criterion. The
    engine delivers ~128-sample blocks from a chained setTimeout, so a stall longer
    than that discards the tail while still reporting done -- and the driver then
    caches the truncated audio as a complete result.
    """

    def setUp(self) -> None:
        self.harness = HARNESS_JS.read_text(encoding="utf-8")
        self.worker = WORKER_JS.read_text(encoding="utf-8")

    def test_harness_reads_the_override_and_defaults_to_80(self) -> None:
        self.assertIn("globalThis.googleTtsForNvdaDrainIdleMs", self.harness)
        self.assertIn(
            "const synthesisFinishedIdleMs = Number(globalThis.googleTtsForNvdaDrainIdleMs) > 0", self.harness
        )
        # the literal 80 must remain as the fallback so the browser path is unchanged
        self.assertRegex(self.harness, r"googleTtsForNvdaDrainIdleMs[\s\S]{0,120}:\s*80;")

    def test_override_expression_evaluates_to_80_when_unset(self) -> None:
        """Prove the browser path is numerically identical, not just textually similar."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        match = re.search(r"const synthesisFinishedIdleMs = ([\s\S]*?);\n", self.harness)
        assert match is not None
        script = (
            "const f = new Function('return (' + process.argv[1] + ');');"
            "const cases = [['unset', undefined], ['250', 250], ['zero', 0], ['nan', NaN], ['neg', -5]];"
            "for (const [label, value] of cases) {"
            "  if (value === undefined) delete globalThis.googleTtsForNvdaDrainIdleMs;"
            "  else globalThis.googleTtsForNvdaDrainIdleMs = value;"
            "  console.log(label + '=' + f());"
            "}"
        )
        completed = subprocess.run(
            [node, "-e", script, match.group(1)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        values = dict(line.split("=", 1) for line in completed.stdout.split())
        self.assertEqual("80", values["unset"], "browser path must keep 80 ms")
        self.assertEqual("80", values["zero"])
        self.assertEqual("80", values["nan"])
        self.assertEqual("80", values["neg"])
        self.assertEqual("250", values["250"])

    def test_worker_sets_the_override_before_loading_the_harness(self) -> None:
        setAt = self.worker.index("globalThis.googleTtsForNvdaDrainIdleMs = DRAIN_IDLE_MS")
        requireAt = self.worker.index('require(path.join(WEB_DIR, "bridgeHarness.js"))')
        self.assertLess(setAt, requireAt, "the harness reads the value into a const at load time")

    def test_worker_default_is_more_generous_than_the_browser_value(self) -> None:
        match = re.search(r"GTTS_DRAIN_IDLE_MS\) \|\| (\d+)", self.worker)
        assert match is not None
        self.assertGreater(int(match.group(1)), 80)

    def test_runtime_process_priority_is_elevated(self) -> None:
        """A starved or power-throttled process stalls the audio pump."""
        source = (DRIVER_DIR / "bun_bridge.py").read_text(encoding="utf-8-sig")
        self.assertIn("_elevate_chrome_priority(process.pid)", source)

    def test_accounting_ignores_superseded_sessions(self) -> None:
        """Otherwise the warning fires on the very pattern it exists to diagnose."""
        client = bun_bridge.BunClient.__new__(bun_bridge.BunClient)
        client._lock = threading.RLock()
        client._deliveredAudioPackets = 0
        client._deliveredAudioSamples = 0
        stale = json.dumps({"type": "audio", "sessionId": "old", "sampleRate": 24000, "data": "AAAAAA=="})
        current = json.dumps({"type": "audio", "sessionId": "new", "sampleRate": 24000, "data": "AAAAAA=="})
        client._count_delivered_audio(stale, "new")
        self.assertEqual(0, client._deliveredAudioPackets)
        client._count_delivered_audio(current, "new")
        self.assertEqual(1, client._deliveredAudioPackets)


class RedistributionAndDocsTests(unittest.TestCase):
    """Shipping a third-party binary carries obligations the docs must reflect."""

    def setUp(self) -> None:
        self.licensePath = BUN_RUNTIME_DIR / "LICENSE-bun.txt"
        self.manifest = (ROOT / "googleTtsForNvda" / "manifest.ini").read_text(encoding="utf-8-sig")
        self.readme = (ROOT / "readme.md").read_text(encoding="utf-8")

    def test_bun_license_notice_ships_with_the_addon(self) -> None:
        self.assertTrue(self.licensePath.is_file(), "bunRuntime/LICENSE-bun.txt must ship next to bun.exe")

    def test_bun_license_notice_covers_mit_and_the_lgpl_relink_terms(self) -> None:
        text = self.licensePath.read_text(encoding="utf-8")
        self.assertIn("MIT", text)
        # JavaScriptCore is statically linked and is LGPL-2, whose terms require the
        # relinking instructions to travel with the binary.
        self.assertIn("LGPL", text)
        self.assertIn("JavaScriptCore", text)
        self.assertIn("relink", text.lower())

    def test_readme_lists_bun_in_the_license_section(self) -> None:
        self.assertIn("Bun runtime", self.readme)
        self.assertIn("LICENSE-bun.txt", self.readme)

    def test_build_workflow_verifies_the_license_notice_is_packaged(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("synthDrivers/googleTtsForNvda/bunRuntime/LICENSE-bun.txt", workflow)

    def test_manifest_description_no_longer_requires_a_browser(self) -> None:
        description = _manifest_value(self.manifest, "description")
        self.assertIn("bundled", description.lower())
        self.assertNotIn("through a supported Chromium browser runtime", description)
        self.assertIn("no web browser needs to be installed", description)

    def test_manifest_changelog_mentions_the_bundled_runtime(self) -> None:
        self.assertIn("bundled speech runtime", _manifest_value(self.manifest, "changelog"))

    def test_manifest_keeps_its_nvda_version_bounds(self) -> None:
        """Guard against an accidental edit; bumping these is a deliberate release step."""
        self.assertEqual("2024.1.0", _manifest_value(self.manifest, "minimumNVDAVersion"))
        self.assertEqual("2026.3.0", _manifest_value(self.manifest, "lastTestedNVDAVersion"))

    def test_manifest_parses_with_the_parser_nvda_uses(self) -> None:
        """Triple-quoted multi-line values are easy to break; parse it for real."""
        try:
            import configobj
        except ImportError:
            self.skipTest("configobj is not installed")
        parsed = configobj.ConfigObj(str(ROOT / "googleTtsForNvda" / "manifest.ini"), encoding="utf-8")
        for key in ("name", "summary", "description", "author", "version", "changelog"):
            self.assertTrue(str(parsed.get(key, "")).strip(), f"manifest {key} is empty")
        self.assertIn("bundled", parsed["description"].lower())

    def test_readme_documents_all_four_runtimes_and_the_build_download(self) -> None:
        self.assertIn("Do I need to install a web browser?", self.readme)
        self.assertIn("Bundled runtime (no browser required)", self.readme)
        self.assertIn("fetch_bun_runtime.py", self.readme)
        self.assertIn("SKIP_BUN_RUNTIME", self.readme)

    def test_contributing_documents_the_build_workflow_and_the_download(self) -> None:
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("build.yml", contributing)
        self.assertIn("fetch_bun_runtime.py", contributing)
        self.assertIn("check_bun_runtime.py", contributing)


def _manifest_value(text: str, key: str) -> str:
    """Read one manifest.ini value, handling both plain and triple-quoted forms."""
    marker = f"{key} = "
    start = text.index(marker) + len(marker)
    if text.startswith('"""', start):
        end = text.index('"""', start + 3)
        return text[start + 3 : end]
    end = text.index("\n", start)
    return text[start:end].strip().strip('"')


class WorkflowTests(unittest.TestCase):
    """The CI build workflow must bundle the runtime from the documented URL."""

    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    def test_uses_the_requested_release_url(self) -> None:
        self.assertIn(
            "https://github.com/oven-sh/bun/releases/latest/download/bun-windows-x64.zip",
            self.workflow,
        )

    def test_invokes_the_shared_fetcher(self) -> None:
        self.assertIn("fetch_bun_runtime.py", self.workflow)

    def test_verifies_the_runtime_lands_inside_the_package(self) -> None:
        self.assertIn("synthDrivers/googleTtsForNvda/runtime/bun.exe", self.workflow)

    def test_builds_on_windows_and_uploads_the_addon(self) -> None:
        self.assertIn("windows-latest", self.workflow)
        self.assertIn("actions/upload-artifact", self.workflow)
        self.assertIn(".nvda-addon", self.workflow)

    def test_build_scripts_share_the_fetcher(self) -> None:
        for name in ("build.bat", "build.sh"):
            with self.subTest(script=name):
                self.assertIn("fetch_bun_runtime.py", (ROOT / name).read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

"""Tests for pure helper functions in bridge.py.

These tests exercise browser discovery helpers, config normalization,
path safety, error classification, and utility functions that can run
without NVDA or a real browser process.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from tests.test_support import load_driver_module

bridge = load_driver_module("bridge")


# ---------------------------------------------------------------------------
# _safe_join
# ---------------------------------------------------------------------------


class SafeJoinTests(unittest.TestCase):
    """Verify _safe_join prevents path traversal."""

    def setUp(self) -> None:
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp())
        self._root = self._tmpdir / "root"
        self._root.mkdir()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_normal_relative_path(self) -> None:
        result = bridge._safe_join(self._root, "subdir/file.txt")
        self.assertEqual((self._root / "subdir" / "file.txt").resolve(), result)

    def test_rejects_dotdot(self) -> None:
        result = bridge._safe_join(self._root, "../etc/passwd")
        self.assertEqual((self._root / "__invalid__").resolve(), result)

    def test_rejects_absolute_path(self) -> None:
        result = bridge._safe_join(self._root, "/etc/passwd")
        self.assertEqual((self._root / "__invalid__").resolve(), result)

    def test_rejects_encoded_dotdot(self) -> None:
        result = bridge._safe_join(self._root, "%2e%2e/etc/passwd")
        self.assertEqual((self._root / "__invalid__").resolve(), result)

    def test_accepts_clean_relative_path(self) -> None:
        result = bridge._safe_join(self._root, "clean/path")
        self.assertTrue(result.resolve().is_relative_to(self._root.resolve()))


# ---------------------------------------------------------------------------
# _normalize_browser_runtime
# ---------------------------------------------------------------------------


class NormalizeBrowserRuntimeTests(unittest.TestCase):
    """Verify _normalize_browser_runtime maps inputs to valid runtimes."""

    def test_chrome_variants(self) -> None:
        for value in ("chrome", "Chrome", "CHROME", " chrome "):
            with self.subTest(value=value):
                self.assertEqual("chrome", bridge._normalize_browser_runtime(value))

    def test_edge_variants(self) -> None:
        for value in ("edge", "Edge", "EDGE"):
            with self.subTest(value=value):
                self.assertEqual("edge", bridge._normalize_browser_runtime(value))

    def test_brave_variants(self) -> None:
        for value in ("brave", "Brave", "BRAVE"):
            with self.subTest(value=value):
                self.assertEqual("brave", bridge._normalize_browser_runtime(value))

    def test_unknown_defaults_to_chrome(self) -> None:
        self.assertEqual("chrome", bridge._normalize_browser_runtime("firefox"))
        self.assertEqual("chrome", bridge._normalize_browser_runtime(""))
        self.assertEqual("chrome", bridge._normalize_browser_runtime(None))


# ---------------------------------------------------------------------------
# _runtime_fallback_order
# ---------------------------------------------------------------------------


class RuntimeFallbackOrderTests(unittest.TestCase):
    """Verify _runtime_fallback_order puts preferred first, then chrome, edge, brave."""

    def test_preferred_is_first(self) -> None:
        order = bridge._runtime_fallback_order("edge")
        self.assertEqual("edge", order[0])

    def test_chrome_is_second_by_default(self) -> None:
        order = bridge._runtime_fallback_order("chrome")
        self.assertEqual("chrome", order[0])
        self.assertEqual("edge", order[1])

    def test_all_runtimes_present(self) -> None:
        order = bridge._runtime_fallback_order("brave")
        self.assertEqual(3, len(order))
        self.assertIn("chrome", order)
        self.assertIn("edge", order)
        self.assertIn("brave", order)

    def test_no_duplicates(self) -> None:
        order = bridge._runtime_fallback_order("chrome")
        self.assertEqual(len(order), len(set(order)))


# ---------------------------------------------------------------------------
# _format_bytes
# ---------------------------------------------------------------------------


class FormatBytesTests(unittest.TestCase):
    """Verify _format_bytes formats byte counts as MB."""

    def test_one_mb(self) -> None:
        self.assertEqual("1.0 MB", bridge._format_bytes(1024 * 1024))

    def test_zero_bytes(self) -> None:
        self.assertEqual("0.0 MB", bridge._format_bytes(0))

    def test_half_mb(self) -> None:
        self.assertEqual("0.5 MB", bridge._format_bytes(512 * 1024))


# ---------------------------------------------------------------------------
# _is_transient_runtime_evaluate_error
# ---------------------------------------------------------------------------


class TransientErrorClassificationTests(unittest.TestCase):
    """Verify _is_transient_runtime_evaluate_error classifies CDP errors."""

    def test_transient_error_detected(self) -> None:
        error = bridge.CdpError(
            "CDP error",
            "CDP error for Runtime.evaluate: Cannot find default execution context",
        )
        self.assertTrue(bridge._is_transient_runtime_evaluate_error(error))

    def test_non_transient_error_not_detected(self) -> None:
        error = bridge.CdpError("Connection refused", "Connection refused")
        self.assertFalse(bridge._is_transient_runtime_evaluate_error(error))

    def test_non_cdp_error_not_detected(self) -> None:
        self.assertFalse(bridge._is_transient_runtime_evaluate_error(Exception("foo")))


# ---------------------------------------------------------------------------
# _runtime_error_requires_recycle
# ---------------------------------------------------------------------------


class RuntimeRecycleClassificationTests(unittest.TestCase):
    """Verify _runtime_error_requires_recycle classifies errors for recycling."""

    def test_browser_speech_error_requires_recycle(self) -> None:
        error = bridge._BrowserSpeechError("Could not speak", "detail", audioStarted=False)
        self.assertTrue(bridge._runtime_error_requires_recycle(error))

    def test_cdp_cancelled_does_not_require_recycle(self) -> None:
        self.assertFalse(bridge._runtime_error_requires_recycle(bridge.CdpCancelled()))

    def test_timeout_error_requires_recycle(self) -> None:
        error = bridge.CdpError("Timed out", "Timed out waiting for Runtime.evaluate")
        self.assertTrue(bridge._runtime_error_requires_recycle(error))

    def test_generic_exception_requires_recycle(self) -> None:
        self.assertTrue(bridge._runtime_error_requires_recycle(RuntimeError("oops")))


# ---------------------------------------------------------------------------
# _raise_if_cancelled
# ---------------------------------------------------------------------------


class RaiseIfCancelledTests(unittest.TestCase):
    """Verify _raise_if_cancelled raises CdpCancelled when event is set."""

    def test_set_event_raises(self) -> None:
        event = threading.Event()
        event.set()
        with self.assertRaises(bridge.CdpCancelled):
            bridge._raise_if_cancelled(event)

    def test_unset_event_passes(self) -> None:
        event = threading.Event()
        bridge._raise_if_cancelled(event)  # should not raise

    def test_none_event_passes(self) -> None:
        bridge._raise_if_cancelled(None)  # should not raise


# ---------------------------------------------------------------------------
# browser_runtime_for_path
# ---------------------------------------------------------------------------


class BrowserRuntimeForPathTests(unittest.TestCase):
    """Verify browser_runtime_for_path maps executable names to runtimes."""

    def test_msedge_exe(self) -> None:
        self.assertEqual("edge", bridge.browser_runtime_for_path("/Program Files/Microsoft/Edge/msedge.exe"))

    def test_msedge_no_ext(self) -> None:
        self.assertEqual("edge", bridge.browser_runtime_for_path("/usr/bin/msedge"))

    def test_brave_exe(self) -> None:
        self.assertEqual("brave", bridge.browser_runtime_for_path("/Brave/brave.exe"))

    def test_chrome_exe(self) -> None:
        self.assertEqual("chrome", bridge.browser_runtime_for_path("/Chrome/chrome.exe"))

    def test_unknown_defaults_to_chrome(self) -> None:
        self.assertEqual("chrome", bridge.browser_runtime_for_path("/something/unknown.exe"))


# ---------------------------------------------------------------------------
# _friendly_cdp_error
# ---------------------------------------------------------------------------


class FriendlyCdpErrorTests(unittest.TestCase):
    """Verify _friendly_cdp_error creates CdpError with technical detail."""

    def test_message_only(self) -> None:
        error = bridge._friendly_cdp_error("Something failed")
        self.assertIsInstance(error, bridge.CdpError)
        self.assertEqual("Something failed", str(error))
        self.assertIsNone(error.technicalDetail)

    def test_message_with_detail(self) -> None:
        error = bridge._friendly_cdp_error("Something failed", "detail info")
        self.assertEqual("Something failed", str(error))
        self.assertEqual("detail info", error.technicalDetail)


# ---------------------------------------------------------------------------
# browser_runtime_snapshot
# ---------------------------------------------------------------------------


class BrowserRuntimeSnapshotTests(unittest.TestCase):
    """Verify browser_runtime_snapshot returns a complete status dict."""

    def test_snapshot_returns_dict_with_expected_keys(self) -> None:
        snapshot = bridge.browser_runtime_snapshot()
        self.assertIsInstance(snapshot, dict)
        for key in (
            "selectedRuntime",
            "paths",
            "executableAvailability",
            "edgeWebView2Available",
            "availability",
            "effectivePath",
            "effectiveRuntime",
        ):
            self.assertIn(key, snapshot, f"Missing key: {key}")

    def test_selected_runtime_defaults_to_configured(self) -> None:
        snapshot = bridge.browser_runtime_snapshot()
        self.assertEqual(bridge.configured_browser_runtime(), snapshot["selectedRuntime"])

    def test_paths_are_strings_or_none(self) -> None:
        snapshot = bridge.browser_runtime_snapshot()
        for runtime, path in snapshot["paths"].items():
            self.assertIn(runtime, bridge.BROWSER_RUNTIMES)
            self.assertTrue(path is None or isinstance(path, str))

    def test_availability_matches_executable_and_edge(self) -> None:
        snapshot = bridge.browser_runtime_snapshot()
        for runtime in bridge.BROWSER_RUNTIMES:
            exe = snapshot["executableAvailability"].get(runtime, False)
            edge_ok = snapshot["edgeWebView2Available"]
            expected = bool(exe) and (runtime != bridge.BROWSER_RUNTIME_EDGE or edge_ok)
            self.assertEqual(expected, snapshot["availability"].get(runtime, False))


# ---------------------------------------------------------------------------
# edge_webview2_blocks_effective_runtime
# ---------------------------------------------------------------------------


class EdgeWebview2BlocksTests(unittest.TestCase):
    """Verify edge_webview2_blocks_effective_runtime returns a bool."""

    def test_returns_bool(self) -> None:
        result = bridge.edge_webview2_blocks_effective_runtime()
        self.assertIsInstance(result, bool)

    def test_with_explicit_runtime(self) -> None:
        for runtime in bridge.BROWSER_RUNTIMES:
            result = bridge.edge_webview2_blocks_effective_runtime(runtime)
            self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# effective_browser_runtime
# ---------------------------------------------------------------------------


class EffectiveBrowserRuntimeTests(unittest.TestCase):
    """Verify effective_browser_runtime returns a runtime string or None."""

    def test_returns_str_or_none(self) -> None:
        result = bridge.effective_browser_runtime()
        self.assertTrue(result is None or isinstance(result, str))

    def test_with_explicit_runtime(self) -> None:
        for runtime in bridge.BROWSER_RUNTIMES:
            result = bridge.effective_browser_runtime(runtime)
            self.assertTrue(result is None or isinstance(result, str))


# ---------------------------------------------------------------------------
# configured_browser_runtime
# ---------------------------------------------------------------------------


class ConfiguredBrowserRuntimeTests(unittest.TestCase):
    """Verify configured_browser_runtime returns a valid runtime string."""

    def test_returns_valid_runtime(self) -> None:
        runtime = bridge.configured_browser_runtime()
        self.assertIn(runtime, bridge.BROWSER_RUNTIMES)


# ---------------------------------------------------------------------------
# browser_executable_available
# ---------------------------------------------------------------------------


class BrowserExecutableAvailableTests(unittest.TestCase):
    """Verify browser_executable_available returns a bool."""

    def test_returns_bool(self) -> None:
        for runtime in bridge.BROWSER_RUNTIMES:
            result = bridge.browser_executable_available(runtime)
            self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# browser_availability
# ---------------------------------------------------------------------------


class BrowserAvailabilityTests(unittest.TestCase):
    """Verify browser_availability returns a dict of bools."""

    def test_returns_dict_with_all_runtimes(self) -> None:
        avail = bridge.browser_availability()
        self.assertIsInstance(avail, dict)
        for runtime in bridge.BROWSER_RUNTIMES:
            self.assertIn(runtime, avail)
            self.assertIsInstance(avail[runtime], bool)


# ---------------------------------------------------------------------------
# _browser_choices
# ---------------------------------------------------------------------------


class BrowserChoicesTests(unittest.TestCase):
    """Verify _browser_choices filters by availability."""

    def test_returns_tuple(self) -> None:
        choices = bridge._browser_choices()
        self.assertIsInstance(choices, tuple)

    def test_each_choice_is_runtime_path_pair(self) -> None:
        choices = bridge._browser_choices()
        for runtime, path in choices:
            self.assertIn(runtime, bridge.BROWSER_RUNTIMES)
            self.assertIsInstance(path, str)

    def test_skip_runtimes_excludes(self) -> None:
        choices = bridge._browser_choices(skipRuntimes={bridge.BROWSER_RUNTIME_CHROME})
        for runtime, _path in choices:
            self.assertNotEqual(bridge.BROWSER_RUNTIME_CHROME, runtime)


# ---------------------------------------------------------------------------
# browser_runtime_available
# ---------------------------------------------------------------------------


class BrowserRuntimeAvailableTests(unittest.TestCase):
    """Verify browser_runtime_available and classmethod delegations."""

    def test_browser_runtime_available_without_args(self) -> None:
        avail = bridge.browser_runtime_available()
        self.assertIsInstance(avail, bool)

    def test_browser_runtime_available_with_known_runtimes(self) -> None:
        for runtime in bridge.BROWSER_RUNTIMES:
            avail = bridge.browser_runtime_available(runtime)
            self.assertIsInstance(avail, bool)

    def test_browser_runtime_available_unknown_runtime_returns_false(self) -> None:
        self.assertFalse(bridge.browser_runtime_available("non_existent_browser_runtime"))

    def test_browser_process_manager_delegation(self) -> None:
        self.assertEqual(
            bridge.browser_runtime_available(),
            bridge.BrowserProcessManager.browser_runtime_available(),
        )
        for runtime in bridge.BROWSER_RUNTIMES:
            self.assertEqual(
                bridge.browser_runtime_available(runtime),
                bridge.BrowserProcessManager.browser_runtime_available(runtime),
            )

    def test_chrome_tts_bridge_delegation(self) -> None:
        self.assertEqual(
            bridge.browser_runtime_available(),
            bridge.ChromeTtsBridge.browser_runtime_available(),
        )
        for runtime in bridge.BROWSER_RUNTIMES:
            self.assertEqual(
                bridge.browser_runtime_available(runtime),
                bridge.ChromeTtsBridge.browser_runtime_available(runtime),
            )


# ---------------------------------------------------------------------------
# _browser_profile_in_use_error
# ---------------------------------------------------------------------------


class BrowserProfileInUseErrorTests(unittest.TestCase):
    """Verify profile-in-use error creation with exit codes."""

    def test_default_exit_code(self) -> None:
        err = bridge._browser_profile_in_use_error()
        self.assertIn("21", err.technicalDetail)

    def test_custom_exit_code_zero(self) -> None:
        err = bridge._browser_profile_in_use_error(0)
        self.assertIn("0", err.technicalDetail)


if __name__ == "__main__":
    unittest.main()

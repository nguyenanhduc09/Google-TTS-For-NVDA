"""Concurrency and lifecycle tests for standby._StandbyRuntimeManager.

Loads the real standby module with temporary NVDA stubs (isolated inside
_load_standby_module) and exercises real _StandbyRuntimeManager state
transitions, generation-counter fencing, cancelEvent propagation,
claim/release/terminate synchronization, and lock-free watcher handling.
"""

from __future__ import annotations

import logging
import sys
import threading
import unittest
from types import ModuleType
from typing import Any
from unittest import mock

from tests.test_support import (
    DRIVER_DIR,
    _test_driver_package,
    is_lock_free_from_other_thread,
    load_driver_module,
    load_module_from_path,
)

bridge_module = load_driver_module("bridge")


def _load_standby_module() -> ModuleType:
    """Load the real standby module with temporary NVDA stubs."""
    package = _test_driver_package()
    qualname = f"{package.__name__}.standby"
    existing = sys.modules.get(qualname)
    if existing is not None:
        return existing

    from types import SimpleNamespace

    saved = {name: sys.modules.get(name) for name in ("config", "globalVars", "logHandler")}
    fakeConfig = ModuleType("config")
    fakeConfig.conf = {}  # type: ignore[attr-defined]
    fakeGlobalVars = ModuleType("globalVars")
    fakeGlobalVars.appArgs = SimpleNamespace(secure=False)  # type: ignore[attr-defined]
    fakeLogHandler = ModuleType("logHandler")
    fakeLogHandler.log = logging.getLogger("standby-test")  # type: ignore[attr-defined]
    sys.modules["config"] = fakeConfig
    sys.modules["globalVars"] = fakeGlobalVars
    sys.modules["logHandler"] = fakeLogHandler
    try:
        return load_module_from_path(qualname, DRIVER_DIR / "standby.py")
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBridge:
    """Minimal stand-in for ChromeTtsBridge."""

    def __init__(self) -> None:
        self.terminate_calls = 0

    def ensure_connection(self, cancelEvent: Any = None) -> None:
        pass

    def preload_voice(self, options: Any, cancelEvent: Any = None) -> None:
        pass

    def terminate(self) -> None:
        self.terminate_calls += 1


class _DummyWorkerThread:
    """No-op Thread stand-in for synchronous refresh_async state tests."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return False


def _new_manager(*, initialized: bool = True) -> tuple[ModuleType, Any]:
    standby = _load_standby_module()
    mgr = standby._StandbyRuntimeManager()
    if initialized:
        mgr.initialize()
    return standby, mgr


# ---------------------------------------------------------------------------
# Test 1: Generation counter prevents stale workers
# ---------------------------------------------------------------------------


class GenerationCounterTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager generation-counter fencing."""

    def test_refresh_increments_generation(self) -> None:
        """Each refresh_async increments the generation counter."""
        standby, mgr = _new_manager()
        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby.threading, "Thread", _DummyWorkerThread),
        ):
            mgr.refresh_async("first")
            gen1 = mgr._generation
            mgr.refresh_async("second")
            gen2 = mgr._generation

        self.assertEqual(1, gen1)
        self.assertEqual(2, gen2)
        self.assertGreater(gen2, gen1)

    def test_generation_mismatch_cancels_worker(self) -> None:
        """A worker running with an outdated generation aborts without touching state."""
        standby, mgr = _new_manager()
        mgr._generation = 5
        emptyCatalog = bridge_module.VoiceCatalog([])

        with (
            mock.patch.object(standby, "_installed_catalog", return_value=emptyCatalog),
            mock.patch.object(standby, "_catalog_signature", return_value=("sig", 0)),
            mock.patch.object(standby, "ChromeTtsBridge") as mockBridgeCls,
        ):
            mgr._run_refresh(3, threading.Event(), "stale worker")

        mockBridgeCls.assert_not_called()
        self.assertIsNone(mgr._bridge)
        self.assertFalse(mgr._ready)

    def test_generation_match_keeps_worker(self) -> None:
        """A worker whose generation stays current marks the bridge ready."""
        standby, mgr = _new_manager()
        mgr._generation = 5
        emptyCatalog = bridge_module.VoiceCatalog([])
        fakeBridge = _FakeBridge()

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "DirectoryChangeWatcher"),
            mock.patch.object(standby, "_installed_catalog", return_value=emptyCatalog),
            mock.patch.object(standby, "_catalog_signature", return_value=("sig", 0)),
            mock.patch.object(standby, "_warmup_options", return_value=[]),
            mock.patch.object(standby, "ChromeTtsBridge", return_value=fakeBridge),
        ):
            mgr._run_refresh(5, threading.Event(), "matching worker")

        self.assertIs(fakeBridge, mgr._bridge)
        self.assertTrue(mgr._ready)

    def test_claim_increments_generation(self) -> None:
        """claim_bridge increments generation to invalidate old workers."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge
        mgr._signature = "sig"
        mgr._ready = True
        mgr._cancelEvent = threading.Event()

        with mock.patch.object(standby, "_catalog_signature", return_value="sig"):
            claimed = mgr.claim_bridge(emptyCatalog)

        self.assertIs(fakeBridge, claimed)
        self.assertEqual(1, mgr._generation)
        self.assertTrue(mgr._synthActive)
        self.assertIsNone(mgr._cancelEvent)

    def test_release_increments_generation(self) -> None:
        """release_synth_bridge increments generation and schedules standby refresh."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        fakeBridge = _FakeBridge()
        mgr._synthActive = True
        mgr._generation = 3

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "_catalog_signature", return_value="sig"),
            mock.patch.object(mgr, "refresh_async") as mockRefresh,
        ):
            ok = mgr.release_synth_bridge(fakeBridge, emptyCatalog)

        self.assertTrue(ok)
        self.assertEqual(4, mgr._generation)
        self.assertFalse(mgr._synthActive)
        mockRefresh.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: cancelEvent propagation
# ---------------------------------------------------------------------------


class CancelEventTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager cancelEvent propagation across refresh cycles."""

    def test_clear_standby_sets_cancel_event(self) -> None:
        """_cancel_current_worker_locked sets the cancelEvent and clears it."""
        _, mgr = _new_manager()
        cancel = threading.Event()
        mgr._cancelEvent = cancel

        mgr._cancel_current_worker_locked()

        self.assertTrue(cancel.is_set())
        self.assertIsNone(mgr._cancelEvent)

    def test_clear_standby_handles_none_cancel_event(self) -> None:
        """_cancel_current_worker_locked handles None cancelEvent without raising."""
        _, mgr = _new_manager()
        mgr._cancelEvent = None

        mgr._cancel_current_worker_locked()
        self.assertIsNone(mgr._cancelEvent)

    def test_refresh_cancels_existing_worker(self) -> None:
        """Starting a new refresh_async cancels the previous worker's event."""
        standby, mgr = _new_manager()
        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby.threading, "Thread", _DummyWorkerThread),
        ):
            mgr.refresh_async("first")
            oldCancel = mgr._cancelEvent
            mgr.refresh_async("second")
            newCancel = mgr._cancelEvent

        self.assertIsNotNone(oldCancel)
        self.assertIsNotNone(newCancel)
        self.assertIsNot(oldCancel, newCancel)
        self.assertTrue(oldCancel.is_set())
        self.assertFalse(newCancel.is_set())


# ---------------------------------------------------------------------------
# Test 3: claim_bridge behavior
# ---------------------------------------------------------------------------


class ClaimBridgeTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager.claim_bridge behavior."""

    def test_claim_returns_bridge_when_signature_matches(self) -> None:
        """claim_bridge returns the bridge when catalog signature matches."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge
        mgr._signature = "test-signature"
        mgr._ready = True

        with mock.patch.object(standby, "_catalog_signature", return_value="test-signature"):
            result = mgr.claim_bridge(emptyCatalog)

        self.assertIs(fakeBridge, result)
        self.assertIsNone(mgr._bridge)
        self.assertFalse(mgr._ready)
        self.assertEqual(0, fakeBridge.terminate_calls)

    def test_claim_returns_none_when_signature_mismatch(self) -> None:
        """claim_bridge returns None and terminates stale bridge on signature mismatch."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge
        mgr._signature = "old-signature"
        mgr._ready = True

        with mock.patch.object(standby, "_catalog_signature", return_value="new-signature"):
            result = mgr.claim_bridge(emptyCatalog)

        self.assertIsNone(result)
        self.assertIsNone(mgr._bridge)
        self.assertEqual(1, fakeBridge.terminate_calls)

    def test_claim_returns_none_when_shutdown(self) -> None:
        """claim_bridge returns None when shutdown is active."""
        standby, mgr = _new_manager(initialized=False)
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge

        result = mgr.claim_bridge(bridge_module.VoiceCatalog([]))

        self.assertIsNone(result)
        self.assertEqual(0, fakeBridge.terminate_calls)

    def test_claim_returns_none_when_not_ready(self) -> None:
        """claim_bridge returns None and cleans up bridge when not ready."""
        standby, mgr = _new_manager()
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge
        mgr._signature = "test"
        mgr._ready = False

        with mock.patch.object(standby, "_catalog_signature", return_value="test"):
            result = mgr.claim_bridge(bridge_module.VoiceCatalog([]))

        self.assertIsNone(result)
        self.assertIsNone(mgr._bridge)
        self.assertEqual(1, fakeBridge.terminate_calls)


# ---------------------------------------------------------------------------
# Test 4: release_synth_bridge behavior
# ---------------------------------------------------------------------------


class ReleaseSynthBridgeTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager.release_synth_bridge behavior."""

    def test_release_stores_bridge_for_reuse(self) -> None:
        """release_synth_bridge stores the bridge and sets ready=True."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        mgr._synthActive = True
        fakeBridge = _FakeBridge()

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "_catalog_signature", return_value="test-sig"),
            mock.patch.object(mgr, "refresh_async"),
        ):
            ok = mgr.release_synth_bridge(fakeBridge, emptyCatalog)

        self.assertTrue(ok)
        self.assertIs(fakeBridge, mgr._bridge)
        self.assertEqual("test-sig", mgr._signature)
        self.assertTrue(mgr._ready)
        self.assertFalse(mgr._synthActive)

    def test_release_returns_false_when_shutdown(self) -> None:
        """release_synth_bridge returns False when shutdown."""
        _, mgr = _new_manager(initialized=False)
        mgr._synthActive = True
        result = mgr.release_synth_bridge(_FakeBridge(), bridge_module.VoiceCatalog([]))

        self.assertFalse(result)
        self.assertFalse(mgr._synthActive)

    def test_release_terminates_previous_bridge(self) -> None:
        """release_synth_bridge terminates the previous bridge."""
        standby, mgr = _new_manager()
        emptyCatalog = bridge_module.VoiceCatalog([])
        mgr._synthActive = True
        oldBridge = _FakeBridge()
        newBridge = _FakeBridge()
        mgr._bridge = oldBridge

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "_catalog_signature", return_value="test-sig"),
            mock.patch.object(mgr, "refresh_async"),
        ):
            ok = mgr.release_synth_bridge(newBridge, emptyCatalog)

        self.assertTrue(ok)
        self.assertEqual(1, oldBridge.terminate_calls)
        self.assertIs(newBridge, mgr._bridge)


# ---------------------------------------------------------------------------
# Test 5: terminate behavior
# ---------------------------------------------------------------------------


class TerminateTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager.terminate shuts down the manager."""

    def test_terminate_sets_shutdown_flag(self) -> None:
        """terminate sets _shutdown=True and clears/terminates bridge."""
        _, mgr = _new_manager()
        fakeBridge = _FakeBridge()
        mgr._bridge = fakeBridge

        mgr.terminate()

        self.assertTrue(mgr._shutdown)
        self.assertIsNone(mgr._bridge)
        self.assertEqual(1, fakeBridge.terminate_calls)

    def test_terminate_clears_synthetic_active(self) -> None:
        """terminate clears _synthActive flag and stops any attached watcher."""
        _, mgr = _new_manager()
        mgr._synthActive = True
        mgr._bridge = _FakeBridge()
        watcher = mock.MagicMock()
        mgr._watcher = watcher

        mgr.terminate()

        self.assertFalse(mgr._synthActive)
        self.assertIsNone(mgr._watcher)
        watcher.signal_stop.assert_called_once()
        watcher.stop.assert_called_once()

    def test_terminate_increments_generation(self) -> None:
        """terminate increments generation to invalidate old workers."""
        _, mgr = _new_manager()
        mgr._generation = 5
        mgr._bridge = _FakeBridge()

        mgr.terminate()

        self.assertEqual(6, mgr._generation)

    def test_terminate_handles_none_bridge(self) -> None:
        """terminate handles None bridge without raising."""
        _, mgr = _new_manager()

        mgr.terminate()
        self.assertTrue(mgr._shutdown)


class _FakeStandbyWatcher:
    def __init__(self, *args: object, on_stop: Any = None, **kwargs: object) -> None:
        self.started = False
        self.signaled = False
        self.stopped = False
        self.stop_calls = 0
        self._on_stop = on_stop

    def start(self) -> None:
        self.started = True

    def signal_stop(self) -> None:
        self.signaled = True

    def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1
        if self._on_stop is not None:
            self._on_stop()


class StandbyRefreshLockSafetyTests(unittest.TestCase):
    """Verify real _StandbyRuntimeManager._run_refresh avoids holding _lock across I/O and watcher stop."""

    def test_run_refresh_releases_lock_around_catalog_and_watcher_stop(self) -> None:
        standby = _load_standby_module()
        mgr = standby._StandbyRuntimeManager()
        mgr._shutdown = False
        mgr._generation = 1

        lockFreeObserved: dict[str, bool] = {}
        oldWatcher = _FakeStandbyWatcher(
            on_stop=lambda: lockFreeObserved.__setitem__("watcher_stop", is_lock_free_from_other_thread(mgr._lock))
        )
        mgr._watcher = oldWatcher

        def fake_installed_catalog() -> object:
            lockFreeObserved["installed_catalog"] = is_lock_free_from_other_thread(mgr._lock)
            return bridge_module.VoiceCatalog.load()

        def fake_catalog_signature(cat: object) -> tuple[str, int]:
            lockFreeObserved["catalog_signature"] = is_lock_free_from_other_thread(mgr._lock)
            return ("sig", len(getattr(cat, "packages", ())))

        fakeBridgeInst = mock.MagicMock()
        cancelEvent = threading.Event()

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "DirectoryChangeWatcher", _FakeStandbyWatcher),
            mock.patch.object(standby, "_installed_catalog", fake_installed_catalog),
            mock.patch.object(standby, "_catalog_signature", fake_catalog_signature),
            mock.patch.object(standby, "_warmup_options", return_value=[]),
            mock.patch.object(standby, "ChromeTtsBridge", return_value=fakeBridgeInst),
        ):
            mgr._run_refresh(1, cancelEvent, "test refresh")

        self.assertEqual(1, oldWatcher.stop_calls)
        self.assertTrue(lockFreeObserved.get("installed_catalog"), lockFreeObserved)
        self.assertTrue(lockFreeObserved.get("catalog_signature"), lockFreeObserved)
        self.assertTrue(lockFreeObserved.get("watcher_stop"), lockFreeObserved)

    def test_start_watchers_locked_returns_old_watcher_without_stopping_under_lock(self) -> None:
        standby = _load_standby_module()
        mgr = standby._StandbyRuntimeManager()
        mgr._shutdown = False
        mgr._synthActive = False

        oldWatcher = _FakeStandbyWatcher()
        mgr._watcher = oldWatcher

        with (
            mock.patch.object(standby, "keep_browser_runtime_ready_enabled", return_value=True),
            mock.patch.object(standby, "DirectoryChangeWatcher", _FakeStandbyWatcher),
            mgr._lock,
        ):
            returned = mgr._start_watchers_locked()

        self.assertIs(oldWatcher, returned)
        self.assertTrue(oldWatcher.signaled)
        self.assertFalse(oldWatcher.stopped)

    def test_voice_for_language_handles_empty_catalog_and_missing_fallback(self) -> None:
        standby = _load_standby_module()
        self.assertEqual("", standby._voice_for_language(bridge_module.VoiceCatalog([]), "fr-FR", "missing-voice"))


if __name__ == "__main__":
    unittest.main()

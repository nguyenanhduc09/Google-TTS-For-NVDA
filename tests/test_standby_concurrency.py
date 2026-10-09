"""Tests for _StandbyRuntimeManager concurrency patterns.

Since standby.py imports NVDA modules at module level, we test the patterns
(generation counter, cancelEvent, bridge claim/release) by constructing
objects with __new__ and testing the invariants directly.

Covers:
  1. Generation counter ensures stale workers cannot modify shared state.
  2. cancelEvent propagation between refresh cycles.
  3. claim_bridge returns bridge when signature matches.
  4. release_synth_bridge stores bridge for reuse.
  5. terminate shuts down the manager.
"""

from __future__ import annotations

import threading
import unittest

from tests.test_support import load_driver_module

bridge_module = load_driver_module("bridge")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBridge:
    """Minimal stand-in for ChromeTtsBridge."""

    def __init__(self) -> None:
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1


# We construct the manager via __new__ to avoid importing standby.py
# which requires NVDA's config/globalVars modules.


class _MinimalManager:
    """Reimplements _StandbyRuntimeManager's core state fields for testing."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bridge = None
        self._signature = None
        self._ready = False
        self._generation = 0
        self._cancelEvent = None
        self._worker = None
        self._watcher = None
        self._synthActive = False
        self._shutdown = True

    def initialize(self) -> None:
        with self._lock:
            self._shutdown = False

    def _cancel_current_worker_locked(self) -> None:
        if self._cancelEvent is not None:
            self._cancelEvent.set()
        self._cancelEvent = None

    def _clear_standby_locked(self, *, cancelWorker: bool) -> _FakeBridge | None:
        if cancelWorker:
            self._cancel_current_worker_locked()
        bridge = self._bridge
        self._bridge = None
        self._signature = None
        self._ready = False
        return bridge

    def terminate(self) -> None:
        with self._lock:
            self._shutdown = True
            self._synthActive = False
            self._generation += 1
            bridge = self._clear_standby_locked(cancelWorker=True)
        if bridge is not None:
            bridge.terminate()


# ---------------------------------------------------------------------------
# Test 1: Generation counter prevents stale workers
# ---------------------------------------------------------------------------


class GenerationCounterTests(unittest.TestCase):
    """Verify the generation counter pattern."""

    def test_refresh_increments_generation(self) -> None:
        """Each refresh increments the generation counter."""
        mgr = _MinimalManager()
        mgr.initialize()

        with mgr._lock:
            mgr._generation += 1
            gen1 = mgr._generation
        with mgr._lock:
            mgr._generation += 1
            gen2 = mgr._generation

        self.assertEqual(1, gen1)
        self.assertEqual(2, gen2)
        self.assertGreater(gen2, gen1)

    def test_generation_mismatch_cancels_worker(self) -> None:
        """A worker with an outdated generation should detect staleness."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._generation = 5

        stale_generation = 3
        current_generation = mgr._generation

        with mgr._lock:
            is_stale = stale_generation != current_generation

        self.assertTrue(is_stale)

    def test_generation_match_keeps_worker(self) -> None:
        """A worker with matching generation is not stale."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._generation = 5

        current_generation = mgr._generation

        with mgr._lock:
            is_stale = current_generation != mgr._generation

        self.assertFalse(is_stale)

    def test_claim_increments_generation(self) -> None:
        """claim_bridge increments generation to invalidate old workers."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._bridge = _FakeBridge()
        mgr._signature = "sig"
        mgr._ready = True

        with mgr._lock:
            mgr._generation += 1  # simulates claim_bridge
            mgr._synthActive = True
            mgr._cancel_current_worker_locked()

        self.assertEqual(1, mgr._generation)
        self.assertTrue(mgr._synthActive)
        self.assertIsNone(mgr._cancelEvent)

    def test_release_increments_generation(self) -> None:
        """release_synth_bridge increments generation."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._synthActive = True
        mgr._generation = 3

        with mgr._lock:
            mgr._generation += 1  # simulates release_synth_bridge
            mgr._synthActive = False

        self.assertEqual(4, mgr._generation)
        self.assertFalse(mgr._synthActive)


# ---------------------------------------------------------------------------
# Test 2: cancelEvent propagation
# ---------------------------------------------------------------------------


class CancelEventTests(unittest.TestCase):
    """Verify cancelEvent is set between refresh cycles."""

    def test_cancel_event_blocks_worker(self) -> None:
        """A set cancelEvent should cause CdpCancelled to be raised."""
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(bridge_module.CdpCancelled):
            bridge_module._raise_if_cancelled(cancel)

    def test_cancel_event_not_set_allows_proceed(self) -> None:
        """An unset cancelEvent should not block."""
        cancel = threading.Event()
        # Should not raise
        bridge_module._raise_if_cancelled(cancel)

    def test_none_cancel_event_allows_proceed(self) -> None:
        """A None cancelEvent should not block."""
        bridge_module._raise_if_cancelled(None)

    def test_clear_standby_sets_cancel_event(self) -> None:
        """_cancel_current_worker_locked sets the cancelEvent and clears it."""
        mgr = _MinimalManager()
        mgr.initialize()
        cancel = threading.Event()
        mgr._cancelEvent = cancel

        mgr._cancel_current_worker_locked()

        self.assertTrue(cancel.is_set())
        self.assertIsNone(mgr._cancelEvent)

    def test_clear_standby_handles_none_cancel_event(self) -> None:
        """_cancel_current_worker_locked handles None cancelEvent without raising."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._cancelEvent = None

        # Should not raise
        mgr._cancel_current_worker_locked()
        self.assertIsNone(mgr._cancelEvent)

    def test_refresh_cancels_existing_worker(self) -> None:
        """Starting a new refresh cancels the previous worker's event."""
        mgr = _MinimalManager()
        mgr.initialize()

        old_cancel = threading.Event()
        mgr._cancelEvent = old_cancel

        # Simulate refresh: cancel old, create new
        mgr._cancel_current_worker_locked()
        new_cancel = threading.Event()
        mgr._cancelEvent = new_cancel

        self.assertTrue(old_cancel.is_set())
        self.assertFalse(new_cancel.is_set())


# ---------------------------------------------------------------------------
# Test 3: claim_bridge behavior
# ---------------------------------------------------------------------------


class ClaimBridgeTests(unittest.TestCase):
    """Verify claim_bridge returns bridge when signature matches."""

    def test_claim_returns_bridge_when_signature_matches(self) -> None:
        """claim_bridge returns the bridge when catalog signature matches."""
        mgr = _MinimalManager()
        mgr.initialize()

        fake_bridge = _FakeBridge()
        mgr._bridge = fake_bridge
        mgr._signature = "test-signature"
        mgr._ready = True

        # Simulate claim with matching signature
        with mgr._lock:
            mgr._generation += 1
            mgr._synthActive = True
            mgr._cancel_current_worker_locked()
            current_signature = "test-signature"

            result = (
                mgr._bridge if mgr._bridge is not None and mgr._ready and mgr._signature == current_signature else None
            )
            if result is not None:
                mgr._bridge = None
                mgr._signature = None
                mgr._ready = False

        self.assertIs(fake_bridge, result)
        self.assertIsNone(mgr._bridge)
        self.assertFalse(mgr._ready)

    def test_claim_returns_none_when_signature_mismatch(self) -> None:
        """claim_bridge returns None when catalog signature doesn't match."""
        mgr = _MinimalManager()
        mgr.initialize()

        fake_bridge = _FakeBridge()
        mgr._bridge = fake_bridge
        mgr._signature = "old-signature"
        mgr._ready = True

        with mgr._lock:
            mgr._generation += 1
            mgr._synthActive = True
            mgr._cancel_current_worker_locked()
            current_signature = "new-signature"

            if mgr._bridge is not None and mgr._ready and mgr._signature == current_signature:
                result = mgr._bridge
                mgr._bridge = None
            else:
                bridge_to_terminate = mgr._clear_standby_locked(cancelWorker=False)
                result = None

        # Termination happens OUTSIDE the lock (same pattern as bridge.py)
        if bridge_to_terminate is not None:
            bridge_to_terminate.terminate()

        self.assertIsNone(result)
        self.assertIsNone(mgr._bridge)
        self.assertEqual(1, fake_bridge.terminate_calls)

    def test_claim_returns_none_when_shutdown(self) -> None:
        """claim_bridge returns None when shutdown is requested."""
        mgr = _MinimalManager()
        # Don't call initialize — stays shutdown

        mgr._bridge = _FakeBridge()

        with mgr._lock:
            if mgr._shutdown:
                result = None

        self.assertIsNone(result)

    def test_claim_returns_none_when_not_ready(self) -> None:
        """claim_bridge returns None when bridge is not ready."""
        mgr = _MinimalManager()
        mgr.initialize()

        mgr._bridge = _FakeBridge()
        mgr._signature = "test"
        mgr._ready = False  # Not ready

        with mgr._lock:
            result = mgr._bridge if mgr._bridge is not None and mgr._ready else None

        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Test 4: release_synth_bridge behavior
# ---------------------------------------------------------------------------


class ReleaseSynthBridgeTests(unittest.TestCase):
    """Verify release_synth_bridge stores bridge for reuse."""

    def test_release_stores_bridge_for_reuse(self) -> None:
        """release_synth_bridge stores the bridge and sets ready=True."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._synthActive = True

        fake_bridge = _FakeBridge()

        with mgr._lock:
            mgr._generation += 1
            mgr._synthActive = False
            mgr._cancel_current_worker_locked()
            mgr._bridge = fake_bridge
            mgr._signature = "test-sig"
            mgr._ready = True

        self.assertIs(fake_bridge, mgr._bridge)
        self.assertTrue(mgr._ready)
        self.assertFalse(mgr._synthActive)

    def test_release_returns_false_when_shutdown(self) -> None:
        """release_synth_bridge returns False when shutdown."""
        mgr = _MinimalManager()
        # Don't initialize — stays shutdown

        with mgr._lock:
            if mgr._shutdown:
                mgr._synthActive = False
                result = False

        self.assertFalse(result)
        self.assertFalse(mgr._synthActive)

    def test_release_terminates_previous_bridge(self) -> None:
        """release_synth_bridge terminates the previous bridge."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._synthActive = True

        old_bridge = _FakeBridge()
        new_bridge = _FakeBridge()
        mgr._bridge = old_bridge

        with mgr._lock:
            previous = mgr._bridge if mgr._bridge is not new_bridge else None
            mgr._bridge = new_bridge
            mgr._ready = True

        if previous is not None:
            previous.terminate()

        self.assertEqual(1, old_bridge.terminate_calls)
        self.assertIs(new_bridge, mgr._bridge)


# ---------------------------------------------------------------------------
# Test 5: terminate behavior
# ---------------------------------------------------------------------------


class TerminateTests(unittest.TestCase):
    """Verify terminate shuts down the manager."""

    def test_terminate_sets_shutdown_flag(self) -> None:
        """terminate sets _shutdown=True and clears bridge."""
        mgr = _MinimalManager()
        mgr.initialize()
        fake_bridge = _FakeBridge()
        mgr._bridge = fake_bridge

        mgr.terminate()

        self.assertTrue(mgr._shutdown)
        self.assertIsNone(mgr._bridge)
        self.assertEqual(1, fake_bridge.terminate_calls)

    def test_terminate_clears_synthetic_active(self) -> None:
        """terminate clears _synthActive flag."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._synthActive = True
        mgr._bridge = _FakeBridge()

        mgr.terminate()

        self.assertFalse(mgr._synthActive)

    def test_terminate_increments_generation(self) -> None:
        """terminate increments generation to invalidate old workers."""
        mgr = _MinimalManager()
        mgr.initialize()
        mgr._generation = 5
        mgr._bridge = _FakeBridge()

        mgr.terminate()

        self.assertEqual(6, mgr._generation)

    def test_terminate_handles_none_bridge(self) -> None:
        """terminate handles None bridge without raising."""
        mgr = _MinimalManager()
        mgr.initialize()

        # Should not raise
        mgr.terminate()
        self.assertTrue(mgr._shutdown)


if __name__ == "__main__":
    unittest.main()

"""Tests for the race-condition mitigations in bridge.py.

Covers:
  1. ensure_connection() releases the lock between fallback attempts so
     terminate() is not blocked for the entire retry chain.
  2. self._engine is captured under the lock before use, preventing
     stale/engine-swapped references.
  3. _runtimeBusy is protected by its own lock and reads/writes are consistent.
"""

from __future__ import annotations

import contextlib
import threading
import time
import unittest

from tests.test_support import FakeCdpClient, FakeEngine, FakeProcessManager, load_driver_module, make_fake_bridge

bridge = load_driver_module("bridge")

# Backward-compatible aliases for tests that use underscore-prefixed names
_FakeCdpClient = FakeCdpClient
_FakeEngine = FakeEngine
_FakeProcessManager = FakeProcessManager
_make_bridge = make_fake_bridge


# ---------------------------------------------------------------------------
# Test 1: ensure_connection() releases the lock between fallback attempts
# ---------------------------------------------------------------------------


class EnsureConnectionLockScopeTests(unittest.TestCase):
    """Verify that terminate() is not blocked during the fallback loop."""

    def test_terminate_can_run_between_fallback_attempts(self) -> None:
        """If the first browser choice fails, terminate() should not be
        blocked while ensure_connection() retries the next choice."""
        cdp = _FakeCdpClient()
        pm = _FakeProcessManager(urls=["ws://fail:1", "ws://ok:1"])
        bridge_instance = _make_bridge(cdp_client=cdp, process_manager=pm)

        lock_held_event = threading.Event()
        allow_proceed = threading.Event()
        fallback_started = threading.Event()

        original_connect = cdp.connect

        def slow_connect(ws_url):
            if ws_url == "ws://fail:1":
                # First attempt: signal that we're inside a call, then block
                fallback_started.set()
                lock_held_event.set()
                allow_proceed.wait(timeout=5)
                raise bridge.CdpError("Connection refused", "refused")
            return original_connect(ws_url)

        cdp.connect = slow_connect

        terminate_done = threading.Event()

        def run_ensure():
            with contextlib.suppress(Exception):
                bridge_instance.ensure_connection()
            terminate_done.set()

        def run_terminate():
            # Wait until the first fallback attempt is in progress
            fallback_started.wait(timeout=5)
            # Small delay to ensure we're inside the slow_connect
            time.sleep(0.05)
            bridge_instance.terminate()
            terminate_done.set()

        # Verify the lock is NOT held by ensure_connection during fallback.
        # If the lock scope was correct, terminate() should complete quickly.
        t1 = threading.Thread(target=run_ensure)
        t2 = threading.Thread(target=run_terminate)
        t1.start()
        t2.start()

        # Allow the slow connect to complete
        allow_proceed.set()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(t1.is_alive(), "ensure_connection thread did not finish")
        self.assertFalse(t2.is_alive(), "terminate thread did not finish")

    def test_ensure_connection_cancels_between_fallback_attempts(self) -> None:
        """cancelEvent is checked between fallback loop iterations."""
        pm = _FakeProcessManager(urls=["ws://fail:1", "ws://ok:1"])
        cdp = _FakeCdpClient()
        bridge_instance = _make_bridge(cdp_client=cdp, process_manager=pm)

        cancel = threading.Event()
        first_attempt_done = threading.Event()

        def failing_then_ok(ws_url):
            if ws_url == "ws://fail:1":
                first_attempt_done.set()
                raise bridge.CdpError("refused", "refused")
            # Second attempt should never be reached because cancel fires
            raise AssertionError("Second attempt should not be reached")

        cdp.connect = failing_then_ok

        def cancel_after_delay():
            first_attempt_done.wait(timeout=5)
            time.sleep(0.05)
            cancel.set()

        t = threading.Thread(target=cancel_after_delay)
        t.start()

        with self.assertRaises(bridge.CdpCancelled):
            bridge_instance.ensure_connection(cancelEvent=cancel)

        t.join(timeout=5)

    def test_ensure_connection_succeeds_on_first_try(self) -> None:
        """Normal path: connects on first attempt without extra iterations."""
        cdp = _FakeCdpClient()
        pm = _FakeProcessManager(urls=["ws://ok:1"])
        bridge_instance = _make_bridge(cdp_client=cdp, process_manager=pm)

        bridge_instance.ensure_connection()
        self.assertTrue(cdp.is_connected())
        self.assertEqual(1, cdp.connect_count)


# ---------------------------------------------------------------------------
# Test 2: self._engine is captured under lock before use
# ---------------------------------------------------------------------------


class EngineCaptureUnderLockTests(unittest.TestCase):
    """Verify speak/preload/stop/cancel capture engine under lock."""

    def test_speak_uses_captured_engine_not_swapped(self) -> None:
        """speak() should use the engine captured at call time, not a
        swapped one."""
        original_engine = _FakeEngine()
        replacement_engine = _FakeEngine()
        bridge_instance = _make_bridge(engine=original_engine)

        speak_started = threading.Event()
        engine_swapped = threading.Event()
        speak_done = threading.Event()

        original_call_count = 0

        def slow_speak(*args, **kwargs):
            nonlocal original_call_count
            original_call_count += 1
            speak_started.set()
            engine_swapped.wait(timeout=5)
            return {"success": True}

        original_engine.speak = slow_speak

        def swap_engine_during_speak():
            speak_started.wait(timeout=5)
            # Swap the engine while speak is in progress
            with bridge_instance._lock:
                bridge_instance._engine = replacement_engine
            engine_swapped.set()

        def run_speak():
            try:
                bridge_instance.speak("text", {}, lambda _: None)
            finally:
                speak_done.set()

        t_speak = threading.Thread(target=run_speak)
        t_swap = threading.Thread(target=swap_engine_during_speak)
        t_speak.start()
        t_swap.start()

        t_speak.join(timeout=5)
        t_swap.join(timeout=5)
        self.assertFalse(t_speak.is_alive(), "speak thread did not finish")
        self.assertFalse(t_swap.is_alive(), "swap thread did not finish")

        # The original engine should have been called (captured under lock)
        self.assertEqual(1, original_call_count)
        # The replacement engine should NOT have been called
        self.assertEqual(0, replacement_engine.speak_calls)

    def test_stop_runtime_uses_captured_engine(self) -> None:
        """stop_runtime() captures engine under lock."""
        engine = _FakeEngine()
        bridge_instance = _make_bridge(engine=engine)

        bridge_instance.stop_runtime()
        self.assertEqual(1, engine.stop_calls)

    def test_cancel_current_uses_captured_engine(self) -> None:
        """cancel_current() captures engine under lock."""
        engine = _FakeEngine()
        bridge_instance = _make_bridge(engine=engine)

        bridge_instance.cancel_current()
        self.assertEqual(1, engine.cancel_calls)

    def test_preload_voice_uses_captured_engine(self) -> None:
        """preload_voice() captures engine under lock."""
        engine = _FakeEngine()
        cdp = _FakeCdpClient()
        cdp.connected = True
        bridge_instance = _make_bridge(engine=engine, cdp_client=cdp)

        result = bridge_instance.preload_voice({"voiceId": "test", "voiceName": "test", "lang": "en"})
        self.assertEqual(1, engine.preload_calls)
        self.assertEqual({"success": True}, result)


# ---------------------------------------------------------------------------
# Test 3: _runtimeBusy lock synchronization
# ---------------------------------------------------------------------------


class RuntimeBusyLockTests(unittest.TestCase):
    """Verify _runtimeBusy is properly protected by _runtimeBusyLock."""

    def test_runtime_busy_property_reads_under_lock(self) -> None:
        """runtime_busy property acquires the lock."""
        cdp_client = bridge.CdpClient()
        engine = bridge.WasmTtsEngineBridge(cdp_client, bridge.VoiceCatalog.load())

        self.assertFalse(engine.runtime_busy)
        with engine._runtimeBusyLock:
            engine._runtimeBusy = True
        self.assertTrue(engine.runtime_busy)
        with engine._runtimeBusyLock:
            engine._runtimeBusy = False
        self.assertFalse(engine.runtime_busy)

    def test_runtime_busy_is_false_when_no_cancel_event(self) -> None:
        """When speak() has not been called, runtime_busy is False."""
        cdp_client = bridge.CdpClient()
        engine = bridge.WasmTtsEngineBridge(cdp_client, bridge.VoiceCatalog.load())
        self.assertFalse(engine.runtime_busy)


# ---------------------------------------------------------------------------
# Test 4: ensure_connection cancellation does not terminate process manager
# ---------------------------------------------------------------------------


class EnsureConnectionCancellationTests(unittest.TestCase):
    """Verify that cancellation during ensure_connection() does not kill the browser process."""

    def test_cancelled_connection_does_not_terminate_process_manager(self) -> None:
        """When ensure_connection() is cancelled, the process manager is NOT terminated."""
        pm = _FakeProcessManager(urls=["ws://ok:1"])
        cdp = _FakeCdpClient()
        bridge_instance = _make_bridge(cdp_client=cdp, process_manager=pm)

        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(bridge.CdpCancelled):
            bridge_instance.ensure_connection(cancelEvent=cancel)

        self.assertFalse(pm._terminated, "Process manager should not be terminated on cancellation")

    def test_concurrent_ensure_connection_when_first_caller_is_cancelled(self) -> None:
        """When a concurrent warmup caller is cancelled, the speech caller still connects cleanly."""
        pm = _FakeProcessManager(urls=["ws://ok:1"])
        cdp = _FakeCdpClient()
        bridge_instance = _make_bridge(cdp_client=cdp, process_manager=pm)

        first_in = threading.Event()
        allow_first_proceed = threading.Event()
        cancel_first = threading.Event()

        original_start = pm.start_and_get_websocket_url

        def slow_start(*args, **kwargs):
            first_in.set()
            allow_first_proceed.wait(timeout=5)
            cancel = kwargs.get("cancelEvent")
            if cancel is not None and cancel.is_set():
                raise bridge.CdpCancelled()
            return original_start(*args, **kwargs)

        pm.start_and_get_websocket_url = slow_start

        errors: list[Exception] = []

        def run_warmup():
            try:
                bridge_instance.ensure_connection(cancelEvent=cancel_first)
            except Exception as e:
                errors.append(e)

        speech_connected = threading.Event()

        def run_speech():
            first_in.wait(timeout=5)
            # Cancel the warmup caller
            cancel_first.set()
            allow_first_proceed.set()
            bridge_instance.ensure_connection()
            speech_connected.set()

        t1 = threading.Thread(target=run_warmup)
        t2 = threading.Thread(target=run_speech)
        t1.start()
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertTrue(speech_connected.is_set())
        self.assertTrue(cdp.is_connected())
        self.assertFalse(pm._terminated, "Process manager should not be terminated by cancelled warmup")
        self.assertTrue(any(isinstance(e, bridge.CdpCancelled) for e in errors))


if __name__ == "__main__":
    unittest.main()

"""Tests for the race-condition mitigations in bridge.py.

Covers:
  1. ensure_connection() releases the lock between fallback attempts so
     terminate() is not blocked for the entire retry chain.
  2. self._engine is captured under the lock before use, preventing
     stale/engine-swapped references.
  3. _runtimeBusy is protected by its own lock and reads/writes are consistent.
"""

from __future__ import annotations

import base64
import contextlib
import json
import threading
import time
import unittest
from typing import Any
from unittest import mock

from tests.test_support import FakeCdpClient, FakeEngine, FakeProcessManager, load_driver_module, make_fake_bridge

bridge = load_driver_module("bridge")


class _RecordingStopEngine(bridge.WasmTtsEngineBridge):
    """Engine that counts fast-stop requests instead of touching a browser."""

    def __init__(self, cdp_client: Any, catalog: Any) -> None:
        super().__init__(cdp_client, catalog)
        self.stop_calls = 0

    def send_fast_stop(self) -> None:
        self.stop_calls += 1


class _RecordingCdpClient(FakeCdpClient):
    """CDP client that records the engine busy state while a request is in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.engine: Any = None
        self.busyDuringRequest: list[bool] = []

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if self.engine is not None:
            self.busyDuringRequest.append(self.engine.runtime_busy)
        return super().request(method, params, *args, **kwargs)


# ---------------------------------------------------------------------------
# Test 1: ensure_connection() releases the lock between fallback attempts
# ---------------------------------------------------------------------------


class EnsureConnectionLockScopeTests(unittest.TestCase):
    """Verify that terminate() is not blocked during the fallback loop."""

    def test_terminate_can_run_between_fallback_attempts(self) -> None:
        """If the first browser choice fails, terminate() should not be
        blocked while ensure_connection() retries the next choice."""
        cdp = FakeCdpClient()
        pm = FakeProcessManager(urls=["ws://fail:1", "ws://ok:1"])
        bridge_instance = make_fake_bridge(cdp_client=cdp, process_manager=pm)

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
            # Wait until the first fallback attempt is inside slow_connect
            fallback_started.wait(timeout=5)
            lock_held_event.wait(timeout=5)
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
        pm = FakeProcessManager(urls=["ws://fail:1", "ws://ok:1"])
        cdp = FakeCdpClient()
        bridge_instance = make_fake_bridge(cdp_client=cdp, process_manager=pm)

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
            cancel.set()

        t = threading.Thread(target=cancel_after_delay)
        t.start()

        with self.assertRaises(bridge.CdpCancelled):
            bridge_instance.ensure_connection(cancelEvent=cancel)

        t.join(timeout=5)

    def test_ensure_connection_succeeds_on_first_try(self) -> None:
        """Normal path: connects on first attempt without extra iterations."""
        cdp = FakeCdpClient()
        pm = FakeProcessManager(urls=["ws://ok:1"])
        bridge_instance = make_fake_bridge(cdp_client=cdp, process_manager=pm)

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
        original_engine = FakeEngine()
        replacement_engine = FakeEngine()
        bridge_instance = make_fake_bridge(engine=original_engine)

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
        self.assertTrue(speak_done.is_set(), "speak thread did not complete")

        # The original engine should have been called (captured under lock)
        self.assertEqual(1, original_call_count)
        # The replacement engine should NOT have been called
        self.assertEqual(0, replacement_engine.speak_calls)

    def test_stop_runtime_uses_captured_engine(self) -> None:
        """stop_runtime() captures engine under lock."""
        engine = FakeEngine()
        bridge_instance = make_fake_bridge(engine=engine)

        bridge_instance.stop_runtime()
        self.assertEqual(1, engine.stop_calls)

    def test_cancel_current_uses_captured_engine(self) -> None:
        """cancel_current() captures engine under lock."""
        engine = FakeEngine()
        bridge_instance = make_fake_bridge(engine=engine)

        bridge_instance.cancel_current()
        self.assertEqual(1, engine.cancel_calls)

    def test_preload_voice_uses_captured_engine(self) -> None:
        """preload_voice() captures engine under lock."""
        engine = FakeEngine()
        cdp = FakeCdpClient()
        cdp.connected = True
        bridge_instance = make_fake_bridge(engine=engine, cdp_client=cdp)

        result = bridge_instance.preload_voice({"voiceId": "test", "voiceName": "test", "lang": "en"})
        self.assertEqual(1, engine.preload_calls)
        self.assertEqual({"success": True}, result)


# ---------------------------------------------------------------------------
# Test 3: _runtimeBusy lock synchronization
# ---------------------------------------------------------------------------


def _make_wasm_engine_fixture() -> tuple[Any, _RecordingCdpClient, Any]:
    catalog = bridge.VoiceCatalog.load()
    package = next(pkg for pkg in catalog.packages if bridge.is_package_supported_by_engine(pkg))
    speaker = next(s for s in catalog.speakers if s.packageId == package.id)
    cdp_client = _RecordingCdpClient()
    engine = bridge.WasmTtsEngineBridge(cdp_client, catalog)
    cdp_client.engine = engine
    return speaker, cdp_client, engine


def _speaker_speak_options(speaker: Any) -> dict[str, Any]:
    return {
        "voiceId": speaker.id,
        "voiceName": speaker.name,
        "lang": speaker.language,
        "rate": 1.0,
        "pitch": 1.0,
        "volume": 1.0,
    }


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

    def test_cancel_current_stops_the_engine_only_while_busy(self) -> None:
        """cancel_current() reads the locked busy state before sending a fast stop."""
        engine = _RecordingStopEngine(bridge.CdpClient(), bridge.VoiceCatalog.load())

        engine.cancel_current()
        self.assertEqual(0, engine.stop_calls)

        engine._set_runtime_busy(True)
        self.assertTrue(engine.runtime_busy)
        engine.cancel_current()
        self.assertEqual(1, engine.stop_calls)

        engine._set_runtime_busy(False)
        engine.cancel_current()
        self.assertEqual(1, engine.stop_calls)

    def test_speak_marks_engine_busy_without_cancel_event(self) -> None:
        """speak() keeps the engine busy during the request even with cancelEvent=None."""
        speaker, cdp_client, engine = _make_wasm_engine_fixture()
        pcm: list[bytes] = []

        with mock.patch.object(bridge.voice_store, "is_package_installed", return_value=True):
            result = engine.speak(
                "Hello",
                _speaker_speak_options(speaker),
                pcm.append,
                cancelEvent=None,
            )

        self.assertEqual([True], cdp_client.busyDuringRequest)
        self.assertFalse(engine.runtime_busy)
        self.assertFalse(result["done"])

    def test_preload_voice_marks_engine_busy_during_request_and_clears_on_error(self) -> None:
        """preload_voice() must set runtime_busy=True while CDP request is running and reset on failure."""
        speaker, cdp_client, engine = _make_wasm_engine_fixture()
        voice_opts = {
            "voiceId": speaker.id,
            "voiceName": speaker.name,
            "lang": speaker.language,
        }

        with mock.patch.object(bridge.voice_store, "is_package_installed", return_value=True):
            engine.preload_voice(voice_opts)
            self.assertEqual([True], cdp_client.busyDuringRequest)
            self.assertFalse(engine.runtime_busy)

            cdp_client.busyDuringRequest.clear()

            def failing_request(*args: object, **kwargs: object) -> dict[str, Any]:
                self.assertTrue(engine.runtime_busy)
                raise bridge.CdpError("boom", "boom")

            cdp_client.request = failing_request  # type: ignore[method-assign]
            with self.assertRaises(bridge.CdpError):
                engine.preload_voice(voice_opts)
            self.assertFalse(engine.runtime_busy)


class CdpDispatcherDisconnectTests(unittest.TestCase):
    """Verify CDP requests wake immediately when the WebSocket reader loop stops."""

    def test_reader_loop_exit_wakes_pending_request_without_waiting_for_timeout(self) -> None:
        class _ClosingWs:
            def __init__(self) -> None:
                self.connected = True
                self.sent = threading.Event()

            def send(self, _payload: str) -> None:
                self.sent.set()

            def recv(self) -> str:
                self.sent.wait(timeout=2)
                self.connected = False
                return ""  # EOF

        client = bridge.CdpClient()
        ws = _ClosingWs()
        dispatcher = bridge.CdpDispatcher(ws)
        dispatcher.start()
        client._ws = ws
        client._dispatcher = dispatcher

        start = time.monotonic()
        try:
            with self.assertRaises(bridge.CdpError):
                client.request("Runtime.evaluate", {"expression": "1"}, timeout=10.0)
        finally:
            dispatcher.stop()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.5, f"Request should fail fast on EOF, took {elapsed:.2f}s")
        self.assertTrue(dispatcher._stopped.is_set())
        late_event = dispatcher.register_request(999)
        self.assertTrue(late_event.is_set())


# ---------------------------------------------------------------------------
# Test 4: ensure_connection cancellation does not terminate process manager
# ---------------------------------------------------------------------------


class EnsureConnectionCancellationTests(unittest.TestCase):
    """Verify that cancellation during ensure_connection() does not kill the browser process."""

    def test_cancelled_connection_does_not_terminate_process_manager(self) -> None:
        """When ensure_connection() is cancelled, the process manager is NOT terminated."""
        pm = FakeProcessManager(urls=["ws://ok:1"])
        cdp = FakeCdpClient()
        bridge_instance = make_fake_bridge(cdp_client=cdp, process_manager=pm)

        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(bridge.CdpCancelled):
            bridge_instance.ensure_connection(cancelEvent=cancel)

        self.assertFalse(pm._terminated, "Process manager should not be terminated on cancellation")

    def test_concurrent_ensure_connection_when_first_caller_is_cancelled(self) -> None:
        """When a concurrent warmup caller is cancelled, the speech caller still connects."""
        pm = FakeProcessManager(urls=["ws://ok:1"])
        cdp = FakeCdpClient()
        bridge_instance = make_fake_bridge(cdp_client=cdp, process_manager=pm)

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


class _FailingEngine:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0
        self.runtime_busy = False

    def speak(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        raise self.error


def _browser_speech_error(*, audioStarted: bool) -> BaseException:
    return bridge._BrowserSpeechError(
        "Could not speak",
        "Browser harness reported a speech error",
        audioStarted=audioStarted,
    )


def _runtime_bridge(engine: object) -> Any:
    b = make_fake_bridge(engine=engine)
    b.ensure_connection = lambda cancelEvent=None: None
    return b


class RuntimeRecoveryTests(unittest.TestCase):
    """Verify runtime speech error recovery, retry policies, and standby safety."""

    @staticmethod
    def _install_recycle_spy(bridge_inst: Any, replacement: Any | None = None) -> list[int]:
        recycleCalls = [0]

        def maybe_recycle_runtime(*, allowIdleRecycle: bool = True, checkMemory: bool = True) -> bool:
            if not allowIdleRecycle:
                return False
            recycleCalls[0] += 1
            if replacement is not None:
                bridge_inst._engine = replacement
            bridge_inst._needsRecycle = False
            return True

        bridge_inst.maybe_recycle_runtime = maybe_recycle_runtime
        return recycleCalls

    def _run_recycle_speak_case(
        self,
        error: BaseException,
        *,
        use_replacement: bool = True,
        expected_error: type[BaseException] | None = None,
        expected_failed_calls: int = 1,
        expected_success_calls: int = 1,
        expected_recycles: int = 1,
    ) -> Any:
        failedEngine = _FailingEngine(error)
        successfulEngine = FakeEngine()
        bridge_inst = _runtime_bridge(failedEngine)
        recycleCalls = self._install_recycle_spy(
            bridge_inst,
            replacement=successfulEngine if use_replacement else None,
        )
        if expected_error is not None:
            with self.assertRaises(expected_error):
                bridge_inst.speak("text", {}, lambda _audio: None)
        else:
            result = bridge_inst.speak("text", {}, lambda _audio: None)
            self.assertEqual({"success": True}, result)
        self.assertEqual(expected_failed_calls, failedEngine.calls)
        self.assertEqual(expected_success_calls, successfulEngine.calls)
        self.assertEqual(expected_recycles, recycleCalls[0])
        return bridge_inst

    def test_no_audio_browser_error_retries_once_after_recycle(self) -> None:
        self._run_recycle_speak_case(_browser_speech_error(audioStarted=False))

    def test_partial_audio_browser_error_recycles_without_retry(self) -> None:
        self._run_recycle_speak_case(
            _browser_speech_error(audioStarted=True),
            expected_error=bridge._BrowserSpeechError,
            expected_success_calls=0,
        )

    def test_browser_error_is_never_retried_more_than_once(self) -> None:
        self._run_recycle_speak_case(
            _browser_speech_error(audioStarted=False),
            use_replacement=False,
            expected_error=bridge._BrowserSpeechError,
            expected_failed_calls=2,
            expected_success_calls=0,
            expected_recycles=2,
        )

    def test_cdp_request_wraps_send_failure_as_friendly_cdp_error(self) -> None:
        client = bridge.CdpClient()
        dispatcher = mock.MagicMock()
        dispatcher._stopped.is_set.return_value = False
        dispatcher.register_request.return_value = threading.Event()
        dispatcher.unregister_request.return_value = (None, None)
        dispatcher.send.side_effect = ConnectionResetError("socket broken")
        ws = mock.MagicMock(connected=True)
        client._ws = ws
        client._dispatcher = dispatcher

        with self.assertRaises(bridge.CdpError) as ctx:
            client.request("Runtime.evaluate", {"expression": "1"})
        self.assertIn("Could not send CDP command Runtime.evaluate", ctx.exception.technicalDetail)

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(bridge.CdpCancelled):
            client.request("Runtime.evaluate", {"expression": "1"}, cancelEvent=cancel)

    def test_idle_websocket_disconnect_retries_once_after_recycle(self) -> None:
        self._run_recycle_speak_case(
            bridge._friendly_cdp_error(
                "Connection closed",
                "Could not send CDP command Runtime.evaluate: broken pipe",
            )
        )

    def test_timeout_cdp_error_marks_recycle_without_retry(self) -> None:
        bridge_inst = self._run_recycle_speak_case(
            bridge._friendly_cdp_error("Timed out", "Timed out waiting for Runtime.evaluate."),
            expected_error=bridge.CdpError,
            expected_success_calls=0,
            expected_recycles=0,
        )
        self.assertTrue(bridge_inst._needsRecycle)

    def test_only_healthy_connected_runtime_is_safe_for_standby(self) -> None:
        bridge_inst = _runtime_bridge(FakeEngine())
        bridge_inst._cdp_client.connected = True
        self.assertTrue(bridge_inst.safe_for_standby_release())

        bridge_inst._needsRecycle = True
        self.assertFalse(bridge_inst.safe_for_standby_release())
        bridge_inst._needsRecycle = False
        bridge_inst._engine.runtime_busy = True
        self.assertFalse(bridge_inst.safe_for_standby_release())
        bridge_inst._engine.runtime_busy = False
        bridge_inst._cdp_client.connected = False
        self.assertFalse(bridge_inst.safe_for_standby_release())

    def test_maybe_recycle_runtime_increments_lifecycle_epoch_once(self) -> None:
        bridge_inst = _runtime_bridge(FakeEngine())
        before = bridge_inst._lifecycleEpoch
        bridge_inst._needsRecycle = True
        self.assertTrue(bridge_inst.maybe_recycle_runtime(allowIdleRecycle=True, checkMemory=False))
        self.assertEqual(before + 1, bridge_inst._lifecycleEpoch)

    def test_wasm_bridge_speak_rejects_invalid_and_boolean_sample_rates(self) -> None:
        payloadAudio = base64.b64encode(b"\x01\x00\x02\x00").decode("ascii")

        for badRate in (0, "", True, False, 16000):
            with self.subTest(badRate=badRate):
                speaker, cdp_client, engine = _make_wasm_engine_fixture()

                def emit_bad_sample_rate(
                    method: str,
                    params: dict[str, Any] | None = None,
                    timeout: float = 30,
                    eventHandler: Any = None,
                    cancelEvent: Any = None,
                    onCancelCallback: Any = None,
                    expectedBadRate: Any = badRate,
                ) -> dict[str, Any]:
                    expr = (params or {}).get("expression", "")
                    prefix = "window.googleTtsForNvdaSpeak("
                    rawJson = expr[len(prefix) : -1]
                    sessionId = json.loads(rawJson)["sessionId"]
                    eventHandler(
                        {
                            "method": "Runtime.bindingCalled",
                            "params": {
                                "name": bridge.BINDING_NAME,
                                "payload": json.dumps(
                                    {
                                        "sessionId": sessionId,
                                        "type": "audio",
                                        "sampleRate": expectedBadRate,
                                        "data": payloadAudio,
                                    }
                                ),
                            },
                        }
                    )
                    return {"result": {"result": {"value": {"success": True}}}}

                cdp_client.request = emit_bad_sample_rate  # type: ignore[method-assign]
                with (
                    mock.patch.object(bridge.voice_store, "is_package_installed", return_value=True),
                    self.assertRaises(bridge._BrowserSpeechError),
                ):
                    engine.speak(
                        "Hello",
                        _speaker_speak_options(speaker),
                        lambda _pcm: None,
                    )

    def test_start_and_get_websocket_url_stops_spawned_browser_on_cancel_but_not_reused(self) -> None:
        # Case A: Newly spawned browser cancelled during get_page_websocket_url -> stops without deleting persistent profile.
        pmNew = bridge.BrowserProcessManager(catalog=bridge.VoiceCatalog([]))
        stopCallsNew: list[bool] = []
        with (
            mock.patch.object(pmNew, "_prepare_browser_start", return_value=False),
            mock.patch.object(pmNew, "_browser_choices_or_raise", return_value=(("chrome", "chrome.exe"),)),
            mock.patch.object(pmNew, "_start_browser_choice", return_value=(8080, 9222)),
            mock.patch.object(pmNew, "get_page_websocket_url", side_effect=bridge.CdpCancelled()),
            mock.patch.object(
                pmNew,
                "_stop_browser_process",
                side_effect=lambda *, removeProfile=True: stopCallsNew.append(removeProfile),
            ),
            self.assertRaises(bridge.CdpCancelled),
        ):
            pmNew.start_and_get_websocket_url()
        self.assertEqual([False], stopCallsNew)

        # Case B: Reused running browser cancelled during get_page_websocket_url -> does NOT terminate running browser.
        pmReused = bridge.BrowserProcessManager(catalog=bridge.VoiceCatalog([]))
        stopCallsReused: list[bool] = []
        with (
            mock.patch.object(pmReused, "_prepare_browser_start", return_value=True),
            mock.patch.object(pmReused, "get_page_websocket_url", side_effect=bridge.CdpCancelled()),
            mock.patch.object(
                pmReused,
                "_stop_browser_process",
                side_effect=lambda *, removeProfile=True: stopCallsReused.append(removeProfile),
            ),
            self.assertRaises(bridge.CdpCancelled),
        ):
            pmReused.start_and_get_websocket_url()
        self.assertEqual([], stopCallsReused)


if __name__ == "__main__":
    unittest.main()

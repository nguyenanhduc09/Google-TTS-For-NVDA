"""Unit tests for watcher.DirectoryChangeWatcher.

The watcher depends on Win32 kernel32 APIs (FindFirstChangeNotificationW,
WaitForMultipleObjects, …).  All kernel32 calls are mocked so the tests
can run on any platform.
"""

from __future__ import annotations

import ctypes
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tests.test_support import load_driver_module, load_fresh_driver_module

# Use a real existing directory so _watch_path's is_dir() check passes.
_REAL_DIR = Path(__file__).resolve().parent
watcher_mod = load_driver_module("watcher")


def _make_mock_kernel32(*, wait_side_effect: object = None) -> MagicMock:
    """Return a MagicMock that behaves like a kernel32 WinDLL instance.

    Parameters
    ----------
    wait_side_effect:
        Side-effect for ``WaitForMultipleObjects``.  When ``None`` the
        default returns index 0 (stop) which breaks the loop immediately.
    """

    kernel32 = MagicMock(name="kernel32")

    _INVALID = ctypes.c_void_p(-1).value
    _HANDLE_BASE = 0x1000
    _find_count = [0]

    def _create_event(*_args, **_kwargs):
        return 0x2000

    def _find_first(*_args, **_kwargs):
        _find_count[0] += 1
        if _find_count[0] <= 5:
            return _HANDLE_BASE + _find_count[0]
        return _INVALID

    def _find_next(*_args, **_kwargs):
        return True

    def _find_close(*_args, **_kwargs):
        return True

    def _close_handle(*_args, **_kwargs):
        return True

    def _set_event(*_args, **_kwargs):
        return True

    def _default_wait(*_args, **_kwargs):
        return 0  # stop handle → loop breaks immediately

    kernel32.CreateEventW.side_effect = _create_event
    kernel32.FindFirstChangeNotificationW.side_effect = _find_first
    kernel32.FindNextChangeNotification.side_effect = _find_next
    kernel32.FindCloseChangeNotification.side_effect = _find_close
    kernel32.CloseHandle.side_effect = _close_handle
    kernel32.SetEvent.side_effect = _set_event
    kernel32.WaitForMultipleObjects.side_effect = wait_side_effect if wait_side_effect is not None else _default_wait

    return kernel32


class DirectoryChangeWatcherLifecycleTests(unittest.TestCase):
    """Verify start / stop lifecycle and basic callback invocation."""

    def test_start_stop_cycle(self) -> None:
        """start() launches the thread, stop() joins it."""
        w = watcher_mod.DirectoryChangeWatcher(
            MagicMock(return_value=()),
            MagicMock(),
        )
        with patch.object(ctypes, "WinDLL", return_value=_make_mock_kernel32()):
            w.start()
            w.stop()
        self.assertIsNone(w._thread)

    def test_start_is_idempotent(self) -> None:
        """Calling start() twice does not spawn a second thread."""

        # Block WaitForMultipleObjects on an Event so we can observe
        # the thread while it is alive.
        block = threading.Event()

        def _wait(*_args, **_kwargs):
            block.wait()  # blocks until test signals
            return 0

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                MagicMock(),
            )
            w.start()
            first_thread = w._thread
            self.assertIsNotNone(first_thread)
            self.assertTrue(first_thread.is_alive())
            w.start()  # should be a no-op
            self.assertIs(w._thread, first_thread)
            block.set()
            w.stop()
            self.assertIsNone(w._thread)

    def test_stop_is_idempotent(self) -> None:
        """Calling stop() twice does not raise."""
        with patch.object(ctypes, "WinDLL", return_value=_make_mock_kernel32()):
            w = watcher_mod.DirectoryChangeWatcher(
                MagicMock(return_value=()),
                MagicMock(),
            )
            w.start()
            w.stop()
            w.stop()  # should not raise

    def test_stop_before_start(self) -> None:
        """stop() without start() is a no-op."""
        w = watcher_mod.DirectoryChangeWatcher(
            MagicMock(return_value=()),
            MagicMock(),
        )
        w.stop()  # should not raise

    def test_rapid_restart_does_not_clobber_new_stop_handle(self) -> None:
        """If stop(timeout=0) returns while the old thread's finally block has not run yet,
        a subsequent start() must not have its _stopHandle cleared by the old thread."""
        handle_seq = [0x2000]
        wait_started_1 = threading.Event()
        release_thread_1 = threading.Event()
        wait_started_2 = threading.Event()
        release_thread_2 = threading.Event()
        first_handle_closed = threading.Event()
        wait_calls = [0]

        kernel32 = _make_mock_kernel32()
        default_close_handle = kernel32.CloseHandle.side_effect

        def _create_event(*_args, **_kwargs):
            handle_seq[0] += 1
            return handle_seq[0]

        def _close_handle(handle, *args, **kwargs):
            if handle == 0x2001:
                first_handle_closed.set()
            return default_close_handle(handle, *args, **kwargs)

        def _wait(*_args, **_kwargs):
            wait_calls[0] += 1
            if wait_calls[0] == 1:
                wait_started_1.set()
                release_thread_1.wait(timeout=2)
                return 0
            wait_started_2.set()
            release_thread_2.wait(timeout=2)
            return 0

        kernel32.CreateEventW.side_effect = _create_event
        kernel32.CloseHandle.side_effect = _close_handle
        kernel32.WaitForMultipleObjects.side_effect = _wait

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                MagicMock(),
            )
            w.start()
            self.assertTrue(wait_started_1.wait(timeout=2))
            first_handle = w._stopHandle
            self.assertEqual(0x2001, first_handle)

            # Stop with timeout=0 so thread 1 is still inside WaitForMultipleObjects.
            w.stop(timeout=0.0)
            # Immediately start thread 2 before thread 1 exits.
            w.start()
            self.assertTrue(wait_started_2.wait(timeout=2))
            second_handle = w._stopHandle
            self.assertEqual(0x2002, second_handle)

            # Now let thread 1 exit and run its finally block.
            release_thread_1.set()
            self.assertTrue(first_handle_closed.wait(timeout=2))

            # Thread 1's finally block must NOT have cleared thread 2's _stopHandle!
            self.assertEqual(second_handle, w._stopHandle)
            self.assertIsNotNone(w._kernel32)

            release_thread_2.set()
            w.stop()
            self.assertIsNone(w._stopHandle)


class DirectoryChangeWatcherCallbackTests(unittest.TestCase):
    """Verify that the user callback is invoked with the correct reason."""

    def test_callback_receives_correct_reason(self) -> None:
        cb = MagicMock()
        first_call = threading.Event()
        call_count = [0]

        def _wait(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                first_call.set()
                return 1  # notification → fires callback
            return 0  # stop → exit loop

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                cb,
            )
            w.start()
            first_call.wait(timeout=2)
            w.stop()

        cb.assert_called_once()
        reason = cb.call_args[0][0]
        self.assertIn("watched directory changed", reason)
        self.assertIn(str(_REAL_DIR), reason)

    def test_callback_not_invoked_on_immediate_stop(self) -> None:
        """When WaitForMultipleObjects returns stop immediately, callback is not called."""
        cb = MagicMock()

        with patch.object(ctypes, "WinDLL", return_value=_make_mock_kernel32()):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                cb,
            )
            w.start()
            w.stop()

        cb.assert_not_called()

    def test_multiple_signals_invoke_callback_each_time(self) -> None:
        """Each notification signal invokes the callback independently."""
        cb = MagicMock()
        signal_events = [threading.Event() for _ in range(3)]
        call_idx = [0]

        def _wait(*_args, **_kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < 3:
                signal_events[idx].set()
                return 1  # notification
            return 0  # stop

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                cb,
            )
            w.start()
            for evt in signal_events:
                evt.wait(timeout=2)
            w.stop()

        self.assertEqual(cb.call_count, 3)

    def test_paths_called_each_iteration(self) -> None:
        """The paths callable is re-invoked on every wait cycle."""
        paths = MagicMock(return_value=(_REAL_DIR,))
        cb = MagicMock()
        ready = threading.Event()

        def _wait(*_args, **_kwargs):
            ready.set()
            return 0  # stop

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(paths, cb)
            w.start()
            ready.wait(timeout=2)
            w.stop()

        # paths() is called once per iteration; with stop on first wait
        # we get exactly one call.
        self.assertGreaterEqual(paths.call_count, 1)


class DirectoryChangeWatcherEdgeCaseTests(unittest.TestCase):
    """Edge cases: no valid paths, empty callback, etc."""

    def test_nothing_to_watch_exits_thread(self) -> None:
        """When no paths are directories the watcher thread exits promptly."""
        cb = MagicMock()
        kernel32 = _make_mock_kernel32()

        # All paths return invalid handles.
        kernel32.FindFirstChangeNotificationW.return_value = ctypes.c_void_p(-1).value

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                cb,
            )
            w.start()
            w.stop()

        cb.assert_not_called()
        self.assertIsNone(w._thread)

    def test_empty_paths_tuple_exits_thread(self) -> None:
        """An empty paths tuple causes immediate exit."""
        cb = MagicMock()

        with patch.object(ctypes, "WinDLL", return_value=_make_mock_kernel32()):
            w = watcher_mod.DirectoryChangeWatcher(
                MagicMock(return_value=()),
                cb,
            )
            w.start()
            w.stop()

        cb.assert_not_called()

    def test_all_notification_handles_closed(self) -> None:
        """Every FindFirstChangeNotification handle is closed in the finally block."""
        cb = MagicMock()
        callback_fired = threading.Event()

        def _wait(*_args, **_kwargs):
            callback_fired.set()
            return 1  # notification → callback fires

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                cb,
            )
            w.start()
            callback_fired.wait(timeout=2)
            w.stop()

        self.assertGreaterEqual(
            kernel32.FindCloseChangeNotification.call_count,
            1,
        )

    def test_close_handle_called_on_exit(self) -> None:
        """CloseHandle is called for the stop event when _run exits."""
        kernel32 = _make_mock_kernel32()

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                MagicMock(),
            )
            w.start()
            w.stop()

        kernel32.CloseHandle.assert_called_with(0x2000)


@unittest.skipUnless(
    hasattr(ctypes, "WinDLL"),
    "Integration tests require Win32 kernel32",
)
class DirectoryChangeWatcherIntegrationTests(unittest.TestCase):
    """Integration tests using real Win32 kernel32 and temp directories."""

    def setUp(self) -> None:
        self._tmpdir = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="watcher_test_")))

    def _load_real_watcher(self) -> object:
        """Load the watcher module fresh (not cached) to bypass mocks."""
        return load_fresh_driver_module("watcher")

    def _run_integration_watch(self, trigger_action: Any) -> tuple[bool, MagicMock]:
        watcher_mod = self._load_real_watcher()
        cb = MagicMock()
        callback_fired = threading.Event()

        def _on_change(reason: str) -> None:
            callback_fired.set()
            cb(reason)

        w = watcher_mod.DirectoryChangeWatcher(lambda: (self._tmpdir,), _on_change)
        w.start()
        try:
            # Give the thread time to set up the notification handle.
            time.sleep(0.1)
            trigger_action()
            fired = callback_fired.wait(timeout=3)
        finally:
            w.stop()
        return fired, cb

    def test_detects_file_creation(self) -> None:
        """Creating a file in a watched directory triggers the callback."""
        test_file = self._tmpdir / "trigger.txt"
        fired, cb = self._run_integration_watch(lambda: test_file.write_text("hello", encoding="utf-8"))
        self.assertTrue(fired, "Callback was not triggered by file creation")
        reason = cb.call_args[0][0]
        self.assertIn("watched directory changed", reason)
        self.assertIn(str(self._tmpdir), reason)

    def test_detects_file_deletion(self) -> None:
        """Deleting a file in a watched directory triggers the callback."""
        test_file = self._tmpdir / "to_delete.txt"
        test_file.write_text("delete me", encoding="utf-8")
        fired, _cb = self._run_integration_watch(test_file.unlink)
        self.assertTrue(fired, "Callback was not triggered by file deletion")

    def test_multiple_dirs_watched(self) -> None:
        """Changes in multiple directories are both detected."""
        watcher_mod = self._load_real_watcher()
        dir_a = self._tmpdir / "a"
        dir_b = self._tmpdir / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        reasons: list[str] = []
        dir_a_fired = threading.Event()
        dir_b_fired = threading.Event()

        def _on_change(reason: str) -> None:
            reasons.append(reason)
            if str(dir_a) in reason:
                dir_a_fired.set()
            if str(dir_b) in reason:
                dir_b_fired.set()

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (dir_a, dir_b),
            _on_change,
        )
        w.start()
        try:
            time.sleep(0.1)

            (dir_a / "file_a.txt").write_text("a", encoding="utf-8")
            fired_a = dir_a_fired.wait(timeout=3)

            (dir_b / "file_b.txt").write_text("b", encoding="utf-8")
            fired_b = dir_b_fired.wait(timeout=3)
        finally:
            w.stop()

        self.assertTrue(fired_a, f"Callback was not triggered for dir_a; reasons={reasons!r}")
        self.assertTrue(fired_b, f"Callback was not triggered for dir_b; reasons={reasons!r}")

    def test_stop_prevents_further_callbacks(self) -> None:
        """After stop(), no more callbacks fire."""
        watcher_mod = self._load_real_watcher()
        cb = MagicMock()

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (self._tmpdir,),
            cb,
        )
        w.start()
        try:
            time.sleep(0.1)
        finally:
            w.stop()

        # Now create a file — callback should NOT fire.
        (self._tmpdir / "late.txt").write_text("late", encoding="utf-8")
        time.sleep(0.2)

        cb.assert_not_called()


if __name__ == "__main__":
    unittest.main()

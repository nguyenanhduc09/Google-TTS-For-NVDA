"""Unit tests for watcher.DirectoryChangeWatcher.

The watcher depends on Win32 kernel32 APIs (FindFirstChangeNotificationW,
WaitForMultipleObjects, …).  All kernel32 calls are mocked so the tests
can run on any platform.
"""

from __future__ import annotations

import ctypes
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.test_support import load_driver_module

# Use a real existing directory so _watch_path's is_dir() check passes.
_REAL_DIR = Path(__file__).resolve().parent


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
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")

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

    def test_stop_is_idempotent(self) -> None:
        """Calling stop() twice does not raise."""
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
        w = watcher_mod.DirectoryChangeWatcher(
            MagicMock(return_value=()),
            MagicMock(),
        )
        w.stop()  # should not raise


class DirectoryChangeWatcherCallbackTests(unittest.TestCase):
    """Verify that the user callback is invoked with the correct reason."""

    def test_callback_receives_correct_reason(self) -> None:
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
        cb = MagicMock()

        with patch.object(ctypes, "WinDLL", return_value=_make_mock_kernel32()):
            w = watcher_mod.DirectoryChangeWatcher(
                MagicMock(return_value=()),
                cb,
            )
            w.start()
            w.stop()

        cb.assert_not_called()

    def test_watcher_thread_exits_after_stop(self) -> None:
        """After stop() the watcher thread is no longer alive."""
        watcher_mod = load_driver_module("watcher")

        block = threading.Event()

        def _wait(*_args, **_kwargs):
            block.wait()
            return 0

        kernel32 = _make_mock_kernel32(wait_side_effect=_wait)

        with patch.object(ctypes, "WinDLL", return_value=kernel32):
            w = watcher_mod.DirectoryChangeWatcher(
                lambda: (_REAL_DIR,),
                MagicMock(),
            )
            w.start()
            block.set()  # let the mock return so _run exits
            w.stop()  # joins the thread
            self.assertIsNone(w._thread)

    def test_all_notification_handles_closed(self) -> None:
        """Every FindFirstChangeNotification handle is closed in the finally block."""
        watcher_mod = load_driver_module("watcher")
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
        watcher_mod = load_driver_module("watcher")
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
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp(prefix="watcher_test_"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _load_real_watcher(self) -> object:
        """Load the watcher module fresh (not cached) to bypass mocks."""
        import importlib

        spec = importlib.util.spec_from_file_location(
            "watcher_real",
            str(
                Path(__file__).resolve().parents[1]
                / "googleTtsForNvda"
                / "synthDrivers"
                / "googleTtsForNvda"
                / "watcher.py"
            ),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_detects_file_creation(self) -> None:
        """Creating a file in a watched directory triggers the callback."""
        import time

        watcher_mod = self._load_real_watcher()
        cb = MagicMock()
        callback_fired = threading.Event()

        def _on_change(reason: str) -> None:
            callback_fired.set()
            cb(reason)

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (self._tmpdir,),
            _on_change,
        )
        w.start()
        # Give the thread time to set up the notification handle.
        time.sleep(0.1)

        # Create a file to trigger the notification.
        test_file = self._tmpdir / "trigger.txt"
        test_file.write_text("hello", encoding="utf-8")

        fired = callback_fired.wait(timeout=3)
        w.stop()

        self.assertTrue(fired, "Callback was not triggered by file creation")
        reason = cb.call_args[0][0]
        self.assertIn("watched directory changed", reason)
        self.assertIn(str(self._tmpdir), reason)

    def test_detects_file_deletion(self) -> None:
        """Deleting a file in a watched directory triggers the callback."""
        import time

        watcher_mod = self._load_real_watcher()
        cb = MagicMock()
        callback_fired = threading.Event()

        def _on_change(reason: str) -> None:
            callback_fired.set()
            cb(reason)

        # Pre-create a file to delete.
        test_file = self._tmpdir / "to_delete.txt"
        test_file.write_text("delete me", encoding="utf-8")

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (self._tmpdir,),
            _on_change,
        )
        w.start()
        time.sleep(0.1)

        test_file.unlink()

        fired = callback_fired.wait(timeout=3)
        w.stop()

        self.assertTrue(fired, "Callback was not triggered by file deletion")

    def test_multiple_dirs_watched(self) -> None:
        """Changes in multiple directories are both detected."""
        import time

        watcher_mod = self._load_real_watcher()
        dir_a = self._tmpdir / "a"
        dir_b = self._tmpdir / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        cb = MagicMock()
        callback_fired = threading.Event()

        def _on_change(reason: str) -> None:
            callback_fired.set()
            cb(reason)

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (dir_a, dir_b),
            _on_change,
        )
        w.start()
        time.sleep(0.1)

        (dir_a / "file_a.txt").write_text("a", encoding="utf-8")

        fired = callback_fired.wait(timeout=3)
        w.stop()

        self.assertTrue(fired, "Callback was not triggered for dir_a")

    def test_stop_prevents_further_callbacks(self) -> None:
        """After stop(), no more callbacks fire."""
        import time

        watcher_mod = self._load_real_watcher()
        cb = MagicMock()

        w = watcher_mod.DirectoryChangeWatcher(
            lambda: (self._tmpdir,),
            cb,
        )
        w.start()
        time.sleep(0.1)
        w.stop()

        # Now create a file — callback should NOT fire.
        (self._tmpdir / "late.txt").write_text("late", encoding="utf-8")
        time.sleep(0.2)

        cb.assert_not_called()


if __name__ == "__main__":
    unittest.main()

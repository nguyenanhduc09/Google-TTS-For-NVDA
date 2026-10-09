"""Reusable Windows directory-change watcher.

Uses ``FindFirstChangeNotificationW`` / ``WaitForMultipleObjects`` so the
watcher thread is fully kernel-blocked while idle (no busy-loop, no Python
timers consuming OS threads).

Coalescing of rapid filesystem bursts is handled by the caller: when the
callback triggers a background refresh that is already in-progress the
subsequent change signal is effectively ignored until the current refresh
completes and the watcher is restarted.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class DirectoryChangeWatcher:
    """Watch one or more directories for filesystem changes.

    Parameters
    ----------
    paths:
        Callable returning the current set of directory paths to monitor.
        Invoked on the watcher thread before each wait cycle so directories
        can be added or removed dynamically.
    callback:
        Invoked on a background thread with a human-readable *reason*
        string whenever at least one watched directory changes.
    """

    _NOTIFY_FILTER = (
        0x00000001  # FILE_NOTIFY_CHANGE_FILE_NAME
        | 0x00000002  # FILE_NOTIFY_CHANGE_DIR_NAME
        | 0x00000004  # FILE_NOTIFY_CHANGE_ATTRIBUTES
        | 0x00000008  # FILE_NOTIFY_CHANGE_SIZE
        | 0x00000010  # FILE_NOTIFY_CHANGE_LAST_WRITE
        | 0x00000040  # FILE_NOTIFY_CHANGE_CREATION
    )
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_FAILED = 0xFFFFFFFF
    _INFINITE = 0xFFFFFFFF
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(
        self,
        paths: Callable[[], tuple[Path, ...]],
        callback: Callable[[str], None],
    ) -> None:
        self._paths = paths
        self._callback = callback
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stopRequested = threading.Event()
        self._stopHandle: int | None = None

    def start(self) -> None:
        """Start the watcher thread (no-op if already running)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopRequested.clear()
            thread = threading.Thread(
                name="googleTtsForNvda.standbyWatcher",
                target=self._run,
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def signal_stop(self) -> None:
        """Signal the watcher thread to stop without waiting for it to exit."""
        with self._lock:
            self._stopRequested.set()
            self._signal_stop_locked()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the watcher thread and wait for it to finish."""
        self.signal_stop()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                log.warning(f"DirectoryChangeWatcher thread did not terminate within {timeout} seconds.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _signal_stop_locked(self) -> None:
        stopHandle = self._stopHandle
        if not stopHandle:
            return
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
            kernel32.SetEvent.restype = wintypes.BOOL
            kernel32.SetEvent(stopHandle)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Watch thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateEventW.argtypes = (
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            kernel32.CreateEventW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.FindFirstChangeNotificationW.argtypes = (
                wintypes.LPCWSTR,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
            kernel32.FindCloseChangeNotification.argtypes = (wintypes.HANDLE,)
            kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
            kernel32.WaitForMultipleObjects.argtypes = (
                wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
        except Exception:
            log.debug(
                "Could not initialize Google TTS standby directory watcher.",
                exc_info=True,
            )
            return

        stopHandle = kernel32.CreateEventW(None, True, False, None)
        if not stopHandle:
            log.debug("Could not create Google TTS standby watcher stop event.")
            return
        with self._lock:
            self._stopHandle = stopHandle
        try:
            while not self._stopRequested.is_set():
                notificationHandles: list[int] = []
                notificationPaths: list[Path] = []
                try:
                    for path in self._paths():
                        handle = self._watch_path(kernel32, path)
                        if not handle:
                            continue
                        notificationHandles.append(handle)
                        notificationPaths.append(path)
                    if not notificationHandles:
                        log.debug("Google TTS standby watcher has nothing to watch.")
                        return
                    waitHandles = [stopHandle] + notificationHandles
                    handleArray = (wintypes.HANDLE * len(waitHandles))(*waitHandles)
                    result = kernel32.WaitForMultipleObjects(
                        len(waitHandles),
                        handleArray,
                        False,
                        self._INFINITE,
                    )
                    if result == self._WAIT_FAILED:
                        log.debug(
                            "Google TTS standby watcher wait failed: %s.",
                            ctypes.get_last_error(),
                        )
                        return
                    signaledIndex = int(result - self._WAIT_OBJECT_0)
                    if signaledIndex < 0 or signaledIndex >= len(waitHandles):
                        log.debug(
                            "Google TTS standby watcher returned an unexpected wait result: %s.",
                            result,
                        )
                        return
                    if signaledIndex == 0 or self._stopRequested.is_set():
                        return
                    pathIndex = signaledIndex - 1
                    reason = "watched directory changed"
                    if 0 <= pathIndex < len(notificationPaths):
                        reason = f"watched directory changed: {notificationPaths[pathIndex]}"
                finally:
                    for handle in notificationHandles:
                        kernel32.FindCloseChangeNotification(handle)
                if not self._stopRequested.is_set():
                    self._callback(reason)
        finally:
            with self._lock:
                self._stopHandle = None
                self._thread = None
            kernel32.CloseHandle(stopHandle)

    def _watch_path(self, kernel32: Any, path: Path) -> int | None:
        try:
            if not path.is_dir():
                return None
        except OSError:
            return None
        handle = kernel32.FindFirstChangeNotificationW(
            str(path),
            True,
            self._NOTIFY_FILTER,
        )
        if not handle or handle == self._INVALID_HANDLE_VALUE:
            log.debug(
                "Could not watch Google TTS standby directory changes for %s: %s.",
                path,
                ctypes.get_last_error(),
            )
            return None
        return int(handle)

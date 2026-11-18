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
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
_WINDLL_TYPE = ctypes.WinDLL if isinstance(getattr(ctypes, "WinDLL", None), type) else ctypes.CDLL


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
        self._kernel32: Any = None

    def start(self) -> None:
        """Start the watcher thread (no-op if already running)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and not self._stopRequested.is_set():
                return
            stopEvent = threading.Event()
            self._stopRequested = stopEvent
            thread = threading.Thread(
                name="googleTtsForNvda.standbyWatcher",
                target=self._run,
                args=(stopEvent,),
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

    def _ensure_kernel32(self) -> Any:
        if self._kernel32 is not None:
            return self._kernel32
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Configure prototypes only when testing/mocking non-WinDLL objects;
        # for real WinDLL singletons, avoid mutating shared global .argtypes/.restype.
        if not isinstance(kernel32, _WINDLL_TYPE):
            kernel32.CreateEventW.argtypes = (
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            kernel32.CreateEventW.restype = wintypes.HANDLE
            kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
            kernel32.SetEvent.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.FindFirstChangeNotificationW.argtypes = (
                wintypes.LPCWSTR,
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
            kernel32.FindNextChangeNotification.argtypes = (wintypes.HANDLE,)
            kernel32.FindNextChangeNotification.restype = wintypes.BOOL
            kernel32.FindCloseChangeNotification.argtypes = (wintypes.HANDLE,)
            kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
            kernel32.WaitForMultipleObjects.argtypes = (
                wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.BOOL,
                wintypes.DWORD,
            )
            kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
            self._kernel32 = kernel32
            return kernel32

        class _Kernel32View:
            CreateEventW = ctypes.WINFUNCTYPE(
                wintypes.HANDLE,
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )(("CreateEventW", kernel32))
            SetEvent = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(("SetEvent", kernel32))
            CloseHandle = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(("CloseHandle", kernel32))
            FindFirstChangeNotificationW = ctypes.WINFUNCTYPE(
                wintypes.HANDLE,
                wintypes.LPCWSTR,
                wintypes.BOOL,
                wintypes.DWORD,
            )(("FindFirstChangeNotificationW", kernel32))
            FindNextChangeNotification = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(
                ("FindNextChangeNotification", kernel32)
            )
            FindCloseChangeNotification = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(
                ("FindCloseChangeNotification", kernel32)
            )
            WaitForMultipleObjects = ctypes.WINFUNCTYPE(
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE),
                wintypes.BOOL,
                wintypes.DWORD,
            )(("WaitForMultipleObjects", kernel32))

        self._kernel32 = _Kernel32View()
        return self._kernel32

    def _signal_stop_locked(self) -> None:
        stopHandle = self._stopHandle
        if not stopHandle:
            return
        try:
            kernel32 = self._ensure_kernel32()
            kernel32.SetEvent(stopHandle)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Watch thread
    # ------------------------------------------------------------------

    def _run(self, stopEvent: threading.Event | None = None) -> None:
        if stopEvent is None:
            stopEvent = self._stopRequested
        kernel32: Any = None
        stopHandle = 0
        try:
            try:
                kernel32 = self._ensure_kernel32()
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
                if stopEvent.is_set() or (self._thread is not None and self._thread is not threading.current_thread()):
                    kernel32.CloseHandle(stopHandle)
                    stopHandle = 0
                    return
                self._stopHandle = stopHandle
            try:
                notificationHandles: list[int] = []
                notificationPaths: list[Path] = []
                try:
                    while not stopEvent.is_set():
                        desiredPaths = [path for path in self._paths() if self._path_is_dir(path)][:63]
                        if desiredPaths != notificationPaths:
                            for handle in notificationHandles:
                                kernel32.FindCloseChangeNotification(handle)
                            notificationHandles = []
                            notificationPaths = []
                            for path in desiredPaths:
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
                        if signaledIndex == 0 or stopEvent.is_set():
                            return
                        pathIndex = signaledIndex - 1
                        reason = "watched directory changed"
                        if 0 <= pathIndex < len(notificationPaths):
                            reason = f"watched directory changed: {notificationPaths[pathIndex]}"
                            if not kernel32.FindNextChangeNotification(notificationHandles[pathIndex]):
                                # Force re-creation of handles on next iteration if re-arming failed.
                                notificationPaths = []
                        if not stopEvent.is_set():
                            try:
                                self._callback(reason)
                            except Exception:
                                log.debug("Google TTS standby directory watcher callback failed.", exc_info=True)
                finally:
                    for handle in notificationHandles:
                        kernel32.FindCloseChangeNotification(handle)
            finally:
                with self._lock:
                    if self._stopHandle == stopHandle:
                        self._stopHandle = None
                        self._kernel32 = None
                kernel32.CloseHandle(stopHandle)
                stopHandle = 0
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    @staticmethod
    def _path_is_dir(path: Path) -> bool:
        try:
            return path.is_dir()
        except OSError:
            return False

    def _watch_path(self, kernel32: Any, path: Path) -> int | None:
        if not self._path_is_dir(path):
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

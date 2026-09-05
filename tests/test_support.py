"""Shared helpers for standalone tests that must not import NVDA."""

from __future__ import annotations

import importlib.util
import struct
import sys
import threading
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DRIVER_DIR = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda"
DRIVER_PATH = DRIVER_DIR / "__init__.py"
PROCESSING_PATH = DRIVER_DIR / "speech_processing.py"
UNICODE_DATA_PATH = DRIVER_DIR / "unicode_data.py"
_TEST_DRIVER_PACKAGE = "_google_tts_for_nvda_test_driver"


def _test_driver_package() -> ModuleType:
    package = sys.modules.get(_TEST_DRIVER_PACKAGE)
    if package is None:
        package = ModuleType(_TEST_DRIVER_PACKAGE)
        package.__path__ = [str(DRIVER_DIR)]  # type: ignore[attr-defined]
        package.__package__ = _TEST_DRIVER_PACKAGE
        sys.modules[_TEST_DRIVER_PACKAGE] = package
    return package


@cache
def load_driver_module(moduleName: str) -> Any:
    """Load one pure driver module without executing its NVDA package initializer."""
    if not moduleName.isidentifier():
        raise ValueError(f"Invalid driver module name: {moduleName!r}")
    path = DRIVER_DIR / f"{moduleName}.py"
    if not path.is_file():
        raise FileNotFoundError(path)
    _test_driver_package()
    qualifiedName = f"{_TEST_DRIVER_PACKAGE}.{moduleName}"
    spec = importlib.util.spec_from_file_location(qualifiedName, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import specification for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualifiedName] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(qualifiedName, None)
        raise
    return module


def pcm_bytes(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm_samples(pcm: bytes) -> tuple[int, ...]:
    if len(pcm) % 2:
        raise ValueError("Signed 16-bit PCM must contain an even number of bytes")
    return struct.unpack(f"<{len(pcm) // 2}h", pcm)


# ---------------------------------------------------------------------------
# Shared fake helpers for bridge / engine / process-manager tests
# ---------------------------------------------------------------------------

bridge_module = load_driver_module("bridge")


class FakeCdpClient:
    """Minimal stand-in for CdpClient that tracks connect/close calls."""

    def __init__(self) -> None:
        self.connected = False
        self.connect_count = 0
        self.close_count = 0
        self._lock = threading.Lock()

    def is_connected(self) -> bool:
        with self._lock:
            return self.connected

    def connect(self, ws_url: str) -> None:
        with self._lock:
            self.connected = True
            self.connect_count += 1

    def close(self) -> None:
        with self._lock:
            self.connected = False
            self.close_count += 1


class FakeEngine:
    """Minimal stand-in for WasmTtsEngineBridge that records calls."""

    def __init__(self, *, speak_result: dict | None = None) -> None:
        self._speak_result = speak_result or {"success": True}
        self._busy_lock = threading.Lock()
        self._runtime_busy = False
        self.speak_calls = 0
        self.stop_calls = 0
        self.cancel_calls = 0
        self.preload_calls = 0
        self._stop_event = threading.Event()

    @property
    def runtime_busy(self) -> bool:
        with self._busy_lock:
            return self._runtime_busy

    @runtime_busy.setter
    def runtime_busy(self, value: bool) -> None:
        with self._busy_lock:
            self._runtime_busy = value

    @property
    def calls(self) -> int:
        return self.speak_calls

    def speak(self, *args, **kwargs):
        self.speak_calls += 1
        return self._speak_result

    def stop_runtime(self):
        self.stop_calls += 1

    def cancel_current(self):
        self.cancel_calls += 1

    def preload_voice(self, *args, **kwargs):
        self.preload_calls += 1
        return {"success": True}

    def enable_cdp_domains(self, *, cancelEvent=None):
        pass

    def wait_until_ready(self, *, cancelEvent=None):
        pass

    def send_fast_stop(self):
        pass


class FakeProcessManager:
    """Minimal stand-in for BrowserProcessManager."""

    def __init__(self, *, urls: list[str] | None = None) -> None:
        self._urls = list(urls or ["ws://fake:9222/devtools/page/1"])
        self._index = 0
        self._started_count = 0
        self._terminated = False
        self._lock = threading.Lock()
        self.profile_runtime = "chrome"

    def start_and_get_websocket_url(self, *, cancelEvent=None, skipRuntimes=None):
        with self._lock:
            self._started_count += 1
            if self._index >= len(self._urls):
                raise bridge_module.CdpError("No more runtimes", "Exhausted")
            url = self._urls[self._index]
            self._index += 1
            return url

    def terminate(self):
        with self._lock:
            self._terminated = True

    def _log_browser_runtime_failure(self, runtime, exc, has_more_choices):
        pass


def make_fake_bridge(
    *,
    engine: FakeEngine | None = None,
    cdp_client: FakeCdpClient | None = None,
    process_manager: FakeProcessManager | None = None,
) -> Any:
    """Build a ChromeTtsBridge using fake internals (no real browser)."""
    b = bridge_module.ChromeTtsBridge.__new__(bridge_module.ChromeTtsBridge)
    b._lock = threading.RLock()
    b._connectionLock = threading.Lock()
    b._cdp_client = cdp_client or FakeCdpClient()
    b._process_manager = process_manager or FakeProcessManager()
    b._engine = engine or FakeEngine()
    b._needsRecycle = False
    b._recycleUrgent = False
    b._recycleReason = ""
    b._lastMemoryCheckAt = 0.0
    b._runtimeReadyAt = 0.0
    b._highMemorySampleCount = 0
    b.catalog = bridge_module.VoiceCatalog.load()
    return b

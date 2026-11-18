"""Shared helpers for standalone tests that must not import NVDA."""

from __future__ import annotations

import importlib.util
import io
import itertools
import struct
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import cache
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DRIVER_DIR = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda"
DRIVER_PATH = DRIVER_DIR / "__init__.py"
PROCESSING_PATH = DRIVER_DIR / "speech_processing.py"
UNICODE_DATA_PATH = DRIVER_DIR / "unicode_data.py"
_TEST_DRIVER_PACKAGE = "_google_tts_for_nvda_test_driver"
_FRESH_IMPORT_COUNTER = itertools.count(1)


def _ensure_driver_package(packageName: str) -> ModuleType:
    package = sys.modules.get(packageName)
    if package is None:
        package = ModuleType(packageName)
        package.__path__ = [str(DRIVER_DIR)]  # type: ignore[attr-defined]
        package.__package__ = packageName
        sys.modules[packageName] = package
    return package


def _test_driver_package() -> ModuleType:
    return _ensure_driver_package(_TEST_DRIVER_PACKAGE)


def load_module_from_path(qualname: str, path: Path) -> Any:
    """Load and register a Python module from an arbitrary file path."""
    if not path.is_file():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location(qualname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create an import specification for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(qualname, None)
        raise
    return module


@cache
def load_driver_module(moduleName: str) -> Any:
    """Load one pure driver module without executing its NVDA package initializer."""
    if not moduleName.isidentifier():
        raise ValueError(f"Invalid driver module name: {moduleName!r}")
    _test_driver_package()
    return load_module_from_path(f"{_TEST_DRIVER_PACKAGE}.{moduleName}", DRIVER_DIR / f"{moduleName}.py")


def load_fresh_driver_module(moduleName: str) -> Any:
    """Load one driver module uncached in an isolated temporary package namespace."""
    if not moduleName.isidentifier():
        raise ValueError(f"Invalid driver module name: {moduleName!r}")
    pkgName = f"_google_tts_fresh_driver_{next(_FRESH_IMPORT_COUNTER)}"
    _ensure_driver_package(pkgName)
    try:
        return load_module_from_path(f"{pkgName}.{moduleName}", DRIVER_DIR / f"{moduleName}.py")
    finally:
        prefix = f"{pkgName}."
        for key in [k for k in sys.modules if k == pkgName or k.startswith(prefix)]:
            sys.modules.pop(key, None)


def is_lock_free_from_other_thread(lock: threading.RLock | threading.Lock) -> bool:
    """Return True if another thread can immediately acquire ``lock`` non-blockingly."""
    result: list[bool] = []

    def probe() -> None:
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        result.append(acquired)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(timeout=2)
    return bool(result and result[0])


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

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"result": {"result": {"value": True}}}


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
            if skipRuntimes and self.profile_runtime in skipRuntimes:
                for candidate in ("edge", "brave", "chrome"):
                    if candidate not in skipRuntimes:
                        self.profile_runtime = candidate
                        break
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
    b._lifecycleEpoch = 0
    b.catalog = bridge_module.VoiceCatalog.load()
    return b


def write_test_engine(root: Path, version: str, *, voices: bool = True) -> Path:
    """Create a temporary fake engine version directory for catalog tests."""
    version_dir = root / version
    version_dir.mkdir(parents=True, exist_ok=True)
    if voices:
        (version_dir / "voices.json").write_text("[]", encoding="utf-8")
    return version_dir


def captured_prints(print_mock: Any) -> str:
    """Join positional first arguments logged via a mocked builtins.print."""
    return " ".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)


class FakeHttpResponse:
    """Minimal urlopen() context manager so download tests never touch the network."""

    def __init__(self, payload: bytes, content_length: int | None = None) -> None:
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload) if content_length is None else content_length)}

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class FakeConf(dict[str, Any]):
    """Lightweight dict-backed NVDA config.conf mock with spec and section caches."""

    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        *,
        cache: dict[str, Any] | None = None,
        root_cache: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(initial or {})
        self.spec: dict[str, Any] = {}
        self._cache: dict[str, Any] = {} if cache is None else cache
        self.rootSection = SimpleNamespace(_cache={} if root_cache is None else root_cache)


@contextmanager
def temporary_doc_tree(
    build_i18n_mod: Any,
    *,
    en_html: str | None = None,
    lang: str | None = None,
    po_text: str | None = None,
    html_text: str | None = None,
    md_text: str | None = None,
    locale_po_text: str | None = None,
    manifest_text: str | None = None,
    create_lang_dir: bool = True,
    patch_en_path: bool = True,
    patch_addon_dir: bool = True,
    patch_doc_pot: bool = False,
    patch_locale_dir: bool = False,
    en_path_override: Path | None = None,
) -> Iterator[SimpleNamespace]:
    """Create a temporary ``doc/`` (and optional ``locale/``) layout and patch ``build_i18n`` constants."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        doc_root = root / "doc"
        locale_root = root / "locale"
        doc_pot_path = doc_root / "readme.pot"
        en_html_path = doc_root / "en" / "readme.html"
        if en_html is not None:
            en_html_path.parent.mkdir(parents=True, exist_ok=True)
            en_html_path.write_text(en_html, encoding="utf-8")

        lang_dir = doc_root / lang if lang else None
        po_file = None
        html_file = None
        md_file = None
        locale_lang_dir = locale_root / lang if lang else None
        locale_po_file = None
        if lang_dir is not None:
            po_file = lang_dir / "readme.po"
            html_file = lang_dir / "readme.html"
            md_file = lang_dir / "readme.md"
            if create_lang_dir or po_text is not None or html_text is not None or md_text is not None:
                lang_dir.mkdir(parents=True, exist_ok=True)
            if po_text is not None:
                po_file.write_text(po_text, encoding="utf-8")
            if html_text is not None:
                html_file.write_text(html_text, encoding="utf-8")
            if md_text is not None:
                md_file.write_text(md_text, encoding="utf-8")

        if locale_lang_dir is not None and (locale_po_text is not None or manifest_text is not None):
            locale_lang_dir.mkdir(parents=True, exist_ok=True)
            locale_po_file = locale_lang_dir / "LC_MESSAGES" / "nvda.po"
            if locale_po_text is not None:
                locale_po_file.parent.mkdir(parents=True, exist_ok=True)
                locale_po_file.write_text(locale_po_text, encoding="utf-8")
            if manifest_text is not None:
                (locale_lang_dir / "manifest.ini").write_text(manifest_text, encoding="utf-8")

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(build_i18n_mod, "DOC_DIR", doc_root))
            if patch_en_path:
                stack.enter_context(
                    mock.patch.object(
                        build_i18n_mod,
                        "DOC_EN_PATH",
                        en_path_override if en_path_override is not None else en_html_path,
                    )
                )
            if patch_doc_pot:
                stack.enter_context(mock.patch.object(build_i18n_mod, "DOC_POT_PATH", doc_pot_path))
            if patch_locale_dir:
                locale_root.mkdir(parents=True, exist_ok=True)
                stack.enter_context(mock.patch.object(build_i18n_mod, "LOCALE_DIR", locale_root))
            if patch_addon_dir:
                stack.enter_context(mock.patch.object(build_i18n_mod, "ADDON_DIR", root))
            yield SimpleNamespace(
                root=root,
                doc_root=doc_root,
                doc_pot=doc_pot_path,
                locale_root=locale_root,
                en_html=en_html_path,
                lang_dir=lang_dir,
                po_file=po_file,
                html_file=html_file,
                md_file=md_file,
                locale_lang_dir=locale_lang_dir,
                locale_po_file=locale_po_file,
            )


@contextmanager
def temporary_po_file(content: str, filename: str = "test.po") -> Iterator[Path]:
    """Create a temporary PO file with UTF-8 content and yield its path."""
    with tempfile.TemporaryDirectory() as td:
        po_path = Path(td) / filename
        po_path.write_text(content, encoding="utf-8")
        yield po_path


@contextmanager
def temporary_manifest_file(build_i18n_mod: Any, content: str) -> Iterator[Path]:
    """Create a temporary manifest.ini file and patch ``build_i18n.MANIFEST_SOURCE``."""
    with tempfile.TemporaryDirectory() as td:
        manifest_path = Path(td) / "manifest.ini"
        manifest_path.write_text(content, encoding="utf-8")
        with mock.patch.object(build_i18n_mod, "MANIFEST_SOURCE", manifest_path):
            yield manifest_path


def interactive_options_for(
    build_i18n_mod: Any,
    inputs: list[str],
    *,
    languages: list[str] | None = None,
    doc_languages: list[str] | None = None,
) -> tuple[object, ...]:
    """Run ``build_i18n._interactive_options`` with mocked locales, stdin answers, and print suppression."""
    with ExitStack() as stack:
        if languages is not None:
            stack.enter_context(mock.patch.object(build_i18n_mod, "_addon_languages", return_value=languages))
        if doc_languages is not None:
            stack.enter_context(mock.patch.object(build_i18n_mod, "_addon_doc_languages", return_value=doc_languages))
        stack.enter_context(mock.patch("builtins.input", side_effect=inputs))
        stack.enter_context(mock.patch("builtins.print"))
        return tuple(build_i18n_mod._interactive_options(build_i18n_mod.DEFAULT_CHECKS))


@contextmanager
def patched_i18n_cli(
    build_i18n_mod: Any,
    argv: list[str],
    *,
    capture_print: bool = True,
    capture_stderr: bool = False,
) -> Iterator[SimpleNamespace]:
    """Patch ``build_i18n.sys.argv`` (and optionally ``builtins.print`` / ``sys.stderr``)."""
    stderr_buf = io.StringIO() if capture_stderr else None
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(build_i18n_mod.sys, "argv", ["build_i18n.py", *argv]))
        if stderr_buf is not None:
            stack.enter_context(mock.patch("sys.stderr", stderr_buf))
        print_mock = stack.enter_context(mock.patch("builtins.print")) if capture_print else None
        yield SimpleNamespace(print_mock=print_mock, stderr=stderr_buf)

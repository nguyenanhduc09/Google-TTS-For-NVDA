"""Bundled Bun speech runtime: process lifecycle and CDP-shaped IPC.

This module lets the add-on drive Google's WASM TTS engine through a bundled Bun
executable instead of a headless Chromium browser. It deliberately presents the
same duck-typed surface as :class:`bridge.BrowserProcessManager` and
:class:`bridge.CdpClient`, so :class:`bridge.ChromeTtsBridge` and the whole of
:class:`bridge.WasmTtsEngineBridge` -- including ``speak()`` and its
``handle_event()`` audio/mark/segmentEnd parser -- are shared verbatim by both
runtimes. Only ``ChromeTtsBridge.__init__`` decides which pair to construct.

Two things make that possible:

* ``bunRuntime/worker.js`` reuses ``web/bridgeHarness.js`` unchanged, so the
  engine is driven through the same ``window.googleTtsForNvda*`` entry points and
  the same session-token/packet-sizing/DSP behaviour as in the browser.
* The worker emits ``{"method": "Runtime.bindingCalled", "params": {"name": ...,
  "payload": ...}}`` envelopes on stdout, byte-for-byte the shape
  ``WasmTtsEngineBridge.handle_event()`` already consumes.

What disappears relative to the browser path: browser discovery and registry
scans, user-data/profile directories and their lock contention, the
``DevToolsActivePort`` handshake, the local HTTP server and its COOP/COEP
headers, the WebSocket transport (and the vendored ``websocketClientRepo``),
window hiding, and process-priority elevation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

try:
    from logHandler import log
except Exception:  # pragma: no cover - used by the standalone tests.
    import logging

    log = logging.getLogger("googleTtsForNvda")

try:
    import addonHandler

    addonHandler.initTranslation()
except Exception:  # pragma: no cover - used by the standalone tests.

    def _(message: str) -> str:
        return message


from . import bun_bootstrap, voice_store
from .bridge import (
    BINDING_NAME,
    SAMPLE_RATE,
    CdpCancelled,
    _friendly_cdp_error,
    _hidden_chrome_startup_kwargs,
    _process_tree_memory_usage,
    _raise_if_cancelled,
)
from .catalog import VoiceCatalog

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
BUN_RUNTIME = "bun"
BUN_RUNTIME_LABEL = "Bundled runtime (no browser)"
BUN_PATH_ENV_VAR = "GTTS_BUN_PATH"
BUN_EXECUTABLE_NAMES = ("bun.exe", "bun")
# Placeholder handed to BunClient.connect(); the Bun runtime has no WebSocket URL.
BUN_CONNECTION_URL = "bun://googleTtsForNvda"
STARTUP_GRACE_SECONDS = 2.0
READY_LINE_POLL_SECONDS = 0.01
STOP_GRACE_SECONDS = 2.0
STDIO_READ_POLL_SECONDS = 0.005
SPEAK_FUNCTION_NAME = "googleTtsForNvdaSpeak"
PRELOAD_FUNCTION_NAME = "googleTtsForNvdaPreload"
STOP_FUNCTION_NAME = "googleTtsForNvdaStop"
READY_FUNCTION_NAME = "googleTtsForNvdaReady"

_CALL_PATTERN = re.compile(
    r"^window\.(?P<function>googleTtsForNvda(?:Speak|Preload))\((?P<payload>.*)\)\s*$",
    re.DOTALL,
)


def runtime_dir() -> Path:
    """Directory the build places ``bun.exe`` in."""
    return RUNTIME_DIR


def bun_candidates() -> tuple[str, ...]:
    """Executable candidates in priority order.

    ``GTTS_BUN_PATH`` mirrors the existing ``CHROME_PATH``/``EDGE_PATH``/
    ``BRAVE_PATH`` convention for portable or custom installations.
    """
    candidates: list[str] = []
    override = os.environ.get(BUN_PATH_ENV_VAR, "").strip().strip('"')
    if override:
        candidates.append(override)
    for name in BUN_EXECUTABLE_NAMES:
        candidates.append(str(RUNTIME_DIR / name))
    return tuple(candidates)


def find_bun() -> str | None:
    """Return the first existing Bun executable, or ``None``."""
    for candidate in bun_candidates():
        if candidate and os.path.isfile(candidate):
            return candidate
    for name in BUN_EXECUTABLE_NAMES:
        resolved = _which(name)
        if resolved:
            return resolved
    return None


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def bun_runtime_available() -> bool:
    return find_bun() is not None


def _extract_json_argument(expression: str, payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise _friendly_cdp_error(
            _("Google TTS For NVDA could not start speech in the bundled speech runtime."),
            f"Malformed speech payload for {expression[:40]}: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise _friendly_cdp_error(
            _("Google TTS For NVDA could not start speech in the bundled speech runtime."),
            "Speech payload was not a JSON object.",
        )
    return parsed


def classify_expression(expression: str) -> tuple[str, dict[str, Any] | None]:
    """Translate a CDP ``Runtime.evaluate`` expression into a worker operation.

    ``WasmTtsEngineBridge`` builds JavaScript expression strings, so this is the
    one place that has to understand them. The forms are exactly the four the
    engine bridge emits; anything else is a programming error and raises.
    """
    stripped = expression.strip()
    if f"{READY_FUNCTION_NAME}()" in stripped:
        return "ready", None
    match = _CALL_PATTERN.match(stripped)
    if match is not None:
        functionName = match.group("function")
        payload = _extract_json_argument(stripped, match.group("payload"))
        if functionName == SPEAK_FUNCTION_NAME:
            return "speak", payload
        return "preload", payload
    if STOP_FUNCTION_NAME in stripped:
        return "stop", None
    raise _friendly_cdp_error(
        _("Google TTS For NVDA could not start speech in the bundled speech runtime."),
        f"Unsupported Runtime.evaluate expression for the bundled runtime: {stripped[:120]}",
    )


class BunProcessManager:
    """Spawns and supervises the bundled Bun worker.

    Duck-compatible with :class:`bridge.BrowserProcessManager` for everything
    ``ChromeTtsBridge`` touches.
    """

    # There is exactly one bundled executable, so there is no candidate list to
    # fall back through; retrying would loop on the same missing file.
    supports_runtime_fallback = False

    def __init__(self, catalog: VoiceCatalog) -> None:
        self.catalog = catalog
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._profileRuntime: str | None = None
        self._bootstrap: dict[str, Path] | None = None

    @property
    def chrome_process(self) -> subprocess.Popen[bytes] | None:
        with self._lock:
            return self._process

    @property
    def debug_port(self) -> int | None:
        return None

    @property
    def server_port(self) -> int | None:
        return None

    @property
    def profile_runtime(self) -> str | None:
        with self._lock:
            return self._profileRuntime

    def browser_memory_usage(self) -> dict[str, int] | None:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return None
        return _process_tree_memory_usage(process.pid)

    def _log_browser_runtime_failure(self, runtime: str, error: Exception, willTryFallback: bool) -> None:
        log.debug(
            "Google TTS bundled speech runtime (%s) failed to start%s: %s",
            runtime,
            ", trying fallback" if willTryFallback else "",
            error,
            exc_info=True,
        )

    def write_runtime_voices_json(self) -> Path:
        """Publish the installed-package catalog, as ``start_server()`` does."""
        runtimeRoot = voice_store.data_root() / "runtime"
        runtimeRoot.mkdir(parents=True, exist_ok=True)
        voicesPath = runtimeRoot / "voices.json"
        voicesPath.write_text(self.catalog.to_runtime_json(), encoding="utf-8")
        return voicesPath

    def environment(self, bootstrap: dict[str, Path], voicesPath: Path) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "GTTS_ENGINE_DIR": str(bun_bootstrap.ENGINE_DIR),
                "GTTS_WEB_DIR": str(bun_bootstrap.BASE_DIR / "web"),
                "GTTS_MAIN_ENTRY": str(bootstrap["main"]),
                "GTTS_PTHREAD_ENTRY": str(bootstrap["pthread"]),
                "GTTS_VOICE_DIR": str(voice_store.voice_dir()),
                "GTTS_VOICES_JSON": str(voicesPath),
                "GTTS_BINDING_NAME": BINDING_NAME,
            }
        )
        # Keep the engine's verbose native logging out of the protocol stream.
        environment.setdefault("GTTS_DEBUG", "0")
        return environment

    def start_and_get_websocket_url(
        self,
        cancelEvent: threading.Event | None = None,
        skipRuntimes: set[str] | None = None,
    ) -> str:
        _raise_if_cancelled(cancelEvent)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return BUN_CONNECTION_URL
        bunPath = find_bun()
        if bunPath is None:
            raise _friendly_cdp_error(
                _(
                    "The bundled Google TTS speech runtime was not found. "
                    "Reinstall Google TTS For NVDA, or set GTTS_BUN_PATH to a Bun executable."
                ),
                f"No Bun executable found. Searched: {', '.join(bun_candidates())}",
            )
        try:
            bootstrap = bun_bootstrap.generate()
        except bun_bootstrap.BunBootstrapError as exc:
            raise _friendly_cdp_error(
                _("Google TTS For NVDA could not prepare the bundled speech runtime."),
                str(exc),
            ) from exc
        voicesPath = self.write_runtime_voices_json()
        args = [bunPath, "run", str(bootstrap["worker"])]
        _raise_if_cancelled(cancelEvent)
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.environment(bootstrap, voicesPath),
                bufsize=0,
                **_hidden_chrome_startup_kwargs(),
            )
        except OSError as exc:
            raise _friendly_cdp_error(
                _("Google TTS For NVDA could not start the bundled speech runtime."),
                f"Failed to execute {bunPath}: {exc}",
            ) from exc
        with self._lock:
            self._process = process
            self._bootstrap = bootstrap
            self._profileRuntime = BUN_RUNTIME
        # Fail fast on an immediately-dying runtime instead of letting
        # wait_until_ready() poll for four seconds first.
        deadline = time.monotonic() + STARTUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            _raise_if_cancelled(cancelEvent)
            if process.poll() is not None:
                raise _friendly_cdp_error(
                    _("Google TTS For NVDA could not start the bundled speech runtime."),
                    f"Bun exited immediately with code {process.returncode}.",
                )
            time.sleep(READY_LINE_POLL_SECONDS)
        log.debug("Google TTS bundled speech runtime started (pid %d, %s).", process.pid, bunPath)
        return BUN_CONNECTION_URL

    def terminate(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._profileRuntime = None
        if process is None:
            return
        if process.poll() is None:
            with suppress(Exception):
                if process.stdin is not None:
                    process.stdin.close()
            with suppress(Exception):
                process.terminate()
                process.wait(timeout=STOP_GRACE_SECONDS)
            with suppress(Exception):
                process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            with suppress(Exception):
                if stream is not None:
                    stream.close()


class BunClient:
    """Line-oriented IPC client presenting the :class:`bridge.CdpClient` surface."""

    def __init__(self, processManager: BunProcessManager) -> None:
        self._processManager = processManager
        self._lock = threading.RLock()
        self._sendLock = threading.Lock()
        self._msgId = 0
        self._connected = False
        self._ready = False
        self._loadError = ""
        self._process: subprocess.Popen[bytes] | None = None
        self._readerThread: threading.Thread | None = None
        self._stopped = threading.Event()
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict[str, Any]] = {}
        self._eventHandlers: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._handlerErrors: dict[int, BaseException] = {}

    @property
    def ws(self) -> Any:
        return None

    @property
    def dispatcher(self) -> Any:
        return None

    def is_connected(self) -> bool:
        with self._lock:
            process = self._process
            return self._connected and process is not None and process.poll() is None

    def next_msg_id(self) -> int:
        with self._lock:
            self._msgId += 1
            return self._msgId

    def connect(self, wsUrl: str) -> None:
        process = self._processManager.chrome_process
        if process is None:
            raise _friendly_cdp_error(
                _("Google TTS For NVDA is not connected to the bundled speech runtime."),
                "Bun worker process is not running.",
            )
        with self._lock:
            self.close_locked()
            self._process = process
            self._connected = True
            self._stopped.clear()
            self._readerThread = threading.Thread(
                name="googleTtsForNvda.bunReader",
                target=self._reader_loop,
                daemon=True,
            )
            self._readerThread.start()

    def close(self) -> None:
        with self._lock:
            self.close_locked()

    def close_locked(self) -> None:
        self._stopped.set()
        self._connected = False
        self._ready = False
        thread = self._readerThread
        self._readerThread = None
        for event in self._pending.values():
            event.set()
        self._pending.clear()
        self._eventHandlers.clear()
        self._handlerErrors.clear()
        self._responses.clear()
        self._process = None
        if thread is not None and thread is not threading.current_thread():
            with suppress(Exception):
                thread.join(timeout=1.0)

    def _write(self, message: dict[str, Any]) -> None:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise _friendly_cdp_error(
                _("Google TTS For NVDA is not connected to the bundled speech runtime."),
                "Bun worker stdin is unavailable.",
            )
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._sendLock:
            process.stdin.write(payload)
            process.stdin.flush()

    def _reader_loop(self) -> None:
        with self._lock:
            process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while not self._stopped.is_set():
                line = process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    log.debug("Google TTS bundled runtime sent a non-protocol line: %.120s", text)
                    continue
                self._dispatch(message)
        except Exception:
            log.debug("Google TTS bundled runtime reader stopped", exc_info=True)
        finally:
            with self._lock:
                self._connected = False
                for event in self._pending.values():
                    event.set()

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "Runtime.ready":
            with self._lock:
                self._ready = bool(message.get("harnessReady", True))
            return
        if method in ("Runtime.loadError", "Runtime.crashed"):
            detail = str(message.get("error") or method)
            with self._lock:
                self._loadError = detail
                self._ready = False
            log.debug("Google TTS bundled runtime reported %s: %s", method, detail)
            return
        if method == "Runtime.bindingCalled":
            params = message.get("params")
            if isinstance(params, dict) and params.get("name") == BINDING_NAME:
                envelope = {"method": "Runtime.bindingCalled", "params": params}
                self._fan_out(envelope)
            return
        msgId = message.get("id")
        if msgId is None:
            return
        with self._lock:
            event = self._pending.get(int(msgId))
            if event is not None:
                self._responses[int(msgId)] = message
        if event is not None:
            event.set()

    def _fan_out(self, message: dict[str, Any]) -> None:
        with self._lock:
            handlers = list(self._eventHandlers.items())
        for handlerMsgId, handler in handlers:
            try:
                handler(message)
            except Exception as exc:
                with self._lock:
                    self._handlerErrors.setdefault(handlerMsgId, exc)
                    pending = self._pending.get(handlerMsgId)
                    if pending is not None:
                        pending.set()
                log.debug("Error in bundled runtime event handler", exc_info=True)

    def send_command_async(self, method: str, params: dict[str, Any] | None = None) -> None:
        with suppress(Exception):
            self._send_evaluate(method, params, msgId=None)

    def _send_evaluate(self, method: str, params: dict[str, Any] | None, msgId: int | None) -> str:
        if method != "Runtime.evaluate":
            raise _friendly_cdp_error(
                _("Google TTS For NVDA could not start speech in the bundled speech runtime."),
                f"The bundled speech runtime does not implement CDP method {method}.",
            )
        expression = str((params or {}).get("expression") or "")
        operation, payload = classify_expression(expression)
        message: dict[str, Any] = {"op": operation}
        if msgId is not None:
            message["id"] = msgId
        if payload is not None:
            message["payload"] = payload
        self._write(message)
        return operation

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30,
        eventHandler: Callable[[dict[str, Any]], None] | None = None,
        cancelEvent: threading.Event | None = None,
        onCancelCallback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not self.is_connected():
            raise _friendly_cdp_error(
                _("Google TTS For NVDA is not connected to the bundled speech runtime."),
                "Bun worker is not running.",
            )
        with self._lock:
            loadError = self._loadError
        if loadError:
            raise _friendly_cdp_error(
                _("Google TTS For NVDA could not load the bundled speech engine."),
                loadError,
            )
        if method in ("Runtime.enable", "Page.enable", "Runtime.addBinding"):
            return {"id": self.next_msg_id(), "result": {}}
        msgId = self.next_msg_id()
        event = threading.Event()
        with self._lock:
            self._pending[msgId] = event
            if eventHandler is not None:
                self._eventHandlers[msgId] = eventHandler
        try:
            try:
                self._send_evaluate(method, params, msgId=msgId)
            except Exception:
                if cancelEvent is not None and cancelEvent.is_set():
                    raise CdpCancelled() from None
                raise
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancelEvent is not None and cancelEvent.is_set():
                    if onCancelCallback is not None:
                        with suppress(Exception):
                            onCancelCallback()
                    raise CdpCancelled()
                if event.wait(timeout=STDIO_READ_POLL_SECONDS):
                    break
            else:
                raise _friendly_cdp_error(
                    _("The bundled speech runtime did not respond in time."),
                    f"Timed out waiting for {method}.",
                )
        finally:
            with self._lock:
                self._pending.pop(msgId, None)
                self._eventHandlers.pop(msgId, None)
                response = self._responses.pop(msgId, None)
                handlerError = self._handlerErrors.pop(msgId, None)

        if handlerError is not None:
            if cancelEvent is not None and cancelEvent.is_set():
                raise CdpCancelled()
            if onCancelCallback is not None:
                with suppress(Exception):
                    onCancelCallback()
            raise handlerError
        if response is None:
            if cancelEvent is not None and cancelEvent.is_set():
                raise CdpCancelled()
            raise _friendly_cdp_error(
                _("The bundled speech runtime connection closed unexpectedly."),
                "Bun worker stdout closed.",
            )
        if not response.get("ok", True):
            detail = str(response.get("error") or "Bundled speech runtime reported a failure.")
            raise _friendly_cdp_error(
                _("Google TTS For NVDA could not start speech in the bundled speech runtime."),
                detail,
            )
        if method == "Runtime.evaluate" and self._is_ready_probe(params):
            with self._lock:
                ready = self._ready
            return {"id": msgId, "result": {"result": {"value": ready}}}
        value = response.get("result")
        return {"id": msgId, "result": {"result": {"value": value if isinstance(value, dict) else {}}}}

    def _is_ready_probe(self, params: dict[str, Any] | None) -> bool:
        expression = str((params or {}).get("expression") or "")
        return f"{READY_FUNCTION_NAME}()" in expression

    def format_exception(self, exceptionDetails: dict[str, Any]) -> str:
        exception = exceptionDetails.get("exception")
        if isinstance(exception, dict) and exception.get("description"):
            return str(exception["description"])
        if exceptionDetails.get("text"):
            return str(exceptionDetails["text"])
        return "Bundled speech runtime exception."


def bundled_runtime_sample_rate() -> int:
    """Sample rate the Bun runtime emits; kept next to ``bridge.SAMPLE_RATE``."""
    return SAMPLE_RATE

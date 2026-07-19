# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import base64
from contextlib import suppress
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
	import winreg
except ImportError:  # pragma: no cover - NVDA add-on is Windows-only.
	winreg = None  # type: ignore

try:
	from logHandler import log
except Exception:  # pragma: no cover - used by the standalone smoke test.
	import logging

	log = logging.getLogger("googleTtsForNvda")

try:
	import config  # type: ignore
except Exception:  # pragma: no cover - used by standalone smoke tests.
	config = None  # type: ignore

try:
	import addonHandler

	addonHandler.initTranslation()
except Exception:  # pragma: no cover - used by standalone smoke tests.
	def _(message: str) -> str:
		return message

from .catalog import ENGINE_DIR, VoiceCatalog, is_package_supported_by_engine
from . import voice_store


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
WEBSOCKET_CLIENT_DIR = BASE_DIR / "websocketClientRepo"
BINDING_NAME = "googleTtsForNvdaBridge"
SAMPLE_RATE = 24000
RECV_POLL_TIMEOUT = 0.001
STARTUP_POLL_INTERVAL = 0.01
STOP_EXPRESSION = "window.googleTtsForNvdaStop && window.googleTtsForNvdaStop()"
LOCAL_CACHE_DIR_NAME = "googleTtsForNvda"
CHROME_PROFILE_DIR_NAME = "chromeProfiles"
EDGE_PROFILE_DIR_NAME = "edgeProfiles"
BRAVE_PROFILE_DIR_NAME = "braveProfiles"
PERSISTENT_PROFILE_DIR_NAME = "persistentSession"
PERSISTENT_PROFILE_MAX_BYTES = 500 * 1024 * 1024
CONFIG_SECTION = "googleTtsForNvda"
CONFIG_BROWSER_RUNTIME = "browserRuntime"
CONFIG_AUTO_LANGUAGE_DETECTION = "autoLanguageDetection"
CONFIG_AUTO_LANGUAGE_PREFERRED = "autoLanguagePreferred"
CONFIG_AUTO_LANGUAGE_CANDIDATES = "autoLanguageCandidates"
CONFIG_AUTO_LANGUAGE_PROFILES = "autoLanguageProfiles"
BROWSER_RUNTIME_EDGE = "edge"
BROWSER_RUNTIME_CHROME = "chrome"
BROWSER_RUNTIME_BRAVE = "brave"
DEFAULT_BROWSER_RUNTIME = BROWSER_RUNTIME_CHROME
DEFAULT_AUTO_LANGUAGE_DETECTION = False
DEFAULT_AUTO_LANGUAGE_PREFERRED = ""
DEFAULT_AUTO_LANGUAGE_CANDIDATES = ""
DEFAULT_AUTO_LANGUAGE_PROFILES = ""
BROWSER_RUNTIME_LABELS = {
	BROWSER_RUNTIME_EDGE: "Microsoft Edge",
	BROWSER_RUNTIME_CHROME: "Google Chrome",
	BROWSER_RUNTIME_BRAVE: "Brave",
}
BROWSER_RUNTIMES = (BROWSER_RUNTIME_CHROME, BROWSER_RUNTIME_EDGE, BROWSER_RUNTIME_BRAVE)

if str(WEBSOCKET_CLIENT_DIR) not in sys.path:
	sys.path.insert(1, str(WEBSOCKET_CLIENT_DIR))

import websocket  # type: ignore


class CdpError(Exception):
	def __init__(self, message: str, technicalDetail: str | None = None) -> None:
		super().__init__(message)
		self.technicalDetail = technicalDetail


class _BrowserProfileInUseError(CdpError):
	pass


class CdpCancelled(Exception):
	pass


AudioCallback = Callable[[bytes], None]
MarkCallback = Callable[[int], None]


class _ThreadingTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
	allow_reuse_address = True
	daemon_threads = True

	def handle_error(self, request: Any, client_address: Any) -> None:
		exc_type, _exc_value, _tb = sys.exc_info()
		if exc_type is not None and issubclass(exc_type, (ConnectionResetError, BrokenPipeError, OSError)):
			return
		super().handle_error(request, client_address)

	def finish_request(self, request: Any, client_address: Any) -> None:
		try:
			super().finish_request(request, client_address)
		except (ConnectionResetError, BrokenPipeError, OSError):
			pass


class _BridgeRequestHandler(http.server.SimpleHTTPRequestHandler):
	server_version = "GoogleTtsForNvda/0.4"
	extensions_map = {
		**http.server.SimpleHTTPRequestHandler.extensions_map,
		".wasm": "application/wasm",
		".js": "application/javascript",
	}

	def end_headers(self) -> None:
		self.send_header("Cross-Origin-Opener-Policy", "same-origin")
		self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
		self.send_header("Cross-Origin-Resource-Policy", "same-origin")
		super().end_headers()

	def log_message(self, format: str, *args: object) -> None:
		return

	def translate_path(self, path: str) -> str:
		route = urllib.parse.urlparse(path).path
		if route == "/":
			return str(WEB_DIR / "index.html")
		if route == "/voices.json":
			return str(voice_store.data_root() / "runtime" / "voices.json")
		if route.startswith("/engine/"):
			return str(_safe_join(ENGINE_DIR, route[len("/engine/") :]))
		if route.startswith("/voices/"):
			return str(_safe_join(voice_store.voice_dir(), route[len("/voices/") :]))
		if route.endswith(".zvoice"):
			return str(_safe_join(voice_store.voice_dir(), route.lstrip("/")))
		return str(_safe_join(WEB_DIR, route.lstrip("/")))


def _safe_join(root: Path, relative: str) -> Path:
	root = root.resolve()
	target = (root / urllib.parse.unquote(relative).replace("/", os.sep)).resolve()
	if root != target and root not in target.parents:
		return root / "__invalid__"
	return target


def _read_json_endpoint(port: int, path: str, method: str = "GET", timeout: float = 5) -> Any:
	url = f"http://127.0.0.1:{port}{path}"
	request = urllib.request.Request(url, method=method)
	with urllib.request.urlopen(request, timeout=timeout) as response:
		return json.loads(response.read().decode("utf-8"))


def _raise_if_cancelled(cancelEvent: threading.Event | None) -> None:
	if cancelEvent is not None and cancelEvent.is_set():
		raise CdpCancelled()


def _friendly_cdp_error(message: str, technicalDetail: str | None = None) -> CdpError:
	if technicalDetail:
		log.debug("Google TTS Chromium browser runtime detail: %s", technicalDetail)
	return CdpError(message, technicalDetail)


_TRANSIENT_RUNTIME_EVALUATE_ERRORS = (
	"Cannot find default execution context",
	"Execution context was destroyed",
	"Cannot find context with specified id",
	"Inspected target navigated or closed",
)


def _is_transient_runtime_evaluate_error(error: CdpError) -> bool:
	technicalDetail = str(getattr(error, "technicalDetail", "") or "")
	return "CDP error for Runtime.evaluate:" in technicalDetail and any(
		message in technicalDetail
		for message in _TRANSIENT_RUNTIME_EVALUATE_ERRORS
	)


def _browser_profile_in_use_error() -> _BrowserProfileInUseError:
	message = _(
		"The browser profile used by Google TTS For NVDA is already in use. "
		"Restart NVDA, or close any leftover supported Chromium browser helper processes."
	)
	technicalDetail = "Chromium browser runtime exited with profile-in-use code 21."
	log.debug("Google TTS Chromium browser runtime detail: %s", technicalDetail)
	return _BrowserProfileInUseError(message, technicalDetail)


def _hidden_chrome_startup_kwargs() -> dict[str, Any]:
	if os.name != "nt":
		return {}
	startupInfo = subprocess.STARTUPINFO()
	startupInfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
	startupInfo.wShowWindow = 0
	return {
		"startupinfo": startupInfo,
		"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
	}


def _hide_chrome_windows(processId: int) -> None:
	if os.name != "nt":
		return
	try:
		import ctypes
		from ctypes import wintypes

		windowProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
		user32 = ctypes.windll.user32
		# Use local prototypes so this helper does not mutate ctypes function
		# signatures shared with NVDA core in the same Python process.
		enumWindows = ctypes.WINFUNCTYPE(
			wintypes.BOOL,
			windowProc,
			wintypes.LPARAM,
		)(("EnumWindows", user32))
		getWindowThreadProcessId = ctypes.WINFUNCTYPE(
			wintypes.DWORD,
			wintypes.HWND,
			ctypes.POINTER(wintypes.DWORD),
		)(("GetWindowThreadProcessId", user32))
		isWindowVisible = ctypes.WINFUNCTYPE(
			wintypes.BOOL,
			wintypes.HWND,
		)(("IsWindowVisible", user32))
		showWindow = ctypes.WINFUNCTYPE(
			wintypes.BOOL,
			wintypes.HWND,
			ctypes.c_int,
		)(("ShowWindow", user32))

		@windowProc
		def enum_window(hwnd: int, _param: int) -> bool:
			ownerProcessId = wintypes.DWORD()
			getWindowThreadProcessId(hwnd, ctypes.byref(ownerProcessId))
			if ownerProcessId.value == processId and isWindowVisible(hwnd):
				showWindow(hwnd, 0)
			return True

		enumWindows(enum_window, 0)
	except Exception:
		log.debug("Could not hide Google TTS browser helper window.", exc_info=True)


def _elevate_chrome_priority(processId: int) -> None:
	if os.name != "nt":
		return
	try:
		import ctypes
		from ctypes import wintypes

		ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
		kernel32 = ctypes.windll.kernel32
		openProcess = ctypes.WINFUNCTYPE(
			wintypes.HANDLE,
			wintypes.DWORD,
			wintypes.BOOL,
			wintypes.DWORD,
		)(("OpenProcess", kernel32))
		setPriorityClass = ctypes.WINFUNCTYPE(
			wintypes.BOOL,
			wintypes.HANDLE,
			wintypes.DWORD,
		)(("SetPriorityClass", kernel32))
		closeHandle = ctypes.WINFUNCTYPE(
			wintypes.BOOL,
			wintypes.HANDLE,
		)(("CloseHandle", kernel32))
		handle = openProcess(0x0200, False, processId)
		if handle:
			setPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
			closeHandle(handle)
	except Exception:
		log.debug("Could not elevate Google TTS browser process priority.", exc_info=True)


def _normalize_browser_runtime(runtime: str | None) -> str:
	runtime = str(runtime or "").strip().lower()
	if runtime in BROWSER_RUNTIMES:
		return runtime
	return DEFAULT_BROWSER_RUNTIME


def configured_browser_runtime() -> str:
	if config is None:
		return DEFAULT_BROWSER_RUNTIME
	try:
		return _normalize_browser_runtime(config.conf[CONFIG_SECTION][CONFIG_BROWSER_RUNTIME])
	except Exception:
		return DEFAULT_BROWSER_RUNTIME


def set_configured_browser_runtime(runtime: str) -> str:
	runtime = _normalize_browser_runtime(runtime)
	if config is None:
		return runtime
	try:
		config.conf[CONFIG_SECTION][CONFIG_BROWSER_RUNTIME] = runtime
	except Exception:
		pass
	try:
		baseProfile = config.conf.profiles[0]
		if CONFIG_SECTION not in baseProfile:
			baseProfile[CONFIG_SECTION] = {}
		baseProfile[CONFIG_SECTION][CONFIG_BROWSER_RUNTIME] = runtime
	except Exception:
		pass
	return runtime


def _runtime_fallback_order(runtime: str | None = None) -> tuple[str, ...]:
	preferred = _normalize_browser_runtime(runtime)
	return tuple(dict.fromkeys((preferred, BROWSER_RUNTIME_CHROME, BROWSER_RUNTIME_EDGE, BROWSER_RUNTIME_BRAVE)))


def _edge_webview2_candidates() -> list[str]:
	executableName = "msedgewebview2.exe"
	envFolder = os.environ.get("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", "")
	envCandidates: list[str] = []
	if envFolder:
		envPath = Path(envFolder)
		envCandidates.extend(
			[
				str(envPath / executableName),
				*[
					str(candidate)
					for candidate in envPath.glob(f"*/{executableName}")
				],
			]
		)
	commonRoots = [
		Path(root) / "Microsoft" / "EdgeWebView" / "Application"
		for root in (
			os.environ.get("PROGRAMFILES", ""),
			os.environ.get("PROGRAMFILES(X86)", ""),
			os.environ.get("LOCALAPPDATA", ""),
		)
		if root
	]
	commonCandidates = [
		str(root / executableName)
		for root in commonRoots
	]
	versionedCandidates: list[str] = []
	for root in commonRoots:
		try:
			versionedCandidates.extend(str(candidate) for candidate in root.glob(f"*/{executableName}"))
		except OSError:
			pass
	return [*envCandidates, *commonCandidates, *versionedCandidates]


def _registry_has_edge_webview2_runtime() -> bool:
	if winreg is None:
		return False
	registryRoots = [
		(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
		(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
		(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients"),
		(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
		(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
		(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
	]
	for hive, subKey in registryRoots:
		try:
			with winreg.OpenKey(hive, subKey) as rootKey:
				index = 0
				while True:
					try:
						childName = winreg.EnumKey(rootKey, index)
					except OSError:
						break
					index += 1
					try:
						with winreg.OpenKey(rootKey, childName) as childKey:
							values = []
							for valueName in ("name", "DisplayName"):
								try:
									value, _ = winreg.QueryValueEx(childKey, valueName)
									values.append(str(value))
								except OSError:
									pass
							name = " ".join(values).lower()
							if "webview2" not in name and "edge webview" not in name:
								continue
							try:
								version, _ = winreg.QueryValueEx(childKey, "pv")
							except OSError:
								version = ""
							return str(version or "installed").strip() not in ("", "0.0.0.0")
					except OSError:
						pass
		except OSError:
			pass
	return False


def _registry_app_paths(executableName: str) -> list[str]:
	if winreg is None:
		return []
	paths: list[str] = []
	registryKeys = [
		(winreg.HKEY_LOCAL_MACHINE, fr"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executableName}"),
		(winreg.HKEY_CURRENT_USER, fr"Software\Microsoft\Windows\CurrentVersion\App Paths\{executableName}"),
	]
	for hive, subKey in registryKeys:
		try:
			with winreg.OpenKey(hive, subKey) as key:
				path, _ = winreg.QueryValueEx(key, "")
				paths.append(str(path))
		except OSError:
			pass
	return paths


def _browser_candidates(runtime: str) -> list[str]:
	runtime = _normalize_browser_runtime(runtime)
	if runtime == BROWSER_RUNTIME_EDGE:
		executableName = "msedge.exe"
		envPath = os.environ.get("EDGE_PATH", "")
		commonPaths = [
			str(Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / executableName),
			str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / executableName),
			str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / executableName),
			shutil.which("msedge.exe") or "",
			shutil.which("msedge") or "",
		]
	elif runtime == BROWSER_RUNTIME_BRAVE:
		executableName = "brave.exe"
		envPath = os.environ.get("BRAVE_PATH", "")
		commonPaths = [
			str(Path(os.environ.get("PROGRAMFILES", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / executableName),
			str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / executableName),
			str(Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware" / "Brave-Browser" / "Application" / executableName),
			shutil.which("brave.exe") or "",
			shutil.which("brave") or "",
		]
	else:
		executableName = "chrome.exe"
		envPath = os.environ.get("CHROME_PATH", "")
		commonPaths = [
			str(Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / executableName),
			str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / executableName),
			str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / executableName),
			shutil.which("chrome.exe") or "",
			shutil.which("chrome") or "",
		]
	return [envPath, *_registry_app_paths(executableName), *commonPaths]


def browser_path_for_runtime(runtime: str) -> str | None:
	for candidate in _browser_candidates(runtime):
		if candidate and Path(candidate).is_file():
			return candidate
	return None


def browser_executable_available(runtime: str) -> bool:
	return browser_path_for_runtime(runtime) is not None


def edge_webview2_available() -> bool:
	for candidate in _edge_webview2_candidates():
		if candidate and Path(candidate).is_file():
			return True
	return _registry_has_edge_webview2_runtime()


def browser_runtime_available(runtime: str) -> bool:
	if not browser_executable_available(runtime):
		return False
	if _normalize_browser_runtime(runtime) == BROWSER_RUNTIME_EDGE:
		return edge_webview2_available()
	return True


def browser_availability() -> dict[str, bool]:
	return {runtime: browser_runtime_available(runtime) for runtime in BROWSER_RUNTIMES}


def _browser_choices(
	runtime: str | None = None,
	skipRuntimes: set[str] | None = None,
) -> tuple[tuple[str, str], ...]:
	skipRuntimes = skipRuntimes or set()
	choices: list[tuple[str, str]] = []
	for candidateRuntime in _runtime_fallback_order(runtime or configured_browser_runtime()):
		if candidateRuntime in skipRuntimes:
			continue
		path = browser_path_for_runtime(candidateRuntime)
		if not path:
			continue
		if candidateRuntime == BROWSER_RUNTIME_EDGE and not edge_webview2_available():
			continue
		choices.append((candidateRuntime, path))
	return tuple(choices)


def _find_browser_choice(runtime: str | None = None) -> tuple[str, str] | None:
	choices = _browser_choices(runtime)
	return choices[0] if choices else None


def browser_runtime_snapshot(runtime: str | None = None) -> dict[str, Any]:
	selectedRuntime = _normalize_browser_runtime(runtime or configured_browser_runtime())
	paths = {candidateRuntime: browser_path_for_runtime(candidateRuntime) for candidateRuntime in BROWSER_RUNTIMES}
	executableAvailability = {
		candidateRuntime: path is not None
		for candidateRuntime, path in paths.items()
	}
	edgeWebView2Available = edge_webview2_available()
	availability = {
		candidateRuntime: bool(path)
		and (candidateRuntime != BROWSER_RUNTIME_EDGE or edgeWebView2Available)
		for candidateRuntime, path in paths.items()
	}
	effectivePath = None
	effectiveRuntime = None
	for candidateRuntime in _runtime_fallback_order(selectedRuntime):
		if not availability.get(candidateRuntime, False):
			continue
		effectivePath = paths.get(candidateRuntime)
		if effectivePath:
			effectiveRuntime = candidateRuntime
			break
	return {
		"selectedRuntime": selectedRuntime,
		"paths": paths,
		"executableAvailability": executableAvailability,
		"edgeWebView2Available": edgeWebView2Available,
		"availability": availability,
		"effectivePath": effectivePath,
		"effectiveRuntime": effectiveRuntime,
	}


def browser_runtime_for_path(browserPath: str) -> str:
	exeName = Path(browserPath).name.lower()
	if exeName in ("msedge.exe", "msedge"):
		return BROWSER_RUNTIME_EDGE
	if exeName in ("brave.exe", "brave"):
		return BROWSER_RUNTIME_BRAVE
	return BROWSER_RUNTIME_CHROME


def effective_browser_runtime(runtime: str | None = None) -> str | None:
	choice = _find_browser_choice(runtime)
	if choice is None:
		return None
	return choice[0]


def find_browser(runtime: str | None = None) -> str | None:
	choice = _find_browser_choice(runtime)
	if choice is None:
		return None
	return choice[1]


def edge_webview2_blocks_effective_runtime(runtime: str | None = None) -> bool:
	selectedRuntime = _normalize_browser_runtime(runtime or configured_browser_runtime())
	if edge_webview2_available():
		return False
	if _find_browser_choice(selectedRuntime) is not None:
		return False
	return any(
		candidateRuntime == BROWSER_RUNTIME_EDGE
		and browser_executable_available(BROWSER_RUNTIME_EDGE)
		for candidateRuntime in _runtime_fallback_order(selectedRuntime)
	)


class CdpDispatcher:
	"""Event-driven WebSocket message dispatcher separating network receive loops from callers."""

	def __init__(self, ws: websocket.WebSocket) -> None:
		self._ws = ws
		self._sendLock = threading.Lock()
		self._stateLock = threading.Lock()
		self._pendingEvents: dict[int, threading.Event] = {}
		self._responses: dict[int, dict[str, Any]] = {}
		self._eventHandlers: dict[int, Callable[[dict[str, Any]], None]] = {}
		self._stopped = threading.Event()
		self._readerThread: threading.Thread | None = None

	def start(self) -> None:
		with self._stateLock:
			if self._readerThread is not None and self._readerThread.is_alive():
				return
			self._stopped.clear()
			self._readerThread = threading.Thread(
				name="googleTtsForNvda.cdpReader",
				target=self._reader_loop,
				daemon=True,
			)
			self._readerThread.start()

	def stop(self) -> None:
		self._stopped.set()
		with self._stateLock:
			for event in self._pendingEvents.values():
				event.set()
			self._pendingEvents.clear()
			self._eventHandlers.clear()

	def send(self, command: dict[str, Any]) -> None:
		payload = json.dumps(command)
		with self._sendLock:
			self._ws.send(payload)

	def register_request(
		self,
		msgId: int,
		eventHandler: Callable[[dict[str, Any]], None] | None = None,
	) -> threading.Event:
		event = threading.Event()
		with self._stateLock:
			self._pendingEvents[msgId] = event
			if eventHandler is not None:
				self._eventHandlers[msgId] = eventHandler
		return event

	def unregister_request(self, msgId: int) -> dict[str, Any] | None:
		with self._stateLock:
			self._pendingEvents.pop(msgId, None)
			self._eventHandlers.pop(msgId, None)
			return self._responses.pop(msgId, None)

	def _reader_loop(self) -> None:
		try:
			while not self._stopped.is_set():
				try:
					rawMessage = self._ws.recv()
				except (websocket.WebSocketTimeoutException, TimeoutError):
					continue
				except Exception:
					break
				if not rawMessage:
					break
				try:
					message = json.loads(rawMessage)
				except Exception:
					continue

				with self._stateLock:
					handlers = list(self._eventHandlers.values())
					msgId = message.get("id")
					event = self._pendingEvents.get(msgId) if msgId is not None else None
					if event is not None and msgId is not None:
						self._responses[msgId] = message

				for handler in handlers:
					try:
						handler(message)
					except Exception:
						log.debug("Error in CDP event handler", exc_info=True)

				if event is not None:
					event.set()
		finally:
			with self._stateLock:
				for ev in self._pendingEvents.values():
					ev.set()


class BrowserProcessManager:
	"""Manages browser executable discovery, profile directories, HTTP server, and subprocess lifecycle."""

	def __init__(self, catalog: VoiceCatalog) -> None:
		self.catalog = catalog
		self._server: _ThreadingTcpServer | None = None
		self._serverThread: threading.Thread | None = None
		self._serverPort: int | None = None
		self._chromeProcess: subprocess.Popen[bytes] | None = None
		self._debugPort: int | None = None
		self._profileDir: Path | None = None
		self._profileRuntime: str | None = None
		self._profileIsPersistent = False
		self._lock = threading.RLock()

	@classmethod
	def find_browser(cls) -> str | None:
		return find_browser()

	@classmethod
	def find_chrome(cls) -> str | None:
		return cls.find_browser()

	@property
	def chrome_process(self) -> subprocess.Popen[bytes] | None:
		return self._chromeProcess

	@property
	def debug_port(self) -> int | None:
		return self._debugPort

	@property
	def server_port(self) -> int | None:
		return self._serverPort

	@property
	def profile_runtime(self) -> str | None:
		return self._profileRuntime

	def start_server(self) -> int:
		with self._lock:
			if self._server is not None and self._serverPort is not None:
				return self._serverPort
			runtimeDir = voice_store.data_root() / "runtime"
			runtimeDir.mkdir(parents=True, exist_ok=True)
			(runtimeDir / "voices.json").write_text(self.catalog.to_runtime_json(), encoding="utf-8")
			self._server = _ThreadingTcpServer(("127.0.0.1", 0), _BridgeRequestHandler)
			self._serverPort = int(self._server.server_address[1])
			self._serverThread = threading.Thread(
				name="googleTtsForNvda.http",
				target=self._server.serve_forever,
				daemon=True,
			)
			self._serverThread.start()
			return self._serverPort

	def _stop_browser_process(self, *, removeProfile: bool) -> None:
		if self._chromeProcess is not None and self._chromeProcess.poll() is None:
			with suppress(Exception):
				self._chromeProcess.terminate()
				self._chromeProcess.wait(timeout=2)
			with suppress(Exception):
				self._chromeProcess.kill()
		self._chromeProcess = None
		self._debugPort = None
		if removeProfile:
			self._remove_chrome_profile()
		else:
			self._release_chrome_profile()

	def _prepare_browser_start(self, cancelEvent: threading.Event | None = None) -> bool:
		self.start_server()
		if self._chromeProcess is not None and self._chromeProcess.poll() is None:
			if self._debugPort is not None and self._serverPort is not None:
				return True
			self._stop_browser_process(removeProfile=False)
		elif self._chromeProcess is not None:
			self._stop_browser_process(removeProfile=False)
		_raise_if_cancelled(cancelEvent)
		return False

	def _browser_choices_or_raise(
		self,
		skipRuntimes: set[str] | None = None,
	) -> tuple[tuple[str, str], ...]:
		choices = _browser_choices(skipRuntimes=skipRuntimes)
		if choices:
			return choices
		if edge_webview2_blocks_effective_runtime():
			raise _friendly_cdp_error(
				_(
					"Microsoft Edge WebView2 Runtime was not found. "
					"Install or repair Microsoft Edge WebView2 Runtime before using Microsoft Edge as the Google TTS For NVDA Chromium browser runtime."
				),
				"Microsoft Edge executable was found, but Microsoft Edge WebView2 Runtime was not found.",
			)
		raise _friendly_cdp_error(
			_("No supported Chromium browser runtime was found. Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."),
			"No supported Chromium browser runtime executable was found.",
		)

	def _log_browser_runtime_failure(self, runtime: str, error: Exception, willTryFallback: bool) -> None:
		label = BROWSER_RUNTIME_LABELS.get(runtime, runtime)
		action = "trying the next fallback runtime" if willTryFallback else "no fallback runtime remains"
		detail = str(getattr(error, "technicalDetail", "") or error)
		log.debug(
			"Google TTS %s browser runtime failed; %s. %s",
			label,
			action,
			detail,
			exc_info=True,
		)

	def _start_browser_choice(
		self,
		browserRuntime: str,
		browserPath: str,
		cancelEvent: threading.Event | None = None,
	) -> tuple[int, int]:
		for usePersistentProfile in (True, False):
			profileDir = self._get_browser_profile_dir(browserRuntime, usePersistentProfile)
			devToolsFile = profileDir / "DevToolsActivePort"
			try:
				devToolsFile.unlink()
			except FileNotFoundError:
				pass
			pageUrl = self._page_url()
			args = [
				browserPath,
				"--headless=new",
				"--remote-debugging-port=0",
				"--remote-allow-origins=*",
				f"--user-data-dir={profileDir}",
				"--no-first-run",
				"--no-default-browser-check",
				"--disable-background-networking",
				"--disable-breakpad",
				"--disable-crash-reporter",
				"--disable-gpu",
				"--noerrdialogs",
				"--autoplay-policy=no-user-gesture-required",
				"--window-position=-32000,-32000",
				"--window-size=1,1",
				"--disable-background-timer-throttling",
				"--disable-backgrounding-occluded-windows",
				"--disable-renderer-backgrounding",
				"--js-flags=--no-idle-gc --wasm-lazy-compilation=false --wasm-dynamic-tiering --max-old-space-size=512",
				"--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,TimerThrottlingForBackgroundTabs",
				"--enable-features=AudioWorkletThreadRealtimePriority,WebAssemblySimd,WebAssemblyTiering,WasmCodeGC,WasmCodeProtection",
				"--enable-wasm-simd",
				pageUrl,
			]
			try:
				self._chromeProcess = subprocess.Popen(
					args,
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
					**_hidden_chrome_startup_kwargs(),
				)
				_hide_chrome_windows(self._chromeProcess.pid)
				_elevate_chrome_priority(self._chromeProcess.pid)
				self._debugPort = self._read_devtools_port(devToolsFile, cancelEvent)
				_hide_chrome_windows(self._chromeProcess.pid)
			except _BrowserProfileInUseError:
				self._debugPort = None
				if usePersistentProfile:
					log.debug(
						"Google TTS persistent browser profile is in use; retrying with a temporary profile."
					)
					self._release_chrome_profile()
					continue
				self._remove_chrome_profile()
				raise
			except Exception:
				self._stop_browser_process(removeProfile=True)
				raise
			assert self._serverPort is not None and self._debugPort is not None
			return self._serverPort, self._debugPort
		raise _browser_profile_in_use_error()

	def _start_first_available_browser(
		self,
		choices: tuple[tuple[str, str], ...],
		cancelEvent: threading.Event | None = None,
	) -> tuple[int, int]:
		lastError: Exception | None = None
		for index, (browserRuntime, browserPath) in enumerate(choices):
			_raise_if_cancelled(cancelEvent)
			try:
				return self._start_browser_choice(browserRuntime, browserPath, cancelEvent)
			except CdpCancelled:
				raise
			except Exception as exc:
				lastError = exc
				self._log_browser_runtime_failure(browserRuntime, exc, index < len(choices) - 1)
		if lastError is not None:
			raise lastError
		raise _friendly_cdp_error(
			_("No supported Chromium browser runtime was found. Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."),
			"No supported Chromium browser runtime executable was found.",
		)

	def start_browser(self, cancelEvent: threading.Event | None = None) -> tuple[int, int]:
		with self._lock:
			if self._prepare_browser_start(cancelEvent):
				assert self._serverPort is not None and self._debugPort is not None
				return self._serverPort, self._debugPort
			return self._start_first_available_browser(
				self._browser_choices_or_raise(),
				cancelEvent,
			)

	def get_page_websocket_url(self, cancelEvent: threading.Event | None = None) -> str:
		with self._lock:
			pageUrl = self._page_url()
			debugPort = self._debugPort
			chromeProcess = self._chromeProcess
		if debugPort is None:
			raise _friendly_cdp_error(
				_("The Chromium browser runtime is not ready yet."),
				"Browser DevTools port is not ready.",
			)
		for attempt in range(200):
			_raise_if_cancelled(cancelEvent)
			if chromeProcess is not None:
				_hide_chrome_windows(chromeProcess.pid)
			try:
				targets = _read_json_endpoint(debugPort, "/json/list", timeout=0.5)
			except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
				time.sleep(STARTUP_POLL_INTERVAL)
				continue
			if isinstance(targets, list):
				for target in targets:
					if not isinstance(target, dict):
						continue
					if target.get("type") != "page":
						continue
					wsUrl = target.get("webSocketDebuggerUrl")
					if not isinstance(wsUrl, str):
						continue
					if target.get("url") == pageUrl:
						return wsUrl
			time.sleep(STARTUP_POLL_INTERVAL)
		raise _friendly_cdp_error(
			_("Google TTS For NVDA could not find its speech page in the Chromium browser runtime."),
			"Could not find browser speech page target.",
		)

	def start_and_get_websocket_url(
		self,
		cancelEvent: threading.Event | None = None,
		skipRuntimes: set[str] | None = None,
	) -> str:
		with self._lock:
			if self._prepare_browser_start(cancelEvent):
				try:
					return self.get_page_websocket_url(cancelEvent)
				except CdpCancelled:
					raise
				except Exception as exc:
					self._log_browser_runtime_failure(
						self._profileRuntime or configured_browser_runtime(),
						exc,
						True,
					)
					self._stop_browser_process(removeProfile=True)

			choices = self._browser_choices_or_raise(skipRuntimes=skipRuntimes)
			lastError: Exception | None = None
			for index, (browserRuntime, browserPath) in enumerate(choices):
				_raise_if_cancelled(cancelEvent)
				try:
					self._start_browser_choice(browserRuntime, browserPath, cancelEvent)
					return self.get_page_websocket_url(cancelEvent)
				except CdpCancelled:
					raise
				except Exception as exc:
					lastError = exc
					self._stop_browser_process(removeProfile=True)
					self._log_browser_runtime_failure(browserRuntime, exc, index < len(choices) - 1)
			if lastError is not None:
				raise lastError
			raise _friendly_cdp_error(
				_("No supported Chromium browser runtime was found. Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."),
				"No supported Chromium browser runtime executable was found.",
			)

	def terminate(self) -> None:
		with self._lock:
			if self._chromeProcess is not None and self._chromeProcess.poll() is None:
				with suppress(Exception):
					self._chromeProcess.terminate()
					self._chromeProcess.wait(timeout=2)
				with suppress(Exception):
					self._chromeProcess.kill()
			self._chromeProcess = None
			self._debugPort = None
			if self._server is not None:
				self._server.shutdown()
				self._server.server_close()
			self._server = None
			self._serverThread = None
			self._serverPort = None
			self._release_chrome_profile()

	def _get_browser_profile_dir(self, runtime: str, usePersistentProfile: bool = True) -> Path:
		runtime = _normalize_browser_runtime(runtime)
		if self._profileDir is not None:
			if self._profileRuntime == runtime:
				self._profileDir.mkdir(parents=True, exist_ok=True)
				return self._profileDir
			self._release_chrome_profile()
		root = self._browser_profile_root(runtime)
		root.mkdir(parents=True, exist_ok=True)
		self._cleanup_old_browser_profiles(root, runtime)
		if usePersistentProfile:
			profileDir = root / PERSISTENT_PROFILE_DIR_NAME
		else:
			profileDir = root / f"session-{os.getpid()}-{time.monotonic_ns()}"
		reused = profileDir.exists()
		profileDir.mkdir(parents=True, exist_ok=True)
		for lockName in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
			with suppress(OSError):
				(profileDir / lockName).unlink()
		with suppress(OSError):
			(profileDir / "DevToolsActivePort").unlink()
		self._profileDir = profileDir
		self._profileRuntime = runtime
		self._profileIsPersistent = usePersistentProfile
		log.debug(
			"Google TTS %s browser profile directory: %s (reused=%s, persistent=%s)",
			runtime, profileDir, reused, usePersistentProfile,
		)
		return self._profileDir

	def _browser_profile_root(self, runtime: str) -> Path:
		base = os.environ.get("LOCALAPPDATA")
		root = Path(base) if base else Path(tempfile.gettempdir())
		return root / LOCAL_CACHE_DIR_NAME / self._browser_profile_dir_name(runtime)

	def _browser_profile_dir_name(self, runtime: str) -> str:
		runtime = _normalize_browser_runtime(runtime)
		if runtime == BROWSER_RUNTIME_EDGE:
			return EDGE_PROFILE_DIR_NAME
		if runtime == BROWSER_RUNTIME_BRAVE:
			return BRAVE_PROFILE_DIR_NAME
		return CHROME_PROFILE_DIR_NAME

	def _cleanup_old_browser_profiles(self, root: Path, runtime: str) -> None:
		cutoff = time.time() - 2 * 24 * 60 * 60
		for child in root.iterdir():
			if not child.is_dir() or not child.name.startswith("session-"):
				continue
			try:
				if child.stat().st_mtime >= cutoff:
					continue
				shutil.rmtree(child, ignore_errors=True)
			except OSError:
				continue
		# Guard against unbounded persistent profile growth.
		persistent = root / PERSISTENT_PROFILE_DIR_NAME
		if persistent.is_dir():
			try:
				totalSize = 0
				for candidate in persistent.rglob("*"):
					if not candidate.is_file():
						continue
					totalSize += candidate.stat().st_size
					if totalSize > PERSISTENT_PROFILE_MAX_BYTES:
						break
				if totalSize > PERSISTENT_PROFILE_MAX_BYTES:
					log.debug(
						"Persistent %s browser profile exceeds 500 MB (%d bytes), resetting.",
						runtime, totalSize,
					)
					shutil.rmtree(persistent, ignore_errors=True)
			except OSError:
				pass

	def _release_chrome_profile(self) -> None:
		"""Release the profile directory reference without deleting it.

		Preserves the persistent profile (including the browser's compiled
		WASM code cache) so the next startup can skip WASM recompilation.  Only
		transient files (lock files, DevToolsActivePort) are cleaned up.
		"""
		profileDir = self._profileDir
		profileIsPersistent = self._profileIsPersistent
		self._profileDir = None
		self._profileRuntime = None
		self._profileIsPersistent = False
		if profileDir is None:
			return
		if not profileIsPersistent:
			with suppress(OSError):
				shutil.rmtree(profileDir, ignore_errors=True)
			return
		for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
					 "lockfile", "DevToolsActivePort"):
			with suppress(OSError):
				(profileDir / name).unlink()

	def _remove_chrome_profile(self) -> None:
		"""Fully delete the profile directory (used on browser startup failure)."""
		profileDir = self._profileDir
		self._profileDir = None
		self._profileRuntime = None
		self._profileIsPersistent = False
		if profileDir is None:
			return
		try:
			shutil.rmtree(profileDir, ignore_errors=True)
		except OSError:
			log.debug("Could not remove Google TTS browser session profile.", exc_info=True)

	def _read_devtools_port(self, devToolsFile: Path, cancelEvent: threading.Event | None = None) -> int:
		for attempt in range(400):
			_raise_if_cancelled(cancelEvent)
			if self._chromeProcess is not None:
				_hide_chrome_windows(self._chromeProcess.pid)
			if self._chromeProcess is not None and self._chromeProcess.poll() is not None:
				exitCode = self._chromeProcess.returncode
				self._chromeProcess = None
				if exitCode == 21:
					raise _browser_profile_in_use_error()
				raise _friendly_cdp_error(
					_("The Chromium browser runtime closed before Google TTS For NVDA was ready."),
					f"Chromium browser runtime exited before DevTools became available: {exitCode}",
				)
			if devToolsFile.is_file():
				lines = devToolsFile.read_text(encoding="utf-8").splitlines()
				if lines:
					return int(lines[0])
			time.sleep(STARTUP_POLL_INTERVAL)
		raise _friendly_cdp_error(
			_("The Chromium browser runtime did not start in time."),
			"Timed out waiting for browser DevTools.",
		)

	def _page_url(self) -> str:
		if self._serverPort is None:
			raise _friendly_cdp_error(
				_("Google TTS For NVDA could not start its local browser bridge."),
				"Bridge HTTP server is not running.",
			)
		return f"http://127.0.0.1:{self._serverPort}/"


class CdpClient:
	"""Core WebSocket IPC transport and message ID correlation layer for browser CDP interaction."""

	def __init__(self) -> None:
		self._ws: websocket.WebSocket | None = None
		self._dispatcher: CdpDispatcher | None = None
		self._msgId = 0
		self._msgIdLock = threading.Lock()
		self._lock = threading.RLock()

	@property
	def ws(self) -> websocket.WebSocket | None:
		return self._ws

	@property
	def dispatcher(self) -> CdpDispatcher | None:
		return self._dispatcher

	def connect(self, wsUrl: str) -> None:
		with self._lock:
			self.close()
			self._ws = websocket.create_connection(wsUrl, timeout=15)
			self._ws.settimeout(0.05)
			self._dispatcher = CdpDispatcher(self._ws)
			self._dispatcher.start()

	def close(self) -> None:
		with self._lock:
			if self._dispatcher is not None:
				self._dispatcher.stop()
				self._dispatcher = None
			if self._ws is not None:
				with suppress(Exception):
					self._ws.close()
				self._ws = None

	def is_connected(self) -> bool:
		with self._lock:
			return self._ws is not None and self._ws.connected and self._dispatcher is not None

	def next_msg_id(self) -> int:
		with self._msgIdLock:
			self._msgId += 1
			return self._msgId

	def send_command_async(self, method: str, params: dict[str, Any] | None = None) -> None:
		with self._lock:
			dispatcher = self._dispatcher
			ws = self._ws
		if ws is None or not ws.connected or dispatcher is None:
			return
		msgId = self.next_msg_id()
		command = {"id": msgId, "method": method, "params": params or {}}
		try:
			dispatcher.send(command)
		except Exception:
			log.debug("Could not asynchronously send CDP command %s", method, exc_info=True)

	def request(
		self,
		method: str,
		params: dict[str, Any] | None = None,
		timeout: float = 30,
		eventHandler: Callable[[dict[str, Any]], None] | None = None,
		cancelEvent: threading.Event | None = None,
		onCancelCallback: Callable[[], None] | None = None,
	) -> dict[str, Any]:
		with self._lock:
			dispatcher = self._dispatcher
			ws = self._ws
		if ws is None or dispatcher is None or not ws.connected:
			raise _friendly_cdp_error(
				_("Google TTS For NVDA is not connected to the Chromium browser runtime."),
				"Browser DevTools websocket is not connected.",
			)
		msgId = self.next_msg_id()
		command = {"id": msgId, "method": method, "params": params or {}}
		event = dispatcher.register_request(msgId, eventHandler=eventHandler)
		try:
			dispatcher.send(command)
			deadline = time.monotonic() + timeout
			while time.monotonic() < deadline:
				if cancelEvent is not None and cancelEvent.is_set():
					if onCancelCallback is not None:
						with suppress(Exception):
							onCancelCallback()
					raise CdpCancelled()
				if event.wait(timeout=0.01):
					break
			else:
				raise _friendly_cdp_error(
					_("The Chromium browser runtime did not respond in time."),
					f"Timed out waiting for {method}.",
				)
		finally:
			response = dispatcher.unregister_request(msgId)

		if response is None:
			raise _friendly_cdp_error(
				_("The Chromium browser runtime connection closed unexpectedly."),
				"Browser DevTools websocket closed.",
			)
		if "error" in response:
			raise _friendly_cdp_error(
				_("The Chromium browser runtime reported an error while processing speech."),
				f"CDP error for {method}: {response['error']}",
			)
		exceptionDetails = response.get("result", {}).get("exceptionDetails")
		if isinstance(exceptionDetails, dict):
			raise _friendly_cdp_error(
				_("The Chromium browser runtime reported an error while preparing speech."),
				self.format_exception(exceptionDetails),
			)
		return response

	def format_exception(self, exceptionDetails: dict[str, Any]) -> str:
		exception = exceptionDetails.get("exception")
		if isinstance(exception, dict) and exception.get("description"):
			return str(exception["description"])
		if exceptionDetails.get("text"):
			return str(exceptionDetails["text"])
		return "Browser DevTools runtime exception."


class WasmTtsEngineBridge:
	"""Routes high-level domain speech synthesis commands to the Google WASM TTS engine via CDP."""

	def __init__(self, cdp_client: CdpClient, catalog: VoiceCatalog) -> None:
		self._cdp = cdp_client
		self.catalog = catalog
		self._lastStopSentAt = 0.0
		self._stopLock = threading.Lock()
		self._runtimeBusy = False

	def enable_cdp_domains(self) -> None:
		self._cdp.request("Runtime.enable", timeout=15)
		self._cdp.request("Page.enable", timeout=15)
		self._cdp.request("Runtime.addBinding", {"name": BINDING_NAME}, timeout=15)

	def wait_until_ready(self, cancelEvent: threading.Event | None = None) -> None:
		expression = """
		typeof window.googleTtsForNvdaSpeak === "function"
		&& typeof window.googleTtsForNvdaPreload === "function"
		&& typeof window.googleTtsForNvdaBridge === "function"
		&& typeof window.googleTtsForNvdaReady === "function"
		&& window.googleTtsForNvdaReady() === true
		"""
		for attempt in range(400):
			_raise_if_cancelled(cancelEvent)
			try:
				response = self._cdp.request(
					"Runtime.evaluate",
					{"expression": expression, "returnByValue": True},
					timeout=5,
					cancelEvent=cancelEvent,
				)
			except CdpError as exc:
				if _is_transient_runtime_evaluate_error(exc):
					time.sleep(STARTUP_POLL_INTERVAL)
					continue
				raise
			if response.get("result", {}).get("result", {}).get("value") is True:
				return
			time.sleep(STARTUP_POLL_INTERVAL)
		raise _friendly_cdp_error(
			_("Google TTS For NVDA could not finish loading the browser speech engine."),
			"Browser speech harness did not finish loading.",
		)

	def preload_voice(self, options: dict[str, Any], cancelEvent: threading.Event | None = None) -> dict[str, Any]:
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not is_package_supported_by_engine(package):
			raise _friendly_cdp_error(
				_("This voice package is not supported by the bundled Google TTS engine."),
				f"Unsupported voice package for {package.id}.",
			)
		if not voice_store.is_package_installed(package):
			raise _friendly_cdp_error(
				_("This voice package is not installed. Open Google TTS Voice Manager to install it."),
				f"Missing voice package: {package.id}.",
			)
		_raise_if_cancelled(cancelEvent)
		payload = {
			"sessionId": f"preload-{time.monotonic_ns()}",
			"voiceName": options["voiceName"],
			"lang": options["lang"],
			"text": str(options.get("warmupText") or " "),
		}
		response = self._cdp.request(
			"Runtime.evaluate",
			{
				"expression": f"window.googleTtsForNvdaPreload({json.dumps(payload, ensure_ascii=False)})",
				"awaitPromise": True,
				"returnByValue": True,
				"userGesture": True,
				"timeout": 60000,
			},
			timeout=70,
			cancelEvent=cancelEvent,
			onCancelCallback=self.send_fast_stop,
		)
		value = response.get("result", {}).get("result", {}).get("value")
		return value if isinstance(value, dict) else {"success": True}

	def speak(
		self,
		text: str,
		options: dict[str, Any],
		onAudio: AudioCallback,
		cancelEvent: threading.Event | None = None,
		onMark: MarkCallback | None = None,
		segments: list[str] | None = None,
	) -> dict[str, Any]:
		if not text.strip():
			return {"success": True, "empty": True}
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not is_package_supported_by_engine(package):
			raise _friendly_cdp_error(
				_("This voice package is not supported by the bundled Google TTS engine."),
				f"Unsupported voice package for {package.id}.",
			)
		if not voice_store.is_package_installed(package):
			raise _friendly_cdp_error(
				_("This voice package is not installed. Open Google TTS Voice Manager to install it."),
				f"Missing voice package: {package.id}.",
			)
		_raise_if_cancelled(cancelEvent)
		sessionId = f"{time.monotonic_ns()}"
		payload = {
			"sessionId": sessionId,
			"text": text,
			"voiceName": options["voiceName"],
			"lang": options["lang"],
			"rate": options["rate"],
			"artificialRate": options.get("artificialRate", 1),
			"pitch": options["pitch"],
			"postPitch": options.get("postPitch", 1),
			"volume": options["volume"],
			"outputGain": options.get("outputGain", options["volume"]),
		}
		if segments:
			payload["segments"] = segments
		state: dict[str, Any] = {"audioChunks": 0, "done": False}
		startedAt = time.perf_counter()
		firstAudioAt: float | None = None

		def handle_event(message: dict[str, Any]) -> None:
			nonlocal firstAudioAt
			if message.get("method") != "Runtime.bindingCalled":
				return
			params = message.get("params") or {}
			if params.get("name") != BINDING_NAME:
				return
			rawPayload = params.get("payload")
			if not isinstance(rawPayload, str):
				return
			event = json.loads(rawPayload)
			if event.get("sessionId") != sessionId:
				return
			eventType = event.get("type")
			if eventType == "started":
				log.debug(
					"Google TTS session %s started in Chromium after %.1f ms.",
					sessionId,
					(time.perf_counter() - startedAt) * 1000,
				)
			elif eventType == "audio":
				if cancelEvent is not None and cancelEvent.is_set():
					return
				audio = base64.b64decode(str(event.get("data") or ""))
				if audio:
					if cancelEvent is not None and cancelEvent.is_set():
						return
					if firstAudioAt is None:
						firstAudioAt = time.perf_counter()
						log.debug(
							"Google TTS session %s first audio after %.1f ms.",
							sessionId,
							(firstAudioAt - startedAt) * 1000,
						)
					state["audioChunks"] += 1
					onAudio(audio)
			elif eventType == "mark":
				if onMark is not None:
					try:
						onMark(max(0, int(event.get("charIndex") or 0)))
					except (TypeError, ValueError):
						pass
			elif eventType == "done":
				state["done"] = True
			elif eventType == "error":
				detail = str(event.get("message") or "Browser speech synthesis failed.")
				raise _friendly_cdp_error(
					_("Google TTS For NVDA could not speak this text."),
					detail,
				)

		expression = f"window.googleTtsForNvdaSpeak({json.dumps(payload, ensure_ascii=False)})"
		self._runtimeBusy = cancelEvent is not None
		try:
			response = self._cdp.request(
				"Runtime.evaluate",
				{
					"expression": expression,
					"awaitPromise": True,
					"returnByValue": True,
					"userGesture": True,
					"timeout": 120000,
				},
				timeout=130,
				eventHandler=handle_event,
				cancelEvent=cancelEvent,
				onCancelCallback=self.send_fast_stop,
			)
		except CdpCancelled:
			raise
		finally:
			self._runtimeBusy = False
		result = response.get("result", {}).get("result", {})
		if result.get("subtype") == "error":
			raise _friendly_cdp_error(
				_("Google TTS For NVDA could not start speech in the Chromium browser runtime."),
				result.get("description") or "Browser speech evaluation failed.",
			)
		value = result.get("value")
		if isinstance(value, dict):
			value.update(state)
			return value
		return state

	def send_fast_stop(self) -> None:
		if not self._cdp.is_connected():
			return
		with self._stopLock:
			now = time.monotonic()
			if now - self._lastStopSentAt < 0.02:
				return
			self._lastStopSentAt = now
			self._cdp.send_command_async(
				"Runtime.evaluate",
				{
					"expression": STOP_EXPRESSION,
					"awaitPromise": False,
					"returnByValue": True,
				},
			)

	def stop_runtime(self) -> None:
		try:
			self._cdp.request(
				"Runtime.evaluate",
				{
					"expression": STOP_EXPRESSION,
					"awaitPromise": True,
					"returnByValue": True,
				},
				timeout=5,
			)
		except Exception:
			log.debug("Could not stop Google TTS Chromium browser runtime.", exc_info=True)

	def cancel_current(self) -> None:
		if self._runtimeBusy:
			self.send_fast_stop()


class ChromeTtsBridge:
	"""Public facade orchestrating BrowserProcessManager, CdpClient, and WasmTtsEngineBridge."""

	def __init__(self, catalog: VoiceCatalog | None = None) -> None:
		self.catalog = catalog or VoiceCatalog.load()
		self._process_manager = BrowserProcessManager(self.catalog)
		self._cdp_client = CdpClient()
		self._engine = WasmTtsEngineBridge(self._cdp_client, self.catalog)
		self._lock = threading.RLock()

	@classmethod
	def find_browser(cls) -> str | None:
		return BrowserProcessManager.find_browser()

	@classmethod
	def find_chrome(cls) -> str | None:
		return cls.find_browser()

	@property
	def _ws(self) -> websocket.WebSocket | None:
		return self._cdp_client.ws

	@property
	def _dispatcher(self) -> CdpDispatcher | None:
		return self._cdp_client.dispatcher

	@property
	def _chromeProcess(self) -> subprocess.Popen[bytes] | None:
		return self._process_manager.chrome_process

	@property
	def _debugPort(self) -> int | None:
		return self._process_manager.debug_port

	@property
	def _serverPort(self) -> int | None:
		return self._process_manager.server_port

	def ensure_connection(self) -> None:
		with self._lock:
			if self._cdp_client.is_connected():
				return
			skipRuntimes: set[str] = set()
			while True:
				runtime: str | None = None
				try:
					wsUrl = self._process_manager.start_and_get_websocket_url(
						skipRuntimes=skipRuntimes,
					)
					runtime = self._process_manager.profile_runtime
					self._cdp_client.connect(wsUrl)
					self._engine.enable_cdp_domains()
					self._engine.wait_until_ready()
					return
				except CdpCancelled:
					self._cdp_client.close()
					self._process_manager.terminate()
					raise
				except Exception as exc:
					self._cdp_client.close()
					runtime = runtime or self._process_manager.profile_runtime
					self._process_manager.terminate()
					if runtime:
						runtime = _normalize_browser_runtime(runtime)
						skipRuntimes.add(runtime)
						if _browser_choices(skipRuntimes=skipRuntimes):
							self._process_manager._log_browser_runtime_failure(runtime, exc, True)
							continue
					raise

	def preload_voice(self, options: dict[str, Any], cancelEvent: threading.Event | None = None) -> dict[str, Any]:
		self.ensure_connection()
		return self._engine.preload_voice(options, cancelEvent=cancelEvent)

	def speak(
		self,
		text: str,
		options: dict[str, Any],
		onAudio: AudioCallback,
		cancelEvent: threading.Event | None = None,
		onMark: MarkCallback | None = None,
		segments: list[str] | None = None,
	) -> dict[str, Any]:
		self.ensure_connection()
		return self._engine.speak(
			text,
			options,
			onAudio,
			cancelEvent=cancelEvent,
			onMark=onMark,
			segments=segments,
		)

	def stop_runtime(self) -> None:
		try:
			self.ensure_connection()
			self._engine.stop_runtime()
		except Exception:
			log.debug("Could not stop Google TTS Chromium browser runtime.", exc_info=True)

	def cancel_current(self) -> None:
		self._engine.cancel_current()

	def terminate(self) -> None:
		with self._lock:
			self._cdp_client.close()
			self._process_manager.terminate()

	def _cdp_request(
		self,
		method: str,
		params: dict[str, Any] | None = None,
		timeout: float = 30,
		eventHandler: Callable[[dict[str, Any]], None] | None = None,
		cancelEvent: threading.Event | None = None,
	) -> dict[str, Any]:
		return self._cdp_client.request(
			method,
			params,
			timeout=timeout,
			eventHandler=eventHandler,
			cancelEvent=cancelEvent,
			onCancelCallback=self._engine.send_fast_stop,
		)

	def _send_stop(self) -> None:
		self._engine.send_fast_stop()

	def _wait_until_ready(self, cancelEvent: threading.Event | None = None) -> None:
		self._engine.wait_until_ready(cancelEvent=cancelEvent)

	def _close_websocket(self) -> None:
		self._cdp_client.close()

	def _format_exception(self, exceptionDetails: dict[str, Any]) -> str:
		return self._cdp_client.format_exception(exceptionDetails)

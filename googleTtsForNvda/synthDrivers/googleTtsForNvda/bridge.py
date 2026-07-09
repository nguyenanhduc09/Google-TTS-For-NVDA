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

from .catalog import ENGINE_DIR, VoiceCatalog
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
PERSISTENT_PROFILE_DIR_NAME = "persistentSession"
CONFIG_SECTION = "googleTtsForNvda"
CONFIG_BROWSER_RUNTIME = "browserRuntime"
BROWSER_RUNTIME_EDGE = "edge"
BROWSER_RUNTIME_CHROME = "chrome"
DEFAULT_BROWSER_RUNTIME = BROWSER_RUNTIME_EDGE
BROWSER_RUNTIME_LABELS = {
	BROWSER_RUNTIME_EDGE: "Microsoft Edge",
	BROWSER_RUNTIME_CHROME: "Google Chrome",
}
BROWSER_RUNTIMES = (BROWSER_RUNTIME_EDGE, BROWSER_RUNTIME_CHROME)

if str(WEBSOCKET_CLIENT_DIR) not in sys.path:
	sys.path.insert(1, str(WEBSOCKET_CLIENT_DIR))

import websocket  # type: ignore


class CdpError(Exception):
	def __init__(self, message: str, technicalDetail: str | None = None) -> None:
		super().__init__(message)
		self.technicalDetail = technicalDetail


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
	server_version = "GoogleTtsForNvda/0.1"
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
		log.debug("Google TTS browser runtime detail: %s", technicalDetail)
	return CdpError(message, technicalDetail)


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
		user32.EnumWindows.argtypes = [windowProc, wintypes.LPARAM]
		user32.EnumWindows.restype = wintypes.BOOL
		user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
		user32.GetWindowThreadProcessId.restype = wintypes.DWORD
		user32.IsWindowVisible.argtypes = [wintypes.HWND]
		user32.IsWindowVisible.restype = wintypes.BOOL
		user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
		user32.ShowWindow.restype = wintypes.BOOL

		@windowProc
		def enum_window(hwnd: int, _param: int) -> bool:
			ownerProcessId = wintypes.DWORD()
			user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ownerProcessId))
			if ownerProcessId.value == processId and user32.IsWindowVisible(hwnd):
				user32.ShowWindow(hwnd, 0)
			return True

		user32.EnumWindows(enum_window, 0)
	except Exception:
		log.debug("Could not hide Google TTS Chrome helper window.", exc_info=True)


def _elevate_chrome_priority(processId: int) -> None:
	if os.name != "nt":
		return
	try:
		import ctypes

		ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
		kernel32 = ctypes.windll.kernel32
		handle = kernel32.OpenProcess(0x0200, False, processId)
		if handle:
			kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
			kernel32.CloseHandle(handle)
	except Exception:
		log.debug("Could not elevate Google TTS Chrome process priority.", exc_info=True)


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


def _runtime_fallback_order(runtime: str | None = None) -> tuple[str, str]:
	preferred = _normalize_browser_runtime(runtime)
	fallback = BROWSER_RUNTIME_CHROME if preferred == BROWSER_RUNTIME_EDGE else BROWSER_RUNTIME_EDGE
	return preferred, fallback


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


def browser_runtime_available(runtime: str) -> bool:
	return browser_path_for_runtime(runtime) is not None


def browser_availability() -> dict[str, bool]:
	return {runtime: browser_runtime_available(runtime) for runtime in BROWSER_RUNTIMES}


def browser_runtime_for_path(browserPath: str) -> str:
	exeName = Path(browserPath).name.lower()
	if exeName in ("msedge.exe", "msedge"):
		return BROWSER_RUNTIME_EDGE
	return BROWSER_RUNTIME_CHROME


def effective_browser_runtime(runtime: str | None = None) -> str | None:
	path = find_browser(runtime)
	if path is None:
		return None
	return browser_runtime_for_path(path)


def find_browser(runtime: str | None = None) -> str | None:
	for candidateRuntime in _runtime_fallback_order(runtime or configured_browser_runtime()):
		path = browser_path_for_runtime(candidateRuntime)
		if path:
			return path
	return None


class ChromeTtsBridge:
	def __init__(self, catalog: VoiceCatalog | None = None) -> None:
		self.catalog = catalog or VoiceCatalog.load()
		self._server: _ThreadingTcpServer | None = None
		self._serverThread: threading.Thread | None = None
		self._serverPort: int | None = None
		self._chromeProcess: subprocess.Popen[bytes] | None = None
		self._debugPort: int | None = None
		self._ws: websocket.WebSocket | None = None
		self._lock = threading.RLock()
		self._msgIdLock = threading.Lock()
		self._stopLock = threading.Lock()
		self._msgId = 0
		self._profileDir: Path | None = None
		self._lastStopSentAt = 0.0
		self._runtimeBusy = False

	@classmethod
	def find_chrome(cls) -> str | None:
		return find_browser()

	def ensure_connection(self) -> None:
		with self._lock:
			if self._ws is not None and self._ws.connected:
				return
			try:
				self._start_server()
				self._start_chrome()
				wsUrl = self._get_page_websocket_url()
				self._ws = websocket.create_connection(wsUrl, timeout=15)
				self._ws.settimeout(RECV_POLL_TIMEOUT)
				self._cdp_request("Runtime.enable", timeout=15)
				self._cdp_request("Page.enable", timeout=15)
				self._cdp_request("Runtime.addBinding", {"name": BINDING_NAME}, timeout=15)
				self._wait_until_ready()
			except Exception:
				self._close_websocket()
				raise

	def preload_voice(self, options: dict[str, Any], cancelEvent: threading.Event | None = None) -> dict[str, Any]:
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not voice_store.is_package_installed(package):
			raise _friendly_cdp_error(
				"This Google TTS For NVDA voice package is not installed. Open Google TTS Voice Manager to install it.",
				f"Missing voice package: {package.id}.",
			)
		self.ensure_connection()
		_raise_if_cancelled(cancelEvent)
		payload = {
			"sessionId": f"preload-{time.monotonic_ns()}",
			"voiceName": options["voiceName"],
			"lang": options["lang"],
		}
		response = self._cdp_request(
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
	) -> dict[str, Any]:
		if not text.strip():
			return {"success": True, "empty": True}
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not voice_store.is_package_installed(package):
			raise _friendly_cdp_error(
				"This Google TTS For NVDA voice package is not installed. Open Google TTS Voice Manager to install it.",
				f"Missing voice package: {package.id}.",
			)
		self.ensure_connection()
		_raise_if_cancelled(cancelEvent)
		sessionId = f"{time.monotonic_ns()}"
		payload = {
			"sessionId": sessionId,
			"text": text,
			"voiceName": options["voiceName"],
			"lang": options["lang"],
			"rate": options["rate"],
			"pitch": options["pitch"],
			"volume": options["volume"],
			"outputGain": options.get("outputGain", options["volume"]),
		}
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
					raise CdpCancelled()
				audio = base64.b64decode(str(event.get("data") or ""))
				if audio:
					if cancelEvent is not None and cancelEvent.is_set():
						raise CdpCancelled()
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
				detail = str(event.get("message") or "Chrome TTS failed.")
				raise _friendly_cdp_error(
					"Google TTS For NVDA could not synthesize this text.",
					detail,
				)

		expression = f"window.googleTtsForNvdaSpeak({json.dumps(payload, ensure_ascii=False)})"
		try:
			response = self._cdp_request(
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
			)
		except CdpCancelled:
			raise
		result = response.get("result", {}).get("result", {})
		if result.get("subtype") == "error":
			raise _friendly_cdp_error(
				"Google TTS For NVDA could not start speech in the browser runtime.",
				result.get("description") or "Chrome TTS evaluation failed.",
			)
		value = result.get("value")
		if isinstance(value, dict):
			value.update(state)
			return value
		return state

	def stop_runtime(self) -> None:
		try:
			self.ensure_connection()
			self._cdp_request(
				"Runtime.evaluate",
				{
					"expression": STOP_EXPRESSION,
					"awaitPromise": True,
					"returnByValue": True,
				},
				timeout=5,
			)
		except Exception:
			log.debug("Could not stop Chrome TTS runtime.", exc_info=True)

	def cancel_current(self) -> None:
		if self._runtimeBusy:
			self._send_stop()

	def terminate(self) -> None:
		with self._lock:
			self._close_websocket()
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

	def _start_server(self) -> None:
		if self._server is not None:
			return
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

	def _start_chrome(self, cancelEvent: threading.Event | None = None) -> None:
		if self._chromeProcess is not None and self._chromeProcess.poll() is None:
			if self._debugPort is not None:
				return
			with suppress(Exception):
				self._chromeProcess.terminate()
				self._chromeProcess.wait(timeout=2)
			with suppress(Exception):
				self._chromeProcess.kill()
			self._chromeProcess = None
			self._debugPort = None
		_raise_if_cancelled(cancelEvent)
		chromePath = self.find_chrome()
		if not chromePath:
			raise _friendly_cdp_error(
				"Microsoft Edge or Google Chrome was not found. Install one of them or set EDGE_PATH/CHROME_PATH.",
				"No supported browser runtime executable was found.",
			)
		profileDir = self._get_chrome_profile_dir(chromePath)
		devToolsFile = profileDir / "DevToolsActivePort"
		try:
			devToolsFile.unlink()
		except FileNotFoundError:
			pass
		pageUrl = self._page_url()
		args = [
			chromePath,
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
		self._chromeProcess = subprocess.Popen(
			args,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			**_hidden_chrome_startup_kwargs(),
		)
		_hide_chrome_windows(self._chromeProcess.pid)
		_elevate_chrome_priority(self._chromeProcess.pid)
		try:
			self._debugPort = self._read_devtools_port(devToolsFile, cancelEvent)
			_hide_chrome_windows(self._chromeProcess.pid)
		except Exception:
			with suppress(Exception):
				self._chromeProcess.terminate()
				self._chromeProcess.wait(timeout=2)
			with suppress(Exception):
				self._chromeProcess.kill()
			self._chromeProcess = None
			self._debugPort = None
			self._remove_chrome_profile()
			raise

	def _get_chrome_profile_dir(self, browserPath: str) -> Path:
		if self._profileDir is not None:
			self._profileDir.mkdir(parents=True, exist_ok=True)
			return self._profileDir
		root = self._browser_profile_root(browserPath)
		root.mkdir(parents=True, exist_ok=True)
		self._cleanup_old_chrome_profiles(root)
		profileDir = root / PERSISTENT_PROFILE_DIR_NAME
		reused = profileDir.exists()
		profileDir.mkdir(parents=True, exist_ok=True)
		for lockName in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
			with suppress(OSError):
				(profileDir / lockName).unlink()
		with suppress(OSError):
			(profileDir / "DevToolsActivePort").unlink()
		self._profileDir = profileDir
		log.debug(
			"Google TTS browser profile directory: %s (reused=%s)",
			profileDir, reused,
		)
		return self._profileDir

	def _browser_profile_root(self, browserPath: str) -> Path:
		base = os.environ.get("LOCALAPPDATA")
		root = Path(base) if base else Path(tempfile.gettempdir())
		return root / LOCAL_CACHE_DIR_NAME / self._browser_profile_dir_name(browserPath)

	def _browser_profile_dir_name(self, browserPath: str) -> str:
		exeName = Path(browserPath).name.lower()
		if exeName in ("msedge.exe", "msedge"):
			return EDGE_PROFILE_DIR_NAME
		return CHROME_PROFILE_DIR_NAME

	def _cleanup_old_chrome_profiles(self, root: Path) -> None:
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
				totalSize = sum(
					f.stat().st_size for f in persistent.rglob("*") if f.is_file()
				)
				if totalSize > 500 * 1024 * 1024:  # 500 MB
					log.debug(
						"Persistent Chrome profile exceeds 500 MB (%d bytes), resetting.",
						totalSize,
					)
					shutil.rmtree(persistent, ignore_errors=True)
			except OSError:
				pass

	def _release_chrome_profile(self) -> None:
		"""Release the profile directory reference without deleting it.

		Preserves the persistent profile (including Chrome's compiled WASM
		code cache) so the next startup can skip WASM recompilation.  Only
		transient files (lock files, DevToolsActivePort) are cleaned up.
		"""
		profileDir = self._profileDir
		self._profileDir = None
		if profileDir is None:
			return
		for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
					 "lockfile", "DevToolsActivePort"):
			with suppress(OSError):
				(profileDir / name).unlink()

	def _remove_chrome_profile(self) -> None:
		"""Fully delete the profile directory (used on Chrome startup failure)."""
		profileDir = self._profileDir
		self._profileDir = None
		if profileDir is None:
			return
		try:
			shutil.rmtree(profileDir, ignore_errors=True)
		except OSError:
			log.debug("Could not remove Google TTS Chrome session profile.", exc_info=True)

	def _read_devtools_port(self, devToolsFile: Path, cancelEvent: threading.Event | None = None) -> int:
		for _ in range(400):
			_raise_if_cancelled(cancelEvent)
			if self._chromeProcess is not None:
				_hide_chrome_windows(self._chromeProcess.pid)
			if self._chromeProcess is not None and self._chromeProcess.poll() is not None:
				exitCode = self._chromeProcess.returncode
				self._chromeProcess = None
				if exitCode == 21:
					raise _friendly_cdp_error(
						"The browser profile used by Google TTS For NVDA is already in use. Restart NVDA, or close any leftover Microsoft Edge or Google Chrome helper processes.",
						"Browser runtime exited with profile-in-use code 21.",
					)
				raise _friendly_cdp_error(
					"The browser runtime closed before Google TTS For NVDA was ready.",
					f"Browser runtime exited before DevTools became available: {exitCode}",
				)
			if devToolsFile.is_file():
				lines = devToolsFile.read_text(encoding="utf-8").splitlines()
				if lines:
					return int(lines[0])
			time.sleep(STARTUP_POLL_INTERVAL)
		raise _friendly_cdp_error(
			"Timed out waiting for the browser runtime to start.",
			"Timed out waiting for Chrome DevTools.",
		)

	def _page_url(self) -> str:
		if self._serverPort is None:
			raise _friendly_cdp_error(
				"Google TTS For NVDA could not start its local browser bridge.",
				"Bridge HTTP server is not running.",
			)
		return f"http://127.0.0.1:{self._serverPort}/"

	def _get_page_websocket_url(self, cancelEvent: threading.Event | None = None) -> str:
		pageUrl = self._page_url()
		if self._debugPort is None:
			raise _friendly_cdp_error(
				"The browser runtime is not ready yet.",
				"Chrome DevTools port is not ready.",
			)
		for _ in range(200):
			_raise_if_cancelled(cancelEvent)
			if self._chromeProcess is not None:
				_hide_chrome_windows(self._chromeProcess.pid)
			try:
				targets = _read_json_endpoint(self._debugPort, "/json/list", timeout=0.5)
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
			"Google TTS For NVDA could not find its speech page in the browser runtime.",
			"Could not find Chrome TTS page target.",
		)

	def _next_msg_id(self) -> int:
		with self._msgIdLock:
			self._msgId += 1
			return self._msgId

	def _cdp_request(
		self,
		method: str,
		params: dict[str, Any] | None = None,
		timeout: float = 30,
		eventHandler: Callable[[dict[str, Any]], None] | None = None,
		cancelEvent: threading.Event | None = None,
	) -> dict[str, Any]:
		if self._ws is None:
			raise _friendly_cdp_error(
				"Google TTS For NVDA is not connected to the browser runtime.",
				"Chrome DevTools websocket is not connected.",
			)
		msgId = self._next_msg_id()
		command = {"id": msgId, "method": method, "params": params or {}}
		with self._lock:
			self._runtimeBusy = cancelEvent is not None
			try:
				self._ws.send(json.dumps(command))
				deadline = time.monotonic() + timeout
				while time.monotonic() < deadline:
					if cancelEvent is not None and cancelEvent.is_set():
						self._send_stop()
						while True:
							try:
								self._ws.recv()
							except websocket.WebSocketTimeoutException:
								break
							except Exception:
								break
						raise CdpCancelled()
					try:
						rawMessage = self._ws.recv()
					except websocket.WebSocketTimeoutException:
						continue
					if not rawMessage:
						raise _friendly_cdp_error(
							"The browser runtime connection closed unexpectedly.",
							"Chrome DevTools websocket closed.",
						)
					message = json.loads(rawMessage)
					if eventHandler is not None:
						eventHandler(message)
					if message.get("id") != msgId:
						continue
					if "error" in message:
						raise _friendly_cdp_error(
							"The browser runtime reported an error while processing speech.",
							f"CDP error for {method}: {message['error']}",
						)
					exceptionDetails = message.get("result", {}).get("exceptionDetails")
					if isinstance(exceptionDetails, dict):
						raise _friendly_cdp_error(
							"The browser runtime reported an error while preparing speech.",
							self._format_exception(exceptionDetails),
						)
					return message
			finally:
				self._runtimeBusy = False
		raise _friendly_cdp_error(
			"Timed out waiting for the browser runtime to respond.",
			f"Timed out waiting for {method}.",
		)

	def _send_stop(self) -> None:
		ws = self._ws
		if ws is None or not ws.connected:
			return
		with self._stopLock:
			now = time.monotonic()
			if now - self._lastStopSentAt < 0.02:
				return
			self._lastStopSentAt = now
			try:
				command = {
					"id": self._next_msg_id(),
					"method": "Runtime.evaluate",
					"params": {
						"expression": STOP_EXPRESSION,
						"awaitPromise": False,
						"returnByValue": True,
					},
				}
				ws.send(json.dumps(command))
			except Exception:
				log.debug("Could not send fast Chrome TTS stop command.", exc_info=True)

	def _wait_until_ready(self, cancelEvent: threading.Event | None = None) -> None:
		expression = """
		typeof window.googleTtsForNvdaSpeak === "function"
		&& typeof window.googleTtsForNvdaPreload === "function"
		&& typeof window.googleTtsForNvdaBridge === "function"
		&& typeof window.googleTtsForNvdaReady === "function"
		&& window.googleTtsForNvdaReady() === true
		"""
		for _ in range(400):
			_raise_if_cancelled(cancelEvent)
			response = self._cdp_request(
				"Runtime.evaluate",
				{"expression": expression, "returnByValue": True},
				timeout=5,
				cancelEvent=cancelEvent,
			)
			if response.get("result", {}).get("result", {}).get("value") is True:
				return
			time.sleep(STARTUP_POLL_INTERVAL)
		raise _friendly_cdp_error(
			"Google TTS For NVDA could not finish loading the browser speech engine.",
			"Chrome TTS harness did not finish loading.",
		)

	def _close_websocket(self) -> None:
		if self._ws is None:
			return
		try:
			self._ws.close()
		except Exception:
			pass
		self._ws = None

	def _format_exception(self, exceptionDetails: dict[str, Any]) -> str:
		exception = exceptionDetails.get("exception")
		if isinstance(exception, dict) and exception.get("description"):
			return str(exception["description"])
		if exceptionDetails.get("text"):
			return str(exceptionDetails["text"])
		return "Chrome DevTools runtime exception."

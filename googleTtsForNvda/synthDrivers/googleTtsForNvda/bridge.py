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

from .catalog import ENGINE_DIR, VoiceCatalog
from . import voice_store


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
WEBSOCKET_CLIENT_DIR = BASE_DIR / "websocketClientRepo"
BINDING_NAME = "googleTtsForNvdaBridge"
SAMPLE_RATE = 24000
RECV_POLL_TIMEOUT = 0.005
STARTUP_POLL_INTERVAL = 0.05
STOP_EXPRESSION = "window.googleTtsForNvdaStop && window.googleTtsForNvdaStop()"

if str(WEBSOCKET_CLIENT_DIR) not in sys.path:
	sys.path.insert(1, str(WEBSOCKET_CLIENT_DIR))

import websocket  # type: ignore


class CdpError(Exception):
	pass


class CdpCancelled(Exception):
	pass


AudioCallback = Callable[[bytes], None]


class _ThreadingTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
	allow_reuse_address = True
	daemon_threads = True


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
		candidates = [os.environ.get("CHROME_PATH", "")]
		if winreg is not None:
			registryKeys = [
				(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
				(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
			]
			for hive, subKey in registryKeys:
				try:
					with winreg.OpenKey(hive, subKey) as key:
						path, _ = winreg.QueryValueEx(key, "")
						candidates.append(path)
				except OSError:
					pass
		candidates.extend(
			[
				str(Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
				str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
				str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
				shutil.which("chrome.exe") or "",
				shutil.which("chrome") or "",
			],
		)
		for candidate in candidates:
			if candidate and Path(candidate).is_file():
				return candidate
		return None

	def ensure_connection(self, cancelEvent: threading.Event | None = None) -> None:
		with self._lock:
			_raise_if_cancelled(cancelEvent)
			if self._ws is not None and self._ws.connected:
				return
			try:
				self._start_server()
				_raise_if_cancelled(cancelEvent)
				self._start_chrome(cancelEvent)
				_raise_if_cancelled(cancelEvent)
				wsUrl = self._get_page_websocket_url(cancelEvent)
				_raise_if_cancelled(cancelEvent)
				self._ws = websocket.create_connection(wsUrl, timeout=15)
				self._ws.settimeout(RECV_POLL_TIMEOUT)
				self._cdp_request("Runtime.enable", timeout=15, cancelEvent=cancelEvent)
				self._cdp_request("Page.enable", timeout=15, cancelEvent=cancelEvent)
				self._cdp_request("Runtime.addBinding", {"name": BINDING_NAME}, timeout=15, cancelEvent=cancelEvent)
				self._wait_until_ready(cancelEvent)
			except CdpCancelled:
				self._close_websocket()
				raise

	def preload_voice(self, options: dict[str, Any], cancelEvent: threading.Event | None = None) -> dict[str, Any]:
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not voice_store.is_package_installed(package):
			raise CdpError(f"Google TTS voice package is not installed: {package.id}")
		self.ensure_connection(cancelEvent)
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
	) -> dict[str, Any]:
		if not text.strip():
			return {"success": True, "empty": True}
		package = self.catalog.package_for_voice(str(options["voiceId"]))
		if not voice_store.is_package_installed(package):
			raise CdpError(f"Google TTS voice package is not installed: {package.id}")
		self.ensure_connection(cancelEvent)
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
					"Google TTS session %s started in Chrome after %.1f ms.",
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
			elif eventType == "done":
				state["done"] = True
			elif eventType == "error":
				raise CdpError(str(event.get("message") or "Chrome TTS failed."))

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
			raise CdpError(result.get("description") or "Chrome TTS evaluation failed.")
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
				try:
					self._chromeProcess.terminate()
					self._chromeProcess.wait(timeout=5)
				except subprocess.TimeoutExpired:
					self._chromeProcess.kill()
				except Exception:
					pass
			self._chromeProcess = None
			self._debugPort = None
			if self._server is not None:
				self._server.shutdown()
				self._server.server_close()
			self._server = None
			self._serverThread = None
			self._serverPort = None
			self._remove_chrome_profile()

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
			return
		_raise_if_cancelled(cancelEvent)
		chromePath = self.find_chrome()
		if not chromePath:
			raise CdpError("Google Chrome was not found. Install Chrome or set CHROME_PATH.")
		profileDir = self._get_chrome_profile_dir()
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
			"--noerrdialogs",
			"--autoplay-policy=no-user-gesture-required",
			pageUrl,
		]
		self._chromeProcess = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		try:
			self._debugPort = self._read_devtools_port(devToolsFile, cancelEvent)
		except CdpCancelled:
			with suppress(Exception):
				self._chromeProcess.terminate()
				self._chromeProcess.wait(timeout=2)
			self._chromeProcess = None
			self._debugPort = None
			self._remove_chrome_profile()
			raise

	def _get_chrome_profile_dir(self) -> Path:
		if self._profileDir is not None:
			self._profileDir.mkdir(parents=True, exist_ok=True)
			return self._profileDir
		root = voice_store.data_root() / "chromeProfiles"
		root.mkdir(parents=True, exist_ok=True)
		self._cleanup_old_chrome_profiles(root)
		self._profileDir = root / f"session-{os.getpid()}-{time.monotonic_ns()}"
		self._profileDir.mkdir(parents=True, exist_ok=True)
		return self._profileDir

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

	def _remove_chrome_profile(self) -> None:
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
			if self._chromeProcess is not None and self._chromeProcess.poll() is not None:
				exitCode = self._chromeProcess.returncode
				self._chromeProcess = None
				if exitCode == 21:
					raise CdpError("The Google TTS Chrome profile is already in use.")
				raise CdpError(f"Chrome exited before DevTools became available: {exitCode}")
			if devToolsFile.is_file():
				lines = devToolsFile.read_text(encoding="utf-8").splitlines()
				if lines:
					return int(lines[0])
			time.sleep(STARTUP_POLL_INTERVAL)
		raise CdpError("Timed out waiting for Chrome DevTools.")

	def _page_url(self) -> str:
		if self._serverPort is None:
			raise CdpError("Bridge HTTP server is not running.")
		return f"http://127.0.0.1:{self._serverPort}/"

	def _get_page_websocket_url(self, cancelEvent: threading.Event | None = None) -> str:
		pageUrl = self._page_url()
		if self._debugPort is None:
			raise CdpError("Chrome DevTools port is not ready.")
		for _ in range(200):
			_raise_if_cancelled(cancelEvent)
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
		raise CdpError("Could not find Chrome TTS page target.")

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
			raise CdpError("Chrome DevTools websocket is not connected.")
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
						raise CdpCancelled()
					try:
						rawMessage = self._ws.recv()
					except websocket.WebSocketTimeoutException:
						continue
					if not rawMessage:
						raise CdpError("Chrome DevTools websocket closed.")
					message = json.loads(rawMessage)
					if eventHandler is not None:
						eventHandler(message)
					if message.get("id") != msgId:
						continue
					if "error" in message:
						raise CdpError(f"CDP error for {method}: {message['error']}")
					exceptionDetails = message.get("result", {}).get("exceptionDetails")
					if isinstance(exceptionDetails, dict):
						raise CdpError(self._format_exception(exceptionDetails))
					return message
			finally:
				self._runtimeBusy = False
		raise CdpError(f"Timed out waiting for {method}.")

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
		raise CdpError("Chrome TTS harness did not finish loading.")

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

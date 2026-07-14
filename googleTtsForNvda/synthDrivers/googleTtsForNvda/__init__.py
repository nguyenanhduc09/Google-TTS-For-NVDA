# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import suppress
import os
import json
import re
import threading
import time
import unicodedata
from typing import Any

import addonHandler
import config
import globalVars
import synthDriverHandler
import wx
from autoSettingsUtils.driverSetting import DriverSetting
from logHandler import log
from nvwave import WavePlayer
from speech.commands import BreakCommand, IndexCommand, LangChangeCommand, PitchCommand, RateCommand, VolumeCommand
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached

from .bridge import (
	CdpCancelled,
	ChromeTtsBridge,
	CONFIG_AUTO_LANGUAGE_CANDIDATES,
	CONFIG_AUTO_LANGUAGE_DETECTION,
	CONFIG_AUTO_LANGUAGE_PREFERRED,
	CONFIG_AUTO_LANGUAGE_PROFILES,
	CONFIG_SECTION,
	DEFAULT_AUTO_LANGUAGE_CANDIDATES,
	DEFAULT_AUTO_LANGUAGE_DETECTION,
	DEFAULT_AUTO_LANGUAGE_PREFERRED,
	DEFAULT_AUTO_LANGUAGE_PROFILES,
	SAMPLE_RATE,
)
from .catalog import EngineLibraryError, VoiceCatalog
from . import language_detector, voice_store


addonHandler.initTranslation()


_SHORT_CACHE_MAX_CHARS = 5000
_SHORT_CACHE_MAX_ITEMS = 4096
_SHORT_CACHE_MAX_BYTES = 200 * 1024 * 1024
_OUTPUT_GAIN_MAKEUP = 2.0
_PROTECTED_ENGINE_RATE = 1.0
_MIN_ARTIFICIAL_RATE = 0.5
_MAX_ARTIFICIAL_RATE = 2.2
_SpeechRequest = tuple[list[Any], str, int, bool, int, int, threading.Event]
_IndexMarker = tuple[Any, int]
_FAST_FIRST_SEGMENT_MIN_CHARS = 30
_REGULAR_SEGMENT_MIN_CHARS = 110
_FAST_FIRST_SEGMENT_MAX_CHARS = 64
_REGULAR_SEGMENT_MAX_CHARS = 180
_SEAMLESS_UTTERANCE_MAX_CHARS = 900
_FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS = 30
_FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS = 90
_FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD = 40
_SOFT_PHRASE_SEGMENT_MIN_CHARS = 80
_SOFT_PHRASE_SEGMENT_MAX_CHARS = 170
_SOFT_PHRASE_SEGMENT_LOOKAHEAD = 40
_UI_SUMMARY_SEGMENT_MIN_CHARS = 90
_UI_SUMMARY_SEGMENT_MAX_CHARS = 135
_UI_SUMMARY_SEGMENT_LOOKAHEAD = 30
_UI_BOUNDARY_SEGMENT_MIN_CHARS = 45
_UI_BOUNDARY_SEGMENT_MAX_CHARS = 140
_UI_BOUNDARY_LOOKAHEAD = 45
_URL_TOKEN_SEGMENT_MAX_CHARS = 220
_FORCED_SEGMENT_MIN_CHARS = 32
_FORCED_SEGMENT_FORWARD_LOOKAHEAD = 24
_FORCED_SEGMENT_HARD_MAX_CHARS = 256
_VOICE_WARMUP_TEXT = "a"
_AUTO_LANGUAGE_NOTICE_ID = "notice"
_AUTO_DETECT_MIN_SCORE = 2
_AUTO_DETECT_MIN_MARGIN = 1


class ReadOnlyTextDriverSetting(DriverSetting):
	"""Marker setting rendered as a read-only edit field by the global plugin."""

	readOnlyText = True

_COMMON_ABBREVIATIONS = {
	# English
	"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "gen", "col", "maj", "capt", "lt", "sgt",
	"hon", "gov", "sen", "rep", "esq", "vs", "etc", "inc", "ltd", "co", "corp", "no", "fig", "eq",
	"vol", "ch", "p", "pp", "sec", "min", "max", "approx", "est", "dept", "dist", "ave", "blvd", "rd",
	"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
	"ph", "phd", "md", "ba", "ma", "bsc", "msc", "jd", "llb", "llm",
	# German
	"usw", "bzw", "ca", "evtl", "ggf", "inkl", "nr", "ing", "mag",
	# French
	"mme", "mlle", "mgr", "ex",
	# Spanish / Portuguese
	"sra", "srta", "dra", "profa", "num", "pag", "cap", "ej", "av",
	# Vietnamese
	"tp", "ths", "ts", "gs", "pgs", "bs", "ks", "cn", "tx", "tt", "qd", "nd",
	# Russian / Cyrillic
	"ул", "им", "обл", "рис", "см", "стр", "тд", "тп", "пр", "руб", "коп", "тыс", "млн", "млрд", "др", "г", "гор", "пер", "пл", "просп",
}

_SENTENCE_TERMINATOR_RE = re.compile(
	r"([。！？；｡।॥؟։።፧፨]+|[.!?;]+)"
	r"(['\"\)\]\}”’」』）》〉»\u2018-\u201F\u3009\u300B\u300D\u300F\u3011\uFF09\uFF3D\uFF5D]*)"
	r"(\s*)"
)
_LANGUAGE_WORD_RE = re.compile(r"[^\W\d_]+(?:['’_-][^\W\d_]+)?", re.UNICODE)
_VIETNAMESE_LETTERS = set(
	"ăâđêôơư"
	"áàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệ"
	"íìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)
_VIETNAMESE_WORDS = {
	"anh", "ban", "bạn", "bao", "bi", "bị", "bo", "bỏ", "cai", "cái", "cac", "các", "can", "cần",
	"cau", "câu", "cho", "co", "có", "con", "cua", "của", "cung", "cùng", "dang", "đang", "de", "để",
	"den", "đến", "di", "đi", "do", "đó", "duoc", "được", "hay", "hon", "hơn", "khi", "khong",
	"không", "la", "là", "lam", "làm", "len", "lên", "mot", "một", "nay", "này", "neu", "nếu",
	"nguoi", "người", "nhung", "những", "o", "ở", "qua", "ra", "rang", "rằng", "roi", "rồi", "sau",
	"se", "sẽ", "thi", "thì", "toi", "tôi", "trong", "tu", "từ", "va", "và", "vao", "vào", "ve",
	"về", "vi", "vì", "voi", "với",
}
_ENGLISH_WORDS = {
	"a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at", "be", "because",
	"been", "before", "between", "browser", "but", "by", "can", "chrome", "click", "could", "did",
	"do", "does", "download", "edge", "for", "from", "has", "have", "if", "in", "install", "is",
	"it", "language", "more", "not", "of", "on", "open", "or", "package", "press", "runtime", "select",
	"settings", "speech", "than", "that", "the", "then", "there", "this", "to", "use", "voice", "was",
	"were", "when", "will", "with", "you", "your",
}
_LATIN_SCRIPT_RANGES = ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F), (0x1E00, 0x1EFF))
_LATIN_SCRIPT_ROOTS = {
	"bs", "ca", "cs", "cy", "da", "de", "en", "es", "et", "fi", "fil", "fr", "hr", "hu", "id",
	"is", "it", "jv", "lt", "lv", "ms", "nb", "nl", "pl", "pt", "ro", "sk", "sl", "sq", "sr",
	"su", "sv", "sw", "tr", "vi",
}
_LANGUAGE_SCRIPT_RANGES = {
	"ar": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
	"as": ((0x0980, 0x09FF),),
	"bn": ((0x0980, 0x09FF),),
	"brx": ((0x0900, 0x097F),),
	"bg": ((0x0400, 0x052F),),
	"cmn": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
	"doi": ((0x0900, 0x097F),),
	"el": ((0x0370, 0x03FF),),
	"gu": ((0x0A80, 0x0AFF),),
	"he": ((0x0590, 0x05FF),),
	"hi": ((0x0900, 0x097F),),
	"ja": ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
	"km": ((0x1780, 0x17FF),),
	"kn": ((0x0C80, 0x0CFF),),
	"ko": ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)),
	"kok": ((0x0900, 0x097F),),
	"ks": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0x0900, 0x097F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
	"mai": ((0x0900, 0x097F),),
	"ml": ((0x0D00, 0x0D7F),),
	"mni": ((0x0980, 0x09FF), (0xABC0, 0xABFF)),
	"mr": ((0x0900, 0x097F),),
	"ne": ((0x0900, 0x097F),),
	"or": ((0x0B00, 0x0B7F),),
	"pa": ((0x0A00, 0x0A7F),),
	"ru": ((0x0400, 0x052F),),
	"sa": ((0x0900, 0x097F),),
	"sat": ((0x1C50, 0x1C7F),),
	"sd": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0x0900, 0x097F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
	"si": ((0x0D80, 0x0DFF),),
	"sr": ((0x0400, 0x052F),),
	"ta": ((0x0B80, 0x0BFF),),
	"te": ((0x0C00, 0x0C7F),),
	"th": ((0x0E00, 0x0E7F),),
	"uk": ((0x0400, 0x052F),),
	"ur": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
	"yue": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
	"zh": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
}


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "googleTtsForNvda"
	description = _("Google TTS For NVDA")
	_STANDARD_SUPPORTED_SETTINGS = (
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		synthDriverHandler.SynthDriver.RateBoostSetting(),
		synthDriverHandler.SynthDriver.PitchSetting(),
		synthDriverHandler.SynthDriver.VolumeSetting(),
	)
	_AUTO_LANGUAGE_NOTICE_SETTING = ReadOnlyTextDriverSetting(
		_AUTO_LANGUAGE_NOTICE_ID,
		_("Auto-detect status"),
		availableInSettingsRing=True,
		useConfig=False,
		defaultVal=_AUTO_LANGUAGE_NOTICE_ID,
	)
	supportedCommands = {
		BreakCommand,
		IndexCommand,
		LangChangeCommand,
		RateCommand,
		PitchCommand,
		VolumeCommand,
	}
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}
	cachePropertiesByDefault = False

	@property
	def supportedSettings(self) -> tuple[Any, ...]:
		if self._auto_language_detection_enabled():
			return (self._AUTO_LANGUAGE_NOTICE_SETTING,)
		return self._STANDARD_SUPPORTED_SETTINGS

	@classmethod
	def check(cls) -> bool:
		# Keep the driver visible; runtime dependencies are validated when selected.
		return True

	def __init__(self) -> None:
		super().__init__()
		try:
			fullCatalog = VoiceCatalog.load()
		except EngineLibraryError as exc:
			wx.CallAfter(self._show_engine_library_error, exc)
			raise RuntimeError(self._engine_library_error_message(exc)) from exc
		installedPackages = voice_store.installed_packages(fullCatalog)
		if not installedPackages:
			# Defer UI until after this constructor aborts so synth startup is
			# not blocked by a modal dialog waiting for user input.
			wx.CallAfter(self._prompt_for_voice_install)
			raise RuntimeError(
				_(
					"No Google TTS For NVDA voice packages are installed. "
					"Open Google TTS Voice Manager to download a voice package."
				)
			)
		self.catalog = VoiceCatalog(installedPackages)
		if not self.catalog.speakers:
			wx.CallAfter(
				self._prompt_for_voice_install,
				_(
					"No installed Google TTS For NVDA voices can be used.\n\n"
					"Press OK to open Google TTS Voice Manager and install another voice package.\n"
					"Press Cancel to keep using your current synthesizer for now."
				),
			)
			raise RuntimeError(
				_(
					"The installed Google TTS For NVDA voice packages do not include any voices this engine can use. "
					"Open Google TTS Voice Manager to install another voice package."
				)
			)
		if ChromeTtsBridge.find_chrome() is None:
			wx.CallAfter(self._show_missing_chrome_error)
			raise RuntimeError(
				_(
					"Microsoft Edge or Google Chrome was not found. "
					"Install one of them, or set EDGE_PATH/CHROME_PATH to a browser executable."
				)
			)
		self.availableVoices = self._build_available_voices()
		self.availableLanguages = {speaker.language for speaker in self.catalog.speakers}
		self._bridge = ChromeTtsBridge(self.catalog)
		self._playerOutputDevice = self._current_output_device()
		self._player = self._create_wave_player(self._playerOutputDevice)
		self._speechCondition = threading.Condition()
		self._speechQueue: deque[_SpeechRequest] = deque()
		self._activeCancelEvent: threading.Event | None = None
		self._shutdownEvent = threading.Event()
		self._cacheLock = threading.RLock()
		self._shortAudioCache: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
		self._shortAudioCacheBytes = 0
		self._audioChunksSinceDeviceCheck = 0
		self._worker = threading.Thread(
			name="googleTtsForNvda.speech",
			target=self._speech_loop,
			daemon=True,
		)
		self._worker.start()
		self.__voice = self._initial_voice()
		self._rate = 50
		self._rateBoost = False
		self._pitch = 50
		self._volume = 100
		self._warmupThread: threading.Thread | None = None
		self._warmupCancelEvent = threading.Event()
		self._warm_current_voice_async()

	def _prompt_for_voice_install(self, message: str | None = None) -> None:
		# Fallback for direct synth-driver loads when the global plugin did
		# not intercept synth selection before this constructor was reached.
		def prompt_when_ready(retries: int = 200) -> None:
			if retries <= 0:
				return
			for win in wx.GetTopLevelWindows():
				if not win.IsShown():
					continue
				clsName = win.__class__.__name__
				# Wait if there is an active MessageDialog (NVDA error dialog)
				# or any modal dialog other than settings/voice manager dialogs.
				if "MessageDialog" in clsName:
					wx.CallLater(150, prompt_when_ready, retries - 1)
					return
				if isinstance(win, wx.Dialog) and getattr(win, "IsModal", lambda: False)():
					if not any(known in clsName for known in ("SettingsDialog", "SynthesizerDialog", "VoiceManagerDialog")):
						wx.CallLater(150, prompt_when_ready, retries - 1)
						return
			try:
				import gui
				from globalPlugins.googleTtsForNvda import open_voice_manager_download_tab

				answer = gui.messageBox(
					message or _(
						"No Google TTS For NVDA voices are installed.\n\n"
						"Press OK to open Google TTS Voice Manager and download a voice package.\n"
						"Press Cancel to keep using your current synthesizer for now.\n\n"
						"You can also open Voice Manager later from NVDA Menu > Tools > "
						"Google TTS Voice Manager, or press NVDA+Ctrl+Shift+G."
					),
					_("Google TTS For NVDA"),
					wx.OK | wx.CANCEL | wx.ICON_INFORMATION,
					gui.mainFrame,
				)
				if answer == getattr(wx, "ID_OK", wx.OK) or answer == wx.OK:
					open_voice_manager_download_tab()
			except Exception:
				log.exception("Could not show Google TTS voice install prompt.", exc_info=True)

		# Start checking after 250ms to allow NVDA to catch the RuntimeError,
		# restore the fallback synthesizer, and display its own warning message box.
		wx.CallLater(250, prompt_when_ready)

	def _engine_library_error_message(self, error: EngineLibraryError) -> str:
		if error.kind == "unsupportedVersion":
			found = ", ".join(error.foundVersions) if error.foundVersions else _("another version")
			return _(
				"Google TTS For NVDA could not be loaded because the WASM TTS Engine version is not supported.\n\n"
				"This add-on supports WASM TTS Engine version {supported}, but found: {found}.\n\n"
				"Install a Google TTS For NVDA package that includes the supported WASM TTS Engine."
			).format(supported=error.supportedVersion, found=found)
		if error.kind == "missing":
			return _(
				"Google TTS For NVDA could not be loaded because the WASM TTS Engine library is missing.\n\n"
				"Reinstall Google TTS For NVDA with the included WASM TTS Engine library."
			)
		if error.kind == "incomplete":
			return _(
				"Google TTS For NVDA could not be loaded because the WASM TTS Engine library is incomplete.\n\n"
				"Reinstall Google TTS For NVDA with the complete WASM TTS Engine library."
			)
		return _(
			"Google TTS For NVDA could not be loaded because the WASM TTS Engine voice catalog could not be read.\n\n"
			"Reinstall Google TTS For NVDA with a supported WASM TTS Engine library."
		)

	def _show_engine_library_error(self, error: EngineLibraryError) -> None:
		try:
			import gui

			log.error("Google TTS WASM TTS Engine error: %s", error.technicalDetail)
			gui.messageBox(
				self._engine_library_error_message(error),
				_("Google TTS For NVDA"),
				wx.OK | wx.ICON_ERROR,
				gui.mainFrame,
			)
		except Exception:
			log.exception("Could not show Google TTS WASM TTS Engine error.", exc_info=True)

	def _show_missing_chrome_error(self) -> None:
		try:
			import gui

			gui.messageBox(
				_("Microsoft Edge or Google Chrome was not found. Install one of them, or set EDGE_PATH/CHROME_PATH to a browser executable."),
				_("Google TTS For NVDA"),
				wx.OK | wx.ICON_ERROR,
				gui.mainFrame,
			)
		except Exception:
			log.exception("Could not show supported browser missing message.", exc_info=True)

	def terminate(self) -> None:
		with suppress(Exception):
			self._warmupCancelEvent.set()
		self.cancel()
		self._shutdownEvent.set()
		with self._speechCondition:
			self._speechCondition.notify_all()
		with suppress(Exception):
			self._bridge.terminate()
		with suppress(Exception):
			self._player.close()

	def speak(self, speechSequence: list[Any]) -> None:
		sequence = list(speechSequence)
		cancelEvent = threading.Event()
		voice = self.__voice
		rate = self._rate
		rateBoost = self._rateBoost
		pitch = self._pitch
		volume = self._volume
		with suppress(Exception):
			self._warmupCancelEvent.set()
		with self._speechCondition:
			if self._shutdownEvent.is_set():
				return
			self._speechQueue.append((sequence, voice, rate, rateBoost, pitch, volume, cancelEvent))
			self._speechCondition.notify()

	def cancel(self) -> None:
		with self._speechCondition:
			if self._activeCancelEvent is not None:
				self._activeCancelEvent.set()
			for request in self._speechQueue:
				request[-1].set()
			self._speechQueue.clear()
			self._speechCondition.notify_all()
		with suppress(Exception):
			self._player.stop()

	def pause(self, switch: bool) -> None:
		self._player.pause(switch)

	def _current_output_device(self) -> str:
		try:
			return str(config.conf["audio"]["outputDevice"])
		except Exception:
			return getattr(WavePlayer, "DEFAULT_DEVICE_KEY", "default")

	def _create_wave_player(self, outputDevice: str) -> WavePlayer:
		try:
			return WavePlayer(
				channels=1,
				samplesPerSec=SAMPLE_RATE,
				bitsPerSample=16,
				outputDevice=outputDevice,
			)
		except TypeError:
			return WavePlayer(
				channels=1,
				samplesPerSec=SAMPLE_RATE,
				bitsPerSample=16,
			)

	def _ensure_current_output_device(self) -> None:
		outputDevice = self._current_output_device()
		if outputDevice == self._playerOutputDevice:
			return
		with suppress(Exception):
			self._player.close()
		self._playerOutputDevice = outputDevice
		self._player = self._create_wave_player(outputDevice)

	def _build_available_voices(self) -> "OrderedDict[str, VoiceInfo]":
		voices: OrderedDict[str, VoiceInfo] = OrderedDict()
		for speaker in self.catalog.speakers:
			label = f"{speaker.name} ({speaker.language})"
			voices[speaker.id] = VoiceInfo(speaker.id, label, speaker.language)
		return voices

	def _initial_voice(self) -> str:
		try:
			configured = config.conf["speech"][self.name]["voice"]
			if configured in self.availableVoices:
				return configured
		except Exception:
			pass
		for speaker in self.catalog.speakers:
			if speaker.language == "en-US":
				return speaker.id
		return next(iter(self.availableVoices))

	def _iter_speech_chunks(
		self,
		speechSequence: list[Any],
		voice: str,
		rate: int,
		rateBoost: bool,
		pitch: int,
		volume: int,
		cancelEvent: threading.Event,
	) -> Iterator[tuple[str, Any]]:
		textParts: list[str] = []
		textCharCount = 0
		pendingIndexes: list[_IndexMarker] = []
		firstTextSegment = True
		activeVoice = voice
		activeLanguage: str | None = None

		def flush_text() -> Iterator[tuple[str, Any]]:
			nonlocal firstTextSegment, textCharCount, pendingIndexes
			rawText = "".join(textParts)
			textParts.clear()
			textCharCount = 0
			sanitizedText = self._sanitize_speech_text(rawText)
			leftTrimmed = len(sanitizedText) - len(sanitizedText.lstrip())
			text = sanitizedText.strip()
			indexes = [
				(index, max(0, min(len(text), charOffset - leftTrimmed)))
				for index, charOffset in pendingIndexes
			]
			pendingIndexes = []
			if not text:
				for index, _charOffset in indexes:
					if cancelEvent.is_set():
						return
					yield ("index", index)
				return
			segments = list(self._iter_indexed_text_segments(text, indexes, firstTextSegment))
			groupedSegments: list[tuple[str, list[_IndexMarker]]] = []

			def flush_grouped_segments() -> Iterator[tuple[str, Any]]:
				nonlocal firstTextSegment
				if not groupedSegments:
					return
				rawSegments = [segment for segment, _segmentIndexes in groupedSegments]
				spokenSegments = self._spoken_bridge_segments(rawSegments)
				textGroup = "".join(spokenSegments)
				hiddenSegments = spokenSegments if len(spokenSegments) > 1 else None
				groupIndexes: list[_IndexMarker] = []
				charOffset = 0
				for spokenSegment, (_rawSegment, segmentIndexes) in zip(spokenSegments, groupedSegments):
					for index, indexOffset in segmentIndexes:
						groupIndexes.append((index, charOffset + indexOffset))
					charOffset += len(spokenSegment)
				groupProfile = self._auto_detect_profile_for_text(
					textGroup,
					activeVoice,
					activeLanguage,
					voice,
					rate,
					rateBoost,
					pitch,
					volume,
				)
				options = self._speech_options(
					groupProfile["rate"],
					groupProfile["pitch"],
					groupProfile["volume"],
					groupProfile["voice"],
					groupProfile["rateBoost"],
				)
				groupedSegments.clear()
				firstTextSegment = False
				yield ("text", (textGroup, options, groupIndexes, hiddenSegments))

			for i, (segment, segmentIndexes) in enumerate(segments):
				if cancelEvent.is_set():
					return
				groupedSegments.append((segment, segmentIndexes))
				if i < len(segments) - 1 and self._should_pause_after_segment(segment):
					yield from flush_grouped_segments()
					yield ("break", 95)
			yield from flush_grouped_segments()

		for item in speechSequence:
			if cancelEvent.is_set():
				return
			itemType = type(item)
			if itemType is str:
				textParts.append(item)
				textCharCount += len(item)
			elif itemType is BreakCommand:
				yield from flush_text()
				if cancelEvent.is_set():
					return
				yield ("break", max(0, int(item.time)))
			elif itemType is IndexCommand:
				pendingIndexes.append((item.index, textCharCount))
			elif itemType is LangChangeCommand:
				yield from flush_text()
				if cancelEvent.is_set():
					return
				activeLanguage = getattr(item, "googleTtsForNvdaLanguage", None) or getattr(item, "lang", None)
				activeVoice = self._voice_for_language(activeLanguage, voice)
			elif itemType is RateCommand:
				yield from flush_text()
				if cancelEvent.is_set():
					return
				rate = int(item.newValue)
			elif itemType is PitchCommand:
				yield from flush_text()
				if cancelEvent.is_set():
					return
				pitch = int(item.newValue)
			elif itemType is VolumeCommand:
				yield from flush_text()
				if cancelEvent.is_set():
					return
				volume = int(item.newValue)
		yield from flush_text()

	def _iter_indexed_text_segments(
		self,
		text: str,
		indexes: list[_IndexMarker],
		fastFirstSegment: bool,
	) -> Iterator[tuple[str, list[_IndexMarker]]]:
		if not indexes:
			for segment in self._iter_text_segments_for_latency(text, fastFirstSegment):
				yield segment, []
			return
		segments: list[tuple[str, int, int]] = []
		searchStart = 0
		for segment in self._iter_text_segments_for_latency(text, fastFirstSegment):
			segmentStart = text.find(segment, searchStart)
			if segmentStart < 0:
				segmentStart = searchStart
			segmentEnd = segmentStart + len(segment)
			segments.append((segment, segmentStart, segmentEnd))
			searchStart = segmentEnd
		if not segments:
			return
		indexPosition = 0
		for segmentPosition, (segment, segmentStart, segmentEnd) in enumerate(segments):
			segmentIndexes: list[_IndexMarker] = []
			while indexPosition < len(indexes) and indexes[indexPosition][1] <= segmentEnd:
				index, charOffset = indexes[indexPosition]
				segmentIndexes.append((index, max(0, min(len(segment), charOffset - segmentStart))))
				indexPosition += 1
			if segmentPosition == len(segments) - 1:
				while indexPosition < len(indexes):
					index, _charOffset = indexes[indexPosition]
					segmentIndexes.append((index, len(segment)))
					indexPosition += 1
			yield segment, segmentIndexes

	def _split_text_for_latency(self, text: str) -> list[str]:
		return list(self._iter_text_segments_for_latency(text, False))

	def _sanitize_speech_text(self, text: str) -> str:
		if not text:
			return text
		sanitized = "".join(" " if unicodedata.category(character) == "Co" else character for character in text)
		return self._normalize_ui_speech_boundaries(sanitized)

	def _normalize_ui_speech_boundaries(self, text: str) -> str:
		if not self._looks_like_ui_summary(text):
			return text
		text = re.sub(r"(?i)(\bCtrl\+\s+[A-Z])\s+(?=(?:not\s+selected|selected)\b)", r"\1, ", text)
		text = re.sub(r"(?i)(?<!,)\s+\b(not\s+selected|selected)\b", r", \1", text, count=1)
		return text

	def _spoken_bridge_segments(self, segments: list[str]) -> list[str]:
		spokenSegments: list[str] = []
		for segment in segments:
			if spokenSegments and spokenSegments[-1] and segment and spokenSegments[-1][-1].isalnum() and segment[0].isalnum():
				spokenSegments[-1] += " "
			spokenSegments.append(segment)
		return spokenSegments

	def _find_sentence_splits(self, text: str) -> list[int]:
		splits: list[int] = []
		for m in _SENTENCE_TERMINATOR_RE.finditer(text):
			terminator = m.group(1)
			trailing_ws = m.group(3)
			end = m.end()
			if end == len(text):
				continue
			if terminator[0] in "。！？；｡।॥؟։።፧፨":
				splits.append(end)
				continue
			if not trailing_ws:
				continue
			if terminator[0] == ".":
				start = m.start()
				w_start = start - 1
				while w_start >= 0 and text[w_start].isalnum():
					w_start -= 1
				word_before = text[w_start + 1 : start].lower()
				if word_before.isdigit():
					continue
				if len(word_before) == 1 and word_before.isalpha():
					continue
				if word_before in _COMMON_ABBREVIATIONS:
					continue
				if w_start >= 0 and text[w_start] == ".":
					continue
			splits.append(end)
		return splits

	def _iter_text_segments_for_latency(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		remaining = text.strip()
		if not remaining:
			return
		splits = self._find_sentence_splits(text)

		chunk_start = 0
		all_boundaries = splits + [len(text)]
		first_yield = True
		for end_idx in all_boundaries:
			candidate = text[chunk_start:end_idx].strip()
			if not candidate:
				continue
			target_len = _FAST_FIRST_SEGMENT_MIN_CHARS if (first_yield and fastFirstSegment) else _REGULAR_SEGMENT_MIN_CHARS
			if len(candidate) >= target_len or end_idx == len(text):
				for segment in self._iter_forced_latency_segments(candidate, first_yield):
					yield segment
					first_yield = False
				chunk_start = end_idx

	def _iter_forced_latency_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		remaining = text.strip()
		if len(remaining) <= _SEAMLESS_UTTERANCE_MAX_CHARS:
			yield from self._iter_soft_phrase_segments(remaining, fastFirstSegment)
			return
		first_yield = fastFirstSegment
		while remaining:
			if self._looks_like_url_token(remaining):
				max_len = min(_URL_TOKEN_SEGMENT_MAX_CHARS, _FORCED_SEGMENT_HARD_MAX_CHARS)
			else:
				max_len = _FAST_FIRST_SEGMENT_MAX_CHARS if first_yield else _REGULAR_SEGMENT_MAX_CHARS
			if len(remaining) <= max_len:
				yield remaining
				return
			cut = self._find_forced_latency_cut(remaining, max_len)
			segment = remaining[:cut].strip()
			if segment:
				yield segment
			remaining = remaining[cut:].strip()
			first_yield = False

	def _iter_soft_phrase_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		remaining = text.strip()
		if self._looks_like_ui_summary(remaining):
			yield from self._iter_ui_summary_segments(remaining)
			return
		first_segment = fastFirstSegment
		while len(remaining) > _SOFT_PHRASE_SEGMENT_MAX_CHARS:
			cut = self._find_soft_phrase_cut(remaining, first_segment)
			if cut is None:
				break
			segment = remaining[:cut].strip()
			if segment:
				yield segment
			remaining = remaining[cut:].strip()
			first_segment = False
		if remaining:
			yield remaining

	def _iter_ui_summary_segments(self, text: str) -> Iterator[str]:
		remaining = text.strip()
		first_segment = True
		while len(remaining) > _UI_SUMMARY_SEGMENT_MAX_CHARS:
			cut = self._find_ui_boundary_cut(remaining, first_segment)
			if cut is None:
				cut = self._find_whitespace_cut(
					remaining,
					_UI_SUMMARY_SEGMENT_MIN_CHARS,
					_UI_SUMMARY_SEGMENT_MAX_CHARS,
					_UI_SUMMARY_SEGMENT_LOOKAHEAD,
				)
			if cut is None:
				break
			segment = remaining[:cut].strip()
			if segment:
				yield segment
			remaining = remaining[cut:].strip()
			first_segment = False
		if remaining:
			yield remaining

	def _looks_like_ui_summary(self, text: str) -> bool:
		normalized = f" {text.lower()} "
		return (
			normalized.endswith(" row ")
			or " chọn hàng " in normalized
			or " selected " in normalized
			or " not selected " in normalized
			or " edit " in normalized
			or " button " in normalized
			or " link " in normalized
			or " liên kết " in normalized
			or " nút " in normalized
		)

	def _find_ui_boundary_cut(self, text: str, fastFirstSegment: bool = False) -> int | None:
		min_len = _FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS if fastFirstSegment else _UI_BOUNDARY_SEGMENT_MIN_CHARS
		max_len = _FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS if fastFirstSegment else _UI_BOUNDARY_SEGMENT_MAX_CHARS
		lookahead = _FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD if fastFirstSegment else _UI_BOUNDARY_LOOKAHEAD
		min_len = min(len(text), min_len)
		max_len = min(len(text), max_len)
		lookahead_end = min(len(text), max_len + lookahead)
		boundary_re = re.compile(
			r"(?i)(?:^|\s)(?:not\s+selected|selected|button|link|edit|row|nút|liên\s+kết)(?=\s|$)"
		)
		best: int | None = None
		for match in boundary_re.finditer(text):
			cut = match.end()
			if cut < min_len or cut > lookahead_end:
				continue
			if cut <= max_len:
				best = cut
			elif best is None:
				return cut
		return best

	def _find_soft_phrase_cut(self, text: str, fastFirstSegment: bool = False) -> int | None:
		soft_break_chars = ",，、;；"
		if fastFirstSegment:
			min_len = min(len(text), _FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS)
			max_len = min(len(text), _FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
			lookahead = _FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD
		else:
			min_len = min(len(text), _SOFT_PHRASE_SEGMENT_MIN_CHARS)
			max_len = min(len(text), _SOFT_PHRASE_SEGMENT_MAX_CHARS)
			lookahead = _SOFT_PHRASE_SEGMENT_LOOKAHEAD
		for index in range(max_len, min_len - 1, -1):
			if text[index - 1] in soft_break_chars:
				return index
		lookahead_end = min(len(text), max_len + lookahead)
		for index in range(max_len, lookahead_end):
			if text[index] in soft_break_chars:
				return index + 1
		return None

	def _find_whitespace_cut(self, text: str, min_len: int, max_len: int, lookahead: int) -> int | None:
		min_len = min(len(text), min_len)
		max_len = min(len(text), max_len)
		for index in range(max_len, min_len - 1, -1):
			if text[index - 1].isspace():
				return index
		lookahead_end = min(len(text), max_len + lookahead)
		for index in range(max_len, lookahead_end):
			if text[index].isspace():
				return index
		return None

	def _find_forced_latency_cut(self, text: str, max_len: int) -> int:
		if len(text) <= max_len:
			return len(text)
		min_len = min(max_len, max(_FORCED_SEGMENT_MIN_CHARS, int(max_len * 0.55)))
		soft_break_chars = ",，、:：;；"
		for index in range(max_len, min_len - 1, -1):
			if text[index - 1] in soft_break_chars and self._is_forced_soft_break(text, index):
				return index
		for index in range(max_len, min_len - 1, -1):
			if text[index - 1].isspace():
				return index
		lookahead_end = min(len(text), max_len + _FORCED_SEGMENT_FORWARD_LOOKAHEAD)
		for index in range(max_len, lookahead_end):
			if text[index].isspace():
				return index
		url_break_chars = "/\\?&=#%._-~:"
		for index in range(max_len, min_len - 1, -1):
			if text[index - 1] in url_break_chars:
				return index
		for index in range(max_len, lookahead_end):
			if text[index] in url_break_chars:
				return index + 1
		if text[max_len - 1].isalnum() and text[max_len].isalnum():
			word_end = min(len(text), _FORCED_SEGMENT_HARD_MAX_CHARS)
			for index in range(max_len, word_end):
				if not text[index].isalnum():
					return index
		return max_len

	def _looks_like_url_token(self, text: str) -> bool:
		if any(character.isspace() for character in text):
			return False
		return "://" in text or "/" in text or "\\" in text

	def _is_forced_soft_break(self, text: str, index: int) -> bool:
		character = text[index - 1]
		if character in ":：":
			before = text[index - 2] if index >= 2 else ""
			after = text[index] if index < len(text) else ""
			if before.isdigit() and after.isdigit():
				return False
		return True

	def _should_pause_after_segment(self, segment: str) -> bool:
		stripped = segment.rstrip()
		while stripped and stripped[-1] in "'\")]}”’」』）》〉»":
			stripped = stripped[:-1].rstrip()
		return bool(stripped) and stripped[-1] in ".!?。！？｡।॥؟։።፧፨"

	def _speech_loop(self) -> None:
		while not self._shutdownEvent.is_set():
			with self._speechCondition:
				while not self._speechQueue and not self._shutdownEvent.is_set():
					self._speechCondition.wait()
				if self._shutdownEvent.is_set():
					return
				request = self._speechQueue.popleft()
				self._activeCancelEvent = request[-1]
			try:
				self._speak_worker(*request)
			finally:
				with self._speechCondition:
					if self._activeCancelEvent is request[-1]:
						self._activeCancelEvent = None

	def _speak_worker(
		self,
		speechSequence: list[Any],
		voice: str,
		rate: int,
		rateBoost: bool,
		pitch: int,
		volume: int,
		cancelEvent: threading.Event,
	) -> None:
		try:
			self._ensure_current_output_device()
			self._audioChunksSinceDeviceCheck = 0
			for kind, payload in self._iter_speech_chunks(
				speechSequence,
				voice,
				rate,
				rateBoost,
				pitch,
				volume,
				cancelEvent,
			):
				if cancelEvent.is_set():
					return
				if kind == "text":
					text, options, indexes, hiddenSegments = payload
					self._speak_text(text, options, cancelEvent, indexes, hiddenSegments)
				elif kind == "break":
					self._feed_silence(payload)
				elif kind == "index":
					self._sync_player()
					if not cancelEvent.is_set():
						synthIndexReached.notify(synth=self, index=payload)
			if not cancelEvent.is_set():
				self._finish_request_audio()
			if not cancelEvent.is_set():
				synthDoneSpeaking.notify(synth=self)
		except CdpCancelled:
			log.debug("Google TTS speech cancelled.")
		except Exception:
			log.exception("Google TTS speech failed.", exc_info=True)
			if not cancelEvent.is_set():
				synthDoneSpeaking.notify(synth=self)

	def _speak_text(
		self,
		text: str,
		options: dict[str, Any],
		cancelEvent: threading.Event,
		indexes: list[_IndexMarker] | None = None,
		hiddenSegments: list[str] | None = None,
	) -> None:
		indexes = indexes or []
		leadingIndexes = [index for index, charOffset in indexes if charOffset <= 0]
		remainingIndexes = [(index, charOffset) for index, charOffset in indexes if charOffset > 0]
		for index in leadingIndexes:
			if cancelEvent.is_set():
				return
			self._sync_player()
			synthIndexReached.notify(synth=self, index=index)

		hasInternalIndexes = any(0 < charOffset < len(text) for _index, charOffset in remainingIndexes)

		cacheKey = self._short_cache_key(text, options, hiddenSegments)
		if cacheKey is not None:
			cached = self._get_cached_audio(cacheKey)
			if cached is not None:
				if not cancelEvent.is_set():
					if hasInternalIndexes:
						self._feed_audio_with_indexes(cached, remainingIndexes, len(text), cancelEvent)
					else:
						self._feed_audio(cached)
						for index, _charOffset in remainingIndexes:
							if cancelEvent.is_set():
								return
							self._sync_player()
							synthIndexReached.notify(synth=self, index=index)
				return

		audioParts: list[bytes] = []
		pendingIndexes = sorted(remainingIndexes, key=lambda item: item[1])

		def notify_indexes_through(charOffset: int, *, sync: bool = False) -> None:
			nonlocal pendingIndexes
			while pendingIndexes and pendingIndexes[0][1] <= charOffset:
				index, _indexOffset = pendingIndexes.pop(0)
				if cancelEvent.is_set():
					return
				if sync:
					self._sync_player()
				synthIndexReached.notify(synth=self, index=index)

		def on_mark(charOffset: int) -> None:
			if not cancelEvent.is_set():
				notify_indexes_through(max(0, min(len(text), charOffset)))

		def on_audio(pcm: bytes) -> None:
			if cacheKey is not None and pcm:
				audioParts.append(pcm)
			if not cancelEvent.is_set():
				self._feed_audio(pcm)

		self._bridge.speak(
			text,
			options,
			on_audio,
			cancelEvent,
			onMark=on_mark if hasInternalIndexes else None,
			segments=hiddenSegments,
		)

		audio = b"".join(audioParts) if audioParts else b""
		if pendingIndexes and not cancelEvent.is_set():
			for index, _charOffset in pendingIndexes:
				if cancelEvent.is_set():
					return
				self._sync_player()
				synthIndexReached.notify(synth=self, index=index)
		if cacheKey is not None and len(audio) >= 64:
			self._put_cached_audio(cacheKey, audio)

	def _feed_audio(self, pcm: bytes) -> None:
		if pcm:
			self._audioChunksSinceDeviceCheck += 1
			if self._audioChunksSinceDeviceCheck >= 50:
				self._ensure_current_output_device()
				self._audioChunksSinceDeviceCheck = 0
			self._player.feed(pcm)

	def _feed_audio_with_indexes(
		self,
		pcm: bytes,
		indexes: list[_IndexMarker],
		totalCharacters: int,
		cancelEvent: threading.Event,
	) -> None:
		if not indexes:
			self._feed_audio(pcm)
			return
		if not pcm:
			for index, _charOffset in indexes:
				if cancelEvent.is_set():
					return
				self._sync_player()
				synthIndexReached.notify(synth=self, index=index)
			return
		totalBytes = len(pcm)
		totalCharacters = max(1, totalCharacters)
		byteIndexes: list[tuple[Any, int]] = []
		for index, charOffset in indexes:
			clampedOffset = max(0, min(totalCharacters, charOffset))
			byteOffset = int((clampedOffset / totalCharacters) * totalBytes)
			byteOffset -= byteOffset % 2
			byteIndexes.append((index, max(0, min(totalBytes, byteOffset))))
		position = 0
		for index, byteOffset in byteIndexes:
			if cancelEvent.is_set():
				return
			if byteOffset > position:
				self._feed_audio(pcm[position:byteOffset])
				position = byteOffset
			self._sync_player()
			if not cancelEvent.is_set():
				synthIndexReached.notify(synth=self, index=index)
		if not cancelEvent.is_set() and position < totalBytes:
			self._feed_audio(pcm[position:])

	def _sync_player(self) -> None:
		sync = getattr(self._player, "sync", None)
		if sync is not None:
			sync()
			return
		self._player.idle()

	def _has_queued_speech(self) -> bool:
		with self._speechCondition:
			return bool(self._speechQueue)

	def _finish_request_audio(self) -> None:
		if self._has_queued_speech():
			self._sync_player()
			return
		self._player.idle()

	def _short_cache_key(
		self,
		text: str,
		options: dict[str, Any],
		hiddenSegments: list[str] | None = None,
	) -> tuple[Any, ...] | None:
		if len(text) > _SHORT_CACHE_MAX_CHARS:
			return None
		return (
			text,
			tuple(hiddenSegments or ()),
			options.get("voiceId"),
			options.get("rate"),
			options.get("pitch"),
			options.get("volume"),
			options.get("outputGain"),
			options.get("artificialRate"),
		)

	def _get_cached_audio(self, key: tuple[Any, ...]) -> bytes | None:
		with self._cacheLock:
			audio = self._shortAudioCache.get(key)
			if audio is not None:
				self._shortAudioCache.move_to_end(key)
				return audio
		return None

	def _put_cached_audio(self, key: tuple[Any, ...], audio: bytes) -> None:
		if not audio:
			return
		if len(audio) > _SHORT_CACHE_MAX_BYTES:
			return
		with self._cacheLock:
			oldAudio = self._shortAudioCache.pop(key, None)
			if oldAudio is not None:
				self._shortAudioCacheBytes -= len(oldAudio)
			self._shortAudioCache[key] = audio
			self._shortAudioCacheBytes += len(audio)
			self._shortAudioCache.move_to_end(key)
			while (
				len(self._shortAudioCache) > _SHORT_CACHE_MAX_ITEMS
				or self._shortAudioCacheBytes > _SHORT_CACHE_MAX_BYTES
			):
				_, removedAudio = self._shortAudioCache.popitem(last=False)
				self._shortAudioCacheBytes -= len(removedAudio)

	def _feed_silence(self, milliseconds: int) -> None:
		if milliseconds <= 0:
			return
		frameCount = int(SAMPLE_RATE * milliseconds / 1000)
		self._audioChunksSinceDeviceCheck += 1
		if self._audioChunksSinceDeviceCheck >= 50:
			self._ensure_current_output_device()
			self._audioChunksSinceDeviceCheck = 0
		self._player.feed(b"\x00\x00" * frameCount)

	def _auto_detect_profile_for_text(
		self,
		text: str,
		activeVoice: str,
		activeLanguage: str | None,
		baseVoice: str,
		rate: int,
		rateBoost: bool,
		pitch: int,
		volume: int,
	) -> dict[str, Any]:
		# Explicit language changes from NVDA or the focused app should remain authoritative.
		if not self._auto_language_detection_enabled():
			return self._speech_profile(activeVoice, rate, rateBoost, pitch, volume)
		candidateLanguages = self._auto_language_candidates()
		if not candidateLanguages:
			return self._speech_profile(activeVoice, rate, rateBoost, pitch, volume)
		if activeLanguage:
			profileLanguage = self._auto_language_candidate_for_language(activeLanguage, candidateLanguages)
			if profileLanguage:
				return self._auto_language_profile(
					profileLanguage,
					activeVoice,
					rate,
					rateBoost,
					pitch,
					volume,
				)
			return self._speech_profile(activeVoice, rate, rateBoost, pitch, volume)
		if activeVoice != baseVoice:
			voiceLanguage = self.catalog.language_for_voice(activeVoice)
			profileLanguage = self._auto_language_candidate_for_language(voiceLanguage, candidateLanguages)
			if profileLanguage:
				return self._auto_language_profile(
					profileLanguage,
					activeVoice,
					rate,
					rateBoost,
					pitch,
					volume,
				)
			return self._speech_profile(activeVoice, rate, rateBoost, pitch, volume)
		if len(candidateLanguages) == 1:
			return self._auto_language_profile(
				candidateLanguages[0],
				activeVoice,
				rate,
				rateBoost,
				pitch,
				volume,
			)
		detectedLanguage = self._detect_auto_language(text, candidateLanguages)
		if detectedLanguage is None:
			detectedLanguage = self._auto_language_preferred(candidateLanguages, activeVoice)
		return self._auto_language_profile(
			detectedLanguage,
			activeVoice,
			rate,
			rateBoost,
			pitch,
			volume,
		)

	def _speech_profile(
		self,
		voice: str,
		rate: int,
		rateBoost: bool,
		pitch: int,
		volume: int,
	) -> dict[str, Any]:
		return {
			"voice": voice,
			"rate": max(0, min(100, int(rate))),
			"rateBoost": bool(rateBoost),
			"pitch": max(0, min(100, int(pitch))),
			"volume": max(0, min(100, int(volume))),
		}

	def _auto_language_profile(
		self,
		language: str | None,
		fallbackVoice: str,
		fallbackRate: int,
		fallbackRateBoost: bool,
		fallbackPitch: int,
		fallbackVolume: int,
	) -> dict[str, Any]:
		profile = self._auto_language_profile_for_language(language)
		voice = str(profile.get("voice") or "")
		if not self._voice_matches_language(voice, language):
			voice = self._voice_for_language(language, fallbackVoice)
		return self._speech_profile(
			voice,
			self._profile_int(profile.get("rate"), fallbackRate),
			self._profile_bool(profile.get("rateBoost"), fallbackRateBoost),
			self._profile_int(profile.get("pitch"), fallbackPitch),
			self._profile_int(profile.get("volume"), fallbackVolume),
		)

	def _voice_matches_language(self, voice: str, language: str | None) -> bool:
		if voice not in self.availableVoices:
			return False
		if not language:
			return True
		try:
			voiceLanguage = self.catalog.language_for_voice(voice)
		except Exception:
			return False
		return self._language_matches(voiceLanguage, language)

	def _auto_language_notice_message(self) -> str:
		return _(
			"Voice settings are managed by Google TTS for NVDA. "
			"Open the Google TTS for NVDA category in NVDA Settings to configure them."
		)

	def _get_notice(self) -> str:
		return self._auto_language_notice_message()

	def _set_notice(self, value: str) -> None:
		return

	def _auto_language_detection_enabled(self) -> bool:
		try:
			value = config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_DETECTION]
		except Exception:
			return DEFAULT_AUTO_LANGUAGE_DETECTION
		if isinstance(value, str):
			return value.strip().lower() in ("1", "true", "yes", "on")
		return bool(value)

	def _auto_language_profiles(self) -> dict[str, dict[str, Any]]:
		try:
			rawValue = config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PROFILES]
		except Exception:
			rawValue = DEFAULT_AUTO_LANGUAGE_PROFILES
		try:
			parsed = json.loads(str(rawValue or "{}"))
		except (TypeError, ValueError):
			return {}
		if not isinstance(parsed, dict):
			return {}
		profiles: dict[str, dict[str, Any]] = {}
		for rawLanguage, rawProfile in parsed.items():
			languageKey = self._normalize_language(str(rawLanguage))
			if languageKey and isinstance(rawProfile, dict):
				profiles[languageKey] = dict(rawProfile)
		return profiles

	def _auto_language_profile_for_language(self, language: str | None) -> dict[str, Any]:
		languageKey = self._normalize_language(language)
		if not languageKey:
			return {}
		profiles = self._auto_language_profiles()
		profile = profiles.get(languageKey)
		if profile is not None:
			return profile
		languageKeys = self._language_match_keys(language)
		for profileLanguage, profile in profiles.items():
			if self._language_match_keys(profileLanguage).intersection(languageKeys):
				return profile
		return {}

	def _profile_int(self, value: Any, default: int) -> int:
		try:
			return max(0, min(100, int(value)))
		except (TypeError, ValueError):
			return max(0, min(100, int(default)))

	def _profile_bool(self, value: Any, default: bool = False) -> bool:
		if isinstance(value, str):
			return value.strip().lower() in ("1", "true", "yes", "on")
		if value is None:
			return default
		return bool(value)

	def _auto_language_candidates(self) -> list[str]:
		profiles = self._auto_language_profiles()
		try:
			rawValue = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_CANDIDATES])
		except Exception:
			rawValue = DEFAULT_AUTO_LANGUAGE_CANDIDATES
		availableByKey = {
			self._normalize_language(language): language
			for language in self.availableLanguages
		}
		if profiles:
			profileCandidates = [
				availableByKey[languageKey]
				for languageKey, profile in profiles.items()
				if languageKey in availableByKey and self._profile_bool(profile.get("enabled"), False)
			]
			return profileCandidates
		candidates: list[str] = []
		seen: set[str] = set()
		for rawLanguage in rawValue.split(","):
			key = self._normalize_language(rawLanguage)
			if not key or key in seen or key not in availableByKey:
				continue
			candidates.append(availableByKey[key])
			seen.add(key)
		return candidates

	def _auto_language_preferred(self, candidateLanguages: list[str], fallbackVoice: str) -> str:
		try:
			configured = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PREFERRED])
		except Exception:
			configured = DEFAULT_AUTO_LANGUAGE_PREFERRED
		configuredKey = self._normalize_language(configured)
		for language in candidateLanguages:
			if self._normalize_language(language) == configuredKey:
				return language
		fallbackLanguage = self.catalog.language_for_voice(fallbackVoice)
		fallbackRoot = self._language_root(fallbackLanguage)
		for language in candidateLanguages:
			if self._language_root(language) == fallbackRoot:
				return language
		return candidateLanguages[0] if candidateLanguages else fallbackLanguage

	def _auto_language_candidate_for_language(self, language: str | None, candidateLanguages: list[str]) -> str:
		languageKeys = self._language_match_keys(language)
		for candidate in candidateLanguages:
			if self._language_match_keys(candidate).intersection(languageKeys):
				return candidate
		languageRoot = self._language_root(language)
		for candidate in candidateLanguages:
			if self._language_root(candidate) == languageRoot:
				return candidate
		return ""

	def _detect_auto_language(self, text: str, candidateLanguages: list[str]) -> str | None:
		cldLanguage = language_detector.detect_language(text, candidateLanguages)
		if cldLanguage is not None:
			return cldLanguage
		candidateByRoot: dict[str, str] = {}
		for language in candidateLanguages:
			candidateByRoot.setdefault(self._language_root(language), language)
		scores = {root: 0 for root in candidateByRoot}
		for token in _LANGUAGE_WORD_RE.findall(text):
			root, score = self._language_token_signal(token, set(candidateByRoot))
			if root is not None and root in scores:
				scores[root] += score
		if not scores:
			return None
		ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
		bestRoot, bestScore = ranked[0]
		secondScore = ranked[1][1] if len(ranked) > 1 else 0
		if bestScore < _AUTO_DETECT_MIN_SCORE or bestScore - secondScore < _AUTO_DETECT_MIN_MARGIN:
			return None
		return candidateByRoot[bestRoot]

	def _has_letter_tokens(self, text: str) -> bool:
		return any(bool(token.strip("'’_-")) for token in _LANGUAGE_WORD_RE.findall(text))

	def _language_token_signal(self, token: str, candidateRoots: set[str]) -> tuple[str | None, int]:
		normalized = token.strip("'’_-").casefold()
		if not normalized or self._looks_like_url_token(normalized):
			return None, 0
		scriptRoot = self._language_script_signal(normalized, candidateRoots)
		if scriptRoot is not None:
			return scriptRoot, 2
		if "vi" in candidateRoots and any(character in _VIETNAMESE_LETTERS for character in normalized):
			return "vi", 2
		viScore = 1 if "vi" in candidateRoots and normalized in _VIETNAMESE_WORDS else 0
		enScore = 1 if "en" in candidateRoots and normalized in _ENGLISH_WORDS else 0
		if viScore > enScore:
			return "vi", viScore
		if enScore > viScore:
			return "en", enScore
		return None, 0

	def _language_root(self, language: str | None) -> str:
		return self._normalize_language(language).split("-", 1)[0]

	def _language_script_signal(self, token: str, candidateRoots: set[str]) -> str | None:
		matchingRoots: set[str] = set()
		for root in candidateRoots:
			ranges = self._script_ranges_for_language_root(root)
			if not ranges:
				continue
			if self._token_has_character_in_ranges(token, ranges):
				matchingRoots.add(root)
		if len(matchingRoots) == 1:
			return next(iter(matchingRoots))
		return None

	def _script_ranges_for_language_root(self, root: str) -> tuple[tuple[int, int], ...]:
		ranges = _LANGUAGE_SCRIPT_RANGES.get(root, ())
		if root in _LATIN_SCRIPT_ROOTS:
			ranges = ranges + _LATIN_SCRIPT_RANGES
		return ranges

	def _token_has_character_in_ranges(self, token: str, ranges: tuple[tuple[int, int], ...]) -> bool:
		for character in token:
			codepoint = ord(character)
			if any(start <= codepoint <= end for start, end in ranges):
				return True
		return False

	def _speech_options(
		self,
		rate: int,
		pitch: int,
		volume: int,
		voice: str | None = None,
		rateBoost: bool | None = None,
	) -> dict[str, Any]:
		speaker = self.catalog.speaker_for_voice(voice or self.__voice)
		package = self.catalog.package_for_voice(speaker.id)
		volumeLevel = max(0.0, min(1.0, volume / 100.0))
		outputGain = max(0.0, min(_OUTPUT_GAIN_MAKEUP, volumeLevel * _OUTPUT_GAIN_MAKEUP))
		desiredRate = self._rate_to_chrome(rate, rateBoost)
		engineRate = desiredRate
		artificialRate = 1.0
		if self._uses_protected_engine_rate(package.id) and desiredRate > _PROTECTED_ENGINE_RATE:
			engineRate = _PROTECTED_ENGINE_RATE
			artificialRate = max(_MIN_ARTIFICIAL_RATE, min(_MAX_ARTIFICIAL_RATE, desiredRate / engineRate))
		return {
			"voiceId": speaker.id,
			"voiceName": speaker.name,
			"lang": speaker.language,
			"rate": round(engineRate, 3),
			"artificialRate": round(artificialRate, 3),
			"pitch": self._pitch_to_chrome(pitch),
			"volume": round(volumeLevel, 4),
			"outputGain": round(outputGain, 4),
		}

	def _uses_protected_engine_rate(self, packageId: str) -> bool:
		return packageId.lower().endswith("-seanet")

	def _voice_for_language(self, lang: str | None, fallbackVoice: str) -> str:
		if not lang:
			return fallbackVoice
		normalizedLang = self._normalize_language(lang)
		if not normalizedLang:
			return fallbackVoice
		fallbackSpeaker = self.catalog.speaker_for_voice(fallbackVoice)
		if self._language_matches(fallbackSpeaker.language, normalizedLang):
			return fallbackVoice
		for speaker in self.catalog.speakers:
			if self._language_matches(speaker.language, normalizedLang):
				return speaker.id
		rootLang = normalizedLang.split("-", 1)[0]
		if self._normalize_language(fallbackSpeaker.language).split("-", 1)[0] == rootLang:
			return fallbackVoice
		for speaker in self.catalog.speakers:
			if self._normalize_language(speaker.language).split("-", 1)[0] == rootLang:
				return speaker.id
		return fallbackVoice

	def _normalize_language(self, lang: str | None) -> str:
		return str(lang or "").replace("_", "-").lower()

	def _language_match_keys(self, language: str | None) -> set[str]:
		key = self._normalize_language(language)
		if not key:
			return set()
		aliases = {key}
		aliasMap = {
			"cmn-cn": {"zh-cn"},
			"zh-cn": {"cmn-cn"},
			"cmn-tw": {"zh-tw"},
			"zh-tw": {"cmn-tw"},
			"yue-hk": {"zh-hk"},
			"zh-hk": {"yue-hk"},
			"zh": {"cmn-cn", "cmn-tw", "yue-hk"},
			"fil-ph": {"tl", "fil"},
			"tl": {"fil-ph", "fil"},
			"ar-xa": {"ar"},
			"ar": {"ar-xa"},
		}
		aliases.update(aliasMap.get(key, set()))
		if key.startswith("fil-"):
			aliases.update({"fil", "tl"})
		return aliases

	def _language_matches(self, left: str | None, right: str | None) -> bool:
		leftKeys = self._language_match_keys(left)
		rightKeys = self._language_match_keys(right)
		return bool(leftKeys and rightKeys and leftKeys.intersection(rightKeys))

	def _rate_to_chrome(self, value: int, rateBoost: bool | None = None) -> float:
		percent = max(0, min(100, value)) / 100.0
		rate = 0.35 + (2.0 - 0.35) * percent
		boostEnabled = self._rateBoost if rateBoost is None else bool(rateBoost)
		if boostEnabled:
			rate *= 2
		return round(max(0.1, min(10.0, rate)), 3)

	def _pitch_to_chrome(self, pitch: int) -> float:
		pitchSemitones = -12.0 + 24.0 * max(0, min(100, pitch)) / 100.0
		return round(max(0.1, min(3.0, 1.0 + pitchSemitones / 20.0)), 3)

	def _get_voice(self) -> str:
		return self.__voice

	def _set_voice(self, value: str) -> None:
		if value not in self.availableVoices:
			value = next(iter(self.availableVoices))
		self.__voice = value
		self._warm_current_voice_async()

	def _warm_current_voice_async(self) -> None:
		if self._shutdownEvent.is_set():
			return
		options = self._speech_options(self._rate, self._pitch, 0)
		with suppress(Exception):
			self._warmupCancelEvent.set()
		cancelEvent = threading.Event()
		self._warmupCancelEvent = cancelEvent

		def warm() -> None:
			try:
				self._bridge.ensure_connection()
			except Exception:
				log.debug("Google TTS bridge eager connection failed.", exc_info=True)
				return
			if cancelEvent.is_set() or self._shutdownEvent.is_set():
				return
			try:
				warmupOptions = dict(options)
				warmupOptions["warmupText"] = _VOICE_WARMUP_TEXT
				self._bridge.preload_voice(warmupOptions, cancelEvent)
			except CdpCancelled:
				log.debug("Google TTS voice preload cancelled.")
			except Exception:
				log.debug("Google TTS voice preload failed.", exc_info=True)

		thread = threading.Thread(name="googleTtsForNvda.preload", target=warm, daemon=True)
		self._warmupThread = thread
		thread.start()

	def _get_language(self) -> str:
		lang = self.catalog.language_for_voice(self.__voice)
		langMap = {
			"cmn-CN": "zh_CN",
			"cmn-TW": "zh_TW",
			"yue-HK": "zh_HK",
			"ar-XA": "ar",
			"fil-PH": "tl",
		}
		lowerLang = lang.lower()
		if lang in langMap:
			nvdaLocale = langMap[lang]
		elif lowerLang.startswith("cmn"):
			nvdaLocale = "zh_CN"
		elif lowerLang.startswith("yue"):
			nvdaLocale = "zh_HK"
		else:
			nvdaLocale = lang.replace("-", "_")
		if self._nvda_locale_exists(nvdaLocale):
			return nvdaLocale
		rootLocale = nvdaLocale.split("_", 1)[0]
		if rootLocale != nvdaLocale and self._nvda_locale_exists(rootLocale):
			return rootLocale
		return "en"

	def _nvda_locale_exists(self, locale: str) -> bool:
		try:
			return os.path.isdir(os.path.join(globalVars.appDir, "locale", locale))
		except Exception:
			return False

	def _get_rate(self) -> int:
		return self._rate

	def _set_rate(self, value: int) -> None:
		self._rate = max(0, min(100, int(value)))

	def _get_rateBoost(self) -> bool:
		return self._rateBoost

	def _set_rateBoost(self, value: bool) -> None:
		self._rateBoost = bool(value)

	def _get_pitch(self) -> int:
		return self._pitch

	def _set_pitch(self, value: int) -> None:
		self._pitch = max(0, min(100, int(value)))

	def _get_volume(self) -> int:
		return self._volume

	def _set_volume(self, value: int) -> None:
		self._volume = max(0, min(100, int(value)))
		with suppress(Exception):
			self._player.setVolume(all=1.0)

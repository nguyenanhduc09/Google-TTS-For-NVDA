# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import suppress
import re
import threading
import time
from typing import Any

import addonHandler
import config
import synthDriverHandler
import wx
from logHandler import log
from nvwave import WavePlayer
from speech.commands import BreakCommand, IndexCommand, LangChangeCommand, PitchCommand, RateCommand, VolumeCommand
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached

from .bridge import CdpCancelled, ChromeTtsBridge, SAMPLE_RATE
from .catalog import VoiceCatalog
from . import voice_store


addonHandler.initTranslation()


_SHORT_CACHE_MAX_CHARS = 5000
_SHORT_CACHE_MAX_ITEMS = 4096
_SHORT_CACHE_MAX_BYTES = 200 * 1024 * 1024
_OUTPUT_GAIN_MAKEUP = 2.0
_SpeechRequest = tuple[list[Any], str, int, int, int, threading.Event]
_IndexMarker = tuple[Any, int]
_FAST_FIRST_SEGMENT_MIN_CHARS = 30
_REGULAR_SEGMENT_MIN_CHARS = 110

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


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "googleTtsForNvda"
	description = "Google TTS For NVDA"
	supportedSettings = (
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		synthDriverHandler.SynthDriver.RateBoostSetting(),
		synthDriverHandler.SynthDriver.PitchSetting(),
		synthDriverHandler.SynthDriver.VolumeSetting(),
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

	@classmethod
	def check(cls) -> bool:
		# Keep the driver visible; runtime dependencies are validated when selected.
		return True

	def __init__(self) -> None:
		super().__init__()
		fullCatalog = VoiceCatalog.load()
		installedPackages = voice_store.installed_packages(fullCatalog)
		if not installedPackages:
			# Defer UI until after this constructor aborts so synth startup is
			# not blocked by a modal dialog waiting for user input.
			wx.CallAfter(self._prompt_for_voice_install)
			raise RuntimeError("No Google TTS voice packages are installed.")
		self.catalog = VoiceCatalog(installedPackages)
		if not self.catalog.speakers:
			raise RuntimeError("Installed Google TTS voice packages do not contain usable voices.")
		if ChromeTtsBridge.find_chrome() is None:
			wx.CallAfter(self._show_missing_chrome_error)
			raise RuntimeError("Google Chrome was not found.")
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

	def _prompt_for_voice_install(self) -> None:
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
					_(
						"No Google TTS voice packages are installed. "
						"Download voices now?"
					),
					_("Google TTS For NVDA"),
					wx.OK | wx.CANCEL | wx.ICON_QUESTION,
					gui.mainFrame,
				)
				if answer == getattr(wx, "ID_OK", wx.OK) or answer == wx.OK:
					open_voice_manager_download_tab()
			except Exception:
				log.exception("Could not show Google TTS voice install prompt.", exc_info=True)

		# Start checking after 250ms to allow NVDA to catch the RuntimeError,
		# restore the fallback synthesizer, and display its own warning message box.
		wx.CallLater(250, prompt_when_ready)

	def _show_missing_chrome_error(self) -> None:
		try:
			import gui

			gui.messageBox(
				_("Google Chrome was not found. Install Google Chrome or set CHROME_PATH to chrome.exe."),
				_("Google TTS For NVDA"),
				wx.OK | wx.ICON_ERROR,
				gui.mainFrame,
			)
		except Exception:
			log.exception("Could not show Google Chrome missing message.", exc_info=True)

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
		pitch = self._pitch
		volume = self._volume
		with suppress(Exception):
			self._warmupCancelEvent.set()
		with self._speechCondition:
			if self._shutdownEvent.is_set():
				return
			self._speechQueue.append((sequence, voice, rate, pitch, volume, cancelEvent))
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
		pitch: int,
		volume: int,
		cancelEvent: threading.Event,
	) -> Iterator[tuple[str, Any]]:
		textParts: list[str] = []
		textCharCount = 0
		pendingIndexes: list[_IndexMarker] = []
		firstTextSegment = True
		activeVoice = voice

		def flush_text() -> Iterator[tuple[str, Any]]:
			nonlocal firstTextSegment, textCharCount, pendingIndexes
			rawText = "".join(textParts)
			textParts.clear()
			textCharCount = 0
			leftTrimmed = len(rawText) - len(rawText.lstrip())
			text = rawText.strip()
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
			options = self._speech_options(rate, pitch, volume, activeVoice)
			segments = list(self._iter_indexed_text_segments(text, indexes, firstTextSegment))
			for i, (segment, segmentIndexes) in enumerate(segments):
				if cancelEvent.is_set():
					return
				firstTextSegment = False
				yield ("text", (segment, options, segmentIndexes))
				if i < len(segments) - 1:
					yield ("break", 95)

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
				activeVoice = self._voice_for_language(item.lang, voice)
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
		if not splits:
			yield remaining
			return

		chunk_start = 0
		all_boundaries = splits + [len(text)]
		first_yield = True
		for end_idx in all_boundaries:
			candidate = text[chunk_start:end_idx].strip()
			if not candidate:
				continue
			target_len = _FAST_FIRST_SEGMENT_MIN_CHARS if (first_yield and fastFirstSegment) else _REGULAR_SEGMENT_MIN_CHARS
			if len(candidate) >= target_len or end_idx == len(text):
				yield candidate
				chunk_start = end_idx
				first_yield = False

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
				pitch,
				volume,
				cancelEvent,
			):
				if cancelEvent.is_set():
					return
				if kind == "text":
					text, options, indexes = payload
					self._speak_text(text, options, cancelEvent, indexes)
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

		cacheKey = self._short_cache_key(text, options)
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

	def _short_cache_key(self, text: str, options: dict[str, Any]) -> tuple[Any, ...] | None:
		if len(text) > _SHORT_CACHE_MAX_CHARS:
			return None
		return (
			text,
			options.get("voiceId"),
			options.get("rate"),
			options.get("pitch"),
			options.get("volume"),
			options.get("outputGain"),
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

	def _speech_options(self, rate: int, pitch: int, volume: int, voice: str | None = None) -> dict[str, Any]:
		speaker = self.catalog.speaker_for_voice(voice or self.__voice)
		volumeLevel = max(0.0, min(1.0, volume / 100.0))
		outputGain = max(0.0, min(_OUTPUT_GAIN_MAKEUP, volumeLevel * _OUTPUT_GAIN_MAKEUP))
		return {
			"voiceId": speaker.id,
			"voiceName": speaker.name,
			"lang": speaker.language,
			"rate": self._rate_to_chrome(rate),
			"pitch": self._pitch_to_chrome(pitch),
			"volume": round(volumeLevel, 4),
			"outputGain": round(outputGain, 4),
		}

	def _voice_for_language(self, lang: str | None, fallbackVoice: str) -> str:
		if not lang:
			return fallbackVoice
		normalizedLang = self._normalize_language(lang)
		if not normalizedLang:
			return fallbackVoice
		fallbackSpeaker = self.catalog.speaker_for_voice(fallbackVoice)
		if self._normalize_language(fallbackSpeaker.language) == normalizedLang:
			return fallbackVoice
		for speaker in self.catalog.speakers:
			if self._normalize_language(speaker.language) == normalizedLang:
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

	def _rate_to_chrome(self, value: int) -> float:
		percent = max(0, min(100, value)) / 100.0
		rate = 0.35 + (2.0 - 0.35) * percent
		if self._rateBoost:
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
				self._bridge.preload_voice(options, cancelEvent)
				for voiceId in list(self.availableVoices.keys()):
					if cancelEvent.is_set() or self._shutdownEvent.is_set():
						break
					if voiceId == self.__voice:
						continue
					vOpts = self._speech_options(self._rate, self._pitch, 0, voiceId)
					with suppress(Exception):
						self._bridge.preload_voice(vOpts, cancelEvent)
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
			"yue-HK": "zh_HK",
			"ar-XA": "ar",
			"fil-PH": "tl",
		}
		if lang in langMap:
			return langMap[lang]
		lowerLang = lang.lower()
		if lowerLang.startswith("cmn"):
			return "zh_CN"
		if lowerLang.startswith("yue"):
			return "zh_HK"
		return lang

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

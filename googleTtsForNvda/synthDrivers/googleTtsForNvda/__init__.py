# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import suppress
import re
import threading
import time
import unicodedata
from typing import Any

import addonHandler
import config
import languageHandler
import synthDriverHandler
import wx
from logHandler import log
from nvwave import WavePlayer
from speech.commands import BreakCommand, IndexCommand, LangChangeCommand, PitchCommand, RateCommand, VolumeCommand
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached

from .bridge import CdpCancelled, ChromeTtsBridge, SAMPLE_RATE
from .catalog import EngineLibraryError, VoiceCatalog
from . import voice_store


addonHandler.initTranslation()


_SHORT_CACHE_MAX_CHARS = 5000
_SHORT_CACHE_MAX_ITEMS = 4096
_SHORT_CACHE_MAX_BYTES = 200 * 1024 * 1024
_OUTPUT_GAIN_MAKEUP = 2.0
_PROTECTED_ENGINE_RATE = 1.0
_MIN_ARTIFICIAL_RATE = 0.5
_MAX_ARTIFICIAL_RATE = 2.2
_SpeechRequest = tuple[list[Any], str, int, int, int, int, str, threading.Event]
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


# --- Automatic language detection ---------------------------------------
#
# Mode identifiers, in display order for the NVDA Voice Settings dialog.
_LANGUAGE_DETECTION_OFF = "off"
_LANGUAGE_DETECTION_CONSERVATIVE = "conservative"
_LANGUAGE_DETECTION_AGGRESSIVE = "aggressive"

# Minimum share (0.0-1.0) of "foreign" script characters a text segment must
# contain before a mode will consider switching voices, and the minimum
# segment length (in characters) required for a switch to happen.
# Aggressive mode uses much looser thresholds so it reacts to short phrases;
# conservative mode requires a longer, more confidently foreign segment.
_LANG_DETECT_THRESHOLDS = {
	_LANGUAGE_DETECTION_CONSERVATIVE: {"minRatio": 0.6, "minChars": 12},
	_LANGUAGE_DETECTION_AGGRESSIVE: {"minRatio": 0.2, "minChars": 2},
}

# Unicode code point ranges used to classify a character into a rough script
# category. This is a lightweight, dependency-free heuristic: it is good at
# telling scripts apart (Latin vs Cyrillic vs CJK vs Arabic, etc.) but cannot
# distinguish two languages that share the same script (e.g. English vs.
# French), aside from the Vietnamese-specific diacritics check below.
_SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
	("cyrillic", 0x0400, 0x04FF),
	("greek", 0x0370, 0x03FF),
	("arabic", 0x0600, 0x06FF),
	("hebrew", 0x0590, 0x05FF),
	("hangul", 0xAC00, 0xD7A3),
	("hiragana", 0x3040, 0x309F),
	("katakana", 0x30A0, 0x30FF),
	("han", 0x4E00, 0x9FFF),
	("thai", 0x0E00, 0x0E7F),
	("devanagari", 0x0900, 0x097F),
)
# Maps a detected script to a representative language tag that is looked up
# against installed voices via `_voice_for_language`. Scripts that map to
# more than one plausible language (e.g. Han -> Chinese) use the most common
# case; `_voice_for_language` already falls back gracefully when no voice
# for that exact tag is installed.
_SCRIPT_TO_LANGUAGE = {
	"cyrillic": "ru-RU",
	"greek": "el-GR",
	"arabic": "ar-XA",
	"hebrew": "he-IL",
	"hangul": "ko-KR",
	"hiragana": "ja-JP",
	"katakana": "ja-JP",
	"han": "cmn-CN",
	"thai": "th-TH",
	"devanagari": "hi-IN",
}

# Latin letters that only appear in Vietnamese, used to tell Vietnamese text
# apart from other Latin-script languages such as English.
_VIETNAMESE_CHARS = set("àáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")

# Vietnamese and English share the Latin alphabet, so script ranges alone
# can't tell them apart once Vietnamese diacritics are absent. These are
# purely grammatical/function words with (essentially) zero use as Vietnamese
# loanwords, so matching one is a strong, low-false-positive signal that a
# run of plain Latin letters is English rather than undiacritized Vietnamese.
_ENGLISH_COMMON_WORDS = frozenset({
	"the", "and", "is", "are", "was", "were", "have", "has", "had", "having",
	"this", "that", "these", "those", "with", "without", "from", "your",
	"you", "for", "not", "but", "what", "when", "where", "which", "who",
	"whom", "how", "why", "will", "would", "should", "could", "about",
	"there", "their", "they", "them", "then", "than", "into", "onto",
	"over", "under", "between", "because", "please", "thanks", "welcome",
	"yes", "okay", "sorry", "today", "tomorrow", "yesterday", "again",
	"still", "already", "always", "never", "often", "sometimes", "just",
	"very", "really", "also", "only", "even", "some", "any", "each",
	"other", "more", "most", "much", "many", "such", "before", "after",
	"here", "does", "doesn't", "don't", "isn't", "aren't", "can't", "won't",
	"hello", "hi", "hey", "bye", "goodbye", "yeah", "yep", "nope",
})

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


class SynthDriver(synthDriverHandler.SynthDriver):
	name = "googleTtsForNvda"
	description = _("Google TTS For NVDA")
	supportedSettings = (
		# Voice = language (e.g. "Vietnamese (vi-VN)"); Variant = the specific
		# speaker within that language. This mirrors the eSpeak/IBM TTS
		# convention the user asked for, rather than Voice already being one
		# exact speaker.
		synthDriverHandler.SynthDriver.VoiceSetting(),
		synthDriverHandler.SynthDriver.VariantSetting(),
		synthDriverHandler.SynthDriver.RateSetting(),
		synthDriverHandler.SynthDriver.RateBoostSetting(),
		synthDriverHandler.SynthDriver.PitchSetting(),
		# Inflection sits between Pitch and Volume in the Voice Settings dialog.
		synthDriverHandler.SynthDriver.InflectionSetting(),
		synthDriverHandler.SynthDriver.VolumeSetting(),
		# Custom setting: automatic language detection (Off / Conservative /
		# Aggressive). NVDA renders a choice-type DriverSetting like this as a
		# single-select combo box in the Voice Settings dialog -- selecting one
		# option deselects the others, the same exclusive behaviour as a radio
		# button group.
		synthDriverHandler.DriverSetting(
			"languageDetection",
			# Translators: Label for the language detection setting in the
			# NVDA Voice Settings dialog.
			_("Language detection"),
			# Deliberately NOT in the settings ring: this is an advanced,
			# opt-in feature (unlike Rate/Variant/Voice/Volume/Inflection/
			# Pitch/Rate boost, which are basic settings every synth
			# exposes on the ring). No mainstream NVDA driver puts a
			# feature like this on the ring by default -- users who want
			# it can turn it on from the full Voice Settings dialog.
			availableInSettingsRing=False,
			defaultVal=_LANGUAGE_DETECTION_OFF,
			displayName=_("Language detection"),
		),
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
		self.availableVariants = self._build_available_variants(self.__voice)
		self.__variant = self._initial_variant(self.__voice)
		self._rate = 50
		self._rateBoost = False
		self._pitch = 50
		self._inflection = 50
		# TODO(inflection): logged once at startup so this is discoverable
		# without reading source -- see the long comment in _speech_options()
		# for the full investigation. Short version: the bundled WASM TTS
		# Engine (Chrome/ChromeOS's built-in Google TTS extension) has no
		# pitch-range/inflection parameter anywhere in its API, so this
		# setting currently changes only the NVDA UI value, not the audio.
		log.debug(
			"googleTtsForNvda: the Inflection setting has no audible effect -- "
			"the underlying WASM TTS Engine does not support a pitch-range/"
			"inflection parameter. See the comment in _speech_options() for details."
		)
		self._volume = 100
		self.availableLanguagedetections = self._build_available_language_detections()
		self._languageDetection = self._initial_language_detection()
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
		# The pipeline below (and _speech_options) works in terms of an actual
		# speaker id -- that is `variant` now that `voice` is a language.
		voice = self.__variant
		rate = self._rate
		pitch = self._pitch
		inflection = self._inflection
		volume = self._volume
		languageDetection = self._languageDetection
		with suppress(Exception):
			self._warmupCancelEvent.set()
		with self._speechCondition:
			if self._shutdownEvent.is_set():
				return
			self._speechQueue.append(
				(sequence, voice, rate, pitch, inflection, volume, languageDetection, cancelEvent)
			)
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
		# "Voice" lists languages, not individual speakers -- see the comment
		# on supportedSettings. One entry per unique language, in the same
		# order languages first appear in the (already language-sorted) catalog.
		voices: OrderedDict[str, VoiceInfo] = OrderedDict()
		for speaker in self.catalog.speakers:
			language = speaker.language
			if language in voices:
				continue
			voices[language] = VoiceInfo(language, self._language_display_name(language), language)
		return voices

	def _language_display_name(self, language: str) -> str:
		with suppress(Exception):
			normalized = languageHandler.normalizeLanguage(language)
			if normalized:
				description = languageHandler.getLanguageDescription(normalized)
				if description:
					return f"{description} ({language})"
		return language

	def _initial_voice(self) -> str:
		try:
			configured = config.conf["speech"][self.name]["voice"]
			if configured in self.availableVoices:
				return configured
		except Exception:
			pass
		if "en-US" in self.availableVoices:
			return "en-US"
		return next(iter(self.availableVoices))

	def _build_available_variants(self, language: str) -> "OrderedDict[str, VoiceInfo]":
		# "Variant" lists the specific speakers available within `language` --
		# this is the actual voice identity passed to the TTS engine.
		variants: OrderedDict[str, VoiceInfo] = OrderedDict()
		for speaker in self.catalog.speakers:
			if speaker.language != language:
				continue
			label = f"{speaker.name} ({speaker.gender})" if speaker.gender else speaker.name
			variants[speaker.id] = VoiceInfo(speaker.id, label, speaker.language)
		return variants

	def _initial_variant(self, language: str) -> str:
		try:
			configured = config.conf["speech"][self.name]["variant"]
			if configured in self.availableVariants:
				return configured
		except Exception:
			pass
		for speaker in self.catalog.speakers:
			if speaker.language == language:
				return speaker.id
		# _initial_voice only ever returns a language that has at least one
		# speaker, so availableVariants is never empty in practice.
		return next(iter(self.availableVariants))

	def _build_available_language_detections(self) -> "OrderedDict[str, VoiceInfo]":
		# Translators: Option shown in the Language detection setting to
		# disable automatic voice switching for mixed-language text.
		offLabel = _("Off")
		# Translators: Option shown in the Language detection setting that
		# only switches voice for longer, high-confidence foreign-language
		# segments (fewer, safer switches).
		conservativeLabel = _("Conservative")
		# Translators: Option shown in the Language detection setting that
		# switches voice readily, even for short foreign-language phrases.
		aggressiveLabel = _("Aggressive")
		modes: "OrderedDict[str, VoiceInfo]" = OrderedDict()
		modes[_LANGUAGE_DETECTION_OFF] = VoiceInfo(_LANGUAGE_DETECTION_OFF, offLabel)
		modes[_LANGUAGE_DETECTION_CONSERVATIVE] = VoiceInfo(_LANGUAGE_DETECTION_CONSERVATIVE, conservativeLabel)
		modes[_LANGUAGE_DETECTION_AGGRESSIVE] = VoiceInfo(_LANGUAGE_DETECTION_AGGRESSIVE, aggressiveLabel)
		return modes

	def _initial_language_detection(self) -> str:
		try:
			configured = config.conf["speech"][self.name]["languageDetection"]
			if configured in self.availableLanguagedetections:
				return configured
		except Exception:
			pass
		return _LANGUAGE_DETECTION_OFF

	def _iter_speech_chunks(
		self,
		speechSequence: list[Any],
		voice: str,
		rate: int,
		pitch: int,
		inflection: int,
		volume: int,
		languageDetection: str,
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
			# Automatic language detection only ever picks a voice for this one
			# flushed segment; it never changes `activeVoice` itself, so an
			# explicit LangChangeCommand from NVDA always keeps priority over
			# the heuristic on the next segment.
			segmentVoice = self._detect_language_voice(text, languageDetection, activeVoice)
			options = self._speech_options(rate, pitch, inflection, volume, segmentVoice)
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
		pitch: int,
		inflection: int,
		volume: int,
		languageDetection: str,
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
				inflection,
				volume,
				languageDetection,
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
			options.get("inflectionScale"),
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

	def _speech_options(
		self,
		rate: int,
		pitch: int,
		inflection: int,
		volume: int,
		voice: str | None = None,
	) -> dict[str, Any]:
		# `voice` here means "speaker id" (i.e. variant); fall back to the
		# currently selected variant, not the language-level `voice` setting.
		speaker = self.catalog.speaker_for_voice(voice or self.__variant)
		package = self.catalog.package_for_voice(speaker.id)
		volumeLevel = max(0.0, min(1.0, volume / 100.0))
		outputGain = max(0.0, min(_OUTPUT_GAIN_MAKEUP, volumeLevel * _OUTPUT_GAIN_MAKEUP))
		desiredRate = self._rate_to_chrome(rate)
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
			# TODO(inflection): This engine has no working "inflection" (pitch
			# range/contour) parameter. Confirmed by inspecting the bundled
			# WASM TTS Engine end-to-end:
			#   1. bindings_main.js only exports GoogleTtsInit/InitBuffered/
			#      FinalizeBuffered/GetTimepoints*/InstallVoice/ReadBuffered/
			#      Shutdown -- no pitch-range/prosody function exists.
			#   2. The synthesis request protobuf built in
			#      offscreen_compiled.js only ever sets field 1 (rate) and
			#      field 6 (pitch); fields 2/3/5 are never written anywhere
			#      in the bundle, so their meaning (if any) is unknown --
			#      guessing at them risks sending a malformed request that
			#      could break synthesis entirely, so nobody should just
			#      "try" a field number here.
			#   3. This whole engine is Chrome/ChromeOS's built-in Google
			#      text-to-speech extension (see the file header comment in
			#      streaming_worklet_processor.js). Chrome's own public
			#      chrome.tts / chrome.ttsEngine API only exposes rate,
			#      pitch, and volume -- there has never been an
			#      inflection/pitch-range option at any layer of this stack.
			# `inflectionScale` (0.0-2.0, computed below) is still forwarded
			# end-to-end through bridge.py and bridgeHarness.js purely to
			# keep the plumbing consistent with eSpeak/IBM TTS's Inflection
			# setting (a single value alongside rate/pitch/volume). It is a
			# harmless no-op today. Fixing this for real would require the
			# actual Google TTS WASM engine to add prosody/pitch-range
			# support -- not something fixable from this add-on's code.
			"inflectionScale": self._inflection_to_chrome(inflection),
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

	def _detect_language_voice(self, text: str, mode: str, fallbackVoice: str) -> str:
		"""Guess a better voice for `text` when automatic language detection
		is enabled. Returns `fallbackVoice` unchanged when detection is off,
		when no installed voice matches, or when the text is not confidently
		in a different script/language than the current voice.
		"""
		if mode == _LANGUAGE_DETECTION_OFF:
			return fallbackVoice
		thresholds = _LANG_DETECT_THRESHOLDS.get(mode)
		if thresholds is None:
			return fallbackVoice
		detectedLang = self._classify_text_language(text, thresholds)
		if detectedLang is None:
			return fallbackVoice
		return self._voice_for_language(detectedLang, fallbackVoice)

	def _classify_text_language(self, text: str, thresholds: dict[str, float]) -> str | None:
		"""Classify `text` into a rough BCP-47-ish language tag, or return
		None when no language is confidently dominant enough to justify
		switching voices (per `thresholds`).

		Works word-by-word rather than character-by-character so that plain
		Latin words can be recognized as English via `_ENGLISH_COMMON_WORDS`
		-- character-level Unicode script ranges alone cannot tell English
		apart from Vietnamese written without diacritics.
		"""
		words = _WORD_RE.findall(text)
		letterCounts = {"vi-VN": 0, "en-US": 0}
		totalLetters = 0
		for word in words:
			totalLetters += len(word)
			nonLatinScript = None
			hasVietnameseChar = False
			for character in word:
				codePoint = ord(character)
				if character.lower() in _VIETNAMESE_CHARS:
					hasVietnameseChar = True
					continue
				for scriptName, rangeStart, rangeEnd in _SCRIPT_RANGES:
					if rangeStart <= codePoint <= rangeEnd:
						nonLatinScript = scriptName
						break
				if nonLatinScript:
					break
			if nonLatinScript:
				language = _SCRIPT_TO_LANGUAGE.get(nonLatinScript)
				if language:
					letterCounts[language] = letterCounts.get(language, 0) + len(word)
					continue
			if hasVietnameseChar:
				letterCounts["vi-VN"] += len(word)
				continue
			if word.lower() in _ENGLISH_COMMON_WORDS:
				letterCounts["en-US"] += len(word)
				continue
			# Plain Latin word that isn't a recognized English function word:
			# could be undiacritized Vietnamese, a proper noun, or anything
			# else -- too ambiguous to attribute to a language.
			continue
		if totalLetters < thresholds["minChars"]:
			return None
		dominantLanguage, dominantCount = max(letterCounts.items(), key=lambda item: item[1])
		if dominantCount == 0 or dominantCount / totalLetters < thresholds["minRatio"]:
			return None
		return dominantLanguage

	def _rate_to_chrome(self, value: int) -> float:
		percent = max(0, min(100, value)) / 100.0
		rate = 0.35 + (2.0 - 0.35) * percent
		if self._rateBoost:
			rate *= 2
		return round(max(0.1, min(10.0, rate)), 3)

	def _pitch_to_chrome(self, pitch: int) -> float:
		pitchSemitones = -12.0 + 24.0 * max(0, min(100, pitch)) / 100.0
		return round(max(0.1, min(3.0, 1.0 + pitchSemitones / 20.0)), 3)

	def _inflection_to_chrome(self, inflection: int) -> float:
		# 0 -> 0.0 (flat/monotone), 50 -> 1.0 (engine default contour),
		# 100 -> 2.0 (strongly exaggerated pitch swings).
		return round(max(0.0, min(100, inflection)) / 50.0, 3)

	def _get_voice(self) -> str:
		return self.__voice

	def _set_voice(self, value: str) -> None:
		if value not in self.availableVoices:
			value = next(iter(self.availableVoices))
		if value != self.__voice:
			self.__voice = value
			self.availableVariants = self._build_available_variants(value)
			self.__variant = self._initial_variant(value)
		self._warm_current_voice_async()

	def _get_variant(self) -> str:
		return self.__variant

	def _set_variant(self, value: str) -> None:
		if value not in self.availableVariants:
			value = next(iter(self.availableVariants))
		self.__variant = value
		self._warm_current_voice_async()

	def _warm_current_voice_async(self) -> None:
		if self._shutdownEvent.is_set():
			return
		options = self._speech_options(self._rate, self._pitch, self._inflection, 0)
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
		lang = self.catalog.language_for_voice(self.__variant)
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

	def _get_inflection(self) -> int:
		return self._inflection

	def _set_inflection(self, value: int) -> None:
		self._inflection = max(0, min(100, int(value)))

	def _get_volume(self) -> int:
		return self._volume

	def _set_volume(self, value: int) -> None:
		self._volume = max(0, min(100, int(value)))
		with suppress(Exception):
			self._player.setVolume(all=1.0)

	def _get_languageDetection(self) -> str:
		return self._languageDetection

	def _set_languageDetection(self, value: str) -> None:
		if value not in self.availableLanguagedetections:
			value = _LANGUAGE_DETECTION_OFF
		self._languageDetection = value

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import suppress
from typing import Any

import addonHandler
import config
import globalVars
import languageHandler
import nvwave
import synthDriverHandler
import wx
from autoSettingsUtils.driverSetting import DriverSetting
from autoSettingsUtils.utils import StringParameterInfo
from logHandler import log
from nvwave import WavePlayer
from speech.commands import (
    BreakCommand,
    CharacterModeCommand,
    IndexCommand,
    LangChangeCommand,
    PhonemeCommand,
    PitchCommand,
    RateCommand,
    VolumeCommand,
)
from synthDriverHandler import VoiceInfo, synthDoneSpeaking, synthIndexReached

from . import audio_math, language_detector, language_utils, standby, voice_store
from .bridge import (
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
    CdpCancelled,
    ChromeTtsBridge,
    edge_webview2_blocks_effective_runtime,
)
from .catalog import EngineLibraryError, VoiceCatalog, engine_library_error_message
from .language_profiles import (
    LANGUAGE_WORD_RE as _LANGUAGE_WORD_RE,
)
from .language_profiles import (
    language_token_signal as _language_token_signal,
)
from .speech_processing import (
    DEFAULT_TEXT_SEGMENTER as _TEXT_SEGMENTER,
)
from .speech_processing import (
    LIVE_MULTI_SEGMENT_LEAD_MS,
    PcmLeadBuffer,
    create_pcm_silence_shortener,
    is_complete_speech_result,
    segment_audio_cache_key,
    short_audio_cache_key,
)
from .speech_processing import (
    PAUSE_MODE_DO_NOT_SHORTEN as _PAUSE_MODE_DO_NOT_SHORTEN,
)
from .speech_processing import (
    PAUSE_MODE_SHORTEN_ALL as _PAUSE_MODE_SHORTEN_ALL,
)
from .speech_processing import (
    PAUSE_MODE_SHORTEN_END_ONLY as _PAUSE_MODE_SHORTEN_END_ONLY,
)
from .speech_processing import (
    pcm_has_audible_sample as _pcm_has_audible_sample,
)

addonHandler.initTranslation()


_SHORT_CACHE_MAX_ITEMS = 4096
_SHORT_CACHE_MAX_BYTES = 150 * 1024 * 1024
_SHORT_CACHE_STATS_LOG_INTERVAL = 256
_OUTPUT_GAIN_MAKEUP = audio_math.OUTPUT_GAIN_MAKEUP
# SeaNet can handle mild native speedup; keep JS tempo processing for higher rates.
_PROTECTED_ENGINE_RATE = audio_math.PROTECTED_ENGINE_RATE
_MIN_ARTIFICIAL_RATE = audio_math.MIN_ARTIFICIAL_RATE
_MAX_ARTIFICIAL_RATE = audio_math.MAX_ARTIFICIAL_RATE
_NORMAL_SENTENCE_BREAK_MS = 45
_SHORTENED_SENTENCE_BREAK_MS = 15
_BREAK_RATE_FACTOR_MIN = 0.4
_BREAK_RATE_FACTOR_MAX = 1.8
_END_OF_UTTERANCE_PAUSE_MS = 40
_END_OF_UTTERANCE_RATE_FACTOR_MIN = 0.5
_END_OF_UTTERANCE_RATE_FACTOR_MAX = 1.6
_GOOGLE_TTS_LANG_CHANGE_ATTR = language_detector.GOOGLE_TTS_LANG_CHANGE_ATTR
_MISSING_GOOGLE_TTS_LANGUAGE = language_detector.MISSING_GOOGLE_TTS_LANGUAGE
_SpeechRequest = tuple[list[Any], str, int, bool, int, int, str, threading.Event]
_IndexMarker = tuple[Any, int]
_PRELOAD_RESUME_DELAY_SECONDS = 0.15
_VOICE_WARMUP_TEXT = " "
_AUTO_LANGUAGE_NOTICE_ID = "notice"
_AUTO_DETECT_MIN_SCORE = 2
_AUTO_DETECT_MIN_MARGIN = 1
# Flush a grouped speech segment at soft phrase boundaries when the
# accumulated character count reaches this threshold.  This balances
# segment-cache hit-rate against too many tiny CDP round-trips.
_FLUSH_GROUP_CHARS_THRESHOLD = 120


class ReadOnlyTextDriverSetting(DriverSetting):
    """Marker setting rendered as a read-only edit field by the global plugin."""

    readOnlyText = True


# 5-point rate factor interpolation (from empirically measured break durations).
# Higher rates get shorter breaks; lower rates get longer breaks.
_BREAK_RATE_TABLE = ((10, 1.8), (43, 1.3), (60, 1.0), (75, 0.7), (85, 0.4))


def _interpolate_rate_factor(rate: int, table: tuple[tuple[int, float], ...]) -> float:
    """Linear interpolation over a (rate, factor) lookup table."""
    if rate <= table[0][0]:
        return table[0][1]
    if rate >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        r0, f0 = table[i]
        r1, f1 = table[i + 1]
        if r0 <= rate <= r1:
            t = (rate - r0) / (r1 - r0)
            return f0 + t * (f1 - f0)
    return table[-1][1]


def _break_rate_factor(rate: int) -> float:
    return max(_BREAK_RATE_FACTOR_MIN, min(_BREAK_RATE_FACTOR_MAX, _interpolate_rate_factor(rate, _BREAK_RATE_TABLE)))


def _end_of_utterance_rate_factor(rate: int) -> float:
    return max(
        _END_OF_UTTERANCE_RATE_FACTOR_MIN,
        min(_END_OF_UTTERANCE_RATE_FACTOR_MAX, _interpolate_rate_factor(rate, _BREAK_RATE_TABLE)),
    )


class SynthDriver(synthDriverHandler.SynthDriver):
    name = "googleTtsForNvda"
    description = _("Google TTS For NVDA")
    _PAUSE_MODE_SETTING = DriverSetting(
        "pauseMode",
        _("&Pauses"),
        defaultVal=_PAUSE_MODE_DO_NOT_SHORTEN,
    )
    _STANDARD_SUPPORTED_SETTINGS = (
        synthDriverHandler.SynthDriver.VoiceSetting(),
        synthDriverHandler.SynthDriver.VariantSetting(),
        synthDriverHandler.SynthDriver.RateSetting(),
        synthDriverHandler.SynthDriver.RateBoostSetting(),
        synthDriverHandler.SynthDriver.PitchSetting(),
        synthDriverHandler.SynthDriver.VolumeSetting(),
        _PAUSE_MODE_SETTING,
    )
    _pauseModes = OrderedDict(
        (
            (_PAUSE_MODE_DO_NOT_SHORTEN, StringParameterInfo(_PAUSE_MODE_DO_NOT_SHORTEN, _("Do not shorten"))),
            (
                _PAUSE_MODE_SHORTEN_END_ONLY,
                StringParameterInfo(_PAUSE_MODE_SHORTEN_END_ONLY, _("Shorten at end of text only")),
            ),
            (_PAUSE_MODE_SHORTEN_ALL, StringParameterInfo(_PAUSE_MODE_SHORTEN_ALL, _("Shorten all pauses"))),
        )
    )
    _AUTO_LANGUAGE_NOTICE_SETTING = ReadOnlyTextDriverSetting(
        _AUTO_LANGUAGE_NOTICE_ID,
        _("Automatic language profiles status"),
        availableInSettingsRing=True,
        useConfig=False,
        defaultVal=_AUTO_LANGUAGE_NOTICE_ID,
    )
    supportedCommands = {
        BreakCommand,
        CharacterModeCommand,
        IndexCommand,
        LangChangeCommand,
        PhonemeCommand,
        RateCommand,
        PitchCommand,
        VolumeCommand,
    }
    supportedNotifications = {synthIndexReached, synthDoneSpeaking}
    cachePropertiesByDefault = False

    @property
    def supportedSettings(self) -> tuple[Any, ...]:
        if self._auto_language_detection_enabled():
            return (self._AUTO_LANGUAGE_NOTICE_SETTING, self._PAUSE_MODE_SETTING)
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
        if edge_webview2_blocks_effective_runtime():
            wx.CallAfter(self._prompt_for_edge_webview2_install)
            raise RuntimeError(
                _(
                    "Microsoft Edge WebView2 Runtime was not found. "
                    "Install it before using Microsoft Edge as the Google TTS For NVDA Chromium browser runtime."
                )
            )
        if ChromeTtsBridge.find_browser() is None:
            wx.CallAfter(self._show_missing_chrome_error)
            raise RuntimeError(
                _(
                    "No supported Chromium browser runtime was found. "
                    "Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."
                )
            )
        self._speakersByLanguage = self.catalog.voices_by_language()
        self._speakersByPackage = self._build_speakers_by_package()
        self._speakerVoiceInfos = self._build_speaker_voice_infos()
        self._variantsByLanguage: dict[str, OrderedDict[str, VoiceInfo]] = {}
        self.availableVoices = self._build_available_voices()
        self.availableLanguages = set(self._speakersByLanguage)
        self._playerOutputDevice = self._current_output_device()
        self._player = self._create_wave_player(self._playerOutputDevice)
        self._bridge = standby.claim_bridge(self.catalog) or ChromeTtsBridge(self.catalog)
        self._speechCondition = threading.Condition()
        self._speechQueue: deque[_SpeechRequest] = deque()
        self._activeCancelEvent: threading.Event | None = None
        self._shutdownEvent = threading.Event()
        self._cacheLock = threading.RLock()
        self._shortAudioCache: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
        self._shortAudioCacheBytes = 0
        self._shortAudioCacheHits = 0
        self._shortAudioCacheMisses = 0
        self._shortAudioCacheEvictions = 0
        self._audioChunksSinceDeviceCheck = 0
        self._worker = threading.Thread(
            name="googleTtsForNvda.speech",
            target=self._speech_loop,
            daemon=True,
        )
        self._worker.start()
        self.__voice = self._initial_voice()
        self.__variant = self._initial_variant(self.__voice)
        self._availableVariants = self._build_available_variants(self.__voice)
        self._rate = 50
        self._rateBoost = False
        self._pitch = 50
        self._volume = 100
        self._pauseMode = _PAUSE_MODE_DO_NOT_SHORTEN
        self._warmupThread: threading.Thread | None = None
        self._warmupCancelEvent = threading.Event()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

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
                    if not any(
                        known in clsName for known in ("SettingsDialog", "SynthesizerDialog", "VoiceManagerDialog")
                    ):
                        wx.CallLater(150, prompt_when_ready, retries - 1)
                        return
            try:
                import gui
                from globalPlugins.googleTtsForNvda import open_voice_manager_download_tab

                answer = gui.messageBox(
                    message
                    or _(
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

    def _prompt_for_edge_webview2_install(self) -> None:
        def prompt_when_ready(retries: int = 200) -> None:
            if retries <= 0:
                return
            for win in wx.GetTopLevelWindows():
                if not win.IsShown():
                    continue
                clsName = win.__class__.__name__
                if "MessageDialog" in clsName:
                    wx.CallLater(150, prompt_when_ready, retries - 1)
                    return
                if isinstance(win, wx.Dialog) and getattr(win, "IsModal", lambda: False)():
                    if not any(
                        known in clsName for known in ("SettingsDialog", "SynthesizerDialog", "VoiceManagerDialog")
                    ):
                        wx.CallLater(150, prompt_when_ready, retries - 1)
                        return
            try:
                from globalPlugins.googleTtsForNvda import show_edge_webview2_prompt

                show_edge_webview2_prompt()
            except Exception:
                log.exception("Could not show Microsoft Edge WebView2 Runtime prompt.", exc_info=True)

        wx.CallLater(250, prompt_when_ready)

    def _engine_library_error_message(self, error: EngineLibraryError) -> str:
        return engine_library_error_message(error)

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
                _(
                    "No supported Chromium browser runtime was found. Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."
                ),
                _("Google TTS For NVDA"),
                wx.OK | wx.ICON_ERROR,
                gui.mainFrame,
            )
        except Exception:
            log.exception("Could not show supported browser missing message.", exc_info=True)

    def terminate(self, *args: Any, **kwargs: Any) -> None:
        with suppress(Exception):
            self._warmupCancelEvent.set()
        self.cancel()
        self._shutdownEvent.set()
        with self._speechCondition:
            self._speechCondition.notify_all()
        bridgeReusable = self._bridge_safe_for_standby_release()
        bridgeReleased = False
        releaseFailed = False
        if bridgeReusable:
            try:
                bridgeReleased = standby.release_synth_bridge(self._bridge, self.catalog)
            except Exception:
                releaseFailed = True
                log.debug("Could not release idle Google TTS browser runtime to standby.", exc_info=True)
        if not bridgeReleased:
            with suppress(Exception):
                self._bridge.terminate()
            if not bridgeReusable or releaseFailed:
                reason = (
                    "Google TTS synth released a busy browser runtime"
                    if not bridgeReusable
                    else "Google TTS synth could not release its browser runtime to standby"
                )
                with suppress(Exception):
                    standby.release_synth_without_bridge(reason)
        with suppress(Exception):
            self._player.close()

    def _bridge_safe_for_standby_release(self) -> bool:
        warmupThread = getattr(self, "_warmupThread", None)
        if warmupThread is not None and warmupThread.is_alive():
            return False
        if not self._bridge.safe_for_standby_release():
            return False
        with self._speechCondition:
            return self._activeCancelEvent is None and not self._speechQueue

    def speak(self, speechSequence: list[Any], *args: Any, **kwargs: Any) -> None:
        sequence = list(speechSequence)
        cancelEvent = threading.Event()
        voice = self._current_speaker_id()
        rate = self._rate
        rateBoost = self._rateBoost
        pitch = self._pitch
        volume = self._volume
        pauseMode = self._pauseMode
        with suppress(Exception):
            self._warmupCancelEvent.set()
        with self._speechCondition:
            if self._shutdownEvent.is_set():
                return
            self._speechQueue.append((sequence, voice, rate, rateBoost, pitch, volume, pauseMode, cancelEvent))
            self._speechCondition.notify()

    def cancel(self, *args: Any, **kwargs: Any) -> None:
        with self._speechCondition:
            if self._activeCancelEvent is not None:
                self._activeCancelEvent.set()
            for request in self._speechQueue:
                request[-1].set()
            self._speechQueue.clear()
            self._speechCondition.notify_all()
        with suppress(Exception):
            self._bridge.cancel_current()
        with suppress(Exception):
            self._player.stop()

    def pause(self, switch: bool, *args: Any, **kwargs: Any) -> None:
        self._player.pause(switch)

    def _current_output_device(self) -> str:
        for section in ("audio", "speech"):
            try:
                return str(config.conf[section]["outputDevice"])
            except Exception:
                continue
        return self._default_output_device()

    def _default_output_device(self) -> str:
        try:
            return str(config.conf.getConfigValidation(("audio", "outputDevice")).default)
        except Exception:
            pass
        try:
            return str(config.conf.getConfigValidation(("speech", "outputDevice")).default)
        except Exception:
            return getattr(WavePlayer, "DEFAULT_DEVICE_KEY", "default")

    def _audio_device_error(self) -> bool:
        for apiName in ("isInError", "audioDeviceError"):
            audioDeviceError = getattr(nvwave, apiName, None)
            if not callable(audioDeviceError):
                continue
            try:
                return bool(audioDeviceError())
            except Exception:
                log.debug(
                    "Could not query NVDA audio device error state via %s.",
                    apiName,
                    exc_info=True,
                )
        return False

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
        deviceError = self._audio_device_error()
        if outputDevice == self._playerOutputDevice and not deviceError:
            return
        with suppress(Exception):
            self._player.close()
        self._playerOutputDevice = outputDevice
        self._player = self._create_wave_player(outputDevice)

    def _build_available_voices(self) -> OrderedDict[str, VoiceInfo]:
        voices: OrderedDict[str, VoiceInfo] = OrderedDict()
        for language in self._speakersByLanguage:
            voices[language] = VoiceInfo(language, self._language_display_name(language), language)
        return voices

    def _language_display_name(self, language: str) -> str:
        return language_utils.get_language_display_name(language)

    def _build_speakers_by_package(self) -> dict[str, list[Any]]:
        speakersByPackage: dict[str, list[Any]] = {}
        for speaker in self.catalog.speakers:
            speakersByPackage.setdefault(speaker.packageId, []).append(speaker)
        return speakersByPackage

    def _build_speaker_voice_infos(self) -> OrderedDict[str, VoiceInfo]:
        voices: OrderedDict[str, VoiceInfo] = OrderedDict()
        for speaker in self.catalog.speakers:
            label = f"{speaker.name} ({speaker.language})"
            voices[speaker.id] = VoiceInfo(speaker.id, label, speaker.language)
        return voices

    def _speaker_voice_infos(self) -> OrderedDict[str, VoiceInfo]:
        return OrderedDict(self._speakerVoiceInfos)

    def _speakers_for_language(self, language: str | None) -> list[Any]:
        if not language:
            return []
        speakers = self._speakersByLanguage.get(language)
        if speakers is not None:
            return list(speakers)
        matches: list[Any] = []
        for speakerLanguage, languageSpeakers in self._speakersByLanguage.items():
            if self._language_matches(speakerLanguage, language):
                matches.extend(languageSpeakers)
        return matches

    def _build_available_variants(self, language: str | None = None) -> OrderedDict[str, VoiceInfo]:
        targetLanguage = language or getattr(self, "_SynthDriver__voice", "")
        cachedVariants = self._variantsByLanguage.get(targetLanguage)
        if cachedVariants is not None:
            return OrderedDict(cachedVariants)
        variants: OrderedDict[str, VoiceInfo] = OrderedDict()
        for speaker in self._speakers_for_language(targetLanguage):
            variants[speaker.id] = VoiceInfo(speaker.id, speaker.name, speaker.language)
        if not variants:
            for speaker in self.catalog.speakers:
                variants[speaker.id] = VoiceInfo(speaker.id, speaker.name, speaker.language)
                break
        self._variantsByLanguage[targetLanguage] = variants
        return OrderedDict(variants)

    def _get_availableNotices(self) -> OrderedDict[str, VoiceInfo]:
        message = self._auto_language_notice_message()
        return OrderedDict({message: VoiceInfo(message, message)})

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

    def _initial_variant(self, language: str) -> str:
        variants = self._build_available_variants(language)
        try:
            configuredVariant = config.conf["speech"][self.name]["variant"]
            if configuredVariant in variants:
                return configuredVariant
        except Exception:
            pass
        return next(iter(variants))

    def _ensure_config_compat(self) -> None:
        try:
            synthConfig = config.conf["speech"][self.name]
        except Exception:
            return

        # Ensure default values for all standard supported settings that use config.
        # This prevents NVDA's synthDriverHandler.SynthDriver.loadSettings() from crashing
        # with KeyError when evaluating `c[s.id] is None` on older profiles or profiles
        # created while Automatic Language Profiles was enabled (e.g. rateBoost, pauseMode).
        for setting in self._STANDARD_SUPPORTED_SETTINGS:
            if not getattr(setting, "useConfig", True):
                continue
            settingId = getattr(setting, "id", None)
            if not settingId or settingId in ("voice", "variant"):
                continue
            try:
                if settingId not in synthConfig or synthConfig[settingId] is None:
                    defaultVal = getattr(setting, "defaultVal", None)
                    if defaultVal is not None:
                        synthConfig[settingId] = defaultVal
            except Exception:
                log.debug("Could not ensure Google TTS setting default for %s.", settingId, exc_info=True)

        try:
            configuredVoice = str(synthConfig.get("voice") or "")
        except Exception:
            configuredVoice = ""
        try:
            configuredVariant = str(synthConfig.get("variant") or "")
        except Exception:
            configuredVariant = ""

        if configuredVoice in self.availableVoices:
            language = configuredVoice
            variants = self._build_available_variants(language)
            if configuredVariant in variants:
                return
            try:
                synthConfig["variant"] = next(iter(variants))
            except Exception:
                log.debug("Could not initialize Google TTS variant setting.", exc_info=True)
            return

        try:
            language = self.catalog.language_for_voice(configuredVoice)
        except Exception:
            language = self.__voice
            configuredVoice = ""
        variants = self._build_available_variants(language)
        replacementVariant = configuredVoice if configuredVoice in variants else next(iter(variants), "")
        try:
            synthConfig["voice"] = language
            if replacementVariant:
                synthConfig["variant"] = replacementVariant
        except Exception:
            log.debug("Could not migrate Google TTS voice/variant settings.", exc_info=True)

    _ensure_variant_config_compat = _ensure_config_compat

    def loadSettings(self, onlyChanged: bool = False, *args: Any, **kwargs: Any) -> None:
        self._ensure_config_compat()
        super().loadSettings(onlyChanged, *args, **kwargs)

    def _iter_speech_chunks(
        self,
        speechSequence: list[Any],
        voice: str,
        rate: int,
        rateBoost: bool,
        pitch: int,
        volume: int,
        pauseMode: str,
        cancelEvent: threading.Event,
    ) -> Iterator[tuple[str, Any]]:
        textParts: list[str] = []
        textCharCount = 0
        pendingIndexes: list[_IndexMarker] = []
        firstTextSegment = True
        activeVoice = voice
        activeLanguage: str | None = None
        activeRateCommand: RateCommand | None = None
        activePitchCommand: PitchCommand | None = None
        activeVolumeCommand: VolumeCommand | None = None
        _inCharMode = False

        def flush_text() -> Iterator[tuple[str, Any]]:
            nonlocal firstTextSegment, textCharCount, pendingIndexes
            # When CharacterModeCommand is active (NVDA spelling mode), space out
            # individual characters so the browser TTS engine pronounces each one
            # distinctly — mimicking the SSML <say-as interpret-as="characters">
            # behavior used by other NVDA synthesizer drivers.
            rawText = " ".join(textParts) if _inCharMode else "".join(textParts)
            textParts.clear()
            textCharCount = 0
            sanitizedText = self._sanitize_speech_text(rawText)
            leftTrimmed = len(sanitizedText) - len(sanitizedText.lstrip())
            text = sanitizedText.strip()
            indexes = [
                (index, max(0, min(len(text), charOffset - leftTrimmed))) for index, charOffset in pendingIndexes
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

            def flush_grouped_segments(
                pauseShorteningMode: str = _PAUSE_MODE_DO_NOT_SHORTEN,
            ) -> Iterator[tuple[str, Any]]:
                nonlocal firstTextSegment
                if not groupedSegments:
                    return
                rawSegments = [segment for segment, _segmentIndexes in groupedSegments]
                spokenSegments = self._spoken_bridge_segments(rawSegments)
                textGroup = "".join(spokenSegments)
                hiddenSegments = spokenSegments if len(spokenSegments) > 1 else None
                groupIndexes: list[_IndexMarker] = []
                charOffset = 0
                for spokenSegment, (_rawSegment, segmentIndexes) in zip(spokenSegments, groupedSegments, strict=False):
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
                groupRate = self._apply_prosody_command(groupProfile["rate"], activeRateCommand)
                groupPitch = self._apply_prosody_command(groupProfile["pitch"], activePitchCommand)
                groupVolume = self._apply_prosody_command(groupProfile["volume"], activeVolumeCommand)
                options = self._speech_options(
                    groupRate,
                    groupPitch,
                    groupVolume,
                    groupProfile["voice"],
                    groupProfile["rateBoost"],
                )
                groupedSegments.clear()
                firstTextSegment = False
                yield ("text", (textGroup, options, groupIndexes, hiddenSegments, pauseShorteningMode))

            for i, (segment, segmentIndexes) in enumerate(segments):
                if cancelEvent.is_set():
                    return
                groupedSegments.append((segment, segmentIndexes))
                if i < len(segments) - 1:
                    isSentenceBoundary = self._should_pause_after_segment(segment)
                    accumulatedChars = sum(len(seg) for seg, _ in groupedSegments)
                    if isSentenceBoundary or (
                        pauseMode == _PAUSE_MODE_SHORTEN_ALL and accumulatedChars >= _FLUSH_GROUP_CHARS_THRESHOLD
                    ):
                        yield from flush_grouped_segments(
                            _PAUSE_MODE_SHORTEN_ALL
                            if isSentenceBoundary and pauseMode == _PAUSE_MODE_SHORTEN_ALL
                            else _PAUSE_MODE_DO_NOT_SHORTEN,
                        )
                        yield (
                            "break",
                            self._sentence_break_milliseconds(pauseMode)
                            if isSentenceBoundary
                            else _SHORTENED_SENTENCE_BREAK_MS,
                        )
            yield from flush_grouped_segments(
                pauseMode
                if pauseMode in (_PAUSE_MODE_SHORTEN_END_ONLY, _PAUSE_MODE_SHORTEN_ALL)
                else _PAUSE_MODE_DO_NOT_SHORTEN,
            )

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
                breakMs = max(0, int(item.time))
                if breakMs > 0 and rate > 0:
                    breakMs = int(breakMs * _break_rate_factor(rate))
                yield ("break", breakMs)
            elif itemType is CharacterModeCommand:
                _inCharMode = bool(item.state)
            elif itemType is IndexCommand:
                pendingIndexes.append((item.index, textCharCount))
            elif itemType is LangChangeCommand:
                googleLanguage = getattr(item, _GOOGLE_TTS_LANG_CHANGE_ATTR, _MISSING_GOOGLE_TTS_LANGUAGE)
                if googleLanguage is _MISSING_GOOGLE_TTS_LANGUAGE:
                    if not self._auto_language_detection_enabled():
                        continue
                    googleLanguage = getattr(item, "lang", None)
                yield from flush_text()
                if cancelEvent.is_set():
                    return
                activeLanguage = googleLanguage if isinstance(googleLanguage, str) else None
                activeVoice = self._voice_for_language(activeLanguage, voice)
            elif itemType is PhonemeCommand:
                # Browser TTS does not support IPA phoneme input.
                # Fall back to the alternate text if available.
                if item.text:
                    textParts.append(item.text)
                    textCharCount += len(item.text)
            elif itemType is RateCommand:
                yield from flush_text()
                if cancelEvent.is_set():
                    return
                activeRateCommand = None if self._is_prosody_reset_command(item) else item
            elif itemType is PitchCommand:
                yield from flush_text()
                if cancelEvent.is_set():
                    return
                activePitchCommand = None if self._is_prosody_reset_command(item) else item
            elif itemType is VolumeCommand:
                yield from flush_text()
                if cancelEvent.is_set():
                    return
                activeVolumeCommand = None if self._is_prosody_reset_command(item) else item
        yield from flush_text()

    def _apply_prosody_command(self, baseValue: Any, command: Any | None) -> int:
        try:
            value = int(baseValue)
        except (TypeError, ValueError):
            value = 50
        if command is not None:
            try:
                offset = int(getattr(command, "_offset", 0))
                multiplier = float(getattr(command, "_multiplier", 1))
                if offset:
                    value += offset
                elif multiplier != 1:
                    value = int(value * multiplier)
            except (TypeError, ValueError):
                log.debug("Could not apply Google TTS prosody command.", exc_info=True)
        return max(0, min(100, value))

    def _is_prosody_reset_command(self, command: Any) -> bool:
        try:
            return int(getattr(command, "_offset", 0)) == 0 and float(getattr(command, "_multiplier", 1)) == 1
        except (TypeError, ValueError):
            return False

    def _iter_indexed_text_segments(
        self,
        text: str,
        indexes: list[_IndexMarker],
        fastFirstSegment: bool,
    ) -> Iterator[tuple[str, list[_IndexMarker]]]:
        yield from _TEXT_SEGMENTER.iter_indexed_text_segments(text, indexes, fastFirstSegment)

    def _split_text_for_latency(self, text: str) -> list[str]:
        return _TEXT_SEGMENTER.split_text_for_latency(text)

    def _sanitize_speech_text(self, text: str) -> str:
        return _TEXT_SEGMENTER.sanitize_speech_text(text)

    def _spoken_bridge_segments(self, segments: list[str]) -> list[str]:
        return _TEXT_SEGMENTER.spoken_bridge_segments(segments)

    def _iter_text_segments_for_latency(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
        yield from _TEXT_SEGMENTER.iter_text_segments_for_latency(text, fastFirstSegment)

    def _looks_like_url_token(self, text: str) -> bool:
        return _TEXT_SEGMENTER.looks_like_url_token(text)

    def _should_pause_after_segment(self, segment: str) -> bool:
        return _TEXT_SEGMENTER.should_pause_after_segment(segment)

    def _sentence_break_milliseconds(self, pauseMode: str) -> int:
        if pauseMode == _PAUSE_MODE_SHORTEN_ALL:
            return _SHORTENED_SENTENCE_BREAK_MS
        return _NORMAL_SENTENCE_BREAK_MS

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
        pauseMode: str,
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
                pauseMode,
                cancelEvent,
            ):
                if cancelEvent.is_set():
                    return
                if kind == "text":
                    text, options, indexes, hiddenSegments, pauseShorteningMode = payload
                    self._speak_text(
                        text,
                        options,
                        cancelEvent,
                        indexes,
                        hiddenSegments,
                        pauseShorteningMode,
                    )
                elif kind == "break":
                    self._feed_silence(payload)
                elif kind == "index":
                    self._sync_player()
                    if not cancelEvent.is_set():
                        synthIndexReached.notify(synth=self, index=payload)
            if not cancelEvent.is_set():
                self._finish_request_audio()
            if (
                not cancelEvent.is_set()
                and pauseMode in (_PAUSE_MODE_SHORTEN_END_ONLY, _PAUSE_MODE_SHORTEN_ALL)
                and rate > 0
                and sum(len(item) for item in speechSequence if isinstance(item, str)) > 40
            ):
                self._feed_silence(int(_END_OF_UTTERANCE_PAUSE_MS * _end_of_utterance_rate_factor(rate)))
            if not cancelEvent.is_set():
                synthDoneSpeaking.notify(synth=self)
        except CdpCancelled:
            log.debug("Google TTS speech cancelled.")
        except Exception as exc:
            technicalDetail = str(getattr(exc, "technicalDetail", "") or "")
            if technicalDetail:
                log.exception("Google TTS speech failed: %s", technicalDetail, exc_info=True)
            else:
                log.exception("Google TTS speech failed.", exc_info=True)
            if not cancelEvent.is_set():
                synthDoneSpeaking.notify(synth=self)
        finally:
            if not cancelEvent.is_set():
                self._maybe_recycle_bridge_after_request()

    def _speak_text(
        self,
        text: str,
        options: dict[str, Any],
        cancelEvent: threading.Event,
        indexes: list[_IndexMarker] | None = None,
        hiddenSegments: list[str] | None = None,
        pauseShorteningMode: str = _PAUSE_MODE_DO_NOT_SHORTEN,
    ) -> None:
        # Coalescing: if the request was already cancelled before we even start
        # the CDP round-trip, skip it entirely to avoid wasting browser resources.
        if cancelEvent.is_set():
            return
        originalText = text
        originalHiddenSegments = list(hiddenSegments or [])
        indexes = indexes or []
        leadingIndexes = [index for index, charOffset in indexes if charOffset <= 0]
        remainingIndexes = [(index, charOffset) for index, charOffset in indexes if charOffset > 0]
        for index in leadingIndexes:
            if cancelEvent.is_set():
                return
            self._sync_player()
            synthIndexReached.notify(synth=self, index=index)

        hasInternalIndexes = any(0 < charOffset < len(originalText) for _index, charOffset in remainingIndexes)
        cacheKey = self._short_cache_key(
            originalText,
            options,
            originalHiddenSegments or None,
            pauseShorteningMode,
        )
        segmentCacheKeys: list[tuple[Any, ...] | None] = []
        if len(originalHiddenSegments) > 1:
            segmentCacheKeys = [
                self._segment_cache_key(
                    segment,
                    options,
                    pauseShorteningMode,
                    hasPreviousSegment=segmentIndex > 0,
                    hasNextSegment=segmentIndex < len(originalHiddenSegments) - 1,
                )
                for segmentIndex, segment in enumerate(originalHiddenSegments)
            ]
        log.debug(
            "Google TTS speech group: chars=%d, hiddenSegments=%d, firstHiddenChars=%d, "
            "wholeCacheEligible=%s, pauseMode=%s.",
            len(originalText),
            len(originalHiddenSegments),
            len(originalHiddenSegments[0]) if originalHiddenSegments else len(originalText),
            cacheKey is not None,
            pauseShorteningMode,
        )
        if cacheKey is not None:
            cached = self._get_cached_audio(cacheKey)
            if cached is not None:
                log.debug(
                    "Google TTS short audio cache hit: kind=group, chars=%d, bytes=%d.",
                    len(originalText),
                    len(cached),
                )
                if not cancelEvent.is_set():
                    if hasInternalIndexes:
                        self._feed_audio_with_indexes(cached, remainingIndexes, len(originalText), cancelEvent)
                    else:
                        self._feed_audio(cached)
                        for index, _charOffset in remainingIndexes:
                            if cancelEvent.is_set():
                                return
                            self._sync_player()
                            synthIndexReached.notify(synth=self, index=index)
                return
            log.debug("Google TTS short audio cache miss: kind=group, chars=%d.", len(originalText))

        cachedSegmentAudio: list[bytes] = []
        cachedSegmentCount = 0
        for segmentIndex, segmentKey in enumerate(segmentCacheKeys):
            if segmentKey is None:
                log.debug(
                    "Google TTS segment cache disabled: index=%d, chars=%d.",
                    segmentIndex,
                    len(originalHiddenSegments[segmentIndex]),
                )
                break
            segmentAudio = self._get_cached_audio(segmentKey)
            if segmentAudio is None:
                log.debug(
                    "Google TTS short audio cache miss: kind=segment, index=%d, chars=%d.",
                    segmentIndex,
                    len(originalHiddenSegments[segmentIndex]),
                )
                break
            log.debug(
                "Google TTS short audio cache hit: kind=segment, index=%d, chars=%d, bytes=%d.",
                segmentIndex,
                len(originalHiddenSegments[segmentIndex]),
                len(segmentAudio),
            )
            cachedSegmentAudio.append(segmentAudio)
            cachedSegmentCount += 1

        if segmentCacheKeys and cachedSegmentCount == len(segmentCacheKeys):
            cached = b"".join(cachedSegmentAudio)
            log.debug(
                "Google TTS segment cache satisfied group: segments=%d, chars=%d, bytes=%d.",
                cachedSegmentCount,
                len(originalText),
                len(cached),
            )
            if hasInternalIndexes:
                self._feed_audio_with_indexes(cached, remainingIndexes, len(originalText), cancelEvent)
            elif not cancelEvent.is_set():
                self._feed_audio(cached)
                for index, _charOffset in remainingIndexes:
                    if cancelEvent.is_set():
                        return
                    self._sync_player()
                    synthIndexReached.notify(synth=self, index=index)
            if cacheKey is not None and not cancelEvent.is_set():
                self._put_cached_audio(cacheKey, cached)
            return

        cachedPrefixAudio = b"".join(cachedSegmentAudio)
        cachedPrefixCharacters = sum(len(segment) for segment in originalHiddenSegments[:cachedSegmentCount])
        if cachedPrefixAudio:
            prefixIndexes = [
                (index, charOffset) for index, charOffset in remainingIndexes if charOffset <= cachedPrefixCharacters
            ]
            if prefixIndexes:
                self._feed_audio_with_indexes(
                    cachedPrefixAudio,
                    prefixIndexes,
                    cachedPrefixCharacters,
                    cancelEvent,
                )
            elif not cancelEvent.is_set():
                self._feed_audio(cachedPrefixAudio)
            remainingIndexes = [
                (index, charOffset - cachedPrefixCharacters)
                for index, charOffset in remainingIndexes
                if charOffset > cachedPrefixCharacters
            ]
            if cancelEvent.is_set():
                return

        if segmentCacheKeys:
            synthesisSegments = originalHiddenSegments[cachedSegmentCount:]
            text = "".join(synthesisSegments)
            bridgeSegments = synthesisSegments
        else:
            synthesisSegments = [originalText]
            text = originalText
            bridgeSegments = originalHiddenSegments or None
        if not text:
            return

        audioParts: list[bytes] = [cachedPrefixAudio] if cacheKey is not None and cachedPrefixAudio else []
        segmentAudioParts: list[bytes] = []
        completedSegmentAudio: list[bytes] = []
        collectSegmentAudio = any(key is not None for key in segmentCacheKeys)
        chromeRate = float(options.get("rate", 1))
        if chromeRate <= 1.175:
            keepSilenceMs = int(45 - (chromeRate - 0.35) * 20 / 0.825)
        else:
            keepSilenceMs = int(25 - (chromeRate - 1.175) * 10 / 2.825)
        keepSilenceMs = max(15, min(45, keepSilenceMs))
        silenceShortener = create_pcm_silence_shortener(pauseShorteningMode, SAMPLE_RATE, keepSilenceMs)
        leadBuffer = (
            PcmLeadBuffer(sampleRate=SAMPLE_RATE, leadMs=LIVE_MULTI_SEGMENT_LEAD_MS)
            if len(synthesisSegments) > 1 and not cachedPrefixAudio
            else None
        )
        if leadBuffer is not None:
            log.debug(
                "Google TTS live PCM lead buffer enabled: milliseconds=%d, segments=%d.",
                LIVE_MULTI_SEGMENT_LEAD_MS,
                len(synthesisSegments),
            )
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

        def feed_processed_audio(pcm: bytes) -> None:
            if not pcm:
                return
            if cacheKey is not None:
                audioParts.append(pcm)
            if collectSegmentAudio:
                segmentAudioParts.append(pcm)
            if not cancelEvent.is_set():
                livePcm = leadBuffer.feed(pcm) if leadBuffer is not None else pcm
                self._feed_audio(livePcm)

        def on_audio(pcm: bytes) -> None:
            if silenceShortener is not None:
                feed_processed_audio(silenceShortener.feed(pcm))
            else:
                feed_processed_audio(pcm)

        def on_segment_end() -> None:
            if silenceShortener is not None and not cancelEvent.is_set():
                feed_processed_audio(
                    silenceShortener.flush_boundary(
                        shortenPause=pauseShorteningMode == _PAUSE_MODE_SHORTEN_ALL,
                    )
                )
            if collectSegmentAudio:
                completedSegmentAudio.append(b"".join(segmentAudioParts))
                segmentAudioParts.clear()

        speechResult = self._bridge.speak(
            text,
            options,
            on_audio,
            cancelEvent,
            onMark=on_mark if any(0 < offset < len(text) for _index, offset in pendingIndexes) else None,
            onSegmentEnd=on_segment_end if len(synthesisSegments) > 1 else None,
            segments=bridgeSegments,
            hasPreviousSegment=cachedSegmentCount > 0,
        )
        if silenceShortener is not None:
            feed_processed_audio(silenceShortener.finish())
        if collectSegmentAudio:
            completedSegmentAudio.append(b"".join(segmentAudioParts))
            segmentAudioParts.clear()
        if leadBuffer is not None and not cancelEvent.is_set():
            self._feed_audio(leadBuffer.finish())

        audio = b"".join(audioParts) if audioParts else b""
        if pendingIndexes and not cancelEvent.is_set():
            for index, _charOffset in pendingIndexes:
                if cancelEvent.is_set():
                    return
                self._sync_player()
                synthIndexReached.notify(synth=self, index=index)
        expectedSegmentEnds = max(0, len(synthesisSegments) - 1)
        speechComplete = not cancelEvent.is_set() and is_complete_speech_result(
            speechResult,
            expectedSegmentEnds=expectedSegmentEnds,
        )
        if (
            isinstance(speechResult, dict)
            and speechResult.get("success") is True
            and speechResult.get("done") is True
            and not cancelEvent.is_set()
            and not speechComplete
        ):
            log.debug(
                "Google TTS speech completion rejected: expectedSegmentEnds=%d, actualSegmentEnds=%r.",
                expectedSegmentEnds,
                speechResult.get("segmentEnds"),
            )
        if speechComplete and segmentCacheKeys:
            expectedSegmentCount = len(synthesisSegments)
            if len(completedSegmentAudio) != expectedSegmentCount:
                log.debug(
                    "Google TTS segment cache skipped after boundary mismatch: expected=%d, actual=%d.",
                    expectedSegmentCount,
                    len(completedSegmentAudio),
                )
            else:
                for relativeIndex, segmentAudio in enumerate(completedSegmentAudio):
                    segmentIndex = cachedSegmentCount + relativeIndex
                    segmentKey = segmentCacheKeys[segmentIndex]
                    if segmentKey is not None and len(segmentAudio) >= 64 and _pcm_has_audible_sample(segmentAudio):
                        self._put_cached_audio(segmentKey, segmentAudio)
                        log.debug(
                            "Google TTS short audio cache stored: kind=segment, index=%d, chars=%d, bytes=%d.",
                            segmentIndex,
                            len(originalHiddenSegments[segmentIndex]),
                            len(segmentAudio),
                        )
        if (
            cacheKey is not None
            and speechComplete
            and not cancelEvent.is_set()
            and len(audio) >= 64
            and _pcm_has_audible_sample(audio)
        ):
            self._put_cached_audio(cacheKey, audio)
            log.debug(
                "Google TTS short audio cache stored: kind=group, chars=%d, bytes=%d.",
                len(originalText),
                len(audio),
            )

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

    def _maybe_recycle_bridge_after_request(self) -> None:
        if self._shutdownEvent.is_set():
            return
        queueIdle = not self._has_queued_speech()
        try:
            recycled = self._bridge.maybe_recycle_runtime(allowIdleRecycle=queueIdle)
        except Exception:
            log.debug("Could not recycle Google TTS Chromium runtime.", exc_info=True)
            return
        if not recycled:
            return
        self._clear_short_audio_cache()
        if queueIdle and not self._shutdownEvent.is_set():
            self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

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
        pauseShorteningMode: str = _PAUSE_MODE_DO_NOT_SHORTEN,
    ) -> tuple[Any, ...] | None:
        return short_audio_cache_key(text, options, hiddenSegments, pauseShorteningMode)

    def _segment_cache_key(
        self,
        text: str,
        options: dict[str, Any],
        pauseShorteningMode: str,
        *,
        hasPreviousSegment: bool,
        hasNextSegment: bool,
    ) -> tuple[Any, ...] | None:
        return segment_audio_cache_key(
            text,
            options,
            pauseShorteningMode,
            hasPreviousSegment=hasPreviousSegment,
            hasNextSegment=hasNextSegment,
        )

    def _get_cached_audio(self, key: tuple[Any, ...]) -> bytes | None:
        with self._cacheLock:
            audio = self._shortAudioCache.get(key)
            if audio is not None:
                self._shortAudioCacheHits += 1
                self._shortAudioCache.move_to_end(key)
                return audio
            self._shortAudioCacheMisses += 1
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
            evictionsBefore = self._shortAudioCacheEvictions
            while (
                len(self._shortAudioCache) > _SHORT_CACHE_MAX_ITEMS
                or self._shortAudioCacheBytes > _SHORT_CACHE_MAX_BYTES
            ):
                _, removedAudio = self._shortAudioCache.popitem(last=False)
                self._shortAudioCacheBytes -= len(removedAudio)
                self._shortAudioCacheEvictions += 1
            if (
                self._shortAudioCacheEvictions != evictionsBefore
                and self._shortAudioCacheEvictions
                and self._shortAudioCacheEvictions % _SHORT_CACHE_STATS_LOG_INTERVAL == 0
            ):
                log.debug(
                    "Google TTS short audio cache stats: items=%d, bytes=%d, hits=%d, misses=%d, evictions=%d",
                    len(self._shortAudioCache),
                    self._shortAudioCacheBytes,
                    self._shortAudioCacheHits,
                    self._shortAudioCacheMisses,
                    self._shortAudioCacheEvictions,
                )

    def _clear_short_audio_cache(self) -> None:
        cacheLock = getattr(self, "_cacheLock", None)
        if cacheLock is None:
            return
        with cacheLock:
            if self._shortAudioCache:
                log.debug(
                    "Clearing Google TTS short audio cache: items=%d, bytes=%d, hits=%d, misses=%d, evictions=%d",
                    len(self._shortAudioCache),
                    self._shortAudioCacheBytes,
                    getattr(self, "_shortAudioCacheHits", 0),
                    getattr(self, "_shortAudioCacheMisses", 0),
                    getattr(self, "_shortAudioCacheEvictions", 0),
                )
            self._shortAudioCache.clear()
            self._shortAudioCacheBytes = 0
            self._shortAudioCacheHits = 0
            self._shortAudioCacheMisses = 0
            self._shortAudioCacheEvictions = 0

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
        # Unmarked commands can be NVDA's normalized copies of Google profile commands.
        # External NVDA/app language changes are stripped earlier by the speech filter.
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
        if not language:
            return True
        try:
            voiceLanguage = self.catalog.language_for_voice(voice)
        except Exception:
            return False
        return self._language_matches(voiceLanguage, language)

    def _auto_language_notice_message(self) -> str:
        return _(
            "Voice settings are managed by automatic language profiles. "
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
        availableByKey = {self._normalize_language(language): language for language in self.availableLanguages}
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
        try:
            fallbackLanguage = self.catalog.language_for_voice(fallbackVoice)
        except Exception:
            fallbackLanguage = fallbackVoice
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
        return _language_token_signal(token, candidateRoots, is_url_token=self._looks_like_url_token)

    def _language_root(self, language: str | None) -> str:
        return self._normalize_language(language).split("-", 1)[0]

    def _speech_options(
        self,
        rate: int,
        pitch: int,
        volume: int,
        voice: str | None = None,
        rateBoost: bool | None = None,
    ) -> dict[str, Any]:
        speaker = self.catalog.speaker_for_voice(voice or self._current_speaker_id())
        package = self.catalog.package_for_voice(speaker.id)
        rateBoostVal = bool(self._rateBoost if rateBoost is None else rateBoost)
        options = audio_math.build_speech_options(
            speaker_id=speaker.id,
            speaker_name=speaker.name,
            lang=speaker.language,
            package_id=package.id,
            rate=rate,
            pitch=pitch,
            volume=volume,
            rateBoost=rateBoostVal,
        )
        return options

    def _uses_protected_engine_rate(self, packageId: str) -> bool:
        return audio_math.uses_protected_engine_rate(packageId)

    def _voice_for_language(self, lang: str | None, fallbackVoice: str) -> str:
        if not lang:
            return self._speaker_for_voice_or_language(fallbackVoice)
        normalizedLang = self._normalize_language(lang)
        if not normalizedLang:
            return self._speaker_for_voice_or_language(fallbackVoice)
        fallbackVoice = self._speaker_for_voice_or_language(fallbackVoice)
        fallbackSpeaker = self.catalog.speaker_for_voice(fallbackVoice)
        if self._language_matches(fallbackSpeaker.language, normalizedLang):
            return fallbackVoice
        for speaker in self._speakers_for_language(normalizedLang):
            return speaker.id
        # Language redirect: map unsupported locale to best available alternative.
        redirected = language_detector.redirect_language(normalizedLang, self.availableLanguages)
        if redirected:
            for speaker in self._speakers_for_language(redirected):
                return speaker.id
        rootLang = normalizedLang.split("-", 1)[0]
        if self._normalize_language(fallbackSpeaker.language).split("-", 1)[0] == rootLang:
            return fallbackVoice
        for language, speakers in self._speakersByLanguage.items():
            if self._normalize_language(language).split("-", 1)[0] == rootLang:
                return speakers[0].id
        return fallbackVoice

    def _speaker_for_voice_or_language(self, value: str | None) -> str:
        if value:
            try:
                return self.catalog.speaker_for_voice(value).id
            except Exception:
                pass
            for speaker in self._speakers_for_language(value):
                return speaker.id
        return self._current_speaker_id()

    def _normalize_language(self, lang: str | None) -> str:
        return language_utils.normalize_language(lang)

    def _language_match_keys(self, language: str | None) -> set[str]:
        key = self._normalize_language(language)
        if not key:
            return set()
        return language_detector.language_match_keys(key)

    def _language_matches(self, left: str | None, right: str | None) -> bool:
        return language_detector.language_matches(left, right)

    def _rate_to_chrome(self, value: int, rateBoost: bool | None = None) -> float:
        return audio_math.rate_to_chrome(value, bool(self._rateBoost if rateBoost is None else rateBoost))

    def _pitch_to_chrome(self, pitch: int) -> float:
        return audio_math.pitch_to_chrome(pitch)

    def _get_voice(self) -> str:
        return self.__voice

    def _set_voice(self, value: str) -> None:
        if value not in self.availableVoices:
            value = next(iter(self.availableVoices))
        self.__voice = value
        self._availableVariants = self._build_available_variants(value)
        if getattr(self, "_SynthDriver__variant", "") not in self._availableVariants:
            self.__variant = next(iter(self._availableVariants))
        self._clear_short_audio_cache()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

    def _get_variant(self) -> str:
        return self._current_speaker_id()

    def _set_variant(self, value: str) -> None:
        variants = self._getAvailableVariants()
        if value in variants:
            self.__variant = value
        else:
            self.__variant = next(iter(variants))
        self._clear_short_audio_cache()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

    def _getAvailableVariants(self) -> OrderedDict[str, VoiceInfo]:
        return self._build_available_variants(self.__voice)

    def _current_speaker_id(self) -> str:
        variants = self._build_available_variants(self.__voice)
        if getattr(self, "_SynthDriver__variant", "") in variants:
            return self.__variant
        self._availableVariants = variants
        self.__variant = next(iter(variants))
        return self.__variant

    def _warmup_voice_ids(self) -> list[str]:
        currentVoice = self._current_speaker_id()
        if not self._auto_language_detection_enabled():
            return self._warmup_voice_ids_for_voice(currentVoice)
        candidateLanguages = self._auto_language_candidates_in_warmup_order(currentVoice)
        if not candidateLanguages:
            return self._warmup_voice_ids_for_voice(currentVoice)

        voiceIds: list[str] = []
        seenPackages: set[str] = set()
        for language in candidateLanguages:
            profile = self._auto_language_profile(
                language,
                currentVoice,
                self._rate,
                self._rateBoost,
                self._pitch,
                self._volume,
            )
            voiceId = str(profile.get("voice") or "")
            if not voiceId:
                continue
            for warmupVoiceId in self._warmup_voice_ids_for_voice(voiceId):
                try:
                    packageId = self.catalog.package_for_voice(warmupVoiceId).id
                except Exception:
                    log.debug("Could not resolve Google TTS preload package for %s.", warmupVoiceId, exc_info=True)
                    continue
                if packageId in seenPackages:
                    continue
                seenPackages.add(packageId)
                voiceIds.append(warmupVoiceId)
        return voiceIds or [currentVoice]

    def _auto_language_candidates_in_warmup_order(self, currentVoice: str) -> list[str]:
        candidateLanguages = self._auto_language_candidates()
        if len(candidateLanguages) <= 1:
            return candidateLanguages
        orderedLanguages = list(candidateLanguages)
        preferredLanguage = self._auto_language_preferred(orderedLanguages, currentVoice)
        if preferredLanguage in orderedLanguages:
            orderedLanguages.remove(preferredLanguage)
            orderedLanguages.insert(0, preferredLanguage)
        return orderedLanguages

    def _warmup_voice_ids_for_voice(self, voiceId: str, seenPackages: set[str] | None = None) -> list[str]:
        if seenPackages is None:
            seenPackages = set()
        try:
            speaker = self.catalog.speaker_for_voice(voiceId)
            package = self.catalog.package_for_voice(voiceId)
        except Exception:
            log.debug("Could not resolve Google TTS preload voice %s.", voiceId, exc_info=True)
            return []
        if package.id in seenPackages:
            return []
        seenPackages.add(package.id)
        voiceIds: list[str] = []
        if package.dependentVoiceId:
            dependencyVoiceId = self._voice_id_for_package(package.dependentVoiceId, speaker.speaker)
            if dependencyVoiceId:
                voiceIds.extend(self._warmup_voice_ids_for_voice(dependencyVoiceId, seenPackages))
        if voiceId not in voiceIds:
            voiceIds.append(voiceId)
        return voiceIds

    def _voice_id_for_package(self, packageId: str, preferredSpeaker: str | None = None) -> str:
        speakers = self._speakersByPackage.get(packageId, [])
        fallbackVoiceId = speakers[0].id if speakers else ""
        for speaker in speakers:
            if preferredSpeaker and speaker.speaker == preferredSpeaker:
                return speaker.id
        return fallbackVoiceId

    def _warmup_options_for_voice_ids(self, voiceIds: list[str]) -> list[dict[str, Any]]:
        optionsList: list[dict[str, Any]] = []
        for voiceId in voiceIds:
            try:
                optionsList.append(self._speech_options(self._rate, self._pitch, 0, voiceId, self._rateBoost))
            except Exception:
                log.debug("Could not prepare Google TTS preload options for %s.", voiceId, exc_info=True)
        return optionsList

    def _warm_current_voice_async(self, delay: float = 0.0) -> None:
        if self._shutdownEvent.is_set():
            return
        priorityVoiceIds = self._warmup_voice_ids()
        priorityOptionsList = self._warmup_options_for_voice_ids(priorityVoiceIds)
        if not priorityOptionsList:
            return
        with suppress(Exception):
            self._warmupCancelEvent.set()
        cancelEvent = threading.Event()
        self._warmupCancelEvent = cancelEvent

        def preload_options(optionsList: list[dict[str, Any]]) -> bool:
            for options in optionsList:
                if cancelEvent.is_set() or self._shutdownEvent.is_set():
                    return False
                try:
                    warmupOptions = dict(options)
                    warmupOptions["warmupText"] = _VOICE_WARMUP_TEXT
                    self._bridge.preload_voice(warmupOptions, cancelEvent)
                except CdpCancelled:
                    log.debug("Google TTS voice preload cancelled.")
                    return False
                except Exception:
                    log.debug("Google TTS voice preload failed.", exc_info=True)
            return True

        def warm() -> None:
            if delay > 0 and cancelEvent.wait(delay):
                return
            try:
                self._bridge.ensure_connection(cancelEvent=cancelEvent)
            except CdpCancelled:
                log.debug("Google TTS bridge eager connection cancelled.")
                return
            except Exception:
                log.debug("Google TTS bridge eager connection failed.", exc_info=True)
                return
            if cancelEvent.is_set() or self._shutdownEvent.is_set():
                return
            preload_options(priorityOptionsList)

        thread = threading.Thread(name="googleTtsForNvda.preload", target=warm, daemon=True)
        self._warmupThread = thread
        thread.start()

    def _get_language(self) -> str:
        return language_utils.resolve_nvda_locale(self.__voice)

    def _nvda_locale_exists(self, locale: str) -> bool:
        return language_utils.nvda_locale_exists(locale)

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

    def _get_availablePausemodes(self) -> OrderedDict[str, StringParameterInfo]:
        return self._pauseModes

    def _get_pauseMode(self) -> str:
        return self._pauseMode

    def _set_pauseMode(self, value: str) -> None:
        value = str(value)
        pauseMode = value if value in self._pauseModes else _PAUSE_MODE_DO_NOT_SHORTEN
        if pauseMode == self._pauseMode:
            return
        self._pauseMode = pauseMode
        self._clear_short_audio_cache()

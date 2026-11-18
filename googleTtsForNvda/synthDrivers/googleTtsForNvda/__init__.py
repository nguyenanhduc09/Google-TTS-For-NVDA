from __future__ import annotations

import inspect
import json
import math
import queue
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext, suppress
from typing import Any

import addonHandler
import config
import globalVars
import nvwave
import synthDriverHandler
import wx

try:
    import speechDictHandler
except ImportError:
    speechDictHandler = None  # type: ignore[assignment]
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
    ensure_config_spec,
)
from .catalog import EngineLibraryError, VoiceCatalog, engine_library_error_message
from .language_profiles import (
    LANGUAGE_WORD_RE as _LANGUAGE_WORD_RE,
)
from .language_profiles import (
    has_language_words as _has_language_words,
)
from .language_profiles import (
    language_token_signal as _language_token_signal,
)
from .language_profiles import (
    normalize_mathematical_alphanumeric as _normalize_mathematical_alphanumeric,
)
from .speech_processing import (
    DEFAULT_TEXT_SEGMENTER as _TEXT_SEGMENTER,
)
from .speech_processing import (
    LIVE_MULTI_SEGMENT_LEAD_MS,
    PcmLeadBuffer,
    create_pcm_silence_shortener,
    effective_chrome_rate,
    is_complete_speech_result,
    keep_silence_ms_for_rate,
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
_BREAK_RATE_FACTOR_MIN = audio_math.BREAK_RATE_FACTOR_MIN
_BREAK_RATE_FACTOR_MAX = audio_math.BREAK_RATE_FACTOR_MAX
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


_BREAK_RATE_TABLE = audio_math.BREAK_RATE_TABLE
_interpolate_rate_factor = audio_math.interpolate_rate_factor
_break_rate_factor = audio_math.break_rate_factor


_PENDING_POST_STARTUP_HANDLERS: set[Callable[..., Any]] = set()
_call_compat = language_utils.call_compat


def _show_message_box(
    message: str,
    caption: str,
    style: int,
    parent: Any = None,
) -> int:
    """Display a modal message box across NVDA 2024–2026+ and main."""
    try:
        from globalPlugins.googleTtsForNvda.uiUtils import show_message_box

        return int(show_message_box(message, caption, style, parent))
    except Exception:
        return language_utils.show_message_box(message, caption, style, parent)


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
        availableInSettingsRing=False,
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

    def isSupported(self, settingID: str, *args: Any, **kwargs: Any) -> bool:
        # Always report voice support so NVDA's loadSettings() initializes the
        # active voice instead of calling changeVoice(self, None) when
        # Automatic Language Profiles hides voice controls from supportedSettings.
        if settingID == "voice":
            return True
        return bool(_call_compat(super().isSupported, settingID, *args, **kwargs))

    def languageIsSupported(self, lang: str | None = None, *args: Any, **kwargs: Any) -> bool:
        if lang is None:
            return True
        if self._auto_language_detection_enabled():
            candidates = self._auto_language_candidates()
            if candidates:
                return bool(self._auto_language_candidate_for_language(lang, candidates))
        return bool(self._speakers_for_language(lang))

    @classmethod
    def check(cls, *args: Any, **kwargs: Any) -> bool:
        # Keep the driver visible; runtime dependencies are validated when selected.
        return True

    @staticmethod
    def _schedule_ui_callback(func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        getApp = getattr(wx, "GetApp", None)
        if not callable(getApp) or getApp() is not None:
            wx.CallAfter(func, *args, **kwargs)
            return
        try:
            import core

            postNvdaStartup = getattr(core, "postNvdaStartup", None)
            register = getattr(postNvdaStartup, "register", None)
            unregister = getattr(postNvdaStartup, "unregister", None)
            if callable(register):

                def _on_post_startup(*_eventArgs: Any, **_eventKwargs: Any) -> None:
                    _PENDING_POST_STARTUP_HANDLERS.discard(_on_post_startup)
                    if callable(unregister):
                        with suppress(Exception):
                            unregister(_on_post_startup)
                    wx.CallAfter(func, *args, **kwargs)

                # Hold a strong reference because NVDA's HandlerRegistrar only stores weakrefs.
                _PENDING_POST_STARTUP_HANDLERS.add(_on_post_startup)
                try:
                    register(_on_post_startup)
                except Exception:
                    _PENDING_POST_STARTUP_HANDLERS.discard(_on_post_startup)
                    raise
                return
        except Exception:
            log.debug("Could not defer Google TTS UI callback until postNvdaStartup.", exc_info=True)
        with suppress(Exception):
            wx.CallAfter(func, *args, **kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _call_compat(super().__init__, *args, **kwargs)
        ensure_config_spec()
        try:
            fullCatalog = VoiceCatalog.load()
        except EngineLibraryError as exc:
            self._schedule_ui_callback(self._show_engine_library_error, exc)
            raise RuntimeError(self._engine_library_error_message(exc)) from exc
        installedPackages = voice_store.installed_packages(fullCatalog)
        if not installedPackages:
            # Defer UI until after this constructor aborts so synth startup is
            # not blocked by a modal dialog waiting for user input.
            self._schedule_ui_callback(self._prompt_for_voice_install)
            raise RuntimeError(
                _(
                    "No Google TTS For NVDA voice packages are installed. "
                    "Open Google TTS Voice Manager to download a voice package."
                )
            )
        self.catalog = VoiceCatalog(installedPackages)
        if not self.catalog.speakers:
            self._schedule_ui_callback(
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
            self._schedule_ui_callback(self._prompt_for_edge_webview2_install)
            raise RuntimeError(
                _(
                    "Microsoft Edge WebView2 Runtime was not found. "
                    "Install it before using Microsoft Edge as the Google TTS For NVDA Chromium browser runtime."
                )
            )
        if ChromeTtsBridge.find_browser() is None:
            self._schedule_ui_callback(self._show_missing_chrome_error)
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
        self._availableVoices = self._build_available_voices()
        self._store_available_languages(set(self._speakersByLanguage))
        self._playerLock = threading.RLock()
        self._catalogLock = threading.RLock()
        self._warmupLock = threading.Lock()
        self._playerOutputDevice = self._current_output_device()
        self._player = self._create_wave_player(self._playerOutputDevice)
        self._bridge = standby.claim_bridge(self.catalog) or ChromeTtsBridge(self.catalog)
        self._speechCondition = threading.Condition()
        self._speechQueue: deque[_SpeechRequest] = deque()
        self._activeCancelEvent: threading.Event | None = None
        self._shutdownEvent = threading.Event()
        self._fallbackTriggered = False
        self._consecutiveRuntimeErrors = 0
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
        self._isPaused = False
        self._warmupThread: threading.Thread | None = None
        self._warmupCancelEvent = threading.Event()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

    def _player_lock(self) -> threading.RLock | AbstractContextManager[Any]:
        lock = getattr(self, "_playerLock", None)
        return lock if lock is not None else nullcontext()

    def _catalog_lock(self) -> threading.RLock | AbstractContextManager[Any]:
        lock = getattr(self, "_catalogLock", None)
        return lock if lock is not None else nullcontext()

    def _warmup_lock(self) -> threading.Lock | AbstractContextManager[Any]:
        lock = getattr(self, "_warmupLock", None)
        return lock if lock is not None else nullcontext()

    def _is_shutting_down(self) -> bool:
        shutdownEvent = getattr(self, "_shutdownEvent", None)
        return shutdownEvent is not None and shutdownEvent.is_set()

    def _current_player(self) -> Any:
        with self._player_lock():
            return getattr(self, "_player", None)

    @staticmethod
    def _is_secure_mode() -> bool:
        try:
            return bool(getattr(getattr(globalVars, "appArgs", None), "secure", False))
        except Exception:
            return False

    _NON_BLOCKING_MODAL_DIALOG_NAMES = (
        "SettingsDialog",
        "SynthesizerDialog",
        "SynthesizerSelectionDialog",
        "VoiceManagerDialog",
    )

    def _has_blocking_modal_dialog(self) -> bool:
        for win in wx.GetTopLevelWindows():
            try:
                if not win.IsShown():
                    continue
                clsName = win.__class__.__name__
                # Wait if there is an active MessageDialog (NVDA error dialog)
                # or any modal dialog other than settings/voice manager dialogs.
                if "MessageDialog" in clsName:
                    return True
                if isinstance(win, wx.Dialog) and getattr(win, "IsModal", lambda: False)():
                    if not any(known in clsName for known in self._NON_BLOCKING_MODAL_DIALOG_NAMES):
                        return True
            except RuntimeError:
                continue
        return False

    def _run_prompt_when_modal_dialogs_clear(
        self,
        callback: Callable[[], None],
        retries: int = 200,
    ) -> None:
        if self._is_secure_mode():
            return

        def prompt_when_ready(remaining: int = retries) -> None:
            if remaining <= 0 or self._is_secure_mode():
                return
            try:
                import core

                if getattr(core, "_hasShutdownBeenTriggered", False):
                    return
            except Exception:
                pass
            if self._has_blocking_modal_dialog():
                wx.CallLater(150, prompt_when_ready, remaining - 1)
                return
            callback()

        # Start checking after 250ms to allow NVDA to catch the RuntimeError,
        # restore the fallback synthesizer, and display its own warning message box.
        wx.CallLater(250, prompt_when_ready)

    def _prompt_for_voice_install(self, message: str | None = None) -> None:
        # Fallback for direct synth-driver loads when the global plugin did
        # not intercept synth selection before this constructor was reached.
        def _show_prompt() -> None:
            try:
                from globalPlugins.googleTtsForNvda import open_voice_manager_download_tab

                answer = _show_message_box(
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
                )
                if answer == getattr(wx, "ID_OK", wx.OK) or answer == wx.OK:
                    open_voice_manager_download_tab()
            except Exception:
                log.exception("Could not show Google TTS voice install prompt.", exc_info=True)

        self._run_prompt_when_modal_dialogs_clear(_show_prompt)

    def _prompt_for_edge_webview2_install(self) -> None:
        def _show_prompt() -> None:
            try:
                from globalPlugins.googleTtsForNvda import show_edge_webview2_prompt

                show_edge_webview2_prompt()
            except Exception:
                log.exception("Could not show Microsoft Edge WebView2 Runtime prompt.", exc_info=True)

        self._run_prompt_when_modal_dialogs_clear(_show_prompt)

    def _engine_library_error_message(self, error: EngineLibraryError) -> str:
        return engine_library_error_message(error)

    def _show_runtime_error_message(self, message: str) -> None:
        try:
            from globalPlugins.googleTtsForNvda.uiUtils import show_runtime_error_dialog

            show_runtime_error_dialog(message)
        except Exception:
            try:
                _show_message_box(
                    message,
                    _("Google TTS For NVDA"),
                    wx.OK | wx.ICON_ERROR,
                )
            except Exception:
                log.exception("Could not show Google TTS runtime error dialog.", exc_info=True)

    def _show_engine_library_error(self, error: EngineLibraryError) -> None:
        log.error("Google TTS WASM TTS Engine error: %s", error.technicalDetail)
        self._show_runtime_error_message(self._engine_library_error_message(error))

    def _show_missing_chrome_error(self) -> None:
        self._show_runtime_error_message(
            _(
                "No supported Chromium browser runtime was found. Install Google Chrome, Microsoft Edge, or Brave, or set CHROME_PATH, EDGE_PATH, or BRAVE_PATH to a browser executable."
            )
        )

    def _trigger_fatal_fallback_async(self, message: str) -> None:
        getApp = getattr(wx, "GetApp", None)
        callAfter = getattr(wx, "CallAfter", None)
        if callable(callAfter) and (not callable(getApp) or getApp() is not None):
            try:
                callAfter(self._trigger_fatal_fallback, message)
                return
            except Exception:
                pass
        self._trigger_fatal_fallback(message)

    def _trigger_fatal_fallback(self, message: str) -> None:
        if self._fallbackTriggered or self._shutdownEvent.is_set():
            return
        self._fallbackTriggered = True
        log.error("Google TTS encountered a fatal runtime error: %s", message)
        with self._speechCondition:
            self._speechQueue.clear()
        try:
            self.cancel()
        except Exception:
            log.debug("Could not cancel Google TTS speech before fallback.", exc_info=True)

        try:
            import synthDriverHandler

            isMain = getattr(wx, "IsMainThread", None)
            getApp = getattr(wx, "GetApp", None)
            if (
                callable(isMain)
                and not isMain()
                and callable(getApp)
                and getApp() is not None
                and callable(getattr(wx, "CallAfter", None))
            ):
                wx.CallAfter(_call_compat, synthDriverHandler.findAndSetNextSynth, self.name)
            else:
                _call_compat(synthDriverHandler.findAndSetNextSynth, self.name)
        except Exception:
            log.exception("Could not switch to NVDA fallback synthesizer.", exc_info=True)

        try:
            from globalPlugins.googleTtsForNvda.uiUtils import show_runtime_error_dialog

            show_runtime_error_dialog(message=message, delayMs=150)
        except Exception:

            def show_dialog() -> None:
                try:
                    _show_message_box(
                        message or _("Google TTS For NVDA could not start speech in the Chromium browser runtime."),
                        _("Google TTS For NVDA"),
                        wx.OK | wx.ICON_ERROR,
                    )
                except Exception:
                    log.exception("Could not show Google TTS fatal error dialog.", exc_info=True)

            try:
                self._schedule_ui_callback(lambda: wx.CallLater(150, show_dialog))
            except Exception:
                show_dialog()

    def terminate(self, *args: Any, **kwargs: Any) -> None:
        with self._warmup_lock():
            self._shutdownEvent.set()
            with suppress(Exception):
                self._warmupCancelEvent.set()
        self.cancel()
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
        for threadAttr in ("_worker", "_warmupThread"):
            thread = getattr(self, threadAttr, None)
            if isinstance(thread, threading.Thread) and thread is not threading.current_thread() and thread.is_alive():
                thread.join(timeout=1.5)
        with self._player_lock():
            player = getattr(self, "_player", None)
            self._player = None
        if player is not None:
            with suppress(Exception):
                player.close()
        with suppress(Exception):
            self._clear_short_audio_cache()

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
        with self._player_lock():
            self._isPaused = False
        with self._speechCondition:
            if self._activeCancelEvent is not None:
                self._activeCancelEvent.set()
            for request in self._speechQueue:
                request[-1].set()
            self._speechQueue.clear()
            self._speechCondition.notify_all()
        with suppress(Exception):
            self._bridge.cancel_current()
        player = self._current_player()
        if player is not None:
            with suppress(Exception):
                player.stop()

    def pause(self, switch: bool, *args: Any, **kwargs: Any) -> None:
        paused = bool(switch)
        with self._player_lock():
            self._isPaused = paused
            player = getattr(self, "_player", None)
            if player is not None:
                with suppress(Exception):
                    player.pause(paused)

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

    def _create_wave_player(self, outputDevice: str, *args: Any, **kwargs: Any) -> WavePlayer:
        playerKwargs: dict[str, Any] = {
            "channels": 1,
            "samplesPerSec": SAMPLE_RATE,
            "bitsPerSample": 16,
            "outputDevice": outputDevice,
            "wantDucking": True,
        }
        speechPurpose = getattr(getattr(nvwave, "AudioPurpose", None), "SPEECH", None)
        if speechPurpose is not None:
            playerKwargs["purpose"] = speechPurpose
        playerKwargs.update(kwargs)
        try:
            return _call_compat(WavePlayer, *args, **playerKwargs)
        except TypeError:
            coreKwargs = {key: value for key, value in playerKwargs.items() if key not in ("wantDucking", "purpose")}
            try:
                return _call_compat(WavePlayer, *args, **coreKwargs)
            except TypeError:
                fallbackKwargs = {key: value for key, value in coreKwargs.items() if key != "outputDevice"}
                try:
                    return _call_compat(WavePlayer, *args, **fallbackKwargs)
                except TypeError:
                    return _call_compat(WavePlayer, 1, SAMPLE_RATE, 16)

    def _recreate_wave_player(self, outputDevice: str | None = None) -> None:
        with self._player_lock():
            if self._is_shutting_down():
                return
            targetDevice = self._current_output_device() if outputDevice is None else outputDevice
            newPlayer = self._create_wave_player(targetDevice)
            oldPlayer = getattr(self, "_player", None)
            self._playerOutputDevice = targetDevice
            self._player = newPlayer
            if getattr(self, "_isPaused", False):
                with suppress(Exception):
                    newPlayer.pause(True)
        if oldPlayer is not None:
            with suppress(Exception):
                oldPlayer.close()

    def _ensure_current_output_device(self) -> None:
        outputDevice = self._current_output_device()
        deviceError = self._audio_device_error()
        if outputDevice == self._playerOutputDevice and not deviceError:
            return
        self._recreate_wave_player(outputDevice)

    def _build_available_voices(self) -> OrderedDict[str, VoiceInfo]:
        voices: OrderedDict[str, VoiceInfo] = OrderedDict()
        for language in self._speakersByLanguage:
            voices[language] = VoiceInfo(language, self._language_display_name(language), language)
        return voices

    def _getAvailableVoices(self, *args: Any, **kwargs: Any) -> OrderedDict[str, VoiceInfo]:
        voices = getattr(self, "_availableVoices", None)
        if voices is not None:
            return voices
        if hasattr(self, "_speakersByLanguage"):
            return self._build_available_voices()
        return OrderedDict()

    def _language_display_name(self, language: str) -> str:
        return language_utils.get_language_display_name(language)

    def _build_speakers_by_package(self) -> dict[str, list[Any]]:
        return standby._speakers_by_package(self.catalog)

    def _build_speaker_voice_infos(self) -> OrderedDict[str, VoiceInfo]:
        voices: OrderedDict[str, VoiceInfo] = OrderedDict()
        for speaker in self.catalog.speakers:
            label = f"{speaker.name} ({speaker.language})"
            voices[speaker.id] = VoiceInfo(speaker.id, label, speaker.language)
        return voices

    def _speaker_voice_infos(self) -> OrderedDict[str, VoiceInfo]:
        with self._catalog_lock():
            return OrderedDict(self._speakerVoiceInfos)

    def _speakers_for_language(self, language: str | None) -> list[Any]:
        if not language:
            return []
        with self._catalog_lock():
            speakers = self._speakersByLanguage.get(language)
            if speakers is not None:
                return list(speakers)
            matches: list[Any] = []
            for speakerLanguage, languageSpeakers in self._speakersByLanguage.items():
                if self._language_matches(speakerLanguage, language):
                    matches.extend(languageSpeakers)
            return matches

    def _build_available_variants(self, language: str | None = None) -> OrderedDict[str, VoiceInfo]:
        with self._catalog_lock():
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

    def _store_available_languages(self, languages: set[str]) -> None:
        self._availableLanguages = set(languages)
        descriptor = getattr(type(self), "availableLanguages", None)
        if not (isinstance(descriptor, property) and descriptor.fset is None):
            self.availableLanguages = self._availableLanguages

    def _get_availableLanguages(self, *args: Any, **kwargs: Any) -> set[str]:
        languages = getattr(self, "_availableLanguages", None)
        if languages is not None:
            return set(languages)
        if hasattr(self, "_speakersByLanguage"):
            return set(self._speakersByLanguage)
        voices = getattr(self, "_availableVoices", None)
        if isinstance(voices, dict):
            return {voice.language for voice in voices.values() if getattr(voice, "language", None)}
        return set()

    def _get_availableNotices(self, *args: Any, **kwargs: Any) -> OrderedDict[str, VoiceInfo]:
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
        return next(iter(self.availableVoices), "")

    def _initial_variant(self, language: str) -> str:
        variants = self._build_available_variants(language)
        try:
            configuredVariant = config.conf["speech"][self.name]["variant"]
            if configuredVariant in variants:
                return configuredVariant
        except Exception:
            pass
        return next(iter(variants), "")

    def refresh_installed_catalog(self) -> bool:
        try:
            fullCatalog = VoiceCatalog.load()
            installedPackages = voice_store.installed_packages(fullCatalog)
        except Exception:
            log.debug("Could not reload installed Google TTS voice catalog.", exc_info=True)
            return False
        newCatalog = VoiceCatalog(installedPackages)
        if not newCatalog.speakers:
            return False
        with self._catalog_lock():
            self.catalog = newCatalog
            self._speakersByLanguage = self.catalog.voices_by_language()
            self._speakersByPackage = self._build_speakers_by_package()
            self._speakerVoiceInfos = self._build_speaker_voice_infos()
            self._variantsByLanguage = {}
            self._availableVoices = self._build_available_voices()
            self._store_available_languages(set(self._speakersByLanguage))
            if self.__voice not in self.availableVoices:
                self.__voice = self._initial_voice()
            self._availableVariants = self._build_available_variants(self.__voice)
            if self.__variant not in self._availableVariants:
                self.__variant = self._initial_variant(self.__voice)
            self._clear_short_audio_cache()
            updateBridgeCatalog = getattr(self._bridge, "update_catalog", None)
            if callable(updateBridgeCatalog):
                with suppress(Exception):
                    updateBridgeCatalog(self.catalog)
            else:
                self._bridge.catalog = self.catalog
        self._ensure_config_compat()
        try:
            settingsRing = getattr(globalVars, "settingsRing", None)
            if settingsRing is not None:
                settingsRing.updateSupportedSettings(self)
        except Exception:
            log.debug("Could not update NVDA settings ring after Google TTS catalog refresh.", exc_info=True)
        self._reload_voice_dict()
        return True

    def _ensure_config_compat(self) -> None:
        ensure_config_spec()
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
        _call_compat(super().loadSettings, onlyChanged, *args, **kwargs)
        self._reload_voice_dict()

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
        sawIndexSinceLastText = False
        lastGroupProfileRate: int | None = None
        activeVoice = voice
        activeLanguage: str | None = None
        activeRateCommand: RateCommand | None = None
        activePitchCommand: PitchCommand | None = None
        activeVolumeCommand: VolumeCommand | None = None
        _inCharMode = False

        lastSpokenItemIndex = -1
        for seqIdx, seqItem in enumerate(speechSequence):
            seqItemType = type(seqItem)
            if seqItemType is str:
                if self._sanitize_speech_text(_normalize_mathematical_alphanumeric(seqItem)).strip():
                    lastSpokenItemIndex = seqIdx
            elif seqItemType is PhonemeCommand and getattr(seqItem, "text", None):
                if self._sanitize_speech_text(_normalize_mathematical_alphanumeric(seqItem.text)).strip():
                    lastSpokenItemIndex = seqIdx

        def is_trailing_command_at_utterance_end(seqIdx: int) -> bool:
            if seqIdx <= lastSpokenItemIndex:
                return False
            return not any(type(rem) is BreakCommand for rem in speechSequence[seqIdx + 1 :])

        def append_normalized_text(normalizedItem: str) -> None:
            nonlocal textCharCount, sawIndexSinceLastText
            if not normalizedItem:
                return
            if _inCharMode:
                # Space out items spoken under CharacterModeCommand while keeping
                # textCharCount aligned with IndexCommand offsets even after
                # a trailing CharacterModeCommand(False).
                if textParts and textParts[-1] and not textParts[-1][-1].isspace() and not normalizedItem[0].isspace():
                    textParts.append(" ")
                    textCharCount += 1
            elif (
                sawIndexSinceLastText
                and textParts
                and textParts[-1]
                and _TEXT_SEGMENTER.needs_index_boundary_space(textParts[-1][-1], normalizedItem[0])
            ):
                textParts.append(" ")
                textCharCount += 1
            sawIndexSinceLastText = False
            textParts.append(normalizedItem)
            textCharCount += len(normalizedItem)

        def flush_text(*, isUtteranceEnd: bool = False, beforeBreak: bool = False) -> Iterator[tuple[str, Any]]:
            nonlocal firstTextSegment, textCharCount, pendingIndexes, sawIndexSinceLastText
            rawText = "".join(textParts)
            textParts.clear()
            textCharCount = 0
            sawIndexSinceLastText = False
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
            trailingUtteranceIndexes: list[Any] = []
            if isUtteranceEnd:
                trailingUtteranceIndexes = [index for index, charOffset in indexes if charOffset >= len(text)]
                indexes = [(index, charOffset) for index, charOffset in indexes if charOffset < len(text)]
            segments = list(self._iter_indexed_text_segments(text, indexes, firstTextSegment))
            groupedSegments: list[tuple[str, list[_IndexMarker]]] = []

            def flush_grouped_segments(
                pauseShorteningMode: str = _PAUSE_MODE_DO_NOT_SHORTEN,
            ) -> Iterator[tuple[str, Any]]:
                nonlocal firstTextSegment, lastGroupProfileRate
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
                lastGroupProfileRate = groupProfile["rate"]
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
                            if pauseMode == _PAUSE_MODE_SHORTEN_ALL
                            else _PAUSE_MODE_DO_NOT_SHORTEN,
                        )
                        yield (
                            "break",
                            self._sentence_break_milliseconds(pauseMode)
                            if isSentenceBoundary
                            else _SHORTENED_SENTENCE_BREAK_MS,
                        )
            lastSegment = segments[-1][0] if segments else ""
            lastSegmentEndsWithPunct = self._ends_with_pause_punctuation(lastSegment)
            if pauseMode == _PAUSE_MODE_SHORTEN_ALL:
                finalShorteningMode = _PAUSE_MODE_SHORTEN_ALL
            elif isUtteranceEnd:
                finalShorteningMode = (
                    _PAUSE_MODE_SHORTEN_END_ONLY
                    if pauseMode == _PAUSE_MODE_SHORTEN_END_ONLY and not lastSegmentEndsWithPunct
                    else _PAUSE_MODE_DO_NOT_SHORTEN
                )
            elif beforeBreak:
                finalShorteningMode = _PAUSE_MODE_DO_NOT_SHORTEN
            else:
                finalShorteningMode = (
                    _PAUSE_MODE_DO_NOT_SHORTEN if lastSegmentEndsWithPunct else _PAUSE_MODE_SHORTEN_END_ONLY
                )
            yield from flush_grouped_segments(finalShorteningMode)
            for index in trailingUtteranceIndexes:
                if cancelEvent.is_set():
                    return
                yield ("index", index)

        for seqIdx, item in enumerate(speechSequence):
            if cancelEvent.is_set():
                return
            itemType = type(item)
            if itemType is str:
                append_normalized_text(_normalize_mathematical_alphanumeric(item))
            elif itemType is BreakCommand:
                yield from flush_text(isUtteranceEnd=False, beforeBreak=True)
                if cancelEvent.is_set():
                    return
                breakMs = max(0, int(item.time))
                if breakMs > 0:
                    baseBreakRate = (
                        lastGroupProfileRate
                        if lastGroupProfileRate is not None
                        else self._auto_detect_profile_for_text(
                            "",
                            activeVoice,
                            activeLanguage,
                            voice,
                            rate,
                            rateBoost,
                            pitch,
                            volume,
                        )["rate"]
                    )
                    effectiveBreakRate = self._apply_prosody_command(baseBreakRate, activeRateCommand)
                    if effectiveBreakRate >= 0:
                        breakMs = int(breakMs * _break_rate_factor(effectiveBreakRate))
                yield ("break", breakMs)
            elif itemType is CharacterModeCommand:
                _inCharMode = bool(item.state)
                if textParts:
                    sawIndexSinceLastText = True
            elif itemType is IndexCommand:
                pendingIndexes.append((item.index, textCharCount))
                if textParts:
                    sawIndexSinceLastText = True
            elif itemType is LangChangeCommand or isinstance(item, language_detector.GoogleTtsProfileLangCommand):
                if not self._auto_language_detection_enabled():
                    continue
                if isinstance(item, language_detector.GoogleTtsProfileLangCommand):
                    googleLanguage = item.language
                else:
                    googleLanguage = getattr(item, _GOOGLE_TTS_LANG_CHANGE_ATTR, _MISSING_GOOGLE_TTS_LANGUAGE)
                    if googleLanguage is _MISSING_GOOGLE_TTS_LANGUAGE:
                        googleLanguage = getattr(item, "lang", None)
                yield from flush_text(isUtteranceEnd=is_trailing_command_at_utterance_end(seqIdx))
                if cancelEvent.is_set():
                    return
                lastGroupProfileRate = None
                activeLanguage = googleLanguage if isinstance(googleLanguage, str) else None
                activeVoice = self._voice_for_language(activeLanguage, voice)
            elif itemType is PhonemeCommand:
                # Browser TTS does not support IPA phoneme input.
                # Fall back to the alternate text if available.
                if item.text:
                    if textParts:
                        sawIndexSinceLastText = True
                    append_normalized_text(_normalize_mathematical_alphanumeric(item.text))
                    if textParts:
                        sawIndexSinceLastText = True
            elif itemType is RateCommand:
                yield from flush_text(isUtteranceEnd=is_trailing_command_at_utterance_end(seqIdx))
                if cancelEvent.is_set():
                    return
                activeRateCommand = None if self._is_prosody_reset_command(item) else item
            elif itemType is PitchCommand:
                yield from flush_text(isUtteranceEnd=is_trailing_command_at_utterance_end(seqIdx))
                if cancelEvent.is_set():
                    return
                activePitchCommand = None if self._is_prosody_reset_command(item) else item
            elif itemType is VolumeCommand:
                yield from flush_text(isUtteranceEnd=is_trailing_command_at_utterance_end(seqIdx))
                if cancelEvent.is_set():
                    return
                activeVolumeCommand = None if self._is_prosody_reset_command(item) else item
        yield from flush_text(isUtteranceEnd=True)

    def _apply_prosody_command(self, baseValue: Any, command: Any | None) -> int:
        value = audio_math.clamp_int(baseValue, 50, 0, 100)
        if command is not None:
            try:
                rawOffset = getattr(command, "_offset", None)
                if rawOffset is None:
                    rawOffset = getattr(command, "offset", 0)
                rawMultiplier = getattr(command, "_multiplier", None)
                if rawMultiplier is None:
                    rawMultiplier = getattr(command, "multiplier", 1)
                offset = audio_math.clamp_int(rawOffset, 0, -100, 100)
                multiplier = float(rawMultiplier)
                if not math.isfinite(multiplier):
                    multiplier = 1.0
                if offset:
                    value += offset
                elif multiplier != 1:
                    value = audio_math.clamp_int(value * multiplier, value, 0, 100)
            except (TypeError, ValueError, OverflowError):
                log.debug("Could not apply Google TTS prosody command.", exc_info=True)
        return max(0, min(100, value))

    def _is_prosody_reset_command(self, command: Any) -> bool:
        try:
            rawOffset = getattr(command, "_offset", None)
            rawMultiplier = getattr(command, "_multiplier", None)
            if rawOffset is not None or rawMultiplier is not None:
                return int(0 if rawOffset is None else rawOffset) == 0 and (
                    float(1 if rawMultiplier is None else rawMultiplier) == 1
                )
            isDefault = getattr(command, "isDefault", None)
            if isinstance(isDefault, bool):
                return isDefault
            return int(getattr(command, "offset", 0)) == 0 and float(getattr(command, "multiplier", 1)) == 1
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

    def _ends_with_pause_punctuation(self, segment: str) -> bool:
        return _TEXT_SEGMENTER.ends_with_pause_punctuation(segment)

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
            except Exception:
                log.exception("Unexpected exception in speech loop worker.")
                if not self._shutdownEvent.is_set() and not self._fallbackTriggered:
                    fallbackMsg = _("Google TTS For NVDA could not start speech in the Chromium browser runtime.")
                    self._trigger_fatal_fallback_async(fallbackMsg)
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
            if not cancelEvent.is_set():
                synthDoneSpeaking.notify(synth=self)
                self._consecutiveRuntimeErrors = 0
        except CdpCancelled:
            log.debug("Google TTS speech cancelled.")
        except Exception as exc:
            technicalDetail = str(getattr(exc, "technicalDetail", "") or "")
            if technicalDetail:
                log.exception(f"Google TTS speech failed: {technicalDetail}")
            else:
                log.exception("Google TTS speech failed.")
            if not cancelEvent.is_set():
                synthDoneSpeaking.notify(synth=self)
            if not cancelEvent.is_set() and not self._shutdownEvent.is_set() and not self._fallbackTriggered:
                self._consecutiveRuntimeErrors += 1
                friendlyMessage = str(exc).strip() or _(
                    "Google TTS For NVDA could not start speech in the Chromium browser runtime."
                )
                try:
                    runtimeAvailable = ChromeTtsBridge.browser_runtime_available()
                except Exception:
                    runtimeAvailable = False
                unrecoverable = not runtimeAvailable or self._consecutiveRuntimeErrors >= 2
                if unrecoverable:
                    self._trigger_fatal_fallback_async(friendlyMessage)
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
        text = _normalize_mathematical_alphanumeric(text)
        originalText = text
        originalHiddenSegments = (
            [_normalize_mathematical_alphanumeric(seg) for seg in hiddenSegments] if hiddenSegments else []
        )
        indexes = indexes or []
        leadingIndexes = [index for index, charOffset in indexes if charOffset <= 0]
        remainingIndexes = [(index, charOffset) for index, charOffset in indexes if charOffset > 0]
        for index in leadingIndexes:
            if cancelEvent.is_set():
                return
            if self._isPaused:
                self._sync_player()
            if not cancelEvent.is_set():
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
                            if self._isPaused:
                                self._sync_player()
                            if not cancelEvent.is_set():
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
                    if self._isPaused:
                        self._sync_player()
                    if not cancelEvent.is_set():
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
            for index, _charOffset in remainingIndexes:
                if cancelEvent.is_set():
                    return
                if self._isPaused:
                    self._sync_player()
                if not cancelEvent.is_set():
                    synthIndexReached.notify(synth=self, index=index)
            return

        audioParts: list[bytes] = [cachedPrefixAudio] if cacheKey is not None and cachedPrefixAudio else []
        segmentAudioParts: list[bytes] = []
        completedSegmentAudio: list[bytes] = []
        collectSegmentAudio = any(key is not None for key in segmentCacheKeys)
        chromeRate = effective_chrome_rate(options)
        hasUnpunctuatedHiddenBoundary = len(synthesisSegments) > 1 and any(
            not self._ends_with_pause_punctuation(seg) for seg in synthesisSegments[:-1]
        )
        effectiveShortenerMode = (
            _PAUSE_MODE_SHORTEN_END_ONLY
            if pauseShorteningMode == _PAUSE_MODE_DO_NOT_SHORTEN and hasUnpunctuatedHiddenBoundary
            else pauseShorteningMode
        )
        keepSilenceMs = keep_silence_ms_for_rate(effectiveShortenerMode, chromeRate)
        silenceShortener = create_pcm_silence_shortener(effectiveShortenerMode, SAMPLE_RATE, keepSilenceMs)
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
        deferredMarkOffset = -1
        boundarySegmentIndex = 0
        eventQueue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        consumerErrors: list[BaseException] = []

        def notify_indexes_through(charOffset: int, *, sync: bool = False) -> None:
            nonlocal pendingIndexes
            while pendingIndexes and pendingIndexes[0][1] <= charOffset:
                index, _indexOffset = pendingIndexes.pop(0)
                if cancelEvent.is_set():
                    return
                if sync or self._isPaused:
                    self._sync_player()
                if not cancelEvent.is_set():
                    synthIndexReached.notify(synth=self, index=index)

        def on_mark_impl(charOffset: int) -> None:
            nonlocal deferredMarkOffset
            if cancelEvent.is_set():
                return
            clampedOffset = max(0, min(len(text), charOffset))
            if self._isPaused:
                deferredMarkOffset = max(deferredMarkOffset, clampedOffset)
                return
            notify_indexes_through(clampedOffset)

        def feed_processed_audio(pcm: bytes) -> None:
            nonlocal deferredMarkOffset
            if not pcm:
                return
            if cacheKey is not None:
                audioParts.append(pcm)
            if collectSegmentAudio:
                segmentAudioParts.append(pcm)
            if not cancelEvent.is_set():
                livePcm = leadBuffer.feed(pcm) if leadBuffer is not None else pcm
                self._feed_audio(livePcm)
                if deferredMarkOffset >= 0 and not self._isPaused:
                    flushedOffset = deferredMarkOffset
                    deferredMarkOffset = -1
                    notify_indexes_through(flushedOffset)

        def on_segment_end_impl() -> None:
            nonlocal boundarySegmentIndex
            boundarySegment = (
                synthesisSegments[boundarySegmentIndex] if boundarySegmentIndex < len(synthesisSegments) else ""
            )
            boundarySegmentIndex += 1
            if silenceShortener is not None and not cancelEvent.is_set():
                shortenBoundary = (
                    pauseShorteningMode == _PAUSE_MODE_SHORTEN_ALL
                    or not self._ends_with_pause_punctuation(boundarySegment)
                )
                feed_processed_audio(
                    silenceShortener.flush_boundary(
                        shortenPause=shortenBoundary,
                    )
                )
            if collectSegmentAudio:
                completedSegmentAudio.append(b"".join(segmentAudioParts))
                segmentAudioParts.clear()

        def consume_events() -> None:
            try:
                while True:
                    kind, payload = eventQueue.get()
                    if kind == "audio":
                        if cancelEvent.is_set() or self._is_shutting_down():
                            continue
                        if silenceShortener is not None:
                            feed_processed_audio(silenceShortener.feed(payload))
                        else:
                            feed_processed_audio(payload)
                    elif kind == "mark":
                        on_mark_impl(payload)
                    elif kind == "segmentEnd":
                        try:
                            on_segment_end_impl()
                        finally:
                            payload.set()
                    elif kind == "flush":
                        if silenceShortener is not None and not cancelEvent.is_set():
                            if pauseShorteningMode == _PAUSE_MODE_DO_NOT_SHORTEN:
                                feed_processed_audio(silenceShortener.flush_boundary(shortenPause=False))
                            else:
                                feed_processed_audio(silenceShortener.finish())
                        if collectSegmentAudio:
                            completedSegmentAudio.append(b"".join(segmentAudioParts))
                            segmentAudioParts.clear()
                        if leadBuffer is not None and not cancelEvent.is_set():
                            self._feed_audio(leadBuffer.finish())
                    elif kind == "done":
                        break
            except BaseException as exc:
                if not cancelEvent.is_set():
                    consumerErrors.append(exc)

        def on_audio(pcm: bytes) -> None:
            if not cancelEvent.is_set() and not self._is_shutting_down():
                eventQueue.put(("audio", pcm))

        def on_mark(charOffset: int) -> None:
            if not cancelEvent.is_set() and not self._is_shutting_down():
                eventQueue.put(("mark", charOffset))

        def on_segment_end() -> None:
            ackEvent = threading.Event()
            eventQueue.put(("segmentEnd", ackEvent))
            while not ackEvent.wait(timeout=0.05):
                if cancelEvent.is_set() or self._is_shutting_down() or not consumerThread.is_alive():
                    break

        consumerThread = threading.Thread(
            name="googleTtsForNvda.audioConsumer",
            target=consume_events,
            daemon=True,
        )
        consumerThread.start()
        try:
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
            if not cancelEvent.is_set():
                eventQueue.put(("flush", None))
        finally:
            eventQueue.put(("done", None))
            consumerThread.join(timeout=2.0)
        if consumerErrors and not cancelEvent.is_set():
            raise consumerErrors[0]

        audio = b"".join(audioParts) if audioParts else b""
        if deferredMarkOffset >= 0 and not cancelEvent.is_set():
            notify_indexes_through(deferredMarkOffset, sync=self._isPaused)
            deferredMarkOffset = -1
        if pendingIndexes and not cancelEvent.is_set():
            for index, _charOffset in pendingIndexes:
                if cancelEvent.is_set():
                    return
                if self._isPaused:
                    self._sync_player()
                if not cancelEvent.is_set():
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

    def _active_cancel_set(self) -> bool:
        cancelEvent = getattr(self, "_activeCancelEvent", None)
        return cancelEvent is not None and cancelEvent.is_set()

    def _write_pcm_to_player(
        self,
        pcm: bytes,
        *,
        preparePlayer: Callable[[Any], None] | None = None,
        isSilence: bool = False,
    ) -> None:
        if not pcm or self._is_shutting_down() or self._active_cancel_set():
            return
        self._audioChunksSinceDeviceCheck += 1
        if self._audioChunksSinceDeviceCheck >= 50:
            self._ensure_current_output_device()
            self._audioChunksSinceDeviceCheck = 0
        elif self._audio_device_error():
            self._recreate_wave_player()
            self._audioChunksSinceDeviceCheck = 0
        player = self._current_player()
        if player is None or self._is_shutting_down() or self._active_cancel_set():
            return
        if preparePlayer is not None:
            preparePlayer(player)
        try:
            player.feed(pcm)
        except Exception:
            if self._is_shutting_down() or self._active_cancel_set():
                return
            label = (
                "WavePlayer.feed failed during silence; recreating WavePlayer and retrying."
                if isSilence
                else "WavePlayer.feed failed; recreating WavePlayer and retrying."
            )
            log.debug(label, exc_info=True)
            self._recreate_wave_player()
            if self._is_shutting_down() or self._active_cancel_set():
                return
            retryPlayer = self._current_player()
            if retryPlayer is not None:
                if preparePlayer is not None:
                    preparePlayer(retryPlayer)
                try:
                    retryPlayer.feed(pcm)
                except Exception:
                    log.debug(
                        "WavePlayer.feed retry failed during silence."
                        if isSilence
                        else "WavePlayer.feed retry failed.",
                        exc_info=True,
                    )

    def _feed_audio(self, pcm: bytes) -> None:
        self._write_pcm_to_player(pcm, isSilence=False)

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
                if self._isPaused:
                    self._sync_player()
                if not cancelEvent.is_set():
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
            if self._isPaused:
                self._sync_player()
            if not cancelEvent.is_set():
                synthIndexReached.notify(synth=self, index=index)
        if not cancelEvent.is_set() and position < totalBytes:
            self._feed_audio(pcm[position:])

    def _sync_player(self) -> None:
        if self._is_shutting_down() or self._active_cancel_set():
            return
        player = self._current_player()
        if player is None:
            return
        try:
            sync = getattr(player, "sync", None)
            if sync is not None:
                sync()
                return
            player.idle()
        except Exception:
            if self._is_shutting_down() or self._active_cancel_set():
                return
            log.debug("WavePlayer sync/idle failed; recreating WavePlayer.", exc_info=True)
            self._recreate_wave_player()

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
        if self._is_shutting_down() or self._active_cancel_set():
            return
        if self._has_queued_speech():
            self._sync_player()
            return
        player = self._current_player()
        if player is None:
            return
        try:
            player.idle()
        except Exception:
            if self._is_shutting_down() or self._active_cancel_set():
                return
            log.debug("WavePlayer.idle failed at end of request; recreating WavePlayer.", exc_info=True)
            self._recreate_wave_player()

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
        if milliseconds <= 0 or self._is_shutting_down() or self._active_cancel_set():
            return
        frameCount = int(SAMPLE_RATE * milliseconds / 1000)
        if frameCount <= 0:
            return

        def _disable_leading_silence_trimming(player: Any) -> None:
            # Keep WASAPI from trimming injected pause frames.
            startTrimming = getattr(player, "startTrimmingLeadingSilence", None)
            if callable(startTrimming):
                with suppress(Exception):
                    startTrimming(False)

        self._write_pcm_to_player(
            b"\x00\x00" * frameCount,
            preparePlayer=_disable_leading_silence_trimming,
            isSilence=True,
        )

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
        text = _normalize_mathematical_alphanumeric(text)
        # Unmarked commands can be NVDA's normalized copies of Google profile commands.
        # External NVDA/app language changes are ignored by _speech_chunks when disabled.
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
            with self._catalog_lock():
                try:
                    voiceLanguage = self.catalog.language_for_voice(activeVoice)
                except KeyError:
                    voiceLanguage = ""
            profileLanguage = (
                self._auto_language_candidate_for_language(voiceLanguage, candidateLanguages) if voiceLanguage else ""
            )
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
        preferredLanguage = self._auto_language_preferred(candidateLanguages, activeVoice)
        detectedLanguage = self._detect_auto_language(text, candidateLanguages, preferredLanguage)
        if detectedLanguage is None:
            detectedLanguage = preferredLanguage
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
            "rate": audio_math.clamp_int(rate, 50, 0, 100),
            "rateBoost": audio_math.coerce_bool(rateBoost, False),
            "pitch": audio_math.clamp_int(pitch, 50, 0, 100),
            "volume": audio_math.clamp_int(volume, 100, 0, 100),
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
        return standby._voice_matches_language(self.catalog, voice, language)

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
        return standby._auto_language_detection_enabled()

    def _auto_language_profiles(self) -> dict[str, dict[str, Any]]:
        return standby._auto_language_profiles()

    def _auto_language_profile_for_language(self, language: str | None) -> dict[str, Any]:
        return standby._auto_language_profile_for_language(language)

    def _profile_int(self, value: Any, default: int, minimum: int = 0, maximum: int = 100) -> int:
        return audio_math.clamp_int(value, default, minimum, maximum)

    def _profile_bool(self, value: Any, default: bool = False) -> bool:
        return audio_math.coerce_bool(value, default)

    def _auto_language_candidates(self) -> list[str]:
        return standby._select_auto_language_candidates(self.availableLanguages)

    def _auto_language_preferred(self, candidateLanguages: list[str], fallbackVoice: str) -> str:
        with self._catalog_lock():
            return standby._select_auto_language_preferred(candidateLanguages, fallbackVoice, self.catalog)

    def _auto_language_candidate_for_language(self, language: str | None, candidateLanguages: list[str]) -> str:
        normalizedLanguage = self._normalize_language(language)
        if normalizedLanguage:
            for candidate in candidateLanguages:
                if self._normalize_language(candidate) == normalizedLanguage:
                    return candidate
        languageKeys = self._language_match_keys(language)
        for candidate in candidateLanguages:
            if self._language_match_keys(candidate).intersection(languageKeys):
                return candidate
        languageRoot = self._language_root(language)
        for candidate in candidateLanguages:
            if self._language_root(candidate) == languageRoot:
                return candidate
        return ""

    def _detect_auto_language(
        self,
        text: str,
        candidateLanguages: list[str],
        preferredLanguage: str | None = None,
    ) -> str | None:
        cldLanguage = language_detector.detect_language(
            text,
            candidateLanguages,
            preferredLanguage=preferredLanguage,
        )
        if cldLanguage is not None:
            return cldLanguage
        if not _has_language_words(text):
            return None
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

    def _language_token_signal(self, token: str, candidateRoots: set[str]) -> tuple[str | None, int]:
        return _language_token_signal(token, candidateRoots, is_url_token=self._looks_like_url_token)

    def _language_root(self, language: str | None) -> str:
        return language_utils.language_root(language)

    def _speech_options(
        self,
        rate: int,
        pitch: int,
        volume: int,
        voice: str | None = None,
        rateBoost: bool | None = None,
    ) -> dict[str, Any]:
        with self._catalog_lock():
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
        with self._catalog_lock():
            if not lang:
                return self._speaker_for_voice_or_language(fallbackVoice)
            normalizedLang = self._normalize_language(lang)
            if not normalizedLang:
                return self._speaker_for_voice_or_language(fallbackVoice)
            fallbackVoice = self._speaker_for_voice_or_language(fallbackVoice)
            if not fallbackVoice:
                for speaker in self._speakers_for_language(normalizedLang):
                    return speaker.id
                return ""
            try:
                fallbackSpeaker = self.catalog.speaker_for_voice(fallbackVoice)
            except Exception:
                fallbackSpeaker = None
            if fallbackSpeaker is not None and self._language_matches(fallbackSpeaker.language, normalizedLang):
                return fallbackVoice
            for speaker in self._speakers_for_language(normalizedLang):
                return speaker.id
            # Language redirect: map unsupported locale to best available alternative.
            redirected = language_detector.redirect_language(normalizedLang, self.availableLanguages)
            if redirected:
                for speaker in self._speakers_for_language(redirected):
                    return speaker.id
            rootLang = normalizedLang.split("-", 1)[0]
            if (
                fallbackSpeaker is not None
                and self._normalize_language(fallbackSpeaker.language).split("-", 1)[0] == rootLang
            ):
                return fallbackVoice
            for language, speakers in self._speakersByLanguage.items():
                if self._normalize_language(language).split("-", 1)[0] == rootLang:
                    return speakers[0].id
            return fallbackVoice

    def _speaker_for_voice_or_language(self, value: str | None) -> str:
        with self._catalog_lock():
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
        with self._catalog_lock():
            if value not in self.availableVoices:
                value = next(iter(self.availableVoices), "")
            self.__voice = value
            self._availableVariants = self._build_available_variants(value)
            if getattr(self, "_SynthDriver__variant", "") not in self._availableVariants:
                self.__variant = next(iter(self._availableVariants), "")
        self._clear_short_audio_cache()
        self._reload_voice_dict()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

    def _get_variant(self) -> str:
        return self._current_speaker_id()

    def _set_variant(self, value: str) -> None:
        with self._catalog_lock():
            variants = self._getAvailableVariants()
            if value in variants:
                self.__variant = value
            else:
                self.__variant = next(iter(variants), "")
        self._clear_short_audio_cache()
        self._reload_voice_dict()
        self._warm_current_voice_async(delay=_PRELOAD_RESUME_DELAY_SECONDS)

    def _reload_voice_dict(self) -> None:
        if speechDictHandler is None:
            return
        definitions = getattr(speechDictHandler, "definitions", None)
        loadVoiceDict = (getattr(definitions, "loadVoiceDict", None) if definitions is not None else None) or getattr(
            speechDictHandler, "loadVoiceDict", None
        )
        if callable(loadVoiceDict):
            with suppress(Exception):
                _call_compat(loadVoiceDict, self)

    def _getAvailableVariants(self, *args: Any, **kwargs: Any) -> OrderedDict[str, VoiceInfo]:
        return self._build_available_variants(self.__voice)

    def _current_speaker_id(self) -> str:
        with self._catalog_lock():
            variants = self._build_available_variants(getattr(self, "_SynthDriver__voice", ""))
            if getattr(self, "_SynthDriver__variant", "") in variants:
                return self.__variant
            self._availableVariants = variants
            self.__variant = next(iter(variants), "")
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
                    with self._catalog_lock():
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
        return standby._order_warmup_candidates(
            candidateLanguages,
            self._auto_language_preferred(candidateLanguages, currentVoice),
        )

    def _warmup_voice_ids_for_voice(self, voiceId: str, seenPackages: set[str] | None = None) -> list[str]:
        if seenPackages is None:
            seenPackages = set()
        try:
            with self._catalog_lock():
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
        with self._catalog_lock():
            return standby._voice_id_for_package(self.catalog, self._speakersByPackage, packageId, preferredSpeaker)

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
        with self._warmup_lock():
            if self._shutdownEvent.is_set():
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
                try:
                    if delay > 0 and cancelEvent.wait(delay):
                        return
                    try:
                        self._bridge.ensure_connection(cancelEvent=cancelEvent)
                    except CdpCancelled:
                        log.debug("Google TTS bridge eager connection cancelled.")
                        return
                    except Exception as exc:
                        log.debug("Google TTS bridge eager connection failed.", exc_info=True)
                        if (
                            not cancelEvent.is_set()
                            and not self._shutdownEvent.is_set()
                            and not self._fallbackTriggered
                        ):
                            try:
                                runtimeAvailable = ChromeTtsBridge.browser_runtime_available()
                            except Exception:
                                runtimeAvailable = False
                            if not runtimeAvailable:
                                friendlyMessage = str(exc).strip() or _(
                                    "Google TTS For NVDA could not start speech in the Chromium browser runtime."
                                )
                                self._trigger_fatal_fallback_async(friendlyMessage)
                        return
                    if cancelEvent.is_set() or self._shutdownEvent.is_set():
                        return
                    preload_options(priorityOptionsList)
                finally:
                    with self._warmup_lock():
                        if getattr(self, "_warmupThread", None) is threading.current_thread():
                            self._warmupThread = None

            thread = threading.Thread(name="googleTtsForNvda.preload", target=warm, daemon=True)
            self._warmupThread = thread
            thread.start()

    def _get_language(self) -> str:
        with self._catalog_lock():
            currentVoice = getattr(self, "_SynthDriver__voice", "")
        if self._auto_language_detection_enabled():
            candidateLanguages = self._auto_language_candidates()
            if len(candidateLanguages) == 1:
                return language_utils.resolve_nvda_locale(candidateLanguages[0])
            if candidateLanguages:
                preferredLanguage = self._auto_language_preferred(candidateLanguages, currentVoice)
                if preferredLanguage:
                    return language_utils.resolve_nvda_locale(preferredLanguage)
        return language_utils.resolve_nvda_locale(currentVoice)

    def _nvda_locale_exists(self, locale: str) -> bool:
        return language_utils.nvda_locale_exists(locale)

    def _get_rate(self) -> int:
        return self._rate

    def _set_rate(self, value: int) -> None:
        self._rate = audio_math.clamp_int(value, self._rate, 0, 100)

    def _get_rateBoost(self) -> bool:
        return self._rateBoost

    def _set_rateBoost(self, value: bool) -> None:
        self._rateBoost = audio_math.coerce_bool(value, False)

    def _get_pitch(self) -> int:
        return self._pitch

    def _set_pitch(self, value: int) -> None:
        self._pitch = audio_math.clamp_int(value, self._pitch, 0, 100)

    def _get_volume(self) -> int:
        return self._volume

    def _set_volume(self, value: int) -> None:
        self._volume = audio_math.clamp_int(value, self._volume, 0, 100)
        player = self._current_player()
        if player is not None:
            with suppress(Exception):
                player.setVolume(all=1.0)

    def _get_availablePausemodes(self, *args: Any, **kwargs: Any) -> OrderedDict[str, StringParameterInfo]:
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

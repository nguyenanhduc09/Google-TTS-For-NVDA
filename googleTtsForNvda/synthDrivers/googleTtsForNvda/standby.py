from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import config
import globalVars
from logHandler import log

from . import language_detector, voice_store
from .audio_math import (
    build_speech_options,
)
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
    DEFAULT_KEEP_BROWSER_RUNTIME_READY,
    SYNTH_NAME,
    CdpCancelled,
    ChromeTtsBridge,
    configured_browser_runtime,
    configured_keep_browser_runtime_ready,
)
from .catalog import CATALOG_PATH, ENGINE_DIR, ENGINE_VERSION, VoiceCatalog
from .watcher import DirectoryChangeWatcher

ADDON_DIR = Path(__file__).resolve().parents[2]
_VOICE_WARMUP_TEXT = " "


def _config_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def keep_browser_runtime_ready_enabled() -> bool:
    try:
        if globalVars.appArgs.secure:
            return False
    except Exception:
        log.debug("Could not read NVDA secure-mode state for Google TTS standby.", exc_info=True)
        return False
    try:
        return configured_keep_browser_runtime_ready()
    except Exception:
        log.debug("Could not read Google TTS standby browser runtime setting.", exc_info=True)
        return DEFAULT_KEEP_BROWSER_RUNTIME_READY


def _profile_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return max(0, min(100, int(default)))
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return max(0, min(100, int(default)))


def _profile_bool(value: Any, default: bool = False) -> bool:
    return _config_bool(value, default)


def _installed_catalog() -> VoiceCatalog | None:
    fullCatalog = VoiceCatalog.load()
    installedPackages = voice_store.installed_packages(fullCatalog)
    catalog = VoiceCatalog(installedPackages)
    if not catalog.speakers:
        return None
    return catalog


def _wasm_engine_signature() -> tuple[Any, ...]:
    """Return a fingerprint of the WASM TTS engine directory.

    Includes the pinned engine version, the engine directory modification time,
    and the catalog file modification time so that replacing the engine files
    or editing the voice catalog forces standby to discard the stale bridge
    and warm a fresh one.
    """
    try:
        engine_mtime = ENGINE_DIR.stat().st_mtime_ns
    except OSError:
        engine_mtime = 0
    try:
        catalog_mtime = CATALOG_PATH.stat().st_mtime_ns
    except OSError:
        catalog_mtime = 0
    return (ENGINE_VERSION, engine_mtime, catalog_mtime)


def _catalog_signature(catalog: VoiceCatalog) -> tuple[Any, ...]:
    packageSignature = tuple(
        (package.id, package.compressedSize, package.sha256Checksum, package.dependentVoiceId)
        for package in catalog.packages
    )
    try:
        warmupSignature = tuple(_warmup_voice_ids(catalog, _current_speech_state(catalog)))
    except Exception:
        log.debug("Could not build Google TTS standby warmup signature.", exc_info=True)
        warmupSignature = ()
    return (configured_browser_runtime(), _wasm_engine_signature(), packageSignature, warmupSignature)


def _speakers_by_package(catalog: VoiceCatalog) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for speaker in catalog.speakers:
        grouped.setdefault(speaker.packageId, []).append(speaker)
    return grouped


def _speakers_for_language(catalog: VoiceCatalog, language: str | None) -> list[Any]:
    if not language:
        return []
    speakersByLanguage = catalog.voices_by_language()
    speakers = speakersByLanguage.get(language)
    if speakers is not None:
        return list(speakers)
    matches: list[Any] = []
    for speakerLanguage, languageSpeakers in speakersByLanguage.items():
        if language_detector.language_matches(speakerLanguage, language):
            matches.extend(languageSpeakers)
    return matches


def _available_languages(catalog: VoiceCatalog) -> list[str]:
    return list(catalog.voices_by_language())


def _configured_synth_section() -> Any:
    try:
        return config.conf["speech"][SYNTH_NAME]
    except Exception:
        return {}


def _initial_voice(catalog: VoiceCatalog) -> str:
    available = _available_languages(catalog)
    try:
        configured = str(_configured_synth_section().get("voice") or "")
        if configured in available:
            return configured
    except Exception:
        pass
    if "en-US" in available:
        return "en-US"
    return next(iter(available))


def _variant_for_language(catalog: VoiceCatalog, language: str) -> str:
    variantIds = [speaker.id for speaker in _speakers_for_language(catalog, language)]
    if not variantIds and catalog.speakers:
        variantIds = [catalog.speakers[0].id]
    try:
        configured = str(_configured_synth_section().get("variant") or "")
        if configured in variantIds:
            return configured
    except Exception:
        pass
    return variantIds[0]


def _current_speech_state(catalog: VoiceCatalog) -> dict[str, Any]:
    section = _configured_synth_section()
    language = _initial_voice(catalog)
    voice = _variant_for_language(catalog, language)
    return {
        "voice": voice,
        "rate": _profile_int(section.get("rate"), 50),
        "rateBoost": _profile_bool(section.get("rateBoost"), False),
        "pitch": _profile_int(section.get("pitch"), 50),
        "volume": _profile_int(section.get("volume"), 100),
    }


def _auto_language_detection_enabled() -> bool:
    try:
        value = config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_DETECTION]
    except Exception:
        return DEFAULT_AUTO_LANGUAGE_DETECTION
    return _config_bool(value, DEFAULT_AUTO_LANGUAGE_DETECTION)


def _auto_language_profiles() -> dict[str, dict[str, Any]]:
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
        languageKey = language_detector._normalize_language(str(rawLanguage))
        if languageKey and isinstance(rawProfile, dict):
            profiles[languageKey] = dict(rawProfile)
    return profiles


def _auto_language_profile_for_language(language: str | None) -> dict[str, Any]:
    languageKey = language_detector._normalize_language(language)
    if not languageKey:
        return {}
    profiles = _auto_language_profiles()
    profile = profiles.get(languageKey)
    if profile is not None:
        return profile
    languageKeys = language_detector.language_match_keys(languageKey)
    for profileLanguage, profile in profiles.items():
        if language_detector.language_match_keys(profileLanguage).intersection(languageKeys):
            return profile
    return {}


def _auto_language_candidates(catalog: VoiceCatalog) -> list[str]:
    profiles = _auto_language_profiles()
    try:
        rawValue = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_CANDIDATES])
    except Exception:
        rawValue = DEFAULT_AUTO_LANGUAGE_CANDIDATES
    availableByKey = {
        language_detector._normalize_language(language): language for language in _available_languages(catalog)
    }
    if profiles:
        return [
            availableByKey[languageKey]
            for languageKey, profile in profiles.items()
            if languageKey in availableByKey and _profile_bool(profile.get("enabled"), False)
        ]
    candidates: list[str] = []
    seen: set[str] = set()
    for rawLanguage in rawValue.split(","):
        key = language_detector._normalize_language(rawLanguage)
        if not key or key in seen or key not in availableByKey:
            continue
        candidates.append(availableByKey[key])
        seen.add(key)
    return candidates


def _auto_language_preferred(catalog: VoiceCatalog, candidateLanguages: list[str], fallbackVoice: str) -> str:
    try:
        configured = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PREFERRED])
    except Exception:
        configured = DEFAULT_AUTO_LANGUAGE_PREFERRED
    configuredKey = language_detector._normalize_language(configured)
    for language in candidateLanguages:
        if language_detector._normalize_language(language) == configuredKey:
            return language
    try:
        fallbackLanguage = catalog.language_for_voice(fallbackVoice)
    except Exception:
        fallbackLanguage = fallbackVoice
    fallbackRoot = language_detector._language_root(fallbackLanguage)
    for language in candidateLanguages:
        if language_detector._language_root(language) == fallbackRoot:
            return language
    return candidateLanguages[0] if candidateLanguages else fallbackLanguage


def _auto_language_candidates_in_warmup_order(catalog: VoiceCatalog, currentVoice: str) -> list[str]:
    candidateLanguages = _auto_language_candidates(catalog)
    if len(candidateLanguages) <= 1:
        return candidateLanguages
    orderedLanguages = list(candidateLanguages)
    preferredLanguage = _auto_language_preferred(catalog, orderedLanguages, currentVoice)
    if preferredLanguage in orderedLanguages:
        orderedLanguages.remove(preferredLanguage)
        orderedLanguages.insert(0, preferredLanguage)
    return orderedLanguages


def _voice_matches_language(catalog: VoiceCatalog, voice: str, language: str | None) -> bool:
    if not language:
        return True
    try:
        voiceLanguage = catalog.language_for_voice(voice)
    except Exception:
        return False
    return language_detector.language_matches(voiceLanguage, language)


def _voice_for_language(catalog: VoiceCatalog, language: str | None, fallbackVoice: str) -> str:
    if not language:
        return _speaker_for_voice_or_language(catalog, fallbackVoice)
    normalizedLanguage = language_detector._normalize_language(language)
    if not normalizedLanguage:
        return _speaker_for_voice_or_language(catalog, fallbackVoice)
    fallbackVoice = _speaker_for_voice_or_language(catalog, fallbackVoice)
    fallbackSpeaker = catalog.speaker_for_voice(fallbackVoice)
    if language_detector.language_matches(fallbackSpeaker.language, normalizedLanguage):
        return fallbackVoice
    for speaker in _speakers_for_language(catalog, normalizedLanguage):
        return speaker.id
    # Language redirect: map unsupported locale to best available alternative.
    availableLanguages = set(catalog.voices_by_language())
    redirected = language_detector.redirect_language(normalizedLanguage, availableLanguages)
    if redirected:
        for speaker in _speakers_for_language(catalog, redirected):
            return speaker.id
    rootLanguage = normalizedLanguage.split("-", 1)[0]
    if language_detector._normalize_language(fallbackSpeaker.language).split("-", 1)[0] == rootLanguage:
        return fallbackVoice
    for languageKey, speakers in catalog.voices_by_language().items():
        if language_detector._normalize_language(languageKey).split("-", 1)[0] == rootLanguage:
            return speakers[0].id
    return fallbackVoice


def _speaker_for_voice_or_language(catalog: VoiceCatalog, value: str | None) -> str:
    if value:
        try:
            return catalog.speaker_for_voice(value).id
        except Exception:
            pass
        for speaker in _speakers_for_language(catalog, value):
            return speaker.id
    return catalog.speakers[0].id


def _auto_language_profile(
    catalog: VoiceCatalog,
    language: str | None,
    fallbackVoice: str,
    fallbackRate: int,
    fallbackRateBoost: bool,
    fallbackPitch: int,
    fallbackVolume: int,
) -> dict[str, Any]:
    profile = _auto_language_profile_for_language(language)
    voice = str(profile.get("voice") or "")
    if not _voice_matches_language(catalog, voice, language):
        voice = _voice_for_language(catalog, language, fallbackVoice)
    return {
        "voice": voice,
        "rate": _profile_int(profile.get("rate"), fallbackRate),
        "rateBoost": _profile_bool(profile.get("rateBoost"), fallbackRateBoost),
        "pitch": _profile_int(profile.get("pitch"), fallbackPitch),
        "volume": _profile_int(profile.get("volume"), fallbackVolume),
    }


def _voice_id_for_package(
    catalog: VoiceCatalog,
    speakersByPackage: dict[str, list[Any]],
    packageId: str,
    preferredSpeaker: str | None = None,
) -> str:
    speakers = speakersByPackage.get(packageId, [])
    fallbackVoiceId = speakers[0].id if speakers else ""
    for speaker in speakers:
        if preferredSpeaker and speaker.speaker == preferredSpeaker:
            return speaker.id
    return fallbackVoiceId


def _warmup_voice_ids_for_voice(
    catalog: VoiceCatalog,
    voiceId: str,
    seenPackages: set[str] | None = None,
) -> list[str]:
    if seenPackages is None:
        seenPackages = set()
    try:
        speaker = catalog.speaker_for_voice(voiceId)
        package = catalog.package_for_voice(voiceId)
    except Exception:
        log.debug("Could not resolve Google TTS standby preload voice %s.", voiceId, exc_info=True)
        return []
    if package.id in seenPackages:
        return []
    seenPackages.add(package.id)
    voiceIds: list[str] = []
    if package.dependentVoiceId:
        dependencyVoiceId = _voice_id_for_package(
            catalog,
            _speakers_by_package(catalog),
            package.dependentVoiceId,
            speaker.speaker,
        )
        if dependencyVoiceId:
            voiceIds.extend(_warmup_voice_ids_for_voice(catalog, dependencyVoiceId, seenPackages))
    if voiceId not in voiceIds:
        voiceIds.append(voiceId)
    return voiceIds


def _warmup_voice_ids(catalog: VoiceCatalog, state: dict[str, Any]) -> list[str]:
    currentVoice = str(state["voice"])
    if not _auto_language_detection_enabled():
        return _warmup_voice_ids_for_voice(catalog, currentVoice)
    candidateLanguages = _auto_language_candidates_in_warmup_order(catalog, currentVoice)
    if not candidateLanguages:
        return _warmup_voice_ids_for_voice(catalog, currentVoice)

    voiceIds: list[str] = []
    seenPackages: set[str] = set()
    for language in candidateLanguages:
        profile = _auto_language_profile(
            catalog,
            language,
            currentVoice,
            int(state["rate"]),
            bool(state["rateBoost"]),
            int(state["pitch"]),
            int(state["volume"]),
        )
        voiceId = str(profile.get("voice") or "")
        if not voiceId:
            continue
        for warmupVoiceId in _warmup_voice_ids_for_voice(catalog, voiceId):
            try:
                packageId = catalog.package_for_voice(warmupVoiceId).id
            except Exception:
                log.debug("Could not resolve Google TTS standby preload package for %s.", warmupVoiceId, exc_info=True)
                continue
            if packageId in seenPackages:
                continue
            seenPackages.add(packageId)
            voiceIds.append(warmupVoiceId)
    return voiceIds or [currentVoice]


def _speech_options(
    catalog: VoiceCatalog,
    rate: int,
    pitch: int,
    volume: int,
    voice: str,
    rateBoost: bool,
) -> dict[str, Any]:
    speaker = catalog.speaker_for_voice(voice)
    package = catalog.package_for_voice(speaker.id)
    options = build_speech_options(
        speaker_id=speaker.id,
        speaker_name=speaker.name,
        lang=speaker.language,
        package_id=package.id,
        rate=rate,
        pitch=pitch,
        volume=volume,
        rateBoost=rateBoost,
    )
    return options


def _warmup_options(catalog: VoiceCatalog) -> list[dict[str, Any]]:
    state = _current_speech_state(catalog)
    optionsList: list[dict[str, Any]] = []
    for voiceId in _warmup_voice_ids(catalog, state):
        try:
            optionsList.append(
                _speech_options(
                    catalog,
                    int(state["rate"]),
                    int(state["pitch"]),
                    0,
                    voiceId,
                    bool(state["rateBoost"]),
                ),
            )
        except Exception:
            log.debug("Could not prepare Google TTS standby preload options for %s.", voiceId, exc_info=True)
    return optionsList


def _refresh_reason_requires_runtime_restart(reason: str) -> bool:
    return reason.startswith("watched directory changed")


class _StandbyRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bridge: ChromeTtsBridge | None = None
        self._signature: tuple[Any, ...] | None = None
        self._ready = False
        self._generation = 0
        self._cancelEvent: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._watcher: DirectoryChangeWatcher | None = None
        self._synthActive = False
        self._shutdown = True

    def initialize(self) -> None:
        with self._lock:
            self._shutdown = False

    def refresh_async(self, reason: str = "", *, force: bool = True) -> None:
        bridgeToTerminate: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        with self._lock:
            if self._shutdown:
                return
            if not keep_browser_runtime_ready_enabled() or self._synthActive:
                self._generation += 1
                bridgeToTerminate = self._clear_standby_locked(cancelWorker=True)
                watcherToStop = self._detach_watchers_locked()
                workerAlive = self._worker is not None and self._worker.is_alive()
                if workerAlive:
                    log.debug("Google TTS standby runtime refresh skipped while synth is active or disabled.")
                else:
                    self._worker = None
            elif not force and self._worker is not None and self._worker.is_alive():
                return
            else:
                self._generation += 1
                generation = self._generation
                cancelEvent = threading.Event()
                workerAlive = self._worker is not None and self._worker.is_alive()
                self._cancel_current_worker_locked()
                watcherToStop = self._detach_watchers_locked()
                if workerAlive:
                    # A cancelled worker may still be unwinding a CDP request; do not let
                    # the replacement worker reuse the same browser bridge concurrently.
                    bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
                self._cancelEvent = cancelEvent
                worker = threading.Thread(
                    name="googleTtsForNvda.standby",
                    target=self._run_refresh,
                    args=(generation, cancelEvent, reason),
                    daemon=True,
                )
                self._worker = worker
                worker.start()
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(bridgeToTerminate)

    def claim_bridge(self, catalog: VoiceCatalog) -> ChromeTtsBridge | None:
        bridgeToTerminate: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        claimedBridge: ChromeTtsBridge | None = None
        with self._lock:
            if self._shutdown:
                return None
            self._synthActive = True
            self._generation += 1
            self._cancel_current_worker_locked()
            watcherToStop = self._detach_watchers_locked()
            signature = _catalog_signature(catalog)
            if self._bridge is not None and self._ready and self._signature == signature:
                claimedBridge = self._bridge
                self._bridge = None
                self._signature = None
                self._ready = False
            else:
                bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
        self._stop_watcher(watcherToStop)
        if claimedBridge is not None:
            return claimedBridge
        self._terminate_bridge(bridgeToTerminate)
        return None

    def note_synth_active(self) -> None:
        bridgeToTerminate: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        with self._lock:
            if self._shutdown:
                return
            self._synthActive = True
            self._generation += 1
            self._cancel_current_worker_locked()
            watcherToStop = self._detach_watchers_locked()
            bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(bridgeToTerminate)

    def release_synth_bridge(self, bridge: ChromeTtsBridge, catalog: VoiceCatalog) -> bool:
        previousBridge: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        with self._lock:
            if self._shutdown or not keep_browser_runtime_ready_enabled():
                self._synthActive = False
                return False
            self._synthActive = False
            self._generation += 1
            self._cancel_current_worker_locked()
            watcherToStop = self._detach_watchers_locked()
            previousBridge = self._bridge if self._bridge is not bridge else None
            self._bridge = bridge
            self._signature = _catalog_signature(catalog)
            self._ready = True
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(previousBridge)
        self.refresh_async("Google TTS synth released its browser runtime")
        return True

    def release_synth_without_bridge(self, reason: str = "") -> None:
        watcherToStop: DirectoryChangeWatcher | None = None
        with self._lock:
            if self._shutdown:
                self._synthActive = False
                return
            self._synthActive = False
            self._generation += 1
            self._cancel_current_worker_locked()
            watcherToStop = self._detach_watchers_locked()
        self._stop_watcher(watcherToStop)
        self.refresh_async(reason or "Google TTS synth released without a reusable browser runtime")

    def terminate(self) -> None:
        bridgeToTerminate: ChromeTtsBridge | None
        watcherToStop: DirectoryChangeWatcher | None = None
        with self._lock:
            self._shutdown = True
            self._synthActive = False
            self._generation += 1
            bridgeToTerminate = self._clear_standby_locked(cancelWorker=True)
            watcherToStop = self._detach_watchers_locked()
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(bridgeToTerminate)

    def _run_refresh(self, generation: int, cancelEvent: threading.Event, reason: str) -> None:
        bridgeForWorker: ChromeTtsBridge | None = None
        bridgeToTerminate: ChromeTtsBridge | None = None
        catalog: VoiceCatalog | None = None
        try:
            # Early exit: if the bridge is already warm and the signature matches,
            # skip the full refresh to avoid unnecessary browser restarts.
            with self._lock:
                if self._bridge is not None and self._ready and not _refresh_reason_requires_runtime_restart(reason):
                    catalog = _installed_catalog()
                    if catalog is not None:
                        currentSignature = _catalog_signature(catalog)
                        if self._signature == currentSignature:
                            self._start_watchers_locked()
                            log.debug("Google TTS standby browser runtime already warm, skipping refresh.")
                            return
            if catalog is None:
                catalog = _installed_catalog()
            if cancelEvent.is_set():
                raise CdpCancelled()
            if catalog is None:
                with self._lock:
                    if generation == self._generation:
                        bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
                        self._start_watchers_locked()
                return
            signature = _catalog_signature(catalog)
            with self._lock:
                if generation != self._generation or self._shutdown or self._synthActive:
                    raise CdpCancelled()
                if (
                    self._bridge is not None
                    and self._signature == signature
                    and not _refresh_reason_requires_runtime_restart(reason)
                ):
                    bridgeForWorker = self._bridge
                else:
                    bridgeToTerminate = self._bridge
                    bridgeForWorker = ChromeTtsBridge(catalog)
                    self._bridge = bridgeForWorker
                    self._signature = signature
                    self._ready = False
            self._terminate_bridge(bridgeToTerminate)
            bridgeToTerminate = None
            bridgeForWorker.ensure_connection(cancelEvent=cancelEvent)
            for options in _warmup_options(catalog):
                if cancelEvent.is_set():
                    raise CdpCancelled()
                warmupOptions = dict(options)
                warmupOptions["warmupText"] = _VOICE_WARMUP_TEXT
                bridgeForWorker.preload_voice(warmupOptions, cancelEvent=cancelEvent)
            with self._lock:
                if generation != self._generation or self._bridge is not bridgeForWorker:
                    raise CdpCancelled()
                if self._shutdown or self._synthActive or not keep_browser_runtime_ready_enabled():
                    bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
                    raise CdpCancelled()
                self._ready = True
                self._start_watchers_locked()
            if reason:
                log.debug("Google TTS standby browser runtime is ready: %s.", reason)
            else:
                log.debug("Google TTS standby browser runtime is ready.")
        except CdpCancelled:
            log.debug("Google TTS standby browser runtime refresh cancelled.")
        except Exception:
            log.debug("Google TTS standby browser runtime refresh failed.", exc_info=True)
            with self._lock:
                if generation == self._generation and self._bridge is bridgeForWorker:
                    bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
                    self._start_watchers_locked()
        finally:
            with self._lock:
                if generation == self._generation and self._worker is threading.current_thread():
                    self._worker = None
            self._terminate_bridge(bridgeToTerminate)

    def _clear_standby_locked(self, *, cancelWorker: bool) -> ChromeTtsBridge | None:
        if cancelWorker:
            self._cancel_current_worker_locked()
        bridge = self._bridge
        self._bridge = None
        self._signature = None
        self._ready = False
        return bridge

    def _cancel_current_worker_locked(self) -> None:
        if self._cancelEvent is not None:
            self._cancelEvent.set()
        self._cancelEvent = None

    def _start_watchers_locked(self) -> None:
        oldWatcher = self._detach_watchers_locked()
        if oldWatcher is not None:
            oldWatcher.stop()
        if self._shutdown or self._synthActive or not keep_browser_runtime_ready_enabled():
            return
        generation = self._generation
        self._watcher = DirectoryChangeWatcher(
            self._watch_paths,
            lambda reason: self._refresh_from_watcher(reason, generation),
        )
        self._watcher.start()

    def _detach_watchers_locked(self) -> DirectoryChangeWatcher | None:
        if self._watcher is None:
            return None
        watcher = self._watcher
        self._watcher = None
        if hasattr(watcher, "signal_stop"):
            watcher.signal_stop()
        return watcher

    def _stop_watchers_locked(self) -> None:
        watcher = self._detach_watchers_locked()
        if watcher is not None:
            watcher.stop()

    def _stop_watcher(self, watcher: DirectoryChangeWatcher | None) -> None:
        if watcher is None:
            return
        try:
            watcher.stop()
        except Exception:
            log.debug("Could not stop Google TTS standby directory watcher.", exc_info=True)

    def _refresh_from_watcher(self, reason: str, generation: int) -> None:
        if generation != self._generation or self._shutdown:
            return
        with self._lock:
            if generation != self._generation or self._shutdown:
                return
        self.refresh_async(reason, force=False)

    def _watch_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = [ADDON_DIR]
        try:
            paths.append(voice_store.voice_dir())
        except Exception:
            log.debug("Could not resolve Google TTS voices directory for standby watcher.", exc_info=True)
        uniquePaths: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            try:
                resolved = str(path.resolve())
            except OSError:
                resolved = str(path)
            key = resolved.lower()
            if key in seen:
                continue
            seen.add(key)
            uniquePaths.append(path)
        return tuple(uniquePaths)

    def _terminate_bridge(self, bridge: ChromeTtsBridge | None) -> None:
        if bridge is None:
            return
        try:
            bridge.terminate()
        except Exception:
            log.debug("Could not terminate Google TTS standby browser runtime.", exc_info=True)


_manager = _StandbyRuntimeManager()


def initialize() -> None:
    _manager.initialize()


def refresh_async(reason: str = "", *, force: bool = True) -> None:
    _manager.refresh_async(reason, force=force)


def claim_bridge(catalog: VoiceCatalog) -> ChromeTtsBridge | None:
    return _manager.claim_bridge(catalog)


def note_synth_active() -> None:
    _manager.note_synth_active()


def release_synth_bridge(bridge: ChromeTtsBridge, catalog: VoiceCatalog) -> bool:
    return _manager.release_synth_bridge(bridge, catalog)


def release_synth_without_bridge(reason: str = "") -> None:
    _manager.release_synth_without_bridge(reason)


def terminate() -> None:
    _manager.terminate()

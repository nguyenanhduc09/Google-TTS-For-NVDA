from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

try:
    import config  # type: ignore
except Exception:  # pragma: no cover - used when imported standalone outside NVDA.
    config = None  # type: ignore

try:
    import globalVars  # type: ignore
except Exception:  # pragma: no cover - used when imported standalone outside NVDA.
    globalVars = None  # type: ignore

try:
    from logHandler import log
except Exception:  # pragma: no cover - used when imported standalone outside NVDA.
    import logging

    log = logging.getLogger("googleTtsForNvda")

from . import language_detector, language_utils, voice_store
from .audio_math import (
    build_speech_options,
    clamp_int,
    coerce_bool,
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
    return coerce_bool(value, default)


def keep_browser_runtime_ready_enabled() -> bool:
    if globalVars is not None:
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
    return clamp_int(value, default, 0, 100)


def _profile_bool(value: Any, default: bool = False) -> bool:
    return coerce_bool(value, default)


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
    return next(iter(available), "")


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
    return variantIds[0] if variantIds else ""


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
        languageKey = language_utils.normalize_language(str(rawLanguage))
        if languageKey and isinstance(rawProfile, dict):
            profiles[languageKey] = dict(rawProfile)
    return profiles


def _auto_language_profile_for_language(language: str | None) -> dict[str, Any]:
    languageKey = language_utils.normalize_language(language)
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


def _select_auto_language_candidates(availableLanguages: Any) -> list[str]:
    profiles = _auto_language_profiles()
    try:
        if config is None:
            raise RuntimeError("NVDA config unavailable")
        rawValue = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_CANDIDATES])
    except Exception:
        rawValue = DEFAULT_AUTO_LANGUAGE_CANDIDATES
    availableByKey = {language_utils.normalize_language(language): language for language in availableLanguages}
    if profiles:
        return [
            availableByKey[languageKey]
            for languageKey, profile in profiles.items()
            if languageKey in availableByKey and _profile_bool(profile.get("enabled"), False)
        ]
    candidates: list[str] = []
    seen: set[str] = set()
    for rawLanguage in rawValue.split(","):
        key = language_utils.normalize_language(rawLanguage)
        if not key or key in seen or key not in availableByKey:
            continue
        candidates.append(availableByKey[key])
        seen.add(key)
    return candidates


def _auto_language_candidates(catalog: VoiceCatalog) -> list[str]:
    return _select_auto_language_candidates(_available_languages(catalog))


def _select_auto_language_preferred(
    candidateLanguages: list[str],
    fallbackVoice: str,
    catalog: VoiceCatalog,
) -> str:
    try:
        if config is None:
            raise RuntimeError("NVDA config unavailable")
        configured = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PREFERRED])
    except Exception:
        configured = DEFAULT_AUTO_LANGUAGE_PREFERRED
    configuredKey = language_utils.normalize_language(configured)
    for language in candidateLanguages:
        if language_utils.normalize_language(language) == configuredKey:
            return language
    try:
        fallbackLanguage = catalog.language_for_voice(fallbackVoice)
    except Exception:
        fallbackLanguage = fallbackVoice
    fallbackRoot = language_utils.language_root(fallbackLanguage)
    for language in candidateLanguages:
        if language_utils.language_root(language) == fallbackRoot:
            return language
    return candidateLanguages[0] if candidateLanguages else fallbackLanguage


def _auto_language_preferred(catalog: VoiceCatalog, candidateLanguages: list[str], fallbackVoice: str) -> str:
    return _select_auto_language_preferred(candidateLanguages, fallbackVoice, catalog)


def _order_warmup_candidates(candidateLanguages: list[str], preferredLanguage: str) -> list[str]:
    if len(candidateLanguages) <= 1:
        return candidateLanguages
    orderedLanguages = list(candidateLanguages)
    if preferredLanguage in orderedLanguages:
        orderedLanguages.remove(preferredLanguage)
        orderedLanguages.insert(0, preferredLanguage)
    return orderedLanguages


def _auto_language_candidates_in_warmup_order(catalog: VoiceCatalog, currentVoice: str) -> list[str]:
    candidateLanguages = _auto_language_candidates(catalog)
    if len(candidateLanguages) <= 1:
        return candidateLanguages
    return _order_warmup_candidates(
        candidateLanguages,
        _auto_language_preferred(catalog, candidateLanguages, currentVoice),
    )


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
    normalizedLanguage = language_utils.normalize_language(language)
    if not normalizedLanguage:
        return _speaker_for_voice_or_language(catalog, fallbackVoice)
    fallbackVoice = _speaker_for_voice_or_language(catalog, fallbackVoice)
    if not fallbackVoice:
        for speaker in _speakers_for_language(catalog, normalizedLanguage):
            return speaker.id
        return ""
    try:
        fallbackSpeaker = catalog.speaker_for_voice(fallbackVoice)
    except Exception:
        fallbackSpeaker = None
    if fallbackSpeaker is not None and language_detector.language_matches(fallbackSpeaker.language, normalizedLanguage):
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
    if fallbackSpeaker is not None and language_utils.language_root(fallbackSpeaker.language) == rootLanguage:
        return fallbackVoice
    for languageKey, speakers in catalog.voices_by_language().items():
        if language_utils.language_root(languageKey) == rootLanguage:
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
    return catalog.speakers[0].id if catalog.speakers else ""


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
    if not reason.startswith("watched directory changed"):
        return False
    prefix = "watched directory changed: "
    if reason.startswith(prefix):
        changedPathText = reason[len(prefix) :].strip()
        if changedPathText:
            try:
                changedPath = Path(changedPathText).resolve()
                voicesDir = voice_store.voice_dir().resolve()
                addonDir = ADDON_DIR.resolve()
                if changedPath == voicesDir and voicesDir != addonDir:
                    # Voice folder edits (.download temp files, etc.) still go through
                    # _catalog_signature() and restart only when installed voices change.
                    return False
            except OSError:
                pass
    return True


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
        self._cleanupThreads: set[threading.Thread] = set()
        self._synthActive = False
        self._shutdown = True

    def initialize(self) -> None:
        with self._lock:
            self._shutdown = False

    def refresh_async(self, reason: str = "", *, force: bool = True) -> None:
        bridgeToTerminate: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        previousWorker: threading.Thread | None = None
        worker: threading.Thread | None = None
        pendingGeneration = 0
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
                pendingGeneration = self._generation
                cancelEvent = threading.Event()
                previousWorker = self._worker
                workerAlive = previousWorker is not None and previousWorker.is_alive()
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
                    args=(pendingGeneration, cancelEvent, reason),
                    daemon=True,
                )
                self._worker = worker
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(bridgeToTerminate)
        if worker is None:
            return
        if (
            previousWorker is not None
            and previousWorker is not threading.current_thread()
            and hasattr(previousWorker, "join")
            and previousWorker.is_alive()
        ):
            previousWorker.join(timeout=0.5)
        with self._lock:
            if (
                self._shutdown
                or self._synthActive
                or not keep_browser_runtime_ready_enabled()
                or self._generation != pendingGeneration
                or self._worker is not worker
            ):
                if self._worker is worker:
                    self._worker = None
                return
            worker.start()

    def _dispose_detached_async(
        self,
        watcher: DirectoryChangeWatcher | None,
        bridge: ChromeTtsBridge | None,
    ) -> None:
        if watcher is None and bridge is None:
            return

        cleanupThread: threading.Thread | None = None

        def _cleanup() -> None:
            try:
                self._stop_watcher(watcher)
                self._terminate_bridge(bridge)
            finally:
                with self._lock:
                    if cleanupThread is not None:
                        self._cleanupThreads.discard(cleanupThread)

        cleanupThread = threading.Thread(
            name="googleTtsForNvda.standbyCleanup",
            target=_cleanup,
            daemon=True,
        )
        with self._lock:
            self._cleanupThreads.add(cleanupThread)
            cleanupThread.start()

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
            safeForStandby = getattr(self._bridge, "safe_for_standby_release", None)
            isConnected = getattr(self._bridge, "is_connected", None)
            bridgeHealthy = (safeForStandby() if callable(safeForStandby) else True) and (
                isConnected() if callable(isConnected) else True
            )
            if self._bridge is not None and self._ready and self._signature == signature and bridgeHealthy:
                claimedBridge = self._bridge
                self._bridge = None
                self._signature = None
                self._ready = False
            else:
                bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
        if claimedBridge is not None:
            self._dispose_detached_async(watcherToStop, None)
            return claimedBridge
        self._dispose_detached_async(watcherToStop, bridgeToTerminate)
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
        self._dispose_detached_async(watcherToStop, bridgeToTerminate)

    def release_synth_bridge(self, bridge: ChromeTtsBridge, catalog: VoiceCatalog) -> bool:
        previousBridge: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        safeForStandby = getattr(bridge, "safe_for_standby_release", None)
        isConnected = getattr(bridge, "is_connected", None)
        bridgeHealthy = (safeForStandby() if callable(safeForStandby) else True) and (
            isConnected() if callable(isConnected) else True
        )
        with self._lock:
            if self._shutdown or not keep_browser_runtime_ready_enabled() or not bridgeHealthy:
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
        self._dispose_detached_async(watcherToStop, previousBridge)
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
        self._dispose_detached_async(watcherToStop, None)
        self.refresh_async(reason or "Google TTS synth released without a reusable browser runtime")

    def terminate(self) -> None:
        bridgeToTerminate: ChromeTtsBridge | None
        watcherToStop: DirectoryChangeWatcher | None = None
        workerToJoin: threading.Thread | None = None
        cleanupThreadsToJoin: list[threading.Thread] = []
        with self._lock:
            self._shutdown = True
            self._synthActive = False
            self._generation += 1
            workerToJoin = self._worker
            cleanupThreadsToJoin = list(self._cleanupThreads)
            bridgeToTerminate = self._clear_standby_locked(cancelWorker=True)
            watcherToStop = self._detach_watchers_locked()
        self._stop_watcher(watcherToStop)
        self._terminate_bridge(bridgeToTerminate)
        if (
            workerToJoin is not None
            and workerToJoin is not threading.current_thread()
            and hasattr(workerToJoin, "join")
            and workerToJoin.is_alive()
        ):
            workerToJoin.join(timeout=2.0)
        for cleanupThread in cleanupThreadsToJoin:
            if cleanupThread is not threading.current_thread() and cleanupThread.is_alive():
                cleanupThread.join(timeout=2.0)

    def _run_refresh(self, generation: int, cancelEvent: threading.Event, reason: str) -> None:
        bridgeForWorker: ChromeTtsBridge | None = None
        bridgeToTerminate: ChromeTtsBridge | None = None
        watcherToStop: DirectoryChangeWatcher | None = None
        try:
            needsCatalogCheck = False
            if not _refresh_reason_requires_runtime_restart(reason):
                with self._lock:
                    needsCatalogCheck = bool(
                        self._bridge is not None
                        and self._ready
                        and generation == self._generation
                        and not self._shutdown
                        and not self._synthActive
                    )
            catalog = _installed_catalog()
            if cancelEvent.is_set():
                raise CdpCancelled()
            if catalog is None:
                with self._lock:
                    if generation == self._generation:
                        bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
                        watcherToStop = self._start_watchers_locked()
                return
            signature = _catalog_signature(catalog)
            if cancelEvent.is_set():
                raise CdpCancelled()
            if needsCatalogCheck:
                with self._lock:
                    if (
                        generation == self._generation
                        and not self._shutdown
                        and not self._synthActive
                        and self._bridge is not None
                        and self._ready
                        and self._signature == signature
                    ):
                        watcherToStop = self._start_watchers_locked()
                        log.debug("Google TTS standby browser runtime already warm, skipping refresh.")
                        return
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
                watcherToStop = self._start_watchers_locked()
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
                    watcherToStop = self._start_watchers_locked()
        finally:
            with self._lock:
                if generation == self._generation and self._worker is threading.current_thread():
                    self._worker = None
            self._stop_watcher(watcherToStop)
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

    def _start_watchers_locked(self) -> DirectoryChangeWatcher | None:
        oldWatcher = self._detach_watchers_locked()
        if self._shutdown or self._synthActive or not keep_browser_runtime_ready_enabled():
            return oldWatcher
        generation = self._generation
        self._watcher = DirectoryChangeWatcher(
            self._watch_paths,
            lambda reason: self._refresh_from_watcher(reason, generation),
        )
        self._watcher.start()
        return oldWatcher

    def _detach_watchers_locked(self) -> DirectoryChangeWatcher | None:
        if self._watcher is None:
            return None
        watcher = self._watcher
        self._watcher = None
        if hasattr(watcher, "signal_stop"):
            watcher.signal_stop()
        return watcher

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
        paths: list[Path] = [ENGINE_DIR]
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

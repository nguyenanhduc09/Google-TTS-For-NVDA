# Google TTS For NVDA — Code Map & Technical Architecture

Technical code map indexing the codebase architecture, modules, classes, functions, and key constants across all subsystems.

---

## 1. Project Architecture

### Directory Layout

- **googleTtsForNvda/** — Add-on source root packaged into distribution bundles
  - **manifest.ini** — Add-on metadata and configuration (version, author, summary, description, changelog)
  - **buildInfo.json** — Internal updater hotfix build metadata (`schema`, `baseVersion`, `updateBuild`)
  - **LICENSE** — Packaged copy of the add-on's GPL-2.0 license
  - **synthDrivers/googleTtsForNvda/** — Synthesizer driver and runtime bridge
    - **__init__.py** — `SynthDriver` implementation, NVDA settings ring, config migration, speech loop, audio cache
    - **audio_math.py** — Pure audio math, rate/pitch/pause conversions, SeaNet/Lemonbalm rate protection, speech options builder
    - **bridge.py** — `ChromeTtsBridge`, `BrowserProcessManager`, `CdpClient`, `CdpDispatcher`, HTTP server, config spec, browser lifecycle
    - **standby.py** — `_StandbyRuntimeManager`, background browser-runtime readiness and voice pre-warming manager
    - **watcher.py** — `DirectoryChangeWatcher`, kernel-blocked Win32 directory-change watcher
    - **catalog.py** — `VoiceCatalog`, `VoicePackage`, `Speaker` models, engine library integrity validation
    - **cld2/** — Vendored Compact Language Detector 2 dynamic libraries (`cld2_x86.dll`, `cld2_x64.dll`, `cld2.dll`, `LICENSE.txt`, `README.txt`)
    - **language_detector.py** — CLD2-backed language detection, locale redirects, candidate priors, single-character and spelling-clause routing
    - **language_profiles.py** — Unicode / NFC normalization, token/URL/emoji predicates, clause-context routing, mixed-text segmentation
    - **language_utils.py** — Language normalization, NVDA special locale mappings, localized display names, `call_compat()`
    - **speech_processing.py** — `TextSegmenter`, Unicode sentence boundary detection, `PcmSilenceShortener`, `PcmLeadBuffer`, grapheme/emoji cut protection
    - **unicode_data.py** — Pre-generated UCD / CLDR script/combining/currency/emoji ranges, units, Latin exemplars, sentence terminals, and normalization table
    - **voice_store.py** — Voice package download, copy, verify, remove, persistent verification cache
    - **web/** — Headless Chromium bridge environment (`index.html`, `bridgeHarness.js`)
    - **WasmTtsEngine/<ENGINE_VERSION>/** — Bundled Chromium WASM TTS engine runtime (`bindings_main.js/.wasm`, `background_compiled.js`, `offscreen.html`, `offscreen_compiled.js`, `manifest.json`, `wasm_tts_manifest_v3.json`, `voices.json`, `streaming_worklet_processor.js`, `_metadata/verified_contents.json`, `VOICE_CATALOG_NOTICE.txt`, licenses)
    - **websocketClientRepo/** — Vendored isolated `websocket-client` package (`websocket/`)
  - **globalPlugins/googleTtsForNvda/** — Global plugin integration and UI components
    - **__init__.py** — Tools menu integration, synth switching interception, voice dict hooks, auto-language speech/SayAll/spelling hooks, read-only settings guards
    - **settings.py** — `GoogleTtsSettingsPanel`, NVDA settings dialog category for Google TTS
    - **uiUtils.py** — Shared UI/NVDA utilities (DIP scaling, read-only text focus handling, size formatting, modal/popup/secure-mode guards, Windows Store detection, runtime error dialogs)
    - **updater.py** — Add-on update manifest checker, HTTPS-only download, SHA-256 verification, updater staging
    - **updateGui.py** — Add-on update notification dialogs, download progress dialogs, auto-check controller
    - **voiceManager.py** — `VoiceManagerDialog`, wx Voice Manager with Installed and Download tabs
  - **doc/** — Localized user documentation (`en/readme.html`, `<locale>/readme.html`)
  - **locale/** — Localized interface catalogs (`<locale>/LC_MESSAGES/nvda.po`, `nvda.mo`, `manifest.ini`, `languageSort.json`)
- **.github/workflows/test.yml** — CI check workflow configuration (see [CONTRIBUTING.md](CONTRIBUTING.md))
- **tests/** — Automated unit tests, fuzzers, benchmarks, and NVDA static API contract checks (see [tests/README.md](tests/README.md))
- **build.bat** / **build.sh** — Windows and POSIX `.nvda-addon` packaging scripts (see [CONTRIBUTING.md](CONTRIBUTING.md))
- **build_i18n.py** — Localization CLI for POT/PO/MO and documentation catalogs (see [TRANSLATING.md](TRANSLATING.md))
- **generate_voices_json.py** — Upstream voice catalog scraper and generator from ChromeOS/Google textproto
- **generate_unicode_data.py** — UCD and CLDR data generator for `unicode_data.py`
- **make_update_manifest.py** — Release update manifest generator producing `stable.json` for the internal updater (see [UPDATER_RELEASE_GUIDE.md](UPDATER_RELEASE_GUIDE.md))
- **AGENTS.md**, **CONTRIBUTING.md**, **TRANSLATING.md**, **UPDATER_RELEASE_GUIDE.md**, **readme.md** — Architecture code map plus contributor/CI, localization, release/updater, and user documentation
- **mypy.ini**, **ruff.toml**, **.gitattributes**, **.gitignore**, **LICENSE** — Tooling, Git, and GPL-2.0 license configuration

---

### Runtime Paths

> **Note**: `<CONFIG>` denotes the active NVDA configuration root: `%APPDATA%/nvda` (installed) or `<NVDA_DIR>/userConfig` (portable / direct), overridable via `-c` / `--config-path`. `<PROFILES_ROOT>` denotes `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/`.

| Purpose | Location / Value | Description & Module Reference |
|---|---|---|
| NVDA config root | `<CONFIG>` | Active NVDA configuration directory (`voice_store.py:_default_config_path()`). |
| Add-on installed root | `<CONFIG>/addons/googleTtsForNvda/` | Directory where the add-on package is unpacked and executed. |
| Add-on user data root | `<CONFIG>/googleTtsForNvda/` | Persistent add-on data directory surviving updates (`voice_store.py:data_root()`). |
| Downloaded voice packages | `<CONFIG>/googleTtsForNvda/voices/*.zvoice` | Downloaded voice package archives (`voice_store.py:voice_dir()`). |
| Voice verification cache | `<CONFIG>/googleTtsForNvda/verified_voices.json` | File size, mtime, and SHA-256 verification cache (`voice_store.py:_verification_cache_path()`). |
| Runtime voices.json | `<CONFIG>/googleTtsForNvda/runtime/voices.json` | Dynamic JSON catalog served via local HTTP bridge (`bridge.py:BrowserProcessManager._write_runtime_voices_json()`). |
| NVDA configuration file | `<CONFIG>/nvda.ini` | Main NVDA config storing settings under `[googleTtsForNvda]` (`bridge.py:CONFIG_SECTION`). |
| Voice dictionaries | `<CONFIG>/speechDicts/` | Per-voice pronunciation dictionaries (`globalPlugins/__init__.py:_load_voice_dictionary_for_voice()`). |
| Browser persistent profiles | `<PROFILES_ROOT>/persistentSession/` | Isolated persistent browser user data directories (`bridge.py:BrowserProcessManager._browser_profile_root()`). |
| Browser temporary profiles | `<PROFILES_ROOT>/session-<pid>-<timestamp>/` | Ephemeral sessions spawned when persistent profile is locked (`bridge.py:BrowserProcessManager._start_browser_choice()`). |
| Profile size tracker | `<PROFILES_ROOT>/profileSizeCheck.json` | 24-hour periodic profile size check tracker (`bridge.py:PERSISTENT_PROFILE_SIZE_CHECK_FILE_NAME`). |
| Updater download directory | `%TEMP%/googleTtsForNvda-updates/` | Staging folder for `stable.json` and `.nvda-addon` installers (`updater.py:_update_download_dir()`). |

---

### Speech Data Flow

1. NVDA calls `SynthDriver.speak()` with a sequence of text segments, pause commands, and speech parameters.
2. The driver segments text for low-latency streaming, applies pause shortening, builds synthesis options (voice, rate, pitch, volume), and enqueues requests to a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP connection are established, then evaluates `window.googleTtsForNvdaSpeak(...)` via CDP `Runtime.evaluate`.
4. `bridgeHarness.js` passes text to the bundled Google WASM TTS engine (`onSpeak`), intercepts synthesized `AudioWorkletNode` buffers, applies anti-aliased pitch shifting and WSOLA tempo stretching (for protected SeaNet/Lemonbalm voices), gain makeup, and soft clipping, converts Float32 audio to 16-bit PCM, and streams base64 PCM chunks back through the `googleTtsForNvdaBridge` CDP binding.
5. Python receives `Runtime.bindingCalled` events via `CdpDispatcher`, decodes base64 PCM bytes, and feeds audio directly to `nvwave.WavePlayer` for output.

---

## 2. Synth Driver & NVDA Integration Code Map

- **Synthesizer Switching & Voice Prompt (`globalPlugins/__init__.py`)**:
  - Intercepts `synthDriverHandler.setSynth` to verify that at least one Google TTS voice package is installed; prompts with Voice Manager if none is installed (`_wrap_nvda_callable()`, `_extract_set_synth_invocation()`, `_call_set_synth_compat()`, `_normalize_set_synth_args()`, `_callable_contains_target()`, `_unwrap_self_reference()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, `_unpatch_synth_selection()`).

- **Voice Dictionaries & Read-Only Settings Guards (`globalPlugins/__init__.py`)**:
  - Patches NVDA voice dictionary dialogs and auto-settings panels (`_is_voice_dictionary_dialog()`, `_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_hide_google_tts_auto_profile_speech_controls()`, `_patch_read_only_text_setting()`, `_unpatch_read_only_text_setting()`).
  - Hooks `speechDictHandler.loadVoiceDict` via `_VoiceDictionarySynthProxy` to load speaker-specific `.dic` files for the active Google TTS variant with in-memory caching validated by file stat signatures (`_load_voice_dictionary_for_voice()`, `_resolve_nvda_voice_speech_dict()`, `_file_stat_signature()`, `_voiceDictCache`, `_current_google_tts_speaker_id()`, `_patch_google_tts_voice_dictionary_loading()`, `_unpatch_google_tts_voice_dictionary_loading()`). For automatic language profile speech hooks, see [Section 9](#9-automatic-language-detection--profiles-code-map).

- **Synth Driver Core Lifecycle & Audio Output (`synthDrivers/__init__.py`)**:
  - Subclasses `synthDriverHandler.SynthDriver`, managing driver lifecycle, settings ring (`_PAUSE_MODE_SETTING` / `pauseMode`), config migration, live catalog updates, prosody commands, indexed PCM playback, and audio output (`_ensure_config_compat()`, `terminate()`, `speak()`, `cancel()`, `pause()`, `loadSettings()`, `refresh_installed_catalog()`, `isSupported()`, `languageIsSupported()`, `_player_lock`, `_catalog_lock`, `_warmup_lock`, `_current_player()`, `_iter_speech_chunks()`, `_speak_worker()`, `_speak_text()`, `_active_cancel_set()`, `_apply_prosody_command()`, `_is_prosody_reset_command()`, `_write_pcm_to_player()`, `_feed_audio()`, `_feed_audio_with_indexes()`, `_feed_silence()`, `_sync_player()`, `_finish_request_audio()`, `_current_output_device()`, `_default_output_device()`, `_audio_device_error()`, `_create_wave_player()`, `_recreate_wave_player()`, `_ensure_current_output_device()`, `_schedule_ui_callback()`, `_run_prompt_when_modal_dialogs_clear()`).

- **Tools Menu Integration & Input Gestures (`globalPlugins/__init__.py`)**:
  - Integrates into NVDA Tools menu and registers input gestures for Voice Manager and Settings dialogs (`GlobalPlugin.__init__()`, `GlobalPlugin._on_post_nvda_startup()`, `GlobalPlugin.terminate()`, `GlobalPlugin.on_open_voice_manager()`, `GlobalPlugin.script_openVoiceManager()`, `GlobalPlugin.script_openSettings()`, `GlobalPlugin.__gestures`).

---

## 3. Browser Runtime, Lifecycle & Resilience Code Map

- **Config Spec & Browser Availability (`bridge.py`)**:
  - Config spec registration & cache eviction: `ensure_config_spec()`, `CONFIG_SPEC`, `CONFIG_AUTO_UPDATE_CHECK`, `configured_browser_runtime()`, `configured_keep_browser_runtime_ready()`.
  - Availability & resolution: `browser_runtime_available()`, `browser_executable_available()`, `edge_webview2_available()`, `edge_webview2_blocks_effective_runtime()`, `find_browser()`, `effective_browser_runtime()`, `_runtime_fallback_order()`, `_no_browser_runtime_error()`.

- **Chromium Profile Isolation & Lock Recovery (`bridge.py`)**:
  - Hidden process startup & termination: `_hidden_chrome_startup_kwargs()`, `_chrome_window_hider_apis()`, `_hide_chrome_windows()`, `_terminate_subprocess()`.
  - Session startup & catalog sync: `BrowserProcessManager._start_browser_choice()` spawns Chromium with `DEVNULL` stdio, catches `_BrowserProfileInUseError` (from `_read_devtools_port()` / `_browser_profile_in_use_error()`), and retries with an isolated temporary session profile; `_write_runtime_voices_json()` and `update_catalog()` refresh the served catalog in place.
  - Profile lifecycle & size bounds: `_browser_profile_dir_name()`, `_get_browser_profile_dir()`, `_cleanup_old_browser_profiles()`, `_release_chrome_profile()`, `_remove_chrome_profile()`, `PERSISTENT_PROFILE_MAX_BYTES`, `PERSISTENT_PROFILE_SIZE_CHECK_FILE_NAME`, `PERSISTENT_PROFILE_SIZE_CHECK_INTERVAL_SECONDS`, `_persistent_profile_size_check_due()`, `_remember_persistent_profile_size_check()`.

- **Speech Loop Crash Resilience & Fatal Fallback (`synthDrivers/__init__.py`, `uiUtils.py`)**:
  - `_speech_loop()` wraps `_speak_worker()` in exception handling so the daemon thread survives synthesis errors; `_speak_worker()` and `_warm_current_voice_async()` verify `ChromeTtsBridge.browser_runtime_available()`.
  - `_trigger_fatal_fallback()` and `_trigger_fatal_fallback_async()` debounce fatal errors, flush `_speechQueue`, stop playback, switch NVDA to its fallback synthesizer, and invoke `uiUtils.show_runtime_error_dialog()`.

- **CDP Connection, Dispatcher & Harness Readiness (`bridge.py`)**:
  - `ChromeTtsBridge.ensure_connection()` serializes startup via `_connectionLock` with candidate fallback and cancellation checks; `_set_engine_locked()` and `update_catalog()` manage engine/catalog state.
  - `CdpClient.request()` manages WebSocket CDP calls (`CdpCancelled` on cancel); `CdpDispatcher` routes `Runtime.bindingCalled` events and wakes pending requests fast on disconnect or runtime error.
  - `WasmTtsEngineBridge` tracks busy state and cancellation (`runtime_busy`, `_set_runtime_busy()`, `cancel_current()`, `send_fast_stop()`, `enable_cdp_domains()`, `wait_until_ready()`).

- **Runtime Health, Memory Throttling & Recycling (`bridge.py`, `synthDrivers/__init__.py`)**:
  - `_elevate_chrome_priority()` sets `ABOVE_NORMAL_PRIORITY_CLASS` and disables Windows EcoQoS via `SetProcessInformation`.
  - `_process_tree_memory_usage()` and `BrowserProcessManager.browser_memory_usage()` check `RUNTIME_PRIVATE_BYTES_RECYCLE_THRESHOLD` (1280 MB) and `RUNTIME_WORKING_SET_BYTES_RECYCLE_THRESHOLD` (1024 MB); `_mark_runtime_for_recycle_locked()`, `_mark_runtime_error_for_recycle()`, `_mark_memory_recycle_if_needed_locked()`, `_reset_runtime_locked()`, `ChromeTtsBridge.maybe_recycle_runtime()`, and `SynthDriver._maybe_recycle_bridge_after_request()` recycle runtimes safely.

---

## 4. CDP Communication, WebSocket & Dependency Isolation Code Map

- **Bundled WebSocket Client & Local HTTP Isolation (`bridge.py`, `websocketClientRepo/websocket/`)**:
  - Relative-imported vendored WebSocket client (`_abnf.py:native_byteorder,_mask`, `_http.py:HAVE_PYTHON_SOCKS,_start_proxied_socket`, `_utils.py:_create_bundled_utf8_validator,validate_utf8`) plus proxy-bypassed `_LOCAL_HTTP_OPENER` (`ProxyHandler({})`) for loopback CDP requests.

- **Cross-Origin Isolation HTTP Headers (`bridge.py:_BridgeRequestHandler`)**:
  - Serves `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`, and `Cross-Origin-Resource-Policy: same-origin` for `SharedArrayBuffer` in multi-threaded WASM.

---

## 5. Audio Math, Loudness & SeaNet/Lemonbalm Processing Code Map

- **Pure Audio Math & Speech Options (`audio_math.py`)**:
  - Rate and pitch mapping: `rate_to_chrome()` (0.35x–2.0x standard / up to 4.0x with boost, clamped `0.1x–10.0x`) and `pitch_to_chrome()` (-12 to +12 semitones, clamped `0.1x–3.0x`).
  - SeaNet/Lemonbalm rate & pitch decoupling (`uses_protected_engine_rate()`, `_SEANET_PACKAGE_RE`): caps WASM engine rate at `PROTECTED_ENGINE_RATE` (`1.25x`), clamps `artificialRate` to `MIN_ARTIFICIAL_RATE` (`0.5x`)–`MAX_ARTIFICIAL_RATE` (`1.85x`) with soft-knee compression above `_SOFT_KNEE_ARTIFICIAL_RATE` (`1.60x`, `_MAX_RAW_PROTECTED_RATIO`), and decouples pitch (`enginePitch = 1.0`, `postPitch = pitchValue`).
  - Options & rate-break helpers: `build_speech_options()` builds normalized options with `OUTPUT_GAIN_MAKEUP` (`1.70x`); `BREAK_RATE_TABLE`, `BREAK_RATE_FACTOR_MIN`, `BREAK_RATE_FACTOR_MAX`, `END_OF_UTTERANCE_RATE_FACTOR_MIN`, `END_OF_UTTERANCE_RATE_FACTOR_MAX`, `interpolate_rate_factor()`, `break_rate_factor()`, `end_of_utterance_rate_factor()`, `clamp_int()`, and `coerce_bool()` supply shared rate-pause scaling and option coercion.

- **Browser Harness Audio Processing Pipeline (`web/bridgeHarness.js`)**:
  - Engine lifecycle & scheduling: `installZeroDelayMacrotaskScheduler()`, `googleTtsSetTimeout()`, `googleTtsClearTimeout()`, `isTtsEngineInstance()`, `getTtsEngine()`, `ensureEngineInitialized()`, `canInstallVoiceOnDemand()`, `packagePrepareKeys()`, `waitForPendingPackagePrepares()`, `ensureLanguageReady()`, `prepareEngineAndLanguage()`, `runEngineSpeak()`, `waitForEngineDrain()`, `drainPendingWorkletChunks()`, `flushEngineLeftoverSamples()`, `stopActiveSynthesis()`, `stopActiveSynthesisWithTimeout()`, `stopIfActiveForControl()`, `resetSynthesisRunState()`, `googleTtsForNvdaPreload()`, `googleTtsForNvdaSpeak()`, `googleTtsForNvdaStop()`.
  - Packetization, gain, anti-aliased pitch & WSOLA tempo: `buffersToPcmBase64()`, `appendSamples()`, `audioPacketSampleTarget()`, `queueAudioPacket()`, `flushAudioQueue()`, `outputGainFromPayload()`, `limitSample()`, `artificialRateFromPayload()`, `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `filterPitchAntiAlias()`, `processPitchSamples()`, `resetTempoProcessor()`, `processTempoSamples()`, `queueTempoInput()`, `appendStabilizedTempoTail()`, `blendTailWithFrame()`, `bestTempoFrameOffset()`, `tempoFrameOutput()`, `pushTempoPart()`, `flushAudioProcessors()`, `queueAudio()`, `queueProcessedAudio()`, `finishSegmentAudio()`.

---

## 6. Speech Processing, Latency Segmentation & Pause Shortening Code Map

- **Latency Text Segmentation (`speech_processing.py:TextSegmenter`)**:
  - Sanitization & segmentation: `sanitize_speech_text()` (`_SPEECH_SANITIZE_TABLE` maps non-standard Unicode whitespace from `NORMALIZATION_TABLE` and PUA codepoints to ASCII spaces with 1:1 index alignment), `split_text_for_latency()`, `iter_text_segments_for_latency()`, `iter_indexed_text_segments()`, `spoken_bridge_segments()`, `needs_index_boundary_space()`.
  - Grapheme & emoji cut protection: `_extend_cut_over_combining_marks()` extends cuts over combining marks (`language_profiles.is_combining_mark()`), `GRAPHEME_EXTEND_EXTRA_RANGES` (`ZWNJ`, halfwidth Kana marks, skin tones, tags), `ZWJ` sequences, and `REGIONAL_INDICATOR_RANGES` flag pairs.
  - Boundary detection & guards: sentence splits (`find_sentence_splits()`, `is_sentence_terminator_character()`, `TAILORED_SENTENCE_TERMINATORS`, `_is_sentence_trailing_closer()`, `_sentence_terminator_stays_with_token()`, `_period_stays_with_previous_token()`, `_period_is_numeric_separator()`), soft phrase cuts (`SOFT_BREAK_CHARS`, `_is_soft_break_character()`, `_is_colon_like_character()`, `_is_dash_like_character()`, `_find_soft_phrase_cut()`, `_is_contextual_soft_phrase_cut()`, `_is_forced_soft_break()`), space-free scripts (`NO_SPACE_SCRIPT_PROFILES`, `_find_no_space_range()`, `_is_no_space_script_character()`, `_find_no_space_script_cut()`), forced cuts (`_iter_forced_latency_segments()`, `_find_forced_latency_cut()`), and structural URL/wrapper guards (`looks_like_url_token()`, `_strip_surrounding_wrappers()`, `should_pause_after_segment()`, `ends_with_pause_punctuation()`).

- **Pause Shortening & Lead Buffering (`speech_processing.py`, `synthDrivers/__init__.py`)**:
  - `PcmSilenceShortener` (`PAUSE_MODE_DO_NOT_SHORTEN`, `PAUSE_MODE_SHORTEN_END_ONLY`, `PAUSE_MODE_SHORTEN_ALL`, `SHORTENED_SILENCE_KEEP_MS`, `SHORTENED_ALL_PAUSES_KEEP_MS`, `SEANET_RAW_SILENCE_THRESHOLD`, `MAX_PAUSE_SILENCE_THRESHOLD`, `minPauseTriggerMs`, `feed()`, `flush_boundary()`, `finish()`), `create_pcm_silence_shortener()` (with rate-scaled `keepSilenceMs` via `effective_chrome_rate()` and `keep_silence_ms_for_rate()`, `min_pause_trigger_ms_for_rate()`, and SeaNet/gain-aware `pause_silence_noise_floor()`), `pcm_bytes_for_milliseconds()`, `align_pcm_bytes()`, `pcm_has_audible_sample()`.
  - `PcmLeadBuffer` (`LIVE_MULTI_SEGMENT_LEAD_MS`) buffers initial multi-segment audio; `synthDrivers/__init__.py` scales `BreakCommand` via `audio_math.break_rate_factor()` and streams consecutive clauses/sentences inside one bridge batch (`TextSegmenter.spoken_bridge_segments()`, up to `SHORT_CACHE_MAX_HIDDEN_SEGMENTS` / `SHORT_CACHE_MAX_CHARS`), advancing `IndexCommand` markers at `segmentEnd` and only flushing at sentence boundaries when auto-language switching needs per-sentence detection (`_auto_language_detection_enabled()` and `activeLanguage is None`).

---

## 7. Voice Catalog & Voice Store Code Map

- **Voice Catalog & Models (`catalog.py`)**:
  - Engine integrity: `BASE_DIR`, `ENGINE_VERSION`, `ENGINE_ROOT`, `ENGINE_DIR`, `CATALOG_PATH`, `REQUIRED_ENGINE_FILES`, `UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS` (`("locomel", "darwinnrio")`), `is_package_supported_by_engine()`, `package_id_to_language()`, `inspect_engine_library()`, `EngineLibraryError`, `engine_library_error_message()`.
  - Models (`VoicePackage`, `Speaker`, `VoiceCatalog`): `load()`, `package_for_voice()`, `speaker_for_voice()`, `package_by_id()`, `voices_by_language()`, `language_for_voice()`, `to_runtime_json()`.

- **Package Management & Verification Cache (`voice_store.py`)**:
  - Operations & TLS guards: `package_file()`, `download_package()` (`DOWNLOAD_CHUNK_SIZE`, `VOICE_PACKAGE_MAX_BYTES`, SHA-256 verification), `_HttpsOnlyRedirectHandler`, `_open_https_url()`, `_verify_package_file()`, `remove_package()`, `copy_existing_package()`, `_package_lock()`, `_package_generation()`, `_bump_package_state_generation()`.
  - Verification cache (`_VERIFICATION_CACHE_FILE`, `_VERIFICATION_CACHE_VERSION`, `_verificationCacheLock`): `_load_persistent_verification_cache()`, `_save_persistent_verification_cache()`, `_remember_verified_package()`, `_forget_verified_package()`, `is_package_installed()`, `physically_installed_packages()`, `usable_installed_packages()`, `installed_packages()`.

---

## 8. Voice Preload, Standby Readiness & Directory Watcher Code Map

- **Voice Preloading & Harness Session Fencing (`synthDrivers/__init__.py`, `standby.py`, `web/bridgeHarness.js`)**:
  - `SynthDriver._warm_current_voice_async()` and `standby.py` resolve warmup voice IDs (`_warmup_voice_ids()`, `_warmup_voice_ids_for_voice()`, `_auto_language_candidates_in_warmup_order()`, `_voice_id_for_package()`, `_select_auto_language_candidates()`, `_select_auto_language_preferred()`, `_order_warmup_candidates()`) and call `ChromeTtsBridge.preload_voice()` / `WasmTtsEngineBridge.preload_voice()`.
  - `bridgeHarness.js` fences `emit()`, `queueAudioPacket()`, `queueProcessedAudio()`, `finishSegmentAudio()`, and `stopActiveSynthesis()` with monotonic session tokens (`currentSessionToken`, `beginSession()`, `isCurrentSession()`).

- **Standby Browser Runtime Manager (`standby.py`) & Directory Watcher (`watcher.py`)**:
  - `keep_browser_runtime_ready_enabled()` and `_StandbyRuntimeManager` (`refresh_async()`, `_run_refresh()`, `claim_bridge()`, `release_synth_bridge()`, `_dispose_detached_async()`, `terminate()`).
  - `_catalog_signature()` combines `configured_browser_runtime()`, `_wasm_engine_signature()` (`ENGINE_VERSION`, `ENGINE_DIR` mtime, `CATALOG_PATH` mtime), installed package metadata, and warmup voice IDs.
  - `DirectoryChangeWatcher` (`_ensure_kernel32()`, `_path_is_dir()`, `start()`, `signal_stop()`, `stop(timeout=5.0)`) monitors watched directories via Win32 `FindFirstChangeNotificationW` / `WaitForMultipleObjects`.

---

## 9. Automatic Language Detection & Profiles Code Map

- **CLD2 Language Detector (`language_detector.py`)**:
  - `_Cld2Detector._load_library()` loads `cld2_x64.dll` or `cld2_x86.dll` from `cld2/`; `GoogleTtsProfileLangCommand` tags speech sequence items.
  - `detect_language(text, candidateLanguages, preferredLanguage=None)` returns `DetectionResult` (`_MIN_RELIABLE_PERCENT`, `_MAX_CLD2_INPUT_CHARS`), routing single graphemes (`resolve_single_grapheme_candidate()`), spelling clauses (`is_spelling_only_text()`), and strings without language words (`not has_language_words()`) to `_preferred_candidate()`, recovering short words via candidate priors, and falling back to Unicode script and CLDR exemplar signals.
  - Aliases, dialect redirects & matching: `_LANGUAGE_ALIASES`, `_CHINESE_LANGUAGE_ROOTS` (`{"cmn", "zh"}`), `_LANGUAGE_REDIRECTS`, `redirect_language()`, `language_matches()`, `language_match_keys()`, `_language_family()`, `_candidate_for_language()`.

- **Unified Language Profiles & Mixed-Text Segmentation (`language_profiles.py`)**:
  - Normalization: `normalize_mathematical_alphanumeric()` applies `NORMALIZATION_TABLE` and composes NFD sequences to NFC while preserving singleton Greek punctuation (`U+037E`, `U+0387` via `_NFC_PRESERVED_PUNCT_RE`).
  - Token & character predicates: `in_codepoint_ranges()`, `is_combining_mark()`, `is_letter_codepoint()`, `is_number_codepoint()`, `is_closing_punctuation_codepoint()`, `is_wrapper_punctuation_codepoint()`, `punctuation_subcategory_codepoint()`, `is_punctuation_codepoint()`, `is_single_grapheme_token()`, `has_vietnamese_diacritics()`, `is_emoji_token()`, `is_number_token()`, `is_url_or_email_token()`, `is_symbol_or_emoji_token()`, `has_language_words()`, `is_spelling_only_text()`, `get_character_script()`, `single_character_candidate_root()`, `single_grapheme_candidate_root()` cover numbers (`_NUMBER_CHUNK_PATTERN`), RFC 3986 URLs/emails/domains (`_URL_EMAIL_DOMAIN_PATTERN`), ISO 4217 currencies, CLDR units, time formats, and emoji runs (`_EMOJI_RUN_PATTERN`).
  - Mixed-text segmentation: `segment_mixed_text()` splits mixed-script and multi-Latin/Han text into language-tagged segments (`_SCRIPT_TO_CANDIDATE_ROOTS`, `latin_exemplar_signal()`, `script_ranges_for_language_root()`, `token_has_character_in_ranges()`, `language_script_signal()`, `language_token_signal()`, `_is_clause_break_chunk()`, `_CLAUSE_BREAK_PUNCTUATION`).

- **Synth-Side Language Selection (`synthDrivers/__init__.py`) & Locale Utilities (`language_utils.py`)**:
  - Driver resolution: `_detect_auto_language()`, `_auto_detect_profile_for_text()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_language_token_signal()`, `_voice_for_language()` (with `redirect_language()` fallback), `_voice_matches_language()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`, `_get_language()`, `languageIsSupported()`, and `ReadOnlyTextDriverSetting` (`_auto_language_notice_message()`).
  - `language_utils.py`: `call_compat()`, `normalize_language()`, `normalize_language_code()`, `normalize_language_key()`, `language_root()`, `parse_language_codes()`, `get_nvda_locale_for_language()`, `resolve_nvda_locale()`, `nvda_locale_exists()`, `_fallback_normalize_locale()`, `_nvda_locale_exists_in_dir()`, `SPECIAL_NVDA_LOCALES`, `get_language_display_name()`, `_resolve_alive_dialog_parent()`, `show_message_box()`.

- **NVDA Speech Filter, SayAll & Spelling Overlays (`globalPlugins/__init__.py`)**:
  - Speech sequence filter: `_register_auto_language_speech_filter()`, `_filter_auto_language_speech_sequence()`, `_strip_external_lang_changes_and_coalesce()`, `_GOOGLE_TTS_LANG_CHANGE_ATTR`, `_GOOGLE_TTS_SPELLING_LANG_ATTR`, `_google_lang_change_command()`, `_append_google_lang_change()`.
  - Speech, SayAll & voice dictionary hooks: `_patch_auto_language_voice_dictionary()`, `_unpatch_auto_language_voice_dictionary()`, `_temporary_config_override()`, `_normalize_text_for_auto_profile()`, `_auto_language_for_process_text()`, `make_speak_with_auto_profile()`, `wrapped_speak_with_auto_profile()`, `speak_with_auto_profile()`, `make_speak_without_pauses_with_auto_profile()`, `wrapped_speak_without_pauses_with_auto_profile()`, `speak_without_pauses_with_auto_profile()`, `process_text_with_auto_voice_dictionary()`, `should_make_lang_change_command_with_auto_profile()`, `should_switch_voice_with_auto_profile()`, `manager_should_switch_voice_with_auto_profile()`, `_patch_say_all_handler()`, `initialize_say_all_with_auto_profile()`.
  - Spelling & character navigation hooks: `_nvda_unicode_normalization_enabled()`, `_auto_language_for_spelling_text()`, `_auto_profile_character_settings_for_language()`, `_auto_profile_character_context_for_text()`, `_single_auto_profile_character_settings()`, `_iter_spelling_or_char_desc_with_auto_profile()`, `get_spelling_speech_with_auto_profile()`, `get_single_char_description_with_auto_profile()`, `should_use_spelling_functionality_with_auto_profile()`.

---

## 10. Volatile RAM Speech Cache Code Map

- **RAM Audio Cache (`synthDrivers/__init__.py`, `speech_processing.py`)**:
  - Driver cache (`_short_cache_key()`, `_segment_cache_key()`, `_get_cached_audio()`, `_put_cached_audio()`, `_clear_short_audio_cache()`): bounded by `_SHORT_CACHE_MAX_ITEMS` (4096) and `_SHORT_CACHE_MAX_BYTES` (150 MB); invalidated on termination, voice/rate/pitch change, or runtime recycle.
  - Cache keys (`speech_processing.py`): `short_audio_cache_key()`, `segment_audio_cache_key()`, `SHORT_CACHE_MAX_CHARS` (5000), `SHORT_CACHE_MAX_HIDDEN_SEGMENTS` (24), `is_complete_speech_result()`.

---

## 11. Voice Manager, Settings & Updater UI Code Map

- **Voice Manager Dialog (`globalPlugins/voiceManager.py:VoiceManagerDialog`)**:
  - Tabs & sorting: `_build_installed_tab()`, `_build_download_tab()`, `_current_ui_language()`, `_language_sort_rules_for_current_ui()`, `_rule_based_visible_sort_key()`, `_populate_installed_list()`, `_populate_download_list()`.
  - Dependencies, download, removal & shutdown: `_with_required_download_dependencies()`, `_with_installed_dependents()`, `_missing_dependency_for_package()`, `on_download_selected()`, `on_remove_selected()`, `_remove_packages()`, `_removes_all_usable_voices()`, `_reset_configured_voice_if_removed()`, `_reset_auto_language_profile_variants_if_removed()`, `_refresh_current_google_synth_catalog()`, `on_open_folder()`, `prepare_for_shutdown()`.

- **Settings Panel (`settings.py:GoogleTtsSettingsPanel`) & UI Utilities (`uiUtils.py`)**:
  - `_SettingsGroup`, `_bounded_profile_int()`, `_create_settings_slider()`, `_refresh_settings_layout()`, `_refresh_installed_catalog_in_panel()`, `onPanelActivated()`, `cancel_pending_runtime_change()`, browser/standby controls, automatic language detection and per-profile voice/prosody/capital/spelling controls, and updater controls.
  - `uiUtils.py`: `is_secure_mode()`, `is_google_synth_current()`, `window_is_alive()`, `dialog_parent()`, `pre_popup()`, `post_popup()`, `is_windows_store_nvda()`, `show_message_box()`, `stop_call_later()`, `step_after_synth_switch()`, `is_modal_dialog_open()`, `bind_read_only_text_focus_announcement()`, `resize_read_only_text_for_content()`, `format_size_mb()`, `format_size_auto()`, `_from_dip()`, `open_synthesizer_dialog()`, `show_runtime_error_dialog()`.

- **Internal Add-on Updater (`updater.py`, `updateGui.py`)**:
  - `updater.py`: `_HttpsOnlyRedirectHandler`, `_open_https_url()`, `_response_uses_https()`, `_is_reparse_or_symlink()`, `_ensure_safe_download_dir()`, `_version_comparison_key()`, `check_for_update()`, `fetch_update_manifest()`, `_parse_update_info()`, `UpdateInfo`, `UpdateCheckResult`, `download_update()`.
  - `updateGui.py`: `_UpdateAvailableDialog`, `_UpdateDownloadDialog`, `start_manual_update_check()`, `start_automatic_update_check()`, `_UpdateCheckController`.

---

## 12. Developer Tools Reference Map

- **Voice Catalog Generator (`generate_voices_json.py`)**:
  - `dependent_voice_id_for_package()`, `_SHARED_MULTI_DEPENDENCY_RE`, `_extract_speakers_blocks()`, `extract_speakers_from_textproto()`, `_fetch_speaker_wave()`, `fetch_new_package_speakers()`, `check_engine_version_alignment()`, `latest_engine_voices_json()`, `select_engine_voices_json()`, `_atomic_write_json()`, `MAX_PACKAGE_BYTES`, `MAX_TEXTPROTO_BYTES`, `get_native_language_name()`, `format_speaker_name()`, `NATIVE_LANGUAGE_NAMES`.

- **Unicode Data Generator (`generate_unicode_data.py` -> `unicode_data.py`)**:
  - Generator helpers: `_parse_ucd_records()`, `_supported_language_scripts()`, `_likely_scripts()`, `_language_aliases()`, `_language_data_scripts()`, `_parent_locales()`, `_configured_engine_version()`, `_configured_voices_json()`, `_extract_combining_mark_ranges()`, `_extract_grapheme_break_ranges()`, `_extract_ucd_category_ranges()`, `_extract_currency_symbol_ranges()`, `_extract_emoji_pictographic_ranges()`, `_extract_cldr_active_currencies()`, `_extract_cldr_unit_symbols()`, `_extract_latin_language_exemplars()`, `_build_normalization_table()`.
  - Exported constants (`unicode_data.py`): `UNICODE_VERSION`, `CLDR_VERSION`, `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `LANGUAGE_SCRIPT_RANGES`, `NO_SPACE_SCRIPT_RANGES`, `SENTENCE_TERMINAL_CODEPOINTS`, `COMBINING_MARK_RANGES`, `GRAPHEME_EXTEND_EXTRA_RANGES`, `REGIONAL_INDICATOR_RANGES`, `LETTER_RANGES`, `NUMBER_RANGES`, `CONNECTOR_PUNCTUATION_RANGES`, `DASH_PUNCTUATION_RANGES`, `OPENING_PUNCTUATION_RANGES`, `CLOSING_PUNCTUATION_RANGES`, `OTHER_PUNCTUATION_RANGES`, `CURRENCY_SYMBOL_RANGES`, `EMOJI_PICTOGRAPHIC_RANGES`, `CLDR_ACTIVE_CURRENCIES`, `CLDR_UNIT_SYMBOLS`, `LATIN_LANGUAGE_EXEMPLARS`, `NORMALIZATION_TABLE`.

- **Localization Tooling (`build_i18n.py`)**:
  - `_translatable_source_messages()`, `_write_pot()`, `_run_msgmerge()`, `_verify_merged_po()`, `_check_catalog()`, `_check_format_interpolations()`, `_check_html_tag_interpolations()`, `_check_po_syntax()`, `_check_po_syntax_with_msgfmt()`, `_extract_doc_segments()`, `_build_doc_for_language()`, `_markdown_to_doc_html()`, `_close_optional_doc_tags()`, `_strip_outer_table_pipes()`, `_compile_mo_file()`.

- **Update Manifest Generator (`make_update_manifest.py`)**:
  - `build_update_manifest()`, `_atomic_write_text()`.

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
    - **__init__.py** — `SynthDriver` implementation, NVDA settings ring, speech loop, rate factor tables, audio cache
    - **audio_math.py** — Pure audio math, rate/pitch conversions, SeaNet rate protection, speech options builder
    - **bridge.py** — `ChromeTtsBridge`, `BrowserProcessManager`, `CdpClient`, `CdpDispatcher`, HTTP server, browser lifecycle
    - **standby.py** — `_StandbyRuntimeManager`, background browser-runtime readiness and voice pre-warming manager
    - **watcher.py** — `DirectoryChangeWatcher`, kernel-blocked Win32 directory-change watcher
    - **catalog.py** — `VoiceCatalog`, `VoicePackage`, `Speaker` models, engine library integrity validation
    - **cld2/** — Vendored Compact Language Detector 2 dynamic libraries (`cld2_x86.dll`, `cld2_x64.dll`, `cld2.dll`, `LICENSE.txt`, `README.txt`)
    - **language_detector.py** — CLD2-backed language detection, locale redirects, candidate priors, single-character and spelling-clause routing
    - **language_profiles.py** — Unicode 17.0 / NFC normalization, token classification, clause-context routing, mixed-text & emoji segmentation
    - **language_utils.py** — Language normalization, NVDA special locale mappings, localized display names
    - **speech_processing.py** — `TextSegmenter`, Unicode sentence boundary detection, `PcmSilenceShortener`, `PcmLeadBuffer`, grapheme/emoji cut protection
    - **unicode_data.py** — Pre-generated UCD 17.0 / CLDR 48.2 script/combining/currency/emoji ranges, units, Latin exemplars, sentence terminals, and normalization table
    - **voice_store.py** — Voice package download, copy, verify, remove, persistent verification cache
    - **web/** — Headless Chromium bridge environment (`index.html`, `bridgeHarness.js`)
    - **WasmTtsEngine/<ENGINE_VERSION>/** — Bundled Chromium WASM TTS engine runtime (`bindings_main.js/.wasm`, `background_compiled.js`, `offscreen.html`, `offscreen_compiled.js`, `manifest.json`, `wasm_tts_manifest_v3.json`, `voices.json`, `streaming_worklet_processor.js`, `_metadata/verified_contents.json`, licenses)
    - **websocketClientRepo/** — Vendored isolated `websocket-client` package (`websocket/`)
  - **globalPlugins/googleTtsForNvda/** — Global plugin integration and UI components
    - **__init__.py** — Tools menu integration, synth switching interception, voice dict hooks, auto-language speech/SayAll/spelling hooks, settings specs
    - **settings.py** — `GoogleTtsSettingsPanel`, NVDA settings dialog category for Google TTS
    - **uiUtils.py** — Shared UI utilities, DIP scaling, read-only text focus handling, size formatting, error dialogs
    - **updater.py** — Add-on update manifest checker, download, SHA-256 verification, updater staging
    - **updateGui.py** — Add-on update notification dialogs, download progress dialogs, auto-check controller
    - **voiceManager.py** — `VoiceManagerDialog`, wx Voice Manager with Installed and Download tabs
  - **doc/** — Localized user documentation (`en/readme.html`, `<locale>/readme.html`)
  - **locale/** — Localized interface catalogs (`<locale>/LC_MESSAGES/nvda.po`, `nvda.mo`, `manifest.ini`, `languageSort.json`)
- **.github/workflows/test.yml** — Continuous integration workflow (linting, type checking, unit tests)
- **tests/** — Automated test suite, benchmarks, and contract checkers (see [tests/README.md](file:///c:/Users/hungv/Documents/Codex/Google-TTS-For-NVDA/tests/README.md))
- **build.bat** / **build.sh** — Windows and POSIX build scripts (clean, lint, compile i18n/docs, package `.nvda-addon`)
- **build_i18n.py** — Localization CLI tool (POT/PO extraction, merge, validation, Markdown/HTML doc generation)
- **generate_voices_json.py** — Upstream voice catalog scraper and generator from ChromeOS/Google textproto
- **generate_unicode_data.py** — UCD 17.0 and CLDR 48.2 data generator for `unicode_data.py`
- **make_update_manifest.py** — Release update manifest generator creating `stable.json` for internal updater
- **AGENTS.md**, **CONTRIBUTING.md**, **TRANSLATING.md**, **UPDATER_RELEASE_GUIDE.md**, **readme.md** — Developer, localization, release, and user documentation
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
| Runtime voices.json | `<CONFIG>/googleTtsForNvda/runtime/voices.json` | Dynamic JSON catalog served via local HTTP bridge (`bridge.py:BrowserProcessManager.start_server()`). |
| NVDA configuration file | `<CONFIG>/nvda.ini` | Main NVDA config storing settings under `[googleTtsForNvda]` (`bridge.py:CONFIG_SECTION`). |
| Voice dictionaries | `<CONFIG>/speechDicts/` | Per-voice pronunciation dictionaries (`globalPlugins/__init__.py:_load_voice_dictionary_for_voice()`). |
| Browser persistent profiles | `<PROFILES_ROOT>/persistentSession/` | Isolated persistent browser user data directories (`bridge.py:BrowserProcessManager._browser_profile_root()`). |
| Browser temporary profiles | `<PROFILES_ROOT>/session-<pid>-<timestamp>/` | Ephemeral sessions spawned when persistent profile is locked (`bridge.py:BrowserProcessManager._start_browser_choice()`). |
| Profile size tracker | `<PROFILES_ROOT>/profile_size_check.json` | 24-hour periodic profile size check tracker (`bridge.py:_persistent_profile_size_check_due()`). |
| Updater download directory | `%TEMP%/googleTtsForNvda-updates/` | Staging folder for `stable.json` and `.nvda-addon` installers (`updater.py:_update_download_dir()`). |

---

### Speech Data Flow

1. NVDA calls `SynthDriver.speak()` with a sequence of text segments, pause commands, and speech parameters.
2. The driver segments text for low-latency streaming, applies pause shortening, builds synthesis options (voice, rate, pitch, volume), and enqueues requests to a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP connection are established, then evaluates `window.googleTtsForNvdaSpeak(...)` via CDP `Runtime.evaluate`.
4. `bridgeHarness.js` passes text to the bundled Google WASM TTS engine (`onSpeak`), intercepts synthesized `AudioWorkletNode` buffers, applies gain makeup and clipping protection, converts Float32 audio to 16-bit PCM, and transmits base64 PCM chunks back through the `googleTtsForNvdaBridge` CDP binding.
5. Python receives `Runtime.bindingCalled` events via `CdpDispatcher`, decodes base64 PCM bytes, and feeds audio directly to `nvwave.WavePlayer` for output.

---

## 2. Synth Driver & NVDA Integration Code Map

- **Synthesizer Switching & Voice Prompt (`globalPlugins/__init__.py`)**:
  - Intercepts `synthDriverHandler.setSynth` to verify that at least one Google TTS voice package is installed; prompts with Voice Manager if none is installed (`_normalize_set_synth_args()`, `_call_set_synth_compat()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, `_unpatch_synth_selection()`).

- **Voice Dictionaries & Read-Only Settings Hooks (`globalPlugins/__init__.py`)**:
  - Patches NVDA voice dictionary dialogs and auto-settings panels (`_is_voice_dictionary_dialog()`, `_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_patch_read_only_text_setting()`, `_unpatch_read_only_text_setting()`).
  - Hooks `speechDictHandler.loadVoiceDict` via `_VoiceDictionarySynthProxy` to load speaker-specific `.dic` files for the active Google TTS variant with in-memory caching validated by file stat signatures (`_load_voice_dictionary_for_voice()`, `_resolve_nvda_voice_speech_dict()`, `_file_stat_signature()`, `_voiceDictCache`, `_current_google_tts_speaker_id()`, `_patch_google_tts_voice_dictionary_loading()`, `_unpatch_google_tts_voice_dictionary_loading()`). For automatic language profile speech hooks, see [Section 9](#9-automatic-language-detection--profiles-code-map).

- **Synth Driver Core Lifecycle & Audio Output (`synthDrivers/__init__.py`)**:
  - Subclasses `synthDriverHandler.SynthDriver`, managing driver lifecycle, settings ring (`_PAUSE_MODE_SETTING` / `pauseMode`), prosody commands, indexed PCM playback, and audio output (`terminate()`, `speak()`, `cancel()`, `pause()`, `loadSettings()`, `isSupported()`, `languageIsSupported()`, `_iter_speech_chunks()`, `_speak_worker()`, `_speak_text()`, `_ends_with_pause_punctuation()`, `_sentence_break_milliseconds()`, `_apply_prosody_command()`, `_is_prosody_reset_command()`, `_feed_audio()`, `_feed_audio_with_indexes()`, `_feed_silence()`, `_sync_player()`, `_finish_request_audio()`, `_current_output_device()`, `_default_output_device()`, `_audio_device_error()`, `_create_wave_player()`, `_ensure_current_output_device()`).

- **Tools Menu Integration & Input Gestures (`globalPlugins/__init__.py`)**:
  - Integrates into NVDA Tools menu and registers input gestures for Voice Manager and Settings dialogs (`GlobalPlugin.terminate()`, `GlobalPlugin.on_open_voice_manager()`, `GlobalPlugin.script_openVoiceManager()`, `GlobalPlugin.script_openSettings()`, `GlobalPlugin.__gestures`).

---

## 3. Browser Runtime, Lifecycle & Resilience Code Map

- **Browser Runtime Availability & Selection (`bridge.py`)**:
  - Availability verification: `browser_runtime_available()` (also exposed on `BrowserProcessManager` and `ChromeTtsBridge`), `browser_executable_available()`, `edge_webview2_available()`, `edge_webview2_blocks_effective_runtime()`.
  - Discovery & resolution: `find_browser()`, `effective_browser_runtime()`, `_runtime_fallback_order()`.

- **Chromium Profile Isolation & Lock Recovery (`bridge.py`)**:
  - Hidden process startup: `_hidden_chrome_startup_kwargs()` sets hidden `STARTUPINFO` flags and `CREATE_NO_WINDOW`.
  - Session startup & retry: `BrowserProcessManager._start_browser_choice()` spawns Chromium with `DEVNULL` stdio, catches `_BrowserProfileInUseError` (from `_read_devtools_port()` / `_browser_profile_in_use_error()`), and retries with an isolated temporary session profile.
  - Profile lifecycle & size bounds: `_browser_profile_dir_name()`, `_get_browser_profile_dir()`, `_cleanup_old_browser_profiles()`, `_release_chrome_profile()`, `_remove_chrome_profile()`, `PERSISTENT_PROFILE_MAX_BYTES`, `PERSISTENT_PROFILE_SIZE_CHECK_INTERVAL_SECONDS`, `_persistent_profile_size_check_due()`, `_remember_persistent_profile_size_check()`.

- **Speech Loop Crash Resilience & Fatal Fallback (`synthDrivers/__init__.py`)**:
  - `_speech_loop()` wraps `self._speak_worker(*request)` in `try...except Exception:` so the daemon thread survives unhandled synthesis errors.
  - `_speak_worker()` and `_warm_current_voice_async()` verify `ChromeTtsBridge.browser_runtime_available()` before synthesis.
  - `_trigger_fatal_fallback()` debounces fatal errors, flushes `_speechQueue`, stops playback, switches NVDA to its fallback synthesizer, and invokes `show_runtime_error_dialog()` (`globalPlugins/uiUtils.py`).

- **CDP Connection, Dispatcher & Harness Readiness (`bridge.py`)**:
  - `ChromeTtsBridge.ensure_connection()` serializes startup via `_connectionLock` with candidate fallback and cancellation checks.
  - `CdpClient.request()` manages WebSocket CDP calls and raises `CdpCancelled` when cancelled; `CdpDispatcher` routes `Runtime.bindingCalled` events and fails active requests fast on runtime errors.
  - `WasmTtsEngineBridge` tracks busy state and cancellation (`runtime_busy`, `_set_runtime_busy()`, `cancel_current()`, `send_fast_stop()`, `enable_cdp_domains()`, `wait_until_ready()`).

- **Runtime Health, Memory Throttling & Recycling (`bridge.py`, `synthDrivers/__init__.py`)**:
  - `_elevate_chrome_priority()` sets `ABOVE_NORMAL_PRIORITY_CLASS` and disables Windows EcoQoS via `SetProcessInformation`.
  - `_process_tree_memory_usage()` and `BrowserProcessManager.browser_memory_usage()` track process-tree memory; `ChromeTtsBridge.maybe_recycle_runtime()` and `SynthDriver._maybe_recycle_bridge_after_request()` recycle runtimes on memory thresholds or unrecoverable errors.

---

## 4. CDP Communication, WebSocket & Dependency Isolation Code Map

- **Bundled WebSocket Client (`websocketClientRepo/websocket/`)**:
  - Loaded via package-relative imports in `bridge.py` (`_abnf.py:native_byteorder,_mask`, `_http.py:HAVE_PYTHON_SOCKS,_start_proxied_socket`, `_utils.py:_create_bundled_utf8_validator,validate_utf8`).

- **Cross-Origin Isolation HTTP Headers (`bridge.py:_BridgeRequestHandler`)**:
  - Serves `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`, and `Cross-Origin-Resource-Policy: same-origin` for `SharedArrayBuffer` in multi-threaded WASM.

---

## 5. Audio Math, Loudness & SeaNet Processing Code Map

- **Pure Audio Math & Speech Options (`audio_math.py`)**:
  - Rate and pitch mapping: `rate_to_chrome()` (`MIN_ARTIFICIAL_RATE` 0.5x to `MAX_ARTIFICIAL_RATE` 10.0x) and `pitch_to_chrome()` (0.5x to 2.0x).
  - SeaNet rate protection & options builder: `uses_protected_engine_rate()` caps WASM engine rate at `PROTECTED_ENGINE_RATE` (3.0x) and delegates higher rates to post-processing tempo stretching; `build_speech_options()` builds the normalized options payload with `OUTPUT_GAIN_MAKEUP` (1.70x).

- **Browser Harness Audio Processing Pipeline (`bridgeHarness.js`)**:
  - Engine lifecycle: `isTtsEngineInstance()`, `getTtsEngine()`, `ensureEngineInitialized()`, `ensureLanguageReady()`, `stopActiveSynthesis()`, `googleTtsForNvdaPreload()`, `googleTtsForNvdaSpeak()`.
  - Packetization, gain & WSOLA stretching: `buffersToPcmBase64()`, `appendSamples()`, `audioPacketSampleTarget()`, `queueAudioPacket()`, `flushAudioQueue()`, `outputGainFromPayload()`, `limitSample()`, `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `processPitchSamples()`, `processTempoSamples()`, `queueTempoInput()`, `flushAudioProcessors()`, `flushTempoProcessor()`, `queueAudio()`, `finishSegmentAudio()`.

---

## 6. Speech Processing, Latency Segmentation & Pause Shortening Code Map

- **Latency Text Segmentation (`speech_processing.py:TextSegmenter`)**:
  - Sanitization & segmentation: `sanitize_speech_text()` (`_SPEECH_SANITIZE_TABLE` maps non-standard Unicode whitespace from `NORMALIZATION_TABLE` and PUA codepoints to ASCII spaces with 1:1 index alignment), `split_text_for_latency()`, `iter_text_segments_for_latency()`, `iter_indexed_text_segments()`, `spoken_bridge_segments()`, `needs_index_boundary_space()`.
  - Grapheme & emoji cut protection: `_extend_cut_over_combining_marks()` extends cuts over combining marks (`M*`, `_is_combining_mark()`), `ZWNJ`/`ZWJ` (`0x200C`, `0x200D`), variation selectors (`0xFE0E`, `0xFE0F`, `0xE0100`-`0xE01EF`), keycaps (`0x20E3`), skin tones (`0x1F3FB`-`0x1F3FF`), tags (`0xE0020`-`0xE007F`), and flag regional indicators (`0x1F1E6`-`0x1F1FF`).
  - Boundary detection & guards: sentence splits (`find_sentence_splits()`, `is_sentence_terminator_character()`, `TAILORED_SENTENCE_TERMINATORS`, `_is_sentence_trailing_closer()`, `_sentence_terminator_stays_with_token()`, `_period_stays_with_previous_token()`, `_period_is_numeric_separator()`), soft phrase cuts (`SOFT_BREAK_CHARS`, `_is_soft_break_character()`, `_is_colon_like_character()`, `_is_dash_like_character()`, `_find_soft_phrase_cut()`, `_is_contextual_soft_phrase_cut()`, `_is_forced_soft_break()`), space-free scripts (`NO_SPACE_SCRIPT_PROFILES`, `_find_no_space_range()`, `_is_no_space_script_character()`, `_find_no_space_script_cut()`), forced cuts (`_iter_forced_latency_segments()`, `_find_forced_latency_cut()`), and structural URL/wrapper guards (`looks_like_url_token()`, `_strip_surrounding_wrappers()`, `should_pause_after_segment()`, `ends_with_pause_punctuation()`).

- **Pause Shortening & Lead Buffering (`speech_processing.py`, `synthDrivers/__init__.py`)**:
  - `PcmSilenceShortener` (`PAUSE_MODE_DO_NOT_SHORTEN`, `PAUSE_MODE_SHORTEN_END_ONLY`, `PAUSE_MODE_SHORTEN_ALL`, `SHORTENED_SILENCE_KEEP_MS`, `SHORTENED_ALL_PAUSES_KEEP_MS`, `feed()`, `flush_boundary()`, `finish()`), `create_pcm_silence_shortener()` (with rate-scaled `keepSilenceMs` via `effective_chrome_rate()` and `keep_silence_ms_for_rate()`), `pcm_bytes_for_milliseconds()`, `align_pcm_bytes()`, `pcm_has_audible_sample()`.
  - `PcmLeadBuffer` (`LIVE_MULTI_SEGMENT_LEAD_MS`) buffers initial multi-segment audio; `synthDrivers/__init__.py` scales `BreakCommand`, sentence breaks, and end-of-utterance pauses via `_NORMAL_SENTENCE_BREAK_MS`, `_SHORTENED_SENTENCE_BREAK_MS`, `_END_OF_UTTERANCE_PAUSE_MS`, `_BREAK_RATE_TABLE`, `_interpolate_rate_factor()`, `_break_rate_factor()`, `_end_of_utterance_rate_factor()`, `_ends_with_pause_punctuation()`, `_sentence_break_milliseconds()`, and flushes grouped segments at `_FLUSH_GROUP_CHARS_THRESHOLD`.

---

## 7. Voice Catalog & Voice Store Code Map

- **Voice Catalog & Models (`catalog.py`)**:
  - Engine integrity: `BASE_DIR`, `ENGINE_VERSION`, `ENGINE_ROOT`, `ENGINE_DIR`, `CATALOG_PATH`, `REQUIRED_ENGINE_FILES`, `UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS`, `inspect_engine_library()`, `EngineLibraryError`, `engine_library_error_message()`.
  - Models (`VoicePackage`, `Speaker`, `VoiceCatalog`): `load()`, `from_json()`, `package_for_voice()`, `speaker_for_voice()`, `voices_by_language()`, `language_for_voice()`, `to_runtime_json()`.

- **Package Management & Verification Cache (`voice_store.py`)**:
  - Operations: `package_file()`, `download_package()` (`DOWNLOAD_CHUNK_SIZE`, `VOICE_PACKAGE_MAX_BYTES`, SHA-256 verification), `remove_package()`, `copy_existing_package()`.
  - Verification cache (`_VERIFICATION_CACHE_FILE`, `_VERIFICATION_CACHE_VERSION`, `_verificationCacheLock`): `_load_persistent_verification_cache()`, `_save_persistent_verification_cache()`, `_remember_verified_package()`, `_forget_verified_package()`, `is_package_installed()`, `physically_installed_packages()`, `usable_installed_packages()`, `installed_packages()`.

---

## 8. Voice Preload, Standby Readiness & Directory Watcher Code Map

- **Voice Preloading & Harness Session Fencing**:
  - `SynthDriver._warm_current_voice_async()` and `standby.py` resolve warmup voice IDs (`_warmup_voice_ids()`, `_warmup_voice_ids_for_voice()`, `_auto_language_candidates_in_warmup_order()`, `_voice_id_for_package()`) and call `ChromeTtsBridge.preload_voice()` / `WasmTtsEngineBridge.preload_voice()` (`window.googleTtsForNvdaPreload(...)`).
  - `bridgeHarness.js` fences `emit()`, `queueAudioPacket()`, `queueProcessedAudio()`, `finishSegmentAudio()`, and `stopActiveSynthesis()` with monotonic session tokens (`currentSessionToken`, `beginSession()`, `isCurrentSession()`).

- **Standby Browser Runtime Manager (`standby.py`) & Directory Watcher (`watcher.py`)**:
  - `keep_browser_runtime_ready_enabled()` and `_StandbyRuntimeManager` (`refresh_async()`, `_run_refresh()`, `claim_bridge()`, `release_synth_bridge()`, `terminate()`).
  - `_catalog_signature()` combines `configured_browser_runtime()`, `_wasm_engine_signature()` (`ENGINE_VERSION`, `ENGINE_DIR` mtime, `CATALOG_PATH` mtime), installed package metadata, and warmup voice IDs.
  - `DirectoryChangeWatcher` (`start()`, `signal_stop()`, `stop(timeout=5.0)`) monitors watched directories via Win32 `FindFirstChangeNotificationW` / `WaitForMultipleObjects`.

---

## 9. Automatic Language Detection & Profiles Code Map

- **CLD2 Language Detector (`language_detector.py`)**:
  - `_Cld2Detector._load_library()` loads `cld2_x64.dll` or `cld2_x86.dll` from `cld2/`.
  - `detect_language(text, candidateLanguages, preferredLanguage=None)` returns `DetectionResult` (`_MIN_RELIABLE_PERCENT`), routing single graphemes (`is_single_grapheme_token()`), single-letter Latin spelling clauses (`is_spelling_only_text()`), and strings without language words (`not has_language_words()`) to `preferredLanguage` (unless a single character matches a non-preferred candidate script or CLDR Latin exemplar), recovering short words via candidate priors, and falling back to Unicode script and CLDR exemplar signals (`latin_exemplar_signal()`).
  - Aliases, dialect redirects & matching: `_LANGUAGE_ALIASES`, `_CHINESE_LANGUAGE_ROOTS`, `_LANGUAGE_REDIRECTS`, `redirect_language()`, `language_matches()`, `language_match_keys()`, `_language_family()`, `_language_root()`, `_candidate_for_language()`.

- **Unified Language Profiles & Mixed-Text Segmentation (`language_profiles.py`)**:
  - Normalization: `normalize_mathematical_alphanumeric(text)` applies `NORMALIZATION_TABLE` and composes NFD sequences to NFC while preserving singleton Greek punctuation (`U+037E`, `U+0387` via `_NFC_PRESERVED_PUNCT_RE`).
  - Token classification: `is_combining_mark()`, `is_single_grapheme_token()`, `is_number_token()`, `is_url_token()`, `is_symbol_or_emoji_token()`, `has_language_words()`, `is_spelling_only_text()`, and `classify_token()` classify numbers (`_NUMBER_CHUNK_PATTERN`, `_PERCENT_PATTERN`, localized decimal/thousands separators), structural RFC 3986 URLs/emails/dotted domains (`_URL_EMAIL_DOMAIN_PATTERN`), UCD/ISO 4217 currencies, CLDR units, time formats, and symbols.
  - Mixed-text segmentation: `segment_mixed_text(text, candidateLanguages, preferredLanguage, defaultLanguage=None)` splits mixed-script and multi-Latin/Han text into language-tagged segments (`_SCRIPT_TO_CANDIDATE_ROOTS`, `latin_exemplar_signal()`, `_is_clause_break_chunk()`, `_CLAUSE_BREAK_PUNCTUATION`).
  - Script & exemplar helpers: `script_ranges_for_language_root()`, `token_has_character_in_ranges()`, `language_script_signal()`, `latin_exemplar_signal()`, `has_vietnamese_diacritics()`, `language_token_signal()`.

- **Synth-Side Language Selection (`synthDrivers/__init__.py`) & Locale Utilities (`language_utils.py`)**:
  - Driver resolution: `_detect_auto_language()`, `_auto_detect_profile_for_text()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_language_token_signal()`, `_voice_for_language()` (with `redirect_language()` fallback), `_voice_matches_language()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`, `_get_language()`, `languageIsSupported()`, and `ReadOnlyTextDriverSetting` (`_auto_language_notice_message()`).
  - `language_utils.py`: `normalize_language()`, `normalize_language_code()`, `normalize_language_key()`, `get_nvda_locale_for_language()`, `resolve_nvda_locale()`, `nvda_locale_exists()`, `SPECIAL_NVDA_LOCALES`, `get_language_display_name()`.

- **NVDA Speech Filter, SayAll & Spelling Overlays (`globalPlugins/__init__.py`)**:
  - Speech sequence filter: `_register_auto_language_speech_filter()`, `_filter_auto_language_speech_sequence()`, `_strip_external_lang_changes_and_coalesce()`, `_GOOGLE_TTS_SPELLING_LANG_ATTR`, `_google_lang_change_command()`.
  - Speech, SayAll & voice dictionary hooks: `_patch_auto_language_voice_dictionary()`, `_unpatch_auto_language_voice_dictionary()`, `make_speak_with_auto_profile()`, `speak_with_auto_profile()`, `speak_without_pauses_with_auto_profile()`, `_autoProfileSpeakDepth`, `_originalSayAllSpeak`, `_originalSayAllSpeakWithoutPauses`, `process_text_with_auto_voice_dictionary()`, `should_make_lang_change_command_with_auto_profile()`, `should_switch_voice_with_auto_profile()`.
  - Spelling & character navigation hooks: `_nvda_unicode_normalization_enabled()`, `_auto_language_for_spelling_text()`, `_segment_spelling_text_by_language()`, `_spelling_text_is_composite_symbol_cluster()`, `_wrap_spelling_speech_items_with_language()`, `get_spelling_speech_with_auto_profile()`, `should_use_spelling_functionality_with_auto_profile()`.

---

## 10. Volatile RAM Speech Cache Code Map

- **RAM Audio Cache (`synthDrivers/__init__.py`, `speech_processing.py`)**:
  - Driver cache (`_get_cached_audio()`, `_put_cached_audio()`, `_get_cached_segment_audio()`, `_put_cached_segment_audio()`, `_clear_short_audio_cache()`): bounded by `_SHORT_CACHE_MAX_ITEMS` (4096) and `_SHORT_CACHE_MAX_BYTES` (150 MB); invalidated on termination, voice/rate/pitch change, or runtime recycle.
  - Cache keys (`speech_processing.py`): `short_audio_cache_key()`, `segment_audio_cache_key()`, `SHORT_CACHE_MAX_CHARS` (5000), `SHORT_CACHE_MAX_HIDDEN_SEGMENTS` (24), `is_complete_speech_result()`.

---

## 11. Voice Manager, Settings & Updater UI Code Map

- **Voice Manager Dialog (`globalPlugins/voiceManager.py:VoiceManagerDialog`)**:
  - Tabs & sorting: `_build_installed_tab()`, `_build_download_tab()`, `_current_ui_language()`, `_language_sort_rules_for_current_ui()`, `_rule_based_visible_sort_key()`, `_populate_installed_list()`, `_populate_download_list()`.
  - Dependencies, download & removal: `_with_required_download_dependencies()`, `_with_installed_dependents()`, `_missing_dependency_for_package()`, `on_download_selected()`, `on_remove_selected()`, `_remove_packages()`, `_removes_all_usable_voices()`, `_reset_configured_voice_if_removed()`, `_reset_auto_language_profile_variants_if_removed()`, `on_open_folder()`.

- **Settings Panel (`settings.py:GoogleTtsSettingsPanel`) & UI Utilities (`uiUtils.py`)**:
  - `_SettingsGroup`, `_refresh_settings_layout()`, browser runtime selector, standby runtime toggle, automatic language detection and per-profile voice/prosody/capital/spelling controls, and updater controls.
  - `uiUtils.py`: `bind_read_only_text_focus_announcement()`, `resize_read_only_text_for_content()`, `format_size_mb()`, `format_size_auto()`, `_from_dip()`, `open_synthesizer_dialog()`, `show_runtime_error_dialog()`.

- **Internal Add-on Updater (`updater.py`, `updateGui.py`, `make_update_manifest.py`)**:
  - `updater.py`: `check_for_update()`, `fetch_update_manifest()`, `_parse_update_info()`, `UpdateInfo`, `UpdateCheckResult`, `download_update()`.
  - `updateGui.py`: `_UpdateAvailableDialog`, `_UpdateDownloadDialog`, `start_manual_update_check()`, `start_automatic_update_check()`, `_UpdateCheckController`.
  - `make_update_manifest.py`: `build_update_manifest()`.

---

## 12. Developer Tools & Build Scripts Code Map

- **Voice Catalog Generator (`generate_voices_json.py`)**:
  - `extract_speakers_from_textproto()`, `fetch_new_package_speakers()`, `check_engine_version_alignment()`, `latest_engine_voices_json()`, `get_native_language_name()`, `format_speaker_name()`, `NATIVE_LANGUAGE_NAMES`.

- **Unicode Data Generator (`generate_unicode_data.py` -> `unicode_data.py`)**:
  - Generator functions: `_parse_ucd_records()`, `_supported_language_scripts()`, `_likely_scripts()`, `_extract_combining_mark_ranges()`, `_extract_currency_symbol_ranges()`, `_extract_emoji_pictographic_ranges()`, `_extract_cldr_active_currencies()`, `_extract_cldr_unit_symbols()`, `_extract_cldr_latin_exemplars()`, `_build_normalization_table()` (`_SUPPLEMENTAL_NORMALIZATION_ENTRIES`, `_EXCLUDED_NFC_SINGLETONS`), `check_engine_version_alignment()`, `_configured_voices_json()`.
  - Exported constants (`unicode_data.py`): `UNICODE_VERSION`, `CLDR_VERSION`, `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `LANGUAGE_SCRIPT_RANGES`, `SENTENCE_TERMINAL_CODEPOINTS`, `COMBINING_MARK_RANGES`, `CURRENCY_SYMBOL_RANGES`, `EMOJI_PICTOGRAPHIC_RANGES`, `CLDR_ACTIVE_CURRENCIES`, `CLDR_UNIT_SYMBOLS`, `LATIN_LANGUAGE_EXEMPLARS`, `NORMALIZATION_TABLE`.

- **Localization CLI (`build_i18n.py`)**:
  - `_translatable_source_messages()`, `_write_pot()`, `_run_msgmerge()`, `_verify_merged_po()`, `_check_catalog()`, `_check_format_interpolations()`, `_check_html_tag_interpolations()`, `_check_po_syntax_with_msgfmt()`, `_extract_doc_segments()`, `_build_doc_for_language()`, `_markdown_to_doc_html()`, `_compile_mo_file()`.

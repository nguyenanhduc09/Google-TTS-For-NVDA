# Google TTS For NVDA — Code Map & Technical Architecture

Technical code map indexing the codebase architecture, modules, classes, functions, and key constants across all subsystems.

---

## 1. Project Architecture

### Directory Layout

- **googleTtsForNvda/**
  - **manifest.ini** — Add-on metadata and configuration
  - **buildInfo.json** — Internal updater hotfix build metadata
  - **LICENSE** — Packaged copy of the add-on's GPL-2.0 license
  - **synthDrivers/googleTtsForNvda/**
    - **__init__.py** — SynthDriver; NVDA integration and settings ring
    - **audio_math.py** — Pure audio math, rate/pitch conversions, SeaNet rate protection
    - **bridge.py** — ChromeTtsBridge; HTTP server, browser lifecycle, CDP/WS
    - **standby.py** — Background browser-runtime readiness manager
    - **watcher.py** — Reusable Win32 directory-change watcher with heartbeat logging
    - **catalog.py** — VoiceCatalog, VoicePackage, Speaker models
    - **cld2/** — Vendored CLD2 x86/x64 DLLs and documentation
    - **language_detector.py** — CLD2-backed language detection with x86/x64 DLL selection
    - **language_profiles.py** — Pure Unicode-script fallback language detection
    - **language_utils.py** — Language normalization, NVDA special locale mappings, display names
    - **speech_processing.py** — Text segmentation, Unicode 17.0 sentence boundaries, pause shortening
    - **unicode_data.py** — Pre-generated Unicode 17.0 / CLDR 48.2 script ranges and terminals
    - **voice_store.py** — Download, copy, verify, remove voice packages
    - **web/**
      - **index.html** — Loaded in the headless Chromium browser runtime
      - **bridgeHarness.js** — Shims chrome.* APIs, calls WASM engine, captures AudioWorklet PCM, sends base64 chunks through CDP
    - **WasmTtsEngine/<ENGINE_VERSION>/**
      - **bindings_main.js / .wasm** — Chromium WASM TTS engine binary and bindings
      - **offscreen_compiled.js** — Background worker script
      - **voices.json** — Voice catalog and speaker metadata
      - **LICENSE** — Chromium WASM TTS BSD-3-Clause license
      - **EIGEN_LICENSE** — Eigen dependency Apache-2.0 license
      - **streaming_worklet_processor.js** — AudioWorklet PCM processor
    - **websocketClientRepo/** — Vendored websocket-client library
  - **globalPlugins/googleTtsForNvda/**
    - **__init__.py** — Tools menu integration and global plugin hooks
    - **settings.py** — Google TTS settings panel
    - **uiUtils.py** — Shared UI utilities, read-only text focus handling, size formatting
    - **updater.py** — Add-on update manifest/download/verification core
    - **updateGui.py** — Add-on update check/download/install UI flow
    - **voiceManager.py** — wx Voice Manager dialog
  - **doc/**
    - **en/readme.html** — English user documentation
    - **<locale>/readme.html** — Localized user documentation
  - **locale/**
    - **<locale>/** — Localized interface catalogs
- **build.bat** — Windows build and packaging script
- **build.sh** — Linux/WSL build and packaging script
- **build_i18n.py** — Localization and documentation translation tool
- **generate_voices_json.py** — Upstream voice catalog generator
- **generate_unicode_data.py** — Unicode and CLDR script tables generator
- **readme.md** — Repository documentation

### Runtime Paths

| Purpose | Location |
|---|---|
| NVDA config root | `globalVars.appArgs.configPath` |
| Add-on data root | `{configPath}/googleTtsForNvda/` |
| Downloaded voices | `{configPath}/googleTtsForNvda/voices/` |
| Runtime voices.json | `{configPath}/googleTtsForNvda/runtime/voices.json` |
| Browser profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/persistentSession` |
| Temporary browser profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/session-<pid>-<timestamp>` |
| Master catalog | `WasmTtsEngine/<ENGINE_VERSION>/voices.json` |

### Speech Data Flow

1. NVDA calls `SynthDriver.speak()` with a speech sequence.
2. The driver segments text, builds options for voice/rate/pitch/volume, and queues synthesis on a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP are connected, then evaluates `window.googleTtsForNvdaSpeak(...)` via `Runtime.evaluate`.
4. `bridgeHarness.js` calls the Google WASM TTS engine through the dynamically resolved engine object's `onSpeak`, intercepts `AudioWorkletNode` buffers, converts float32 audio to int16 PCM, and sends base64 audio chunks through the `googleTtsForNvdaBridge` CDP binding.
5. Python receives `Runtime.bindingCalled`, decodes PCM, and feeds it to `nvwave.WavePlayer`.

---

## 2. Synth Driver & NVDA Integration Code Map

- **Synth Switching & Selection Interception**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_normalize_set_synth_args()`, `_call_set_synth_compat()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, and `_unpatch_synth_selection()`.

- **Voice Dictionaries & Speech Hooks**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_patch_read_only_text_setting()`, and `_unpatch_read_only_text_setting()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_VoiceDictionarySynthProxy`, `_load_voice_dictionary_for_voice()`, `_current_google_tts_speaker_id()`, `_patch_google_tts_voice_dictionary_loading()`, and `_unpatch_google_tts_voice_dictionary_loading()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_filter_auto_language_speech_sequence()`, `_patch_auto_language_voice_dictionary()`, `process_text_with_auto_voice_dictionary()`, `get_spelling_speech_with_auto_profile()`, and `should_use_spelling_functionality_with_auto_profile()`.

- **Synth Driver Entry Points & Speech Loop**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver.terminate()`, `SynthDriver.speak()`, `SynthDriver._speech_loop()`, `SynthDriver._speak_worker()`, `SynthDriver.cancel()`, `SynthDriver.pause()`, `SynthDriver.loadSettings()`, and `SynthDriver.isSupported()`.

- **Audio Output & Device Error Recovery**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver._current_output_device()`, `SynthDriver._default_output_device()`, `SynthDriver._audio_device_error()`, `SynthDriver._create_wave_player()`, and `SynthDriver._ensure_current_output_device()`.

- **Fatal Fallback & Runtime Dialogs**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver._trigger_fatal_fallback()`, `SynthDriver._show_engine_library_error()`, and `SynthDriver._show_missing_chrome_error()`.
  - `globalPlugins/googleTtsForNvda/uiUtils.py`: `show_runtime_error_dialog()`.

- **GUI Settings Panel & Global Plugin Integration**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py`: `GoogleTtsSettingsPanel.makeSettings()` and `GoogleTtsSettingsPanel.onSave()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `GlobalPlugin.terminate()`, `GlobalPlugin.on_open_voice_manager()`, `GlobalPlugin.script_openVoiceManager()`, and `GlobalPlugin.script_openSettings()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` input gesture map: `GlobalPlugin.__gestures`, `GlobalPlugin.script_openVoiceManager()`, and `GlobalPlugin.script_openSettings()`.

- **NVDA Static API Contract Checks & Test Suite**:
  - `tests/check_nvda_api_contracts.py`: static contract runner checking NVDA API compatibility across releases.
  - `tests/NVDA_CHROMIUM_MANUAL_CHECKLIST.md`: manual test checklist for interactive NVDA runtime validation.
  - Standalone unit tests in `tests/` covering fallback behavior, speech loop resilience, logger compatibility, and voice setting states.

---

## 3. Browser Runtime, Lifecycle & Resilience Code Map

- **Browser Runtime Availability & Delegation**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:browser_runtime_available(runtime: str | None = None) -> bool` — Validates browser executable availability and Edge WebView2 requirement.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager.browser_runtime_available(cls, runtime: str | None = None) -> bool` — Classmethod delegating to module-level `browser_runtime_available()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.browser_runtime_available(cls, runtime: str | None = None) -> bool` — Classmethod delegating to `BrowserProcessManager.browser_runtime_available()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:browser_executable_available(runtime: str) -> bool` — Checks browser executable existence on disk.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:edge_webview2_available() -> bool` — Verifies Microsoft Edge WebView2 runtime availability via Windows registry.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:find_browser() -> str | None` — Discovers the first available supported Chromium executable.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:effective_browser_runtime() -> str | None` — Resolves the effective runtime from configuration and availability.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_runtime_fallback_order() -> list[str]` — Generates candidate fallback sequence starting with the configured runtime.

- **Chromium Profile Management & Profile-In-Use Recovery**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_hidden_chrome_startup_kwargs() -> dict[str, Any]` — Prepares hidden window `STARTUPINFO` flags (`STARTF_USESHOWWINDOW`, `wShowWindow = 0`) and `CREATE_NO_WINDOW` creation flags on Windows.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_browser_profile_in_use_error(exitCode: int = 21) -> _BrowserProfileInUseError` — Constructs profile-in-use exception recording process exit code.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._read_devtools_port()` — Reads `DevToolsActivePort`; on persistent profile early exit, raises `_browser_profile_in_use_error(exitCode)`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._start_browser_choice()` — Spawns Chromium with `DEVNULL` standard handles, catches `_BrowserProfileInUseError`, and retries with an isolated temporary session profile.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._browser_profile_root()`, `_browser_profile_dir_name()`, `_get_browser_profile_dir()` — Manages isolated profile directory trees (`chromeProfiles`, `edgeProfiles`, `braveProfiles`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._cleanup_old_browser_profiles()`, `_release_chrome_profile()`, `_remove_chrome_profile()` — Preserves persistent profiles while removing temporary profiles upon shutdown.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:PERSISTENT_PROFILE_MAX_BYTES`, `PERSISTENT_PROFILE_SIZE_CHECK_INTERVAL_SECONDS`, `_persistent_profile_size_check_due()` — Throttles recursive profile size checks.

- **Speech Loop Crash Resilience & Fatal Fallback**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._speech_loop()` — Daemon speech loop containing outer `try...except Exception:` around `self._speak_worker(*request)` to prevent thread termination.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._speak_worker()` — Synthesis worker with defensive `ChromeTtsBridge.browser_runtime_available()` call.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._warm_current_voice_async()` — Background pre-warm worker with defensive runtime availability check.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._trigger_fatal_fallback(friendlyMessage)` — Debounced fatal fallback handler: clears `_speechQueue`, cancels active wave playback, switches to NVDA fallback synth, and shows error dialog.
  - `globalPlugins/googleTtsForNvda/uiUtils.py:show_runtime_error_dialog(message, delayMs=150)` — Centralized error dialog presentation using `gui.mainFrame`, `wx.ICON_ERROR`, and delayed dispatch.

- **CDP Connection, Dispatcher & Harness Readiness**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.ensure_connection(cancelEvent)` — Serialized connection setup via `_connectionLock` with candidate fallback loop and cancellation checks.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:CdpClient.request(method, params, cancelEvent)` — Thread-safe CDP request/response over WebSocket; raises `CdpCancelled` when cancelled.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:CdpDispatcher` — Routes CDP binding/events; propagates runtime errors back to owning request to fail fast without caching bad audio.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:WasmTtsEngineBridge.runtime_busy`, `_set_runtime_busy()`, `cancel_current()`, `send_fast_stop()` — Thread-safe runtime busy tracking and cooperative cancellation signaling.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:WasmTtsEngineBridge.enable_cdp_domains(cancelEvent)`, `wait_until_ready(cancelEvent)` — Enables CDP domains and polls harness readiness with transient error retries.

- **Runtime Health, Memory Throttling & Recycling**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_process_tree_memory_usage(rootPid)` — Collects process-tree private bytes and working set via Win32 Toolhelp32 snapshot.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_elevate_chrome_priority(processId)` — Elevates process tree priority to `ABOVE_NORMAL_PRIORITY_CLASS` and explicitly disables Windows EcoQoS (Power Throttling) via `SetProcessInformation`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager.browser_memory_usage()` — Queries memory usage for running browser process tree.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.maybe_recycle_runtime()` — Recycles browser runtime when memory thresholds are confirmed or speech errors occur.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._maybe_recycle_bridge_after_request()` — Post-request idle recycle scheduler.

---

## 4. CDP Communication, WebSocket & Dependency Isolation Code Map

- **Bundled Dependency Isolation**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` and `websocketClientRepo/__init__.py`: package-relative `websocket` import and private vendored package root.
  - `websocketClientRepo/websocket/_abnf.py`: `native_byteorder` and `_mask()`.
  - `websocketClientRepo/websocket/_http.py`: `HAVE_PYTHON_SOCKS`, `ProxyError`, `ProxyTimeoutError`, `ProxyConnectionError`, `ProxyType`, and `_start_proxied_socket()`.
  - `websocketClientRepo/websocket/_utils.py`: `_create_bundled_utf8_validator()` and `validate_utf8()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_profiles.py`: package-relative `LANGUAGE_SCRIPT_RANGES` import from `unicode_data.py`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: package-relative `SENTENCE_TERMINAL_CODEPOINTS` import from `unicode_data.py`.

- **Cross-Origin Isolation HTTP Headers**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_BridgeRequestHandler`: sends `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`, `Cross-Origin-Resource-Policy: same-origin` required for `SharedArrayBuffer` support.

---

## 5. Audio Math, Loudness & SeaNet Processing Code Map

- **Pure Audio Math & Options**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/audio_math.py`: `OUTPUT_GAIN_MAKEUP`, `PROTECTED_ENGINE_RATE`, `MIN_ARTIFICIAL_RATE`, `MAX_ARTIFICIAL_RATE`, `rate_to_chrome()`, `pitch_to_chrome()`, `uses_protected_engine_rate()`, and `build_speech_options()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_speech_options()`, `_uses_protected_engine_rate()`, `_rate_to_chrome()`, `_pitch_to_chrome()`, and `_short_cache_key()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `WasmTtsEngineBridge.speak()`.

- **Browser Harness Audio Processing**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` engine startup, readiness & cleanup: `isTtsEngineInstance()`, `getTtsEngine()`, `ensureEngineInitialized()`, `ensureLanguageReady()`, `stopActiveSynthesis()`, `googleTtsForNvdaPreload()`, and `googleTtsForNvdaSpeak()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` PCM packetization: `buffersToPcmBase64()`, `appendSamples()`, `audioPacketSampleTarget()`, `queueAudioPacket()`, and `flushAudioQueue()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` fixed gain & clipping limiter: `outputGainFromPayload()`, `limitSample()`, and `buffersToPcmBase64()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` SeaNet tempo & pitch processing: `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `processPitchSamples()`, `processTempoSamples()`, `queueTempoInput()`, `flushAudioProcessors()`, `flushTempoProcessor()`, `queueAudio()`, and `finishSegmentAudio()`.

---

## 6. Speech Processing, Latency Segmentation & Pause Shortening Code Map

- **Pause Shortening & Streaming PCM**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `PAUSE_MODE_DO_NOT_SHORTEN`, `PAUSE_MODE_SHORTEN_END_ONLY`, `PAUSE_MODE_SHORTEN_ALL`, `SHORTENED_ALL_PAUSES_KEEP_MS`, `SHORTENED_SILENCE_KEEP_MS`, `PcmSilenceShortener`, `PcmSilenceShortener._blockSizeSamples`, `PcmSilenceShortener._blockSizeBytes`, `PcmSilenceShortener._pendingBlock`, `PcmSilenceShortener._process_block()`, `PcmSilenceShortener._flush()`, `PcmSilenceShortener._hold_silence()`, `PcmSilenceShortener._release_held_silence()`, `PcmSilenceShortener.feed()`, `PcmSilenceShortener.flush_boundary()`, `PcmSilenceShortener.finish()`, `PcmLeadBuffer`, `PcmLeadBuffer.feed()`, `PcmLeadBuffer.finish()`, `create_pcm_silence_shortener()`, `pcm_bytes_for_milliseconds()`, `align_pcm_bytes()`, and `pcm_has_audible_sample()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` timing constants & rate tables: `_NORMAL_SENTENCE_BREAK_MS`, `_SHORTENED_SENTENCE_BREAK_MS`, `_BREAK_RATE_FACTOR_MIN`, `_BREAK_RATE_FACTOR_MAX`, `_END_OF_UTTERANCE_PAUSE_MS`, `_END_OF_UTTERANCE_RATE_FACTOR_MIN`, `_END_OF_UTTERANCE_RATE_FACTOR_MAX`, `_BREAK_RATE_TABLE`, `_interpolate_rate_factor()`, `_break_rate_factor()`, `_end_of_utterance_rate_factor()`, `_PAUSE_MODE_SETTING`, and `_pauseModes`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` speech flow integration: `speak()`, `_iter_speech_chunks()`, `_sentence_break_milliseconds()`, `_speak_worker()`, `_speak_text()`, `_speak_text().on_segment_end()`, `_short_cache_key()`, `_finish_request_audio()`, and `_feed_silence()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` hidden-segment boundary signaling: `SegmentEndCallback`, `WasmTtsEngineBridge.speak()`, and `ChromeTtsBridge.speak()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` browser hidden-segment boundary events: `googleTtsForNvdaSpeak()`, `finishSegmentAudio()`, and the `segmentEnd` bridge event.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` settings accessors: `_get_availablePausemodes()`, `_get_pauseMode()`, and `_set_pauseMode()`.

- **Text Segmentation & Latency Chunking**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `TextSegmenter`, `DEFAULT_TEXT_SEGMENTER`, `TextSegmenter.split_text_for_latency()`, `TextSegmenter.sanitize_speech_text()`, `TextSegmenter.find_sentence_splits()`, `TextSegmenter.iter_text_segments_for_latency()`, `TextSegmenter.iter_indexed_text_segments()`, `TextSegmenter.spoken_bridge_segments()`, `TextSegmenter.looks_like_url_token()`, `TextSegmenter.should_pause_after_segment()`, `TextSegmenter._needs_spoken_segment_space()`, `TextSegmenter._sentence_terminator_stays_with_token()`, `TextSegmenter._period_stays_with_previous_token()`, `TextSegmenter._period_is_numeric_separator()`, `TextSegmenter._iter_forced_latency_segments()`, `TextSegmenter._iter_soft_phrase_segments()`, `TextSegmenter._find_soft_phrase_cut()`, `TextSegmenter._find_whitespace_cut()`, `TextSegmenter._find_forced_latency_cut()`, `TextSegmenter._find_no_space_script_cut()`, `TextSegmenter._no_space_script_segment_limit()`, `TextSegmenter._extend_cut_over_combining_marks()`, `TextSegmenter._min_orphan_chars()`, `TextSegmenter._is_forced_soft_break()`, `TextSegmenter._is_contextual_soft_phrase_cut()`.
  - Unicode character classifiers: `_is_no_space_script_character()`, `COMMON_ABBREVIATIONS`, `_unicode_name()`, `is_sentence_terminator_character()`, `_is_sentence_terminator_character()`, `_is_soft_break_character()`, `_is_colon_like_character()`, `_is_dash_like_character()`, `_is_sentence_trailing_closer()`, and `_SPEECH_SANITIZE_TABLE`.
  - Segmentation threshold constants: `MIN_ORPHAN_SPACE_CHARS`, `MIN_ORPHAN_NO_SPACE_CHARS`, `FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS`, `FAST_FIRST_PUNCTUATION_FREE_NO_SPACE_TRIGGER_CHARS`, `FAST_FIRST_PREFERRED_SOFT_CHARS`, `FAST_FIRST_PREFERRED_WHITESPACE_CHARS`, `FAST_FIRST_SEGMENT_MIN_CHARS`, `REGULAR_SEGMENT_MIN_CHARS`, `FAST_FIRST_SEGMENT_MAX_CHARS`, `FAST_FIRST_SEGMENT_TRIGGER_CHARS`, `REGULAR_SEGMENT_MAX_CHARS`, `SEAMLESS_UTTERANCE_MAX_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD`, `SOFT_PHRASE_SEGMENT_MIN_CHARS`, `SOFT_PHRASE_SEGMENT_MAX_CHARS`, `SOFT_PHRASE_SEGMENT_LOOKAHEAD`, `URL_TOKEN_SEGMENT_MAX_CHARS`, `FORCED_SEGMENT_MIN_CHARS`, `FORCED_SEGMENT_FORWARD_LOOKAHEAD`, `FORCED_SEGMENT_HARD_MAX_CHARS`, `NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS`, `NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO`, and `NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` adapters: `_iter_speech_chunks()`, `_split_text_for_latency()`, `_sanitize_speech_text()`, `_iter_indexed_text_segments()`, `_iter_text_segments_for_latency()`, `_spoken_bridge_segments()`, `_looks_like_url_token()`, and `_should_pause_after_segment()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` context: `WasmTtsEngineBridge.speak(..., hasPreviousSegment=...)` and `ChromeTtsBridge.speak(..., hasPreviousSegment=...)`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` continuity: `googleTtsForNvdaSpeak()`, `waitForWasmEnd()`, `waitForSynthesisComplete()`, `FakeAudioWorkletNode`, `stopActiveSynthesis()`, `hasPreviousSegment`, `hasBoundaryContext`, `smoothSegmentBoundaries`, `boundaryHoldSamples`, `queueProcessedAudio()`, `finishSegmentAudio()`, `heldBoundarySamples`, and `segmentEnd`.

- **Performance Optimizations**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` timing constants: `_NORMAL_SENTENCE_BREAK_MS`, `_END_OF_UTTERANCE_PAUSE_MS`, `_PRELOAD_RESUME_DELAY_SECONDS`, `_FLUSH_GROUP_CHARS_THRESHOLD`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` coalescing: `_speak_text()` early `cancelEvent.is_set()` check.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` flush logic: `_iter_speech_chunks()` inner loop with threshold-based `accumulatedChars` check.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py` lead buffer: `LIVE_MULTI_SEGMENT_LEAD_MS`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/standby.py` early-exit: `_StandbyRuntimeManager._run_refresh()` warm-bridge skip.

---

## 7. Voice Catalog & Voice Store Code Map

- **`catalog.py` Catalog Constants, Verification & Models**:
  - Engine paths & constants: `BASE_DIR`, `ENGINE_VERSION`, `ENGINE_ROOT`, `ENGINE_DIR`, `CATALOG_PATH`, `REQUIRED_ENGINE_FILES`, and `UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS`.
  - Engine verification & exceptions: `EngineLibraryError`, `inspect_engine_library()`, and `is_package_supported_by_engine()`.
  - Data models & package helpers: `VoicePackage`, `Speaker`, `package_id_to_language()`, and `_safe_str()`.
  - VoiceCatalog model & runtime export: `VoiceCatalog.load()`, `VoiceCatalog.from_json()`, `VoiceCatalog.package_for_voice()`, `VoiceCatalog.speaker_for_voice()`, `VoiceCatalog.to_runtime_json()`, `VoiceCatalog.packages_by_language`, `VoiceCatalog.all_languages`, and `VoiceCatalog.speakers_for_language()`.

- **`voice_store.py` Package Management & Verification**:
  - Runtime path caches: `_dataRootCache`, `_voiceDirCache`, `data_root()`, and `voice_dir()`.
  - Download constants and limits: `VOICE_PACKAGE_MAX_BYTES` and `DOWNLOAD_CHUNK_SIZE`.
  - Verification cache state: `_verifiedPackageCache` and `_persistentVerifiedPackageCache`.
  - Persistent verification cache IO: `_load_persistent_verification_cache()`, `_save_persistent_verification_cache()`, and `_persistent_cache_matches()`.
  - Verification cache mutation: `_remember_verified_package()` and `_forget_verified_package()`.
  - Package verification and batch scans: `_check_package_file_installed()`, `_voice_files_by_name()`, `is_package_installed()`, `physically_installed_packages()`, and `usable_installed_packages()`.
  - Download and package management: `download_package()`, `remove_package()`, and `copy_existing_package()`.

---

## 8. Voice Preload & Session Isolation Code Map

- **Synth-Side Preload Worker**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver._warm_current_voice_async()`, `SynthDriver.speak()`, and `SynthDriver.cancel()`.
  - Voice-id planning: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_voice_ids_for_voice()`, `_voice_id_for_package()`, and `_warmup_options_for_voice_ids()`.
  - Preload option building: `_speech_options()`.

- **Bridge Preload Entry Points**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `ChromeTtsBridge.preload_voice()`, `ChromeTtsBridge.ensure_connection()`, and `WasmTtsEngineBridge.preload_voice()`.

- **Browser Harness Session Isolation**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: `currentSessionToken`, `beginSession()`, `isCurrentSession()`, token-aware `emit()`, `queueAudioPacket()`, `flushAudioQueue()`, `queueProcessedAudio()`, `queueAudio()`, `finishSegmentAudio()`, `scheduleWorkletEmpty()`, `flushTempoProcessor()`, `FakeAudioWorkletNode`, `googleTtsForNvdaPreload()`, `googleTtsForNvdaSpeak()`, and `stopActiveSynthesis()`.

---

## 9. Standby Readiness & Directory Watcher Code Map

- **Standby Runtime Manager**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/standby.py:keep_browser_runtime_ready_enabled() -> bool` — Gating check for standby pre-warming.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/standby.py:_StandbyRuntimeManager` — Manages pre-warmed bridge lifecycle (`refresh_async`, `claim_bridge`, `release_synth_bridge`, `terminate`, `_run_refresh`).

- **Directory Change Watcher**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/watcher.py:DirectoryChangeWatcher` — Reusable Win32 directory change watcher using `FindFirstChangeNotificationW` / `WaitForMultipleObjects` with `_INFINITE` timeout; `stop(timeout=5.0)` performs a bounded join with warning logging.

---

## 10. Automatic Language Detection & Profiles Code Map

- **Synth-Side Language Selection**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_auto_detect_profile_for_text()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_auto_language_candidate_for_language()`, `_detect_auto_language()`, `_language_token_signal()`, `_voice_for_language()`, `_voice_matches_language()`, `_current_speaker_id()`, and `_speech_options()`.
  - Profile-aware warm-up ordering: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_options_for_voice_ids()`, `_warmup_voice_ids_for_voice()`, and `_voice_id_for_package()`.

- **Pure Unicode-Script Fallback**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_profiles.py`: `script_ranges_for_language_root()`, `token_has_character_in_ranges()`, and `language_script_signal()`.

- **NVDA Speech Filter & Language Commands**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_filter_auto_language_speech_sequence()`, `_register_auto_language_speech_filter()`, `_unregister_auto_language_speech_filter()`, `_google_lang_change_command()`, `_google_lang_change_language()`, `_nvda_locale_for_language()`, and `_auto_language_for_process_text()`.

- **Voice Dictionaries & Spelling Overlays**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_patch_auto_language_voice_dictionary()`, `_unpatch_auto_language_voice_dictionary()`, `_auto_profile_character_settings_for_language()`, `_auto_profile_character_context_for_text()`, `_single_auto_profile_character_settings()`, `process_text_with_auto_voice_dictionary()`, `get_spelling_speech_with_auto_profile()`, and `should_use_spelling_functionality_with_auto_profile()`.

- **Settings Ring Notice Integration**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver.supportedSettings`, `SynthDriver.isSupported()`, `ReadOnlyTextDriverSetting`, `_get_availableNotices()`, `_auto_language_notice_message()`, `_get_notice()`, and `_set_notice()`.

- **Settings UI Storage & Validation**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py`: `_installed_speakers_by_language()`, `_current_speech_defaults()`, `_configured_auto_language_detection()`, `_configured_auto_language_preferred()`, `_configured_auto_language_candidates()`, `_configured_auto_language_profiles()`, `_select_preferred_auto_language()`, `_refresh_preferred_language_choices()`, `_ensure_auto_language_profiles()`, `_default_voice_for_language()`, `_valid_profile_variant()`, `_load_selected_auto_language_profile()`, `_store_selected_auto_language_profile()`, `_enabled_auto_language_candidates()`, `_auto_language_status_message()`, `_refresh_auto_language_controls()`, `_refresh_auto_language_profile_value_controls()`, `_save_auto_language_settings()`, and `_refresh_synth_settings_ring()`.

- **Locale Normalization & Display Names**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_utils.py`: `SPECIAL_NVDA_LOCALES`, `normalize_language()`, `normalize_language_code()`, `normalize_language_key()`, `get_nvda_locale_for_language()`, `nvda_locale_exists()`, `resolve_nvda_locale()`, `_language_display_candidates()`, and `get_language_display_name()`.

- **CLD2 Detector & Architecture DLLs**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_detector.py`: `GOOGLE_TTS_LANG_CHANGE_ATTR`, `MISSING_GOOGLE_TTS_LANGUAGE`, `_DLL_DIR`, `_DLL_NAMES`, `_MIN_RELIABLE_PERCENT`, `_LANGUAGE_ALIASES`, `_CHINESE_LANGUAGE_ROOTS`, `_LANGUAGE_REDIRECTS`, `redirect_language()`, `DetectionResult`, `_Cld2Detector`, `_Cld2Detector.detect()`, `_Cld2Detector._load_library()`, `_detector`, `detect_language()`, `_candidate_for_language()`, `_language_match_keys_cached()`, `language_match_keys()`, `_language_aliases()`, `_language_family()`, `_language_root()`, `_normalize_language()`, and `language_matches()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/`: `cld2_x86.dll`, `cld2_x64.dll`, `cld2.dll`, `LICENSE.txt`, and `README.txt`.

---

## 11. Volatile RAM Speech Cache Code Map

- **Cache Read, Write & Lifecycle**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `SynthDriver._speak_text()`, `_short_cache_key()`, `_segment_cache_key()`, `_get_cached_audio()`, `_put_cached_audio()`, and `_clear_short_audio_cache()`.
  - Limits, caps and stats: `_SHORT_CACHE_MAX_ITEMS`, `_SHORT_CACHE_MAX_BYTES`, and `_SHORT_CACHE_STATS_LOG_INTERVAL`.
  - Recycle cleanup: `SynthDriver._maybe_recycle_bridge_after_request()` and `_clear_short_audio_cache()`.

- **Cache Keys, Limits & Lead Buffering**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `SHORT_CACHE_MAX_CHARS`, `SHORT_CACHE_MAX_HIDDEN_SEGMENTS`, `short_audio_cache_key()`, `segment_audio_cache_key()`, `is_complete_speech_result()`, `LIVE_MULTI_SEGMENT_LEAD_MS`, and `PcmLeadBuffer`.

- **Bridge & Browser Completion Signals**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `CdpDispatcher`, `CdpClient.request()`, and `WasmTtsEngineBridge.speak()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: `handleTtsEngineEvent()`, `synthesisErrorMessage`, `googleTtsForNvdaSpeak()`, `waitForWasmEnd()`, `waitForSynthesisComplete()`, `finishSegmentAudio()`, `flushAudioProcessors()`, `flushAudioQueue()`, and `segmentEnd`.

---

## 12. Voice Manager UI Code Map

- **Dialog Lifecycle & Tabs**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `VoiceManagerDialog`, `VoiceManagerDialog.__init__()`, `_build_ui()`, `_build_installed_tab()`, `_build_download_tab()`, `_create_list()`, `focus_default_control()`, `show_download_tab()`, `_focus_active_page()`, `_focus_installed_tab()`, `_focus_download_tab()`, `on_char_hook()`, and `_on_notebook_key_down()`.

- **Language Filtering, Sorting & Display Names**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `get_nvda_locale_for_language()`, `get_language_display_name()`, `_current_ui_language()`, `_locale_candidates()`, `_language_sort_rules_for_current_ui()`, `_load_language_sort_rules()`, `_normalize_language_sort_rules()`, `_combining_marks_from_names()`, `_strip_combining_marks()`, `_rule_based_visible_sort_key()`, `_visible_language_sort_key()`, `_language_codes_for_display()`, `_update_language_combo()`, `_apply_installed_filter()`, `_apply_download_filter()`, `on_installed_language_filter_changed()`, `on_download_language_filter_changed()`, `_populate_installed_list()`, `_populate_download_list()`, `_visible_package_sort_key()`, and `_insert_package_row()`.

- **Package Status & Dependency Analysis**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `_direct_installed_dependents()`, `_direct_download_dependents()`, `_installed_package_status()`, `_download_package_status()`, `_speaker_names()`, `_format_size()`, `_checked_packages()`, `_with_installed_dependents()`, `_with_required_download_dependencies()`, `_missing_dependency_for_package()`, `_dependency_depth()`, `_dependents_first()`, `_dependencies_first()`, and `_package_list_text()`.

- **Download, Installation & Progress Worker**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `on_download_selected()`, `_download_worker()`, `_update_download_progress()`, and `_warm_current_google_synth_voice()`.

- **Package Removal, Safety Protection & Config Reset**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `on_remove_selected()`, `_removes_all_usable_voices()`, `_usable_packages_after_removal()`, `_remove_worker()`, `_reset_configured_voice_if_removed()`, `_reset_auto_language_profile_variants_if_removed()`, and `_apply_reset_voice_to_current_synth()`.

- **Folder & System Integration**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`: `on_open_folder()`.

---

## 13. Add-on Settings, Controls & Updater Code Map

- **Settings Grouped Controls & Status Accessibility**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py`: `_SettingsGroup`, `_SettingsGroup.addLabeledControl()`, `_SettingsGroup.addCheckBox()`, `_SettingsGroup.addButton()`, `GoogleTtsSettingsPanel._add_settings_group()`, and `GoogleTtsSettingsPanel._refresh_settings_layout()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py`: `_from_dip()`, `_estimate_wrapped_line_count()`, `_estimate_text_width()`, `_max_read_only_text_width()`, `_read_only_text_target_width()`, `resize_read_only_text_for_content()`, `bind_read_only_text_focus_announcement()`, `format_size_mb()`, `format_size_auto()`, `open_synthesizer_dialog()`, and `show_runtime_error_dialog()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_make_read_only_text_setting_control()`, `_patch_read_only_text_setting()`, `_unpatch_read_only_text_setting()`, `_hide_google_tts_auto_profile_speech_controls()`, and `_show_manual_web_url_dialog()`.

- **Release Manifest Generation**:
  - `make_update_manifest.py`: `ADDON_ID`, `BUILD_INFO_FILE_NAME`, `DEFAULT_CHANNEL`, `DEFAULT_OUTPUT`, `DEFAULT_URL_TEMPLATE`, `TRANSLATED_MANIFEST_RE`, `IGNORED_SEARCH_DIRS`, `ManifestError`, `_parse_manifest()`, `_read_addon_manifest()`, `_read_addon_build_info()`, `_read_release_notes_by_locale()`, `_sha256()`, `_version_sort_key()`, `_iter_addon_packages()`, `_find_addon_package()`, `build_update_manifest()`, `_parse_args()`, and `main()`.

- **Add-on Updater Core**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py`: `ADDON_ID`, `BUILD_INFO_FILE_NAME`, `UPDATE_CHANNEL`, `UPDATE_MANIFEST_URL`, `MAX_UPDATE_MANIFEST_BYTES`, `MAX_UPDATE_PACKAGE_BYTES`, `DOWNLOAD_CHUNK_SIZE`, `UpdateError`, `UpdateCancelled`, `UpdateInfo`, `UpdateCheckResult`, `DownloadedUpdate`, `current_version()`, `current_update_build()`, `_is_update_available()`, `_parse_update_info()`, `fetch_update_manifest()`, `check_for_update()`, `download_update()`, `remove_update_manifest()`, `remove_downloaded_update()`, `cleanup_update_files()`, and `format_size()`.

- **Runtime UI & Controller Flow**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py`: `CONFIG_AUTO_UPDATE_CHECK`, `DEFAULT_AUTO_UPDATE_CHECK`, `_nvda_translate()`, `automatic_update_check_enabled()`, `set_automatic_update_check_enabled()`, `update_check_in_progress()`, `update_status_message()`, `register_update_status_listener()`, `_notify_update_status_changed()`, `_UpdateAvailableDialog`, `_UpdateDownloadDialog`, `_UpdateCheckController`, `_begin_update_check()`, `_finish_update_check()`, `_start_update_check()`, `start_manual_update_check()`, and `start_automatic_update_check()`.

- **Settings & Global Plugin Integration**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py`: `GoogleTtsSettingsPanel.makeSettings()`, `GoogleTtsSettingsPanel.onSave()`, `GoogleTtsSettingsPanel.on_check_for_updates()`, `GoogleTtsSettingsPanel.on_auto_update_check_changed()`, `GoogleTtsSettingsPanel._refresh_update_controls()`, `GoogleTtsSettingsPanel._restore_update_check_focus()`, and `GoogleTtsSettingsPanel._on_destroy()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `config.conf.spec[...]`, `GlobalPlugin.__init__()`, `GlobalPlugin._on_post_nvda_startup()`, and `GlobalPlugin.terminate()`.

---

## 14. Developer Tools & Build Infrastructure Code Map

- **Voice Catalog Generator (`generate_voices_json.py`)**:
  - `GOOGLE_TTS_JSON_CANDIDATES`, `ENGINE_ROOT_CANDIDATES`, `CATALOG_MODULE_CANDIDATES`, `ENGINE_VERSION_PATTERN`, `_version_sort_key()`, `_resolved_engine_roots()`, `newest_bundled_engine_dir()`, `latest_engine_voices_json()`, `catalog_engine_version()`, `check_engine_version_alignment()`, `select_engine_voices_json()`, `_parse_args()`, and `main()`.

- **Unicode Data Generator (`generate_unicode_data.py`)**:
  - Engine version policy: `ENGINE_ROOT_CANDIDATES`, `CATALOG_MODULE_CANDIDATES`, `ENGINE_VERSION_PATTERN`, `_version_sort_key()`, `_resolved_engine_roots()`, `newest_bundled_engine_dir()`, `check_engine_version_alignment()`, `_configured_engine_version()`, and `_configured_voices_json()`.
  - Pinned inputs and bundled-catalog selection: `DEFAULT_ENGINE_ROOT`, `DEFAULT_OUTPUT`, `DEFAULT_CATALOG_MODULE`, `_configured_engine_version()`, `_configured_voices_json()`, `_supported_locales()`, `_ucd_version()`, and `main()`.
  - UCD/CLDR parsing, range composition, and module rendering: `_parse_ucd_records()`, `_script_aliases()`, `_likely_scripts()`, `_supported_language_scripts()`, `_merge_ranges()`, `_format_ranges()`, `_format_codepoints()`, and `_render_module()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/unicode_data.py` generated tables: `UNICODE_VERSION`, `CLDR_VERSION`, `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `LANGUAGE_SCRIPT_RANGES`, and `SENTENCE_TERMINAL_CODEPOINTS`.

- **Translation & Localization Tool (`build_i18n.py`)**:
  - Source UI extraction & POT writing: `_translatable_source_messages()`, `_manifest_version()`, `_manifest_values()`, and `_write_pot()`.
  - Locale PO template updates: `_find_msgmerge()`, `_purge_obsolete_po_entries()`, `_update_po_from_template()`, `_run_msgmerge()`, `_verify_merged_po()`, `--update-po`, `--language`, `--all-languages`, and `--msgmerge`.
  - PO parsing, interpolation, syntax and sort validation: `_parse_po()`, `_po_fuzzy_msgids()`, `_check_catalog()`, `_check_format_interpolations()`, `_check_html_tag_interpolations()`, `_check_po_syntax_with_msgfmt()`, `_check_po_syntax_fallback()`, `_check_language_files()`, `_check_language_sort_file()`, `_parse_checks()`, and `_print_run_summary()`.
  - Documentation extraction, POT/PO updates & checks: `_extract_doc_segments()`, `_write_doc_pot()`, `_update_doc_po_from_template()`, `_extract_po_from_doc_html()`, `_check_doc_segments()`, `_check_doc_folder_health()`, `_check_doc_language()`, `--extract-doc-template`, `--update-doc-po`, and `--check-docs`.
  - Documentation building & Markdown/HTML conversion: `_DocHtmlExtractor`, `_DocHtmlRebuilder`, `_DocHtmlToMarkdown`, `_RESOLVED_DOC_MARKDOWN_EXTENSIONS`, `_get_active_doc_markdown_extensions()`, `_generate_doc_html_body()`, `_markdown_to_doc_html()`, `_fallback_markdown_to_html_body()`, `_render_table_html()`, `_convert_html_file_to_md()`, `_convert_md_file_to_html()`, `_build_doc_for_language()`, `_build_doc_from_po()`, `--build-docs`, `--html-to-md`, and `--md-to-html`.
  - Generated output writers: `_compile_mo()`, `_compile_mo_file()`, and `_write_translated_manifest()`.
  - Interactive menu: `_prompt_languages()`, `_prompt_checks()`, `_prompt_doc_checks()`, `_interactive_options()`, and `main()`.
  - NVDA locale discovery: `DEFAULT_NVDA_LOCALE_DIRS`, `_supported_nvda_languages_from_dirs()`, and `--nvda-locale-dir`.

- **Packaging Scripts & Licenses**:
  - `build.bat` (Windows) & `build.sh` (WSL/Linux): package entry points enforcing clean builds, syntax verification, conflict marker scans, and `.nvda-addon` archiving.
  - Packaged licenses: `LICENSE` (repository GPL-2.0 copy); `googleTtsForNvda/LICENSE` (packaged GPL-2.0 copy); `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/LICENSE` (Chromium WASM TTS BSD-3-Clause copy); `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/EIGEN_LICENSE` (Eigen Apache-2.0 copy).

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
    - **standby.py** — `_StandbyRuntimeManager`, background browser-runtime readiness manager
    - **watcher.py** — `DirectoryChangeWatcher`, reusable Win32 directory-change watcher with heartbeat logging
    - **catalog.py** — `VoiceCatalog`, `VoicePackage`, `Speaker` models, engine library integrity validation
    - **cld2/** — Vendored Compact Language Detector 2 libraries and documentation
      - **cld2_x86.dll** — 32-bit dynamic library for x86 NVDA runtimes
      - **cld2_x64.dll** — 64-bit dynamic library for x64 / ARM64 NVDA runtimes
      - **cld2.dll** — Generic fallback dynamic library
      - **LICENSE.txt** — CLD2 Apache-2.0 license copy
      - **README.txt** — Vendoring documentation and build instructions
    - **language_detector.py** — CLD2-backed language detection, candidate priors, number/symbol routing, Unicode script signals
    - **language_profiles.py** — Mathematical alphanumeric normalization, token classification, number clustering, mixed-text segmentation, UTS #51 emoji preservation
    - **language_utils.py** — Language normalization, NVDA special locale mappings, localized display names
    - **speech_processing.py** — `TextSegmenter`, Unicode sentence boundary detection, `PcmSilenceShortener`, `PcmLeadBuffer`, combining mark & emoji sequence cut protection
    - **unicode_data.py** — Pre-generated Unicode 17.0 / CLDR 48.2 script ranges, sentence terminals, and normalization table
    - **voice_store.py** — Voice package download, copy, verify, remove, persistent verification cache
    - **web/** — Headless Chromium bridge environment
      - **index.html** — Shell document loaded in headless Chromium browser runtime
      - **bridgeHarness.js** — Shims chrome.* APIs, calls WASM engine, captures AudioWorklet PCM, sends base64 chunks through CDP
    - **WasmTtsEngine/<ENGINE_VERSION>/** — Bundled Chromium WASM TTS engine runtime
      - **bindings_main.js / .wasm** — Chromium WASM TTS engine binary and JavaScript bindings
      - **background_compiled.js** — Extension service worker script
      - **offscreen.html** — Offscreen host document providing Web Audio context
      - **offscreen_compiled.js** — Background worker script hosting AudioContext and WASM instance
      - **manifest.json / wasm_tts_manifest_v3.json** — Extension manifests
      - **voices.json** — Master voice catalog and speaker metadata
      - **streaming_worklet_processor.js** — AudioWorklet PCM processor
      - **_metadata/verified_contents.json** — Extension package integrity manifest
      - **VOICE_CATALOG_NOTICE.txt** — Voice package licensing and terms notice
      - **LICENSE** — Chromium WASM TTS BSD-3-Clause license copy
      - **EIGEN_LICENSE** — Eigen dependency Apache-2.0 license copy
    - **websocketClientRepo/** — Vendored private websocket-client library
      - **__init__.py** — Package isolation root
      - **LICENSE** — Apache-2.0 license copy
      - **websocket/** — Vendored websocket client implementation modules (`_abnf.py`, `_app.py`, `_core.py`, `_http.py`, etc.)
  - **globalPlugins/googleTtsForNvda/** — Global plugin integration and UI components
    - **__init__.py** — Tools menu integration, synth switching interception, voice dict hooks, speech filters, settings specs
    - **settings.py** — `GoogleTtsSettingsPanel`, NVDA settings dialog category for Google TTS
    - **uiUtils.py** — Shared UI utilities, DIP scaling, read-only text focus handling, size formatting, error dialogs
    - **updater.py** — Add-on update manifest checker, download, SHA-256 verification, updater staging
    - **updateGui.py** — Add-on update notification dialogs, download progress dialogs, auto-check controller
    - **voiceManager.py** — `VoiceManagerDialog`, wx Voice Manager with Installed and Download tabs
  - **doc/** — Localized user documentation
    - **en/readme.html** — English user documentation
    - **<locale>/readme.html** — Localized user documentation
  - **locale/** — Localized interface catalogs
    - **<locale>/** — Interface catalogs (`LC_MESSAGES/nvda.po`, `nvda.mo`, `manifest.ini`, `languageSort.json`)
- **tests/** — Automated test suite, benchmark suite, and validation runners (see [tests/README.md](file:///c:/Users/hungv/Documents/Codex/Google-TTS-For-NVDA/tests/README.md) for full test index, architecture, and running instructions)
- **build.bat** — Windows batch build script (clean, lint, compile gettext/manifests/docs, package `.nvda-addon`)
- **build.sh** — Linux/WSL POSIX build script mirroring `build.bat`
- **build_i18n.py** — Localization CLI tool (POT/PO extraction, merge, syntax validation, Markdown/HTML doc generation)
- **generate_voices_json.py** — Upstream voice catalog scraper and generator from ChromeOS/Google textproto
- **generate_unicode_data.py** — Unicode 17.0 and CLDR 48.2 script ranges, sentence terminals, and normalization table generator
- **make_update_manifest.py** — Release update manifest generator creating `stable.json` for internal updater
- **UPDATER_RELEASE_GUIDE.md** — Release instructions for versioning, hotfix increments, and manifest generation
- **CONTRIBUTING.md** — Contribution, PR guidelines, build instructions, and developer workflows
- **TRANSLATING.md** — Translating guidelines, Crowdin sync, PO file workflows, and doc translation guide
- **mypy.ini** — Static type checker configuration targeting Python 3.11
- **ruff.toml** — Ruff linter and formatter configuration (rules, exclusions, line-length)
- **LICENSE** — Repository GNU General Public License v2
- **readme.md** — Repository English overview and quick-start documentation

---

### Runtime Paths

| Purpose | Location / Value | Description & Module Reference |
|---|---|---|
| NVDA config root | `%APPDATA%/nvda` (installed) or `<NVDA_DIR>/userConfig` (portable / direct) | Active NVDA configuration directory. Can also be overridden via `-c` / `--config-path` CLI arguments (`googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py:_default_config_path()`). |
| Add-on installed root | `%APPDATA%/nvda/addons/googleTtsForNvda/` (installed) or `<NVDA_DIR>/userConfig/addons/googleTtsForNvda/` (portable / direct) | Directory where the add-on package is unpacked and executed. |
| Add-on user data root | `%APPDATA%/nvda/googleTtsForNvda/` (installed) or `<NVDA_DIR>/userConfig/googleTtsForNvda/` (portable / direct) | Persistent add-on data directory surviving add-on updates and reinstalls (`googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py:data_root()`). |
| Downloaded voice packages | `%APPDATA%/nvda/googleTtsForNvda/voices/*.zvoice` (installed) or `<NVDA_DIR>/userConfig/googleTtsForNvda/voices/*.zvoice` (portable / direct) | Directory containing downloaded voice package archives (`googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py:voice_dir()`). |
| Voice verification cache | `%APPDATA%/nvda/googleTtsForNvda/verified_voices.json` (installed) or `<NVDA_DIR>/userConfig/googleTtsForNvda/verified_voices.json` (portable / direct) | Cache mapping package files and sizes to verified SHA-256 hashes (`googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py:_verification_cache_path()`). |
| Runtime voices.json | `%APPDATA%/nvda/googleTtsForNvda/runtime/voices.json` (installed) or `<NVDA_DIR>/userConfig/googleTtsForNvda/runtime/voices.json` (portable / direct) | Dynamic JSON catalog exported and served via local HTTP bridge to headless Chromium (`googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager.start_server()`). |
| NVDA configuration file | `%APPDATA%/nvda/nvda.ini` (installed) or `<NVDA_DIR>/userConfig/nvda.ini` (portable / direct) | Main NVDA configuration file storing add-on settings under `[googleTtsForNvda]` section (`googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:CONFIG_SECTION`, `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py:config.conf.spec`). |
| Voice dictionaries | `%APPDATA%/nvda/speechDicts/` (installed) or `<NVDA_DIR>/userConfig/speechDicts/` (portable / direct) | Per-voice speech pronunciation dictionaries managed by NVDA speechDictHandler (`googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py:_load_voice_dictionary_for_voice()`). |
| Browser persistent profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/persistentSession/` | Isolated persistent browser user data directories (`googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._browser_profile_root()`). |
| Browser temporary profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/session-<pid>-<timestamp>/` | Ephemeral browser sessions spawned when the persistent session is in use or locked (`googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._start_browser_choice()`). |
| Profile size tracker | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/profile_size_check.json` | Records 24-hour periodic profile size check timestamp and cumulative size (`googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_persistent_profile_size_check_due()`). |
| Updater download directory | `%TEMP%/googleTtsForNvda-updates/` | Staging folder for downloaded `stable.json` manifests and `.nvda-addon` update installers (`googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py:_update_download_dir()`). |
| Bundled master catalog | `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/voices.json` | Master immutable voice catalog distributed with the add-on (`googleTtsForNvda/synthDrivers/googleTtsForNvda/catalog.py:CATALOG_PATH`). |
| Bundled WASM engine | `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/` | Chromium WASM TTS engine binary, worklet processor, and bindings (`googleTtsForNvda/synthDrivers/googleTtsForNvda/catalog.py:ENGINE_DIR`). |
| Vendored CLD2 DLLs | `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/` | Architecture-specific Compact Language Detector 2 dynamic libraries (`cld2_x86.dll`, `cld2_x64.dll`, `cld2.dll`). |
| Web harness | `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/` | Headless Chromium bridge environment (`index.html`, `bridgeHarness.js`). |

---

### Speech Data Flow

1. NVDA calls `SynthDriver.speak()` with a sequence of text segments, pause commands, and speech parameters.
2. The driver segments text for low-latency streaming, applies pause shortening rules, builds synthesis options (voice, rate, pitch, volume), and enqueues requests to a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP connection are established, then evaluates `window.googleTtsForNvdaSpeak(...)` via CDP `Runtime.evaluate`.
4. `bridgeHarness.js` passes text to the bundled Google WASM TTS engine (`onSpeak`), intercepts synthesized `AudioWorkletNode` buffers, applies gain makeup and clipping protection, converts Float32 audio to 16-bit PCM, and transmits base64 PCM chunks back through the `googleTtsForNvdaBridge` CDP binding.
5. Python receives `Runtime.bindingCalled` events via `CdpDispatcher`, decodes base64 PCM bytes, and feeds audio directly to `nvwave.WavePlayer` for output.

---

## 2. Synth Driver & NVDA Integration Code Map

- **Synth Switching & Selection Interception**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: Intercepts NVDA synthesizer selection (`synthDriverHandler.setSynth`) to verify that at least one Google TTS voice package is installed; prompts user with the Voice Manager dialog if none is installed (`_normalize_set_synth_args()`, `_call_set_synth_compat()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, `_unpatch_synth_selection()`).

- **Voice Dictionaries & Speech Hooks**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: Patches NVDA voice dictionary dialog to display and manage per-speaker pronunciation dictionaries (`_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_patch_read_only_text_setting()`, `_unpatch_read_only_text_setting()`).
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: Hooks `speechDictHandler.loadVoiceDict` via `_VoiceDictionarySynthProxy` to load speaker-specific `.dic` files for the active Google TTS variant (`_load_voice_dictionary_for_voice()`, `_current_google_tts_speaker_id()`, `_patch_google_tts_voice_dictionary_loading()`, `_unpatch_google_tts_voice_dictionary_loading()`).
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: Splits speech sequences by detected language tokens and applies language-specific dictionary substitutions and spelling character rules (`_filter_auto_language_speech_sequence()`, `_patch_auto_language_voice_dictionary()`, `process_text_with_auto_voice_dictionary()`, `get_spelling_speech_with_auto_profile()`, `should_use_spelling_functionality_with_auto_profile()`).

- **Synth Driver Core & Speech Loop**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: Main `SynthDriver` implementation subclassing `synthDriverHandler.SynthDriver`. Dedicated background worker thread `_speech_loop()` pulls from `_speechQueue`, invokes `_speak_worker()` for chunk synthesis, and coordinates pause/resume/cancel events (`terminate()`, `speak()`, `cancel()`, `pause()`, `loadSettings()`, `isSupported()`).

- **Audio Output & Device Error Recovery**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: Manages the active NVDA audio output device, handles device changes gracefully, and re-initializes `nvwave.WavePlayer` upon audio errors (`_current_output_device()`, `_default_output_device()`, `_audio_device_error()`, `_create_wave_player()`, `_ensure_current_output_device()`).

- **Fatal Fallback & Runtime Dialogs**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: Catches unrecoverable browser or engine crashes, clears queues, switches NVDA to its default fallback synthesizer to prevent speech loss, and displays a user error dialog (`_trigger_fatal_fallback()`, `_show_engine_library_error()`, `_show_missing_chrome_error()`).
  - `globalPlugins/googleTtsForNvda/uiUtils.py`: Centralized error dialog presentation using delayed main-thread dispatch (`show_runtime_error_dialog()`).

- **GUI Settings Panel & Global Plugin Integration**:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py`: NVDA Settings dialog panel under Voice / Google TTS category (`GoogleTtsSettingsPanel.makeSettings()`, `GoogleTtsSettingsPanel.onSave()`).
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: Integrates into NVDA Tools menu and registers input gestures for Voice Manager and Settings dialogs (`GlobalPlugin.terminate()`, `GlobalPlugin.on_open_voice_manager()`, `GlobalPlugin.script_openVoiceManager()`, `GlobalPlugin.script_openSettings()`, `GlobalPlugin.__gestures`).

- **Static API Contracts & Testing**:
  - AST contract validation across NVDA releases, interactive manual release checklist, and unit test documentation are indexed in [tests/README.md](file:///c:/Users/hungv/Documents/Codex/Google-TTS-For-NVDA/tests/README.md).

---

## 3. Browser Runtime, Lifecycle & Resilience Code Map

- **Browser Runtime Availability & Selection**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:browser_runtime_available(runtime: str | None = None) -> bool` — Validates executable existence on disk and Edge WebView2 registry presence.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager.browser_runtime_available(cls, runtime: str | None = None) -> bool` — Classmethod delegating to module-level `browser_runtime_available()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.browser_runtime_available(cls, runtime: str | None = None) -> bool` — Classmethod delegating to `BrowserProcessManager.browser_runtime_available()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:browser_executable_available(runtime: str) -> bool` — Checks browser executable presence on disk.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:edge_webview2_available() -> bool` — Verifies Microsoft Edge WebView2 runtime availability via Windows registry keys.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:find_browser() -> str | None` — Discovers the first available supported Chromium executable.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:effective_browser_runtime() -> str | None` — Resolves the active browser runtime from user configuration and disk availability.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_runtime_fallback_order() -> list[str]` — Generates candidate fallback sequence starting with the configured browser runtime.

- **Chromium Profile Isolation & Lock Recovery**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_hidden_chrome_startup_kwargs() -> dict[str, Any]` — Prepares hidden window `STARTUPINFO` flags (`STARTF_USESHOWWINDOW`, `wShowWindow = 0`) and `CREATE_NO_WINDOW` creation flags on Windows.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_browser_profile_in_use_error(exitCode: int = 21) -> _BrowserProfileInUseError` — Constructs profile-in-use exception recording process exit code.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._read_devtools_port()` — Reads `DevToolsActivePort`; on persistent profile early exit, raises `_browser_profile_in_use_error(exitCode)`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._start_browser_choice()` — Spawns Chromium with `DEVNULL` standard handles, catches `_BrowserProfileInUseError`, and retries with an isolated temporary session profile.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._browser_profile_root()`, `_browser_profile_dir_name()`, `_get_browser_profile_dir()` — Manages isolated profile directory trees (`chromeProfiles`, `edgeProfiles`, `braveProfiles`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager._cleanup_old_browser_profiles()`, `_release_chrome_profile()`, `_remove_chrome_profile()` — Preserves persistent profiles while removing temporary profiles upon shutdown.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:PERSISTENT_PROFILE_MAX_BYTES`, `PERSISTENT_PROFILE_SIZE_CHECK_INTERVAL_SECONDS`, `_persistent_profile_size_check_due()`, `_remember_persistent_profile_size_check()` — Throttles recursive profile size checks.

- **Speech Loop Crash Resilience & Fatal Fallback**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._speech_loop()` — Daemon speech loop containing outer `try...except Exception:` around `self._speak_worker(*request)` to prevent thread termination.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._speak_worker()` — Synthesis worker with defensive `ChromeTtsBridge.browser_runtime_available()` call.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._warm_current_voice_async()` — Background pre-warm worker with defensive runtime availability check.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._trigger_fatal_fallback(friendlyMessage)` — Debounced fatal fallback handler: clears `_speechQueue`, cancels active wave playback, switches to NVDA fallback synth, and shows error dialog.
  - `globalPlugins/googleTtsForNvda/uiUtils.py:show_runtime_error_dialog(message, delayMs=150)` — Centralized error dialog presentation using `gui.mainFrame`, `wx.ICON_ERROR`, and delayed dispatch.

- **CDP Connection, Dispatcher & Harness Readiness**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.ensure_connection(cancelEvent)` — Serialized connection setup via `_connectionLock` with candidate fallback loop and cancellation checks.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:CdpClient.request(method, params, cancelEvent)` — Thread-safe CDP request/response over WebSocket; raises `CdpCancelled` when cancelled.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:CdpDispatcher` — Routes CDP binding events; propagates runtime errors back to owning request to fail fast without caching bad audio.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:WasmTtsEngineBridge.runtime_busy`, `_set_runtime_busy()`, `cancel_current()`, `send_fast_stop()` — Thread-safe runtime busy tracking and cooperative cancellation signaling.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:WasmTtsEngineBridge.enable_cdp_domains(cancelEvent)`, `wait_until_ready(cancelEvent)` — Enables CDP domains and polls harness readiness with transient error retries.

- **Runtime Health, Memory Throttling & Recycling**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_process_tree_memory_usage(rootPid)` — Collects process-tree private bytes and working set via Win32 Toolhelp32 snapshot.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_elevate_chrome_priority(processId)` — Elevates process tree priority to `ABOVE_NORMAL_PRIORITY_CLASS` and explicitly disables Windows EcoQoS (Power Throttling) via `SetProcessInformation`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:BrowserProcessManager.browser_memory_usage()` — Queries memory usage for running browser process tree.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:ChromeTtsBridge.maybe_recycle_runtime()` — Recycles browser runtime when memory thresholds are exceeded or unrecoverable errors occur.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py:SynthDriver._maybe_recycle_bridge_after_request()` — Post-request idle recycle scheduler.

---

## 4. CDP Communication, WebSocket & Dependency Isolation Code Map

- **Bundled Dependency Isolation**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` and `googleTtsForNvda/synthDrivers/googleTtsForNvda/websocketClientRepo/__init__.py`: Package-relative `websocket` imports isolated from any external Python packages or NVDA core libraries.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/websocketClientRepo/websocket/_abnf.py`: Low-level WebSocket framing, opcode definitions, and frame masking (`native_byteorder`, `_mask()`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/websocketClientRepo/websocket/_http.py`: HTTP proxy connection handling and tunnel negotiation (`HAVE_PYTHON_SOCKS`, `ProxyError`, `ProxyTimeoutError`, `ProxyConnectionError`, `ProxyType`, `_start_proxied_socket()`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/websocketClientRepo/websocket/_utils.py`: Bundled UTF-8 validation utilities ensuring safe frame text decoding (`_create_bundled_utf8_validator()`, `validate_utf8()`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_profiles.py`: Package-relative `LANGUAGE_SCRIPT_RANGES` import from pre-generated `unicode_data.py`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: Package-relative `SENTENCE_TERMINAL_CODEPOINTS` import from pre-generated `unicode_data.py`.

- **Cross-Origin Isolation HTTP Headers**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py:_BridgeRequestHandler`: Sends `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`, and `Cross-Origin-Resource-Policy: same-origin` required by Chromium to enable `SharedArrayBuffer` for multi-threaded WebAssembly execution.

---

## 5. Audio Math, Loudness & SeaNet Processing Code Map

- **Pure Audio Math & Speech Options**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/audio_math.py`: Pure audio transformation functions without NVDA runtime dependencies:
    - Rate and pitch conversion: `rate_to_chrome()` maps NVDA rate (0-100) to browser rate factor (0.5x to 10.0x); `pitch_to_chrome()` maps NVDA pitch (0-100) to browser pitch factor (0.5x to 2.0x).
    - SeaNet rate protection: `uses_protected_engine_rate()` checks if requested rate exceeds `PROTECTED_ENGINE_RATE` (3.0x). When exceeded, the engine rate is capped at 3.0x and post-processing tempo stretching is applied to prevent acoustic distortion.
    - Rate limits: `MIN_ARTIFICIAL_RATE` (0.5x), `MAX_ARTIFICIAL_RATE` (10.0x).
    - Gain makeup: `OUTPUT_GAIN_MAKEUP` (1.35x fixed gain applied to float32 buffers).
    - Options builder: `build_speech_options()` aggregates rate, pitch, volume, speaker ID, and tempo/pitch factors into a normalized dictionary payload.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: Bridge options adapters (`_speech_options()`, `_uses_protected_engine_rate()`, `_rate_to_chrome()`, `_pitch_to_chrome()`, `_short_cache_key()`).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `WasmTtsEngineBridge.speak()` transmits options payload to browser bridge.

- **Browser Harness Audio Processing Pipeline (`bridgeHarness.js`)**:
  - Engine lifecycle & readiness: `isTtsEngineInstance()`, `getTtsEngine()`, `ensureEngineInitialized()`, `ensureLanguageReady()`, `stopActiveSynthesis()`, `googleTtsForNvdaPreload()`, and `googleTtsForNvdaSpeak()`.
  - Audio packetization & CDP transport: `buffersToPcmBase64()`, `appendSamples()`, `audioPacketSampleTarget()`, `queueAudioPacket()`, and `flushAudioQueue()` bundle raw audio into base64 PCM chunks sent via CDP binding `googleTtsForNvdaBridge`.
  - Fixed output gain & clipping limiter: `outputGainFromPayload()`, `limitSample()`, and `buffersToPcmBase64()` apply fixed 1.35x gain makeup with soft-clipping limiter to avoid digital distortion.
  - SeaNet tempo & pitch stretching: `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `processPitchSamples()`, `processTempoSamples()`, `queueTempoInput()`, `flushAudioProcessors()`, `flushTempoProcessor()`, `queueAudio()`, and `finishSegmentAudio()` stretch audio buffers beyond WASM engine rate caps while preserving pitch and intelligibility.

---

## 6. Speech Processing, Latency Segmentation & Pause Shortening Code Map

- **Latency Text Segmentation (`TextSegmenter`)**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: Progressive segmentation splitting text into latency-optimized chunks so audio synthesis starts immediately while subsequent chunks are processed in the background:
    - Text sanitization: `sanitize_speech_text()`, `_SPEECH_SANITIZE_TABLE` inherits whitespace mapping directly from the official Unicode `NORMALIZATION_TABLE` in `unicode_data.py`, converting all non-standard Unicode whitespace variants and BMP PUA codepoints to standard ASCII spaces with 1:1 character index preservation.
    - Segmentation logic: `split_text_for_latency()`, `iter_text_segments_for_latency()`, `iter_indexed_text_segments()`, `spoken_bridge_segments()`.
    - Combining marks & emoji preservation: `_extend_cut_over_combining_marks()` extends segment cuts over Unicode combining marks (`category M*`), Zero-Width Joiners (`ZWJ` `0x200D`), variation selectors (`0xFE0E`, `0xFE0F`), keycap enclosing marks (`0x20E3`), skin tone modifiers (`0x1F3FB`-`0x1F3FF`), and regional indicator flag pairs (`0x1F1E6`-`0x1F1FF`) to guarantee emoji and grapheme cluster integrity across chunk splits.
    - Fast first segment: cuts the initial phrase quickly (`FAST_FIRST_SEGMENT_MIN_CHARS`, `FAST_FIRST_SEGMENT_MAX_CHARS`, `FAST_FIRST_SEGMENT_TRIGGER_CHARS`, `FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS`) to achieve sub-100ms time-to-speech.
    - Soft phrase cuts: splits at clause boundaries such as commas, semicolons, colons, dashes (`SOFT_PHRASE_SEGMENT_MIN_CHARS`, `SOFT_PHRASE_SEGMENT_MAX_CHARS`, `_find_soft_phrase_cut()`, `_is_contextual_soft_phrase_cut()`).
    - Sentence boundaries: splits at full stops and sentence terminators (`find_sentence_splits()`, `is_sentence_terminator_character()`, `COMMON_ABBREVIATIONS`).
    - Script boundary handling: handles scripts without space separators such as CJK, Thai, Khmer via `NO_SPACE_SCRIPT_PROFILES` reusing official `SCRIPT_RANGES` from `unicode_data.py` (`_find_no_space_script_cut()`, `_is_no_space_script_character()`, `NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS`, `NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO`).
    - Forced latency limits: prevents runaway long utterances without natural breaks (`_iter_forced_latency_segments()`, `FORCED_SEGMENT_MIN_CHARS`, `FORCED_SEGMENT_HARD_MAX_CHARS`).
    - Token analysis: `looks_like_url_token()`, `should_pause_after_segment()`, `_period_is_numeric_separator()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` segmentation integration: `_iter_speech_chunks()`, `_split_text_for_latency()`, `_sanitize_speech_text()`, `_spoken_bridge_segments()`, `_should_pause_after_segment()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: Boundary context and smoothing between consecutive chunks (`hasPreviousSegment`, `hasBoundaryContext`, `smoothSegmentBoundaries`, `finishSegmentAudio()`).

- **Pause Shortening (`PcmSilenceShortener`)**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: Real-time streaming PCM silence trimming:
    - Pause mode policies: `PAUSE_MODE_DO_NOT_SHORTEN` (keep all silence), `PAUSE_MODE_SHORTEN_END_ONLY` (trim utterance end silence), `PAUSE_MODE_SHORTEN_ALL` (shorten inter-phrase and trailing pauses).
    - Thresholds: `SHORTENED_SILENCE_KEEP_MS` (inter-sentence silence limit), `SHORTENED_ALL_PAUSES_KEEP_MS` (inter-clause silence limit).
    - Stream processor: `PcmSilenceShortener` processes 16-bit PCM in fixed block sizes (`_blockSizeBytes`), detects silent blocks (`_hold_silence()`), releases audible blocks (`_release_held_silence()`), and handles boundaries via `feed()`, `flush_boundary()`, `finish()`.
    - Helper functions: `create_pcm_silence_shortener()`, `pcm_bytes_for_milliseconds()`, `align_pcm_bytes()`, `pcm_has_audible_sample()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` timing & rate factor tables:
    - Base pause constants: `_NORMAL_SENTENCE_BREAK_MS`, `_SHORTENED_SENTENCE_BREAK_MS`, `_END_OF_UTTERANCE_PAUSE_MS`.
    - Dynamic pause scaling: interpolates sentence breaks according to user speech rate (`_BREAK_RATE_TABLE`, `_interpolate_rate_factor()`, `_break_rate_factor()`, `_end_of_utterance_rate_factor()`).
    - Settings accessors: `_PAUSE_MODE_SETTING`, `_pauseModes`, `_get_availablePausemodes()`, `_get_pauseMode()`, `_set_pauseMode()`.

- **Lead Buffering & Click Elimination (`PcmLeadBuffer`)**:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `PcmLeadBuffer` with `LIVE_MULTI_SEGMENT_LEAD_MS` holds a small audio lead on multi-segment speech to eliminate speaker pop/click artifacts before audio reaches `nvwave.WavePlayer`.

- **Speech Pipeline Performance Optimizations**:
  - Early cancellation: `_speak_text()` checks `cancelEvent.is_set()` before dispatching segments to the bridge.
  - Chunk flushing: `_iter_speech_chunks()` inner loop flushes audio buffers when accumulated character threshold (`_FLUSH_GROUP_CHARS_THRESHOLD`) is reached.
  - Standby pre-warming skip: `_StandbyRuntimeManager._run_refresh()` skips pre-warming when the bridge is already warm and ready.

---

## 7. Voice Catalog & Voice Store Code Map

- **Voice Catalog & Models (`catalog.py`)**:
  - Engine paths & constants: `BASE_DIR`, `ENGINE_VERSION`, `ENGINE_ROOT`, `ENGINE_DIR`, `CATALOG_PATH`, `REQUIRED_ENGINE_FILES`, and `UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS`.
  - Integrity validation: `inspect_engine_library()` verifies all required WASM files and manifests exist on disk; `EngineLibraryError` and `engine_library_error_message()` format descriptive user errors.
  - Data models:
    - `VoicePackage`: represents a downloadable voice package archive (package ID, target language, download URL, MD5/SHA256 hashes, file size, supported engine versions).
    - `Speaker`: represents a specific speaker voice within a package (voice ID, speaker name, gender, locale).
  - Catalog management (`VoiceCatalog`):
    - Loading & deserialization: `VoiceCatalog.load()`, `VoiceCatalog.from_json()`.
    - Voice resolution: `package_for_voice()`, `speaker_for_voice()`, `packages_by_language`, `all_languages`, `speakers_for_language()`.
    - Runtime catalog export: `VoiceCatalog.to_runtime_json()` generates dynamic JSON served to the browser bridge.

- **Package Management & Verification Cache (`voice_store.py`)**:
  - Runtime paths: `data_root()` returns persistent add-on data root (`<configPath>/googleTtsForNvda/`); `voice_dir()` returns `<configPath>/googleTtsForNvda/voices/`; `package_file()` resolves `.zvoice` package paths.
  - Download & extraction: `download_package()` streams package archives with chunked reads (`DOWNLOAD_CHUNK_SIZE`), enforces size limits (`VOICE_PACKAGE_MAX_BYTES`), and verifies SHA-256 integrity.
  - Package operations: `remove_package()` deletes package file and associated cache entries; `copy_existing_package()` imports external voice files.
  - Verification cache:
    - Persistent disk cache: `verified_voices.json` (`_VERIFICATION_CACHE_FILE`, `_VERIFICATION_CACHE_VERSION`) stores file size, modification timestamp, and SHA-256 hash.
    - Cache operations: `_load_persistent_verification_cache()`, `_save_persistent_verification_cache()`, `_remember_verified_package()`, `_forget_verified_package()`.
    - Fast startup scans: `is_package_installed()`, `physically_installed_packages()`, and `usable_installed_packages()` check disk presence and cached hashes without re-hashing voice files on every startup.

---

## 8. Voice Preload & Session Isolation Code Map

- **Voice Preloading & Warm-up**:
  - Driver-side preload: `SynthDriver._warm_current_voice_async()` launches background pre-warming for the active voice and configured auto-language candidate voices when idle.
  - Candidate planning: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_voice_ids_for_voice()`, `_voice_id_for_package()`, and `_warmup_options_for_voice_ids()`.
  - Bridge entry points: `ChromeTtsBridge.preload_voice()` and `WasmTtsEngineBridge.preload_voice()` evaluate `window.googleTtsForNvdaPreload(...)` in the browser harness to instantiate WASM voice memory.

- **Harness Session Isolation & Audio Fencing (`bridgeHarness.js`)**:
  - Monotonic session tokens: `currentSessionToken`, `beginSession()`, `isCurrentSession()` ensure every speech utterance has a unique token.
  - Stale audio fencing: `emit()`, `queueAudioPacket()`, `queueProcessedAudio()`, `finishSegmentAudio()`, and `stopActiveSynthesis()` check session token before emitting audio chunks, guaranteeing background preloads or interrupted utterances never leak audio into active speech.

---

## 9. Standby Readiness & Directory Watcher Code Map

- **Standby Browser Runtime Manager (`standby.py`)**:
  - Configuration gate: `keep_browser_runtime_ready_enabled()` checks whether standby mode is enabled in `[googleTtsForNvda]` config.
  - Lifecycle manager (`_StandbyRuntimeManager`):
    - Background warm instance: keeps a headless Chromium bridge running in the background while another synthesizer is active.
    - Bridge claiming: `claim_bridge()` transfers the warm bridge instantly when switching to Google TTS.
    - Bridge releasing: `release_synth_bridge()` returns or terminates the bridge when switching away.
    - Lifecycle controls: `refresh_async()`, `_run_refresh()`, and `terminate()`.

- **Directory Change Watcher (`watcher.py`)**:
  - Win32 filesystem watcher (`DirectoryChangeWatcher`):
    - Uses Win32 `FindFirstChangeNotificationW` and `WaitForMultipleObjects` to monitor `<configPath>/googleTtsForNvda/voices/` for file changes.
    - Triggers debounced callbacks when voice packages are added, updated, or removed externally.
    - Clean thread shutdown: `stop(timeout=5.0)` performs bounded thread joins with timeout warning logs.

---

## 10. Automatic Language Detection & Profiles Code Map

- **CLD2 Language Detector (`language_detector.py`)**:
  - Architecture-specific dynamic loading: `_Cld2Detector._load_library()` loads `cld2_x64.dll` (64-bit NVDA) or `cld2_x86.dll` (32-bit NVDA) from vendored `cld2/` folder.
  - Language detection: `detect_language(text, candidateLanguages, preferredLanguage=None)` runs Compact Language Detector 2 on text, returning `DetectionResult` with primary language and confidence percentage (`_MIN_RELIABLE_PERCENT`). Normalizes mathematical alphanumerics, routes standalone numbers (`is_number_token()`) and symbols/emojis (`is_symbol_or_emoji_token()`) strictly to `preferredLanguage`, performs candidate prior recovery for mispredicted short words, falls back to official Unicode script ranges (`language_script_signal()`), and analyzes Latin diacritics (`has_vietnamese_diacritics()`).
  - Aliases & redirects: `_LANGUAGE_ALIASES`, `_CHINESE_LANGUAGE_ROOTS`, and `_LANGUAGE_REDIRECTS` normalize language codes (e.g., mapping Cantonese/Taiwanese variants or dialect redirects).
  - Language matching: `language_matches()`, `language_match_keys()`, `_language_family()`, `_language_root()`, `_candidate_for_language()`.

- **Unified Language Profiles & Mixed-Text Segmentation (`language_profiles.py`)**:
  - Mathematical & enclosed normalization: `normalize_mathematical_alphanumeric(text: str) -> str` normalizes Unicode 17.0 mathematical bold, italic, sans-serif, fraktur, double-struck alphanumerics, letterlike constants, phonetic/enclosed small capitals, parenthesized letters, negative circled/squared letters, and CLDR supplemental character substitutes via pre-generated `NORMALIZATION_TABLE`.
  - Token classification & number clustering: `is_number_token()`, `is_symbol_or_emoji_token()`, `classify_token()` classifies numbers, all 64 official UCD Category Sc currency symbols, active ISO 4217 currencies, international SI units, time formats (24h, 12h AM/PM, colloquial), dimensions, and math operators, ensuring numbers clustered with currencies, units, and time stay intact and route to `preferredLanguage`.
  - Mixed-text segmentation: `segment_mixed_text(text, candidateLanguages, preferredLanguage)` segments sentences with mixed scripts into language-tagged chunks, routing non-Latin scripts according to user candidate language priority without hardcoded bias via dynamic `_SCRIPT_TO_CANDIDATE_ROOTS`, resolving Latin text between candidates, attaching neutral punctuation, coalescing adjacent chunks, and preserving intact UTS #51 emoji sequences (keycaps, flags, skin tone modifiers, ZWJ sequences).
  - Script analysis: `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `_SCRIPT_TO_CANDIDATE_ROOTS` imported from `unicode_data.py`; `script_ranges_for_language_root()`, `token_has_character_in_ranges()`, `language_script_signal()`, `has_vietnamese_diacritics()`.

- **Synth-Side Language Selection (`synthDrivers/googleTtsForNvda/__init__.py`)**:
  - Auto-language resolution: `_detect_auto_language()`, `_auto_detect_profile_for_text()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_language_token_signal()`.
  - Mathematical alphanumeric normalization: normalized in `_speak_text()` and `_speech_options()`.
  - Voice selection per language: `_voice_for_language()`, `_voice_matches_language()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`.
  - Language change command filtering: `_speech_chunks()` ignores `LangChangeCommand` objects when automatic language detection is disabled.

- **NVDA Speech Filter & Spelling Overlays (`globalPlugins/googleTtsForNvda/__init__.py`)**:
  - Speech sequence filtering: `_register_auto_language_speech_filter()`, `_filter_auto_language_speech_sequence()`: passes `speechSequence` through untouched when automatic language profiles are disabled so NVDA can handle language reporting and symbol dictionaries according to NVDA preferences; when enabled, invokes `language_profiles.segment_mixed_text()` when multiple candidate languages are configured to split mixed sentences and inject `_google_lang_change_command` at sub-sentence boundaries; normalizes mathematical alphanumerics on strings passed through the filter.
  - Per-language voice dictionaries: `_patch_auto_language_voice_dictionary()`, `process_text_with_auto_voice_dictionary()` normalizes mathematical alphanumerics and applies per-language dictionary rules.
  - Spelling character context: `_auto_profile_character_settings_for_language()`, `get_spelling_speech_with_auto_profile()`, `should_use_spelling_functionality_with_auto_profile()` ensures spelling characters use localized speech rules.

- **Language Normalization & Display Names (`language_utils.py`)**:
  - Normalization: `normalize_language()`, `normalize_language_code()`, `normalize_language_key()`.
  - Locale resolution: `get_nvda_locale_for_language()`, `resolve_nvda_locale()`, `nvda_locale_exists()`, `SPECIAL_NVDA_LOCALES`.
  - Localized names: `get_language_display_name()`, `_language_display_candidates()`.

- **Settings Ring & Settings UI Integration**:
  - Settings ring notification: `ReadOnlyTextDriverSetting`, `_auto_language_notice_message()`, `_get_notice()`, `_set_notice()`.
  - Settings panel: `GoogleTtsSettingsPanel` controls for candidate languages, preferred language, and per-language voice profile bindings (`_refresh_auto_language_controls()`, `_save_auto_language_settings()`).

---

## 11. Volatile RAM Speech Cache Code Map

- **RAM Audio Cache Lifecycle (`synthDrivers/googleTtsForNvda/__init__.py`)**:
  - Cache operations: `_get_cached_audio()`, `_put_cached_audio()`, `_clear_short_audio_cache()`.
  - Limits and bounds: `_SHORT_CACHE_MAX_ITEMS` (maximum 256 entries), `_SHORT_CACHE_MAX_BYTES` (maximum 16 MB memory cap).
  - Cache invalidation: Cleared on synth termination, voice change, rate/pitch change, or browser runtime recycling (`_maybe_recycle_bridge_after_request()`).

- **Cache Keys & Cache Eligibility (`speech_processing.py`)**:
  - Key generators: `short_audio_cache_key()` hashes normalized text, voice ID, rate, pitch, and volume into a deterministic lookup key; `segment_audio_cache_key()` handles individual multi-segment chunks.
  - Eligibility criteria: `SHORT_CACHE_MAX_CHARS` (limits caching to utterances up to 64 characters), `SHORT_CACHE_MAX_HIDDEN_SEGMENTS`, `is_complete_speech_result()`.

---

## 12. Voice Manager UI Code Map

- **Voice Manager Dialog (`globalPlugins/googleTtsForNvda/voiceManager.py`)**:
  - wxPython dialog: `VoiceManagerDialog` provides a two-tab interface for managing voice packages:
    - Installed tab (`_build_installed_tab()`): lists locally installed voices, speaker count, disk size, and package status (`_installed_package_status()`).
    - Download tab (`_build_download_tab()`): lists available online voice packages with language filters, descriptions, and file sizes (`_download_package_status()`).
  - Language filtering & sorting: `_current_ui_language()`, `_language_sort_rules_for_current_ui()`, `_rule_based_visible_sort_key()`, `_populate_installed_list()`, `_populate_download_list()`.
  - Dependency analysis: `_with_required_download_dependencies()`, `_with_installed_dependents()`, `_missing_dependency_for_package()` resolves shared package dependencies so required base voices are downloaded together.
  - Background download worker: `on_download_selected()`, `_run_worker()`, `set_status()`, `_warm_current_google_synth_voice()`, `_refresh_standby_google_synth_runtime()` downloads packages asynchronously with progress status updates and post-download SHA-256 verification.
  - Safe removal & config fallback: `on_remove_selected()`, `_remove_packages()`, `_removes_all_usable_voices()`, `_usable_packages_after_removal()`, `_confirm_remove_last_active_voice()`, `_confirm_remove_last_inactive_voice()`, `_reset_configured_voice_if_removed()`, `_reset_auto_language_profile_variants_if_removed()`, `_apply_reset_voice_to_current_synth()` prevents removing the last installed voice and automatically resets NVDA voice configuration if active voice is uninstalled.
  - Open data folder: `on_open_folder()` opens `<configPath>/googleTtsForNvda/voices/` in Windows Explorer.

---

## 13. Add-on Settings, Controls & Updater Code Map

- **Settings Panel & Grouped Controls (`settings.py`)**:
  - `GoogleTtsSettingsPanel`: NVDA GUI settings panel under Voice / Google TTS category:
    - Grouped layout: `_SettingsGroup`, `addLabeledControl()`, `addCheckBox()`, `addButton()`, `_refresh_settings_layout()`.
    - Browser runtime selector: choose between Auto, Chrome, Edge, or Brave.
    - Standby runtime toggle: toggle background browser instance for zero-latency synth switching.
    - Pause mode selector: choose between Do not shorten, Shorten end only, or Shorten all.
    - Auto-language detection controls: preferred language combo, candidate language checkboxes, per-language voice profile pickers.
    - Updater controls: automatic update check checkbox and manual Check Now button.

- **Shared UI Utilities (`uiUtils.py`)**:
  - Accessible controls: `bind_read_only_text_focus_announcement()`, `resize_read_only_text_for_content()`.
  - Display formatting: `format_size_mb()`, `format_size_auto()`, `_from_dip()` (DIP screen scaling).
  - Dialog helpers: `open_synthesizer_dialog()`, `show_runtime_error_dialog()`.

- **Internal Add-on Updater (`updater.py` & `updateGui.py`)**:
  - Updater core (`updater.py`):
    - Update checking: `check_for_update()` fetches `stable.json` manifest (`fetch_update_manifest()`), parses version and hotfix metadata (`_parse_update_info()`, `UpdateInfo`, `UpdateCheckResult`).
    - Package download: `download_update()` downloads `.nvda-addon` to `%TEMP%/googleTtsForNvda-updates/` with SHA-256 integrity validation.
  - Updater UI (`updateGui.py`):
    - Dialogs: `_UpdateAvailableDialog` displays release notes and download options; `_UpdateDownloadDialog` displays download progress gauge.
    - Controllers: `start_manual_update_check()`, `start_automatic_update_check()`, `_UpdateCheckController`.
  - Release manifest builder (`make_update_manifest.py`):
    - Generates `stable.json` manifest for GitHub releases (`build_update_manifest()`).
  - Full update lifecycle, versioning conventions, and release workflows are detailed in `UPDATER_RELEASE_GUIDE.md`.

---

## 14. Developer Tools & Build Infrastructure Code Map

- **Voice Catalog Generator (`generate_voices_json.py`)**:
  - Upstream catalog scraping: fetches and extracts ChromeOS Google TTS textproto voice definitions (`extract_speakers_from_textproto()`, `fetch_new_package_speakers()`).
  - Catalog alignment: verifies engine version alignment (`check_engine_version_alignment()`, `latest_engine_voices_json()`) and outputs formatted `voices.json`.
  - Formatting helpers: `get_native_language_name()`, `format_speaker_name()`, `NATIVE_LANGUAGE_NAMES`.

- **Unicode Data Generator (`generate_unicode_data.py`)**:
  - Parsing UCD & CLDR: fetches and parses Unicode Character Database (UCD) 17.0 (`Scripts.txt`, `PropertyValueAliases.txt`, `PropList.txt`, `UnicodeData.txt`) and CLDR 48.2 (`likelySubtags.xml`, `characters.xml`) via `_parse_ucd_records()`, `_supported_language_scripts()`, `_likely_scripts()`.
  - Normalization table builder: `_build_normalization_table()` extracts mathematical alphanumerics, enclosed characters, small capitals, squared words, and CLDR supplemental character substitutes into `NORMALIZATION_TABLE`.
  - Code generation: generates `synthDrivers/googleTtsForNvda/unicode_data.py` containing `UNICODE_VERSION`, `CLDR_VERSION`, `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `LANGUAGE_SCRIPT_RANGES`, `SENTENCE_TERMINAL_CODEPOINTS`, and `NORMALIZATION_TABLE`.
  - Engine version policy: fail-closed validation checking newest bundled engine against `catalog.py:ENGINE_VERSION` (`check_engine_version_alignment()`, `_configured_voices_json()`).

- **Translation & Localization Tool (`build_i18n.py`)**:
  - POT/PO extraction & merge: `_translatable_source_messages()`, `_write_pot()`, `_run_msgmerge()`, `_verify_merged_po()`.
  - Catalog validation: `_check_catalog()`, `_check_format_interpolations()`, `_check_html_tag_interpolations()`, `_check_po_syntax_with_msgfmt()`.
  - Localized documentation: extracts segments from HTML docs (`_extract_doc_segments()`), builds localized HTML from translated PO files (`_build_doc_for_language()`, `_markdown_to_doc_html()`).
  - Compilation: compiles `.po` into binary `.mo` (`_compile_mo_file()`) and generates translated `manifest.ini` files.
  - Step-by-step translation and Crowdin workflows are detailed in `TRANSLATING.md`.

- **Packaging Scripts (`build.bat` & `build.sh`)**:
  - Clean build pipeline:
    1. Clean build artifacts and stale caches (`__pycache__`, `.nvda-addon`).
    2. Scan repository for git merge conflict markers.
    3. Syntax verification (`python -m py_compile`).
    4. Compile translation catalogs (`build_i18n.py --all-languages`).
    5. Compile translated add-on manifests.
    6. Build localized HTML documentation (`build_i18n.py --build-docs --all-languages`).
    7. Archive final distribution package (`googleTtsForNvda-<version>.nvda-addon`).
  - Full packaging instructions, release guidelines, and PR checks are documented in `CONTRIBUTING.md`.

- **Static Type Checking & Code Formatting**:
  - `mypy.ini`: Mypy static type checker configuration targeting Python 3.11.
  - `ruff.toml`: Ruff linter and formatter configuration enforcing 120-character line length, Python 3.11 target, exclusions for vendored libraries (`websocketClientRepo`, `WasmTtsEngine`, `cld2`, `web`), and linter rule sets (`E`, `F`, `W`, `I`, `UP`, `B`, `SIM`).

- **Packaged Licenses**:
  - Repository root GPL-2.0 license: `LICENSE`.
  - Packaged add-on GPL-2.0 license: `googleTtsForNvda/LICENSE`.
  - Chromium WASM TTS BSD-3-Clause license: `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/LICENSE`.
  - Eigen Apache-2.0 license: `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/EIGEN_LICENSE`.
  - Compact Language Detector 2 Apache-2.0 license: `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/LICENSE.txt`.
  - Websocket-client Apache-2.0 license: `googleTtsForNvda/synthDrivers/googleTtsForNvda/websocketClientRepo/LICENSE`.

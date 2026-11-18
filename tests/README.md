# Standalone Regression Tests

Google TTS For NVDA includes a standalone test suite comprising **809 unit tests** in **137 test classes** across **19 test modules**, supplemented by shared test helpers (`test_support.py`), a multilingual segmentation corpus (`segmentation_corpus.json`), static NVDA API contract verification (`check_nvda_api_contracts.py`), and a manual release checklist (`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`).

Unit tests run **without importing or installing NVDA**: isolated modules load via `test_support.py`, NVDA/wx integration hooks run under lightweight shims in `test_nvda_integration_shims.py`, and `bridgeHarness.js` executes under Node.js `node:vm` in `test_bridge_harness.py`.

---

## Running the Tests

Run the complete test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

Run an individual module, test class, or test method:

```powershell
python -m unittest tests.test_speech_processing -v
python -m unittest tests.test_nvda_integration_shims.RealNvdaIntegrationShimTests -v
python -m unittest tests.test_bridge_concurrency.EnsureConnectionCancellationTests.test_cancelled_connection_does_not_terminate_process_manager -v
```

---

## Test Suite Index

| Test Module | Tests | Classes | Coverage Summary |
| :--- | :---: | :---: | :--- |
| `test_audio_math.py` | 11 | 1 | `rate_to_chrome`, `pitch_to_chrome`, `uses_protected_engine_rate` (SeaNet & `lemonbalm`, `PROTECTED_ENGINE_RATE`), `build_speech_options` soft-knee boost & rate/pitch decoupling, input coercion, and gain makeup. |
| `test_bridge_concurrency.py` | 25 | 6 | `_connectionLock` fallback scope, locked engine capture, `_runtimeBusyLock` (including `preload_voice`), `CdpDispatcher` disconnect wakeup, cancellation cleanup, sample-rate validation, idle reconnect retry, and recycle epoch tracking. |
| `test_bridge_harness.py` | 17 | 1 | Node.js `vm` runtime tests for `web/bridgeHarness.js`: engine discovery, control-sequence cancellation, `MessageChannel` zero-delay scheduling, WSOLA tempo/pitch decoupling, Nyquist anti-aliasing, on-demand install coalescing, and audio drain/errors. |
| `test_bridge_helpers.py` | 64 | 24 | Runtime discovery/fallback order, Edge WebView2 checks, hidden startup & priority elevation, profile-lock & cancel handling, `_safe_join`, transient/recycle classification, memory counters, CDP error formatting, and `ensure_config_spec`. |
| `test_build_i18n.py` | 40 | 2 | End-to-end POT extraction, PO creation/merge/rollback, MO compilation, manifest sync, interactive menu zero navigation/exit, and Markdown/HTML documentation build pipelines. |
| `test_build_i18n_helpers.py` | 231 | 26 | PO parsing/escaping/syntax fallback, fuzzy & obsolete entries, format/HTML tag & math checks, Markdown↔HTML conversion (tables, definition lists, entities), RTL rendering, and CLI/interactive recovery. |
| `test_dependency_isolation.py` | 10 | 1 | `websocketClientRepo` isolation, package-relative internal imports, pure-module zero-NVDA-import guard, `globalPlugins` `speech`/`sayAll` guards, and CLD2/browser/WASM asset anchoring. |
| `test_generate_voices_json_helpers.py` | 28 | 8 | Version sorting, bundled engine discovery, `voices.json` selection, catalog/engine version alignment, `dependentVoiceId` resolution, and CLI argument parsing. |
| `test_language_profiles.py` | 65 | 4 | BCP 47 & NVDA locale mapping, `redirect_language`, aliases, `NORMALIZATION_TABLE` & NFC composition (preserving `U+037E`/`U+0387`), currencies, units, numbers, single-char & spelling routing, `_CLAUSE_BREAK_PUNCTUATION`, URLs/domains, Latin exemplars, Han disambiguation, and emoji preservation. |
| `test_nvda_integration_shims.py` | 30 | 1 | Execution tests under NVDA/wx shims for real `synthDrivers` and `globalPlugins` hooks: voice `.dic` loading/caching, auto-language speech/SayAll/spelling/character hooks, `setSynth` & Settings/Voice Manager UI guards, config compat, fatal fallback, pause modes, and updater popup flows. |
| `test_performance.py` | 18 | 6 | `_FLUSH_GROUP_CHARS_THRESHOLD`, `PcmLeadBuffer` (`LIVE_MULTI_SEGMENT_LEAD_MS`), `PAUSE_MODE_*` handling, adaptive JS packet sizing, and multilingual segmentation/PCM throughput benchmarks. |
| `test_segmentation_fuzz.py` | 11 | 2 | Property-based fuzzing across synthetic Unicode corpora (CJK, Thai, Khmer, emoji sequences, URLs, decimals, combining marks). |
| `test_speech_processing.py` | 56 | 9 | `PcmSilenceShortener` (`effective_chrome_rate`, `keep_silence_ms_for_rate`, hold caps), `PcmLeadBuffer`, `TextSegmenter` (`segmentation_corpus.json`, `needs_index_boundary_space`, `ends_with_pause_punctuation`, ZWJ/variation-selector cut guards), Unicode sentence terminators, abbreviation/URL/domain boundaries, and short audio cache keys. |
| `test_standby_concurrency.py` | 22 | 6 | Standby generation counter, cancel events, `claim_bridge`, `release_synth_bridge`, `terminate`, and lock-free catalog refresh & watcher shutdown. |
| `test_support.py` | — | — | Shared `load_module_from_path` / `load_driver_module` / `load_fresh_driver_module` loaders, `FakeCdpClient`, `FakeEngine`, `FakeProcessManager`, `FakeHttpResponse`, `make_fake_bridge`, `write_test_engine`, `captured_prints`, `temporary_doc_tree`, `pcm_bytes`, `pcm_samples`. |
| `test_synth_driver_helpers.py` | 18 | 4 | Pure rate-factor helpers (`interpolate_rate_factor`, `break_rate_factor`, `end_of_utterance_rate_factor`) and `_LANGUAGE_WORD_RE` word matching. |
| `test_unicode_data.py` | 38 | 10 | UCD 17.0 / CLDR 48.2 record parsing, range merging, script aliases, combining/currency/emoji/unit/Latin-exemplar extractors, `NORMALIZATION_TABLE` generation, and delegated `voices.json` engine version gating. |
| `test_updater_security.py` | 79 | 15 | SHA-256 verification, HTTPS-only & redirect-downgrade blocking, path traversal/reparse-point/reserved-device-name guards, size bounds, manifest/release-notes parsing, and version/build comparison. |
| `test_voice_package_lifecycle.py` | 29 | 7 | Voice package download & HTTPS redirect guards, SHA-256 verification cache, copy/remove lifecycle, script-subtag & schema validation in `VoiceCatalog`, and bundled `lemonbalm` support/dependencies. |
| `test_watcher.py` | 17 | 4 | `DirectoryChangeWatcher` start/stop/rapid-restart lifecycle, callbacks, error recovery, and live filesystem integration. |
| **Total** | **809** | **137** | **Standalone regression test suite (plus shared helpers in `test_support.py`)** |

---

## NVDA Static API Contract Checks (`check_nvda_api_contracts.py`)

Verify static AST contracts against local NVDA source checkouts (auto-discovers sibling `../NVDA source code` and `../nvda` trees when omitted):

```powershell
python tests\check_nvda_api_contracts.py
python tests\check_nvda_api_contracts.py "C:\path\to\NVDA source code"
```

The checker inspects local NVDA source trees (e.g., `nvda`, `2024.4.2`, `2025.3.3`, `2026.3.1`) without importing NVDA across **11 categories**:
1. **Synth driver**: `SynthDriver` settings/methods, `VoiceInfo`, `LanguageInfo`, `synthIndexReached`, `synthDoneSpeaking`, `getSynth`, `setSynth`, `changeVoice`, `findAndSetNextSynth`, `SynthSettingsRing.updateSupportedSettings`, `DriverSetting` / `AutoSettings.isSupported` / `StringParameterInfo`, and `speech.commands`.
2. **Audio output**: `nvwave.WavePlayer` / `WinmmWavePlayer` / `WasapiWavePlayer`, `isInError` vs legacy `audioDeviceError`, and `outputDevice` config section (`audio` vs `speech`).
3. **Global plugin**: `GlobalPlugin`, `core.postNvdaStartup`, `gui.mainFrame` / `messageBox` / `VoiceDictionaryDialog`, `scriptHandler.script`, and `globalCommands` categories.
4. **Speech hooks and language profiles**: `speech.extensions.filter_speechSequence`, `speech.speech` (`speak`, `processText`, `getSpellingSpeech`), `speech.shortcutKeys.shouldUseSpellingFunctionality`, `speechDictHandler.loadVoiceDict`, voice dictionary storage (`_speechDictDefinitions` vs `dictionaries`), and language switching (`speech.languageHandling` + `speech.manager.shouldSwitchVoice` + `SynthDriver.languageIsSupported` vs `speech.speak` config flags).
5. **Settings category**: `SettingsPanel`, `MultiCategorySettingsDialog`, `AutoSettingsMixin` (`_getSettingMaker`, `_updateValueForControl`, `onDiscard`, `refreshGui`), `VoiceSettingsPanel.makeSettings`, `NVDASettingsDialog.categoryClasses`, `guiHelper`, and `SelectOnFocusSpinCtrl`.
6. **Voice Manager**: `DPIScaledDialog`, `languageHandler`, `SynthesizerSelectionDialog` / `SynthesizerDialog`, and `gui.addonGui.promptUserForRestart`.
7. **Updater**: `gui.addonGui.installAddon`, `ui.message`, and `addonHandler.initTranslation`.
8. **Browser runtime and standby**: `config.conf`, `globalVars.appArgs` / `appDir`, and `logHandler.log`.
9. **Shared NVDA state**: `languageHandler.getLanguage` and configuration section isolation.
10. **High-risk signatures**: `synthDriverHandler.setSynth` signature compatibility, `AutoSettingsMixin.refreshGui(self)`, `gui.message.MessageDialog` / `wxCallOnMain` vs legacy `messageBox`, and `config.isAppX`.
11. **Add-on compatibility guards**: AST verification of **53** extensible `*args, **kwargs` entry points across `synthDrivers/__init__.py`, `globalPlugins/__init__.py`, and `settings.py`, `HandlerRegistrar` parameter checks (`_filter_auto_language_speech_sequence`, `_on_post_startup`), `language_utils.call_compat` usage, and `SynthDriver` fallback guards (`_current_output_device`, `_audio_device_error`, `_feed_silence`, `_language_token_signal`).

---

## Interactive Manual Release Checklist

Before tagging a release, complete the live NVDA + Chromium verification procedures in [`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`](NVDA_CHROMIUM_MANUAL_CHECKLIST.md).

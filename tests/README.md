# Standalone Regression Tests

Google TTS For NVDA includes a standalone test suite comprising **736 unit tests** in **134 test classes** across **17 test modules**, supplemented by shared test helpers (`test_support.py`), a multilingual segmentation corpus (`segmentation_corpus.json`), static NVDA API contract verification (`check_nvda_api_contracts.py`), and an interactive manual release checklist (`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`).

All unit tests run **without importing NVDA** or requiring an active NVDA installation (via `test_support.load_driver_module()`, a standalone `importlib` loader in `test_updater_security.py`, and local manager fixtures in `test_standby_concurrency.py`).

---

## Running the Tests

Run the complete test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

Run an individual module, test class, or test method:

```powershell
python -m unittest tests.test_speech_processing -v
python -m unittest tests.test_synth_driver_helpers.ConfigCompatTests -v
python -m unittest tests.test_bridge_concurrency.EnsureConnectionCancellationTests.test_cancelled_connection_does_not_terminate_process_manager -v
```

---

## Test Suite Index

| Test Module | Tests | Classes | Coverage Summary |
| :--- | :---: | :---: | :--- |
| `test_audio_math.py` | 6 | 1 | `rate_to_chrome`, `pitch_to_chrome`, `uses_protected_engine_rate` (`PROTECTED_ENGINE_RATE`), `build_speech_options` payloads and gain makeup. |
| `test_bridge_concurrency.py` | 17 | 5 | `_connectionLock` scope across fallbacks, engine reference capture, `_runtimeBusyLock`, cancellation, pre-audio retry vs. post-audio recycling. |
| `test_bridge_helpers.py` | 58 | 21 | Runtime discovery & fallback order, Edge WebView2 checks, hidden process startup, priority elevation, profile-lock recovery, `_safe_join`, transient/recycle error classification, and CDP error formatting. |
| `test_build_i18n.py` | 35 | 2 | End-to-end POT extraction, PO merge, MO compilation, manifest sync, and Markdown/HTML documentation build pipelines. |
| `test_build_i18n_helpers.py` | 210 | 25 | PO parsing/escaping, fuzzy & obsolete entry handling, format/HTML interpolation checks, PO syntax fallback, Markdown↔HTML conversion, RTL rendering, and CLI/menu options. |
| `test_dependency_isolation.py` | 10 | 1 | `websocketClientRepo` isolation, package-relative imports, `speechModule`/`sayAllModule` guards in `globalPlugins/__init__.py`, zero-NVDA-import guard in pure modules, and asset/CLD2/WASM path anchoring. |
| `test_generate_voices_json_helpers.py` | 27 | 7 | Version sorting, bundled engine discovery, `voices.json` selection, catalog/engine version alignment, and CLI argument parsing. |
| `test_language_profiles.py` | 62 | 4 | BCP 47 & NVDA locale mapping, `redirect_language`, catalog aliases, `NORMALIZATION_TABLE` & NFC composition (preserving `U+037E`/`U+0387`), currencies, units, numbers, single-char & spelling routing to `preferredLanguage`, `_CLAUSE_BREAK_PUNCTUATION`, URLs/domains, Latin exemplars, Han disambiguation, and emoji preservation. |
| `test_performance.py` | 26 | 7 | `_FLUSH_GROUP_CHARS_THRESHOLD`, pre-synthesis cancellation coalescing, `PcmLeadBuffer` (80 ms), sentence/end-of-utterance break constants, adaptive JS packet sizing, and segmentation/PCM throughput benchmarks. |
| `test_segmentation_fuzz.py` | 13 | 2 | Property-based fuzzing across 200+ synthetic corpora (CJK, Thai, Khmer, emoji sequences, URLs, decimals, combining marks). |
| `test_speech_processing.py` | 53 | 9 | `PcmSilenceShortener` (`effective_chrome_rate`, `keep_silence_ms_for_rate`), `PcmLeadBuffer`, `TextSegmenter` (`segmentation_corpus.json`, `needs_index_boundary_space`, `ends_with_pause_punctuation`), Unicode sentence terminators, abbreviation/URL/domain boundaries, and short audio cache keys. |
| `test_standby_concurrency.py` | 22 | 5 | Standby generation counter, cancel events, `claim_bridge`, `release_synth_bridge`, and `terminate` lifecycle. |
| `test_support.py` | — | — | Isolated `load_driver_module` loader, `FakeCdpClient`, `FakeEngine`, `FakeProcessManager`, `make_fake_bridge()`, `pcm_bytes()`, `pcm_samples()`. |
| `test_synth_driver_helpers.py` | 36 | 9 | Rate factor interpolation (`_break_rate_factor`, `_end_of_utterance_rate_factor`), `_ensure_config_compat`, fatal fallback debounce, `_speech_loop` resilience, `isSupported("voice")`, `languageIsSupported`, voice `.dic` stat caching, `SayAllHandler.speechWithoutPausesInstance` hooks, and `pauseMode`/utterance-end resolution. |
| `test_unicode_data.py` | 43 | 10 | UCD 17.0 / CLDR 48.2 record parsing, range merging, script aliases, combining/currency/emoji ranges, active currencies, unit symbols, Latin exemplars, sentence terminals, and `NORMALIZATION_TABLE` generation. |
| `test_updater_security.py` | 77 | 15 | SHA-256 verification, HTTPS-only enforcement, path traversal & size bounds, manifest/release-notes parsing, and version/build comparison. |
| `test_voice_package_lifecycle.py` | 24 | 7 | Voice package download guards, SHA-256 verification cache, copy/remove lifecycle, and `VoiceCatalog` validation. |
| `test_watcher.py` | 17 | 4 | `DirectoryChangeWatcher` start/stop lifecycle, callbacks, error recovery, and live filesystem integration. |
| **Total** | **736** | **134** | **Standalone regression test suite** |

---

## NVDA Static API Contract Checks (`check_nvda_api_contracts.py`)

Verify static AST contracts against local NVDA source checkouts (defaults to sibling `../NVDA source code`):

```powershell
python tests\check_nvda_api_contracts.py
python tests\check_nvda_api_contracts.py "C:\path\to\NVDA source code"
```

The checker inspects NVDA source trees (`2024.4.2`, `2025.3.3`, `2026.2`) without importing NVDA across **11 categories**:
1. **Synth driver**: `SynthDriver` settings/methods, `VoiceInfo`, `LanguageInfo`, `synthIndexReached`, `synthDoneSpeaking`, `getSynth`, `setSynth`, `changeVoice`, `findAndSetNextSynth`, `SynthSettingsRing.updateSupportedSettings`, `DriverSetting` / `AutoSettings.isSupported` / `StringParameterInfo`, and `speech.commands`.
2. **Audio output**: `nvwave.WavePlayer` / `WinmmWavePlayer` / `WasapiWavePlayer`, `isInError` vs legacy `audioDeviceError`, and `outputDevice` config section (`audio` vs `speech`).
3. **Global plugin**: `GlobalPlugin`, `core.postNvdaStartup`, `gui.mainFrame` / `messageBox` / `VoiceDictionaryDialog`, `scriptHandler.script`, and `globalCommands` categories.
4. **Speech hooks and language profiles**: `speech.extensions.filter_speechSequence`, `speech.speech` (`speak`, `processText`, `getSpellingSpeech`), `speech.shortcutKeys.shouldUseSpellingFunctionality`, `speechDictHandler.loadVoiceDict`, voice dictionary storage (`_speechDictDefinitions` vs `dictionaries`), and language switching (`speech.languageHandling` + `speech.manager.shouldSwitchVoice` + `SynthDriver.languageIsSupported` vs `speech.speak` config flags).
5. **Settings category**: `SettingsPanel`, `MultiCategorySettingsDialog`, `AutoSettingsMixin` (`_getSettingMaker`, `_updateValueForControl`, `onDiscard`, `refreshGui`), `VoiceSettingsPanel.makeSettings`, `NVDASettingsDialog.categoryClasses`, `guiHelper`, and `SelectOnFocusSpinCtrl`.
6. **Voice Manager & Updater** (categories 6–7): `DPIScaledDialog`, `languageHandler`, `gui.addonGui` (`installAddon`, `promptUserForRestart`), `ui.message`, and `addonHandler.initTranslation`.
7. **Browser runtime, standby & shared NVDA state** (categories 8–9): `config.conf`, `globalVars.appArgs` / `appDir`, `logHandler.log`, and `languageHandler.getLanguage`.
8. **High-risk signatures & Add-on compatibility guards** (categories 10–11): `synthDriverHandler.setSynth` signature, `AutoSettingsMixin.refreshGui(self)`, AST verification of 27 extensible `*args, **kwargs` entry points across `synthDrivers/__init__.py`, `globalPlugins/__init__.py`, and `settings.py`, plus `SynthDriver._current_output_device`, `_audio_device_error`, and `_language_token_signal` fallback guards.

---

## Interactive Manual Release Checklist

Before tagging a release, complete the live NVDA + Chromium verification procedures in [`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`](NVDA_CHROMIUM_MANUAL_CHECKLIST.md).

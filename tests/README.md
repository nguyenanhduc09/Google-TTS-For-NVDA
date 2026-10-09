# Standalone Regression Tests

Google TTS For NVDA includes a standalone test suite comprising **733 unit tests** in **134 test classes** across **17 test modules**, supplemented by shared test helpers (`test_support.py`), a multilingual segmentation corpus (`segmentation_corpus.json`), static NVDA API contract verification (`check_nvda_api_contracts.py`), and an interactive manual release checklist (`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`).

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

| Test Module | Tests | Classes | Key Test Classes & Coverage |
| :--- | :---: | :---: | :--- |
| `test_audio_math.py` | 6 | 1 | `AudioMathTests`: `rate_to_chrome`, `pitch_to_chrome`, `uses_protected_engine_rate` (`PROTECTED_ENGINE_RATE`), `build_speech_options` payloads and gain makeup. |
| `test_bridge_concurrency.py` | 17 | 5 | `EnsureConnectionLockScopeTests`, `EngineCaptureUnderLockTests`, `RuntimeBusyLockTests`, `EnsureConnectionCancellationTests`, `RuntimeRecoveryTests`: lock scope across fallbacks, engine reference capture, busy state lock, cancellation, pre-audio retry vs post-audio recycling. |
| `test_bridge_helpers.py` | 58 | 21 | Runtime discovery (`BrowserRuntimeAvailableTests`, `BrowserExecutableAvailableTests`, `BrowserAvailabilityTests`, `EdgeWebview2BlocksTests`, `EffectiveBrowserRuntimeTests`, `RuntimeFallbackOrderTests`, `BrowserChoicesTests`, `BrowserRuntimeForPathTests`, `NormalizeBrowserRuntimeTests`, `BrowserRuntimeSnapshotTests`, `ConfiguredBrowserRuntimeTests`), process/priority (`BrowserProcessManagerSpawnTests`, `HiddenChromeStartupKwargsTests`, `ElevateChromePriorityTests`, `BrowserProfileInUseErrorTests`), and path/error guards (`SafeJoinTests`, `TransientErrorClassificationTests`, `RuntimeRecycleClassificationTests`, `RaiseIfCancelledTests`, `FriendlyCdpErrorTests`, `FormatBytesTests`). |
| `test_build_i18n.py` | 35 | 2 | `TranslationTemplateUpdateTests`, `I18nBuildAndCheckTests`: end-to-end POT extraction, PO merge, MO compilation, manifest sync, and documentation build pipelines. |
| `test_build_i18n_helpers.py` | 210 | 25 | PO & format validation (`ParsePoTests`, `PoEscapeTests`, `FuzzyPoTests`, `PurgeObsoleteTests`, `FormatSetTests`, `FormatInterpolationTests`, `PoSyntaxFallbackTests`, `MessagePreviewTests`, `ManifestValuesTests`, `CheckHtmlTagInterpolationsTests`) and docs/CLI/RTL (`DocHtmlToMarkdownTests`, `MarkdownToDocHtmlTests`, `ConvertHtmlAndMdFilesTests`, `FormatInlineMarkdownTests`, `SanitizeHrefTests`, `FallbackMarkdownTableTests`, `DefinitionListTests`, `DocumentationAndRtlTests`, `MainCliMarkdownTests`, `InteractiveOptionsMarkdownTests`, `DocBuildIntegrationTests`, `CheckDocLanguageMarkdownTests`, `DocListAndCheckImprovementsTests`, `NormalizeLanguageCodeTests`). |
| `test_dependency_isolation.py` | 10 | 1 | `BundledDependencyIsolationTests`: `websocketClientRepo` isolation, package-relative imports, `speechModule`/`sayAllModule` guards in `globalPlugins/__init__.py`, zero-NVDA-import guard in pure modules, asset/CLD2/WASM engine path anchoring. |
| `test_generate_voices_json_helpers.py` | 27 | 7 | `VersionSortKeyTests`, `NewestBundledEngineDirTests`, `LatestEngineVoicesJsonTests`, `CatalogEngineVersionTests`, `EngineVersionAlignmentTests`, `SelectEngineVoicesJsonTests`, `ParseArgsTests`. |
| `test_language_profiles.py` | 62 | 4 | `LanguageUtilsTests`, `LanguageRedirectTests`, `LanguageMatchesTests`, `LanguageProfilesTests`: BCP 47 normalization, NVDA locale mappings, `redirect_language`, symmetric catalog aliases, `NORMALIZATION_TABLE` & NFC composition (with singleton Greek punctuation `U+037E`/`U+0387` preservation), ISO 4217 currencies, CLDR units, localized decimal/thousands/percent numbers, single-character & spelling routing to `preferredLanguage`, multilingual `_CLAUSE_BREAK_PUNCTUATION` & spaced-separator clause breaks, `_URL_EMAIL_DOMAIN_PATTERN`, CLDR Latin exemplars, Han disambiguation, and UTS #51 emoji preservation. |
| `test_performance.py` | 26 | 7 | `SegmentFlushThresholdTests`, `SpeechCoalescingTests`, `PcmLeadBufferPerformanceTests`, `PauseModePerformanceTests`, `AdaptiveAudioPacketSizingTests`, `SegmentationPerformanceTests`, `PcmProcessingThroughputTests`. |
| `test_segmentation_fuzz.py` | 13 | 2 | `SegmentationFuzzTests`, `SentenceSplitFuzzTests`: property-based fuzzing across 200+ synthetic corpora (CJK, Thai, Khmer, emoji sequences, URLs, decimals, combining marks). |
| `test_speech_processing.py` | 51 | 9 | `PcmSilenceShortenerTests` (including custom `keepSilenceMs`), `PcmLeadBufferTests`, `TextSegmenterTests` (`segmentation_corpus.json` & `needs_index_boundary_space`), `ForcedLatencyCutLoggingTests`, `UnicodeSentenceTerminatorTests`, `SingleLetterAbbreviationGuardTests`, `CommonAbbreviationsIntegrityTests`, `UrlAndDomainBoundaryTests`, `ShortAudioCacheKeyTests`. |
| `test_standby_concurrency.py` | 22 | 5 | `GenerationCounterTests`, `CancelEventTests`, `ClaimBridgeTests`, `ReleaseSynthBridgeTests`, `TerminateTests`. |
| `test_support.py` | — | — | Shared test helpers: isolated `load_driver_module` loader (`_google_tts_for_nvda_test_driver`), `FakeCdpClient`, `FakeEngine`, `FakeProcessManager`, `make_fake_bridge()`, `pcm_bytes()`, `pcm_samples()`. |
| `test_synth_driver_helpers.py` | 35 | 9 | `InterpolateRateFactorTests`, `BreakRateFactorTests`, `EndOfUtteranceRateFactorTests`, `LanguageWordRegexTests`, `ConfigCompatTests`, `FatalFallbackTests`, `SpeechLoopResilienceTests`, `SynthDriverIsSupportedTests`, `AutoLanguageGlobalPluginHooksTests` (voice dictionary `.dic` caching & stat invalidation, `make_speak_with_auto_profile`, `SayAllHandler.speechWithoutPausesInstance.speak` & `speakWithoutPauses` patching, `BreakCommand` rate scaling, `WavePlayer.sync` before `synthIndexReached`, multi-segment spelling, `SynthDriver._get_language`, `languageIsSupported`). |
| `test_unicode_data.py` | 43 | 10 | `UnicodeDataTests`, `ParseUcdRecordsTests`, `MergeRangesTests`, `ScriptAliasesTests`, `FormatRangesTests`, `FormatCodepointsTests`, `BuildNormalizationTableTests`, `FormatNormalizationTableTests`, `RenderModuleTests`, `ConfiguredVoicesJsonTests` (UCD 17.0 / CLDR 48.2 script/combining/currency/emoji ranges, active currencies, unit symbols, Latin exemplars, sentence terminals, normalization table). |
| `test_updater_security.py` | 77 | 15 | `Sha256ValidationTests`, `HttpsOnlyTests`, `PathTraversalTests`, `SizeValidationTests`, `UpdateSizeLimitTests`, `ManifestParsingTests`, `RequiredStringTests`, `OptionalStringTests`, `StripManifestValueTests`, `LocaleKeyTests`, `ReleaseNotesTests`, `UpdateFileNameTests`, `VersionComparisonTests`, `VersionPartsTests`, `UpdateAvailabilityTests`. |
| `test_voice_package_lifecycle.py` | 24 | 7 | `PackageDownloadGuardTests`, `PackageVerificationTests`, `PackageCopyTests`, `VoicePackageLifecycleTests`, `PackageRemovalTests`, `CatalogLoadingTests`, `CatalogValidationTests`. |
| `test_watcher.py` | 17 | 4 | `DirectoryChangeWatcherLifecycleTests`, `DirectoryChangeWatcherCallbackTests`, `DirectoryChangeWatcherEdgeCaseTests`, `DirectoryChangeWatcherIntegrationTests`. |
| **Total** | **733** | **134** | **Standalone regression test suite** |

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

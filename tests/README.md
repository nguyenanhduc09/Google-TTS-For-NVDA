# Standalone Regression Tests

Google TTS For NVDA includes an exhaustive standalone test suite comprising **412 unit tests** across **20 test modules**, supplemented by shared test support infrastructure, a multilingual test corpus, static NVDA API contract verification, and an interactive manual release checklist.

All unit tests run **without importing NVDA** or requiring an active NVDA installation. Pure driver and plugin modules (`speech_processing.py`, `audio_math.py`, `voice_store.py`, `language_detector.py`, `language_utils.py`, `standby.py`, `watcher.py`, `updater.py`, etc.) are loaded directly in isolation via `test_support.py`, ensuring tests exercise the production implementation rather than mock AST copies.

---

## Running the Tests

To run the complete test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

To run an individual test module:

```powershell
python -m unittest tests.test_speech_processing -v
python -m unittest tests.test_bridge_concurrency -v
python -m unittest tests.test_synth_driver_helpers -v
```

To run a specific test class or test method:

```powershell
python -m unittest tests.test_synth_driver_helpers.ConfigCompatTests -v
python -m unittest tests.test_bridge_concurrency.EnsureConnectionCancellationTests.test_cancelled_connection_does_not_terminate_process_manager -v
```

---

## Test Suite Overview

| Test Module | Tests | Classes | Primary Coverage Area |
|---|---|---|---|
| [`test_audio_math.py`](#test_audio_mathpy) | 6 | 1 | Audio math, rate/pitch conversions, SeaNet rate protection |
| [`test_bridge_concurrency.py`](#test_bridge_concurrencypy) | 11 | 4 | Browser connection lock scopes, engine capture under lock, cancellation process preservation |
| [`test_bridge_helpers.py`](#test_bridge_helperspy) | 47 | 16 | Path traversal prevention, browser runtime normalization, fallback order, CDP error classification |
| [`test_build_i18n.py`](#test_build_i18npy) | 6 | 1 | POT translation template updating, locale selection, manifest version sync, obsolete entry purging |
| [`test_build_i18n_helpers.py`](#test_build_i18n_helperspy) | 29 | 7 | PO parsing, format placeholder extraction, PO string escaping, language code normalization |
| [`test_dependency_isolation.py`](#test_dependency_isolationpy) | 8 | 1 | Vendored WebSocket isolation, relative import compliance, driver asset and library path anchoring |
| [`test_generate_unicode_data_helpers.py`](#test_generate_unicode_data_helperspy) | 17 | 6 | UCD record parsing, script alias resolution, range merging, code generation helpers |
| [`test_language_redirect.py`](#test_language_redirectpy) | 26 | 2 | Dialect redirects, root-language fallbacks, CLDR alias resolution, Chinese cross-variant matching |
| [`test_language_utils.py`](#test_language_utilspy) | 6 | 1 | Language tag normalization, NVDA special locale mappings, language display names |
| [`test_performance.py`](#test_performancepy) | 17 | 5 | Segment flush thresholds, request coalescing, lead buffer, pause timings, adaptive packet sizing |
| [`test_runtime_recovery.py`](#test_runtime_recoverypy) | 5 | 1 | Browser speech failure recovery, single retry policy before audio, standby release safety gating |
| [`test_segmentation_benchmarks.py`](#test_segmentation_benchmarkspy) | 12 | 2 | Multilingual segmentation throughput benchmarks, cache warm-up passes, PCM processing speed |
| [`test_segmentation_fuzz.py`](#test_segmentation_fuzzpy) | 13 | 2 | Unicode fuzz testing, sentence split monotonicity, invariant verification across scripts |
| [`test_speech_processing.py`](#test_speech_processingpy) | 38 | 6 | Three pause modes, noise floor, chunk invariance, text segmentation, cache keys, sentence terminals |
| [`test_standby_concurrency.py`](#test_standby_concurrencypy) | 22 | 5 | Generation counter, cancelEvent propagation, bridge claim/release, clean termination |
| [`test_support.py`](#test_supportpy) | — | — | Shared test infrastructure, isolated module loader, mock bridge/CDP/engine/process helpers |
| [`test_synth_driver_helpers.py`](#test_synth_driver_helperspy) | 30 | 7 | Rate factor interpolation, break rate clamping, word dictionaries, config compat, NVDA logger formatting, fatal fallback |
| [`test_unicode_data.py`](#test_unicode_datapy) | 10 | 1 | Unicode 17.0 / CLDR 48.2 script ranges, automatic language profile fallback, sentence terminals |
| [`test_updater_security.py`](#test_updater_securitypy) | 77 | 15 | SHA-256 validation, size checks, path traversal defense, HTTPS enforcement, manifest parsing, versions |
| [`test_voice_package_lifecycle.py`](#test_voice_package_lifecyclepy) | 18 | 6 | Catalog loading/sorting, package verification, removal, copying, full lifecycle, catalog validation |
| [`test_watcher.py`](#test_watcherpy) | 17 | 4 | Win32 DirectoryChangeWatcher lifecycle, callbacks, edge cases, kernel-level directory watching |
| **Total** | **412** | **94** | **Exhaustive standalone test suite** |

---

## Shared Test Support (`test_support.py`)

`test_support.py` provides the foundational test infrastructure used throughout the suite:

- **Isolated module loading** (`load_pure_module`): Uses AST compilation and custom module namespaces to load production driver modules (`speech_processing.py`, `audio_math.py`, `voice_store.py`, `language_detector.py`, etc.) directly without triggering NVDA runtime dependencies.
- **Mock browser & CDP components**:
  - `FakeCdpClient`: Mocks the DevTools WebSocket client, request/response dispatch, cancellation event checks, and domain enabling.
  - `FakeEngine`: Mocks the WASM TTS engine with preload, speak, and cancellation tracking, including busy state simulation.
  - `FakeProcessManager`: Simulates browser process lifecycle, process termination, DevTools port discovery, and memory usage accounting.
  - `make_fake_bridge`: Factory function assembling a test `ChromeTtsBridge` instance wired with mock components for deterministic lifecycle testing.
- **Audio helpers**: `pack_pcm_frames` converts signed 16-bit integer sequences into raw little-endian PCM byte strings.
- **Repository paths**: Standardized path constants (`REPO_ROOT`, `DRIVER_DIR`, `PLUGIN_DIR`) ensuring consistent test execution across local and CI environments.

---

## Detailed Test Module Documentation

### `test_audio_math.py`
*6 tests across 1 class*

Tests pure audio mathematics and speech option calculation helpers in `audio_math.py`:
- **`AudioMathTests`**:
  - `test_rate_to_chrome_mapping`: Verifies non-linear rate mapping curve from NVDA slider values (0–100) to Chrome WASM playback rates.
  - `test_rate_to_chrome_with_rate_boost`: Validates rate calculation when rate boost is enabled.
  - `test_pitch_to_chrome_mapping`: Verifies pitch conversion from NVDA pitch (0–100) to Chrome semitone adjustments (-10 to +10 semitones).
  - `test_uses_protected_engine_rate_detection`: Verifies detection of SeaNet neural models that require rate protection.
  - `test_build_speech_options_standard_package`: Validates speech option dictionary generation for standard voice packages.
  - `test_build_speech_options_seanet_high_rate`: Validates speech option generation for SeaNet packages at high rates, ensuring the engine rate is capped while post-synthesis WSOLA scaling parameters are populated.

### `test_bridge_concurrency.py`
*11 tests across 4 classes*

Verifies thread safety, synchronization, and race-condition mitigations in `bridge.py`:
- **`EnsureConnectionLockScopeTests`**: Verifies that `ensure_connection()` releases the lock between fallback runtime iterations so background worker termination is never blocked, checks `cancelEvent` between fallback iterations, and verifies successful single-attempt connections.
- **`EngineCaptureUnderLockTests`**: Verifies that `speak()`, `stop_runtime()`, `cancel_current()`, and `preload_voice()` capture local references to `self._engine` under `_connectionLock` before invoking methods, preventing `AttributeError` or stale references if another thread recycles or re-creates the engine.
- **`RuntimeBusyLockTests`**: Verifies that the `runtime_busy` property reads engine busy state under its own lock, and safely returns `False` when no cancel event or engine is active.
- **`EnsureConnectionCancellationTests`**:
  - `test_cancelled_connection_does_not_terminate_process_manager`: Verifies that when connection establishment is cancelled (`CdpCancelled`), the unready CDP client WebSocket is closed but the running browser process is **not** terminated, preserving it for immediate reuse by concurrent or subsequent speech requests without connection refused errors (`WinError 10061`).
  - `test_concurrent_ensure_connection_when_first_caller_is_cancelled`: Verifies serialized concurrent callers to `ensure_connection()`, ensuring that if the first caller is cancelled (e.g. background warm-up), the second caller (e.g. interactive speech) acquires the lock and successfully establishes CDP connectivity using the already-running browser.

### `test_bridge_helpers.py`
*47 tests across 16 classes*

Validates pure helper functions in `bridge.py`:
- **`SafeJoinTests`**: Verifies path-traversal defenses against parent directory (`..`), absolute path, and encoded traversal attacks. *(Cross-platform note: assertions call `.resolve()` on expected and actual paths to normalize Windows 8.3 short names).*
- **`NormalizeBrowserRuntimeTests`**: Validates case-insensitive normalization of `chrome`, `edge`, and `brave` strings, defaulting unrecognized names to `chrome`.
- **`RuntimeFallbackOrderTests`**: Verifies candidate ordering: saved runtime first, followed by remaining runtimes without duplicates.
- **`FormatBytesTests`**: Verifies human-readable byte formatting (e.g. bytes, KB, MB).
- **`TransientErrorClassificationTests`**: Verifies detection of transient CDP startup errors (e.g. `Cannot find default execution context`) that require retry rather than immediate failure.
- **`RuntimeRecycleClassificationTests`**: Validates classification of exceptions requiring browser process recycling (`_BrowserSpeechError`, timeout, unexpected exceptions) versus cancellation (`CdpCancelled`), which must not trigger recycle.
- **`RaiseIfCancelledTests`**: Verifies that `_raise_if_cancelled` raises `CdpCancelled` if and only if the provided cancel event is set.
- **`BrowserRuntimeForPathTests`**: Matches browser executable filenames (`chrome.exe`, `msedge.exe`, `brave.exe`, and extensionless variants) to runtime constants.
- **`FriendlyCdpErrorTests`**: Verifies user-friendly CDP error formatting with technical details.
- **`BrowserRuntimeSnapshotTests`**: Tests collection of executable paths, availability flags, and effective runtime snapshots.
- **`EdgeWebview2BlocksTests`**: Tests WebView2 runtime requirement checks specific to Microsoft Edge.
- **`EffectiveBrowserRuntimeTests`**: Tests resolution of effective runtime based on availability and fallback order.
- **`ConfiguredBrowserRuntimeTests`**: Tests retrieval of configured runtime setting.
- **`BrowserExecutableAvailableTests`**: Verifies executable existence check.
- **`BrowserAvailabilityTests`**: Verifies comprehensive availability dictionary across all supported browsers.
- **`BrowserChoicesTests`**: Tests generation and filtering of browser choice tuples.

### `test_build_i18n.py`
*6 tests across 1 class*

Covers localization build workflows in `build_i18n.py`:
- **`TranslationTemplateUpdateTests`**:
  - Verifies that `update` command accepts `--all` and multiple language codes.
  - Verifies menu-driven locale selection for single or all locales.
  - Validates that POT template project version syncs dynamically from `manifest.ini`.
  - Verifies that obsolete PO message blocks are purged cleanly without corrupting active entries.
  - Verifies that newly merged source strings remain empty for translators.
  - Verifies rejection of non-empty translations for newly introduced source strings.

### `test_build_i18n_helpers.py`
*29 tests across 7 classes*

Tests pure helper functions in `build_i18n.py`:
- **`ParsePoTests`**: Validates parsing of standard, multiline, untranslated, fuzzy, and context-specific (`msgctxt`) PO entries.
- **`ExtractFormatPlaceholdersTests`**: Extracts Python format placeholders (`%s`, `{name}`) from strings for translation validation.
- **`NormalizeLanguageCodeTests`**: Normalizes language codes across ISO 639 formats and underscores/hyphens.
- **`PoEscapeTests`**: Validates character escaping (backslashes, quotes, newlines, tabs) in PO format output.
- **`PurgeObsoleteTests`**: Verifies removal of `#~` obsolete message blocks.
- **`ManifestValuesTests`**: Validates reading of add-on metadata from `manifest.ini`.
- **`MessagePreviewTests`**: Tests truncation and newline replacement for CLI progress messages.

### `test_dependency_isolation.py`
*8 tests across 1 class*

Guarantees isolation of bundled libraries and internal modules:
- **`BundledDependencyIsolationTests`**:
  - Verifies that the browser bridge uses Google TTS For NVDA's private vendored WebSocket client even if a foreign top-level `websocket` module is already registered in `sys.modules`.
  - Enforces that the vendored WebSocket library contains no third-party absolute imports.
  - Verifies that bundled WebSocket fallbacks preserve core connection and framing behavior.
  - Enforces package-relative imports across all driver internal modules.
  - Enforces package-relative imports across all global plugin internal modules.
  - Verifies that pure driver modules (`speech_processing.py`, `audio_math.py`, etc.) contain zero NVDA imports.
  - Verifies that CLD2 library candidates are anchored strictly to the driver directory.
  - Verifies that browser assets (`index.html`, `bridgeHarness.js`, WASM engine) are anchored strictly to add-on directories.

### `test_generate_unicode_data_helpers.py`
*17 tests across 6 classes*

Validates pure code-generation helpers in `generate_unicode_data.py`:
- **`ParseUcdRecordsTests`**: Parses Unicode Character Database (UCD) property files for individual codepoints and ranges.
- **`MergeRangesTests`**: Merges overlapping, adjacent, and unsorted codepoint ranges into minimal disjoint intervals.
- **`ScriptAliasesTests`**: Resolves script aliases (e.g. `sc` property aliases, `Hans`/`Hant` to `Han`).
- **`FormatRangesTests`**: Formats integer tuples into Python range tuple representations.
- **`FormatCodepointsTests`**: Formats sorted codepoint collections.
- **`RenderModuleTests`**: Verifies output generation of `unicode_data.py` including version header metadata.

### `test_language_redirect.py`
*26 tests across 2 classes*

Tests dialect redirection and language matching in `language_detector.py`:
- **`LanguageRedirectTests`**:
  - Explicit dialect redirects: French Canadian (`fr-CA` $\rightarrow$ `fr-FR`), Portuguese European (`pt-PT` $\rightarrow$ `pt-BR`), Spanish Spain (`es-ES` $\rightarrow$ `es-MX`), Austrian/Swiss German (`de-AT`/`de-CH` $\rightarrow$ `de-DE`), British/Australian English (`en-GB`/`en-AU` $\rightarrow$ `en-US`), Swiss Italian (`it-CH` $\rightarrow$ `it-IT`).
  - Fallback priorities: explicit dialect redirects take precedence over root-language fallback; root-language fallback applies when no explicit redirect exists.
  - Null/empty handling: returns original language safely when no match or redirect is possible.
- **`LanguageMatchesTests`**:
  - Exact tag matching and root-language matching.
  - CLDR alias matching: Filipino/Tagalog (`fil` $\leftrightarrow$ `tl`), Hebrew (`he` $\leftrightarrow$ `iw`).
  - Chinese script family matching: `zh-CN`, `zh-TW`, `zh-HK`, `zh-Hans`, `zh-Hant`.
  - Non-matching family rejection and underscore normalization.

### `test_language_utils.py`
*6 tests across 1 class*

Tests shared language and locale normalization helpers in `language_utils.py`:
- **`LanguageUtilsTests`**:
  - `test_normalize_language`: Normalizes language tags with subtags (e.g. `en_US` $\rightarrow$ `en-US`).
  - `test_normalize_language_code`: Normalizes language codes and removes invalid characters.
  - `test_get_nvda_locale_special_cases`: Tests special-case mappings for NVDA locales (Traditional Chinese `zh_HK`/`zh_TW`, Arabic variants, Tagalog).
  - `test_get_nvda_locale_prefixes`: Tests prefix-based locale resolution.
  - `test_resolve_nvda_locale_fallback_to_en`: Tests fallback to `en` when locale is unrecognized.
  - `test_get_language_display_name_with_custom_dict`: Tests localized display name resolution with custom dictionary overrides.

### `test_performance.py`
*17 tests across 5 classes*

Verifies performance characteristics, timing constants, and optimization invariants via AST/source inspection and standalone simulations:
- **`SegmentFlushThresholdTests`**: Verifies that `_FLUSH_GROUP_CHARS_THRESHOLD` (120 characters) controls intermediate flushes for `PAUSE_MODE_SHORTEN_ALL`, ensuring short texts remain in a single flush while long texts produce bounded multi-segment groups.
- **`SpeechCoalescingTests`**: Verifies that a pre-set `cancelEvent` is detected immediately at the top of `_speak_text()`, completely bypassing CDP round-trips for already-cancelled utterances.
- **`PcmLeadBufferPerformanceTests`**: Verifies `LIVE_MULTI_SEGMENT_LEAD_MS` is set to 80ms, ensuring lead buffering absorbs packet jitter without perceptible startup latency, and that `finish()` flushes remaining buffered audio cleanly.
- **`PauseModePerformanceTests`**: Verifies optimized timing constants: sentence break pause (45ms), end-of-utterance pause (40ms), and preload resume delay (0.15s).
- **`AdaptiveAudioPacketSizingTests`**: Verifies laddered packet sizing constants in `bridgeHarness.js` (first 120 samples / 5ms, early 1200 samples / 50ms, steady 2400 samples / 100ms, long-stream 3600 samples / 150ms) to ensure instant initial audio response while minimizing ongoing CDP serialization overhead.

### `test_runtime_recovery.py`
*5 tests across 1 class*

Tests browser-reported speech failure recovery and recycling policies:
- **`RuntimeRecoveryTests`**:
  - `test_browser_speech_errors_require_runtime_recycle`: Verifies that browser-side speech errors set the urgent recycle flag.
  - `test_no_audio_browser_error_retries_once_after_recycle`: Verifies that a speech failure occurring before any audio is emitted (`audioStarted=False`) retries exactly once with a fresh runtime.
  - `test_partial_audio_browser_error_recycles_without_retry`: Verifies that a speech failure occurring after audio has already started emitting (`audioStarted=True`) recycles the runtime but **never** retries, preventing repeated or stuttered speech.
  - `test_browser_error_is_never_retried_more_than_once`: Ensures speech requests are never retried more than once after recycling.
  - `test_only_healthy_connected_runtime_is_safe_for_standby`: Ensures only a healthy, connected runtime with an idle engine and no pending recycle flag is accepted for standby release.

### `test_segmentation_benchmarks.py`
*12 tests across 2 classes*

Performance benchmark tests for text segmentation and audio processing throughput:
- **`SegmentationPerformanceTests`**: Runs isolated cache warm-up passes to eliminate cold-start measurement noise, then benchmarks sentence splitting and latency segmentation across:
  - Latin text (1,000 and 5,000 characters)
  - CJK text (1,000 characters)
  - Thai text (1,000 characters)
  - Arabic text (1,000 characters)
  - Hindi / Devanagari text (1,000 characters)
  - Mixed-script text (2,000 characters)
  - Emoji-heavy text (1,000 characters)
  - URL-heavy text (1,000 characters)
  - Fast-first segment throughput (1,000 characters)
  - Linear scaling validation across input lengths.
- **`PcmProcessingThroughputTests`**: Verifies that the PCM silence shortener processes audio substantially faster than real-time playback speed.

### `test_segmentation_fuzz.py`
*13 tests across 2 classes*

Fuzz tests for speech text segmentation using pseudo-random Unicode streams:
- **`SegmentationFuzzTests`**:
  - `test_sentence_splits_never_crash_on_random_unicode`: Asserts zero crashes on arbitrary Unicode sequences.
  - `test_sentence_splits_are_monotonically_increasing`: Verifies split indices are strictly ordered.
  - `test_latency_segments_cover_full_input`: Asserts concatenated segments exactly equal original input text.
  - `test_latency_segments_respect_max_length`: Verifies all segments stay within length boundaries.
  - `test_segments_are_non_empty_strings`: Ensures no zero-length segments are generated.
  - Additional fuzz cases covering mixed-script, whitespace-only, single-character, punctuation-dense, and uniform-script texts.
- **`SentenceSplitFuzzTests`**:
  - `test_sentence_splits_produce_valid_indices`: Validates boundary index ranges.
  - `test_sentence_splits_dont_split_inside_cjk`: Asserts no unwanted splits inside punctuation-free CJK character runs.
  - `test_ellipsis_doesnt_over_split`: Asserts ellipsis character sequences do not produce runaway splits.

### `test_speech_processing.py`
*38 tests across 6 classes*

Comprehensive tests for PCM audio processing, text segmentation, caching, and Unicode sentence boundaries:
- **`PcmSilenceShortenerTests`**: Validates the three pause modes (`shortenAll`, `shortenEndOnly`, `doNotShorten`), inclusive PCM noise floor detection, invariant behavior across arbitrary packet chunk boundaries, flush of incomplete detection blocks at stream finish, flush of audible blocks at hidden boundaries, chunking invariance across hidden boundaries, and rejection of invalid pause modes.
- **`PcmLeadBufferTests`**: Verifies that the lead buffer releases audio once threshold is reached and passes subsequent audio through directly, and that short audio flushes cleanly on finish without loss.
- **`TextSegmenterTests`**: Covers opt-in medium-text fast-first segmentation, enforcement of fast-first limit strictly on the first segment, punctuation-free intact ceilings, trailing orphan word protection across Latin and non-Latin scripts, preference for early soft punctuation breaks, CJK punctuation-free intact ceilings, corpus schema version 1 verification, corpus test case coverage, and ASCII fast-path optimizations (`sample.isascii()` and `_FLATTENED_NO_SPACE_RANGES`).
- **`ShortAudioCacheKeyTests`**: Verifies cache key composition across audio and segmentation parameters, rejection of oversized texts or hidden-segment markers, boundary-context encapsulation in segment cache keys, and requirement of full boundaries for speech completion.
- **`SingleLetterAbbreviationGuardTests`**: Verifies that Kannada and Oriya single-letter words before periods split correctly (ASCII guard preventing non-Latin letters from blocking splits), while Latin single-letter abbreviation initials remain bound to their periods.
- **`UnicodeSentenceTerminatorTests`**: Validates sentence terminal classification across ASCII (`.`, `!`, `?`), CJK fullwidth terminals (`。`, `！`, `？`), Arabic sentence terminals (`؟`, `۔`), Devanagari danda (`।`, `॥`), Thai angular punctuation (`ฯ`), Meetei Mayek section markers (`꯫`), Greek question mark (`;`), tailored ellipsis (`…`), and non-terminal punctuation.

### `test_standby_concurrency.py`
*22 tests across 5 classes*

Tests the background standby runtime manager (`_StandbyRuntimeManager` in `standby.py`):
- **`GenerationCounterTests`**: Verifies generation counter increments on refresh, ensures worker generation mismatch cancels stale tasks, ensures worker generation match allows tasks to proceed, and verifies generation increments on bridge claim and release.
- **`CancelEventTests`**: Verifies `cancelEvent` blocks worker execution when set, allows uncancelled workers to proceed, tests `clear_standby` setting and handling of cancel events, and verifies that launching a new refresh cancels existing background workers.
- **`ClaimBridgeTests`**: Verifies `claim_bridge()` returns the standby bridge when catalog signatures match, returns `None` on signature mismatch, returns `None` after manager shutdown, and returns `None` when bridge is not fully ready.
- **`ReleaseSynthBridgeTests`**: Verifies `release_synth_bridge()` stores a healthy bridge for reuse, returns `False` after manager shutdown, and safely terminates previous bridges when replacing them.
- **`TerminateTests`**: Verifies that `terminate()` sets the shutdown flag, clears synth active markers, increments the generation counter, and safely handles `None` bridge references.

### `test_synth_driver_helpers.py`
*30 tests across 7 classes*

Tests pure helper functions extracted from the SynthDriver (`__init__.py`):
- **`InterpolateRateFactorTests`**: Tests rate factor interpolation across table boundaries, minimum/maximum clamps, midpoint interpolation, and custom tables.
- **`BreakRateFactorTests`**: Verifies break pause rate clamping across low, high, and neutral speech rates.
- **`EndOfUtteranceRateFactorTests`**: Verifies end-of-utterance pause rate factor calculations across speech rates.
- **`LanguageWordRegexTests`**: Tests word detection regex for simple words, apostrophes, hyphens, non-ASCII Unicode words, and rejection of digit-only tokens.
- **`WordDictionaryTests`**: Verifies that embedded Vietnamese and English common word dictionaries are non-empty and contain standard vocabulary.
- **`ConfigCompatTests`**:
  - `test_fills_missing_rate_boost_and_pause_mode`: Verifies that configuration compatibility initialization safely injects missing settings (`rateBoost`, `pauseMode`) into legacy `nvda.ini` configurations.
  - `test_preserves_custom_settings`: Ensures existing custom user settings are never overwritten during compatibility checks.
  - `test_nvda_load_settings_loop_simulation_succeeds`: Simulates NVDA's `synthDriverHandler.SynthDriver.loadSettings()` iteration over `self.supportedSettings`, verifying that missing configuration keys do not raise fatal `KeyError` crashes (such as `KeyError: 'rateBoost'`).
  - `test_speech_failure_logging_compatible_with_nvda_logger`: Verifies that `log.exception()` calls format messages into a single string (e.g. `f"Google TTS speech failed: {technicalDetail}"`) and do **not** pass extra positional formatting arguments (`*args`) with `exc_info=True`. This prevents fatal `TypeError: Logger.exception() got multiple values for argument 'exc_info'` exceptions under NVDA's `logHandler.Logger.exception(self, msg="", exc_info=True, **kwargs)` signature during speech error recovery.
- **`FatalFallbackTests`**:
  - `test_friendly_message_extracted_from_cdp_error`: Verifies that `str(err).strip()` on `CdpError` extracts the user-friendly localized message without requiring any new UI strings.
  - `test_empty_error_falls_back_to_default_message`: Verifies that an empty exception string falls back safely to the pre-existing default localized string.
  - `test_fallback_debounce_prevents_duplicate_dialogs`: Verifies that `_trigger_fatal_fallback` debounces duplicate triggers via `self._fallbackTriggered`, clears the background speech queue, and cancels active audio.

### `test_unicode_data.py`
*10 tests across 1 class*

Verifies generated Unicode script metadata and sentence terminal definitions:
- **`UnicodeDataTests`**:
  - Verifies that generated UCD (17.0) and CLDR (48.2) versions are pinned.
  - Verifies that every language root in the bundled `voices.json` has official script data.
  - Verifies that language script ranges are composed strictly from their mapped scripts.
  - Verifies that script ranges are sorted, non-overlapping, and minimal.
  - Verifies that Unicode 17.0 script ranges outside legacy blocks are present.
  - Verifies that automatic language profile fallback resolves using compiled character ranges.
  - Verifies that shared scripts (e.g. Latin/Cyrillic pairs) remain ambiguous between language profiles to prevent incorrect single-script locks.
  - Verifies that language profile fallback rejects missing or non-matching scripts.
  - Verifies that the official Unicode sentence-terminal property table is complete.
  - Verifies that sentence-terminal tailoring is minimal and disjoint from official tables.

### `test_updater_security.py`
*77 tests across 15 classes*

Exhaustive security and validation tests for the add-on update manager (`updater.py`):
- **`Sha256ValidationTests`**: Validates lowercase, uppercase, prefix-stripped (`sha256:`), and rejects malformed, non-hex, short, long, empty, or missing hashes.
- **`SizeValidationTests`**: Validates positive integer byte sizes; rejects zero, negative, `None`, string, boolean, or missing sizes; allows large valid packages.
- **`PathTraversalTests`**: Ensures downloaded update filenames prevent path traversal: accepts clean `.nvda-addon` filenames; rejects `/`, `\`, `.`, `..`, empty strings, whitespace, and non-addon extensions.
- **`HttpsOnlyTests`**: Enforces that update download URLs use HTTPS; rejects HTTP, FTP, and data URIs.
- **`ManifestParsingTests`**: Validates JSON/INI update manifest parsing, addon ID matching (`googleTtsForNvda`), release channel validation (`stable`), required field enforcement, and rejection of negative `updateBuild` integers.
- **`UpdateSizeLimitTests`**: Verifies maximum package size (50MB) and manifest size (64KB) constants and rejects oversized downloads.
- **`VersionComparisonTests`**: Validates semantic version comparison across major, minor, patch, and identical versions.
- **`StripManifestValueTests`**: Tests quote removal (single and double quotes) and whitespace trimming.
- **`VersionPartsTests`**: Tests numeric component extraction and rejection of non-numeric version strings.
- **`UpdateAvailabilityTests`**: Validates update availability logic, including newer versions, identical versions, and same-version higher `updateBuild` hotfixes.
- **`RequiredStringTests`**: Validates presence, non-empty, and type checks for mandatory manifest strings.
- **`OptionalStringTests`**: Validates optional string handling and defaults.
- **`LocaleKeyTests`**: Tests locale key normalization (hyphens to underscores, whitespace trimming).
- **`ReleaseNotesTests`**: Verifies selection of locale-specific release notes with fallback to English/default.
- **`UpdateFileNameTests`**: Tests update file name construction and validation of `.nvda-addon` extensions.

### `test_voice_package_lifecycle.py`
*18 tests across 6 classes*

Validates the `.zvoice` package management lifecycle (`voice_store.py` and `catalog.py`):
- **`CatalogLoadingTests`**: Tests JSON catalog parsing, package sorting by language then ID, package ID to language extraction, and engine compatibility checks.
- **`PackageVerificationTests`**: Tests `is_package_installed` checking for non-existent files, valid files with matching SHA-256 and size, corrupt files with hash/size mismatches, and empty-hash handling.
- **`PackageRemovalTests`**: Verifies atomic deletion of `.zvoice` files and graceful handling of already-deleted files.
- **`PackageCopyTests`**: Verifies manual package import/copy into voice storage with strict size and SHA-256 validation.
- **`VoicePackageLifecycleTests`**: Tests the complete end-to-end install $\rightarrow$ verify $\rightarrow$ remove $\rightarrow$ verify cycle, and asserts that the verification cache is invalidated immediately upon package removal.
- **`CatalogValidationTests`**: Verifies validation of multiple voice packages in a catalog and asserts that validation warnings from later packages are collected completely rather than skipped by early return.

### `test_watcher.py`
*17 tests across 4 classes*

Unit and integration tests for the Win32 filesystem watcher (`DirectoryChangeWatcher` in `watcher.py`):
- **`DirectoryChangeWatcherLifecycleTests`**: Verifies clean start/stop thread cycles, idempotent `start()`, idempotent `stop()`, and calling `stop()` before `start()`.
- **`DirectoryChangeWatcherCallbackTests`**: Verifies callback invocation with correct change reasons, ensures no callbacks fire on immediate stop, tests multiple notifications, and verifies paths are queried dynamically on every wait cycle.
- **`DirectoryChangeWatcherEdgeCaseTests`**: Verifies immediate thread exit when no directories exist to watch, thread exit on empty path tuples, thread termination after `stop()`, and ensures all Win32 `FindFirstChangeNotificationW` and `CreateEventW` handles are closed in `finally` blocks via `CloseHandle`.
- **`DirectoryChangeWatcherIntegrationTests`**: *(Windows-only)* Uses real temporary directories to verify file creation detection, file deletion detection, simultaneous multi-directory watching, and verification that stopping the watcher prevents subsequent callbacks.

---

## Test Data & Corpora

### `segmentation_corpus.json`

JSON test corpus used by `TextSegmenterTests` in `test_speech_processing.py`. Contains real-world text samples and expected sentence break boundaries across diverse scripts and categories:
- **Schema version**: 1
- **Categories**:
  - `cjk`: Chinese and Japanese text without spaces, testing punctuation-free ceilings and sentence terminals.
  - `thai`: Thai script without spaces, testing angular punctuation and boundary limits.
  - `urls`: Complex web and file URLs, testing traversal protection and non-splitting behavior.
  - `abbreviations`: Single-letter initials, honorifics, and Latin/non-Latin abbreviation periods.
  - `punctuation`: Standard ASCII and Unicode terminal punctuation combinations.
  - `emoji`: Emoji and surrogate pair sequences interspersed with text.
  - `long_sentences`: Sentences exceeding standard chunk limits, testing soft-break fallback and intact ceilings.

---

## NVDA Static API Contract Checks

Static API compatibility can be verified against a local NVDA source checkout:

```powershell
python tests\check_nvda_api_contracts.py
```

By default, the script looks for a sibling `NVDA source code` directory. A custom path can be provided:

```powershell
python tests\check_nvda_api_contracts.py "C:\path\to\NVDA source code"
```

The scanner performs AST and token analysis across 8 major integration categories:
1. **Synth Driver**: `SynthDriver` inheritance, `speak`, `cancel`, `pause`, `terminate`, `isSupported`, `supportedSettings`, `supportedCommands`, and `loadSettings`.
2. **Audio Output**: `nvwave.WavePlayer` constructor signatures, `closeWhenIdle` vs `buffered`, `isInError` vs `audioDeviceError`, and `audio` vs `speech` output device configuration paths.
3. **Global Plugin**: `GlobalPlugin` lifecycle, `post_nvdaStartup` hooks, and Tools menu registration.
4. **Speech Hooks**: Compatibility with speech sequence processing, command tuples, and index markers.
5. **Language Profiles**: Automatic language profile integration and setting ring bindings.
6. **Settings Category**: `gui.AutoSettingsMixin`, `makeSettings`, `onSave`, and `refreshGui` compatibility across NVDA versions.
7. **Voice Manager & Updater**: `wx.Dialog`, `wx.ListCtrl`, `wx.Gauge`, and `addonHandler.installAddonPackage` API contracts.
8. **Browser Runtime & Standby**: Process management, background thread boundaries, and non-blocking NVDA main thread guarantees.

---

## Interactive Manual Release Checklist

Static tests and standalone unit tests cannot validate live Chromium browser window management, audible audio distortion/balance, or real screen-reader focus announcements.

Before tagging a release, complete the manual test procedures documented in:
[`NVDA_CHROMIUM_MANUAL_CHECKLIST.md`](NVDA_CHROMIUM_MANUAL_CHECKLIST.md)

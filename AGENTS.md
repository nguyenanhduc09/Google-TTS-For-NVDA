# Google TTS For NVDA — Agent Engineering Guide

You are working on **Google TTS For NVDA**, an NVDA screen-reader synthesizer add-on. Act as **Codex, a software engineering agent maintaining a production accessibility add-on**, not as an end user. Your job is to make safe, minimal, testable changes that preserve NVDA responsiveness, accessibility, packaging correctness, and the supported Chromium browser WASM TTS bridge.

Product vision: this add-on grew from the dream of making Google TTS usable as a practical, everyday NVDA synthesizer on Windows computers. Preserve that user-facing goal when changing code, documentation, packaging, and translation workflows.

This file is the operating manual for coding agents. Follow it before making or suggesting code changes.

---

## Version 0.3 Product Wording

When writing documentation, release notes, commit messages, or user-facing summaries for version 0.3:

- Describe voice package startup work as an improvement, not as a complete fix. The add-on prepares the currently selected voice package sooner, but Chromium browser runtime and WASM startup still affect timing.
- Describe audio balance, clipping, harshness, or distortion work as an improvement, not as a complete fix. The processing is generic across voice packages and languages; Vietnamese may be mentioned only as a testing example, not as the only affected language.
- Describe long-text and UI-text latency/segmentation work as an improvement, not as a complete fix. Background segmentation can make speech begin sooner and sound more natural, but cache misses and engine behavior can still affect long utterances.
- SeaNet high-rate handling can be described as successful for quality preservation, with the explicit trade-off that high-speed SeaNet speech uses more CPU because generated audio is processed after synthesis.
- Use "voice package" when referring to startup/warm-up behavior. Do not imply that version 0.3 warms or fixes every individual speaker voice independently.

---

## 1. Agent Operating Mode

### Default behavior

- Treat every request as an engineering task: inspect the relevant files, reason about side effects, make the smallest useful change, and verify it.
- Codex may inspect, edit, test, build, and package files in this workspace and may use online research when the task requires current external technical context.
- Codex can run local smoke tests and syntax checks, but must not claim a real interactive NVDA/browser-runtime user test unless that exact runtime test was actually performed.
- Prefer implementation over explanation when the user asks for code changes.
- Do not redesign the architecture unless the request explicitly requires it or the current design blocks correctness.
- Preserve existing public behavior unless the user asks to change it.
- Keep changes localized. Avoid broad refactors mixed with bug fixes.
- Do not introduce network access, downloads, telemetry, background services, or new dependencies without a clear requirement.
- Never block NVDA's main thread with synthesis, browser startup/runtime work, filesystem-heavy work, or network work.

### Before editing

1. Identify the affected layer:
   - NVDA synth driver: `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`
   - Browser/CDP bridge: `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`
   - Voice catalog and storage: `googleTtsForNvda/synthDrivers/googleTtsForNvda/catalog.py`, `googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py`
   - Browser harness: `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`, `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/index.html`
   - Voice Manager UI: `googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py`
   - Packaging/docs: `googleTtsForNvda/manifest.ini`, `googleTtsForNvda/doc/en/readme.html`, build scripts
2. Read nearby code before changing it.
3. Check this guide for non-negotiable constraints.
4. Plan tests before editing.

### While editing

- Maintain compatibility with NVDA add-on conventions.
- Preserve thread cancellation paths and cleanup paths.
- Add concise comments only where behavior is non-obvious, especially for browser-runtime/WASM quirks.
- Keep user-facing strings translatable with `_('...')` where used in NVDA UI code.
- Do not silently swallow exceptions that affect speech, downloads, or packaging. Log enough context for debugging.

### After editing

- Run the smallest relevant checks first, then broader checks if packaging or cross-module behavior changed.
- Report exactly what changed, what was tested, and what could not be tested.
- Mention any remaining risk or follow-up work.

---

## 2. Project Overview

Workspace: `C:\Users\hungv\Documents\Codex\Google-TTS-For-NVDA`

**Google TTS For NVDA** exposes Google's WASM TTS voices to NVDA through:

- an NVDA synth driver,
- a managed headless supported Chromium browser process, such as Google Chrome, Microsoft Edge, or Brave,
- a browser DevTools Protocol (CDP) WebSocket bridge,
- a browser-side JavaScript harness that captures PCM audio from the WASM engine,
- runtime-downloaded `.zvoice` voice packages stored in the user's NVDA config directory.

### High-level architecture

```text
Google-TTS-For-NVDA/
├─ googleTtsForNvda/
│  ├─ manifest.ini
│  ├─ synthDrivers/googleTtsForNvda/
│  │  ├─ __init__.py        SynthDriver; NVDA integration and settings ring
│  │  ├─ bridge.py          ChromeTtsBridge; HTTP server, browser lifecycle, CDP/WS
│  │  ├─ catalog.py         VoiceCatalog, VoicePackage, Speaker models
│  │  ├─ language_detector.py
│  │  │                    CLD2-backed language detection with x86/x64 DLL selection
│  │  ├─ voice_store.py     Download, copy, verify, remove voice packages
│  │  ├─ web/
│  │  │  ├─ index.html      Loaded in the headless Chromium browser runtime
│  │  │  └─ bridgeHarness.js
│  │  │     Shims chrome.* APIs, calls WASM engine, captures AudioWorklet PCM,
│  │  │     sends base64 chunks through the CDP binding
│  │  ├─ WasmTtsEngine/20260625.1/
│  │  │  ├─ bindings_main.js / .wasm
│  │  │  ├─ offscreen_compiled.js
│  │  │  ├─ voices.json
│  │  │  └─ streaming_worklet_processor.js
│  │  └─ websocketClientRepo/   Vendored websocket-client library
│  ├─ globalPlugins/googleTtsForNvda/
│  │  ├─ __init__.py        Tools menu integration
│  │  ├─ settings.py        Google TTS settings panel
│  │  └─ voiceManager.py    wx Voice Manager dialog
│  ├─ doc/
│  │  ├─ en/readme.html
│  │  └─ vi/readme.html
│  └─ locale/
│     └─ vi/
├─ build.bat
├─ build_i18n.py
├─ generate_voices_json.py
└─ readme.md
```

### Speech data flow

1. NVDA calls `SynthDriver.speak()` with a speech sequence.
2. The driver segments text, builds options for voice/rate/pitch/volume, and queues synthesis on a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP are connected, then evaluates `window.googleTtsForNvdaSpeak(...)` via `Runtime.evaluate`.
4. `bridgeHarness.js` calls the Google WASM TTS engine through `window.Uh.onSpeak`, intercepts `AudioWorkletNode` buffers, converts float32 audio to int16 PCM, and sends base64 audio chunks through the `googleTtsForNvdaBridge` CDP binding.
5. Python receives `Runtime.bindingCalled`, decodes PCM, and feeds it to `nvwave.WavePlayer`.

---

## 3. Non-negotiable Product Rules

### Voice packages must not auto-download during speech

- `bridge.py:speak()` and `bridge.py:preload_voice()` must **never** call `voice_store.download_package()`.
- Voice downloads are allowed only from the Voice Manager UI flow in `voiceManager.py`.
- The speech path must use `voice_store.is_package_installed(package)` and fail clearly, normally with `CdpError`, if a required package is missing.

### No `.zvoice` files in the add-on source tree

- The add-on source directory `googleTtsForNvda\` must never contain `.zvoice` files.
- Voice packages belong at runtime under `%NVDA_CONFIG%\googleTtsForNvda\voices\`.
- Before packaging, run:

```powershell
rg --files googleTtsForNvda -g "*.zvoice"
```

Expected result: no files.

### Only installed voices are exposed to NVDA

- `SynthDriver._build_available_voices()` must list only speakers whose packages pass `voice_store.is_package_installed(package)`.
- It is OK to load the full master catalog at startup, but the driver-facing catalog must be filtered to installed packages.
- Do not show remote/uninstalled voices in the synth's voice setting ring.

### First-run / no-voice behavior

If no voice packages are installed when the synth starts:

- Show a `gui.messageBox` prompting the user to download voices.
- OK opens Voice Manager on the Download tab and aborts synth loading by raising `RuntimeError`.
- Cancel aborts synth loading by raising `RuntimeError`.
- Do not fall back to remote downloads or hidden defaults.

### Browser-runtime availability limits

This add-on depends on a supported Chromium browser runtime, such as Google Chrome, Microsoft Edge, or Brave, running in the current Windows user session.

- Do not document or imply that Google TTS For NVDA is suitable for environments where the Chromium browser runtime is unavailable or cannot start.
- User-facing documentation should warn that the add-on should not be relied on at the Windows sign-in screen, secure desktop contexts, Windows PE, recovery environments, or other minimal Windows sessions.
- User-facing documentation should include an Edge-runtime silence troubleshooting note: if Microsoft Edge is selected as the Chromium browser runtime and speech stays silent even though Edge is installed, direct users to install or repair Microsoft Edge WebView2 Runtime using Microsoft's Evergreen Bootstrapper link (`https://go.microsoft.com/fwlink/p/?LinkId=2124703`), then restart NVDA. Also include Microsoft's WebView2 page (`https://developer.microsoft.com/microsoft-edge/webview2`) for offline installers and fixed-version runtime packages.
- If opening a WebView2/download URL fails, the fallback dialog must show the URL in a focusable read-only field with a real label association, bind the same full-value focus announcement used by Google TTS status fields, and include a Copy link button.
- Microsoft Edge WebView2 Runtime is required only when Microsoft Edge is the selected/effective Chromium browser runtime. Google Chrome and Brave must not depend on WebView2; Chrome and Brave availability should be checked only through their browser executable/path. Status messages, fallback logic, prompts, and documentation must not imply that Chrome or Brave needs Edge WebView2.
- Keep fallback/error wording clear: if no supported Chromium browser runtime is available, the synth cannot provide speech through the Google WASM TTS engine.
- Browser-runtime code map:
  - Runtime constants and labels live in `bridge.py`: `BROWSER_RUNTIME_CHROME`, `BROWSER_RUNTIME_EDGE`, `BROWSER_RUNTIME_BRAVE`, `BROWSER_RUNTIMES`, `DEFAULT_BROWSER_RUNTIME`, and `BROWSER_RUNTIME_LABELS`.
  - Detection and fallback flow lives in `bridge.py`: `_runtime_fallback_order()`, `_browser_candidates()`, `browser_path_for_runtime()`, `browser_executable_available()`, `edge_webview2_available()`, `browser_runtime_available()`, `browser_availability()`, `_find_browser_choice()`, `browser_runtime_snapshot()`, `find_browser()`, `effective_browser_runtime()`, and `edge_webview2_blocks_effective_runtime()`.
  - CDP connection and browser-harness readiness live in `bridge.py`: `CdpClient.request()`, `_friendly_cdp_error()`, `_TRANSIENT_RUNTIME_EVALUATE_ERRORS`, `_is_transient_runtime_evaluate_error()`, `WasmTtsEngineBridge.enable_cdp_domains()`, `WasmTtsEngineBridge.wait_until_ready()`, and `ChromeTtsBridge.ensure_connection()`. During startup, transient `Runtime.evaluate` errors such as `Cannot find default execution context` mean the harness execution context is not stable yet; readiness polling should wait and retry, while non-transient CDP errors must still surface as `CdpError`.
  - Settings UI runtime flow lives in `settings.py`: `_runtime_label()`, `_save_browser_runtime()`, `_schedule_runtime_change_after_synth_switch()`, `_clear_pending_runtime_change()`, `_apply_runtime_after_synth_switch()`, `GoogleTTSSettingsPanel._refresh_runtime_snapshot()`, `_format_runtime_choice()`, `_effective_runtime_message()`, and `_select_saved_runtime()`.
  - Keep the fallback order Chrome, Edge, then Brave unless changing the product decision. If the saved runtime is Brave and Brave is unavailable, fallback must still find Chrome or Edge when they are usable.
  - `browser_runtime_snapshot()` is for UI/status code that needs a consistent view of executable availability, Edge WebView2 availability, and the effective fallback runtime. It must not make Chrome or Brave depend on WebView2.
  - `settings.py` runtime status controls must use focusable read-only text with `bind_read_only_text_focus_announcement()` so the full selected/effective Chromium runtime message is announced on focus.
  - Browser profile separation lives in `BrowserProcessManager._browser_profile_root()`, `BrowserProcessManager._browser_profile_dir_name()`, the current-profile `_profileRuntime` guard, and the profile directory constants `CHROME_PROFILE_DIR_NAME`, `EDGE_PROFILE_DIR_NAME`, and `BRAVE_PROFILE_DIR_NAME`. Brave cache/WASM profile data belongs under `braveProfiles`, not the Chrome or Edge profile roots.
  - Browser profile startup fallback lives in `BrowserProcessManager.start_browser()`, `_BrowserProfileInUseError`, `_browser_profile_in_use_error()`, `_get_browser_profile_dir()`, `_cleanup_old_browser_profiles()`, `_release_chrome_profile()`, and `_remove_chrome_profile()`. Start with the persistent `persistentSession` profile so Chromium can reuse WASM/code cache; if Chromium exits with profile-in-use code 21, retry once with a temporary `session-<pid>-<timestamp>` profile under the same runtime profile root.
  - Persistent profile reset is controlled by `PERSISTENT_PROFILE_MAX_BYTES` and must run per runtime profile root only. Resetting an oversized Chrome profile must not remove Edge or Brave profile data, and custom `CHROME_PATH`, `EDGE_PATH`, or `BRAVE_PATH` executable names must not cause profile roots or snapshot runtime status to be inferred from the path basename.
  - Temporary browser profiles are a resilience fallback only. `_release_chrome_profile()` must preserve persistent profiles but remove temporary profiles, while `_remove_chrome_profile()` may delete the current profile after startup failure. Do not make temporary profiles the normal path unless persistent profile reuse is deliberately removed.

### Supported settings ring parameters

Current supported settings:

- `VoiceSetting()` — installed Google TTS language selection when automatic language profiles are off
- `VariantSetting()` — voice name/speaker selection within the selected Google TTS language when automatic language profiles are off
- `RateSetting()` — speech rate, 0-100. Non-SeaNet packages map to browser-runtime rate 0.35-2.0; `*-seanet` packages keep a protected engine rate at higher speeds and use post-synthesis artificial rate processing.
- `RateBoostSetting()` — boolean, doubles computed desired speech rate when enabled. For `*-seanet` packages at high rates, this can increase CPU usage because audio is processed after synthesis.
- `PitchSetting()` — pitch, 0-100, maps through the existing semitone curve
- `VolumeSetting()` — volume, 0-100, maps to browser-runtime volume 0.0-1.0

Do **not** re-add:

- `Transposition`
- `AccelerationMode`

These were removed and must stay removed unless the user explicitly requests a new design and compatibility fix.

### Long-text segmentation

- Long-text latency segmentation should prefer natural sentence and phrase punctuation before falling back to forced length cuts.
- For scripts that often do not separate words with spaces, the synth driver may use conservative fixed-size script-window cuts after punctuation and whitespace checks have failed. This is a latency fallback, not language detection and not word segmentation.
- Keep this fallback independent from automatic language profiles, NVDA Speech Settings, speech dictionaries, and voice dictionary handling.
- Current no-space/low-space script coverage includes CJK/Han and CJK extensions, Bopomofo, Japanese Kana, Thai, Lao, Limbu, Tai Le, New Tai Lue, Buginese, Tai Tham, Khmer, Myanmar, Tibetan, Philippine Brahmic scripts, Balinese, Sundanese, Batak, Javanese, Lepcha, Yi, Rejang, Cham, Tai Viet, and similar scripts where long text commonly cannot rely on spaces as word boundaries.
- Do not add Latin, Cyrillic, Arabic, Hebrew, Ethiopic, Cherokee, Canadian Aboriginal syllabics, or other normally space-separated scripts to the no-space fallback without a specific bug report or clear evidence. For those scripts, punctuation and whitespace-based segmentation should remain the default.

### Status/help control accessibility

- Status/help lines in Speech Settings, the Google TTS settings category, and similar NVDA dialogs must be reachable by Tab and read by NVDA. Use focusable read-only controls for these status lines instead of plain `wx.StaticText`.
- Focusable status/help controls must have a real label association, not only `SetName()`, so NVDA announces the status/help name before the read-only edit role. If the status/help text can wrap or span multiple lines, make focus announce the complete current message while still allowing arrow-key review inside the read-only edit.
- Apply this rule to Chromium browser runtime status, automatic language profile status, Speech Settings notices, current-browser notices, and future status/help fields with similar behavior.
- Accessibility helper map:
  - Use `settings.py:bind_read_only_text_focus_announcement()` for focusable read-only status/help text that may be long or wrapped, including fallback URL fields.
  - Speech Settings read-only notices are created through `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py:_make_read_only_text_setting_control()` and patched by `_patch_read_only_text_setting()`. Keep `_hide_google_tts_auto_profile_speech_controls()` hiding normal speech controls only while automatic language profiles replace them.
  - A `RuntimeError: wrapped C/C++ object of type BoxSizer has been deleted` after switching away from Google TTS can come from a stale NVDA `AutoSettingsMixin.refreshGui` callback against a destroyed Voice Settings panel. Preserve the `_patch_read_only_text_setting()` guard that ignores only the wx "has been deleted" refresh on destroyed panels, and keep other `RuntimeError` failures visible.
  - Manual URL fallback dialogs should follow `_show_manual_web_url_dialog()`: real label association, read-only `wx.TextCtrl`, full-value focus announcement, and a Copy link button.

### Automatic language profiles

Automatic language profiles deliberately have their own profile system and must not write per-language values into NVDA's normal Speech Settings.

- Config keys live under `CONFIG_SECTION = "googleTtsForNvda"`:
  - `autoLanguageDetection` — master enable switch.
  - `autoLanguagePreferred` — preferred language used when text is ambiguous.
  - `autoLanguageCandidates` — comma-separated compatibility list of selected languages.
  - `autoLanguageProfiles` — JSON object keyed by installed language code. Each profile stores `enabled`, `voice`, `rate`, `rateBoost`, `pitch`, `volume`, `capPitchChange`, `sayCapForCapitals`, `beepForCapitals`, and `useSpellingFunctionality`.
- When automatic language profiles are **off**, the synth must use NVDA's normal Speech Settings values for voice, rate, rate boost, pitch, volume, capital-letter handling, and spelling behavior.
- When automatic language profiles are **on**, detected sentences must use the selected language profile values. If only one language profile is enabled, use that profile for every sentence; do not fall back to normal Speech Settings values merely because there is only one candidate. Do not persistently copy these profile values into `config.conf["speech"][synthName]`.
- Keep NVDA-wide Speech Settings in NVDA Speech Settings. This includes automatic language/dialect switching, language change reporting, punctuation and symbol level, trusted voice language, Unicode normalization, Unicode Consortium data (including emoji), normalized-character reporting, extra symbol dictionaries, delayed character descriptions, and cycle speech mode choices.
- Automatic language profiles should use the bundled CLD2 detector (`googleTtsForNvda/synthDrivers/googleTtsForNvda/language_detector.py` and `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/`) as the primary detector. `language_detector.py` must select `cld2_x86.dll` for 32-bit NVDA/Python and `cld2_x64.dll` for 64-bit NVDA/Python, with `cld2.dll` only as a compatibility fallback.
- Do not use unreliable CLD2 results as authoritative for unclear text. If CLD2 is unavailable or uncertain, the synth may use conservative local language signals and then the enabled preferred language; it must not fall back to normal Speech Settings values while automatic language profiles are on.
- Explicit `LangChangeCommand` values from NVDA or the focused app remain authoritative and should not be overridden by automatic language profile selection.
- Automatic language profiles should insert `LangChangeCommand` before NVDA text processing when possible, so symbol pronunciation and speech dictionary processing remain in NVDA's normal speech pipeline for the selected language context.
- Automatic language profile voice dictionary handling must follow the selected profile voice for each enabled language. Temporarily load the matching NVDA voice dictionary only while NVDA processes that segment, then restore the user's current voice dictionary. Default and temporary dictionaries must keep NVDA's normal behavior.
- Keep Google voice catalog language codes separate from NVDA text-processing locales. Catalog/profile/Voice Manager selection should preserve Google language codes such as `vi-VN`, `en-GB`, or `cmn-TW` so the correct Google voice is chosen. Only convert to NVDA locale form when passing language context into NVDA speech processing, `LangChangeCommand`, symbol pronunciation, CLDR/emoji processing, voice dictionaries, or the synth `language` property.
- NVDA locale conversion must follow the installed NVDA locale folders under `globalVars.appDir\locale`: first try the exact normalized locale such as `vi_VN`, then its root such as `vi`, then fall back to `en` if NVDA has no locale data for that language. Preserve special mappings where Google and NVDA use different identifiers, including `cmn-CN -> zh_CN`, `cmn-TW -> zh_TW`, `yue-HK -> zh_HK`, `ar-XA -> ar`, and `fil-PH -> tl` before applying the installed-locale fallback.
- Profile voices must be installed and must match the selected profile language. If a saved profile references a missing or mismatched voice, fall back to an installed voice for that language.
- The Google TTS settings panel must keep the language profile list accessible: use a normal language choice control, a clear checkbox for "Use this language profile", and ordinary labeled controls for profile values. Do not use a multi-column table for these profile controls.
- The Google TTS settings category status line for automatic language profiles must describe the current state, not only the enabled behavior:
  - no installed language voice packages: prompt the user to install at least one language voice package;
  - automatic language profiles off: explain that Google TTS is using NVDA's normal Speech Settings values;
  - automatic language profiles on with no selected profiles: prompt the user to select at least one language profile;
  - automatic language profiles on with selected profiles: explain that selected installed language profiles are used, and one selected profile applies to every sentence.
- The preferred profile language choice must only list languages whose profile is enabled.
- Rate, pitch, and volume profile controls should use sliders, matching NVDA's Speech Settings interaction style. Capital pitch should use NVDA's numeric edit/spin control (`nvdaControls.SelectOnFocusSpinCtrl`) to match Speech Settings.
- Use NVDA's own translated setting names for voice/rate/rate boost/pitch/volume labels where possible instead of inventing add-on-specific translated terms.
- The main checkbox label should describe the broader behavior as automatic language profiles, not only switching between voices, because one enabled profile is valid and applies to every sentence.
- When automatic language profiles are enabled, `SynthDriver.supportedSettings` should hide normal `VoiceSetting`, `VariantSetting`, `RateSetting`, `RateBoostSetting`, `PitchSetting`, and `VolumeSetting`, and instead expose a read-only notice that directs the user to the Google TTS For NVDA settings category. Refresh the settings ring after saving the automatic language profile setting.
- Vietnamese UI/docs must translate "Google TTS for NVDA" as "Google TTS Cho NVDA" when it is user-facing text.
- Automatic language profile code map:
  - Synth-side selection lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_auto_detect_profile_for_text()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_auto_language_candidate_for_language()`, `_detect_auto_language()`, `_language_token_signal()`, `_language_script_signal()`, `_voice_for_language()`, `_voice_matches_language()`, `_current_speaker_id()`, and `_speech_options()`.
  - Profile-aware warm-up ordering lives in the Voice preloading code map: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_options_for_voice_ids()`, `_warmup_voice_ids_for_voice()`, and `_voice_id_for_package()`.
  - NVDA speech pipeline integration lives in `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_filter_auto_language_speech_sequence()`, `_register_auto_language_speech_filter()`, `_unregister_auto_language_speech_filter()`, `_google_lang_change_command()`, `_nvda_locale_for_language()`, `_auto_language_for_process_text()`, `_patch_auto_language_voice_dictionary()`, and `_unpatch_auto_language_voice_dictionary()`.
  - Character, spelling, and symbol-related profile behavior is handled by `_auto_profile_character_settings_for_language()`, `_auto_profile_character_context_for_text()`, `_single_auto_profile_character_settings()`, the patched `speech.getSpellingSpeech`, and the patched `shortcutKeys.shouldUseSpellingFunctionality`. Preserve temporary config overlays and always restore NVDA speech config values.
  - Automatic-language settings ring notice behavior lives in `SynthDriver.supportedSettings`, `ReadOnlyTextDriverSetting`, `_get_availableNotices()`, `_auto_language_notice_message()`, `_get_notice()`, and `_set_notice()`. Keep `_get_availableNotices()` keyed by the notice message, not the static notice setting ID, so the settings ring can announce the notice when automatic language profiles are enabled.
  - Settings UI storage and validation live in `settings.py`: `_installed_speakers_by_language()`, `_current_speech_defaults()`, `_configured_auto_language_detection()`, `_configured_auto_language_preferred()`, `_configured_auto_language_candidates()`, `_configured_auto_language_profiles()`, `_select_preferred_auto_language()`, `_refresh_preferred_language_choices()`, `_ensure_auto_language_profiles()`, `_default_voice_for_language()`, `_valid_profile_variant()`, `_load_selected_auto_language_profile()`, `_store_selected_auto_language_profile()`, `_enabled_auto_language_candidates()`, `_auto_language_status_message()`, `_refresh_auto_language_controls()`, `_refresh_auto_language_profile_value_controls()`, `_save_auto_language_settings()`, and `_refresh_synth_settings_ring(reloadSpeechSettings=False)`.
  - In Settings, profile `voice` values are speaker/variant IDs. `_current_speech_defaults()` should use the current synth `variant` before `voice`, and `_valid_profile_variant()` must validate the saved speaker ID against installed speakers for that Google language.
  - Saving automatic language settings must refresh the settings ring and warm the current voice through `_refresh_synth_settings_ring()` without copying per-language profile values into NVDA's normal speech settings. When saving changes that turn automatic language profiles off for the current Google TTS synth, call `_refresh_synth_settings_ring(reloadSpeechSettings=True)` so the live synth reloads normal Voice/Variant/Rate/RateBoost/Pitch/Volume values from `config.conf["speech"][SYNTH_NAME]` before the settings ring is rebuilt.
  - Language detection wrapper code lives in `language_detector.py`: `_DLL_NAMES`, `DetectionResult`, `_Cld2Detector.detect()`, `_Cld2Detector._load_library()`, `detect_language()`, `_candidate_for_language()`, and `_normalize_language()`. Keep x86/x64 DLL selection compatible with the running NVDA/Python architecture.
  - `detect_language()` must return only one of the enabled Google profile candidate languages, not a raw CLD2 language code. `_MIN_RELIABLE_PERCENT` and `DetectionResult.isReliable` gate CLD2 output before local script/word heuristics or preferred-language fallback are used.

### Volatile RAM speech cache

- Repeated short phrases are cached as PCM in the `SynthDriver` instance only.
- The short-phrase cache is volatile: it is not written to disk and clears when NVDA exits, NVDA restarts, or the PC reboots.
- The current short-phrase cache threshold is 5000 characters.
- Do not add persistent speech-audio caching without an explicit product decision, because cached speech can contain sensitive screen-reader text.

---

## 4. NVDA Integration Rules

- Use `synthDriverHandler.SynthDriver` patterns.
- Use NVDA-style property methods: `_get_propertyName()` and `_set_propertyName()`.
- Keep `cachePropertiesByDefault = False`.
- Preserve compatibility with NVDA 2024 through 2026 on both 32-bit (x86) and 64-bit (x64) builds. When hooking NVDA APIs whose signatures changed across these versions, use compatibility wrappers like the `setSynth` hook rather than assuming only one signature.
- When a task provides or names a local NVDA source-code directory, inspect the relevant NVDA versions available there and prefer an implementation compatible across those versions, especially for scripts, input gestures, settings dialogs, speech processing hooks, and other NVDA internals used by this add-on.
- When adding a new persisted NVDA synth setting such as `VariantSetting()`, protect existing user configs before NVDA's `SynthDriver.loadSettings()` reads the new key. Google TTS does this through `SynthDriver._ensure_variant_config_compat()` and the `loadSettings()` override: create a valid `variant` key when old configs lack it, and migrate old `voice` speaker IDs to the new model where `voice` is the Google language and `variant` is the speaker/voice ID. Without this, NVDA 2024-2026 can raise `KeyError` for the new setting and report a generic "could not load synthesizer" error.
- Follow NVDA's `VariantSetting()` pattern from eSpeak: implement `_get_variant()`, `_set_variant()`, and `_getAvailableVariants()`, and keep dynamic variant lists in the `_availableVariants` cache when needed. Do not assign to `self.availableVariants` directly, because that can shadow NVDA's auto-property and break settings loading/caching.
- NVDA compatibility code map:
  - Synth switching compatibility lives in `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_normalize_set_synth_args()`, `_call_set_synth_compat()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, and `_unpatch_synth_selection()`. These wrappers preserve compatibility with `setSynth` signatures across NVDA versions; do not replace them with a single assumed signature.
  - Voice dictionary/settings dialog hooks live in `_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_patch_read_only_text_setting()`, and `_unpatch_read_only_text_setting()`. This includes the destroyed-panel `AutoSettingsMixin.refreshGui` guard used when stale weakref callbacks run after synth switching. Always unpatch only if the current callable is the one installed by this add-on.
  - Input gesture scripts live in `GlobalPlugin.script_openVoiceManager()` and `GlobalPlugin.script_openSettings()`. `script_openVoiceManager` has the default gesture `kb:NVDA+control+shift+g`; `script_openSettings` intentionally has no default gesture so user assignments are stored by NVDA in `gestures.ini`.
- Support `synthIndexReached` and `synthDoneSpeaking` notifications.
- Speech cancellation must be responsive and must not leave browser-runtime/CDP calls hanging.
- Do not import NVDA-only modules unguarded in modules that may be imported by tests. Existing try/except patterns for `logHandler`, `addonHandler`, and `globalVars` are intentional.
- UI operations must run on the wx/NVDA GUI thread. Use `wx.CallAfter()` when returning from worker threads.
- User-facing UI strings should be wrapped in `_('...')` after `addonHandler.initTranslation()` has been initialized.

---

## 5. Threading, Responsiveness, and Cancellation

- Synthesis runs on daemon background threads named like `googleTtsForNvda.speech`.
- Voice preloading runs on a separate cancellable thread named like `googleTtsForNvda.preload`.
- The browser bridge protects WebSocket access with `threading.RLock`.
- Voice Manager download/install/remove operations must not block the main thread.
- GUI updates from workers must use `wx.CallAfter()`.
- Cancellation should be checked before long operations, between synthesis segments, and before feeding new audio.
- Cleanup must terminate browser-runtime/session resources when the synth shuts down.

Never do these on the NVDA main thread:

- HTTP downloads
- SHA-256 hashing of large voice packages
- Chromium browser runtime startup
- WebSocket/CDP waits
- speech synthesis waits
- package extraction/copying

---

## 6. Browser Runtime, CDP, and WASM Bridge Rules

### Required cross-origin isolation headers

`_BridgeRequestHandler` must send these headers on every response:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

They are required for `SharedArrayBuffer` support. Do not remove or weaken them.

### Browser engine quirks

- `offscreen_compiled.js` expects installed packages at root URLs like `/{packageId}.zvoice`.
- The bridge HTTP server must route root `.zvoice` requests to `voice_store.voice_dir()`.
- The runtime `voices.json` written at bridge startup must mark installed packages as `"remote": false` in the generated JSON model so the engine loads local packages.
- The engine init entry point is called via dynamically resolving the engine object (e.g. `window.Vh.init(extensionId)` in `20260625.1` or `window.Uh.init(...)` in earlier versions).
- The engine global symbol (`window.Vh`, `window.Uh`, etc.) is an obfuscated name from compiled browser extension code that changes across versions. `bridgeHarness.js` resolves this dynamically using `getTtsEngine()`. Do not assume any fixed global name will remain stable across future engine updates.
- `bridgeHarness.js` should remain strict-mode and IIFE-wrapped.
- Avoid changing PCM conversion semantics unless fixing a documented audio bug.

### SeaNet protected rate and pitch handling

- Apply protected high-rate behavior only to package IDs ending in `-seanet`, such as `multi-seanet`, `afh-seanet`, and `fis-seanet`.
- Do not apply the SeaNet artificial-rate path to non-SeaNet packages such as `multi`, `afh`, and `fis`.
- Keep the engine rate safer for SeaNet quality at high speeds, then apply artificial rate processing to generated PCM in `bridgeHarness.js`.
- SeaNet pitch must remain effective even when the underlying WASM engine ignores or weakens its `pitch` option. For SeaNet packages, send neutral engine pitch and carry the desired pitch as `postPitch` for browser-side PCM processing.
- Post-synthesis pitch processing must run before artificial tempo processing. The pitch pass changes duration as a side effect, and `tempoRateFromPayload()` compensates so the user's requested speech rate stays stable.
- Cache keys for short speech must include both `pitch` and `postPitch`; otherwise changing Pitch can replay cached audio generated with the old post-synthesis pitch.
- Expect higher CPU usage when users read quickly or use non-neutral pitch with SeaNet packages because the add-on performs post-synthesis audio processing.
- SeaNet rate/pitch code map:
  - Synth-side option building lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_speech_options()`, `_uses_protected_engine_rate()`, `_rate_to_chrome()`, `_pitch_to_chrome()`, and `_short_cache_key()`.
  - The Python-to-browser payload contract lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `WasmTtsEngineBridge.speak()` must pass `rate`, `artificialRate`, `pitch`, `postPitch`, `volume`, and `outputGain` together.
  - Browser-side AI audio processing lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `processPitchSamples()`, `processTempoSamples()`, `queueTempoInput()`, `flushAudioProcessors()`, `flushTempoProcessor()`, `queueAudio()`, `finishSegmentAudio()`, and `googleTtsForNvdaSpeak()`.

### CDP/WebSocket expectations

- Use the vendored websocket-client library from `websocketClientRepo/`; do not require users to install it with pip.
- Runtime binding messages are part of the audio transport contract. Preserve message shape unless both Python and JS sides are updated together.
- CDP calls should have clear timeouts or cancellation behavior where possible.
- Failures should surface as `CdpError` or logged exceptions with useful context.

---

## 7. Voice Package and Catalog Rules

### Runtime paths

| Purpose | Location |
|---|---|
| NVDA config root | `globalVars.appArgs.configPath` |
| Add-on data root | `{configPath}/googleTtsForNvda/` |
| Downloaded voices | `{configPath}/googleTtsForNvda/voices/` |
| Runtime voices.json | `{configPath}/googleTtsForNvda/runtime/voices.json` |
| Browser profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/persistentSession` |
| Temporary browser profiles | `%LOCALAPPDATA%/googleTtsForNvda/{chromeProfiles,edgeProfiles,braveProfiles}/session-<pid>-<timestamp>` |
| Master catalog | `WasmTtsEngine/20260625.1/voices.json` |

### `voice_store` contract

- `data_root() -> Path`
- `voice_dir() -> Path`
- `is_package_installed(package) -> bool`; verifies existence, size, and SHA-256
- `physically_installed_packages(catalog) -> list[VoicePackage]`; returns packages that pass on-disk installation verification
- `usable_installed_packages(packages) -> list[VoicePackage]`; filters an already verified installed package list by bundled-engine support and dependency availability without re-verifying files. A dependent package is usable only when its full `dependentVoiceId` chain is installed, supported by the bundled engine, and itself usable.
- `installed_packages(catalog) -> list[VoicePackage]`
- `download_package(package, progress?) -> Path`; only called from Voice Manager flows
- `remove_package(package)`
- `copy_existing_package(source, package) -> Path`

The SHA-256 verification cache must be invalidated after download, remove, and copy operations.

### `catalog` contract

- `VoiceCatalog.load(path?) -> VoiceCatalog`
- `VoiceCatalog(packages)` builds a filtered catalog
- `VoiceCatalog.package_for_voice(voiceId) -> VoicePackage`
- `VoiceCatalog.speaker_for_voice(voiceId) -> Speaker`
- `VoicePackage.dependentVoiceId` records a package-level dependency from packages such as `*-seanet` to their base package, such as `*-multi`, `*-afh`, or `*-fis`.
- `VoiceCatalog.to_runtime_json() -> str`

When changing catalog structure, update all code that depends on runtime JSON consumed by the WASM engine.

### Voice preloading

- Preloading lives in `SynthDriver._warm_current_voice_async()` and uses `ChromeTtsBridge.preload_voice()`; it must stay cancellable and must not download packages.
- Preload code map:
  - `_warm_current_voice_async()` starts the cancellable `googleTtsForNvda.preload` thread, optionally waits for the short Voice/Variant-change debounce, ensures the browser/CDP bridge is connected, and runs only the priority preload list.
  - `_warmup_voice_ids()` builds the priority list. When automatic language profiles are off, it uses only the current `VariantSetting()` speaker/voice ID. When automatic language profiles are on, it uses the `voice` speaker IDs from enabled automatic language profiles instead of NVDA's normal Voice/Variant values.
  - `_auto_language_candidates_in_warmup_order()` keeps enabled automatic language profiles in preload order, with the preferred profile language first when there are multiple enabled profiles.
  - `_warmup_voice_ids_for_voice()` expands a voice ID through catalog dependencies and chooses a dependency voice with a matching speaker code when possible.
  - `_voice_id_for_package()` maps a package dependency to a usable speaker voice ID.
  - `_warmup_options_for_voice_ids()` converts warm-up voice IDs into `_speech_options()` dictionaries while dropping stale or invalid voice IDs.
- Browser-side preload isolation lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: `currentSessionToken`, `beginSession()`, `isCurrentSession()`, token-aware `emit()`, `queueAudioPacket()`, `flushAudioQueue()`, `queueProcessedAudio()`, `queueAudio()`, `finishSegmentAudio()`, `scheduleWorkletEmpty()`, `flushTempoProcessor()`, `FakeAudioWorkletNode`, `googleTtsForNvdaPreload()`, `googleTtsForNvdaSpeak()`, and `stopActiveSynthesis()`.
- The Google WASM engine may reuse the same fake `AudioWorkletNode` across preload and speech sessions. While `synthesisGenerating` is true, `FakeAudioWorkletNode.port.postMessage()` must retag the port with the current session token before checking `isCurrentSession()`. If the token is only captured at construction time, later real speech sessions can start but drop every audio buffer as stale.
- Preload by selected/effective voice ID, not by every speaker in a package. The useful effect is to warm the package that contains that voice ID.
- Use a non-speaking warm-up text such as a single space; do not use a letter such as `"a"` for preload warm-up because cancelled or delayed browser/WASM audio must never be audible if it leaks past safeguards.
- Current warm-up behavior with automatic language profiles off: preload the selected `VariantSetting()` voice ID and its catalog dependencies only.
- Current warm-up behavior with automatic language profiles on: preload the voice IDs selected by enabled automatic language profiles and their catalog dependencies. If several profiles are enabled, preload the preferred profile language first, then the remaining enabled profiles. Do not also warm the normal Speech Settings Voice/Variant merely because profiles are enabled.
- Do not background-preload the remaining installed variants/voices after the priority list. The Chromium/WASM runtime has shown instability when preload work competes with ordinary focus speech, so warmup must stay limited to the voices most likely to be needed immediately.
- Before preloading a voice package, expand package dependencies through `VoicePackage.dependentVoiceId`: preload the dependency package first using the matching speaker code when possible, then preload the selected package. For example, `vi-vn-x-multi` preloads only itself, while `vi-vn-x-multi-seanet:gft` preloads `vi-vn-x-multi:gft` before `vi-vn-x-multi-seanet:gft`; the same rule applies to AFH/FIS SeaNet packages and future catalog dependencies.
- Do not infer dependencies merely from package-name suffixes. Use catalog metadata (`dependentVoiceId`) so independent packages, such as `km-kh-x-multi`, remain single-package preloads.
- Deduplicate by package ID during warm-up so different profile voices that share the same package do not preload that package repeatedly.
- Preload is an optimization, not a synth-load requirement. `_warmup_voice_ids_for_voice()` must drop unresolved or stale voice IDs instead of returning them to `_speech_options()`, and `_warm_current_voice_async()` must catch per-voice option preparation errors and skip preload when no valid options remain. A stale saved variant, stale automatic language profile voice, busy browser profile, or unavailable browser runtime must not make `SynthDriver.__init__()` fail merely because preload could not start.
- Real speech has priority over preload. `SynthDriver.speak()` is allowed to cancel the current preload thread before queueing speech, and preload must never hold the WASM/CDP runtime in a way that delays a user-triggered speech request. Do not resume preload from `_speech_loop()` after ordinary speech; Voice and Variant setters may start a new debounced priority preload directly.
- Browser-side audio, worklet callbacks, timers, tempo buffers, and queue flushing must be session-token guarded so cancelled preload audio cannot be emitted into the next real speech session.
- Python CDP event handlers must not raise `CdpCancelled` from the CDP reader thread when late audio events arrive after speech cancellation. In `bridge.py:speak()` event handling, drop audio/mark work once the request cancel event is set; let `CdpClient.request()` and the synth speech worker own cancellation reporting.
- After Voice Manager installs voice packages while Google TTS is the current synth, use the safe path: refresh the Voice Manager package lists and warm the current synth voice with `_warm_current_google_synth_voice()`. Do not hot-reload the live synth catalog or expose newly installed voices in the active settings ring unless the browser runtime/catalog refresh path is updated end-to-end; otherwise NVDA can list a voice the WASM runtime has not loaded.

### Voice Manager package flow

- `VoiceManagerDialog.refresh_lists()` should compute `_allInstalledPackages` with `voice_store.physically_installed_packages()`, cache `_allInstalledPackageIds`, compute `_allUsableInstalledPackages` and `_allUsableInstalledPackageIds` with `voice_store.usable_installed_packages()`, and then populate installed/download lists from those cached sets.
- The Installed tab Status column is the source of truth for whether an on-disk package is usable. It should clearly distinguish usable packages, unsupported packages, packages missing a required package, packages whose required package is not usable, packages that require another package, and packages that are required by installed dependents.
- The Download tab Status column should describe download-time dependency relationships, including whether a selected package requires another package, whether that required package is already usable, and whether a package is required by other downloadable packages.
- `_with_required_download_dependencies()` must expand selected downloads through the full `VoicePackage.dependentVoiceId` chain, not just the direct parent; `_dependencies_first()` must keep dependencies before dependent packages.
- During `on_download_selected()`, selected packages are already filtered by `is_package_supported_by_engine()`. Re-check dependency packages in the worker with `_missing_dependency_for_package()`: every dependency in the chain must exist in the catalog, be supported by the bundled engine, and pass `voice_store.is_package_installed()` before the dependent package is installed or counted as successful.
- Download/install progress should avoid repeated identical progress announcements, remain in the worker/`wx.CallAfter()` pattern, and avoid speaking every small percent change. Announce the busy message, broad progress milestones, and the final result rather than 0%/100% duplicates.
- After a successful install, call `_warm_current_google_synth_voice()` only when at least one package actually installed. This warms the current voice without promising that newly installed voices appear in the running synth immediately.
- Removal must operate on usable packages, not just physically installed packages. `_with_installed_dependents()` should include installed packages that depend on the selected removal set, `_dependents_first()` should remove dependents before their dependencies, and `_removes_all_usable_voices()` must check whether the remaining installed package set still contains at least one usable package.
- If removal would leave no usable voice packages and Google TTS is not the current synth, show a warning that defaults to No; the user must explicitly choose Yes to remove the last usable voice package.
- If removal would leave no usable voice packages and Google TTS is the current synth, do not remove immediately. Ask with a No default, open Select Synthesizer only after an explicit Yes, wait until Google TTS is no longer current, then remove; if the user does not switch away, keep the last usable package installed.
- During removal, `_reset_configured_voice_if_removed()` must reset both saved `voice` language and `variant` speaker ID when the configured voice package was removed, and `_apply_reset_voice_to_current_synth()` should update the live current synth when Google TTS is active.
- `_reset_auto_language_profile_variants_if_removed()` must keep automatic language profiles from pointing at removed or invalid speaker IDs by replacing them with an installed usable speaker for the same language when available.

---

## 8. Voice Manager Accessibility Rules

When modifying `voiceManager.py` or any UI:

- Keep the dialog title clear: `Google TTS Voice Manager`.
- All lists must have an accessible name via `.SetName()`.
- Buttons must use accelerator keys, for example `&Remove selected`.
- Announce successful install/remove actions with `ui.message(...)`.
- Announce download progress at roughly 25% intervals.
- `Escape` closes the manager only when no operation is active.
- Veto closing while an operation is busy.
- On open, call `wx.CallAfter(self.focus_default_control)`.
- After operations, move focus to the most relevant list/control.
- Errors must be visible to screen-reader users, not only logged.
- Per-tab **Filter by language** comboboxes must retain independent selection state per tab and announce item counts clearly when filtered.
- Ensure the **Open voice packages folder** button correctly launches the system file explorer pointing to the installed voice directory.

---

## 9. Coding Conventions

### Python

- Use `# -*- coding: utf-8 -*-` and `from __future__ import annotations` in Python files.
- Use type hints, including Python 3.10+ union syntax (`str | None`).
- Modules use `snake_case`.
- NVDA-compatible properties/methods may use `camelCase` where NVDA expects it.
- Prefer `pathlib.Path` for filesystem paths unless existing code in the local area uses strings.
- Use context managers for files, sockets, temporary resources, and locks where practical.
- Avoid broad `except Exception` unless logging and fallback behavior are intentional.

### JavaScript

- Use `"use strict"`.
- Keep `bridgeHarness.js` IIFE-wrapped.
- Avoid global names except the explicit bridge API expected by Python.
- Keep message formats stable between JS and Python.
- Validate syntax with `node --check` when editing JS.

### Documentation

- Update `googleTtsForNvda/doc/en/readme.html` when changing user-visible settings or behavior.
- Keep localized documentation in `googleTtsForNvda/doc/<language>/readme.html` when a supported translation exists.
- Known stale documentation: it still mentions removed `acceleration mode` and `transposition`; remove or correct those references when touching settings docs.

### Translation and localization

- Keep user-facing NVDA UI strings wrapped in `_('...')` after `addonHandler.initTranslation()` is initialized.
- `TRANSLATING.md` is the source of truth for translator-facing file layout, workflows, checks, and examples. Keep this section focused on agent rules and code-map details.
- Core localization files are `googleTtsForNvda/locale/nvda.pot`, `googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po`, generated `nvda.mo`, localized `manifest.ini`, localized `doc/<language>/readme.html`, and optional `locale/<language>/languageSort.json`.
- Keep localized `readme.html` terminology aligned with the locale's `nvda.po` UI translations and, where a setting label comes from NVDA itself, with NVDA's own locale translation.
- `languageSort.json` affects only Voice Manager display order for translated language names; it must not change displayed names, package IDs, catalog data, download behavior, removal behavior, or runtime JSON.
- When source strings change, refresh the template and validate/build through `build_i18n.py` as described in `TRANSLATING.md`.
- `build.bat` must keep using the non-interactive all-locale i18n path before packaging, then remove `__pycache__` created by syntax checks before packaging.
- Translation tool code map:
  - `build_i18n.py` reads source strings from Python `_()` calls and `googleTtsForNvda/manifest.ini` via `_translatable_source_messages()` and `_manifest_values()`, then writes `googleTtsForNvda/locale/nvda.pot` through `_write_pot()`.
  - `.po` parsing and validation live in `_parse_po()`, `_check_catalog()`, `_check_language_files()`, `_check_language_sort_file()`, `_parse_checks()`, and `_print_run_summary()`. These checks cover NVDA language codes, localized manifest, localized readme, UI strings, placeholders, language sorting, and obsolete active source strings.
  - Generated files are produced by `_compile_mo()` and `_write_translated_manifest()`. Do not hand-edit generated `.mo` files; update `nvda.po` and rebuild.
  - Interactive menu behavior lives in `_prompt_languages()`, `_prompt_checks()`, `_interactive_options()`, and `main()`. Keep all-locale/default choices first so blind translators can choose the broad safe option quickly.
  - NVDA locale discovery uses `DEFAULT_NVDA_LOCALE_DIRS`, `_supported_nvda_languages_from_dirs()`, and `--nvda-locale-dir`. Keep both `C:\Program Files\NVDA\locale` and `C:\Program Files (x86)\NVDA\locale` because supported NVDA versions can be x64 or older x86 installs.
- The English add-on author names are `Nguyen Anh Duc, Dao Duc Trung and Pham Hung Vuong`.
- For Vietnamese localization, write the authors as `Nguyễn Anh Đức, Đào Đức Trung và Phạm Hùng Vương`.
- When an author metadata line includes email addresses for Nguyen Anh Duc/Nguyễn Anh Đức and Dao Duc Trung/Đào Đức Trung, it must also include Pham Hung Vuong/Phạm Hùng Vương with `hungvuong106206@gmail.com`.
- For Vietnamese UI text that names standard dialog buttons, translate button labels consistently: `OK` as `Đồng ý`, `Cancel` as `Hủy bỏ`, `Yes` as `Có`, and `No` as `Không`.

---

## 10. Build, Packaging, and Verification

### Clean before packaging

```powershell
Get-ChildItem -Path googleTtsForNvda -Recurse -Directory -Filter __pycache__ |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
Remove-Item -LiteralPath googleTtsForNvda\googleTtsForNvda.nvda-addon -Force -ErrorAction SilentlyContinue
```

### Build `.nvda-addon`

The `.nvda-addon` file is a ZIP archive:

```powershell
Compress-Archive -Path googleTtsForNvda\* -DestinationPath dist\googleTtsForNvda-X.Y.Z.nvda-addon -Force
```

### Build script code map

- `build.bat` is the release packaging entry point. It reads `version` from `googleTtsForNvda\manifest.ini`, cleans stale build artifacts and `__pycache__`, checks unresolved merge conflict markers, runs `python build_i18n.py --all-languages`, runs Python and JavaScript syntax checks, rejects `.zvoice` files in the source tree, packages `googleTtsForNvda\*` into `dist\googleTtsForNvda-<version>.nvda-addon`, and cleans `__pycache__` again before exit.
- Keep the build steps ordered so generated translations are present before syntax/package checks, and so `__pycache__` created by `compileall` is removed before packaging.
- If adding a new source file type that can contain merge conflict markers or translatable/release content, update the `build.bat` conflict-marker scan patterns and the packaging/check instructions together.

### Required checks by change type

For Python changes:

```powershell
python -m compileall googleTtsForNvda
```

For JavaScript changes:

```powershell
node --check googleTtsForNvda\synthDrivers\googleTtsForNvda\web\bridgeHarness.js
```

For voice/package changes:

```powershell
rg --files googleTtsForNvda -g "*.zvoice"
```

Expected result: no files.

For package inspection:

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path dist\googleTtsForNvda-*.nvda-addon))
$zip.Entries | Select-Object -First 30 -ExpandProperty FullName
$zip.Dispose()
```

### Version management

- Version is in `googleTtsForNvda/manifest.ini`, field `version`.
- Current version: `0.4`.
- Current authors: Nguyen Anh Duc, Dao Duc Trung and Pham Hung Vuong.
- NVDA compatibility: `minimumNVDAVersion = 2024.1.0`, `lastTestedNVDAVersion = 2026.1.0`. Code and packaging should preserve support for NVDA 2024 through 2026 on both 32-bit (x86) and 64-bit (x64) builds.
- Increment `googleTtsForNvda/manifest.ini` before producing a release build.
- Do not increment version for internal experiments unless the user asks for a build/release.

---

## 11. Common Engineering Tasks

### Adding or fixing a synth setting

1. Confirm it belongs in the NVDA settings ring.
2. Add or update `_get_...` / `_set_...` methods in the synth driver.
3. Map NVDA 0-100 values to browser-runtime/WASM-compatible values in one place.
4. Preserve `RateBoostSetting()` behavior.
5. Do not re-add `Transposition` or `AccelerationMode` accidentally.
6. Update documentation and tests/checks.

### Fixing missing voice behavior

1. Check whether the package should be installed locally.
2. Use `voice_store.is_package_installed()`.
3. Do not auto-download.
4. Surface a useful error or Voice Manager prompt depending on startup vs speech-time context.
5. Confirm unavailable voices are not listed in NVDA settings.

### Touching the bridge harness

1. Update JS and Python sides together if the CDP binding protocol changes.
2. Keep cross-origin isolation headers unchanged.
3. Preserve audio chunk ordering and cancellation semantics.
4. Run `node --check`.
5. If possible, run a smoke synthesis with one installed voice.

### Touching Voice Manager

1. Keep all UI accessible by keyboard and screen reader.
2. Keep long operations on workers.
3. Use `wx.CallAfter()` for GUI updates.
4. Announce progress and outcomes with `ui.message(...)`.
5. Verify busy-state close behavior.

### Preparing a release package

1. Update `googleTtsForNvda/manifest.ini` version if this is a release.
2. Remove `__pycache__` and accidental build artifacts.
3. Verify no `.zvoice` files in source.
4. Run Python and JS syntax checks.
5. Build the `.nvda-addon` into `dist\`.
6. Inspect ZIP contents.
7. Summarize version, checks, and any untested runtime behavior.

---

## 12. Common Pitfalls

- Do not import NVDA modules at module level in test-friendly modules unless guarded.
- Do not use pip for `websocket-client`; the project vendors it.
- Do not commit or package temporary browser profiles.
- Do not commit or package `.zvoice` files.
- Do not bypass SHA-256 verification for voice packages.
- Do not forget to invalidate `_verifiedPackageCache` after package changes.
- Do not rename `window.Uh` or assume it is a stable public API.
- Do not remove COOP/COEP/CORP headers.
- Do not expose uninstalled voices in the settings ring.
- Do not make Voice Manager inaccessible by removing names, accelerators, focus handling, or progress announcements.
- Do not mix unrelated refactors with user-requested fixes.

---

## 13. Final Response Format for Coding Agents

When completing a task, respond with:

1. **Changed**: concise summary of files/behavior changed.
2. **Verified**: exact commands/checks run and their result.
3. **Notes/Risks**: anything not tested, compatibility concerns, or required follow-up.

If you could not complete a requested change, say what blocked it and provide the best partial result. Do not pretend a runtime NVDA/browser-runtime test was performed unless it actually was.

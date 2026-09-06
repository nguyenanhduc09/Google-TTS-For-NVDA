# Google TTS For NVDA — Agent Engineering Guide

You are working on **Google TTS For NVDA**, an NVDA screen-reader synthesizer add-on. Act as **Codex, a software engineering agent maintaining a production accessibility add-on**, not as an end user. Your job is to make safe, minimal, testable changes that preserve NVDA responsiveness, accessibility, packaging correctness, and the supported Chromium browser WASM TTS bridge.

Product vision: this add-on grew from the dream of making Google TTS usable as a practical, everyday NVDA synthesizer on Windows computers. Preserve that user-facing goal when changing code, documentation, packaging, and translation workflows.

This file is the operating manual for coding agents. Follow it before making or suggesting code changes.

---

## Product Wording

When writing documentation, release notes, commit messages, or user-facing summaries:

- Describe voice package startup work as an improvement, not as a complete fix. The add-on prepares the currently selected voice package sooner, but Chromium browser runtime and WASM startup still affect timing.
- Describe audio balance, clipping, harshness, or distortion work as an improvement, not as a complete fix. The processing is generic across voice packages and languages; Vietnamese may be mentioned only as a testing example, not as the only affected language.
- Describe long-text and UI-text latency/segmentation work as an improvement, not as a complete fix. Background segmentation can make speech begin sooner and sound more natural, but cache misses and engine behavior can still affect long utterances.
- SeaNet high-rate handling can be described as successful for quality preservation, with the explicit trade-off that high-speed SeaNet speech uses more CPU because generated audio is processed after synthesis.
- Use "voice package" when referring to startup/warm-up behavior. Do not imply that the add-on warms or fixes every individual speaker voice independently.

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
   - Standby browser runtime readiness: `googleTtsForNvda/synthDrivers/googleTtsForNvda/standby.py`
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
- Do not silently swallow exceptions that affect speech, downloads, or packaging. Log enough context for debugging. Always format messages into a single string when using `log.exception(...)` because NVDA's `logHandler.Logger.exception` does not accept `*args` and will raise `TypeError` if extra positional formatting arguments are supplied.
- Verify all package imports strictly match existing module exports. Because `__init__.py` suppresses `F401` for re-exports, ensure no non-existent or stale imports remain in `__init__.py` files to avoid fatal runtime `ImportError` during NVDA plugin/driver discovery.


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
│  ├─ buildInfo.json     Internal updater hotfix build metadata
│  ├─ LICENSE            Packaged copy of the add-on's GPL-2.0 license
│  ├─ synthDrivers/googleTtsForNvda/
│  │  ├─ __init__.py        SynthDriver; NVDA integration and settings ring
│  │  ├─ audio_math.py      Pure audio math, rate/pitch conversions, SeaNet rate protection
│  │  ├─ bridge.py          ChromeTtsBridge; HTTP server, browser lifecycle, CDP/WS
│  │  ├─ standby.py         Optional background browser-runtime readiness manager
│  │  ├─ watcher.py         Reusable Win32 directory-change watcher with heartbeat logging
│  │  ├─ catalog.py         VoiceCatalog, VoicePackage, Speaker models
│  │  ├─ language_detector.py
│  │  │                    CLD2-backed language detection with x86/x64 DLL selection
│  │  ├─ language_utils.py  Language normalization, NVDA special locale mappings, display names
│  │  ├─ voice_store.py     Download, copy, verify, remove voice packages
│  │  ├─ web/
│  │  │  ├─ index.html      Loaded in the headless Chromium browser runtime
│  │  │  └─ bridgeHarness.js
│  │  │     Shims chrome.* APIs, calls WASM engine, captures AudioWorklet PCM,
│  │  │     sends base64 chunks through the CDP binding
│  │  ├─ WasmTtsEngine/<ENGINE_VERSION>/
│  │  │  ├─ bindings_main.js / .wasm
│  │  │  ├─ offscreen_compiled.js
│  │  │  ├─ voices.json
│  │  │  ├─ LICENSE          Chromium WASM TTS BSD-3-Clause license
│  │  │  ├─ EIGEN_LICENSE    Eigen dependency Apache-2.0 license
│  │  │  └─ streaming_worklet_processor.js
│  │  └─ websocketClientRepo/   Vendored websocket-client library
│  ├─ globalPlugins/googleTtsForNvda/
│  │  ├─ __init__.py        Tools menu integration
│  │  ├─ settings.py        Google TTS settings panel
│  │  ├─ uiUtils.py         Shared UI utilities, read-only text focus handling, size formatting
│  │  ├─ updater.py         Add-on update manifest/download/verification core
│  │  ├─ updateGui.py       Add-on update check/download/install UI flow
│  │  └─ voiceManager.py    wx Voice Manager dialog
│  ├─ doc/
│  │  ├─ en/readme.html
│  │  └─ <locale>/readme.html
│  └─ locale/
│     └─ <locale>/
├─ build.bat
├─ build.sh
├─ build_i18n.py
├─ generate_voices_json.py
└─ readme.md
```

### Speech data flow

1. NVDA calls `SynthDriver.speak()` with a speech sequence.
2. The driver segments text, builds options for voice/rate/pitch/volume, and queues synthesis on a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures the Chromium browser runtime and CDP are connected, then evaluates `window.googleTtsForNvdaSpeak(...)` via `Runtime.evaluate`.
4. `bridgeHarness.js` calls the Google WASM TTS engine through the dynamically resolved engine object's `onSpeak`, intercepts `AudioWorkletNode` buffers, converts float32 audio to int16 PCM, and sends base64 audio chunks through the `googleTtsForNvdaBridge` CDP binding.
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
- If opening a WebView2/download URL fails, the fallback dialog must show the URL in a focusable read-only field with a real label association, size the field dynamically with the same read-only text sizing helper used by Google TTS status fields, and include a Copy link button.
- Microsoft Edge WebView2 Runtime is required only when Microsoft Edge is the selected/effective Chromium browser runtime. Google Chrome and Brave must not depend on WebView2; Chrome and Brave availability should be checked only through their browser executable/path. Status messages, fallback logic, prompts, and documentation must not imply that Chrome or Brave needs Edge WebView2.
- Keep fallback/error wording clear: if no supported Chromium browser runtime is available, the synth cannot provide speech through the Google WASM TTS engine.
- Browser runtime fallback starts with the saved/configured runtime, then continues through Chrome, Edge, and Brave with duplicates removed. For speech startup, a runtime is usable only after its executable is found, Edge WebView2 is available when the runtime is Edge, the browser process starts, the DevTools/debug port is available, the Google TTS speech page WebSocket is found, CDP domains are enabled, and the browser harness reports ready. Non-cancellation failures at any of these startup/readiness steps must clean up the failed runtime and try the next runtime; `CdpCancelled` and user cancellation must propagate without trying fallback runtimes.
- If Edge is missing WebView2, skip Edge and continue to Brave when Brave is otherwise usable. Show the WebView2 install/repair prompt only when no supported fallback runtime remains and Edge WebView2 is the blocking condition.
- Runtime status and settings UI may use executable/WebView2 snapshots, but the speech path must validate runtime usability from process startup through page WebSocket discovery and CDP/harness readiness.
- Browser-runtime code map:
  - `bridge.py` runtime constants and labels: `BROWSER_RUNTIME_CHROME`, `BROWSER_RUNTIME_EDGE`, `BROWSER_RUNTIME_BRAVE`, `BROWSER_RUNTIMES`, `DEFAULT_BROWSER_RUNTIME`, and `BROWSER_RUNTIME_LABELS`.
  - `bridge.py` runtime configuration persistence: `CONFIG_BROWSER_RUNTIME`, `CONFIG_KEEP_BROWSER_RUNTIME_READY`, `DEFAULT_KEEP_BROWSER_RUNTIME_READY`, `_set_config_value()`, `configured_browser_runtime()`, `set_configured_browser_runtime()`, `configured_keep_browser_runtime_ready()`, and `set_keep_browser_runtime_ready()`.
  - `bridge.py` availability and fallback selection: `_runtime_fallback_order()`, `_browser_candidates()`, `browser_path_for_runtime()`, `browser_executable_available()`, `edge_webview2_available()`, `browser_runtime_available()`, `browser_availability()`, `_browser_choices()`, `_find_browser_choice()`, `browser_runtime_snapshot()`, `find_browser()`, `effective_browser_runtime()`, and `edge_webview2_blocks_effective_runtime()`.
  - `bridge.py` CDP connection and harness readiness: `CdpDispatcher`, `CdpClient.request()`, `_friendly_cdp_error()`, `_TRANSIENT_RUNTIME_EVALUATE_ERRORS`, `_is_transient_runtime_evaluate_error()`, `WasmTtsEngineBridge.enable_cdp_domains()`, `WasmTtsEngineBridge.wait_until_ready()`, `ChromeTtsBridge._connectionLock`, and `ChromeTtsBridge.ensure_connection()`.
  - `bridge.py` startup cancellation path: `ChromeTtsBridge.speak()`, `ChromeTtsBridge.preload_voice()`, `ChromeTtsBridge.ensure_connection()`, `BrowserProcessManager.start_and_get_websocket_url()`, `WasmTtsEngineBridge.enable_cdp_domains()`, `WasmTtsEngineBridge.wait_until_ready()`, and `CdpClient.request()`.
  - `bridge.py` runtime health, speech-error recovery, and recycle: `_BrowserSpeechError`, `RUNTIME_MEMORY_STARTUP_GRACE_SECONDS`, `RUNTIME_MEMORY_CHECK_INTERVAL_SECONDS`, `RUNTIME_PRIVATE_BYTES_RECYCLE_THRESHOLD`, `RUNTIME_WORKING_SET_BYTES_RECYCLE_THRESHOLD`, `RUNTIME_MEMORY_RECYCLE_CONFIRMATIONS`, `_runtime_error_requires_recycle()`, `_process_tree_memory_usage()`, `BrowserProcessManager.browser_memory_usage()`, `WasmTtsEngineBridge.runtime_busy`, `WasmTtsEngineBridge.speak()`, `ChromeTtsBridge._mark_runtime_error_for_recycle()`, `ChromeTtsBridge._mark_memory_recycle_if_needed_locked()`, `ChromeTtsBridge.maybe_recycle_runtime()`, `ChromeTtsBridge.is_connected()`, `ChromeTtsBridge.safe_for_standby_release()`, and `ChromeTtsBridge.speak()`.
  - `__init__.py` synth-side recycle scheduling and fatal fallback: `SynthDriver._maybe_recycle_bridge_after_request()`, `SynthDriver._speak_worker()`, `SynthDriver._trigger_fatal_fallback()`, `SynthDriver._show_engine_library_error()`, and `SynthDriver._show_missing_chrome_error()`.
  - `globalPlugins/googleTtsForNvda/uiUtils.py` standardized error dialog: `show_runtime_error_dialog()`.
  - `__init__.py` synth/standby handoff: `SynthDriver.__init__()`, `SynthDriver.terminate()`, and `SynthDriver._bridge_safe_for_standby_release()`.
  - `settings.py` runtime settings UI: `_runtime_label()`, `_save_browser_runtime()`, `_schedule_runtime_change_after_synth_switch()`, `_clear_pending_runtime_change()`, `_apply_runtime_after_synth_switch()`, `GoogleTtsSettingsPanel._selected_runtime_choice()`, `GoogleTtsSettingsPanel._refresh_runtime_snapshot()`, `GoogleTtsSettingsPanel._format_runtime_choice()`, `GoogleTtsSettingsPanel.on_runtime_choice_changed()`, `GoogleTtsSettingsPanel._refresh_runtime_status()`, `GoogleTtsSettingsPanel._effective_runtime_message()`, and `GoogleTtsSettingsPanel._select_saved_runtime()`.
  - `settings.py` runtime-ready settings UI: `_configured_keep_browser_runtime_ready()`, `_save_keep_browser_runtime_ready()`, `GoogleTtsSettingsPanel.on_keep_browser_runtime_ready_changed()`, `GoogleTtsSettingsPanel._keep_browser_runtime_ready_status_message()`, and `GoogleTtsSettingsPanel._refresh_keep_browser_runtime_ready_status()`.
  - `standby.py` background runtime readiness: `keep_browser_runtime_ready_enabled()`, `_installed_catalog()`, `_catalog_signature()`, `_current_speech_state()`, `_warmup_voice_ids()`, `_speech_options()`, `_warmup_options()`, `_refresh_reason_requires_runtime_restart()`, `_StandbyRuntimeManager.refresh_async()`, `_StandbyRuntimeManager._run_refresh()`, `_StandbyRuntimeManager._clear_standby_locked()`, `_StandbyRuntimeManager._cancel_current_worker_locked()`, `initialize()`, `refresh_async()`, `claim_bridge()`, `note_synth_active()`, `release_synth_bridge()`, `release_synth_without_bridge()`, and `terminate()`.
  - `watcher.py` reusable Win32 directory-change watcher: `DirectoryChangeWatcher.__init__()`, `DirectoryChangeWatcher.start()`, `DirectoryChangeWatcher.stop()`, `_watch_path()`, `_signal_stop_locked()`.  The watcher uses `FindFirstChangeNotificationW` / `WaitForMultipleObjects` with `_INFINITE` timeout (fully kernel-blocked while idle).  The caller coalesces rapid bursts via the worker-already-running check in `standby.py`.
  - `globalPlugins/googleTtsForNvda/__init__.py` standby lifecycle integration: `_refresh_standby_runtime()`, `GlobalPlugin.__init__()`, `GlobalPlugin._on_post_nvda_startup()`, and `GlobalPlugin.terminate()`.
  - `voiceManager.py` package-change standby refresh: `VoiceManagerDialog._refresh_standby_google_synth_runtime()`.
  - `bridge.py` browser profile roots and profile selection: `BrowserProcessManager._browser_profile_root()`, `BrowserProcessManager._browser_profile_dir_name()`, `_profileRuntime`, `CHROME_PROFILE_DIR_NAME`, `EDGE_PROFILE_DIR_NAME`, and `BRAVE_PROFILE_DIR_NAME`.
  - `bridge.py` browser profile startup, fallback, and cleanup: `BrowserProcessManager.start_browser()`, `BrowserProcessManager.start_and_get_websocket_url()`, `BrowserProcessManager._browser_choices_or_raise()`, `BrowserProcessManager._start_browser_choice()`, `BrowserProcessManager._start_first_available_browser()`, `BrowserProcessManager._read_devtools_port()`, `_BrowserProfileInUseError`, `_browser_profile_in_use_error()`, `_get_browser_profile_dir()`, `_cleanup_old_browser_profiles()`, `_release_chrome_profile()`, and `_remove_chrome_profile()`.
  - `bridge.py` persistent profile size throttling: `PERSISTENT_PROFILE_MAX_BYTES`, `PERSISTENT_PROFILE_SIZE_CHECK_FILE_NAME`, `PERSISTENT_PROFILE_SIZE_CHECK_INTERVAL_SECONDS`, `_persistent_profile_size_check_due()`, and `_remember_persistent_profile_size_check()`.
  - `bridge.py` startup polling and diagnostics: `STARTUP_POLL_INTERVAL`, `BROWSER_WINDOW_HIDE_POLL_INTERVAL_SECONDS`, `BrowserProcessManager.get_page_websocket_url()`, `BrowserProcessManager._read_devtools_port()`, and `ChromeTtsBridge.ensure_connection()`.

- Browser-runtime behavior constraints:
  - During startup, transient `Runtime.evaluate` errors such as `Cannot find default execution context` mean the harness execution context is not stable yet; readiness polling should wait and retry, while non-transient CDP errors must still surface as `CdpError`.
  - If CDP setup or harness readiness fails for the current runtime, `ChromeTtsBridge.ensure_connection()` must close that attempt, terminate the failed browser process, skip that runtime, and try the next fallback runtime unless the failure is cancellation. When cancelled (`CdpCancelled`), `ensure_connection()` closes the unready CDP client WebSocket but preserves the running browser process so concurrent or subsequent speech requests can immediately reuse it without hitting connection refused errors (`WinError 10061`). Concurrent calls to `ensure_connection()` are serialized through `_connectionLock` with cooperative cancellation checks.
  - If the request cancel event is already set while a CDP command is being sent or while the WebSocket closes without a response, `CdpClient.request()` should raise `CdpCancelled` so synth unloads and user-initiated synth switches do not log false speech failures.
  - `CdpDispatcher` must propagate Runtime binding/event handler failures back to the owning request instead of only logging them, because audio decode/feed errors must fail the speech request and must not allow partial PCM to be cached.
  - Startup cancellation must pass the same `cancelEvent` through speech/preload, connection startup, CDP domain enable, harness readiness, and individual CDP requests.
  - Runtime/CDP timeout or closed-WebSocket failures should mark the Chromium runtime for urgent recycle after the current speech request.
  - Browser-harness speech errors must urgently recycle the Chromium/WASM runtime. `ChromeTtsBridge.speak()` may retry the same request at most once after a successful recycle and only when `_BrowserSpeechError.audioStarted` is false; never retry after any PCM packet has been emitted because that can repeat partial speech.
  - Unrecoverable runtime errors during speech or warm-up (missing supported browser runtime, or consecutive speech failures >= 2) must trigger fatal fallback via `SynthDriver._trigger_fatal_fallback()`:
    - Debounce using `self._fallbackTriggered` and `self._shutdownEvent` guards to prevent multiple fallback invocations or dialog storms.
    - Clear `_speechQueue` and call `self.cancel()` to abort pending speech and release wave playback buffers.
    - Switch to NVDA's fallback synthesizer via `synthDriverHandler.findAndSetNextSynth(self.name)` so the screen reader never remains completely silent.
    - Display an error dialog using `globalPlugins.googleTtsForNvda.uiUtils.show_runtime_error_dialog(message=..., delayMs=150)`. The 150ms delay allows the fallback synthesizer to fully take over and announce the error dialog message and native [OK] button to the user.
    - Keep zero new un-translated strings: messages must reuse existing localized messages (`_friendly_cdp_error` strings, `_("No supported Chromium browser runtime was found...")`, `_("Google TTS For NVDA could not start speech in the Chromium browser runtime.")`), and the standard OK button is localized natively by NVDA/wxWidgets.
  - Standardized OK-only error dialogs (missing browser runtime, WASM engine library error, fatal speech runtime failure) are centralized in `globalPlugins/googleTtsForNvda/uiUtils.py:show_runtime_error_dialog()` to maintain consistent UI presentation, title (`_("Google TTS For NVDA")`), error icon (`wx.OK | wx.ICON_ERROR`), parent window binding (`gui.mainFrame`), and optional delayed dispatch via `wx.CallLater`.
  - Memory threshold recycling should ignore normal Chromium/WASM cold-start spikes, require confirmed high-memory samples after the startup grace/interval, and recycle only when the synth worker reports an idle queue.
  - `SynthDriver._maybe_recycle_bridge_after_request()` should run after each non-cancelled speech request, with browser termination kept off NVDA's main thread and away from active audio callbacks.
  - `keepBrowserRuntimeReady` must default to `False`, must be saved through `bridge.py:set_keep_browser_runtime_ready()` so it follows the same active-config/base-profile path as `set_configured_browser_runtime()`, must be gated through `standby.keep_browser_runtime_ready_enabled()`, and must stay disabled in secure mode.
  - Standby refresh must be event-driven from Settings OK/Apply, NVDA startup, synth handoff, Voice Manager package changes, and `DirectoryChangeWatcher` (in `watcher.py`). Do not add periodic voice-folder rescans.
  - Forced standby refresh that replaces an active worker must cancel the old worker and detach/terminate the worker-owned `ChromeTtsBridge` instead of letting the replacement worker reuse the same CDP/browser bridge while the old request is still unwinding.
  - `standby.claim_bridge()` may hand off only a ready bridge whose `_catalog_signature(catalog)` matches the current installed package/runtime state.
  - `SynthDriver.terminate()` must call `SynthDriver._bridge_safe_for_standby_release()` before `standby.release_synth_bridge()`. Busy speech queues, active cancel events, live warmup threads, disconnected CDP clients, busy engines, and bridges marked for recycle must terminate their bridge and use `standby.release_synth_without_bridge()` instead. When runtime-ready mode remains enabled, standby must construct and warm a fresh bridge rather than retain the rejected one.
  - Standby warmup/preload must use installed packages and `ChromeTtsBridge.preload_voice()` only. It must never call `voice_store.download_package()`.
  - `BrowserProcessManager._start_browser_choice()` should bind DevTools to localhost with `--remote-debugging-address=127.0.0.1`.
  - Keep the fallback order Chrome, Edge, then Brave unless changing the product decision. If the saved runtime is Brave and Brave is unavailable, fallback must still find Chrome or Edge when they are usable.
  - `browser_runtime_snapshot()` is for UI/status snapshots only and must not make Chrome or Brave depend on WebView2.
  - Runtime status controls must use focusable read-only text sized through `bind_read_only_text_focus_announcement()`. Runtime choice preview may refresh immediately, but saving still happens only through Settings OK/Apply.
  - Brave cache/WASM profile data belongs under `braveProfiles`, not the Chrome or Edge profile roots.
  - Browser startup should try the persistent `persistentSession` profile first, then retry once with a temporary `session-<pid>-<timestamp>` profile on profile-in-use exit code 21 before trying the next runtime.
  - Profile cleanup and recursive persistent-profile scans must check the startup cancel event so synth switches do not wait for stale filesystem cleanup.
  - `DevToolsActivePort` reads must retry on `PermissionError`, `OSError`, empty content, invalid text, or out-of-range ports while the browser process remains alive. This retry path is shared by Chrome, Edge, and Brave.
  - Persistent profile reset must run per runtime profile root only. Resetting Chrome must not remove Edge or Brave data, and custom executable path basenames must not drive profile root or snapshot runtime inference.
  - The profile-size marker exists to avoid expensive recursive size scans on every startup; keep cancellation checks inside any scan that remains.
  - Window-hiding retries should be throttled separately from fast readiness polling, and startup timing logs should remain debug-only diagnostics.
  - Temporary browser profiles are a resilience fallback only. `_release_chrome_profile()` must preserve persistent profiles but remove temporary profiles, while `_remove_chrome_profile()` may delete the current profile after startup failure.

### Supported speech settings parameters

Current supported settings:

- `VoiceSetting()` — installed Google TTS language selection when automatic language profiles are off
- `VariantSetting()` — voice name/speaker selection within the selected Google TTS language when automatic language profiles are off
- `RateSetting()` — speech rate, 0-100. Non-SeaNet packages map to browser-runtime rate 0.35-2.0; `*-seanet` packages keep a protected engine rate at higher speeds and use post-synthesis artificial rate processing.
- `RateBoostSetting()` — boolean, doubles computed desired speech rate when enabled. For `*-seanet` packages at high rates, this can increase CPU usage because audio is processed after synthesis.
- `PitchSetting()` — pitch, 0-100, maps through the existing semitone curve
- `VolumeSetting()` — volume, 0-100, maps to browser-runtime volume 0.0-1.0
- `pauseMode` / `DriverSetting("&Pauses")` — string choice with `Do not shorten`, `Shorten at end of text only`, and `Shorten all pauses`. The default must remain `Do not shorten` to preserve existing speech timing unless the user opts in. Match the classic three-state pause-option behavior by exposing this in the Speech Settings dialog without putting it in NVDA's quick settings ring. This is a global Google TTS speech option and must remain available whether automatic language profiles are on or off.

Pause shortening is implemented in the synth driver after PCM audio returns from the browser runtime. The browser harness may signal hidden-segment boundaries, but it must not perform pause shortening itself; WASM files must not be modified for pause shortening.

- Match the three pause modes at implementation-only hidden boundaries: `Do not shorten` preserves all engine PCM; `Shorten at end of text only` preserves internal boundary pauses and shortens only the final text-end pause; `Shorten all pauses` shortens internal and final pauses and injects short breaks at every latency segment boundary. Latency segmentation must not make mode 0 or mode 1 behave like mode 2.
- Keep the PCM scanner streaming and block-based rather than slice/convert every individual sample, because pause shortening can run over long synthesized audio. Preserve incomplete detection blocks across `feed()` calls so arbitrary PCM packet boundaries cannot change the output. A whole 5 ms block may use the silence fast path only when every sample stays within the configured noise floor; mixed blocks must retain sample-accurate silence/audio transitions. Preserve the existing held-silence release semantics and incomplete 16-bit sample handling.
- Keep pause mode in the short-audio cache key so cached audio generated with one pause mode is never reused for another mode.
- Clear the short-audio cache when the global pause mode changes.

Pause shortening code map:

- Pure pause and streaming PCM helpers live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `PAUSE_MODE_DO_NOT_SHORTEN`, `PAUSE_MODE_SHORTEN_END_ONLY`, `PAUSE_MODE_SHORTEN_ALL`, `SHORTENED_ALL_PAUSES_KEEP_MS`, `SHORTENED_SILENCE_KEEP_MS`, `PcmSilenceShortener`, `PcmSilenceShortener._blockSizeSamples`, `PcmSilenceShortener._blockSizeBytes`, `PcmSilenceShortener._pendingBlock`, `PcmSilenceShortener._process_block()`, `PcmSilenceShortener._flush()`, `PcmSilenceShortener._hold_silence()`, `PcmSilenceShortener._release_held_silence()`, `PcmSilenceShortener.feed()`, `PcmSilenceShortener.flush_boundary()`, `PcmSilenceShortener.finish()`, `PcmLeadBuffer`, `PcmLeadBuffer.feed()`, `PcmLeadBuffer.finish()`, `create_pcm_silence_shortener()` (accepts optional `keepSilenceMs` to override the default pause duration), `pcm_bytes_for_milliseconds()`, `align_pcm_bytes()`, and `pcm_has_audible_sample()`. This module must remain importable without NVDA.
- NVDA pause settings and sentence-break timing live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_NORMAL_SENTENCE_BREAK_MS`, `_SHORTENED_SENTENCE_BREAK_MS`, `_BREAK_RATE_FACTOR_MIN`, `_BREAK_RATE_FACTOR_MAX`, `_END_OF_UTTERANCE_PAUSE_MS`, `_END_OF_UTTERANCE_RATE_FACTOR_MIN`, `_END_OF_UTTERANCE_RATE_FACTOR_MAX`, `_BREAK_RATE_TABLE`, `_interpolate_rate_factor()`, `_break_rate_factor()`, `_end_of_utterance_rate_factor()`, `_PAUSE_MODE_SETTING`, and `_pauseModes`.
- Rate-adaptive pause shortening in `_speak_text()` calculates `keepSilenceMs` from the Chrome engine rate (`options["rate"]`), which already encodes both NVDA rate (0–100) and rateBoost. At Chrome rate 1.175 (NVDA rate 50, boost off) the standard 25 ms pause is used; slower speech uses up to 45 ms, faster speech down to 15 ms. The `nvdaRate` field is included in `SHORT_AUDIO_CACHE_OPTION_FIELDS`; `rateBoost` is included in the options dict and is already encoded in the `rate` field, so the cache distinguishes different rate/boost combinations.
- Speech flow integration lives in `speak()`, `_iter_speech_chunks()` (handles `CharacterModeCommand` with inter-character spacing for spelling mode, `PhonemeCommand` with fallback text since browser TTS lacks IPA support, rate-adjusted `BreakCommand` via 5-point linear interpolation over `_BREAK_RATE_TABLE` (inverted from empirical measurements) clamped to `[0.4, 1.8]`, and punctuation-injected breaks at every segment boundary when `pauseMode == SHORTEN_ALL`), `_sentence_break_milliseconds()`, `_speak_worker()`, `_speak_text()`, `_speak_text().on_segment_end()`, `_short_cache_key()`, and `_finish_request_audio()` (synchronous `player.idle()` when no queued speech). End-of-utterance pause: `_feed_silence(int(_END_OF_UTTERANCE_PAUSE_MS * _end_of_utterance_rate_factor(rate)))` is called after `_finish_request_audio()` when `pauseMode` is `SHORTEN_END_ONLY` or `SHORTEN_ALL` and `rate > 0`, before `synthDoneSpeaking` is notified. Break silences from `BreakCommand` and segment boundary breaks are intentional pauses and do not pass through `PcmSilenceShortener`; only engine-generated PCM silence is shortened.
- Hidden-segment pause boundary signaling lives in `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py`: `SegmentEndCallback`, `WasmTtsEngineBridge.speak()`, and `ChromeTtsBridge.speak()`.
- Browser-side hidden-segment boundary events live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js`: `googleTtsForNvdaSpeak()`, `finishSegmentAudio()`, and the `segmentEnd` bridge event.
- Short-audio cache identity lives in `speech_processing.py:SHORT_AUDIO_CACHE_OPTION_FIELDS`, `short_audio_cache_key()`, and `segment_audio_cache_key()`; cleanup lives in `_clear_short_audio_cache()`.
- NVDA Speech Settings accessors live in `_get_availablePausemodes()`, `_get_pauseMode()`, and `_set_pauseMode()`.

Do **not** re-add:

- `Transposition`
- `AccelerationMode`

These were removed and must stay removed unless the user explicitly requests a new design and compatibility fix.

### Long-text segmentation

- Long-text latency segmentation should prefer natural sentence and phrase punctuation before falling back to forced length cuts.
- Preserve the fast-first-segment behavior: the first chunk of long text should stay short enough to start speech quickly, while later chunks may be larger to reduce browser/CDP/WASM overhead. Do not raise first-segment limits merely to reduce total segment count unless latency has been measured.
- Apply fast-first segmentation to medium utterances above the configured trigger as well as very long text. When punctuation is unavailable, use the fast whitespace/forced limit instead of falling back to the regular 240-character window.
- Sentences without soft punctuation remain intact as a single segment up to `FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS` (115 chars for space-delimited text) or `FAST_FIRST_PUNCTUATION_FREE_NO_SPACE_TRIGGER_CHARS` (96 chars for no-space text). This avoids unnatural boundary drops and bypasses `PcmLeadBuffer` (80ms holdback) so initial audio starts sooner.
- When soft punctuation is present, fast-first segmentation scans the early preferred window (`30..FAST_FIRST_PREFERRED_SOFT_CHARS` = 55) first, producing a compact initial segment for faster TTFA.
- Trailing orphan protection enforces `MIN_ORPHAN_SPACE_CHARS = 24` and `MIN_ORPHAN_NO_SPACE_CHARS = 16`. Cuts that leave a remainder smaller than these thresholds are rejected across `_find_soft_phrase_cut()`, `_find_whitespace_cut()`, and `_find_no_space_script_cut()`. When whitespace splitting long punctuation-free text, `FAST_FIRST_PREFERRED_WHITESPACE_CHARS = 68` targets a balanced cut instead of splitting at the extreme edge.
- A bounded initial PCM lead may be used for a live multi-segment cache miss to reduce playback underruns. Keep it small, do not apply it to ordinary single-segment speech, and preserve immediate passthrough after the lead is released.
- For browser-side hidden segments, intermediate segments should wait for the WASM engine's synthesis-end signal without also waiting for the DSP/audio queue to become idle; the final segment must still wait for full audio completion. This preserves continuous playback between hidden segments while retaining final completion and cache-integrity guarantees.
- The fake AudioWorklet `clearBuffers` handler must not reset the browser bridge's PCM queue or post-processing state. The engine also sends `clearBuffers` during normal utterance completion, before `finishSegmentAudio()` emits the held boundary tail; cancellation and session lifecycle paths already own explicit queue resets.
- Unicode punctuation helpers live in `_unicode_name()`, `_is_sentence_terminator_character()`, `_is_soft_break_character()`, `_is_colon_like_character()`, `_is_dash_like_character()`, and `_is_sentence_trailing_closer()`. Keep these cached because they run repeatedly while segmenting long text. Sentence trailing closers should accept Unicode closing/final punctuation categories (`Pe`/`Pf`) so sentence breaks can cross localized brackets and quotes.
- For scripts that often do not separate words with spaces, the synth driver may use conservative fixed-size script-window cuts after punctuation and whitespace checks have failed. This is a latency fallback, not language detection and not word segmentation.
- Keep this fallback independent from automatic language profiles, NVDA Speech Settings, speech dictionaries, and voice dictionary handling.
- Current no-space/low-space script coverage includes CJK/Han and CJK extensions, Bopomofo, Japanese Kana, Thai, Lao, Limbu, Tai Le, New Tai Lue, Buginese, Tai Tham, Khmer, Myanmar, Tibetan, Philippine Brahmic scripts, Balinese, Sundanese, Batak, Javanese, Lepcha, Yi, Rejang, Cham, Tai Viet, and similar scripts where long text commonly cannot rely on spaces as word boundaries.
- Do not add Latin, Cyrillic, Arabic, Hebrew, Ethiopic, Cherokee, Canadian Aboriginal syllabics, or other normally space-separated scripts to the no-space fallback without a specific bug report or clear evidence. For those scripts, punctuation and whitespace-based segmentation should remain the default.
- Keep hidden segments in the short-audio cache key so cached audio matches the same browser-side smoothing path.
- Segmentation regression coverage lives in `tests/segmentation_corpus.json` and `tests/test_speech_processing.py`. Keep the corpus cases for locale punctuation, abbreviations, URLs, emoji, CJK/Thai no-space text, and long sentences when changing segmentation.

### Workspace performance optimizations

The workspace version includes several performance optimizations over the download baseline. These must be preserved and tested:

- **Sentence break timing**: `_NORMAL_SENTENCE_BREAK_MS = 45` (reduced from 95ms). This is the pause between sentences in `PAUSE_MODE_SHORTEN_ALL` mode. Keep this value to maintain responsive speech flow.
- **End-of-utterance pause**: `_END_OF_UTTERANCE_PAUSE_MS = 40` (reduced from 80ms). This is the silence appended after the last sentence. Keep this value for faster utterance completion.
- **PCM lead buffer**: `LIVE_MULTI_SEGMENT_LEAD_MS = 80` (reduced from 120ms). This controls how much audio is buffered before playback starts for multi-segment cache misses. Keep this value for faster streaming start.
- **Preload resume delay**: `_PRELOAD_RESUME_DELAY_SECONDS = 0.15` (reduced from 0.45s). This is the debounce delay before resuming preload after voice/variant changes. Keep this value for faster voice switching.
- **Segment flush threshold**: `_FLUSH_GROUP_CHARS_THRESHOLD = 120`. When `PAUSE_MODE_SHORTEN_ALL` is active, soft phrase boundaries trigger intermediate flushes only when accumulated text exceeds this threshold. This balances segment-cache hit-rate against too many tiny CDP round-trips. Keep this threshold to maintain hidden segments for caching while avoiding excessive speech groups.
- **Speech request coalescing**: `_speak_text()` checks `cancelEvent.is_set()` at the top and returns immediately if the request was already cancelled. This skips CDP round-trips for requests that would be interrupted before audio plays. Keep this check to reduce wasted browser resources.
- **Standby early-exit**: `_StandbyRuntimeManager._run_refresh()` skips the full refresh if the bridge is already warm, the catalog signature matches, and the refresh reason does not require a runtime restart. Keep this optimization to avoid unnecessary browser restarts.
- **End-of-utterance silence guard**: End-of-utterance silence is only fed when the total string length in the speech sequence exceeds 40 characters. Keep this guard to avoid wasted silence for short focus announcements.

Performance optimization code map:

- `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` timing constants: `_NORMAL_SENTENCE_BREAK_MS`, `_END_OF_UTTERANCE_PAUSE_MS`, `_PRELOAD_RESUME_DELAY_SECONDS`, `_FLUSH_GROUP_CHARS_THRESHOLD`.
- `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` coalescing: `_speak_text()` early `cancelEvent.is_set()` check.
- `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` flush logic: `_iter_speech_chunks()` inner loop with threshold-based `accumulatedChars` check.
- `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py` lead buffer: `LIVE_MULTI_SEGMENT_LEAD_MS`.
- `googleTtsForNvda/synthDrivers/googleTtsForNvda/standby.py` early-exit: `_StandbyRuntimeManager._run_refresh()` warm-bridge skip.
- Performance benchmarks and regression tests live in `tests/test_performance.py` and `tests/test_segmentation_benchmarks.py`. Refer to `tests/README.md` for test coverage and benchmark thresholds.
- Official script and sentence-terminal tables live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/unicode_data.py`, generated by `generate_unicode_data.py` from pinned UCD and CLDR releases. Do not hand-edit the generated tables. The generator must select the exact `catalog.py:ENGINE_VERSION`, not a lexicographically latest engine directory. Automatic language-profile fallback reads these tables.
- `TAILORED_SENTENCE_TERMINATORS` may contain only supported-language or common sentence endings deliberately excluded from UCD `Sentence_Terminal`. Keep it disjoint from the generated official set. Phrase-level punctuation such as commas, colons, dashes, and semicolons belongs in `SOFT_BREAK_CHARS`, not in sentence-terminal tailoring.
- Long-text segmentation code map:
  - Pure segment-length policy, Unicode punctuation helpers, abbreviation handling, and segmentation flow live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: `TextSegmenter`, `DEFAULT_TEXT_SEGMENTER`, `TextSegmenter.split_text_for_latency()`, `TextSegmenter.sanitize_speech_text()`, `TextSegmenter.find_sentence_splits()`, `TextSegmenter.iter_text_segments_for_latency()`, `TextSegmenter.iter_indexed_text_segments()`, `TextSegmenter.spoken_bridge_segments()`, `TextSegmenter.looks_like_url_token()`, `TextSegmenter.should_pause_after_segment()`, `TextSegmenter._needs_spoken_segment_space()`, `TextSegmenter._sentence_terminator_stays_with_token()`, `TextSegmenter._period_stays_with_previous_token()`, `TextSegmenter._period_is_numeric_separator()`, `TextSegmenter._iter_forced_latency_segments()`, `TextSegmenter._iter_soft_phrase_segments()`, `TextSegmenter._find_soft_phrase_cut()`, `TextSegmenter._find_whitespace_cut()`, `TextSegmenter._find_forced_latency_cut()`, `TextSegmenter._find_no_space_script_cut()`, `TextSegmenter._no_space_script_segment_limit()`, `TextSegmenter._extend_cut_over_combining_marks()`, `TextSegmenter._min_orphan_chars()`, `TextSegmenter._is_forced_soft_break()`, `TextSegmenter._is_contextual_soft_phrase_cut()`, `_is_no_space_script_character()`, `COMMON_ABBREVIATIONS`, `is_sentence_terminator_character()`, `_is_sentence_terminator_character()`, `_is_soft_break_character()`, `_is_sentence_trailing_closer()`, `MIN_ORPHAN_SPACE_CHARS`, `MIN_ORPHAN_NO_SPACE_CHARS`, `FAST_FIRST_PUNCTUATION_FREE_TRIGGER_CHARS`, `FAST_FIRST_PUNCTUATION_FREE_NO_SPACE_TRIGGER_CHARS`, `FAST_FIRST_PREFERRED_SOFT_CHARS`, `FAST_FIRST_PREFERRED_WHITESPACE_CHARS`, `FAST_FIRST_SEGMENT_MIN_CHARS`, `REGULAR_SEGMENT_MIN_CHARS`, `FAST_FIRST_SEGMENT_MAX_CHARS`, `FAST_FIRST_SEGMENT_TRIGGER_CHARS`, `REGULAR_SEGMENT_MAX_CHARS`, `SEAMLESS_UTTERANCE_MAX_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS`, `FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD`, `SOFT_PHRASE_SEGMENT_MIN_CHARS`, `SOFT_PHRASE_SEGMENT_MAX_CHARS`, `SOFT_PHRASE_SEGMENT_LOOKAHEAD`, `URL_TOKEN_SEGMENT_MAX_CHARS`, `FORCED_SEGMENT_MIN_CHARS`, `FORCED_SEGMENT_FORWARD_LOOKAHEAD`, `FORCED_SEGMENT_HARD_MAX_CHARS`, `NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS`, `NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO`, and `NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD`. The single-letter abbreviation guard in `_period_stays_with_previous_token()` is restricted to ASCII letters via `isascii()` so non-Latin single-letter words before periods are not mistaken for abbreviations. This module must remain importable without NVDA.
  - NVDA speech-sequence grouping and adapters live in `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py`: `_iter_speech_chunks()`, `_split_text_for_latency()`, `_sanitize_speech_text()`, `_iter_indexed_text_segments()`, `_iter_text_segments_for_latency()`, `_spoken_bridge_segments()`, `_looks_like_url_token()`, and `_should_pause_after_segment()`.
  - Python hidden-segment boundary context lives in `bridge.py`: `WasmTtsEngineBridge.speak(..., hasPreviousSegment=...)` and `ChromeTtsBridge.speak(..., hasPreviousSegment=...)`.
  - Browser-side hidden-segment continuity lives in `bridgeHarness.js`: `googleTtsForNvdaSpeak()`, `waitForWasmEnd()`, `waitForSynthesisComplete()`, `FakeAudioWorkletNode`, `stopActiveSynthesis()`, `hasPreviousSegment`, `hasBoundaryContext`, `smoothSegmentBoundaries`, `boundaryHoldSamples`, `queueProcessedAudio()`, `finishSegmentAudio()`, `heldBoundarySamples`, and the `segmentEnd` bridge event. The held boundary must be emitted unchanged so Python alone applies the selected pause mode.

- Unicode data generation code map:
  - `generate_unicode_data.py` pinned inputs and bundled-catalog selection: `DEFAULT_ENGINE_ROOT`, `DEFAULT_OUTPUT`, `DEFAULT_CATALOG_MODULE`, `_configured_voices_json()`, `_supported_locales()`, `_ucd_version()`, and `main()`.
  - `generate_unicode_data.py` UCD/CLDR parsing, range composition, and module rendering: `_parse_ucd_records()`, `_script_aliases()`, `_likely_scripts()`, `_supported_language_scripts()`, `_merge_ranges()`, `_format_ranges()`, `_format_codepoints()`, and `_render_module()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/unicode_data.py` generated tables: `UNICODE_VERSION`, `CLDR_VERSION`, `SUPPORTED_LANGUAGE_SCRIPTS`, `SCRIPT_RANGES`, `LANGUAGE_SCRIPT_RANGES`, and `SENTENCE_TERMINAL_CODEPOINTS`.

### Status/help control accessibility

- Status/help lines in Speech Settings, the Google TTS settings category, and similar NVDA dialogs must be reachable by Tab and read by NVDA. Use focusable read-only controls for these status lines instead of plain `wx.StaticText`.
- Focusable status/help controls must have a real label association, not only `SetName()`, so NVDA announces the status/help name before the read-only edit role. If the status/help text can wrap or span multiple lines, size the read-only edit to the current content within sensible width and line limits, and keep the whole value available for arrow-key review inside the edit. Do not add delayed automatic readback with `wx.CallLater()` or `ui.message()` merely to re-speak the status text.
- Runtime, automatic language profile, and update sections in the Google TTS settings category should use the shared `_SettingsGroup` / `_add_settings_group()` wrapper in `settings.py` so grouped controls are parented to the static box returned by `GetStaticBox()` and all three groups keep the same layout pattern. After changing a dynamic status field or showing/hiding grouped controls, call `GoogleTtsSettingsPanel._refresh_settings_layout()` so NVDA updates the settings panel scroll geometry.
- Apply this rule to Chromium browser runtime status, automatic language profile status, Speech Settings notices, current-browser notices, and future status/help fields with similar behavior.
- The helper name `bind_read_only_text_focus_announcement()` is kept for compatibility; current behavior sizes the control and relies on normal read-only edit focus/review behavior instead of delayed extra speech.
- Keep `_hide_google_tts_auto_profile_speech_controls()` hiding normal speech controls only while automatic language profiles replace them.
- Preserve the `_patch_read_only_text_setting()` guard that ignores only the wx "has been deleted" refresh on destroyed panels, and keep other `RuntimeError` failures visible.
- Manual URL fallback dialogs must use real label association, read-only `wx.TextCtrl` sized through `bind_read_only_text_focus_announcement(..., minLines=2, maxLines=5)` without a fixed width, and a Copy link button.
- Accessibility helper map:
  - Google TTS settings grouped controls live in `settings.py`: `_SettingsGroup`, `_SettingsGroup.addLabeledControl()`, `_SettingsGroup.addCheckBox()`, `_SettingsGroup.addButton()`, `GoogleTtsSettingsPanel._add_settings_group()`, and `GoogleTtsSettingsPanel._refresh_settings_layout()`.
  - Read-only status/help sizing lives in `googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py`: `_from_dip()`, `_estimate_wrapped_line_count()`, `_estimate_text_width()`, `_max_read_only_text_width()`, `_read_only_text_target_width()`, `resize_read_only_text_for_content()`, and `bind_read_only_text_focus_announcement()`.
  - Speech Settings read-only notices live in `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_make_read_only_text_setting_control()`, `_patch_read_only_text_setting()`, `_unpatch_read_only_text_setting()`, and `_hide_google_tts_auto_profile_speech_controls()`.
  - Manual URL fallback dialogs live in `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py`: `_show_manual_web_url_dialog()`.

### Add-on updater

- Add-on update checks, downloads, checksum verification, temporary update files, and NVDA add-on installation must not depend on the lifetime of the Google TTS Settings panel.
- `autoUpdateCheckOnStartup` lives under `CONFIG_SECTION = "googleTtsForNvda"` and defaults to `False`.
- Hotfix build metadata lives in `googleTtsForNvda/buildInfo.json`. `baseVersion` must match `googleTtsForNvda/manifest.ini` `version`, and `updateBuild` increments inside the same public version. When releasing a new public version, reset `updateBuild` to `1` for that new `baseVersion`.
- Update availability must compare public/base versions first. Only when the remote and installed base versions are the same may the updater compare `updateBuild`. Missing build metadata is build `0` for backward compatibility.
- `size` and `sha256` verify the downloaded package and must not be used as the signal that a hotfix exists. The release manifest generator must calculate them from the final `.nvda-addon` package.
- `stable.json` must keep the legacy fields consumed by the 0.4 updater (`version`, `url`, `size`, `sha256`, `minimumNVDAVersion`, `lastTestedNVDAVersion`, and release notes) while adding schema 2 build fields (`baseVersion`, `displayVersion`, and `updateBuild`) for 0.5 and newer.
- Automatic startup update checks must run at most once per NVDA startup via `core.postNvdaStartup`; manual checks from Settings must still work when automatic checks are disabled.
- When an update check is already running, the manual Settings button must be disabled or ignored until the current check finishes. Toggling `autoUpdateCheckOnStartup` while a check is running must affect only future NVDA startups and must not cancel, restart, or alter the current check.
- Manual checks show OK/error dialogs for no-update or check failures. Automatic startup checks delete temporary JSON/files and stay silent for no-update or initial check failures.
- The update information dialog must focus the read-only update information/changelog field by default, not Yes/No. Escape remains No. Size this field dynamically through `bind_read_only_text_focus_announcement(..., minLines=5, maxLines=15)` without a fixed width so long localized change logs can use the shared content-based width/height sizing.
- If the user chooses No in the update information dialog, delete the temporary manifest JSON and remember no update state.
- Downloaded add-on files must be verified against both `size` and `sha256` before opening NVDA's add-on installer.
- If the user cancels a download, delete `stable.json`, `stable.json.download`, partial downloads, downloaded `.nvda-addon` files, and the temporary update folder.
- If the user cancels NVDA's add-on install dialog, delete the downloaded `.nvda-addon` and remove the temporary update folder if empty.
- Google TTS Settings must call `updateGui` for updater work and must not own manifest/download/install state.
- Add-on updater code map:
  - Release manifest generation lives in `make_update_manifest.py`: `ADDON_ID`, `BUILD_INFO_FILE_NAME`, `DEFAULT_CHANNEL`, `DEFAULT_OUTPUT`, `DEFAULT_URL_TEMPLATE`, `TRANSLATED_MANIFEST_RE`, `IGNORED_SEARCH_DIRS`, `ManifestError`, `_parse_manifest()`, `_read_addon_manifest()`, `_read_addon_build_info()`, `_read_release_notes_by_locale()`, `_sha256()`, `_version_sort_key()`, `_iter_addon_packages()`, `_find_addon_package()`, `build_update_manifest()`, `_parse_args()`, and `main()`.
  - Manifest/download core lives in `googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py`: `ADDON_ID`, `BUILD_INFO_FILE_NAME`, `UPDATE_CHANNEL`, `UPDATE_MANIFEST_URL`, `MAX_UPDATE_MANIFEST_BYTES`, `MAX_UPDATE_PACKAGE_BYTES`, `DOWNLOAD_CHUNK_SIZE`, `UpdateError`, `UpdateCancelled`, `UpdateInfo`, `UpdateCheckResult`, `DownloadedUpdate`, `current_version()`, `current_update_build()`, `_is_update_available()`, `_parse_update_info()`, `fetch_update_manifest()`, `check_for_update()`, `download_update()`, `remove_update_manifest()`, `remove_downloaded_update()`, `cleanup_update_files()`, and `format_size()`.
  - Runtime UI/controller flow lives in `googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py`: `CONFIG_AUTO_UPDATE_CHECK`, `DEFAULT_AUTO_UPDATE_CHECK`, `_nvda_translate()`, `automatic_update_check_enabled()`, `set_automatic_update_check_enabled()`, `update_check_in_progress()`, `update_status_message()`, `register_update_status_listener()`, `_notify_update_status_changed()`, `_UpdateAvailableDialog`, `_UpdateDownloadDialog`, `_UpdateCheckController`, `_begin_update_check()`, `_finish_update_check()`, `_start_update_check()`, `start_manual_update_check()`, and `start_automatic_update_check()`.
  - Google TTS Settings updater integration lives in `settings.py`: `GoogleTtsSettingsPanel.makeSettings()`, `GoogleTtsSettingsPanel.onSave()`, `GoogleTtsSettingsPanel.on_check_for_updates()`, `GoogleTtsSettingsPanel.on_auto_update_check_changed()`, `GoogleTtsSettingsPanel._refresh_update_controls()`, `GoogleTtsSettingsPanel._restore_update_check_focus()`, and `GoogleTtsSettingsPanel._on_destroy()`.
  - Global plugin startup integration lives in `globalPlugins/googleTtsForNvda/__init__.py`: `config.conf.spec[...]` for `updateGui.CONFIG_AUTO_UPDATE_CHECK`, `GlobalPlugin.__init__()`, `GlobalPlugin._on_post_nvda_startup()`, and `GlobalPlugin.terminate()`.

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
- Automatic language profiles should use the bundled CLD2 detector (`googleTtsForNvda/synthDrivers/googleTtsForNvda/language_detector.py` and `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/`) as the primary detector. `language_detector.py` must select `cld2_x86.dll` for 32-bit NVDA/Python and `cld2_x64.dll` for 64-bit NVDA/Python, with `cld2.dll` only as a 64-bit compatibility fallback copy.
- Bundled CLD2 DLLs are rebuilt from the upstream `CLD2Owners/cld2` source recorded in `googleTtsForNvda/synthDrivers/googleTtsForNvda/cld2/README.txt`. If replacing these DLLs, replace all architecture-specific files together (`cld2_x86.dll`, `cld2_x64.dll`, and the fallback `cld2.dll`), preserve the small exported C ABI used by `language_detector.py` (`cld2_detect_language` and `cld2_version`), update the README provenance, and smoke-test detection on both English and Vietnamese text. Do not drop the x86 DLL while `minimumNVDAVersion` supports 32-bit NVDA.
- Keep CLD2 Windows DLLs on the documented Microsoft Visual C++ Build Tools/MSVC build path unless there is a deliberate product decision to change toolchains. Do not replace them with MinGW-w64 builds merely for convenience, because the MSVC rebuild is intended to produce cleaner Windows DLLs and reduce antivirus false-positive risk. Do not claim this completely eliminates false positives unless the signed/released package has actually been verified with the relevant scanners.
- Do not use unreliable CLD2 results as authoritative for unclear text. If CLD2 is unavailable or uncertain, the synth may use conservative local language signals and then the enabled preferred language; it must not fall back to normal Speech Settings values while automatic language profiles are on.
- Keep language-code equivalence mapping centralized in `language_detector.py`, not duplicated in the synth driver. CLD2 output and Google voice package/profile codes do not always match exactly; preserve strong aliases such as `tl`/`fil-PH`, `jw`/`jv-ID`, `iw`/`he-IL`, `no`/`nn`/`nb-NO`, `ar`/`ar-XA`, and Simplified/Traditional/Cantonese Chinese mappings. Candidate matching should prefer exact normalized codes, then strong aliases, then root/family fallbacks so broad Chinese matching cannot steal a more precise `zh-Hant`, `cmn-TW`, or `yue-HK` profile.
- `LangChangeCommand` values from NVDA or the focused app must not change Google TTS voices or profiles. While Google TTS is the current synthesizer and automatic language profiles are enabled, filter unmarked external language-change commands before NVDA language reporting and text processing so reported language names and character/symbol processing come from Google TTS automatic profile detection, not NVDA/app language-change commands. While profiles are off, preserve unmarked NVDA/app language-change commands so NVDA can still use them for language reporting and character/symbol processing, including punctuation and symbol dictionaries, but the synth driver must ignore those commands so they cannot change Google TTS voices. Because NVDA can rebuild add-on-created language commands without preserving custom attributes, the synth driver may trust unmarked `LangChangeCommand` values only while automatic language profiles are enabled and the Google speech filter has already removed external commands; when profiles are off, unmarked commands must be ignored by the synth driver.
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
- When automatic language profiles are enabled, `SynthDriver.supportedSettings` should hide normal `VoiceSetting`, `VariantSetting`, `RateSetting`, `RateBoostSetting`, `PitchSetting`, and `VolumeSetting`, and expose a read-only notice that directs the user to the Google TTS For NVDA settings category. Keep global `pauseMode` visible and effective for all speech in this mode. Refresh the settings ring after saving the automatic language profile setting.
- Vietnamese UI/docs must translate "Google TTS for NVDA" as "Google TTS Cho NVDA" when it is user-facing text.
- Automatic language profile implementation constraints:
  - `_filter_auto_language_speech_sequence()` is registered with `speech.extensions.filter_speechSequence`, moved to the start of that filter chain when NVDA supports `moveToEnd(..., last=False)`, and must keep `*args, **kwargs` for future NVDA filter arguments.
  - `_filter_auto_language_speech_sequence()` filters unmarked external `LangChangeCommand` values for Google TTS while automatic language profiles are enabled, and preserves unmarked NVDA/app language-change commands when profiles are off.
  - `_auto_language_for_process_text()` must ignore the incoming NVDA/app locale when automatic language profiles are enabled. Use Google TTS detection over enabled profile candidates, then the preferred profile fallback; do not reintroduce locale-derived profile selection from NVDA `LangChangeCommand`.
  - Character, spelling, and symbol wrappers inside `_patch_auto_language_voice_dictionary()` must keep `*args, **kwargs`, forward unknown arguments to NVDA, preserve temporary config overlays, and restore NVDA speech config values.
  - `_get_availableNotices()` must be keyed by the notice message, not the static notice setting ID, so the settings ring can announce the notice when automatic language profiles are enabled.
  - `_refresh_auto_language_profile_value_controls()` must preserve group-local layout item tracking so expanding preferred/profile controls updates the automatic-language group without overlapping the following Updates group.
  - In Settings, profile `voice` values are speaker/variant IDs. `_current_speech_defaults()` should use the current synth `variant` before `voice`, and `_valid_profile_variant()` must validate saved speaker IDs against installed speakers for that Google language.
  - Saving automatic language settings must refresh the settings ring and warm the current voice through `_refresh_synth_settings_ring()` without copying per-language profile values into NVDA's normal speech settings.
  - When saving changes that turn automatic language profiles off for the current Google TTS synth, call `_refresh_synth_settings_ring(reloadSpeechSettings=True)` so the live synth reloads normal Voice/Variant/Rate/RateBoost/Pitch/Volume values from `config.conf["speech"][SYNTH_NAME]` before the settings ring is rebuilt.
  - `language_detector.py` must keep x86/x64 DLL selection compatible with the running NVDA/Python architecture and keep `cld2.dll` as a compatibility fallback after the architecture-specific DLL name.
  - `detect_language()` must return only one of the enabled Google profile candidate languages, not a raw CLD2 language code. `_MIN_RELIABLE_PERCENT` and `DetectionResult.isReliable` gate CLD2 output before local script/word heuristics or preferred-language fallback are used.

- Automatic language profile code map:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` synth-side selection: `_auto_detect_profile_for_text()`, `_auto_language_profile()`, `_auto_language_profile_for_language()`, `_auto_language_candidates()`, `_auto_language_preferred()`, `_auto_language_candidate_for_language()`, `_detect_auto_language()`, `_language_token_signal()`, `_voice_for_language()`, `_voice_matches_language()`, `_current_speaker_id()`, and `_speech_options()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_profiles.py` pure Unicode-script fallback: `script_ranges_for_language_root()`, `token_has_character_in_ranges()`, and `language_script_signal()`. This module must remain importable without NVDA.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` profile-aware warm-up ordering: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_options_for_voice_ids()`, `_warmup_voice_ids_for_voice()`, and `_voice_id_for_package()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` NVDA speech filter and profile language commands: `_filter_auto_language_speech_sequence()`, `_register_auto_language_speech_filter()`, `_unregister_auto_language_speech_filter()`, `_google_lang_change_command()`, `_google_lang_change_language()`, `_nvda_locale_for_language()`, and `_auto_language_for_process_text()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` voice dictionary and character/spelling overlays: `_patch_auto_language_voice_dictionary()`, `_unpatch_auto_language_voice_dictionary()`, `_auto_profile_character_settings_for_language()`, `_auto_profile_character_context_for_text()`, `_single_auto_profile_character_settings()`, `process_text_with_auto_voice_dictionary()`, `get_spelling_speech_with_auto_profile()`, and `should_use_spelling_functionality_with_auto_profile()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` settings-ring notice integration: `SynthDriver.supportedSettings`, `ReadOnlyTextDriverSetting`, `_get_availableNotices()`, `_auto_language_notice_message()`, `_get_notice()`, and `_set_notice()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py` settings UI storage and validation: `_installed_speakers_by_language()`, `_current_speech_defaults()`, `_configured_auto_language_detection()`, `_configured_auto_language_preferred()`, `_configured_auto_language_candidates()`, `_configured_auto_language_profiles()`, `_select_preferred_auto_language()`, `_refresh_preferred_language_choices()`, `_ensure_auto_language_profiles()`, `_default_voice_for_language()`, `_valid_profile_variant()`, `_load_selected_auto_language_profile()`, `_store_selected_auto_language_profile()`, `_enabled_auto_language_candidates()`, `_auto_language_status_message()`, `_refresh_auto_language_controls()`, `_refresh_auto_language_profile_value_controls()`, `_save_auto_language_settings()`, and `_refresh_synth_settings_ring(reloadSpeechSettings=False)`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_utils.py` normalized locale resolution and display name helpers: `SPECIAL_NVDA_LOCALES`, `normalize_language()`, `normalize_language_code()`, `get_nvda_locale_for_language()`, `nvda_locale_exists()`, `resolve_nvda_locale()`, and `get_language_display_name()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/audio_math.py` pure audio conversions and speech options: `OUTPUT_GAIN_MAKEUP`, `PROTECTED_ENGINE_RATE`, `MIN_ARTIFICIAL_RATE`, `MAX_ARTIFICIAL_RATE`, `rate_to_chrome()`, `pitch_to_chrome()`, `uses_protected_engine_rate()`, and `build_speech_options()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_detector.py` detection wrapper: `_DLL_DIR`, `_DLL_NAMES`, `_LANGUAGE_ALIASES`, `_CHINESE_LANGUAGE_ROOTS`, `_LANGUAGE_REDIRECTS` (dialect → best available redirect), `DetectionResult`, `_Cld2Detector.detect()`, `_Cld2Detector._load_library()`, `detect_language()`, `_candidate_for_language()`, `language_match_keys()`, `_language_aliases()`, `_language_family()`, `_language_root()`, `_normalize_language()`, and `redirect_language()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/voice_store.py` package validation: `is_package_installed()` (SHA256 + file size + persistent cache), `validate_package_catalog()` (catalog integrity check for speaker IDs, languages, and metadata consistency).


### Volatile RAM speech cache

- Repeated short phrases are cached as PCM in the `SynthDriver` instance only.
- The short-phrase cache is volatile: it is not written to disk and clears when NVDA exits, NVDA restarts, or the PC reboots.
- The current short-phrase cache threshold is 5000 characters.
- Hidden browser-side segments are part of the cache identity and are capped for caching. They describe boundaries over the same text and therefore count once, not twice, against the character threshold.
- The current short-phrase cache RAM cap is 150 MB.
- Do not add persistent speech-audio caching without an explicit product decision, because cached speech can contain sensitive screen-reader text.
- Cache integrity rule: only cache PCM for a complete, successful, structurally valid speech request. If speech is cancelled, interrupted by a newer utterance, aborted by warm-up/runtime shutdown, the browser bridge does not report successful completion, Runtime binding audio is malformed, an audio callback/feed path fails, or the collected PCM has no audible samples, discard collected PCM so partial or invalid audio such as a cut-off focus announcement cannot be replayed later as a full utterance.
- `SynthDriver._speak_text()` may collect PCM during live synthesis, but it may call `_put_cached_audio()` only after `ChromeTtsBridge.speak()` returns a successful result with browser-side `done`, the request cancel event is still clear, the PCM length is meaningful, and `_pcm_has_audible_sample()` passes.
- Segment PCM may be cached only after the complete bridge request succeeds and every expected boundary is present. Its key must include previous/next boundary context. Reuse only a continuous prefix, and disable independent segment caching when artificial-rate or post-pitch processing carries overlap state across boundaries.
- Keep the cache key aligned with every option that can change rendered PCM, including hidden segments, pause mode, pitch, post-synthesis pitch/rate, and output gain.
- Clearing the volatile PCM cache after a Chromium/WASM runtime recycle is acceptable because it releases memory and avoids retaining audio across a runtime-health reset; do not write the cache to disk.
- Runtime binding events must update `audioChunks` and `done`, forward `segmentEnd`, measure first-audio/inter-packet/segment-resume timing without logging speech text, drop late audio after cancellation, validate `sampleRate`, base64 payloads, and even PCM byte length, and propagate event-handler failures through `_handlerErrors`.
- Preserve the browser-side `done` event as the signal that all queued audio for the session has been flushed, but do not emit `done` or return success after an engine error event. Intermediate hidden segments may use `waitForWasmEnd()` to avoid an extra queue-idle gap; only the final segment may rely on `waitForSynthesisComplete()` before the final processor/queue flush and `done` event.

- Volatile speech cache code map:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` cache read/write: `SynthDriver._speak_text()`, `_short_cache_key()`, `_segment_cache_key()`, `_get_cached_audio()`, `_put_cached_audio()`, and `_clear_short_audio_cache()` (also called from `_set_voice()` and `_set_variant()` on voice/variant change).
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py` cache limits, keys, completion validation, and live lead buffering: `SHORT_CACHE_MAX_CHARS`, `SHORT_CACHE_MAX_HIDDEN_SEGMENTS`, `short_audio_cache_key()`, `segment_audio_cache_key()`, `is_complete_speech_result()`, `LIVE_MULTI_SEGMENT_LEAD_MS`, and `PcmLeadBuffer`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` RAM caps and eviction logging: `_SHORT_CACHE_MAX_ITEMS`, `_SHORT_CACHE_MAX_BYTES`, and `_SHORT_CACHE_STATS_LOG_INTERVAL`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` runtime recycle cleanup: `SynthDriver._maybe_recycle_bridge_after_request()` and `_clear_short_audio_cache()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` completion and payload validation: `CdpDispatcher`, `CdpClient.request()`, and `WasmTtsEngineBridge.speak()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` browser completion and async-error signaling: `handleTtsEngineEvent()`, `synthesisErrorMessage`, `googleTtsForNvdaSpeak()`, `waitForWasmEnd()`, `waitForSynthesisComplete()`, `finishSegmentAudio()`, `flushAudioProcessors()`, `flushAudioQueue()`, and the `segmentEnd` bridge event.

- `tests/README.md` is the source of truth for standalone regression tests, test architecture, shared test fixtures (`test_support.py`), performance benchmarks, fuzz testing, and individual test module coverage. All standalone tests run without NVDA.


---

## 4. NVDA Integration Rules

- Use `synthDriverHandler.SynthDriver` patterns.
- Use NVDA-style property methods: `_get_propertyName()` and `_set_propertyName()`.
- Keep `cachePropertiesByDefault = False`.
- Preserve compatibility across the NVDA version range declared in `googleTtsForNvda/manifest.ini` on both 32-bit (x86) and 64-bit (x64) builds. When hooking NVDA APIs whose signatures changed across supported versions, use compatibility wrappers like the `setSynth` hook rather than assuming only one signature.
- When a task provides or names a local NVDA source-code directory, inspect the relevant NVDA versions available there and prefer an implementation compatible across those versions, especially for scripts, input gestures, settings dialogs, speech processing hooks, and other NVDA internals used by this add-on.
- Any add-on callable that replaces, wraps, or is called directly by an NVDA API should accept and forward `*args, **kwargs` unless NVDA's API contract requires an exact signature. This is intentional compatibility hardening for both old and new NVDA releases; do not simplify wrappers back to a fixed signature just because one inspected NVDA version currently works.
- When adding a new persisted NVDA synth setting such as `RateBoostSetting()`, `VariantSetting()`, or `_PAUSE_MODE_SETTING`, protect existing user configs before NVDA's `SynthDriver.loadSettings()` reads the new key. Google TTS does this through `SynthDriver._ensure_config_compat()` (aliased as `_ensure_variant_config_compat()`) and the `loadSettings()` override: populate default values for all standard supported settings (`rateBoost`, `pauseMode`, `pitch`, `volume`, etc.) when old configs lack them, create a valid `variant` key when old configs lack it, and migrate old `voice` speaker IDs to the new model where `voice` is the Google language and `variant` is the speaker/voice ID. Without this, supported NVDA builds can raise `KeyError` for missing settings in `SynthDriver.loadSettings()` (evaluating `c[s.id] is None`) and report a generic "could not load synthesizer" error.
- Follow NVDA's `VariantSetting()` pattern from eSpeak: implement `_get_variant()`, `_set_variant()`, and `_getAvailableVariants()`, and keep dynamic variant lists in the `_availableVariants` cache when needed. Do not assign to `self.availableVariants` directly, because that can shadow NVDA's auto-property and break settings loading/caching.
- Synth switching wrappers preserve compatibility with `setSynth` signatures across NVDA versions; do not replace them with a single assumed signature.
- Voice dictionary/settings dialog wrappers for NVDA internals must keep `*args, **kwargs`, forward unknown arguments, keep the destroyed-panel `AutoSettingsMixin.refreshGui` guard, and unpatch only if the current callable is the one installed by this add-on.
- Voice dictionary loading wrappers must wrap `speechDictHandler.loadVoiceDict` only when that attribute exists and must keep `*args, **kwargs`.
- Speech-processing wrappers from `_patch_auto_language_voice_dictionary()` must keep and forward `*args, **kwargs` for both older supported NVDA signatures and newer signatures with extra spelling arguments.
- Synth driver, settings panel, global plugin, wx event, and script entry points must keep their compatibility `*args, **kwargs` wrappers unless NVDA requires an exact signature.
- NVDA audio output device storage differs across supported releases: NVDA 2024 stores the selected output device in `config.conf["speech"]["outputDevice"]`, while NVDA 2025 and newer store it in `config.conf["audio"]["outputDevice"]`. Google TTS must read the correct active key before constructing `nvwave.WavePlayer`, and must recreate the player when the real cross-version API `nvwave.isInError()` reports a device-change/error state. Keep only a guarded `audioDeviceError()` fallback for compatibility with unexpected downstream builds.
- `script_openVoiceManager` has the default gesture `kb:NVDA+control+shift+g`; `script_openSettings` intentionally has no default gesture so user assignments are stored by NVDA in `gestures.ini`.
- NVDA's custom `logHandler.Logger.exception()` has the signature `def exception(self, msg: str = "", exc_info: Literal[True] | _excInfo_t | BaseException = True, **kwargs):` which does NOT accept `*args` like Python's standard library `logging.Logger.exception`. Calling `log.exception("... %s", technicalDetail, exc_info=True)` passes `technicalDetail` as positional arg 2 into `exc_info`, raising a fatal `TypeError: Logger.exception() got multiple values for argument 'exc_info'` that crashes worker threads (such as `googleTtsForNvda.speech`). Always format the log message into a single string (e.g. via f-string `f"Google TTS speech failed: {technicalDetail}"`) without extra positional formatting arguments.
- NVDA compatibility code map:
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` synth switching: `_normalize_set_synth_args()`, `_call_set_synth_compat()`, `_set_synth_with_google_tts_voice_prompt()`, `_patch_synth_selection()`, and `_unpatch_synth_selection()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` voice dictionary/settings dialog hooks: `_patch_voice_dictionary_dialog()`, `_unpatch_voice_dictionary_dialog()`, `_patch_read_only_text_setting()`, and `_unpatch_read_only_text_setting()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` voice dictionary loading: `_VoiceDictionarySynthProxy`, `_load_voice_dictionary_for_voice()`, `_current_google_tts_speaker_id()`, `_patch_google_tts_voice_dictionary_loading()`, and `_unpatch_google_tts_voice_dictionary_loading()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` speech processing compatibility: `_filter_auto_language_speech_sequence()`, `_patch_auto_language_voice_dictionary()`, `process_text_with_auto_voice_dictionary()`, `get_spelling_speech_with_auto_profile()`, and `should_use_spelling_functionality_with_auto_profile()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` synth driver NVDA entry points: `SynthDriver.terminate()`, `SynthDriver.speak()`, `SynthDriver._speak_worker()`, `SynthDriver.cancel()`, `SynthDriver.pause()`, and `SynthDriver.loadSettings()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` audio output compatibility: `SynthDriver._current_output_device()`, `SynthDriver._default_output_device()`, `SynthDriver._audio_device_error()`, `SynthDriver._create_wave_player()`, and `SynthDriver._ensure_current_output_device()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` fallback & dialog helpers: `SynthDriver._trigger_fatal_fallback()`, `SynthDriver._show_engine_library_error()`, and `SynthDriver._show_missing_chrome_error()`.
  - `globalPlugins/googleTtsForNvda/uiUtils.py` dialog helpers: `show_runtime_error_dialog()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py` settings panel entry points: `GoogleTtsSettingsPanel.makeSettings()` and `GoogleTtsSettingsPanel.onSave()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` global plugin entry points: `GlobalPlugin.terminate()`, `GlobalPlugin.on_open_voice_manager()`, `GlobalPlugin.script_openVoiceManager()`, and `GlobalPlugin.script_openSettings()`.
  - `googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py` input gesture map: `GlobalPlugin.__gestures`, `GlobalPlugin.script_openVoiceManager()`, and `GlobalPlugin.script_openSettings()`.
  - `tests/test_synth_driver_helpers.py` NVDA compatibility tests: `FatalFallbackTests` (verifies unrecoverable error detection, queue clearing, cancellation, fallback synth invocation, and delayed dialog display), and `ConfigCompatTests.test_speech_failure_logging_compatible_with_nvda_logger` (verifies NVDA `logHandler.Logger.exception` single-string compatibility without positional arguments).
  - `tests/check_nvda_api_contracts.py` static contract runner: `CategoryResult`, `SourceTree`, `discover_trees()`, `check_tree()`, and `main()`; it checks all add-on integration categories and reports high-risk `setSynth`, `WavePlayer`, output-device, `nvwave.isInError`, and `AutoSettingsMixin.refreshGui` contracts without importing NVDA.
  - `tests/NVDA_CHROMIUM_MANUAL_CHECKLIST.md` is the release-test checklist for real NVDA, Chromium/WASM startup, audible PCM, focus announcements, Voice Manager, settings, updater, and lifecycle behavior that static inspection cannot prove.

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
- The engine init entry point is called via dynamically resolving the engine object (for example, minified globals such as `window.Xh`, `window.Vh`, or `window.Uh` seen across different engine builds).
- The engine global symbol (`window.Xh`, `window.Vh`, `window.Uh`, etc.) and its internal instance properties (such as the property holding `AudioContext`, which has varied between `val.i`, `val.j`, etc.) are obfuscated names from compiled browser extension code that change across engine builds. `bridgeHarness.js` resolves the engine dynamically using `getTtsEngine()` and validates candidates in `isTtsEngineInstance()` by scanning properties dynamically for `FakeAudioContext`/`audioWorklet` rather than hardcoding minified property names. Do not assume any fixed global or property name will remain stable across future engine updates.
- Some engine builds need language/package readiness before preload or speech. Keep `ensureLanguageReady(engine, lang)` before `engine.onSpeak(...)` in both `googleTtsForNvdaPreload()` and `googleTtsForNvdaSpeak()`, even if another bundled engine appears to work without this extra step.
- Voice support must be verified from a loadable `.zvoice` package and browser/WASM runtime behavior, not from `voices.json` alone.
- Current engine package compatibility deliberately excludes package IDs containing `locomel` or `lemonbalm` because the bundled engine reports those package families as unavailable even when their `.zvoice` files verify.
- WASM engine version, file checks, and catalog selection live in `catalog.py`: `ENGINE_VERSION`, `ENGINE_ROOT`, `ENGINE_DIR`, `CATALOG_PATH`, `REQUIRED_ENGINE_FILES`, `UNSUPPORTED_ENGINE_PACKAGE_ID_PARTS`, `inspect_engine_library()`, and `is_package_supported_by_engine()`.
- `googleTtsForNvdaSpeak()` must call `stopActiveSynthesis()` on both successful completion and speech errors so audio buffers, timers, session tokens, and engine `onStop()` state do not leak into the next request.
- Keep the first browser-side PCM packet small for startup continuity, use larger steady-state packets to reduce CDP/base64 overhead, and keep Python-side validation in sync with any message-shape or sample-rate changes.
- Post-engine PCM loudness uses a fixed makeup gain derived from the user's Volume slider and a stateless soft limiter for clipping protection. Do not add adaptive per-packet or per-sample gain, gain-release envelopes, or RMS-target tracking: those can make steady speech audibly pump between louder and quieter levels. Loudness processing must not change rate, pitch, segmentation, pause shortening, or voice/package selection.
- Browser harness audio code map:
  - Engine startup/readiness/error cleanup: `isTtsEngineInstance()`, `getTtsEngine()`, `ensureEngineInitialized()`, `ensureLanguageReady()`, `stopActiveSynthesis()`, `googleTtsForNvdaPreload()`, and `googleTtsForNvdaSpeak()`.
  - PCM packetization: `buffersToPcmBase64()`, `appendSamples()`, `audioPacketSampleTarget()`, `queueAudioPacket()`, and `flushAudioQueue()`.
  - Synth-to-browser fixed gain: `OUTPUT_GAIN_MAKEUP` (1.70 in `audio_math.py`; used via `build_speech_options()` from `standby.py` and `__init__.py`; aliased as `_OUTPUT_GAIN_MAKEUP` in `__init__.py`; clamped in `bridgeHarness.js` `outputGainFromPayload()`), `SynthDriver._speech_options()`, and the `outputGain` payload field in `WasmTtsEngineBridge.speak()`.
  - Fixed loudness and clipping protection: `outputGainFromPayload()`, `buffersToPcmBase64()`, and `limitSample()`.

- `bridgeHarness.js` should remain strict-mode and IIFE-wrapped.
- Avoid changing PCM conversion semantics unless fixing a documented audio bug.

### SeaNet protected rate and pitch handling

- Apply protected high-rate behavior only to package IDs ending in `-seanet`, such as `multi-seanet`, `afh-seanet`, and `fis-seanet`.
- Do not apply the SeaNet artificial-rate path to non-SeaNet packages such as `multi`, `afh`, and `fis`.
- Keep the engine rate safer for SeaNet quality at high speeds, then apply artificial rate processing to generated PCM in `bridgeHarness.js`.
- SeaNet package families must use the same fixed post-engine makeup gain and stateless clipping protection as their base packages. The Volume slider must scale the fixed gain identically for `*-multi-seanet`, `*-afh-seanet`, `*-fis-seanet`, `*-multi`, `*-afh`, and `*-fis`.
- SeaNet pitch must remain effective even when the underlying WASM engine ignores or weakens its `pitch` option. For SeaNet packages, send neutral engine pitch and carry the desired pitch as `postPitch` for browser-side PCM processing.
- Post-synthesis pitch processing must run before artificial tempo processing. The pitch pass changes duration as a side effect, and `tempoRateFromPayload()` compensates so the user's requested speech rate stays stable.
- Cache keys for short speech must include both `pitch` and `postPitch`; otherwise changing Pitch can replay cached audio generated with the old post-synthesis pitch.
- Expect higher CPU usage when users read quickly or use non-neutral pitch with SeaNet packages because the add-on performs post-synthesis audio processing.
- SeaNet rate/pitch code map:
  - Synth-side option building: `_speech_options()`, `_uses_protected_engine_rate()`, `_rate_to_chrome()`, `_pitch_to_chrome()`, and `_short_cache_key()`.
  - Python-to-browser payload: `WasmTtsEngineBridge.speak()`.
  - Browser-side loudness/rate/pitch processing: `outputGainFromPayload()`, `limitSample()`, `postPitchFactorFromPayload()`, `tempoRateFromPayload()`, `resetPitchProcessor()`, `processPitchSamples()`, `processTempoSamples()`, `queueTempoInput()`, `flushAudioProcessors()`, `flushTempoProcessor()`, `queueAudio()`, `finishSegmentAudio()`, and `googleTtsForNvdaSpeak()`.

### CDP/WebSocket expectations

- Use the vendored websocket-client library from `websocketClientRepo/`; do not require users to install it with pip.
- Keep add-on-internal imports package-relative and keep the vendored WebSocket client under its private package namespace. Do not insert `websocketClientRepo` into `sys.path`, import a shared top-level `websocket`, or accept `wsaccel`/`python_socks` modules exposed by another add-on.
- Runtime binding messages are part of the audio transport contract. Preserve message shape unless both Python and JS sides are updated together.
- CDP calls should have clear timeouts or cancellation behavior where possible.
- Failures should surface as `CdpError` or logged exceptions with useful context.
- Runtime binding event handlers run on the CDP reader thread, but their failures belong to the active request. `CdpDispatcher` must wake the registered request and store handler errors so `CdpClient.request()` can fail fast, send any fast-stop callback, and prevent partial audio from reaching the volatile speech cache.
- Bundled dependency isolation code map:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` and `websocketClientRepo/__init__.py`: package-relative `websocket` import and private vendored package root.
  - `websocketClientRepo/websocket/_abnf.py`: `native_byteorder` and `_mask()`.
  - `websocketClientRepo/websocket/_http.py`: `HAVE_PYTHON_SOCKS`, `ProxyError`, `ProxyTimeoutError`, `ProxyConnectionError`, `ProxyType`, and `_start_proxied_socket()`.
  - `websocketClientRepo/websocket/_utils.py`: `_create_bundled_utf8_validator()` and `validate_utf8()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/language_profiles.py`: package-relative `LANGUAGE_SCRIPT_RANGES` import from `unicode_data.py`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/speech_processing.py`: package-relative `SENTENCE_TERMINAL_CODEPOINTS` import from `unicode_data.py`.

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
| Master catalog | `WasmTtsEngine/<ENGINE_VERSION>/voices.json` |

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

`data_root()` and `voice_dir()` may cache their resolved `Path` objects, but they must still ensure the runtime directories exist when called so user-deleted config folders can recover without restarting NVDA.

The SHA-256 verification cache must be invalidated after download, remove, and copy operations.

- Keep `is_package_installed()` correct for a single package.
- Keep `physically_installed_packages()` optimized for catalog scans by listing the voice directory once, reusing file stat results, forgetting stale package cache entries, and batching persistent cache writes.
- `voice_store.py` code map:
  - Runtime path caches: `_dataRootCache`, `_voiceDirCache`, `data_root()`, and `voice_dir()`.
  - Verification cache state: `_verifiedPackageCache` and `_persistentVerifiedPackageCache`.
  - Persistent verification cache IO: `_load_persistent_verification_cache()`, `_save_persistent_verification_cache()`, and `_persistent_cache_matches()`.
  - Verification cache mutation: `_remember_verified_package()` and `_forget_verified_package()`.
  - Package verification and batch scans: `_check_package_file_installed()`, `_voice_files_by_name()`, `is_package_installed()`, `physically_installed_packages()`, and `usable_installed_packages()`.

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
- `_warm_current_voice_async()` starts the cancellable `googleTtsForNvda.preload` thread, waits for the short Voice/Variant-change or SynthDriver startup debounce (`_PRELOAD_RESUME_DELAY_SECONDS`), ensures the browser/CDP bridge is connected with the preload cancel event, treats `CdpCancelled` as normal cancellation, and runs only the priority preload list.
- Preload code map:
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` preload worker: `SynthDriver._warm_current_voice_async()`, `SynthDriver.speak()`, and `SynthDriver.cancel()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` voice-id planning: `_warmup_voice_ids()`, `_auto_language_candidates_in_warmup_order()`, `_warmup_voice_ids_for_voice()`, `_voice_id_for_package()`, and `_warmup_options_for_voice_ids()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/__init__.py` preload option building: `_speech_options()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/bridge.py` bridge preload entry points: `ChromeTtsBridge.preload_voice()`, `ChromeTtsBridge.ensure_connection()`, and `WasmTtsEngineBridge.preload_voice()`.
  - `googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js` browser-side session isolation: `currentSessionToken`, `beginSession()`, `isCurrentSession()`, token-aware `emit()`, `queueAudioPacket()`, `flushAudioQueue()`, `queueProcessedAudio()`, `queueAudio()`, `finishSegmentAudio()`, `scheduleWorkletEmpty()`, `flushTempoProcessor()`, `FakeAudioWorkletNode`, `googleTtsForNvdaPreload()`, `googleTtsForNvdaSpeak()`, and `stopActiveSynthesis()`.

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
- Real speech has priority over preload. `SynthDriver.speak()` is allowed to cancel the current preload thread before queueing speech, and preload must never hold the WASM/CDP runtime in a way that delays a user-triggered speech request. `SynthDriver.cancel()` must set active/queued speech cancel events and call `ChromeTtsBridge.cancel_current()` before stopping the player so user cancellation or synth switching can send a fast stop to the Chromium runtime. Do not resume preload from `_speech_loop()` after ordinary speech; Voice and Variant setters may start a new debounced priority preload directly.
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
- Code maps in this file are technical indexes, not user documentation. Each code-map entry should name the owning file/module and the exact functions, classes, constants, or persisted keys involved. Put behavior rules, invariants, accessibility requirements, examples, and rationale in adjacent rule bullets or a `behavior constraints` block, not inside the code-map line.

### Translation and localization

- Keep user-facing NVDA UI strings wrapped in `_('...')` after `addonHandler.initTranslation()` is initialized.
- `TRANSLATING.md` is the source of truth for translator-facing file layout, workflows, checks, and examples. Keep this section focused on agent rules and code-map details.
- Core localization files are `googleTtsForNvda/locale/nvda.pot`, `googleTtsForNvda/locale/<language>/LC_MESSAGES/nvda.po`, generated `nvda.mo`, localized `manifest.ini`, localized `doc/<language>/readme.html`, and optional `locale/<language>/languageSort.json`.
- Keep localized `readme.html` terminology aligned with the locale's `nvda.po` UI translations and, where a setting label comes from NVDA itself, with NVDA's own locale translation.
- `languageSort.json` affects only Voice Manager display order for translated language names; it must not change displayed names, package IDs, catalog data, download behavior, removal behavior, or runtime JSON.
- When source strings change, refresh the template and validate/build through `build_i18n.py` as described in `TRANSLATING.md`.
- `googleTtsForNvda/locale/nvda.pot` is a locally generated, Git-ignored template. A fresh clone is expected not to contain it. Translators may update selected existing locale `.po` files with `build_i18n.py --update-po` plus `--language <language>` or `--all-languages`, or generate only the template with `--extract-template` for a translation editor; the generated `.pot` file must not be committed.
- `build.bat` and `build.sh` must not run `build_i18n.py`; i18n is a separate explicit workflow. Release/package work that changes localized output should run `build_i18n.py` before invoking the package build script.
- Generated `.mo` files are build outputs. Do not hand-edit them; update `nvda.po` and rebuild.
- Keep all-locale/default choices first in the interactive i18n menu so blind translators can choose the broad safe option quickly.
- Keep both `C:\Program Files\NVDA\locale` and `C:\Program Files (x86)\NVDA\locale` in NVDA locale discovery because supported NVDA versions can be x64 or older x86 installs.
- Translation tool code map:
  - `build_i18n.py` source extraction and POT writing: `_translatable_source_messages()`, `_manifest_version()`, `_manifest_values()`, and `_write_pot()`.
  - `build_i18n.py` locale PO template updates: `_find_msgmerge()`, `_purge_obsolete_po_entries()`, `_update_po_from_template()`, `_prompt_languages()`, `--update-po`, `--language`, `--all-languages`, and `--msgmerge`.
  - `build_i18n.py` `.po` parsing and validation: `_parse_po()`, `_check_catalog()`, `_check_language_files()`, `_check_language_sort_file()`, `_parse_checks()`, and `_print_run_summary()`.
  - `build_i18n.py` generated output writers: `_compile_mo()` and `_write_translated_manifest()`.
  - `build_i18n.py` interactive menu: `_prompt_languages()`, `_prompt_checks()`, `_interactive_options()`, and `main()`.
  - `build_i18n.py` NVDA locale discovery: `DEFAULT_NVDA_LOCALE_DIRS`, `_supported_nvda_languages_from_dirs()`, and `--nvda-locale-dir`.

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

### Build script contract

- `build.bat` is the release packaging entry point. It reads `version` from `googleTtsForNvda\manifest.ini`, cleans stale build artifacts and `__pycache__`, checks unresolved merge conflict markers, runs Python and JavaScript syntax checks, rejects `.zvoice` files in the source tree, packages `googleTtsForNvda\*` into `dist\googleTtsForNvda-<version>.nvda-addon`, and cleans `__pycache__` again before exit.
- `build.sh` is the WSL/Linux equivalent entry point, kept in the repo root next to `build.bat`. It runs the same 7 steps in the same order and prints the same `[n/7]`/`[ERROR]` markers. When changing build steps, update both scripts together; `build.sh` cannot run or test NVDA/Chromium runtime behavior, only build/check/package.
- Packaged license file code map: `LICENSE` repository GPL-2.0 copy; `googleTtsForNvda/LICENSE` packaged GPL-2.0 copy; `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/LICENSE` Chromium WASM TTS BSD-3-Clause copy; `googleTtsForNvda/synthDrivers/googleTtsForNvda/WasmTtsEngine/<ENGINE_VERSION>/EIGEN_LICENSE` Eigen Apache-2.0 copy.
- Conflict-marker scan targets live in the root file lists in `build.bat` and `build.sh`; keep `build.sh` included in the Windows `build.bat` scan. The scan should match real Git conflict markers only: `<<<<<<<` at the start of a line with either end-of-line or following text, exactly `=======`, and `>>>>>>>` at the start of a line with either end-of-line or following text. Do not make every line beginning with `=` fail, because vendored/documentation files may use underline-style headings.
- Keep the build steps ordered so syntax/package checks happen before packaging, and so `__pycache__` created by `compileall` is removed before packaging.
- If adding a new source file type that can contain merge conflict markers or translatable/release content, update the `build.bat` conflict-marker scan patterns and the packaging/check instructions together, and mirror the same file-type list in `build.sh`.

### Required checks and CI workflow

- `CONTRIBUTING.md` is the source of truth for CI workflows, local verification commands (Ruff, Mypy, Unittest), toolchain setup, and PowerShell helpers.
- `tests/README.md` is the source of truth for test architecture, individual test modules, and standalone test execution.

To run the complete verification suite locally:

```powershell
python -m ruff check ; python -m ruff format --check ; python -m mypy --config-file mypy.ini --explicit-package-bases --exclude "websocketClientRepo" googleTtsForNvda/synthDrivers/ tests/ googleTtsForNvda/globalPlugins/googleTtsForNvda/__init__.py googleTtsForNvda/globalPlugins/googleTtsForNvda/settings.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updateGui.py googleTtsForNvda/globalPlugins/googleTtsForNvda/uiUtils.py googleTtsForNvda/globalPlugins/googleTtsForNvda/updater.py googleTtsForNvda/globalPlugins/googleTtsForNvda/voiceManager.py ; python -m unittest discover -s tests -v
```

Before packaging, verify no `.zvoice` files are in the source tree:

```powershell
rg --files googleTtsForNvda -g "*.zvoice"
```

For package inspection:

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path dist\googleTtsForNvda-*.nvda-addon))
$zip.Entries | Select-Object -First 30 -ExpandProperty FullName
$zip.Dispose()
```

### Version management

- Version is in `googleTtsForNvda/manifest.ini`, field `version`.
- Current authors: Nguyen Anh Duc, Dao Duc Trung and Pham Hung Vuong.
- NVDA compatibility is declared in `googleTtsForNvda/manifest.ini` through `minimumNVDAVersion` and `lastTestedNVDAVersion`. Code and packaging should preserve the declared supported range on both 32-bit (x86) and 64-bit (x64) builds.
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

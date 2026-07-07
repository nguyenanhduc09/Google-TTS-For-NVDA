# Google TTS For NVDA — Agent Engineering Guide

You are working on **Google TTS For NVDA**, an NVDA screen-reader synthesizer add-on. Act as **Codex, a software engineering agent maintaining a production accessibility add-on**, not as an end user. Your job is to make safe, minimal, testable changes that preserve NVDA responsiveness, accessibility, packaging correctness, and the Google Chrome WASM TTS bridge.

This file is the operating manual for coding agents. Follow it before making or suggesting code changes.

---

## 1. Agent Operating Mode

### Default behavior

- Treat every request as an engineering task: inspect the relevant files, reason about side effects, make the smallest useful change, and verify it.
- Codex may inspect, edit, test, build, and package files in this workspace and may use online research when the task requires current external technical context.
- Codex can run local smoke tests and syntax checks, but must not claim a real interactive NVDA/Chrome user test unless that exact runtime test was actually performed.
- Prefer implementation over explanation when the user asks for code changes.
- Do not redesign the architecture unless the request explicitly requires it or the current design blocks correctness.
- Preserve existing public behavior unless the user asks to change it.
- Keep changes localized. Avoid broad refactors mixed with bug fixes.
- Do not introduce network access, downloads, telemetry, background services, or new dependencies without a clear requirement.
- Never block NVDA's main thread with synthesis, Chrome, filesystem-heavy, or network work.

### Before editing

1. Identify the affected layer:
   - NVDA synth driver: `synthDrivers/googleTtsForNvda/__init__.py`
   - Chrome/CDP bridge: `synthDrivers/googleTtsForNvda/bridge.py`
   - Voice catalog and storage: `catalog.py`, `voice_store.py`
   - Browser harness: `web/bridgeHarness.js`, `web/index.html`
   - Voice Manager UI: `globalPlugins/googleTtsForNvda/voiceManager.py`
   - Packaging/docs: `manifest.ini`, `doc/en/readme.html`, build scripts
2. Read nearby code before changing it.
3. Check this guide for non-negotiable constraints.
4. Plan tests before editing.

### While editing

- Maintain compatibility with NVDA add-on conventions.
- Preserve thread cancellation paths and cleanup paths.
- Add concise comments only where behavior is non-obvious, especially for Chrome/WASM quirks.
- Keep user-facing strings translatable with `_('...')` where used in NVDA UI code.
- Do not silently swallow exceptions that affect speech, downloads, or packaging. Log enough context for debugging.

### After editing

- Run the smallest relevant checks first, then broader checks if packaging or cross-module behavior changed.
- Report exactly what changed, what was tested, and what could not be tested.
- Mention any remaining risk or follow-up work.

---

## 2. Project Overview

Workspace: `C:\Trung\projects\Chrome_TTS`

**Google TTS For NVDA** exposes Google's Chrome WASM TTS voices to NVDA through:

- an NVDA synth driver,
- a managed headless Chrome process,
- a Chrome DevTools Protocol (CDP) WebSocket bridge,
- a browser-side JavaScript harness that captures PCM audio from the WASM engine,
- runtime-downloaded `.zvoice` voice packages stored in the user's NVDA config directory.

### High-level architecture

```text
NVDA process
├─ synthDrivers/googleTtsForNvda/
│  ├─ __init__.py        SynthDriver; NVDA integration and settings ring
│  ├─ bridge.py          ChromeTtsBridge; HTTP server, Chrome lifecycle, CDP/WS
│  ├─ catalog.py         VoiceCatalog, VoicePackage, Speaker models
│  ├─ voice_store.py     Download, copy, verify, remove voice packages
│  ├─ web/
│  │  ├─ index.html      Loaded in headless Chrome
│  │  └─ bridgeHarness.js
│  │     Shims chrome.* APIs, calls WASM engine, captures AudioWorklet PCM,
│  │     sends base64 chunks through the CDP binding
│  ├─ WasmTtsEngine/20260625.1/
│  │  ├─ bindings_main.js / .wasm
│  │  ├─ offscreen_compiled.js
│  │  ├─ voices.json
│  │  └─ streaming_worklet_processor.js
│  └─ websocketClientRepo/   Vendored websocket-client library
└─ globalPlugins/googleTtsForNvda/
   ├─ __init__.py        Tools menu integration
   └─ voiceManager.py    wx Voice Manager dialog
```

### Speech data flow

1. NVDA calls `SynthDriver.speak()` with a speech sequence.
2. The driver segments text, builds options for voice/rate/pitch/volume, and queues synthesis on a background thread.
3. `ChromeTtsBridge.speak()` verifies the required voice package is installed, ensures Chrome and CDP are connected, then evaluates `window.googleTtsForNvdaSpeak(...)` via `Runtime.evaluate`.
4. `bridgeHarness.js` calls the Chrome WASM TTS engine through `window.Uh.onSpeak`, intercepts `AudioWorkletNode` buffers, converts float32 audio to int16 PCM, and sends base64 audio chunks through the `googleTtsForNvdaBridge` CDP binding.
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

### Supported settings ring parameters

Current supported settings:

- `VoiceSetting()` — voice selection
- `RateSetting()` — speech rate, 0-100, maps to Chrome rate 0.35-2.0
- `RateBoostSetting()` — boolean, doubles computed Chrome rate when enabled
- `PitchSetting()` — pitch, 0-100, maps through the existing semitone curve
- `VolumeSetting()` — volume, 0-100, maps to Chrome volume 0.0-1.0

Do **not** re-add:

- `Transposition`
- `AccelerationMode`

These were removed and must stay removed unless the user explicitly requests a new design and compatibility fix.

### Volatile RAM speech cache

- Repeated short phrases are cached as PCM in the `SynthDriver` instance only.
- The short-phrase cache is volatile: it is not written to disk and clears when NVDA exits, NVDA restarts, or the PC reboots.
- The current short-phrase cache threshold is 200 characters.
- Do not add persistent speech-audio caching without an explicit product decision, because cached speech can contain sensitive screen-reader text.

---

## 4. NVDA Integration Rules

- Use `synthDriverHandler.SynthDriver` patterns.
- Use NVDA-style property methods: `_get_propertyName()` and `_set_propertyName()`.
- Keep `cachePropertiesByDefault = False`.
- Support `synthIndexReached` and `synthDoneSpeaking` notifications.
- Speech cancellation must be responsive and must not leave Chrome/CDP calls hanging.
- Do not import NVDA-only modules unguarded in modules that may be imported by tests. Existing try/except patterns for `logHandler`, `addonHandler`, and `globalVars` are intentional.
- UI operations must run on the wx/NVDA GUI thread. Use `wx.CallAfter()` when returning from worker threads.
- User-facing UI strings should be wrapped in `_('...')` after `addonHandler.initTranslation()` has been initialized.

---

## 5. Threading, Responsiveness, and Cancellation

- Synthesis runs on daemon background threads named like `googleTtsForNvda.speech`.
- Voice preloading runs on a separate cancellable thread named like `googleTtsForNvda.preload`.
- The Chrome bridge protects WebSocket access with `threading.RLock`.
- Voice Manager download/install/remove operations must not block the main thread.
- GUI updates from workers must use `wx.CallAfter()`.
- Cancellation should be checked before long operations, between synthesis segments, and before feeding new audio.
- Cleanup must terminate Chrome/session resources when the synth shuts down.

Never do these on the NVDA main thread:

- HTTP downloads
- SHA-256 hashing of large voice packages
- Chrome startup
- WebSocket/CDP waits
- speech synthesis waits
- package extraction/copying

---

## 6. Chrome, CDP, and WASM Bridge Rules

### Required cross-origin isolation headers

`_BridgeRequestHandler` must send these headers on every response:

```text
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
```

They are required for `SharedArrayBuffer` support. Do not remove or weaken them.

### Chrome engine quirks

- `offscreen_compiled.js` expects installed packages at root URLs like `/{packageId}.zvoice`.
- The bridge HTTP server must route root `.zvoice` requests to `voice_store.voice_dir()`.
- The runtime `voices.json` written at bridge startup must mark installed packages as `"remote": false` in the generated JSON model so the engine loads local packages.
- The engine init entry point is called via dynamically resolving the engine object (e.g. `window.Vh.init(extensionId)` in `20260625.1` or `window.Uh.init(...)` in earlier versions).
- The engine global symbol (`window.Vh`, `window.Uh`, etc.) is an obfuscated name from compiled Chrome extension code that changes across versions. `bridgeHarness.js` resolves this dynamically using `getTtsEngine()`. Do not assume any fixed global name will remain stable across future engine updates.
- `bridgeHarness.js` should remain strict-mode and IIFE-wrapped.
- Avoid changing PCM conversion semantics unless fixing a documented audio bug.

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
| Chrome profiles | `{configPath}/googleTtsForNvda/chromeProfiles/session-*` |
| Master catalog | `WasmTtsEngine/20260625.1/voices.json` |

### `voice_store` contract

- `data_root() -> Path`
- `voice_dir() -> Path`
- `is_package_installed(package) -> bool`; verifies existence, size, and SHA-256
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
- `VoiceCatalog.to_runtime_json() -> str`

When changing catalog structure, update all code that depends on runtime JSON consumed by the WASM engine.

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

- Update `doc/en/readme.html` when changing user-visible settings or behavior.
- Known stale documentation: it still mentions removed `acceleration mode` and `transposition`; remove or correct those references when touching settings docs.

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
- Current version: `0.2`.
- Current authors: Dao Duc Trung and Nguyen Anh Duc.
- NVDA compatibility: `minimumNVDAVersion = 2024.1.0`, `lastTestedNVDAVersion = 2026.1.0`.
- Increment `manifest.ini` before producing a release build.
- Do not increment version for internal experiments unless the user asks for a build/release.

---

## 11. Common Engineering Tasks

### Adding or fixing a synth setting

1. Confirm it belongs in the NVDA settings ring.
2. Add or update `_get_...` / `_set_...` methods in the synth driver.
3. Map NVDA 0-100 values to Chrome/WASM-compatible values in one place.
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

1. Update `manifest.ini` version if this is a release.
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
- Do not commit or package temporary Chrome profiles.
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

If you could not complete a requested change, say what blocked it and provide the best partial result. Do not pretend a runtime NVDA/Chrome test was performed unless it actually was.

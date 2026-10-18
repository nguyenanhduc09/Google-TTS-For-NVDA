# Changelog

All notable changes to this fork are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

This fork diverges from
[`nguyenanhduc09/Google-TTS-For-NVDA`](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA)
at release **1.0** (upstream commit `943890c`). Everything below is that delta.

The add-on version in `manifest.ini` is still `1.0`; see
[Release checklist](#release-checklist) for what to bump before publishing.

---

## [Unreleased] — bundled speech runtime

Six commits plus this changelog: **28 files changed, +3,773 / −137**, of which 5 are new
Python modules (`fetch_bun_runtime.py`, `bun_bootstrap.py`, `bun_bridge.py`,
`tests/check_bun_runtime.py`, `tests/test_bun_runtime.py`) and none were removed. Verified on Windows 11 64-bit
with NVDA 2026.2 using the real packaged add-on.

### Added

- **A bundled speech runtime.** Google's WASM TTS engine now runs inside a Bun
  executable shipped in the add-on at
  `synthDrivers/googleTtsForNvda/runtime/bun.exe`, so **no web browser needs to be
  installed**. This is the default and the recommended setting.
- **`bun_bridge.py`** (614 lines) — `BunProcessManager` and `BunClient`. They
  present the same duck-typed surface as `BrowserProcessManager` and `CdpClient`,
  so `ChromeTtsBridge` and the whole of `WasmTtsEngineBridge` — including
  `speak()` and its audio/mark/segmentEnd parser — are shared verbatim by both
  runtimes. Only `ChromeTtsBridge.__init__` chooses which pair to build.
- **`bun_bootstrap.py`** (236 lines) — generates `engine_boot.js` and
  `pthread_worker.js` from Google's pristine engine sources into
  `<CONFIG>/googleTtsForNvda/runtime/bunBootstrap/`. Output is cached against a
  hash manifest, so the cost is paid once per engine revision. Nothing inside
  `WasmTtsEngine/` is modified.
- **`bunRuntime/shim.js`** (374 lines) — the Bun host-API shims (see
  [Technical notes](#technical-notes)).
- **`bunRuntime/worker.js`** (169 lines) — the long-lived stdin/stdout worker
  that replaces headless Chromium.
- **`fetch_bun_runtime.py`** (245 lines) — downloads, extracts, locates,
  verifies and installs `bun.exe`. One implementation shared by `build.bat`,
  `build.sh` and CI, so the three cannot drift.
- **`.github/workflows/build.yml`** — packages a real `.nvda-addon` on every push
  and pull request, uploads it as an artifact, and publishes a GitHub Release on
  `v*` tags. It reopens the finished archive and asserts that `manifest.ini`,
  `runtime/bun.exe`, `bunRuntime/LICENSE-bun.txt`, the driver modules and the WASM
  engine are actually inside, and that no `.zvoice` leaked in.
- **`tests/check_bun_runtime.py`** — manual end-to-end checker that drives the
  real chain and writes a WAV. Not collected by `unittest discover`.
- **`tests/test_bun_runtime.py`** — 100 tests in 10 classes.
- **`bunRuntime/LICENSE-bun.txt`** — Bun's MIT notice plus the LGPL-2
  JavaScriptCore relinking instructions, redistributed because the add-on now
  ships a third-party binary.
- **A fourth runtime choice.** The settings panel's runtime dropdown now lists
  four entries — *Bundled runtime (no browser required)*, Google Chrome,
  Microsoft Edge, Brave — with the bundled one first. Each shows whether it is
  available, and Edge additionally reports WebView2 status.
- `GTTS_BUN_PATH` environment variable to point at a specific Bun executable,
  mirroring the existing `CHROME_PATH` / `EDGE_PATH` / `BRAVE_PATH` convention.
- `bridge.py`: `BROWSER_RUNTIME_BUN`, `CHROMIUM_RUNTIMES`,
  `bun_executable_path()`, `bun_runtime_available()`,
  `effective_speech_runtime()`, `speech_runtime_executable()`,
  `_process_manager_supports_fallback()`.

### Changed

- **`DEFAULT_BROWSER_RUNTIME` is now `bun`** (was `chrome`). A fresh install
  needs nothing else on the machine.
- **`BROWSER_RUNTIMES`** grew from three to four entries. Browsers stay first so
  `_runtime_fallback_order("chrome")` is still
  `(chrome, edge, brave, bun)` — the existing Chromium fallback order is
  untouched and the bundled runtime is appended.
- **`browser_runtime_available()`** with no argument now means "is any usable
  host present", counting the bundled runtime.
- **`edge_webview2_blocks_effective_runtime()`** returns `False` while the
  bundled runtime is in use. Previously a machine with Edge installed but no
  WebView2 Runtime could raise a WebView2 error at a user who never selected
  Edge.
- **Settings wording.** Everything that called all four hosts a *"Chromium
  browser runtime"* now says *"speech runtime"*: the group title, the dropdown
  label, both status labels, the standby checkbox
  (*"Keep Google TTS speech runtime ready in the background"*), and the
  save/apply/defer messages.
- **`manifest.ini` description** no longer claims a Chromium browser is required.
  Three changelog bullets added. `version`, `minimumNVDAVersion` (2024.1.0) and
  `lastTestedNVDAVersion` (2026.3.0) deliberately unchanged.
- **`build.bat` / `build.sh`** went from 7 steps to 8, inserting
  *"Bundling the Bun speech runtime"*. The JavaScript syntax check now also
  covers `bunRuntime/shim.js` and `bunRuntime/worker.js`. Both honour
  `SKIP_BUN_RUNTIME=1` and `BUN_RUNTIME_URL=<url>`.
- **Docs.** `readme.md` (new FAQ entry, four-runtime FAQ, browser-only sections
  scoped with notes, Build Instructions covering the download and both
  workflows, Bun in the license list, *Custom Browser Runtime Paths* →
  *Custom Runtime Paths*); `doc/en/readme.html` (the in-NVDA help, patched
  surgically in its current-state sections only — historical Change Log entries
  left as written); `CONTRIBUTING.md` (build.yml, corrected trigger table, the
  Bun download, a testing section); `AGENTS.md`; `TRANSLATING.md` (manifest keys
  also include `changelog`); `UPDATER_RELEASE_GUIDE.md` (runtime download, 53 MB
  against the 512 MB `MAX_UPDATE_PACKAGE_BYTES`, regenerate `size`/`sha256`);
  `tests/README.md`; `tests/NVDA_CHROMIUM_MANUAL_CHECKLIST.md` (new
  bundled-runtime section).
- **CI** installs `configobj` so a test can parse `manifest.ini` with the same
  library NVDA uses, catching malformed triple-quoted values.
- **Package size: 10.4 MB → ~53 MB** (55,616,002 bytes, 102 entries). Still far
  below the updater's 512 MB limit, and it replaces a 700 MB–1.2 GB browser
  install plus a profile directory the add-on capped at 500 MB.

### Fixed

- **A browser-fallback regression this fork introduced and Windows CI caught.**
  The fallback loop in `ensure_connection()` was guarded by
  `getattr(self, "_speechRuntime", DEFAULT_BROWSER_RUNTIME) == BROWSER_RUNTIME_BUN`.
  That default was inert while the default runtime was `chrome`, but flipping the
  default to `bun` mattered: `tests/test_support.make_fake_bridge()` builds a
  `ChromeTtsBridge` with `__new__`, so `_speechRuntime` is never set, every
  fake-backed bridge looked bundled-runtime-backed, the fallback loop was
  skipped, and a raw `CdpError` escaped instead of `CdpCancelled`. It passed on
  Linux only by accident — with no browsers installed `_browser_choices()` is
  empty there, so the loop exited the same way for the wrong reason. Fixed by
  keying the decision off the process manager
  (`supports_runtime_fallback`) instead of a bridge attribute, plus four
  deterministic tests that do not depend on a timing race.
- **`_browser_choices()` could have offered `bun.exe` as a launch candidate**,
  which would have made `BrowserProcessManager` start Bun with `--headless=new`
  and a CDP port. It now skips anything outside `CHROMIUM_RUNTIMES`.
- The settings dropdown ordering used a slice of `len(BROWSER_RUNTIMES)`, which
  silently drops the wrong entry if the bundled runtime ever moves off the end of
  that tuple. Now deduped with `dict.fromkeys`.
- Two stale assertions in `tests/test_bridge_helpers.py` that pinned the
  three-runtime model, updated rather than deleted — plus a new test pinning the
  `chrome → edge → brave` sub-order so appending `bun` cannot silently reorder
  browser fallback.

### Removed

Nothing. The Chromium path is retained in full and remains a supported,
selectable runtime.

### Security

- `fetch_bun_runtime.py` rejects archive members that escape the extraction
  directory, refuses payloads under 20 MB, and verifies the `MZ` PE magic before
  installing — so an HTML error page or a truncated download cannot be packaged
  as `bun.exe`. It records the archive and executable SHA-256 and the detected
  version in `runtime/bun_version.json`.
- `bunRuntime/shim.js` resolves relative URLs through a `safeJoin()` that mirrors
  `bridge.py:_safe_join()`, rejecting path traversal.
- The worker's stdout carries protocol JSON only; all logging goes to stderr.

---

## Technical notes

Bun was not a drop-in replacement. Three gaps had to be closed, each diagnosed
empirically rather than assumed:

1. **Bun never delivers `worker_threads` messages to `globalThis.onmessage`.**
   Emscripten's glue explicitly assumes otherwise —
   *"Deno and Bun already have `postMessage` defined ... and deliver messages to
   `globalThis.onmessage`"* — and therefore skips installing its own `parentPort`
   bridge. Sending works; receiving does not. Because the WASM is a pthreaded,
   shared-memory build (`shared: true`, 512 MB, a pool of 10 workers), module
   initialisation hung forever on `still waiting on run dependencies:
   loading-workers`. Bun's `--preload` and `bunfig.toml` preload run on the main
   thread only, so the bridge is baked into a generated worker entry file.
2. **`globalThis.name` is `undefined` in a Bun worker**, so Emscripten's pthread
   self-boot test never fires; and Bun's `globalThis.Worker` is DOM-style and
   lacks the `.on()` / `.unref()` that Emscripten's main-thread path calls. The
   shim forces `node:worker_threads.Worker` and mirrors `name` into `workerData`.
3. **No `indexedDB`, and `fetch()` cannot resolve relative URLs.** `IDBFS` is
   only a redundant cache here — voice packages already live on disk and are
   SHA-256 verified by `voice_store.py` — and `IDBFS.mount` just delegates to
   MEMFS, so an *empty* in-memory stub suffices; `IDBFS.reconcile` short-circuits
   via `if (!total) return callback(null)`. Resolving `voices.json` and
   `/<packageId>.zvoice` from disk also removes the need for the local HTTP
   server and its COOP/COEP headers.

Emscripten's minimum-runtime gate would reject Bun's `navigator.userAgent`
(`"Bun/1.4.3"` has no `Chrome/`), but the whole block sits inside
`if (hasProcess && !(process.versions && process.versions.bun) && ...)` —
Emscripten already special-cases Bun, so it never runs.

**Audio behaviour is shared, not duplicated.** `bunRuntime/worker.js` loads
`web/bridgeHarness.js` unchanged, so the soft limiter, output gain, post-pitch
resampling, WSOLA tempo, segment-boundary hold, adaptive packet sizing, session
token fencing and event ordering all live in exactly one file. A test asserts the
DSP is *not* copied into the Bun runtime. The worker emits
`Runtime.bindingCalled` envelopes on stdout in the shape
`WasmTtsEngineBridge.handle_event()` already parses.

### Measured performance

On a 2-CPU Linux container (a real desktop will be faster), through the
production `WasmTtsEngineBridge` → `BunClient` → subprocess path:

| | |
|---|---|
| Bun process spawn | ~20 ms |
| `engine.init()` — 23 MB WASM + 10-worker pthread pool | **1.3–2.0 s** |
| First utterance (cold: unpacks `.zvoice`, loads G2P FST + TFLite) | **~6 s** to first audio |
| Warm single word (`"Tab"`) | **47 ms** |
| Warm sentence (2.73 s of audio) | **224–330 ms** |
| `onSpeak()` returns | **4–7 ms** |
| Cancel mid-utterance | **5–51 ms**, zero leaked audio |
| Peak RSS | **343 MB**, 27 threads |

Output verified as genuine speech, not noise: mono 24 kHz 16-bit, RMS 3629–5953,
~30–34% near-silent (natural inter-word gaps), envelope decaying to zero.

---

## Test results

| | upstream | this fork |
|---|---|---|
| Unit tests | 736 | **838** |
| Test classes | 134 | **144** |
| Test modules | 17 | **18** |
| `ruff check` | pass | pass |
| `ruff format --check` | pass | pass |
| `mypy` | 38 files, no issues | **42 files, no issues** |
| New Python modules | — | **5 added, 0 removed** |
| CI (windows-latest, Py 3.11 + 3.12) | pass | **pass — `Ran 838 tests` → `OK`** |

On Linux the suite reports 14 failures that are **byte-identical to the pristine
upstream baseline**: 12 × `ctypes.WinDLL` (Windows-only API), one requiring the
CLD2 Windows `.dll`, and one browser-fallback test that needs browsers installed
to exercise its path. All 838 pass on Windows CI.

---

## Known gaps

1. **Translation debt — measured, not guessed.** `build_i18n.py --check` is clean
   on upstream (0 errors) and reports **343** on this branch; `--check-docs`
   reports **7** segment-count mismatches (English now has 162 doc segments, the
   translations 159). Diffing regenerated POT msgids gives the real workload:
   **28 new strings, 19 retired → 28 × 7 = 196 entries**. Nine of the 28 are the
   retired strings with *"Chromium browser runtime"* replaced by *"speech
   runtime"* and can be copied and adjusted; ten are genuinely new `bun_bridge`
   diagnostics. Neither check runs in CI, so nothing blocks a build — but the
   seven locales (es, pt_BR, ru, uk, vi, zh_CN, zh_TW) will show English for the
   renamed labels and stale browser wording in their help docs and
   `locale/*/manifest.ini` until retranslated:

   ```
   python build_i18n.py --update-po --all-languages
   python build_i18n.py --update-doc-po --all-languages
   python build_i18n.py --build-ui --all-languages
   python build_i18n.py --build-docs --all-languages
   ```

2. **`bun.exe` is unsigned.** Windows SmartScreen or antivirus may flag it. This
   is the largest remaining real-world risk and is on the manual checklist.
3. **Not verified:** the Windows sign-in screen / secure desktop, 32-bit Windows,
   and `bun build --compile` (Emscripten re-executes the same file in each pthread
   worker, which a single-file compiled binary has no path for — the generated
   bootstrap approach used here deliberately avoids `--compile`).
4. **Bun is a moving target.** The `globalThis.onmessage` gap is a Bun omission;
   if Bun fixes it, the shim still works because it only *adds* a `parentPort`
   listener and re-reads `globalThis.onmessage` at dispatch time.
5. **Engine upgrades.** `bun_bootstrap.find_engine_global()` discovers the
   minified engine global rather than hardcoding it, and a test asserts the
   discovery works against the bundled engine — so a future
   `WasmTtsEngine/<version>` bump fails in CI instead of silently at runtime.

---

## Release checklist

Before publishing this fork as a release:

1. Decide the version. `manifest.ini` is still `version = 1.0`; if you bump it,
   also set `googleTtsForNvda/buildInfo.json` `baseVersion` to match and reset
   `updateBuild` to `1` (see `UPDATER_RELEASE_GUIDE.md`).
2. Clear the translation debt above, or knowingly ship English-only labels.
3. Run `tests/NVDA_CHROMIUM_MANUAL_CHECKLIST.md` on a real machine, including the
   bundled-runtime section: no-browser install, console-window flashing,
   SmartScreen/AV, killing `bun.exe` and recovering, `GTTS_BUN_PATH`, and
   32-bit NVDA on 64-bit Windows.
4. Confirm the `v*` tag triggers `build.yml`'s Release step, which needs
   `permissions: contents: write`.

---

## Commits

| SHA | Subject |
|---|---|
| `08cb370` | Run the WASM TTS engine in a bundled Bun runtime instead of a browser |
| `763eff7` | Correct the 32-bit NVDA requirement in the readme |
| `05f45bf` | Make the bundled runtime a fourth entry in the existing runtime setting |
| `dc6c6b5` | Fix browser fallback regression the four-runtime change introduced |
| `61e04f5` | Make the settings runtime ordering independent of BROWSER_RUNTIMES |
| `15cc2c2` | Fix the manifest and close the documentation gaps the runtime change opened |

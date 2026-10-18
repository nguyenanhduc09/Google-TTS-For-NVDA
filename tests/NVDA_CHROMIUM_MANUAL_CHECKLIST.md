# NVDA + speech runtime manual test checklist

Static checks cannot prove that the speech runtime and its WASM engine start
reliably, that audio is clean and level, or that NVDA announces dynamic controls
correctly. Run this checklist on a real installed add-on. Leave every box
unchecked until that test has actually been performed.

Four runtimes are selectable: the **bundled runtime** (`runtime/bun.exe`, the
default) and the three Chromium browsers. Cover the bundled runtime first; the
browser items apply only when a Chromium runtime is selected.

## Test record

Record one row per meaningful configuration.

| Date | Add-on revision | NVDA/version/arch | Speech runtime/version | Voice package/speaker | Output device | Cold/warm/standby | Result/evidence |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

Also record the NVDA architecture (x86/x64) and whether Windows is 64-bit, since
the bundled runtime is a 64-bit process; whether Edge WebView2 Runtime is
installed; whether automatic language profiles are enabled; and relevant NVDA
log/audio captures. For the bundled runtime, record the version reported by
`runtime/bun_version.json`.

## 1. Synth loading and runtime/WASM startup

### Bundled runtime (default)

- [ ] Install the packaged add-on on a machine with **no browser installed** and verify speech works.
- [ ] Verify the settings panel lists four runtimes with the bundled one first and marked Available.
- [ ] Verify a cold first utterance, then warm speech, and record time to first audio for each.
- [ ] Verify **Keep Google TTS speech runtime ready** both off and on.
- [ ] Verify no console window flashes at NVDA startup or during speech.
- [ ] Verify cancellation while speaking: interrupt mid-utterance repeatedly and confirm speech stops promptly with no leaked or trailing audio.
- [ ] Interrupt and immediately speak again (repeated `NVDA+f12`, or arrow keys while reading), and confirm the *following* utterance is complete — no missing final word. With debug logging on, `bun_bridge` logs `audio accounting agrees: N packets, M samples`; a line saying `audio accounting mismatch` means audio was lost between the runtime and NVDA rather than never being synthesised.
- [ ] Read a long paragraph and a date/number-heavy paragraph end to end and confirm nothing is cut off at the end. `tests/check_interrupt_truncation.py` stress-tests the interrupt path offline.
- [ ] Repeatedly re-read the *same* short text (for example a date) and confirm every rendition is the same length. Differing lengths for identical input mean the harness declared the utterance drained while the engine was still producing audio; raise `GTTS_DRAIN_IDLE_MS` and retest. `tests/check_synthesis_variance.py` measures this offline.
- [ ] Read under load (SayAll on a long document with other add-ons active) and confirm no sentence loses its final word. Record whether raising the runtime process priority was enough or whether `GTTS_DRAIN_IDLE_MS` also had to be raised.
- [ ] Verify the sign-in screen / secure desktop behaviour and record it; do not assume it works.
- [ ] Verify antivirus and Windows SmartScreen do not quarantine or block `runtime/bun.exe`, and that the add-on is signed/trusted as expected.
- [ ] Terminate `bun.exe` from Task Manager while NVDA is idle, then speak; verify recovery without a stale process.
- [ ] Set `GTTS_BUN_PATH` to a valid Bun executable and verify it is used; set it to an invalid path and verify the diagnostic and fallback.
- [ ] Delete `runtime/bun.exe` and verify the bundled-runtime error message, then verify fallback to an installed browser when one exists.
- [ ] On 32-bit (x86) NVDA over 64-bit Windows, verify the bundled runtime still works.
- [ ] Verify peak memory of the runtime process over a long SayAll session and compare against the recycle thresholds.

### Chromium runtimes

- [ ] Exercise Chrome, Edge, and Brave separately when installed.
- [ ] Make the saved runtime unavailable and verify fallback order, diagnostics, and eventual speech.
- [ ] With Edge selected, verify both the usable WebView2 path and the missing/broken WebView2 prompt and links.
- [ ] Verify a missing WebView2 Runtime does **not** block a user who selected the bundled runtime.
- [ ] Switch synthesizers during runtime startup and during voice-package preload; NVDA must remain responsive and cancellation must not be reported as a speech failure.
- [ ] Terminate the managed browser unexpectedly and verify a later utterance recovers without stale processes or profiles.

## 2. PCM, pause shortening, segmentation, and cache behavior

- [ ] Listen to representative base and SeaNet packages, including Multi, AFH, FIS, MultiSeaNet, AFHSeaNet, and FISSeaNet where installed.
- [ ] At volume 100, compare perceived level with other installed synthesizers and listen for clipping, pumping, sudden level changes, harshness, or lost beginnings/endings.
- [ ] Distinguish known distortion produced by the original WASM voice from distortion introduced by add-on PCM processing.
- [ ] Compare the same short and hidden-multi-segment utterances in all three pause modes: **Do not shorten** preserves internal and final engine pauses; **Shorten at end of text only** preserves internal pauses but shortens the final pause; **Shorten all pauses** shortens both internal and final pauses.
- [ ] Exercise PCM arriving in many small packets and verify no clicks or sample loss at packet boundaries.
- [ ] Exercise hidden browser segments and verify continuity, boundary pauses, cancellation, and completion events.
- [ ] Read a medium or long sentence whose first hidden segment ends with a spoken word immediately before punctuation; verify the boundary word is complete on both the first synthesis and a RAM-cache replay.
- [ ] Speak representative corpus cases: localized punctuation/quotes, abbreviations, URLs, emoji, CJK/Thai without spaces, and very long sentences.
- [ ] Change voice, rate, pitch, volume, pause mode, post-processing pitch, and hidden segments; verify cached audio from the previous configuration is not reused.

## 3. NVDA focus and speech inside Chromium

- [ ] Navigate browser chrome: tabs, address bar, menus, downloads, and dialogs.
- [ ] Navigate web content: forms, links, headings, tables, live regions, and dynamically updated controls.
- [ ] Move repeatedly between browse mode, focus mode, NVDA Settings, Voice Manager, and other applications.
- [ ] Verify character/word/line review, spelling, punctuation, emoji, and mixed-language announcements.
- [ ] Repeat with automatic language profiles off and on, including speech dictionaries and voice dictionaries.

## 4. Settings and audio-device behavior

- [ ] Tab through every Google TTS settings control and focusable read-only status/help field; verify labels, values, role, and arrow-key review.
- [ ] Apply, reopen, and cancel settings; verify saved values, immediate previews, and panel cleanup.
- [ ] Toggle automatic language profiles and verify hidden/shown controls, focus order, and scroll layout.
- [ ] On NVDA 2024 verify `speech.outputDevice`; on NVDA 2025+ verify `audio.outputDevice`.
- [ ] Disconnect/change the active output device and verify `nvwave.isInError()` causes safe WavePlayer recreation and speech recovery.
- [ ] Exercise Apply, OK, Cancel, synth switching, and settings-panel destruction while status refresh work is pending.

## 5. Voice Manager

- [ ] Verify keyboard-only use, accessible names, filters, result counts, accelerators, Escape behavior, and focus restoration.
- [ ] Download a package and its dependencies; verify progress, checksum/error handling, installed list refresh, and standby warm-up.
- [ ] Verify missing/invalid dependencies fail clearly and are never downloaded from the speech path.
- [ ] Remove packages, including the last installed voice, and verify synth availability and first-run prompting.
- [ ] Open the voice folder and verify no `.zvoice` package exists in the add-on source tree.

## 6. Updater and lifecycle

- [ ] Exercise update available, no update, declined update, download/verification failure, install, and restart prompts.
- [ ] Verify updater dialogs, links, status text, and progress are keyboard- and screen-reader-accessible.
- [ ] Verify automatic checking happens only as designed at startup and does not block NVDA.
- [ ] Restart/exit NVDA after speech and after standby warm-up; verify browser processes and temporary profiles are cleaned up.
- [ ] Verify secure mode disables unsupported background/runtime behavior and does not expose unsafe UI actions.

## Sign-off

- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `python tests\check_nvda_api_contracts.py` passes against every supported local NVDA tree.
- [ ] Python syntax/compile checks pass and no `.zvoice` exists under the add-on source tree.
- [ ] Runtime results and evidence are attached to the release/test record; failures are not replaced by static-check claims.

Update this checklist whenever the add-on adds an NVDA hook, Chromium lifecycle
path, audio-processing stage, or focusable status/help control.

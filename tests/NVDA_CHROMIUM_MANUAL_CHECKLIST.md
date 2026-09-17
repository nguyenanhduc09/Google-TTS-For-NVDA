# NVDA + Chromium manual test checklist

Static checks cannot prove that Chromium/WASM starts reliably, that audio is
clean and level, or that NVDA announces dynamic controls correctly. Run this
checklist on a real installed add-on. Leave every box unchecked until that test
has actually been performed.

## Test record

Record one row per meaningful configuration.

| Date | Add-on revision | NVDA/version/arch | Chromium runtime/version | Voice package/speaker | Output device | Cold/warm/standby | Result/evidence |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

Also record whether Edge WebView2 Runtime is installed, whether automatic
language profiles are enabled, and relevant NVDA log/audio captures.

## 1. Synth loading and Chromium/WASM startup

- [ ] Start each supported NVDA version with Google TTS selected and verify a cold first utterance.
- [ ] Verify warm speech and startup with **Keep Chromium browser runtime ready** both off and on.
- [ ] Exercise Chrome, Edge, and Brave separately when installed.
- [ ] Make the saved runtime unavailable and verify fallback order, diagnostics, and eventual speech.
- [ ] With Edge selected, verify both the usable WebView2 path and the missing/broken WebView2 prompt and links.
- [ ] Switch synthesizers during browser startup and during voice-package preload; NVDA must remain responsive and cancellation must not be reported as a speech failure.
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

r"""Stress-test the interrupt-then-respeak path for truncated speech.

Kept as a manual checker (not collected by unittest discover) because it needs a
Bun executable and a downloaded voice package.

Mechanism (two defects compounding):

1. `BunClient.send_command_async()` writes `{"op": "stop"}` with **no `id`**, and
   `bunRuntime/worker.js`'s `dispatch()` returned early on a missing id -- so
   `WasmTtsEngineBridge.send_fast_stop()` was silently dropped and the cancelled
   utterance kept running.
2. `web/bridgeHarness.js`'s `googleTtsForNvdaSpeak()` success tail fences every
   call with `sessionToken` EXCEPT the final `await stopActiveSynthesis()`, which
   unconditionally does `currentSessionToken++`, `resetAudioQueue()` and
   `engine.onStop()`. When the still-in-flight old invocation unwinds after the
   new session has begun, that call lands inside the new session and truncates it.

The browser path never shows this because the CDP stop expression really does run,
invalidating the old session before the next evaluate arrives.

Run:  python tests\check_interrupt_truncation.py
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tests.test_support import load_driver_module  # noqa: E402

LONG_TEXT = (
    "The meeting is on the eighteenth of October twenty twenty six at five oh seven in the "
    "evening, and the follow up is scheduled for the third of January twenty twenty seven."
)
SHORT_TEXT = "Eighteen October twenty twenty six."


class _Pkg:
    def __init__(self, packageId: str) -> None:
        self.fileName = f"{packageId}.zvoice"


def main() -> int:
    bridge = load_driver_module("bridge")
    bun_bridge = load_driver_module("bun_bridge")
    audio_math = load_driver_module("audio_math")
    voice_store = load_driver_module("voice_store")

    packageId = os.environ.get("GTTS_PACKAGE_ID", "en-us-x-multi")
    source = Path(os.environ["GTTS_ZVOICE"])
    voice_store.voice_dir()
    target = Path(voice_store.package_file(_Pkg(packageId)))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != source.resolve():
        shutil.copyfile(source, target)

    catalog = bridge.VoiceCatalog.load()
    package = catalog.package_by_id(packageId)
    if not voice_store.is_package_installed(package):
        print("voice package not installed where voice_store expects it:", target)
        return 2
    speaker = next(s for s in catalog.speakers if s.packageId == packageId)
    options = audio_math.build_speech_options(
        speaker_id=speaker.id,
        speaker_name=speaker.name,
        lang=speaker.language,
        package_id=packageId,
        rate=50,
        pitch=50,
        volume=100,
    )

    manager = bun_bridge.BunProcessManager(catalog)
    client = bun_bridge.BunClient(manager)
    engine = bridge.WasmTtsEngineBridge(client, catalog)
    client.connect(manager.start_and_get_websocket_url())
    engine.enable_cdp_domains()
    engine.wait_until_ready()
    print("runtime ready")

    def measure(text: str, label: str) -> float:
        total = bytearray()
        result = engine.speak(text, options, total.extend, cancelEvent=threading.Event())
        seconds = len(total) / 2 / bridge.SAMPLE_RATE
        print(f"  {label:38s} audio={seconds:5.2f}s ok={result.get('success')} chunks={result.get('audioChunks')}")
        return seconds

    measure("Warm up.", "warm-up (loads voice pipeline)")
    baselineShort = measure(SHORT_TEXT, "baseline: short text, uninterrupted")

    # The vulnerable window is NOT while the engine is synthesising -- a superseded
    # invocation cannot unwind until engine.onSpeak() resolves. It is the drain
    # afterwards, where waitForSynthesisComplete() returns immediately once
    # `stopped` is set, so the stale invocation reaches its unfenced trailing
    # stopActiveSynthesis() right as the next session starts. Sweep the interrupt
    # delay to find that window, mirroring repeated NVDA+f12 presses.
    worst = 0.0
    worstDelay = None
    for delay in (0.05, 0.08, 0.10, 0.13, 0.16, 0.20, 0.25, 0.30, 0.40):
        outcome = []

        def speakThenCancel(delay=delay, outcome=outcome) -> None:
            total = bytearray()
            cancel = threading.Event()
            threading.Timer(delay, cancel.set).start()
            try:
                engine.speak(SHORT_TEXT, options, total.extend, cancelEvent=cancel)
                outcome.append("completed")
            except bridge.CdpCancelled:
                outcome.append("CdpCancelled")
            except Exception as error:
                outcome.append(type(error).__name__)

        worker = threading.Thread(target=speakThenCancel)
        worker.start()
        worker.join(timeout=60)
        afterInterrupt = measure(SHORT_TEXT, f"after interrupt at {int(delay * 1000)}ms")
        loss = baselineShort - afterInterrupt
        if loss > worst:
            worst, worstDelay = loss, delay
        if loss > 0.10:
            print(f"    ^^ TRUNCATED by {loss:.2f}s (previous op: {outcome[0] if outcome else '?'})")
    afterInterrupt = baselineShort - worst
    loss = worst

    # Rapid fire: mimic the log the bug was reported from, where NVDA+f12 is pressed
    # again ~2 ms after the cancel is dispatched, so the next speak is issued while
    # the previous one is still unwinding inside the harness.
    print("\n  --- rapid interrupt/respeak cycles ---")
    rapidWorst = 0.0
    for cycle in range(12):
        cancel = threading.Event()
        discarded = bytearray()
        threading.Timer(0.12, cancel.set).start()
        try:
            engine.speak(SHORT_TEXT, options, discarded.extend, cancelEvent=cancel)
        except bridge.CdpCancelled:
            pass
        except Exception:
            pass
        probe = bytearray()
        result = engine.speak(SHORT_TEXT, options, probe.extend, cancelEvent=threading.Event())
        seconds = len(probe) / 2 / bridge.SAMPLE_RATE
        lost = baselineShort - seconds
        rapidWorst = max(rapidWorst, lost)
        flag = "  <== TRUNCATED" if lost > 0.10 else ""
        print(
            f"    cycle {cycle:2d}: audio={seconds:5.2f}s lost={lost:+5.2f}s chunks={result.get('audioChunks')}{flag}"
        )
    if rapidWorst > 0.10:
        print(f"\n  REPRODUCED under rapid interruption: worst loss {rapidWorst:.2f} s")
        return 1
    print(f"\n  Rapid interruption clean: worst loss {rapidWorst:.2f} s")

    loss = baselineShort - afterInterrupt
    print()
    print("==================== RESULT ====================")
    print(f"  baseline short   : {baselineShort:.2f} s")
    print(f"  worst truncation : {loss:.2f} s lost (at interrupt delay {worstDelay}s)")
    if loss > 0.10:
        print("\n  REPRODUCED: the utterance following an interrupt is truncated.")
        return 1
    print("\n  Not truncated: the utterance following an interrupt is complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Measure run-to-run variance in synthesised length for one fixed text.

The truncation hypothesis: web/bridgeHarness.js's waitForSynthesisComplete()
declares the utterance finished on

    const audioHasDrained = lastChunkAt > 0 && now - lastChunkAt >= synthesisFinishedIdleMs;

with synthesisFinishedIdleMs = 80. lastChunkAt is updated on every addBuffer from
the engine, and the engine pumps 128-sample blocks (~5.3 ms each) from a
setTimeout(..., 0) chain. So an 80 ms stall in that pump -- TFLite inference for a
date or number, GC, or event-loop contention with the 10 WASM pthreads -- is read
as "audio drained". The harness then flushes, emits done, and calls
stopActiveSynthesis(), discarding the rest of the utterance while still reporting
success. The result is cached, so the truncated audio replays until invalidated.

This speaks one fixed text many times and reports the spread. A wide spread on
identical input is the signature; a tight one is not.

Run:  python tests\\check_synthesis_variance.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tests.test_support import load_driver_module  # noqa: E402

DEFAULT_TEXT = "18 October 2026"


class _Pkg:
    def __init__(self, packageId: str) -> None:
        self.fileName = f"{packageId}.zvoice"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zvoice", required=True)
    parser.add_argument("--package-id", default=os.environ.get("GTTS_PACKAGE_ID", "en-us-x-multi"))
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--runs", type=int, default=12)
    args = parser.parse_args(argv)

    bridge = load_driver_module("bridge")
    bun_bridge = load_driver_module("bun_bridge")
    audio_math = load_driver_module("audio_math")
    voice_store = load_driver_module("voice_store")

    source = Path(args.zvoice).expanduser()
    voice_store.voice_dir()
    target = Path(voice_store.package_file(_Pkg(args.package_id)))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != source.resolve():
        shutil.copyfile(source, target)

    catalog = bridge.VoiceCatalog.load()
    if not voice_store.is_package_installed(catalog.package_by_id(args.package_id)):
        print("voice package not installed where voice_store expects it:", target)
        return 2
    speaker = next(s for s in catalog.speakers if s.packageId == args.package_id)
    options = audio_math.build_speech_options(
        speaker_id=speaker.id,
        speaker_name=speaker.name,
        lang=speaker.language,
        package_id=args.package_id,
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

    print(f"text: {args.text!r}   voice: {speaker.name}   rate option: {options['rate']}")
    print(f"{'run':>4} {'samples':>9} {'seconds':>8} {'chunks':>7} {'success':>8} {'done':>6} {'ms':>7}")
    durations: list[float] = []
    try:
        for index in range(args.runs):
            audio = bytearray()
            started = time.perf_counter()
            result = engine.speak(args.text, options, audio.extend, cancelEvent=threading.Event())
            elapsed = (time.perf_counter() - started) * 1000
            samples = len(audio) // 2
            seconds = samples / bridge.SAMPLE_RATE
            durations.append(seconds)
            print(
                f"{index:>4} {samples:>9} {seconds:>8.3f} {result.get('audioChunks'):>7} "
                f"{str(result.get('success')):>8} {str(result.get('done')):>6} {elapsed:>7.0f}"
            )
    finally:
        client.close()
        manager.terminate()

    longest = max(durations)
    print("\n==================== RESULT ====================")
    print(f"  runs              : {len(durations)}")
    print(f"  longest           : {longest:.3f} s")
    print(f"  shortest          : {min(durations):.3f} s")
    print(f"  median            : {statistics.median(durations):.3f} s")
    print(f"  stdev             : {statistics.pstdev(durations):.3f} s")
    short = [d for d in durations if d < longest * 0.9]
    print(f"  runs >10% shorter : {len(short)} of {len(durations)}  {[round(d, 3) for d in short]}")
    if short:
        print("\n  TRUNCATION REPRODUCED: identical input produced materially different lengths,")
        print("  and every run reported success=True/done=True. The harness is declaring the")
        print("  utterance drained (synthesisFinishedIdleMs = 80 ms) while the engine is still")
        print("  producing audio.")
        return 1
    print("\n  No variance: identical input produced identical length on every run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Exercise the bundled Bun speech runtime end to end.

This is a manual checker, not part of ``python -m unittest discover``: it needs a
Bun executable and a downloaded ``.zvoice`` voice package, so it cannot run in the
lint/type/test CI job. It exists because the unit tests stub the transport, and
nothing else proves that the real chain works:

    bridge.WasmTtsEngineBridge  ->  bun_bridge.BunClient  ->  subprocess stdin/stdout
      ->  bunRuntime/worker.js  ->  bunRuntime/shim.js  ->  web/bridgeHarness.js
      ->  WasmTtsEngine/<version>/bindings_main.wasm

Usage (Windows, from a built add-on or a source checkout with ``runtime/bun.exe``)::

    python tests\\check_bun_runtime.py --zvoice path\\to\\en-us-x-multi.zvoice

Point ``GTTS_BUN_PATH`` at a Bun executable to override discovery, and
``--package-id``/``--voice-name`` at a different installed voice. Pass ``--out`` to
write the synthesised audio to a WAV file for listening.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
import time
import wave
from contextlib import suppress
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_support import load_driver_module  # noqa: E402

DEFAULT_PACKAGE_ID = "en-us-x-multi"
WARMUP_TEXT = "Warm up."


class _PackageFileName:
    """Minimal stand-in exposing only ``fileName``, for ``voice_store.package_file``."""

    def __init__(self, packageId: str) -> None:
        self.fileName = f"{packageId}.zvoice"


def _install_voice_package(voice_store: Any, packageId: str, source: Path) -> Path:
    """Copy the package where ``voice_store`` expects it, using its own path logic."""
    voice_store.voice_dir()
    target = Path(voice_store.package_file(_PackageFileName(packageId)))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != source.resolve():
        shutil.copyfile(source, target)
    return target


def _find_speaker(catalog: Any, packageId: str, voiceName: str | None) -> Any:
    speakers = [speaker for speaker in catalog.speakers if speaker.packageId == packageId]
    if not speakers:
        raise SystemExit(f"No speakers in the bundled catalog belong to package '{packageId}'.")
    if voiceName:
        for speaker in speakers:
            if speaker.name == voiceName:
                return speaker
        raise SystemExit(f"Voice '{voiceName}' is not in package '{packageId}'.")
    return speakers[0]


def _speak_and_measure(engine: Any, bridge: Any, text: str, options: dict[str, Any]) -> dict[str, Any]:
    """Speak once through the real engine bridge and report what came back."""
    audioChunks: list[bytes] = []
    marks: list[int] = []
    firstAudioAt: list[float] = []

    def onAudio(data: bytes) -> None:
        if not firstAudioAt:
            firstAudioAt.append(time.perf_counter())
        audioChunks.append(data)

    spokenAt = time.perf_counter()
    result = engine.speak(
        text,
        options,
        onAudio,
        cancelEvent=threading.Event(),
        onMark=marks.append,
        onSegmentEnd=lambda: None,
    )
    totalMs = (time.perf_counter() - spokenAt) * 1000
    firstAudioMs = (firstAudioAt[0] - spokenAt) * 1000 if firstAudioAt else -1.0
    pcm = b"".join(audioChunks)
    return {
        "success": bool(result.get("success")),
        "done": bool(result.get("done")),
        "totalMs": totalMs,
        "firstAudioMs": firstAudioMs,
        "chunks": len(audioChunks),
        "marks": len(marks),
        "seconds": len(pcm) / 2 / bridge.SAMPLE_RATE,
        "pcm": pcm,
    }


def _write_wav(path: Path, pcm: bytes, sampleRate: int) -> None:
    with wave.open(str(path), "wb") as wavFile:
        wavFile.setnchannels(1)
        wavFile.setsampwidth(2)
        wavFile.setframerate(sampleRate)
        wavFile.writeframes(pcm)


def run_check(args: argparse.Namespace) -> int:
    bridge = load_driver_module("bridge")
    bun_bridge = load_driver_module("bun_bridge")
    audio_math = load_driver_module("audio_math")
    voice_store = load_driver_module("voice_store")

    source = Path(args.zvoice).expanduser()
    if not source.is_file():
        raise SystemExit(f"Voice package not found: {source}")

    bunPath = bun_bridge.find_bun()
    print(f"Bun executable      : {bunPath or 'NOT FOUND'}")
    if bunPath is None:
        print(
            "No Bun executable was found. Build the add-on (which downloads it), or set "
            f"{bun_bridge.BUN_PATH_ENV_VAR} to a Bun executable.",
        )
        return 2

    target = _install_voice_package(voice_store, args.package_id, source)
    print(f"Voice package       : {target} ({target.stat().st_size:,} bytes)")

    catalog = bridge.VoiceCatalog.load()
    package = catalog.package_by_id(args.package_id)
    if not voice_store.is_package_installed(package):
        print("voice_store does not consider that package installed (checksum or size mismatch).")
        return 2
    speaker = _find_speaker(catalog, args.package_id, args.voice_name)
    print(f"Speaker             : {speaker.name} ({speaker.id}, {speaker.language})")

    manager = bun_bridge.BunProcessManager(catalog)
    client = bun_bridge.BunClient(manager)
    engine = bridge.WasmTtsEngineBridge(client, catalog)

    startedAt = time.perf_counter()
    exitCode = 0
    try:
        client.connect(manager.start_and_get_websocket_url())
        engine.enable_cdp_domains()
        engine.wait_until_ready()
        print(f"Runtime ready       : {(time.perf_counter() - startedAt) * 1000:.0f} ms")

        options = audio_math.build_speech_options(
            speaker_id=speaker.id,
            speaker_name=speaker.name,
            lang=speaker.language,
            package_id=args.package_id,
            rate=args.rate,
            pitch=args.pitch,
            volume=args.volume,
        )

        for label, text in (("cold", WARMUP_TEXT), ("warm", args.text)):
            measurement = _speak_and_measure(engine, bridge, text, options)
            print(
                f"{label.capitalize():5s} utterance    : success={measurement['success']} "
                f"total={measurement['totalMs']:.0f} ms firstAudio={measurement['firstAudioMs']:.0f} ms "
                f"chunks={measurement['chunks']} audio={measurement['seconds']:.2f}s "
                f"marks={measurement['marks']} done={measurement['done']}",
            )
            if label != "warm":
                continue
            if args.out:
                outPath = Path(args.out).expanduser()
                _write_wav(outPath, measurement["pcm"], bridge.SAMPLE_RATE)
                print(f"Wrote               : {outPath} ({outPath.stat().st_size:,} bytes)")
            if not measurement["success"] or len(measurement["pcm"]) < bridge.SAMPLE_RATE:
                print("FAIL: the warm utterance did not produce at least one second of audio.")
                exitCode = 1
    finally:
        with suppress(Exception):
            engine.stop_runtime()
        client.close()
        manager.terminate()

    print("PASS" if exitCode == 0 else "FAIL")
    return exitCode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zvoice", required=True, help="Path to a downloaded .zvoice voice package.")
    parser.add_argument("--package-id", default=DEFAULT_PACKAGE_ID, help="Catalog package id (default: %(default)s).")
    parser.add_argument("--voice-name", default=None, help="Speaker display name (default: first in the package).")
    parser.add_argument("--text", default="The bundled speech runtime is working.", help="Warm utterance text.")
    parser.add_argument("--rate", type=int, default=50)
    parser.add_argument("--pitch", type=int, default=50)
    parser.add_argument("--volume", type=int, default=100)
    parser.add_argument("--out", default=None, help="Write the warm utterance to this WAV file.")
    return run_check(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

"""Node.js runtime tests for googleTtsForNvda/synthDrivers/googleTtsForNvda/web/bridgeHarness.js.

Executes the real browser harness JavaScript inside an isolated Node vm context
so WASM engine discovery, error handling, language install retries, worklet buffer
clearing, async startup polling, controlSequence cancellation, and pitch
resampling boundary clamping are tested directly without launching Chromium.
"""

from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import unittest
from pathlib import Path
from typing import Any

from tests.test_support import DRIVER_DIR

HARNESS_PATH = DRIVER_DIR / "web" / "bridgeHarness.js"
NODE_EXECUTABLE = shutil.which("node")

_VM_RUNNER_PREFIX = r"""
const fs = require("fs");
const vm = require("vm");
const harnessPath = process.argv[2];
const harnessCode = fs.readFileSync(harnessPath, "utf8");

function createHarnessContext(extraGlobals = {}) {
    const events = [];
    const context = {
        events,
        console: { debug() {}, log() {}, warn() {}, error() {} },
        performance,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        queueMicrotask,
        googleTtsForNvdaBridge(payload) {
            events.push(JSON.parse(payload));
        },
        ...extraGlobals,
    };
    context.window = context;
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(harnessCode, context, { filename: harnessPath });
    context.Float32Array = vm.runInContext("Float32Array", context);
    return context;
}

function emitEndEvent(ctx, engine, eventPayload = { type: "end" }) {
    engine.I = false;
    ctx.chrome.runtime.sendMessage({
        type: "offscreenTtsEventResponse",
        event: eventPayload,
    });
}

function collectAudioPcm(ctx) {
    const combined = Buffer.concat(
        ctx.events
            .filter((e) => e && e.type === "audio" && typeof e.data === "string")
            .map((e) => Buffer.from(e.data, "base64"))
    );
    return new Int16Array(combined.buffer, combined.byteOffset, Math.floor(combined.byteLength / 2));
}

function createFakeEngine(ctx, opts = {}) {
    const workletNode = opts.noWorkletNode ? null : (opts.workletNode || new ctx.AudioWorkletNode());
    const sampleLength = opts.sampleLength === undefined ? 64 : opts.sampleLength;
    const fillValue = opts.fillValue === undefined ? 0.25 : opts.fillValue;
    const delayMs = opts.delayMs === undefined ? 5 : opts.delayMs;
    const customOnSpeak = opts.onSpeak;
    const engine = {
        C: { audioWorklet: {} },
        I: Boolean(opts.initialBusy),
        o: workletNode,
        init: opts.init || (async () => {
            if (!engine.o) {
                engine.o = new ctx.AudioWorkletNode();
            }
        }),
        onInstallLanguageRequest: opts.onInstallLanguageRequest || (async () => ({ status: "installed" })),
        onStop: opts.onStop || (async () => undefined),
        onSpeak: customOnSpeak
            ? (...args) => customOnSpeak(...args, engine, workletNode)
            : (async () => {
                engine.I = true;
                setTimeout(() => {
                    if (sampleLength > 0 && engine.o) {
                        engine.o.port.postMessage({
                            command: "addBuffer",
                            buffer: new ctx.Float32Array(sampleLength).fill(fillValue),
                        });
                    }
                    if (opts.skipEndEvent) {
                        engine.I = false;
                    } else {
                        emitEndEvent(ctx, engine, opts.eventPayload);
                    }
                }, delayMs);
            }),
    };
    if (opts.extraProps) {
        Object.assign(engine, opts.extraProps);
    }
    ctx.Yh = engine;
    return engine;
}

function speakWithDefaults(ctx, overrides = {}) {
    return ctx.googleTtsForNvdaSpeak({
        voiceName: "v1",
        lang: "en-US",
        rate: 1,
        pitch: 1,
        volume: 1,
        ...overrides,
    });
}
"""


def _run_harness_js(scriptBody: str, *, timeout: float = 10.0) -> Any:
    fullScript = (
        _VM_RUNNER_PREFIX
        + "\n(async () => {\n"
        + scriptBody
        + "\n})().then((result) => {\n"
        + "    process.stdout.write(JSON.stringify(result === undefined ? null : result));\n"
        + "}).catch((err) => {\n"
        + "    process.stderr.write(String(err && err.stack ? err.stack : err));\n"
        + "    process.exit(1);\n"
        + "});\n"
    )
    completed = subprocess.run(
        [NODE_EXECUTABLE or "node", "-", str(Path(HARNESS_PATH))],
        input=fullScript,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Node script failed with code {completed.returncode}:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return json.loads(completed.stdout) if completed.stdout else None


@unittest.skipUnless(NODE_EXECUTABLE, "Node.js executable required for bridgeHarness runtime tests")
class BridgeHarnessRuntimeTests(unittest.TestCase):
    """Execute bridgeHarness.js directly in Node vm contexts."""

    def test_get_tts_engine_discovers_yh_key_and_caches_instance(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            createFakeEngine(ctx, { noWorkletNode: true, onSpeak: async () => undefined });
            const readyFirst = ctx.googleTtsForNvdaReady();
            delete ctx.Yh;
            const readyCached = ctx.googleTtsForNvdaReady();
            return { readyFirst, readyCached };
            """
        )
        self.assertTrue(result["readyFirst"])
        self.assertTrue(result["readyCached"])

    def test_error_event_surfaces_error_message_and_default_fallback(self) -> None:
        result = _run_harness_js(
            """
            async function runCase(eventPayload) {
                const ctx = createHarnessContext();
                createFakeEngine(ctx, { noWorkletNode: true, sampleLength: 0, eventPayload });
                try {
                    await speakWithDefaults(ctx, { sessionId: "s-err", text: "hello" });
                    return { threw: false };
                } catch (err) {
                    return {
                        threw: true,
                        message: err.message,
                        errorEvents: ctx.events.filter((e) => e.type === "error").map((e) => e.message),
                    };
                }
            }
            const withErrorMessage = await runCase({ type: "error", errorMessage: "WASM OOM error" });
            const bareError = await runCase({ type: "error" });
            return { withErrorMessage, bareError };
            """
        )
        self.assertTrue(result["withErrorMessage"]["threw"])
        self.assertEqual("WASM OOM error", result["withErrorMessage"]["message"])
        self.assertEqual(["WASM OOM error"], result["withErrorMessage"]["errorEvents"])
        self.assertTrue(result["bareError"]["threw"])
        self.assertEqual("Browser speech synthesis failed.", result["bareError"]["message"])

    def test_failed_language_install_is_not_cached_in_ready_languages(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let installAttempts = 0;
            createFakeEngine(ctx, {
                noWorkletNode: true,
                onInstallLanguageRequest: async () => {
                    installAttempts++;
                    return { status: installAttempts === 1 ? "failed" : "installed" };
                },
            });

            let firstError = null;
            try {
                await speakWithDefaults(ctx, { sessionId: "s1", text: "xin chao", voiceName: "vi1", lang: "vi-VN" });
            } catch (err) {
                firstError = err.message;
            }

            const secondResult = await speakWithDefaults(ctx, { sessionId: "s2", text: "xin chao", voiceName: "vi1", lang: "vi-VN" });
            return { installAttempts, firstError, secondResult };
            """
        )
        self.assertEqual(2, result["installAttempts"])
        self.assertIn("failed", result["firstError"])
        self.assertTrue(result["secondResult"]["success"])

    def test_clear_buffers_cancels_empty_timer_even_after_stop(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let emptyCallbacks = 0;
            const workletNode = new ctx.AudioWorkletNode();
            workletNode.port.onmessage = (evt) => {
                if (evt && evt.data && evt.data.type === "empty") {
                    emptyCallbacks++;
                }
            };
            createFakeEngine(ctx, {
                initialBusy: true,
                workletNode,
                onStop: async () => {
                    // offscreen_compiled.js triggers clearBuffers during onStop after stopped=true.
                    workletNode.port.postMessage({ command: "clearBuffers" });
                },
                onSpeak: async (_t, _o, engine) => {
                    workletNode.port.postMessage({
                        command: "addBuffer",
                        buffer: new Float32Array(32).fill(0.1),
                    });
                    await ctx.googleTtsForNvdaStop();
                    engine.I = false;
                },
            });
            const speakRes = await speakWithDefaults(ctx, { sessionId: "s-stop", text: "hello" });
            await new Promise((resolve) => setTimeout(resolve, 30));
            return { speakRes, emptyCallbacks };
            """
        )
        self.assertTrue(result["speakRes"].get("cancelled"))
        self.assertEqual(0, result["emptyCallbacks"])

    def test_async_engine_startup_waits_for_first_poll_and_flushes_leftover_samples(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            createFakeEngine(ctx, {
                extraProps: { J: null, K: 0 },
                onSpeak: async (_t, _o, engine, workletNode) => {
                    // Matches offscreen_compiled.js Ah(): resolves immediately before setTimeout starts synthesis.
                    setTimeout(() => {
                        engine.I = true;
                        setTimeout(() => {
                            workletNode.port.postMessage({
                                command: "addBuffer",
                                buffer: new Float32Array(128).fill(0.2),
                            });
                            const tail = new ctx.Float32Array(128);
                            tail.fill(0.3, 0, 40);
                            engine.J = tail;
                            engine.K = 40;
                            engine.I = false;
                        }, 10);
                    }, 5);
                },
            });
            const speakRes = await speakWithDefaults(ctx, { sessionId: "s-async", text: "hi" });
            const audioChunks = ctx.events.filter((e) => e.type === "audio").map((e) => e.data);
            const doneEvents = ctx.events.filter((e) => e.type === "done").length;
            return { speakRes, audioChunks, doneEvents };
            """
        )
        self.assertTrue(result["speakRes"]["success"])
        self.assertEqual(1, result["doneEvents"])
        totalSamples = sum(len(base64.b64decode(chunk)) // 2 for chunk in result["audioChunks"])
        self.assertEqual(168, totalSamples)

    def test_control_sequence_cancels_superseded_speak_and_preload_safely(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let releaseInstall;
            let callCount = 0;
            const engine = {
                C: { audioWorklet: {} },
                I: false,
                o: null,
                init: async () => {
                    engine.o = new ctx.AudioWorkletNode();
                },
                onInstallLanguageRequest: async (lang) => {
                    callCount++;
                    if (callCount === 1) {
                        await new Promise((resolve) => {
                            releaseInstall = resolve;
                        });
                    }
                    return { status: "installed" };
                },
                onStop: async () => undefined,
                onSpeak: async () => {
                    engine.I = true;
                    setTimeout(() => {
                        engine.o.port.postMessage({
                            command: "addBuffer",
                            buffer: new Float32Array(64).fill(0.25),
                        });
                        engine.I = false;
                        ctx.chrome.runtime.sendMessage({
                            type: "offscreenTtsEventResponse",
                            event: { type: "end" },
                        });
                    }, 5);
                },
            };
            ctx.Yh = engine;

            // 1. Supersede googleTtsForNvdaPreload mid-install with googleTtsForNvdaSpeak
            const preloadPromise = ctx.googleTtsForNvdaPreload({
                sessionId: "preload-1",
                voiceName: "v1",
                lang: "en-US",
            });
            await new Promise((resolve) => setTimeout(resolve, 10));
            const speakPromise = speakWithDefaults(ctx, {
                sessionId: "speak-2",
                segments: ["first", "second"],
                voiceName: "v2",
                lang: "en-GB",
            });
            releaseInstall();
            const [preloadRes, speakRes] = await Promise.all([preloadPromise, speakPromise]);

            // 2. Cancel an in-flight googleTtsForNvdaSpeak via googleTtsForNvdaStop
            callCount = 0;
            const speak3Promise = speakWithDefaults(ctx, {
                sessionId: "speak-3",
                text: "slow",
                voiceName: "v3",
                lang: "fr-FR",
            });
            await new Promise((resolve) => setTimeout(resolve, 10));
            await ctx.googleTtsForNvdaStop();
            releaseInstall();
            const speak3Res = await speak3Promise;
            const speak3Events = ctx.events.filter((e) => e.sessionId === "speak-3");
            const speak2Done = ctx.events.filter((e) => e.sessionId === "speak-2" && e.type === "done").length;

            return { preloadRes, speakRes, speak2Done, speak3Res, speak3EventCount: speak3Events.length };
            """
        )
        self.assertEqual({"success": False, "preloaded": False, "cancelled": True}, result["preloadRes"])
        self.assertEqual({"success": True}, result["speakRes"])
        self.assertEqual(1, result["speak2Done"])
        self.assertEqual({"success": False, "cancelled": True}, result["speak3Res"])
        self.assertEqual(0, result["speak3EventCount"])

    def test_pitch_shift_clamps_final_index_without_nan_dropout(self) -> None:
        result = _run_harness_js(
            """
            async function synthesizeConstantWithPitch(postPitch, sampleLength) {
                const ctx = createHarnessContext();
                createFakeEngine(ctx, { sampleLength, fillValue: 0.5 });
                await speakWithDefaults(ctx, {
                    sessionId: "s-pitch",
                    text: "a",
                    outputGain: 1.0,
                    artificialRate: 1.25,
                    postPitch,
                });
                return ctx.events.filter((e) => e.type === "audio").map((e) => e.data);
            }

            const highPitchChunks = await synthesizeConstantWithPitch(1.35, 257);
            const lowPitchChunks = await synthesizeConstantWithPitch(0.65, 259);
            return { highPitchChunks, lowPitchChunks };
            """
        )
        for label in ("highPitchChunks", "lowPitchChunks"):
            rawPcm = b"".join(base64.b64decode(c) for c in result[label])
            self.assertGreater(len(rawPcm), 0, label)
            samples = struct.unpack(f"<{len(rawPcm) // 2}h", rawPcm)
            # Constant 0.5 input signal must stay near 16383 throughout (NaN becomes 0 in Int16Array).
            self.assertEqual(
                [], [s for s in samples if s < 14000], f"Unexpected NaN/zero dropout in {label}: {samples[-8:]}"
            )

    def test_preload_suppresses_audio_emissions_even_when_worklet_buffers_arrive(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            createFakeEngine(ctx, { sampleLength: 256, fillValue: 0.5 });

            const preloadRes = await ctx.googleTtsForNvdaPreload({
                sessionId: "preload-audio-check",
                voiceName: "v1",
                lang: "en-US",
                text: "warmup",
            });
            const audioDuringPreload = ctx.events.filter((e) => e.type === "audio").length;

            const speakRes = await speakWithDefaults(ctx, { sessionId: "speak-after-preload", text: "hello" });
            const audioAfterSpeak = ctx.events.filter((e) => e.sessionId === "speak-after-preload" && e.type === "audio").length;
            return { preloadRes, audioDuringPreload, speakRes, audioAfterSpeak };
            """
        )
        self.assertEqual({"success": True, "preloaded": True}, result["preloadRes"])
        self.assertEqual(0, result["audioDuringPreload"])
        self.assertEqual({"success": True}, result["speakRes"])
        self.assertGreater(result["audioAfterSpeak"], 0)

    def test_empty_speak_resets_session_and_allows_subsequent_audio(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            createFakeEngine(ctx, { sampleLength: 128, fillValue: 0.4 });

            const emptyRes = await speakWithDefaults(ctx, { sessionId: "empty-1", text: "" });
            const speakRes = await speakWithDefaults(ctx, { sessionId: "speak-1", text: "hi" });
            const audioCount = ctx.events.filter((e) => e.sessionId === "speak-1" && e.type === "audio").length;
            return { emptyRes, speakRes, audioCount };
            """
        )
        self.assertEqual({"success": True, "empty": True}, result["emptyRes"])
        self.assertEqual({"success": True}, result["speakRes"])
        self.assertGreater(result["audioCount"], 0)

    def test_sync_engine_init_throw_resets_init_promise_for_retry(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let initCalls = 0;
            createFakeEngine(ctx, {
                sampleLength: 64,
                fillValue: 0.3,
                init: () => {
                    initCalls++;
                    if (initCalls === 1) {
                        throw new Error("init sync boom");
                    }
                    return Promise.resolve();
                },
            });

            let firstErr = null;
            try {
                await speakWithDefaults(ctx, { sessionId: "s-init-1", text: "hello" });
            } catch (err) {
                firstErr = err.message;
            }
            const secondRes = await speakWithDefaults(ctx, { sessionId: "s-init-2", text: "hello" });
            return { initCalls, firstErr, secondRes };
            """
        )
        self.assertEqual(2, result["initCalls"])
        self.assertEqual("init sync boom", result["firstErr"])
        self.assertEqual({"success": True}, result["secondRes"])

    def test_wsola_high_tempo_streaming_does_not_discard_unconsumed_samples(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            createFakeEngine(ctx, {
                initialBusy: true,
                onSpeak: async (_t, _o, engine, workletNode) => {
                    for (let i = 0; i < 6; i++) {
                        const block = new ctx.Float32Array(2400);
                        for (let j = 0; j < block.length; j++) {
                            block[j] = 0.25 * Math.sin((i * 2400 + j) * 0.05);
                        }
                        workletNode.port.postMessage({
                            command: "addBuffer",
                            buffer: block,
                        });
                    }
                    engine.I = false;
                },
            });
            const speakRes = await speakWithDefaults(ctx, {
                sessionId: "s-wsola-high-tempo",
                text: "streaming tempo test",
                rate: 1.25,
                artificialRate: 1.5,
                useProtectedEngineRate: true,
            });
            const totalOutSamples = collectAudioPcm(ctx).length;
            const expectedApprox = Math.round((6 * 2400) / 1.5);
            return { speakRes, totalOutSamples, expectedApprox };
            """
        )
        self.assertEqual({"success": True}, result["speakRes"])
        self.assertGreater(result["totalOutSamples"], int(result["expectedApprox"] * 0.85))
        self.assertLess(result["totalOutSamples"], int(result["expectedApprox"] * 1.15))

    def test_concurrent_language_installs_coalesce_via_pending_languages(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let installCalls = 0;
            let releaseInstall;
            createFakeEngine(ctx, {
                onInstallLanguageRequest: async () => {
                    installCalls++;
                    await new Promise((resolve) => {
                        releaseInstall = resolve;
                    });
                    return { status: "installed" };
                },
            });
            const p1 = ctx.googleTtsForNvdaPreload({ sessionId: "p1", voiceName: "v1", lang: "vi-VN" });
            await new Promise((resolve) => setTimeout(resolve, 10));
            const p2 = ctx.googleTtsForNvdaPreload({ sessionId: "p2", voiceName: "v1", lang: "vi-VN" });
            await new Promise((resolve) => setTimeout(resolve, 10));
            releaseInstall();
            const [r1, r2] = await Promise.all([p1, p2]);
            return { installCalls, r1, r2 };
            """
        )
        self.assertEqual(1, result["installCalls"])
        self.assertEqual({"success": False, "preloaded": False, "cancelled": True}, result["r1"])
        self.assertEqual({"success": True, "preloaded": True}, result["r2"])

    def test_pitch_changes_preserve_utterance_duration_and_tail_frequency(self) -> None:
        result = _run_harness_js(
            """
            async function runPitchCase(postPitch, inputSamples, artificialRate) {
                const ctx = createHarnessContext();
                createFakeEngine(ctx, {
                    onSpeak: async (_t, _o, engine, workletNode) => {
                        engine.I = true;
                        setTimeout(() => {
                            for (let off = 0; off < inputSamples; off += 128) {
                                const n = Math.min(128, inputSamples - off);
                                const buf = new ctx.Float32Array(n);
                                for (let j = 0; j < n; j++) {
                                    buf[j] = 0.3 * Math.sin((2 * Math.PI * 180 * (off + j)) / 24000);
                                }
                                workletNode.port.postMessage({ command: "addBuffer", buffer: buf });
                            }
                            emitEndEvent(ctx, engine);
                        }, 2);
                    },
                });
                await speakWithDefaults(ctx, {
                    sessionId: `s-pitch-${postPitch}-${inputSamples}`,
                    text: "duration stability check",
                    lang: "vi-VN",
                    rate: 1.25,
                    artificialRate,
                    postPitch,
                    outputGain: 1.0,
                });
                const pcm = collectAudioPcm(ctx);
                const start = Math.floor(pcm.length * 0.7);
                let zc = 0;
                for (let i = start + 1; i < pcm.length; i++) {
                    if ((pcm[i - 1] < 0 && pcm[i] >= 0) || (pcm[i - 1] >= 0 && pcm[i] < 0)) {
                        zc++;
                    }
                }
                const tailF0 = (zc / 2) / Math.max(0.001, (pcm.length - start) / 24000);
                return { outSamples: pcm.length, expectedSamples: Math.round(inputSamples / artificialRate), tailF0 };
            }

            const cases = [];
            for (const [postPitch, inputSamples, artificialRate] of [
                [0.4, 1200, 1.0],
                [1.12, 1200, 1.0],
                [1.6, 1200, 1.0],
                [0.4, 4800, 1.27],
                [1.0, 4800, 1.27],
                [1.12, 4800, 1.27],
                [1.6, 4800, 1.27],
            ]) {
                const res = await runPitchCase(postPitch, inputSamples, artificialRate);
                cases.push({ postPitch, inputSamples, artificialRate, ...res });
            }
            return cases;
            """
        )
        for case in result:
            ratio = case["outSamples"] / case["expectedSamples"]
            self.assertAlmostEqual(1.0, ratio, delta=0.02, msg=f"Duration drift on {case}")
            if case["inputSamples"] >= 4800:
                expectedF0 = 180.0 * (2.0 ** ((case["postPitch"] - 1.0) * 20.0 / 12.0))
                self.assertAlmostEqual(
                    expectedF0, case["tailF0"], delta=expectedF0 * 0.12, msg=f"Tail pitch drift on {case}"
                )

    def test_zero_delay_scheduler_uses_message_channel_and_supports_clear_timeout(self) -> None:
        result = _run_harness_js(
            """
            let nativeCalls = 0;
            let channelPosts = 0;
            class FakeMessageChannel {
                constructor() {
                    this.port1 = { onmessage: null };
                    this.port2 = {
                        postMessage: () => {
                            channelPosts++;
                            setImmediate(() => {
                                if (typeof this.port1.onmessage === "function") {
                                    this.port1.onmessage({ data: null });
                                }
                            });
                        },
                    };
                }
            }
            const ctx = createHarnessContext({
                MessageChannel: FakeMessageChannel,
                setTimeout(fn, delay, ...args) {
                    nativeCalls++;
                    return setTimeout(fn, delay, ...args);
                },
            });
            const order = [];
            ctx.setTimeout((v) => order.push("zero:" + v), 0, "a");
            ctx.setTimeout(() => order.push("undef"));
            const cancelledId = ctx.setTimeout(() => order.push("never"), 0);
            ctx.clearTimeout(cancelledId);
            await new Promise((resolve) => ctx.setTimeout(() => {
                order.push("delayed");
                resolve();
            }, 5));
            return { nativeCalls, channelPosts, order };
            """
        )
        self.assertEqual(1, result["nativeCalls"])
        self.assertEqual(3, result["channelPosts"])
        self.assertEqual(["zero:a", "undef", "delayed"], result["order"])

    def test_local_voice_packages_skip_eager_language_install_and_serialize_prepares(self) -> None:
        result = _run_harness_js(
            """
            const ctx = createHarnessContext();
            let langInstalls = 0;
            let activeSpeakInits = 0;
            let maxConcurrentSpeakInits = 0;
            let releaseFirstSpeak;
            createFakeEngine(ctx, {
                extraProps: {
                    G: { v1: "pkg-dep", v2: "pkg-main" },
                    N: { v1: "pkg-dep", v2: "pkg-main" },
                    S: { v1: "/pkg-dep.zvoice", v2: "/pkg-main.zvoice" },
                    D: { v2: "pkg-dep" },
                },
                onInstallLanguageRequest: async () => {
                    langInstalls++;
                    return { status: "installed" };
                },
                onSpeak: async (_text, opts, engine, workletNode) => {
                    activeSpeakInits++;
                    maxConcurrentSpeakInits = Math.max(maxConcurrentSpeakInits, activeSpeakInits);
                    if (opts.voiceName === "v2") {
                        await new Promise((resolve) => {
                            releaseFirstSpeak = resolve;
                        });
                    }
                    activeSpeakInits--;
                    engine.I = true;
                    setTimeout(() => {
                        workletNode.port.postMessage({
                            command: "addBuffer",
                            buffer: new ctx.Float32Array(64).fill(0.25),
                        });
                        emitEndEvent(ctx, engine);
                    }, 2);
                },
            });

            // Start v2 (which depends on pkg-dep), supersede with v1 (pkg-dep) before v2 resolves onSpeak.
            const p1 = speakWithDefaults(ctx, { sessionId: "s-dep-1", text: "one", voiceName: "v2" });
            await new Promise((resolve) => setTimeout(resolve, 5));
            const p2 = speakWithDefaults(ctx, { sessionId: "s-dep-2", text: "two" });
            await new Promise((resolve) => setTimeout(resolve, 10));
            const concurrentWhileBlocked = maxConcurrentSpeakInits;
            releaseFirstSpeak();
            const [r1, r2] = await Promise.all([p1, p2]);
            return { langInstalls, concurrentWhileBlocked, maxConcurrentSpeakInits, r1, r2 };
            """
        )
        self.assertEqual(0, result["langInstalls"])
        self.assertEqual(1, result["concurrentWhileBlocked"])
        self.assertEqual(1, result["maxConcurrentSpeakInits"])
        self.assertTrue(result["r1"].get("cancelled"))
        self.assertEqual({"success": True}, result["r2"])

    def test_wsola_covers_low_male_pitch_periods_and_decoupled_extremes(self) -> None:
        """Upgraded WSOLA must maintain periodic waveform continuity on low male F0 (115 Hz) across extreme rates/pitches."""
        result = _run_harness_js(
            """
            async function runLowMaleCase(artificialRate, postPitch) {
                const ctx = createHarnessContext();
                const inputSamples = 14400; // 600 ms @ 24 kHz; 115 Hz T0 = 208.7 samples
                createFakeEngine(ctx, {
                    onSpeak: async (_t, _o, engine, workletNode) => {
                        engine.I = true;
                        setTimeout(() => {
                            for (let off = 0; off < inputSamples; off += 600) {
                                const n = Math.min(600, inputSamples - off);
                                const buf = new ctx.Float32Array(n);
                                for (let j = 0; j < n; j++) {
                                    const t = (off + j) / 24000;
                                    // Rich harmonic glottal-like male voice wave @ 115 Hz
                                    buf[j] = 0.22 * Math.sin(2 * Math.PI * 115 * t)
                                        + 0.14 * Math.sin(2 * Math.PI * 230 * t + 0.4)
                                        + 0.08 * Math.sin(2 * Math.PI * 345 * t + 0.9);
                                }
                                workletNode.port.postMessage({ command: "addBuffer", buffer: buf });
                            }
                            emitEndEvent(ctx, engine);
                        }, 2);
                    },
                });
                await speakWithDefaults(ctx, {
                    sessionId: `s-male-${artificialRate}-${postPitch}`,
                    text: "male periodicity check",
                    rate: 1.25,
                    artificialRate,
                    postPitch,
                    outputGain: 1.0,
                });
                const pcm = collectAudioPcm(ctx);
                // Compute normalized peak autocorrelation around expected shifted period (50..480 samples).
                const pFactor = Math.max(0.35, Math.min(2.5, Math.pow(2, (Math.max(0.4, Math.min(1.6, postPitch)) - 1) * 20 / 12)));
                const expectedPeriod = Math.round(24000 / (115 * pFactor));
                const winStart = Math.floor(pcm.length * 0.25);
                const winLen = Math.min(1200, Math.floor(pcm.length * 0.5));
                let bestCorr = 0;
                for (let lag = Math.max(40, expectedPeriod - 12); lag <= expectedPeriod + 12; lag++) {
                    if (winStart + winLen + lag >= pcm.length) break;
                    let num = 0, e1 = 0, e2 = 0;
                    for (let i = 0; i < winLen; i++) {
                        const a = pcm[winStart + i];
                        const b = pcm[winStart + i + lag];
                        num += a * b;
                        e1 += a * a;
                        e2 += b * b;
                    }
                    const corr = num / Math.sqrt(Math.max(1, e1 * e2));
                    if (corr > bestCorr) bestCorr = corr;
                }
                return {
                    outSamples: pcm.length,
                    expectedSamples: Math.round(inputSamples / artificialRate),
                    bestCorr,
                };
            }

            const cases = [];
            for (const [artificialRate, postPitch] of [
                [1.6, 1.0],
                [1.85, 0.4],
                [1.6, 1.6],
            ]) {
                cases.push({ artificialRate, postPitch, ...(await runLowMaleCase(artificialRate, postPitch)) });
            }
            return cases;
            """
        )
        for case in result:
            ratio = case["outSamples"] / case["expectedSamples"]
            self.assertAlmostEqual(1.0, ratio, delta=0.02, msg=f"Duration mismatch: {case}")
            self.assertGreater(case["bestCorr"], 0.90, msg=f"Lost periodicity on {case}")

    def test_pitch_up_anti_alias_filter_suppresses_nyquist_folding(self) -> None:
        """Up-pitching must attenuate tones above the resampled Nyquist limit without degrading speech-band tones."""
        result = _run_harness_js(
            """
            async function measureOutputRms(freqHz, postPitch) {
                const ctx = createHarnessContext();
                const inputSamples = 4800;
                createFakeEngine(ctx, {
                    onSpeak: async (_t, _o, engine, workletNode) => {
                        engine.I = true;
                        setTimeout(() => {
                            const buf = new ctx.Float32Array(inputSamples);
                            for (let i = 0; i < inputSamples; i++) {
                                buf[i] = 0.4 * Math.sin((2 * Math.PI * freqHz * i) / 24000);
                            }
                            workletNode.port.postMessage({ command: "addBuffer", buffer: buf });
                            emitEndEvent(ctx, engine);
                        }, 2);
                    },
                });
                await speakWithDefaults(ctx, {
                    sessionId: `s-aa-${freqHz}-${postPitch}`,
                    text: "aa check",
                    artificialRate: 1.0,
                    postPitch,
                    outputGain: 1.0,
                });
                const pcm = collectAudioPcm(ctx);
                const skip = Math.floor(pcm.length * 0.1);
                let sq = 0;
                for (let i = skip; i < pcm.length; i++) {
                    const s = pcm[i] / 32768;
                    sq += s * s;
                }
                return Math.sqrt(sq / Math.max(1, pcm.length - skip));
            }
            const lowBandRms = await measureOutputRms(300, 1.6);
            const aliasBandRms = await measureOutputRms(9500, 1.6);
            return { lowBandRms, aliasBandRms };
            """
        )
        self.assertGreater(result["lowBandRms"], 0.25)
        self.assertLess(result["aliasBandRms"], result["lowBandRms"] * 0.35)


if __name__ == "__main__":
    unittest.main()

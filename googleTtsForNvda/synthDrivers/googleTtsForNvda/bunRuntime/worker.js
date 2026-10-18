// worker.js — the long-lived Bun process that replaces headless Chromium.
//
// This is the Bun counterpart of web/index.html. It loads, in order:
//
//   1. shim.js                 — the Bun host-API shims
//   2. ../web/bridgeHarness.js — UNCHANGED, shared verbatim with the browser path
//   3. engine_boot.js          — generated: bindings_main.js + offscreen_compiled.js
//
// Reusing bridgeHarness.js as-is is deliberate: all of the audio behaviour lives
// there (soft limiter and output gain, post-pitch resampling, WSOLA tempo for
// artificial rate, segment boundary hold, adaptive packet sizing, session token
// fencing, mark/segmentEnd/done event ordering). Duplicating it would risk the two
// runtimes drifting apart, and tests/test_performance.py already asserts those
// constants by reading web/bridgeHarness.js.
//
// Protocol — one JSON object per line.
//
// stdin (from Python):
//   {"id":<int>,"op":"speak","payload":{...}}
//   {"id":<int>,"op":"preload","payload":{...}}
//   {"id":<int>,"op":"stop"}
//   {"id":<int>,"op":"ready"}
//
// stdout (to Python). Only protocol JSON is ever written here; all logging goes to
// stderr, because bridge.py's event parser expects CDP-shaped envelopes:
//   {"method":"Runtime.ready"}
//   {"method":"Runtime.bindingCalled","params":{"name":"googleTtsForNvdaBridge","payload":"<json>"}}
//   {"id":<int>,"ok":true,"result":{...}}
//   {"id":<int>,"ok":false,"error":"<message>"}
//
// The bindingCalled envelope is shaped exactly like the CDP event that
// WasmTtsEngineBridge.handle_event() already parses, so that method is shared by
// both runtimes without modification.
"use strict";

const path = require("node:path");

const BINDING_NAME = process.env.GTTS_BINDING_NAME || "googleTtsForNvdaBridge";
const WEB_DIR = process.env.GTTS_WEB_DIR || "";
const MAIN_ENTRY = process.env.GTTS_MAIN_ENTRY || "";
const PREVIOUS_SPEAK_SETTLE_TIMEOUT_MS = Number(process.env.GTTS_SPEAK_SETTLE_TIMEOUT_MS) || 1000;

function log(...args) {
	console.error("[bunRuntime]", ...args);
}

function writeLine(object) {
	try {
		process.stdout.write(JSON.stringify(object) + "\n");
	} catch (error) {
		log("failed to serialise protocol message:", String(error));
	}
}

function fail(id, error) {
	writeLine({ id, ok: false, error: (error && error.message) || String(error) });
}

// Audio accounting, so Python can prove whether a truncated utterance lost samples
// in transport or never had them synthesised. jsAudioPackets/jsSamples count what
// this side emitted; BunClient compares them with what handle_event() delivered.
const emitted = { packets: 0, samples: 0 };

// The harness calls this instead of the CDP Runtime.addBinding function.
globalThis[BINDING_NAME] = function googleTtsForNvdaBridge(payloadJson) {
	const text = String(payloadJson);
	try {
		const message = JSON.parse(text);
		if (message && message.type === "audio") {
			emitted.packets += 1;
			// bridgeHarness.js sends {type,sampleRate,data} with data as base64 s16 PCM
			// and no explicit sample count, so derive it from the payload length.
			const encoded = String(message.data || "");
			const padding = encoded.length - encoded.replace(/=+$/, "").length;
			const byteCount = Math.floor((encoded.length * 3) / 4) - padding;
			emitted.samples += Math.max(0, Math.floor(byteCount / 2));
		}
	} catch (error) {
		// Never let accounting break the protocol stream.
	}
	writeLine({
		method: "Runtime.bindingCalled",
		params: { name: BINDING_NAME, payload: text },
	});
};

let loadError = null;
try {
	if (!WEB_DIR || !MAIN_ENTRY) {
		throw new Error("GTTS_WEB_DIR and GTTS_MAIN_ENTRY must be set");
	}
	// shim.js is loaded first so window/self, the Worker shim and the indexedDB
	// stub exist before the engine evaluates.
	require(path.join(__dirname, "shim.js"));
	// bridgeHarness.js installs window.AudioContext / AudioWorkletNode / chrome and
	// the googleTtsForNvda* entry points. It must run before the engine so that
	// `new AudioContext({sampleRate:24000})` inside the engine picks up the fake.
	require(path.join(WEB_DIR, "bridgeHarness.js"));
	// Generated bootstrap: Google's bindings_main.js + offscreen_compiled.js.
	require(MAIN_ENTRY);
} catch (error) {
	loadError = error;
	log("engine load failed:", (error && error.stack) || String(error));
}

const ready = !loadError && typeof globalThis.googleTtsForNvdaSpeak === "function";
if (loadError) {
	writeLine({ method: "Runtime.loadError", error: (loadError && loadError.message) || String(loadError) });
} else {
	writeLine({ method: "Runtime.ready", harnessReady: ready });
	log("worker ready; harness functions:", typeof globalThis.googleTtsForNvdaSpeak);
}

// bridgeHarness.js's googleTtsForNvdaSpeak() success tail fences every call with
// sessionToken EXCEPT the final `await stopActiveSynthesis()`, which unconditionally
// does currentSessionToken++, resetAudioQueue() and engine.onStop(). If a superseded
// invocation is still unwinding when the next session has already called
// beginSession(), that unfenced call lands inside the NEW session and truncates it
// mid-utterance -- the tail of the sentence is simply never emitted.
//
// The browser path narrows this window because its CDP stop expression really does
// run at cancel time. Here we close it structurally instead: never let two
// invocations overlap. engine.onSpeak() resolves as soon as buffered synthesis is
// kicked off (the audio is pumped by later setTimeout callbacks), and
// waitForSynthesisComplete() returns immediately once `stopped` is set, so a
// superseded invocation settles in a few microtasks and the bounded wait below is
// effectively free.
let activeSpeak = null;

function settleWithBound(promise, milliseconds) {
	let timer = null;
	const timeout = new Promise((resolve) => {
		timer = setTimeout(() => resolve("timeout"), milliseconds);
	});
	return Promise.race([
		Promise.resolve(promise).then(
			() => "settled",
			() => "settled",
		),
		timeout,
	]).then((outcome) => {
		if (timer !== null) clearTimeout(timer);
		return outcome;
	});
}

async function speakExclusive(payload) {
	const previous = activeSpeak;
	if (previous) {
		log("speak arrived while a previous utterance was still in flight; settling it first");
		try {
			if (typeof globalThis.googleTtsForNvdaStop === "function") {
				await globalThis.googleTtsForNvdaStop();
			}
		} catch (error) {
			log("stop before re-speak failed:", String(error));
		}
		const outcome = await settleWithBound(previous, PREVIOUS_SPEAK_SETTLE_TIMEOUT_MS);
		if (outcome === "timeout") {
			log("previous utterance did not settle within", PREVIOUS_SPEAK_SETTLE_TIMEOUT_MS, "ms; starting anyway");
		}
	}
	emitted.packets = 0;
	emitted.samples = 0;
	const current = globalThis.googleTtsForNvdaSpeak(payload);
	activeSpeak = current;
	try {
		const result = await current;
		if (result && typeof result === "object") {
			result.jsAudioPackets = emitted.packets;
			result.jsAudioSamples = emitted.samples;
		}
		return result;
	} finally {
		if (activeSpeak === current) {
			activeSpeak = null;
		}
	}
}

const inflight = new Map();

async function runOp(id, message) {
	const op = message.op;
	const payload = message.payload || {};
	if (!ready) {
		throw new Error("Bun speech runtime failed to load the WASM TTS engine.");
	}
	if (op === "ready") {
		const isReady = typeof globalThis.googleTtsForNvdaReady === "function" && globalThis.googleTtsForNvdaReady() === true;
		return { ready: isReady };
	}
	if (op === "stop") {
		if (typeof globalThis.googleTtsForNvdaStop === "function") {
			await globalThis.googleTtsForNvdaStop();
		}
		return { stopped: true };
	}
	if (op === "preload") {
		return await globalThis.googleTtsForNvdaPreload(payload);
	}
	if (op === "speak") {
		return await speakExclusive(payload);
	}
	throw new Error("Unknown operation: " + String(op));
}

function dispatch(line) {
	let message;
	try {
		message = JSON.parse(line);
	} catch (error) {
		log("ignoring malformed stdin line:", line.slice(0, 120));
		return;
	}
	const id = message.id;
	if (id === undefined || id === null) {
		// Fire-and-forget command from BunClient.send_command_async(), which is how
		// WasmTtsEngineBridge.send_fast_stop() cancels speech. These used to be
		// dropped here, so cancellation never reached the engine and the cancelled
		// utterance stayed in flight -- which is what allowed the race above.
		runOp(null, message).catch((error) => {
			log("fire-and-forget command failed:", (error && error.message) || String(error));
		});
		return;
	}
	if (inflight.has(id)) {
		log("ignoring duplicate request id", id);
		return;
	}
	inflight.set(id, true);
	runOp(id, message).then(
		(result) => {
			inflight.delete(id);
			writeLine({ id, ok: true, result: result === undefined ? null : result });
		},
		(error) => {
			inflight.delete(id);
			fail(id, error);
		},
	);
}

let pending = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
	pending += chunk;
	let newline = pending.indexOf("\n");
	while (newline >= 0) {
		const line = pending.slice(0, newline).trim();
		pending = pending.slice(newline + 1);
		newline = pending.indexOf("\n");
		if (line) {
			dispatch(line);
		}
	}
});
process.stdin.on("end", () => process.exit(0));
process.stdin.on("error", () => process.exit(0));
process.on("uncaughtException", (error) => {
	log("uncaught exception:", (error && error.stack) || String(error));
	writeLine({ method: "Runtime.crashed", error: (error && error.message) || String(error) });
	process.exit(1);
});

// shim.js — makes Google's bundled WASM TTS engine run inside Bun.
//
// The add-on never uses Chromium as a *browser*: web/index.html is an empty page
// whose only job is to host three scripts, and web/bridgeHarness.js already fakes
// every Web API the engine touches. So the browser is only being used as a
// JavaScript + WebAssembly host. This file supplies the handful of host APIs that
// Bun does not provide, which lets the very same bridgeHarness.js and the very
// same engine files run here unchanged.
//
// Three real gaps are closed (all measured against Bun 1.4.x):
//
//   1. Bun never delivers worker_threads messages to `globalThis.onmessage`.
//      Emscripten's glue (bindings_main.js, "Deno and Bun already have postMessage
//      defined ... and deliver messages to globalThis.onmessage") skips installing
//      its own parentPort bridge whenever `globalThis.postMessage` exists. That
//      assumption is wrong for Bun: sending works, receiving does not. Without the
//      bridge the pthread pool never answers CMD_LOAD and module initialisation
//      hangs forever on `still waiting on run dependencies: loading-workers`.
//
//   2. Emscripten's pthread self-boot test at the tail of bindings_main.js keys off
//      `globalThis.name`, which is undefined inside a Bun worker, and off
//      `globalThis.Worker`, which in Bun is the DOM-style Worker and therefore has
//      no `.on()` / `.unref()`. Both are corrected here.
//
//   3. There is no `indexedDB` (so Emscripten's IDBFS cache asserts) and `fetch()`
//      cannot resolve the relative URLs the engine uses (`voices.json`,
//      `/<packageId>.zvoice`), because there is no document base URL and no local
//      HTTP server in this runtime.
//
// Nothing under WasmTtsEngine/ or web/ is modified.
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const workerThreads = require("node:worker_threads");

const ENGINE_DIR = process.env.GTTS_ENGINE_DIR || "";
const VOICE_DIR = process.env.GTTS_VOICE_DIR || "";
const VOICES_JSON = process.env.GTTS_VOICES_JSON || "";
const PTHREAD_ENTRY = process.env.GTTS_PTHREAD_ENTRY || "";
const DEBUG = process.env.GTTS_DEBUG === "1";

function debug(...args) {
	if (DEBUG) console.error("[bunRuntime]", ...args);
}

// The engine is Closure-compiled output that assumes a browser global scope.
globalThis.window = globalThis.window || globalThis;
globalThis.self = globalThis.self || globalThis;

// ---------------------------------------------------------------------------
// Gap 1 — parentPort -> globalThis.onmessage bridge (pthread workers only).
//
// globalThis.onmessage is read at dispatch time, not captured now, because
// bindings_main.js assigns it later (runtime_pthread.js: `self.onmessage =
// handleMessage`). Installing this before the engine loads is what matters.
// ---------------------------------------------------------------------------
if (!workerThreads.isMainThread && workerThreads.parentPort && !globalThis.__gttsPthreadBridge) {
	globalThis.__gttsPthreadBridge = true;
	workerThreads.parentPort.on("message", (message) => {
		if (typeof globalThis.onmessage === "function") {
			globalThis.onmessage({ data: message });
		}
	});
	debug("installed parentPort -> onmessage bridge in pthread worker");
}

// ---------------------------------------------------------------------------
// Gap 2 — Worker construction.
//
// Emscripten's allocateUnusedWorker() passes {name: "em-pthread-N"} and expects the
// worker to notice. Bun does forward `workerData`, so translate the name into it,
// force the node:worker_threads Worker class, and redirect the worker to a
// generated entry file that installs Gap 1's bridge before bindings_main.js runs.
// Bun's --preload / bunfig preload only execute on the main thread, so a preload
// flag cannot be used for this.
// ---------------------------------------------------------------------------
(function installPthreadWorkerShim() {
	if (globalThis.__gttsWorkerShim) {
		return;
	}
	globalThis.__gttsWorkerShim = true;
	const NativeWorker = workerThreads.Worker;

	function PatchedWorker(target, options) {
		const opts = Object.assign({}, options);
		let workerName = "";
		try {
			if (typeof opts.name === "string") {
				workerName = opts.name;
			} else if (target && typeof target.name === "string") {
				workerName = target.name;
			}
		} catch (_) {
			workerName = "";
		}
		const isPthreadWorker = workerName.startsWith("em-pthread") || opts.workerData === "em-pthread";
		if (isPthreadWorker) {
			opts.workerData = "em-pthread";
			if (PTHREAD_ENTRY) {
				target = PTHREAD_ENTRY;
			}
		}
		debug("spawning worker", String(target).slice(-40), "pthread=" + isPthreadWorker);
		return new NativeWorker(target, opts);
	}
	PatchedWorker.prototype = NativeWorker.prototype;

	// bindings_main.js reassigns globalThis.Worker from node:worker_threads under
	// ENVIRONMENT_IS_NODE; patch both so the shim survives that assignment.
	try {
		workerThreads.Worker = PatchedWorker;
	} catch (_) {}
	globalThis.Worker = PatchedWorker;
})();

// ---------------------------------------------------------------------------
// Gap 3a — relative-URL fetch, replacing bridge.py's local HTTP server.
//
// Mirrors _BridgeRequestHandler.translate_path(): "voices.json" resolves to the
// generated runtime catalog and any "*.zvoice" resolves inside the voice package
// directory. Traversal is rejected the same way _safe_join() rejects it.
// ---------------------------------------------------------------------------
function safeJoin(root, relative) {
	const resolvedRoot = path.resolve(root);
	const target = path.resolve(resolvedRoot, decodeURIComponent(String(relative).replace(/^[\\/]+/, "")));
	if (target !== resolvedRoot && !target.startsWith(resolvedRoot + path.sep)) {
		return null;
	}
	return target;
}

function resolveLocalUrl(url) {
	const route = String(url).split("?")[0].split("#")[0];
	if (route === "/" || route === "") {
		return null;
	}
	if (route.replace(/^\/+/, "") === "voices.json") {
		return VOICES_JSON || null;
	}
	if (route.startsWith("/engine/")) {
		return ENGINE_DIR ? safeJoin(ENGINE_DIR, route.slice("/engine/".length)) : null;
	}
	if (route.endsWith(".zvoice")) {
		return VOICE_DIR ? safeJoin(VOICE_DIR, route.replace(/^\/+/, "")) : null;
	}
	return null;
}

(function installFetchShim() {
	if (globalThis.__gttsFetchShim) {
		return;
	}
	globalThis.__gttsFetchShim = true;
	const nativeFetch = globalThis.fetch;
	globalThis.fetch = async function patchedFetch(input, init) {
		const url = typeof input === "string" ? input : String((input && input.url) || input);
		if (/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) {
			return nativeFetch(input, init);
		}
		const file = resolveLocalUrl(url);
		debug("fetch", JSON.stringify(url), "->", file || "NOT FOUND");
		if (!file || !fs.existsSync(file)) {
			return new Response("not found", { status: 404, statusText: "Not Found" });
		}
		return new Response(fs.readFileSync(file), {
			status: 200,
			statusText: "OK",
			headers: { "Content-Type": "application/octet-stream" },
		});
	};
})();

// ---------------------------------------------------------------------------
// Gap 3b — minimal in-memory indexedDB.
//
// offscreen_compiled.js mounts Emscripten's IDBFS at /voices purely as a cache and
// then calls FS.syncfs(). IDBFS.mount() just delegates to MEMFS, so mounting is
// harmless; only syncfs touches indexedDB, where it asserts. That cache is
// redundant in this add-on: voice packages already live on disk under
// <CONFIG>/googleTtsForNvda/voices/ and are SHA-256 verified by voice_store.py,
// and they reach the engine through the fetch shim above, never through /voices.
//
// An *empty* database is therefore exactly correct, and IDBFS.reconcile()
// short-circuits via `if (!total) return callback(null)` when the local and remote
// entry sets match. All that is needed is open()/onupgradeneeded/onsuccess plus a
// cursor that reports no records.
// ---------------------------------------------------------------------------
(function installIndexedDbStub() {
	if (typeof globalThis.indexedDB !== "undefined") {
		return;
	}

	class FakeRequest {
		constructor() {
			this.result = undefined;
			this.error = null;
			this.transaction = null;
			this.onsuccess = null;
			this.onerror = null;
			this.onupgradeneeded = null;
		}

		_fire(handler, target) {
			if (typeof handler !== "function") {
				return;
			}
			try {
				handler({ target: target || this, type: "success" });
			} catch (_) {}
		}
	}

	class FakeIndex {
		constructor(name) {
			this.name = name || "timestamp";
		}

		openKeyCursor() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess, { result: null }), 0);
			return request;
		}

		openCursor() {
			return this.openKeyCursor();
		}

		get() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess, { result: undefined }), 0);
			return request;
		}
	}

	class FakeObjectStore {
		constructor(name) {
			this.name = name;
			this.keyPath = null;
			this.indexNames = { contains: () => false };
		}

		index(name) {
			return new FakeIndex(name);
		}

		createIndex(name) {
			return new FakeIndex(name);
		}

		_ack() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess), 0);
			return request;
		}

		put() {
			return this._ack();
		}

		add() {
			return this._ack();
		}

		delete() {
			return this._ack();
		}

		clear() {
			return this._ack();
		}

		get() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess, { result: undefined }), 0);
			return request;
		}

		openCursor() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess, { result: null }), 0);
			return request;
		}

		count() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess, { result: 0 }), 0);
			return request;
		}
	}

	class FakeTransaction {
		constructor(stores) {
			this._stores = stores || {};
			this.onerror = null;
			this.onabort = null;
			this.oncomplete = null;
		}

		objectStore(name) {
			if (!this._stores[name]) {
				this._stores[name] = new FakeObjectStore(name);
			}
			return this._stores[name];
		}

		_complete() {
			if (typeof this.oncomplete === "function") {
				try {
					this.oncomplete({ target: this });
				} catch (_) {}
			}
		}
	}

	class FakeDatabase {
		constructor() {
			this._stores = {};
			this.onerror = null;
			this.onversionchange = null;
			this.objectStoreNames = { contains: (name) => !!this._stores[name] };
		}

		createObjectStore(name) {
			this._stores[name] = new FakeObjectStore(name);
			return this._stores[name];
		}

		deleteObjectStore(name) {
			delete this._stores[name];
		}

		transaction(names) {
			const requested = Array.isArray(names) ? names : [names];
			const transaction = new FakeTransaction(this._stores);
			for (const name of requested) {
				if (!this._stores[name]) {
					this._stores[name] = new FakeObjectStore(name);
				}
			}
			setTimeout(() => transaction._complete(), 0);
			return transaction;
		}

		close() {}
	}

	globalThis.indexedDB = {
		open() {
			const request = new FakeRequest();
			const database = new FakeDatabase();
			request.result = database;
			setTimeout(() => {
				const transaction = new FakeTransaction(database._stores);
				request.transaction = transaction;
				request._fire(request.onupgradeneeded, { result: database, transaction });
				request._fire(request.onsuccess, { result: database });
				transaction._complete();
			}, 0);
			return request;
		},
		deleteDatabase() {
			const request = new FakeRequest();
			setTimeout(() => request._fire(request.onsuccess), 0);
			return request;
		},
		databases: async () => [],
		cmp: () => 0,
	};
	globalThis.IDBKeyRange = { only: (value) => value, bound: () => null };
	debug("installed in-memory indexedDB stub");
})();

module.exports = { ENGINE_DIR, VOICE_DIR, VOICES_JSON, PTHREAD_ENTRY, debug, resolveLocalUrl, safeJoin };

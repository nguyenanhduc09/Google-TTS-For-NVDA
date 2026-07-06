(function () {
	"use strict";

	let currentSessionId = null;
	let currentOutputGain = 1;
	let lastChunkAt = 0;
	let stopped = false;
	let initPromise = null;
	const firstAudioPacketSamples = 24;
	const steadyAudioPacketSamples = 240;
	const synthesisIdlePollMs = 5;
	const synthesisFallbackIdleMs = 250;
	const workletEmptyDelayMs = 40;
	let emittedAudioPackets = 0;
	let pendingAudioBuffers = [];
	let pendingAudioSampleCount = 0;
	let sawSynthesisEnd = false;
	let currentEndResolver = null;
	const messageListeners = [];

	function emit(message) {
		if (!message || !currentSessionId) {
			return;
		}
		message.sessionId = currentSessionId;
		window.googleTtsForNvdaBridge(JSON.stringify(message));
	}

	function dispatchChromeMessage(message, callback) {
		const run = async () => {
			let response = { result: "stubbed" };
			if (message && message.type === "offscreenTtsEventResponse") {
				handleTtsEngineEvent(message.event);
				response = { result: "handled" };
				if (callback) {
					callback(response);
				}
				return response;
			}
			for (const listener of messageListeners) {
				let listenerResponse;
				const maybePromise = listener(message, { id: "google-tts-for-nvda" }, (value) => {
					listenerResponse = value;
				});
				if (maybePromise && typeof maybePromise.then === "function") {
					listenerResponse = await maybePromise;
				}
				if (listenerResponse !== undefined) {
					response = listenerResponse;
				}
			}
			if (callback) {
				callback(response);
			}
			return response;
		};
		return run();
	}

	const chromeApi = {};
	chromeApi.runtime = {
		onMessage: {
			addListener(listener) {
				messageListeners.push(listener);
			},
		},
		sendMessage(...args) {
			const message = typeof args[0] === "string" ? args[1] : args[0];
			const callback = args.find((arg) => typeof arg === "function");
			return dispatchChromeMessage(message, callback);
		},
		getURL(path) {
			return `/${path.replace(/^\/+/, "")}`;
		},
		getPlatformInfo() {
			return Promise.resolve({ os: "win", arch: "x86-64", nacl_arch: "x86-64" });
		},
		onInstalled: { addListener() {} },
		onStartup: { addListener() {} },
	};
	chromeApi.storage = {
		local: {
			_store: {},
			async get(key) {
				if (typeof key === "string") {
					return { [key]: this._store[key] };
				}
				return { ...this._store };
			},
			async set(values) {
				Object.assign(this._store, values);
			},
		},
	};
	chromeApi.ttsEngine = {
		LanguageInstallStatus: {
			INSTALLED: "installed",
			NOT_INSTALLED: "notInstalled",
			INSTALLING: "installing",
		},
		TtsClientSource: { CHROMEFEATURE: "chrome_feature" },
		updateLanguage() {},
		updateVoices() {},
		onSpeak: { addListener() {} },
		onStop: { addListener() {} },
		onPause: { addListener() {} },
		onResume: { addListener() {} },
		onInstallLanguageRequest: { addListener() {} },
		onLanguageStatusRequest: { addListener() {} },
		onUninstallLanguageRequest: { addListener() {} },
	};
	chromeApi.offscreen = {
		Reason: { AUDIO_PLAYBACK: "AUDIO_PLAYBACK", USER_MEDIA: "USER_MEDIA" },
		async hasDocument() { return true; },
		async createDocument() {},
		async closeDocument() {},
	};
	window.chrome = chromeApi;

	class FakeAudioContext {
		constructor(options) {
			this.sampleRate = options && options.sampleRate ? options.sampleRate : 24000;
			this.destination = {};
			this.audioWorklet = {
				addModule: async () => undefined,
			};
		}

		createGain() {
			return {
				gain: { value: 1 },
				connect() {},
			};
		}

		async resume() {}
		async suspend() {}
	}

	function outputGainFromPayload(payload) {
		const gain = Number(payload && payload.outputGain);
		if (!Number.isFinite(gain)) {
			return 1;
		}
		return Math.max(0, Math.min(2, gain));
	}

	function buffersToPcmBase64(buffers, sampleCount) {
		const bytes = new Uint8Array(sampleCount * 2);
		const view = new DataView(bytes.buffer);
		let outputIndex = 0;
		for (const buffer of buffers) {
			for (let inputIndex = 0; inputIndex < buffer.length; inputIndex++) {
				const sample = Math.max(-1, Math.min(1, buffer[inputIndex] * currentOutputGain));
				view.setInt16(outputIndex * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
				outputIndex++;
			}
		}
		let binary = "";
		const chunkSize = 0x8000;
		for (let index = 0; index < bytes.length; index += chunkSize) {
			binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
		}
		return btoa(binary);
	}

	function resetAudioQueue() {
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets = 0;
	}

	function handleTtsEngineEvent(event) {
		if (!event || !currentSessionId) {
			return;
		}
		if (event.type === "word") {
			emit({ type: "mark", charIndex: Math.max(0, Number(event.charIndex) || 0) });
			return;
		}
		if (event.type === "end") {
			sawSynthesisEnd = true;
			if (currentEndResolver) {
				currentEndResolver();
			}
			return;
		}
		if (event.type === "error") {
			emit({ type: "error", message: "Chrome TTS synthesis failed." });
		}
	}

	function scheduleWorkletEmpty(port) {
		if (!port) {
			return;
		}
		if (port._emptyTimer) {
			clearTimeout(port._emptyTimer);
		}
		port._emptyTimer = setTimeout(() => {
			port._emptyTimer = null;
			if (!stopped && typeof port.onmessage === "function") {
				port.onmessage({ data: { type: "empty" } });
			}
		}, workletEmptyDelayMs);
	}

	function flushAudioQueue() {
		if (!pendingAudioSampleCount || stopped) {
			resetAudioQueue();
			return;
		}
		emit({
			type: "audio",
			sampleRate: 24000,
			data: buffersToPcmBase64(pendingAudioBuffers, pendingAudioSampleCount),
		});
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets++;
	}

	function queueAudio(samples) {
		pendingAudioBuffers.push(samples.slice());
		pendingAudioSampleCount += samples.length;
		const packetSamples = emittedAudioPackets === 0 ? firstAudioPacketSamples : steadyAudioPacketSamples;
		if (pendingAudioSampleCount >= packetSamples) {
			flushAudioQueue();
		}
	}

	class FakeAudioWorkletNode {
		constructor() {
			this.port = {
				onmessage: null,
				postMessage(message) {
					if (!message || stopped) {
						return;
					}
					if (message.command === "clearBuffers") {
						resetAudioQueue();
						if (this._emptyTimer) {
							clearTimeout(this._emptyTimer);
							this._emptyTimer = null;
						}
						return;
					}
					if (message.command !== "addBuffer" || !message.buffer) {
						return;
					}
					const samples = message.buffer instanceof Float32Array
						? message.buffer
						: new Float32Array(message.buffer);
					lastChunkAt = performance.now();
					queueAudio(samples);
					scheduleWorkletEmpty(this);
				},
			};
		}

		connect() {}
		disconnect() {}
	}

	window.AudioContext = FakeAudioContext;
	window.webkitAudioContext = FakeAudioContext;
	window.AudioWorkletNode = FakeAudioWorkletNode;

	async function waitForSynthesisComplete(timeoutMs) {
		const startedAt = performance.now();
		while (performance.now() - startedAt < timeoutMs) {
			if (stopped || sawSynthesisEnd) {
				return;
			}
			await new Promise((resolve) => {
				currentEndResolver = resolve;
				setTimeout(resolve, synthesisIdlePollMs);
			});
			currentEndResolver = null;
			if (lastChunkAt > 0 && performance.now() - lastChunkAt >= synthesisFallbackIdleMs) {
				return;
			}
		}
		throw new Error("Timed out waiting for Chrome TTS audio.");
	}

	function getTtsEngine() {
		if (window.Vh && typeof window.Vh.onSpeak === "function") {
			return window.Vh;
		}
		if (window.Uh && typeof window.Uh.onSpeak === "function") {
			return window.Uh;
		}
		for (const key of Object.getOwnPropertyNames(window)) {
			try {
				const val = window[key];
				if (val && typeof val === "object" && typeof val.onSpeak === "function" && typeof val.init === "function" && typeof val.onStop === "function") {
					return val;
				}
			} catch (_) {}
		}
		return null;
	}

	const readyLanguages = new Set();

	async function ensureLanguageReady(engine, lang) {
		if (!lang || readyLanguages.has(lang)) {
			return;
		}
		if (typeof engine.onInstallLanguageRequest === "function") {
			try {
				await engine.onInstallLanguageRequest(lang);
				readyLanguages.add(lang);
			} catch (error) {
				console.warn("onInstallLanguageRequest failed for", lang, error);
			}
		}
	}

	async function ensureEngineInitialized() {
		const engine = getTtsEngine();
		if (!engine) {
			throw new Error("Chrome WASM TTS engine was not loaded.");
		}
		if (!initPromise) {
			initPromise = engine.init("google-tts-for-nvda").catch((error) => {
				initPromise = null;
				throw error;
			});
		}
		await initPromise;
	}

	async function stopActiveSynthesis() {
		stopped = true;
		if (currentEndResolver) {
			currentEndResolver();
			currentEndResolver = null;
		}
		resetAudioQueue();
		const engine = getTtsEngine();
		if (engine && typeof engine.onStop === "function") {
			await engine.onStop();
		}
	}

	window.googleTtsForNvdaStop = async function googleTtsForNvdaStop() {
		const sessionId = currentSessionId;
		await stopActiveSynthesis();
		if (currentSessionId === sessionId) {
			currentSessionId = null;
		}
	};

	window.googleTtsForNvdaPreload = async function googleTtsForNvdaPreload(payload) {
		currentSessionId = payload.sessionId;
		currentOutputGain = 0;
		lastChunkAt = 0;
		stopped = false;
		sawSynthesisEnd = false;
		resetAudioQueue();
		await ensureEngineInitialized();
		const engine = getTtsEngine();
		if (!engine) {
			throw new Error("Chrome WASM TTS engine was not loaded.");
		}
		if (!readyLanguages.has(payload.lang)) {
			await ensureLanguageReady(engine, payload.lang);
		}
		await engine.onSpeak("", {
			voiceName: payload.voiceName,
			lang: payload.lang,
			rate: 1,
			pitch: 1,
			volume: 0,
		});
		if (currentSessionId === payload.sessionId) {
			currentSessionId = null;
		}
		return { success: true, preloaded: true };
	};

	window.googleTtsForNvdaSpeak = async function googleTtsForNvdaSpeak(payload) {
		try {
			if (currentSessionId) {
				await stopActiveSynthesis();
			}
			await ensureEngineInitialized();
			const engine = getTtsEngine();
			if (!engine) {
				throw new Error("Chrome WASM TTS engine was not loaded.");
			}
			if (!readyLanguages.has(payload.lang)) {
				await ensureLanguageReady(engine, payload.lang);
			}
			const sessionId = payload.sessionId;
			currentSessionId = sessionId;
			currentOutputGain = outputGainFromPayload(payload);
			lastChunkAt = 0;
			stopped = false;
			sawSynthesisEnd = false;
			resetAudioQueue();
			emit({ type: "started" });
			await engine.onSpeak(payload.text, {
				voiceName: payload.voiceName,
				lang: payload.lang,
				rate: payload.rate,
				pitch: payload.pitch,
				volume: payload.volume,
			});
			await waitForSynthesisComplete(120000);
			flushAudioQueue();
			emit({ type: "done" });
			await stopActiveSynthesis();
			if (currentSessionId === sessionId) {
				currentSessionId = null;
			}
			return { success: true };
		} catch (error) {
			emit({ type: "error", message: error && error.message ? error.message : String(error) });
			if (currentSessionId === payload.sessionId) {
				currentSessionId = null;
			}
			throw error;
		}
	};
})();


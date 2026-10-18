(function () {
	"use strict";

	let currentSessionId = null;
	let currentSessionToken = 0;
	let currentMarkOffset = 0;
	let currentOutputGain = 1;
	let currentTempoRate = 1;
	let currentPostPitchFactor = 1;
	let lastChunkAt = 0;
	let stopped = false;
	let initPromise = null;
	let suppressBridgeAudio = false;
	// First packets protect startup continuity; later packets reduce CDP/base64 overhead.
	const firstAudioPacketSamples = 120;
	const earlyAudioPacketSamples = 1200;
	const steadyAudioPacketSamples = 2400;
	const longStreamAudioPacketSamples = 3600;
	const earlyAudioPacketCount = 3;
	const steadyAudioPacketCount = 8;
	const softLimiterKnee = 0.82;
	const softLimiterCeiling = 0.94;
	const synthesisIdlePollMs = 2;
	const synthesisGeneratingEmptyDelayMs = 500;
	// How long the engine's audio pump may go quiet before the utterance is treated as
	// finished. This is the ONLY completion criterion for an utterance that produced any
	// audio at all: endHasSettled below is gated on lastChunkAt <= 0, i.e. no audio.
	// The engine delivers 128-sample blocks (~5.3 ms each) from a chained setTimeout, so
	// a stall longer than this -- TFLite inference on a date or number, GC, or event-loop
	// contention -- is misread as "drained", the tail is discarded and the utterance is
	// still reported as done. 80 ms suits the Chromium renderer, which gets its own
	// prioritised process; hosts that share one event loop with the WASM pthread pool can
	// override it by setting this global before the harness loads.
	const synthesisFinishedIdleMs = Number(globalThis.googleTtsForNvdaDrainIdleMs) > 0
		? Number(globalThis.googleTtsForNvdaDrainIdleMs)
		: 80;
	const tempoFrameSamples = 720;
	const tempoOverlapSamples = 180;
	const tempoSynthesisHopSamples = tempoFrameSamples - tempoOverlapSamples;
	const tempoSearchSamples = 120;
	const tempoSearchStep = 6;
	const boundaryHoldSamples = 3600;
	let emittedAudioPackets = 0;
	let pendingAudioBuffers = [];
	let pendingAudioSampleCount = 0;
	let pitchInputBuffer = new Float32Array(0);
	let pitchReadOffset = 0;
	let tempoInputBuffer = new Float32Array(0);
	let tempoReadOffset = 0;
	let tempoOverlapTail = new Float32Array(0);
	let tempoStarted = false;
	let heldBoundarySamples = new Float32Array(0);
	let smoothSegmentBoundaries = false;
	let sawSynthesisEnd = false;
	let synthesisEndAt = 0;
	let synthesisErrorMessage = "";
	let synthesisGenerating = false;
	let currentAudioPort = null;
	let currentEndResolver = null;
	const messageListeners = [];

	function beginSession(sessionId, suppressAudio) {
		currentSessionToken++;
		currentSessionId = sessionId;
		suppressBridgeAudio = suppressAudio;
		return currentSessionToken;
	}

	function isCurrentSession(sessionToken) {
		return sessionToken === currentSessionToken;
	}

	function emit(message, sessionToken = currentSessionToken) {
		if (!message || !currentSessionId || suppressBridgeAudio || !isCurrentSession(sessionToken)) {
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
		return Math.max(0, Math.min(1.70, gain));
	}

	function tempoRateFromPayload(payload) {
		const rate = Number(payload && payload.artificialRate);
		const artificialRate = Number.isFinite(rate) ? Math.max(0.5, Math.min(2.2, rate)) : 1;
		const postPitchFactor = Math.max(0.35, Math.min(2.5, currentPostPitchFactor || 1));
		return Math.max(0.35, Math.min(5.5, artificialRate / postPitchFactor));
	}

	function postPitchFactorFromPayload(payload) {
		const pitchFactor = Number(payload && payload.postPitch);
		if (!Number.isFinite(pitchFactor)) {
			return 1;
		}
		return Math.max(0.35, Math.min(2.5, pitchFactor));
	}

	function limitSample(sample) {
		const sign = sample < 0 ? -1 : 1;
		const magnitude = Math.abs(sample);
		if (magnitude <= softLimiterKnee) {
			return sample;
		}
		const kneeRange = 1 - softLimiterKnee;
		const shaped = softLimiterKnee + (softLimiterCeiling - softLimiterKnee) * Math.tanh((magnitude - softLimiterKnee) / kneeRange);
		return sign * Math.min(softLimiterCeiling, shaped);
	}

	const base64Chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

	function fastUint8ToBase64(uint8Array) {
		const len = uint8Array.length;
		const extraBytes = len % 3;
		let output = "";
		const parts = [];

		for (let index = 0, len2 = len - extraBytes; index < len2; index += 3) {
			const triplet = (uint8Array[index] << 16) + (uint8Array[index + 1] << 8) + uint8Array[index + 2];
			parts.push(
				base64Chars.charAt((triplet >> 18) & 0x3f) +
					base64Chars.charAt((triplet >> 12) & 0x3f) +
					base64Chars.charAt((triplet >> 6) & 0x3f) +
					base64Chars.charAt(triplet & 0x3f)
			);
			if (parts.length >= 1024) {
				output += parts.join("");
				parts.length = 0;
			}
		}
		if (parts.length > 0) {
			output += parts.join("");
		}

		if (extraBytes === 1) {
			const val = uint8Array[len - 1];
			output += base64Chars.charAt(val >> 2) + base64Chars.charAt((val << 4) & 0x3f) + "==";
		} else if (extraBytes === 2) {
			const val = (uint8Array[len - 2] << 8) + uint8Array[len - 1];
			output +=
				base64Chars.charAt(val >> 10) +
				base64Chars.charAt((val >> 4) & 0x3f) +
				base64Chars.charAt((val << 2) & 0x3f) +
				"=";
		}
		return output;
	}

	function buffersToPcmBase64(buffers, sampleCount) {
		const bytes = new Uint8Array(sampleCount * 2);
		const pcm = new Int16Array(bytes.buffer);
		let outputIndex = 0;
		for (const buffer of buffers) {
			for (let inputIndex = 0; inputIndex < buffer.length; inputIndex++) {
				// A fixed gain avoids the audible pumping caused by adaptive gain and limiter release envelopes.
				const sample = limitSample(buffer[inputIndex] * currentOutputGain);
				pcm[outputIndex++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
			}
		}
		return fastUint8ToBase64(bytes);
	}

	function resetAudioQueue() {
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets = 0;
		resetPitchProcessor();
		resetTempoProcessor();
		heldBoundarySamples = new Float32Array(0);
	}

	function resetPitchProcessor() {
		pitchInputBuffer = new Float32Array(0);
		pitchReadOffset = 0;
	}

	function resetTempoProcessor() {
		tempoInputBuffer = new Float32Array(0);
		tempoReadOffset = 0;
		tempoOverlapTail = new Float32Array(0);
		tempoStarted = false;
	}

	function appendTempoOutput(outputParts, samples) {
		if (samples && samples.length) {
			outputParts.push(samples.slice());
		}
	}

	function combineSampleParts(parts) {
		if (!parts.length) {
			return new Float32Array(0);
		}
		if (parts.length === 1) {
			return parts[0];
		}
		let totalLength = 0;
		for (const part of parts) {
			totalLength += part.length;
		}
		const combined = new Float32Array(totalLength);
		let offset = 0;
		for (const part of parts) {
			combined.set(part, offset);
			offset += part.length;
		}
		return combined;
	}

	function bestTempoFrameOffset(nominalOffset) {
		if (!tempoStarted || !tempoOverlapTail.length) {
			return nominalOffset;
		}
		const start = Math.max(0, nominalOffset - tempoSearchSamples);
		const end = Math.min(tempoInputBuffer.length - tempoFrameSamples, nominalOffset + tempoSearchSamples);
		let bestOffset = Math.max(0, Math.min(nominalOffset, tempoInputBuffer.length - tempoFrameSamples));
		let bestScore = Number.POSITIVE_INFINITY;
		for (let offset = start; offset <= end; offset += tempoSearchStep) {
			let score = 0;
			for (let i = 0; i < tempoOverlapSamples; i += 3) {
				const diff = tempoOverlapTail[i] - tempoInputBuffer[offset + i];
				score += diff * diff;
			}
			if (score < bestScore) {
				bestScore = score;
				bestOffset = offset;
			}
		}
		return bestOffset;
	}

	function tempoFrameOutput(frame) {
		const parts = [];
		if (!tempoStarted) {
			appendTempoOutput(parts, frame.subarray(0, tempoFrameSamples - tempoOverlapSamples));
			tempoOverlapTail = frame.slice(tempoFrameSamples - tempoOverlapSamples);
			tempoStarted = true;
			return combineSampleParts(parts);
		}
		const overlap = new Float32Array(tempoOverlapSamples);
		for (let i = 0; i < tempoOverlapSamples; i++) {
			const weight = (i + 1) / (tempoOverlapSamples + 1);
			overlap[i] = tempoOverlapTail[i] * (1 - weight) + frame[i] * weight;
		}
		appendTempoOutput(parts, overlap);
		appendTempoOutput(parts, frame.subarray(tempoOverlapSamples, tempoFrameSamples - tempoOverlapSamples));
		tempoOverlapTail = frame.slice(tempoFrameSamples - tempoOverlapSamples);
		return combineSampleParts(parts);
	}

	function processPitchSamples(samples, final = false) {
		if (Math.abs(currentPostPitchFactor - 1) < 0.001) {
			resetPitchProcessor();
			return samples;
		}
		if (samples.length) {
			pitchInputBuffer = appendSamples(pitchInputBuffer, samples);
		}
		const outputParts = [];
		if (pitchInputBuffer.length > 1) {
			const outputLength = Math.max(
				0,
				Math.floor((pitchInputBuffer.length - 1 - pitchReadOffset) / currentPostPitchFactor)
			);
			if (outputLength > 0) {
				const output = new Float32Array(outputLength);
				for (let index = 0; index < outputLength; index++) {
					const inputIndex = Math.floor(pitchReadOffset);
					const fraction = pitchReadOffset - inputIndex;
					const current = pitchInputBuffer[inputIndex];
					const next = pitchInputBuffer[Math.min(inputIndex + 1, pitchInputBuffer.length - 1)];
					output[index] = current + (next - current) * fraction;
					pitchReadOffset += currentPostPitchFactor;
				}
				appendTempoOutput(outputParts, output);
			}
			const discard = Math.max(0, Math.floor(pitchReadOffset) - 1);
			if (discard > 0) {
				pitchInputBuffer = pitchInputBuffer.slice(discard);
				pitchReadOffset -= discard;
			}
		}
		if (final) {
			if (pitchInputBuffer.length) {
				const remainingOffset = Math.min(Math.floor(pitchReadOffset), pitchInputBuffer.length - 1);
				appendTempoOutput(outputParts, pitchInputBuffer.subarray(remainingOffset));
			}
			resetPitchProcessor();
		}
		return combineSampleParts(outputParts);
	}

	function processTempoSamples(samples, final = false) {
		if (Math.abs(currentTempoRate - 1) < 0.001) {
			resetTempoProcessor();
			return samples;
		}
		if (samples.length) {
			tempoInputBuffer = appendSamples(tempoInputBuffer, samples);
		}
		const outputParts = [];
		const analysisHop = Math.max(1, Math.round(tempoSynthesisHopSamples * currentTempoRate));
		while (tempoReadOffset + tempoFrameSamples <= tempoInputBuffer.length) {
			const frameOffset = bestTempoFrameOffset(tempoReadOffset);
			const frame = tempoInputBuffer.subarray(frameOffset, frameOffset + tempoFrameSamples);
			appendTempoOutput(outputParts, tempoFrameOutput(frame));
			tempoReadOffset = frameOffset + analysisHop;
			if (tempoReadOffset > tempoSearchSamples) {
				const discard = tempoReadOffset - tempoSearchSamples;
				tempoInputBuffer = tempoInputBuffer.slice(discard);
				tempoReadOffset -= discard;
			}
		}
		if (final) {
			if (tempoStarted && tempoOverlapTail.length) {
				appendTempoOutput(outputParts, tempoOverlapTail);
			}
			const remainingOffset = Math.min(tempoReadOffset, tempoInputBuffer.length);
			appendTempoOutput(outputParts, tempoInputBuffer.subarray(remainingOffset));
			resetTempoProcessor();
		}
		return combineSampleParts(outputParts);
	}

	function queueTempoInput(samples, sessionToken = currentSessionToken) {
		const tempoSamples = processTempoSamples(samples);
		if (tempoSamples.length) {
			queueProcessedAudio(tempoSamples, sessionToken);
		}
	}

	function flushAudioProcessors(sessionToken = currentSessionToken) {
		const pitchSamples = processPitchSamples(new Float32Array(0), true);
		if (pitchSamples.length) {
			queueTempoInput(pitchSamples, sessionToken);
		}
		flushTempoProcessor(sessionToken);
	}

	function flushTempoProcessor(sessionToken = currentSessionToken) {
		if (Math.abs(currentTempoRate - 1) < 0.001) {
			resetTempoProcessor();
			return;
		}
		const output = processTempoSamples(new Float32Array(0), true);
		if (output.length) {
			queueProcessedAudio(output, sessionToken);
		}
	}

	function appendSamples(first, second) {
		if (!first.length) {
			return second;
		}
		if (!second.length) {
			return first;
		}
		const joined = new Float32Array(first.length + second.length);
		joined.set(first, 0);
		joined.set(second, first.length);
		return joined;
	}

	function audioPacketSampleTarget() {
		if (emittedAudioPackets === 0) {
			return firstAudioPacketSamples;
		}
		if (emittedAudioPackets < earlyAudioPacketCount) {
			return earlyAudioPacketSamples;
		}
		if (emittedAudioPackets < steadyAudioPacketCount) {
			return steadyAudioPacketSamples;
		}
		return longStreamAudioPacketSamples;
	}

	function queueAudioPacket(samples, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			return;
		}
		if (!samples.length) {
			return;
		}
		pendingAudioBuffers.push(samples);
		pendingAudioSampleCount += samples.length;
		const packetSamples = audioPacketSampleTarget();
		if (pendingAudioSampleCount >= packetSamples) {
			flushAudioQueue(sessionToken);
		}
	}

	function handleTtsEngineEvent(event) {
		if (!event || !currentSessionId) {
			return;
		}
		if (event.type === "word") {
			emit({ type: "mark", charIndex: currentMarkOffset + Math.max(0, Number(event.charIndex) || 0) });
			return;
		}
		if (event.type === "end") {
			sawSynthesisEnd = true;
			synthesisEndAt = performance.now();
			if (currentEndResolver) {
				currentEndResolver();
			}
			return;
		}
		if (event.type === "error") {
			synthesisErrorMessage = event.message || "Browser speech synthesis failed.";
			if (currentEndResolver) {
				currentEndResolver();
			}
		}
	}

	function scheduleWorkletEmpty(port, sessionToken = currentSessionToken) {
		if (!port) {
			return;
		}
		if (port._emptyTimer) {
			clearTimeout(port._emptyTimer);
		}
		port._emptyTimer = setTimeout(() => {
			port._emptyTimer = null;
			if (!stopped && isCurrentSession(sessionToken) && typeof port.onmessage === "function") {
				port.onmessage({ data: { type: "empty" } });
			}
		}, synthesisGenerating ? synthesisGeneratingEmptyDelayMs : synthesisFinishedIdleMs);
	}

	function flushAudioQueue(sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			resetAudioQueue();
			return;
		}
		if (stopped) {
			resetAudioQueue();
			return;
		}
		if (!pendingAudioSampleCount) {
			return;
		}
		emit({
			type: "audio",
			sampleRate: 24000,
			data: buffersToPcmBase64(pendingAudioBuffers, pendingAudioSampleCount),
		}, sessionToken);
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets++;
	}

	function queueProcessedAudio(samples, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			return;
		}
		if (!smoothSegmentBoundaries) {
			queueAudioPacket(samples, sessionToken);
			return;
		}
		const joinedSamples = appendSamples(heldBoundarySamples, samples);
		if (joinedSamples.length <= boundaryHoldSamples) {
			heldBoundarySamples = joinedSamples;
			return;
		}
		const emitCount = joinedSamples.length - boundaryHoldSamples;
		queueAudioPacket(joinedSamples.subarray(0, emitCount), sessionToken);
		heldBoundarySamples = joinedSamples.slice(emitCount);
	}

	function queueAudio(samples, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			return;
		}
		const pitchSamples = processPitchSamples(samples);
		if (pitchSamples.length) {
			queueTempoInput(pitchSamples, sessionToken);
		}
	}

	function finishSegmentAudio(hasNextSegment, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			resetAudioQueue();
			return;
		}
		if (!smoothSegmentBoundaries) {
			return;
		}
		if (!hasNextSegment) {
			flushAudioProcessors(sessionToken);
		}
		let samples = heldBoundarySamples;
		heldBoundarySamples = new Float32Array(0);
		queueAudioPacket(samples, sessionToken);
		if (hasNextSegment) {
			flushAudioQueue(sessionToken);
		}
	}

	class FakeAudioWorkletNode {
		constructor() {
			const sessionToken = currentSessionToken;
			this.port = {
				_sessionToken: sessionToken,
				onmessage: null,
				postMessage(message) {
					if (!message || stopped) {
						return;
					}
					if (synthesisGenerating) {
						this._sessionToken = currentSessionToken;
					}
					if (!isCurrentSession(this._sessionToken)) {
						return;
					}
					if (message.command === "clearBuffers") {
						// The engine sends clearBuffers from its normal end path as well as from
						// cancellation. Session cancellation already resets the bridge queue in
						// stopActiveSynthesis(); clearing it here would discard the boundary hold
						// and DSP tail before finishSegmentAudio() can emit the final word.
						if (this._emptyTimer) {
							clearTimeout(this._emptyTimer);
							this._emptyTimer = null;
						}
						if (currentAudioPort === this) {
							currentAudioPort = null;
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
					currentAudioPort = this;
					queueAudio(samples, this._sessionToken);
					scheduleWorkletEmpty(this, this._sessionToken);
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
			if (synthesisErrorMessage) {
				throw new Error(synthesisErrorMessage);
			}
			if (stopped) {
				return;
			}
			const now = performance.now();
			const audioHasDrained = lastChunkAt > 0 && now - lastChunkAt >= synthesisFinishedIdleMs;
			const endHasSettled = sawSynthesisEnd && synthesisEndAt > 0 && now - synthesisEndAt >= synthesisFinishedIdleMs;
			if (audioHasDrained || (endHasSettled && lastChunkAt <= 0)) {
				return;
			}
			await new Promise((resolve) => {
				currentEndResolver = resolve;
				setTimeout(resolve, synthesisIdlePollMs);
			});
			currentEndResolver = null;
		}
		throw new Error("Timed out waiting for browser speech audio.");
	}

	async function waitForWasmEnd(timeoutMs) {
		const startedAt = performance.now();
		while (performance.now() - startedAt < timeoutMs) {
			if (synthesisErrorMessage) {
				throw new Error(synthesisErrorMessage);
			}
			if (stopped) {
				return;
			}
			if (sawSynthesisEnd) {
				return;
			}
			await new Promise((resolve) => {
				currentEndResolver = resolve;
				setTimeout(resolve, synthesisIdlePollMs);
			});
			currentEndResolver = null;
		}
		throw new Error("Timed out waiting for WASM engine synthesis to complete.");
	}

	function isTtsEngineInstance(val) {
		if (!val || typeof val !== "object"
			|| typeof val.onSpeak !== "function"
			|| typeof val.init !== "function"
			|| typeof val.onStop !== "function") {
			return false;
		}
		for (const key of Object.getOwnPropertyNames(val)) {
			try {
				const prop = val[key];
				if (prop && (prop.audioWorklet || prop instanceof FakeAudioContext)) {
					return true;
				}
			} catch (_) {}
		}
		return false;
	}

	function getTtsEngine() {
		// Engine globals are minified and have changed between bundled engine versions.
		for (const key of ["Xh", "Vh", "Uh"]) {
			if (isTtsEngineInstance(window[key])) {
				return window[key];
			}
		}
		for (const key of Object.getOwnPropertyNames(window)) {
			try {
				const val = window[key];
				if (isTtsEngineInstance(val)) {
					return val;
				}
			} catch (_) {}
		}
		return null;
	}

	const readyLanguages = new Set();
	const readyVoices = new Set();

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
			throw new Error("WASM TTS engine was not loaded.");
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
		currentSessionToken++;
		stopped = true;
		synthesisGenerating = false;
		synthesisErrorMessage = "";
		smoothSegmentBoundaries = false;
		if (currentEndResolver) {
			currentEndResolver();
			currentEndResolver = null;
		}
		resetAudioQueue();
		const engine = getTtsEngine();
		if (engine && typeof engine.onStop === "function") {
			try {
				await engine.onStop();
			} catch (error) {
				console.debug("Ignored engine stop failure during cancellation:", error);
			}
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
		const sessionToken = beginSession(payload.sessionId, true);
		currentOutputGain = 0;
		try {
			lastChunkAt = 0;
			stopped = false;
			sawSynthesisEnd = false;
			synthesisEndAt = 0;
			synthesisErrorMessage = "";
			synthesisGenerating = false;
			resetAudioQueue();
			currentTempoRate = 1;
			currentPostPitchFactor = 1;
			smoothSegmentBoundaries = false;
			await ensureEngineInitialized();
			const engine = getTtsEngine();
			if (!engine) {
				throw new Error("WASM TTS engine was not loaded.");
			}
			if (!readyLanguages.has(payload.lang)) {
				await ensureLanguageReady(engine, payload.lang);
			}
			if (readyVoices.has(payload.voiceName)) {
				return { success: true, preloaded: true, cached: true };
			}
			synthesisGenerating = true;
			try {
				await engine.onSpeak(payload.text || " ", {
					voiceName: payload.voiceName,
					lang: payload.lang,
					rate: 1,
					pitch: 1,
					volume: 0,
				});
			} finally {
				synthesisGenerating = false;
			}
			if (synthesisErrorMessage) {
				throw new Error(synthesisErrorMessage);
			}
			if (!isCurrentSession(sessionToken)) {
				return { success: false, preloaded: false, cancelled: true };
			}
			if (lastChunkAt > 0) {
				scheduleWorkletEmpty(currentAudioPort, sessionToken);
			}
			readyVoices.add(payload.voiceName);
			return { success: true, preloaded: true };
		} finally {
			if (currentSessionId === payload.sessionId && isCurrentSession(sessionToken)) {
				currentSessionId = null;
				currentSessionToken++;
				suppressBridgeAudio = false;
			}
		}
	};

	window.googleTtsForNvdaReady = function googleTtsForNvdaReady() {
		return getTtsEngine() !== null;
	};

	window.googleTtsForNvdaSpeak = async function googleTtsForNvdaSpeak(payload) {
		try {
			if (currentSessionId) {
				await stopActiveSynthesis();
			}
			await ensureEngineInitialized();
			const engine = getTtsEngine();
			if (!engine) {
				throw new Error("WASM TTS engine was not loaded.");
			}
			if (!readyLanguages.has(payload.lang)) {
				await ensureLanguageReady(engine, payload.lang);
			}
			const sessionId = payload.sessionId;
			const textSegments = Array.isArray(payload.segments) && payload.segments.length
				? payload.segments.filter((segment) => typeof segment === "string" && segment.length)
				: [payload.text];
			const hasPreviousSegment = payload.hasPreviousSegment === true;
			const hasBoundaryContext = textSegments.length > 1 || hasPreviousSegment;
			const sessionToken = beginSession(sessionId, false);
			currentMarkOffset = 0;
			currentOutputGain = outputGainFromPayload(payload);
			lastChunkAt = 0;
			stopped = false;
			sawSynthesisEnd = false;
			synthesisErrorMessage = "";
			synthesisGenerating = false;
			resetAudioQueue();
			currentPostPitchFactor = postPitchFactorFromPayload(payload);
			currentTempoRate = tempoRateFromPayload(payload);
			smoothSegmentBoundaries = hasBoundaryContext;
			emit({ type: "started" }, sessionToken);
			for (let segmentIndex = 0; segmentIndex < textSegments.length; segmentIndex++) {
				if (stopped || !isCurrentSession(sessionToken)) {
					break;
				}
				const textSegment = textSegments[segmentIndex];
				lastChunkAt = 0;
				sawSynthesisEnd = false;
				synthesisEndAt = 0;
				synthesisErrorMessage = "";
				synthesisGenerating = true;
				try {
					await engine.onSpeak(textSegment, {
						voiceName: payload.voiceName,
						lang: payload.lang,
						rate: payload.rate,
						pitch: payload.pitch,
						volume: payload.volume,
					});
				} finally {
					synthesisGenerating = false;
				}
				if (!isCurrentSession(sessionToken)) {
					break;
				}
				if (lastChunkAt > 0) {
					scheduleWorkletEmpty(currentAudioPort, sessionToken);
				}
				
				const hasNextSegment = segmentIndex < textSegments.length - 1;
				
				if (hasNextSegment) {
					// The normal engine end path has already settled its worklet buffers. Avoid
					// adding another queue-idle delay before starting the next hidden segment.
					await waitForWasmEnd(120000);
				} else {
					await waitForSynthesisComplete(120000);
				}
				
				if (!isCurrentSession(sessionToken)) {
					break;
				}
				
				finishSegmentAudio(hasNextSegment, sessionToken);
				if (hasNextSegment) {
					// Each hidden segment is a separate WASM onSpeak call; expose that boundary so
					// Python can shorten the engine's end-of-utterance silence without splitting CDP requests.
					emit({ type: "segmentEnd" }, sessionToken);
				}
				currentMarkOffset += textSegment.length;
			}
			currentMarkOffset = 0;
			readyVoices.add(payload.voiceName);
			flushAudioProcessors(sessionToken);
			flushAudioQueue(sessionToken);
			emit({ type: "done" }, sessionToken);
			await stopActiveSynthesis();
			smoothSegmentBoundaries = false;
			if (currentSessionId === sessionId) {
				currentSessionId = null;
			}
			return { success: true };
		} catch (error) {
			emit({ type: "error", message: error && error.message ? error.message : String(error) });
			try {
				const stoppedCleanly = await Promise.race([
					stopActiveSynthesis().then(() => true),
					new Promise((resolve) => setTimeout(() => resolve(false), 1000)),
				]);
				if (!stoppedCleanly) {
					console.debug("Timed out while stopping engine after speech error.");
				}
			} catch (stopError) {
				console.debug("Ignored engine stop failure after speech error:", stopError);
			}
			if (currentSessionId === payload.sessionId) {
				currentSessionId = null;
			}
			currentMarkOffset = 0;
			smoothSegmentBoundaries = false;
			suppressBridgeAudio = false;
			throw error;
		}
	};
})();

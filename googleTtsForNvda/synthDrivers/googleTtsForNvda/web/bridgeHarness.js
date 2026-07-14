(function () {
	"use strict";

	let currentSessionId = null;
	let currentMarkOffset = 0;
	let currentOutputGain = 1;
	let currentTempoRate = 1;
	let currentAgcGain = 1;
	let currentLimiterGain = 1;
	let lastChunkAt = 0;
	let stopped = false;
	let initPromise = null;
	let suppressBridgeAudio = false;
	const firstAudioPacketSamples = 120;
	const steadyAudioPacketSamples = 1200;
	const agcTargetRms = 0.18;
	const agcSilenceFloor = 0.012;
	const agcMinGain = 0.55;
	const agcMaxGain = 1.7;
	const agcAttackStep = 0.08;
	const agcReleaseStep = 0.006;
	const peakLimiterCeiling = 0.92;
	const peakLimiterReleaseStep = 0.0002;
	const softLimiterKnee = 0.82;
	const softLimiterCeiling = 0.94;
	const synthesisIdlePollMs = 2;
	const synthesisGeneratingEmptyDelayMs = 500;
	const synthesisFinishedIdleMs = 80;
	const tempoFrameSamples = 720;
	const tempoOverlapSamples = 180;
	const tempoSynthesisHopSamples = tempoFrameSamples - tempoOverlapSamples;
	const tempoSearchSamples = 120;
	const tempoSearchStep = 6;
	const boundaryHoldSamples = 3600;
	const boundaryMaxLeadingTrimSamples = 3600;
	const boundarySilenceThreshold = 0.003;
	let emittedAudioPackets = 0;
	let pendingAudioBuffers = [];
	let pendingAudioSampleCount = 0;
	let tempoInputBuffer = new Float32Array(0);
	let tempoReadOffset = 0;
	let tempoOverlapTail = new Float32Array(0);
	let tempoStarted = false;
	let heldBoundarySamples = new Float32Array(0);
	let smoothSegmentBoundaries = false;
	let trimLeadingBoundarySilence = false;
	let leadingBoundaryTrimBudget = 0;
	let sawSynthesisEnd = false;
	let synthesisGenerating = false;
	let currentAudioPort = null;
	let currentEndResolver = null;
	const messageListeners = [];

	function emit(message) {
		if (!message || !currentSessionId || suppressBridgeAudio) {
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

	function tempoRateFromPayload(payload) {
		const rate = Number(payload && payload.artificialRate);
		if (!Number.isFinite(rate)) {
			return 1;
		}
		return Math.max(0.5, Math.min(2.2, rate));
	}

	function updateAgcGain(buffers, sampleCount) {
		if (!sampleCount || !currentOutputGain) {
			return;
		}
		let sumSquares = 0;
		for (const buffer of buffers) {
			for (let inputIndex = 0; inputIndex < buffer.length; inputIndex++) {
				sumSquares += buffer[inputIndex] * buffer[inputIndex];
			}
		}
		const rms = Math.sqrt(sumSquares / sampleCount);
		if (!Number.isFinite(rms) || rms < agcSilenceFloor) {
			return;
		}
		let targetGain = agcTargetRms / (rms * currentOutputGain);
		targetGain = Math.max(agcMinGain, Math.min(agcMaxGain, targetGain));
		if (targetGain < currentAgcGain) {
			currentAgcGain = Math.max(targetGain, currentAgcGain - agcAttackStep);
		} else {
			currentAgcGain = Math.min(targetGain, currentAgcGain + agcReleaseStep);
		}
	}

	function gainForSample(sample) {
		const baseGain = currentOutputGain * currentAgcGain;
		const peak = Math.abs(sample * baseGain);
		const targetLimiterGain = peak > peakLimiterCeiling ? peakLimiterCeiling / peak : 1;
		if (targetLimiterGain < currentLimiterGain) {
			currentLimiterGain = targetLimiterGain;
		} else {
			currentLimiterGain = Math.min(1, currentLimiterGain + peakLimiterReleaseStep);
		}
		return baseGain * currentLimiterGain;
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
		const view = new DataView(bytes.buffer);
		let outputIndex = 0;
		updateAgcGain(buffers, sampleCount);
		for (const buffer of buffers) {
			for (let inputIndex = 0; inputIndex < buffer.length; inputIndex++) {
				const gain = gainForSample(buffer[inputIndex]);
				const sample = limitSample(buffer[inputIndex] * gain);
				view.setInt16(outputIndex * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
				outputIndex++;
			}
		}
		return fastUint8ToBase64(bytes);
	}

	function resetAudioQueue() {
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets = 0;
		resetTempoProcessor();
		heldBoundarySamples = new Float32Array(0);
		trimLeadingBoundarySilence = false;
		leadingBoundaryTrimBudget = 0;
		currentAgcGain = 1;
		currentLimiterGain = 1;
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

	function flushTempoProcessor() {
		if (Math.abs(currentTempoRate - 1) < 0.001) {
			resetTempoProcessor();
			return;
		}
		const output = processTempoSamples(new Float32Array(0), true);
		if (output.length) {
			queueProcessedAudio(output);
		}
	}

	function appendSamples(first, second) {
		if (!first.length) {
			return second.slice();
		}
		if (!second.length) {
			return first.slice();
		}
		const joined = new Float32Array(first.length + second.length);
		joined.set(first, 0);
		joined.set(second, first.length);
		return joined;
	}

	function trimLeadingSilence(samples) {
		if (!trimLeadingBoundarySilence || !samples.length) {
			return samples;
		}
		const limit = Math.min(samples.length, leadingBoundaryTrimBudget);
		let index = 0;
		while (index < limit && Math.abs(samples[index]) < boundarySilenceThreshold) {
			index++;
		}
		leadingBoundaryTrimBudget -= index;
		if (index < samples.length || leadingBoundaryTrimBudget <= 0) {
			trimLeadingBoundarySilence = false;
			leadingBoundaryTrimBudget = 0;
		}
		return samples.subarray(index);
	}

	function trimTrailingSilence(samples) {
		let index = samples.length - 1;
		while (index >= 0 && Math.abs(samples[index]) < boundarySilenceThreshold) {
			index--;
		}
		return index < 0 ? new Float32Array(0) : samples.subarray(0, index + 1);
	}

	function queueAudioPacket(samples) {
		if (!samples.length) {
			return;
		}
		pendingAudioBuffers.push(samples.slice());
		pendingAudioSampleCount += samples.length;
		const packetSamples = emittedAudioPackets === 0 ? firstAudioPacketSamples : steadyAudioPacketSamples;
		if (pendingAudioSampleCount >= packetSamples) {
			flushAudioQueue();
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
			if (currentEndResolver) {
				currentEndResolver();
			}
			return;
		}
		if (event.type === "error") {
			emit({ type: "error", message: "Browser speech synthesis failed." });
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
		}, synthesisGenerating ? synthesisGeneratingEmptyDelayMs : synthesisFinishedIdleMs);
	}

	function flushAudioQueue() {
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
		});
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets++;
	}

	function queueProcessedAudio(samples) {
		if (!smoothSegmentBoundaries) {
			queueAudioPacket(samples);
			return;
		}
		const trimmedSamples = trimLeadingSilence(samples);
		if (!trimmedSamples.length) {
			return;
		}
		const joinedSamples = appendSamples(heldBoundarySamples, trimmedSamples);
		if (joinedSamples.length <= boundaryHoldSamples) {
			heldBoundarySamples = joinedSamples;
			return;
		}
		const emitCount = joinedSamples.length - boundaryHoldSamples;
		queueAudioPacket(joinedSamples.subarray(0, emitCount));
		heldBoundarySamples = joinedSamples.slice(emitCount);
	}

	function queueAudio(samples) {
		const tempoSamples = processTempoSamples(samples);
		if (tempoSamples.length) {
			queueProcessedAudio(tempoSamples);
		}
	}

	function finishSegmentAudio(hasNextSegment) {
		if (!smoothSegmentBoundaries) {
			return;
		}
		if (!hasNextSegment) {
			flushTempoProcessor();
		}
		let samples = heldBoundarySamples;
		heldBoundarySamples = new Float32Array(0);
		if (hasNextSegment) {
			samples = trimTrailingSilence(samples);
		}
		queueAudioPacket(samples);
		if (hasNextSegment) {
			flushAudioQueue();
		}
		if (hasNextSegment) {
			trimLeadingBoundarySilence = true;
			leadingBoundaryTrimBudget = boundaryMaxLeadingTrimSamples;
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
			if (lastChunkAt > 0 && performance.now() - lastChunkAt >= synthesisFinishedIdleMs) {
				return;
			}
		}
		throw new Error("Timed out waiting for browser speech audio.");
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
		stopped = true;
		synthesisGenerating = false;
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
		currentSessionId = payload.sessionId;
		currentOutputGain = 0;
		suppressBridgeAudio = true;
		try {
			lastChunkAt = 0;
			stopped = false;
			sawSynthesisEnd = false;
			synthesisGenerating = false;
			resetAudioQueue();
			currentTempoRate = 1;
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
				await engine.onSpeak(payload.text || "a", {
					voiceName: payload.voiceName,
					lang: payload.lang,
					rate: 1,
					pitch: 1,
					volume: 0,
				});
			} finally {
				synthesisGenerating = false;
			}
			if (lastChunkAt > 0) {
				scheduleWorkletEmpty(currentAudioPort);
			}
			readyVoices.add(payload.voiceName);
			return { success: true, preloaded: true };
		} finally {
			if (currentSessionId === payload.sessionId) {
				currentSessionId = null;
			}
			suppressBridgeAudio = false;
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
			const hasHiddenSegments = textSegments.length > 1;
			currentSessionId = sessionId;
			currentMarkOffset = 0;
			currentOutputGain = outputGainFromPayload(payload);
			suppressBridgeAudio = false;
			lastChunkAt = 0;
			stopped = false;
			sawSynthesisEnd = false;
			synthesisGenerating = false;
			resetAudioQueue();
			currentTempoRate = tempoRateFromPayload(payload);
			smoothSegmentBoundaries = hasHiddenSegments;
			emit({ type: "started" });
			for (let segmentIndex = 0; segmentIndex < textSegments.length; segmentIndex++) {
				if (stopped) {
					break;
				}
				const textSegment = textSegments[segmentIndex];
				lastChunkAt = 0;
				sawSynthesisEnd = false;
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
				if (lastChunkAt > 0) {
					scheduleWorkletEmpty(currentAudioPort);
				}
				await waitForSynthesisComplete(120000);
				finishSegmentAudio(segmentIndex < textSegments.length - 1);
				currentMarkOffset += textSegment.length;
			}
			currentMarkOffset = 0;
			readyVoices.add(payload.voiceName);
			flushTempoProcessor();
			flushAudioQueue();
			emit({ type: "done" });
			await stopActiveSynthesis();
			smoothSegmentBoundaries = false;
			if (currentSessionId === sessionId) {
				currentSessionId = null;
			}
			return { success: true };
		} catch (error) {
			emit({ type: "error", message: error && error.message ? error.message : String(error) });
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

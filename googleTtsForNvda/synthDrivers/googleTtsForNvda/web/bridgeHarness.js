(function () {
	"use strict";

	const nativeSetTimeout = typeof window.setTimeout === "function" ? window.setTimeout : setTimeout;
	const nativeClearTimeout = typeof window.clearTimeout === "function" ? window.clearTimeout : clearTimeout;

	function scheduleNativeTimeout(fn, delay, ...args) {
		return nativeSetTimeout.call(window, fn, delay, ...args);
	}

	function clearNativeTimeout(id) {
		if (id !== null && id !== undefined) {
			nativeClearTimeout.call(window, id);
		}
	}

	function clearPortEmptyTimer(port) {
		if (port && port._emptyTimer) {
			clearNativeTimeout(port._emptyTimer);
			port._emptyTimer = null;
		}
	}

	function installZeroDelayMacrotaskScheduler() {
		if (typeof MessageChannel !== "function") {
			return;
		}
		// Avoid browser nested-timer throttling (4 ms / 15.6 ms ticks) on zero-delay engine read loops.
		const channel = new MessageChannel();
		const pendingQueue = [];
		const activeTimers = new Map();
		let nextTimerId = 1;

		channel.port1.onmessage = () => {
			const id = pendingQueue.shift();
			if (id === undefined) {
				return;
			}
			const entry = activeTimers.get(id);
			if (!entry) {
				return;
			}
			activeTimers.delete(id);
			entry.fn(...entry.args);
		};

		window.setTimeout = function googleTtsSetTimeout(fn, delay, ...args) {
			const numericDelay = delay === undefined || delay === null ? 0 : Number(delay);
			if (typeof fn === "function" && (!Number.isFinite(numericDelay) || numericDelay <= 0)) {
				const id = -(nextTimerId++);
				activeTimers.set(id, { fn, args });
				pendingQueue.push(id);
				channel.port2.postMessage(null);
				return id;
			}
			return nativeSetTimeout.call(window, fn, delay, ...args);
		};

		window.clearTimeout = function googleTtsClearTimeout(id) {
			if (typeof id === "number" && id < 0) {
				if (activeTimers.delete(id) && activeTimers.size === 0) {
					pendingQueue.length = 0;
				}
				return;
			}
			return nativeClearTimeout.call(window, id);
		};
	}

	installZeroDelayMacrotaskScheduler();

	let currentSessionId = null;
	let currentSessionToken = 0;
	let controlSequence = 0;
	let cachedTtsEngine = null;
	let currentMarkOffset = 0;
	let currentOutputGain = 1;
	let currentArtificialRate = 1;
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
	const synthesisFinishedIdleMs = 80;
	const synthesisSettledIdleMs = 15;
	// Span full male/female glottal pitch periods (>= 18 ms) with 50% Hann COLA overlap.
	const tempoFrameSamples = 440;
	const tempoOverlapSamples = 220;
	const tempoSynthesisHopSamples = tempoFrameSamples - tempoOverlapSamples;
	const tempoSearchSamples = 220;
	const tempoSearchStep = 3;
	function buildFadeInWindow(length) {
		const weights = new Float32Array(length);
		for (let i = 0; i < length; i++) {
			weights[i] = 0.5 - 0.5 * Math.cos((Math.PI * (i + 1)) / (length + 1));
		}
		return weights;
	}
	const tempoFadeInWindow = buildFadeInWindow(tempoOverlapSamples);
	function getFadeInWindow(length) {
		return length === tempoOverlapSamples ? tempoFadeInWindow : buildFadeInWindow(length);
	}
	let emittedAudioPackets = 0;
	let pendingAudioBuffers = [];
	let pendingAudioSampleCount = 0;
	let pitchInputBuffer = new Float32Array(0);
	let pitchReadOffset = 0;
	let pitchAaFactor = 1;
	let pitchAaB0 = 1;
	let pitchAaB1 = 0;
	let pitchAaB2 = 0;
	let pitchAaA1 = 0;
	let pitchAaA2 = 0;
	let pitchAaZ1 = 0;
	let pitchAaZ2 = 0;
	let pitchAaInitialized = false;
	let tempoInputBuffer = new Float32Array(0);
	let tempoReadOffset = 0;
	let tempoOverlapTail = new Float32Array(0);
	let tempoStarted = false;
	let tempoLastTailEnd = 0;
	let totalTempoInputSamples = 0;
	let totalTempoEmittedSamples = 0;
	let sawSynthesisEnd = false;
	let synthesisEndAt = 0;
	let synthesisErrorMessage = "";
	let synthesisGenerating = false;
	let sessionActive = false;
	let inFlightStopPromise = null;
	let initializedEngine = null;
	let currentAudioPort = null;
	let currentEndResolver = null;
	let pendingWorkletChunks = [];
	let pendingWorkletPort = null;
	let pendingWorkletSessionToken = 0;
	let workletDrainScheduled = false;
	let currentAudioSampleRate = 24000;
	const messageListeners = [];

	function beginSession(sessionId, suppressAudio) {
		currentSessionToken++;
		sessionActive = true;
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
			return `/${String(path || "").replace(/^\/+/, "")}`;
		},
		getPlatformInfo() {
			return Promise.resolve({ os: "win", arch: "x86-64", nacl_arch: "x86-64" });
		},
		onInstalled: { addListener() {} },
		onStartup: { addListener() {} },
	};
	const localStorageStore = {};
	chromeApi.storage = {
		local: {
			_store: localStorageStore,
			async get(key) {
				if (key === null || key === undefined) {
					return { ...localStorageStore };
				}
				if (typeof key === "string") {
					return Object.prototype.hasOwnProperty.call(localStorageStore, key)
						? { [key]: localStorageStore[key] }
						: {};
				}
				if (Array.isArray(key)) {
					const result = {};
					for (const entryKey of key) {
						if (typeof entryKey === "string" && Object.prototype.hasOwnProperty.call(localStorageStore, entryKey)) {
							result[entryKey] = localStorageStore[entryKey];
						}
					}
					return result;
				}
				if (typeof key === "object") {
					const result = { ...key };
					for (const entryKey of Object.keys(key)) {
						if (Object.prototype.hasOwnProperty.call(localStorageStore, entryKey)) {
							result[entryKey] = localStorageStore[entryKey];
						}
					}
					return result;
				}
				return { ...localStorageStore };
			},
			async set(values) {
				if (values && typeof values === "object") {
					Object.assign(localStorageStore, values);
				}
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
			const requestedRate = options && typeof options.sampleRate !== "boolean" ? Number(options.sampleRate) : NaN;
			this.sampleRate = Number.isFinite(requestedRate) && requestedRate > 0 ? requestedRate : 24000;
			currentAudioSampleRate = this.sampleRate;
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
		async close() {}
	}

	function outputGainFromPayload(payload) {
		if (
			!payload
			|| payload.outputGain === undefined
			|| payload.outputGain === null
			|| payload.outputGain === ""
			|| typeof payload.outputGain === "boolean"
		) {
			return 1;
		}
		const gain = Number(payload.outputGain);
		if (!Number.isFinite(gain)) {
			return 1;
		}
		return Math.max(0, Math.min(1.70, gain));
	}

	function artificialRateFromPayload(payload) {
		const raw = payload ? payload.artificialRate : undefined;
		const rate = typeof raw === "boolean" ? NaN : Number(raw);
		if (!Number.isFinite(rate) || rate <= 0) {
			return 1;
		}
		return Math.max(0.5, Math.min(1.85, rate));
	}

	function tempoRateFromPayload(payload) {
		const artificialRate = artificialRateFromPayload(payload);
		const postPitchFactor = postPitchFactorFromPayload(payload);
		return Math.max(0.25, Math.min(5.5, artificialRate / postPitchFactor));
	}

	function postPitchFactorFromPayload(payload) {
		const raw = payload ? payload.postPitch : undefined;
		const pitchFactor = typeof raw === "boolean" ? NaN : Number(raw);
		if (!Number.isFinite(pitchFactor) || pitchFactor <= 0 || Math.abs(pitchFactor - 1) < 0.001) {
			return 1;
		}
		// Convert WebSpeech/Chrome linear pitch (0.4..1.6) to frequency multiplier
		// matching Google WASM engine's Math.pow(2, (pitch - 1) * 20 / 12)
		const clampedPitch = Math.max(0.4, Math.min(1.6, pitchFactor));
		const freqMultiplier = Math.pow(2, (clampedPitch - 1) * 20 / 12);
		return Math.max(0.35, Math.min(2.5, freqMultiplier));
	}

	function limitSample(sample) {
		if (!Number.isFinite(sample)) {
			return 0;
		}
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
		for (const port of [currentAudioPort, pendingWorkletPort]) {
			clearPortEmptyTimer(port);
		}
		pendingWorkletChunks = [];
		pendingWorkletPort = null;
		workletDrainScheduled = false;
		pendingAudioBuffers = [];
		pendingAudioSampleCount = 0;
		emittedAudioPackets = 0;
		resetPitchProcessor();
		resetTempoProcessor();
	}

	function resetPitchProcessor() {
		pitchInputBuffer = new Float32Array(0);
		pitchReadOffset = 0;
		pitchAaFactor = 1;
		pitchAaZ1 = 0;
		pitchAaZ2 = 0;
		pitchAaInitialized = false;
	}

	function filterPitchAntiAlias(samples, factor) {
		if (!(factor > 1.05) || !samples.length) {
			return samples;
		}
		if (Math.abs(pitchAaFactor - factor) > 0.001) {
			const fcNorm = Math.min(0.46, 0.45 / factor);
			const k = Math.tan(Math.PI * fcNorm);
			const k2 = k * k;
			const norm = 1 / (1 + Math.SQRT2 * k + k2);
			pitchAaB0 = k2 * norm;
			pitchAaB1 = 2 * pitchAaB0;
			pitchAaB2 = pitchAaB0;
			pitchAaA1 = 2 * (k2 - 1) * norm;
			pitchAaA2 = (1 - Math.SQRT2 * k + k2) * norm;
			pitchAaFactor = factor;
			pitchAaInitialized = false;
		}
		const filtered = new Float32Array(samples.length);
		if (!pitchAaInitialized) {
			// Seed Direct Form II Transposed steady state from samples[0] to prevent DC step transients.
			const x0 = samples[0];
			pitchAaZ1 = (1 - pitchAaB0) * x0;
			pitchAaZ2 = (pitchAaB2 - pitchAaA2) * x0;
			pitchAaInitialized = true;
		}
		let z1 = pitchAaZ1;
		let z2 = pitchAaZ2;
		for (let i = 0; i < samples.length; i++) {
			const x = samples[i];
			const y = pitchAaB0 * x + z1;
			z1 = pitchAaB1 * x - pitchAaA1 * y + z2;
			z2 = pitchAaB2 * x - pitchAaA2 * y;
			filtered[i] = y;
		}
		pitchAaZ1 = z1;
		pitchAaZ2 = z2;
		return filtered;
	}

	function resetTempoProcessor() {
		tempoInputBuffer = new Float32Array(0);
		tempoReadOffset = 0;
		tempoOverlapTail = new Float32Array(0);
		tempoStarted = false;
		tempoLastTailEnd = 0;
		totalTempoInputSamples = 0;
		totalTempoEmittedSamples = 0;
	}

	function pushTempoPart(parts, samples) {
		if (samples && samples.length) {
			parts.push(samples);
			totalTempoEmittedSamples += samples.length;
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

	function bestTempoFrameOffset(
		nominalOffset,
		tail = tempoOverlapTail,
		overlapLen = tempoOverlapSamples,
		frameLen = tempoFrameSamples,
		searchRange = tempoSearchSamples
	) {
		const maxStart = Math.max(0, tempoInputBuffer.length - frameLen);
		const clampedNominal = Math.max(0, Math.min(Math.round(nominalOffset), maxStart));
		if (!tempoStarted || !tail || !tail.length || overlapLen <= 0) {
			return clampedNominal;
		}
		const compareLen = Math.min(overlapLen, tail.length);
		const start = Math.max(0, clampedNominal - searchRange);
		const end = Math.min(maxStart, clampedNominal + searchRange);
		let bestOffset = clampedNominal;
		let bestScore = 0;
		for (let i = 0; i < compareLen; i += 2) {
			const diff = tail[i] - tempoInputBuffer[bestOffset + i];
			bestScore += diff * diff;
		}
		if (bestScore === 0) {
			return bestOffset;
		}
		const evalCandidate = (offset) => {
			let score = 0;
			for (let i = 0; i < compareLen; i += 2) {
				const diff = tail[i] - tempoInputBuffer[offset + i];
				score += diff * diff;
				if (score >= bestScore) {
					return false;
				}
			}
			if (score < bestScore) {
				bestScore = score;
				bestOffset = offset;
			}
			return true;
		};
		for (let offset = start; offset <= end; offset += tempoSearchStep) {
			if (offset === clampedNominal) {
				continue;
			}
			evalCandidate(offset);
			if (bestScore === 0) {
				return bestOffset;
			}
		}
		if (tempoSearchStep > 1) {
			const fineStart = Math.max(start, bestOffset - tempoSearchStep + 1);
			const fineEnd = Math.min(end, bestOffset + tempoSearchStep - 1);
			const coarseWinner = bestOffset;
			for (let offset = fineStart; offset <= fineEnd; offset++) {
				if (offset === coarseWinner || offset === clampedNominal) {
					continue;
				}
				evalCandidate(offset);
				if (bestScore === 0) {
					return bestOffset;
				}
			}
		}
		return bestOffset;
	}

	function tempoFrameOutput(frame) {
		if (!tempoStarted) {
			tempoOverlapTail = frame.slice(tempoFrameSamples - tempoOverlapSamples);
			tempoStarted = true;
			return frame.slice(0, tempoFrameSamples - tempoOverlapSamples);
		}
		const output = new Float32Array(tempoSynthesisHopSamples);
		for (let i = 0; i < tempoOverlapSamples; i++) {
			const weight = tempoFadeInWindow[i];
			output[i] = tempoOverlapTail[i] * (1 - weight) + frame[i] * weight;
		}
		output.set(
			frame.subarray(tempoOverlapSamples, tempoFrameSamples - tempoOverlapSamples),
			tempoOverlapSamples
		);
		tempoOverlapTail = frame.slice(tempoFrameSamples - tempoOverlapSamples);
		return output;
	}

	function processPitchSamples(samples, final = false) {
		if (Math.abs(currentPostPitchFactor - 1) < 0.001) {
			resetPitchProcessor();
			return samples;
		}
		if (samples.length) {
			pitchInputBuffer = appendSamples(
				pitchInputBuffer,
				filterPitchAntiAlias(samples, currentPostPitchFactor)
			);
		}
		const outputParts = [];
		const bufLen = pitchInputBuffer.length;
		if (bufLen > 1) {
			const limit = final ? (bufLen - 1) : (bufLen - 2);
			if (limit > pitchReadOffset) {
				const outputLength = Math.max(
					0,
					Math.ceil((limit - pitchReadOffset) / currentPostPitchFactor)
				);
				if (outputLength > 0) {
					const output = new Float32Array(outputLength);
					for (let index = 0; index < outputLength; index++) {
						const inputIndex = Math.min(bufLen - 1, Math.max(0, Math.floor(pitchReadOffset)));
						const fraction = Math.max(0, Math.min(1, pitchReadOffset - inputIndex));
						const p0 = pitchInputBuffer[Math.max(0, inputIndex - 1)];
						const p1 = pitchInputBuffer[inputIndex];
						const p2 = pitchInputBuffer[Math.min(inputIndex + 1, bufLen - 1)];
						const p3 = pitchInputBuffer[Math.min(inputIndex + 2, bufLen - 1)];
						const c0 = p1;
						const c1 = 0.5 * (p2 - p0);
						const c2 = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3;
						const c3 = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3;
						output[index] = ((c3 * fraction + c2) * fraction + c1) * fraction + c0;
						pitchReadOffset += currentPostPitchFactor;
					}
					outputParts.push(output);
				}
			}
			const discard = Math.min(bufLen, Math.max(0, Math.floor(pitchReadOffset) - 1));
			if (discard > 0 && !final) {
				pitchInputBuffer = pitchInputBuffer.slice(discard);
				pitchReadOffset -= discard;
			}
		} else if (final && bufLen === 1 && pitchReadOffset < 1) {
			outputParts.push(pitchInputBuffer.slice(0, 1));
		}
		if (final) {
			resetPitchProcessor();
		}
		return combineSampleParts(outputParts);
	}

	function blendTailWithFrame(prevTail, frame, stepEmit, crossLen) {
		const fade = getFadeInWindow(crossLen);
		const output = new Float32Array(stepEmit);
		for (let i = 0; i < crossLen; i++) {
			const weight = fade[i];
			output[i] = prevTail[i] * (1 - weight) + frame[i] * weight;
		}
		const directEnd = Math.min(stepEmit, frame.length);
		if (directEnd > crossLen) {
			output.set(frame.subarray(crossLen, directEnd), crossLen);
		}
		return output;
	}

	function appendStabilizedTempoTail(parts) {
		const targetTotal = Math.round(totalTempoInputSamples / currentTempoRate);
		const bufLen = tempoInputBuffer.length;
		let needed = Math.max(0, targetTotal - totalTempoEmittedSamples);
		if (!bufLen || needed <= 0) {
			return;
		}
		const subFrame = Math.min(tempoFrameSamples, Math.max(Math.min(bufLen, 64), Math.floor(bufLen * 0.75)));
		const subOverlap = Math.min(tempoOverlapSamples, Math.max(16, Math.floor(subFrame * 0.4)));
		const subHop = Math.max(16, subFrame - subOverlap);
		if (!tempoStarted && needed > bufLen && bufLen >= 48) {
			const first = tempoInputBuffer.subarray(0, subFrame);
			const take = Math.min(subHop, needed);
			pushTempoPart(parts, first.slice(0, take));
			tempoOverlapTail = first.slice(take);
			tempoStarted = true;
			tempoReadOffset = take * currentTempoRate;
			needed = Math.max(0, targetTotal - totalTempoEmittedSamples);
		}
		if (!tempoStarted) {
			const outLen = Math.min(needed, bufLen);
			const out = tempoInputBuffer.slice(0, outLen);
			if (outLen < bufLen && outLen >= 24) {
				const crossLen = Math.min(24, Math.floor(outLen / 2));
				const fade = getFadeInWindow(crossLen);
				const outStart = outLen - crossLen;
				const endStart = bufLen - crossLen;
				for (let i = 0; i < crossLen; i++) {
					const w = fade[i];
					out[outStart + i] = out[outStart + i] * (1 - w) + tempoInputBuffer[endStart + i] * w;
				}
			}
			pushTempoPart(parts, out);
			return;
		}
		if (bufLen >= 48 && bufLen >= subFrame) {
			const subSearch = Math.min(tempoSearchSamples, Math.max(24, Math.floor(subFrame / 2)));
			let guard = 0;
			while (needed > subOverlap && guard++ < 64) {
				const matchOverlap = Math.min(tempoOverlapTail.length, subOverlap);
				const bestOff = bestTempoFrameOffset(
					tempoReadOffset,
					tempoOverlapTail,
					matchOverlap,
					subFrame,
					subSearch
				);
				const frame = tempoInputBuffer.subarray(bestOff, bestOff + subFrame);
				const stepEmit = Math.min(subHop, needed);
				const crossLen = Math.min(matchOverlap, stepEmit, frame.length);
				pushTempoPart(parts, blendTailWithFrame(tempoOverlapTail, frame, stepEmit, crossLen));
				tempoOverlapTail = stepEmit < frame.length ? frame.slice(stepEmit) : new Float32Array(0);
				tempoReadOffset += stepEmit * currentTempoRate;
				needed = Math.max(0, targetTotal - totalTempoEmittedSamples);
			}
		}
		if (needed > 0 && tempoOverlapTail.length > 0) {
			const finalLen = Math.min(needed, tempoOverlapTail.length);
			const endSliceStart = Math.max(0, bufLen - finalLen);
			const endSlice = tempoInputBuffer.subarray(endSliceStart, endSliceStart + finalLen);
			const crossLen = Math.min(finalLen, endSlice.length);
			const out = blendTailWithFrame(tempoOverlapTail, endSlice, finalLen, crossLen);
			if (finalLen > crossLen) {
				out.set(tempoOverlapTail.subarray(crossLen, finalLen), crossLen);
			}
			pushTempoPart(parts, out);
		}
	}

	function processTempoSamples(samples, final = false) {
		if (Math.abs(currentTempoRate - 1) < 0.001) {
			resetTempoProcessor();
			return samples;
		}
		if (samples.length) {
			tempoInputBuffer = appendSamples(tempoInputBuffer, samples);
			totalTempoInputSamples += samples.length;
		}
		const outputParts = [];
		const analysisHop = Math.max(1, tempoSynthesisHopSamples * currentTempoRate);
		while (true) {
			const nominalOffset = Math.round(tempoReadOffset);
			const rightMargin = (final || !tempoStarted) ? 0 : tempoSearchSamples;
			if (nominalOffset + tempoFrameSamples + rightMargin > tempoInputBuffer.length) {
				break;
			}
			const frameOffset = bestTempoFrameOffset(nominalOffset);
			const frame = tempoInputBuffer.subarray(frameOffset, frameOffset + tempoFrameSamples);
			pushTempoPart(outputParts, tempoFrameOutput(frame));
			tempoLastTailEnd = frameOffset + tempoFrameSamples;
			tempoReadOffset += analysisHop;
		}
		if (!final) {
			const discard = Math.min(
				tempoLastTailEnd,
				Math.max(0, Math.floor(tempoReadOffset) - tempoSearchSamples)
			);
			if (discard > 0) {
				tempoInputBuffer = tempoInputBuffer.slice(discard);
				tempoReadOffset -= discard;
				tempoLastTailEnd = Math.max(0, tempoLastTailEnd - discard);
			}
		} else {
			appendStabilizedTempoTail(outputParts);
			resetTempoProcessor();
		}
		return combineSampleParts(outputParts);
	}

	function queueTempoInput(samples, sessionToken = currentSessionToken) {
		const tempoSamples = processTempoSamples(samples, false);
		if (tempoSamples.length) {
			const pitchSamples = processPitchSamples(tempoSamples, false);
			if (pitchSamples.length) {
				queueProcessedAudio(pitchSamples, sessionToken);
			}
		}
	}

	function flushAudioProcessors(sessionToken = currentSessionToken) {
		if (stopped || !isCurrentSession(sessionToken)) {
			return;
		}
		drainPendingWorkletChunks(false);
		const tempoTail = Math.abs(currentTempoRate - 1) < 0.001
			? new Float32Array(0)
			: processTempoSamples(new Float32Array(0), true);
		resetTempoProcessor();
		if (Math.abs(currentPostPitchFactor - 1) < 0.001) {
			resetPitchProcessor();
			if (tempoTail.length) {
				queueProcessedAudio(tempoTail, sessionToken);
			}
			return;
		}
		const pitchTail = processPitchSamples(tempoTail, true);
		if (pitchTail.length) {
			queueProcessedAudio(pitchTail, sessionToken);
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
		let baseTarget = longStreamAudioPacketSamples;
		if (emittedAudioPackets < earlyAudioPacketCount) {
			baseTarget = earlyAudioPacketSamples;
		} else if (emittedAudioPackets < steadyAudioPacketCount) {
			baseTarget = steadyAudioPacketSamples;
		}
		if (currentArtificialRate > 1.05) {
			return Math.max(firstAudioPacketSamples, Math.round(baseTarget / currentArtificialRate));
		}
		return baseTarget;
	}

	function queueAudioPacket(samples, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			return;
		}
		if (suppressBridgeAudio) {
			pendingAudioBuffers = [];
			pendingAudioSampleCount = 0;
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
		if (!event || !sessionActive || stopped) {
			return;
		}
		if (event.type === "word") {
			emit({ type: "mark", charIndex: currentMarkOffset + Math.max(0, Number(event.charIndex) || 0) });
			return;
		}
		if (event.type === "end") {
			drainPendingWorkletChunks(false);
			sawSynthesisEnd = true;
			synthesisEndAt = performance.now();
			if (currentEndResolver) {
				currentEndResolver();
			}
			return;
		}
		if (event.type === "error") {
			synthesisErrorMessage = event.errorMessage || event.message || "Browser speech synthesis failed.";
			if (currentEndResolver) {
				currentEndResolver();
			}
		}
	}

	function isEngineStillGenerating() {
		const engine = getTtsEngine();
		if (engine && typeof engine.I === "boolean") {
			return engine.I;
		}
		return synthesisGenerating;
	}

	function flushEngineLeftoverSamples(sessionToken = currentSessionToken) {
		if (stopped || !isCurrentSession(sessionToken)) {
			return;
		}
		const engine = getTtsEngine();
		if (
			engine
			&& engine.J
			&& (engine.J instanceof Float32Array || (ArrayBuffer.isView(engine.J) && typeof engine.J.length === "number"))
			&& engine.K > 0
		) {
			const count = Math.min(engine.K, engine.J.length);
			const leftover = engine.J instanceof Float32Array
				? engine.J.slice(0, count)
				: new Float32Array(engine.J.subarray ? engine.J.subarray(0, count) : engine.J).slice(0, count);
			engine.J = null;
			engine.K = 0;
			lastChunkAt = performance.now();
			queueAudio(leftover, sessionToken);
		}
	}

	function scheduleWorkletEmpty(port, sessionToken = currentSessionToken) {
		if (!port) {
			return;
		}
		clearPortEmptyTimer(port);
		const checkEmpty = () => {
			port._emptyTimer = null;
			if (stopped || sawSynthesisEnd || !isCurrentSession(sessionToken) || typeof port.onmessage !== "function") {
				return;
			}
			if (isEngineStillGenerating()) {
				port._emptyTimer = scheduleNativeTimeout(checkEmpty, 0);
				return;
			}
			drainPendingWorkletChunks(false);
			flushEngineLeftoverSamples(sessionToken);
			if (!sawSynthesisEnd && typeof port.onmessage === "function") {
				port.onmessage({ data: { type: "empty" } });
			}
		};
		const engine = getTtsEngine();
		const hasNativeBusyFlag = Boolean(engine && typeof engine.I === "boolean");
		const delayMs = hasNativeBusyFlag
			? 0
			: (synthesisGenerating ? synthesisGeneratingEmptyDelayMs : synthesisFinishedIdleMs);
		port._emptyTimer = scheduleNativeTimeout(checkEmpty, delayMs);
	}

	function flushAudioQueue(sessionToken = currentSessionToken) {
		if (stopped || !isCurrentSession(sessionToken)) {
			return;
		}
		if (suppressBridgeAudio) {
			pendingAudioBuffers = [];
			pendingAudioSampleCount = 0;
			return;
		}
		if (!pendingAudioSampleCount) {
			return;
		}
		emit({
			type: "audio",
			sampleRate: currentAudioSampleRate,
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
		queueAudioPacket(samples, sessionToken);
	}

	function queueAudio(samples, sessionToken = currentSessionToken) {
		if (!isCurrentSession(sessionToken)) {
			return;
		}
		queueTempoInput(samples, sessionToken);
	}

	function finishSegmentAudio(hasNextSegment, sessionToken = currentSessionToken) {
		if (stopped || !isCurrentSession(sessionToken)) {
			return;
		}
		if (!hasNextSegment) {
			return;
		}
		flushAudioProcessors(sessionToken);
		flushAudioQueue(sessionToken);
	}

	function drainPendingWorkletChunks(allowEmptyNotify = false) {
		workletDrainScheduled = false;
		if (!pendingWorkletChunks.length) {
			return;
		}
		const chunks = pendingWorkletChunks;
		const port = pendingWorkletPort;
		const sessionToken = pendingWorkletSessionToken;
		pendingWorkletChunks = [];
		pendingWorkletPort = null;
		if (stopped || !isCurrentSession(sessionToken)) {
			return;
		}
		const combined = combineSampleParts(chunks);
		if (combined.length) {
			queueAudio(combined, sessionToken);
		}
		if (!allowEmptyNotify || !port) {
			return;
		}
		const engine = getTtsEngine();
		if (engine && typeof engine.I === "boolean" && !engine.I) {
			clearPortEmptyTimer(port);
			flushEngineLeftoverSamples(sessionToken);
			if (!sawSynthesisEnd && typeof port.onmessage === "function") {
				port.onmessage({ data: { type: "empty" } });
			}
			return;
		}
		scheduleWorkletEmpty(port, sessionToken);
	}

	class FakeAudioWorkletNode {
		constructor() {
			const sessionToken = currentSessionToken;
			this.port = {
				_sessionToken: sessionToken,
				onmessage: null,
				postMessage(message) {
					if (!message) {
						return;
					}
					if (message.command === "clearBuffers") {
						// The engine sends clearBuffers from its normal end path as well as from
						// cancellation. Session cancellation already resets the bridge queue in
						// stopActiveSynthesis(); clearing it here would discard the boundary hold
						// and DSP tail before finishSegmentAudio() can emit the final word.
						clearPortEmptyTimer(this);
						return;
					}
					if (stopped) {
						return;
					}
					if (synthesisGenerating) {
						this._sessionToken = currentSessionToken;
					}
					if (!isCurrentSession(this._sessionToken)) {
						return;
					}
					if (message.command !== "addBuffer" || !message.buffer) {
						return;
					}
					const engine = getTtsEngine();
					if (
						engine
						&& engine.J
						&& (
							engine.J === message.buffer
							|| (
								engine.J.buffer
								&& (
									engine.J.buffer === message.buffer
									|| (message.buffer.buffer && engine.J.buffer === message.buffer.buffer)
								)
							)
						)
					) {
						// offscreen_compiled.js Jh() clears its local reference when flushing a.J
						// at 128 samples or EOF without clearing engine.J / engine.K on the instance.
						engine.J = null;
						engine.K = 0;
					}
					let samples;
					if (message.buffer instanceof Float32Array) {
						samples = message.buffer.slice();
					} else if (ArrayBuffer.isView(message.buffer)) {
						samples = new Float32Array(message.buffer);
					} else if (
						message.buffer
						&& typeof message.buffer.slice === "function"
						&& typeof message.buffer.byteLength === "number"
					) {
						samples = new Float32Array(message.buffer.slice(0));
					} else {
						samples = new Float32Array(message.buffer);
					}
					if (!samples.length) {
						return;
					}
					lastChunkAt = performance.now();
					currentAudioPort = this;
					pendingWorkletChunks.push(samples);
					pendingWorkletPort = this;
					pendingWorkletSessionToken = this._sessionToken;
					if (!workletDrainScheduled) {
						workletDrainScheduled = true;
						queueMicrotask(() => {
							drainPendingWorkletChunks(true);
						});
					}
				},
			};
			currentAudioPort = this.port;
		}

		connect() {}
		disconnect() {}
	}

	window.AudioContext = FakeAudioContext;
	window.webkitAudioContext = FakeAudioContext;
	window.AudioWorkletNode = FakeAudioWorkletNode;

	function getEngineWorkletPort(engine) {
		return (engine && engine.o && engine.o.port) || currentAudioPort;
	}

	async function waitForEngineDrain(timeoutMs, checkIdleDrain, timeoutMessage, sessionToken = currentSessionToken) {
		const startedAt = performance.now();
		let pollIterations = 0;
		while (performance.now() - startedAt < timeoutMs) {
			if (synthesisErrorMessage) {
				throw new Error(synthesisErrorMessage);
			}
			if (stopped || !isCurrentSession(sessionToken)) {
				return;
			}
			if (sawSynthesisEnd) {
				drainPendingWorkletChunks(false);
				flushEngineLeftoverSamples(sessionToken);
				return;
			}
			const now = performance.now();
			const engine = getTtsEngine();
			if (engine && typeof engine.I === "boolean" && !engine.I) {
				if (lastChunkAt > 0 || (pollIterations > 0 && now - startedAt >= synthesisFinishedIdleMs)) {
					drainPendingWorkletChunks(false);
					flushEngineLeftoverSamples(sessionToken);
					const workletPort = getEngineWorkletPort(engine);
					if (!sawSynthesisEnd && workletPort && typeof workletPort.onmessage === "function") {
						workletPort.onmessage({ data: { type: "empty" } });
					}
					if (synthesisErrorMessage) {
						throw new Error(synthesisErrorMessage);
					}
					return;
				}
			}
			if (checkIdleDrain) {
				const requiredIdleMs = sawSynthesisEnd ? synthesisSettledIdleMs : synthesisFinishedIdleMs;
				const audioHasDrained = lastChunkAt > 0 && now - lastChunkAt >= requiredIdleMs;
				const endHasSettled = sawSynthesisEnd && synthesisEndAt > 0 && now - synthesisEndAt >= requiredIdleMs;
				if (audioHasDrained || (endHasSettled && lastChunkAt <= 0)) {
					drainPendingWorkletChunks(false);
					flushEngineLeftoverSamples(sessionToken);
					if (synthesisErrorMessage) {
						throw new Error(synthesisErrorMessage);
					}
					return;
				}
			}
			let resolver = null;
			await new Promise((resolve) => {
				let timerId = null;
				resolver = () => {
					if (timerId !== null) {
						clearNativeTimeout(timerId);
						timerId = null;
					}
					if (currentEndResolver === resolver) {
						currentEndResolver = null;
					}
					resolve();
				};
				currentEndResolver = resolver;
				timerId = scheduleNativeTimeout(() => {
					timerId = null;
					if (currentEndResolver === resolver) {
						currentEndResolver = null;
					}
					resolve();
				}, synthesisIdlePollMs);
			});
			if (currentEndResolver === resolver) {
				currentEndResolver = null;
			}
			pollIterations++;
		}
		throw new Error(timeoutMessage);
	}

	function waitForSynthesisComplete(timeoutMs, sessionToken = currentSessionToken) {
		return waitForEngineDrain(timeoutMs, true, "Timed out waiting for browser speech audio.", sessionToken);
	}

	function waitForWasmEnd(timeoutMs, sessionToken = currentSessionToken) {
		return waitForEngineDrain(timeoutMs, false, "Timed out waiting for WASM engine synthesis to complete.", sessionToken);
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
		if (isTtsEngineInstance(cachedTtsEngine)) {
			return cachedTtsEngine;
		}
		cachedTtsEngine = null;
		// Engine globals are minified and have changed between bundled engine versions.
		for (const key of ["Yh", "Xh", "Vh", "Uh"]) {
			if (isTtsEngineInstance(window[key])) {
				cachedTtsEngine = window[key];
				return cachedTtsEngine;
			}
		}
		for (const key of Object.getOwnPropertyNames(window)) {
			try {
				const val = window[key];
				if (isTtsEngineInstance(val)) {
					cachedTtsEngine = val;
					return cachedTtsEngine;
				}
			} catch (_) {}
		}
		return null;
	}

	const readyLanguages = new Set();
	const pendingLanguages = new Map();
	const pendingPackagePrepares = new Map();
	const readyVoices = new Set();

	function canInstallVoiceOnDemand(engine, voiceName) {
		return Boolean(
			engine
			&& voiceName
			&& engine.G
			&& typeof engine.G === "object"
			&& engine.S
			&& typeof engine.S === "object"
			&& typeof engine.S[voiceName] === "string"
			&& engine.S[voiceName].indexOf("http") !== 0
		);
	}

	function packagePrepareKeys(engine, voiceName) {
		const keys = [];
		if (!engine || !voiceName) {
			return keys;
		}
		const primaryKey = (engine.N && engine.N[voiceName]) || (engine.G && engine.G[voiceName]) || voiceName;
		if (primaryKey) {
			keys.push(primaryKey);
		}
		const dependentKey = engine.D && engine.D[voiceName];
		if (dependentKey && dependentKey !== primaryKey) {
			keys.push(dependentKey);
		}
		return keys;
	}

	async function waitForPendingPackagePrepares(engine, voiceName) {
		const keys = packagePrepareKeys(engine, voiceName);
		const inFlight = keys.map((key) => pendingPackagePrepares.get(key)).filter(Boolean);
		if (inFlight.length) {
			await Promise.allSettled(inFlight);
		}
	}

	async function runEngineSpeak(engine, text, options, shouldAbort) {
		const voiceName = options && typeof options.voiceName === "string" ? options.voiceName : "";
		await waitForPendingPackagePrepares(engine, voiceName);
		if (shouldAbort()) {
			return false;
		}
		const keys = packagePrepareKeys(engine, voiceName);
		const speakPromise = Promise.resolve(engine.onSpeak(text, options));
		if (keys.length) {
			const tracked = speakPromise.catch(() => undefined);
			for (const key of keys) {
				pendingPackagePrepares.set(key, tracked);
			}
			tracked.finally(() => {
				for (const key of keys) {
					if (pendingPackagePrepares.get(key) === tracked) {
						pendingPackagePrepares.delete(key);
					}
				}
			});
		}
		await speakPromise;
		return true;
	}

	async function ensureLanguageReady(engine, lang) {
		if (!lang || readyLanguages.has(lang)) {
			return;
		}
		let installPromise = pendingLanguages.get(lang);
		if (!installPromise && typeof engine.onInstallLanguageRequest === "function") {
			installPromise = (async () => {
				const result = await engine.onInstallLanguageRequest(lang);
				if (result && result.status && result.status !== "installed") {
					throw new Error(`Failed to prepare language ${lang} (${result.status}).`);
				}
				readyLanguages.add(lang);
			})().finally(() => {
				if (pendingLanguages.get(lang) === installPromise) {
					pendingLanguages.delete(lang);
				}
			});
			pendingLanguages.set(lang, installPromise);
		}
		if (installPromise) {
			await installPromise;
		}
	}

	async function ensureEngineInitialized() {
		const engine = getTtsEngine();
		if (!engine) {
			throw new Error("WASM TTS engine was not loaded.");
		}
		if (!initPromise || initializedEngine !== engine) {
			initializedEngine = engine;
			initPromise = Promise.resolve()
				.then(() => engine.init("google-tts-for-nvda"))
				.catch((error) => {
					if (initializedEngine === engine) {
						initPromise = null;
						initializedEngine = null;
					}
					throw error;
				});
		}
		await initPromise;
	}

	async function stopActiveSynthesis() {
		const stopToken = ++currentSessionToken;
		sessionActive = false;
		stopped = true;
		synthesisGenerating = false;
		synthesisErrorMessage = "";
		sawSynthesisEnd = false;
		synthesisEndAt = 0;
		lastChunkAt = 0;
		currentSessionId = null;
		currentMarkOffset = 0;
		suppressBridgeAudio = false;
		if (currentEndResolver) {
			currentEndResolver();
			currentEndResolver = null;
		}
		resetAudioQueue();
		if (inFlightStopPromise) {
			await Promise.race([
				inFlightStopPromise,
				new Promise((resolve) => scheduleNativeTimeout(resolve, 1000)),
			]);
			if (currentSessionToken !== stopToken) {
				return;
			}
		}
		const engine = getTtsEngine();
		if (engine && typeof engine.onStop === "function" && !inFlightStopPromise) {
			const stopPromise = (async () => {
				try {
					await engine.onStop();
				} catch (error) {
					console.debug("Ignored engine stop failure during cancellation:", error);
				}
			})();
			inFlightStopPromise = stopPromise;
			try {
				await stopPromise;
			} finally {
				if (inFlightStopPromise === stopPromise) {
					inFlightStopPromise = null;
				}
			}
		}
		if (currentSessionToken === stopToken) {
			sessionActive = false;
			currentSessionId = null;
			suppressBridgeAudio = false;
		}
	}

	async function stopActiveSynthesisWithTimeout(timeoutMs = 1000) {
		try {
			const stoppedCleanly = await Promise.race([
				stopActiveSynthesis().then(() => true),
				new Promise((resolve) => scheduleNativeTimeout(() => resolve(false), timeoutMs)),
			]);
			if (!stoppedCleanly) {
				console.debug("Timed out while stopping WASM TTS engine.");
			}
		} catch (stopError) {
			console.debug("Ignored engine stop failure:", stopError);
		}
	}

	function resetSynthesisRunState() {
		lastChunkAt = 0;
		stopped = false;
		sawSynthesisEnd = false;
		synthesisEndAt = 0;
		synthesisErrorMessage = "";
		synthesisGenerating = false;
		resetAudioQueue();
	}

	async function stopIfActiveForControl(seq) {
		if (sessionActive || currentSessionId || inFlightStopPromise) {
			await stopActiveSynthesisWithTimeout(1000);
			if (controlSequence !== seq) {
				return false;
			}
		}
		return true;
	}

	async function prepareEngineAndLanguage(lang, voiceName, seq) {
		await ensureEngineInitialized();
		if (controlSequence !== seq) {
			return null;
		}
		const engine = getTtsEngine();
		if (!engine) {
			throw new Error("WASM TTS engine was not loaded.");
		}
		if (lang && !readyLanguages.has(lang) && !canInstallVoiceOnDemand(engine, voiceName)) {
			await ensureLanguageReady(engine, lang);
			if (controlSequence !== seq) {
				return null;
			}
		}
		await waitForPendingPackagePrepares(engine, voiceName);
		if (controlSequence !== seq) {
			return null;
		}
		return engine;
	}

	window.googleTtsForNvdaStop = async function googleTtsForNvdaStop() {
		++controlSequence;
		await stopActiveSynthesis();
	};

	window.googleTtsForNvdaPreload = async function googleTtsForNvdaPreload(payload) {
		const request = payload && typeof payload === "object" ? payload : {};
		const seq = ++controlSequence;
		if (!(await stopIfActiveForControl(seq))) {
			return { success: false, preloaded: false, cancelled: true };
		}
		const sessionToken = beginSession(request.sessionId, true);
		currentOutputGain = 0;
		try {
			resetSynthesisRunState();
			currentArtificialRate = 1;
			currentTempoRate = 1;
			currentPostPitchFactor = 1;
			const lang = typeof request.lang === "string" ? request.lang : "";
			const voiceName = typeof request.voiceName === "string" ? request.voiceName : "";
			const engine = await prepareEngineAndLanguage(lang, voiceName, seq);
			if (!engine || controlSequence !== seq || !isCurrentSession(sessionToken)) {
				return { success: false, preloaded: false, cancelled: true };
			}
			if (!voiceName) {
				throw new Error("Missing voiceName for preload.");
			}
			if (readyVoices.has(voiceName)) {
				return { success: true, preloaded: true, cached: true };
			}
			const preloadPort = getEngineWorkletPort(engine);
			if (preloadPort) {
				preloadPort._sessionToken = sessionToken;
			}
			synthesisGenerating = true;
			let speakStarted = false;
			try {
				speakStarted = await runEngineSpeak(
					engine,
					typeof request.text === "string" && request.text.length ? request.text : " ",
					{
						voiceName: voiceName,
						lang: lang,
						rate: 1,
						pitch: 1,
						volume: 0,
					},
					() => stopped || controlSequence !== seq || !isCurrentSession(sessionToken)
				);
			} finally {
				synthesisGenerating = false;
			}
			if (!speakStarted || stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
				return { success: false, preloaded: false, cancelled: true };
			}
			if (synthesisErrorMessage) {
				throw new Error(synthesisErrorMessage);
			}
			if (lastChunkAt > 0 && !sawSynthesisEnd) {
				scheduleWorkletEmpty(getEngineWorkletPort(engine), sessionToken);
			}
			await waitForWasmEnd(15000, sessionToken);
			if (stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
				return { success: false, preloaded: false, cancelled: true };
			}
			readyVoices.add(voiceName);
			await stopActiveSynthesisWithTimeout(1000);
			return { success: true, preloaded: true };
		} catch (error) {
			if (isCurrentSession(sessionToken)) {
				await stopActiveSynthesisWithTimeout(1000);
			}
			throw error;
		} finally {
			if (controlSequence === seq) {
				sessionActive = false;
				if (currentSessionId === request.sessionId) {
					currentSessionId = null;
				}
				suppressBridgeAudio = false;
				currentOutputGain = 1;
			}
		}
	};

	window.googleTtsForNvdaReady = function googleTtsForNvdaReady() {
		return getTtsEngine() !== null;
	};

	window.googleTtsForNvdaSpeak = async function googleTtsForNvdaSpeak(payload) {
		const request = payload && typeof payload === "object" ? payload : {};
		const requestSessionId = request.sessionId;
		const seq = ++controlSequence;
		let sessionToken = 0;
		try {
			if (!(await stopIfActiveForControl(seq))) {
				return { success: false, cancelled: true };
			}
			const lang = typeof request.lang === "string" ? request.lang : "";
			const voiceName = typeof request.voiceName === "string" ? request.voiceName : "";
			const engine = await prepareEngineAndLanguage(lang, voiceName, seq);
			if (!engine || controlSequence !== seq) {
				return { success: false, cancelled: true };
			}
			const sessionId = requestSessionId;
			const filteredSegments = Array.isArray(request.segments)
				? request.segments.filter((segment) => typeof segment === "string" && segment.length)
				: [];
			const fallbackText = typeof request.text === "string" && request.text.length ? request.text : "";
			const textSegments = filteredSegments.length ? filteredSegments : (fallbackText ? [fallbackText] : []);
			if (!textSegments.length) {
				if (controlSequence === seq) {
					sessionActive = false;
					currentSessionId = null;
					suppressBridgeAudio = false;
				}
				return { success: true, empty: true };
			}
			sessionToken = beginSession(sessionId, false);
			currentMarkOffset = 0;
			currentOutputGain = outputGainFromPayload(request);
			resetSynthesisRunState();
			currentArtificialRate = artificialRateFromPayload(request);
			currentPostPitchFactor = postPitchFactorFromPayload(request);
			currentTempoRate = tempoRateFromPayload(request);
			const engineRate = typeof request.rate === "number" && Number.isFinite(request.rate) ? request.rate : 1;
			const enginePitch = typeof request.pitch === "number" && Number.isFinite(request.pitch) ? request.pitch : 1;
			const engineVolume = typeof request.volume === "number" && Number.isFinite(request.volume) ? request.volume : 1;
			emit({ type: "started" }, sessionToken);
			for (let segmentIndex = 0; segmentIndex < textSegments.length; segmentIndex++) {
				if (stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
					return { success: false, cancelled: true };
				}
				const textSegment = textSegments[segmentIndex];
				lastChunkAt = 0;
				sawSynthesisEnd = false;
				synthesisEndAt = 0;
				synthesisErrorMessage = "";
				const speakPort = getEngineWorkletPort(engine);
				if (speakPort) {
					speakPort._sessionToken = sessionToken;
				}
				synthesisGenerating = true;
				let speakStarted = false;
				try {
					speakStarted = await runEngineSpeak(
						engine,
						textSegment,
						{
							voiceName: voiceName,
							lang: lang,
							rate: engineRate,
							pitch: enginePitch,
							volume: engineVolume,
						},
						() => stopped || controlSequence !== seq || !isCurrentSession(sessionToken)
					);
				} finally {
					synthesisGenerating = false;
				}
				if (!speakStarted || stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
					return { success: false, cancelled: true };
				}
				if (lastChunkAt > 0 && !sawSynthesisEnd) {
					scheduleWorkletEmpty(getEngineWorkletPort(engine), sessionToken);
				}

				const hasNextSegment = segmentIndex < textSegments.length - 1;

				if (hasNextSegment) {
					// The normal engine end path has already settled its worklet buffers. Avoid
					// adding another queue-idle delay before starting the next hidden segment.
					await waitForWasmEnd(120000, sessionToken);
				} else {
					await waitForSynthesisComplete(120000, sessionToken);
				}

				if (stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
					return { success: false, cancelled: true };
				}

				finishSegmentAudio(hasNextSegment, sessionToken);
				if (hasNextSegment) {
					// Each hidden segment is a separate WASM onSpeak call; expose that boundary so
					// Python can shorten the engine's end-of-utterance silence without splitting CDP requests.
					emit({ type: "segmentEnd" }, sessionToken);
				}
				currentMarkOffset += textSegment.length;
			}
			if (stopped || controlSequence !== seq || !isCurrentSession(sessionToken)) {
				return { success: false, cancelled: true };
			}
			currentMarkOffset = 0;
			if (voiceName) {
				readyVoices.add(voiceName);
			}
			flushAudioProcessors(sessionToken);
			flushAudioQueue(sessionToken);
			emit({ type: "done" }, sessionToken);
			await stopActiveSynthesisWithTimeout(1000);
			if (controlSequence === seq) {
				sessionActive = false;
				if (currentSessionId === sessionId) {
					currentSessionId = null;
				}
			}
			return { success: true };
		} catch (error) {
			const ownsControl = sessionToken ? isCurrentSession(sessionToken) : controlSequence === seq;
			if (ownsControl) {
				emit({
					type: "error",
					message: error && error.message ? error.message : String(error),
				}, sessionToken || currentSessionToken);
				await stopActiveSynthesisWithTimeout(1000);
				if (controlSequence === seq) {
					sessionActive = false;
					if (currentSessionId === requestSessionId) {
						currentSessionId = null;
					}
					currentMarkOffset = 0;
					suppressBridgeAudio = false;
				}
			}
			throw error;
		}
	};
})();

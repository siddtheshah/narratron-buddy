import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Page, WebSocketRoute } from '@playwright/test';

type DeviceAwarePcmModule = typeof import('./device-aware-pcm.js');
type PcmWorkletProcessorModule = typeof import('./pcm-worklet-processor.js');

interface MockPlaybackBuffer {
  readonly sampleRate: number;
  readonly length: number;
  readonly channelData: Float32Array;
}

interface MockPlaybackSourceHandle {
  readonly started: boolean;
  triggerEnded(): void;
}

interface MockAudioContextRecord {
  readonly sampleRate: number;
  readonly state: AudioContextState;
  readonly resumed: boolean;
  readonly playbackBuffers: readonly MockPlaybackBuffer[];
  readonly playbackSources: readonly MockPlaybackSourceHandle[];
  readonly worklets: readonly {
    readonly disconnected: boolean;
    emitPcm(samples: readonly number[], rms: number): void;
  }[];
  readonly sources: readonly { readonly disconnected: boolean }[];
}

interface MockMediaEnvironment {
  readonly contexts: readonly MockAudioContextRecord[];
  readonly encoderCount: number;
  readonly closedEncoderCount: number;
  readonly stoppedTrackCount: number;
  setFailGetUserMedia(error: Error | null): void;
  setNow(ms: number): void;
  advanceNow(ms: number): void;
  tick(): Promise<void>;
}

declare global {
  interface Window {
    __micTestEnv: MockMediaEnvironment;
  }
}

function installBrowserMediaMocksScript(): string {
  return `(() => {
    const contexts = [];
    const tracks = [];
    const encoders = [];
    let failGetUserMediaError = null;
    let nowMs = 1000;
    Date.now = () => nowMs;

    class MockTrack {
      constructor() {
        this.stopped = false;
        tracks.push(this);
      }
      stop() {
        this.stopped = true;
      }
    }

    class MockMediaStream {
      constructor() {
        this._tracks = [new MockTrack()];
      }
      getTracks() {
        return this._tracks;
      }
    }

    class MockMediaStreamAudioSourceNode {
      constructor() {
        this.disconnected = false;
      }
      connect() {}
      disconnect() {
        this.disconnected = true;
      }
    }

    class MockAudioWorkletNode {
      constructor() {
        this.disconnected = false;
        this.port = { onmessage: null };
      }
      connect() {}
      disconnect() {
        this.disconnected = true;
      }
      emitPcm(samples, rms) {
        const int16 = new Int16Array(samples);
        const float32 = new Float32Array(samples.length);
        for (let i = 0; i < samples.length; i += 1) {
          float32[i] = samples[i] < 0 ? samples[i] / 32768 : samples[i] / 32767;
        }
        this.port.onmessage?.({
          data: {
            pcm: new Uint8Array(int16.buffer),
            float32,
            rms,
          },
        });
      }
    }

    class MockAudioBuffer {
      constructor(channels, length, sampleRate) {
        this.numberOfChannels = channels;
        this.length = length;
        this.sampleRate = sampleRate;
        this.channelData = new Float32Array(length);
      }
      getChannelData() {
        return this.channelData;
      }
    }

    class MockBufferSourceNode {
      constructor() {
        this.buffer = null;
        this.onended = null;
        this.started = false;
      }
      connect() {}
      start() {
        this.started = true;
      }
      stop() {
        this.started = false;
      }
      triggerEnded() {
        this.onended?.();
      }
    }

    class MockAudioContext {
      constructor(opts = {}) {
        this.sampleRate = opts.sampleRate ?? 48000;
        this.state = 'running';
        this.resumed = false;
        this.destination = {};
        this.worklets = [];
        this.sources = [];
        this.playbackBuffers = [];
        this.playbackSources = [];
        this.audioWorklet = {
          addModule: async () => {},
        };
        contexts.push(this);
      }
      createMediaStreamSource() {
        const source = new MockMediaStreamAudioSourceNode();
        this.sources.push(source);
        return source;
      }
      createBuffer(channels, length, sampleRate) {
        const buf = new MockAudioBuffer(channels, length, sampleRate);
        this.playbackBuffers.push(buf);
        return buf;
      }
      createBufferSource() {
        const src = new MockBufferSourceNode();
        this.playbackSources.push(src);
        return src;
      }
      async resume() {
        this.resumed = true;
      }
      async close() {
        this.state = 'closed';
      }
    }

    class MockAudioEncoder {
      constructor(callbacks) {
        this.callbacks = callbacks;
        this.state = 'unconfigured';
        encoders.push(this);
      }
      configure() {
        this.state = 'configured';
      }
      encode() {}
      async flush() {}
      close() {
        this.state = 'closed';
      }
    }

    class MockAudioData {
      close() {}
    }

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: async () => [
          { kind: 'audioinput', deviceId: 'mic-1', label: 'Studio Microphone' },
        ],
        getUserMedia: async () => {
          if (failGetUserMediaError) {
            const err = failGetUserMediaError;
            failGetUserMediaError = null;
            throw err;
          }
          return new MockMediaStream();
        },
        addEventListener: () => {},
      },
    });

    window.AudioContext = MockAudioContext;
    window.AudioWorkletNode = function () {
      const node = new MockAudioWorkletNode();
      const latest = contexts[contexts.length - 1];
      if (latest) latest.worklets.push(node);
      return node;
    };
    window.AudioEncoder = MockAudioEncoder;
    window.AudioData = MockAudioData;

    window.__micTestEnv = {
      contexts,
      get encoderCount() {
        return encoders.length;
      },
      get closedEncoderCount() {
        return encoders.filter((item) => item.state === 'closed').length;
      },
      get stoppedTrackCount() {
        return tracks.filter((item) => item.stopped).length;
      },
      setFailGetUserMedia(error) {
        failGetUserMediaError = error;
      },
      setNow(ms) {
        nowMs = ms;
      },
      advanceNow(ms) {
        nowMs += ms;
      },
      tick: () => new Promise((resolve) => window.setTimeout(resolve, 0)),
    };
  })();`;
}

async function mountPcmHarness(canvas: CanvasHarness, page: Page): Promise<void> {
  await page.addInitScript({ content: installBrowserMediaMocksScript() });
  await canvas.mount();
}

test('device-aware-pcm module supports non-continuous VAD and handles getUserMedia errors', async ({
  canvas,
  page,
}) => {
  await mountPcmHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const canonical = await window.__importModule<DeviceAwarePcmModule>(
      '/static/generated/canvas/device-aware-pcm.js',
    );

    const events: string[] = [];
    const dataLengths: number[] = [];
    await canonical.listenForSpeech({
      sampleRate: 16000,
      vadThreshold: 0.05,
      vadMinRecordingTime: 180,
      vadSilenceDuration: 600,
      continuous: false,
      onSpeechStart: () => events.push('start'),
      onData: ({ pcm }) => dataLengths.push(pcm.byteLength),
      onSpeechEnd: () => events.push('end'),
    });

    const ctx = window.__micTestEnv.contexts[0];
    const worklet = ctx.worklets[0];
    // Below threshold while not speaking is ignored.
    worklet.emitPcm([10, 20], 0.01);
    // Above threshold starts speech.
    worklet.emitPcm([1000, -1000], 0.2);
    window.__micTestEnv.advanceNow(200);
    worklet.emitPcm([0, 0], 0.0);
    window.__micTestEnv.advanceNow(700);
    worklet.emitPcm([0, 0], 0.0);

    const afterVadEnd = {
      events: [...events],
      dataLengths: [...dataLengths],
      contextState: ctx.state,
      workletDisconnected: worklet.disconnected,
      sourceDisconnected: ctx.sources[0].disconnected,
      stoppedTracks: window.__micTestEnv.stoppedTrackCount,
    };

    // getUserMedia rejection invokes onError cleanly and rethrows without masking.
    window.__micTestEnv.setFailGetUserMedia(new Error('Device busy'));
    let onErrorMessage = '';
    let thrownMessage = '';
    try {
      await canonical.listenForSpeech({
        onData: () => {},
        onError: (err) => {
          onErrorMessage = err.message;
        },
      });
    } catch (err: unknown) {
      thrownMessage = err instanceof Error ? err.message : String(err);
    }

    return { afterVadEnd, onErrorMessage, thrownMessage };
  });

  expect(result.afterVadEnd).toEqual({
    events: ['start', 'end'],
    dataLengths: [4, 4, 4],
    contextState: 'closed',
    workletDisconnected: true,
    sourceDisconnected: true,
    stoppedTracks: 1,
  });
  expect(result.onErrorMessage).toBe('Device busy');
  expect(result.thrownMessage).toBe('Device busy');
});

test('canvas contains PCM microphone test controls that record 16 kHz PCM and play back signed 16-bit audio', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installBrowserMediaMocksScript() });
  await canvas.openCanvas({
    isOrator: true,
    apiRoutes: {
      '/api/theaters/stage/agent/status': { agent_running: false },
    },
  });

  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-mic-config').click();
  await expect(page.locator('#mic-config-modal')).toHaveClass(/active/);

  const recordBtn = page.locator('#menu-mic-test-record');
  const playBtn = page.locator('#menu-mic-test-play');
  const statusEl = page.locator('#menu-mic-test-status');

  await expect(recordBtn).toHaveAttribute('aria-pressed', 'false');
  await expect(playBtn).toBeDisabled();
  await expect(statusEl).toContainText('Record');

  // Wait until dynamic module import of device-aware-pcm.js finishes if still in flight,
  // then start PCM mic test recording.
  await expect(async () => {
    await recordBtn.click();
    await expect(recordBtn).toHaveAttribute('aria-pressed', 'true', { timeout: 500 });
  }).toPass({ timeout: 5000 });

  await expect(recordBtn).toHaveText('Stop recording');
  await expect(statusEl).toContainText('Recording from the selected microphone');

  // Emit quiet signed 16-bit LE samples (rms: 0) to confirm vadThreshold: 0 captures quiet audio.
  await page.evaluate(() => {
    const captureContext = window.__micTestEnv.contexts[0];
    captureContext.worklets[0].emitPcm([16384, -16384, 8192, -8192], 0);
  });

  // Stop recording and verify playback button becomes enabled.
  await recordBtn.click();
  await expect(recordBtn).toHaveAttribute('aria-pressed', 'false');
  await expect(recordBtn).toHaveText('Record test');
  await expect(playBtn).toBeEnabled();
  await expect(statusEl).toHaveText('Recording ready. Play it back to verify your microphone.');

  // Start playback and verify 16 kHz signed 16-bit PCM decoding and AudioBuffer playback.
  await playBtn.click();
  await expect(statusEl).toHaveText('Playing your PCM microphone test…');

  const playbackDetails = await page.evaluate(() => {
    const captureContext = window.__micTestEnv.contexts[0];
    const playbackContext = window.__micTestEnv.contexts[1];
    const buffer = playbackContext.playbackBuffers[0];
    const source = playbackContext.playbackSources[0];
    const decodedSamples = [...buffer.channelData];
    source.triggerEnded();
    return {
      captureSampleRate: captureContext.sampleRate,
      captureClosed: captureContext.state === 'closed',
      playbackSampleRate: playbackContext.sampleRate,
      playbackResumed: playbackContext.resumed,
      bufferLength: buffer.length,
      decodedSamples,
      sourceStarted: source.started,
      playbackClosedOnEnd: playbackContext.state === 'closed',
    };
  });

  expect(playbackDetails.captureSampleRate).toBe(16000);
  expect(playbackDetails.captureClosed).toBe(true);
  expect(playbackDetails.playbackSampleRate).toBe(16000);
  expect(playbackDetails.playbackResumed).toBe(true);
  expect(playbackDetails.bufferLength).toBe(4);
  expect(playbackDetails.decodedSamples).toEqual([0.5, -0.5, 0.25, -0.25]);
  expect(playbackDetails.sourceStarted).toBe(true);
  expect(playbackDetails.playbackClosedOnEnd).toBe(true);
  await expect(statusEl).toHaveText(
    'Playback finished. If you heard yourself clearly, your mic and PCM path are working.',
  );
});

test('first-time orator opens microphone configuration automatically after closing tutorial', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installBrowserMediaMocksScript() });
  await canvas.openCanvas({
    isOrator: true,
    skipDefaultLocalStorage: true,
    apiRoutes: {
      '/api/theaters/stage/agent/status': { agent_running: false },
    },
  });

  const howtoModal = page.locator('#orator-howto-modal');
  const micConfigModal = page.locator('#mic-config-modal');

  await expect(howtoModal).toHaveClass(/active/, { timeout: 3000 });
  await expect(howtoModal).toHaveAttribute('aria-hidden', 'false');
  await expect(micConfigModal).not.toHaveClass(/active/);

  await page.locator('#howto-ack-btn').click();

  await expect(howtoModal).not.toHaveClass(/active/);
  await expect(howtoModal).toHaveAttribute('aria-hidden', 'true');
  await expect(micConfigModal).toHaveClass(/active/);
  await expect(micConfigModal).toHaveAttribute('aria-hidden', 'false');
  expect(await page.evaluate(() => localStorage.getItem('narratron_orator_howto_seen'))).toBe(
    'true',
  );

  // Manually reopening and closing the tutorial does not re-open mic config.
  await page.locator('#mic-config-done-btn').click();
  await expect(micConfigModal).not.toHaveClass(/active/);

  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-howto').click();
  await expect(howtoModal).toHaveClass(/active/);

  await page.locator('#howto-ack-btn').click();
  await expect(howtoModal).not.toHaveClass(/active/);
  await expect(micConfigModal).not.toHaveClass(/active/);
});

test('enabling mic summons an inactive agent before opening WebSocket and starting capture', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installBrowserMediaMocksScript() });

  const timeline: string[] = [];
  let agentRunning = false;
  let agentSocket: WebSocketRoute | null = null;

  const { apiRequests } = await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      narratron_mic_mode: 'toggle',
    },
    apiRoutes: {
      '/api/theaters/stage/agent/status': async (route) => {
        await route.fulfill({ json: { agent_running: agentRunning } });
      },
      '/api/theaters/stage/agent/start': async (route) => {
        timeline.push('agent/start');
        agentRunning = true;
        await route.fulfill({ json: { ok: true, agent_running: true } });
      },
    },
    onWebSocket: (ws) => {
      const pathname = new URL(ws.url()).pathname;
      if (pathname === '/ws/stage/agent') {
        timeline.push('ws/agent');
        agentSocket = ws;
      }
    },
  });

  const micBtn = page.locator('#agent-connect-btn');
  await expect(micBtn).toHaveAttribute('aria-pressed', 'false');
  expect(timeline).toEqual([]);
  expect(agentSocket).toBeNull();

  await expect(async () => {
    await micBtn.click();
    await expect(micBtn).toHaveAttribute('aria-pressed', 'true', { timeout: 1500 });
  }).toPass({ timeout: 5000 });

  await expect(micBtn).toHaveText('🎙️ Mic Off');
  expect(timeline).toEqual(['agent/start', 'ws/agent']);
  expect(apiRequests.some((req) => req.method === 'POST' && req.pathname === '/api/theaters/stage/agent/start')).toBe(true);

  const captureState = await page.evaluate(() => ({
    contexts: window.__micTestEnv.contexts.length,
    encoders: window.__micTestEnv.encoderCount,
    closedEncoders: window.__micTestEnv.closedEncoderCount,
    stoppedTracksWhileActive: window.__micTestEnv.stoppedTrackCount,
  }));
  expect(captureState.contexts).toBe(1);
  expect(captureState.encoders).toBe(1);
  expect(captureState.closedEncoders).toBe(0);
  // refreshMicrophoneDevices(true) opens and immediately stops a permission-probe track.
  expect(captureState.stoppedTracksWhileActive).toBe(1);

  // Clicking again stops the mic capture cleanly.
  await micBtn.click();
  await expect(micBtn).toHaveAttribute('aria-pressed', 'false');
  await expect(micBtn).toHaveText('🎙️ Mic On');

  const stoppedState = await page.evaluate(async () => {
    await window.__micTestEnv.tick();
    return {
      closedEncoders: window.__micTestEnv.closedEncoderCount,
      stoppedTracks: window.__micTestEnv.stoppedTrackCount,
    };
  });
  expect(stoppedState.closedEncoders).toBe(1);
  expect(stoppedState.stoppedTracks).toBe(2);
});

test('compiled PCM audio worklets load and resample inside real AudioWorkletGlobalScope', async ({
  canvas,
  page,
}) => {
  await canvas.mount();
  const result = await page.evaluate(async () => {
    const { buildPcmWorkletSource } = await window.__importModule<PcmWorkletProcessorModule>(
      '/static/generated/canvas/pcm-worklet-processor.js',
    );
    const runWorklet = async (
      sourceCode: string,
      processorName: string,
      sampleRate: number,
      length: number,
    ): Promise<unknown[]> => {
      const offline = new OfflineAudioContext(1, length, sampleRate);
      const blobUrl = URL.createObjectURL(
        new Blob([sourceCode], { type: 'application/javascript' }),
      );
      try {
        await offline.audioWorklet.addModule(blobUrl);
      } finally {
        URL.revokeObjectURL(blobUrl);
      }
      const source = offline.createBufferSource();
      const buffer = offline.createBuffer(1, length, sampleRate);
      const channel = buffer.getChannelData(0);
      for (let i = 0; i < length; i++) {
        channel[i] = 0.5;
      }
      source.buffer = buffer;
      const node = new AudioWorkletNode(offline, processorName);
      const messages: unknown[] = [];
      node.port.onmessage = (event: MessageEvent<unknown>) => {
        messages.push(event.data);
      };
      source.connect(node);
      node.connect(offline.destination);
      source.start(0);
      await offline.startRendering();
      return messages;
    };

    const deviceMsgs = (await runWorklet(
      buildPcmWorkletSource('narratron-device-pcm', 16_000, 480, 'pcm-vad'),
      'narratron-device-pcm',
      48_000,
      1920,
    )) as Array<{ pcm: Uint8Array; float32: Float32Array; rms: number }>;

    const remoteMsgs = (await runWorklet(
      buildPcmWorkletSource('remote-pcm-processor', 16_000, 480, 'float32'),
      'remote-pcm-processor',
      48_000,
      1920,
    )) as Float32Array[];

    return {
      deviceCount: deviceMsgs.length,
      deviceFloatLen: deviceMsgs[0]?.float32.length ?? 0,
      devicePcmBytes: deviceMsgs[0]?.pcm.byteLength ?? 0,
      deviceRms: deviceMsgs[0]?.rms ?? 0,
      remoteCount: remoteMsgs.length,
      remoteFloatLen: remoteMsgs[0]?.length ?? 0,
      remoteSample0: remoteMsgs[0]?.[0] ?? 0,
    };
  });

  expect(result.deviceCount).toBe(1);
  expect(result.deviceFloatLen).toBe(480);
  expect(result.devicePcmBytes).toBe(960);
  expect(result.deviceRms).toBeCloseTo(0.5, 2);
  expect(result.remoteCount).toBe(1);
  expect(result.remoteFloatLen).toBe(480);
  expect(result.remoteSample0).toBeCloseTo(0.5, 2);
});

import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type { Page, WebSocketRoute } from '@playwright/test';

type AudioRecorderModule = typeof import('./audio-recorder.js');
type AgentSocket = import('./audio-recorder.js').AgentSocket;

interface MockAudioDataInit {
  readonly format: AudioSampleFormat;
  readonly sampleRate: number;
  readonly numberOfFrames: number;
  readonly numberOfChannels: number;
  readonly timestamp: number;
  readonly data: Float32Array;
}

interface MockEncodedChunk {
  readonly byteLength: number;
  copyTo(destination: Uint8Array): void;
}

interface MockEncoderCallbacks {
  output(chunk: MockEncodedChunk): void;
  error(error: DOMException): void;
}

interface MockEncoderHandle {
  readonly state: CodecState;
  readonly encoded: ReadonlyArray<{
    readonly timestamp: number;
    readonly sampleRate: number;
    readonly numberOfFrames: number;
    readonly numberOfChannels: number;
    readonly format: AudioSampleFormat;
  }>;
  readonly flushCount: number;
  emitOutput(bytes?: readonly number[]): void;
  resolveNextFlush(): void;
  rejectNextFlush(error: Error): void;
}

interface MockTrackHandle {
  readonly stopped: boolean;
}

interface MockMediaStreamHandle {
  readonly tracks: readonly MockTrackHandle[];
}

interface MockWorkletHandle {
  readonly connected: boolean;
  readonly disconnected: boolean;
  emitPacket(rms: number, fillValue?: number): void;
  emitSpeech(): void;
  emitSilence(): void;
}

interface MockSourceHandle {
  readonly connected: boolean;
  readonly disconnected: boolean;
}

interface MockAudioContextHandle {
  readonly sampleRate: number;
  readonly state: AudioContextState;
  readonly addedModuleUrls: readonly string[];
  readonly sources: readonly MockSourceHandle[];
  readonly worklets: readonly MockWorkletHandle[];
}

interface RecordedVadEvent {
  readonly type: string;
  readonly reason: string | null;
  readonly ts: string | null;
}

interface WireRecord {
  readonly kind: 'audio' | 'json';
  readonly type: string;
  readonly byteLength?: number;
  readonly bytes?: readonly number[];
  readonly reason?: string;
}

interface MockSocketHandle extends AgentSocket {
  readonly wire: readonly WireRecord[];
}

interface AudioRecorderTestHarness {
  readonly mod: AudioRecorderModule;
  readonly socket: MockSocketHandle;
  readonly encoders: readonly MockEncoderHandle[];
  readonly contexts: readonly MockAudioContextHandle[];
  readonly streams: readonly MockMediaStreamHandle[];
  readonly getUserMediaConstraints: readonly MediaStreamConstraints[];
  readonly vadEvents: readonly RecordedVadEvent[];
  readonly handler: (opusBuffer: ArrayBuffer) => void;
  now(): number;
  setNow(ms: number): void;
  advanceNow(ms: number): void;
  tick(): Promise<void>;
  createSocket(): MockSocketHandle;
  setPendingAddModule(): { resolve: () => void; reject: (error: Error) => void };
  setEmitBeforeConnectFinish(rms: number): void;
  disableAudioEncoder(): void;
}

declare global {
  interface Window {
    __audioHarness: AudioRecorderTestHarness;
  }
}

const CANONICAL_MODULE_URL = '/static/generated/canvas/audio-recorder.js';

async function mountAudioHarness(
  canvas: CanvasHarness,
  page: Page,
  options: { initialTimeMs?: number } = {},
): Promise<void> {
  await canvas.mount();
  await page.evaluate(
    async ({ canonicalUrl, initialTimeMs }) => {
      interface WorkletMessagePayload {
        readonly pcm: Uint8Array;
        readonly float32: Float32Array;
        readonly rms: number;
      }

      class MockTrack implements MockTrackHandle {
        stopped = false;
        stop(): void {
          this.stopped = true;
        }
      }

      class MockMediaStream implements MockMediaStreamHandle {
        readonly tracks: MockTrack[] = [new MockTrack()];
        getTracks(): MockTrack[] {
          return this.tracks;
        }
      }

      class MockMediaStreamAudioSourceNode implements MockSourceHandle {
        connected = false;
        disconnected = false;
        connect(_destination: MockAudioWorkletNode): void {
          this.connected = true;
        }
        disconnect(): void {
          this.disconnected = true;
        }
      }

      class MockAudioDestinationNode {}

      let emitOnConnectRms: number | null = null;

      class MockAudioWorkletNode implements MockWorkletHandle {
        connected = false;
        disconnected = false;
        readonly port: {
          onmessage: ((event: { data: WorkletMessagePayload }) => void) | null;
        } = { onmessage: null };

        constructor(_context: MockAudioContext, _name: string) {}

        connect(_destination: MockAudioDestinationNode): void {
          this.connected = true;
          if (emitOnConnectRms !== null) {
            const rms = emitOnConnectRms;
            emitOnConnectRms = null;
            this.emitPacket(rms, 0.25);
          }
        }

        disconnect(): void {
          this.disconnected = true;
        }

        emitPacket(rms: number, fillValue = rms): void {
          const float32 = new Float32Array(480).fill(fillValue);
          const pcm = new Uint8Array(float32.length * 2);
          this.port.onmessage?.({ data: { pcm, float32, rms } });
        }

        emitSpeech(): void {
          this.emitPacket(0.25, 0.25);
        }

        emitSilence(): void {
          this.emitPacket(0, 0);
        }
      }

      const contexts: MockAudioContext[] = [];
      let pendingAddModulePromise: Promise<void> | null = null;

      class MockAudioContext implements MockAudioContextHandle {
        readonly sampleRate: number;
        state: AudioContextState = 'running';
        readonly destination = new MockAudioDestinationNode();
        readonly addedModuleUrls: string[] = [];
        readonly sources: MockMediaStreamAudioSourceNode[] = [];
        readonly worklets: MockAudioWorkletNode[] = [];
        readonly audioWorklet: { addModule: (url: string) => Promise<void> };

        constructor(opts?: AudioContextOptions) {
          this.sampleRate = opts?.sampleRate ?? 48000;
          contexts.push(this);
          this.audioWorklet = {
            addModule: async (url: string): Promise<void> => {
              this.addedModuleUrls.push(url);
              if (pendingAddModulePromise) {
                const waitForModule = pendingAddModulePromise;
                pendingAddModulePromise = null;
                await waitForModule;
              }
            },
          };
        }

        createMediaStreamSource(_stream: MockMediaStream): MockMediaStreamAudioSourceNode {
          const source = new MockMediaStreamAudioSourceNode();
          this.sources.push(source);
          return source;
        }

        async close(): Promise<void> {
          this.state = 'closed';
        }
      }

      const encoders: MockAudioEncoder[] = [];

      class MockAudioEncoder implements MockEncoderHandle {
        state: CodecState = 'unconfigured';
        readonly encoded: Array<{
          readonly timestamp: number;
          readonly sampleRate: number;
          readonly numberOfFrames: number;
          readonly numberOfChannels: number;
          readonly format: AudioSampleFormat;
        }> = [];
        private readonly callbacks: MockEncoderCallbacks;
        private readonly flushes: Array<{
          resolve: () => void;
          reject: (error: Error) => void;
        }> = [];

        constructor(callbacks: MockEncoderCallbacks) {
          this.callbacks = callbacks;
          encoders.push(this);
        }

        get flushCount(): number {
          return this.flushes.length;
        }

        configure(_config: AudioEncoderConfig): void {
          this.state = 'configured';
        }

        encode(data: MockAudioData): void {
          this.encoded.push({
            timestamp: data.init.timestamp,
            sampleRate: data.init.sampleRate,
            numberOfFrames: data.init.numberOfFrames,
            numberOfChannels: data.init.numberOfChannels,
            format: data.init.format,
          });
        }

        flush(): Promise<void> {
          return new Promise<void>((resolve, reject) => {
            this.flushes.push({ resolve, reject });
          });
        }

        emitOutput(bytes: readonly number[] = [1, 2]): void {
          this.callbacks.output({
            byteLength: bytes.length,
            copyTo(destination: Uint8Array): void {
              destination.set(bytes);
            },
          });
        }

        resolveNextFlush(): void {
          const next = this.flushes.shift();
          if (!next) throw new Error('No pending AudioEncoder.flush() to resolve');
          next.resolve();
        }

        rejectNextFlush(error: Error): void {
          const next = this.flushes.shift();
          if (!next) throw new Error('No pending AudioEncoder.flush() to reject');
          next.reject(error);
        }

        close(): void {
          this.state = 'closed';
        }
      }

      class MockAudioData {
        readonly init: MockAudioDataInit;
        closed = false;
        constructor(init: MockAudioDataInit) {
          this.init = init;
        }
        close(): void {
          this.closed = true;
        }
      }

      let nowMs = initialTimeMs;
      Date.now = () => nowMs;

      const streams: MockMediaStream[] = [];
      const getUserMediaConstraints: MediaStreamConstraints[] = [];

      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: {
          getUserMedia: async (constraints: MediaStreamConstraints): Promise<MockMediaStream> => {
            getUserMediaConstraints.push(constraints);
            const stream = new MockMediaStream();
            streams.push(stream);
            return stream;
          },
        },
      });

      const originalAudioContext = MockAudioContext;
      const PatchedAudioWorkletNode = function (
        context: MockAudioContext,
        name: string,
      ): MockAudioWorkletNode {
        const node = new MockAudioWorkletNode(context, name);
        context.worklets.push(node);
        return node;
      };

      const win = window as unknown as {
        AudioContext: typeof originalAudioContext;
        AudioWorkletNode: typeof PatchedAudioWorkletNode;
        AudioEncoder?: typeof MockAudioEncoder;
        AudioData: typeof MockAudioData;
      };
      win.AudioContext = originalAudioContext;
      win.AudioWorkletNode = PatchedAudioWorkletNode;
      win.AudioEncoder = MockAudioEncoder;
      win.AudioData = MockAudioData;

      const createSocket = (): MockSocketHandle => {
        const wire: WireRecord[] = [];
        return {
          readyState: WebSocket.OPEN,
          wire,
          send(value: string | ArrayBufferLike | Blob | ArrayBufferView): void {
            if (typeof value === 'string') {
              const parsed = JSON.parse(value) as { type: string; reason?: string };
              wire.push({ kind: 'json', type: parsed.type, reason: parsed.reason });
              return;
            }
            if (value instanceof ArrayBuffer) {
              wire.push({
                kind: 'audio',
                type: 'audio',
                byteLength: value.byteLength,
                bytes: [...new Uint8Array(value)],
              });
              return;
            }
            if (ArrayBuffer.isView(value)) {
              const view = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
              wire.push({
                kind: 'audio',
                type: 'audio',
                byteLength: view.byteLength,
                bytes: [...view],
              });
            }
          },
        };
      };

      const socket = createSocket();
      window.agentWs = socket;

      const vadEvents: RecordedVadEvent[] = [];
      for (const name of [
        'vadstart',
        'vadstop',
        'narratron:vad-start',
        'narratron:vad-stop',
        'narratron:speech-start',
        'narratron:speech-end',
      ]) {
        window.addEventListener(name, (event: Event) => {
          const custom = event as CustomEvent<{ reason?: string; ts?: string } | undefined>;
          vadEvents.push({
            type: event.type,
            reason: custom.detail?.reason ?? null,
            ts: custom.detail?.ts ?? null,
          });
        });
      }

      const canonicalMod = await window.__importModule<AudioRecorderModule>(canonicalUrl);

      const handler = (opusBuffer: ArrayBuffer): void => {
        if (!window.agentWs || window.agentWs.readyState !== WebSocket.OPEN) return;
        window.agentWs.send(opusBuffer);
      };

      window.__audioHarness = {
        mod: canonicalMod,
        socket,
        encoders,
        contexts,
        streams,
        getUserMediaConstraints,
        vadEvents,
        handler,
        now: () => nowMs,
        setNow: (ms: number) => {
          nowMs = ms;
        },
        advanceNow: (ms: number) => {
          nowMs += ms;
        },
        tick: () => new Promise<void>((resolve) => window.setTimeout(resolve, 0)),
        createSocket,
        setPendingAddModule: () => {
          let resolveFn: () => void = () => {};
          let rejectFn: (error: Error) => void = () => {};
          pendingAddModulePromise = new Promise<void>((resolve, reject) => {
            resolveFn = resolve;
            rejectFn = reject;
          });
          return { resolve: resolveFn, reject: rejectFn };
        },
        setEmitBeforeConnectFinish: (rms: number) => {
          emitOnConnectRms = rms;
        },
        disableAudioEncoder: () => {
          win.AudioEncoder = undefined;
        },
      };
    },
    {
      canonicalUrl: CANONICAL_MODULE_URL,
      initialTimeMs: options.initialTimeMs ?? 1000,
    },
  );
}

test('new speech waits for preceding Opus output and end boundary', async ({ canvas, page }) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    const startRes = await h.mod.startAudioRecorderWorklet(h.handler);
    const worklet = h.contexts[0].worklets[0];

    // First utterance: loud frame at t=1000, then silence past minSpeech (250ms) and silence (1200ms).
    worklet.emitSpeech();
    h.advanceNow(300);
    worklet.emitSilence();
    h.advanceNow(1300);
    worklet.emitSilence();

    // Second utterance starts immediately while the first encoder flush is still pending.
    h.advanceNow(100);
    worklet.emitSpeech();
    await h.tick();

    const whileFlushingTypes = h.socket.wire.map((item) => item.type);
    const whileFlushingEncodedCount = h.encoders[0].encoded.length;
    const pendingFlushes = h.encoders[0].flushCount;

    // Complete first flush and emit Opus output before second utterance proceeds.
    h.encoders[0].emitOutput([1, 2]);
    h.encoders[0].resolveNextFlush();
    await h.tick();

    const afterFirstFlushTypes = h.socket.wire.map((item) => item.type);
    const encodedTimestamps = h.encoders[0].encoded.map((item) => item.timestamp);

    // Finish second utterance.
    h.advanceNow(300);
    worklet.emitSilence();
    h.advanceNow(1300);
    worklet.emitSilence();
    await h.tick();
    h.encoders[0].emitOutput([3, 4]);
    h.encoders[0].resolveNextFlush();
    await h.tick();

    const finalTypes = h.socket.wire.map((item) => item.type);
    const finalStop = h.mod.stopMicrophone();
    await h.tick();
    await finalStop;

    return {
      startRes,
      whileFlushingTypes,
      whileFlushingEncodedCount,
      pendingFlushes,
      afterFirstFlushTypes,
      encodedTimestamps,
      finalTypes,
    };
  });

  expect(result.startRes).toEqual([null, null, null, true]);
  expect(result.whileFlushingTypes).toEqual(['activity_start']);
  expect(result.whileFlushingEncodedCount).toBe(3);
  expect(result.pendingFlushes).toBe(1);
  expect(result.afterFirstFlushTypes).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
  ]);
  expect(result.encodedTimestamps).toEqual([0, 30000, 60000, 90000]);
  expect(result.finalTypes).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
    'audio',
    'activity_end',
  ]);
});

test('microphone restart drains old capture before starting another', async ({ canvas, page }) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    await h.mod.startAudioRecorderWorklet(h.handler);
    const oldContext = h.contexts[0];
    const oldWorklet = oldContext.worklets[0];
    oldWorklet.emitSpeech();
    await h.tick();

    const stopping = h.mod.stopMicrophone();
    const restarting = h.mod.startAudioRecorderWorklet(h.handler);
    // Late packets on old worklet must be ignored while draining.
    oldWorklet.emitSpeech();
    await h.tick();

    const contextCountBeforeDrain = h.contexts.length;
    h.encoders[0].emitOutput([5, 6]);
    h.encoders[0].resolveNextFlush();
    await stopping;
    await restarting;

    const newWorklet = h.contexts[1].worklets[0];
    newWorklet.emitSpeech();
    await h.tick();

    const typesAfterRestart = h.socket.wire.map((item) => item.type);
    const oldEncoderState = h.encoders[0].state;
    const oldEncodedCount = h.encoders[0].encoded.length;
    const oldContextState = oldContext.state;
    const oldTrackStopped = h.streams[0].tracks[0].stopped;

    // Late encoder output or worklet silence on old capture cannot affect wire.
    h.encoders[0].emitOutput([7, 8]);
    oldWorklet.emitSilence();
    await h.tick();

    const typesAfterLateOldEvents = h.socket.wire.map((item) => item.type);

    const finalStop = h.mod.stopMicrophone();
    await h.tick();
    h.encoders[1].resolveNextFlush();
    await finalStop;

    return {
      contextCountBeforeDrain,
      contextCountAfterDrain: h.contexts.length,
      typesAfterRestart,
      oldEncoderState,
      oldEncodedCount,
      oldContextState,
      oldTrackStopped,
      typesAfterLateOldEvents,
    };
  });

  expect(result.contextCountBeforeDrain).toBe(1);
  expect(result.contextCountAfterDrain).toBe(2);
  expect(result.typesAfterRestart).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
  ]);
  expect(result.oldEncoderState).toBe('closed');
  expect(result.oldEncodedCount).toBe(1);
  expect(result.oldContextState).toBe('closed');
  expect(result.oldTrackStopped).toBe(true);
  expect(result.typesAfterLateOldEvents).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
  ]);
});

test('push to talk mutes delivery while retaining the capture for the next press', async ({
  canvas,
  page,
}) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    await h.mod.startAudioRecorderWorklet(h.handler, false);
    const worklet = h.contexts[0].worklets[0];

    worklet.emitSpeech();
    await h.tick();
    const mutedWire = [...h.socket.wire];

    h.mod.setMicrophoneTransmissionEnabled(true);
    // VAD is already speaking, so no second onSpeechStart is required.
    worklet.emitSpeech();
    await h.tick();
    const afterFirstPressTypes = h.socket.wire.map((item) => item.type);
    const encodedAfterFirstPress = h.encoders[0].encoded.length;

    h.mod.setMicrophoneTransmissionEnabled(false);
    worklet.emitSpeech();
    await h.tick();
    h.encoders[0].emitOutput([1, 2]);
    h.encoders[0].resolveNextFlush();
    await h.tick();

    const afterReleaseTypes = h.socket.wire.map((item) => item.type);
    const encodedAfterRelease = h.encoders[0].encoded.length;
    const contextCountAfterRelease = h.contexts.length;
    const encoderStateAfterRelease = h.encoders[0].state;

    h.mod.setMicrophoneTransmissionEnabled(true);
    worklet.emitSpeech();
    await h.tick();
    const afterSecondPressTypes = h.socket.wire.map((item) => item.type);
    const encodedAfterSecondPress = h.encoders[0].encoded.length;

    h.advanceNow(300);
    worklet.emitSilence();
    h.advanceNow(1300);
    worklet.emitSilence();
    await h.tick();
    h.encoders[0].emitOutput([3, 4]);
    h.encoders[0].resolveNextFlush();
    await h.tick();

    const finalTypes = h.socket.wire.map((item) => item.type);
    await h.mod.stopMicrophone();

    return {
      mutedWire,
      afterFirstPressTypes,
      encodedAfterFirstPress,
      afterReleaseTypes,
      encodedAfterRelease,
      contextCountAfterRelease,
      encoderStateAfterRelease,
      afterSecondPressTypes,
      encodedAfterSecondPress,
      finalTypes,
    };
  });

  expect(result.mutedWire).toEqual([]);
  expect(result.afterFirstPressTypes).toEqual(['activity_start']);
  expect(result.encodedAfterFirstPress).toBe(1);
  expect(result.afterReleaseTypes).toEqual(['activity_start', 'audio', 'activity_end']);
  expect(result.encodedAfterRelease).toBe(1);
  expect(result.contextCountAfterRelease).toBe(1);
  expect(result.encoderStateAfterRelease).toBe('configured');
  expect(result.afterSecondPressTypes).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
  ]);
  expect(result.encodedAfterSecondPress).toBe(2);
  expect(result.finalTypes).toEqual([
    'activity_start',
    'audio',
    'activity_end',
    'activity_start',
    'audio',
    'activity_end',
  ]);
});

test('old capture cannot deliver packets or end into replacement socket', async ({
  canvas,
  page,
}) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    await h.mod.startAudioRecorderWorklet(h.handler);
    const worklet = h.contexts[0].worklets[0];
    worklet.emitSpeech();
    await h.tick();

    const stopping = h.mod.stopMicrophone();
    const replacementSocket = h.createSocket();
    window.agentWs = replacementSocket;
    await h.tick();

    h.encoders[0].emitOutput([9, 10]);
    h.encoders[0].resolveNextFlush();
    await stopping;

    return {
      originalTypes: h.socket.wire.map((item) => item.type),
      replacementWire: replacementSocket.wire,
    };
  });

  expect(result.originalTypes).toEqual(['activity_start']);
  expect(result.replacementWire).toEqual([]);
});

test('speech captured before initialization returns delivers its first packet', async ({
  canvas,
  page,
}) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    // Simulate canvas.js keeping its UI recording flag false until startAudioRecorderWorklet resolves.
    let isRecording = false;
    const canvasLikeHandler = (opusBuffer: ArrayBuffer): void => {
      void isRecording;
      if (!window.agentWs || window.agentWs.readyState !== WebSocket.OPEN) return;
      window.agentWs.send(opusBuffer);
    };

    h.setEmitBeforeConnectFinish(0.25);
    const startRes = await h.mod.startAudioRecorderWorklet(canvasLikeHandler);
    isRecording = true;
    await h.tick();

    h.encoders[0].emitOutput([11, 12]);
    const wireSnapshot = [...h.socket.wire];
    const encodedCount = h.encoders[0].encoded.length;

    const stopping = h.mod.stopMicrophone();
    await h.tick();
    h.encoders[0].resolveNextFlush();
    await stopping;

    return {
      startRes,
      encodedCount,
      wireSnapshot,
    };
  });

  expect(result.startRes).toEqual([null, null, null, true]);
  expect(result.encodedCount).toBe(1);
  expect(result.wireSnapshot).toHaveLength(2);
  expect(result.wireSnapshot[0]).toMatchObject({ kind: 'json', type: 'activity_start' });
  expect(result.wireSnapshot[1]).toEqual({
    kind: 'audio',
    type: 'audio',
    byteLength: 2,
    bytes: [11, 12],
  });
});

test('stopping pending microphone initialization releases the late capture', async ({
  canvas,
  page,
}) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;
    const gate = h.setPendingAddModule();
    const starting = h.mod.startAudioRecorderWorklet(h.handler);
    await h.tick();

    await h.mod.stopMicrophone();
    h.setEmitBeforeConnectFinish(0.25);
    gate.resolve();

    const startRes = await starting;
    await h.tick();

    const context = h.contexts[0];
    const worklet = context.worklets[0];
    const source = context.sources[0];

    return {
      startRes,
      trackStopped: h.streams[0].tracks[0].stopped,
      contextState: context.state,
      workletDisconnected: worklet.disconnected,
      sourceDisconnected: source.disconnected,
      encoderState: h.encoders[0].state,
      wire: h.socket.wire,
    };
  });

  expect(result.startRes).toEqual([null, null, null, false]);
  expect(result.trackStopped).toBe(true);
  expect(result.contextState).toBe('closed');
  expect(result.workletDisconnected).toBe(true);
  expect(result.sourceDisconnected).toBe(true);
  expect(result.encoderState).toBe('closed');
  expect(result.wire).toEqual([]);
});

test('forwards audio over real WebSocket and flushes on stop', async ({
  canvas,
  page,
}) => {
  const serverMessages: Array<string | Buffer> = [];
  await page.routeWebSocket(/wss:\/\/canvas\.test\/ws\/agent/, (ws: WebSocketRoute) => {
    ws.onMessage((message) => {
      serverMessages.push(message);
    });
  });

  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;

    const realWs = new WebSocket('wss://canvas.test/ws/agent');
    await new Promise<void>((resolve, reject) => {
      realWs.addEventListener('open', () => resolve(), { once: true });
      realWs.addEventListener('error', () => reject(new Error('WebSocket failed to open')), {
        once: true,
      });
    });
    window.agentWs = realWs;

    await h.mod.startAudioRecorderWorklet(h.handler);
    h.contexts[0].worklets[0].emitSpeech();
    await h.tick();
    h.encoders[0].emitOutput([42, 99]);

    const stopping = h.mod.stopMicrophone();
    await h.tick();
    h.encoders[0].resolveNextFlush();
    await stopping;
    await h.tick();
    realWs.close();

    return {
      vadEventNames: h.vadEvents.map((event) => event.type),
    };
  });

  expect(result.vadEventNames).toEqual([
    'vadstart',
    'narratron:vad-start',
    'narratron:speech-start',
    'vadstop',
    'narratron:vad-stop',
    'narratron:speech-end',
  ]);
  expect(serverMessages).toHaveLength(3);
  expect(JSON.parse(String(serverMessages[0]))).toMatchObject({
    type: 'activity_start',
    reason: 'speech_start',
  });
  expect(Buffer.isBuffer(serverMessages[1])).toBe(true);
  expect([...(serverMessages[1] as Buffer)]).toEqual([42, 99]);
  expect(JSON.parse(String(serverMessages[2]))).toMatchObject({
    type: 'activity_end',
    reason: 'speech_end',
  });
});

test('forwards deviceId, applies MIC_DETECT_THRESHOLD, and handles missing AudioEncoder or startup errors', async ({
  canvas,
  page,
}) => {
  await mountAudioHarness(canvas, page);
  const result = await page.evaluate(async () => {
    const h = window.__audioHarness;

    window.NARRATRON_MIC_DEVICE_ID = 'usb-mic-123';
    window.MIC_DETECT_THRESHOLD = 0.5;
    await h.mod.startAudioRecorderWorklet(h.handler);
    const worklet = h.contexts[0].worklets[0];

    // Below 0.5 threshold: ignored by VAD.
    worklet.emitPacket(0.2);
    await h.tick();
    const belowThresholdWire = [...h.socket.wire];

    // Above 0.5 threshold: triggers speech_start.
    worklet.emitPacket(0.6);
    await h.tick();
    const aboveThresholdTypes = h.socket.wire.map((item) => item.type);

    const stopping = h.mod.stopMicrophone();
    await h.tick();
    h.encoders[0].resolveNextFlush();
    await stopping;

    // Startup error in audioWorklet.addModule closes the encoder and rethrows.
    const gate = h.setPendingAddModule();
    const failingStart = h.mod.startAudioRecorderWorklet(h.handler);
    await h.tick();
    gate.reject(new Error('Worklet load failed'));
    let startupErrorMessage = '';
    try {
      await failingStart;
    } catch (error: unknown) {
      startupErrorMessage = error instanceof Error ? error.message : String(error);
    }
    const encoderClosedOnStartupError = h.encoders[1].state === 'closed';

    // Missing AudioEncoder surfaces browser upgrade message.
    h.disableAudioEncoder();
    let missingEncoderMessage = '';
    try {
      await h.mod.startAudioRecorderWorklet(h.handler);
    } catch (error: unknown) {
      missingEncoderMessage = error instanceof Error ? error.message : String(error);
    }

    return {
      firstConstraints: h.getUserMediaConstraints[0],
      belowThresholdWire,
      aboveThresholdTypes,
      startupErrorMessage,
      encoderClosedOnStartupError,
      missingEncoderMessage,
    };
  });

  expect(result.firstConstraints).toMatchObject({
    audio: {
      deviceId: { exact: 'usb-mic-123' },
      channelCount: 1,
      sampleRate: { ideal: 16000 },
    },
  });
  expect(result.belowThresholdWire).toEqual([]);
  expect(result.aboveThresholdTypes).toEqual(['activity_start']);
  expect(result.startupErrorMessage).toBe('Worklet load failed');
  expect(result.encoderClosedOnStartupError).toBe(true);
  expect(result.missingEncoderMessage).toBe(
    'Your browser is out of date and needs Opus audio support. Please update your browser and try again.',
  );
});

import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';
import {
  CANVAS_ORIGIN,
  REPO_ROOT,
  expect,
  test,
  type CanvasHarness,
} from '../../testing/ui/canvas_fixtures';

type TestlabAudioVadModule = typeof import('./testlab-audio-vad.js');

interface MockDeviceSpec {
  readonly kind: string;
  readonly deviceId: string;
  readonly label: string;
}

interface MockTrackRecord {
  readonly stopped: boolean;
}

interface MockVadWorkletRecord {
  readonly disconnected: boolean;
  emitSamples(samples: readonly number[], rms: number): void;
}

interface MockVadContextRecord {
  readonly sampleRate: number;
  readonly state: AudioContextState;
  readonly worklets: readonly MockVadWorkletRecord[];
  readonly sources: readonly { readonly disconnected: boolean }[];
}

interface MockVadLabEnv {
  readonly contexts: readonly MockVadContextRecord[];
  readonly tracks: readonly MockTrackRecord[];
  readonly getUserMediaCalls: readonly MediaStreamConstraints[];
  readonly revokedUrls: readonly string[];
  setDevices(nextDevices: readonly MockDeviceSpec[]): void;
  setFailGetUserMedia(message: string | null): void;
  setFailEnumerateDevices(message: string | null): void;
  disableGetUserMedia(): void;
  advanceNow(ms: number): void;
  dispatchDeviceChange(): void;
}

declare global {
  interface Window {
    __vadLabEnv: MockVadLabEnv;
  }
}

const VAD_HTML_PATH = path.join(REPO_ROOT, 'testlab', 'audio_vad_lab.html');
const VAD_SHIM_PATH = path.join(REPO_ROOT, 'testlab', 'static', 'audio-vad-lab.js');

function installVadLabMocksScript(): string {
  return `(() => {
    const contexts = [];
    const tracks = [];
    const getUserMediaCalls = [];
    const revokedUrls = [];
    const deviceChangeListeners = new Set();
    let nowMs = 10_000;
    let devices = [
      { kind: 'audioinput', deviceId: 'mic-usb', label: '' },
      { kind: 'audioinput', deviceId: 'mic-desk', label: '' },
      { kind: 'audiooutput', deviceId: 'speaker-1', label: 'Speakers' },
    ];
    let failGetUserMediaMessage = null;
    let failEnumerateMessage = null;
    let getUserMediaDisabled = false;

    Date.now = () => nowMs;

    const originalRevoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url) => {
      revokedUrls.push(String(url));
      originalRevoke(url);
    };

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
      emitSamples(samples, rms) {
        const int16 = new Int16Array(samples);
        this.port.onmessage?.({
          data: {
            pcm: new Uint8Array(int16.buffer),
            rms,
          },
        });
      }
    }

    class MockAudioContext {
      constructor(opts = {}) {
        this.sampleRate = opts.sampleRate ?? 48_000;
        this.state = 'running';
        this.destination = {};
        this.worklets = [];
        this.sources = [];
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
      async close() {
        this.state = 'closed';
      }
    }

    const mediaDevicesMock = {
      enumerateDevices: async () => {
        if (failEnumerateMessage) {
          const err = new Error(failEnumerateMessage);
          failEnumerateMessage = null;
          throw err;
        }
        return devices.map((device) => ({ ...device }));
      },
      getUserMedia: async (constraints) => {
        getUserMediaCalls.push(constraints);
        if (failGetUserMediaMessage) {
          const err = new Error(failGetUserMediaMessage);
          failGetUserMediaMessage = null;
          throw err;
        }
        return new MockMediaStream();
      },
      addEventListener: (type, listener) => {
        if (type === 'devicechange' && typeof listener === 'function') {
          deviceChangeListeners.add(listener);
        }
      },
      removeEventListener: (type, listener) => {
        if (type === 'devicechange') {
          deviceChangeListeners.delete(listener);
        }
      },
    };

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: mediaDevicesMock,
    });

    window.AudioContext = MockAudioContext;
    window.AudioWorkletNode = function () {
      const node = new MockAudioWorkletNode();
      const latest = contexts[contexts.length - 1];
      if (latest) latest.worklets.push(node);
      return node;
    };

    window.__vadLabEnv = {
      contexts,
      tracks,
      getUserMediaCalls,
      revokedUrls,
      setDevices(nextDevices) {
        devices = nextDevices.map((device) => ({ ...device }));
      },
      setFailGetUserMedia(message) {
        failGetUserMediaMessage = message;
      },
      setFailEnumerateDevices(message) {
        failEnumerateMessage = message;
      },
      disableGetUserMedia() {
        getUserMediaDisabled = true;
        Object.defineProperty(navigator, 'mediaDevices', {
          configurable: true,
          value: getUserMediaDisabled ? {} : mediaDevicesMock,
        });
      },
      advanceNow(ms) {
        nowMs += ms;
      },
      dispatchDeviceChange() {
        for (const listener of deviceChangeListeners) {
          listener(new Event('devicechange'));
        }
      },
    };
  })();`;
}

async function openVadLabPage(canvas: CanvasHarness, page: Page): Promise<void> {
  const html = await fs.promises.readFile(VAD_HTML_PATH, 'utf-8');
  const shimJs = await fs.promises.readFile(VAD_SHIM_PATH, 'utf-8');

  await page.addInitScript({ content: installVadLabMocksScript() });
  await canvas.serveRoute(
    (url: URL) =>
      url.origin === CANVAS_ORIGIN &&
      (url.pathname === '/vad' ||
        url.pathname === '/static/audio-vad-lab.js' ||
        url.pathname === '/favicon.ico'),
    async (route: Route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/vad') {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
        return;
      }
      if (pathname === '/static/audio-vad-lab.js') {
        await route.fulfill({
          contentType: 'text/javascript; charset=utf-8',
          body: shimJs,
        });
        return;
      }
      await route.fulfill({ status: 204 });
    },
  );

  await page.goto(`${CANVAS_ORIGIN}/vad`);
}

test('audio_vad_lab.html uses generated TS bundle, deprecated shim matches exports, and pure helpers encode clamped WAV', async ({
  canvas,
  page,
}) => {
  const html = await fs.promises.readFile(VAD_HTML_PATH, 'utf-8');
  expect(html).toContain('<script type="module" src="/static/generated/canvas/testlab-audio-vad.js"></script>');
  expect(html).not.toContain('src="/static/audio-vad-lab.js"');

  const shimJs = await fs.promises.readFile(VAD_SHIM_PATH, 'utf-8');
  await canvas.serveRoute(`${CANVAS_ORIGIN}/static/audio-vad-lab.js`, async (route: Route) => {
    await route.fulfill({
      contentType: 'text/javascript; charset=utf-8',
      body: shimJs,
    });
  });
  await canvas.mount({ html: '<div id="empty"></div>' });

  const result = await page.evaluate(async () => {
    const canonical = await window.__importModule<TestlabAudioVadModule>(
      '/static/generated/canvas/testlab-audio-vad.js',
    );
    const shim = await window.__importModule<TestlabAudioVadModule>(
      '/static/audio-vad-lab.js',
    );

    const sameIdentity =
      canonical.SAMPLE_RATE === shim.SAMPLE_RATE &&
      canonical.initAudioVadLab === shim.initAudioVadLab &&
      canonical.listenForSpeechWithDevice === shim.listenForSpeechWithDevice &&
      canonical.pcmToWav === shim.pcmToWav &&
      canonical.thresholdFromSensitivity === shim.thresholdFromSensitivity;

    const initOnEmptyDom = canonical.initAudioVadLab();

    const thresholds = {
      low: canonical.thresholdFromSensitivity(0),
      mid: canonical.thresholdFromSensitivity(50),
      high: canonical.thresholdFromSensitivity(100),
    };

    const chunk1 = new Int16Array([1000, -1000]).buffer;
    const chunk2 = new Int16Array([20_000, -20_000]).buffer;
    // +12 dB ~= 3.981x gain: 1000 -> ~3981, 20000 -> clamped to 32767 / -32768.
    const wavBlob = canonical.pcmToWav([chunk1, chunk2], 12);
    const wavBuf = await wavBlob.arrayBuffer();
    const view = new DataView(wavBuf);
    const readAscii = (offset: number, len: number): string =>
      Array.from({ length: len }, (_, i) => String.fromCharCode(view.getUint8(offset + i))).join('');

    const samples = [
      view.getInt16(44, true),
      view.getInt16(46, true),
      view.getInt16(48, true),
      view.getInt16(50, true),
    ];

    return {
      sameIdentity,
      initOnEmptyDom,
      thresholds,
      mimeType: wavBlob.type,
      riff: readAscii(0, 4),
      waveFmt: readAscii(8, 8),
      dataHeader: readAscii(36, 4),
      fileSizeMinus8: view.getUint32(4, true),
      channels: view.getUint16(22, true),
      sampleRate: view.getUint32(24, true),
      bitsPerSample: view.getUint16(34, true),
      dataBytes: view.getUint32(40, true),
      samples,
    };
  });

  expect(result.sameIdentity).toBe(true);
  expect(result.initOnEmptyDom).toBeNull();
  expect(result.thresholds.low).toBeCloseTo(0.1, 6);
  expect(result.thresholds.mid).toBeCloseTo(0.01, 6);
  expect(result.thresholds.high).toBeCloseTo(0.001, 6);
  expect(result.mimeType).toBe('audio/wav');
  expect(result.riff).toBe('RIFF');
  expect(result.waveFmt).toBe('WAVEfmt ');
  expect(result.dataHeader).toBe('data');
  expect(result.fileSizeMinus8).toBe(44);
  expect(result.channels).toBe(1);
  expect(result.sampleRate).toBe(16_000);
  expect(result.bitsPerSample).toBe(16);
  expect(result.dataBytes).toBe(8);
  expect(result.samples[0]).toBeGreaterThan(3900);
  expect(result.samples[0]).toBeLessThan(4050);
  expect(result.samples[1]).toBe(-result.samples[0]);
  expect(result.samples[2]).toBe(32_767);
  expect(result.samples[3]).toBe(-32_768);
});

test('Audio VAD Lab initializes labels, refreshes microphones with permission, handles devicechange, and reports list errors', async ({
  canvas,
  page,
}) => {
  await openVadLabPage(canvas, page);

  await expect(page.locator('#sensitivity-value')).toHaveText('50% (RMS 0.0100)');
  await expect(page.locator('#speech-duration-value')).toHaveText('180 ms');
  await expect(page.locator('#silence-duration-value')).toHaveText('600 ms');
  await expect(page.locator('#playback-gain-value')).toHaveText('+12 dB');

  // Unlabeled audioinput devices appear with permission prompt hint.
  await expect(page.locator('#microphone option')).toHaveCount(3);
  await expect(page.locator('#microphone option').nth(1)).toHaveText(
    'Microphone 1 (allow access to identify)',
  );
  await expect(page.locator('#microphone-hint')).toHaveText(
    'Click Refresh devices and allow microphone access to identify each microphone.',
  );

  // Select second microphone and verify change logs selection.
  await page.locator('#microphone').selectOption('mic-desk');
  await expect(page.locator('#log')).toContainText(
    'Selected microphone: Microphone 2 (allow access to identify).',
  );

  // Refresh devices requests permission, stops probe track, updates labels, and preserves selection.
  await page.evaluate(() => {
    window.__vadLabEnv.setDevices([
      { kind: 'audioinput', deviceId: 'mic-usb', label: 'USB Podcaster' },
      { kind: 'audioinput', deviceId: 'mic-desk', label: 'Desk Condenser' },
    ]);
  });
  await page.locator('#refresh-microphones').click();

  await expect(page.locator('#microphone option').nth(1)).toHaveText('USB Podcaster');
  await expect(page.locator('#microphone option').nth(2)).toHaveText('Desk Condenser');
  await expect(page.locator('#microphone')).toHaveValue('mic-desk');
  await expect(page.locator('#microphone-hint')).toHaveText(
    '2 microphones available. The selection is used for the next recording.',
  );

  const probeInfo = await page.evaluate(() => ({
    calls: window.__vadLabEnv.getUserMediaCalls,
    stoppedCount: window.__vadLabEnv.tracks.filter((t) => t.stopped).length,
  }));
  expect(probeInfo.calls).toEqual([{ audio: true }]);
  expect(probeInfo.stoppedCount).toBe(1);

  // devicechange removes selected device -> falls back to default empty value and singular hint.
  await page.evaluate(() => {
    window.__vadLabEnv.setDevices([
      { kind: 'audioinput', deviceId: 'mic-usb', label: 'USB Podcaster' },
    ]);
    window.__vadLabEnv.dispatchDeviceChange();
  });
  await expect(page.locator('#microphone option')).toHaveCount(2);
  await expect(page.locator('#microphone')).toHaveValue('');
  await expect(page.locator('#microphone-hint')).toHaveText(
    '1 microphone available. The selection is used for the next recording.',
  );

  // enumerateDevices error updates #status with .error class.
  await page.evaluate(() => {
    window.__vadLabEnv.setFailEnumerateDevices('Permission blocked');
    window.__vadLabEnv.dispatchDeviceChange();
  });
  await expect(page.locator('#status')).toHaveText('Unable to list microphones: Permission blocked');
  await expect(page.locator('#status')).toHaveClass(/error/);
});

test('Audio VAD Lab filters speech segments, adjusts playback gain live, and clears playback', async ({
  canvas,
  page,
}) => {
  await openVadLabPage(canvas, page);
  await page.evaluate(() => {
    window.__vadLabEnv.setDevices([
      { kind: 'audioinput', deviceId: 'mic-studio', label: 'Studio XLR' },
    ]);
    window.__vadLabEnv.dispatchDeviceChange();
  });
  await expect(page.locator('#microphone option')).toHaveCount(2);
  await page.locator('#microphone').selectOption('mic-studio');

  // Set 0 dB playback gain initially so WAV sample values are directly inspectable.
  await page.locator('#playback-gain').fill('0');
  await page.locator('#playback-gain').dispatchEvent('input');
  await expect(page.locator('#playback-gain-value')).toHaveText('+0 dB');

  await page.locator('#record-button').click();
  await expect(page.locator('#record-button')).toHaveText('Stop and prepare playback');
  await expect(page.locator('#record-button')).toHaveClass(/recording/);
  await expect(page.locator('#sensitivity')).toBeDisabled();
  await expect(page.locator('#speech-duration')).toBeDisabled();
  await expect(page.locator('#silence-duration')).toBeDisabled();
  await expect(page.locator('#microphone')).toBeDisabled();
  await expect(page.locator('#refresh-microphones')).toBeDisabled();
  await expect(page.locator('#status')).toContainText('Listening on Studio XLR');

  const mediaCall = await page.evaluate(() => window.__vadLabEnv.getUserMediaCalls[0]);
  expect(mediaCall).toEqual({
    audio: {
      deviceId: { exact: 'mic-studio' },
      channelCount: 1,
      sampleRate: { ideal: 16_000 },
      echoCancellation: true,
      noiseSuppression: true,
    },
  });

  // Drive VAD: initial silence discarded -> speech burst 1 -> silence past threshold -> discarded silence -> speech burst 2.
  await page.evaluate(() => {
    const worklet = window.__vadLabEnv.contexts[0].worklets[0];
    // Pre-speech silence (ignored)
    worklet.emitSamples([99, 99], 0.002);
    // Speech burst 1 (retained)
    worklet.emitSamples([1000, -1000], 0.05);
    window.__vadLabEnv.advanceNow(200);
    // Trailing silence inside segment until duration > 600ms (retained while speaking=true)
    worklet.emitSamples([10, -10], 0.001);
    window.__vadLabEnv.advanceNow(700);
    worklet.emitSamples([20, -20], 0.001);
    // Silence after speech end (discarded)
    worklet.emitSamples([888, -888], 0.001);
    // Speech burst 2 (retained)
    worklet.emitSamples([2000, -2000], 0.08);
  });

  await expect(page.locator('#log')).toContainText('Speech start.');
  await expect(page.locator('#log')).toContainText('Speech end — subsequent silence is discarded.');

  // Stop recording and inspect WAV playback.
  await page.locator('#record-button').click();
  await expect(page.locator('#record-button')).toHaveText('Start microphone test');
  await expect(page.locator('#record-button')).not.toHaveClass(/recording/);
  await expect(page.locator('#sensitivity')).toBeEnabled();
  await expect(page.locator('#clear-button')).toBeEnabled();
  await expect(page.locator('#playback')).toBeVisible();
  await expect(page.locator('#status')).toContainText('VAD-filtered speech retained at +0 dB');
  await expect(page.locator('#log')).toContainText('Prepared');
  expect(
    await page.locator('#log .stop').allTextContents(),
  ).toHaveLength(2);

  const logCountAfterStop = await page.locator('#log > div').count();
  const decodedWav = await page.evaluate(async () => {
    const audioEl = document.querySelector('#playback') as HTMLAudioElement;
    const response = await fetch(audioEl.src);
    const buffer = await response.arrayBuffer();
    const view = new DataView(buffer);
    const sampleCount = (buffer.byteLength - 44) / 2;
    const samples: number[] = [];
    for (let i = 0; i < sampleCount; i += 1) {
      samples.push(view.getInt16(44 + i * 2, true));
    }
    const ctx = window.__vadLabEnv.contexts[0];
    return {
      src: audioEl.src,
      samples,
      contextState: ctx.state,
      workletDisconnected: ctx.worklets[0].disconnected,
      sourceDisconnected: ctx.sources[0].disconnected,
      allTracksStopped: window.__vadLabEnv.tracks.every((t) => t.stopped),
    };
  });

  expect(decodedWav.contextState).toBe('closed');
  expect(decodedWav.workletDisconnected).toBe(true);
  expect(decodedWav.sourceDisconnected).toBe(true);
  expect(decodedWav.allTracksStopped).toBe(true);
  // Pre-speech [99, 99] and mid-silence [888, -888] must be absent.
  expect(decodedWav.samples).toEqual([1000, -1000, 10, -10, 20, -20, 2000, -2000]);

  // Adjusting playback gain updates WAV URL & status without adding a log line.
  await page.locator('#playback-gain').fill('6');
  await page.locator('#playback-gain').dispatchEvent('input');
  await expect(page.locator('#playback-gain-value')).toHaveText('+6 dB');
  await expect(page.locator('#status')).toContainText('at +6 dB playback gain.');
  expect(await page.locator('#log > div').count()).toBe(logCountAfterStop);

  const amplifiedSrc = await page.locator('#playback').getAttribute('src');
  expect(amplifiedSrc).not.toBe(decodedWav.src);
  const revokedBeforeClear = await page.evaluate(() => window.__vadLabEnv.revokedUrls.length);
  expect(revokedBeforeClear).toBeGreaterThanOrEqual(1);

  // Clear recording resets playback, button, status, and revokes latest blob URL.
  await page.locator('#clear-button').click();
  await expect(page.locator('#playback')).toBeHidden();
  await expect(page.locator('#playback')).not.toHaveAttribute('src');
  await expect(page.locator('#clear-button')).toBeDisabled();
  await expect(page.locator('#status')).toHaveText(
    'Recording cleared. Choose settings, then start another microphone test.',
  );
  await expect(page.locator('#log')).toContainText('Playback cleared.');
});

test('Audio VAD Lab handles zero-speech stop, getUserMedia errors, missing mediaDevices, and beforeunload', async ({
  canvas,
  page,
}) => {
  await openVadLabPage(canvas, page);

  // 1. Stop when no speech was detected.
  await page.locator('#record-button').click();
  await expect(page.locator('#status')).toContainText('Listening on');
  await page.locator('#record-button').click();
  await expect(page.locator('#status')).toHaveText(
    'No speech was retained. Raise sensitivity or speak closer to the microphone.',
  );
  await expect(page.locator('#status')).toHaveClass(/error/);
  await expect(page.locator('#playback')).toBeHidden();
  await expect(page.locator('#log')).toContainText(
    'No PCM data was received while VAD reported speech.',
  );

  // 2. getUserMedia error during startRecording calls onError, restores controls, and preserves error status.
  await page.evaluate(() => {
    window.__vadLabEnv.setFailGetUserMedia('Hardware busy');
  });
  await page.locator('#record-button').click();
  await expect(page.locator('#status')).toHaveText('Microphone/VAD error: Hardware busy');
  await expect(page.locator('#status')).toHaveClass(/error/);
  await expect(page.locator('#record-button')).toHaveText('Start microphone test');
  await expect(page.locator('#sensitivity')).toBeEnabled();
  await expect(page.locator('#log')).toContainText('Error: Hardware busy');

  // 3. beforeunload stops active recording cleanly.
  await page.locator('#record-button').click();
  await expect(page.locator('#status')).toContainText('Listening on');
  await page.evaluate(() => {
    window.dispatchEvent(new Event('beforeunload'));
  });
  const afterUnload = await page.evaluate(() => {
    const latestCtx = window.__vadLabEnv.contexts[window.__vadLabEnv.contexts.length - 1];
    return {
      contextState: latestCtx.state,
      workletDisconnected: latestCtx.worklets[0].disconnected,
    };
  });
  expect(afterUnload).toEqual({
    contextState: 'closed',
    workletDisconnected: true,
  });

  // 4. Missing navigator.mediaDevices.getUserMedia shows HTTPS/localhost guidance.
  await openVadLabPage(canvas, page);
  await page.evaluate(() => {
    window.__vadLabEnv.disableGetUserMedia();
  });
  await page.locator('#record-button').click();
  await expect(page.locator('#status')).toHaveText(
    'This browser does not provide microphone capture. Use HTTPS or localhost.',
  );
  await expect(page.locator('#status')).toHaveClass(/error/);
});

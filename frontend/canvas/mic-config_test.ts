import { expect, test } from '../../testing/ui/canvas_fixtures';

type MicConfigModule = typeof import('/static/generated/canvas/mic-config.js');
type MicConfigController = import('/static/generated/canvas/mic-config.js').MicConfigController;
type MicMode = import('/static/generated/canvas/mic-config.js').MicMode;
type ListenForSpeechOptions =
  import('/static/generated/audio/device-aware-pcm.js').ListenForSpeechOptions;

interface MockAudioPlaybackRecord {
  readonly sampleRate: number;
  readonly samples: readonly number[];
  readonly resumed: boolean;
  readonly started: boolean;
  closed: boolean;
  finish(): void;
}

interface MicConfigHarnessWindow extends Window {
  micConfig?: MicConfigController;
  micModeState: MicMode;
  recordingActive: boolean;
  cancelMicCaptureCount: number;
  updateMicToggleCount: number;
  openMicConfigCount: number;
  closeMicConfigCount: number;
  invalidateAuthCount: number;
  listenCalls: Array<{
    readonly deviceId?: string;
    readonly sampleRate?: number;
    readonly vadThreshold?: number;
    readonly continuous?: boolean;
  }>;
  stoppedTestCaptureCount: number;
  stoppedPermissionTracks: number;
  mockDevices: Array<{ readonly kind: string; readonly deviceId: string; readonly label: string }>;
  failEnumerate: boolean;
  listenAvailable: boolean;
  emitDeviceChange: () => void;
  emitPcmSamples: (samples: readonly number[]) => void;
  audioPlaybacks: MockAudioPlaybackRecord[];
  NARRATRON_MIC_DEVICE_ID?: string;
  MIC_DETECT_THRESHOLD?: number | string;
  MIC_SENSITIVITY?: number;
}

const MIC_CONFIG_URL = '/static/generated/canvas/mic-config.js';

const MIC_CONFIG_SHELL_HTML = `
  <div id="orator-howto-modal" aria-hidden="true">
    <button id="howto-close-btn" type="button">Close</button>
    <button id="howto-ack-btn" type="button">Got it</button>
  </div>
  <button id="menu-item-mic-config" type="button">Mic Settings</button>
  <div id="mic-config-modal" aria-hidden="true">
    <button id="mic-config-close-btn" type="button">Close</button>
    <input type="checkbox" id="menu-mic-mode-toggle" />
    <select id="menu-mic-device-select"></select>
    <button id="menu-mic-device-refresh" type="button">Refresh</button>
    <span id="menu-mic-device-hint"></span>
    <button id="menu-mic-test-record" type="button" aria-pressed="false">Record test</button>
    <button id="menu-mic-test-play" type="button" disabled>Play recording</button>
    <span id="menu-mic-test-status"></span>
    <input type="range" id="menu-mic-sensitivity-slider" min="0" max="100" value="50" />
    <span id="menu-mic-sensitivity-val">50%</span>
    <button id="mic-config-done-btn" type="button">Done</button>
  </div>
`;

function installMicMocksScript(): string {
  return `
    window.micModeState = 'toggle';
    window.recordingActive = false;
    window.cancelMicCaptureCount = 0;
    window.updateMicToggleCount = 0;
    window.openMicConfigCount = 0;
    window.closeMicConfigCount = 0;
    window.invalidateAuthCount = 0;
    window.listenCalls = [];
    window.stoppedTestCaptureCount = 0;
    window.stoppedPermissionTracks = 0;
    window.mockDevices = [];
    window.failEnumerate = false;
    window.listenAvailable = true;
    window.audioPlaybacks = [];

    const deviceListeners = new Set();
    let currentOnData = null;

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: async () => {
          if (window.failEnumerate) {
            throw new Error('enumerate failed');
          }
          return window.mockDevices.slice();
        },
        getUserMedia: async () => ({
          getTracks: () => [
            {
              stop: () => {
                window.stoppedPermissionTracks += 1;
              },
            },
          ],
        }),
        addEventListener: (eventName, handler) => {
          if (eventName === 'devicechange') deviceListeners.add(handler);
        },
        removeEventListener: (eventName, handler) => {
          if (eventName === 'devicechange') deviceListeners.delete(handler);
        },
      },
    });

    window.emitDeviceChange = () => {
      for (const handler of deviceListeners) {
        handler(new Event('devicechange'));
      }
    };

    window.__mockListenForSpeech = async (options) => {
      window.listenCalls.push({
        deviceId: options.deviceId,
        sampleRate: options.sampleRate,
        vadThreshold: options.vadThreshold,
        continuous: options.continuous,
      });
      currentOnData = options.onData;
      return () => {
        window.stoppedTestCaptureCount += 1;
        currentOnData = null;
      };
    };

    window.emitPcmSamples = (samples) => {
      if (!currentOnData) return;
      const int16 = new Int16Array(samples.length);
      for (let i = 0; i < samples.length; i += 1) {
        int16[i] = samples[i];
      }
      currentOnData({
        pcm: new Uint8Array(int16.buffer),
        float32: new Float32Array(samples.length),
        rms: 0.25,
      });
    };

    window.__createMockAudioContext = (sampleRate) => {
      const record = {
        sampleRate,
        samples: [],
        resumed: false,
        started: false,
        closed: false,
        finish: () => {},
      };
      const context = {
        state: 'suspended',
        destination: {},
        createBuffer: (_channels, length) => {
          const channel = new Float32Array(length);
          return {
            getChannelData: () => channel,
          };
        },
        createBufferSource: () => {
          let onended = null;
          let attachedBuffer = null;
          const source = {
            set buffer(buf) {
              attachedBuffer = buf;
            },
            get buffer() {
              return attachedBuffer;
            },
            set onended(fn) {
              onended = fn;
            },
            get onended() {
              return onended;
            },
            connect: () => {},
            start: () => {
              record.started = true;
              if (attachedBuffer) {
                record.samples = Array.from(attachedBuffer.getChannelData(0));
              }
              record.finish = () => {
                if (onended) onended();
              };
            },
            stop: () => {},
          };
          return source;
        },
        resume: async () => {
          record.resumed = true;
          context.state = 'running';
        },
        close: async () => {
          context.state = 'closed';
          record.closed = true;
        },
      };
      window.audioPlaybacks.push(record);
      return context;
    };
  `;
}

test('mic config manages mode toggle, device enumeration, permission refresh, and storage errors', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MIC_CONFIG_SHELL_HTML,
    localStorage: {
      narratron_mic_mode: 'push-to-talk',
      narratron_mic_device_id: 'usb-mic-2',
    },
    moduleScripts: [installMicMocksScript()],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMicConfigController } = await window.__importModule<MicConfigModule>(moduleUrl);
    const win = window as unknown as MicConfigHarnessWindow & {
      __mockListenForSpeech: (opts: ListenForSpeechOptions) => Promise<() => void>;
      __createMockAudioContext: (sampleRate: number) => AudioContext;
    };
    win.micModeState = 'push-to-talk';
    win.mockDevices = [
      { kind: 'videoinput', deviceId: 'cam-1', label: 'Camera' },
      { kind: 'audioinput', deviceId: 'mic-1', label: '' },
      { kind: 'audioinput', deviceId: 'usb-mic-2', label: '' },
    ];

    win.micConfig = createMicConfigController({
      micModeToggle: document.getElementById('menu-mic-mode-toggle') as HTMLInputElement | null,
      micDeviceSelect: document.getElementById('menu-mic-device-select') as HTMLSelectElement | null,
      micDeviceRefresh: document.getElementById('menu-mic-device-refresh'),
      micDeviceHint: document.getElementById('menu-mic-device-hint'),
      micTestRecordBtn: document.getElementById('menu-mic-test-record') as HTMLButtonElement | null,
      micTestPlayBtn: document.getElementById('menu-mic-test-play') as HTMLButtonElement | null,
      micTestStatus: document.getElementById('menu-mic-test-status'),
      micSensSlider: document.getElementById('menu-mic-sensitivity-slider') as HTMLInputElement | null,
      micSensValDisplay: document.getElementById('menu-mic-sensitivity-val'),
      micConfigModal: document.getElementById('mic-config-modal'),
      menuItemMicConfig: document.getElementById('menu-item-mic-config'),
      micConfigCloseBtn: document.getElementById('mic-config-close-btn'),
      micConfigDoneBtn: document.getElementById('mic-config-done-btn'),
      oratorHowtoModal: document.getElementById('orator-howto-modal'),
      howtoCloseBtn: document.getElementById('howto-close-btn'),
      howtoAckBtn: document.getElementById('howto-ack-btn'),
      getMicMode: () => win.micModeState,
      setMicMode: (mode: MicMode) => {
        win.micModeState = mode;
      },
      isRecording: () => win.recordingActive,
      cancelMicCapture: () => {
        win.cancelMicCaptureCount += 1;
      },
      updateMicToggleUI: () => {
        win.updateMicToggleCount += 1;
      },
      getListenForSpeech: () => (win.listenAvailable ? win.__mockListenForSpeech : undefined),
      getAuthState: async () => null,
      invalidateAuthStateCache: () => {
        win.invalidateAuthCount += 1;
      },
      createAudioContext: win.__createMockAudioContext,
    });
  }, MIC_CONFIG_URL);

  // Initial state restores stored device, shows fallback label + permission hint, and syncs toggle.
  await expect(page.locator('#menu-mic-mode-toggle')).toBeChecked();
  await expect(page.locator('#menu-mic-device-select')).toHaveValue('usb-mic-2');
  await expect(page.locator('#menu-mic-device-hint')).toHaveText(
    'Refresh and allow microphone access to identify devices.',
  );
  const initialOptions = await page
    .locator('#menu-mic-device-select option')
    .evaluateAll((els) => els.map((el) => el.textContent));
  expect(initialOptions).toEqual([
    'System default microphone',
    'Microphone 1 (allow access to identify)',
    'Microphone 2 (allow access to identify)',
  ]);

  // Toggling mode cancels mic capture, updates mode, saves to localStorage, and updates UI.
  await page.locator('#menu-mic-mode-toggle').click();
  const modeAfterClick = await page.evaluate(() => {
    const win = window as unknown as MicConfigHarnessWindow;
    return {
      mode: win.micModeState,
      stored: localStorage.getItem('narratron_mic_mode'),
      cancels: win.cancelMicCaptureCount,
      uiUpdates: win.updateMicToggleCount,
    };
  });
  expect(modeAfterClick).toEqual({
    mode: 'toggle',
    stored: 'toggle',
    cancels: 1,
    uiUpdates: 2,
  });

  // Clicking refresh requests permission track, stops it, and displays device labels.
  await page.evaluate(() => {
    const win = window as unknown as MicConfigHarnessWindow;
    win.mockDevices = [
      { kind: 'audioinput', deviceId: 'mic-1', label: 'Desk Mic' },
      { kind: 'audioinput', deviceId: 'usb-mic-2', label: 'Studio USB Mic' },
    ];
  });
  await page.locator('#menu-mic-device-refresh').click();
  await expect(page.locator('#menu-mic-device-hint')).toHaveText(
    'Your selection is used when you connect.',
  );
  expect(
    await page.evaluate(
      () => (window as unknown as MicConfigHarnessWindow).stoppedPermissionTracks,
    ),
  ).toBe(1);

  // Changing select persists device id & updates window global.
  await page.locator('#menu-mic-device-select').selectOption('mic-1');
  await expect(page.locator('#menu-mic-device-hint')).toHaveText(
    'Saved in this browser. Reconnect to use this microphone.',
  );
  expect(
    await page.evaluate(() => ({
      globalId: (window as unknown as MicConfigHarnessWindow).NARRATRON_MIC_DEVICE_ID,
      storedId: localStorage.getItem('narratron_mic_device_id'),
    })),
  ).toEqual({ globalId: 'mic-1', storedId: 'mic-1' });

  // Enumeration failure on devicechange surfaces permission guidance.
  await page.evaluate(() => {
    const win = window as unknown as MicConfigHarnessWindow;
    win.failEnumerate = true;
    win.emitDeviceChange();
  });
  await expect(page.locator('#menu-mic-device-hint')).toHaveText(
    'Unable to list microphones. Check browser permissions.',
  );
});

test('mic config guards live capture, records and plays back PCM audio, and stops capture on modal close', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MIC_CONFIG_SHELL_HTML,
    localStorage: {
      narratron_mic_device_id: 'studio-mic',
    },
    moduleScripts: [installMicMocksScript()],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMicConfigController } = await window.__importModule<MicConfigModule>(moduleUrl);
    const win = window as unknown as MicConfigHarnessWindow & {
      __mockListenForSpeech: (opts: ListenForSpeechOptions) => Promise<() => void>;
      __createMockAudioContext: (sampleRate: number) => AudioContext;
    };
    win.mockDevices = [{ kind: 'audioinput', deviceId: 'studio-mic', label: 'Studio Mic' }];

    win.micConfig = createMicConfigController({
      micModeToggle: document.getElementById('menu-mic-mode-toggle') as HTMLInputElement | null,
      micDeviceSelect: document.getElementById('menu-mic-device-select') as HTMLSelectElement | null,
      micDeviceRefresh: document.getElementById('menu-mic-device-refresh'),
      micDeviceHint: document.getElementById('menu-mic-device-hint'),
      micTestRecordBtn: document.getElementById('menu-mic-test-record') as HTMLButtonElement | null,
      micTestPlayBtn: document.getElementById('menu-mic-test-play') as HTMLButtonElement | null,
      micTestStatus: document.getElementById('menu-mic-test-status'),
      micSensSlider: document.getElementById('menu-mic-sensitivity-slider') as HTMLInputElement | null,
      micSensValDisplay: document.getElementById('menu-mic-sensitivity-val'),
      micConfigModal: document.getElementById('mic-config-modal'),
      menuItemMicConfig: document.getElementById('menu-item-mic-config'),
      micConfigCloseBtn: document.getElementById('mic-config-close-btn'),
      micConfigDoneBtn: document.getElementById('mic-config-done-btn'),
      oratorHowtoModal: document.getElementById('orator-howto-modal'),
      howtoCloseBtn: document.getElementById('howto-close-btn'),
      howtoAckBtn: document.getElementById('howto-ack-btn'),
      getMicMode: () => win.micModeState,
      setMicMode: (mode: MicMode) => {
        win.micModeState = mode;
      },
      isRecording: () => win.recordingActive,
      cancelMicCapture: () => {
        win.cancelMicCaptureCount += 1;
      },
      updateMicToggleUI: () => {
        win.updateMicToggleCount += 1;
      },
      getListenForSpeech: () => (win.listenAvailable ? win.__mockListenForSpeech : undefined),
      getAuthState: async () => null,
      invalidateAuthStateCache: () => {
        win.invalidateAuthCount += 1;
      },
      onCloseMicConfig: () => {
        win.closeMicConfigCount += 1;
      },
      createAudioContext: win.__createMockAudioContext,
    });
  }, MIC_CONFIG_URL);

  // Guard 1: refuses test when live microphone is active.
  await page.evaluate(() => {
    (window as unknown as MicConfigHarnessWindow).recordingActive = true;
  });
  await page.locator('#menu-mic-test-record').click();
  await expect(page.locator('#menu-mic-test-status')).toHaveText(
    'Disconnect the live microphone before running a local mic test.',
  );

  // Guard 2: refuses test when listenForSpeech has not loaded yet.
  await page.evaluate(() => {
    const win = window as unknown as MicConfigHarnessWindow;
    win.recordingActive = false;
    win.listenAvailable = false;
  });
  await page.locator('#menu-mic-test-record').click();
  await expect(page.locator('#menu-mic-test-status')).toHaveText(
    'Audio capture is still loading. Please try again in a moment.',
  );

  // Successful capture + stop + PCM playback.
  await page.evaluate(() => {
    (window as unknown as MicConfigHarnessWindow).listenAvailable = true;
  });
  await page.locator('#menu-mic-test-record').click();
  await expect(page.locator('#menu-mic-test-record')).toHaveText('Stop recording');
  await expect(page.locator('#menu-mic-test-record')).toHaveAttribute('aria-pressed', 'true');

  await page.evaluate(() => {
    (window as unknown as MicConfigHarnessWindow).emitPcmSamples([-16384, 16384]);
  });
  await page.locator('#menu-mic-test-record').click();
  await expect(page.locator('#menu-mic-test-record')).toHaveText('Record test');
  await expect(page.locator('#menu-mic-test-play')).toBeEnabled();
  await expect(page.locator('#menu-mic-test-status')).toHaveText(
    'Recording ready. Play it back to verify your microphone.',
  );

  await page.locator('#menu-mic-test-play').click();
  await expect(page.locator('#menu-mic-test-status')).toHaveText(
    'Playing your PCM microphone test…',
  );
  const playbackState = await page.evaluate(() => {
    const win = window as unknown as MicConfigHarnessWindow;
    const rec = win.audioPlaybacks[0];
    rec.finish();
    return {
      sampleRate: rec.sampleRate,
      samples: rec.samples,
      resumed: rec.resumed,
      started: rec.started,
      closed: rec.closed,
      listenCalls: win.listenCalls,
    };
  });
  expect(playbackState).toEqual({
    sampleRate: 16000,
    samples: [-0.5, 0.5],
    resumed: true,
    started: true,
    closed: true,
    listenCalls: [
      {
        deviceId: 'studio-mic',
        sampleRate: 16000,
        vadThreshold: 0,
        continuous: true,
      },
    ],
  });
  await expect(page.locator('#menu-mic-test-status')).toHaveText(
    'Playback finished. If you heard yourself clearly, your mic and PCM path are working.',
  );

  // Opening mic config modal, starting a test recording, and closing modal stops active capture.
  await page.locator('#menu-item-mic-config').click();
  await expect(page.locator('#mic-config-modal')).toHaveClass(/active/);
  await page.locator('#menu-mic-test-record').click();
  await expect(page.locator('#menu-mic-test-record')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#mic-config-done-btn').click();
  await expect(page.locator('#mic-config-modal')).not.toHaveClass(/active/);
  expect(
    await page.evaluate(() => {
      const win = window as unknown as MicConfigHarnessWindow;
      return {
        stopped: win.stoppedTestCaptureCount,
        onClose: win.closeMicConfigCount,
      };
    }),
  ).toEqual({ stopped: 2, onClose: 1 });
});

test('mic config syncs sensitivity with auth/server, handles modal transitions, and cleans up on destroy', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MIC_CONFIG_SHELL_HTML,
    localStorage: {
      narratron_mic_sensitivity: '30',
    },
    moduleScripts: [installMicMocksScript()],
  });

  const savedSensitivities: number[] = [];
  await canvas.serveRoute('**/api/auth/mic-sensitivity', async (route) => {
    const body = route.request().postDataJSON() as { mic_sensitivity: number };
    savedSensitivities.push(body.mic_sensitivity);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    });
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMicConfigController } = await window.__importModule<MicConfigModule>(moduleUrl);
    const win = window as unknown as MicConfigHarnessWindow & {
      __mockListenForSpeech: (opts: ListenForSpeechOptions) => Promise<() => void>;
      __createMockAudioContext: (sampleRate: number) => AudioContext;
    };
    win.micConfig = createMicConfigController({
      micModeToggle: document.getElementById('menu-mic-mode-toggle') as HTMLInputElement | null,
      micDeviceSelect: document.getElementById('menu-mic-device-select') as HTMLSelectElement | null,
      micDeviceRefresh: document.getElementById('menu-mic-device-refresh'),
      micDeviceHint: document.getElementById('menu-mic-device-hint'),
      micTestRecordBtn: document.getElementById('menu-mic-test-record') as HTMLButtonElement | null,
      micTestPlayBtn: document.getElementById('menu-mic-test-play') as HTMLButtonElement | null,
      micTestStatus: document.getElementById('menu-mic-test-status'),
      micSensSlider: document.getElementById('menu-mic-sensitivity-slider') as HTMLInputElement | null,
      micSensValDisplay: document.getElementById('menu-mic-sensitivity-val'),
      micConfigModal: document.getElementById('mic-config-modal'),
      menuItemMicConfig: document.getElementById('menu-item-mic-config'),
      micConfigCloseBtn: document.getElementById('mic-config-close-btn'),
      micConfigDoneBtn: document.getElementById('mic-config-done-btn'),
      oratorHowtoModal: document.getElementById('orator-howto-modal'),
      howtoCloseBtn: document.getElementById('howto-close-btn'),
      howtoAckBtn: document.getElementById('howto-ack-btn'),
      getMicMode: () => win.micModeState,
      setMicMode: (mode: MicMode) => {
        win.micModeState = mode;
      },
      isRecording: () => win.recordingActive,
      cancelMicCapture: () => {
        win.cancelMicCaptureCount += 1;
      },
      updateMicToggleUI: () => {
        win.updateMicToggleCount += 1;
      },
      getListenForSpeech: () => win.__mockListenForSpeech,
      getAuthState: async () => ({
        authenticated: true,
        user: { mic_sensitivity: 0.72 },
      }),
      invalidateAuthStateCache: () => {
        win.invalidateAuthCount += 1;
      },
      onOpenMicConfig: () => {
        win.openMicConfigCount += 1;
      },
      onCloseMicConfig: () => {
        win.closeMicConfigCount += 1;
      },
      createAudioContext: win.__createMockAudioContext,
    });
  }, MIC_CONFIG_URL);

  // Authenticated profile sensitivity (72%) wins over localStorage (30%).
  await expect(page.locator('#menu-mic-sensitivity-val')).toHaveText('72%');
  await expect(page.locator('#menu-mic-sensitivity-slider')).toHaveValue('72');
  const initialThreshold = await page.evaluate(() => ({
    sensitivity: (window as unknown as MicConfigHarnessWindow).MIC_SENSITIVITY,
    threshold: (window as unknown as MicConfigHarnessWindow).MIC_DETECT_THRESHOLD,
  }));
  expect(initialThreshold.sensitivity).toBeCloseTo(0.72, 6);
  expect(Number(initialThreshold.threshold)).toBeCloseTo(0.01 * Math.pow(10, (0.5 - 0.72) * 2), 6);

  // 'input' updates local values without syncing to DB; 'change' POSTs and invalidates auth cache.
  await page.locator('#menu-mic-sensitivity-slider').evaluate((el) => {
    const slider = el as HTMLInputElement;
    slider.value = '80';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('#menu-mic-sensitivity-val')).toHaveText('80%');
  expect(savedSensitivities).toEqual([]);

  await page.locator('#menu-mic-sensitivity-slider').evaluate((el) => {
    const slider = el as HTMLInputElement;
    slider.value = '85';
    slider.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await expect.poll(() => savedSensitivities.length).toBe(1);
  expect(savedSensitivities[0]).toBeCloseTo(0.85, 6);
  expect(
    await page.evaluate(() => (window as unknown as MicConfigHarnessWindow).invalidateAuthCount),
  ).toBe(1);

  // How-to modal chains into mic config modal when acknowledged.
  await page.evaluate(() => {
    (window as unknown as MicConfigHarnessWindow).micConfig!.openOratorHowtoModal({
      openMicConfigOnClose: true,
    });
  });
  expect(
    await page.evaluate(() => (window as unknown as MicConfigHarnessWindow).micConfig!.isOratorHowtoOpen()),
  ).toBe(true);
  await page.locator('#howto-ack-btn').click();
  await expect(page.locator('#orator-howto-modal')).not.toHaveClass(/active/);
  await expect(page.locator('#mic-config-modal')).toHaveClass(/active/);
  expect(
    await page.evaluate(() => ({
      seen: localStorage.getItem('narratron_orator_howto_seen'),
      openMicCalls: (window as unknown as MicConfigHarnessWindow).openMicConfigCount,
      isMicOpen: (window as unknown as MicConfigHarnessWindow).micConfig!.isMicConfigOpen(),
    })),
  ).toEqual({
    seen: 'true',
    openMicCalls: 1,
    isMicOpen: true,
  });

  // Backdrop click closes mic config modal, and destroy() removes all handlers.
  await page.locator('#mic-config-modal').click({ position: { x: 1, y: 1 } });
  await expect(page.locator('#mic-config-modal')).not.toHaveClass(/active/);

  await page.evaluate(() => {
    (window as unknown as MicConfigHarnessWindow).micConfig!.destroy();
  });
  await page.locator('#menu-item-mic-config').click();
  await expect(page.locator('#mic-config-modal')).not.toHaveClass(/active/);
});

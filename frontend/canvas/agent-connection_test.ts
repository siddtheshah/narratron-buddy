import type { WebSocketRoute } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

type AgentConnectionModule = typeof import('/static/generated/canvas/agent-connection.js');
type AgentConnectionController =
  import('/static/generated/canvas/agent-connection.js').AgentConnectionController;
type AgentThoughtPayload =
  import('/static/generated/canvas/agent-connection.js').AgentThoughtPayload;
type StartAudioRecorderWorkletFn =
  import('/static/generated/canvas/agent-connection.js').StartAudioRecorderWorkletFn;
type ToolActivitySnapshot =
  import('/static/generated/canvas/agent-activity.js').ToolActivitySnapshot;
type OpusPacketHandler = import('/static/generated/canvas/audio-recorder.js').OpusPacketHandler;
type MicMode = import('/static/generated/canvas/mic-config.js').MicMode;

interface AgentHarnessWindow extends Window {
  agentConn: AgentConnectionController;
  oratorFlag: boolean;
  activeWindowFlag: boolean;
  micModeFlag: MicMode;
  workletAvailable: boolean;
  workletShouldStart: boolean;
  workletError: string | null;
  workletHandler: OpusPacketHandler | null;
  workletInitialFlags: boolean[];
  transmissionCalls: boolean[];
  stopMicCalls: number;
  refreshDevicesCalls: Array<boolean | undefined>;
  closeGameCalls: number;
  shareToasts: string[];
  agentThoughts: AgentThoughtPayload[];
  agentActivities: Array<ToolActivitySnapshot | null | undefined>;
  pcmBroadcastCalls: boolean[];
  stopSpeechCalls: number;
  navigations: string[];
  alerts: string[];
  mockContextClosed: number;
  pendingWorkletGate: (() => void) | null;
  holdWorkletStart: boolean;
}

const MODULE_URL = '/static/generated/canvas/agent-connection.js';

const HARNESS_HTML = `
  <div id="status-indicator" class="status-dot disconnected"></div>
  <button id="agent-start-stop-btn" type="button" class="summon-guide">🚀 Summon</button>
  <button id="agent-connect-btn" type="button" aria-pressed="false">🎙️ Mic On</button>
  <div id="insufficient-credits-modal" aria-hidden="true">
    <button id="credits-modal-close-btn" type="button">Close</button>
    <div id="credits-modal-message">Default insufficient credits message</div>
    <button id="credits-modal-deploy-btn" type="button">Deploy</button>
    <button id="credits-modal-dismiss-btn" type="button">Dismiss</button>
  </div>
`;

async function initHarness(
  page: Parameters<Parameters<typeof test>[2]>[0]['page'],
  options: {
    theaterId?: string | null;
    isOrator?: boolean;
    isActiveWindow?: boolean;
    micMode?: MicMode;
  } = {},
): Promise<void> {
  await page.evaluate(
    async ({
      moduleUrl,
      theaterId,
      isOrator,
      isActiveWindow,
      micMode,
    }: {
      moduleUrl: string;
      theaterId: string | null;
      isOrator: boolean;
      isActiveWindow: boolean;
      micMode: MicMode;
    }) => {
      const { createAgentConnectionController } =
        await window.__importModule<AgentConnectionModule>(moduleUrl);
      const win = window as unknown as AgentHarnessWindow;
      win.oratorFlag = isOrator;
      win.activeWindowFlag = isActiveWindow;
      win.micModeFlag = micMode;
      win.workletAvailable = true;
      win.workletShouldStart = true;
      win.workletError = null;
      win.workletHandler = null;
      win.workletInitialFlags = [];
      win.transmissionCalls = [];
      win.stopMicCalls = 0;
      win.refreshDevicesCalls = [];
      win.closeGameCalls = 0;
      win.shareToasts = [];
      win.agentThoughts = [];
      win.agentActivities = [];
      win.pcmBroadcastCalls = [];
      win.stopSpeechCalls = 0;
      win.navigations = [];
      win.alerts = [];
      win.mockContextClosed = 0;
      win.pendingWorkletGate = null;
      win.holdWorkletStart = false;

      const startWorklet: StartAudioRecorderWorkletFn = async (
        handler: OpusPacketHandler,
        initiallyEnabled?: boolean,
      ) => {
        win.workletHandler = handler;
        win.workletInitialFlags.push(Boolean(initiallyEnabled));
        if (win.holdWorkletStart) {
          await new Promise<void>((resolve) => {
            win.pendingWorkletGate = resolve;
          });
        }
        if (win.workletError) {
          throw new Error(win.workletError);
        }
        if (!win.workletShouldStart) {
          return [null, null, null, false] as const;
        }
        const mockContext = {
          close: async (): Promise<void> => {
            win.mockContextClosed += 1;
          },
        } as unknown as AudioContext;
        return [null, mockContext, null, true] as const;
      };

      win.agentConn = createAgentConnectionController({
        theaterId,
        agentStartStopBtn: document.getElementById('agent-start-stop-btn') as HTMLButtonElement | null,
        agentConnectBtn: document.getElementById('agent-connect-btn') as HTMLButtonElement | null,
        statusIndicator: document.getElementById('status-indicator'),
        isCurrentOrator: () => win.oratorFlag,
        isActiveCanvasWindow: () => win.activeWindowFlag,
        getMicMode: () => win.micModeFlag,
        refreshMicrophoneDevices: async (requestPermission?: boolean) => {
          win.refreshDevicesCalls.push(requestPermission);
        },
        getStartAudioRecorderWorklet: () => (win.workletAvailable ? startWorklet : undefined),
        getSetMicrophoneTransmissionEnabled: () => (enabled: boolean) => {
          win.transmissionCalls.push(enabled);
        },
        getStopMicrophone: () => () => {
          win.stopMicCalls += 1;
        },
        closeGameRenderer: () => {
          win.closeGameCalls += 1;
        },
        showShareToast: (message: string) => {
          win.shareToasts.push(message);
        },
        updateAgentThought: (thought: AgentThoughtPayload) => {
          win.agentThoughts.push(thought);
        },
        updateAgentActivity: (toolActivity?: ToolActivitySnapshot | null) => {
          win.agentActivities.push(toolActivity);
        },
        setPcmBroadcasting: (isBroadcasting: boolean) => {
          win.pcmBroadcastCalls.push(isBroadcasting);
        },
        stopSceneSpeech: () => {
          win.stopSpeechCalls += 1;
        },
        navigateTo: (url: string) => {
          win.navigations.push(url);
        },
        alertUser: (message: string) => {
          win.alerts.push(message);
        },
      });
      win.agentConn.updateMicToggleUI();
    },
    {
      moduleUrl: MODULE_URL,
      theaterId: options.theaterId === undefined ? 'th-agent' : options.theaterId,
      isOrator: options.isOrator ?? true,
      isActiveWindow: options.isActiveWindow ?? true,
      micMode: options.micMode ?? 'toggle',
    },
  );
}

test('insufficient credits modal controls message, aria-hidden, dismiss/close, and deploy navigation', async ({
  canvas,
  page,
}) => {
  await canvas.mount({ html: HARNESS_HTML });
  await initHarness(page);

  const modal = page.locator('#insufficient-credits-modal');
  const message = page.locator('#credits-modal-message');

  await expect(modal).toHaveAttribute('aria-hidden', 'true');
  await expect(modal).not.toHaveClass(/active/);

  await page.evaluate(() => {
    window.showInsufficientCreditsModal?.('Top up credits now');
  });
  await expect(modal).toHaveClass(/active/);
  await expect(modal).toHaveAttribute('aria-hidden', 'false');
  await expect(message).toHaveText('Top up credits now');

  await page.locator('#credits-modal-dismiss-btn').click();
  await expect(modal).not.toHaveClass(/active/);
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  // Calling without a message keeps the previous message text.
  await page.evaluate(() => {
    window.showInsufficientCreditsModal?.();
  });
  await expect(modal).toHaveClass(/active/);
  await expect(message).toHaveText('Top up credits now');

  await page.locator('#credits-modal-close-btn').click();
  await expect(modal).not.toHaveClass(/active/);
  await expect(modal).toHaveAttribute('aria-hidden', 'true');

  await page.locator('#credits-modal-deploy-btn').click();
  const navigations = await page.evaluate(
    () => (window as unknown as AgentHarnessWindow).navigations,
  );
  expect(navigations).toEqual(['/deploy?from=canvas']);
});

test('start, stop, and status endpoints manage UI, credits errors, spacebar guard, and focus checks', async ({
  canvas,
  page,
}) => {
  const apiCalls: string[] = [];
  let startBehavior: 'ok' | '402' | '500' = 'ok';
  let statusPayload: { agent_running?: boolean; insufficient_credits?: boolean } = {
    agent_running: false,
  };
  let stopShouldFail = false;

  await canvas.serveRoute('/api/theaters/th-agent/agent/*', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    apiCalls.push(`${route.request().method()} ${pathname}`);

    if (pathname.endsWith('/status')) {
      await route.fulfill({ json: statusPayload });
      return;
    }
    if (pathname.endsWith('/start')) {
      if (startBehavior === '402') {
        await route.fulfill({
          status: 402,
          json: { insufficient_credits: true, detail: 'Zero credit balance' },
        });
        return;
      }
      if (startBehavior === '500') {
        await route.fulfill({
          status: 500,
          json: { detail: 'Backend exploded' },
        });
        return;
      }
      await route.fulfill({ json: { ok: true, agent_running: true } });
      return;
    }
    if (pathname.endsWith('/stop')) {
      if (stopShouldFail) {
        await route.abort('failed');
        return;
      }
      await route.fulfill({ json: { status: 'stopped', agent_running: false } });
      return;
    }
    await route.fulfill({ status: 404, json: {} });
  });

  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, () => {});

  await canvas.mount({ html: HARNESS_HTML });
  await initHarness(page);

  const btn = page.locator('#agent-start-stop-btn');
  await expect(btn).toHaveText('🚀 Summon');
  await expect(btn).toHaveClass(/summon-guide/);

  // Spacebar on focused Summon/Banish button must be prevented and must not summon.
  await btn.focus();
  const keyResults = await btn.evaluate((el) => {
    const down = new KeyboardEvent('keydown', {
      code: 'Space',
      key: ' ',
      bubbles: true,
      cancelable: true,
    });
    const up = new KeyboardEvent('keyup', {
      code: 'Space',
      key: 'Spacebar',
      bubbles: true,
      cancelable: true,
    });
    el.dispatchEvent(down);
    el.dispatchEvent(up);
    return { downPrevented: down.defaultPrevented, upPrevented: up.defaultPrevented };
  });
  expect(keyResults).toEqual({ downPrevented: true, upPrevented: true });
  expect(apiCalls).toEqual([]);

  // 402 Insufficient credits shows modal, does not toast, and resets UI + closes game renderer.
  startBehavior = '402';
  await btn.click();
  await expect(page.locator('#insufficient-credits-modal')).toHaveClass(/active/);
  await expect(page.locator('#credits-modal-message')).toHaveText('Zero credit balance');
  await expect(btn).toHaveText('🚀 Summon');
  await expect(btn).not.toBeFocused();
  const after402 = await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    return {
      started: win.agentConn.isAgentStarted(),
      winStarted: window.isAgentStarted,
      toasts: win.shareToasts,
      closeGameCalls: win.closeGameCalls,
    };
  });
  expect(after402).toEqual({
    started: false,
    winStarted: false,
    toasts: [],
    closeGameCalls: 2,
  });

  // 500 Non-credit error shows toast, does not reopen modal.
  await page.locator('#credits-modal-close-btn').click();
  startBehavior = '500';
  await btn.click();
  await expect.poll(async () =>
    page.evaluate(() => (window as unknown as AgentHarnessWindow).shareToasts),
  ).toEqual(['Backend exploded']);
  await expect(page.locator('#insufficient-credits-modal')).not.toHaveClass(/active/);

  // 200 OK transitions to Banish and connects WebSocket.
  startBehavior = 'ok';
  await btn.click();
  await expect(btn).toHaveText('🛑 Banish');
  await expect(btn).not.toHaveClass(/summon-guide/);
  expect(await page.evaluate(() => window.isAgentStarted)).toBe(true);

  // Clicking again stops agent theater even if /stop fails.
  stopShouldFail = true;
  await btn.click();
  await expect(btn).toHaveText('🚀 Summon');
  expect(await page.evaluate(() => (window as unknown as AgentHarnessWindow).agentConn.isAgentStarted())).toBe(false);

  // Focus event checks status when orator, and skips when viewer.
  statusPayload = { agent_running: true };
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(btn).toHaveText('🛑 Banish');

  statusPayload = { agent_running: false, insufficient_credits: true };
  await page.evaluate(() => {
    (window as unknown as AgentHarnessWindow).agentConn.checkAgentStatus();
  });
  await expect(btn).toHaveText('🚀 Summon');

  const countBeforeViewerFocus = apiCalls.length;
  await page.evaluate(() => {
    (window as unknown as AgentHarnessWindow).oratorFlag = false;
    window.dispatchEvent(new Event('focus'));
  });
  expect(apiCalls.length).toBe(countBeforeViewerFocus);
});

test('WebSocket lifecycle handles deduplicated connect, ping, messages, audio forwarding, and reconnect', async ({
  canvas,
  page,
}) => {
  await page.clock.install();

  const receivedFromClient: Array<string | Buffer> = [];
  let activeWs: WebSocketRoute | null = null;
  let wsConnectionCount = 0;

  await canvas.mount({ html: HARNESS_HTML });
  await initHarness(page, { theaterId: 'th agent/1' });

  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws) => {
    const pathname = new URL(ws.url()).pathname;
    if (pathname === '/ws/th%20agent%2F1/agent') {
      wsConnectionCount += 1;
      activeWs = ws;
      ws.onMessage((msg) => {
        receivedFromClient.push(msg);
      });
    }
  });

  // Concurrent connectAgent calls while CONNECTING share one WebSocket.
  const connectResult = await page.evaluate(async () => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.setAgentStartedUI(true);
    const [ws1, ws2] = await Promise.all([
      win.agentConn.connectAgent(),
      win.agentConn.connectAgent(),
    ]);
    // Calling when already OPEN returns same socket.
    const ws3 = await win.agentConn.connectAgent();
    return {
      same12: ws1 === ws2,
      same23: ws2 === ws3,
      hasDisconnectedClass: document.getElementById('status-indicator')?.classList.contains('disconnected'),
      winMatches: window.agentWs === ws1,
    };
  });
  expect(connectResult).toEqual({
    same12: true,
    same23: true,
    hasDisconnectedClass: false,
    winMatches: true,
  });
  expect(wsConnectionCount).toBe(1);

  // audioRecorderHandler sends Opus ArrayBuffer when open, and keepalive ping fires every 5000ms.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    const packet = new Uint8Array([1, 2, 3, 4]).buffer;
    win.agentConn.audioRecorderHandler(packet);
  });
  await expect.poll(() => receivedFromClient.length).toBe(1);
  expect(Buffer.isBuffer(receivedFromClient[0])).toBe(true);
  expect(Array.from(receivedFromClient[0] as Buffer)).toEqual([1, 2, 3, 4]);

  await page.clock.fastForward(5100);
  await expect.poll(() => receivedFromClient.length).toBe(2);
  expect(JSON.parse(String(receivedFromClient[1]))).toMatchObject({ type: 'ping' });

  // setupComplete & pong mark setup complete and live_ready: true.
  activeWs!.send(JSON.stringify({ setupComplete: true }));
  await expect.poll(() => page.evaluate(() => window.isSetupComplete)).toBe(true);
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).agentActivities.at(-1)),
  ).toEqual({ live_ready: true });

  // agent_failed updates thought to 'wandering', cleans up WS, and sets started=false.
  activeWs!.send(JSON.stringify({ type: 'agent_failed' }));
  await expect(page.locator('#status-indicator')).toHaveClass(/disconnected/);
  const afterFailed = await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    return {
      ws: win.agentConn.getAgentWs(),
      winWs: window.agentWs,
      started: win.agentConn.isAgentStarted(),
      thoughts: win.agentThoughts,
      lastActivity: win.agentActivities.at(-1),
    };
  });
  expect(afterFailed).toEqual({
    ws: null,
    winWs: null,
    started: false,
    thoughts: [{ text: 'wandering' }],
    lastActivity: { live_ready: false },
  });

  // audioRecorderHandler ignores packets when socket is closed/null.
  const countBeforeClosedPacket = receivedFromClient.length;
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.audioRecorderHandler(new Uint8Array([9, 9]).buffer);
    win.agentConn.flushClientAudioBuffer();
  });
  expect(receivedFromClient.length).toBe(countBeforeClosedPacket);

  // Reconnect on insufficient_credits message closes WS and opens modal.
  await page.evaluate(async () => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.setAgentStartedUI(true);
    await win.agentConn.connectAgent();
  });
  await expect.poll(() => wsConnectionCount).toBe(2);
  activeWs!.send(JSON.stringify({ type: 'insufficient_credits', detail: 'Out of credits on WS' }));
  await expect(page.locator('#insufficient-credits-modal')).toHaveClass(/active/);
  await expect(page.locator('#credits-modal-message')).toHaveText('Out of credits on WS');
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).agentConn.isAgentStarted()),
  ).toBe(false);

  // Unexpected close while agent is started and active triggers automatic reconnect.
  await page.evaluate(async () => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.setAgentStartedUI(true);
    await win.agentConn.connectAgent();
  });
  await expect.poll(() => wsConnectionCount).toBe(3);
  activeWs!.close();
  await expect.poll(() => wsConnectionCount).toBe(4);

  // Failed connection schedules 3000ms reconnect timer and retries when active.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.cleanupAgentConnection();
  });
  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws) => {
    wsConnectionCount += 1;
    if (wsConnectionCount === 5) {
      ws.close({ code: 1011, reason: 'boom' });
      return;
    }
    ws.send(JSON.stringify({ type: 'pong' }));
  });
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.ensureAgentConnectionWhenActive();
  });
  await expect.poll(() => wsConnectionCount).toBe(5);
  await page.clock.fastForward(3100);
  await expect.poll(() => wsConnectionCount).toBe(6);
  await expect.poll(() => page.evaluate(() => window.isSetupComplete)).toBe(true);
});

test('toggle mic mode handles enable, opus streaming, cancel, cancellation race, and error alert', async ({
  canvas,
  page,
}) => {
  let startCalls = 0;
  let releaseStartGate: (() => void) | null = null;
  let holdStart = false;
  const wsPackets: Array<string | Buffer> = [];

  await canvas.serveRoute('/api/theaters/th-agent/agent/start', async (route) => {
    startCalls += 1;
    if (holdStart) {
      await new Promise<void>((resolve) => {
        releaseStartGate = resolve;
      });
    }
    await route.fulfill({ json: { ok: true } });
  });

  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, (ws) => {
    ws.onMessage((msg) => {
      wsPackets.push(msg);
    });
  });

  await canvas.mount({ html: HARNESS_HTML });
  await initHarness(page, { micMode: 'toggle' });

  const connectBtn = page.locator('#agent-connect-btn');
  await expect(connectBtn).toHaveText('🎙️ Mic On');
  await expect(connectBtn).toHaveAttribute('aria-pressed', 'false');

  // Clicking Mic On summons agent, connects WS, refreshes devices with permission, and activates recording.
  await connectBtn.click();
  await expect(connectBtn).toHaveText('🎙️ Mic Off');
  await expect(connectBtn).toHaveAttribute('aria-pressed', 'true');
  await expect(connectBtn).toBeEnabled();
  expect(startCalls).toBe(1);

  const activeState = await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.workletHandler?.(new Uint8Array([5, 6, 7]).buffer);
    return {
      recording: win.agentConn.isRecording(),
      captureReady: win.agentConn.isMicCaptureReady(),
      awaiting: win.agentConn.isMicAwaiting(),
      refreshCalls: win.refreshDevicesCalls,
      initialFlags: win.workletInitialFlags,
      transmissionCalls: win.transmissionCalls,
    };
  });
  expect(activeState).toEqual({
    recording: true,
    captureReady: true,
    awaiting: false,
    refreshCalls: [true],
    initialFlags: [true],
    transmissionCalls: [true],
  });
  await expect.poll(() => wsPackets.length).toBe(1);
  expect(Array.from(wsPackets[0] as Buffer)).toEqual([5, 6, 7]);

  // Clicking Mic Off cancels mic capture, closes AudioContext, stops mic, and clears PCM indicator.
  await connectBtn.click();
  await expect(connectBtn).toHaveText('🎙️ Mic On');
  await expect(connectBtn).toHaveAttribute('aria-pressed', 'false');
  const stoppedState = await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    return {
      recording: win.agentConn.isRecording(),
      captureReady: win.agentConn.isMicCaptureReady(),
      stopMicCalls: win.stopMicCalls,
      mockContextClosed: win.mockContextClosed,
      pcmBroadcastCalls: win.pcmBroadcastCalls,
    };
  });
  expect(stoppedState).toEqual({
    recording: false,
    captureReady: false,
    stopMicCalls: 1,
    mockContextClosed: 1,
    pcmBroadcastCalls: [false],
  });

  // Cancellation during in-flight startAgentTheater aborts enableMic cleanly.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.setAgentStartedUI(false);
    win.agentConn.cleanupAgentConnection();
  });
  holdStart = true;
  await connectBtn.click();
  await expect(connectBtn).toHaveText('🎙️ Awaiting');
  await expect(connectBtn).toBeDisabled();

  await page.evaluate(() => {
    (window as unknown as AgentHarnessWindow).agentConn.cancelMicCapture();
  });
  await expect(connectBtn).toHaveText('🎙️ Mic On');
  await expect(connectBtn).toBeEnabled();

  releaseStartGate!();
  holdStart = false;
  await expect
    .poll(() =>
      page.evaluate(() => {
        const win = window as unknown as AgentHarnessWindow;
        return {
          captureReady: win.agentConn.isMicCaptureReady(),
          recording: win.agentConn.isRecording(),
          workletCalls: win.workletInitialFlags.length,
        };
      }),
    )
    .toEqual({ captureReady: false, recording: false, workletCalls: 1 });

  // When worklet returns started=false, awaiting clears quietly without alerting.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.workletShouldStart = false;
  });
  await connectBtn.click();
  await expect(connectBtn).toHaveText('🎙️ Mic On');
  await expect(connectBtn).toBeEnabled();
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).alerts),
  ).toEqual([]);

  // Failure in worklet shows alertUser and restores Mic On.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.workletShouldStart = true;
    win.workletError = 'Microphone permission denied';
  });
  await connectBtn.click();
  await expect(connectBtn).toHaveText('🎙️ Mic On');
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).alerts),
  ).toEqual(['Microphone permission denied']);
});

test('push-to-talk handles pointer press/release, reuse, blur/hidden release, and awaiting release', async ({
  canvas,
  page,
}) => {
  let statusCalls = 0;
  await canvas.serveRoute('/api/theaters/th-agent/agent/status', async (route) => {
    statusCalls += 1;
    await route.fulfill({ json: { agent_running: true } });
  });
  await canvas.serveRoute('/api/theaters/th-agent/agent/start', async (route) => {
    await route.fulfill({ json: { ok: true } });
  });
  await page.routeWebSocket(/wss?:\/\/canvas\.test\/ws\/.*/, () => {});

  await canvas.mount({ html: HARNESS_HTML });
  await initHarness(page, { micMode: 'push-to-talk' });

  const pttBtn = page.locator('#agent-connect-btn');
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');

  // Hold worklet startup so we can verify awaiting UI and releasing before worklet finishes.
  await page.evaluate(() => {
    (window as unknown as AgentHarnessWindow).holdWorkletStart = true;
  });

  const box = (await pttBtn.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();

  await expect(pttBtn).toHaveText('🎙️ Awaiting');
  await expect(pttBtn).toBeEnabled(); // Toggle-only disable; PTT button stays enabled while awaiting.
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).stopSpeechCalls),
  ).toBe(1);

  // Release pointer while still awaiting -> worklet resolves with initiallyEnabled=false after gate opens.
  await page.mouse.up();
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.holdWorkletStart = false;
    win.pendingWorkletGate?.();
    win.pendingWorkletGate = null;
  });

  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');
  await expect
    .poll(() =>
      page.evaluate(() => {
        const win = window as unknown as AgentHarnessWindow;
        return {
          captureReady: win.agentConn.isMicCaptureReady(),
          recording: win.agentConn.isRecording(),
          transmissionCalls: win.transmissionCalls,
        };
      }),
    )
    .toEqual({
      captureReady: true,
      recording: false,
      transmissionCalls: [false, false],
    });

  // Subsequent press reuses ready capture without re-invoking startWorklet.
  await page.mouse.down();
  await expect(pttBtn).toHaveText('🎙️ Talking');
  await expect(pttBtn).toHaveAttribute('aria-pressed', 'true');

  // Window blur releases held PTT press.
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');
  await expect(pttBtn).toHaveAttribute('aria-pressed', 'false');

  // Hotkey press is released when document becomes hidden.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.agentConn.beginMicPress('hotkey');
  });
  await expect(pttBtn).toHaveText('🎙️ Talking');
  expect(
    await page.evaluate(() => (window as unknown as AgentHarnessWindow).agentConn.getMicPressSource()),
  ).toBe('hotkey');

  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.activeWindowFlag = false;
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');
  expect(statusCalls).toBe(0);

  // When active window becomes visible again for orator, visibilitychange checks agent status.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.activeWindowFlag = true;
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => statusCalls).toBe(1);

  // Non-orator beginMicPress is ignored.
  await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    win.oratorFlag = false;
    win.agentConn.beginMicPress('hotkey');
  });
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');

  const finalCheck = await page.evaluate(() => {
    const win = window as unknown as AgentHarnessWindow;
    return {
      workletCount: win.workletInitialFlags.length,
      transmissionCalls: win.transmissionCalls,
      pressHeld: win.agentConn.isMicPressHeld(),
      pressSource: win.agentConn.getMicPressSource(),
    };
  });
  expect(finalCheck).toEqual({
    workletCount: 1,
    transmissionCalls: [false, false, true, false, true, false],
    pressHeld: false,
    pressSource: null,
  });

  // Destroy cleans up listeners, globals, and active WebSocket.
  await page.evaluate(() => {
    (window as unknown as AgentHarnessWindow).agentConn.destroy();
  });
  const afterDestroy = await page.evaluate(() => ({
    ws: (window as unknown as AgentHarnessWindow).agentConn.getAgentWs(),
    winWs: window.agentWs,
    hasStartGlobal: window.startAgentTheater !== undefined,
    hasModalGlobal: window.showInsufficientCreditsModal !== undefined,
  }));
  expect(afterDestroy).toEqual({
    ws: null,
    winWs: null,
    hasStartGlobal: false,
    hasModalGlobal: false,
  });
});

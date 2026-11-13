import type { WebSocketRoute } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

interface HotkeySpec {
  code: string;
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
}

interface HotkeyTestWindow extends Window {
  setMicHotkey?: (code: string) => void;
  setTextHotkey?: (hk: string | Partial<HotkeySpec>) => void;
  setTextCommandHotkey?: (hk: string | Partial<HotkeySpec>) => void;
  isAgentStarted?: boolean;
  agentWs?: WebSocket | null;
}

function installToggleMicMocks(): string {
  return `
    window.__mockGetUserMediaCalls = 0;
    const mockTrack = { stop() {} };
    const mockStream = { getTracks() { return [mockTrack]; } };
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        addEventListener: () => {},
        removeEventListener: () => {},
        enumerateDevices: async () => [{ kind: 'audioinput', deviceId: 'default', label: 'Default Mic' }],
        getUserMedia: async () => {
          window.__mockGetUserMediaCalls += 1;
          return mockStream;
        },
      },
    });
    window.AudioEncoder = class MockAudioEncoder {
      constructor(init) {
        this._init = init;
        this.state = 'unconfigured';
      }
      configure() {
        this.state = 'configured';
      }
      encode() {}
      flush() {
        return Promise.resolve();
      }
      close() {
        this.state = 'closed';
      }
    };
    window.AudioContext = class MockAudioContext {
      constructor() {
        this.state = 'running';
        this.destination = {};
        this.audioWorklet = { addModule: async () => {} };
      }
      createMediaStreamSource() {
        return { connect() {}, disconnect() {} };
      }
      close() {
        this.state = 'closed';
        return Promise.resolve();
      }
    };
    window.AudioWorkletNode = class MockAudioWorkletNode {
      constructor() {
        this.port = { onmessage: null };
      }
      connect() {}
      disconnect() {}
    };
  `;
}

test('orator how-to and input configuration modal expose default hotkeys and open flow', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({
    isOrator: true,
    skipDefaultLocalStorage: true,
  });

  // First-time orator sees the Quick Start Guide documenting text & mic hotkeys and Input Configuration.
  const howtoModal = page.locator('#orator-howto-modal');
  await expect(howtoModal).toHaveClass(/active/, { timeout: 3000 });
  await expect(howtoModal).toContainText('Use Mic or Text Input');
  await expect(howtoModal).toContainText('Input Configuration');
  await expect(page.locator('#howto-text-hotkey-display')).toHaveText('Shift + Space');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('Ctrl + Shift + [');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Shift + Space');

  // Acknowledging the how-to guide persists the seen flag and opens Input Configuration.
  await page.locator('#howto-ack-btn').click();
  await expect(howtoModal).not.toHaveClass(/active/);
  expect(await page.evaluate(() => localStorage.getItem('narratron_orator_howto_seen'))).toBe('true');

  const micConfigModal = page.locator('#mic-config-modal');
  await expect(micConfigModal).toHaveClass(/active/);

  // Both text hotkey and mic hotkey controls live inside #mic-config-modal before Done.
  const modalButtons = await micConfigModal.evaluate((modal) => {
    const html = modal.innerHTML;
    return {
      hasTextHotkey: modal.querySelector('#menu-item-text-hotkey') !== null,
      hasMicHotkey: modal.querySelector('#menu-item-hotkey') !== null,
      textBeforeDone:
        html.indexOf('id="menu-item-text-hotkey"') < html.indexOf('id="mic-config-done-btn"'),
      micBeforeDone:
        html.indexOf('id="menu-item-hotkey"') < html.indexOf('id="mic-config-done-btn"'),
    };
  });
  expect(modalButtons).toEqual({
    hasTextHotkey: true,
    hasMicHotkey: true,
    textBeforeDone: true,
    micBeforeDone: true,
  });

  await expect(page.locator('#menu-item-text-hotkey')).toHaveAttribute(
    'title',
    'Click to rebind Orator text hotkey',
  );
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Shift + Space');
  await expect(page.locator('#menu-item-hotkey')).toHaveAttribute(
    'title',
    'Click to rebind the Orator mic hotkey',
  );
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + Shift + [');

  await page.locator('#mic-config-done-btn').click();
  await expect(micConfigModal).not.toHaveClass(/active/);
});

test('hotkey rebinding ignores standalone modifiers, resolves conflicts, cancels on Escape/close, and persists across reload', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-mic-config').click();
  const micConfigModal = page.locator('#mic-config-modal');
  await expect(micConfigModal).toHaveClass(/active/);

  const textBtn = page.locator('#menu-item-text-hotkey');
  const textDisplay = page.locator('#menu-text-hotkey-display');
  const micBtn = page.locator('#menu-item-hotkey');
  const micDisplay = page.locator('#menu-hotkey-display');
  const wheelBtn = page.locator('#action-wheel-rebind');
  const wheelLabel = page.locator('#action-wheel-binding-label');

  // 1. Standalone modifiers do not complete rebinding; clicking another rebind control cancels the active one.
  await textBtn.click();
  await expect(textDisplay).toHaveText('Press key combo to rebind...');

  for (const modifierCode of ['ShiftLeft', 'ControlLeft', 'AltLeft', 'MetaLeft']) {
    await page.evaluate((code: string) => {
      window.dispatchEvent(new KeyboardEvent('keydown', { code, key: code, bubbles: true, cancelable: true }));
    }, modifierCode);
    await expect(textDisplay).toHaveText('Press key combo to rebind...');
  }

  // Switching to mic hotkey rebind cancels text hotkey rebind and restores its display.
  await micBtn.click();
  await expect(textDisplay).toHaveText('Text Hotkey: Shift + Space');
  await expect(micDisplay).toHaveText('Press key combo to rebind...');

  // Switching to action wheel rebind cancels mic hotkey rebind.
  await wheelBtn.click();
  await expect(micDisplay).toHaveText('Mic Hotkey: Ctrl + Shift + [');
  await expect(wheelLabel).toContainText('Press a mouse button or key combo');

  // Switching back to text hotkey rebind cancels action wheel rebind.
  await textBtn.click();
  await expect(textDisplay).toHaveText('Press key combo to rebind...');
  await expect(wheelLabel).toHaveText('Action Wheel: Right mouse');

  // Closing the modal via Done cancels any active rebinding without saving.
  await page.locator('#mic-config-done-btn').click();
  await expect(micConfigModal).not.toHaveClass(/active/);
  await expect(textDisplay).toHaveText('Text Hotkey: Shift + Space');
  expect(await page.evaluate(() => localStorage.getItem('narratron_text_hotkey'))).toBeNull();

  // 2. Escape cancels rebinding (both when dispatched directly on window and when pressed in modal).
  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-mic-config').click();
  await textBtn.click();
  await expect(textDisplay).toHaveText('Press key combo to rebind...');
  await page.evaluate(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', { code: 'Escape', key: 'Escape', bubbles: true, cancelable: true }),
    );
  });
  await expect(textDisplay).toHaveText('Text Hotkey: Shift + Space');
  await expect(micConfigModal).toHaveClass(/active/);
  expect(await page.evaluate(() => localStorage.getItem('narratron_text_hotkey'))).toBeNull();

  // Also verify keyboard Escape while rebinding mic hotkey closes cleanly and does not save.
  await micBtn.click();
  await expect(micDisplay).toHaveText('Press key combo to rebind...');
  await page.keyboard.press('Escape');
  await expect(micConfigModal).not.toHaveClass(/active/);
  await expect(micDisplay).toHaveText('Mic Hotkey: Ctrl + Shift + [');
  expect(await page.evaluate(() => localStorage.getItem('narratron_mic_hotkey'))).toBeNull();

  // 3. Rebind text hotkey to Ctrl + Alt + K and mic hotkey to Alt + 9.
  await page.locator('#menu-left-toggle-btn').click();
  await page.locator('#menu-item-mic-config').click();

  await textBtn.click();
  await page.keyboard.press('Control+Alt+KeyK');
  await expect(textDisplay).toHaveText('Text Hotkey: Ctrl + Alt + K');
  await expect(page.locator('#howto-text-hotkey-display')).toHaveText('Ctrl + Alt + K');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Ctrl + Alt + K');

  await micBtn.click();
  await page.keyboard.press('Alt+Digit9');
  await expect(micDisplay).toHaveText('Mic Hotkey: Alt + 9');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('Alt + 9');

  await page.locator('#mic-config-close-btn').click();

  const savedText = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('narratron_text_hotkey') ?? 'null'),
  ) as HotkeySpec | null;
  const savedMic = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('narratron_mic_hotkey') ?? 'null'),
  ) as HotkeySpec | null;

  expect(savedText).toMatchObject({ code: 'KeyK', altKey: true, ctrlKey: true, shiftKey: false });
  expect(savedMic).toMatchObject({ code: 'Digit9', altKey: true, ctrlKey: false, shiftKey: false });

  // Old default Shift+Space no longer opens orator command; new Ctrl+Alt+K does.
  const overlay = page.locator('#orator-command-overlay');
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  await page.keyboard.press('Control+Alt+KeyK');
  await expect(overlay).toHaveClass(/active/);
  await page.keyboard.press('Control+Alt+KeyK');
  await expect(overlay).not.toHaveClass(/active/);

  // 4. Persisted hotkeys survive canvas reload, and invalid JSON falls back to defaults.
  await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      narratron_text_hotkey: JSON.stringify(savedText),
      narratron_mic_hotkey: JSON.stringify(savedMic),
    },
  });
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Ctrl + Alt + K');
  await expect(page.locator('#howto-text-hotkey-display')).toHaveText('Ctrl + Alt + K');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Ctrl + Alt + K');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Alt + 9');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('Alt + 9');

  await page.keyboard.press('Control+Alt+KeyK');
  await expect(page.locator('#orator-command-overlay')).toHaveClass(/active/);

  await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      narratron_text_hotkey: '{invalid-json',
      narratron_mic_hotkey: '{invalid-json',
    },
  });
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Shift + Space');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + Shift + [');
});

test('window.setTextHotkey, setTextCommandHotkey, and setMicHotkey update storage, labels, and shortcuts', async ({
  canvas,
  page,
}) => {
  await canvas.openCanvas({ isOrator: true });

  const helpersAvailable = await page.evaluate(() => {
    const win = window as unknown as HotkeyTestWindow;
    return (
      typeof win.setTextHotkey === 'function' &&
      typeof win.setTextCommandHotkey === 'function' &&
      typeof win.setMicHotkey === 'function' &&
      win.setTextCommandHotkey === win.setTextHotkey
    );
  });
  expect(helpersAvailable).toBe(true);

  await page.evaluate(() => {
    const win = window as unknown as HotkeyTestWindow;
    win.setTextHotkey?.('KeyT');
    win.setMicHotkey?.('KeyM');
  });

  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: T');
  await expect(page.locator('#howto-text-hotkey-display')).toHaveText('T');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('T');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: M');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('M');

  const overlay = page.locator('#orator-command-overlay');
  const commandInput = page.locator('#orator-command-input');
  await page.keyboard.press('KeyT');
  await expect(overlay).toHaveClass(/active/);
  await expect(commandInput).toBeFocused();
  await page.keyboard.press('KeyT');
  await expect(overlay).not.toHaveClass(/active/);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  await page.evaluate(() => {
    const win = window as unknown as HotkeyTestWindow;
    win.setTextCommandHotkey?.({
      code: 'Slash',
      key: '/',
      ctrlKey: true,
      shiftKey: true,
      altKey: false,
    });
  });

  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Ctrl + Shift + /');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Ctrl + Shift + /');

  const storedText = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('narratron_text_hotkey') ?? 'null'),
  ) as HotkeySpec | null;
  expect(storedText).toEqual({
    code: 'Slash',
    key: '/',
    altKey: false,
    ctrlKey: true,
    shiftKey: true,
  });

  await page.keyboard.press('Control+Shift+Slash');
  await expect(overlay).toHaveClass(/active/);
  await expect(commandInput).toBeFocused();
  await page.keyboard.press('Control+Shift+Slash');
  await expect(overlay).not.toHaveClass(/active/);
});

test('text hotkey opens, submits, and dismisses orator command while suppressing repeat events, editable inputs, and viewers', async ({
  canvas,
  page,
}) => {
  const submittedCommands: Array<{ text: string }> = [];
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/orator/command': async (route) => {
        const body = JSON.parse(route.request().postData() ?? '{}') as { text: string };
        submittedCommands.push(body);
        await route.fulfill({ json: { ok: true } });
      },
    },
  });

  const overlay = page.locator('#orator-command-overlay');
  const commandInput = page.locator('#orator-command-input');

  // 1. Key repeat on text hotkey does not open overlay.
  await page.evaluate(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', {
        code: 'Space',
        key: ' ',
        shiftKey: true,
        repeat: true,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  await expect(overlay).not.toHaveClass(/active/);

  // 2. Non-repeat Shift+Space opens overlay, sets aria-hidden=false, and focuses input.
  await page.keyboard.press('Shift+Space');
  await expect(overlay).toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'false');
  await expect(commandInput).toBeFocused();

  // 3. Pressing Shift+Space while focused in #orator-command-input dismisses overlay and clears value.
  await commandInput.fill('Look around the tavern');
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'true');
  await expect(commandInput).toHaveValue('');

  // Also verify dismissal when overlay is open while focus is outside #orator-command-input.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('Shift+Space');
  await expect(overlay).toHaveClass(/active/);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  // 4. Pressing Escape while open also dismisses overlay and clears value.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('Shift+Space');
  await expect(overlay).toHaveClass(/active/);
  await commandInput.fill('Cancelled command');
  await page.keyboard.press('Escape');
  await expect(overlay).not.toHaveClass(/active/);
  await expect(commandInput).toHaveValue('');

  // 5. Submitting sends POST /api/orator/command and closes overlay.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('Shift+Space');
  await commandInput.fill('Summon the dragon');
  await commandInput.press('Enter');
  await expect(overlay).not.toHaveClass(/active/);
  expect(submittedCommands).toEqual([{ text: 'Summon the dragon' }]);

  // 6. Editable elements suppress opening via text hotkey (input, textarea, select, contentEditable).
  const chatInput = page.locator('#chat-input');
  await chatInput.focus();
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  await page.evaluate(() => {
    const editable = document.createElement('div');
    editable.id = 'test-contenteditable';
    editable.contentEditable = 'true';
    editable.tabIndex = 0;
    document.body.appendChild(editable);
    editable.focus();
  });
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  await page.evaluate(() => {
    const selectEl = document.getElementById('menu-mic-device-select') as HTMLSelectElement | null;
    selectEl?.focus();
  });
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  // 7. Non-orator (viewer) cannot open orator command via text hotkey.
  await canvas.openCanvas({ isOrator: false });
  await page.keyboard.press('Shift+Space');
  await expect(page.locator('#orator-command-overlay')).not.toHaveClass(/active/);
});

test('mic hotkey triggers toggle and push-to-talk modes and respects input-focus and command-overlay suppression', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installToggleMicMocks() });

  let agentStartCalls = 0;
  let agentWsSocket: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    localStorage: { narratron_mic_mode: 'toggle' },
    apiRoutes: {
      '/api/theaters/stage/agent/start': async (route) => {
        agentStartCalls += 1;
        await route.fulfill({ json: { ok: true } });
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/stage/agent') {
        agentWsSocket = ws;
      }
    },
  });

  const connectBtn = page.locator('#agent-connect-btn');
  await expect(connectBtn).toHaveText('🎙️ Mic On');

  // Suppressed while typing in an input.
  await page.locator('#chat-input').focus();
  await page.keyboard.press('Control+Shift+BracketLeft');
  expect(agentStartCalls).toBe(0);
  await expect(connectBtn).toHaveText('🎙️ Mic On');

  // Suppressed while #orator-command-overlay is open.
  await page.locator('body').click({ position: { x: 10, y: 10 } });
  await page.keyboard.press('Shift+Space');
  await expect(page.locator('#orator-command-overlay')).toHaveClass(/active/);
  await page.keyboard.press('Control+Shift+BracketLeft');
  expect(agentStartCalls).toBe(0);
  await page.keyboard.press('Escape');

  // Outside inputs/overlays, mic hotkey activates mic in toggle mode.
  await page.keyboard.press('Control+Shift+BracketLeft');
  await expect.poll(() => agentStartCalls).toBe(1);
  await expect.poll(() => agentWsSocket !== null).toBe(true);
  await expect(connectBtn).toHaveText('🎙️ Mic Off');

  // Pressing mic hotkey again toggles mic off.
  await page.keyboard.press('Control+Shift+BracketLeft');
  await expect(connectBtn).toHaveText('🎙️ Mic On');

  // Push-to-talk mode: holding mic hotkey starts talking, releasing stops.
  agentStartCalls = 0;
  await canvas.openCanvas({
    isOrator: true,
    localStorage: { narratron_mic_mode: 'push-to-talk' },
    apiRoutes: {
      '/api/theaters/stage/agent/start': async (route) => {
        agentStartCalls += 1;
        await route.fulfill({ json: { ok: true } });
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/stage/agent') {
        agentWsSocket = ws;
      }
    },
  });

  const pttBtn = page.locator('#agent-connect-btn');
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');

  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('BracketLeft');
  await expect.poll(() => agentStartCalls).toBe(1);
  await expect(pttBtn).toHaveText('🎙️ Talking');

  await page.keyboard.up('BracketLeft');
  await expect(pttBtn).toHaveText('🎙️ Hold to Talk');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');

  // Viewer cannot trigger mic hotkey.
  agentStartCalls = 0;
  await canvas.openCanvas({
    isOrator: false,
    localStorage: { narratron_mic_mode: 'toggle' },
    apiRoutes: {
      '/api/theaters/stage/agent/start': async (route) => {
        agentStartCalls += 1;
        await route.fulfill({ json: { ok: true } });
      },
    },
  });
  await page.keyboard.press('Control+Shift+BracketLeft');
  expect(agentStartCalls).toBe(0);
});

test('spacebar cannot summon or banish agent when agentStartStopBtn is focused and click blurs it', async ({
  canvas,
  page,
}) => {
  let startCalls = 0;
  let stopCalls = 0;

  await canvas.openCanvas({
    isOrator: true,
    viewport: { width: 1400, height: 900 },
    apiRoutes: {
      '/api/theaters/stage/agent/start': async (route) => {
        startCalls += 1;
        await route.fulfill({ json: { ok: true } });
      },
      '/api/theaters/stage/agent/stop': async (route) => {
        stopCalls += 1;
        await route.fulfill({ json: { ok: true } });
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/stage/agent') {
        ws.send(JSON.stringify({ setupComplete: true }));
      }
    },
  });

  const startStopBtn = page.locator('#agent-start-stop-btn');
  await expect(startStopBtn).toHaveText('🚀 Summon');

  // Focusing the summon button and pressing Space (native and synthetic Spacebar/Space) must not summon.
  await startStopBtn.focus();
  await expect(startStopBtn).toBeFocused();
  await page.keyboard.press('Space');

  const keyPrevented = await startStopBtn.evaluate((btn) => {
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
    btn.dispatchEvent(down);
    btn.dispatchEvent(up);
    return { downPrevented: down.defaultPrevented, upPrevented: up.defaultPrevented };
  });
  expect(keyPrevented).toEqual({ downPrevented: true, upPrevented: true });
  expect(startCalls).toBe(0);
  await expect(startStopBtn).toHaveText('🚀 Summon');

  // Clicking the summon button blurs it immediately and summons the agent.
  await startStopBtn.click();
  await expect.poll(() => startCalls).toBe(1);
  await expect(startStopBtn).toHaveText('🛑 Banish');
  await expect(startStopBtn).not.toBeFocused();

  // Re-focusing the banish button and pressing Space must not banish the agent.
  await startStopBtn.focus();
  await expect(startStopBtn).toBeFocused();
  await page.keyboard.press('Space');
  expect(stopCalls).toBe(0);
  await expect(startStopBtn).toHaveText('🛑 Banish');

  // Plain Space still works for typing inside editable inputs like #chat-input.
  const chatInput = page.locator('#chat-input');
  await chatInput.focus();
  await page.keyboard.type('hello world');
  await expect(chatInput).toHaveValue('hello world');
});

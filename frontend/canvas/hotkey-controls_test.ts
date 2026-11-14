import type { Page } from '@playwright/test';
import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';

type HotkeyControlsModule = typeof import('/static/generated/canvas/hotkey-controls.js');
type HotkeyControlsController =
  import('/static/generated/canvas/hotkey-controls.js').HotkeyControlsController;
type HotkeySpec = import('/static/generated/canvas/hotkey-controls.js').HotkeySpec;
type MicMode = import('/static/generated/canvas/mic-config.js').MicMode;
type MicPressSource = import('/static/generated/canvas/hotkey-controls.js').MicPressSource;

interface HotkeyHarnessWindow extends Window {
  hotkeyControls?: HotkeyControlsController;
  hotkeyMod?: HotkeyControlsModule;
  isOratorFlag: boolean;
  micModeFlag: MicMode;
  micPressSourceFlag: MicPressSource;
  beginMicPressCalls: Array<'hotkey' | 'button-key'>;
  releaseMicPressCount: number;
  connectClickCount: number;
  stopSpeechCount: number;
  rebindStartCount: number;
  startStopClickCount: number;
}

const HOTKEY_CONTROLS_URL = '/static/generated/canvas/hotkey-controls.js';

const HOTKEY_SHELL_HTML = `
  <button id="agent-start-stop-btn" type="button">Summon</button>
  <button id="agent-connect-btn" type="button">Mic On</button>

  <button id="menu-item-text-hotkey" type="button">
    <span id="menu-text-hotkey-display"></span>
  </button>
  <button id="menu-item-hotkey" type="button">
    <span id="menu-hotkey-display"></span>
  </button>
  <button id="legacy-mic-hotkey-btn" type="button">
    <span id="legacy-hotkey-display"></span>
  </button>

  <span id="howto-text-hotkey-display"></span>
  <span id="howto-mic-hotkey-display"></span>

  <div id="orator-command-overlay" aria-hidden="true">
    <form id="orator-command-form">
      <input id="orator-command-input" type="text" />
      <span id="orator-command-dismiss-hotkey"></span>
      <span id="orator-command-status"></span>
    </form>
  </div>

  <input id="chat-input" type="text" />
  <textarea id="notes-textarea"></textarea>
  <select id="mic-select"><option value="a">A</option></select>
  <div id="editable-box" contenteditable="true" tabindex="0"></div>
`;

async function mountHotkeyHarness(
  canvas: CanvasHarness,
  page: Page,
  options: {
    readonly localStorage?: Readonly<Record<string, string>>;
    readonly includeLegacyBtn?: boolean;
    readonly theaterId?: string | null;
  } = {},
): Promise<void> {
  await canvas.mount({
    html: HOTKEY_SHELL_HTML,
    localStorage: options.localStorage,
    moduleScripts: [
      `
      window.isOratorFlag = true;
      window.micModeFlag = 'toggle';
      window.micPressSourceFlag = null;
      window.beginMicPressCalls = [];
      window.releaseMicPressCount = 0;
      window.connectClickCount = 0;
      window.stopSpeechCount = 0;
      window.rebindStartCount = 0;
      window.startStopClickCount = 0;
      document.getElementById('agent-connect-btn').addEventListener('click', () => {
        window.connectClickCount += 1;
      });
      document.getElementById('agent-start-stop-btn').addEventListener('click', () => {
        window.startStopClickCount += 1;
      });
    `,
    ],
  });

  await page.evaluate(
    async ({
      moduleUrl,
      includeLegacyBtn,
      theaterId,
    }: {
      moduleUrl: string;
      includeLegacyBtn: boolean;
      theaterId: string | null;
    }) => {
      const mod = await window.__importModule<HotkeyControlsModule>(moduleUrl);
      const win = window as unknown as HotkeyHarnessWindow;
      win.hotkeyMod = mod;
      win.hotkeyControls = mod.createHotkeyControlsController({
        isCurrentOrator: () => win.isOratorFlag,
        getMicMode: () => win.micModeFlag,
        getMicPressSource: () => win.micPressSourceFlag,
        beginMicPress: (source) => {
          win.beginMicPressCalls.push(source);
          win.micPressSourceFlag = source;
        },
        releaseMicPress: () => {
          win.releaseMicPressCount += 1;
          win.micPressSourceFlag = null;
        },
        agentStartStopBtn: document.getElementById('agent-start-stop-btn'),
        agentConnectBtn: document.getElementById('agent-connect-btn') as HTMLButtonElement | null,
        menuItemHotkey: document.getElementById('menu-item-hotkey'),
        menuHotkeyDisplay: document.getElementById('menu-hotkey-display'),
        howtoMicDisplay: document.getElementById('howto-mic-hotkey-display'),
        micHotkeyBtn: includeLegacyBtn ? document.getElementById('legacy-mic-hotkey-btn') : null,
        hotkeyDisplay: includeLegacyBtn ? document.getElementById('legacy-hotkey-display') : null,
        menuItemTextHotkey: document.getElementById('menu-item-text-hotkey'),
        menuTextHotkeyDisplay: document.getElementById('menu-text-hotkey-display'),
        howtoTextDisplay: document.getElementById('howto-text-hotkey-display'),
        dismissHotkeyDisplay: document.getElementById('orator-command-dismiss-hotkey'),
        oratorCommandOverlay: document.getElementById('orator-command-overlay'),
        oratorCommandForm: document.getElementById('orator-command-form') as HTMLFormElement | null,
        oratorCommandInput: document.getElementById('orator-command-input') as HTMLInputElement | null,
        oratorCommandStatus: document.getElementById('orator-command-status'),
        getTheaterId: () => theaterId,
        stopSceneSpeech: () => {
          win.stopSpeechCount += 1;
        },
        onRebindStart: () => {
          win.rebindStartCount += 1;
        },
      });
    },
    {
      moduleUrl: HOTKEY_CONTROLS_URL,
      includeLegacyBtn: options.includeLegacyBtn ?? false,
      theaterId: options.theaterId ?? 'stage-1',
    },
  );
}

test('formatHotkeyLabel and hotkeyMatchesEvent format all key variants and match modifiers', async ({
  canvas,
  page,
}) => {
  await canvas.mount({ html: '' });

  const results = await page.evaluate(async (moduleUrl: string) => {
    const { formatHotkeyLabel, hotkeyMatchesEvent } =
      await window.__importModule<HotkeyControlsModule>(moduleUrl);

    const labels = {
      empty: formatHotkeyLabel(null),
      key: formatHotkeyLabel({
        code: 'KeyK',
        key: 'k',
        ctrlKey: true,
        altKey: true,
        shiftKey: false,
      }),
      digit: formatHotkeyLabel({
        code: 'Digit9',
        key: '9',
        ctrlKey: false,
        altKey: true,
        shiftKey: false,
      }),
      space: formatHotkeyLabel({
        code: 'Space',
        key: ' ',
        ctrlKey: false,
        altKey: false,
        shiftKey: true,
      }),
      punctuation: [
        'BracketLeft',
        'BracketRight',
        'Backquote',
        'Semicolon',
        'Quote',
        'Comma',
        'Period',
        'Slash',
        'Backslash',
        'Minus',
        'Equal',
      ].map((code) =>
        formatHotkeyLabel({ code, key: code, altKey: false, ctrlKey: false, shiftKey: false }),
      ),
      fallbackCode: formatHotkeyLabel({
        code: 'F8',
        key: 'F8',
        altKey: false,
        ctrlKey: false,
        shiftKey: false,
      }),
    };

    const spec: HotkeySpec = {
      code: 'KeyM',
      key: 'm',
      altKey: false,
      ctrlKey: true,
      shiftKey: true,
    };
    const exactMatch = hotkeyMatchesEvent(
      new KeyboardEvent('keydown', { code: 'KeyM', ctrlKey: true, shiftKey: true }),
      spec,
    );
    const wrongMod = hotkeyMatchesEvent(
      new KeyboardEvent('keydown', { code: 'KeyM', ctrlKey: true, shiftKey: false }),
      spec,
    );
    const nullMatch = hotkeyMatchesEvent(new KeyboardEvent('keydown', { code: 'KeyM' }), null);

    return { labels, exactMatch, wrongMod, nullMatch };
  }, HOTKEY_CONTROLS_URL);

  expect(results.labels.empty).toBe('');
  expect(results.labels.key).toBe('Ctrl + Alt + K');
  expect(results.labels.digit).toBe('Alt + 9');
  expect(results.labels.space).toBe('Shift + Space');
  expect(results.labels.punctuation).toEqual([
    '[',
    ']',
    '`',
    ';',
    "'",
    ',',
    '.',
    '/',
    '\\',
    '-',
    '=',
  ]);
  expect(results.labels.fallbackCode).toBe('F8');
  expect(results.exactMatch).toBe(true);
  expect(results.wrongMod).toBe(false);
  expect(results.nullMatch).toBe(false);
});

test('loads defaults or valid stored hotkeys and falls back cleanly on malformed JSON', async ({
  canvas,
  page,
}) => {
  await mountHotkeyHarness(canvas, page);
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Shift + Space');
  await expect(page.locator('#howto-text-hotkey-display')).toHaveText('Shift + Space');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Shift + Space');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + Shift + [');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('Ctrl + Shift + [');

  // Invalid JSON falls back to defaults.
  await mountHotkeyHarness(canvas, page, {
    localStorage: {
      narratron_text_hotkey: '{broken-json',
      narratron_mic_hotkey: 'null',
    },
  });
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Shift + Space');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + Shift + [');

  // Valid stored hotkeys are restored and reflected in UI.
  await mountHotkeyHarness(canvas, page, {
    includeLegacyBtn: true,
    localStorage: {
      narratron_text_hotkey: JSON.stringify({
        code: 'KeyT',
        key: 't',
        altKey: true,
        ctrlKey: false,
        shiftKey: false,
      }),
      narratron_mic_hotkey: JSON.stringify({
        code: 'Period',
        key: '.',
        altKey: false,
        ctrlKey: true,
        shiftKey: false,
      }),
    },
  });
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Alt + T');
  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + .');
  await expect(page.locator('#legacy-hotkey-display')).toHaveText('Mic Hotkey: Ctrl + .');
});

test('rebinding ignores modifiers, cancels mutually and on Escape, flashes success, and persists', async ({
  canvas,
  page,
}) => {
  await mountHotkeyHarness(canvas, page, { includeLegacyBtn: true });

  const textBtn = page.locator('#menu-item-text-hotkey');
  const textDisplay = page.locator('#menu-text-hotkey-display');
  const micBtn = page.locator('#menu-item-hotkey');
  const micDisplay = page.locator('#menu-hotkey-display');
  const legacyBtn = page.locator('#legacy-mic-hotkey-btn');
  const legacyDisplay = page.locator('#legacy-hotkey-display');

  await textBtn.click();
  await expect(textDisplay).toHaveText('Press key combo to rebind...');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).rebindStartCount),
  ).toBe(1);
  expect(
    await page.evaluate(() =>
      (window as unknown as HotkeyHarnessWindow).hotkeyControls?.isRebindingTextHotkey(),
    ),
  ).toBe(true);

  for (const mod of ['ControlLeft', 'ControlRight', 'AltLeft', 'ShiftLeft', 'MetaLeft']) {
    await page.evaluate((code: string) => {
      window.dispatchEvent(new KeyboardEvent('keydown', { code, key: code, bubbles: true }));
    }, mod);
    await expect(textDisplay).toHaveText('Press key combo to rebind...');
  }

  // Starting mic rebind cancels text rebind and invokes onRebindStart.
  await micBtn.click();
  await expect(textDisplay).toHaveText('Text Hotkey: Shift + Space');
  await expect(micDisplay).toHaveText('Press key combo to rebind...');
  expect(
    await page.evaluate(() => {
      const ctrl = (window as unknown as HotkeyHarnessWindow).hotkeyControls!;
      return { mic: ctrl.isRebindingMicHotkey(), text: ctrl.isRebindingTextHotkey() };
    }),
  ).toEqual({ mic: true, text: false });

  // Escape cancels mic rebind without saving.
  await page.keyboard.press('Escape');
  await expect(micDisplay).toHaveText('Mic Hotkey: Ctrl + Shift + [');
  expect(await page.evaluate(() => localStorage.getItem('narratron_mic_hotkey'))).toBeNull();

  // Rebinding via legacy button sets active state, then completes on Alt+Digit9.
  await legacyBtn.click();
  await expect(legacyDisplay).toHaveText('Press key combo to rebind...');
  await page.keyboard.press('Alt+Digit9');
  await expect(legacyDisplay).toHaveText('Mic Hotkey: Alt + 9');
  await expect(micDisplay).toHaveText('Mic Hotkey: Alt + 9');
  await expect(page.locator('#howto-mic-hotkey-display')).toHaveText('Alt + 9');

  // Flash feedback clears after 600ms.
  await expect(legacyBtn).toHaveCSS('background-color', 'rgb(16, 185, 129)');
  await page.waitForTimeout(650);
  await expect(legacyBtn).toHaveCSS('background-color', 'rgba(255, 255, 255, 0.08)');

  // Rebind text hotkey to Ctrl+Alt+K and verify cancelRebinding() cleans up active rebinds.
  await textBtn.click();
  await page.evaluate(() => {
    (window as unknown as HotkeyHarnessWindow).hotkeyControls?.cancelRebinding();
  });
  await expect(textDisplay).toHaveText('Text Hotkey: Shift + Space');

  await textBtn.click();
  await page.keyboard.press('Control+Alt+KeyK');
  await expect(textDisplay).toHaveText('Text Hotkey: Ctrl + Alt + K');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Ctrl + Alt + K');
  await page.waitForTimeout(650);

  const stored = await page.evaluate(() => ({
    text: JSON.parse(localStorage.getItem('narratron_text_hotkey') ?? 'null') as HotkeySpec | null,
    mic: JSON.parse(localStorage.getItem('narratron_mic_hotkey') ?? 'null') as HotkeySpec | null,
  }));
  expect(stored.text).toMatchObject({ code: 'KeyK', altKey: true, ctrlKey: true, shiftKey: false });
  expect(stored.mic).toMatchObject({ code: 'Digit9', altKey: true, ctrlKey: false, shiftKey: false });
});

test('setMicHotkey and setTextHotkey update storage, UI, and active dispatch', async ({
  canvas,
  page,
}) => {
  await mountHotkeyHarness(canvas, page);

  await page.evaluate(() => {
    const ctrl = (window as unknown as HotkeyHarnessWindow).hotkeyControls!;
    ctrl.setMicHotkey('KeyM');
    ctrl.setTextHotkey({ code: 'Slash', key: '/', ctrlKey: true, shiftKey: true, altKey: false });
  });

  await expect(page.locator('#menu-hotkey-display')).toHaveText('Mic Hotkey: M');
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: Ctrl + Shift + /');
  await expect(page.locator('#orator-command-dismiss-hotkey')).toHaveText('Ctrl + Shift + /');

  await page.keyboard.press('KeyM');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).connectClickCount),
  ).toBe(1);

  await page.keyboard.press('Control+Shift+Slash');
  await expect(page.locator('#orator-command-overlay')).toHaveClass(/active/);
  await page.keyboard.press('Control+Shift+Slash');
  await expect(page.locator('#orator-command-overlay')).not.toHaveClass(/active/);

  await page.evaluate(() => {
    (window as unknown as HotkeyHarnessWindow).hotkeyControls?.setTextHotkey('KeyT');
  });
  await expect(page.locator('#menu-text-hotkey-display')).toHaveText('Text Hotkey: T');
});

test('orator command overlay opens, suppresses repeats/inputs/viewers, submits, and shows errors', async ({
  canvas,
  page,
}) => {
  let rejectNext = true;
  const submitted: Array<{ url: string; text: string }> = [];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/orator/command',
    async (route) => {
      const body = JSON.parse(route.request().postData() ?? '{}') as { text: string };
      submitted.push({ url: route.request().url(), text: body.text });
      if (rejectNext) {
        rejectNext = false;
        await route.fulfill({ status: 400, json: { detail: 'Theater busy' } });
        return;
      }
      await route.fulfill({ json: { ok: true } });
    },
  );

  await mountHotkeyHarness(canvas, page, { theaterId: 'my stage' });

  const overlay = page.locator('#orator-command-overlay');
  const input = page.locator('#orator-command-input');
  const status = page.locator('#orator-command-status');

  // Repeat keydown does not open.
  await page.evaluate(() => {
    window.dispatchEvent(
      new KeyboardEvent('keydown', {
        code: 'Space',
        key: ' ',
        shiftKey: true,
        repeat: true,
        bubbles: true,
      }),
    );
  });
  await expect(overlay).not.toHaveClass(/active/);

  // Editable elements suppress opening.
  for (const selector of ['#chat-input', '#notes-textarea', '#mic-select', '#editable-box']) {
    await page.locator(selector).focus();
    await page.keyboard.press('Shift+Space');
    await expect(overlay).not.toHaveClass(/active/);
  }
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());

  // Non-orator cannot open via hotkey or method.
  await page.evaluate(() => {
    (window as unknown as HotkeyHarnessWindow).isOratorFlag = false;
  });
  await page.keyboard.press('Shift+Space');
  await expect(overlay).not.toHaveClass(/active/);

  // Orator opens, stops scene speech, focuses input, and handles failed then successful submit.
  await page.evaluate(() => {
    (window as unknown as HotkeyHarnessWindow).isOratorFlag = true;
  });
  await page.keyboard.press('Shift+Space');
  await expect(overlay).toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'false');
  await expect(input).toBeFocused();
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).stopSpeechCount),
  ).toBe(1);

  await input.fill('Cast fireball');
  await input.press('Enter');
  await expect(status).toHaveText('Theater busy');
  await expect(overlay).toHaveClass(/active/);
  await expect(input).toBeFocused();

  await input.press('Enter');
  await expect(overlay).not.toHaveClass(/active/);
  await expect(overlay).toHaveAttribute('aria-hidden', 'true');
  await expect(input).toHaveValue('');
  expect(submitted).toEqual([
    { url: 'https://canvas.test/api/orator/command?theater_id=my%20stage', text: 'Cast fireball' },
    { url: 'https://canvas.test/api/orator/command?theater_id=my%20stage', text: 'Cast fireball' },
  ]);

  // Escape inside input closes and clears value.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('Shift+Space');
  await input.fill('Draft text');
  await page.keyboard.press('Escape');
  await expect(overlay).not.toHaveClass(/active/);
  await expect(input).toHaveValue('');
});

test('mic shortcut supports toggle, push-to-talk keydown/keyup, button-key Enter, and space guards', async ({
  canvas,
  page,
}) => {
  await mountHotkeyHarness(canvas, page);

  // Toggle mode clicks connect button unless disabled or suppressed.
  await page.keyboard.press('Control+Shift+BracketLeft');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).connectClickCount),
  ).toBe(1);

  await page.evaluate(() => {
    (document.getElementById('agent-connect-btn') as HTMLButtonElement).disabled = true;
  });
  await page.keyboard.press('Control+Shift+BracketLeft');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).connectClickCount),
  ).toBe(1);

  // Push-to-talk mode with hotkey and modifier release.
  await page.evaluate(() => {
    const win = window as unknown as HotkeyHarnessWindow;
    (document.getElementById('agent-connect-btn') as HTMLButtonElement).disabled = false;
    win.micModeFlag = 'push-to-talk';
  });

  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('BracketLeft');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).beginMicPressCalls),
  ).toEqual(['hotkey']);

  // Releasing a modifier key releases PTT press.
  await page.keyboard.up('Shift');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).releaseMicPressCount),
  ).toBe(1);
  await page.keyboard.up('BracketLeft');
  await page.keyboard.up('Control');

  // Focused connect button + Enter triggers button-key press/release.
  await page.locator('#agent-connect-btn').focus();
  await page.keyboard.down('Enter');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).beginMicPressCalls),
  ).toEqual(['hotkey', 'button-key']);
  await page.keyboard.up('Enter');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).releaseMicPressCount),
  ).toBe(2);

  // Focused agentStartStopBtn blocks Space / Shift+Space activation.
  await page.locator('#agent-start-stop-btn').focus();
  await page.keyboard.press('Space');
  await page.keyboard.press('Shift+Space');
  expect(
    await page.evaluate(() => (window as unknown as HotkeyHarnessWindow).startStopClickCount),
  ).toBe(0);
  await expect(page.locator('#orator-command-overlay')).not.toHaveClass(/active/);

  // Plain Space works inside editable #chat-input.
  await page.locator('#chat-input').focus();
  await page.keyboard.type('a b');
  await expect(page.locator('#chat-input')).toHaveValue('a b');

  // destroy() detaches listeners cleanly.
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    (window as unknown as HotkeyHarnessWindow).hotkeyControls?.destroy();
  });
  await page.keyboard.press('Shift+Space');
  await expect(page.locator('#orator-command-overlay')).not.toHaveClass(/active/);
});

import type { WebSocketRoute } from '@playwright/test';
import { expect, test } from '../../testing/ui/canvas_fixtures';

type SceneModule = typeof import('/static/generated/canvas/scene.js');
type SpeechReplayModule = typeof import('/static/generated/canvas/speech-replay.js');
type SceneSpeechState = import('/static/generated/canvas/scene.js').SceneSpeechState;

interface MockAudioRecord {
  src: string;
  paused: boolean;
  ended: boolean;
  onended: (() => void) | null;
  onerror: (() => void) | null;
}

interface AudioTrackingWindow extends Window {
  __playedAudio: MockAudioRecord[];
  __audioConstructorCalls: number;
}

const SCENE_MODULE_URL = '/static/generated/canvas/scene.js';
const SCENE_SHIM_URL = '/static/js/canvas-scene.js';
const SPEECH_REPLAY_MODULE_URL = '/static/generated/canvas/speech-replay.js';

function installMockAudioScript(): string {
  return `
    window.__playedAudio = [];
    window.__audioConstructorCalls = 0;
    window.Audio = class MockAudio {
      constructor(src = '') {
        window.__audioConstructorCalls += 1;
        this.src = src;
        this.paused = true;
        this.ended = false;
        this.muted = false;
        this.volume = 1;
        this.currentTime = 0;
        this.readyState = 0;
        this.onended = null;
        this.onerror = null;
        this._listeners = new Map();
        if (src) {
          window.__playedAudio.push(this);
        }
      }
      addEventListener(type, fn) {
        const list = this._listeners.get(type) || [];
        list.push(fn);
        this._listeners.set(type, list);
      }
      removeEventListener(type, fn) {
        const list = this._listeners.get(type) || [];
        this._listeners.set(type, list.filter((item) => item !== fn));
      }
      play() {
        this.paused = false;
        return Promise.resolve();
      }
      pause() {
        this.paused = true;
      }
      triggerEnded() {
        this.ended = true;
        this.paused = true;
        if (typeof this.onended === 'function') this.onended();
      }
    };
  `;
}

test('scene module and deprecated shim share identity and handle generation-aware speech, captions, and replay', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installMockAudioScript() });
  await canvas.mount({
    html: '<div id="narration-pane"></div><button id="replay-btn" type="button"></button>',
  });

  const result = await page.evaluate(
    async ({ sceneUrl, shimUrl, replayUrl }) => {
      const sceneMod = await window.__importModule<SceneModule>(sceneUrl);
      const shimMod = await window.__importModule<SceneModule>(shimUrl);
      const replayMod = await window.__importModule<SpeechReplayModule>(replayUrl);

      const overlay = document.getElementById('narration-pane') as HTMLElement;
      const replayBtn = document.getElementById('replay-btn') as HTMLButtonElement;
      const states: SceneSpeechState[] = [];
      let audioAllowed = true;

      const scene = sceneMod.createCanvasScene({
        narrationPane: {
          element: overlay,
          prepare: (caption) => {
            caption.tabIndex = 0;
          },
        },
        resolveUrl: (url) => `${url}?token=1`,
        audioEnabled: () => audioAllowed,
        onSpeechStateChange: (state) => {
          states.push(state);
          replayMod.updateSpeechReplayButtonUI(replayBtn, state);
        },
      });

      // Future-generation clip stays queued until generation 2 commits.
      scene.enqueueSceneSpeech('/future.mp3', 2);
      scene.updateSceneDialogue([], 'Opening curtain', null, 1);
      const beforeCommit = (window as unknown as AudioTrackingWindow).__playedAudio.length;

      // Committing generation 2 starts the queued clip and renders dialogue + narration.
      scene.updateSceneDialogue(
        [
          {
            speaker: 'Guide',
            text: 'Welcome',
            kind: 'thought',
            spans: [{ text: 'Welcome', color: 'rgb(255, 0, 0)', font: 'fantasy', effect: 'wave' }],
          },
        ],
        'Second scene',
        [{ text: 'Second scene', effect: 'glow' }],
        2,
      );
      const afterCommit = (window as unknown as AudioTrackingWindow).__playedAudio.length;
      const firstClipSrc = (window as unknown as AudioTrackingWindow).__playedAudio[0].src;
      const captionEl = overlay.querySelector<HTMLElement>('.scene-description');
      const bubbleEl = overlay.querySelector<HTMLElement>('.scene-dialogue-bubble');
      const captionText = captionEl?.textContent;
      const captionTabIndex = captionEl?.tabIndex;
      const bubbleIsThought = bubbleEl?.classList.contains('thought');
      const speakerText = bubbleEl?.querySelector('.scene-dialogue-speaker')?.textContent;

      // Older generation clip is ignored.
      scene.enqueueSceneSpeech('/stale.mp3', 1);
      const afterStale = (window as unknown as AudioTrackingWindow).__playedAudio.length;

      // Silencing blocks additional clips in generation 2 until replay or generation 3.
      scene.stopSceneSpeech();
      const pausedOnStop = (window as unknown as AudioTrackingWindow).__playedAudio[0].paused;
      const clearedSrcOnStop = (window as unknown as AudioTrackingWindow).__playedAudio[0].src;
      scene.enqueueSceneSpeech('/same-gen-after-stop.mp3', 2);
      const afterSameGen = (window as unknown as AudioTrackingWindow).__playedAudio.length;

      scene.replayLatestSpeech();
      const playedList = (window as unknown as AudioTrackingWindow).__playedAudio as Array<
        MockAudioRecord & { triggerEnded?: () => void }
      >;
      playedList[1]?.triggerEnded?.();
      const afterReplay = playedList.length;

      // Muted state suppresses playback but keeps latest speech available for later replay.
      audioAllowed = false;
      scene.stopSceneSpeech();
      scene.enqueueSceneSpeech('/muted-gen3.mp3', 3);
      scene.updateSceneDialogue([], 'Muted scene', null, 3);
      const afterMuted = (window as unknown as AudioTrackingWindow).__playedAudio.length;
      const hasLatestWhileMuted = scene.hasLatestSpeech();

      audioAllowed = true;
      scene.replayLatestSpeech();
      const afterUnmutedReplay = (window as unknown as AudioTrackingWindow).__playedAudio.length;

      return {
        sameCreateCanvasScene: sceneMod.createCanvasScene === shimMod.createCanvasScene,
        beforeCommit,
        afterCommit,
        afterStale,
        pausedOnStop,
        clearedSrcOnStop,
        afterSameGen,
        afterReplay,
        afterMuted,
        hasLatestWhileMuted,
        afterUnmutedReplay,
        firstClipSrc,
        lastClipSrc: (window as unknown as AudioTrackingWindow).__playedAudio.at(-1)?.src,
        captionText,
        captionTabIndex,
        bubbleIsThought,
        speakerText,
        replayBtnDisabled: replayBtn.disabled,
        replayBtnPlaying: replayBtn.classList.contains('playing'),
        stateCount: states.length,
      };
    },
    { sceneUrl: SCENE_MODULE_URL, shimUrl: SCENE_SHIM_URL, replayUrl: SPEECH_REPLAY_MODULE_URL },
  );

  expect(result.sameCreateCanvasScene).toBe(true);
  expect(result.beforeCommit).toBe(0);
  expect(result.afterCommit).toBe(1);
  expect(result.afterStale).toBe(1);
  expect(result.pausedOnStop).toBe(true);
  expect(result.clearedSrcOnStop).toBe('');
  expect(result.afterSameGen).toBe(1);
  expect(result.afterReplay).toBe(3);
  expect(result.afterMuted).toBe(3);
  expect(result.hasLatestWhileMuted).toBe(true);
  expect(result.afterUnmutedReplay).toBe(4);
  expect(result.firstClipSrc).toBe('/future.mp3?token=1');
  expect(result.lastClipSrc).toBe('/muted-gen3.mp3?token=1');
  expect(result.captionText).toBe('Second scene');
  expect(result.captionTabIndex).toBe(0);
  expect(result.bubbleIsThought).toBe(true);
  expect(result.speakerText).toBe('Guide');
  expect(result.replayBtnDisabled).toBe(false);
  expect(result.replayBtnPlaying).toBe(true);
  expect(result.stateCount).toBeGreaterThan(0);
});

test('speech replay controls handle button clicks, pointer propagation, and 1s VAD speech interruption', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: '<div id="outer"><button id="latest-speech-replay-btn" type="button" disabled></button></div>',
  });

  const outcome = await page.evaluate(async (replayUrl: string) => {
    const { createSpeechReplayControls } = await window.__importModule<SpeechReplayModule>(replayUrl);
    const btn = document.getElementById('latest-speech-replay-btn') as HTMLButtonElement;
    const outer = document.getElementById('outer') as HTMLElement;

    let replayCount = 0;
    let stopCount = 0;
    let outerPointerDowns = 0;
    let micMode = 'toggle';

    outer.addEventListener('pointerdown', () => {
      outerPointerDowns += 1;
    });

    const controls = createSpeechReplayControls({
      replayButton: btn,
      replayLatestSpeech: () => {
        replayCount += 1;
      },
      stopSceneSpeech: () => {
        stopCount += 1;
      },
      getMicMode: () => micMode,
      vadInterruptDelayMs: 120,
    });

    controls.updateButtonState({ hasLatestSpeech: false, playing: false });
    const disabledInitially = btn.disabled;
    const titleInitially = btn.title;

    controls.updateButtonState({ hasLatestSpeech: true, playing: true });
    const enabledAfterSpeech = !btn.disabled;
    const titleWhilePlaying = btn.title;
    const hasPlayingClass = btn.classList.contains('playing');

    btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    btn.click();

    // VAD start followed by vadstop before timeout must NOT interrupt speech.
    window.dispatchEvent(new Event('vadstart'));
    await new Promise((resolve) => window.setTimeout(resolve, 60));
    window.dispatchEvent(new Event('vadstop'));
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    const stopCountAfterBriefVad = stopCount;

    // Sustained VAD speech in push-to-talk mode must NOT interrupt speech.
    micMode = 'push-to-talk';
    window.dispatchEvent(new Event('narratron:speech-start'));
    await new Promise((resolve) => window.setTimeout(resolve, 160));
    const stopCountInPushToTalk = stopCount;
    window.dispatchEvent(new Event('narratron:speech-end'));

    // Sustained VAD speech in toggle mode interrupts after delay.
    micMode = 'toggle';
    window.dispatchEvent(new Event('vadstart'));
    await new Promise((resolve) => window.setTimeout(resolve, 180));
    const stopCountAfterSustainedVad = stopCount;

    controls.dispose();

    return {
      disabledInitially,
      titleInitially,
      enabledAfterSpeech,
      titleWhilePlaying,
      hasPlayingClass,
      outerPointerDowns,
      replayCount,
      stopCountAfterBriefVad,
      stopCountInPushToTalk,
      stopCountAfterSustainedVad,
    };
  }, SPEECH_REPLAY_MODULE_URL);

  expect(outcome.disabledInitially).toBe(true);
  expect(outcome.titleInitially).toBe('No speech to replay');
  expect(outcome.enabledAfterSpeech).toBe(true);
  expect(outcome.titleWhilePlaying).toBe('Replaying latest speech… (click to restart)');
  expect(outcome.hasPlayingClass).toBe(true);
  expect(outcome.outerPointerDowns).toBe(0);
  expect(outcome.replayCount).toBe(1);
  expect(outcome.stopCountAfterBriefVad).toBe(0);
  expect(outcome.stopCountInPushToTalk).toBe(0);
  expect(outcome.stopCountAfterSustainedVad).toBe(1);
});

test('canvas.html wires push-to-talk, orator text command, VAD 1s interruption, mute, and replay button to scene speech', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installMockAudioScript() });

  let stateSocket: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      narratron_mic_mode: 'push-to-talk',
      narratron_music_muted: 'false',
    },
    apiRoutes: {
      '/api/latest': {
        latest: '',
        time: 1,
        viewer_collab_enabled: true,
        scene_dialogue: [{ speaker: 'Narrator', text: 'A lantern flickers.', kind: 'speech' }],
        narration: 'In the courtyard',
        committed_scene_speech_generation: 1,
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/canvas-state') {
        stateSocket = ws;
        ws.send(JSON.stringify({ type: 'state_ready' }));
      }
    },
  });

  await expect.poll(() => stateSocket !== null).toBe(true);
  const replayBtn = page.locator('#latest-speech-replay-btn');
  await expect(replayBtn).toBeDisabled();

  // Trigger speech playback via state socket.
  stateSocket!.send(
    JSON.stringify({
      type: 'scene_speech_ready',
      audio_url: '/speech/gen1-a.mp3',
      speech_generation: 1,
    }),
  );

  await expect(replayBtn).toBeEnabled();
  await expect(replayBtn).toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio.length)).toBe(1);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[0].paused)).toBe(false);

  // 1. Push-to-talk mic press interrupts active speech playback.
  await page.locator('#agent-connect-btn').dispatchEvent('pointerdown', { button: 0, pointerId: 1 });
  await expect(replayBtn).not.toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[0].paused)).toBe(true);
  await page.locator('#agent-connect-btn').dispatchEvent('pointerup', { button: 0, pointerId: 1 });

  // Replay latest speech restarts playback.
  await replayBtn.click();
  await expect(replayBtn).toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio.length)).toBe(2);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[1].paused)).toBe(false);

  // 2. Opening orator text command (default Shift+Space) interrupts active speech playback.
  await page.keyboard.press('Shift+Space');
  await expect(page.locator('#orator-command-overlay')).toHaveClass(/active/);
  await expect(replayBtn).not.toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[1].paused)).toBe(true);
  await page.keyboard.press('Escape');

  // Replay again for mute test.
  await replayBtn.click();
  await expect(replayBtn).toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio.length)).toBe(3);

  // 3. Muting interrupts active speech playback.
  await page.locator('#music-mute-btn').click();
  await expect(replayBtn).not.toHaveClass(/playing/);
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[2].paused)).toBe(true);
});

test('canvas.html VAD 1000ms interruption stops active speech in toggle mic mode', async ({
  canvas,
  page,
}) => {
  await page.addInitScript({ content: installMockAudioScript() });

  let stateSocket: WebSocketRoute | null = null;
  await canvas.openCanvas({
    isOrator: true,
    localStorage: {
      narratron_mic_mode: 'toggle',
      narratron_music_muted: 'false',
    },
    apiRoutes: {
      '/api/latest': {
        latest: '',
        time: 1,
        viewer_collab_enabled: true,
        scene_dialogue: [{ speaker: 'Narrator', text: 'The gates open.', kind: 'speech' }],
        narration: 'At the gates',
        committed_scene_speech_generation: 1,
      },
    },
    onWebSocket: (ws) => {
      if (new URL(ws.url()).pathname === '/ws/canvas-state') {
        stateSocket = ws;
        ws.send(JSON.stringify({ type: 'state_ready' }));
      }
    },
  });

  await expect.poll(() => stateSocket !== null).toBe(true);
  stateSocket!.send(
    JSON.stringify({
      type: 'scene_speech_ready',
      audio_url: '/speech/vad-test.mp3',
      speech_generation: 1,
    }),
  );

  const replayBtn = page.locator('#latest-speech-replay-btn');
  await expect(replayBtn).toHaveClass(/playing/);

  // Dispatch vadstart and verify it does NOT stop immediately, then stops after 1000ms.
  await page.evaluate(() => {
    window.dispatchEvent(new Event('vadstart'));
  });
  await page.waitForTimeout(300);
  await expect(replayBtn).toHaveClass(/playing/);

  await expect(replayBtn).not.toHaveClass(/playing/, { timeout: 2500 });
  expect(await page.evaluate(() => (window as unknown as AudioTrackingWindow).__playedAudio[0].paused)).toBe(true);
});

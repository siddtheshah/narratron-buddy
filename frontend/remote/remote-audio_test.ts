import { expect, test, type CanvasHarness } from '../../testing/ui/canvas_fixtures';
import type {
    RemoteAudioController,
    RemoteAudioState,
    RemoteSpeechItem,
} from './remote-audio.js';

type RemoteAudioModule = typeof import('./remote-audio.js');

interface StubAudioRecord {
    src: string;
    paused: boolean;
    muted: boolean;
    volume: number;
    playCalls: number;
    loadCalls: number;
    onended: ((event: Event) => void) | null;
    onerror: ((event: Event) => void) | null;
    rejectNextPlayWith: Error | null;
    getAttribute(name: string): string | null;
    removeAttribute(name: string): void;
    play(): Promise<void>;
    pause(): void;
    load(): void;
    dispatchEvent(event: Event): boolean;
}

interface RemoteAudioTestWindow extends Window {
    stubAudioInstances: StubAudioRecord[];
    audioController: RemoteAudioController;
    audioErrors: number;
}

const CANONICAL_URL = '/static/generated/remote/remote-audio.js';

const AUDIO_HTML = `
  <div class="preview-frame" role="button" tabindex="0" aria-label="Unmute canvas audio" aria-pressed="false" style="width: 120px; height: 80px;">
    <span id="preview-audio-feedback" aria-hidden="true" hidden></span>
  </div>
`;

async function mountAudioHarness(
    canvas: CanvasHarness,
    moduleUrl: string = CANONICAL_URL,
): Promise<void> {
    await canvas.page.addInitScript(() => {
        const win = window as unknown as RemoteAudioTestWindow;
        win.stubAudioInstances = [];

        class StubAudio implements StubAudioRecord {
            src = '';
            paused = true;
            muted = false;
            volume = 1;
            playCalls = 0;
            loadCalls = 0;
            onended: ((event: Event) => void) | null = null;
            onerror: ((event: Event) => void) | null = null;
            rejectNextPlayWith: Error | null = null;

            constructor() {
                win.stubAudioInstances.push(this);
            }

            getAttribute(name: string): string | null {
                return name === 'src' ? (this.src || null) : null;
            }

            removeAttribute(name: string): void {
                if (name === 'src') {
                    this.src = '';
                }
            }

            play(): Promise<void> {
                this.playCalls += 1;
                if (this.rejectNextPlayWith) {
                    const err = this.rejectNextPlayWith;
                    this.rejectNextPlayWith = null;
                    return Promise.reject(err);
                }
                this.paused = false;
                return Promise.resolve();
            }

            pause(): void {
                this.paused = true;
            }

            load(): void {
                this.loadCalls += 1;
            }

            dispatchEvent(event: Event): boolean {
                if (event.type === 'ended') {
                    this.onended?.(event);
                } else if (event.type === 'error') {
                    this.onerror?.(event);
                }
                return true;
            }
        }

        (window as unknown as { Audio: typeof StubAudio }).Audio = StubAudio;
    });

    await canvas.mount({ html: AUDIO_HTML });

    await canvas.page.evaluate(async (url: string) => {
        const mod = await window.__importModule<RemoteAudioModule>(url);
        const win = window as unknown as RemoteAudioTestWindow;
        win.audioErrors = 0;
        const frame = document.querySelector<HTMLElement>('.preview-frame');
        const feedback = document.getElementById('preview-audio-feedback');
        if (!frame || !feedback) {
            throw new Error('Missing remote audio test DOM elements.');
        }
        win.audioController = mod.initializeRemoteAudio({
            frame,
            feedback,
            onError: () => {
                win.audioErrors += 1;
            },
        });
    }, moduleUrl);
}

test('defaults muted and toggles via click, Space, and Enter with feedback flash', async ({ canvas, page }) => {
    await page.clock.install();
    await mountAudioHarness(canvas, CANONICAL_URL);

    const frame = page.locator('.preview-frame');
    const feedback = page.locator('#preview-audio-feedback');

    await expect(frame).toHaveAttribute('aria-label', 'Unmute canvas audio');
    await expect(frame).toHaveAttribute('aria-pressed', 'false');
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioController.isUnmuted())).toBe(false);
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances.every((a) => a.muted))).toBe(true);

    await frame.click();
    await expect(frame).toHaveAttribute('aria-label', 'Mute canvas audio');
    await expect(frame).toHaveAttribute('aria-pressed', 'true');
    await expect(feedback).toBeVisible();
    await expect(feedback).toHaveText('🔊');
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioController.isUnmuted())).toBe(true);
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances.every((a) => !a.muted))).toBe(true);

    await page.clock.runFor(800);
    await expect(feedback).toBeHidden();

    await frame.press('Space');
    await expect(frame).toHaveAttribute('aria-label', 'Unmute canvas audio');
    await expect(feedback).toBeVisible();
    await expect(feedback).toHaveText('🔇');

    // Repeated Space keydown should not toggle mute again.
    await frame.dispatchEvent('keydown', { key: ' ', repeat: true });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioController.isUnmuted())).toBe(false);

    await frame.press('Enter');
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioController.isUnmuted())).toBe(true);
});

test('follows music playlist changes, pause state, track ended, and visibility', async ({ canvas, page }) => {
    await mountAudioHarness(canvas);

    const initialState: RemoteAudioState = {
        music: { music_id: 'rain', tracks: ['/audio/rain.mp3'], paused: false },
        committed_scene_speech_generation: 1,
    };

    await page.evaluate((state: RemoteAudioState) => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(state, true);
    }, initialState);

    expect(await page.evaluate(() => {
        const music = (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0];
        return { src: music.getAttribute('src'), paused: music.paused, muted: music.muted, volume: music.volume };
    })).toEqual({ src: '/audio/rain.mp3', paused: false, muted: true, volume: 0.5 });

    // Pausing and resuming music.
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(
            { music: { music_id: 'rain', tracks: ['/audio/rain.mp3'], paused: true } },
            true,
        );
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].paused)).toBe(true);

    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(
            { music: { music_id: 'rain', tracks: ['/audio/rain.mp3'], paused: false } },
            true,
        );
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].paused)).toBe(false);

    // Switching playlist selects new track.
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(
            { music: { music_id: 'forest', tracks: ['/audio/forest-a.mp3', '/audio/forest-b.mp3'], paused: false } },
            true,
        );
    });
    const firstTrack = await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].getAttribute('src'));
    expect(['/audio/forest-a.mp3', '/audio/forest-b.mp3']).toContain(firstTrack);

    // Ended event advances to the remaining track without repeating until exhausted.
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].dispatchEvent(new Event('ended'));
    });
    const secondTrack = await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].getAttribute('src'));
    expect(['/audio/forest-a.mp3', '/audio/forest-b.mp3']).toContain(secondTrack);
    expect(secondTrack).not.toBe(firstTrack);

    // Clearing visibility pauses playback; clearing tracks removes src.
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(
            { music: { music_id: 'forest', tracks: ['/audio/forest-a.mp3'], paused: false } },
            false,
        );
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].paused)).toBe(true);

    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update(
            { music: { music_id: null, tracks: [], paused: false } },
            true,
        );
    });
    expect(await page.evaluate(() => {
        const music = (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0];
        return { src: music.getAttribute('src'), paused: music.paused };
    })).toEqual({ src: null, paused: true });
});

test('queues pre-commit speech, drops stale clips, caps queue at 32, and handles errors', async ({ canvas, page }) => {
    await mountAudioHarness(canvas);

    await page.evaluate(() => {
        const win = window as unknown as RemoteAudioTestWindow;
        win.audioController.update({ committed_scene_speech_generation: 1 }, true);
        // Pre-commit clip for generation 2 waits until generation 2 is committed.
        win.audioController.enqueueSpeech({ speech_generation: 2, audio_url: '/speech/gen2-a.mp3' });
    });

    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].getAttribute('src'))).toBeNull();

    // Commit generation 2 -> buffered clip plays immediately.
    await page.evaluate(() => {
        const win = window as unknown as RemoteAudioTestWindow;
        win.audioController.update({ committed_scene_speech_generation: 2 }, true);
        win.audioController.enqueueSpeech({ speech_generation: 2, audio_url: '/speech/gen2-b.mp3' });
        // Stale generation 1 clip is ignored.
        win.audioController.enqueueSpeech({ speech_generation: 1, audio_url: '/speech/stale.mp3' });
    });

    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].getAttribute('src'))).toBe('/speech/gen2-a.mp3');

    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].dispatchEvent(new Event('ended'));
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].getAttribute('src'))).toBe('/speech/gen2-b.mp3');

    // Advancing generation clears active speech.
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).audioController.update({ committed_scene_speech_generation: 3 }, true);
    });
    expect(await page.evaluate(() => {
        const speech = (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1];
        return { src: speech.getAttribute('src'), paused: speech.paused };
    })).toEqual({ src: null, paused: true });

    // Queue cap at 32 items keeps the latest 32.
    await page.evaluate(() => {
        const win = window as unknown as RemoteAudioTestWindow;
        for (let i = 1; i <= 35; i += 1) {
            win.audioController.enqueueSpeech({ speech_generation: 4, audio_url: `/speech/${i}.mp3` });
        }
        win.audioController.update({ committed_scene_speech_generation: 4 }, true);
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].getAttribute('src'))).toBe('/speech/4.mp3');

    // Playback errors: AbortError ignored; real play error while muted ignored; real error while unmuted triggers onError.
    await page.evaluate(async () => {
        const win = window as unknown as RemoteAudioTestWindow;
        const music = win.stubAudioInstances[0];
        const abortErr = new Error('aborted');
        abortErr.name = 'AbortError';
        music.rejectNextPlayWith = abortErr;
        win.audioController.update({ music: { music_id: 'err', tracks: ['/audio/err.mp3'], paused: false } }, true);
        await Promise.resolve();
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioErrors)).toBe(0);

    // Unmute, then trigger music.onerror -> calls onError once.
    await page.locator('.preview-frame').click();
    await page.evaluate(() => {
        (window as unknown as RemoteAudioTestWindow).stubAudioInstances[0].dispatchEvent(new Event('error'));
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioErrors)).toBe(1);

    // Non-abort play() rejection while unmuted triggers onError and blocks element until mute toggle clears blocked set.
    await page.evaluate(async () => {
        const win = window as unknown as RemoteAudioTestWindow;
        const music = win.stubAudioInstances[0];
        const notAllowed = new Error('not allowed');
        notAllowed.name = 'NotAllowedError';
        music.rejectNextPlayWith = notAllowed;
        win.audioController.update({ music: { music_id: 'err2', tracks: ['/audio/err2.mp3'], paused: false } }, true);
        await Promise.resolve();
        await Promise.resolve();
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).audioErrors)).toBe(2);

    // Inactive update stops speech and music.
    await page.evaluate(() => {
        const win = window as unknown as RemoteAudioTestWindow;
        const item: RemoteSpeechItem = { speech_generation: 5, audio_url: '/speech/ignored.mp3' };
        win.audioController.update(null, false);
        win.audioController.enqueueSpeech(item);
    });
    expect(await page.evaluate(() => (window as unknown as RemoteAudioTestWindow).stubAudioInstances[1].getAttribute('src'))).toBeNull();
});

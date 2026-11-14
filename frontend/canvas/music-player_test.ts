import { expect, test } from '../../testing/ui/canvas_fixtures';

type MusicPlayerModule = typeof import('/static/generated/canvas/music-player.js');
type MusicPlayerController = import('/static/generated/canvas/music-player.js').MusicPlayerController;

interface MockAudioSnapshot {
  src: string;
  volume: number;
  muted: boolean;
  paused: boolean;
  currentTime: number;
  playCalls: number;
  pauseCalls: number;
}

interface MusicHarnessWindow extends Window {
  musicPlayer?: MusicPlayerController;
  mockAudios: MockAudioSnapshot[];
  mockAudioInstances: Array<{
    emit(eventName: string): void;
  }>;
  stoppedSpeechCount: number;
  trackChanges: string[];
}

const MUSIC_PLAYER_URL = '/static/generated/canvas/music-player.js';

const MUSIC_SHELL_HTML = `
  <div id="music-controls">
    <span id="music-status">🎵 Silent</span>
    <button id="music-mute-btn" type="button">🔊 Mute</button>
    <input type="range" id="music-volume-slider" min="0" max="1" step="0.01" value="0.5">
    <span id="volume-percent">50%</span>
  </div>
`;

function installMockAudioAndSetupScript(): string {
  return `
    window.mockAudios = [];
    window.mockAudioInstances = [];
    window.stoppedSpeechCount = 0;
    window.trackChanges = [];

    window.__createMockAudio = function () {
      const listeners = new Map();
      const record = {
        src: '',
        volume: 1,
        muted: false,
        paused: true,
        currentTime: 0,
        playCalls: 0,
        pauseCalls: 0,
      };
      const audio = {
        readyState: 4,
        onended: null,
        ontimeupdate: null,
        get src() { return record.src; },
        set src(value) { record.src = value; },
        get volume() { return record.volume; },
        set volume(value) { record.volume = value; },
        get muted() { return record.muted; },
        set muted(value) { record.muted = value; },
        get paused() { return record.paused; },
        get currentTime() { return record.currentTime; },
        set currentTime(value) { record.currentTime = value; },
        addEventListener(name, fn) {
          if (!listeners.has(name)) listeners.set(name, new Set());
          listeners.get(name).add(fn);
        },
        removeEventListener(name, fn) {
          listeners.get(name)?.delete(fn);
        },
        play() {
          record.playCalls += 1;
          record.paused = false;
          return Promise.resolve();
        },
        pause() {
          record.pauseCalls += 1;
          record.paused = true;
        },
        emit(eventName) {
          for (const fn of listeners.get(eventName) || []) {
            fn({ target: audio });
          }
        },
      };
      window.mockAudios.push(record);
      window.mockAudioInstances.push(audio);
      return audio;
    };
  `;
}

test('music player cycles through playlist without repeating until exhausted and prunes removed tracks', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MUSIC_SHELL_HTML,
    moduleScripts: [installMockAudioAndSetupScript()],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMusicPlayer } = await window.__importModule<MusicPlayerModule>(moduleUrl);
    const win = window as unknown as MusicHarnessWindow & {
      __createMockAudio: () => HTMLAudioElement;
    };
    win.musicPlayer = createMusicPlayer({
      theaterId: 'stage',
      muteButton: document.getElementById('music-mute-btn'),
      statusElement: document.getElementById('music-status'),
      volumeSlider: document.getElementById('music-volume-slider') as HTMLInputElement | null,
      volumePercentElement: document.getElementById('volume-percent'),
      onMuteStopSpeech: () => {
        win.stoppedSpeechCount += 1;
      },
      onTrackChange: (src: string) => {
        win.trackChanges.push(src);
      },
      createAudio: () => win.__createMockAudio(),
    });
  }, MUSIC_PLAYER_URL);

  // Play 3-track playlist and simulate 3 ended events (4 total plays across cycle boundary).
  const tracks = ['/t1.mp3', '/t2.mp3', '/t3.mp3'];
  const played = await page.evaluate(async (playlistTracks: string[]) => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.playPlaylist('tavern', playlistTracks);
    await Promise.resolve();
    for (let i = 0; i < 3; i += 1) {
      const latestInstance = win.mockAudioInstances[win.mockAudioInstances.length - 1];
      latestInstance.emit('ended');
      await Promise.resolve();
    }
    return win.trackChanges;
  }, tracks);

  expect(played).toHaveLength(4);
  // First 3 tracks cover every track in the playlist once without repetition.
  expect(new Set(played.slice(0, 3))).toEqual(new Set(tracks));
  // 4th track begins a new cycle and must not immediately repeat the 3rd track.
  expect(played[3]).not.toBe(played[2]);

  // Crossfade created a new audio instance for each track switch.
  const audioCount = await page.evaluate(
    () => (window as unknown as MusicHarnessWindow).mockAudios.length,
  );
  expect(audioCount).toBe(4);

  // Removing a track and continuing the playlist prunes deleted tracks without breaking cycling.
  const afterPrune = await page.evaluate(async () => {
    const win = window as unknown as MusicHarnessWindow;
    win.trackChanges.length = 0;
    win.musicPlayer!.playPlaylist('tavern', ['/t1.mp3', '/t2.mp3']);
    await Promise.resolve();
    win.mockAudioInstances[win.mockAudioInstances.length - 1].emit('ended');
    await Promise.resolve();
    return win.trackChanges;
  });
  expect(afterPrune).toHaveLength(2);
  expect(afterPrune.every((src) => src === '/t1.mp3' || src === '/t2.mp3')).toBe(true);
});

test('music player discards saved playback when server switches to a different playlist', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MUSIC_SHELL_HTML,
    localStorage: {
      'narratron-music-playback:stage': JSON.stringify({
        playlist: 'forest',
        track: '/forest-2.mp3',
        position: 42.5,
      }),
    },
    moduleScripts: [installMockAudioAndSetupScript()],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMusicPlayer } = await window.__importModule<MusicPlayerModule>(moduleUrl);
    const win = window as unknown as MusicHarnessWindow & {
      __createMockAudio: () => HTMLAudioElement;
    };
    win.musicPlayer = createMusicPlayer({
      theaterId: 'stage',
      muteButton: document.getElementById('music-mute-btn'),
      statusElement: document.getElementById('music-status'),
      volumeSlider: document.getElementById('music-volume-slider') as HTMLInputElement | null,
      volumePercentElement: document.getElementById('volume-percent'),
      playbackStorage: window.localStorage,
      createAudio: () => win.__createMockAudio(),
    });
    win.musicPlayer.applyMusicState({
      time: 5,
      playlist: 'dungeon',
      tracks: ['/dungeon-1.mp3'],
      paused: false,
    });
  }, MUSIC_PLAYER_URL);

  await expect(page.locator('#music-status')).toHaveText('🎵 dungeon');
  const result = await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    return {
      src: win.musicPlayer!.getCurrentTrackSrc(),
      currentTime: win.mockAudios[0].currentTime,
      stored: localStorage.getItem('narratron-music-playback:stage'),
    };
  });
  expect(result.src).toBe('/dungeon-1.mp3');
  expect(result.currentTime).toBe(0);
});

test('music player applies server music state, pauses, resumes, restores position, and stops', async ({
  canvas,
  page,
}) => {
  await canvas.mount({
    html: MUSIC_SHELL_HTML,
    localStorage: {
      'narratron-music-playback:stage': JSON.stringify({
        playlist: 'forest',
        track: '/forest-2.mp3',
        position: 42.5,
        playbackStates: [['forest', ['/forest-1.mp3', '/forest-2.mp3']]],
      }),
    },
    moduleScripts: [installMockAudioAndSetupScript()],
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { createMusicPlayer } = await window.__importModule<MusicPlayerModule>(moduleUrl);
    const win = window as unknown as MusicHarnessWindow & {
      __createMockAudio: () => HTMLAudioElement;
    };
    win.musicPlayer = createMusicPlayer({
      theaterId: 'stage',
      muteButton: document.getElementById('music-mute-btn'),
      statusElement: document.getElementById('music-status'),
      volumeSlider: document.getElementById('music-volume-slider') as HTMLInputElement | null,
      volumePercentElement: document.getElementById('volume-percent'),
      playbackStorage: window.localStorage,
      onMuteStopSpeech: () => {
        win.stoppedSpeechCount += 1;
      },
      onTrackChange: (src: string) => {
        win.trackChanges.push(src);
      },
      createAudio: () => win.__createMockAudio(),
    });
  }, MUSIC_PLAYER_URL);

  // Restores saved track and seek position when matching playlist arrives.
  await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.applyMusicState({
      time: 10,
      music_id: 'forest',
      tracks: ['/forest-1.mp3', '/forest-2.mp3'],
      paused: false,
    });
  });

  await expect(page.locator('#music-status')).toHaveText('🎵 forest');
  const afterRestore = await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    return {
      currentSrc: win.musicPlayer!.getCurrentTrackSrc(),
      currentTime: win.mockAudios[0].currentTime,
      paused: win.mockAudios[0].paused,
    };
  });
  expect(afterRestore).toEqual({
    currentSrc: '/forest-2.mp3',
    currentTime: 42.5,
    paused: false,
  });

  // Stale timestamp (<= 10) is ignored.
  await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.applyMusicState({
      time: 9,
      music_id: null,
      tracks: [],
    });
  });
  await expect(page.locator('#music-status')).toHaveText('🎵 forest');

  // Pausing updates status and pauses audio.
  await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.applyMusicState({
      time: 11,
      music_id: 'forest',
      tracks: ['/forest-1.mp3', '/forest-2.mp3'],
      paused: true,
    });
  });
  await expect(page.locator('#music-status')).toHaveText('🎵 forest (Paused)');
  expect(
    await page.evaluate(() => (window as unknown as MusicHarnessWindow).mockAudios[0].paused),
  ).toBe(true);

  // Resuming plays again.
  await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.applyMusicState({
      time: 12,
      music_id: 'forest',
      tracks: ['/forest-1.mp3', '/forest-2.mp3'],
      paused: false,
    });
  });
  await expect(page.locator('#music-status')).toHaveText('🎵 forest');
  expect(
    await page.evaluate(() => (window as unknown as MusicHarnessWindow).mockAudios[0].paused),
  ).toBe(false);

  // Mute button stops scene speech and persists state, volume slider > 0 auto-unmutes.
  await page.locator('#music-mute-btn').click();
  await expect(page.locator('#music-mute-btn')).toHaveText('🔇 Unmute');
  expect(
    await page.evaluate(() => (window as unknown as MusicHarnessWindow).stoppedSpeechCount),
  ).toBe(1);

  await page.locator('#music-volume-slider').evaluate((el) => {
    const input = el as HTMLInputElement;
    input.value = '0.7';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('#music-mute-btn')).toHaveText('🔊 Mute');
  await expect(page.locator('#volume-percent')).toHaveText('70%');

  // Clearing active playlist stops music (mute/silent path pauses and clears immediately).
  await page.locator('#music-mute-btn').click();
  await page.evaluate(() => {
    const win = window as unknown as MusicHarnessWindow;
    win.musicPlayer!.applyMusicState({
      time: 13,
      music_id: null,
      tracks: [],
    });
  });
  await expect(page.locator('#music-status')).toHaveText('🎵 Silent');
  expect(
    await page.evaluate(() => ({
      trackSrc: (window as unknown as MusicHarnessWindow).musicPlayer!.getCurrentTrackSrc(),
      audioSrc: (window as unknown as MusicHarnessWindow).mockAudios[0].src,
    })),
  ).toEqual({ trackSrc: null, audioSrc: '' });

  // Destroy detaches controls cleanly.
  await page.evaluate(() => {
    (window as unknown as MusicHarnessWindow).musicPlayer!.destroy();
  });
  await page.locator('#music-mute-btn').click();
  await expect(page.locator('#music-mute-btn')).toHaveText('🔇 Unmute');
});

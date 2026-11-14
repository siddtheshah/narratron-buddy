export const MUSIC_MUTED_STORAGE_KEY = 'narratron_music_muted';

export interface MusicStatePayload {
  readonly time: number;
  readonly paused?: boolean;
  readonly music_id?: string | null;
  readonly playlist?: string | null;
  readonly tracks?: readonly string[];
}

export interface PlaylistPlaybackState {
  playedTracks: Set<string>;
  lastPlayedTrack: string | null;
}

export interface SavedMusicPlaybackState {
  readonly playlist: string;
  readonly track: string;
  readonly position?: number;
  readonly playbackStates?: ReadonlyArray<readonly [string, readonly string[]]>;
}

export interface RestoredTrackResult {
  readonly track: string;
  readonly position: number;
}

export interface CreateMusicPlayerOptions {
  readonly theaterId: string;
  readonly muteButton: HTMLElement | null;
  readonly statusElement: HTMLElement | null;
  readonly volumeSlider: HTMLInputElement | null;
  readonly volumePercentElement: HTMLElement | null;
  readonly playbackStorage?: Storage | null;
  readonly onMuteStopSpeech?: () => void;
  readonly onTrackChange?: (trackSrc: string) => void;
  readonly createAudio?: () => HTMLAudioElement;
}

export interface MusicPlayerController {
  isMuted(): boolean;
  setMuted(muted: boolean): void;
  getCurrentTrackSrc(): string | null;
  setCurrentTrackSrc(src: string | null): void;
  applyMusicState(music: MusicStatePayload | null | undefined): void;
  playPlaylist(playlistName: string, tracks: readonly string[]): void;
  playTrack(src: string, startTime?: number): void;
  stopPlaylist(): void;
  savePlaybackState(): void;
  destroy(): void;
}

function readInitialMuted(): boolean {
  try {
    return localStorage.getItem(MUSIC_MUTED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function gentleFadeInCurve(progress: number): number {
  return Math.pow(progress, 2);
}

function gentleFadeOutCurve(progress: number): number {
  return Math.pow(1 - progress, 2);
}

/**
 * Manage background theater music playback, crossfade transitions, per-playlist
 * shuffle cycles, mute/volume persistence, and playback position restoration.
 */
export function createMusicPlayer(options: CreateMusicPlayerOptions): MusicPlayerController {
  const {
    theaterId,
    muteButton,
    statusElement,
    volumeSlider,
    volumePercentElement,
    playbackStorage = null,
    onMuteStopSpeech,
    onTrackChange,
    createAudio = () => new Audio(),
  } = options;

  let muted = readInitialMuted();
  let currentPlaylistTracks: string[] = [];
  // Each playlist keeps its own shuffle cycle. A track is added to its
  // played set as soon as it starts, so changing playlists does not reset
  // the cycle when the user later returns to it.
  const playlistPlaybackState = new Map<string, PlaylistPlaybackState>();
  let bgMusic: HTMLAudioElement | null = createAudio();
  let outgoingMusic: HTMLAudioElement | null = null;
  let isMusicPausedState = false;
  let lastPlaylistTime = 0;
  let activePlaylistName: string | null = null;
  let fadeInterval: number | null = null;
  let outgoingFadeInterval: number | null = null;
  let currentTrackSrc: string | null = null;
  let lastMusicStateSave = 0;
  const musicPlaybackStorageKey = `narratron-music-playback:${theaterId}`;

  function getSliderVolume(): number {
    if (!volumeSlider) return 0.5;
    const parsed = parseFloat(volumeSlider.value);
    return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.5;
  }

  function loadMusicPlaybackState(): SavedMusicPlaybackState | null {
    if (!playbackStorage) return null;
    try {
      const raw = playbackStorage.getItem(musicPlaybackStorageKey);
      if (!raw) return null;
      const saved = JSON.parse(raw) as Partial<SavedMusicPlaybackState> | null;
      if (!saved || !saved.playlist || !saved.track) return null;

      for (const [playlistName, playedTracks] of saved.playbackStates || []) {
        playlistPlaybackState.set(playlistName, {
          playedTracks: new Set(playedTracks),
          lastPlayedTrack: playedTracks[playedTracks.length - 1] || null,
        });
      }
      return {
        playlist: saved.playlist,
        track: saved.track,
        position: saved.position,
        playbackStates: saved.playbackStates,
      };
    } catch (error) {
      console.warn('Could not restore saved music playback state:', error);
      return null;
    }
  }

  let restoredMusicPlayback: SavedMusicPlaybackState | null = loadMusicPlaybackState();

  function savePlaybackState(): void {
    if (!playbackStorage || !activePlaylistName || !currentTrackSrc || !bgMusic) return;
    try {
      const payload: SavedMusicPlaybackState = {
        playlist: activePlaylistName,
        track: currentTrackSrc,
        position: Number.isFinite(bgMusic.currentTime) ? bgMusic.currentTime : 0,
        playbackStates: [...playlistPlaybackState.entries()].map(([name, state]) => [
          name,
          [...state.playedTracks],
        ]),
      };
      playbackStorage.setItem(musicPlaybackStorageKey, JSON.stringify(payload));
    } catch (error) {
      console.warn('Could not save music playback state:', error);
    }
  }

  function setMuted(nextMuted: boolean): void {
    muted = Boolean(nextMuted);
    try {
      localStorage.setItem(MUSIC_MUTED_STORAGE_KEY, String(muted));
    } catch {
      // Storage can be unavailable in private or restricted browser contexts.
    }
    if (bgMusic) bgMusic.muted = muted;
    if (outgoingMusic) outgoingMusic.muted = muted;
    if (muted) {
      onMuteStopSpeech?.();
    }
    if (muteButton) {
      muteButton.textContent = muted ? '🔇 Unmute' : '🔊 Mute';
      muteButton.style.background = muted ? 'rgba(239, 68, 68, 0.2)' : 'rgba(255,255,255,0.08)';
      muteButton.style.borderColor = muted ? 'rgba(239, 68, 68, 0.4)' : 'var(--glass-border)';
    }
  }

  function setupBgMusicListeners(audioObj: HTMLAudioElement | null): void {
    if (!audioObj) return;
    audioObj.addEventListener('ended', () => {
      if (audioObj === bgMusic) {
        playNextTrack();
      }
    });
    audioObj.addEventListener('timeupdate', () => {
      if (audioObj === bgMusic && Date.now() - lastMusicStateSave >= 1000) {
        lastMusicStateSave = Date.now();
        savePlaybackState();
      }
    });
  }

  function cancelFade(): void {
    if (fadeInterval !== null) {
      window.clearInterval(fadeInterval);
      fadeInterval = null;
    }
  }

  function cancelOutgoingFade(): void {
    if (outgoingFadeInterval !== null) {
      window.clearInterval(outgoingFadeInterval);
      outgoingFadeInterval = null;
    }
    if (outgoingMusic) {
      try {
        outgoingMusic.pause();
        outgoingMusic.src = '';
      } catch (e) {
        console.warn('Error stopping outgoing music:', e);
      }
      outgoingMusic = null;
    }
  }

  function fadeIn(durationMs = 3500): void {
    cancelFade();
    if (isMusicPausedState) {
      if (bgMusic) {
        try {
          bgMusic.pause();
        } catch {
          // Ignore media pause errors during teardown.
        }
      }
      return;
    }
    const targetVolume = getSliderVolume();
    if (muted || targetVolume === 0) {
      if (bgMusic) bgMusic.volume = targetVolume;
      return;
    }

    if (bgMusic) bgMusic.volume = 0;
    const startTime = Date.now();

    fadeInterval = window.setInterval(() => {
      if (isMusicPausedState) {
        cancelFade();
        if (bgMusic) {
          try {
            bgMusic.pause();
          } catch {
            // Ignore media pause errors.
          }
        }
        return;
      }
      const elapsed = Date.now() - startTime;
      const progress = Math.min(1, elapsed / durationMs);
      const currentTarget = getSliderVolume();
      const gentleVal = gentleFadeInCurve(progress) * currentTarget;

      if (bgMusic) {
        bgMusic.volume = Math.min(currentTarget, Math.max(0, gentleVal));
      }

      if (progress >= 1) {
        cancelFade();
        if (bgMusic && !muted) {
          bgMusic.volume = currentTarget;
        }
      }
    }, 30);
  }

  function fadeOut(onComplete?: () => void, durationMs = 2500): void {
    cancelFade();
    if (!bgMusic) {
      onComplete?.();
      return;
    }
    if (bgMusic.paused || muted || bgMusic.volume === 0) {
      try {
        bgMusic.pause();
      } catch {
        // Ignore media pause errors.
      }
      onComplete?.();
      return;
    }

    const startVolume = bgMusic.volume;
    const startTime = Date.now();

    fadeInterval = window.setInterval(() => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(1, elapsed / durationMs);
      const gentleVal = startVolume * gentleFadeOutCurve(progress);

      if (bgMusic) {
        bgMusic.volume = Math.max(0, gentleVal);
      }

      if (progress >= 1) {
        cancelFade();
        onComplete?.();
      }
    }, 30);
  }

  function getPlaylistPlaybackState(
    playlistName: string,
    tracks: readonly string[],
  ): PlaylistPlaybackState {
    let state = playlistPlaybackState.get(playlistName);
    if (!state) {
      state = { playedTracks: new Set(), lastPlayedTrack: null };
      playlistPlaybackState.set(playlistName, state);
    }

    // Keep the state valid if a playlist's contents change while the
    // canvas is open (for example, after tracks are added or removed).
    const availableTracks = new Set(tracks);
    state.playedTracks = new Set(
      [...state.playedTracks].filter((track) => availableTracks.has(track)),
    );
    return state;
  }

  function chooseNextTrack(playlistName: string, tracks: readonly string[]): string {
    const state = getPlaylistPlaybackState(playlistName, tracks);
    let candidates = tracks.filter((track) => !state.playedTracks.has(track));

    // Every song has played once: begin a new shuffle cycle.
    if (candidates.length === 0) {
      state.playedTracks.clear();
      candidates = [...tracks];
      // Avoid an immediate repeat at the cycle boundary when there
      // is another track to choose from.
      if (candidates.length > 1 && state.lastPlayedTrack) {
        candidates = candidates.filter((track) => track !== state.lastPlayedTrack);
      }
    }

    const track = candidates[Math.floor(Math.random() * candidates.length)];
    state.playedTracks.add(track);
    state.lastPlayedTrack = track;
    return track;
  }

  function getRestoredTrack(
    playlistName: string,
    tracks: readonly string[],
  ): RestoredTrackResult | null {
    if (!restoredMusicPlayback) return null;

    if (restoredMusicPlayback.playlist !== playlistName) {
      // The agent selected a different playlist while this viewer was
      // away, so its old track must not override the current choice.
      restoredMusicPlayback = null;
      try {
        playbackStorage?.removeItem(musicPlaybackStorageKey);
      } catch {
        // Ignore storage errors.
      }
      return null;
    }

    const track = tracks.includes(restoredMusicPlayback.track)
      ? restoredMusicPlayback.track
      : null;
    const position = Math.max(0, Number(restoredMusicPlayback.position) || 0);
    restoredMusicPlayback = null;
    return track ? { track, position } : null;
  }

  function startTrack(src: string, startTime = 0, fadeDurationMs = 3500): void {
    currentTrackSrc = src;
    onTrackChange?.(src);
    if (!bgMusic) return;
    bgMusic.src = src;
    bgMusic.muted = muted;
    if (startTime > 0) {
      const targetAudio = bgMusic;
      const restorePosition = (): void => {
        targetAudio.currentTime = startTime;
      };
      if (targetAudio.readyState >= HTMLMediaElement.HAVE_METADATA) {
        restorePosition();
      } else {
        targetAudio.addEventListener('loadedmetadata', restorePosition, { once: true });
      }
    }
    const targetVol = getSliderVolume();
    if (!muted && targetVol > 0) {
      bgMusic.volume = 0;
    } else {
      bgMusic.volume = targetVol;
    }
    const playPromise = bgMusic.play();
    if (playPromise && typeof playPromise.then === 'function') {
      playPromise
        .then(() => {
          if (isMusicPausedState) {
            if (bgMusic) {
              try {
                bgMusic.pause();
              } catch {
                // Ignore media pause errors.
              }
            }
            return;
          }
          fadeIn(fadeDurationMs);
          savePlaybackState();
        })
        .catch((err: unknown) => {
          console.warn(
            'Audio autoplay blocked/failed. User interaction may be required. Error:',
            err,
          );
        });
    }
  }

  function playTrack(src: string, startTime = 0): void {
    const blendDuration = 3500;
    if (bgMusic && !bgMusic.paused && bgMusic.src && currentTrackSrc && currentTrackSrc !== src) {
      // Crossfade / blend outgoing music with incoming track
      cancelOutgoingFade();
      outgoingMusic = bgMusic;
      const outgoingStartVol = outgoingMusic.volume;
      const fadeOutStartTime = Date.now();

      outgoingMusic.onended = null;
      outgoingMusic.ontimeupdate = null;

      outgoingFadeInterval = window.setInterval(() => {
        const elapsed = Date.now() - fadeOutStartTime;
        const progress = Math.min(1, elapsed / blendDuration);
        const gentleVal = outgoingStartVol * gentleFadeOutCurve(progress);
        if (outgoingMusic) {
          outgoingMusic.volume = Math.max(0, gentleVal);
        }
        if (progress >= 1) {
          cancelOutgoingFade();
        }
      }, 30);

      bgMusic = createAudio();
      setupBgMusicListeners(bgMusic);
      startTrack(src, startTime, blendDuration);
    } else {
      startTrack(src, startTime, blendDuration);
    }
  }

  function playPlaylist(playlistName: string, tracks: readonly string[]): void {
    activePlaylistName = playlistName;
    currentPlaylistTracks = [...tracks];
    if (currentPlaylistTracks.length > 0) {
      playTrack(chooseNextTrack(playlistName, currentPlaylistTracks));
    }
  }

  function playNextTrack(): void {
    if (currentPlaylistTracks.length === 0 || !activePlaylistName) return;
    playTrack(chooseNextTrack(activePlaylistName, currentPlaylistTracks));
  }

  function stopPlaylist(): void {
    cancelOutgoingFade();
    fadeOut(() => {
      if (bgMusic) {
        bgMusic.pause();
        bgMusic.src = '';
      }
      currentPlaylistTracks = [];
      currentTrackSrc = null;
    }, 2500);
  }

  function applyMusicState(music: MusicStatePayload | null | undefined): void {
    if (!music || !(music.time > lastPlaylistTime)) return;
    lastPlaylistTime = music.time;
    isMusicPausedState = Boolean(music.paused);
    const activeMusicId = music.music_id || music.playlist || null;
    const tracks = Array.isArray(music.tracks) ? music.tracks : [];
    if (activeMusicId) {
      if (activePlaylistName !== activeMusicId) {
        activePlaylistName = activeMusicId;
        if (statusElement) {
          statusElement.textContent = `\u{1F3B5} ${activeMusicId}`;
        }
        const restoredTrack = getRestoredTrack(activeMusicId, tracks);
        if (restoredTrack) {
          currentPlaylistTracks = [...tracks];
          playTrack(restoredTrack.track, restoredTrack.position);
        } else {
          playPlaylist(activeMusicId, tracks);
        }
      }

      if (music.paused) {
        if (statusElement) {
          statusElement.textContent = `\u{1F3B5} ${activeMusicId} (Paused)`;
        }
        if (bgMusic) {
          try {
            bgMusic.pause();
          } catch {
            // Ignore pause error.
          }
        }
        fadeOut(() => {
          if (bgMusic) {
            try {
              bgMusic.pause();
            } catch {
              // Ignore pause error.
            }
          }
        }, 300);
      } else {
        if (statusElement) {
          statusElement.textContent = `\u{1F3B5} ${activeMusicId}`;
        }
        if (bgMusic && bgMusic.paused && currentPlaylistTracks.length > 0) {
          bgMusic.volume = 0;
          const playPromise = bgMusic.play();
          if (playPromise && typeof playPromise.then === 'function') {
            playPromise.then(() => fadeIn(1500)).catch((err: unknown) => console.warn(err));
          }
        }
      }
    } else {
      activePlaylistName = null;
      if (statusElement) {
        statusElement.textContent = '\u{1F3B5} Silent';
      }
      stopPlaylist();
    }
  }

  // Initialize volume and mute UI
  bgMusic.volume = getSliderVolume();
  setMuted(muted);
  setupBgMusicListeners(bgMusic);

  const onBeforeUnload = (): void => savePlaybackState();
  window.addEventListener('beforeunload', onBeforeUnload);

  const onMuteClick = (): void => {
    setMuted(!muted);
    if (muted) {
      cancelFade();
    } else if (bgMusic && bgMusic.paused && currentPlaylistTracks.length > 0) {
      bgMusic.volume = 0;
      const playPromise = bgMusic.play();
      if (playPromise && typeof playPromise.then === 'function') {
        playPromise.then(() => fadeIn(2500)).catch(() => {});
      }
    } else {
      fadeIn(2000);
    }
  };
  muteButton?.addEventListener('click', onMuteClick);

  const onVolumeInput = (e: Event): void => {
    cancelFade();
    const target = e.target as HTMLInputElement;
    const volume = parseFloat(target.value);
    if (bgMusic && Number.isFinite(volume)) {
      bgMusic.volume = volume;
    }
    if (volumePercentElement && Number.isFinite(volume)) {
      volumePercentElement.textContent = `${Math.round(volume * 100)}%`;
    }
    if (Number.isFinite(volume) && volume > 0 && muted) {
      setMuted(false);
    }
  };
  volumeSlider?.addEventListener('input', onVolumeInput);

  return {
    isMuted: () => muted,
    setMuted,
    getCurrentTrackSrc: () => currentTrackSrc,
    setCurrentTrackSrc: (src: string | null) => {
      currentTrackSrc = src;
    },
    applyMusicState,
    playPlaylist,
    playTrack,
    stopPlaylist,
    savePlaybackState,
    destroy: () => {
      cancelFade();
      cancelOutgoingFade();
      if (bgMusic) {
        try {
          bgMusic.pause();
          bgMusic.src = '';
        } catch {
          // Ignore teardown errors.
        }
        bgMusic = null;
      }
      window.removeEventListener('beforeunload', onBeforeUnload);
      muteButton?.removeEventListener('click', onMuteClick);
      volumeSlider?.removeEventListener('input', onVolumeInput);
    },
  };
}

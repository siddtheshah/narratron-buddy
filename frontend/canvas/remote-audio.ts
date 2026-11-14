// Watch is an independent listener, using the same music and speech as the canvas.

export interface RemoteMusicState {
    readonly music_id?: string | null;
    readonly tracks?: readonly string[] | null;
    readonly paused?: boolean;
}

export interface RemoteAudioState {
    readonly music?: RemoteMusicState | null;
    readonly committed_scene_speech_generation?: number | null;
}

export interface RemoteSpeechItem {
    readonly audio_url?: string | null;
    readonly speech_generation?: number | null;
}

export interface InitializeRemoteAudioOptions {
    readonly frame: HTMLElement;
    readonly feedback: HTMLElement;
    readonly onError: () => void;
}

export interface RemoteAudioController {
    isUnmuted(): boolean;
    update(state: RemoteAudioState | null | undefined, visible: boolean): void;
    enqueueSpeech(item: RemoteSpeechItem): void;
}

interface QueuedRemoteSpeech {
    readonly audio_url: string;
    readonly speech_generation: number;
}

function isAbortError(error: unknown): boolean {
    return typeof error === 'object' && error !== null && 'name' in error && (error as { name: unknown }).name === 'AbortError';
}

export function initializeRemoteAudio({ frame, feedback, onError }: InitializeRemoteAudioOptions): RemoteAudioController {
    const music = new Audio();
    const speech = new Audio();
    music.muted = speech.muted = true;
    music.volume = 0.5;
    let muted = true;
    let active = false;
    let musicState: RemoteMusicState | null = null;
    let playlistId: string | null = null;
    let tracksSignature = '';
    let remainingTracks: string[] = [];
    let speechGeneration: number | null = null;
    let playingGeneration: number | null = null;
    let speechQueue: QueuedRemoteSpeech[] = [];
    let feedbackTimer: number | undefined;
    const blocked = new Set<HTMLAudioElement>();

    function renderMute(flash = false): void {
        frame.setAttribute('aria-label', muted ? 'Unmute canvas audio' : 'Mute canvas audio');
        frame.setAttribute('aria-pressed', String(!muted));
        frame.title = muted ? 'Tap to unmute canvas audio' : 'Tap to mute canvas audio';
        if (!flash) return;
        window.clearTimeout(feedbackTimer);
        feedback.textContent = muted ? '🔇' : '🔊';
        feedback.hidden = false;
        feedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, 800);
    }

    function play(audio: HTMLAudioElement): void {
        if (!active || !audio.getAttribute('src') || !audio.paused || blocked.has(audio)) return;
        const source = audio.getAttribute('src');
        audio.play().catch((error: unknown) => {
            // Pausing or replacing a source can cancel an outstanding play call.
            if (isAbortError(error) || !active || audio.getAttribute('src') !== source) return;
            blocked.add(audio);
            if (!muted && active) onError();
        });
    }

    function clearSpeech(): void {
        speech.pause();
        speech.removeAttribute('src');
        speech.load();
        playingGeneration = null;
    }

    function nextSpeech(): void {
        if (!active || playingGeneration !== null) return;
        if (speechGeneration !== null) {
            const currentGeneration = speechGeneration;
            speechQueue = speechQueue.filter((item) => item.speech_generation >= currentGeneration);
        }
        const index = speechQueue.findIndex((item) => item.speech_generation === speechGeneration);
        if (index < 0) return;
        const [item] = speechQueue.splice(index, 1);
        playingGeneration = item.speech_generation;
        speech.src = item.audio_url;
        blocked.delete(speech);
        play(speech);
    }

    function nextTrack(): void {
        if (!musicState?.tracks?.length || !playlistId) return;
        if (!remainingTracks.length) remainingTracks = [...musicState.tracks];
        const index = Math.floor(Math.random() * remainingTracks.length);
        music.src = remainingTracks.splice(index, 1)[0];
        blocked.delete(music);
        if (!musicState.paused) play(music);
    }

    music.onended = nextTrack;
    speech.onended = speech.onerror = () => {
        clearSpeech();
        nextSpeech();
    };
    music.onerror = () => {
        blocked.add(music);
        if (!muted && active) onError();
    };

    function toggleMute(): void {
        muted = !muted;
        music.muted = speech.muted = muted;
        blocked.clear();
        renderMute(true);
        if (!musicState?.paused) play(music);
        play(speech);
    }
    frame.addEventListener('click', toggleMute);
    frame.addEventListener('keydown', (event: KeyboardEvent) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        if (!event.repeat) toggleMute();
    });
    renderMute();

    return {
        isUnmuted(): boolean {
            return !muted;
        },
        update(state: RemoteAudioState | null | undefined, visible: boolean): void {
            active = Boolean(state && visible);
            musicState = state?.music || null;
            if (!active || !state) {
                music.pause();
                speechQueue = [];
                clearSpeech();
                return;
            }
            const generation = state.committed_scene_speech_generation;
            if (typeof generation === 'number' && Number.isInteger(generation) && generation !== speechGeneration) {
                speechGeneration = generation;
                if (playingGeneration !== null && playingGeneration !== generation) clearSpeech();
            }
            nextSpeech();
            const nextId = musicState?.music_id || null;
            const signature = JSON.stringify(musicState?.tracks || []);
            if (nextId !== playlistId || signature !== tracksSignature) {
                playlistId = nextId;
                tracksSignature = signature;
                remainingTracks = [];
                music.pause();
                music.removeAttribute('src');
                music.load();
                nextTrack();
            }
            if (!playlistId || !musicState?.tracks?.length || musicState.paused) music.pause();
            else play(music);
        },
        enqueueSpeech(item: RemoteSpeechItem): void {
            if (!active || !item.audio_url || typeof item.speech_generation !== 'number' || !Number.isInteger(item.speech_generation)) return;
            if (speechGeneration !== null && item.speech_generation < speechGeneration) return;
            // Synthesis can finish before the matching scene is committed.
            speechQueue.push({
                audio_url: item.audio_url,
                speech_generation: item.speech_generation,
            });
            speechQueue = speechQueue.slice(-32);
            nextSpeech();
        },
    };
}

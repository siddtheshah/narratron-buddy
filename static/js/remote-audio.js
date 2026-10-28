// Watch is an independent listener, using the same music and speech as the canvas.
export function initializeRemoteAudio({ frame, feedback, onError }) {
    const music = new Audio();
    const speech = new Audio();
    music.muted = speech.muted = true;
    music.volume = 0.5;
    let muted = true;
    let active = false;
    let musicState = null;
    let playlistId = null;
    let tracksSignature = '';
    let remainingTracks = [];
    let speechGeneration = null;
    let playingGeneration = null;
    let speechQueue = [];
    let feedbackTimer;
    const blocked = new Set();

    function renderMute(flash = false) {
        frame.setAttribute('aria-label', muted ? 'Unmute canvas audio' : 'Mute canvas audio');
        frame.setAttribute('aria-pressed', String(!muted));
        frame.title = muted ? 'Tap to unmute canvas audio' : 'Tap to mute canvas audio';
        if (!flash) return;
        clearTimeout(feedbackTimer);
        feedback.textContent = muted ? '🔇' : '🔊';
        feedback.hidden = false;
        feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 800);
    }

    function play(audio) {
        if (!active || !audio.getAttribute('src') || !audio.paused || blocked.has(audio)) return;
        const source = audio.getAttribute('src');
        audio.play().catch(error => {
            // Pausing or replacing a source can cancel an outstanding play call.
            if (error.name === 'AbortError' || !active || audio.getAttribute('src') !== source) return;
            blocked.add(audio);
            if (!muted && active) onError();
        });
    }

    function clearSpeech() {
        speech.pause();
        speech.removeAttribute('src');
        speech.load();
        playingGeneration = null;
    }

    function nextSpeech() {
        if (!active || playingGeneration !== null) return;
        speechQueue = speechQueue.filter(item => item.speech_generation >= speechGeneration);
        const index = speechQueue.findIndex(item => item.speech_generation === speechGeneration);
        if (index < 0) return;
        const [item] = speechQueue.splice(index, 1);
        playingGeneration = item.speech_generation;
        speech.src = item.audio_url;
        blocked.delete(speech);
        play(speech);
    }

    function nextTrack() {
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

    function toggleMute() {
        muted = !muted;
        music.muted = speech.muted = muted;
        blocked.clear();
        renderMute(true);
        if (!musicState?.paused) play(music);
        play(speech);
    }
    frame.addEventListener('click', toggleMute);
    frame.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        if (!event.repeat) toggleMute();
    });
    renderMute();

    return {
        update(state, visible) {
            active = Boolean(state && visible);
            musicState = state?.music || null;
            if (!active) {
                music.pause();
                speechQueue = [];
                clearSpeech();
                return;
            }
            const generation = state.committed_scene_speech_generation;
            if (Number.isInteger(generation) && generation !== speechGeneration) {
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
        enqueueSpeech(item) {
            if (!active || !item.audio_url || !Number.isInteger(item.speech_generation)) return;
            if (speechGeneration !== null && item.speech_generation < speechGeneration) return;
            // Synthesis can finish before the matching scene is committed.
            speechQueue.push(item);
            speechQueue = speechQueue.slice(-32);
            nextSpeech();
        },
    };
}

/** Shared scene captions, dialogue effects, and generation-aware speech playback. */
export function createCanvasScene({ narrationPane, resolveUrl = url => url, audioEnabled = () => true }) {
    const sceneDialogueOverlay = narrationPane.element;
    let sceneDialogueSignature = '';
    let sceneDialogueRenderId = 0;
    const dialogueSpeakerColors = new Map();
    const dialogueSpeakerPalette = ['#fbbf24', '#67e8f9', '#f9a8d4', '#a7f3d0', '#c4b5fd', '#fdba74', '#93c5fd', '#f0abfc'];
    const sceneSpeechQueue = [];
    let sceneSpeechPlaying = false;
    let currentSceneAudio = null;
    let committedSceneSpeechGeneration = null;

    function stopSceneSpeech() {
        sceneSpeechQueue.length = 0;
        sceneSpeechPlaying = false;
        if (currentSceneAudio) {
            const audio = currentSceneAudio;
            currentSceneAudio = null;
            audio.onended = null;
            audio.onerror = null;
            try {
                audio.pause();
                audio.src = '';
            } catch (_) { }
        }
    }

    function playQueuedSceneSpeech() {
        if (sceneSpeechPlaying) return;
        const playNext = () => {
            // Keep audio from a future generation queued: synthesis can
            // finish before its scene state is committed and rendered.
            while (
                sceneSpeechQueue.length > 0
                && sceneSpeechQueue[0].speechGeneration < committedSceneSpeechGeneration
            ) {
                sceneSpeechQueue.shift();
            }
            const nextIndex = sceneSpeechQueue.findIndex(
                item => item.speechGeneration === committedSceneSpeechGeneration,
            );
            if (nextIndex < 0) {
                sceneSpeechPlaying = false;
                currentSceneAudio = null;
                return;
            }
            const [next] = sceneSpeechQueue.splice(nextIndex, 1);
            sceneSpeechPlaying = true;
            const audio = new Audio(next.audioUrl);
            currentSceneAudio = audio;
            audio.onended = audio.onerror = () => {
                if (currentSceneAudio === audio) {
                    currentSceneAudio = null;
                }
                playNext();
            };
            audio.play().catch(() => {
                if (currentSceneAudio === audio) {
                    currentSceneAudio = null;
                }
                playNext();
            });
        };
        playNext();
    }

    function enqueueSceneSpeech(audioUrl, speechGeneration) {
        if (!audioEnabled() || !audioUrl || !Number.isInteger(speechGeneration)) return;
        if (committedSceneSpeechGeneration !== null && speechGeneration < committedSceneSpeechGeneration) return;
        sceneSpeechQueue.push({ audioUrl: resolveUrl(audioUrl), speechGeneration });
        playQueuedSceneSpeech();
    }

    function setCommittedSceneSpeechGeneration(speechGeneration) {
        if (!Number.isInteger(speechGeneration)) return;
        if (speechGeneration === committedSceneSpeechGeneration) {
            playQueuedSceneSpeech();
            return;
        }
        if (committedSceneSpeechGeneration !== null && speechGeneration < committedSceneSpeechGeneration) return;

        committedSceneSpeechGeneration = speechGeneration;
        // Interrupt older speech, retaining audio for this scene and any
        // future scene whose synthesis finished before its state arrived.
        const pendingAudio = sceneSpeechQueue.filter(item => item.speechGeneration >= speechGeneration);
        stopSceneSpeech();
        sceneSpeechQueue.push(...pendingAudio);
        playQueuedSceneSpeech();
    }

    function getDialogueSpeakerColor(speaker) {
        if (!dialogueSpeakerColors.has(speaker)) {
            dialogueSpeakerColors.set(speaker, dialogueSpeakerPalette[dialogueSpeakerColors.size % dialogueSpeakerPalette.length]);
        }
        return dialogueSpeakerColors.get(speaker);
    }

    function createSpanElement(span) {
        const el = document.createElement('span');
        el.className = 'text-span';
        if (span.effect && span.effect !== 'none') {
            el.classList.add(`text-effect-${span.effect}`);
        }
        if (span.font && span.font !== 'default') {
            el.classList.add(`text-font-${span.font}`);
        }
        if (span.color) {
            el.style.color = span.color;
        }
        return el;
    }

    function applySplittingIfAvailable(element) {
        if (typeof window.Splitting === 'function') {
            try {
                window.Splitting({ target: element, by: 'chars' });
            } catch (e) {
                console.warn('Splitting error:', e);
            }
        }
    }

    function updateSceneDialogue(dialogue, narration, narration_spans, sceneSpeechGeneration) {
        if (!sceneDialogueOverlay) return;
        setCommittedSceneSpeechGeneration(sceneSpeechGeneration);
        const lines = (Array.isArray(dialogue) ? dialogue : [])
            .filter(item => item && item.text)
            .map(item => ({
                speaker: String(item.speaker || 'Narrator'),
                text: String(item.text),
                kind: item.kind === 'thought' ? 'thought' : 'speech',
                spans: Array.isArray(item.spans) ? item.spans : null,
            }));
        const description = String(narration || '').trim();
        const nSpans = Array.isArray(narration_spans) ? narration_spans : null;
        const signature = JSON.stringify({ lines, description, nSpans });
        if (signature === sceneDialogueSignature) return;
        sceneDialogueSignature = signature;
        const renderId = ++sceneDialogueRenderId;
        sceneDialogueOverlay.replaceChildren();
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        if (description) {
            const caption = document.createElement('div');
            caption.className = 'narration scene-narration scene-description';
            narrationPane.prepare(caption);
            const spansToRender = nSpans && nSpans.length > 0
                ? nSpans
                : [{ text: description, effect: 'none', font: 'default' }];
            for (const s of spansToRender) {
                const spanEl = createSpanElement(s);
                spanEl.textContent = s.text;
                caption.appendChild(spanEl);
                if (s.effect && s.effect !== 'none' && !reduceMotion) {
                    applySplittingIfAvailable(spanEl);
                }
            }
            sceneDialogueOverlay.appendChild(caption);
        }

        (async () => {
            for (const item of lines) {
                if (renderId !== sceneDialogueRenderId) return;
                const bubble = document.createElement('div');
                bubble.className = `scene-dialogue-bubble ${item.kind}`;
                const speaker = document.createElement('span');
                speaker.className = 'scene-dialogue-speaker';
                speaker.textContent = item.speaker;
                speaker.style.color = getDialogueSpeakerColor(item.speaker);
                const textContainer = document.createElement('span');
                textContainer.className = 'scene-dialogue-text';
                bubble.append(speaker, textContainer);
                sceneDialogueOverlay.appendChild(bubble);

                const lineSpans = item.spans && item.spans.length > 0
                    ? item.spans
                    : [{ text: item.text, effect: 'none', font: 'default' }];

                if (reduceMotion) {
                    for (const s of lineSpans) {
                        const spanEl = createSpanElement(s);
                        spanEl.textContent = s.text;
                        textContainer.appendChild(spanEl);
                    }
                    continue;
                }

                const wordCount = item.text.trim().split(/\s+/).filter(Boolean).length;
                const charCount = Math.max(Array.from(item.text).length, 1);
                const delay = Math.round((wordCount * 1000) / (4.5 * charCount));

                for (const s of lineSpans) {
                    if (renderId !== sceneDialogueRenderId) return;
                    const spanEl = createSpanElement(s);
                    textContainer.appendChild(spanEl);
                    const characters = Array.from(s.text);
                    for (const character of characters) {
                        if (renderId !== sceneDialogueRenderId) return;
                        spanEl.textContent += character;
                        await new Promise(resolve => window.setTimeout(resolve, delay));
                    }
                    if (s.effect && s.effect !== 'none') {
                        applySplittingIfAvailable(spanEl);
                    }
                }
            }
        })();
    }

    return { updateSceneDialogue, enqueueSceneSpeech };
}

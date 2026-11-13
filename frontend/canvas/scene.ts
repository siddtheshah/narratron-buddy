export interface SceneTextSpan {
  readonly text: string;
  readonly effect?: string | null;
  readonly font?: string | null;
  readonly color?: string | null;
}

export interface SceneDialogueLine {
  readonly speaker?: string | null;
  readonly text?: string | null;
  readonly kind?: string | null;
  readonly spans?: readonly SceneTextSpan[] | null;
}

export interface SceneNarrationPane {
  readonly element: HTMLElement | null;
  prepare(caption: HTMLElement): void;
}

export interface SceneSpeechState {
  readonly playing: boolean;
  readonly hasLatestSpeech: boolean;
}

export interface CreateCanvasSceneOptions {
  readonly narrationPane: SceneNarrationPane;
  readonly resolveUrl?: (url: string) => string;
  readonly audioEnabled?: () => boolean;
  readonly onSpeechStateChange?: (state: SceneSpeechState) => void;
}

export interface CanvasSceneController {
  updateSceneDialogue(
    dialogue: readonly SceneDialogueLine[] | null | undefined,
    narration: string | null | undefined,
    narrationSpans: readonly SceneTextSpan[] | null | undefined,
    sceneSpeechGeneration?: number | null,
  ): void;
  enqueueSceneSpeech(audioUrl: string, speechGeneration: number): void;
  stopSceneSpeech(): void;
  replayLatestSpeech(): void;
  hasLatestSpeech(): boolean;
  isSpeechPlaying(): boolean;
}

interface QueuedSceneSpeech {
  readonly audioUrl: string;
  readonly speechGeneration: number;
}

interface NormalizedDialogueLine {
  readonly speaker: string;
  readonly text: string;
  readonly kind: 'speech' | 'thought';
  readonly spans: readonly SceneTextSpan[] | null;
}

interface SplittingWindow extends Window {
  Splitting?: (options: { target: HTMLElement; by: string }) => void;
}

const DIALOGUE_SPEAKER_PALETTE: readonly string[] = [
  '#fbbf24',
  '#67e8f9',
  '#f9a8d4',
  '#a7f3d0',
  '#c4b5fd',
  '#fdba74',
  '#93c5fd',
  '#f0abfc',
];

/** Shared scene captions, dialogue effects, and generation-aware speech playback. */
export function createCanvasScene(options: CreateCanvasSceneOptions): CanvasSceneController {
  const {
    narrationPane,
    resolveUrl = (url: string) => url,
    audioEnabled = () => true,
    onSpeechStateChange = () => {},
  } = options;

  const sceneDialogueOverlay = narrationPane.element;
  let sceneDialogueSignature = '';
  let sceneDialogueRenderId = 0;
  const dialogueSpeakerColors = new Map<string, string>();
  const sceneSpeechQueue: QueuedSceneSpeech[] = [];
  let sceneSpeechPlaying = false;
  let currentSceneAudio: HTMLAudioElement | null = null;
  let committedSceneSpeechGeneration: number | null = null;
  let silencedSceneSpeechGeneration: number | null = null;
  let latestSpeechClips: string[] = [];
  let latestSpeechGeneration: number | null = null;

  function notifySpeechState(): void {
    try {
      onSpeechStateChange({
        playing: Boolean(sceneSpeechPlaying),
        hasLatestSpeech: latestSpeechClips.length > 0,
      });
    } catch (err) {
      console.warn('[canvas-scene] onSpeechStateChange error:', err);
    }
  }

  function resetSceneSpeech(): void {
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
      } catch {
        // Ignore cleanup errors on partially initialized audio elements.
      }
    }
    notifySpeechState();
  }

  function stopSceneSpeech(): void {
    if (committedSceneSpeechGeneration !== null) {
      silencedSceneSpeechGeneration = committedSceneSpeechGeneration;
    }
    resetSceneSpeech();
  }

  function playQueuedSceneSpeech(): void {
    if (!audioEnabled() || sceneSpeechPlaying) return;
    const playNext = (): void => {
      if (!audioEnabled()) {
        sceneSpeechPlaying = false;
        currentSceneAudio = null;
        notifySpeechState();
        return;
      }
      // Keep audio from a future generation queued: synthesis can
      // finish before its scene state is committed and rendered.
      while (
        sceneSpeechQueue.length > 0 &&
        committedSceneSpeechGeneration !== null &&
        sceneSpeechQueue[0].speechGeneration < committedSceneSpeechGeneration
      ) {
        sceneSpeechQueue.shift();
      }
      const nextIndex =
        committedSceneSpeechGeneration === null
          ? -1
          : sceneSpeechQueue.findIndex(
              (item) => item.speechGeneration === committedSceneSpeechGeneration,
            );
      if (nextIndex < 0) {
        sceneSpeechPlaying = false;
        currentSceneAudio = null;
        notifySpeechState();
        return;
      }
      const [next] = sceneSpeechQueue.splice(nextIndex, 1);
      sceneSpeechPlaying = true;
      notifySpeechState();
      const audio = new Audio(next.audioUrl);
      currentSceneAudio = audio;
      const finishClip = (): void => {
        if (currentSceneAudio === audio) {
          currentSceneAudio = null;
        }
        playNext();
      };
      audio.onended = finishClip;
      audio.onerror = finishClip;
      Promise.resolve(audio.play()).catch(finishClip);
    };
    playNext();
  }

  function replayLatestSpeech(): void {
    if (!latestSpeechClips.length || !audioEnabled()) return;
    silencedSceneSpeechGeneration = null;
    resetSceneSpeech();
    const targetGen =
      committedSceneSpeechGeneration !== null
        ? committedSceneSpeechGeneration
        : (latestSpeechGeneration !== null ? latestSpeechGeneration : 0);
    for (const url of latestSpeechClips) {
      sceneSpeechQueue.push({ audioUrl: url, speechGeneration: targetGen });
    }
    playQueuedSceneSpeech();
    notifySpeechState();
  }

  function enqueueSceneSpeech(audioUrl: string, speechGeneration: number): void {
    if (!audioUrl || !Number.isInteger(speechGeneration)) return;
    const resolved = resolveUrl(audioUrl);
    if (latestSpeechGeneration === null || speechGeneration > latestSpeechGeneration) {
      latestSpeechGeneration = speechGeneration;
      latestSpeechClips = [resolved];
    } else if (speechGeneration === latestSpeechGeneration) {
      if (!latestSpeechClips.includes(resolved)) {
        latestSpeechClips.push(resolved);
      }
    }
    notifySpeechState();

    if (!audioEnabled()) return;
    if (committedSceneSpeechGeneration !== null && speechGeneration < committedSceneSpeechGeneration) {
      return;
    }
    if (silencedSceneSpeechGeneration !== null && speechGeneration <= silencedSceneSpeechGeneration) {
      return;
    }
    sceneSpeechQueue.push({ audioUrl: resolved, speechGeneration });
    playQueuedSceneSpeech();
  }

  function setCommittedSceneSpeechGeneration(speechGeneration: number | null | undefined): void {
    if (typeof speechGeneration !== 'number' || !Number.isInteger(speechGeneration)) return;
    if (speechGeneration === committedSceneSpeechGeneration) {
      playQueuedSceneSpeech();
      return;
    }
    if (committedSceneSpeechGeneration !== null && speechGeneration < committedSceneSpeechGeneration) {
      return;
    }

    committedSceneSpeechGeneration = speechGeneration;
    if (silencedSceneSpeechGeneration !== null && speechGeneration > silencedSceneSpeechGeneration) {
      silencedSceneSpeechGeneration = null;
    }
    // Interrupt older speech, retaining audio for this scene and any
    // future scene whose synthesis finished before its state arrived.
    const pendingAudio = sceneSpeechQueue.filter(
      (item) => item.speechGeneration >= speechGeneration,
    );
    resetSceneSpeech();
    if (silencedSceneSpeechGeneration !== speechGeneration) {
      sceneSpeechQueue.push(...pendingAudio);
      playQueuedSceneSpeech();
    }
  }

  function getDialogueSpeakerColor(speaker: string): string {
    const existing = dialogueSpeakerColors.get(speaker);
    if (existing !== undefined) {
      return existing;
    }
    const color =
      DIALOGUE_SPEAKER_PALETTE[dialogueSpeakerColors.size % DIALOGUE_SPEAKER_PALETTE.length];
    dialogueSpeakerColors.set(speaker, color);
    return color;
  }

  function createSpanElement(span: SceneTextSpan): HTMLSpanElement {
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

  function applySplittingIfAvailable(element: HTMLElement): void {
    const splittingWin = window as SplittingWindow;
    if (typeof splittingWin.Splitting === 'function') {
      try {
        splittingWin.Splitting({ target: element, by: 'chars' });
      } catch (err) {
        console.warn('Splitting error:', err);
      }
    }
  }

  function updateSceneDialogue(
    dialogue: readonly SceneDialogueLine[] | null | undefined,
    narration: string | null | undefined,
    narrationSpans: readonly SceneTextSpan[] | null | undefined,
    sceneSpeechGeneration?: number | null,
  ): void {
    if (!sceneDialogueOverlay) return;
    setCommittedSceneSpeechGeneration(sceneSpeechGeneration);
    const lines: NormalizedDialogueLine[] = (Array.isArray(dialogue) ? dialogue : [])
      .filter((item): item is SceneDialogueLine & { readonly text: string } =>
        Boolean(item && item.text),
      )
      .map((item) => ({
        speaker: String(item.speaker || 'Narrator'),
        text: String(item.text),
        kind: item.kind === 'thought' ? 'thought' : 'speech',
        spans: Array.isArray(item.spans) ? item.spans : null,
      }));
    const description = String(narration || '').trim();
    const nSpans = Array.isArray(narrationSpans) ? narrationSpans : null;
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
      const spansToRender: readonly SceneTextSpan[] =
        nSpans && nSpans.length > 0
          ? nSpans
          : [{ text: description, effect: 'none', font: 'default' }];
      for (const span of spansToRender) {
        const spanEl = createSpanElement(span);
        spanEl.textContent = span.text;
        caption.appendChild(spanEl);
        if (span.effect && span.effect !== 'none' && !reduceMotion) {
          applySplittingIfAvailable(spanEl);
        }
      }
      sceneDialogueOverlay.appendChild(caption);
    }

    void (async () => {
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

        const lineSpans: readonly SceneTextSpan[] =
          item.spans && item.spans.length > 0
            ? item.spans
            : [{ text: item.text, effect: 'none', font: 'default' }];

        if (reduceMotion) {
          for (const span of lineSpans) {
            const spanEl = createSpanElement(span);
            spanEl.textContent = span.text;
            textContainer.appendChild(spanEl);
          }
          continue;
        }

        const wordCount = item.text.trim().split(/\s+/).filter(Boolean).length;
        const charCount = Math.max(Array.from(item.text).length, 1);
        const delay = Math.round((wordCount * 1000) / (4.5 * charCount));

        for (const span of lineSpans) {
          if (renderId !== sceneDialogueRenderId) return;
          const spanEl = createSpanElement(span);
          textContainer.appendChild(spanEl);
          const characters = Array.from(span.text);
          for (const character of characters) {
            if (renderId !== sceneDialogueRenderId) return;
            spanEl.textContent += character;
            await new Promise<void>((resolve) => {
              window.setTimeout(resolve, delay);
            });
          }
          if (span.effect && span.effect !== 'none') {
            applySplittingIfAvailable(spanEl);
          }
        }
      }
    })();
  }

  return {
    updateSceneDialogue,
    enqueueSceneSpeech,
    stopSceneSpeech,
    replayLatestSpeech,
    hasLatestSpeech: () => latestSpeechClips.length > 0,
    isSpeechPlaying: () => Boolean(sceneSpeechPlaying),
  };
}

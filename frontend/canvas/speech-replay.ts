import type { SceneSpeechState } from './scene.js';

export interface CreateSpeechReplayControlsOptions {
  readonly replayButton: HTMLButtonElement | null;
  readonly replayLatestSpeech: () => void;
  readonly stopSceneSpeech: () => void;
  readonly getMicMode: () => string;
  readonly vadInterruptDelayMs?: number;
  readonly eventTarget?: EventTarget;
}

export interface SpeechReplayControls {
  updateButtonState(state: SceneSpeechState | null | undefined): void;
  handleVadSpeechStart(): void;
  handleVadSpeechEnd(): void;
  dispose(): void;
}

/** Update the replay button enabled state, playing class, and accessible title. */
export function updateSpeechReplayButtonUI(
  button: HTMLButtonElement | null,
  state: SceneSpeechState | null | undefined,
): void {
  if (!button) return;
  const hasLatest = Boolean(state?.hasLatestSpeech);
  const isPlaying = Boolean(state?.playing);
  button.disabled = !hasLatest;
  button.classList.toggle('playing', isPlaying);
  button.title = hasLatest
    ? (isPlaying ? 'Replaying latest speech… (click to restart)' : 'Replay latest speech')
    : 'No speech to replay';
}

/** Wire speech replay button interactions and VAD speech interruption timers. */
export function createSpeechReplayControls(
  options: CreateSpeechReplayControlsOptions,
): SpeechReplayControls {
  const {
    replayButton,
    replayLatestSpeech,
    stopSceneSpeech,
    getMicMode,
    vadInterruptDelayMs = 1000,
    eventTarget = window,
  } = options;

  let vadSpeechTimer: number | null = null;
  let isVadSpeaking = false;

  function clearVadTimer(): void {
    if (vadSpeechTimer !== null) {
      window.clearTimeout(vadSpeechTimer);
      vadSpeechTimer = null;
    }
  }

  function handleVadSpeechStart(): void {
    if (getMicMode() === 'push-to-talk') return;
    if (isVadSpeaking) return;
    isVadSpeaking = true;
    clearVadTimer();
    vadSpeechTimer = window.setTimeout(() => {
      vadSpeechTimer = null;
      if (isVadSpeaking && getMicMode() !== 'push-to-talk') {
        stopSceneSpeech();
      }
    }, vadInterruptDelayMs);
  }

  function handleVadSpeechEnd(): void {
    isVadSpeaking = false;
    clearVadTimer();
  }

  const onReplayClick = (): void => {
    replayLatestSpeech();
  };
  const onPointerStop = (event: Event): void => {
    event.stopPropagation();
  };

  replayButton?.addEventListener('click', onReplayClick);
  replayButton?.addEventListener('pointerdown', onPointerStop);
  replayButton?.addEventListener('mousedown', onPointerStop);

  eventTarget.addEventListener('narratron:speech-start', handleVadSpeechStart);
  eventTarget.addEventListener('narratron:speech-end', handleVadSpeechEnd);
  eventTarget.addEventListener('vadstart', handleVadSpeechStart);
  eventTarget.addEventListener('vadstop', handleVadSpeechEnd);

  function dispose(): void {
    clearVadTimer();
    isVadSpeaking = false;
    replayButton?.removeEventListener('click', onReplayClick);
    replayButton?.removeEventListener('pointerdown', onPointerStop);
    replayButton?.removeEventListener('mousedown', onPointerStop);
    eventTarget.removeEventListener('narratron:speech-start', handleVadSpeechStart);
    eventTarget.removeEventListener('narratron:speech-end', handleVadSpeechEnd);
    eventTarget.removeEventListener('vadstart', handleVadSpeechStart);
    eventTarget.removeEventListener('vadstop', handleVadSpeechEnd);
  }

  return {
    updateButtonState: (state) => updateSpeechReplayButtonUI(replayButton, state),
    handleVadSpeechStart,
    handleVadSpeechEnd,
    dispose,
  };
}

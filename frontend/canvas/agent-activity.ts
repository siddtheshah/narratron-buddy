export interface DiceResultSummary {
  readonly tier?: string;
  readonly notation?: string;
  readonly total?: number | string;
}

export interface ToolActivitySnapshot {
  image_generating?: boolean;
  animation_generating?: boolean;
  user_action_processing?: boolean;
  music_generating?: boolean;
  dice_rolling?: boolean;
  reference_generating?: boolean;
  live_ready?: boolean;
  dice_result?: DiceResultSummary | null;
}

export interface CreateAgentActivityControllerOptions {
  readonly pcmBroadcastIndicator: HTMLElement | null;
  readonly drawingIndicator: HTMLElement | null;
  readonly writingIndicator: HTMLElement | null;
  readonly musicIndicator: HTMLElement | null;
  readonly diceIndicator: HTMLElement | null;
  readonly referenceIndicator: HTMLElement | null;
  readonly disconnectedIndicator: HTMLElement | null;
  readonly onReconnect?: () => void;
  readonly staleMs?: number;
  readonly maxMs?: number;
  readonly pollIntervalMs?: number;
}

export interface AgentActivityController {
  update(toolActivity?: ToolActivitySnapshot | null): void;
  setPcmBroadcasting(isBroadcasting: boolean): void;
  setDisconnected(isDisconnected: boolean): void;
  destroy(): void;
}

type ActivityField =
  | 'image_generating'
  | 'animation_generating'
  | 'user_action_processing'
  | 'music_generating'
  | 'dice_rolling'
  | 'reference_generating';

const ACTIVITY_FIELDS: readonly ActivityField[] = [
  'image_generating',
  'animation_generating',
  'user_action_processing',
  'music_generating',
  'dice_rolling',
  'reference_generating',
];

const DICE_TIERS = ['low', 'middle', 'high'] as const;

interface ActivityTiming {
  startedAt: number;
  confirmedAt: number;
  timedOut: boolean;
}

const DEFAULT_STALE_MS = 30 * 1000;
const DEFAULT_MAX_MS = 10 * 60 * 1000;
const DEFAULT_POLL_INTERVAL_MS = 1000;

function setIndicatorVisibility(element: HTMLElement | null, visible: boolean | undefined): void {
  if (!element) return;
  const active = Boolean(visible);
  if (element.classList.contains('active') !== active) {
    element.classList.toggle('active', active);
  }
  const hidden = String(!active);
  if (element.getAttribute('aria-hidden') !== hidden) {
    element.setAttribute('aria-hidden', hidden);
  }
}

/**
 * Track per-tool activity heartbeats, stale/max timeouts, and live voice
 * broadcast indicators without re-triggering active CSS animation timelines.
 */
export function createAgentActivityController(
  options: CreateAgentActivityControllerOptions,
): AgentActivityController {
  const {
    pcmBroadcastIndicator,
    drawingIndicator,
    writingIndicator,
    musicIndicator,
    diceIndicator,
    referenceIndicator,
    disconnectedIndicator,
    onReconnect,
    staleMs = DEFAULT_STALE_MS,
    maxMs = DEFAULT_MAX_MS,
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  } = options;

  const currentActivity: ToolActivitySnapshot = {};
  const activityTiming = new Map<ActivityField, ActivityTiming>();

  if (disconnectedIndicator && onReconnect) {
    disconnectedIndicator.addEventListener('click', onReconnect);
  }

  function update(toolActivity: ToolActivitySnapshot | null = {}): void {
    const incoming = toolActivity ?? {};
    // Each tool needs its own confirmation: a live-connection heartbeat
    // must not keep an unrelated tool indicator alive after an error.
    Object.assign(currentActivity, incoming);
    const now = Date.now();
    for (const field of ACTIVITY_FIELDS) {
      let timing = activityTiming.get(field);
      const value = incoming[field];
      if (value === false) {
        activityTiming.delete(field);
      } else if (value === true) {
        if (!timing) {
          timing = { startedAt: now, confirmedAt: now, timedOut: false };
          activityTiming.set(field, timing);
        }
        timing.confirmedAt = now;
      }
      if (timing && value !== false) {
        // Latch the hard timeout until completion, so a stuck true
        // flag cannot repeatedly bring the indicator back.
        timing.timedOut ||= now - timing.startedAt >= maxMs;
        currentActivity[field] = !timing.timedOut && now - timing.confirmedAt < staleMs;
      }
    }

    const isGeneratingImage = Boolean(currentActivity.image_generating);
    const isGeneratingAnimation = Boolean(currentActivity.animation_generating);
    setIndicatorVisibility(drawingIndicator, isGeneratingImage || isGeneratingAnimation);
    if (drawingIndicator) {
      drawingIndicator.title = isGeneratingAnimation && !isGeneratingImage
        ? 'Agent is generating an animation'
        : 'Agent is painting a scene';
    }
    setIndicatorVisibility(writingIndicator, currentActivity.user_action_processing);
    setIndicatorVisibility(musicIndicator, currentActivity.music_generating);
    setIndicatorVisibility(diceIndicator, currentActivity.dice_rolling);
    setIndicatorVisibility(referenceIndicator, Boolean(currentActivity.reference_generating));

    const diceResult = currentActivity.dice_result ?? {};
    const tier = diceResult.tier && (DICE_TIERS as readonly string[]).includes(diceResult.tier)
      ? diceResult.tier
      : '';
    if (diceIndicator) {
      for (const resultTier of DICE_TIERS) {
        const className = `dice-result-${resultTier}`;
        const selected = resultTier === tier;
        if (diceIndicator.classList.contains(className) !== selected) {
          diceIndicator.classList.toggle(className, selected);
        }
      }
      diceIndicator.title = diceResult.notation
        ? `Rolled ${diceResult.notation}: ${diceResult.total}`
        : 'The story is rolling the dice';
    }
  }

  function setPcmBroadcasting(isBroadcasting: boolean): void {
    setIndicatorVisibility(pcmBroadcastIndicator, isBroadcasting);
  }

  function setDisconnected(isDisconnected: boolean): void {
    setIndicatorVisibility(disconnectedIndicator, isDisconnected);
  }

  const onSpeechStart = (): void => setPcmBroadcasting(true);
  const onSpeechEnd = (): void => setPcmBroadcasting(false);
  // These map to the complete RMS VAD speech segment, not PCM chunk boundaries.
  window.addEventListener('narratron:speech-start', onSpeechStart);
  window.addEventListener('narratron:speech-end', onSpeechEnd);

  // Also expire indicators when requests fail and no new snapshot arrives.
  const expiryTimer = setInterval(() => update(), pollIntervalMs);

  function destroy(): void {
    clearInterval(expiryTimer);
    window.removeEventListener('narratron:speech-start', onSpeechStart);
    window.removeEventListener('narratron:speech-end', onSpeechEnd);
    if (disconnectedIndicator && onReconnect) {
      disconnectedIndicator.removeEventListener('click', onReconnect);
    }
  }

  return {
    update,
    setPcmBroadcasting,
    setDisconnected,
    destroy,
  };
}

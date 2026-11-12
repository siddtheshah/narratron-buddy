export interface CanvasPinState {
  readonly pinned: boolean;
  readonly musicPinned: boolean;
}

export interface CreateCanvasPinControlOptions {
  readonly button: HTMLButtonElement | null;
  readonly label: HTMLElement | null;
  readonly theaterId: string | null;
  readonly isOrator: () => boolean;
  readonly getState: () => CanvasPinState;
  readonly setState: (state: CanvasPinState) => void;
  readonly onUpdated?: (state: CanvasPinState) => void;
}

export interface CanvasPinControl {
  update(): void;
}

interface PinApiResponse {
  readonly detail?: string;
  readonly pinned?: boolean;
  readonly music_pinned?: boolean;
}

/** Manage the orator pin button that locks the active scene image and music. */
export function createCanvasPinControl(options: CreateCanvasPinControlOptions): CanvasPinControl {
  const { button, label, theaterId, isOrator, getState, setState, onUpdated } = options;

  function update(): void {
    if (!button) return;
    const { pinned, musicPinned } = getState();
    const allPinned = pinned && musicPinned;
    button.style.display = isOrator() ? 'inline-flex' : 'none';
    button.classList.toggle('active', allPinned);
    button.setAttribute('aria-pressed', pinned !== musicPinned ? 'mixed' : String(allPinned));
    button.title = allPinned
      ? 'Unpin image and music and allow changes'
      : 'Pin the current image and music and block changes';
    if (label) {
      label.textContent = allPinned ? 'Image and music pinned' : 'Pin image and music';
    }
  }

  button?.addEventListener('click', async () => {
    if (!isOrator() || !theaterId || button.disabled) return;
    button.disabled = true;
    try {
      const { pinned, musicPinned } = getState();
      const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/canvas-pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pinned: !(pinned && musicPinned) }),
      });
      const result = await response.json().catch(() => ({})) as PinApiResponse;
      if (!response.ok) {
        throw new Error(result.detail || 'Unable to update the canvas pin.');
      }
      const nextState: CanvasPinState = {
        pinned: Boolean(result.pinned),
        musicPinned: Boolean(result.music_pinned),
      };
      setState(nextState);
      update();
      onUpdated?.(nextState);
    } catch (error) {
      console.error('Failed to update canvas pin:', error);
      button.title = error instanceof Error ? error.message : String(error);
    } finally {
      button.disabled = false;
    }
  });

  return { update };
}

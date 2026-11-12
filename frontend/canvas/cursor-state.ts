export type CanvasCursorType = 'select' | 'marker' | 'text' | 'navigate';

export type CanvasCursorState =
  | Readonly<{ type: 'select' | 'text' | 'navigate'; color: null }>
  | Readonly<{ type: 'marker'; color: string }>;

export type CanvasCursorSelection =
  | { type: 'select' | 'text' | 'navigate'; color?: null }
  | { type: 'marker'; color: string };

export type CanvasCursorListener = (state: CanvasCursorState) => void;

export interface CanvasCursorStore {
  readonly current: CanvasCursorState;
  select(next: CanvasCursorSelection): void;
  subscribe(listener: CanvasCursorListener): () => void;
}

const VALID_CURSOR_TYPES: ReadonlySet<string> = new Set<CanvasCursorType>([
  'select',
  'marker',
  'text',
  'navigate',
]);

/** One left-click tool per viewer. View framing is managed separately. */
export function createCanvasCursorState(): CanvasCursorStore {
  let current: CanvasCursorState = Object.freeze({ type: 'select', color: null });
  const listeners = new Set<CanvasCursorListener>();

  return {
    get current(): CanvasCursorState {
      return current;
    },
    select(next: CanvasCursorSelection): void {
      if (!next || !VALID_CURSOR_TYPES.has(next.type)) {
        throw new Error(`Unknown canvas cursor state: ${next?.type}`);
      }
      if (next.type === 'marker') {
        if (!next.color) {
          throw new Error('A marker cursor requires a color');
        }
        if (current.type === 'marker' && current.color === next.color) {
          return;
        }
        current = Object.freeze({ type: 'marker', color: next.color });
      } else {
        if (current.type === next.type && current.color === null) {
          return;
        }
        current = Object.freeze({ type: next.type, color: null });
      }
      for (const listener of listeners) {
        listener(current);
      }
    },
    subscribe(listener: CanvasCursorListener): () => void {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export const canvasCursorState: CanvasCursorStore = createCanvasCursorState();

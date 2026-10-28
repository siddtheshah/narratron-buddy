/** One left-click tool per viewer. View framing is managed separately. */
export function createCanvasCursorState() {
    let current = Object.freeze({ type: 'select', color: null });
    const listeners = new Set();

    return {
        get current() { return current; },
        select(next) {
            if (!['select', 'marker', 'text', 'navigate'].includes(next.type)) {
                throw new Error(`Unknown canvas cursor state: ${next.type}`);
            }
            if (next.type === 'marker' && !next.color) {
                throw new Error('A marker cursor requires a color');
            }
            const color = next.type === 'marker' ? next.color : null;
            if (current.type === next.type && current.color === color) return;
            current = Object.freeze({ type: next.type, color });
            for (const listener of listeners) listener(current);
        },
        subscribe(listener) {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}

export const canvasCursorState = createCanvasCursorState();

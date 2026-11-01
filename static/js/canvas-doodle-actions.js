/**
 * Canvas Doodle Action classes and Undo Manager.
 * Implements the Command pattern for undoing drawings, text annotations,
 * stamps, and canvas clear operations.
 */

export function restoreSegmentsToServer(segments, sendFn) {
    if (!Array.isArray(segments) || segments.length === 0) return;
    const send = typeof sendFn === 'function'
        ? sendFn
        : (typeof sendOrQueueDoodleMessage === 'function' ? sendOrQueueDoodleMessage : null);
    if (!send) return;

    let currentBatch = null;
    for (const seg of segments) {
        const color = seg.color;
        const size = seg.size || 3;
        const stroke_id = seg.stroke_id;
        if (
            currentBatch &&
            currentBatch.color === color &&
            currentBatch.size === size &&
            currentBatch.stroke_id === stroke_id &&
            currentBatch.points.length <= 396
        ) {
            const pts = currentBatch.points;
            if (pts[pts.length - 2] === seg.x0 && pts[pts.length - 1] === seg.y0) {
                pts.push(seg.x1, seg.y1);
            } else {
                pts.push(seg.x0, seg.y0, seg.x1, seg.y1);
            }
        } else {
            if (currentBatch) send(currentBatch);
            currentBatch = {
                type: 'draw_batch',
                color,
                size,
                stroke_id,
                points: [seg.x0, seg.y0, seg.x1, seg.y1]
            };
        }
    }
    if (currentBatch) send(currentBatch);
}

export class DoodleAction {
    constructor(type) {
        this.type = type;
    }

    getContext(context = {}) {
        return {
            getActions: () => {
                if (context.doodleActions !== undefined) return context.doodleActions;
                return typeof doodleActions !== 'undefined' ? doodleActions : [];
            },
            setActions: (actions) => {
                if (context.doodleActions !== undefined) context.doodleActions = actions;
                if (typeof doodleActions !== 'undefined') doodleActions = actions;
            },
            getRenderer: () => {
                if (context.doodleRenderer !== undefined) return context.doodleRenderer;
                return typeof doodleRenderer !== 'undefined' ? doodleRenderer : null;
            },
            redraw: () => {
                if (typeof context.redrawAllDoodles === 'function') context.redrawAllDoodles();
                else if (typeof redrawAllDoodles === 'function') redrawAllDoodles();
            },
            sendMessage: (msg) => {
                if (typeof context.sendOrQueueDoodleMessage === 'function') context.sendOrQueueDoodleMessage(msg);
                else if (typeof sendOrQueueDoodleMessage === 'function') sendOrQueueDoodleMessage(msg);
            },
            restoreSegments: (segments) => {
                if (typeof context.restoreSegmentsToServer === 'function') context.restoreSegmentsToServer(segments);
                else if (typeof restoreSegmentsToServer === 'function') restoreSegmentsToServer(segments);
            }
        };
    }

    undo(context) {
        throw new Error(`Undo not implemented for action type ${this.type}`);
    }

    static from(data) {
        if (!data) return null;
        if (data instanceof DoodleAction || (typeof data.undo === 'function')) return data;
        switch (data.type) {
            case 'draw_stroke':
                return new DrawStrokeDoodleAction(data.stroke_id, data.segments);
            case 'delete_segments':
                return new DeleteSegmentsDoodleAction(data.segments);
            case 'place_text':
                return new PlaceTextDoodleAction(data.action);
            case 'edit_text':
                return new EditTextDoodleAction(data.before, data.after);
            case 'move_text':
                return new MoveTextDoodleAction(data.before, data.after);
            case 'delete_text':
                return new DeleteTextDoodleAction(data.items);
            case 'place_stamp':
                return new PlaceStampDoodleAction(data.action, data.replaced);
            case 'transform_stamp':
            case 'move_stamp':
            case 'resize_stamp':
            case 'rotate_stamp':
                return new TransformStampDoodleAction(data.before, data.after, data.type);
            case 'delete_stamp':
                return new DeleteStampDoodleAction(data.items);
            case 'clear':
                return new ClearCanvasDoodleAction(data.previousActions);
            case 'compound_delete':
                return new CompoundDeleteDoodleAction(data.segments, data.stamps, data.texts);
            default:
                return null;
        }
    }
}

export class DrawStrokeDoodleAction extends DoodleAction {
    constructor(strokeId, segments = []) {
        super('draw_stroke');
        this.stroke_id = strokeId;
        this.segments = Array.isArray(segments) ? [...segments] : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        const strokeId = this.stroke_id;
        const segIds = new Set(this.segments.map(s => s.id).filter(Boolean));
        let actions = ctx.getActions();
        actions = actions.filter(a => {
            if (a.type !== 'draw') return true;
            if (strokeId && a.stroke_id === strokeId) return false;
            if (a.id && segIds.has(a.id)) return false;
            return true;
        });
        ctx.setActions(actions);
        ctx.getRenderer()?.clearSelectedSegments();
        ctx.redraw();
        ctx.sendMessage({
            type: 'remove_draw',
            stroke_id: strokeId,
            ids: Array.from(segIds)
        });
        return true;
    }
}

export class DeleteSegmentsDoodleAction extends DoodleAction {
    constructor(segments = []) {
        super('delete_segments');
        this.segments = Array.isArray(segments) ? [...segments] : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        ctx.getRenderer()?.clearSelectedSegments();
        const actions = ctx.getActions();
        for (const seg of this.segments) {
            actions.push({ ...seg });
        }
        ctx.redraw();
        ctx.restoreSegments(this.segments);
        return true;
    }
}

export class PlaceTextDoodleAction extends DoodleAction {
    constructor(action) {
        super('place_text');
        this.action = action ? { ...action } : null;
    }

    undo(context) {
        if (!this.action?.id) return false;
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        const idx = actions.findIndex(a => a.type === 'text' && a.id === this.action.id);
        if (idx >= 0) {
            actions.splice(idx, 1);
            ctx.sendMessage({ type: 'remove_text', id: this.action.id });
            ctx.redraw();
        }
        return true;
    }
}

export class EditTextDoodleAction extends DoodleAction {
    constructor(before, after) {
        super('edit_text');
        this.before = before ? { ...before } : null;
        this.after = after ? { ...after } : null;
    }

    undo(context) {
        if (!this.before?.id) return false;
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        const idx = actions.findIndex(a => a.type === 'text' && a.id === this.before.id);
        if (idx >= 0) {
            actions[idx] = { ...this.before };
        } else {
            actions.push({ ...this.before });
        }
        ctx.sendMessage({ ...this.before });
        ctx.redraw();
        return true;
    }
}

export class MoveTextDoodleAction extends DoodleAction {
    constructor(before, after) {
        super('move_text');
        this.before = before ? { ...before } : null;
        this.after = after ? { ...after } : null;
    }

    undo(context) {
        if (!this.before?.id) return false;
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        const idx = actions.findIndex(a => a.type === 'text' && a.id === this.before.id);
        if (idx >= 0) {
            actions[idx] = { ...this.before };
            ctx.sendMessage({ ...this.before });
            ctx.redraw();
        }
        return true;
    }
}

export class DeleteTextDoodleAction extends DoodleAction {
    constructor(items = []) {
        super('delete_text');
        this.items = Array.isArray(items) ? items.map(i => ({ ...i })) : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        for (const item of this.items) {
            const idx = actions.findIndex(a => a.type === 'text' && a.id === item.id);
            if (idx >= 0) actions[idx] = { ...item };
            else actions.push({ ...item });
            ctx.sendMessage({ ...item });
        }
        ctx.redraw();
        return true;
    }
}

export class PlaceStampDoodleAction extends DoodleAction {
    constructor(action, replaced = null) {
        super('place_stamp');
        this.action = action ? { ...action } : null;
        this.replaced = replaced ? { ...replaced } : null;
    }

    undo(context) {
        if (!this.action?.id) return false;
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        const idx = actions.findIndex(a => a.type === 'stamp' && a.id === this.action.id);
        if (idx >= 0) {
            actions.splice(idx, 1);
            ctx.sendMessage({ type: 'remove_stamp', id: this.action.id });
        }
        if (this.replaced) {
            const rep = { ...this.replaced };
            const firstNonStamp = actions.findIndex(item => item.type !== 'stamp');
            if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, rep);
            else actions.push(rep);
            ctx.sendMessage(rep);
        }
        ctx.redraw();
        return true;
    }
}

export class TransformStampDoodleAction extends DoodleAction {
    constructor(before, after, transformType = 'transform_stamp') {
        super(transformType || 'transform_stamp');
        this.before = before ? { ...before } : null;
        this.after = after ? { ...after } : null;
    }

    undo(context) {
        if (!this.before?.id) return false;
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        const idx = actions.findIndex(a => a.type === 'stamp' && a.id === this.before.id);
        if (idx >= 0) {
            actions[idx] = { ...this.before };
            ctx.sendMessage({ ...this.before });
            ctx.redraw();
        }
        return true;
    }
}

export class DeleteStampDoodleAction extends DoodleAction {
    constructor(items = []) {
        super('delete_stamp');
        this.items = Array.isArray(items) ? items.map(i => ({ ...i })) : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        for (const item of this.items) {
            const idx = actions.findIndex(a => a.type === 'stamp' && a.id === item.id);
            if (idx >= 0) actions[idx] = { ...item };
            else {
                const firstNonStamp = actions.findIndex(a => a.type !== 'stamp');
                if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, { ...item });
                else actions.push({ ...item });
            }
            ctx.sendMessage({ ...item });
        }
        ctx.redraw();
        return true;
    }
}

export class CompoundDeleteDoodleAction extends DoodleAction {
    constructor(segments = [], stamps = [], texts = []) {
        super('compound_delete');
        this.segments = Array.isArray(segments) ? segments.map(s => ({ ...s })) : [];
        this.stamps = Array.isArray(stamps) ? stamps.map(s => ({ ...s })) : [];
        this.texts = Array.isArray(texts) ? texts.map(t => ({ ...t })) : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        const actions = ctx.getActions();
        if (this.segments.length > 0) {
            for (const seg of this.segments) {
                actions.push({ ...seg });
            }
            ctx.restoreSegments(this.segments);
        }
        if (this.stamps.length > 0) {
            for (const item of this.stamps) {
                const idx = actions.findIndex(a => a.type === 'stamp' && a.id === item.id);
                if (idx >= 0) actions[idx] = { ...item };
                else {
                    const firstNonStamp = actions.findIndex(a => a.type !== 'stamp');
                    if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, { ...item });
                    else actions.push({ ...item });
                }
                ctx.sendMessage({ ...item });
            }
        }
        if (this.texts.length > 0) {
            for (const item of this.texts) {
                const idx = actions.findIndex(a => a.type === 'text' && a.id === item.id);
                if (idx >= 0) actions[idx] = { ...item };
                else actions.push({ ...item });
                ctx.sendMessage({ ...item });
            }
        }
        ctx.redraw();
        return true;
    }
}

export class ClearCanvasDoodleAction extends DoodleAction {
    constructor(previousActions = []) {
        super('clear');
        this.previousActions = Array.isArray(previousActions) ? previousActions.map(a => ({ ...a })) : [];
    }

    undo(context) {
        const ctx = this.getContext(context);
        const restored = this.previousActions.map(a => ({ ...a }));
        ctx.setActions(restored);
        ctx.redraw();
        const segs = restored.filter(a => a.type === 'draw');
        if (segs.length > 0) ctx.restoreSegments(segs);
        restored.filter(a => a.type === 'stamp').forEach(s => ctx.sendMessage({ ...s }));
        restored.filter(a => a.type === 'text').forEach(t => ctx.sendMessage({ ...t }));
        return true;
    }
}

export function createDoodleUndoManager({
    getStack = null,
    stack = [],
    maxHistory = 100,
    getContext = () => ({}),
    onStateChange = () => {},
    isEnabled = () => true
} = {}) {
    let internalStack = stack;
    const resolveStack = () => (typeof getStack === 'function' ? getStack() : internalStack);

    return {
        get stack() { return resolveStack(); },
        set stack(val) {
            internalStack = Array.isArray(val) ? val : [];
        },
        canUndo() {
            const s = resolveStack();
            return s.length > 0 && (typeof isEnabled === 'function' ? isEnabled() : true);
        },
        push(action) {
            const doodleAction = (action instanceof DoodleAction || (action && typeof action.undo === 'function'))
                ? action
                : DoodleAction.from(action);
            if (!doodleAction) return null;
            const s = resolveStack();
            s.push(doodleAction);
            if (s.length > maxHistory) {
                s.shift();
            }
            if (typeof onStateChange === 'function') onStateChange();
            return doodleAction;
        },
        undo() {
            const s = resolveStack();
            if (s.length === 0) return false;
            const action = s.pop();
            if (typeof onStateChange === 'function') onStateChange();
            if (action && typeof action.undo === 'function') {
                const ctx = typeof getContext === 'function' ? getContext() : {};
                return action.undo(ctx);
            }
            return false;
        },
        clear() {
            const s = resolveStack();
            s.length = 0;
            if (typeof onStateChange === 'function') onStateChange();
        }
    };
}

// Attach to window if running in browser script context
if (typeof window !== 'undefined') {
    window.DoodleAction = DoodleAction;
    window.DrawStrokeDoodleAction = DrawStrokeDoodleAction;
    window.DeleteSegmentsDoodleAction = DeleteSegmentsDoodleAction;
    window.PlaceTextDoodleAction = PlaceTextDoodleAction;
    window.EditTextDoodleAction = EditTextDoodleAction;
    window.MoveTextDoodleAction = MoveTextDoodleAction;
    window.DeleteTextDoodleAction = DeleteTextDoodleAction;
    window.PlaceStampDoodleAction = PlaceStampDoodleAction;
    window.TransformStampDoodleAction = TransformStampDoodleAction;
    window.DeleteStampDoodleAction = DeleteStampDoodleAction;
    window.CompoundDeleteDoodleAction = CompoundDeleteDoodleAction;
    window.ClearCanvasDoodleAction = ClearCanvasDoodleAction;
    window.createDoodleUndoManager = createDoodleUndoManager;
    window.restoreSegmentsToServer = restoreSegmentsToServer;
}

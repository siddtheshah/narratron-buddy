/**
 * Canvas Doodle Action classes and Undo Manager.
 * Implements the Command pattern for undoing drawings, text annotations,
 * stamps, and canvas clear operations.
 */

export interface DrawSegmentItem {
  readonly type?: string;
  readonly id?: string;
  readonly stroke_id?: string;
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
  readonly color: string;
  readonly size?: number;
  readonly [key: string]: unknown;
}

export interface TextAnnotationItem {
  readonly type?: string;
  readonly id?: string;
  readonly x: number;
  readonly y: number;
  readonly text: string;
  readonly color: string;
  readonly size: number;
  readonly font?: string;
  readonly [key: string]: unknown;
}

export interface StampAnnotationItem {
  readonly type?: string;
  readonly id?: string;
  readonly stamp_id?: string | number;
  readonly url?: string;
  readonly name?: string;
  readonly x: number;
  readonly y: number;
  readonly size: number;
  readonly rotation?: number;
  readonly [key: string]: unknown;
}

export interface DoodleCanvasItem {
  readonly type?: string;
  readonly id?: string;
  readonly stroke_id?: string;
  readonly [key: string]: unknown;
}

export interface DrawBatchMessage {
  type: 'draw_batch';
  color: string;
  size: number;
  stroke_id: string | undefined;
  points: number[];
  readonly [key: string]: unknown;
}

export type DoodleMessage = Record<string, unknown>;

export interface DoodleRendererLike {
  clearSelectedSegments(): void;
}

export interface DoodleUndoContext {
  doodleActions?: DoodleCanvasItem[];
  doodleRenderer?: DoodleRendererLike | null;
  redrawAllDoodles?: () => void;
  sendOrQueueDoodleMessage?: (msg: DoodleMessage) => void;
  restoreSegmentsToServer?: (
    segments: readonly DrawSegmentItem[],
    sendFn?: (msg: DrawBatchMessage) => void,
  ) => void;
}

export interface ResolvedDoodleUndoContext {
  getActions(): DoodleCanvasItem[];
  setActions(actions: DoodleCanvasItem[]): void;
  getRenderer(): DoodleRendererLike | null;
  redraw(): void;
  sendMessage(msg: DoodleMessage): void;
  restoreSegments(segments: readonly DrawSegmentItem[]): void;
}

export type DoodleTransformType =
  | 'transform_stamp'
  | 'move_stamp'
  | 'resize_stamp'
  | 'rotate_stamp';

export interface CustomUndoableAction {
  readonly type?: string;
  undo(context?: DoodleUndoContext): boolean;
}

export type SerializedDoodleUndoAction =
  | {
      readonly type: 'draw_stroke';
      readonly stroke_id?: string;
      readonly segments?: readonly DrawSegmentItem[];
    }
  | {
      readonly type: 'delete_segments';
      readonly segments?: readonly DrawSegmentItem[];
    }
  | {
      readonly type: 'place_text';
      readonly action?: TextAnnotationItem | null;
    }
  | {
      readonly type: 'edit_text';
      readonly before?: TextAnnotationItem | null;
      readonly after?: TextAnnotationItem | null;
    }
  | {
      readonly type: 'move_text';
      readonly before?: TextAnnotationItem | null;
      readonly after?: TextAnnotationItem | null;
    }
  | {
      readonly type: 'delete_text';
      readonly items?: readonly TextAnnotationItem[];
    }
  | {
      readonly type: 'place_stamp';
      readonly action?: StampAnnotationItem | null;
      readonly replaced?: StampAnnotationItem | null;
    }
  | {
      readonly type: DoodleTransformType;
      readonly before?: StampAnnotationItem | null;
      readonly after?: StampAnnotationItem | null;
    }
  | {
      readonly type: 'delete_stamp';
      readonly items?: readonly StampAnnotationItem[];
    }
  | {
      readonly type: 'clear';
      readonly previousActions?: readonly DoodleCanvasItem[];
    }
  | {
      readonly type: 'compound_delete';
      readonly segments?: readonly DrawSegmentItem[];
      readonly stamps?: readonly StampAnnotationItem[];
      readonly texts?: readonly TextAnnotationItem[];
    };

export type DoodleActionInput = DoodleAction | CustomUndoableAction | SerializedDoodleUndoAction;

export interface CreateDoodleUndoManagerOptions {
  readonly getStack?: (() => DoodleAction[]) | null;
  readonly stack?: DoodleAction[];
  readonly maxHistory?: number;
  readonly getContext?: () => DoodleUndoContext;
  readonly onStateChange?: () => void;
  readonly isEnabled?: () => boolean;
}

export interface DoodleUndoManager {
  stack: DoodleAction[];
  canUndo(): boolean;
  push(action: DoodleActionInput | null | undefined): DoodleAction | null;
  undo(): boolean;
  clear(): void;
}

declare global {
  var doodleActions: DoodleCanvasItem[] | undefined;
  var doodleRenderer: DoodleRendererLike | null | undefined;
  var redrawAllDoodles: (() => void) | undefined;
  var sendOrQueueDoodleMessage: ((msg: DoodleMessage) => void) | undefined;

  interface Window {
    DoodleAction: typeof DoodleAction;
    DrawStrokeDoodleAction: typeof DrawStrokeDoodleAction;
    DeleteSegmentsDoodleAction: typeof DeleteSegmentsDoodleAction;
    PlaceTextDoodleAction: typeof PlaceTextDoodleAction;
    EditTextDoodleAction: typeof EditTextDoodleAction;
    MoveTextDoodleAction: typeof MoveTextDoodleAction;
    DeleteTextDoodleAction: typeof DeleteTextDoodleAction;
    PlaceStampDoodleAction: typeof PlaceStampDoodleAction;
    TransformStampDoodleAction: typeof TransformStampDoodleAction;
    DeleteStampDoodleAction: typeof DeleteStampDoodleAction;
    CompoundDeleteDoodleAction: typeof CompoundDeleteDoodleAction;
    ClearCanvasDoodleAction: typeof ClearCanvasDoodleAction;
    createDoodleUndoManager: typeof createDoodleUndoManager;
    restoreSegmentsToServer: typeof restoreSegmentsToServer;
  }
}

function insertStampBeforeNonStamps(
  actions: DoodleCanvasItem[],
  stamp: DoodleCanvasItem,
): void {
  const firstNonStamp = actions.findIndex((item) => item.type !== 'stamp');
  if (firstNonStamp >= 0) {
    actions.splice(firstNonStamp, 0, stamp);
  } else {
    actions.push(stamp);
  }
}

function restoreStampItems(
  actions: DoodleCanvasItem[],
  items: readonly StampAnnotationItem[],
  sendMessage: (msg: DoodleMessage) => void,
): void {
  for (const item of items) {
    const idx = actions.findIndex((a) => a.type === 'stamp' && a.id === item.id);
    if (idx >= 0) {
      actions[idx] = { ...item };
    } else {
      insertStampBeforeNonStamps(actions, { ...item });
    }
    sendMessage({ ...item });
  }
}

function restoreTextItems(
  actions: DoodleCanvasItem[],
  items: readonly TextAnnotationItem[],
  sendMessage: (msg: DoodleMessage) => void,
): void {
  for (const item of items) {
    const idx = actions.findIndex((a) => a.type === 'text' && a.id === item.id);
    if (idx >= 0) {
      actions[idx] = { ...item };
    } else {
      actions.push({ ...item });
    }
    sendMessage({ ...item });
  }
}

/** Batch contiguous drawing segments into draw_batch messages and send them to the server. */
export function restoreSegmentsToServer(
  segments: readonly DrawSegmentItem[],
  sendFn?: (msg: DrawBatchMessage) => void,
): void {
  if (!Array.isArray(segments) || segments.length === 0) {
    return;
  }
  const send =
    typeof sendFn === 'function'
      ? sendFn
      : typeof sendOrQueueDoodleMessage === 'function'
        ? (sendOrQueueDoodleMessage as (msg: DrawBatchMessage) => void)
        : null;
  if (!send) {
    return;
  }

  let currentBatch: DrawBatchMessage | null = null;
  for (const seg of segments) {
    const color = seg.color;
    const size = seg.size || 3;
    const stroke_id = seg.stroke_id;
    if (
      currentBatch &&
      currentBatch.color === color &&
      currentBatch.size === size &&
      currentBatch.stroke_id === stroke_id &&
      currentBatch.points.length <= 396 &&
      currentBatch.points[currentBatch.points.length - 2] === seg.x0 &&
      currentBatch.points[currentBatch.points.length - 1] === seg.y0
    ) {
      currentBatch.points.push(seg.x1, seg.y1);
    } else {
      if (currentBatch) {
        send(currentBatch);
      }
      currentBatch = {
        type: 'draw_batch',
        color,
        size,
        stroke_id,
        points: [seg.x0, seg.y0, seg.x1, seg.y1],
      };
    }
  }
  if (currentBatch) {
    send(currentBatch);
  }
}

function isCustomUndoable(data: DoodleActionInput): data is CustomUndoableAction {
  return 'undo' in data && typeof data.undo === 'function';
}

export class DoodleAction {
  readonly type: string;

  constructor(type: string) {
    this.type = type;
  }

  getContext(context: DoodleUndoContext = {}): ResolvedDoodleUndoContext {
    const sendMessage = (msg: DoodleMessage): void => {
      if (typeof context.sendOrQueueDoodleMessage === 'function') {
        context.sendOrQueueDoodleMessage(msg);
      } else if (typeof sendOrQueueDoodleMessage === 'function') {
        sendOrQueueDoodleMessage(msg);
      }
    };

    return {
      getActions: (): DoodleCanvasItem[] => {
        if (context.doodleActions !== undefined) {
          return context.doodleActions;
        }
        return typeof doodleActions !== 'undefined' ? doodleActions : [];
      },
      setActions: (actions: DoodleCanvasItem[]): void => {
        if (context.doodleActions !== undefined) {
          context.doodleActions = actions;
        }
        if (typeof doodleActions !== 'undefined') {
          doodleActions = actions;
        }
      },
      getRenderer: (): DoodleRendererLike | null => {
        if (context.doodleRenderer !== undefined) {
          return context.doodleRenderer;
        }
        return typeof doodleRenderer !== 'undefined' ? doodleRenderer : null;
      },
      redraw: (): void => {
        if (typeof context.redrawAllDoodles === 'function') {
          context.redrawAllDoodles();
        } else if (typeof redrawAllDoodles === 'function') {
          redrawAllDoodles();
        }
      },
      sendMessage,
      restoreSegments: (segments: readonly DrawSegmentItem[]): void => {
        if (typeof context.restoreSegmentsToServer === 'function') {
          context.restoreSegmentsToServer(segments, sendMessage);
        } else {
          restoreSegmentsToServer(segments, sendMessage);
        }
      },
    };
  }

  undo(_context?: DoodleUndoContext): boolean {
    throw new Error(`Undo not implemented for action type ${this.type}`);
  }

  static from(data: DoodleActionInput | null | undefined): DoodleAction | null {
    if (!data) {
      return null;
    }
    if (data instanceof DoodleAction || isCustomUndoable(data)) {
      return data as DoodleAction;
    }
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
  readonly stroke_id: string | undefined;
  readonly segments: DrawSegmentItem[];

  constructor(strokeId?: string, segments: readonly DrawSegmentItem[] = []) {
    super('draw_stroke');
    this.stroke_id = strokeId;
    this.segments = Array.isArray(segments) ? [...segments] : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
    const ctx = this.getContext(context);
    const strokeId = this.stroke_id;
    const segIds = new Set(
      this.segments.map((s) => s.id).filter((id): id is string => Boolean(id)),
    );
    let actions = ctx.getActions();
    actions = actions.filter((a) => {
      if (a.type !== 'draw') {
        return true;
      }
      if (strokeId && a.stroke_id === strokeId) {
        return false;
      }
      if (a.id && segIds.has(a.id)) {
        return false;
      }
      return true;
    });
    ctx.setActions(actions);
    ctx.getRenderer()?.clearSelectedSegments();
    ctx.redraw();
    ctx.sendMessage({
      type: 'remove_draw',
      stroke_id: strokeId,
      ids: Array.from(segIds),
    });
    return true;
  }
}

export class DeleteSegmentsDoodleAction extends DoodleAction {
  readonly segments: DrawSegmentItem[];

  constructor(segments: readonly DrawSegmentItem[] = []) {
    super('delete_segments');
    this.segments = Array.isArray(segments) ? [...segments] : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
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
  readonly action: TextAnnotationItem | null;

  constructor(action?: TextAnnotationItem | null) {
    super('place_text');
    this.action = action ? { ...action } : null;
  }

  override undo(context?: DoodleUndoContext): boolean {
    const targetId = this.action?.id;
    if (!targetId) {
      return false;
    }
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    const idx = actions.findIndex((a) => a.type === 'text' && a.id === targetId);
    if (idx >= 0) {
      actions.splice(idx, 1);
      ctx.sendMessage({ type: 'remove_text', id: targetId });
      ctx.redraw();
    }
    return true;
  }
}

export class EditTextDoodleAction extends DoodleAction {
  readonly before: TextAnnotationItem | null;
  readonly after: TextAnnotationItem | null;

  constructor(before?: TextAnnotationItem | null, after?: TextAnnotationItem | null) {
    super('edit_text');
    this.before = before ? { ...before } : null;
    this.after = after ? { ...after } : null;
  }

  override undo(context?: DoodleUndoContext): boolean {
    const before = this.before;
    if (!before?.id) {
      return false;
    }
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    const idx = actions.findIndex((a) => a.type === 'text' && a.id === before.id);
    if (idx >= 0) {
      actions[idx] = { ...before };
    } else {
      actions.push({ ...before });
    }
    ctx.sendMessage({ ...before });
    ctx.redraw();
    return true;
  }
}

export class MoveTextDoodleAction extends DoodleAction {
  readonly before: TextAnnotationItem | null;
  readonly after: TextAnnotationItem | null;

  constructor(before?: TextAnnotationItem | null, after?: TextAnnotationItem | null) {
    super('move_text');
    this.before = before ? { ...before } : null;
    this.after = after ? { ...after } : null;
  }

  override undo(context?: DoodleUndoContext): boolean {
    const before = this.before;
    if (!before?.id) {
      return false;
    }
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    const idx = actions.findIndex((a) => a.type === 'text' && a.id === before.id);
    if (idx >= 0) {
      actions[idx] = { ...before };
      ctx.sendMessage({ ...before });
      ctx.redraw();
    }
    return true;
  }
}

export class DeleteTextDoodleAction extends DoodleAction {
  readonly items: TextAnnotationItem[];

  constructor(items: readonly TextAnnotationItem[] = []) {
    super('delete_text');
    this.items = Array.isArray(items) ? items.map((i) => ({ ...i })) : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
    const ctx = this.getContext(context);
    restoreTextItems(ctx.getActions(), this.items, ctx.sendMessage);
    ctx.redraw();
    return true;
  }
}

export class PlaceStampDoodleAction extends DoodleAction {
  readonly action: StampAnnotationItem | null;
  readonly replaced: StampAnnotationItem | null;

  constructor(action?: StampAnnotationItem | null, replaced: StampAnnotationItem | null = null) {
    super('place_stamp');
    this.action = action ? { ...action } : null;
    this.replaced = replaced ? { ...replaced } : null;
  }

  override undo(context?: DoodleUndoContext): boolean {
    const targetId = this.action?.id;
    if (!targetId) {
      return false;
    }
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    const idx = actions.findIndex((a) => a.type === 'stamp' && a.id === targetId);
    if (idx >= 0) {
      actions.splice(idx, 1);
      ctx.sendMessage({ type: 'remove_stamp', id: targetId });
    }
    if (this.replaced) {
      const rep: DoodleCanvasItem = { ...this.replaced };
      insertStampBeforeNonStamps(actions, rep);
      ctx.sendMessage(rep);
    }
    ctx.redraw();
    return true;
  }
}

export class TransformStampDoodleAction extends DoodleAction {
  readonly before: StampAnnotationItem | null;
  readonly after: StampAnnotationItem | null;

  constructor(
    before?: StampAnnotationItem | null,
    after?: StampAnnotationItem | null,
    transformType: DoodleTransformType = 'transform_stamp',
  ) {
    super(transformType || 'transform_stamp');
    this.before = before ? { ...before } : null;
    this.after = after ? { ...after } : null;
  }

  override undo(context?: DoodleUndoContext): boolean {
    const before = this.before;
    if (!before?.id) {
      return false;
    }
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    const idx = actions.findIndex((a) => a.type === 'stamp' && a.id === before.id);
    if (idx >= 0) {
      actions[idx] = { ...before };
      ctx.sendMessage({ ...before });
      ctx.redraw();
    }
    return true;
  }
}

export class DeleteStampDoodleAction extends DoodleAction {
  readonly items: StampAnnotationItem[];

  constructor(items: readonly StampAnnotationItem[] = []) {
    super('delete_stamp');
    this.items = Array.isArray(items) ? items.map((i) => ({ ...i })) : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
    const ctx = this.getContext(context);
    restoreStampItems(ctx.getActions(), this.items, ctx.sendMessage);
    ctx.redraw();
    return true;
  }
}

export class CompoundDeleteDoodleAction extends DoodleAction {
  readonly segments: DrawSegmentItem[];
  readonly stamps: StampAnnotationItem[];
  readonly texts: TextAnnotationItem[];

  constructor(
    segments: readonly DrawSegmentItem[] = [],
    stamps: readonly StampAnnotationItem[] = [],
    texts: readonly TextAnnotationItem[] = [],
  ) {
    super('compound_delete');
    this.segments = Array.isArray(segments) ? segments.map((s) => ({ ...s })) : [];
    this.stamps = Array.isArray(stamps) ? stamps.map((s) => ({ ...s })) : [];
    this.texts = Array.isArray(texts) ? texts.map((t) => ({ ...t })) : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
    const ctx = this.getContext(context);
    const actions = ctx.getActions();
    if (this.segments.length > 0) {
      for (const seg of this.segments) {
        actions.push({ ...seg });
      }
      ctx.restoreSegments(this.segments);
    }
    if (this.stamps.length > 0) {
      restoreStampItems(actions, this.stamps, ctx.sendMessage);
    }
    if (this.texts.length > 0) {
      restoreTextItems(actions, this.texts, ctx.sendMessage);
    }
    ctx.redraw();
    return true;
  }
}

export class ClearCanvasDoodleAction extends DoodleAction {
  readonly previousActions: DoodleCanvasItem[];

  constructor(previousActions: readonly DoodleCanvasItem[] = []) {
    super('clear');
    this.previousActions = Array.isArray(previousActions)
      ? previousActions.map((a) => ({ ...a }))
      : [];
  }

  override undo(context?: DoodleUndoContext): boolean {
    const ctx = this.getContext(context);
    const restored = this.previousActions.map((a) => ({ ...a }));
    ctx.setActions(restored);
    ctx.redraw();
    const segs = restored.filter((a): a is DrawSegmentItem => a.type === 'draw');
    if (segs.length > 0) {
      ctx.restoreSegments(segs);
    }
    restored
      .filter((a) => a.type === 'stamp')
      .forEach((s) => ctx.sendMessage({ ...s }));
    restored
      .filter((a) => a.type === 'text')
      .forEach((t) => ctx.sendMessage({ ...t }));
    return true;
  }
}

/** Create a bounded undo stack manager coordinating action normalization and callbacks. */
export function createDoodleUndoManager({
  getStack = null,
  stack = [],
  maxHistory = 100,
  getContext = (): DoodleUndoContext => ({}),
  onStateChange = (): void => {},
  isEnabled = (): boolean => true,
}: CreateDoodleUndoManagerOptions = {}): DoodleUndoManager {
  let internalStack: DoodleAction[] = stack;
  const resolveStack = (): DoodleAction[] =>
    typeof getStack === 'function' ? getStack() : internalStack;

  return {
    get stack(): DoodleAction[] {
      return resolveStack();
    },
    set stack(val: DoodleAction[]) {
      internalStack = Array.isArray(val) ? val : [];
    },
    canUndo(): boolean {
      const s = resolveStack();
      return s.length > 0 && (typeof isEnabled === 'function' ? isEnabled() : true);
    },
    push(action: DoodleActionInput | null | undefined): DoodleAction | null {
      const doodleAction =
        action && (action instanceof DoodleAction || isCustomUndoable(action))
          ? (action as DoodleAction)
          : DoodleAction.from(action);
      if (!doodleAction) {
        return null;
      }
      const s = resolveStack();
      s.push(doodleAction);
      if (s.length > maxHistory) {
        s.shift();
      }
      if (typeof onStateChange === 'function') {
        onStateChange();
      }
      return doodleAction;
    },
    undo(): boolean {
      const s = resolveStack();
      if (s.length === 0) {
        return false;
      }
      const action = s.pop();
      if (typeof onStateChange === 'function') {
        onStateChange();
      }
      if (action && typeof action.undo === 'function') {
        const ctx = typeof getContext === 'function' ? getContext() : {};
        return action.undo(ctx);
      }
      return false;
    },
    clear(): void {
      const s = resolveStack();
      s.length = 0;
      if (typeof onStateChange === 'function') {
        onStateChange();
      }
    },
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

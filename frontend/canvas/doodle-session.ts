import { canvasCursorState, type CanvasCursorState } from './cursor-state.js';
import {
  ClearCanvasDoodleAction,
  CompoundDeleteDoodleAction,
  DeleteSegmentsDoodleAction,
  DeleteTextDoodleAction,
  DrawStrokeDoodleAction,
  createDoodleUndoManager,
  restoreSegmentsToServer,
  type DoodleAction,
  type DoodleActionInput,
  type DoodleCanvasItem,
  type DoodleMessage,
  type DoodleUndoContext,
  type DoodleUndoManager,
  type DrawSegmentItem,
  type StampAnnotationItem,
  type TextAnnotationItem,
} from './doodle-actions.js';
import { createDoodleRenderer, type DoodleAnnotation, type DoodleRenderer } from './doodle-renderer.js';
import { createTextAnnotations, type TextAnnotation } from './text-annotations.js';
import type { HistoryItem } from './history-paging.js';

export const DOODLE_RECONNECT_DELAY_MS = 5000;

export function isActiveCanvasWindow(): boolean {
  return document.visibilityState === 'visible' && document.hasFocus();
}

export interface SessionDoodleAction extends DoodleAnnotation {
  type: 'draw' | 'text' | 'stamp';
  client_message_id?: string;
  page_id?: string;
  [key: string]: unknown;
}

export interface DrawBatchPayload {
  type: 'draw_batch';
  stroke_id: string;
  page_id: string | null;
  color: string;
  size: number;
  points: number[];
  client_message_id?: string;
  [key: string]: unknown;
}

export interface DoodleSnapshotPayload {
  type?: string;
  page_id?: string | null;
  enabled?: boolean;
  stamps?: readonly DoodleCanvasItem[];
  batches?: readonly DoodleBatchWirePayload[];
  annotations?: readonly DoodleCanvasItem[];
}

export interface DoodleBatchWirePayload {
  stroke_id?: string;
  client_message_id?: string;
  color: string;
  size?: number;
  points: readonly number[];
}

export interface DeleteTransaction {
  type: 'compound_delete';
  segments: DrawSegmentItem[];
  stamps: StampAnnotationItem[];
  texts: TextAnnotationItem[];
}

interface DoodleSocketPayload extends DoodleSnapshotPayload {
  type: string;
  client_message_id?: string;
  stroke_id?: string;
  id?: string;
  ids?: readonly string[];
  x0?: number;
  y0?: number;
  x1?: number;
  y1?: number;
  color?: string;
  size?: number;
  points?: readonly number[];
  baton_state?: unknown;
  active_viewers?: unknown;
}

export interface CreateDoodleSessionOptions {
  readonly theaterId: string | null;
  readonly canvas: HTMLCanvasElement;
  readonly imgContainer: HTMLElement;
  readonly pagingControls: HTMLElement | null;
  readonly doodleToolbar: HTMLElement | null;
  readonly doodlesToggleBtn: HTMLButtonElement | null;
  readonly paletteToggle: HTMLElement | null;
  readonly paletteMenu: HTMLElement | null;
  readonly colorBtns: readonly HTMLElement[];
  readonly clearBtn: HTMLElement | null;
  readonly undoBtn: HTMLButtonElement | null;
  readonly brushSizeSlider: HTMLInputElement | null;
  readonly textAnnotationToggle: HTMLButtonElement | null;
  readonly textAnnotationMenu: HTMLElement | null;
  readonly textAnnotationFont: HTMLSelectElement | null;
  readonly textAnnotationSize: HTMLInputElement | null;
  readonly textAnnotationSizeValue: HTMLElement | null;
  readonly textAnnotationColors: readonly HTMLElement[];
  readonly textLayer: HTMLElement | null;
  readonly stampLayer: HTMLElement | null;
  readonly isOrator: () => boolean;
  readonly isCollabModeEnabled: () => boolean;
  readonly isPagedBack: () => boolean;
  readonly getPage: () => { width: number; height: number } | null;
  readonly canPlaceStamps: () => boolean;
  readonly stopSceneSpeech: () => void;
  readonly setDrawingColorSelected: (selected: boolean) => void;
  readonly onMoveStamp: (
    action: DoodleAnnotation,
    previous: DoodleAnnotation,
    commit: boolean,
  ) => void;
  readonly onRemoveStamp: (action: DoodleAnnotation) => void;
  readonly onSelectStamp: (action: DoodleAnnotation) => void;
  readonly onApplyStampAnnotation: (action: DoodleAnnotation) => void;
  readonly onBringStampToFront: (stampId: string) => void;
  readonly onDoodlesDisplayChanged?: () => void;
  readonly onHandleSocketData?: (data: DoodleSocketPayload) => boolean;
  readonly onToggleMicSocketMessage?: () => void;
  readonly onBatonStateSocketMessage?: (batonState: unknown, activeViewers: unknown) => void;
  readonly isActionWheelRightClickBound?: () => boolean;
}

export interface DoodleSessionController {
  readonly doodleRenderer: DoodleRenderer;
  readonly doodleUndoManager: DoodleUndoManager;
  getDoodleActions(): SessionDoodleAction[];
  setDoodleActions(actions: readonly DoodleCanvasItem[]): void;
  getDoodleUndoStack(): DoodleAction[];
  setDoodleUndoStack(stack: DoodleAction[]): void;
  getActiveDeleteTransaction(): DeleteTransaction | null;
  getPendingTextEdits(): Map<string, DoodleCanvasItem>;
  isDoodlesEnabled(): boolean;
  setDoodlesDisplay(enabled: boolean): void;
  displayPageDoodles(item: HistoryItem): void;
  requestPageDoodles(pageId?: string | null): Promise<void>;
  sendOrQueueDoodleMessage(message: DoodleMessage): void;
  flushDrawBatch(): void;
  redrawAllDoodles(): void;
  resizeDoodleCanvas(): void;
  pushUndoAction(action: DoodleActionInput): void;
  undoLastDoodleAction(): boolean;
  updateUndoButtonState(): void;
  updateTextAnnotationAvailability(): void;
  refreshCanvasToolInteraction(): void;
  cycleCanvasCursor(): void;
  cancelDrawing(): void;
  destroy(): void;
}

function isSessionDoodleAction(item: DoodleCanvasItem): item is SessionDoodleAction {
  return item.type === 'draw' || item.type === 'text' || item.type === 'stamp';
}

function toDrawSegmentItem(action: SessionDoodleAction): DrawSegmentItem | null {
  if (
    action.type !== 'draw' ||
    typeof action.x0 !== 'number' ||
    typeof action.y0 !== 'number' ||
    typeof action.x1 !== 'number' ||
    typeof action.y1 !== 'number' ||
    typeof action.color !== 'string'
  ) {
    return null;
  }
  return {
    ...action,
    type: 'draw',
    x0: action.x0,
    y0: action.y0,
    x1: action.x1,
    y1: action.y1,
    color: action.color,
    size: action.size,
  };
}

function toTextAnnotationItem(action: SessionDoodleAction): TextAnnotationItem | null {
  if (
    action.type !== 'text' ||
    typeof action.x !== 'number' ||
    typeof action.y !== 'number' ||
    typeof action.text !== 'string' ||
    typeof action.color !== 'string' ||
    typeof action.size !== 'number'
  ) {
    return null;
  }
  return {
    ...action,
    type: 'text',
    x: action.x,
    y: action.y,
    text: action.text,
    color: action.color,
    size: action.size,
    font: action.font,
  };
}

function toTextAnnotation(action: DoodleAnnotation): TextAnnotation | null {
  if (
    action.type !== 'text' ||
    typeof action.id !== 'string' ||
    typeof action.x !== 'number' ||
    typeof action.y !== 'number' ||
    typeof action.text !== 'string' ||
    typeof action.color !== 'string' ||
    typeof action.size !== 'number'
  ) {
    return null;
  }
  return {
    ...action,
    type: 'text',
    id: action.id,
    x: action.x,
    y: action.y,
    text: action.text,
    color: action.color,
    size: action.size,
    font: action.font,
  };
}

function generateClientId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function isEditableElement(target: Element | null): boolean {
  if (!target) return false;
  const htmlEl = target as HTMLElement;
  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' ||
    htmlEl.isContentEditable ||
    target.getAttribute('contenteditable') === 'true'
  );
}

/**
 * Orchestrate the canvas doodle WebSocket lifecycle, optimistic offline message
 * queue, stroke/text drawing tools, palette/menu UI, and undo/delete actions.
 */
export function createDoodleSession(
  options: CreateDoodleSessionOptions,
): DoodleSessionController {
  const {
    theaterId,
    canvas,
    imgContainer,
    pagingControls,
    doodleToolbar,
    doodlesToggleBtn,
    paletteToggle,
    paletteMenu,
    colorBtns,
    clearBtn,
    undoBtn,
    brushSizeSlider,
    textAnnotationToggle,
    textAnnotationMenu,
    textAnnotationFont,
    textAnnotationSize,
    textAnnotationSizeValue,
    textAnnotationColors,
    textLayer,
    stampLayer,
    isOrator,
    isCollabModeEnabled,
    isPagedBack,
    getPage,
    canPlaceStamps,
    stopSceneSpeech,
    setDrawingColorSelected,
    onMoveStamp,
    onRemoveStamp,
    onSelectStamp,
    onApplyStampAnnotation,
    onBringStampToFront,
    onDoodlesDisplayChanged,
    onHandleSocketData,
    onToggleMicSocketMessage,
    onBatonStateSocketMessage,
    isActionWheelRightClickBound,
  } = options;

  let currentColor: string | null = null;
  let lastSelectedTool: { type: 'marker'; color: string } | { type: 'text' } = {
    type: 'marker',
    color: '#ef4444',
  };
  let currentBrushSize = 3;
  let isDrawing = false;
  let currentStrokeId: string | null = null;
  let lastNormX = 0;
  let lastNormY = 0;
  let doodlesEnabled = true;
  let displayedDoodlePageId: string | null = null;
  let doodlePageRequestSequence = 0;
  let textAnnotationMode = false;
  let currentTextColor = '#ffffff';
  let activeTextEditor: HTMLTextAreaElement | null = null;

  let doodleActions: SessionDoodleAction[] = [];
  let doodleUndoStack: DoodleAction[] = [];
  let activeDeleteTransaction: DeleteTransaction | null = null;
  let pendingDrawBatch: DrawBatchPayload | null = null;
  let drawBatchFrame: number | null = null;

  // Keep drawing responsive even while reconnecting. The queue
  // is flushed only after the server's initial snapshot arrives,
  // so that snapshot cannot overwrite locally drawn strokes.
  const pendingDoodleMessages: DoodleMessage[] = [];
  const inFlightDoodleMessages = new Map<string, DoodleMessage>();
  const pendingTextEdits = new Map<string, SessionDoodleAction>();
  let nextDoodleMessageId = 0;
  let ws: WebSocket | null = null;
  let doodleSocketReady = false;
  let doodleReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let destroyed = false;

  const doodleRenderer = createDoodleRenderer({
    canvas,
    getPage,
    isVisible: () => doodlesEnabled,
    textLayer,
    canEditText: () =>
      canAddTextAnnotation() && !isPagedBack() && canvasCursorState.current.type === 'select',
    onEditText: (action) => openTextAnnotationEditor(action),
    onMoveText: (action, previous, commit) => moveTextAnnotation(action, previous, commit),
    stampLayer,
    canMoveStamp: () =>
      canPlaceStamps() && !isPagedBack() && canvasCursorState.current.type === 'select',
    onMoveStamp,
    onRemoveStamp,
    onSelectStamp,
    onRemoveText: (action) => removeTextAnnotation(action),
    canSelectSegment: () =>
      doodlesEnabled && !isPagedBack() && canvasCursorState.current.type === 'select',
    onRemoveSegment: (removed) => {
      const removedIds = new Set<string>();
      const removedRefs = new Set<DoodleAnnotation>();
      for (const item of removed || []) {
        if (item.id) removedIds.add(item.id);
        else removedRefs.add(item);
      }
      const removedActions = doodleActions.filter(
        (a) => (a.id && removedIds.has(a.id)) || removedRefs.has(a),
      );
      doodleActions = doodleActions.filter(
        (a) => !(a.id && removedIds.has(a.id)) && !removedRefs.has(a),
      );
      const removedSegments = removedActions
        .map(toDrawSegmentItem)
        .filter((seg): seg is DrawSegmentItem => seg !== null);
      if (removedSegments.length > 0) {
        if (activeDeleteTransaction) {
          activeDeleteTransaction.segments.push(...removedSegments);
        } else {
          pushUndoAction(new DeleteSegmentsDoodleAction(removedSegments));
        }
        sendOrQueueDoodleMessage({
          type: 'remove_draw',
          ids: removedSegments.map((s) => s.id).filter((id): id is string => Boolean(id)),
        });
      }
      redrawAllDoodles();
    },
  });

  const resizeDoodleCanvas = (): void => {
    doodleRenderer.resize(doodleActions);
  };

  const redrawAllDoodles = (): void => {
    doodleRenderer.redraw(doodleActions);
  };

  function updateUndoButtonState(): void {
    if (!undoBtn) return;
    const canUndo = doodleUndoManager.canUndo() && doodlesEnabled && !isPagedBack();
    undoBtn.disabled = !canUndo;
    undoBtn.style.opacity = canUndo ? '1' : '0.4';
    undoBtn.style.cursor = canUndo ? 'pointer' : 'not-allowed';
  }

  function getDoodleUndoContext(): DoodleUndoContext {
    return {
      get doodleActions(): DoodleCanvasItem[] {
        return doodleActions;
      },
      set doodleActions(val: DoodleCanvasItem[]) {
        doodleActions = Array.isArray(val) ? val.filter(isSessionDoodleAction) : [];
      },
      doodleRenderer,
      redrawAllDoodles,
      sendOrQueueDoodleMessage,
      restoreSegmentsToServer,
    };
  }

  const doodleUndoManager = createDoodleUndoManager({
    getStack: () => doodleUndoStack,
    getContext: getDoodleUndoContext,
    onStateChange: () => updateUndoButtonState(),
    isEnabled: () => doodlesEnabled && !isPagedBack(),
  });

  function pushUndoAction(action: DoodleActionInput): void {
    doodleUndoManager.push(action);
  }

  function undoLastDoodleAction(): boolean {
    return doodleUndoManager.undo();
  }

  function renderDoodleBatch(data: DoodleBatchWirePayload): void {
    const points = data.points;
    if (!Array.isArray(points) || points.length < 4) return;
    const batchStrokeId =
      data.stroke_id || data.client_message_id || generateClientId('batch');
    for (let index = 0; index < points.length - 2; index += 2) {
      const action: SessionDoodleAction = {
        type: 'draw',
        stroke_id: batchStrokeId,
        x0: points[index],
        y0: points[index + 1],
        x1: points[index + 2],
        y1: points[index + 3],
        color: data.color,
        size: data.size || 3,
      };
      doodleActions.push(action);
      doodleRenderer.renderStrokeSegment(action);
    }
  }

  function displayPageDoodles(item: HistoryItem): void {
    const pageId = item.page_id || null;
    if (displayedDoodlePageId === pageId) return;
    flushDrawBatch();
    isDrawing = false;
    activeTextEditor?.remove();
    activeTextEditor = null;
    displayedDoodlePageId = pageId;
    doodleActions = [];
    doodleUndoStack = [];
    updateUndoButtonState();
    doodleRenderer.clearSelectedSegments();
    setDoodlesDisplay(item.doodles_enabled !== false);
    redrawAllDoodles();
    void requestPageDoodles(pageId);
  }

  async function requestPageDoodles(pageId: string | null = displayedDoodlePageId): Promise<void> {
    if (!pageId) return;
    const sequence = ++doodlePageRequestSequence;
    if (ws && ws.readyState === WebSocket.OPEN) {
      doodleSocketReady = false;
      ws.send(JSON.stringify({ type: 'subscribe_doodles', page_id: pageId }));
      return;
    }
    try {
      const params = new URLSearchParams({
        theater_id: theaterId || '',
        page_id: pageId,
      });
      const response = await fetch(`/api/doodles?${params.toString()}`);
      if (!response.ok) throw new Error(`Unable to load page doodles (${response.status}).`);
      const snapshot = (await response.json()) as DoodleSnapshotPayload;
      if (sequence === doodlePageRequestSequence) {
        applyDoodleSnapshot(snapshot);
      }
    } catch (error) {
      console.warn('Could not load page doodles.', error);
    }
  }

  function applyDoodleSnapshot(data: DoodleSnapshotPayload): void {
    if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;
    displayedDoodlePageId = data.page_id || displayedDoodlePageId;
    doodleActions = [];
    for (const action of data.stamps || []) {
      if (isSessionDoodleAction(action)) doodleActions.push(action);
    }
    for (const batch of data.batches || []) {
      renderDoodleBatch(batch);
    }
    for (const action of data.annotations || []) {
      if (isSessionDoodleAction(action)) doodleActions.push(action);
    }
    if (data.enabled !== undefined) {
      setDoodlesDisplay(data.enabled);
    }
    redrawAllDoodles();
  }

  function flushDrawBatch(): void {
    drawBatchFrame = null;
    if (!pendingDrawBatch) return;
    const batch = pendingDrawBatch;
    pendingDrawBatch = null;
    sendOrQueueDoodleMessage(batch);
  }

  function sendOrQueueDoodleMessage(message: DoodleMessage): void {
    if (message.page_id === undefined && displayedDoodlePageId) {
      message.page_id = displayedDoodlePageId;
    }
    if (typeof message.client_message_id !== 'string' || !message.client_message_id) {
      nextDoodleMessageId += 1;
      message.client_message_id = `${Date.now()}-${nextDoodleMessageId}`;
    }
    const clientMessageId = String(message.client_message_id);
    if (ws && ws.readyState === WebSocket.OPEN && doodleSocketReady) {
      try {
        ws.send(JSON.stringify(message));
        inFlightDoodleMessages.set(clientMessageId, message);
      } catch {
        pendingDoodleMessages.push(message);
        scheduleDoodleReconnect(0);
      }
      return;
    }
    pendingDoodleMessages.push(message);
    scheduleDoodleReconnect(0);
  }

  function flushQueuedDoodleMessages(): void {
    if (!ws || ws.readyState !== WebSocket.OPEN || !doodleSocketReady) return;
    const queuedMessages = pendingDoodleMessages.splice(0);
    for (const message of queuedMessages) {
      sendOrQueueDoodleMessage(message);
      if (typeof message.page_id === 'string' && message.page_id !== displayedDoodlePageId) {
        continue;
      }
      if (message.type === 'draw_batch') {
        renderDoodleBatch(message as unknown as DoodleBatchWirePayload);
      } else if (message.type === 'clear') {
        doodleActions = [];
        redrawAllDoodles();
      } else if (message.type === 'toggle_doodles') {
        setDoodlesDisplay(Boolean(message.enabled));
      } else if (message.type === 'text' && isSessionDoodleAction(message)) {
        applyTextAnnotation(message);
      } else if (message.type === 'stamp' && isSessionDoodleAction(message)) {
        onApplyStampAnnotation(message);
      } else if (message.type === 'remove_stamp') {
        const idx = doodleActions.findIndex((a) => a.type === 'stamp' && a.id === message.id);
        if (idx >= 0) {
          doodleActions.splice(idx, 1);
          redrawAllDoodles();
        }
      } else if (message.type === 'remove_text') {
        const idx = doodleActions.findIndex((a) => a.type === 'text' && a.id === message.id);
        if (idx >= 0) {
          doodleActions.splice(idx, 1);
          redrawAllDoodles();
        }
      } else if (message.type === 'remove_draw') {
        const strokeId = typeof message.stroke_id === 'string' ? message.stroke_id : null;
        const ids = Array.isArray(message.ids) ? new Set(message.ids.map(String)) : null;
        doodleActions = doodleActions.filter((a) => {
          if (a.type !== 'draw') return true;
          if (strokeId && a.stroke_id === strokeId) return false;
          if (ids && a.id && ids.has(a.id)) return false;
          return true;
        });
        redrawAllDoodles();
      }
    }
  }

  function scheduleDoodleReconnect(delay = DOODLE_RECONNECT_DELAY_MS): void {
    // A closed socket does not need to be revived merely to
    // redraw server-persisted strokes. Reconnect only when
    // this browser has local doodle changes to deliver.
    if (
      destroyed ||
      !pendingDoodleMessages.length ||
      doodleReconnectTimer !== null ||
      !isActiveCanvasWindow()
    ) {
      return;
    }
    doodleReconnectTimer = setTimeout(() => {
      doodleReconnectTimer = null;
      connectDoodleSocket();
    }, delay);
  }

  function canAddTextAnnotation(): boolean {
    return doodlesEnabled && (isOrator() || isCollabModeEnabled());
  }

  function setPaletteOpen(open: boolean): void {
    if (!paletteToggle || !paletteMenu) return;
    paletteToggle.setAttribute('aria-expanded', String(open));
    paletteMenu.classList.toggle('open', open);
    paletteMenu.hidden = !open;
  }

  function setTextAnnotationMenuOpen(open: boolean): void {
    if (!textAnnotationToggle || !textAnnotationMenu) return;
    textAnnotationToggle.setAttribute('aria-expanded', String(open));
    textAnnotationMenu.classList.toggle('open', open);
    textAnnotationMenu.hidden = !open;
  }

  function refreshCanvasToolInteraction(): void {
    if (textLayer) {
      textLayer.hidden = !doodlesEnabled;
      textLayer.style.zIndex = currentColor !== null ? '4' : '6';
    }
    if (stampLayer) {
      stampLayer.hidden = !doodlesEnabled;
      stampLayer.style.zIndex = '4';
    }
    const hasCanvasTool = currentColor !== null || textAnnotationMode;
    const canvasInteractive = hasCanvasTool && doodlesEnabled && !isPagedBack();
    canvas.classList.toggle('drawing-disabled', !canvasInteractive);
    canvas.style.pointerEvents = canvasInteractive ? 'auto' : 'none';
    canvas.style.cursor = canvasInteractive
      ? textAnnotationMode
        ? 'text'
        : 'crosshair'
      : 'default';
    setDrawingColorSelected(canvasInteractive);
  }

  function applyCanvasCursorState(state: CanvasCursorState): void {
    // These values are projections for drawing code; only this function updates them.
    currentColor = state.type === 'marker' ? state.color : null;
    textAnnotationMode = state.type === 'text';
    imgContainer.dataset.cursorMode = state.type;
    isDrawing = false;
    currentStrokeId = null;
    flushDrawBatch();
    activeTextEditor?.remove();
    activeTextEditor = null;
    if (state.type === 'marker') {
      lastSelectedTool = { type: 'marker', color: state.color };
    } else if (textAnnotationMode) {
      lastSelectedTool = { type: 'text' };
    }
    doodleRenderer.cancelInteraction();
    colorBtns.forEach((button) => {
      const active = state.type === 'marker' && button.dataset.color === state.color;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    textAnnotationToggle?.classList.toggle('active', textAnnotationMode);
    textAnnotationToggle?.setAttribute('aria-pressed', String(textAnnotationMode));
    setTextAnnotationMenuOpen(false);
    pagingControls?.classList.remove('canvas-selected');
    refreshCanvasToolInteraction();
  }

  function setTextAnnotationMode(active: boolean, openMenu = active): void {
    if (active && !canAddTextAnnotation()) return;
    canvasCursorState.select({ type: active ? 'text' : 'select' });
    setTextAnnotationMenuOpen(Boolean(openMenu && textAnnotationMode));
  }

  function updateTextAnnotationAvailability(): void {
    if (!textAnnotationToggle) return;
    const available = canAddTextAnnotation();
    textAnnotationToggle.disabled = !available;
    textAnnotationToggle.title = available
      ? 'Add text annotation'
      : 'Text annotations require collaboration mode for viewers';
    if (!available && textAnnotationMode) {
      activeTextEditor?.remove();
      activeTextEditor = null;
      setTextAnnotationMode(false, false);
    }
  }

  function setDoodlesDisplay(enabled: boolean): void {
    doodlesEnabled = Boolean(enabled);
    canvas.style.display = doodlesEnabled ? 'block' : 'none';
    if (doodlesEnabled) {
      doodleRenderer.resize(doodleActions);
    }
    if (doodleToolbar) {
      doodleToolbar.style.display = doodlesEnabled ? 'flex' : 'none';
    }
    if (doodlesToggleBtn) {
      doodlesToggleBtn.textContent = doodlesEnabled ? '✏️ Doodles: On' : '✏️ Doodles: Off';
      doodlesToggleBtn.title = doodlesEnabled
        ? 'Hide doodles for this page.'
        : 'Show doodles for this page.';
      doodlesToggleBtn.style.color = doodlesEnabled ? '#a5b4fc' : '#94a3b8';
      doodlesToggleBtn.style.background = doodlesEnabled
        ? 'rgba(99, 102, 241, 0.2)'
        : 'rgba(255, 255, 255, 0.08)';
      doodlesToggleBtn.style.borderColor = doodlesEnabled
        ? 'rgba(99, 102, 241, 0.4)'
        : 'var(--glass-border)';
    }
    updateTextAnnotationAvailability();
    updateUndoButtonState();
    refreshCanvasToolInteraction();
    onDoodlesDisplayChanged?.();
  }

  function setDrawingColor(color: string | null | undefined): void {
    canvasCursorState.select(color ? { type: 'marker', color } : { type: 'select' });
  }

  function cycleCanvasCursor(): void {
    const current = canvasCursorState.current.type;
    if (current === 'marker' || current === 'text') {
      canvasCursorState.select({ type: 'select' });
    } else if (current === 'select') {
      canvasCursorState.select({ type: 'navigate' });
    } else if (!doodlesEnabled) {
      canvasCursorState.select({ type: 'select' });
    } else if (lastSelectedTool.type === 'text' && canAddTextAnnotation()) {
      setTextAnnotationMode(true, false);
    } else {
      const color = lastSelectedTool.type === 'marker' ? lastSelectedTool.color : '#ef4444';
      setDrawingColor(color);
    }
  }

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl =
    `${wsProtocol}//${window.location.host}/ws/doodle` +
    (theaterId ? `?theater_id=${encodeURIComponent(theaterId)}` : '');

  function connectDoodleSocket(initialConnection = false): void {
    if (destroyed) return;
    // The first connection must not depend on focus: on a
    // newly opened page, the script can run before the window
    // has received focus. Subsequent retries stay limited to
    // an active window by scheduleDoodleReconnect.
    if (
      (!initialConnection && !isActiveCanvasWindow()) ||
      (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))
    ) {
      return;
    }
    const socketPageId = displayedDoodlePageId;
    const socketUrl = socketPageId ? `${wsUrl}&page_id=${encodeURIComponent(socketPageId)}` : wsUrl;
    const socket = new WebSocket(socketUrl);
    ws = socket;
    doodleSocketReady = false;

    socket.onopen = () => {
      // Reconnect to the page being browsed, which may differ from the orator.
      if (displayedDoodlePageId && displayedDoodlePageId !== socketPageId) {
        void requestPageDoodles();
      }
    };

    socket.onclose = () => {
      if (ws === socket) {
        doodleSocketReady = false;
        for (const message of inFlightDoodleMessages.values()) {
          pendingDoodleMessages.push(message);
        }
        inFlightDoodleMessages.clear();
        scheduleDoodleReconnect();
      }
    };

    socket.onerror = () => socket.close();

    socket.onmessage = (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as DoodleSocketPayload;
      if (onHandleSocketData && onHandleSocketData(data)) return;
      if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;

      if (data.type === 'doodles_toggle') {
        setDoodlesDisplay(Boolean(data.enabled));
      } else if (data.type === 'clear') {
        doodleActions = [];
        redrawAllDoodles();
      } else if (data.type === 'doodle_snapshot') {
        doodlePageRequestSequence += 1;
        applyDoodleSnapshot(data);
        doodleSocketReady = true;
        flushQueuedDoodleMessages();
      } else if (data.type === 'doodle_ack') {
        if (data.client_message_id) {
          inFlightDoodleMessages.delete(data.client_message_id);
          pendingTextEdits.delete(data.client_message_id);
        }
      } else if (data.type === 'text_annotation_rejected' || data.type === 'stamp_rejected') {
        if (data.client_message_id) {
          inFlightDoodleMessages.delete(data.client_message_id);
          const index = doodleActions.findIndex(
            (action) => action.client_message_id === data.client_message_id,
          );
          if (index >= 0) {
            const previous = pendingTextEdits.get(data.client_message_id);
            if (previous) doodleActions[index] = previous;
            else doodleActions.splice(index, 1);
            pendingTextEdits.delete(data.client_message_id);
            redrawAllDoodles();
          }
        }
      } else if (data.type === 'draw_batch' && Array.isArray(data.points) && typeof data.color === 'string') {
        renderDoodleBatch({
          stroke_id: data.stroke_id,
          client_message_id: data.client_message_id,
          color: data.color,
          size: data.size,
          points: data.points,
        });
      } else if (
        data.type === 'draw' &&
        typeof data.x0 === 'number' &&
        typeof data.y0 === 'number' &&
        typeof data.x1 === 'number' &&
        typeof data.y1 === 'number' &&
        typeof data.color === 'string'
      ) {
        const strokeId = data.stroke_id || data.client_message_id || generateClientId('stroke');
        const drawAction: SessionDoodleAction = {
          type: 'draw',
          id: data.id,
          stroke_id: strokeId,
          client_message_id: data.client_message_id,
          x0: data.x0,
          y0: data.y0,
          x1: data.x1,
          y1: data.y1,
          color: data.color,
          size: data.size || 3,
        };
        doodleActions.push(drawAction);
        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        doodleRenderer.renderSegment(
          data.x0 * rect.width,
          data.y0 * rect.height,
          data.x1 * rect.width,
          data.y1 * rect.height,
          data.color,
          data.size || 3,
        );
      } else if (data.type === 'text') {
        const textAction = data as unknown as DoodleCanvasItem;
        if (isSessionDoodleAction(textAction)) {
          applyTextAnnotation(textAction);
        }
      } else if (data.type === 'stamp') {
        const stampAction = data as unknown as DoodleCanvasItem;
        if (isSessionDoodleAction(stampAction)) {
          onApplyStampAnnotation(stampAction);
        }
      } else if (data.type === 'select_stamp') {
        if (data.id) {
          onBringStampToFront(data.id);
        }
      } else if (data.type === 'remove_stamp') {
        const idx = doodleActions.findIndex((a) => a.type === 'stamp' && a.id === data.id);
        if (idx >= 0) {
          doodleActions.splice(idx, 1);
          redrawAllDoodles();
        }
      } else if (data.type === 'remove_text') {
        const idx = doodleActions.findIndex((a) => a.type === 'text' && a.id === data.id);
        if (idx >= 0) {
          doodleActions.splice(idx, 1);
          redrawAllDoodles();
        }
      } else if (data.type === 'remove_draw') {
        const strokeId = data.stroke_id;
        const ids = Array.isArray(data.ids) ? new Set(data.ids) : null;
        doodleActions = doodleActions.filter((a) => {
          if (a.type !== 'draw') return true;
          if (strokeId && a.stroke_id === strokeId) return false;
          if (ids && a.id && ids.has(a.id)) return false;
          return true;
        });
        redrawAllDoodles();
      } else if (data.type === 'toggle_mic') {
        onToggleMicSocketMessage?.();
      } else if (data.type === 'baton_state') {
        onBatonStateSocketMessage?.(data.baton_state, data.active_viewers);
      }
    };
  }

  function drawLine(
    nx0: number,
    ny0: number,
    nx1: number,
    ny1: number,
    color: string,
    size: number,
    emit: boolean,
  ): void {
    const strokeId = currentStrokeId || generateClientId('stroke');
    const action: SessionDoodleAction = {
      type: 'draw',
      stroke_id: strokeId,
      x0: nx0,
      y0: ny0,
      x1: nx1,
      y1: ny1,
      color,
      size,
    };

    doodleActions.push(action);
    doodleRenderer.renderStrokeSegment(action);

    if (!emit) return;

    if (
      !pendingDrawBatch ||
      pendingDrawBatch.color !== color ||
      pendingDrawBatch.size !== size ||
      pendingDrawBatch.stroke_id !== strokeId ||
      pendingDrawBatch.points[pendingDrawBatch.points.length - 2] !== nx0 ||
      pendingDrawBatch.points[pendingDrawBatch.points.length - 1] !== ny0
    ) {
      flushDrawBatch();
      pendingDrawBatch = {
        type: 'draw_batch',
        stroke_id: strokeId,
        page_id: displayedDoodlePageId,
        color,
        size,
        points: [nx0, ny0],
      };
    }
    pendingDrawBatch.points.push(nx1, ny1);
    if (drawBatchFrame === null) {
      drawBatchFrame = requestAnimationFrame(flushDrawBatch);
    }
  }

  function applyTextAnnotation(action: SessionDoodleAction): void {
    const index = doodleActions.findIndex(
      (existing) => existing.type === 'text' && existing.id === action.id,
    );
    if (index >= 0) doodleActions[index] = action;
    else doodleActions.push(action);
    redrawAllDoodles();
  }

  const textAnnotations = createTextAnnotations({
    canvas,
    container: imgContainer,
    canEdit: canAddTextAnnotation,
    isPagedBack,
    stopSpeech: stopSceneSpeech,
    getActiveEditor: () => activeTextEditor,
    setActiveEditor: (editor) => {
      activeTextEditor = editor;
    },
    getDefaults: () => ({
      color: currentTextColor,
      font: textAnnotationFont?.value || 'Outfit',
      size: Number(textAnnotationSize?.value || 32),
    }),
    getImagePlane: (existing) => (getPage() ? doodleRenderer.imagePlane(existing) : null),
    send: (action) => sendOrQueueDoodleMessage(action as unknown as DoodleMessage),
    rememberEdit: (action, previous) => {
      if (action.client_message_id) {
        pendingTextEdits.set(action.client_message_id, previous as unknown as SessionDoodleAction);
      }
    },
    apply: (action) => applyTextAnnotation(action as unknown as SessionDoodleAction),
    pushUndo: pushUndoAction,
  });

  function moveTextAnnotation(
    action: DoodleAnnotation,
    previous: DoodleAnnotation,
    commit: boolean,
  ): void {
    const nextText = toTextAnnotation(action);
    const prevText = toTextAnnotation(previous);
    if (nextText && prevText) {
      textAnnotations.move(nextText, prevText, commit);
    }
  }

  function openTextAnnotationEditor(
    existing: DoodleAnnotation | null,
    placement: DoodleAnnotation | { x: number; y: number } | null = existing,
  ): void {
    const existingText = existing ? toTextAnnotation(existing) : null;
    const point =
      placement && typeof placement.x === 'number' && typeof placement.y === 'number'
        ? { x: placement.x, y: placement.y }
        : null;
    textAnnotations.open(existingText, point);
  }

  function removeTextAnnotation(action: DoodleAnnotation | readonly DoodleAnnotation[] | null): void {
    const list = Array.isArray(action) ? action : action ? [action] : [];
    const removedItems: TextAnnotationItem[] = [];
    for (const item of list) {
      const annotationId = item?.id;
      if (!annotationId) continue;
      const index = doodleActions.findIndex(
        (existing) => existing.type === 'text' && existing.id === annotationId,
      );
      if (index >= 0) {
        const [removed] = doodleActions.splice(index, 1);
        const textItem = toTextAnnotationItem(removed);
        if (textItem) {
          removedItems.push(textItem);
        }
        sendOrQueueDoodleMessage({ type: 'remove_text', id: annotationId });
      }
    }
    if (removedItems.length > 0) {
      if (activeDeleteTransaction) {
        activeDeleteTransaction.texts.push(...removedItems);
      } else {
        pushUndoAction(new DeleteTextDoodleAction(removedItems));
      }
    }
    redrawAllDoodles();
  }

  function endDrawingStroke(): void {
    if (!isDrawing && !currentStrokeId) return;
    const strokeId = currentStrokeId;
    isDrawing = false;
    currentStrokeId = null;
    flushDrawBatch();
    if (strokeId) {
      const strokeSegments = doodleActions
        .filter((a) => a.type === 'draw' && a.stroke_id === strokeId)
        .map(toDrawSegmentItem)
        .filter((seg): seg is DrawSegmentItem => seg !== null);
      if (strokeSegments.length > 0) {
        pushUndoAction(new DrawStrokeDoodleAction(strokeId, strokeSegments));
      }
    }
  }

  const handleDoodlesToggleClick = (): void => {
    if (!isOrator()) return;
    const newEnabled = !doodlesEnabled;
    setDoodlesDisplay(newEnabled);
    sendOrQueueDoodleMessage({ type: 'toggle_doodles', enabled: newEnabled });
  };

  const handleBrushSizeInput = (e: Event): void => {
    const input = e.target as HTMLInputElement;
    currentBrushSize = parseInt(input.value, 10);
  };

  const handleDocumentClick = (event: MouseEvent): void => {
    const target = event.target as Node | null;
    const clickedCanvas = Boolean(target && imgContainer.contains(target));
    const clickedDrawingControls = Boolean(target && doodleToolbar?.contains(target));
    if (!clickedCanvas && !clickedDrawingControls && (currentColor !== null || textAnnotationMode)) {
      setDrawingColor(null);
    }
    pagingControls?.classList.toggle(
      'canvas-selected',
      clickedCanvas && canvasCursorState.current.type === 'select',
    );
    if (textAnnotationMode) {
      pagingControls?.classList.remove('canvas-selected');
    }
  };

  const handleDocumentContextMenu = (event: MouseEvent): void => {
    const target = event.target as Element | null;
    if (!target) return;
    const clickedCanvas = imgContainer === target || imgContainer.contains(target);
    if (!clickedCanvas) return;
    if (
      target.closest(
        'button, a, select, [role="dialog"], #orator-command-overlay, #doodle-toolbar, #history-paging-controls',
      )
    ) {
      return;
    }
    if (isOrator() && isActionWheelRightClickBound?.()) {
      return;
    }
    event.preventDefault();
    cycleCanvasCursor();
  };

  const handleTextAnnotationToggleClick = (): void => {
    if (!canAddTextAnnotation()) return;
    setTextAnnotationMode(!textAnnotationMode, !textAnnotationMode);
  };

  const handleTextAnnotationSizeInput = (): void => {
    if (textAnnotationSizeValue && textAnnotationSize) {
      textAnnotationSizeValue.textContent = `${textAnnotationSize.value} px`;
    }
  };

  const handlePaletteToggleClick = (): void => {
    if (!paletteToggle) return;
    setPaletteOpen(paletteToggle.getAttribute('aria-expanded') !== 'true');
  };

  const handleDocumentPointerDown = (event: PointerEvent): void => {
    const target = event.target as Element | null;
    if (!target) return;
    if (paletteMenu && !paletteMenu.hidden && !target.closest('.doodle-palette')) {
      setPaletteOpen(false);
    }
    if (textAnnotationMenu && !textAnnotationMenu.hidden && !target.closest('.text-annotation-tool')) {
      setTextAnnotationMenuOpen(false);
    }
  };

  const handleDocumentKeyDownBubble = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && paletteMenu && !paletteMenu.hidden) {
      setPaletteOpen(false);
      paletteToggle?.focus();
    } else if (event.key === 'Escape' && textAnnotationMenu && !textAnnotationMenu.hidden) {
      setTextAnnotationMenuOpen(false);
      textAnnotationToggle?.focus();
    }
  };

  const handleDocumentDeleteKeyDownCapture = (event: KeyboardEvent): void => {
    if (event.key === 'Delete' || event.key === 'Backspace') {
      if (isEditableElement(document.activeElement)) return;
      activeDeleteTransaction = {
        type: 'compound_delete',
        segments: [],
        stamps: [],
        texts: [],
      };
      queueMicrotask(() => {
        if (activeDeleteTransaction) {
          const tx = activeDeleteTransaction;
          activeDeleteTransaction = null;
          if (tx.segments.length > 0 || tx.stamps.length > 0 || tx.texts.length > 0) {
            pushUndoAction(new CompoundDeleteDoodleAction(tx.segments, tx.stamps, tx.texts));
          }
        }
      });
    }
  };

  const handleWindowUndoKeyDownCapture = (event: KeyboardEvent): void => {
    const isZ = event.key === 'z' || event.key === 'Z' || event.code === 'KeyZ';
    if ((event.ctrlKey || event.metaKey) && isZ && !event.shiftKey && !event.altKey) {
      if (isEditableElement(document.activeElement)) return;
      if (!doodlesEnabled || isPagedBack()) return;
      event.preventDefault();
      undoLastDoodleAction();
    }
  };

  const handleCanvasClick = (event: MouseEvent): void => {
    if (!textAnnotationMode || !canAddTextAnnotation() || event.target !== canvas) return;
    event.preventDefault();
    openTextAnnotationEditor(null, doodleRenderer.normalizedPoint(event));
  };

  const handleCanvasMouseDown = (e: MouseEvent): void => {
    if (e.button !== 0) return;
    if (!doodlesEnabled || !currentColor) return;
    isDrawing = true;
    currentStrokeId = generateClientId('stroke');
    const coords = doodleRenderer.normalizedPoint(e);
    lastNormX = coords.x;
    lastNormY = coords.y;
  };

  const handleCanvasMouseMove = (e: MouseEvent): void => {
    if (!isDrawing || !doodlesEnabled || !currentColor) return;
    const coords = doodleRenderer.normalizedPoint(e);
    drawLine(lastNormX, lastNormY, coords.x, coords.y, currentColor, currentBrushSize, true);
    lastNormX = coords.x;
    lastNormY = coords.y;
  };

  const handleCanvasTouchStart = (e: TouchEvent): void => {
    if (!doodlesEnabled || !currentColor) return;
    e.preventDefault();
    isDrawing = true;
    currentStrokeId = generateClientId('stroke');
    const coords = doodleRenderer.normalizedPoint(e);
    lastNormX = coords.x;
    lastNormY = coords.y;
  };

  const handleCanvasTouchMove = (e: TouchEvent): void => {
    if (!isDrawing || !doodlesEnabled || !currentColor) return;
    e.preventDefault();
    const coords = doodleRenderer.normalizedPoint(e);
    drawLine(lastNormX, lastNormY, coords.x, coords.y, currentColor, currentBrushSize, true);
    lastNormX = coords.x;
    lastNormY = coords.y;
  };

  const handleClearClick = (): void => {
    endDrawingStroke();
    activeTextEditor?.remove();
    activeTextEditor = null;
    for (let index = pendingDoodleMessages.length - 1; index >= 0; index -= 1) {
      if (pendingDoodleMessages[index].page_id === displayedDoodlePageId) {
        pendingDoodleMessages.splice(index, 1);
      }
    }
    for (const [id, message] of inFlightDoodleMessages) {
      if (message.page_id === displayedDoodlePageId) {
        inFlightDoodleMessages.delete(id);
      }
    }
    doodleRenderer.clearSelectedSegments();
    const previous = [...doodleActions];
    if (previous.length > 0) {
      pushUndoAction(new ClearCanvasDoodleAction(previous));
    }
    doodleActions = [];
    redrawAllDoodles();
    sendOrQueueDoodleMessage({ type: 'clear' });
  };

  const handleUndoClick = (): void => {
    if (!doodlesEnabled || isPagedBack()) return;
    undoLastDoodleAction();
  };

  const handleVisibilityChange = (): void => {
    if (isActiveCanvasWindow()) scheduleDoodleReconnect(0);
  };

  const handleWindowFocus = (): void => {
    scheduleDoodleReconnect(0);
  };

  const textColorClickHandlers = new Map<HTMLElement, () => void>();
  for (const button of textAnnotationColors) {
    const handler = (): void => {
      currentTextColor = button.dataset.color || '#ffffff';
      for (const option of textAnnotationColors) {
        const selected = option === button;
        option.classList.toggle('active', selected);
        option.setAttribute('aria-pressed', String(selected));
      }
      if (activeTextEditor) {
        activeTextEditor.style.color = currentTextColor;
      }
    };
    textColorClickHandlers.set(button, handler);
    button.addEventListener('click', handler);
  }

  const colorBtnClickHandlers = new Map<HTMLElement, () => void>();
  for (const btn of colorBtns) {
    const handler = (): void => {
      const nextColor = btn.classList.contains('active') ? null : btn.dataset.color;
      setDrawingColor(nextColor);
      setPaletteOpen(false);
    };
    colorBtnClickHandlers.set(btn, handler);
    btn.addEventListener('click', handler);
  }

  doodlesToggleBtn?.addEventListener('click', handleDoodlesToggleClick);
  brushSizeSlider?.addEventListener('input', handleBrushSizeInput);
  window.addEventListener('resize', resizeDoodleCanvas);
  document.addEventListener('click', handleDocumentClick);
  document.addEventListener('contextmenu', handleDocumentContextMenu);
  textAnnotationToggle?.addEventListener('click', handleTextAnnotationToggleClick);
  textAnnotationSize?.addEventListener('input', handleTextAnnotationSizeInput);
  paletteToggle?.addEventListener('click', handlePaletteToggleClick);
  document.addEventListener('pointerdown', handleDocumentPointerDown);
  document.addEventListener('keydown', handleDocumentKeyDownBubble);
  document.addEventListener('keydown', handleDocumentDeleteKeyDownCapture, true);
  window.addEventListener('keydown', handleWindowUndoKeyDownCapture, true);
  canvas.addEventListener('click', handleCanvasClick);
  canvas.addEventListener('mousedown', handleCanvasMouseDown);
  canvas.addEventListener('mousemove', handleCanvasMouseMove);
  canvas.addEventListener('mouseup', endDrawingStroke);
  canvas.addEventListener('mouseout', endDrawingStroke);
  canvas.addEventListener('touchstart', handleCanvasTouchStart, { passive: false });
  canvas.addEventListener('touchmove', handleCanvasTouchMove, { passive: false });
  canvas.addEventListener('touchend', endDrawingStroke);
  clearBtn?.addEventListener('click', handleClearClick);
  undoBtn?.addEventListener('click', handleUndoClick);
  document.addEventListener('visibilitychange', handleVisibilityChange);
  window.addEventListener('focus', handleWindowFocus);

  const unsubscribeCursor = canvasCursorState.subscribe(applyCanvasCursorState);
  applyCanvasCursorState(canvasCursorState.current);
  connectDoodleSocket(true);
  resizeDoodleCanvas();

  return {
    doodleRenderer,
    doodleUndoManager,
    getDoodleActions: () => doodleActions,
    setDoodleActions: (actions: readonly DoodleCanvasItem[]) => {
      doodleActions = actions.filter(isSessionDoodleAction);
    },
    getDoodleUndoStack: () => doodleUndoStack,
    setDoodleUndoStack: (stack: DoodleAction[]) => {
      doodleUndoStack = Array.isArray(stack) ? stack : [];
      doodleUndoManager.stack = doodleUndoStack;
    },
    getActiveDeleteTransaction: () => activeDeleteTransaction,
    getPendingTextEdits: () => pendingTextEdits,
    isDoodlesEnabled: () => doodlesEnabled,
    setDoodlesDisplay,
    displayPageDoodles,
    requestPageDoodles,
    sendOrQueueDoodleMessage,
    flushDrawBatch,
    redrawAllDoodles,
    resizeDoodleCanvas,
    pushUndoAction,
    undoLastDoodleAction,
    updateUndoButtonState,
    updateTextAnnotationAvailability,
    refreshCanvasToolInteraction,
    cycleCanvasCursor,
    cancelDrawing: () => {
      isDrawing = false;
      flushDrawBatch();
    },
    destroy: () => {
      destroyed = true;
      unsubscribeCursor();
      if (doodleReconnectTimer !== null) {
        clearTimeout(doodleReconnectTimer);
        doodleReconnectTimer = null;
      }
      if (drawBatchFrame !== null) {
        cancelAnimationFrame(drawBatchFrame);
        drawBatchFrame = null;
      }
      if (ws) {
        try {
          ws.close();
        } catch {
          // Ignore close errors during teardown.
        }
        ws = null;
      }
      for (const [button, handler] of textColorClickHandlers) {
        button.removeEventListener('click', handler);
      }
      for (const [btn, handler] of colorBtnClickHandlers) {
        btn.removeEventListener('click', handler);
      }
      doodlesToggleBtn?.removeEventListener('click', handleDoodlesToggleClick);
      brushSizeSlider?.removeEventListener('input', handleBrushSizeInput);
      window.removeEventListener('resize', resizeDoodleCanvas);
      document.removeEventListener('click', handleDocumentClick);
      document.removeEventListener('contextmenu', handleDocumentContextMenu);
      textAnnotationToggle?.removeEventListener('click', handleTextAnnotationToggleClick);
      textAnnotationSize?.removeEventListener('input', handleTextAnnotationSizeInput);
      paletteToggle?.removeEventListener('click', handlePaletteToggleClick);
      document.removeEventListener('pointerdown', handleDocumentPointerDown);
      document.removeEventListener('keydown', handleDocumentKeyDownBubble);
      document.removeEventListener('keydown', handleDocumentDeleteKeyDownCapture, true);
      window.removeEventListener('keydown', handleWindowUndoKeyDownCapture, true);
      canvas.removeEventListener('click', handleCanvasClick);
      canvas.removeEventListener('mousedown', handleCanvasMouseDown);
      canvas.removeEventListener('mousemove', handleCanvasMouseMove);
      canvas.removeEventListener('mouseup', endDrawingStroke);
      canvas.removeEventListener('mouseout', endDrawingStroke);
      canvas.removeEventListener('touchstart', handleCanvasTouchStart);
      canvas.removeEventListener('touchmove', handleCanvasTouchMove);
      canvas.removeEventListener('touchend', endDrawingStroke);
      clearBtn?.removeEventListener('click', handleClearClick);
      undoBtn?.removeEventListener('click', handleUndoClick);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    },
  };
}

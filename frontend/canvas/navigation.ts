import { canvasCursorState, type CanvasCursorState } from './cursor-state.js';

interface PointerPoint {
  x: number;
  y: number;
}

export interface CanvasNavigationOptions {
  container?: HTMLElement;
  toggle?: HTMLElement;
  surface?: HTMLElement;
}

export interface CanvasNavigationController {
  reset(): void;
  destroy(): void;
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 5;
const WHEEL_ZOOM_SENSITIVITY = 0.0015;
const LINE_HEIGHT_PX = 16;

function midpoint(points: readonly PointerPoint[]): PointerPoint {
  return {
    x: (points[0].x + points[1].x) / 2,
    y: (points[0].y + points[1].y) / 2,
  };
}

function distance(points: readonly PointerPoint[]): number {
  return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
}

export function initializeCanvasNavigation(
  options: CanvasNavigationOptions = {},
): CanvasNavigationController | null {
  const container = options.container ?? document.getElementById('image-container');
  const toggle = options.toggle ?? document.getElementById('canvas-navigation-toggle');
  const surface = options.surface ?? document.getElementById('canvas-navigation-surface');
  if (!container || !toggle || !surface) {
    return null;
  }

  let scale = 1;
  let x = 0;
  let y = 0;
  const pointers = new Map<number, PointerPoint>();

  function apply(): void {
    container!.style.setProperty('--canvas-view-scale', String(scale));
    container!.style.setProperty('--canvas-view-x', `${x}px`);
    container!.style.setProperty('--canvas-view-y', `${y}px`);
  }

  function reset(): void {
    scale = 1;
    x = 0;
    y = 0;
    apply();
  }

  function zoomAt(nextScale: number, clientX: number, clientY: number): void {
    const clampedScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, nextScale));
    const rect = surface!.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const ratio = clampedScale / scale;
    x = px - (px - x) * ratio;
    y = py - (py - y) * ratio;
    scale = clampedScale;
    apply();
  }

  function updateCursor(state: CanvasCursorState): void {
    const enabled = state.type === 'navigate';
    for (const pointerId of pointers.keys()) {
      if (surface!.hasPointerCapture(pointerId)) {
        surface!.releasePointerCapture(pointerId);
      }
    }
    pointers.clear();
    surface!.classList.remove('dragging');
    container!.classList.toggle('canvas-navigation-enabled', enabled);
    toggle!.setAttribute('aria-pressed', String(enabled));
    toggle!.title = enabled
      ? 'Switch to selection · Scroll to zoom, drag to pan, double-click to reset'
      : 'Enable zoom and pan · Double-click to reset view';
  }

  const onToggleClick = (): void => {
    canvasCursorState.select({
      type: canvasCursorState.current.type === 'navigate' ? 'select' : 'navigate',
    });
  };
  toggle.addEventListener('click', onToggleClick);
  const unsubscribe = canvasCursorState.subscribe(updateCursor);
  updateCursor(canvasCursorState.current);

  const onWheel = (event: WheelEvent): void => {
    event.preventDefault();
    const multiplier =
      event.deltaMode === 1 ? LINE_HEIGHT_PX : event.deltaMode === 2 ? surface.clientHeight : 1;
    const delta = event.deltaY * multiplier;
    zoomAt(scale * Math.exp(-delta * WHEEL_ZOOM_SENSITIVITY), event.clientX, event.clientY);
  };
  surface.addEventListener('wheel', onWheel, { passive: false });

  const onPointerDown = (event: PointerEvent): void => {
    if (event.button !== 0) {
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    surface.setPointerCapture(event.pointerId);
    surface.classList.add('dragging');
  };
  surface.addEventListener('pointerdown', onPointerDown);

  const onPointerMove = (event: PointerEvent): void => {
    event.stopPropagation();
    const previous = pointers.get(event.pointerId);
    if (!previous) {
      return;
    }
    const before = [...pointers.values()];
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const after = [...pointers.values()];
    if (after.length === 2) {
      const oldCenter = midpoint(before);
      const center = midpoint(after);
      const beforeDistance = distance(before);
      if (beforeDistance > 0) {
        zoomAt((scale * distance(after)) / beforeDistance, oldCenter.x, oldCenter.y);
      }
      x += center.x - oldCenter.x;
      y += center.y - oldCenter.y;
    } else if (after.length === 1) {
      x += event.clientX - previous.x;
      y += event.clientY - previous.y;
    }
    apply();
  };
  surface.addEventListener('pointermove', onPointerMove);

  const onPointerEnd = (event: PointerEvent): void => {
    event.stopPropagation();
    pointers.delete(event.pointerId);
    if (!pointers.size) {
      surface.classList.remove('dragging');
    }
  };
  const pointerEndTypes = ['pointerup', 'pointercancel', 'lostpointercapture'] as const;
  for (const type of pointerEndTypes) {
    surface.addEventListener(type, onPointerEnd);
  }

  const suppressMouseEvent = (event: MouseEvent): void => {
    event.preventDefault();
    event.stopPropagation();
  };
  const mouseSuppressTypes = ['mousedown', 'mouseup', 'click'] as const;
  for (const type of mouseSuppressTypes) {
    surface.addEventListener(type, suppressMouseEvent);
  }

  const onWindowBlur = (): void => {
    pointers.clear();
    surface.classList.remove('dragging');
  };
  window.addEventListener('blur', onWindowBlur);

  const onDblClick = (event: MouseEvent): void => {
    const target = event.target;
    if (
      target !== container &&
      (!(target instanceof Element) ||
        !target.closest(
          '#canvas-navigation-toggle, #canvas-navigation-surface, #doodle-canvas, #canvas-renderer',
        ))
    ) {
      return;
    }
    reset();
  };
  container.addEventListener('dblclick', onDblClick);

  return {
    reset,
    destroy(): void {
      unsubscribe();
      toggle.removeEventListener('click', onToggleClick);
      surface.removeEventListener('wheel', onWheel);
      surface.removeEventListener('pointerdown', onPointerDown);
      surface.removeEventListener('pointermove', onPointerMove);
      for (const type of pointerEndTypes) {
        surface.removeEventListener(type, onPointerEnd);
      }
      for (const type of mouseSuppressTypes) {
        surface.removeEventListener(type, suppressMouseEvent);
      }
      window.removeEventListener('blur', onWindowBlur);
      container.removeEventListener('dblclick', onDblClick);
    },
  };
}

initializeCanvasNavigation();

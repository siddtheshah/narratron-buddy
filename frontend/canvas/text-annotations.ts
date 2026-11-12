import {
  EditTextDoodleAction,
  MoveTextDoodleAction,
  PlaceTextDoodleAction,
  type DoodleAction,
  type TextAnnotationItem,
} from './doodle-actions.js';
import type { ImagePlane, Point } from './doodle-types.js';

export interface TextAnnotation extends TextAnnotationItem {
  type: 'text';
  id: string;
  client_message_id?: string;
}

export interface TextAnnotationsOptions {
  canvas: HTMLCanvasElement;
  container: HTMLElement;
  canEdit(): boolean;
  isPagedBack(): boolean;
  stopSpeech(): void;
  getActiveEditor(): HTMLTextAreaElement | null;
  setActiveEditor(editor: HTMLTextAreaElement | null): void;
  getDefaults(): { color: string; font: string; size: number };
  getImagePlane(existing: TextAnnotation | null): ImagePlane | null;
  send(action: TextAnnotation): void;
  rememberEdit(action: TextAnnotation, previous: TextAnnotation): void;
  apply(action: TextAnnotation): void;
  pushUndo(action: DoodleAction): void;
}

export interface TextAnnotationsController {
  open(existing: TextAnnotation | null, placement?: Point | null): void;
  move(action: TextAnnotation, previous: TextAnnotation, commit: boolean): void;
}

export function createTextAnnotations(options: TextAnnotationsOptions): TextAnnotationsController {
  function move(action: TextAnnotation, previous: TextAnnotation, commit: boolean): void {
    if (commit) {
      action = { ...action };
      delete action.client_message_id;
      options.send(action);
      options.rememberEdit(action, previous);
      if (previous.x !== action.x || previous.y !== action.y) {
        options.pushUndo(new MoveTextDoodleAction(previous, action));
      }
    }
    options.apply(action);
  }

  function open(existing: TextAnnotation | null, placement: Point | null = existing): void {
    if (!options.canEdit() || options.isPagedBack() || !placement) return;
    options.stopSpeech();
    options.getActiveEditor()?.remove();

    const coords = placement;
    const editor = document.createElement('textarea');
    editor.rows = 1;
    editor.value = existing?.text || '';
    editor.maxLength = 240;
    editor.className = 'canvas-text-editor';
    editor.setAttribute('aria-label', 'Text annotation');
    editor.placeholder = 'Type annotation…';
    const plane = options.getImagePlane(existing);
    const defaults = options.getDefaults();
    editor.style.left = plane ? `${plane.x + coords.x * plane.width}px` : `${coords.x * 100}%`;
    editor.style.top = plane ? `${plane.y + coords.y * plane.height}px` : `${coords.y * 100}%`;
    editor.style.fontFamily = `"${existing?.font || defaults.font}", sans-serif`;
    const editingSize = Number(existing?.size || defaults.size);
    editor.style.fontSize = `${plane ? editingSize * plane.width / 1000 : editingSize}px`;
    editor.style.color = existing?.color || defaults.color;
    options.container.appendChild(editor);
    options.setActiveEditor(editor);

    let finished = false;
    const finish = (commit: boolean): void => {
      if (finished) return;
      finished = true;
      const text = editor.value.trim();
      if (commit && text && options.canEdit()) {
        // Defaults may change while the editor is open (for example its color).
        const defaults = options.getDefaults();
        const action: TextAnnotation = {
          id: existing?.id || ((typeof crypto !== 'undefined' && crypto.randomUUID)
            ? crypto.randomUUID() : ('text-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7))),
          type: 'text', x: coords.x, y: coords.y, text,
          color: existing?.color || defaults.color,
          size: Number(existing?.size || defaults.size),
          font: existing?.font || defaults.font,
        };
        options.send(action);
        if (existing) options.rememberEdit(action, existing);
        options.apply(action);
        options.pushUndo(existing
          ? new EditTextDoodleAction(existing, action)
          : new PlaceTextDoodleAction(action));
      }
      editor.remove();
      if (options.getActiveEditor() === editor) options.setActiveEditor(null);
    };
    editor.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        finish(true);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        finish(false);
        options.canvas.focus();
      }
    });
    editor.addEventListener('blur', () => finish(true), { once: true });
    editor.focus();
    editor.select();
  }

  return { open, move };
}

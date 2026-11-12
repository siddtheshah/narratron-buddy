/** Persisted drawing/annotation fields consumed by the canvas renderer. */
export interface DoodleAnnotation {
  type: 'draw' | 'text' | 'stamp';
  id?: string;
  stroke_id?: string;
  selected?: boolean;
  x?: number;
  y?: number;
  x0?: number;
  y0?: number;
  x1?: number;
  y1?: number;
  size?: number;
  color?: string;
  text?: string;
  font?: string;
  url?: string;
  name?: string;
  rotation?: number;
  draggable?: boolean;
  canvas_width?: number;
  canvas_height?: number;
  canvas_size?: number;
  canvas_page_width?: number;
  canvas_page_height?: number;
  canvas_font_size?: number;
  canvas_line_width?: number;
}

export interface AnnotationNode extends HTMLElement {
  annotation: DoodleAnnotation;
}

export interface Point { x: number; y: number }
export interface ImagePlane extends Point { width: number; height: number }
export interface ScreenPlane { left: number; top: number; width: number; height: number }
export type SegmentTarget = string | DoodleAnnotation | (string | DoodleAnnotation)[] | null;
export type TextTarget = string | DoodleAnnotation | AnnotationNode;

export interface DoodleRendererOptions {
  canvas: HTMLCanvasElement;
  isVisible?: () => boolean;
  textLayer?: HTMLElement | null;
  canEditText?: () => boolean;
  onEditText?: (action: DoodleAnnotation) => void;
  onMoveText?: (action: DoodleAnnotation, previous: DoodleAnnotation, commit: boolean) => void;
  onRemoveText?: (action: DoodleAnnotation) => void;
  onSelectText?: (action: DoodleAnnotation) => void;
  stampLayer?: HTMLElement | null;
  canMoveStamp?: () => boolean;
  onMoveStamp?: (action: DoodleAnnotation, previous: DoodleAnnotation, commit: boolean) => void;
  onRemoveStamp?: (action: DoodleAnnotation) => void;
  onSelectStamp?: (action: DoodleAnnotation) => void;
  canSelectSegment?: () => boolean;
  onSelectSegment?: (actions: DoodleAnnotation[]) => void;
  onRemoveSegment?: (actions: DoodleAnnotation[]) => void;
  getPage?: () => { width: number; height: number } | null;
  fitMode?: 'contain' | 'cover';
}

export interface MoveDrag {
  original: DoodleAnnotation;
  x: number;
  y: number;
  moved: boolean;
}
export interface BoxDrag {
  startClientX: number;
  startClientY: number;
  startX: number;
  startY: number;
  isMulti: boolean;
  active: boolean;
  pointerId: number;
}

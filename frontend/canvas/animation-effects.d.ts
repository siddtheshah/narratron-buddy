// Typed boundary for the legacy browser effect module.

export interface ImageCentroid { cx: number; cy: number; maxRadiusRatio: number }
export interface MeshVertex { x: number; y: number; u: number; v: number }
export function layerTransform(effect: string, seconds: number): { x: number; y: number; rotation: number; scale: number };
export function layerOpacity(effect: string, seconds: number, baseOpacity?: number): number;
export function layerHaloParams(effect: string, seconds: number, amplitude?: number, opacity?: number): {
  isLight: boolean; haloScale: number; haloAlpha: number; haloFilter: string;
} | null;
export function layerPieceFilter(effect: string, seconds?: number): string;
export function isMeshDistortionEffect(effect: string): boolean;
export function calculateMeshGrid(width: number, height: number, phase: number, amplitude?: number,
  centroid?: ImageCentroid | null, mode?: string, gridDivs?: number): MeshVertex[][];
export function layerReflectiveDraw(effect: string, seconds: number, context: CanvasRenderingContext2D,
  image: HTMLImageElement, width: number, height: number): boolean;
export function layerMirageDraw(effect: string, seconds: number, context: CanvasRenderingContext2D,
  image: HTMLImageElement, width: number, height: number): boolean;
export function layerEnergyBlastDraw(effect: string, seconds: number, context: CanvasRenderingContext2D,
  image: HTMLImageElement, width: number, height: number): boolean;

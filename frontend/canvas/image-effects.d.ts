// Typed boundary for the legacy browser effect module.
export interface ImageEffectController {
  element: HTMLElement;
  destroy(): void;
  setEffect(effect: string): void;
  setIntensity(intensity: number): void;
}
export const IMAGE_EFFECTS: Readonly<Record<string, { label: string; classes: readonly string[] }>>;
export const IMAGE_EFFECT_DEFAULT_INTENSITIES: Readonly<Record<string, number>>;
export function attachImageEffect(image: HTMLImageElement, options: { effect: string; intensity: number }): ImageEffectController;

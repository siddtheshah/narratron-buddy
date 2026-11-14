/** Layered Animation Lab page controller: preset/piece catalog, layer stack UI, and canvas loop. */

import {
  calculateMeshGrid,
  isMeshDistortionEffect,
  layerEnergyBlastDraw,
  layerFlameRippleDraw,
  layerHaloParams,
  layerOpacity,
  layerPieceFilter,
  layerReflectiveDraw,
  layerTransform,
  type ImageCentroid,
  type MeshVertex,
} from '../shared/animation-effects.js';

export interface PieceEntry {
  readonly id?: string;
  readonly url: string;
  readonly filename: string;
}

export interface EffectEntry {
  readonly id: string;
  readonly name?: string;
}

export interface PresetLayerEntry {
  readonly name: string;
  readonly url: string;
  readonly filename: string;
  readonly effect: string;
  readonly speed: number;
  readonly amplitude: number;
  readonly opacity: number;
}

export interface PresetEntry {
  readonly id: string;
  readonly name: string;
  readonly layers: readonly PresetLayerEntry[];
}

export interface LayeredAnimationCatalog {
  readonly pieces: readonly PieceEntry[];
  readonly effects: readonly EffectEntry[];
  readonly presets: readonly PresetEntry[];
}

export interface LayerPropsInput {
  readonly name?: string;
  readonly url?: string;
  readonly filename?: string;
  readonly effect?: string;
  readonly speed?: number;
  readonly amplitude?: number;
  readonly opacity?: number;
  readonly xOffset?: number;
  readonly yOffset?: number;
  readonly scale?: number;
  readonly rotation?: number;
  readonly order?: number;
}

interface CachedLayerImage extends HTMLImageElement {
  _dominantHueKey?: string;
  _dominantHue?: number;
  _massMomentsKey?: string;
  _massMoments?: unknown;
  _centroidKey?: string;
  _centroid?: ImageCentroid;
}

export interface AnimationLayer {
  readonly id: string;
  name: string;
  url: string;
  filename: string;
  effect: string;
  speed: number;
  amplitude: number;
  opacity: number;
  xOffset: number;
  yOffset: number;
  scale: number;
  rotation: number;
  visible: boolean;
  imgElement: CachedLayerImage;
  loaded: boolean;
}

export interface LayeredAnimationLabController {
  initCatalog(): Promise<void>;
  addLayer(initialProps?: LayerPropsInput): void;
  loadPreset(presetId: string): void;
  exportJsonManifest(): void;
  importJsonManifest(): void;
  destroy(): void;
}

const FALLBACK_EFFECTS: readonly string[] = [
  'none',
  'sway',
  'gentle_rocking',
  'vibrate',
  'pulse',
  'twist',
  'bend',
  'light_halo',
  'dark_halo',
  'ghostly',
  'reflective',
  'mirage',
  'energy_blast',
];

/** Computes the opaque-pixel bounding centroid and radius ratio for a layer image. */
export function computeImageCentroid(sourceImage: CachedLayerImage | null): ImageCentroid {
  const key = sourceImage?.currentSrc || sourceImage?.src || '';
  if (sourceImage && sourceImage._centroidKey === key && sourceImage._centroid) {
    return sourceImage._centroid;
  }
  if (!sourceImage || !sourceImage.naturalWidth) {
    return { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
  }
  const cacheFallback = (): ImageCentroid => {
    const fallbackCentroid: ImageCentroid = { cx: 0.5, cy: 0.5, maxRadiusRatio: 0.707 };
    sourceImage._centroidKey = key;
    sourceImage._centroid = fallbackCentroid;
    return fallbackCentroid;
  };
  try {
    const tempCanvas = document.createElement('canvas');
    const w = Math.min(256, sourceImage.naturalWidth);
    const h = Math.max(1, Math.round((w * sourceImage.naturalHeight) / sourceImage.naturalWidth));
    tempCanvas.width = w;
    tempCanvas.height = h;
    const tempCtx = tempCanvas.getContext('2d', { willReadFrequently: true });
    if (!tempCtx) {
      return cacheFallback();
    }
    tempCtx.drawImage(sourceImage, 0, 0, w, h);
    const imgData = tempCtx.getImageData(0, 0, w, h).data;
    let minX = w;
    let maxX = 0;
    let minY = h;
    let maxY = 0;
    let count = 0;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (imgData[(y * w + x) * 4 + 3] > 15) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
          count++;
        }
      }
    }
    if (count === 0) {
      return cacheFallback();
    }
    const bboxCx = (minX + maxX) / 2 / w;
    const bboxCy = (minY + maxY) / 2 / h;
    const halfW = (maxX - minX) / 2 / w;
    const halfH = (maxY - minY) / 2 / h;
    const calculatedCentroid: ImageCentroid = {
      cx: bboxCx,
      cy: bboxCy,
      maxRadiusRatio: Math.max(0.1, Math.hypot(halfW, halfH)),
    };
    sourceImage._centroidKey = key;
    sourceImage._centroid = calculatedCentroid;
    return calculatedCentroid;
  } catch {
    return cacheFallback();
  }
}

function drawMeshDistortion(
  context: CanvasRenderingContext2D,
  sourceImage: HTMLImageElement,
  drawWidth: number,
  drawHeight: number,
  phase: number,
  amplitude = 1.0,
  centroid: ImageCentroid | null = null,
  mode = 'twist',
): void {
  const vertices = calculateMeshGrid(drawWidth, drawHeight, phase, amplitude, centroid, mode);
  const grid = vertices.length - 1;
  const drawTriangle = (p0: MeshVertex, p1: MeshVertex, p2: MeshVertex): void => {
    const sx0 = p0.u * sourceImage.naturalWidth;
    const sy0 = p0.v * sourceImage.naturalHeight;
    const sx1 = p1.u * sourceImage.naturalWidth;
    const sy1 = p1.v * sourceImage.naturalHeight;
    const sx2 = p2.u * sourceImage.naturalWidth;
    const sy2 = p2.v * sourceImage.naturalHeight;
    context.save();
    context.beginPath();
    context.moveTo(p0.x, p0.y);
    context.lineTo(p1.x, p1.y);
    context.lineTo(p2.x, p2.y);
    context.closePath();
    context.clip();
    const denom = sx0 * (sy1 - sy2) + sx1 * (sy2 - sy0) + sx2 * (sy0 - sy1);
    if (Math.abs(denom) > 1e-5) {
      const m11 = (p0.x * (sy1 - sy2) + p1.x * (sy2 - sy0) + p2.x * (sy0 - sy1)) / denom;
      const m12 = (p0.y * (sy1 - sy2) + p1.y * (sy2 - sy0) + p2.y * (sy0 - sy1)) / denom;
      const m21 = (p0.x * (sx2 - sx1) + p1.x * (sx0 - sx2) + p2.x * (sx1 - sx0)) / denom;
      const m22 = (p0.y * (sx2 - sx1) + p1.y * (sx0 - sx2) + p2.y * (sx1 - sx0)) / denom;
      const dx =
        (p0.x * (sx1 * sy2 - sx2 * sy1) +
          p1.x * (sx2 * sy0 - sx0 * sy2) +
          p2.x * (sx0 * sy1 - sx1 * sy0)) /
        denom;
      const dy =
        (p0.y * (sx1 * sy2 - sx2 * sy1) +
          p1.y * (sx2 * sy0 - sx0 * sy2) +
          p2.y * (sx0 * sy1 - sx1 * sy0)) /
        denom;
      context.transform(m11, m12, m21, m22, dx, dy);
      context.drawImage(sourceImage, 0, 0);
    }
    context.restore();
  };
  for (let j = 0; j < grid; j++) {
    for (let i = 0; i < grid; i++) {
      drawTriangle(vertices[j][i], vertices[j][i + 1], vertices[j + 1][i]);
      drawTriangle(vertices[j][i + 1], vertices[j + 1][i + 1], vertices[j + 1][i]);
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** Wires the Layered Animation Lab DOM controls, layer stack, and canvas render loop. */
export function initLayeredAnimationLab(): LayeredAnimationLabController | null {
  const rawCanvas = document.getElementById('stage-canvas');
  const rawStageViewport = document.getElementById('stage-viewport');
  const rawLayerList = document.getElementById('layer-list');
  const rawPresetSelect = document.getElementById('preset-select');
  const rawBgSelect = document.getElementById('bg-select');
  const rawAspectSelect = document.getElementById('aspect-select');
  const rawBtnPlayPause = document.getElementById('btn-play-pause');
  const rawMasterSpeedInput = document.getElementById('master-speed');
  const rawMasterSpeedVal = document.getElementById('master-speed-val');
  const rawFpsCounter = document.getElementById('fps-counter');
  const rawLayerCountStatus = document.getElementById('layer-count-status');
  const rawCanvasDimStatus = document.getElementById('canvas-dim-status');
  const rawBtnAddLayer = document.getElementById('btn-add-layer');
  const rawBtnClearAll = document.getElementById('btn-clear-all');
  const rawBtnExportJson = document.getElementById('btn-export-json');
  const rawBtnImportJson = document.getElementById('btn-import-json');
  const rawJsonIo = document.getElementById('json-io');

  if (
    !(rawCanvas instanceof HTMLCanvasElement) ||
    !(rawStageViewport instanceof HTMLElement) ||
    !(rawLayerList instanceof HTMLElement) ||
    !(rawPresetSelect instanceof HTMLSelectElement) ||
    !(rawBgSelect instanceof HTMLSelectElement) ||
    !(rawAspectSelect instanceof HTMLSelectElement) ||
    !(rawBtnPlayPause instanceof HTMLButtonElement) ||
    !(rawMasterSpeedInput instanceof HTMLInputElement) ||
    !(rawMasterSpeedVal instanceof HTMLElement) ||
    !(rawFpsCounter instanceof HTMLElement) ||
    !(rawLayerCountStatus instanceof HTMLElement) ||
    !(rawCanvasDimStatus instanceof HTMLElement) ||
    !(rawBtnAddLayer instanceof HTMLButtonElement) ||
    !(rawBtnClearAll instanceof HTMLButtonElement) ||
    !(rawBtnExportJson instanceof HTMLButtonElement) ||
    !(rawBtnImportJson instanceof HTMLButtonElement) ||
    !(rawJsonIo instanceof HTMLTextAreaElement)
  ) {
    return null;
  }

  const context2d = rawCanvas.getContext('2d');
  if (!context2d) {
    return null;
  }

  const canvas = rawCanvas;
  const ctx = context2d;
  const stageViewport = rawStageViewport;
  const layerList = rawLayerList;
  const presetSelect = rawPresetSelect;
  const bgSelect = rawBgSelect;
  const aspectSelect = rawAspectSelect;
  const btnPlayPause = rawBtnPlayPause;
  const masterSpeedInput = rawMasterSpeedInput;
  const masterSpeedVal = rawMasterSpeedVal;
  const fpsCounter = rawFpsCounter;
  const layerCountStatus = rawLayerCountStatus;
  const canvasDimStatus = rawCanvasDimStatus;
  const btnAddLayer = rawBtnAddLayer;
  const btnClearAll = rawBtnClearAll;
  const btnExportJson = rawBtnExportJson;
  const btnImportJson = rawBtnImportJson;
  const jsonIo = rawJsonIo;

  let catalog: LayeredAnimationCatalog = { pieces: [], effects: [], presets: [] };
  let layers: AnimationLayer[] = [];
  let isPlaying = true;
  let masterSpeed = 1.0;
  let animFrameId: number | null = null;
  let destroyed = false;
  let frameCount = 0;
  let fpsTimer = performance.now();

  function updateStatus(): void {
    layerCountStatus.textContent = `${layers.length} Layer${layers.length === 1 ? '' : 's'} Active`;
  }

  function exportJsonManifest(): void {
    const manifest = {
      version: 1,
      type: 'layered_animation_manifest',
      layers: layers.map((layer, idx) => ({
        name: layer.name,
        url: layer.url,
        filename: layer.filename,
        effect: layer.effect,
        speed: layer.speed,
        amplitude: layer.amplitude,
        opacity: layer.opacity,
        scale: layer.scale,
        order: idx,
      })),
    };
    jsonIo.value = JSON.stringify(manifest, null, 2);
  }

  function buildRangeControl(
    labelId: string,
    labelText: string,
    min: string,
    max: string,
    step: string,
    value: number,
    action: string,
    layerId: string,
  ): HTMLLabelElement {
    const label = document.createElement('label');
    const span = document.createElement('span');
    span.id = labelId;
    span.textContent = labelText;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = min;
    input.max = max;
    input.step = step;
    input.value = String(value);
    input.setAttribute('data-action', action);
    input.setAttribute('data-id', layerId);
    label.append(span, input);
    return label;
  }

  function buildLayerCard(layer: AnimationLayer): HTMLDivElement {
    const card = document.createElement('div');
    card.className = 'layer-card';

    const header = document.createElement('div');
    header.className = 'layer-header';

    const thumb = document.createElement('img');
    thumb.className = 'layer-thumb';
    thumb.src = layer.url;
    thumb.alt = layer.name;

    const titleWrap = document.createElement('div');
    titleWrap.className = 'layer-title';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = layer.name;
    nameInput.setAttribute('data-action', 'rename');
    nameInput.setAttribute('data-id', layer.id);
    titleWrap.append(nameInput);

    const actions = document.createElement('div');
    actions.className = 'layer-actions';

    const actionButtons: ReadonlyArray<{
      readonly action: string;
      readonly title: string;
      readonly text: string;
      readonly className?: string;
    }> = [
      {
        action: 'toggle-vis',
        title: 'Toggle visibility',
        text: layer.visible ? '👁️' : '🙈',
      },
      { action: 'move-up', title: 'Move up stack', text: '▲' },
      { action: 'move-down', title: 'Move down stack', text: '▼' },
      { action: 'delete', title: 'Delete layer', text: 'Delete', className: 'btn-danger' },
    ];
    for (const spec of actionButtons) {
      const btn = document.createElement('button');
      btn.type = 'button';
      if (spec.className) {
        btn.className = spec.className;
      }
      btn.setAttribute('data-action', spec.action);
      btn.setAttribute('data-id', layer.id);
      btn.title = spec.title;
      btn.textContent = spec.text;
      actions.append(btn);
    }

    header.append(thumb, titleWrap, actions);

    const body = document.createElement('div');
    body.className = 'layer-body';

    const pieceLabel = document.createElement('label');
    pieceLabel.textContent = 'Piece Image:';
    const pieceSelect = document.createElement('select');
    pieceSelect.setAttribute('data-action', 'change-image');
    pieceSelect.setAttribute('data-id', layer.id);
    for (const piece of catalog.pieces) {
      const option = document.createElement('option');
      option.value = piece.url;
      option.textContent = piece.filename;
      option.selected = piece.url === layer.url;
      pieceSelect.append(option);
    }
    pieceLabel.append(pieceSelect);

    const effectLabel = document.createElement('label');
    effectLabel.textContent = 'Motion Effect:';
    const effectSelect = document.createElement('select');
    effectSelect.setAttribute('data-action', 'change-effect');
    effectSelect.setAttribute('data-id', layer.id);
    const availableEffects =
      catalog.effects.length > 0 ? catalog.effects.map((e) => e.id) : FALLBACK_EFFECTS;
    for (const ef of availableEffects) {
      const option = document.createElement('option');
      option.value = ef;
      option.textContent = ef.toUpperCase();
      option.selected = ef === layer.effect;
      effectSelect.append(option);
    }
    effectLabel.append(effectSelect);

    body.append(
      pieceLabel,
      effectLabel,
      buildRangeControl(
        `label-speed-${layer.id}`,
        `Speed (${layer.speed}x):`,
        '0.1',
        '3.0',
        '0.1',
        layer.speed,
        'change-speed',
        layer.id,
      ),
      buildRangeControl(
        `label-amp-${layer.id}`,
        `Amplitude (${layer.amplitude}x):`,
        '0.0',
        '3.0',
        '0.1',
        layer.amplitude,
        'change-amp',
        layer.id,
      ),
      buildRangeControl(
        `label-opacity-${layer.id}`,
        `Opacity (${Math.round(layer.opacity * 100)}%):`,
        '0.0',
        '1.0',
        '0.05',
        layer.opacity,
        'change-opacity',
        layer.id,
      ),
      buildRangeControl(
        `label-scale-${layer.id}`,
        `Scale (${layer.scale}x):`,
        '0.2',
        '2.5',
        '0.05',
        layer.scale,
        'change-scale',
        layer.id,
      ),
    );

    card.append(header, body);
    return card;
  }

  function renderLayerList(): void {
    layerList.replaceChildren();
    if (layers.length === 0) {
      const empty = document.createElement('p');
      empty.style.cssText = 'color: var(--subtle); text-align: center; margin: 20px 0;';
      empty.textContent = 'No layers. Click "+ Add Layer" to start.';
      layerList.append(empty);
      return;
    }
    // Render top-to-bottom in UI (Foreground first, Background last)
    const sortedLayers = [...layers].reverse();
    for (const layer of sortedLayers) {
      layerList.append(buildLayerCard(layer));
    }
  }

  function addLayer(initialProps: LayerPropsInput = {}): void {
    const defaultPiece: PieceEntry = catalog.pieces[0] ?? { url: '', filename: '' };
    const newLayer: AnimationLayer = {
      id: 'layer_' + Math.random().toString(36).substring(2, 9),
      name: initialProps.name || defaultPiece.id || `Layer ${layers.length + 1}`,
      url: initialProps.url || defaultPiece.url || '',
      filename: initialProps.filename || defaultPiece.filename || '',
      effect: initialProps.effect || 'none',
      speed: initialProps.speed ?? 1.0,
      amplitude: initialProps.amplitude ?? 1.0,
      opacity: initialProps.opacity ?? 1.0,
      xOffset: initialProps.xOffset ?? 0,
      yOffset: initialProps.yOffset ?? 0,
      scale: initialProps.scale ?? 1.0,
      rotation: initialProps.rotation ?? 0,
      visible: true,
      imgElement: new Image(),
      loaded: false,
    };

    if (newLayer.url) {
      newLayer.imgElement.crossOrigin = 'anonymous';
      newLayer.imgElement.src = newLayer.url;
      newLayer.imgElement.onload = (): void => {
        newLayer.loaded = true;
        renderLayerList();
      };
    }

    layers.push(newLayer);
    renderLayerList();
    updateStatus();
  }

  function populatePresets(): void {
    presetSelect.replaceChildren(new Option('-- Custom Composition --', ''));
    for (const preset of catalog.presets) {
      const opt = document.createElement('option');
      opt.value = preset.id;
      opt.textContent = preset.name;
      presetSelect.append(opt);
    }
  }

  function loadPreset(presetId: string): void {
    const preset = catalog.presets.find((p) => p.id === presetId);
    if (!preset) return;

    layers = [];
    preset.layers.forEach((layerSpec, idx) => {
      addLayer({
        name: layerSpec.name,
        url: layerSpec.url,
        filename: layerSpec.filename,
        effect: layerSpec.effect,
        speed: layerSpec.speed,
        amplitude: layerSpec.amplitude,
        opacity: layerSpec.opacity,
        order: idx,
      });
    });
    presetSelect.value = presetId;
    exportJsonManifest();
  }

  function addDefaultLayerFromPieces(): void {
    if (catalog.pieces.length === 0) return;
    catalog.pieces.forEach((piece, idx) => {
      const effect = idx === 1 ? 'pulse' : idx === 2 ? 'vibrate' : 'none';
      addLayer({
        name: piece.id,
        url: piece.url,
        filename: piece.filename,
        effect,
        speed: 1.0,
        amplitude: 1.0,
        opacity: 1.0,
        order: idx,
      });
    });
  }

  async function initCatalog(): Promise<void> {
    try {
      const res = await fetch('/api/layered-animation/catalog');
      if (res.ok) {
        catalog = (await res.json()) as LayeredAnimationCatalog;
        populatePresets();
        if (catalog.presets.length > 0) {
          loadPreset(catalog.presets[0].id);
        } else if (catalog.pieces.length > 0) {
          addDefaultLayerFromPieces();
        }
      }
    } catch (err: unknown) {
      console.error('Failed to load layered animation catalog:', err);
    }
  }

  function resizeCanvas(): void {
    const rect = stageViewport.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    canvasDimStatus.textContent = `Canvas: ${canvas.width}x${canvas.height}`;
  }

  function renderLoop(time: number): void {
    if (destroyed) return;
    const seconds = time / 1000;

    frameCount++;
    if (time - fpsTimer >= 1000) {
      fpsCounter.textContent = `${frameCount} FPS`;
      frameCount = 0;
      fpsTimer = time;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    ctx.save();
    ctx.scale(dpr, dpr);

    // Render layers bottom-to-top (index 0 background first)
    for (const layer of layers) {
      if (
        !layer.visible ||
        !layer.imgElement.complete ||
        layer.imgElement.naturalWidth === 0
      ) {
        continue;
      }

      const source = layer.imgElement;
      const scaleFit = Math.min(width / source.naturalWidth, height / source.naturalHeight);
      const drawWidth = source.naturalWidth * scaleFit * layer.scale;
      const drawHeight = source.naturalHeight * scaleFit * layer.scale;
      const cx = width / 2 + layer.xOffset;
      const cy = height / 2 + layer.yOffset;

      const effectiveTime = seconds * layer.speed * masterSpeed;
      const rawTransform = layerTransform(layer.effect, effectiveTime);

      const x = rawTransform.x * layer.amplitude;
      const y = rawTransform.y * layer.amplitude;
      const rotation =
        rawTransform.rotation * layer.amplitude + (layer.rotation * Math.PI) / 180;
      const scaleMod = 1 + (rawTransform.scale - 1) * layer.amplitude;

      const halo = layerHaloParams(layer.effect, effectiveTime, layer.amplitude, layer.opacity);
      if (halo) {
        ctx.save();
        ctx.globalAlpha = halo.haloAlpha;
        ctx.translate(cx + x, cy + y);
        ctx.rotate(rotation);
        ctx.scale(scaleMod * halo.haloScale, scaleMod * halo.haloScale);
        ctx.filter = halo.haloFilter;
        ctx.drawImage(source, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
        ctx.restore();
      }

      ctx.save();
      ctx.globalAlpha = layerOpacity(layer.effect, effectiveTime, layer.opacity);
      ctx.translate(cx + x, cy + y);
      ctx.rotate(rotation);
      ctx.scale(scaleMod, scaleMod);
      const pieceFilter = layerPieceFilter(layer.effect, effectiveTime);
      if (pieceFilter !== 'none') {
        ctx.filter = pieceFilter;
      }
      if (isMeshDistortionEffect(layer.effect)) {
        const centroid = computeImageCentroid(source);
        drawMeshDistortion(
          ctx,
          source,
          drawWidth,
          drawHeight,
          effectiveTime,
          layer.amplitude,
          centroid,
          layer.effect,
        );
      } else if (
        !layerEnergyBlastDraw(layer.effect, effectiveTime, ctx, source, drawWidth, drawHeight) &&
        !layerFlameRippleDraw(layer.effect, effectiveTime, ctx, source, drawWidth, drawHeight) &&
        !layerReflectiveDraw(layer.effect, effectiveTime, ctx, source, drawWidth, drawHeight)
      ) {
        ctx.drawImage(source, -drawWidth / 2, -drawHeight / 2, drawWidth, drawHeight);
      }
      ctx.restore();
    }

    ctx.restore();
    animFrameId = requestAnimationFrame(renderLoop);
  }

  function importJsonManifest(): void {
    try {
      const parsed: unknown = JSON.parse(jsonIo.value);
      if (isRecord(parsed) && Array.isArray(parsed.layers)) {
        layers = [];
        parsed.layers.forEach((rawLayer: unknown, idx: number) => {
          const item = isRecord(rawLayer) ? rawLayer : {};
          addLayer({
            name: typeof item.name === 'string' && item.name ? item.name : `Layer ${idx + 1}`,
            url: typeof item.url === 'string' ? item.url : '',
            filename: typeof item.filename === 'string' ? item.filename : '',
            effect: typeof item.effect === 'string' && item.effect ? item.effect : 'none',
            speed: typeof item.speed === 'number' ? item.speed : 1.0,
            amplitude: typeof item.amplitude === 'number' ? item.amplitude : 1.0,
            opacity: typeof item.opacity === 'number' ? item.opacity : 1.0,
            scale: typeof item.scale === 'number' ? item.scale : 1.0,
            order: idx,
          });
        });
        renderLayerList();
      }
    } catch {
      window.alert('Invalid JSON manifest format.');
    }
  }

  const onLayerListInput = (event: Event): void => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const id = target.getAttribute('data-id');
    const action = target.getAttribute('data-action');
    const layer = layers.find((item) => item.id === id);
    if (!layer || !id) return;

    if (action === 'rename') {
      layer.name = target.value;
    } else if (action === 'change-speed') {
      layer.speed = parseFloat(target.value);
      const lbl = document.getElementById(`label-speed-${id}`);
      if (lbl) lbl.textContent = `Speed (${layer.speed.toFixed(1)}x):`;
    } else if (action === 'change-amp') {
      layer.amplitude = parseFloat(target.value);
      const lbl = document.getElementById(`label-amp-${id}`);
      if (lbl) lbl.textContent = `Amplitude (${layer.amplitude.toFixed(1)}x):`;
    } else if (action === 'change-opacity') {
      layer.opacity = parseFloat(target.value);
      const lbl = document.getElementById(`label-opacity-${id}`);
      if (lbl) lbl.textContent = `Opacity (${Math.round(layer.opacity * 100)}%):`;
    } else if (action === 'change-scale') {
      layer.scale = parseFloat(target.value);
      const lbl = document.getElementById(`label-scale-${id}`);
      if (lbl) lbl.textContent = `Scale (${layer.scale.toFixed(2)}x):`;
    }
  };
  layerList.addEventListener('input', onLayerListInput);

  const onLayerListChange = (event: Event): void => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    const id = target.getAttribute('data-id');
    const action = target.getAttribute('data-action');
    const layer = layers.find((item) => item.id === id);
    if (!layer) return;

    if (action === 'change-image') {
      layer.url = target.value;
      delete layer.imgElement._dominantHue;
      delete layer.imgElement._dominantHueKey;
      delete layer.imgElement._massMoments;
      delete layer.imgElement._massMomentsKey;
      delete layer.imgElement._centroid;
      delete layer.imgElement._centroidKey;
      layer.imgElement.src = layer.url;
      const matched = catalog.pieces.find((p) => p.url === layer.url);
      if (matched) layer.filename = matched.filename;
      renderLayerList();
    } else if (action === 'change-effect') {
      layer.effect = target.value;
    }
  };
  layerList.addEventListener('change', onLayerListChange);

  const onLayerListClick = (event: Event): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const btn = target.closest('button');
    if (!btn) return;
    const id = btn.getAttribute('data-id');
    const action = btn.getAttribute('data-action');
    const index = layers.findIndex((item) => item.id === id);
    if (index === -1) return;

    if (action === 'toggle-vis') {
      layers[index].visible = !layers[index].visible;
      renderLayerList();
    } else if (action === 'delete') {
      layers.splice(index, 1);
      renderLayerList();
      updateStatus();
      exportJsonManifest();
    } else if (action === 'move-up' && index < layers.length - 1) {
      const temp = layers[index];
      layers[index] = layers[index + 1];
      layers[index + 1] = temp;
      renderLayerList();
    } else if (action === 'move-down' && index > 0) {
      const temp = layers[index];
      layers[index] = layers[index - 1];
      layers[index - 1] = temp;
      renderLayerList();
    }
  };
  layerList.addEventListener('click', onLayerListClick);

  const onAddLayerClick = (): void => {
    addLayer();
  };
  btnAddLayer.addEventListener('click', onAddLayerClick);

  const onClearAllClick = (): void => {
    layers = [];
    renderLayerList();
    updateStatus();
    exportJsonManifest();
  };
  btnClearAll.addEventListener('click', onClearAllClick);

  const onExportJsonClick = (): void => {
    exportJsonManifest();
  };
  btnExportJson.addEventListener('click', onExportJsonClick);

  const onImportJsonClick = (): void => {
    importJsonManifest();
  };
  btnImportJson.addEventListener('click', onImportJsonClick);

  const onPresetChange = (): void => {
    if (presetSelect.value) {
      loadPreset(presetSelect.value);
    }
  };
  presetSelect.addEventListener('change', onPresetChange);

  const onBgChange = (): void => {
    stageViewport.className = `stage-viewport bg-${bgSelect.value}`;
  };
  bgSelect.addEventListener('change', onBgChange);

  const onAspectChange = (): void => {
    const [w, h] = aspectSelect.value.split('/');
    stageViewport.style.aspectRatio = `${w} / ${h}`;
    resizeCanvas();
  };
  aspectSelect.addEventListener('change', onAspectChange);

  const onPlayPauseClick = (): void => {
    isPlaying = !isPlaying;
    btnPlayPause.textContent = isPlaying ? 'Pause Motion' : 'Resume Motion';
  };
  btnPlayPause.addEventListener('click', onPlayPauseClick);

  const onMasterSpeedInput = (): void => {
    masterSpeed = parseFloat(masterSpeedInput.value);
    masterSpeedVal.textContent = `${masterSpeed.toFixed(1)}x`;
  };
  masterSpeedInput.addEventListener('input', onMasterSpeedInput);

  window.addEventListener('resize', resizeCanvas);

  resizeCanvas();
  void initCatalog();
  animFrameId = requestAnimationFrame(renderLoop);

  return {
    initCatalog,
    addLayer,
    loadPreset,
    exportJsonManifest,
    importJsonManifest,
    destroy(): void {
      destroyed = true;
      if (animFrameId !== null) {
        cancelAnimationFrame(animFrameId);
        animFrameId = null;
      }
      layerList.removeEventListener('input', onLayerListInput);
      layerList.removeEventListener('change', onLayerListChange);
      layerList.removeEventListener('click', onLayerListClick);
      btnAddLayer.removeEventListener('click', onAddLayerClick);
      btnClearAll.removeEventListener('click', onClearAllClick);
      btnExportJson.removeEventListener('click', onExportJsonClick);
      btnImportJson.removeEventListener('click', onImportJsonClick);
      presetSelect.removeEventListener('change', onPresetChange);
      bgSelect.removeEventListener('change', onBgChange);
      aspectSelect.removeEventListener('change', onAspectChange);
      btnPlayPause.removeEventListener('click', onPlayPauseClick);
      masterSpeedInput.removeEventListener('input', onMasterSpeedInput);
      window.removeEventListener('resize', resizeCanvas);
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initLayeredAnimationLab();
  });
} else {
  initLayeredAnimationLab();
}

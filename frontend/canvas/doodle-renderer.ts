import type { DoodleAnnotation, AnnotationNode, Point, ImagePlane, ScreenPlane, SegmentTarget, TextTarget, DoodleRendererOptions, MoveDrag, BoxDrag } from './doodle-types.js';
export type { DoodleAnnotation, DoodleRendererOptions, AnnotationNode } from './doodle-types.js';

declare global { interface Window { isCanvasSelectionActive?: boolean } }

export function createDoodleRenderer({ canvas, isVisible = () => true, textLayer = null, canEditText = () => false, onEditText = () => {}, onMoveText = () => {}, onRemoveText = () => {}, onSelectText = () => {}, stampLayer = null, canMoveStamp = () => false, onMoveStamp = () => {}, onRemoveStamp = () => {}, onSelectStamp = () => {}, canSelectSegment = () => true, onSelectSegment = () => {}, onRemoveSegment = () => {}, getPage = () => null, fitMode = "contain" }: DoodleRendererOptions) {
    function annotationChildren(layer: HTMLElement): AnnotationNode[] {
        return Array.from(layer.children) as AnnotationNode[];
    }

    function imagePlane(action: DoodleAnnotation | null = null): ImagePlane {
        const page = getPage();
        const pageWidth = action?.canvas_page_width || page?.width || 0;
        const pageHeight = action?.canvas_page_height || page?.height || 0;
        const width = canvas?.clientWidth || 0;
        const height = canvas?.clientHeight || 0;
        if (!(pageWidth > 0 && pageHeight > 0)) return {x: 0, y: 0, width, height};
        const scale = fitMode === "cover" ? Math.max(width / pageWidth, height / pageHeight) : Math.min(width / pageWidth, height / pageHeight);
        const imageWidth = pageWidth * scale;
        const imageHeight = pageHeight * scale;
        return {x: (width - imageWidth) / 2, y: (height - imageHeight) / 2, width: imageWidth, height: imageHeight};
    }

    function screenPlane(action: DoodleAnnotation | null = null): ScreenPlane {
        const rect = canvas.getBoundingClientRect();
        const plane = imagePlane(action);
        const sx = rect.width / canvas.clientWidth;
        const sy = rect.height / canvas.clientHeight;
        return {left: rect.left + plane.x * sx, top: rect.top + plane.y * sy, width: plane.width * sx, height: plane.height * sy};
    }

    function normalizedPoint(event: { clientX: number; clientY: number } | { touches: ArrayLike<{ clientX: number; clientY: number }> }): Point {
        const plane = screenPlane();
        const pointer = 'touches' in event ? event.touches[0] : event;
        return {x: Math.max(0, Math.min(1, (pointer.clientX - plane.left) / plane.width)),
            y: Math.max(0, Math.min(1, (pointer.clientY - plane.top) / plane.height))};
    }

    function textSize(action: DoodleAnnotation): number {
        return Number.isInteger(action.canvas_font_size)
            ? action.canvas_font_size! * imagePlane(action).height / 10000
            : Math.max(12, Number(action.size) || 32);
    }

    function resizedStamp(action: DoodleAnnotation, size: number): DoodleAnnotation {
        const resized = {...action, size};
        if (Number.isInteger(action.canvas_width) && Number.isInteger(action.canvas_height)) {
            const ratio = size / (Number(action.size) || 80);
            resized.canvas_width = Math.max(1, Math.round(action.canvas_width! * ratio));
            resized.canvas_height = Math.max(1, Math.round(action.canvas_height! * ratio));
            resized.canvas_size = size;
        }
        return resized;
    }
    const context = canvas?.getContext("2d");

    const selectedSegmentIds = new Set<string>();

    function ensureSegmentId(action: DoodleAnnotation | null): string | null {
        if (!action) return null;
        if (!action.id) {
            action.id = (typeof crypto !== "undefined" && crypto.randomUUID)
                ? crypto.randomUUID()
                : ("seg-" + Math.random().toString(36).slice(2, 10));
        }
        if (!action.stroke_id) {
            action.stroke_id = action.id;
        }
        return action.id;
    }

    function isSegmentSelected(action: DoodleAnnotation | null): boolean {
        if (!action) return false;
        if (action.selected) return true;
        return Boolean(action.id && selectedSegmentIds.has(action.id));
    }

    function getSelectedSegments(): DoodleAnnotation[] {
        return lastDoodleActions.filter(action => action.type === "draw" && isSegmentSelected(action));
    }

    function selectSegment(target: SegmentTarget, multi = false): void {
        if (typeof canSelectSegment === "function" && !canSelectSegment()) return;
        if (!multi) {
            selectedSegmentIds.clear();
            lastDoodleActions.forEach(a => {
                if (a.type === "draw") a.selected = false;
            });
        }
        const items = Array.isArray(target) ? target : (target ? [target] : []);
        items.forEach(item => {
            const action = (typeof item === "string")
                ? lastDoodleActions.find(a => a.type === "draw" && (a.id === item || a.stroke_id === item))
                : item;
            if (action && action.type === "draw") {
                ensureSegmentId(action);
                const strokeId = action.stroke_id;
                if (strokeId) {
                    lastDoodleActions.forEach(a => {
                        if (a.type === "draw" && (a.stroke_id === strokeId || a.id === strokeId)) {
                            ensureSegmentId(a);
                            selectedSegmentIds.add(a.id!);
                            a.selected = true;
                        }
                    });
                } else {
                    selectedSegmentIds.add(action.id!);
                    action.selected = true;
                }
            }
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment(getSelectedSegments());
        }
        redraw();
    }

    function deselectSegment(target: SegmentTarget): void {
        const items = Array.isArray(target) ? target : (target ? [target] : []);
        items.forEach(item => {
            const action = (typeof item === "string")
                ? lastDoodleActions.find(a => a.type === "draw" && (a.id === item || a.stroke_id === item))
                : item;
            if (action) {
                const strokeId = action.stroke_id;
                if (strokeId) {
                    lastDoodleActions.forEach(a => {
                        if (a.type === "draw" && (a.stroke_id === strokeId || a.id === strokeId)) {
                            if (a.id) selectedSegmentIds.delete(a.id);
                            a.selected = false;
                        }
                    });
                } else {
                    if (action.id) selectedSegmentIds.delete(action.id);
                    action.selected = false;
                }
            }
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment(getSelectedSegments());
        }
        redraw();
    }

    function clearSelectedSegments(): void {
        if (selectedSegmentIds.size === 0 && !lastDoodleActions.some(a => a.type === "draw" && a.selected)) {
            return;
        }
        selectedSegmentIds.clear();
        lastDoodleActions.forEach(a => {
            if (a.type === "draw") a.selected = false;
        });
        if (typeof onSelectSegment === "function") {
            onSelectSegment([]);
        }
        redraw();
    }

    function distanceToSegment(px: number, py: number, x0: number, y0: number, x1: number, y1: number): number {
        const dx = x1 - x0;
        const dy = y1 - y0;
        const l2 = dx * dx + dy * dy;
        if (l2 === 0) return Math.hypot(px - x0, py - y0);
        let t = ((px - x0) * dx + (py - y0) * dy) / l2;
        t = Math.max(0, Math.min(1, t));
        const projX = x0 + t * dx;
        const projY = y0 + t * dy;
        return Math.hypot(px - projX, py - projY);
    }

    function segmentsIntersect(x1: number, y1: number, x2: number, y2: number, x3: number, y3: number, x4: number, y4: number): boolean {
        function ccw(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): boolean {
            return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax);
        }
        return (ccw(x1, y1, x3, y3, x4, y4) !== ccw(x2, y2, x3, y3, x4, y4)) &&
               (ccw(x1, y1, x2, y2, x3, y3) !== ccw(x1, y1, x2, y2, x4, y4));
    }

    function lineIntersectsRect(ax: number, ay: number, bx: number, by: number, minX: number, minY: number, maxX: number, maxY: number): boolean {
        return segmentsIntersect(ax, ay, bx, by, minX, minY, maxX, minY) ||
               segmentsIntersect(ax, ay, bx, by, minX, maxY, maxX, maxY) ||
               segmentsIntersect(ax, ay, bx, by, minX, minY, minX, maxY) ||
               segmentsIntersect(ax, ay, bx, by, maxX, minY, maxX, maxY);
    }

    function findSegmentAt(px: number, py: number, threshold: number | null = null): DoodleAnnotation | null {
        if (!canvas) return null;
        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return null;
        for (let i = lastDoodleActions.length - 1; i >= 0; i--) {
            const action = lastDoodleActions[i];
            if (action.type !== "draw") continue;
            ensureSegmentId(action);
            const plane = screenPlane(action);
            const x0 = plane.left - rect.left + Number(action.x0) * plane.width;
            const y0 = plane.top - rect.top + Number(action.y0) * plane.height;
            const x1 = plane.left - rect.left + Number(action.x1) * plane.width;
            const y1 = plane.top - rect.top + Number(action.y1) * plane.height;
            const limit = threshold !== null ? threshold : Math.max(8, (Number(action.size) || 3) / 2 + 5);
            if (distanceToSegment(px, py, x0, y0, x1, y1) <= limit) {
                return action;
            }
        }
        return null;
    }

    function getSegmentsInRect(x0: number, y0: number, x1: number, y1: number): DoodleAnnotation[] {
        if (!canvas) return [];
        const rect = canvas.getBoundingClientRect();
        let minX = Math.min(x0, x1);
        let maxX = Math.max(x0, x1);
        let minY = Math.min(y0, y1);
        let maxY = Math.max(y0, y1);
        if (maxX <= 1 && maxY <= 1 && rect.width > 0 && rect.height > 0) {
            // Already normalized [0..1]
        } else if (rect.width > 0 && rect.height > 0) {
            minX /= rect.width;
            maxX /= rect.width;
            minY /= rect.height;
            maxY /= rect.height;
        }
        return lastDoodleActions.filter(action => {
            if (action.type !== "draw") return false;
            ensureSegmentId(action);
            const plane = screenPlane(action);
            const ax = (plane.left - rect.left + Number(action.x0) * plane.width) / rect.width;
            const ay = (plane.top - rect.top + Number(action.y0) * plane.height) / rect.height;
            const bx = (plane.left - rect.left + Number(action.x1) * plane.width) / rect.width;
            const by = (plane.top - rect.top + Number(action.y1) * plane.height) / rect.height;
            const segMinX = Math.min(ax, bx);
            const segMaxX = Math.max(ax, bx);
            const segMinY = Math.min(ay, by);
            const segMaxY = Math.max(ay, by);
            if (segMaxX < minX || segMinX > maxX || segMaxY < minY || segMinY > maxY) {
                return false;
            }
            if ((ax >= minX && ax <= maxX && ay >= minY && ay <= maxY) ||
                (bx >= minX && bx <= maxX && by >= minY && by <= maxY)) {
                return true;
            }
            return lineIntersectsRect(ax, ay, bx, by, minX, minY, maxX, maxY);
        });
    }

    function selectSegmentsInRect(x0: number, y0: number, x1: number, y1: number, multi = false): DoodleAnnotation[] {
        const found = getSegmentsInRect(x0, y0, x1, y1);
        selectSegment(found, multi);
        return found;
    }

    const interactionTarget = canvas?.parentElement || canvas;

    function getOrCreateSelectionBox(): HTMLElement | null {
        if (!interactionTarget) return null;
        let box = interactionTarget.querySelector<HTMLElement>(".canvas-selection-box");
        if (!box) {
            box = document.createElement("div");
            box.className = "canvas-selection-box";
            box.style.position = "absolute";
            box.style.border = "1.5px dashed #818cf8";
            box.style.background = "rgba(99, 102, 241, 0.16)";
            box.style.borderRadius = "4px";
            box.style.pointerEvents = "none";
            box.style.zIndex = "25";
            box.style.display = "none";
            box.style.boxSizing = "border-box";
            interactionTarget.appendChild(box);
        }
        return box;
    }

    let boxDrag: BoxDrag | null = null;

    function cancelInteraction(): void {
        const pointerId = boxDrag?.pointerId;
        boxDrag = null;
        if (typeof window !== 'undefined') window.isCanvasSelectionActive = false;
        const box = interactionTarget?.querySelector<HTMLElement>('.canvas-selection-box');
        if (box) box.style.display = 'none';
        if (pointerId !== undefined && interactionTarget.hasPointerCapture(pointerId)) {
            interactionTarget.releasePointerCapture(pointerId);
        }
        clearSelectedSegments();
        selectMovableStamp(null);
        selectMovableText(null);
    }

    interactionTarget?.addEventListener("pointerdown", event => {
        if (event.button !== 0) return;
        if (event.target instanceof Element && (
            event.target.closest(".canvas-stamp-annotation") ||
            event.target.closest(".canvas-text-annotation") ||
            event.target.closest(".stamp-resize-handle") ||
            event.target.closest(".stamp-rotate-handle") ||
            event.target.closest("#orator-command-overlay") ||
            event.target.closest("#history-paging-controls") ||
            event.target.closest(".a2ui-surface") ||
            event.target.closest("#sticky-notes-toggle-btn") ||
            event.target.closest("#suggestions-toggle-btn") ||
            event.target.closest("#hide-canvas-text-btn") ||
            event.target.closest("#prompt-toggle-btn") ||
            event.target.closest("#canvas-pin-btn") ||
            event.target.closest("#agent-activity-tray") ||
            event.target.closest("#narration-pane-mount") ||
            event.target.closest("button, a, input, select, textarea, [role='button']")
        )) {
            return;
        }
        if (typeof canSelectSegment === "function" && !canSelectSegment()) return;
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        event.preventDefault();

        const startX = event.clientX - rect.left;
        const startY = event.clientY - rect.top;

        boxDrag = {
            startClientX: event.clientX,
            startClientY: event.clientY,
            startX,
            startY,
            isMulti: event.shiftKey || event.ctrlKey || event.metaKey,
            active: false,
            pointerId: event.pointerId,
        };

        if (interactionTarget.setPointerCapture) {
            try {
                interactionTarget.setPointerCapture(event.pointerId);
            } catch (_) {}
        }
    });

    interactionTarget?.addEventListener("dragstart", event => {
        if (event.target instanceof Element && (
            event.target.closest("#sticky-notes-toggle-btn") ||
            event.target.closest("#suggestions-toggle-btn") ||
            event.target.closest("#hide-canvas-text-btn") ||
            event.target.closest("#prompt-toggle-btn") ||
            event.target.closest("#canvas-pin-btn") ||
            event.target.closest(".canvas-stamp-annotation") ||
            event.target.closest(".canvas-text-annotation") ||
            event.target.closest("#orator-command-overlay") ||
            event.target.closest("#history-paging-controls") ||
            event.target.closest(".a2ui-surface") ||
            event.target.closest("button, a, input, select, textarea, [role='button']")
        )) {
            return;
        }
        if (boxDrag || (typeof canSelectSegment === "function" && canSelectSegment())) {
            event.preventDefault();
        }
    });

    interactionTarget?.addEventListener("pointermove", event => {
        if (!boxDrag) return;
        const dx = event.clientX - boxDrag.startClientX;
        const dy = event.clientY - boxDrag.startClientY;
        if (!boxDrag.active && Math.hypot(dx, dy) >= 4) {
            boxDrag.active = true;
            if (typeof window !== "undefined") window.isCanvasSelectionActive = true;
            if (interactionTarget.classList.contains("stamp-drag-over")) {
                interactionTarget.classList.remove("stamp-drag-over");
            }
            const box = getOrCreateSelectionBox();
            if (box) box.style.display = "block";
        }
        if (boxDrag.active) {
            event.preventDefault();
            if (interactionTarget.classList.contains("stamp-drag-over")) {
                interactionTarget.classList.remove("stamp-drag-over");
            }
            const rect = canvas.getBoundingClientRect();
            const curX = event.clientX - rect.left;
            const curY = event.clientY - rect.top;
            const boxLeft = Math.max(0, Math.min(rect.width, Math.min(boxDrag.startX, curX)));
            const boxTop = Math.max(0, Math.min(rect.height, Math.min(boxDrag.startY, curY)));
            const boxRight = Math.max(0, Math.min(rect.width, Math.max(boxDrag.startX, curX)));
            const boxBottom = Math.max(0, Math.min(rect.height, Math.max(boxDrag.startY, curY)));

            const box = getOrCreateSelectionBox();
            if (box) {
                // The overlay belongs to the untransformed container, while hit
                // testing above uses screen pixels relative to the viewed canvas.
                const parentRect = interactionTarget.getBoundingClientRect();
                box.style.left = `${boxLeft + rect.left - parentRect.left - interactionTarget.clientLeft + interactionTarget.scrollLeft}px`;
                box.style.top = `${boxTop + rect.top - parentRect.top - interactionTarget.clientTop + interactionTarget.scrollTop}px`;
                box.style.width = `${boxRight - boxLeft}px`;
                box.style.height = `${boxBottom - boxTop}px`;
            }
        }
    });

    const selectedStampItems = new Set<AnnotationNode>();
    let selectedStampItem: AnnotationNode | null = null;

    function selectMovableStamp(item: AnnotationNode | AnnotationNode[] | null, multi = false): void {
        if (!stampLayer) return;
        const previous = selectedStampItem;
        const items = (Array.isArray(item) ? item : (item ? [item] : []))
            .filter(node => node.annotation?.draggable !== false);
        if (!multi) {
            selectedStampItems.clear();
        }
        items.forEach(node => {
            if (node) selectedStampItems.add(node);
        });
        annotationChildren(stampLayer).forEach(node => {
            const isMovable = selectedStampItems.has(node);
            node.classList.toggle("movable", isMovable);
            if (!isMovable && (document.activeElement === node || node.contains(document.activeElement))) {
                if (typeof (document.activeElement as HTMLElement | null)?.blur === "function") {
                    (document.activeElement as HTMLElement).blur();
                }
                if (typeof node.blur === "function") {
                    node.blur();
                }
            }
        });
        const lastItem = items[items.length - 1] || null;
        selectedStampItem = lastItem;
        if (lastItem) {
            stampLayer.appendChild(lastItem);
            if (typeof lastItem.focus === "function") {
                lastItem.focus();
            }
            if (previous !== lastItem && typeof onSelectStamp === "function" && lastItem.annotation) {
                onSelectStamp(lastItem.annotation);
            }
        }
    }

    function getStampsInRect(x0: number, y0: number, x1: number, y1: number): AnnotationNode[] {
        if (!stampLayer || !canvas) return [];
        const canvasRect = canvas.getBoundingClientRect();
        if (!canvasRect.width || !canvasRect.height) return [];
        const minX = Math.min(x0, x1);
        const maxX = Math.max(x0, x1);
        const minY = Math.min(y0, y1);
        const maxY = Math.max(y0, y1);

        return annotationChildren(stampLayer).filter(item => {
            if (!item.classList.contains("canvas-stamp-annotation")) return false;
            const itemRect = item.getBoundingClientRect();
            const stampLeft = itemRect.left - canvasRect.left;
            const stampTop = itemRect.top - canvasRect.top;
            const stampRight = itemRect.right - canvasRect.left;
            const stampBottom = itemRect.bottom - canvasRect.top;

            return stampLeft <= maxX && stampRight >= minX && stampTop <= maxY && stampBottom >= minY;
        });
    }

    const selectedTextItems = new Set<AnnotationNode>();
    let selectedTextItem: AnnotationNode | null = null;

    function selectMovableText(item: TextTarget | TextTarget[] | null, multi = false): void {
        if (!textLayer) return;
        const previous = selectedTextItem;
        const rawItems = Array.isArray(item) ? item : (item ? [item] : []);
        const items = rawItems.map(it => {
            if (!it) return null;
            if (it instanceof HTMLElement) return it;
            const targetId = typeof it === 'string' ? it : it.id;
            return annotationChildren(textLayer).find(node => node.dataset?.annotationId === targetId || node.annotation?.id === targetId) || null;
        }).filter((node): node is AnnotationNode => node !== null);

        if (!multi) {
            selectedTextItems.clear();
        }
        items.forEach(node => {
            if (node) selectedTextItems.add(node);
        });
        annotationChildren(textLayer).forEach(node => {
            const isMovable = selectedTextItems.has(node);
            node.classList.toggle("movable", isMovable);
            if (!isMovable && (document.activeElement === node || node.contains(document.activeElement))) {
                if (typeof (document.activeElement as HTMLElement | null)?.blur === "function") {
                    (document.activeElement as HTMLElement).blur();
                }
                if (typeof node.blur === "function") {
                    node.blur();
                }
            }
        });
        const lastItem = items[items.length - 1] || null;
        selectedTextItem = lastItem;
        if (lastItem) {
            textLayer.appendChild(lastItem);
            if (typeof lastItem.focus === "function") {
                lastItem.focus();
            }
            if (previous !== lastItem && typeof onSelectText === "function" && lastItem.annotation) {
                onSelectText(lastItem.annotation);
            }
        }
    }

    function getTextInRect(x0: number, y0: number, x1: number, y1: number): AnnotationNode[] {
        if (!textLayer || !canvas) return [];
        const canvasRect = canvas.getBoundingClientRect();
        if (!canvasRect.width || !canvasRect.height) return [];
        let minX = Math.min(x0, x1);
        let maxX = Math.max(x0, x1);
        let minY = Math.min(y0, y1);
        let maxY = Math.max(y0, y1);
        if (maxX <= 1 && maxY <= 1 && (canvasRect.width > 1 || canvasRect.height > 1)) {
            minX *= canvasRect.width;
            maxX *= canvasRect.width;
            minY *= canvasRect.height;
            maxY *= canvasRect.height;
        }

        return annotationChildren(textLayer).filter(item => {
            if (!item.classList.contains("canvas-text-annotation")) return false;
            const itemRect = item.getBoundingClientRect();
            if (itemRect.width > 0 || itemRect.height > 0) {
                const textLeft = itemRect.left - canvasRect.left;
                const textTop = itemRect.top - canvasRect.top;
                const textRight = itemRect.right - canvasRect.left;
                const textBottom = itemRect.bottom - canvasRect.top;

                return textLeft <= maxX && textRight >= minX && textTop <= maxY && textBottom >= minY;
            }
            if (item.annotation) {
                const tx = Number(item.annotation.x) * canvasRect.width;
                const ty = Number(item.annotation.y) * canvasRect.height;
                const size = Number(item.annotation.size) || 32;
                const lines = String(item.annotation.text || "").split("\n").length;
                const estHeight = size * 1.15 * Math.max(1, lines);
                const estWidth = size * 0.6 * Math.max(1, String(item.annotation.text || "").length);
                const textRight = tx + estWidth;
                const textBottom = ty + estHeight;
                return tx <= maxX && textRight >= minX && ty <= maxY && textBottom >= minY;
            }
            return false;
        });
    }

    const finishSelectionBox = (event: PointerEvent): void => {
        if (!boxDrag) return;
        const drag = boxDrag;
        boxDrag = null;
        if (typeof window !== "undefined") window.isCanvasSelectionActive = false;
        if (interactionTarget.classList.contains("stamp-drag-over")) {
            interactionTarget.classList.remove("stamp-drag-over");
        }

        if (interactionTarget.hasPointerCapture && interactionTarget.hasPointerCapture(event.pointerId)) {
            try {
                interactionTarget.releasePointerCapture(event.pointerId);
            } catch (_) {}
        }

        const box = getOrCreateSelectionBox();
        if (box) box.style.display = "none";

        const rect = canvas.getBoundingClientRect();
        if (drag.active) {
            const curX = event.clientX - rect.left;
            const curY = event.clientY - rect.top;
            const boxLeft = Math.min(drag.startX, curX);
            const boxTop = Math.min(drag.startY, curY);
            const boxRight = Math.max(drag.startX, curX);
            const boxBottom = Math.max(drag.startY, curY);

            const segmentsInBox = getSegmentsInRect(boxLeft, boxTop, boxRight, boxBottom);
            selectSegment(segmentsInBox, drag.isMulti);

            const stampsInBox = getStampsInRect(boxLeft, boxTop, boxRight, boxBottom);
            if (stampsInBox.length > 0) {
                selectMovableStamp(stampsInBox, drag.isMulti);
            } else if (!drag.isMulti) {
                selectMovableStamp(null);
            }

            const textsInBox = getTextInRect(boxLeft, boxTop, boxRight, boxBottom);
            if (textsInBox.length > 0) {
                selectMovableText(textsInBox, drag.isMulti);
            } else if (!drag.isMulti) {
                selectMovableText(null);
            }
        } else {
            const hit = findSegmentAt(drag.startX, drag.startY);
            if (hit) {
                selectSegment(hit, drag.isMulti);
                if (!drag.isMulti) {
                    selectMovableStamp(null);
                    selectMovableText(null);
                }
            } else if (!drag.isMulti) {
                clearSelectedSegments();
                selectMovableStamp(null);
                selectMovableText(null);
            }
        }
    };

    interactionTarget?.addEventListener("pointerup", finishSelectionBox);
    interactionTarget?.addEventListener("pointercancel", finishSelectionBox);
    interactionTarget?.addEventListener("lostpointercapture", finishSelectionBox);

    if (typeof window !== "undefined") {
        window.addEventListener("keydown", event => {
            const active = document.activeElement;
            if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || (active as HTMLElement).isContentEditable)) return;
            if (event.key === "Escape") {
                if (boxDrag) {
                    boxDrag = null;
                    const box = getOrCreateSelectionBox();
                    if (box) box.style.display = "none";
                }
                if (selectedSegmentIds.size > 0) {
                    event.preventDefault();
                    clearSelectedSegments();
                }
                if (selectedStampItems.size > 0 || selectedStampItem) {
                    event.preventDefault();
                    selectMovableStamp(null);
                }
                if (selectedTextItems.size > 0 || selectedTextItem) {
                    event.preventDefault();
                    selectMovableText(null);
                }
            } else if (event.key === "Delete" || event.key === "Backspace") {
                let handled = false;
                const segmentsToRemove = (selectedSegmentIds.size > 0 && typeof onRemoveSegment === "function")
                    ? getSelectedSegments()
                    : [];
                const stampsToRemove = (selectedStampItems.size > 0 && typeof onRemoveStamp === "function" && canMoveStamp())
                    ? Array.from(selectedStampItems).map(node => node.annotation).filter(Boolean)
                    : [];
                const canDeleteText = typeof canEditText === "function" ? canEditText() : true;
                const textsToRemove = (selectedTextItems.size > 0 && typeof onRemoveText === "function" && canDeleteText)
                    ? Array.from(selectedTextItems).map(node => node.annotation).filter(Boolean)
                    : [];

                if (segmentsToRemove.length > 0) {
                    clearSelectedSegments();
                    onRemoveSegment(segmentsToRemove);
                    handled = true;
                }
                if (stampsToRemove.length > 0) {
                    selectMovableStamp(null);
                    stampsToRemove.forEach(ann => onRemoveStamp(ann));
                    handled = true;
                }
                if (textsToRemove.length > 0) {
                    selectMovableText(null);
                    textsToRemove.forEach(ann => onRemoveText(ann));
                    handled = true;
                }
                if (handled) {
                    event.preventDefault();
                }
            }
        });
    }

    if (typeof window !== "undefined") {
        window.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (event.target instanceof Element && (
                event.target.closest(".canvas-stamp-annotation") ||
                event.target.closest(".stamp-resize-handle") ||
                event.target.closest(".stamp-rotate-handle") ||
                event.target.closest(".canvas-selection-box")
            )) return;
            if (selectedStampItems.size > 0 || selectedStampItem) {
                selectMovableStamp(null);
            }
        });
    } else {
        stampLayer?.parentElement?.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (event.target instanceof Element && !event.target.closest(".canvas-stamp-annotation")) selectMovableStamp(null);
        });
    }

    function renderSelectableStamp(action: DoodleAnnotation): void {
        if (!stampLayer || !action.id) return;
        const existingItem = annotationChildren(stampLayer).find(node => node.dataset.annotationId === action.id);
        const item = existingItem ?? Object.assign(document.createElement("div"), { annotation: action });
        if (!existingItem) {
            item.className = "canvas-stamp-annotation";
            item.dataset.annotationId = action.id;
            item.tabIndex = 0;
            item.title = "Drag to move; click to select, resize and rotate; press Delete to remove";

            const img = document.createElement("img");
            img.src = action.url || "";
            img.alt = action.name || "Stamp";
            img.draggable = false;
            item.appendChild(img);

            const resizeHandle = document.createElement("div");
            resizeHandle.className = "stamp-resize-handle";
            resizeHandle.title = "Drag to resize stamp";
            item.appendChild(resizeHandle);

            const rotateHandle = document.createElement("button");
            rotateHandle.type = "button";
            rotateHandle.className = "stamp-rotate-handle";
            rotateHandle.title = "Drag to rotate stamp; use Left/Right arrows for 15° steps";
            rotateHandle.setAttribute("aria-label", "Rotate stamp");
            rotateHandle.textContent = "↻";
            item.appendChild(rotateHandle);

            let rotateDrag: { original: DoodleAnnotation; centerX: number; centerY: number; startAngle: number } | null = null;
            rotateHandle.addEventListener("pointerdown", event => {
                event.stopPropagation();
                if (event.button !== 0 || !canMoveStamp() || !item.classList.contains("movable")) return;
                event.preventDefault();
                const rect = screenPlane(item.annotation);
                const centerX = rect.left + Number(item.annotation.x) * rect.width;
                const centerY = rect.top + Number(item.annotation.y) * rect.height;
                rotateDrag = {
                    original: item.annotation, centerX, centerY,
                    startAngle: Math.atan2(event.clientY - centerY, event.clientX - centerX),
                };
                rotateHandle.setPointerCapture(event.pointerId);
            });
            rotateHandle.addEventListener("pointermove", event => {
                if (!rotateDrag || !canMoveStamp()) return;
                const angle = Math.atan2(event.clientY - rotateDrag.centerY, event.clientX - rotateDrag.centerX);
                const degrees = (Number(rotateDrag.original.rotation) || 0) + (angle - rotateDrag.startAngle) * 180 / Math.PI;
                const rotation = ((Math.round(degrees) % 360) + 360) % 360;
                onMoveStamp({ ...rotateDrag.original, rotation }, rotateDrag.original, false);
            });
            const finishRotation = (event: PointerEvent): void => {
                if (!rotateDrag) return;
                const finished = rotateDrag;
                rotateDrag = null;
                if (rotateHandle.hasPointerCapture(event.pointerId)) rotateHandle.releasePointerCapture(event.pointerId);
                if (event.type === "pointerup" && canMoveStamp()) {
                    if ((Number(item.annotation.rotation) || 0) !== (Number(finished.original.rotation) || 0)) {
                        onMoveStamp(item.annotation, finished.original, true);
                    }
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            rotateHandle.addEventListener("pointerup", finishRotation);
            rotateHandle.addEventListener("pointercancel", finishRotation);
            rotateHandle.addEventListener("lostpointercapture", finishRotation);
            rotateHandle.addEventListener("keydown", event => {
                if (!canMoveStamp() || !item.classList.contains("movable")) return;
                if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                event.preventDefault();
                event.stopPropagation();
                const step = event.key === "ArrowRight" ? 15 : -15;
                const rotation = ((Number(item.annotation.rotation) || 0) + step + 360) % 360;
                onMoveStamp({ ...item.annotation, rotation }, item.annotation, true);
            });

            let resizeDrag: { original: DoodleAnnotation; startX: number; startY: number; startSize: number; moved: boolean } | null = null;
            resizeHandle.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canMoveStamp() || !item.classList.contains("movable")) return;
                event.stopPropagation();
                event.preventDefault();
                resizeDrag = {
                    original: item.annotation,
                    startX: event.clientX,
                    startY: event.clientY,
                    startSize: Math.max(24, Number(item.annotation.size) || 80),
                    moved: false,
                };
                resizeHandle.setPointerCapture(event.pointerId);
            });

            resizeHandle.addEventListener("pointermove", event => {
                if (!resizeDrag || !canMoveStamp()) return;
                const rect = (canvas && canvas.getBoundingClientRect().width > 0)
                    ? screenPlane(resizeDrag.original)
                    : (stampLayer?.getBoundingClientRect() || null);
                if (!rect || !rect.width || !rect.height) return;
                const centerX = rect.left + Number(resizeDrag.original.x) * rect.width;
                const centerY = rect.top + Number(resizeDrag.original.y) * rect.height;
                const angle = (Number(resizeDrag.original.rotation) || 0) * Math.PI / 180;
                const dx = event.clientX - centerX;
                const dy = event.clientY - centerY;
                const dist = Math.max(Math.abs(dx * Math.cos(angle) + dy * Math.sin(angle)),
                    Math.abs(-dx * Math.sin(angle) + dy * Math.cos(angle)));
                const currentPixelSize = dist * 2;
                const refWidth = 1000;
                const normalizedSize = Math.round((currentPixelSize / rect.width) * refWidth);
                const newSize = Math.max(24, Math.min(512, normalizedSize));
                if (Math.abs(newSize - resizeDrag.startSize) > 2) {
                    resizeDrag.moved = true;
                }
                onMoveStamp(resizedStamp(resizeDrag.original, newSize), resizeDrag.original, false);
            });

            const finishResize = (event: PointerEvent): void => {
                if (!resizeDrag) return;
                const finished = resizeDrag;
                resizeDrag = null;
                if (resizeHandle.hasPointerCapture(event.pointerId)) resizeHandle.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canMoveStamp()) {
                    onMoveStamp(item.annotation, finished.original, true);
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            resizeHandle.addEventListener("pointerup", finishResize);
            resizeHandle.addEventListener("pointercancel", finishResize);
            resizeHandle.addEventListener("lostpointercapture", finishResize);

            item.addEventListener("wheel", event => {
                if (!canMoveStamp() || !item.classList.contains("movable")) return;
                event.preventDefault();
                event.stopPropagation();
                const step = event.deltaY < 0 ? 8 : -8;
                const currentSize = Math.max(24, Number(item.annotation.size) || 80);
                const nextSize = Math.max(24, Math.min(512, currentSize + step));
                if (nextSize !== currentSize) {
                    onMoveStamp(resizedStamp(item.annotation, nextSize), item.annotation, true);
                }
            }, { passive: false });

            let drag: MoveDrag | null = null;
            item.addEventListener("click", event => {
                event.stopPropagation();
                if (canMoveStamp()) selectMovableStamp(item);
            });
            item.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canMoveStamp()) return;
                event.stopPropagation();
                event.preventDefault();
                selectMovableStamp(item);
                drag = {original: item.annotation, x: event.clientX, y: event.clientY, moved: false};
                item.setPointerCapture(event.pointerId);
            });
            item.addEventListener("pointermove", event => {
                if (!drag || !canMoveStamp()) return;
                const dx = event.clientX - drag.x;
                const dy = event.clientY - drag.y;
                if (!drag.moved && Math.hypot(dx, dy) < 4) return;
                const rect = screenPlane(drag.original);
                if (!rect.width || !rect.height) return;
                drag.moved = true;
                item.classList.add("dragging");
                onMoveStamp({
                    ...drag.original,
                    x: Math.max(0, Math.min(1, Number(drag.original.x) + dx / rect.width)),
                    y: Math.max(0, Math.min(1, Number(drag.original.y) + dy / rect.height)),
                }, drag.original, false);
            });
            const finishMove = (event: PointerEvent): void => {
                if (!drag) return;
                const finished = drag;
                drag = null;
                item.classList.remove("dragging");
                if (item.hasPointerCapture(event.pointerId)) item.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canMoveStamp()) {
                    onMoveStamp(item.annotation, finished.original, true);
                } else {
                    onMoveStamp(finished.original, finished.original, false);
                }
            };
            item.addEventListener("pointerup", finishMove);
            item.addEventListener("pointercancel", finishMove);
            item.addEventListener("lostpointercapture", finishMove);
            stampLayer.appendChild(item);
        }
        item.annotation = action;
        item.style.pointerEvents = action.draggable === false ? "none" : "";
        item.tabIndex = action.draggable === false ? -1 : 0;
        // Size in layout pixels; the viewer transform scales the whole layer.
        const plane = imagePlane(action);
        const width = plane.width || stampLayer?.clientWidth || 1000;
        const refWidth = 1000;
        const scale = width / refWidth;
        const baseSize = Math.max(16, Number(action.size) || 80);
        const renderedSize = Math.max(16, Math.round(baseSize * scale));
        item.style.width = `${Number.isInteger(action.canvas_width) ? action.canvas_width! * width / 10000 : renderedSize}px`;
        item.style.height = `${Number.isInteger(action.canvas_height) ? action.canvas_height! * plane.height / 10000 : renderedSize}px`;
        const canonical = (action.canvas_page_width ?? 0) > 0 || (getPage()?.width ?? 0) > 0;
        item.style.left = canonical ? `${plane.x + Number(action.x) * plane.width}px` : `${Number(action.x) * 100}%`;
        item.style.top = canonical ? `${plane.y + Number(action.y) * plane.height}px` : `${Number(action.y) * 100}%`;
        item.style.transform = `translate(-50%, -50%) rotate(${Number(action.rotation) || 0}deg)`;
        const img = item.querySelector("img");
        if (img && img.getAttribute("src") !== action.url) {
            img.src = action.url || "";
        }
    }

    if (typeof window !== "undefined") {
        window.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (event.target instanceof Element && (
                event.target.closest(".canvas-text-annotation") ||
                event.target.closest(".canvas-selection-box")
            )) return;
            if (selectedTextItems.size > 0 || selectedTextItem) {
                selectMovableText(null);
            }
        });
    } else {
        textLayer?.parentElement?.addEventListener("pointerdown", event => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            if (event.target instanceof Element && (
                event.target.closest(".canvas-text-annotation") ||
                event.target.closest(".canvas-selection-box")
            )) return;
            if (selectedTextItems.size > 0 || selectedTextItem) {
                selectMovableText(null);
            }
        });
    }

    function renderSelectableText(action: DoodleAnnotation): void {
        if (!textLayer || !action.id) return;
        const existingLabel = annotationChildren(textLayer).find(node => node.dataset.annotationId === action.id);
        const label = existingLabel ?? Object.assign(document.createElement("span"), { annotation: action });
        if (!existingLabel) {
            label.className = "canvas-text-annotation";
            label.dataset.annotationId = action.id;
            label.tabIndex = 0;
            label.title = "Click to select, then drag to move; double-click or press Enter to edit";
            let drag: MoveDrag | null = null;
            label.addEventListener("click", event => {
                event.stopPropagation();
                if (canEditText()) selectMovableText(label, event.shiftKey || event.ctrlKey || event.metaKey);
            });
            label.addEventListener("pointerdown", event => {
                if (event.button !== 0 || !canEditText() || !label.classList.contains("movable")) return;
                event.preventDefault();
                drag = {original: label.annotation, x: event.clientX, y: event.clientY, moved: false};
                label.setPointerCapture(event.pointerId);
            });
            label.addEventListener("pointermove", event => {
                if (!drag || !canEditText()) return;
                const dx = event.clientX - drag.x;
                const dy = event.clientY - drag.y;
                if (!drag.moved && Math.hypot(dx, dy) < 4) return;
                const rect = screenPlane(drag.original);
                if (!rect.width || !rect.height) return;
                drag.moved = true;
                label.classList.add("dragging");
                onMoveText({...drag.original,
                    x: Math.max(0, Math.min(1, Number(drag.original.x) + dx / rect.width)),
                    y: Math.max(0, Math.min(1, Number(drag.original.y) + dy / rect.height)),
                }, drag.original, false);
            });
            const finishMove = (event: PointerEvent): void => {
                if (!drag) return;
                const finished = drag;
                drag = null;
                label.classList.remove("dragging");
                if (label.hasPointerCapture(event.pointerId)) label.releasePointerCapture(event.pointerId);
                if (!finished.moved) return;
                if (event.type === "pointerup" && canEditText()) {
                    onMoveText(label.annotation, finished.original, true);
                } else {
                    onMoveText(finished.original, finished.original, false);
                }
            };
            label.addEventListener("pointerup", finishMove);
            label.addEventListener("pointercancel", finishMove);
            label.addEventListener("lostpointercapture", finishMove);
            label.addEventListener("dblclick", event => {
                event.stopPropagation();
                if (canEditText()) {
                    selectMovableText(null);
                    onEditText(label.annotation);
                }
            });
            label.addEventListener("keydown", event => {
                if (event.key === "Enter" && canEditText()) {
                    event.preventDefault();
                    selectMovableText(null);
                    onEditText(label.annotation);
                } else if (event.key === "Escape") {
                    selectMovableText(null);
                }
            });
            textLayer.appendChild(label);
        }
        label.annotation = action;
        label.textContent = String(action.text).split("\n").slice(0, 4).join("\n");
        const plane = imagePlane(action);
        const canonical = (action.canvas_page_width ?? 0) > 0 || (getPage()?.width ?? 0) > 0;
        label.style.left = canonical ? `${plane.x + Number(action.x) * plane.width}px` : `${Number(action.x) * 100}%`;
        label.style.top = canonical ? `${plane.y + Number(action.y) * plane.height}px` : `${Number(action.y) * 100}%`;
        label.style.font = `600 ${textSize(action)}px "${String(action.font || "Outfit").replace(/["'`;{}]/g, "")}", sans-serif`;
        label.style.lineHeight = "1.15";
        label.style.width = Number.isInteger(action.canvas_width) ? `${action.canvas_width! * plane.width / 10000}px` : "";
        label.style.height = Number.isInteger(action.canvas_height) ? `${action.canvas_height! * plane.height / 10000}px` : "";
        label.style.overflow = Number.isInteger(action.canvas_width) ? "hidden" : "";
    }

    function renderText(action: DoodleAnnotation): void {
        if (!context || !canvas || !action?.text) return;

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        const size = textSize(action);
        const font = String(action.font || "Outfit").replace(/["'`;{}]/g, "");
        const lines = String(action.text).split("\n").slice(0, 4);
        const plane = imagePlane(action);
        const x = plane.x + Number(action.x) * plane.width;
        const y = plane.y + Number(action.y) * plane.height;

        context.save();
        if (Number.isInteger(action.canvas_width) && Number.isInteger(action.canvas_height)) {
            context.beginPath();
            context.rect(x, y, action.canvas_width! * plane.width / 10000, action.canvas_height! * plane.height / 10000);
            context.clip();
        }
        context.globalCompositeOperation = "source-over";
        context.font = `600 ${size}px "${font}", sans-serif`;
        context.textBaseline = "top";
        context.lineJoin = "round";
        context.strokeStyle = "rgba(0, 0, 0, 0.72)";
        context.lineWidth = Math.max(2, size / 10);
        context.fillStyle = action.color || "#ffffff";
        lines.forEach((line, index) => {
            const lineY = y + (index * size * 1.15);
            context.strokeText(line, x, lineY);
            context.fillText(line, x, lineY);
        });
        context.restore();
        renderSelectableText(action);
    }

    function renderSegment(x0: number, y0: number, x1: number, y1: number, color: string, size: number, isSelected = false): void {
        if (!context) return;

        context.save();
        if (isSelected) {
            context.beginPath();
            context.globalCompositeOperation = "source-over";
            context.strokeStyle = "#818cf8";
            context.lineWidth = Math.max(size + 6, 8);
            context.lineCap = "round";
            context.lineJoin = "round";
            context.shadowColor = "rgba(99, 102, 241, 0.75)";
            context.shadowBlur = 8;
            context.moveTo(x0, y0);
            context.lineTo(x1, y1);
            context.stroke();
        }

        context.beginPath();
        if (color === "erase") {
            context.globalCompositeOperation = "destination-out";
            context.strokeStyle = "rgba(0,0,0,1)";
        } else {
            context.globalCompositeOperation = "source-over";
            context.strokeStyle = color;
        }
        context.shadowColor = "transparent";
        context.shadowBlur = 0;
        context.moveTo(x0, y0);
        context.lineTo(x1, y1);
        context.lineWidth = size;
        context.lineCap = "round";
        context.lineJoin = "round";
        context.stroke();

        if (isSelected) {
            context.globalCompositeOperation = "source-over";
            context.fillStyle = "#ffffff";
            context.strokeStyle = "#6366f1";
            context.lineWidth = 2;
            const handleRadius = Math.max(3, Math.min(6, size / 2 + 2));
            [ [x0, y0], [x1, y1] ].forEach(([hx, hy]) => {
                context.beginPath();
                context.arc(hx, hy, handleRadius, 0, Math.PI * 2);
                context.fill();
                context.stroke();
            });
        }
        context.restore();
    }

    let lastDoodleActions: DoodleAnnotation[] = [];

    function renderStrokeSegment(action: DoodleAnnotation, selected = false): void {
        const plane = imagePlane(action);
        const lineWidth = Number.isInteger(action.canvas_line_width)
            ? action.canvas_line_width! * plane.width / 10000
            : ((getPage()?.width ?? 0) > 0 ? (Number(action.size) || 3) * plane.width / 1000 : (Number(action.size) || 3));
        renderSegment(plane.x + action.x0! * plane.width, plane.y + action.y0! * plane.height,
            plane.x + action.x1! * plane.width, plane.y + action.y1! * plane.height, action.color || "", lineWidth, selected);
    }

    function redraw(actions?: DoodleAnnotation[]): void {
        if (!canvas || !context) return;
        if (actions) lastDoodleActions = actions;

        if (textLayer) {
            const ids = new Set(lastDoodleActions.filter(action => action.type === "text").map(action => action.id));
            annotationChildren(textLayer).forEach(label => {
                if (!ids.has(label.dataset.annotationId)) {
                    selectedTextItems.delete(label);
                    label.remove();
                }
            });
            if (selectedTextItem && !ids.has(selectedTextItem.dataset?.annotationId)) {
                selectedTextItem = null;
            }
            textLayer.hidden = !isVisible();
        }

        if (stampLayer) {
            const ids = new Set(lastDoodleActions.filter(action => action.type === "stamp").map(action => action.id));
            annotationChildren(stampLayer).forEach(item => {
                if (!ids.has(item.dataset.annotationId)) item.remove();
            });
            stampLayer.hidden = !isVisible();
        }

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        context.clearRect(0, 0, rect.width, rect.height);
        if (!isVisible()) return;

        lastDoodleActions.forEach((action) => {
            if (action.type === "draw") {
                ensureSegmentId(action);
                const isSelected = isSegmentSelected(action);
                renderStrokeSegment(action, isSelected);
            } else if (action.type === "text") {
                renderText(action);
            } else if (action.type === "stamp") {
                renderSelectableStamp(action);
            }
        });
        syncStampLayerOrder();
    }

    // Layer stamp nodes to match doodle order without touching nodes already in place.
    // Re-parenting a node that holds pointer capture fires lostpointercapture and
    // aborts an in-progress drag, so nodes are only moved when the order differs.
    function syncStampLayerOrder(): void {
        if (!stampLayer) return;
        const desired = lastDoodleActions
            .filter(action => action.type === "stamp" && action.id)
            .map(action => annotationChildren(stampLayer).find(node => node.dataset.annotationId === action.id))
            .filter((node): node is AnnotationNode => node !== undefined);
        desired.forEach((node, index) => {
            const current = stampLayer.children[index];
            if (current !== node) stampLayer.insertBefore(node, current || null);
        });
    }

    function resize(actions?: DoodleAnnotation[]): void {
        if (!canvas || !context) return;
        if (actions) lastDoodleActions = actions;

        const rect = { width: canvas.clientWidth, height: canvas.clientHeight };
        if (rect.width === 0 || rect.height === 0) return;

        const dpr = window.devicePixelRatio || 1;
        if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
            canvas.width = Math.floor(rect.width * dpr);
            canvas.height = Math.floor(rect.height * dpr);
        }

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.scale(dpr, dpr);
        redraw(lastDoodleActions);
    }

    if (typeof ResizeObserver !== "undefined") {
        const resizeTarget = canvas?.parentElement || canvas || stampLayer;
        if (resizeTarget) {
            let lastObservedWidth = 0;
            let lastObservedHeight = 0;
            const ro = new ResizeObserver(entries => {
                for (const entry of entries) {
                    const width = entry.contentRect.width;
                    const height = entry.contentRect.height;
                    if (Math.abs(width - lastObservedWidth) > 0.5 || Math.abs(height - lastObservedHeight) > 0.5) {
                        lastObservedWidth = width;
                        lastObservedHeight = height;
                        resize(lastDoodleActions);
                    }
                }
            });
            ro.observe(resizeTarget);
        }
    }

    return {
        renderSegment,
        renderStrokeSegment,
        normalizedPoint,
        imagePlane,
        renderText,
        renderStamp: renderSelectableStamp,
        redraw,
        resize,
        selectSegment,
        deselectSegment,
        clearSelectedSegments,
        getSelectedSegments,
        cancelInteraction,
        isSegmentSelected,
        findSegmentAt,
        getSegmentsInRect,
        selectSegmentsInRect,
        selectStamp: selectMovableStamp,
        getSelectedStamps: () => Array.from(selectedStampItems).map(node => node.annotation).filter(Boolean),
        getStampsInRect,
        selectText: selectMovableText,
        getSelectedTexts: () => Array.from(selectedTextItems).map(node => node.annotation).filter(Boolean),
        getSelectedText: () => Array.from(selectedTextItems).map(node => node.annotation).filter(Boolean),
        getTextInRect,
        getTextsInRect: getTextInRect,
        selectTextsInRect: (x0: number, y0: number, x1: number, y1: number, multi = false): AnnotationNode[] => {
            const found = getTextInRect(x0, y0, x1, y1);
            selectMovableText(found, multi);
            return found;
        },
        clearSelectedTexts: () => selectMovableText(null),
        isTextSelected: (target: TextTarget | null): boolean => {
            if (!target) return false;
            if (typeof target === "string") {
                return Array.from(selectedTextItems).some(node => node.dataset?.annotationId === target || node.annotation?.id === target);
            }
            if (target instanceof HTMLElement) {
                return selectedTextItems.has(target);
            }
            if (target.id) {
                return Array.from(selectedTextItems).some(node => node.dataset?.annotationId === target.id || node.annotation?.id === target.id);
            }
            return false;
        },
    };
}

export type DoodleRenderer = ReturnType<typeof createDoodleRenderer>;

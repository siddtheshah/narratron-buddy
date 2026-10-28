(() => {
    const container = document.getElementById('image-container');
    const toggle = document.getElementById('canvas-navigation-toggle');
    const surface = document.getElementById('canvas-navigation-surface');
    if (!container || !toggle || !surface) return;

    let enabled = false;
    let scale = 1;
    let x = 0;
    let y = 0;
    const pointers = new Map();

    function apply() {
        container.style.setProperty('--canvas-view-scale', scale);
        container.style.setProperty('--canvas-view-x', `${x}px`);
        container.style.setProperty('--canvas-view-y', `${y}px`);
    }

    function zoomAt(nextScale, clientX, clientY) {
        nextScale = Math.max(0.5, Math.min(5, nextScale));
        const rect = surface.getBoundingClientRect();
        const px = clientX - rect.left;
        const py = clientY - rect.top;
        const ratio = nextScale / scale;
        x = px - (px - x) * ratio;
        y = py - (py - y) * ratio;
        scale = nextScale;
        apply();
    }

    toggle.addEventListener('click', () => {
        enabled = !enabled;
        pointers.clear();
        surface.classList.remove('dragging');
        container.classList.toggle('canvas-navigation-enabled', enabled);
        toggle.setAttribute('aria-pressed', String(enabled));
        toggle.title = enabled
            ? 'Disable zoom and pan and reset view · Scroll to zoom, drag to pan, double-click to reset'
            : 'Enable zoom and pan';
        if (!enabled) {
            scale = 1;
            x = y = 0;
            apply();
        }
    });

    surface.addEventListener('wheel', event => {
        event.preventDefault();
        const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? surface.clientHeight : 1);
        zoomAt(scale * Math.exp(-delta * 0.0015), event.clientX, event.clientY);
    }, { passive: false });

    surface.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.stopPropagation();
        event.preventDefault();
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        surface.setPointerCapture(event.pointerId);
        surface.classList.add('dragging');
    });

    surface.addEventListener('pointermove', event => {
        event.stopPropagation();
        const previous = pointers.get(event.pointerId);
        if (!previous) return;
        const before = [...pointers.values()];
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        const after = [...pointers.values()];
        if (after.length === 2) {
            const midpoint = points => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 });
            const distance = points => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
            const oldCenter = midpoint(before);
            const center = midpoint(after);
            if (distance(before) > 0) zoomAt(scale * distance(after) / distance(before), oldCenter.x, oldCenter.y);
            x += center.x - oldCenter.x;
            y += center.y - oldCenter.y;
        } else if (after.length === 1) {
            x += event.clientX - previous.x;
            y += event.clientY - previous.y;
        }
        apply();
    });

    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        surface.addEventListener(type, event => {
            event.stopPropagation();
            pointers.delete(event.pointerId);
            if (!pointers.size) surface.classList.remove('dragging');
        });
    }
    for (const type of ['mousedown', 'mouseup', 'click', 'contextmenu']) {
        surface.addEventListener(type, event => {
            event.preventDefault();
            event.stopPropagation();
        });
    }
    window.addEventListener('blur', () => {
        pointers.clear();
        surface.classList.remove('dragging');
    });
    surface.addEventListener('dblclick', () => {
        scale = 1;
        x = y = 0;
        apply();
    });
})();

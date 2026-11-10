import { createGameRenderer } from './game-renderer.js';

export function initializeRemoteGame({ actionUrl, canEdit, canContribute, refresh }) {
    const viewport = document.getElementById('remote-game-viewport');
    const container = document.getElementById('remote-game-content');
    const stage = document.getElementById('remote-game-stage');
    const level = document.getElementById('game-zoom-level');
    const zoomOut = document.getElementById('game-zoom-out');
    const zoomIn = document.getElementById('game-zoom-in');
    let zoom = 1;
    let fitting = true;
    const renderer = createGameRenderer({
        container, toggleButton: document.createElement('button'),
        actionUrl, canEdit, canContribute, refresh, canMove: () => false, embedded: true,
    });

    function updateZoom() {
        const frame = container.querySelector('.game-html-panel');
        const width = Number.parseFloat(frame?.dataset.panelWidth) || 510;
        container.style.setProperty('--remote-game-width', `${width}px`);
        if (fitting && viewport.clientWidth) zoom = Math.min(1, viewport.clientWidth / width);
        container.style.transform = `scale(${zoom})`;
        stage.style.width = `${width * zoom}px`;
        stage.style.height = `${container.offsetHeight * zoom}px`;
        level.value = `${Math.round(zoom * 100)}%`;
        zoomOut.disabled = zoom <= 0.25;
        zoomIn.disabled = zoom >= 3;
    }

    function changeZoom(amount) {
        fitting = false;
        zoom = Math.max(0.25, Math.min(3, Math.round((zoom + amount) * 100) / 100));
        updateZoom();
    }
    zoomOut.addEventListener('click', () => changeZoom(-0.25));
    zoomIn.addEventListener('click', () => changeZoom(0.25));
    document.getElementById('game-zoom-fit').addEventListener('click', () => {
        fitting = true;
        updateZoom();
        viewport.scrollTo(0, 0);
    });
    const observer = new ResizeObserver(updateZoom);
    observer.observe(viewport);
    observer.observe(container);

    return {
        render(game) {
            renderer.render(game);
            updateZoom();
        },
    };
}

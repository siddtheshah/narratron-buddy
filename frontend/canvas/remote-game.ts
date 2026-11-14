import { createGameRenderer, type GamePayload } from './game-renderer.js';

export interface InitializeRemoteGameOptions {
    readonly actionUrl: () => string;
    readonly canEdit: () => boolean;
    readonly canContribute: () => boolean;
    readonly refresh: () => void | Promise<void>;
}

export interface RemoteGameController {
    render(game: GamePayload | null | undefined): void;
}

export function initializeRemoteGame({
    actionUrl,
    canEdit,
    canContribute,
    refresh,
}: InitializeRemoteGameOptions): RemoteGameController {
    const viewport = document.getElementById('remote-game-viewport');
    const container = document.getElementById('remote-game-content');
    const stage = document.getElementById('remote-game-stage');
    const level = document.getElementById('game-zoom-level');
    const zoomOut = document.getElementById('game-zoom-out');
    const zoomIn = document.getElementById('game-zoom-in');
    const zoomFit = document.getElementById('game-zoom-fit');

    if (
        !(viewport instanceof HTMLElement) ||
        !(container instanceof HTMLElement) ||
        !(stage instanceof HTMLElement) ||
        !(level instanceof HTMLOutputElement || level instanceof HTMLInputElement) ||
        !(zoomOut instanceof HTMLButtonElement) ||
        !(zoomIn instanceof HTMLButtonElement) ||
        !(zoomFit instanceof HTMLButtonElement)
    ) {
        throw new Error('Remote game DOM elements are missing.');
    }

    const viewportEl = viewport;
    const containerEl = container;
    const stageEl = stage;
    const levelEl = level;
    const zoomOutEl = zoomOut;
    const zoomInEl = zoomIn;
    const zoomFitEl = zoomFit;

    let zoom = 1;
    let fitting = true;
    const renderer = createGameRenderer({
        container: containerEl,
        toggleButton: document.createElement('button'),
        actionUrl,
        canEdit,
        canContribute,
        refresh,
        canMove: () => false,
        embedded: true,
    });

    function updateZoom(): void {
        const frame = containerEl.querySelector<HTMLElement>('.game-html-panel');
        const width = Number.parseFloat(frame?.dataset.panelWidth ?? '') || 510;
        containerEl.style.setProperty('--remote-game-width', `${width}px`);
        if (fitting && viewportEl.clientWidth) zoom = Math.min(1, viewportEl.clientWidth / width);
        containerEl.style.transform = `scale(${zoom})`;
        stageEl.style.width = `${width * zoom}px`;
        stageEl.style.height = `${containerEl.offsetHeight * zoom}px`;
        levelEl.value = `${Math.round(zoom * 100)}%`;
        zoomOutEl.disabled = zoom <= 0.25;
        zoomInEl.disabled = zoom >= 3;
    }

    function changeZoom(amount: number): void {
        fitting = false;
        zoom = Math.max(0.25, Math.min(3, Math.round((zoom + amount) * 100) / 100));
        updateZoom();
    }

    zoomOutEl.addEventListener('click', () => changeZoom(-0.25));
    zoomInEl.addEventListener('click', () => changeZoom(0.25));
    zoomFitEl.addEventListener('click', () => {
        fitting = true;
        updateZoom();
        viewportEl.scrollTo(0, 0);
    });
    const observer = new ResizeObserver(updateZoom);
    observer.observe(viewportEl);
    observer.observe(containerEl);

    return {
        render(game: GamePayload | null | undefined): void {
            renderer.render(game);
            updateZoom();
        },
    };
}

export async function createNarrationPane(mount) {
    if (!mount) throw new Error('Narration pane mount point is missing.');

    const response = await fetch('/static/html/narration-pane.html');
    if (!response.ok) throw new Error(`Unable to load narration pane (${response.status}).`);

    const template = document.createElement('template');
    template.innerHTML = await response.text();
    const container = template.content.firstElementChild;
    if (!container) throw new Error('Narration pane template is empty.');
    mount.replaceWith(container);

    let drawingColorSelected = true;

    function prepare(narration) {
        if (!narration) return;
        narration.setAttribute('role', 'region');
        narration.setAttribute('aria-label', 'Scene narration');
        narration.tabIndex = drawingColorSelected ? -1 : 0;
        narration.title = drawingColorSelected
            ? 'Deselect the active drawing color to scroll the full narration'
            : 'Scroll to read the full narration';
    }

    function setDrawingColorSelected(selected) {
        drawingColorSelected = Boolean(selected);
        container.classList.toggle('narration-interactive', !drawingColorSelected);
        prepare(container.querySelector('.scene-description'));
    }

    return { element: container, prepare, setDrawingColorSelected };
}

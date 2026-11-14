export interface NarrationPaneController {
  readonly element: HTMLElement;
  prepare(narration: HTMLElement | null | undefined): void;
  setDrawingColorSelected(selected: boolean): void;
}

/** Load the narration pane template, mount it in place of `mount`, and return its controller. */
export async function createNarrationPane(
  mount: Element | null | undefined,
): Promise<NarrationPaneController> {
  if (!mount) {
    throw new Error('Narration pane mount point is missing.');
  }

  const response = await fetch('/static/html/narration-pane.html');
  if (!response.ok) {
    throw new Error(`Unable to load narration pane (${response.status}).`);
  }

  const template = document.createElement('template');
  template.innerHTML = await response.text();
  const firstChild = template.content.firstElementChild as HTMLElement | null;
  if (!firstChild) {
    throw new Error('Narration pane template is empty.');
  }
  const container: HTMLElement = firstChild;
  mount.replaceWith(container);

  let drawingColorSelected = true;

  function prepare(narration: HTMLElement | null | undefined): void {
    if (!narration) return;
    narration.setAttribute('role', 'region');
    narration.setAttribute('aria-label', 'Scene narration');
    narration.tabIndex = drawingColorSelected ? -1 : 0;
    narration.title = drawingColorSelected
      ? 'Deselect the active drawing color to scroll the full narration'
      : 'Scroll to read the full narration';
  }

  function setDrawingColorSelected(selected: boolean): void {
    drawingColorSelected = Boolean(selected);
    container.classList.toggle('narration-interactive', !drawingColorSelected);
    prepare(container.querySelector<HTMLElement>('.scene-description'));
  }

  return { element: container, prepare, setDrawingColorSelected };
}

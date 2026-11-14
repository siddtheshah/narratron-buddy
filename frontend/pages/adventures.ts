/** Premade Adventures page: catalog fetch, search/tag filtering, and instant deploy. */

import type {} from '../shared/auth-flow.js';

export interface AdventureSummary {
  readonly id: string;
  readonly title?: string;
  readonly description?: string;
  readonly genre?: string;
  readonly tags?: readonly string[];
  readonly created_at?: string;
  readonly lore_count?: number;
  readonly track_count?: number;
  readonly reference_count?: number;
  readonly difficulty?: string;
}

interface DeployTheaterResponse {
  readonly theater_id?: string;
  readonly theater?: {
    readonly theater_id?: string;
  };
  readonly detail?: unknown;
}

export interface AdventuresController {
  loadAdventures(): Promise<void>;
  selectTag(tag: string): void;
  filterAdventures(): void;
  deployAndPlay(adventureId: string, title?: string): Promise<void>;
  destroy(): void;
}

export function formatAdventureDate(isoStr: string | undefined): string {
  if (!isoStr) return 'Recently Added';
  const parsed = new Date(isoStr);
  if (Number.isNaN(parsed.getTime())) return 'Recently Added';
  return parsed.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function getButtonTag(button: HTMLElement): string {
  if (button.dataset.tag) {
    return button.dataset.tag;
  }
  const text = button.textContent?.trim() ?? '';
  if (text.includes('All')) return 'all';
  if (text === 'Norse') return 'Norse Fantasy';
  return text;
}

function createMetaPill(text: string, title: string): HTMLSpanElement {
  const pill = document.createElement('span');
  pill.className = 'meta-pill';
  pill.title = title;
  pill.textContent = text;
  return pill;
}

export function initAdventuresPage(): AdventuresController | null {
  const rawGrid = document.getElementById('adventuresGrid');
  const rawSearch = document.getElementById('searchInput');
  const rawOverlay = document.getElementById('deployOverlay');
  const rawOverlayTitle = document.getElementById('deployModalTitle');
  if (
    !(rawGrid instanceof HTMLElement) ||
    !(rawSearch instanceof HTMLInputElement) ||
    !(rawOverlay instanceof HTMLElement) ||
    !(rawOverlayTitle instanceof HTMLElement)
  ) {
    return null;
  }

  const grid = rawGrid;
  const searchInput = rawSearch;
  const deployOverlay = rawOverlay;
  const deployModalTitle = rawOverlayTitle;

  let allAdventures: AdventureSummary[] = [];
  let activeTag = 'all';
  let pendingDeployAdventureId: string | null = null;

  function renderLoadError(message: string): void {
    const box = document.createElement('div');
    box.style.cssText =
      'grid-column: 1 / -1; text-align: center; padding: 3rem; background: var(--glass-bg); border-radius: var(--radius);';

    const icon = document.createElement('div');
    icon.style.cssText = 'font-size: 2.5rem; margin-bottom: 0.75rem;';
    icon.textContent = '⚠️';

    const heading = document.createElement('h3');
    heading.style.cssText = 'margin-bottom: 0.5rem; color: #fff;';
    heading.textContent = 'Could not load adventures';

    const detail = document.createElement('p');
    detail.style.cssText = 'color: var(--text-secondary); margin-bottom: 1rem;';
    detail.textContent = message || 'Please check your connection and try again.';

    const retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'btn btn-secondary';
    retryBtn.textContent = '↻ Retry';
    retryBtn.addEventListener('click', () => {
      void loadAdventures();
    });

    box.append(icon, heading, detail, retryBtn);
    grid.replaceChildren(box);
  }

  function renderEmptyState(): void {
    const empty = document.createElement('div');
    empty.style.cssText =
      'grid-column: 1 / -1; text-align: center; padding: 4rem 1rem; background: var(--glass-bg); border-radius: var(--radius); border: 1px dashed var(--glass-border);';

    const icon = document.createElement('div');
    icon.style.cssText = 'font-size: 2.5rem; margin-bottom: 0.75rem;';
    icon.textContent = '🔍';

    const heading = document.createElement('h3');
    heading.style.cssText = 'margin-bottom: 0.5rem; color: #fff;';
    heading.textContent = 'No adventures matched your search';

    const copy = document.createElement('p');
    copy.style.cssText = 'color: var(--text-secondary); margin-bottom: 1.2rem;';
    copy.textContent = 'Try searching for a different keyword or genre.';

    const clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.className = 'btn btn-secondary btn-sm';
    clearBtn.textContent = 'Clear Filters';
    clearBtn.addEventListener('click', () => {
      searchInput.value = '';
      selectTag('all');
    });

    empty.append(icon, heading, copy, clearBtn);
    grid.replaceChildren(empty);
  }

  function buildAdventureCard(adv: AdventureSummary): HTMLDivElement {
    const card = document.createElement('div');
    card.className = 'card';
    card.id = `card-${adv.id}`;

    const coverContainer = document.createElement('div');
    coverContainer.className = 'card-cover-container';

    const coverImg = document.createElement('img');
    coverImg.className = 'card-cover';
    coverImg.src = `/api/adventures/${encodeURIComponent(adv.id)}/cover`;
    coverImg.alt = adv.title || adv.id;

    const placeholder = document.createElement('div');
    placeholder.className = 'card-cover-placeholder';
    placeholder.style.display = 'none';
    placeholder.textContent = '🗺️';

    coverImg.addEventListener('error', () => {
      coverImg.style.display = 'none';
      placeholder.style.display = 'flex';
    });

    const overlay = document.createElement('div');
    overlay.className = 'card-cover-overlay';

    const badges = document.createElement('div');
    badges.className = 'card-badges';

    const dateBadge = document.createElement('span');
    dateBadge.className = 'badge-date';
    dateBadge.textContent = `📅 ${formatAdventureDate(adv.created_at)}`;

    const genreBadge = document.createElement('span');
    genreBadge.className = 'badge-genre';
    genreBadge.textContent = adv.genre || 'Adventure';

    badges.append(dateBadge, genreBadge);
    coverContainer.append(coverImg, placeholder, overlay, badges);

    const body = document.createElement('div');
    body.className = 'card-body';

    const title = document.createElement('h2');
    title.className = 'card-title';
    title.textContent = adv.title || adv.id;

    const desc = document.createElement('p');
    desc.className = 'card-desc';
    desc.textContent = adv.description || 'Step into this premade Narratron adventure experience.';

    const pills = document.createElement('div');
    pills.className = 'card-meta-pills';

    const loreCount = adv.lore_count || 0;
    const trackCount = adv.track_count || 0;
    const refCount = adv.reference_count || 0;

    if (loreCount > 0) {
      pills.append(createMetaPill(`📜 ${loreCount} Lore Docs`, `${loreCount} Lore Documents`));
    }
    if (trackCount > 0) {
      pills.append(createMetaPill(`🎵 ${trackCount} Soundtracks`, `${trackCount} Audio Tracks`));
    }
    if (refCount > 0) {
      pills.append(
        createMetaPill(`🖼️ ${refCount} Reference Visuals`, `${refCount} Reference Images`),
      );
    }
    if (adv.difficulty) {
      pills.append(createMetaPill(`⚡ ${adv.difficulty}`, 'Difficulty'));
    }

    const actions = document.createElement('div');
    actions.className = 'card-actions';

    const playBtn = document.createElement('button');
    playBtn.type = 'button';
    playBtn.className = 'btn btn-primary';
    const playLabel = document.createElement('span');
    playLabel.textContent = '🚀 Play Adventure';
    playBtn.append(playLabel);
    playBtn.addEventListener('click', () => {
      void deployAndPlay(adv.id, adv.title);
    });

    const deployerLink = document.createElement('a');
    deployerLink.href = `/deploy?creation_path=adventure&preset=${encodeURIComponent(adv.id)}`;
    deployerLink.className = 'btn btn-secondary btn-sm';
    deployerLink.title = 'Configure settings before deploying';
    const deployerLabel = document.createElement('span');
    deployerLabel.textContent = '⚙️ Deployer';
    deployerLink.append(deployerLabel);

    actions.append(playBtn, deployerLink);
    body.append(title, desc, pills, actions);
    card.append(coverContainer, body);
    return card;
  }

  function renderFiltered(adventures: readonly AdventureSummary[]): void {
    if (adventures.length === 0) {
      renderEmptyState();
      return;
    }
    grid.replaceChildren(...adventures.map(buildAdventureCard));
  }

  function filterAdventures(): void {
    const query = searchInput.value.toLowerCase().trim();
    const tagQuery = activeTag.toLowerCase();
    const filtered = allAdventures.filter((adv) => {
      const title = (adv.title || '').toLowerCase();
      const desc = (adv.description || '').toLowerCase();
      const genre = (adv.genre || '').toLowerCase();
      const tags = (adv.tags || []).map((tag) => tag.toLowerCase());

      const matchesQuery =
        !query ||
        title.includes(query) ||
        desc.includes(query) ||
        tags.some((tag) => tag.includes(query)) ||
        genre.includes(query);
      const matchesTag =
        activeTag === 'all' ||
        genre.includes(tagQuery) ||
        tags.some((tag) => tag.includes(tagQuery));

      return matchesQuery && matchesTag;
    });

    renderFiltered(filtered);
  }

  function selectTag(tag: string): void {
    activeTag = tag;
    document.querySelectorAll<HTMLElement>('.tag-btn').forEach((button) => {
      const buttonTag = getButtonTag(button);
      button.classList.toggle('active', buttonTag.toLowerCase() === tag.toLowerCase());
    });
    filterAdventures();
  }

  async function loadAdventures(): Promise<void> {
    try {
      const res = await fetch('/api/adventures');
      if (!res.ok) {
        throw new Error('Failed to load adventures');
      }
      allAdventures = (await res.json()) as AdventureSummary[];
      filterAdventures();
    } catch (error: unknown) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : 'Please check your connection and try again.';
      renderLoadError(message);
    }
  }

  async function deployAndPlay(adventureId: string, title?: string): Promise<void> {
    if (!window.currentUser) {
      pendingDeployAdventureId = adventureId;
      window.openAuthModal?.('login');
      return;
    }

    deployModalTitle.textContent = `Launching "${title || 'Adventure'}"...`;
    deployOverlay.classList.add('show');

    try {
      const formData = new FormData();
      formData.append('name', title || 'Narratron Adventure');
      formData.append('creation_mode', 'adventure');
      formData.append('preset_adventure_id', adventureId);
      formData.append('enable_adventure_mode', 'true');

      const res = await fetch('/api/theaters/create-and-deploy', {
        method: 'POST',
        body: formData,
      });

      const data = (await res.json()) as DeployTheaterResponse;
      if (!res.ok) {
        const detail = typeof data.detail === 'string' && data.detail ? data.detail : null;
        throw new Error(detail ?? 'Failed to deploy adventure theater.');
      }

      const theaterId = data.theater_id || data.theater?.theater_id;
      if (theaterId) {
        window.location.href = `/canvas?theater_id=${encodeURIComponent(theaterId)}`;
      } else {
        window.location.href = '/deploy';
      }
    } catch (error: unknown) {
      deployOverlay.classList.remove('show');
      const message = error instanceof Error ? error.message : String(error);
      window.alert(`Could not launch adventure: ${message}`);
    }
  }

  const onSearchInput = (): void => {
    filterAdventures();
  };
  searchInput.addEventListener('input', onSearchInput);

  const tagButtons = Array.from(document.querySelectorAll<HTMLElement>('.tag-btn'));
  const tagListeners: Array<{ button: HTMLElement; handler: () => void }> = [];
  for (const button of tagButtons) {
    const handler = (): void => {
      selectTag(getButtonTag(button));
    };
    button.addEventListener('click', handler);
    tagListeners.push({ button, handler });
  }

  const previousOnAuthSuccess = window.onAuthSuccess;
  const onAuthSuccessHandler = (action?: string, data?: unknown): void => {
    if ((action === 'login' || action === 'register') && pendingDeployAdventureId) {
      const targetAdvId = pendingDeployAdventureId;
      pendingDeployAdventureId = null;
      const adv = allAdventures.find((item) => item.id === targetAdvId);
      const title = adv?.title ?? 'Narratron Adventure';
      void deployAndPlay(targetAdvId, title);
    }
    previousOnAuthSuccess?.(action, data);
  };
  window.onAuthSuccess = onAuthSuccessHandler;

  void loadAdventures();

  return {
    loadAdventures,
    selectTag,
    filterAdventures,
    deployAndPlay,
    destroy(): void {
      searchInput.removeEventListener('input', onSearchInput);
      for (const { button, handler } of tagListeners) {
        button.removeEventListener('click', handler);
      }
      if (window.onAuthSuccess === onAuthSuccessHandler) {
        window.onAuthSuccess = previousOnAuthSuccess;
      }
    },
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initAdventuresPage();
  });
} else {
  initAdventuresPage();
}

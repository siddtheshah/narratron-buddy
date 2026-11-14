/** Theater Deployer page: creation paths, presets, config editor, theater list, and pricing modal. */

import type {} from './auth-flow.js';
import type {} from './credit-purchase.js';
import type {} from './device-navigation.js';

export type CreationPath = 'blank' | 'adventure';

export interface AdventurePreset {
  readonly id: string;
  readonly title?: string;
  readonly genre?: string;
  readonly description?: string;
  readonly lore_count?: number;
  readonly track_count?: number;
  readonly reference_count?: number;
  readonly difficulty?: string;
}

export interface TheaterSummary {
  readonly theater_id: string;
  readonly name: string;
  readonly is_owner?: boolean;
  readonly join_key?: string;
  readonly last_used_at?: string;
  readonly created_at?: string;
  readonly mounted_references?: readonly unknown[];
  readonly mounted_playlists?: Readonly<Record<string, unknown>>;
}

export interface PricingCalculation {
  readonly usage_credits?: number;
  readonly storage_credits?: number;
}

export interface PricingRatesResponse {
  readonly credits_per_usd: number;
  readonly usd_per_credit: number;
  readonly live_agent_tool_call_credit_rate: number;
  readonly story_planning_credit_rate?: number;
  readonly character_voicing_turn_credit_rate?: number;
  readonly interactive_canvas_tool_credit_rate?: number;
  readonly interactive_canvas_credit_rate?: number;
  readonly image_credit_rate: number;
  readonly music_credit_rate?: number;
  readonly layered_animation_credit_rate?: number;
  readonly storage_gb_daily_rate: number;
  readonly calculation?: PricingCalculation;
}

interface DefaultConfigResponse {
  readonly config_yaml?: string;
  readonly detail?: unknown;
}

interface DeployTheaterResponse {
  readonly theater_id?: string;
  readonly detail?: unknown;
}

interface RetitleResponse {
  readonly status?: string;
  readonly detail?: unknown;
}

interface ResolveJoinKeyResponse {
  readonly theater_id?: string;
  readonly name?: string;
  readonly user_id?: number | string;
  readonly detail?: unknown;
}

interface VerifyPaymentResponse {
  readonly verified?: boolean;
  readonly credits_added?: number;
}

interface DetailPayload {
  readonly detail?: unknown;
}

export interface CreationAuthUser {
  readonly id?: number | string;
  readonly username?: string;
  readonly credits?: number;
}

export interface TheaterCreationController {
  selectCreationPath(mode: string): void;
  onAdventurePresetSelected(): void;
  deployNewTheater(): Promise<void>;
  loadTheaters(): Promise<void>;
  destroyTheater(theaterId: string): Promise<void>;
  openPricingModal(): void;
  closePricingModal(): void;
  fetchPricingRates(): Promise<void>;
  calculateLivePricingEstimate(): Promise<void>;
}

declare global {
  interface Window {
    theaterDeletionConfirmed?: boolean;
    openPricingModal?: () => void;
    closePricingModal?: () => void;
    fetchPricingRates?: () => Promise<void>;
    calculateLivePricingEstimate?: () => Promise<void>;
    dismissCanvasReturnBanner?: () => void;
    selectCreationPath?: (mode: string) => void;
    onAdventurePresetSelected?: () => void;
    deployNewTheater?: () => Promise<void>;
    loadTheaters?: () => Promise<void>;
    acceptDeploymentAcknowledgement?: () => void;
    submitCanvasJoinKey?: () => Promise<void>;
    closeJoinKeyModal?: () => void;
    destroyTheater?: (theaterId: string) => Promise<void>;
    deleteTheater?: (theaterId: string) => Promise<void>;
    startEditingTitle?: (theaterId: string, currentName: string) => void;
    saveTheaterTitle?: (theaterId: string) => Promise<void>;
    copyJoinKey?: (key: string, btn: HTMLButtonElement) => void;
    openCanvas?: (theaterId: string, isOwner?: boolean, joinKey?: string) => void;
  }
}

const DEPLOYMENT_ACKNOWLEDGEMENT_KEY = 'narratron.deployAcknowledgement.v1';
const CANVAS_RETURN_SUGGESTION_KEY = 'narratron.canvasReturnSuggestion.v1';

const HTML_ESCAPE_MAP: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#039;',
};

function getEl(id: string): HTMLElement | null {
  return document.getElementById(id);
}

function getInput(id: string): HTMLInputElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLInputElement ? el : null;
}

function getTextArea(id: string): HTMLTextAreaElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLTextAreaElement ? el : null;
}

function getSelect(id: string): HTMLSelectElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLSelectElement ? el : null;
}

function getButton(id: string): HTMLButtonElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLButtonElement ? el : null;
}

function getDetails(id: string): HTMLDetailsElement | null {
  const el = document.getElementById(id);
  return el instanceof HTMLDetailsElement ? el : null;
}

function escapeHtml(value: unknown): string {
  if (!value) return '';
  return String(value).replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char] ?? char);
}

function getErrorMessage(error: unknown, fallback = 'Unknown error'): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

function getDetailString(payload: unknown): string | undefined {
  if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
    const detail = (payload as DetailPayload).detail;
    return typeof detail === 'string' && detail ? detail : undefined;
  }
  return undefined;
}

function getDefaultTheaterName(): string {
  const day = new Date().toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  return `Theater ${day}`;
}

function resolveTheaterUrl(theaterId: string): string {
  if (window.NarratronNavigation) {
    return window.NarratronNavigation.theaterUrl(theaterId);
  }
  return `/canvas?theater_id=${encodeURIComponent(theaterId)}`;
}

function extractAuthChangedUser(detail: unknown): CreationAuthUser | null {
  if (typeof detail === 'object' && detail !== null && 'user' in detail) {
    const user = (detail as { readonly user?: unknown }).user;
    if (typeof user === 'object' && user !== null) {
      return user as CreationAuthUser;
    }
  }
  return null;
}

/** Wire up the Theater Deployer page controls, modals, pricing calculator, and theater table. */
export function initializeTheaterCreation(): TheaterCreationController | null {
  if (!document.getElementById('createTheaterPanel') || !document.getElementById('theatersTableBody')) {
    return null;
  }

  let currentUser: CreationAuthUser | null = window.currentUser ?? null;
  const defaultTheaterName = getDefaultTheaterName();
  const theaterNameInput = getInput('theaterName');
  if (theaterNameInput) {
    theaterNameInput.value = defaultTheaterName;
  }

  const deployQuery = new URLSearchParams(window.location.search);
  const requestedCreationPath = deployQuery.get('creation_path');
  let currentCreationPath: CreationPath =
    requestedCreationPath === 'adventure' ? 'adventure' : 'blank';

  let theaterDefaultConfigLoaded = false;
  let theaterDefaultConfigRequest: Promise<void> | null = null;
  let advancedConfigIsCanonical = false;
  let availableAdventurePresets: readonly AdventurePreset[] = [];
  let pendingCanvasTheaterId: string | null = null;
  let theaterDeletionConfirmed = false;
  window.theaterDeletionConfirmed = false;

  function hasDeploymentAcknowledgement(): boolean {
    return localStorage.getItem(DEPLOYMENT_ACKNOWLEDGEMENT_KEY) === 'accepted';
  }

  function updateDeploymentAcknowledgementButton(): void {
    const safeContent = getInput('safeContentAcknowledgement')?.checked ?? false;
    const referenceRights = getInput('referenceRightsAcknowledgement')?.checked ?? false;
    const button = getButton('deploymentAcknowledgementButton');
    if (button) {
      button.disabled = !(safeContent && referenceRights);
    }
  }

  function showDeploymentAcknowledgement(): void {
    if (!hasDeploymentAcknowledgement()) {
      getEl('deploymentAcknowledgementModal')?.classList.add('active');
    }
  }

  function acceptDeploymentAcknowledgement(): void {
    const safeContent = getInput('safeContentAcknowledgement')?.checked ?? false;
    const referenceRights = getInput('referenceRightsAcknowledgement')?.checked ?? false;
    if (!safeContent || !referenceRights) return;

    localStorage.setItem(DEPLOYMENT_ACKNOWLEDGEMENT_KEY, 'accepted');
    getEl('deploymentAcknowledgementModal')?.classList.remove('active');
  }

  function dismissCanvasReturnBanner(): void {
    getEl('canvasReturnBanner')?.classList.remove('visible');
  }

  function selectCreationPath(mode: string): void {
    currentCreationPath = mode === 'adventure' ? 'adventure' : 'blank';

    const cardBlank = getEl('pathCardBlank');
    const cardAdventure = getEl('pathCardAdventure');
    const secBlank = getEl('pathSectionBlank');
    const secAdventure = getEl('pathSectionAdventure');
    const deployBtnText = getEl('deployBtnText');
    const theaterNameConfig = getEl('theaterNameConfig');

    for (const card of [cardBlank, cardAdventure]) {
      card?.classList.remove('active');
    }
    for (const sec of [secBlank, secAdventure]) {
      if (sec) sec.style.display = 'none';
    }
    if (theaterNameConfig) theaterNameConfig.style.display = 'block';

    if (currentCreationPath === 'adventure') {
      cardAdventure?.classList.add('active');
      if (secAdventure) secAdventure.style.display = 'block';
      if (deployBtnText) deployBtnText.innerText = 'Start Adventure';
    } else {
      cardBlank?.classList.add('active');
      if (secBlank) secBlank.style.display = 'block';
      if (deployBtnText) deployBtnText.innerText = '🚀 Deploy Theater';
    }
  }

  function showCanvasReturnSuggestion(): void {
    const params = new URLSearchParams(window.location.search);
    if (params.get('from') !== 'canvas' || localStorage.getItem(CANVAS_RETURN_SUGGESTION_KEY)) {
      return;
    }

    localStorage.setItem(CANVAS_RETURN_SUGGESTION_KEY, 'seen');
    selectCreationPath('blank');
    getEl('canvasReturnBanner')?.classList.add('visible');
    params.delete('from');
    const query = params.toString();
    window.history.replaceState(
      {},
      document.title,
      `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
    );
  }

  function onAdventurePresetSelected(): void {
    const select = getSelect('adventurePresetSelect');
    const card = getEl('adventurePresetCard');
    const selectedId = select ? select.value : '';

    if (!selectedId) {
      if (card) card.style.display = 'none';
      return;
    }

    const adv = availableAdventurePresets.find((a) => a.id === selectedId);
    if (!adv) {
      if (card) card.style.display = 'none';
      return;
    }

    if (card) {
      card.style.display = 'block';
      const banner = getEl('presetCardBanner');
      const genre = getEl('presetCardGenre');
      const title = getEl('presetCardTitle');
      const desc = getEl('presetCardDesc');
      const pills = getEl('presetCardPills');

      if (banner) {
        banner.style.backgroundImage = `url('/api/adventures/${encodeURIComponent(adv.id)}/cover')`;
      }
      if (genre) genre.innerText = adv.genre || 'Adventure';
      if (title) title.innerText = adv.title || adv.id;
      if (desc) desc.innerText = adv.description || '';
      if (pills) {
        const items: string[] = [];
        if (adv.lore_count) items.push(`<span>📜 ${adv.lore_count} Lore Docs</span>`);
        if (adv.track_count) items.push(`<span>🎵 ${adv.track_count} Soundtracks</span>`);
        if (adv.reference_count) items.push(`<span>🖼️ ${adv.reference_count} References</span>`);
        if (adv.difficulty) items.push(`<span>⚡ ${escapeHtml(adv.difficulty)}</span>`);
        pills.innerHTML = items.join(' • ');
      }
    }

    const nameInput = getInput('theaterName');
    if (nameInput && (nameInput.value === defaultTheaterName || !nameInput.value.trim())) {
      nameInput.value = adv.title || adv.id;
    }
  }

  async function loadAdventurePresets(): Promise<void> {
    const select = getSelect('adventurePresetSelect');
    if (!select) return;
    try {
      const res = await fetch('/api/adventures');
      if (!res.ok) return;
      const loaded = (await res.json()) as AdventurePreset[];
      availableAdventurePresets = Array.isArray(loaded) ? loaded : [];

      select.innerHTML =
        '<option value="">✨ Custom / Blank Adventure (No Preset)</option>' +
        availableAdventurePresets
          .map(
            (adv) =>
              `<option value="${escapeHtml(adv.id)}">${escapeHtml(adv.title || adv.id)} (${escapeHtml(adv.genre || 'Adventure')})</option>`,
          )
          .join('');

      const presetParam = deployQuery.get('preset');
      if (presetParam) {
        select.value = presetParam;
        onAdventurePresetSelected();
      }
    } catch (error: unknown) {
      console.warn('Failed to load adventure presets:', error);
    }
  }

  async function loadTheaterDefaultConfig(): Promise<void> {
    if (theaterDefaultConfigLoaded) return;
    if (theaterDefaultConfigRequest) return theaterDefaultConfigRequest;

    const editor = getTextArea('theaterConfigEditor');
    const status = getEl('theaterConfigEditorStatus');
    if (!editor) return;

    theaterDefaultConfigRequest = (async () => {
      editor.value = 'Loading theater_default.yaml...';
      editor.disabled = true;
      if (status) status.textContent = 'Loading theater_default.yaml...';

      try {
        const response = await fetch('/api/theaters/default-config');
        const data = (await response.json()) as DefaultConfigResponse;
        if (!response.ok) {
          throw new Error(getDetailString(data) || 'Failed to load theater_default.yaml');
        }
        editor.value = data.config_yaml || '';
        theaterDefaultConfigLoaded = true;
        if (status) {
          status.textContent =
            'Loaded from theater_default.yaml. Changes apply only to this new theater.';
        }
      } catch (error: unknown) {
        editor.value = '';
        if (status) {
          status.textContent = getErrorMessage(error, 'Could not load theater_default.yaml.');
        }
      } finally {
        editor.disabled = false;
        theaterDefaultConfigRequest = null;
      }
    })();
    return theaterDefaultConfigRequest;
  }

  async function deployNewTheater(): Promise<void> {
    currentUser = window.currentUser ?? currentUser;
    if (!hasDeploymentAcknowledgement()) {
      showDeploymentAcknowledgement();
      return;
    }

    if (!currentUser) {
      window.openAuthModal?.('login');
      return;
    }

    const enteredName = getInput('theaterName')?.value.trim() ?? '';
    const name =
      !enteredName || enteredName === defaultTheaterName ? getDefaultTheaterName() : enteredName;
    const style = getTextArea('theaterStyle')?.value.trim() ?? '';
    const specialInstructions = getTextArea('theaterSpecialInstructions')?.value.trim() ?? '';
    const deployBtn = getButton('deployBtn');
    const deploySpinner = getEl('deploySpinner');
    const deployBtnText = getEl('deployBtnText');

    if (deployBtn) deployBtn.disabled = true;
    if (deploySpinner) deploySpinner.style.display = 'inline-block';
    if (deployBtnText) deployBtnText.innerText = 'Deploying Theater...';

    try {
      if (theaterDefaultConfigRequest) await theaterDefaultConfigRequest;
      const formData = new FormData();
      formData.append('name', name);
      formData.append('creation_mode', currentCreationPath);
      const useBlankConfig = currentCreationPath === 'blank';
      if (useBlankConfig) {
        formData.append('agent_style', style);
        formData.append('agent_special_instructions', specialInstructions);
        formData.append('advanced_config_canonical', String(advancedConfigIsCanonical));
        formData.append(
          'use_generated_music',
          getInput('cfgUseGeneratedMusic')?.checked ? 'true' : 'false',
        );
        formData.append(
          'enable_image_generation',
          getInput('cfgImageGeneration')?.checked ? 'true' : 'false',
        );
        formData.append(
          'enable_scene_animations',
          getInput('cfgSceneAnimations')?.checked ? 'true' : 'false',
        );
        formData.append(
          'enable_interactive_canvas',
          getInput('cfgInteractiveCanvas')?.checked ? 'true' : 'false',
        );
      }
      formData.append(
        'enable_adventure_mode',
        currentCreationPath === 'adventure' ||
          (useBlankConfig && Boolean(getInput('cfgAdventureMode')?.checked))
          ? 'true'
          : 'false',
      );
      if (currentCreationPath === 'adventure') {
        formData.append(
          'story_planning_style',
          getTextArea('storyPlanningStyle')?.value.trim() ?? '',
        );
      }

      const presetAdventureId = getSelect('adventurePresetSelect')?.value || '';
      if (currentCreationPath === 'adventure' && presetAdventureId) {
        formData.append('preset_adventure_id', presetAdventureId);
      }

      const theaterConfigYaml = getTextArea('theaterConfigEditor')?.value.trim() ?? '';
      if (useBlankConfig && advancedConfigIsCanonical && theaterConfigYaml) {
        formData.append('advanced_config', theaterConfigYaml);
      }

      const response = await fetch('/api/theaters/create-and-deploy', {
        method: 'POST',
        body: formData,
      });

      let result: DeployTheaterResponse = {};
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        try {
          result = (await response.json()) as DeployTheaterResponse;
        } catch {
          result = {};
        }
      } else {
        const text = await response.text();
        result = { detail: text || response.statusText || `Server error (${response.status})` };
      }

      if (response.ok && result.theater_id) {
        window.location.href = resolveTheaterUrl(result.theater_id);
      } else {
        alert(
          'Error deploying theater: ' +
            (getDetailString(result) || `Server error (${response.status})`),
        );
      }
    } catch (err: unknown) {
      console.error(err);
      alert('Failed to deploy theater: ' + getErrorMessage(err));
    } finally {
      if (deployBtn) deployBtn.disabled = false;
      if (deploySpinner) deploySpinner.style.display = 'none';
      if (deployBtnText) deployBtnText.innerText = '🚀 Deploy Theater';
    }
  }

  function copyJoinKey(key: string, btn: HTMLButtonElement): void {
    void navigator.clipboard.writeText(key).then(() => {
      const orig = btn.innerText;
      btn.innerText = '✓ Copied';
      window.setTimeout(() => {
        btn.innerText = orig;
      }, 2000);
    });
  }

  function openCanvas(theaterId: string, isOwner = false, _joinKey = ''): void {
    if (window.NarratronNavigation?.isMobile()) {
      window.location.href = window.NarratronNavigation.theaterUrl(theaterId);
      return;
    }
    if (isOwner) {
      window.location.href = `/canvas?theater_id=${encodeURIComponent(theaterId)}`;
      return;
    }
    pendingCanvasTheaterId = theaterId;
    const displayEl = getEl('promptTheaterIdDisplay');
    if (displayEl) displayEl.textContent = theaterId;
    const keyInput = getInput('canvasJoinKeyInput');
    if (keyInput) keyInput.value = '';
    const errEl = getEl('joinKeyModalError');
    if (errEl) errEl.style.display = 'none';
    getEl('joinKeyModal')?.classList.add('active');
  }

  function closeJoinKeyModal(): void {
    getEl('joinKeyModal')?.classList.remove('active');
    pendingCanvasTheaterId = null;
  }

  async function submitCanvasJoinKey(): Promise<void> {
    const key = getInput('canvasJoinKeyInput')?.value.trim() ?? '';
    const err = getEl('joinKeyModalError');
    if (err) err.style.display = 'none';

    if (!key) {
      if (err) {
        err.textContent = 'Please enter a valid Join Key.';
        err.style.display = 'block';
      }
      return;
    }

    try {
      const res = await fetch('/api/theaters/resolve-join-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ join_key: key }),
      });
      const data = (await res.json()) as ResolveJoinKeyResponse;
      if (res.ok && data.theater_id) {
        if (data.theater_id !== pendingCanvasTheaterId) {
          if (err) {
            err.textContent = `This Join Key belongs to a different theater (${data.name || data.theater_id}).`;
            err.style.display = 'block';
          }
          return;
        }
        closeJoinKeyModal();
        window.location.href = resolveTheaterUrl(data.theater_id);
      } else if (err) {
        err.textContent = getDetailString(data) || 'Invalid Join Key.';
        err.style.display = 'block';
      }
    } catch (error: unknown) {
      if (err) {
        err.textContent = 'Error validating Join Key: ' + getErrorMessage(error);
        err.style.display = 'block';
      }
    }
  }

  async function saveTheaterTitle(theaterId: string): Promise<void> {
    const input = getInput(`editTitleInput-${theaterId}`);
    if (!input) return;
    const newName = input.value.trim();
    if (!newName) {
      alert('Theater title cannot be empty.');
      return;
    }
    try {
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/retitle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      const data = (await res.json()) as RetitleResponse;
      if (res.ok && data.status === 'ok') {
        await loadTheaters();
      } else {
        alert('Error updating title: ' + (getDetailString(data) || 'Failed to rename theater.'));
      }
    } catch (error: unknown) {
      alert('Failed to update title: ' + getErrorMessage(error));
    }
  }

  function startEditingTitle(theaterId: string, currentName: string): void {
    const cell = getEl(`nameCell-${theaterId}`);
    if (!cell) return;
    cell.replaceChildren();

    const input = document.createElement('input');
    input.type = 'text';
    input.id = `editTitleInput-${theaterId}`;
    input.value = currentName;
    input.style.cssText =
      'padding:4px 8px; font-size:0.85rem; border-radius:6px; background:rgba(11,13,18,0.9); border:1px solid var(--accent-primary); color:var(--text-primary); width:180px;';
    input.addEventListener('keydown', (event: KeyboardEvent) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        void saveTheaterTitle(theaterId);
      } else if (event.key === 'Escape') {
        void loadTheaters();
      }
    });

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'btn-sm btn-success';
    saveBtn.style.cssText = 'padding:2px 8px; font-size:0.75rem;';
    saveBtn.textContent = 'Save';
    saveBtn.addEventListener('click', () => {
      void saveTheaterTitle(theaterId);
    });

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn-sm btn-secondary';
    cancelBtn.style.cssText = 'padding:2px 8px; font-size:0.75rem;';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => {
      void loadTheaters();
    });

    cell.append(input, saveBtn, cancelBtn);
    input.focus();
    input.select();
  }

  async function destroyTheater(theaterId: string): Promise<void> {
    if (!window.theaterDeletionConfirmed && !theaterDeletionConfirmed) {
      if (!confirm(`Are you sure you want to delete theater ${theaterId}?`)) return;
      window.theaterDeletionConfirmed = true;
      theaterDeletionConfirmed = true;
    }
    try {
      try {
        await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/agent/stop`, {
          method: 'POST',
        });
      } catch (stopErr: unknown) {
        console.warn('Failed to stop agent session prior to theater deletion:', stopErr);
      }
      const res = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = (await res.json()) as DetailPayload;
        alert(getDetailString(data) || 'Failed to destroy theater.');
        return;
      }
      await loadTheaters();
    } catch (err: unknown) {
      alert('Error destroying theater: ' + getErrorMessage(err));
    }
  }

  async function loadTheaters(): Promise<void> {
    currentUser = window.currentUser ?? currentUser;
    const body = getEl('theatersTableBody');
    if (!body) return;
    const panelTitle = getEl('theatersPanelTitle');
    try {
      const res = await fetch('/api/theaters');
      if (res.status === 401) {
        if (panelTitle) panelTitle.innerHTML = '<span>👤</span> Personal Sessions';
        body.innerHTML =
          '<tr><td colspan="4" class="empty-state">Please log in to view your theaters.</td></tr>';
        return;
      }
      const theaters = (await res.json()) as TheaterSummary[] | null;

      if (panelTitle) {
        panelTitle.innerHTML = currentUser
          ? '<span>👤</span> Personal Sessions'
          : '<span>🌐</span> Global Theaters';
      }

      let displayTheaters = Array.isArray(theaters) ? [...theaters] : [];
      if (currentUser) {
        displayTheaters = displayTheaters.filter((s) => Boolean(s.is_owner));
      }

      displayTheaters.sort((a, b) => {
        const timeA = a.last_used_at || a.created_at || '';
        const timeB = b.last_used_at || b.created_at || '';
        return timeB.localeCompare(timeA);
      });

      if (displayTheaters.length === 0) {
        const emptyMsg = currentUser
          ? 'No personal sessions found. Create your first theater above!'
          : 'No global theaters deployed yet.';
        body.innerHTML = `<tr><td colspan="4" class="empty-state">${emptyMsg}</td></tr>`;
        return;
      }

      body.replaceChildren();
      for (const s of displayTheaters) {
        const tr = document.createElement('tr');
        const refCount = s.mounted_references ? s.mounted_references.length : 0;
        const plCount = s.mounted_playlists ? Object.keys(s.mounted_playlists).length : 0;

        // Name cell
        const nameTd = document.createElement('td');
        const nameCell = document.createElement('div');
        nameCell.id = `nameCell-${s.theater_id}`;
        nameCell.style.cssText = 'display:flex; align-items:center; gap:6px;';
        const strongName = document.createElement('strong');
        strongName.textContent = s.name;
        nameCell.append(strongName);
        if (s.is_owner) {
          const ownerSpan = document.createElement('span');
          ownerSpan.style.cssText = 'color:#a78bfa; font-size:0.75rem;';
          ownerSpan.textContent = '(Owner)';
          const editBtn = document.createElement('button');
          editBtn.type = 'button';
          editBtn.className = 'copy-key-btn';
          editBtn.title = 'Retitle Theater';
          editBtn.style.cssText = 'margin-left:6px; padding:2px 6px; font-size:0.75rem;';
          editBtn.textContent = '✏️ Edit';
          editBtn.addEventListener('click', () => startEditingTitle(s.theater_id, s.name));
          nameCell.append(' ', ownerSpan, ' ', editBtn);
        }
        nameTd.append(nameCell);

        // Join key cell
        const keyTd = document.createElement('td');
        const keyBadge = document.createElement('span');
        keyBadge.className = 'badge join-key-badge';
        keyBadge.textContent = s.join_key ?? '';
        keyTd.append(keyBadge);
        if (s.is_owner && s.join_key && !s.join_key.includes('Owner')) {
          const joinKey = s.join_key;
          const copyBtn = document.createElement('button');
          copyBtn.type = 'button';
          copyBtn.className = 'copy-key-btn';
          copyBtn.textContent = '📋 Copy';
          copyBtn.addEventListener('click', () => copyJoinKey(joinKey, copyBtn));
          keyTd.append(copyBtn);
        }

        // Assets cell
        const assetsTd = document.createElement('td');
        assetsTd.textContent = `${refCount} refs, ${plCount} playlists`;

        // Actions cell
        const actionsTd = document.createElement('td');
        const launchBtn = document.createElement('button');
        launchBtn.type = 'button';
        launchBtn.className = 'btn-sm btn-success theater-action-icon canvas-launch';
        launchBtn.title = 'Launch Canvas';
        launchBtn.setAttribute('aria-label', 'Launch Canvas');
        const launchIcon = document.createElement('span');
        launchIcon.setAttribute('aria-hidden', 'true');
        launchIcon.textContent = '▶';
        launchBtn.append(launchIcon);
        launchBtn.addEventListener('click', () =>
          openCanvas(s.theater_id, Boolean(s.is_owner), s.join_key || ''),
        );
        actionsTd.append(launchBtn);

        const remoteLink = document.createElement('a');
        remoteLink.href = `/remote?theater_id=${encodeURIComponent(s.theater_id)}`;
        remoteLink.className = 'btn-sm btn-success theater-action-icon remote-launch';
        remoteLink.title = 'Open Mobile';
        remoteLink.setAttribute('aria-label', 'Open Mobile');
        const remoteIcon = document.createElement('span');
        remoteIcon.setAttribute('aria-hidden', 'true');
        remoteIcon.textContent = '📱';
        const remoteLabel = document.createElement('span');
        remoteLabel.textContent = 'Open Mobile';
        remoteLink.append(remoteIcon, remoteLabel);
        actionsTd.append(' ', remoteLink);

        const exportLink = document.createElement('a');
        exportLink.href = `/api/theaters/${encodeURIComponent(s.theater_id)}/export-assets`;
        exportLink.className = 'btn-sm btn-secondary theater-action-icon export-theater-btn';
        exportLink.title = 'Export Assets';
        exportLink.setAttribute('aria-label', 'Export Assets');
        exportLink.setAttribute('download', '');
        const exportIcon = document.createElement('span');
        exportIcon.setAttribute('aria-hidden', 'true');
        exportIcon.textContent = '📦';
        exportLink.append(exportIcon);
        actionsTd.append(' ', exportLink);

        if (s.is_owner) {
          const builderLink = document.createElement('a');
          builderLink.href = `/theater-editor?theater_id=${encodeURIComponent(s.theater_id)}`;
          builderLink.className = 'btn-sm btn-secondary theater-action-icon builder-launch';
          builderLink.title = 'Edit in Builder';
          builderLink.setAttribute('aria-label', 'Edit in Builder');
          const builderIcon = document.createElement('span');
          builderIcon.setAttribute('aria-hidden', 'true');
          builderIcon.textContent = '✦';
          builderLink.append(builderIcon);
          actionsTd.append(' ', builderLink);

          const destroyBtn = document.createElement('button');
          destroyBtn.type = 'button';
          destroyBtn.className = 'btn-sm btn-danger destroy-theater-btn';
          destroyBtn.textContent = 'Destroy';
          destroyBtn.addEventListener('click', () => {
            void destroyTheater(s.theater_id);
          });
          actionsTd.append(' ', destroyBtn);
        }

        tr.append(nameTd, keyTd, assetsTd, actionsTd);
        body.append(tr);
      }
    } catch (err: unknown) {
      console.error('Error loading theaters:', err);
      body.innerHTML =
        '<tr><td colspan="4" class="empty-state">Error loading active theaters.</td></tr>';
    }
  }

  async function calculateLivePricingEstimate(): Promise<void> {
    const toolCallsInput = getInput('calcToolCallsInput');
    const imagesInput = getInput('calcImagesInput');
    const musicInput = getInput('calcMusicInput');
    const storyTurnsInput = getInput('calcStoryTurnsInput');
    const characterVoicingInput = getInput('calcCharacterVoicingInput');
    const interactiveCanvasInput = getInput('calcInteractiveCanvasInput');
    const storageInput = getInput('calcStorageInput');

    if (!toolCallsInput || !imagesInput || !storageInput) return;

    const toolCalls = parseInt(toolCallsInput.value, 10) || 0;
    const images = parseInt(imagesInput.value, 10) || 0;
    const music = musicInput ? parseInt(musicInput.value, 10) || 0 : 0;
    const storyTurns = storyTurnsInput ? parseInt(storyTurnsInput.value, 10) || 0 : 0;
    const voicedTurns = characterVoicingInput?.checked ? storyTurns : 0;
    const interactiveCanvasUses = interactiveCanvasInput
      ? parseInt(interactiveCanvasInput.value, 10) || 0
      : 0;
    const storageGb = parseFloat(storageInput.value) || 0;

    try {
      const queryParams = new URLSearchParams({
        live_agent_tool_calls: String(toolCalls),
        images_created: String(images),
        music_created: String(music),
        story_plans: String(storyTurns),
        character_voiced_turns: String(voicedTurns),
        interactive_canvas_used: String(interactiveCanvasUses),
        gb_amount: String(storageGb),
        days: '30',
      });
      const res = await fetch(`/api/pricing?${queryParams.toString()}`);
      if (res.ok) {
        const data = (await res.json()) as PricingRatesResponse;
        const calc = data.calculation;
        const usageCredits = calc?.usage_credits || 0.0;
        const storageCredits = calc?.storage_credits || 0.0;
        const totalCredits = usageCredits + storageCredits;
        const totalUsd = totalCredits * (data.usd_per_credit || 0.05);

        const totalCreditsEl = getEl('calcTotalCredits');
        if (totalCreditsEl) totalCreditsEl.innerText = `⚡ ${totalCredits.toFixed(1)} Cr`;
        const totalUsdEl = getEl('calcTotalUsd');
        if (totalUsdEl) totalUsdEl.innerText = `~$${totalUsd.toFixed(2)} USD`;
        const breakdownEl = getEl('calcBreakdownText');
        if (breakdownEl) {
          const voicingDetail = voicedTurns ? ' + character voicing' : '';
          breakdownEl.innerText = `${toolCalls} live-agent tool calls + ${images} images + ${music} music tracks + ${storyTurns} story turns${voicingDetail} + ${interactiveCanvasUses} canvas tool uses + ${storageGb} GB/mo`;
        }
      }
    } catch (error: unknown) {
      console.error('Error calculating live pricing estimate:', error);
    }
  }

  async function fetchPricingRates(): Promise<void> {
    const statusText = getEl('pricingLiveStatusText');
    const updatedText = getEl('pricingLastUpdated');
    if (statusText) statusText.innerText = 'Fetching live rates...';

    try {
      const response = await fetch('/api/pricing');
      if (!response.ok) {
        throw new Error(`Failed to fetch pricing rates: ${response.status}`);
      }
      const data = (await response.json()) as PricingRatesResponse;

      const setRate = (id: string, value: string): void => {
        const node = getEl(id);
        if (node) node.innerText = value;
      };

      setRate('rateCreditsPerUsd', data.credits_per_usd.toFixed(1));
      setRate('rateUsdPerCredit', `$${data.usd_per_credit.toFixed(2)}`);
      setRate('rateLiveAgentToolCall', data.live_agent_tool_call_credit_rate.toFixed(3));
      setRate(
        'rateLiveAgentToolCallUsd',
        `$${(data.live_agent_tool_call_credit_rate * data.usd_per_credit).toFixed(4)}`,
      );
      const storyPlanningRate = data.story_planning_credit_rate ?? 0.5;
      setRate('rateStoryPlanning', storyPlanningRate.toFixed(2));
      setRate('rateStoryPlanningUsd', `$${(storyPlanningRate * data.usd_per_credit).toFixed(2)}`);
      const characterVoicingRate = data.character_voicing_turn_credit_rate ?? 0.25;
      setRate('rateCharacterVoicing', characterVoicingRate.toFixed(2));
      setRate(
        'rateCharacterVoicingUsd',
        `$${(characterVoicingRate * data.usd_per_credit).toFixed(2)}`,
      );
      const interactiveCanvasRate =
        data.interactive_canvas_tool_credit_rate ?? data.interactive_canvas_credit_rate ?? 0.25;
      setRate('rateInteractiveCanvas', interactiveCanvasRate.toFixed(2));
      setRate(
        'rateInteractiveCanvasUsd',
        `$${(interactiveCanvasRate * data.usd_per_credit).toFixed(2)}`,
      );
      setRate('rateImage', data.image_credit_rate.toFixed(1));
      setRate('rateImageUsd', `$${(data.image_credit_rate * data.usd_per_credit).toFixed(2)}`);
      const musicRate = data.music_credit_rate || 2.0;
      setRate('rateMusic', musicRate.toFixed(1));
      setRate('rateMusicUsd', `$${(musicRate * data.usd_per_credit).toFixed(2)}`);
      const layeredAnimationRate = data.layered_animation_credit_rate ?? 6.5;
      setRate('rateLayeredAnimation', layeredAnimationRate.toFixed(1));
      setRate(
        'rateLayeredAnimationUsd',
        `$${(layeredAnimationRate * data.usd_per_credit).toFixed(2)}`,
      );
      setRate('rateStorageDaily', data.storage_gb_daily_rate.toFixed(3));
      if (statusText) statusText.innerText = 'Live Rates Active';
      if (updatedText) {
        updatedText.innerText = `Updated: ${new Date().toLocaleTimeString()}`;
      }

      await calculateLivePricingEstimate();
    } catch (err: unknown) {
      console.error('Error loading pricing rates:', err);
      if (statusText) statusText.innerText = 'Error loading rates';
    }
  }

  function openPricingModal(): void {
    const modal = getEl('pricingModal');
    if (modal) {
      modal.classList.add('active');
      void fetchPricingRates();
    }
  }

  function closePricingModal(): void {
    const modal = getEl('pricingModal');
    if (modal) {
      modal.classList.remove('active');
    }
  }

  async function checkPaymentStatusInURL(): Promise<void> {
    const urlParams = new URLSearchParams(window.location.search);
    const paymentStatus = urlParams.get('payment');
    const sessionId = urlParams.get('session_id');

    if (paymentStatus === 'success' && sessionId) {
      try {
        const res = await fetch(
          `/api/payments/verify-session?session_id=${encodeURIComponent(sessionId)}`,
        );
        const data = (await res.json()) as VerifyPaymentResponse;
        if (res.ok && data.verified) {
          await window.checkAuthStatus?.();
          alert(`🎉 Payment Successful! Added +${data.credits_added || 0} Credits to your account.`);
          window.history.replaceState({}, document.title, window.location.pathname);
        }
      } catch (e: unknown) {
        console.error('Error verifying Stripe session:', e);
      }
    }
  }

  // Expose globals required by shared topbar, credit purchase modal, and inline handlers/tests.
  window.openPricingModal = openPricingModal;
  window.closePricingModal = closePricingModal;
  window.fetchPricingRates = fetchPricingRates;
  window.calculateLivePricingEstimate = calculateLivePricingEstimate;
  window.dismissCanvasReturnBanner = dismissCanvasReturnBanner;
  window.selectCreationPath = selectCreationPath;
  window.onAdventurePresetSelected = onAdventurePresetSelected;
  window.deployNewTheater = deployNewTheater;
  window.loadTheaters = loadTheaters;
  window.acceptDeploymentAcknowledgement = acceptDeploymentAcknowledgement;
  window.submitCanvasJoinKey = submitCanvasJoinKey;
  window.closeJoinKeyModal = closeJoinKeyModal;
  window.destroyTheater = destroyTheater;
  window.deleteTheater = destroyTheater;
  window.startEditingTitle = startEditingTitle;
  window.saveTheaterTitle = saveTheaterTitle;
  window.copyJoinKey = copyJoinKey;
  window.openCanvas = openCanvas;

  // Wire static DOM event listeners cleanly.
  getEl('safeContentAcknowledgement')?.addEventListener(
    'change',
    updateDeploymentAcknowledgementButton,
  );
  getEl('referenceRightsAcknowledgement')?.addEventListener(
    'change',
    updateDeploymentAcknowledgementButton,
  );
  getEl('deploymentAcknowledgementButton')?.addEventListener(
    'click',
    acceptDeploymentAcknowledgement,
  );
  getEl('dismissCanvasReturnBtn')?.addEventListener('click', dismissCanvasReturnBanner);
  getEl('pathCardBlank')?.addEventListener('click', () => selectCreationPath('blank'));
  getEl('pathCardAdventure')?.addEventListener('click', () => selectCreationPath('adventure'));
  getEl('adventurePresetSelect')?.addEventListener('change', onAdventurePresetSelected);
  getEl('deployBtn')?.addEventListener('click', () => {
    void deployNewTheater();
  });
  getEl('refreshTheatersBtn')?.addEventListener('click', () => {
    void loadTheaters();
  });
  getEl('canvasJoinKeyInput')?.addEventListener('keypress', (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      void submitCanvasJoinKey();
    }
  });
  getEl('joinKeySubmitBtn')?.addEventListener('click', () => {
    void submitCanvasJoinKey();
  });
  getEl('joinKeyCancelBtn')?.addEventListener('click', closeJoinKeyModal);
  getEl('closePricingHeaderBtn')?.addEventListener('click', closePricingModal);
  getEl('refreshPricingRatesBtn')?.addEventListener('click', () => {
    void fetchPricingRates();
  });
  getEl('pricingBuyCreditsBtn')?.addEventListener('click', () => {
    closePricingModal();
    window.openBuyCreditsModal?.();
  });
  getEl('closePricingFooterBtn')?.addEventListener('click', closePricingModal);

  for (const calcInputId of [
    'calcStoryTurnsInput',
    'calcInteractiveCanvasInput',
    'calcToolCallsInput',
    'calcImagesInput',
    'calcMusicInput',
    'calcStorageInput',
  ]) {
    getEl(calcInputId)?.addEventListener('input', () => {
      void calculateLivePricingEstimate();
    });
  }
  getEl('calcCharacterVoicingInput')?.addEventListener('change', () => {
    void calculateLivePricingEstimate();
  });

  window.addEventListener('narratron:auth-changed', (event: Event) => {
    const detail = event instanceof CustomEvent ? event.detail : undefined;
    currentUser = extractAuthChangedUser(detail) ?? window.currentUser ?? null;
    void loadTheaters();
  });

  const simpleConfig = getDetails('simpleConfig');
  const advancedConfig = getDetails('advancedConfig');
  simpleConfig?.addEventListener('toggle', () => {
    if (simpleConfig.open) {
      if (advancedConfig) advancedConfig.open = false;
      advancedConfigIsCanonical = false;
    }
  });
  advancedConfig?.addEventListener('toggle', () => {
    if (advancedConfig.open) {
      if (simpleConfig) simpleConfig.open = false;
      advancedConfigIsCanonical = true;
      void loadTheaterDefaultConfig();
    }
  });

  document.addEventListener('click', (event: MouseEvent) => {
    const modal = getEl('pricingModal');
    if (modal && event.target === modal) {
      closePricingModal();
    }
  });

  if (deployQuery.get('adventure_mode') === 'true' || deployQuery.get('preset')) {
    const adventureModeInput = getInput('cfgAdventureMode');
    if (adventureModeInput) adventureModeInput.checked = true;
  }

  selectCreationPath(currentCreationPath);
  void loadAdventurePresets();

  showDeploymentAcknowledgement();
  showCanvasReturnSuggestion();
  void loadTheaters();
  void checkPaymentStatusInURL();

  return {
    selectCreationPath,
    onAdventurePresetSelected,
    deployNewTheater,
    loadTheaters,
    destroyTheater,
    openPricingModal,
    closePricingModal,
    fetchPricingRates,
    calculateLivePricingEstimate,
  };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initializeTheaterCreation();
  });
} else {
  initializeTheaterCreation();
}

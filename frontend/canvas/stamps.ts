import {
  DeleteStampDoodleAction,
  PlaceStampDoodleAction,
  TransformStampDoodleAction,
  type DoodleAction,
  type DoodleMessage,
  type DoodleTransformType,
  type StampAnnotationItem,
} from './doodle-actions.js';
import type { Point } from './doodle-types.js';

declare global {
  interface Window {
    isCanvasSelectionActive?: boolean;
  }
}

export interface StampUser {
  readonly id?: string | number | null;
  readonly username?: string | null;
}

export interface AuthStateResult {
  readonly authenticated?: boolean;
  readonly user?: StampUser | null;
}

export interface StampItem {
  readonly id: string | number;
  readonly name: string;
  readonly url: string;
}

export interface StampDragPayload {
  readonly stamp_id: string | number;
  readonly name?: string;
  readonly url: string;
}

export interface ReferenceItem {
  readonly name?: string | null;
}

export interface ReferenceEntry {
  readonly name: string;
  readonly kind: string;
}

export interface ReferenceSources {
  readonly characters?: readonly ReferenceItem[] | null;
  readonly scenes?: readonly ReferenceItem[] | null;
  readonly references?: readonly ReferenceItem[] | null;
  readonly stamps?: readonly ReferenceItem[] | null;
}

export interface PendingStampGeneration {
  readonly name: string;
  readonly prompt: string;
}

export interface StampCanvasItem {
  type: 'draw' | 'text' | 'stamp' | string;
  id?: string;
  stamp_id?: string | number;
  user_id?: string | number;
  canvas_asset_id?: string;
  managed_by?: string;
  url?: string;
  name?: string;
  x?: number;
  y?: number;
  size?: number;
  rotation?: number;
  client_message_id?: string;
}

export interface StampAnnotation extends StampAnnotationItem {
  type: 'stamp';
  id: string;
  stamp_id?: string | number;
  user_id?: string | number;
  canvas_asset_id?: string;
  managed_by?: string;
  url: string;
  name: string;
  x: number;
  y: number;
  size: number;
  rotation?: number;
  client_message_id?: string;
}

export interface DeleteTransaction {
  readonly stamps: StampCanvasItem[];
}

export interface StampGenerationResponse {
  readonly detail?: unknown;
  readonly stamps?: readonly StampItem[];
  readonly credits_charged?: number;
}

export interface CreateCanvasStampsControllerOptions {
  readonly doc?: Document;
  readonly canvas?: HTMLCanvasElement | null;
  readonly imgContainer?: HTMLElement | null;
  readonly stampLayer?: HTMLElement | null;
  readonly chatOpenStampsBtn?: HTMLElement | null;
  readonly chatPanelTabBtn?: HTMLElement | null;
  readonly chatUserBar?: HTMLElement | null;
  readonly stampManagerPane?: HTMLElement | null;
  readonly stampManagerGrid?: HTMLElement | null;
  readonly stampManagerEmpty?: HTMLElement | null;
  readonly stampEmptyMessage?: HTMLElement | null;
  readonly stampManagerCount?: HTMLElement | null;
  readonly stampManagerPermissionBanner?: HTMLElement | null;
  readonly panelBody?: HTMLElement | null;
  readonly assetSearchInput?: HTMLInputElement | null;
  readonly assetSearchEmpty?: HTMLElement | null;
  readonly stampManagerBody?: HTMLElement | null;
  readonly stampGenerationForm?: HTMLFormElement | null;
  readonly stampGenerationPrompt?: HTMLTextAreaElement | null;
  readonly stampGenerationStatus?: HTMLElement | null;
  readonly profileLink?: HTMLAnchorElement | null;
  readonly emptyProfileBtn?: HTMLAnchorElement | null;
  readonly getTheaterId: () => string | null;
  readonly getCurrentUser: () => StampUser | null;
  readonly setCurrentUser?: (user: StampUser | null) => void;
  readonly getMyUserId?: () => string | number | null;
  readonly getAuthState?: () => Promise<AuthStateResult>;
  readonly openAuthModal?: (mode: 'login' | 'signup') => void;
  readonly hasContributorPermission: () => boolean;
  readonly isOrator: () => boolean;
  readonly isOwner: () => boolean;
  readonly isDoodlesEnabled: () => boolean;
  readonly isPagedBack: () => boolean;
  readonly enableDoodles: () => void;
  readonly selectSelectCursor: () => void;
  readonly getDoodleActions: () => StampCanvasItem[];
  readonly redrawDoodles: () => void;
  readonly sendDoodleMessage: (message: DoodleMessage) => void;
  readonly rememberPendingEdit?: (clientMessageId: string, previous: StampCanvasItem) => void;
  readonly getActiveDeleteTransaction?: () => DeleteTransaction | null;
  readonly pushUndoAction?: (action: DoodleAction) => void;
  readonly getActiveTab?: () => string;
  readonly isPlaylistExpanded?: (playlistName: string) => boolean;
  readonly resolveImageUrl?: (url: string) => string;
  readonly getNormalizedDropPoint?: (event: MouseEvent) => Point | null;
  readonly getReferenceSources?: () => ReferenceSources;
  readonly onTabsVisibilityUpdate?: () => void;
  readonly onGenerationPermissionUpdate?: () => void;
  readonly onOpenNonStampTab?: (tab: string) => void;
  readonly getIsOpen?: () => boolean;
  readonly setIsOpen?: (open: boolean) => void;
  readonly getCachedUserStamps?: () => StampItem[] | null;
  readonly setCachedUserStamps?: (stamps: StampItem[] | null) => void;
  readonly getCachedTheaterStamps?: () => StampItem[];
  readonly setCachedTheaterStamps?: (stamps: StampItem[]) => void;
}

export interface CanvasStampsController {
  readonly pendingGenerations: Map<number, PendingStampGeneration>;
  isOpen(): boolean;
  setOpen(open: boolean): void;
  getCachedUserStamps(): StampItem[] | null;
  setCachedUserStamps(stamps: StampItem[] | null): void;
  getCachedTheaterStamps(): StampItem[];
  setCachedTheaterStamps(stamps: StampItem[]): void;
  getStampLoadError(): string;
  setStampLoadError(error: string): void;
  canPlaceStamps(): boolean;
  placeStampOnCanvas(stampData: StampDragPayload, normX: number, normY: number): void;
  bringStampToFront(stampId: string): void;
  selectStampAnnotation(action: StampCanvasItem): void;
  applyStampAnnotation(action: StampCanvasItem): void;
  moveStampAnnotation(
    action: StampCanvasItem,
    previous: StampCanvasItem | null,
    commit: boolean,
  ): void;
  removeStampAnnotation(action: { id?: string }): void;
  loadUserStamps(): Promise<void>;
  renderGrid(stamps?: readonly StampItem[]): void;
  updatePermissionBanner(): void;
  updateGenerationPermission(): void;
  updateGenerationStatus(message?: string): void;
  updateProfileLinks(user: StampUser | null | undefined): void;
  getStampUsageStorageKey(): string;
  getStampLastUsedTimestamp(stampId: string | number): number;
  recordStampUsage(stampId: string | number | null | undefined): void;
  applyAssetSearch(): void;
  getAvailableReferences(): ReferenceEntry[];
  extractTaggedReferences(promptText: string): string[];
  attachReferenceAutocomplete(textarea: HTMLTextAreaElement | null): void;
}

const STAMP_NAME_STOPWORDS = new Set(
  (
    'a an the and or but of for to in on at by with from into onto over under ' +
    'as is are was were be been being it its this that these those ' +
    'i me my we our you your he him his she her they them their ' +
    'some any each all no not so very can could should would will ' +
    'do does did have has had about up down out through between ' +
    'who whom whose what when where why how than then there here ' +
    'both few more most other such only own same too until while ' +
    'again further once during before after above below against off just now'
  ).split(' '),
);

function generateAnnotationId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function escapeCanvasReferenceRegex(str: string): string {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function toStampAnnotationItem(item: StampCanvasItem): StampAnnotationItem {
  return {
    type: item.type,
    id: item.id,
    stamp_id: item.stamp_id,
    url: item.url,
    name: item.name,
    x: Number(item.x ?? 0),
    y: Number(item.y ?? 0),
    size: Number(item.size ?? 80),
    rotation: item.rotation,
    user_id: item.user_id,
    canvas_asset_id: item.canvas_asset_id,
    managed_by: item.managed_by,
  };
}

/** Update header and empty-state profile links for the active signed-in user. */
export function updateStampManagerProfileLinks(
  user: StampUser | null | undefined,
  doc: Document = document,
): void {
  const profileLink = doc.getElementById('stamp-manager-profile-link') as HTMLAnchorElement | null;
  const emptyProfileBtn = doc.getElementById(
    'stamp-manager-empty-profile-btn',
  ) as HTMLAnchorElement | null;
  const targetUrl =
    user && user.username ? `/users/${encodeURIComponent(user.username)}` : '/profile';
  if (profileLink) {
    profileLink.href = targetUrl;
    profileLink.style.display = user && user.username ? 'inline-flex' : 'none';
  }
  if (emptyProfileBtn) {
    emptyProfileBtn.href = targetUrl;
  }
}

/** Derive a concise 1-3 word stamp name from a natural-language generation prompt. */
export function stampNameFromPrompt(prompt: string): string {
  const words = prompt.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) ?? [];
  const name = words
    .filter((word) => !STAMP_NAME_STOPWORDS.has(word.toLowerCase()))
    .slice(0, 3)
    .join(' ');
  return (name || 'Stamp').slice(0, 100);
}

/** Collect deduplicated reference entries from character, scene, reference, and stamp lists. */
export function collectTheaterReferences(sources: ReferenceSources): ReferenceEntry[] {
  const entries: ReferenceEntry[] = [];
  const seen = new Set<string>();
  const addEntry = (rawName: string | null | undefined, kind: string): void => {
    const name = String(rawName ?? '').trim();
    const key = name.toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    entries.push({ name, kind });
  };
  for (const item of sources.characters ?? []) addEntry(item?.name, 'character');
  for (const item of sources.scenes ?? []) addEntry(item?.name, 'scene');
  for (const item of sources.references ?? []) addEntry(item?.name, 'reference');
  for (const item of sources.stamps ?? []) addEntry(item?.name, 'stamp');
  return entries;
}

/** Extract up to 4 deduplicated `@reference` tags from a generation prompt. */
export function extractTaggedCanvasReferences(
  promptText: string,
  available: readonly ReferenceEntry[],
): string[] {
  if (!promptText || !promptText.includes('@')) return [];
  const sorted = [...available].sort((a, b) => b.name.length - a.name.length);
  const hits: Array<{ index: number; name: string }> = [];
  const occupied: Array<[number, number]> = [];
  const isOverlapping = (start: number, end: number): boolean =>
    occupied.some(([s, e]) => start < e && end > s);

  for (const entry of sorted) {
    const tokens = entry.name
      .trim()
      .split(/[\s_-]+/)
      .filter(Boolean)
      .map(escapeCanvasReferenceRegex);
    if (!tokens.length) continue;
    const pattern = new RegExp(
      `(?:^|[\\s([{"',;:])@(${tokens.join('[\\s_-]+')})(?=$|[\\s.,!?;:)\\]}"'])`,
      'gi',
    );
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(promptText)) !== null) {
      const matchedTag = match[1];
      const tagStart = match.index + match[0].lastIndexOf('@');
      const tagEnd = tagStart + 1 + matchedTag.length;
      if (!isOverlapping(tagStart, tagEnd)) {
        occupied.push([tagStart, tagEnd]);
        hits.push({ index: tagStart, name: entry.name });
      }
    }
  }

  const fallbackPattern = /(?:^|[\s([{"',;:])@([a-zA-Z0-9][\w-]*)/g;
  let fallbackMatch: RegExpExecArray | null;
  while ((fallbackMatch = fallbackPattern.exec(promptText)) !== null) {
    const token = fallbackMatch[1];
    const tagStart = fallbackMatch.index + fallbackMatch[0].lastIndexOf('@');
    const tagEnd = tagStart + 1 + token.length;
    if (!isOverlapping(tagStart, tagEnd)) {
      occupied.push([tagStart, tagEnd]);
      hits.push({ index: tagStart, name: token });
    }
  }

  hits.sort((a, b) => a.index - b.index);
  const result: string[] = [];
  const seen = new Set<string>();
  for (const hit of hits) {
    const key = hit.name.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      result.push(hit.name);
      if (result.length >= 4) break;
    }
  }
  return result;
}

/** Attach `@mention` autocomplete dropdown behavior to a prompt textarea. */
export function attachCanvasReferenceAutocomplete(
  textarea: HTMLTextAreaElement | null,
  getAvailableReferences: () => readonly ReferenceEntry[],
  doc: Document = document,
): void {
  if (!textarea || !textarea.parentElement || textarea.dataset.refAutocompleteAttached === 'true') {
    return;
  }
  textarea.dataset.refAutocompleteAttached = 'true';
  const container = textarea.parentElement;
  const menu = doc.createElement('div');
  menu.className = 'reference-mention-menu';
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  container.appendChild(menu);

  let filtered: ReferenceEntry[] = [];
  let activeIndex = 0;
  let mentionStart = -1;

  function closeMenu(): void {
    menu.hidden = true;
    menu.replaceChildren();
    filtered = [];
    activeIndex = 0;
    mentionStart = -1;
  }

  function applySelection(entry: ReferenceEntry | undefined): void {
    if (!entry || mentionStart < 0 || !textarea) return;
    const caret =
      typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
    const before = textarea.value.slice(0, mentionStart);
    const after = textarea.value.slice(caret);
    const insertion = `@${entry.name} `;
    textarea.value = before + insertion + after.replace(/^\s+/, '');
    const nextCaret = before.length + insertion.length;
    textarea.focus();
    textarea.setSelectionRange(nextCaret, nextCaret);
    closeMenu();
  }

  function renderMenu(): void {
    menu.replaceChildren();
    if (!filtered.length) {
      closeMenu();
      return;
    }
    menu.hidden = false;
    filtered.forEach((entry, idx) => {
      const option = doc.createElement('button');
      option.type = 'button';
      option.className = 'reference-mention-option' + (idx === activeIndex ? ' active' : '');
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', idx === activeIndex ? 'true' : 'false');
      option.dataset.referenceName = entry.name;
      option.dataset.referenceKind = entry.kind;
      const nameSpan = doc.createElement('span');
      nameSpan.className = 'reference-mention-name';
      nameSpan.textContent = `@${entry.name}`;
      const kindSpan = doc.createElement('span');
      kindSpan.className = 'reference-mention-kind';
      kindSpan.textContent = entry.kind;
      option.append(nameSpan, kindSpan);
      option.addEventListener('mousedown', (event) => {
        event.preventDefault();
        applySelection(entry);
      });
      option.addEventListener('click', (event) => {
        event.preventDefault();
        applySelection(entry);
      });
      menu.appendChild(option);
    });
  }

  function updateMenu(): void {
    if (!textarea) return;
    const caret =
      typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
    const beforeCaret = textarea.value.slice(0, caret);
    const match = /(?:^|[\s([{"',;:])@([^@\r\n]*)$/.exec(beforeCaret);
    if (!match) {
      closeMenu();
      return;
    }
    const query = match[1].toLowerCase();
    mentionStart = caret - match[1].length - 1;
    const available = getAvailableReferences();
    const prefixMatches: ReferenceEntry[] = [];
    const substringMatches: ReferenceEntry[] = [];
    for (const entry of available) {
      const lower = entry.name.toLowerCase();
      if (lower.startsWith(query)) prefixMatches.push(entry);
      else if (lower.includes(query)) substringMatches.push(entry);
    }
    filtered = [...prefixMatches, ...substringMatches];
    if (!filtered.length) {
      closeMenu();
      return;
    }
    if (activeIndex >= filtered.length) activeIndex = 0;
    renderMenu();
  }

  textarea.addEventListener('input', updateMenu);
  textarea.addEventListener('click', updateMenu);
  textarea.addEventListener('keydown', (event) => {
    if (menu.hidden || !filtered.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      event.stopImmediatePropagation();
      activeIndex = (activeIndex + 1) % filtered.length;
      renderMenu();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopImmediatePropagation();
      activeIndex = (activeIndex - 1 + filtered.length) % filtered.length;
      renderMenu();
    } else if (event.key === 'Tab' || event.key === 'Enter') {
      event.preventDefault();
      event.stopImmediatePropagation();
      applySelection(filtered[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeMenu();
    }
  });
  textarea.addEventListener('blur', () => {
    setTimeout(closeMenu, 150);
  });
}

function getTheaterOrGameStampStem(item: StampCanvasItem): string {
  const assetId = item.canvas_asset_id ? String(item.canvas_asset_id) : '';
  if (assetId.startsWith('stamp:')) return assetId.replace(/^stamp:/, '');
  const sid = item.stamp_id ? String(item.stamp_id) : '';
  if (sid.startsWith('theater:')) {
    const parts = sid.split(':');
    const last = parts[parts.length - 1] ?? '';
    return last.replace(/\.[^/.]+$/, '');
  }
  if (item.managed_by === 'game') return String(item.name ?? '');
  return '';
}

/** Determine whether two stamp actions represent the same logical stamp slot on the canvas. */
export function isSameStampKind(
  item: StampCanvasItem | null | undefined,
  action: StampCanvasItem | null | undefined,
): boolean {
  if (!item || !action || item.type !== 'stamp' || action.type !== 'stamp') return false;
  if (item.id && action.id && String(item.id) === String(action.id)) return true;
  if (item.user_id && action.user_id && String(item.user_id) === String(action.user_id)) {
    if (item.stamp_id && action.stamp_id && String(item.stamp_id) === String(action.stamp_id)) {
      return true;
    }
  }
  const itemIsTheaterOrGame =
    item.managed_by === 'game' ||
    Boolean(item.stamp_id && String(item.stamp_id).startsWith('theater:')) ||
    Boolean(item.canvas_asset_id && String(item.canvas_asset_id).startsWith('stamp:'));
  const actionIsTheaterOrGame =
    action.managed_by === 'game' ||
    Boolean(action.stamp_id && String(action.stamp_id).startsWith('theater:')) ||
    Boolean(action.canvas_asset_id && String(action.canvas_asset_id).startsWith('stamp:'));

  if (itemIsTheaterOrGame || actionIsTheaterOrGame) {
    if (item.stamp_id && action.stamp_id && String(item.stamp_id) === String(action.stamp_id)) {
      return true;
    }
    if (
      item.canvas_asset_id &&
      action.canvas_asset_id &&
      String(item.canvas_asset_id) === String(action.canvas_asset_id)
    ) {
      return true;
    }
    const itemStem = getTheaterOrGameStampStem(item);
    const actionStem = getTheaterOrGameStampStem(action);
    if (itemStem && actionStem && itemStem === actionStem) return true;
  }
  return false;
}

/** Manage canvas stamp annotations, stamp manager drawer, recency sorting, generation, and drag-and-drop. */
export function createCanvasStampsController(
  options: CreateCanvasStampsControllerOptions,
): CanvasStampsController {
  const doc = options.doc ?? document;
  const canvas = options.canvas !== undefined ? options.canvas : (doc.getElementById('doodle-canvas') as HTMLCanvasElement | null);
  const imgContainer = options.imgContainer !== undefined ? options.imgContainer : doc.getElementById('image-container');
  const getStampLayer = (): HTMLElement | null =>
    options.stampLayer !== undefined ? options.stampLayer : doc.getElementById('canvas-stamp-layer');
  const chatOpenStampsBtn =
    options.chatOpenStampsBtn !== undefined ? options.chatOpenStampsBtn : doc.getElementById('chat-open-stamps-btn');
  const chatPanelTabBtn =
    options.chatPanelTabBtn !== undefined ? options.chatPanelTabBtn : doc.getElementById('chat-show-chat-btn');
  const chatUserBar =
    options.chatUserBar !== undefined ? options.chatUserBar : doc.getElementById('chat-user-bar');
  const stampManagerPane =
    options.stampManagerPane !== undefined ? options.stampManagerPane : doc.getElementById('stamp-manager-pane');
  const stampManagerGrid =
    options.stampManagerGrid !== undefined ? options.stampManagerGrid : doc.getElementById('stamp-manager-grid');
  const stampManagerEmpty =
    options.stampManagerEmpty !== undefined ? options.stampManagerEmpty : doc.getElementById('stamp-manager-empty');
  const stampEmptyMessage =
    options.stampEmptyMessage !== undefined ? options.stampEmptyMessage : doc.getElementById('stamp-empty-message');
  const stampManagerCount =
    options.stampManagerCount !== undefined ? options.stampManagerCount : doc.getElementById('stamp-manager-count');
  const stampManagerPermissionBanner =
    options.stampManagerPermissionBanner !== undefined
      ? options.stampManagerPermissionBanner
      : doc.getElementById('stamp-manager-permission-banner');
  const panelBody =
    options.panelBody !== undefined ? options.panelBody : doc.getElementById('panel-body');
  const assetSearchInput =
    options.assetSearchInput !== undefined
      ? options.assetSearchInput
      : (doc.getElementById('asset-search') as HTMLInputElement | null);
  const assetSearchEmpty =
    options.assetSearchEmpty !== undefined ? options.assetSearchEmpty : doc.getElementById('asset-search-empty');
  const stampManagerBody =
    options.stampManagerBody !== undefined ? options.stampManagerBody : doc.getElementById('stamp-manager-body');
  const stampGenerationForm =
    options.stampGenerationForm !== undefined
      ? options.stampGenerationForm
      : (doc.getElementById('stamp-generation-form') as HTMLFormElement | null);
  const stampGenerationPrompt =
    options.stampGenerationPrompt !== undefined
      ? options.stampGenerationPrompt
      : (doc.getElementById('stamp-generation-prompt') as HTMLTextAreaElement | null);
  const stampGenerationStatus =
    options.stampGenerationStatus !== undefined
      ? options.stampGenerationStatus
      : doc.getElementById('stamp-generation-status');
  const profileLink =
    options.profileLink !== undefined
      ? options.profileLink
      : (doc.getElementById('stamp-manager-profile-link') as HTMLAnchorElement | null);
  const emptyProfileBtn =
    options.emptyProfileBtn !== undefined
      ? options.emptyProfileBtn
      : (doc.getElementById('stamp-manager-empty-profile-btn') as HTMLAnchorElement | null);

  let internalIsOpen = false;
  let internalCachedUserStamps: StampItem[] | null = null;
  let internalCachedTheaterStamps: StampItem[] = [];
  let stampLoadError = '';
  let isFetchingUserStamps = false;
  let nextStampGenerationId = 0;
  const pendingGenerations = new Map<number, PendingStampGeneration>();

  const isOpen = (): boolean => (options.getIsOpen ? options.getIsOpen() : internalIsOpen);
  const setOpenState = (open: boolean): void => {
    internalIsOpen = open;
    options.setIsOpen?.(open);
  };
  const getCachedUserStamps = (): StampItem[] | null =>
    options.getCachedUserStamps ? options.getCachedUserStamps() : internalCachedUserStamps;
  const setCachedUserStamps = (stamps: StampItem[] | null): void => {
    internalCachedUserStamps = stamps;
    options.setCachedUserStamps?.(stamps);
  };
  const getCachedTheaterStamps = (): StampItem[] =>
    options.getCachedTheaterStamps ? options.getCachedTheaterStamps() : internalCachedTheaterStamps;
  const setCachedTheaterStamps = (stamps: StampItem[]): void => {
    internalCachedTheaterStamps = stamps;
    options.setCachedTheaterStamps?.(stamps);
  };
  const getActiveTab = (): string => options.getActiveTab?.() ?? 'stamps';

  function resolveUrl(url: string): string {
    return options.resolveImageUrl ? options.resolveImageUrl(url) : url;
  }

  function updateProfileLinks(user: StampUser | null | undefined): void {
    const targetUrl =
      user && user.username ? `/users/${encodeURIComponent(user.username)}` : '/profile';
    if (profileLink) {
      profileLink.href = targetUrl;
      profileLink.style.display = user && user.username ? 'inline-flex' : 'none';
    }
    if (emptyProfileBtn) {
      emptyProfileBtn.href = targetUrl;
    }
  }

  function getStampUsageStorageKey(): string {
    const user = options.getCurrentUser();
    const uid = user && user.id ? user.id : null;
    return uid ? `narratron_stamp_usage_${uid}` : 'narratron_stamp_usage';
  }

  function getStampUsageMap(): Record<string, number> {
    try {
      const specificKey = getStampUsageStorageKey();
      const rawSpecific = localStorage.getItem(specificKey);
      if (rawSpecific) {
        const parsed: unknown = JSON.parse(rawSpecific);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          return parsed as Record<string, number>;
        }
      }
      const rawGlobal = localStorage.getItem('narratron_stamp_usage');
      if (rawGlobal) {
        const parsed: unknown = JSON.parse(rawGlobal);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          return parsed as Record<string, number>;
        }
      }
    } catch {
      // Ignore storage access errors or corrupt JSON
    }
    return {};
  }

  function getStampLastUsedTimestamp(stampId: string | number): number {
    const idStr = String(stampId);
    const map = getStampUsageMap();
    if (map[idStr]) {
      const val = Number(map[idStr]);
      if (!Number.isNaN(val)) return val;
    }

    const actions = options.getDoodleActions();
    for (let i = actions.length - 1; i >= 0; i -= 1) {
      const action = actions[i];
      if (action && action.type === 'stamp' && String(action.stamp_id) === idStr) {
        return 1000 + i;
      }
    }
    return 0;
  }

  function recordStampUsage(stampId: string | number | null | undefined): void {
    if (stampId === null || stampId === undefined) return;
    const idStr = String(stampId);
    const now = Date.now();
    const map = getStampUsageMap();
    map[idStr] = now;
    try {
      const json = JSON.stringify(map);
      localStorage.setItem(getStampUsageStorageKey(), json);
      localStorage.setItem('narratron_stamp_usage', json);
    } catch {
      // Ignore storage quota/availability errors
    }
    const cached = getCachedUserStamps();
    if (cached) {
      renderGrid(cached);
    }
  }

  function bringStampToFront(stampId: string): void {
    const actions = options.getDoodleActions();
    const index = actions.findIndex(
      (existing) => existing.type === 'stamp' && existing.id === stampId,
    );
    if (index >= 0) {
      const [selected] = actions.splice(index, 1);
      const firstNonStamp = actions.findIndex((existing) => existing.type !== 'stamp');
      if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, selected);
      else actions.push(selected);
    }
    const stampLayer = getStampLayer();
    if (stampLayer) {
      const el = Array.from(stampLayer.children).find(
        (node) => (node as HTMLElement).dataset.annotationId === stampId,
      );
      if (el && stampLayer.lastElementChild !== el) {
        stampLayer.appendChild(el);
      }
    }
  }

  function selectStampAnnotation(action: StampCanvasItem): void {
    if (action.id) {
      bringStampToFront(action.id);
    }
    if (action.stamp_id !== undefined && action.stamp_id !== null) {
      recordStampUsage(action.stamp_id);
    }
    options.sendDoodleMessage({ type: 'select_stamp', id: action.id });
  }

  function applyStampAnnotation(action: StampCanvasItem): void {
    const actions = options.getDoodleActions();
    const index = actions.findIndex((existing) => isSameStampKind(existing, action));
    if (index >= 0) actions.splice(index, 1);
    const firstNonStamp = actions.findIndex((existing) => existing.type !== 'stamp');
    if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, action);
    else actions.push(action);
    options.redrawDoodles();
  }

  function moveStampAnnotation(
    action: StampCanvasItem,
    previous: StampCanvasItem | null,
    commit: boolean,
  ): void {
    let nextAction = action;
    if (commit) {
      if (nextAction.stamp_id !== undefined && nextAction.stamp_id !== null) {
        recordStampUsage(nextAction.stamp_id);
      }
      nextAction = { ...nextAction };
      delete nextAction.client_message_id;
      const msg: DoodleMessage = { ...nextAction };
      options.sendDoodleMessage(msg);
      if (typeof msg.client_message_id === 'string') {
        nextAction.client_message_id = msg.client_message_id;
        if (previous) {
          options.rememberPendingEdit?.(msg.client_message_id, previous);
        }
      }
      if (previous) {
        const moved = previous.x !== nextAction.x || previous.y !== nextAction.y;
        const resized = previous.size !== nextAction.size;
        const rotated = previous.rotation !== nextAction.rotation;
        if ((moved || resized || rotated) && options.pushUndoAction) {
          const transformType: DoodleTransformType =
            resized && !moved && !rotated
              ? 'resize_stamp'
              : rotated && !moved && !resized
                ? 'rotate_stamp'
                : 'transform_stamp';
          options.pushUndoAction(
            new TransformStampDoodleAction(
              toStampAnnotationItem(previous),
              toStampAnnotationItem(nextAction),
              transformType,
            ),
          );
        }
      }
    }
    applyStampAnnotation(nextAction);
  }

  function removeStampAnnotation(action: { id?: string }): void {
    const actions = options.getDoodleActions();
    const index = actions.findIndex(
      (existing) => existing.type === 'stamp' && existing.id === action.id,
    );
    if (index >= 0) {
      const [removed] = actions.splice(index, 1);
      options.sendDoodleMessage({ type: 'remove_stamp', id: action.id });
      const activeTx = options.getActiveDeleteTransaction?.() ?? null;
      if (activeTx) {
        activeTx.stamps.push(removed);
      } else {
        options.pushUndoAction?.(new DeleteStampDoodleAction([toStampAnnotationItem(removed)]));
      }
      options.redrawDoodles();
    }
  }

  function canPlaceStamps(): boolean {
    if (!options.hasContributorPermission()) return false;
    if (options.isOrator()) return true;
    return options.isDoodlesEnabled();
  }

  function placeStampOnCanvas(stampData: StampDragPayload, normX: number, normY: number): void {
    if (!canPlaceStamps() || options.isPagedBack()) return;
    options.selectSelectCursor();
    if (options.isOrator() && !options.isDoodlesEnabled()) {
      options.enableDoodles();
    }
    const user = options.getCurrentUser();
    const myId = user && user.id ? user.id : options.getMyUserId?.() ?? '';
    const action: StampAnnotation = {
      type: 'stamp',
      id: generateAnnotationId('stamp'),
      stamp_id: stampData.stamp_id,
      user_id: myId,
      url: stampData.url,
      name: stampData.name || 'Stamp',
      x: normX,
      y: normY,
      size: 80,
      rotation: 0,
    };

    const actions = options.getDoodleActions();
    let replacedStamp: StampCanvasItem | null = null;
    const existingIndex = actions.findIndex((item) => isSameStampKind(item, action));
    if (existingIndex >= 0) {
      action.id = actions[existingIndex].id || action.id;
      [replacedStamp] = actions.splice(existingIndex, 1);
    }
    const firstNonStamp = actions.findIndex((item) => item.type !== 'stamp');
    if (firstNonStamp >= 0) actions.splice(firstNonStamp, 0, action);
    else actions.push(action);

    recordStampUsage(stampData.stamp_id);
    options.sendDoodleMessage(action as unknown as DoodleMessage);
    options.redrawDoodles();
    options.pushUndoAction?.(
      new PlaceStampDoodleAction(
        action,
        replacedStamp ? toStampAnnotationItem(replacedStamp) : null,
      ),
    );
  }

  function updateGenerationStatus(message = ''): void {
    const statusEl = stampGenerationStatus ?? doc.getElementById('stamp-generation-status');
    if (!statusEl) return;
    const count = pendingGenerations.size;
    const pending = count
      ? `${count} stamp${count === 1 ? '' : 's'} generating. You can generate another while you wait.`
      : '';
    statusEl.textContent = [message, pending].filter(Boolean).join(' ');
  }

  function updateGenerationPermission(): void {
    options.onGenerationPermissionUpdate?.();
    const activeTab = getActiveTab();
    const owner = options.isOwner();
    const form = stampGenerationForm ?? (doc.getElementById('stamp-generation-form') as HTMLFormElement | null);
    if (form) form.hidden = !owner || activeTab !== 'stamps';
    const statusEl = stampGenerationStatus ?? doc.getElementById('stamp-generation-status');
    if (statusEl) statusEl.hidden = !owner || activeTab !== 'stamps';
  }

  function updatePermissionBanner(): void {
    if (!stampManagerPermissionBanner) return;
    const canContribute = options.hasContributorPermission();
    const isOrator = options.isOrator();
    stampManagerPermissionBanner.style.display =
      isOrator || getActiveTab() !== 'stamps' ? 'none' : '';
    if (isOrator) return;
    const active = canPlaceStamps();
    stampManagerPermissionBanner.className = `stamp-manager-permission-banner ${
      active ? 'contributor' : 'non-contributor'
    }`;
    if (!canContribute) {
      stampManagerPermissionBanner.innerHTML =
        '🔒 <strong>Viewer Mode</strong>: You need contributor permission to place stamps on the canvas.';
    } else if (!options.isDoodlesEnabled()) {
      stampManagerPermissionBanner.innerHTML =
        '✏️ <strong>Doodles are Off</strong>: The Orator must turn on Doodles before contributors can place stamps.';
    } else {
      stampManagerPermissionBanner.innerHTML =
        '✨ <strong>Contributor Active</strong>: Drag any stamp out of this pane and onto the canvas.';
    }
  }

  function renderGrid(stamps: readonly StampItem[] = getCachedUserStamps() ?? []): void {
    if (!stampManagerGrid) return;
    stampManagerGrid.innerHTML = '';
    const theaterStamps = getCachedTheaterStamps();
    const isStampsActive = getActiveTab() === 'stamps';
    if (stampManagerCount && isStampsActive) {
      stampManagerCount.textContent = `${stamps.length}/10`;
      stampManagerCount.title =
        `${stamps.length} user stamps · ${theaterStamps.length} theater stamps` +
        (pendingGenerations.size ? ` · ${pendingGenerations.size} generating` : '');
    }

    if (stamps.length === 0 && theaterStamps.length === 0 && pendingGenerations.size === 0) {
      if (stampManagerEmpty && isStampsActive) stampManagerEmpty.style.display = 'flex';
      if (stampEmptyMessage) {
        stampEmptyMessage.textContent = stampLoadError || 'No stamps available yet.';
      }
      return;
    }
    if (stampManagerEmpty && isStampsActive) stampManagerEmpty.style.display = 'none';
    if (stampLoadError) {
      const notice = doc.createElement('div');
      notice.className = 'stamp-section-label';
      notice.textContent = stampLoadError;
      stampManagerGrid.appendChild(notice);
    }

    const sortStamps = (items: readonly StampItem[]): StampItem[] =>
      [...items].sort((a, b) => {
        const timeA = getStampLastUsedTimestamp(a.id);
        const timeB = getStampLastUsedTimestamp(b.id);
        if (timeA !== timeB) return timeB - timeA;
        const idA = Number(a.id) || 0;
        const idB = Number(b.id) || 0;
        return idA - idB;
      });

    const sortedStamps = [...sortStamps(stamps), ...sortStamps(theaterStamps)];
    const addSection = (label: string, divider = false): void => {
      const heading = doc.createElement('div');
      heading.className = `stamp-section-label${divider ? ' stamp-section-divider' : ''}`;
      heading.textContent = label;
      stampManagerGrid.appendChild(heading);
    };
    addSection('User stamps');
    if (stamps.length === 0) addSection('No user stamps uploaded yet.');

    const canContribute = options.hasContributorPermission();
    const canDrag = canPlaceStamps();

    sortedStamps.forEach((stamp, index) => {
      if (index === stamps.length) addSection('Theater stamps', true);
      const card = doc.createElement('div');
      card.className = `stamp-card ${canDrag ? 'can-drag' : 'locked'}`;
      card.dataset.assetSearch = JSON.stringify(stamp);
      card.draggable = canDrag;
      card.dataset.stampId = String(stamp.id);
      card.dataset.stampName = stamp.name || 'Stamp';
      card.dataset.stampUrl = resolveUrl(stamp.url);
      const stampLabel = stamp.name || 'Stamp';
      card.title = canDrag
        ? `Drag "${stampLabel}" onto canvas`
        : !canContribute
          ? `"${stampLabel}" (Requires Contributor permission to place)`
          : `"${stampLabel}" (Doodles must be enabled by Orator to place stamps)`;

      const img = doc.createElement('img');
      img.className = 'stamp-card-thumb';
      img.src = resolveUrl(stamp.url);
      img.alt = stamp.name;
      img.draggable = false;

      const nameSpan = doc.createElement('span');
      nameSpan.className = 'stamp-card-name';
      nameSpan.textContent = stamp.name;

      card.appendChild(img);
      card.appendChild(nameSpan);

      if (canDrag) {
        const grip = doc.createElement('span');
        grip.className = 'stamp-card-grip';
        grip.textContent = '⠿';
        card.appendChild(grip);

        card.addEventListener('dragstart', (event) => {
          if (!canPlaceStamps()) {
            event.preventDefault();
            return;
          }
          const payload: StampDragPayload = {
            stamp_id: stamp.id,
            name: stamp.name,
            url: stamp.url,
          };
          const json = JSON.stringify(payload);
          if (event.dataTransfer) {
            try {
              event.dataTransfer.setData('application/json', json);
            } catch {
              // Ignore browser DataTransfer type restrictions
            }
            try {
              event.dataTransfer.setData('text/plain', json);
            } catch {
              // Ignore browser DataTransfer type restrictions
            }
            event.dataTransfer.effectAllowed = 'copyMove';
          }
        });

        card.addEventListener('pointerdown', (event) => {
          if (event.button !== 0 || !canPlaceStamps()) return;
          let moved = false;
          const startX = event.clientX;
          const startY = event.clientY;
          let ghost: HTMLDivElement | null = null;

          const onPointerMove = (moveEvt: PointerEvent): void => {
            const dist = Math.hypot(moveEvt.clientX - startX, moveEvt.clientY - startY);
            if (!moved && dist > 6) {
              moved = true;
              ghost = doc.createElement('div');
              ghost.className = 'stamp-drag-ghost';
              const ghostImg = doc.createElement('img');
              ghostImg.src = stamp.url;
              ghost.appendChild(ghostImg);
              doc.body.appendChild(ghost);
            }
            if (ghost) {
              ghost.style.left = `${moveEvt.clientX}px`;
              ghost.style.top = `${moveEvt.clientY}px`;
            }
          };

          const onPointerUp = (upEvt: PointerEvent): void => {
            window.removeEventListener('pointermove', onPointerMove);
            window.removeEventListener('pointerup', onPointerUp);
            window.removeEventListener('pointercancel', onPointerUp);
            if (ghost) {
              ghost.remove();
              ghost = null;
            }
            if (!moved) return;

            const targetEl =
              canvas && canvas.offsetWidth > 0 && canvas.offsetHeight > 0
                ? canvas
                : (imgContainer ?? canvas);
            const rect = targetEl ? targetEl.getBoundingClientRect() : null;
            if (
              rect &&
              rect.width > 0 &&
              rect.height > 0 &&
              upEvt.clientX >= rect.left &&
              upEvt.clientX <= rect.right &&
              upEvt.clientY >= rect.top &&
              upEvt.clientY <= rect.bottom
            ) {
              const projected = options.getNormalizedDropPoint?.(upEvt) ?? null;
              const normX =
                projected?.x ?? Math.max(0, Math.min(1, (upEvt.clientX - rect.left) / rect.width));
              const normY =
                projected?.y ?? Math.max(0, Math.min(1, (upEvt.clientY - rect.top) / rect.height));
              placeStampOnCanvas({ stamp_id: stamp.id, name: stamp.name, url: stamp.url }, normX, normY);
            }
          };

          window.addEventListener('pointermove', onPointerMove);
          window.addEventListener('pointerup', onPointerUp);
          window.addEventListener('pointercancel', onPointerUp);
        });
      }

      stampManagerGrid.appendChild(card);
    });

    if (theaterStamps.length === 0) {
      addSection(pendingGenerations.size ? 'Theater stamps' : 'Theater stamps · None provided', true);
    }

    pendingGenerations.forEach((job, jobId) => {
      const card = doc.createElement('div');
      card.className = 'stamp-card stamp-card-generating';
      card.dataset.assetSearch = `${job.name} ${job.prompt}`;
      card.dataset.generationId = String(jobId);
      card.draggable = false;
      card.setAttribute('aria-busy', 'true');
      card.setAttribute('aria-label', `${job.name}: Generating`);
      card.title = `Generating “${job.name}” — ${job.prompt}`;

      const icon = doc.createElement('span');
      icon.className = 'stamp-card-thumb stamp-generation-placeholder';
      icon.setAttribute('aria-hidden', 'true');
      const nameSpan = doc.createElement('span');
      nameSpan.className = 'stamp-card-name';
      nameSpan.textContent = job.name;
      const progress = doc.createElement('span');
      progress.className = 'stamp-card-progress';
      progress.textContent = 'Generating…';
      card.append(icon, nameSpan, progress);
      stampManagerGrid.appendChild(card);
    });
  }

  async function loadUserStamps(): Promise<void> {
    if (!stampManagerGrid || isFetchingUserStamps) return;
    isFetchingUserStamps = true;
    if (stampManagerEmpty) stampManagerEmpty.style.display = 'none';

    try {
      const theaterId = options.getTheaterId() ?? '';
      const results = await Promise.allSettled([
        fetch('/api/users/me/stamps').then(async (res): Promise<unknown> => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        }),
        fetch(`/api/theaters/${encodeURIComponent(theaterId)}/stamps`).then(
          async (res): Promise<unknown> => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
          },
        ),
      ]);
      const userStamps =
        results[0].status === 'fulfilled' && Array.isArray(results[0].value)
          ? (results[0].value as StampItem[])
          : [];
      const theaterStamps =
        results[1].status === 'fulfilled' && Array.isArray(results[1].value)
          ? (results[1].value as StampItem[])
          : [];
      setCachedUserStamps(userStamps);
      setCachedTheaterStamps(theaterStamps);
      stampLoadError = results.some((result) => result.status === 'rejected')
        ? 'Some stamps could not be loaded. Reopen the manager to retry.'
        : '';
      renderGrid(userStamps);
    } catch (err) {
      console.warn('Unable to load user stamps:', err);
      stampManagerGrid.innerHTML = '';
      if (stampManagerEmpty) stampManagerEmpty.style.display = 'flex';
      if (stampEmptyMessage) stampEmptyMessage.textContent = 'Unable to load stamps at this time.';
    } finally {
      isFetchingUserStamps = false;
    }
  }

  function applyAssetSearch(): void {
    const body = stampManagerBody ?? doc.getElementById('stamp-manager-body');
    const searchEl =
      assetSearchInput ?? (doc.getElementById('asset-search') as HTMLInputElement | null);
    if (!body || !searchEl) return;
    const query = searchEl.value.trim().toLocaleLowerCase();
    const matches = (text: string): boolean => text.toLocaleLowerCase().includes(query);

    body.querySelectorAll<HTMLElement>('[data-asset-search]').forEach((card) => {
      const playlist = card.closest<HTMLElement>('.playlist-group');
      card.classList.toggle(
        'asset-search-hidden',
        !matches(`${card.dataset.assetSearch ?? ''} ${playlist?.dataset.playlistName ?? ''}`),
      );
    });

    body.querySelectorAll<HTMLElement>('.playlist-group').forEach((group) => {
      const playlistName = group.dataset.playlistName ?? '';
      const nameMatches = matches(playlistName);
      const tracks = Array.from(
        group.querySelectorAll<HTMLElement>('.playlist-track-list [data-asset-search]'),
      );
      const visible =
        nameMatches || tracks.some((track) => !track.classList.contains('asset-search-hidden'));
      group.classList.toggle('asset-search-hidden', !visible);
      group
        .querySelector<HTMLElement>('.playlist-header-push')
        ?.classList.toggle('asset-search-hidden', !visible);
      const expanded = query ? visible : Boolean(options.isPlaylistExpanded?.(playlistName));
      const trackList = group.querySelector<HTMLElement>('.playlist-track-list');
      if (trackList) trackList.hidden = !expanded;
      group
        .querySelector<HTMLElement>('.playlist-header-toggle')
        ?.setAttribute('aria-expanded', String(expanded));
      const chevron = group.querySelector<HTMLElement>('.playlist-chevron');
      const symbol = expanded ? '▼' : '▶';
      if (chevron && chevron.textContent !== symbol) chevron.textContent = symbol;
      group.classList.toggle('minimized', !expanded);
    });

    const panels: Readonly<Record<string, string>> = {
      stamps: 'stamp-manager-grid',
      scenes: 'scenes-manager-grid',
      characters: 'characters-manager-grid',
      playlists: 'playlists-manager',
      animations: 'animations-manager',
    };
    const panelId = panels[getActiveTab()] ?? 'stamp-manager-grid';
    const panel = doc.getElementById(panelId);
    const panelCards = panel ? Array.from(panel.querySelectorAll<HTMLElement>('[data-asset-search]')) : [];
    const pendingCards = Array.from(
      doc.querySelectorAll<HTMLElement>('#asset-generation-pending [data-asset-search]'),
    );
    const visible = [...panelCards, ...pendingCards].some(
      (card) => !card.classList.contains('asset-search-hidden'),
    );
    const emptyEl = assetSearchEmpty ?? doc.getElementById('asset-search-empty');
    if (emptyEl) emptyEl.hidden = !query || visible;
  }

  function setOpen(open: boolean): void {
    const user = options.getCurrentUser();
    if (open && (!user || !user.id)) {
      if (options.getAuthState) {
        options
          .getAuthState()
          .then((data) => {
            if (data && data.authenticated && data.user) {
              options.setCurrentUser?.(data.user);
              updateProfileLinks(data.user);
              setOpen(true);
            } else {
              options.openAuthModal?.('login');
            }
          })
          .catch(() => {
            options.openAuthModal?.('login');
          });
      } else {
        options.openAuthModal?.('login');
      }
      return;
    }

    const nextOpen = Boolean(open);
    setOpenState(nextOpen);

    if (nextOpen && panelBody && panelBody.style.display !== 'none' && stampManagerPane) {
      const chatComposer = doc.getElementById('chat-form');
      const chatPrompt = doc.getElementById('chat-input');
      const footer = stampManagerPane.querySelector<HTMLElement>('.stamp-manager-footer');
      const stampPrompt =
        stampGenerationPrompt ??
        (doc.getElementById('stamp-generation-prompt') as HTMLElement | null);
      if (chatComposer && footer) {
        const composerStyle = getComputedStyle(chatComposer);
        footer.style.padding = composerStyle.padding;
        footer.style.gap = composerStyle.gap;
        footer.style.margin = composerStyle.margin;
        footer.style.borderTop = composerStyle.borderTop;
      }
      if (chatPrompt && stampPrompt) {
        const promptStyle = getComputedStyle(chatPrompt);
        stampPrompt.style.height = `${chatPrompt.getBoundingClientRect().height}px`;
        stampPrompt.style.padding = promptStyle.padding;
        stampPrompt.style.font = promptStyle.font;
      }
    }

    if (panelBody) panelBody.style.display = nextOpen ? 'none' : 'flex';
    if (stampManagerPane) stampManagerPane.style.display = nextOpen ? 'flex' : 'none';
    chatOpenStampsBtn?.classList.toggle('active', nextOpen);
    chatOpenStampsBtn?.setAttribute('aria-selected', String(nextOpen));
    chatPanelTabBtn?.setAttribute('aria-selected', String(!nextOpen));

    if (nextOpen) {
      options.onTabsVisibilityUpdate?.();
      const activeTab = getActiveTab();
      if (activeTab === 'stamps') {
        updatePermissionBanner();
        updateProfileLinks(options.getCurrentUser());
        const cached = getCachedUserStamps();
        if (cached) renderGrid(cached);
        void loadUserStamps();
      } else {
        options.onOpenNonStampTab?.(activeTab);
      }
    }
  }

  function getAvailableReferences(): ReferenceEntry[] {
    const external = options.getReferenceSources?.() ?? {};
    return collectTheaterReferences({
      characters: external.characters,
      scenes: external.scenes,
      references: external.references,
      stamps: getCachedTheaterStamps(),
    });
  }

  function extractTaggedReferences(promptText: string): string[] {
    return extractTaggedCanvasReferences(promptText, getAvailableReferences());
  }

  function attachReferenceAutocomplete(textarea: HTMLTextAreaElement | null): void {
    attachCanvasReferenceAutocomplete(textarea, getAvailableReferences, doc);
  }

  // Wire DOM listeners
  if (assetSearchInput) {
    assetSearchInput.addEventListener('input', applyAssetSearch);
  }
  if (stampManagerBody && typeof MutationObserver !== 'undefined') {
    new MutationObserver(applyAssetSearch).observe(stampManagerBody, {
      childList: true,
      subtree: true,
    });
  }

  chatOpenStampsBtn?.addEventListener('click', () => {
    if (!isOpen()) setOpen(true);
  });
  chatPanelTabBtn?.addEventListener('click', () => setOpen(false));
  chatUserBar?.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const tab =
      event.key === 'Home'
        ? chatPanelTabBtn
        : event.key === 'End'
          ? chatOpenStampsBtn
          : event.target === chatPanelTabBtn
            ? chatOpenStampsBtn
            : chatPanelTabBtn;
    tab?.focus();
    tab?.click();
  });

  const requireSignedInUser = (event: MouseEvent): void => {
    const user = options.getCurrentUser();
    if (!user || !user.id) {
      event.preventDefault();
      options.openAuthModal?.('login');
    }
  };
  emptyProfileBtn?.addEventListener('click', requireSignedInUser);
  profileLink?.addEventListener('click', requireSignedInUser);

  if (stampGenerationPrompt) {
    attachReferenceAutocomplete(stampGenerationPrompt);
    stampGenerationPrompt.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.isComposing) return;
      event.preventDefault();
      if (!event.repeat) stampGenerationForm?.requestSubmit();
    });
  }

  stampGenerationForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!options.isOwner()) return;
    const promptInput =
      stampGenerationPrompt ??
      (doc.getElementById('stamp-generation-prompt') as HTMLTextAreaElement | null);
    const statusEl = stampGenerationStatus ?? doc.getElementById('stamp-generation-status');
    const prompt = promptInput?.value.trim() ?? '';
    if (!prompt) {
      if (statusEl) statusEl.textContent = 'Enter an image prompt.';
      return;
    }
    const name = stampNameFromPrompt(prompt);
    const jobId = ++nextStampGenerationId;
    pendingGenerations.set(jobId, { name, prompt });
    if (promptInput) promptInput.value = '';
    renderGrid(getCachedUserStamps() ?? []);
    updateGenerationStatus();
    let resultMessage = '';
    try {
      const theaterId = options.getTheaterId() ?? '';
      const payload: { name: string; prompt: string; references?: string[] } = { name, prompt };
      const refs = extractTaggedReferences(prompt);
      if (refs.length) payload.references = refs;
      const response = await fetch(`/api/theaters/${encodeURIComponent(theaterId)}/stamps/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as StampGenerationResponse;
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to generate stamp.');
      }
      // Responses may arrive out of order, with an older stamp list.
      const mergedStamps = new Map(getCachedTheaterStamps().map((stamp) => [stamp.id, stamp]));
      (data.stamps ?? []).forEach((stamp) => mergedStamps.set(stamp.id, stamp));
      setCachedTheaterStamps([...mergedStamps.values()]);
      resultMessage = `“${name}” saved to this theater and ready to use. Charged ${data.credits_charged} credits.`;
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : '';
      resultMessage = `“${name}”: ${errMsg || 'Unable to generate stamp. Please try again.'}`;
      if (promptInput && !promptInput.value.trim()) promptInput.value = prompt;
    } finally {
      pendingGenerations.delete(jobId);
      renderGrid(getCachedUserStamps() ?? []);
      updateGenerationStatus(resultMessage);
    }
  });

  if (imgContainer) {
    imgContainer.addEventListener('dragover', (event) => {
      if (typeof window !== 'undefined' && window.isCanvasSelectionActive) return;
      const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
      const isStampDrag = types.includes('application/json') || types.includes('text/plain');
      if (canPlaceStamps() && isStampDrag) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        imgContainer.classList.add('stamp-drag-over');
      }
    });
    imgContainer.addEventListener('dragleave', () => {
      imgContainer.classList.remove('stamp-drag-over');
    });
    imgContainer.addEventListener('drop', (event) => {
      imgContainer.classList.remove('stamp-drag-over');
      if (!canPlaceStamps() || !event.dataTransfer) return;
      try {
        let raw = '';
        try {
          raw = event.dataTransfer.getData('application/json');
        } catch {
          // Ignore unavailable MIME type
        }
        if (!raw) {
          try {
            raw = event.dataTransfer.getData('text/plain') || event.dataTransfer.getData('text');
          } catch {
            // Ignore unavailable MIME type
          }
        }
        if (!raw) return;
        const stampData = JSON.parse(raw) as Partial<StampDragPayload> | null;
        if (!stampData || !stampData.stamp_id) return;
        event.preventDefault();
        const targetEl =
          canvas && canvas.offsetWidth > 0 && canvas.offsetHeight > 0
            ? canvas
            : (imgContainer ?? canvas);
        const rect = targetEl ? targetEl.getBoundingClientRect() : null;
        if (!rect || !rect.width || !rect.height) return;
        const projected = options.getNormalizedDropPoint?.(event) ?? null;
        const normX =
          projected?.x ?? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        const normY =
          projected?.y ?? Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
        placeStampOnCanvas(
          {
            stamp_id: stampData.stamp_id,
            name: stampData.name,
            url: stampData.url ?? '',
          },
          normX,
          normY,
        );
      } catch (err) {
        console.error('Error dropping stamp onto canvas:', err);
      }
    });
  }

  return {
    pendingGenerations,
    isOpen,
    setOpen,
    getCachedUserStamps,
    setCachedUserStamps,
    getCachedTheaterStamps,
    setCachedTheaterStamps,
    getStampLoadError: () => stampLoadError,
    setStampLoadError: (error: string) => {
      stampLoadError = error;
    },
    canPlaceStamps,
    placeStampOnCanvas,
    bringStampToFront,
    selectStampAnnotation,
    applyStampAnnotation,
    moveStampAnnotation,
    removeStampAnnotation,
    loadUserStamps,
    renderGrid,
    updatePermissionBanner,
    updateGenerationPermission,
    updateGenerationStatus,
    updateProfileLinks,
    getStampUsageStorageKey,
    getStampLastUsedTimestamp,
    recordStampUsage,
    applyAssetSearch,
    getAvailableReferences,
    extractTaggedReferences,
    attachReferenceAutocomplete,
  };
}

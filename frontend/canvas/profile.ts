/** User profile page rendering, settings, bio editing, gifts, stamps, and account deletion. */

import { setupProfileFriends, type ProfileSummary } from './friends.js';

export interface ProfileStats {
  readonly lifetime_credits_used: number;
  readonly theater_views: number;
}

export interface ProfileStamp {
  readonly id: number;
  readonly name: string;
  readonly url?: string;
}

export interface UserProfile extends ProfileSummary {
  readonly bio?: string;
  readonly profile_color?: string;
  readonly stats_visible?: boolean;
  readonly stats?: ProfileStats | null;
  readonly stamps?: readonly ProfileStamp[];
}

interface PricingResponse {
  readonly image_credit_rate?: number;
}

interface GiftCreateResponse {
  readonly link?: string;
  readonly expires_at?: string;
  readonly detail?: unknown;
}

interface DetailResponse {
  readonly detail?: unknown;
}

interface PickrColor {
  toHEXA(): { toString(): string };
}

interface PickrInstance {
  on(event: 'change' | 'save', callback: (picked: PickrColor) => void): PickrInstance;
  hide(): void;
  destroyAndRemove(): void;
}

interface PickrConstructor {
  create(options: {
    el: string;
    theme: string;
    default: string;
    components: {
      preview: boolean;
      opacity: boolean;
      hue: boolean;
      interaction: {
        hex: boolean;
        input: boolean;
        save: boolean;
      };
    };
  }): PickrInstance;
}

export interface ProfilePageController {
  load(activeTab?: string): Promise<void>;
  activateTab(id: string): void;
}

declare global {
  interface Window {
    Pickr?: PickrConstructor;
    deleteStamp?: (stampId: number) => Promise<void>;
  }
}

const HTML_ESCAPE_MAP: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => HTML_ESCAPE_MAP[char] ?? char);
}

function panelMarkup(
  id: string,
  title: string,
  subtitle: string,
  body: string,
  active = false,
): string {
  return `<section id="${id}" class="tab-panel ${active ? 'active' : ''}" role="tabpanel"><header class="panel-header"><div><p class="eyebrow">Profile</p><h2>${title}</h2><p class="panel-subtitle">${subtitle}</p></div></header>${body}</section>`;
}

function tabMarkup(
  id: string,
  icon: string,
  label: string,
  note = '',
  danger = false,
  active = false,
): string {
  return `<button type="button" class="tab-button ${danger ? 'danger' : ''} ${active ? 'active' : ''}" role="tab" aria-selected="${active}" aria-controls="${id}" data-tab="${id}"><span class="tab-icon">${icon}</span><span class="tab-label">${label}</span>${note ? `<span class="tab-note">${note}</span>` : ''}</button>`;
}

function getErrorDetail(data: DetailResponse, fallback: string): string {
  return typeof data.detail === 'string' && data.detail ? data.detail : fallback;
}

export function initializeProfilePage(): ProfilePageController | null {
  const rootEl = document.getElementById('profile');
  if (!rootEl) return null;
  const el = rootEl;

  const username = decodeURIComponent(window.location.pathname.split('/').pop() ?? '');
  let profilePicker: PickrInstance | null = null;
  let selectedProfileColor = '#818cf8';

  function activateTab(id: string): void {
    document.querySelectorAll<HTMLButtonElement>('.tab-button').forEach((button) => {
      const selected = button.dataset.tab === id;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
    document.querySelectorAll<HTMLElement>('.tab-panel').forEach((tabPanel) => {
      tabPanel.classList.toggle('active', tabPanel.id === id);
    });
  }

  window.activateTab = activateTab;

  async function deleteStamp(stampId: number): Promise<void> {
    if (!window.confirm('Are you sure you want to delete this stamp?')) return;
    try {
      const resp = await fetch(`/api/users/me/stamps/${stampId}`, { method: 'DELETE' });
      if (resp.ok) {
        await load('stamps');
      } else {
        const err = (await resp.json().catch(() => ({}))) as DetailResponse;
        window.alert(getErrorDetail(err, 'Could not delete stamp.'));
      }
    } catch {
      window.alert('Network error while deleting stamp.');
    }
  }

  window.deleteStamp = deleteStamp;

  async function load(activeTab = 'overview'): Promise<void> {
    const response = await fetch(`/api/users/${encodeURIComponent(username)}/profile`);
    if (!response.ok) {
      el.innerHTML =
        '<div class="panel"><h1>Profile not found</h1><p class="empty">This user does not exist.</p></div>';
      return;
    }

    const profile = (await response.json()) as UserProfile;
    const rawColor = profile.profile_color ?? '';
    const color = /^#[0-9a-fA-F]{6}$/.test(rawColor) ? rawColor : '#818cf8';
    const initial = esc(profile.username).slice(0, 1).toUpperCase();
    const bio = profile.bio
      ? esc(profile.bio)
      : '<span class="empty">This user has not written a bio yet.</span>';
    const stats = profile.stats
      ? `<div class="stats"><div class="stat"><b>${Number(profile.stats.lifetime_credits_used).toFixed(1)}</b><span>Credits used</span></div><div class="stat"><b>${profile.stats.theater_views}</b><span>Theater views</span></div></div>`
      : '<p class="private">This user keeps their stats private.</p>';
    const stamps = Array.isArray(profile.stamps) ? profile.stamps : [];
    const stampCount = stamps.length;

    const stampCards = stampCount
      ? `<div class="stamps-grid">${stamps
          .map(
            (stamp) =>
              `<div class="stamp-card"><div class="stamp-thumb-wrap"><img src="${esc(stamp.url || `/api/stamps/${stamp.id}`)}" alt="${esc(stamp.name)}" class="stamp-thumb" loading="lazy"></div><div class="stamp-title" title="${esc(stamp.name)}">${esc(stamp.name)}</div>${profile.is_owner ? `<button type="button" class="stamp-delete-btn" data-stamp-id="${stamp.id}">Delete</button>` : ''}</div>`,
          )
          .join('')}</div>`
      : '<p class="empty">No stamps stored yet.</p>';

    const upload = profile.is_owner
      ? `<details id="stamp-upload" class="stamp-upload-box"><summary class="stamp-expand-summary">Add a stamp <span class="empty" style="float:right;font-weight:400;">${stampCount} / 10 used</span></summary>${
          stampCount < 10
            ? '<div class="stamp-upload-form"><label class="stamp-file-label" for="stamp-file-input">Choose Image</label><input id="stamp-file-input" type="file" accept="image/png,image/jpeg,image/webp,image/gif"><span id="stamp-file-chosen" class="stamp-file-name">No file chosen</span><input id="stamp-name-input" type="text" placeholder="Stamp name (optional)" style="max-width:180px;padding:.6rem;"><button id="stamp-upload-btn" type="button" style="padding:.6rem 1rem;">Upload</button><span id="stamp-upload-status" style="font-size:.85rem;"></span></div>'
            : '<p style="margin:.6rem 0 0;color:#fca5a5;font-size:.88rem;">You have reached your limit. Delete a stamp before adding another.</p>'
        }</details>`
      : '';

    const generate =
      profile.is_owner && stampCount < 10
        ? `<details id="stamp-generation" class="stamp-upload-box" style="margin-top:1rem;">
        <summary class="stamp-expand-summary">Generate a stamp</summary>
        <form id="stamp-generate-form">
        <p id="stamp-generation-cost" class="empty" style="font-size:.85rem;">Uses the standard image generation credit rate. Credits are charged when your stamp is saved.</p>
        <label for="stamp-generate-name">Stamp name</label><input id="stamp-generate-name" type="text" maxlength="100" required placeholder="Goblin scout">
        <label for="stamp-generate-prompt">Describe your stamp</label><textarea id="stamp-generate-prompt" maxlength="4000" required placeholder="A goblin scout wearing a green cloak"></textarea>
        <div class="action-row"><button id="stamp-generate-btn" type="submit">Generate stamp</button><span id="stamp-generate-status" role="status" style="margin-left:.75rem;font-size:.85rem;"></span></div>
        </form>
      </details>`
        : '';

    const overview = panelMarkup(
      'overview',
      'Overview',
      'A glimpse into this storyteller’s Narratron journey.',
      `<div class="bio-heading"><div class="section-label">Bio</div>${profile.is_owner ? '<button id="edit-bio" class="bio-edit-btn" type="button">Edit bio</button>' : ''}</div><div id="bio-display" class="bio-copy">${bio}</div><div class="section-label">Stats</div>${stats}`,
      true,
    );
    const stampsPanel = panelMarkup(
      'stamps',
      'Stamps',
      `${stampCount} of 10 personal stamps collected.`,
      `${stampCards}${upload}${generate}`,
    );
    const settings = profile.is_owner
      ? panelMarkup(
          'settings',
          'Profile settings',
          'Customize how your profile appears to other storytellers.',
          `<label style="margin-top:0;">Name color <span id="color-picker"></span></label><label><input id="visible" type="checkbox" ${profile.stats_visible ? 'checked' : ''}> Show my stats publicly</label><div class="action-row"><button id="save" type="button">Save changes</button><span id="status" aria-live="polite"></span></div>`,
        )
      : '';
    const gifts = profile.is_owner
      ? panelMarkup(
          'gifts',
          'Gift credits',
          'Create a single-use gift link for another storyteller.',
          '<div class="gift-box"><p>Create a link that expires in one week. You are charged only when the recipient claims it.</p><div class="gift-row"><input id="gift-credits" type="number" min="0.1" step="0.1" placeholder="Credits"><button id="create-gift" type="button">Create gift link</button></div><div id="gift-status" class="gift-link" aria-live="polite"></div></div>',
        )
      : '';
    const danger = profile.is_owner
      ? panelMarkup(
          'danger',
          'Danger zone',
          'Account removal is permanent and cannot be undone.',
          `<div class="danger-box"><p style="margin:0 0 1rem;color:#fca5a5;">Remove your account, deployed theaters, credits, and saved data.</p><button id="start-delete-btn" class="danger-btn" type="button">Delete account</button><div id="delete-confirm-box" style="display:none;margin-top:1rem;"><p style="margin:0 0 .75rem;color:var(--muted);font-size:.88rem;">Type <b>${esc(profile.username)}</b> to confirm.</p><input id="delete-confirm-input" type="text" placeholder="${esc(profile.username)}" autocomplete="off"><div class="action-row"><button id="cancel-delete-btn" class="cancel-btn" type="button">Cancel</button><button id="confirm-delete-btn" class="danger-confirm-btn" type="button" disabled>Permanently delete</button><span id="delete-status" style="color:#f87171;" aria-live="polite"></span></div></div></div>`,
        )
      : '';
    const tabs =
      tabMarkup('overview', '◉', 'Overview', 'Profile', false, true) +
      tabMarkup('stamps', '✦', 'Stamps', String(stampCount)) +
      (profile.is_owner
        ? `<div class="tab-divider"></div>${tabMarkup('settings', '⚙', 'Settings')}${tabMarkup('gifts', '↗', 'Gift credits')}${tabMarkup('danger', '!', 'Danger zone', '', true)}`
        : '');

    el.innerHTML = `<div class="profile-shell" style="--accent:${color}"><aside class="profile-sidebar"><div class="identity"><div class="avatar">${initial}</div><div><h1 style="color:${color}">${esc(profile.username)}</h1><p>${profile.is_owner ? 'Your profile' : 'Storyteller profile'}</p></div></div><nav class="tab-list" role="tablist" aria-label="Profile sections">${tabs}</nav></aside><div class="panel">${overview}${stampsPanel}${settings}${gifts}${danger}</div></div>`;

    document.querySelectorAll<HTMLButtonElement>('.tab-button').forEach((button) => {
      button.addEventListener('click', () => activateTab(button.dataset.tab ?? 'overview'));
    });
    activateTab(activeTab);
    void setupProfileFriends(profile);

    if (profilePicker) {
      profilePicker.destroyAndRemove();
      profilePicker = null;
    }
    selectedProfileColor = color;
    if (profile.is_owner && window.Pickr) {
      profilePicker = window.Pickr.create({
        el: '#color-picker',
        theme: 'monolith',
        default: color,
        components: {
          preview: true,
          opacity: false,
          hue: true,
          interaction: { hex: true, input: true, save: true },
        },
      })
        .on('change', (picked) => {
          selectedProfileColor = picked.toHEXA().toString();
        })
        .on('save', (picked) => {
          selectedProfileColor = picked.toHEXA().toString();
          profilePicker?.hide();
        });
    }

    el.querySelectorAll<HTMLButtonElement>('.stamp-delete-btn[data-stamp-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const stampId = Number(btn.dataset.stampId);
        if (Number.isFinite(stampId)) {
          void deleteStamp(stampId);
        }
      });
    });

    const save = document.getElementById('save');
    if (save instanceof HTMLButtonElement) {
      save.onclick = async (): Promise<void> => {
        const visibleCheckbox = document.getElementById('visible');
        const status = document.getElementById('status');
        const result = await fetch('/api/users/me/profile', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            bio: profile.bio,
            stats_visible:
              visibleCheckbox instanceof HTMLInputElement ? visibleCheckbox.checked : false,
            profile_color: selectedProfileColor,
          }),
        });
        if (status) {
          status.textContent = result.ok ? 'Saved' : 'Could not save';
        }
        if (result.ok) {
          await load();
        }
      };
    }

    const editBio = document.getElementById('edit-bio');
    if (editBio instanceof HTMLButtonElement) {
      editBio.onclick = (): void => {
        const bioDisplay = document.getElementById('bio-display');
        if (!bioDisplay) return;
        bioDisplay.innerHTML = `<textarea id="bio-inline" maxlength="1000" placeholder="Tell people about yourself">${esc(profile.bio)}</textarea><div class="bio-edit-actions"><button id="save-bio" type="button">Save bio</button><button id="cancel-bio" class="bio-cancel-btn" type="button">Cancel</button><span id="bio-status" aria-live="polite"></span></div>`;
        editBio.style.display = 'none';
        const bioInput = document.getElementById('bio-inline');
        const cancelBio = document.getElementById('cancel-bio');
        const saveBio = document.getElementById('save-bio');
        const bioStatus = document.getElementById('bio-status');
        if (bioInput instanceof HTMLTextAreaElement) {
          bioInput.focus();
        }
        if (cancelBio instanceof HTMLButtonElement) {
          cancelBio.onclick = () => {
            void load();
          };
        }
        if (saveBio instanceof HTMLButtonElement && bioInput instanceof HTMLTextAreaElement) {
          saveBio.onclick = async (): Promise<void> => {
            saveBio.disabled = true;
            const result = await fetch('/api/users/me/profile', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                bio: bioInput.value,
                stats_visible: profile.stats_visible,
                profile_color: color,
              }),
            });
            if (result.ok) {
              await load();
            } else {
              if (bioStatus) bioStatus.textContent = 'Could not save';
              saveBio.disabled = false;
            }
          };
        }
      };
    }

    const createGift = document.getElementById('create-gift');
    if (createGift instanceof HTMLButtonElement) {
      createGift.onclick = async (): Promise<void> => {
        const giftCreditsInput = document.getElementById('gift-credits');
        const status = document.getElementById('gift-status');
        if (!(giftCreditsInput instanceof HTMLInputElement) || !status) return;
        const amount = Number(giftCreditsInput.value);
        if (!Number.isFinite(amount) || amount <= 0) {
          status.textContent = 'Enter a positive number of credits.';
          return;
        }
        createGift.disabled = true;
        status.textContent = 'Creating link…';
        try {
          const result = await fetch('/api/credit-gifts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ credits: amount }),
          });
          const data = (await result.json().catch(() => ({}))) as GiftCreateResponse;
          if (!result.ok || !data.link) {
            status.textContent = getErrorDetail(data, 'Could not create gift link.');
            return;
          }
          const expires = data.expires_at ? new Date(data.expires_at).toLocaleString() : '';
          status.innerHTML = `Share this link (expires ${expires}): <a href="${esc(data.link)}">${esc(data.link)}</a>`;
          if (navigator.clipboard) {
            navigator.clipboard.writeText(data.link).catch(() => {});
          }
        } catch {
          status.textContent = 'Network error while creating gift link.';
        } finally {
          createGift.disabled = false;
        }
      };
    }

    const startDeleteBtn = document.getElementById('start-delete-btn');
    const deleteConfirmBox = document.getElementById('delete-confirm-box');
    const deleteConfirmInput = document.getElementById('delete-confirm-input');
    const cancelDeleteBtn = document.getElementById('cancel-delete-btn');
    const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
    const deleteStatus = document.getElementById('delete-status');

    if (
      startDeleteBtn instanceof HTMLButtonElement &&
      deleteConfirmBox instanceof HTMLElement &&
      deleteConfirmInput instanceof HTMLInputElement &&
      cancelDeleteBtn instanceof HTMLButtonElement &&
      confirmDeleteBtn instanceof HTMLButtonElement &&
      deleteStatus instanceof HTMLElement
    ) {
      startDeleteBtn.onclick = () => {
        startDeleteBtn.style.display = 'none';
        deleteConfirmBox.style.display = 'block';
        deleteConfirmInput.value = '';
        confirmDeleteBtn.disabled = true;
        deleteConfirmInput.focus();
      };
      cancelDeleteBtn.onclick = () => {
        deleteConfirmBox.style.display = 'none';
        startDeleteBtn.style.display = 'inline-block';
        deleteStatus.textContent = '';
      };
      deleteConfirmInput.oninput = () => {
        confirmDeleteBtn.disabled = deleteConfirmInput.value.trim() !== profile.username;
      };
      confirmDeleteBtn.onclick = async (): Promise<void> => {
        if (deleteConfirmInput.value.trim() !== profile.username) return;
        confirmDeleteBtn.disabled = true;
        deleteConfirmInput.disabled = true;
        deleteStatus.textContent = 'Deleting account…';
        try {
          const resp = await fetch('/api/users/me', { method: 'DELETE' });
          if (resp.ok) {
            deleteStatus.style.color = '#86efac';
            deleteStatus.textContent = 'Account deleted. Redirecting…';
            window.setTimeout(() => {
              window.location.href = '/deploy';
            }, 1000);
          } else {
            const data = (await resp.json().catch(() => ({}))) as DetailResponse;
            deleteStatus.textContent = getErrorDetail(data, 'Could not delete account.');
            confirmDeleteBtn.disabled = false;
            deleteConfirmInput.disabled = false;
          }
        } catch {
          deleteStatus.textContent = 'Network error while deleting account.';
          confirmDeleteBtn.disabled = false;
          deleteConfirmInput.disabled = false;
        }
      };
    }

    const generateForm = document.getElementById('stamp-generate-form');
    if (generateForm instanceof HTMLFormElement) {
      fetch('/api/pricing')
        .then((res) => (res.ok ? (res.json() as Promise<PricingResponse>) : null))
        .then((rates) => {
          const costLabel = document.getElementById('stamp-generation-cost');
          if (costLabel && rates && Number.isFinite(rates.image_credit_rate)) {
            costLabel.textContent = `${rates.image_credit_rate} credits per stamp. Credits are charged when your stamp is saved.`;
          }
        })
        .catch(() => {});

      generateForm.onsubmit = async (event: SubmitEvent): Promise<void> => {
        event.preventDefault();
        const nameInput = document.getElementById('stamp-generate-name');
        const promptInput = document.getElementById('stamp-generate-prompt');
        const button = document.getElementById('stamp-generate-btn');
        const status = document.getElementById('stamp-generate-status');
        if (
          !(nameInput instanceof HTMLInputElement) ||
          !(promptInput instanceof HTMLTextAreaElement) ||
          !(button instanceof HTMLButtonElement) ||
          !status
        ) {
          return;
        }
        const name = nameInput.value.trim();
        const prompt = promptInput.value.trim();
        if (!name || !prompt) {
          status.textContent = 'Enter a stamp name and description.';
          return;
        }
        button.disabled = true;
        status.style.color = 'var(--muted)';
        status.textContent = 'Generating stamp… This may take a minute.';
        try {
          const res = await fetch('/api/users/me/stamps/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, prompt }),
          });
          const data = (await res.json().catch(() => ({}))) as DetailResponse;
          if (!res.ok) {
            throw new Error(getErrorDetail(data, 'Could not generate stamp.'));
          }
          await load('stamps');
        } catch (error: unknown) {
          status.style.color = '#f87171';
          const message = error instanceof Error ? error.message : '';
          status.textContent = message || 'Network error while generating stamp.';
        } finally {
          button.disabled = false;
        }
      };
    }

    const stampFileInput = document.getElementById('stamp-file-input');
    const stampFileChosen = document.getElementById('stamp-file-chosen');
    const stampUploadBtn = document.getElementById('stamp-upload-btn');
    const stampNameInput = document.getElementById('stamp-name-input');
    const stampUploadStatus = document.getElementById('stamp-upload-status');

    if (
      stampFileInput instanceof HTMLInputElement &&
      stampFileChosen &&
      stampNameInput instanceof HTMLInputElement
    ) {
      stampFileInput.onchange = () => {
        const file = stampFileInput.files?.[0];
        stampFileChosen.textContent = file ? file.name : 'No file chosen';
        if (file && !stampNameInput.value.trim()) {
          stampNameInput.value = file.name.replace(/\.[^/.]+$/, '');
        }
      };
    }

    if (
      stampUploadBtn instanceof HTMLButtonElement &&
      stampFileInput instanceof HTMLInputElement &&
      stampNameInput instanceof HTMLInputElement &&
      stampUploadStatus
    ) {
      stampUploadBtn.onclick = async (): Promise<void> => {
        const file = stampFileInput.files?.[0];
        if (!file) {
          stampUploadStatus.style.color = '#f87171';
          stampUploadStatus.textContent = 'Please choose an image file first.';
          return;
        }
        const formData = new FormData();
        formData.append('file', file);
        const customName = stampNameInput.value.trim();
        if (customName) {
          formData.append('name', customName);
        }
        stampUploadBtn.disabled = true;
        stampUploadStatus.textContent = 'Uploading stamp…';
        try {
          const resp = await fetch('/api/users/me/stamps', { method: 'POST', body: formData });
          const data = (await resp.json().catch(() => ({}))) as DetailResponse;
          if (resp.ok) {
            stampUploadStatus.style.color = '#86efac';
            stampUploadStatus.textContent = 'Stamp uploaded!';
            await load('stamps');
          } else {
            stampUploadStatus.style.color = '#f87171';
            stampUploadStatus.textContent = getErrorDetail(data, 'Could not upload stamp.');
            stampUploadBtn.disabled = false;
          }
        } catch {
          stampUploadStatus.style.color = '#f87171';
          stampUploadStatus.textContent = 'Network error while uploading stamp.';
          stampUploadBtn.disabled = false;
        }
      };
    }
  }

  const initialTab = window.location.hash === '#gifts' ? 'gifts' : 'overview';
  void load(initialTab);

  return { load, activateTab };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initializeProfilePage();
  });
} else {
  initializeProfilePage();
}

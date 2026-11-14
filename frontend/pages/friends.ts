/** Friend controls shared by profiles and the join page. */

import type {} from '../shared/auth-flow.js';

export type FriendStatus = 'incoming' | 'accepted' | 'outgoing';
export type FriendAction = 'request' | 'accept' | 'remove';

export interface ProfileSummary {
  readonly username: string;
  readonly is_owner?: boolean;
}

export interface FriendAuthState {
  readonly authenticated: boolean;
  readonly user?: {
    readonly username: string;
  } | null;
}

export interface FriendshipRecord {
  readonly username: string;
  readonly status: FriendStatus;
}

export interface FriendTheater {
  readonly username: string;
  readonly theater_id: string;
  readonly name?: string;
  readonly online?: boolean;
  readonly active_viewers?: readonly string[];
}

export interface FriendTheatersController {
  refresh(): Promise<void>;
  destroy(): void;
}

interface ApiErrorPayload {
  readonly detail?: unknown;
}

interface JoinTheaterPayload {
  readonly theater_id?: string;
  readonly detail?: unknown;
}

declare global {
  interface Window {
    setupProfileFriends?: (profile: ProfileSummary) => Promise<void>;
    activateTab?: (id: string) => void;
  }
}

const HTML_ESCAPE_MAP: Readonly<Record<string, string>> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

const FRIEND_STATE_HEADINGS: Readonly<Record<FriendStatus, string>> = {
  incoming: 'Incoming requests',
  accepted: 'Friends',
  outgoing: 'Sent requests',
};

const FRIEND_REMOVE_LABELS: Readonly<Record<FriendStatus, string>> = {
  incoming: 'Decline',
  accepted: 'Remove friend',
  outgoing: 'Cancel request',
};

function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (char) => HTML_ESCAPE_MAP[char] ?? char);
}

function profileLink(username: string): string {
  return `/users/${encodeURIComponent(username)}`;
}

function actionButtonMarkup(label: string, action: FriendAction, username: string): string {
  return `<button type="button" data-friend-action="${action}" data-friend-username="${escapeHtml(username)}">${label}</button>`;
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function isFriendAction(value: string | undefined): value is FriendAction {
  return value === 'request' || value === 'accept' || value === 'remove';
}

async function changeFriend(username: string, action: FriendAction): Promise<void> {
  const response = await fetch(`/api/friends/${encodeURIComponent(username)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  const data = (await response.json()) as ApiErrorPayload;
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' && data.detail ? data.detail : 'Could not update friendship.');
  }
}

export async function setupProfileFriends(profile: ProfileSummary): Promise<void> {
  const root = document.querySelector('.profile-shell');
  if (!root) return;

  const area = document.createElement('div');
  area.style.cssText = 'display:grid;gap:1rem;margin-top:1rem';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');

  if (profile.is_owner) {
    const navButton = document.createElement('button');
    navButton.type = 'button';
    navButton.className = 'tab-button';
    navButton.dataset.tab = 'friends';
    navButton.setAttribute('role', 'tab');
    navButton.setAttribute('aria-controls', 'friends');
    navButton.setAttribute('aria-selected', 'false');
    navButton.textContent = '♡ Friends & requests';
    navButton.onclick = () => window.activateTab?.('friends');
    root.querySelector('.tab-list')?.insertBefore(navButton, root.querySelector('[data-tab="danger"]'));

    const section = document.createElement('section');
    section.id = 'friends';
    section.className = 'tab-panel';
    section.setAttribute('role', 'tabpanel');
    section.innerHTML =
      '<h2>Friends & requests</h2><p class="panel-subtitle">Accept requests to become friends and join each other’s theaters.</p>';
    section.append(area, status);
    root.querySelector('.panel')?.append(section);
  } else {
    root.querySelector('.identity')?.after(area, status);
  }

  async function refresh(): Promise<void> {
    try {
      const response = await fetch('/api/friends', { cache: 'no-store' });
      if (response.status === 401) {
        area.textContent = 'Sign in to send a friend request.';
        return;
      }
      if (!response.ok) throw new Error('Could not load friends.');
      const friends = (await response.json()) as FriendshipRecord[];
      if (profile.is_owner) {
        const states: readonly FriendStatus[] = ['incoming', 'accepted', 'outgoing'];
        area.innerHTML = states
          .map((state) => {
            const entries = friends.filter((friend) => friend.status === state);
            const heading = FRIEND_STATE_HEADINGS[state];
            const rows = entries.length
              ? entries
                  .map(
                    (friend) =>
                      `<div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:.8rem 0"><a style="color:#c7d2fe" href="${profileLink(friend.username)}">${escapeHtml(friend.username)}</a>${state === 'incoming' ? actionButtonMarkup('Accept', 'accept', friend.username) : ''}${actionButtonMarkup(FRIEND_REMOVE_LABELS[state], 'remove', friend.username)}</div>`,
                  )
                  .join('')
              : '<p class="empty">None yet.</p>';
            return `<div><h3>${heading}</h3>${rows}</div>`;
          })
          .join('');
      } else {
        const relationship = friends.find(
          (friend) => friend.username.toLowerCase() === profile.username.toLowerCase(),
        );
        const state = relationship?.status;
        area.innerHTML =
          state === 'accepted'
            ? `<span>Friends</span>${actionButtonMarkup('Remove friend', 'remove', profile.username)}`
            : state === 'outgoing'
              ? `<span>Friend request sent</span>${actionButtonMarkup('Cancel request', 'remove', profile.username)}`
              : state === 'incoming'
                ? `${actionButtonMarkup('Accept friend request', 'accept', profile.username)}${actionButtonMarkup('Decline', 'remove', profile.username)}`
                : actionButtonMarkup('Send Friend Request', 'request', profile.username);
      }
    } catch (error: unknown) {
      status.textContent = getErrorMessage(error, 'Could not load friends.');
    }
  }

  area.onclick = async (event: MouseEvent): Promise<void> => {
    const rawTarget = event.target instanceof Element ? event.target.closest('[data-friend-action]') : null;
    if (!(rawTarget instanceof HTMLButtonElement) || rawTarget.disabled) return;
    const targetUsername = rawTarget.dataset.friendUsername;
    const action = rawTarget.dataset.friendAction;
    if (!targetUsername || !isFriendAction(action)) return;
    rawTarget.disabled = true;
    status.textContent = '';
    try {
      await changeFriend(targetUsername, action);
      await refresh();
    } catch (error: unknown) {
      status.textContent = getErrorMessage(error, 'Could not update friendship.');
      rawTarget.disabled = false;
    }
  };

  await refresh();
}

export async function startFriendTheaters(): Promise<FriendTheatersController | null> {
  const rawSection = document.getElementById('friend-theaters');
  const rawList = document.getElementById('friend-theaters-list');
  const rawStatus = document.getElementById('friend-theaters-status');
  const rawFooter = rawSection?.querySelector('.friend-theaters-footer');
  if (!rawSection || !rawList || !rawStatus || !(rawFooter instanceof HTMLElement)) {
    return null;
  }

  const section = rawSection;
  const list = rawList;
  const status = rawStatus;
  const footer = rawFooter;

  let selectedUsername: string | null = null;
  let selectedTheaterId: string | null = null;
  let joining = false;
  let friends = new Map<string, FriendTheater[]>();

  function showSelectedTheaters(): void {
    const details = document.getElementById('friend-active-theaters');
    const rawJoin = document.getElementById('friend-theater-join');
    if (!details || !(rawJoin instanceof HTMLButtonElement)) return;
    const join = rawJoin;
    const friendButtons = Array.from(list.querySelectorAll<HTMLButtonElement>('.friend-username'));
    const selected = friendButtons.find((friend) => friend.dataset.username === selectedUsername);
    join.disabled = !selectedTheaterId;
    friendButtons.forEach((friend) => {
      friend.setAttribute('aria-pressed', String(friend === selected));
    });
    details.replaceChildren();
    if (!selected || !selectedUsername) {
      details.innerHTML = '<p>Select a friend to see their active theaters.</p>';
      return;
    }
    for (const theater of friends.get(selectedUsername) ?? []) {
      const option = document.createElement('label');
      option.className = 'friend-theater-option';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'friend-theater';
      radio.value = theater.theater_id;
      radio.checked = selectedTheaterId === theater.theater_id;
      radio.onchange = () => {
        selectedTheaterId = theater.theater_id;
        join.disabled = false;
        status.textContent = '';
      };
      const name = document.createElement('span');
      name.className = 'friend-theater-name';
      const displayName = theater.name || theater.theater_id;
      name.textContent = displayName;
      name.title = displayName;
      const info = document.createElement('span');
      info.className = 'friend-theater-info';
      const viewers = document.createElement('small');
      viewers.className = 'friend-theater-viewers';
      const activeViewers = theater.active_viewers ?? [];
      viewers.textContent = `${activeViewers.length} ${activeViewers.length === 1 ? 'viewer' : 'viewers'}`;
      viewers.title = activeViewers.length
        ? `Viewing: ${activeViewers.map((username) => `@${username}`).join(', ')}`
        : 'No signed-in viewers';
      radio.setAttribute('aria-label', displayName);
      info.append(name, viewers);
      option.append(radio, info);
      details.append(option);
    }
  }

  async function refresh(): Promise<void> {
    if (document.hidden || joining) return;
    try {
      const auth: FriendAuthState = window.getAuthState
        ? await window.getAuthState()
        : { authenticated: false };
      if (!auth.authenticated) {
        footer.hidden = true;
        selectedUsername = null;
        selectedTheaterId = null;
        list.innerHTML =
          '<p>Sign in to see your friends’ theaters and join them in one click.</p><button type="button" class="join-btn" id="friend-theaters-sign-in">Sign in</button>';
        const signInBtn = document.getElementById('friend-theaters-sign-in');
        if (signInBtn) {
          signInBtn.onclick = () => window.openAuthModal?.('login');
        }
        return;
      }
      section.hidden = false;
      const response = await fetch('/api/friend-theaters', { cache: 'no-store' });
      if (!response.ok) throw new Error('Could not load friends’ theaters.');
      const allTheaters = (await response.json()) as FriendTheater[];
      const theaters = allTheaters.filter((theater) => Boolean(theater.online));
      footer.hidden = !theaters.length;
      friends = new Map<string, FriendTheater[]>();
      for (const theater of theaters) {
        const existing = friends.get(theater.username);
        if (existing) {
          existing.push(theater);
        } else {
          friends.set(theater.username, [theater]);
        }
      }
      if (!selectedUsername || !friends.has(selectedUsername)) {
        selectedUsername = null;
      }
      if (
        !selectedUsername ||
        !friends.get(selectedUsername)?.some((theater) => theater.theater_id === selectedTheaterId)
      ) {
        selectedTheaterId = null;
      }
      const usernamesScroll = list.querySelector('.friend-usernames')?.scrollTop ?? 0;
      const theatersScroll = list.querySelector('#friend-active-theaters')?.scrollTop ?? 0;
      list.replaceChildren();
      const currentUsername = auth.user?.username ?? '';
      if (!theaters.length) {
        const friendshipsResponse = await fetch('/api/friends', { cache: 'no-store' });
        if (!friendshipsResponse.ok) throw new Error('Could not load friends.');
        const friendships = (await friendshipsResponse.json()) as FriendshipRecord[];
        const hasAcceptedFriend = friendships.some((friend) => friend.status === 'accepted');
        list.innerHTML = hasAcceptedFriend
          ? `<p>No friends have active theaters right now. Manage <a href="${profileLink(currentUsername)}">your friends</a>.</p>`
          : `<p>You do not have any friends yet.</p><a href="${profileLink(currentUsername)}#gifts" class="join-btn">Create a gift link</a>`;
      } else {
        list.innerHTML =
          '<div class="friend-theaters-browser"><nav class="friend-usernames" aria-label="Friends with active theaters"></nav><div id="friend-active-theaters" class="friend-active-theaters" role="region" aria-label="Active theaters"></div></div>';
        const joinBtn = document.getElementById('friend-theater-join');
        if (joinBtn instanceof HTMLButtonElement) {
          joinBtn.onclick = async (event: MouseEvent): Promise<void> => {
            const join = event.currentTarget;
            if (!(join instanceof HTMLButtonElement)) return;
            const theaterId = selectedTheaterId;
            if (!theaterId || joining) return;
            joining = true;
            join.disabled = true;
            status.textContent = '';
            const controls = section.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
              'input, .friend-username',
            );
            controls.forEach((control) => {
              control.disabled = true;
            });
            try {
              const result = await fetch(`/api/friend-theaters/${encodeURIComponent(theaterId)}/join`, {
                method: 'POST',
              });
              const data = (await result.json()) as JoinTheaterPayload;
              if (!result.ok || !data.theater_id) {
                throw new Error(
                  typeof data.detail === 'string' && data.detail ? data.detail : 'Could not join theater.',
                );
              }
              window.location.href = `/canvas?theater_id=${encodeURIComponent(data.theater_id)}`;
            } catch (error: unknown) {
              status.textContent = getErrorMessage(error, 'Could not join theater.');
              joining = false;
              join.disabled = false;
              controls.forEach((control) => {
                control.disabled = false;
              });
            }
          };
        }
      }

      const usernamesNav = list.querySelector('.friend-usernames');
      if (usernamesNav) {
        for (const username of [...friends.keys()].sort((a, b) => a.localeCompare(b))) {
          const friend = document.createElement('button');
          friend.type = 'button';
          friend.className = 'friend-username';
          friend.dataset.username = username;
          friend.textContent = username;
          friend.setAttribute('aria-controls', 'friend-active-theaters');
          friend.onclick = () => {
            if (selectedUsername !== username) selectedTheaterId = null;
            selectedUsername = username;
            status.textContent = '';
            showSelectedTheaters();
          };
          usernamesNav.append(friend);
        }
      }

      showSelectedTheaters();
      if (theaters.length) {
        const activeList = list.querySelector('#friend-active-theaters');
        if (usernamesNav) usernamesNav.scrollTop = usernamesScroll;
        if (activeList) activeList.scrollTop = theatersScroll;
      }
    } catch (error: unknown) {
      section.hidden = false;
      status.textContent = getErrorMessage(error, 'Could not load friends’ theaters.');
    }
  }

  await refresh();
  const intervalId = window.setInterval(() => {
    void refresh();
  }, 15000);
  const onVisibilityChange = (): void => {
    if (!document.hidden) void refresh();
  };
  document.addEventListener('visibilitychange', onVisibilityChange);

  return {
    refresh,
    destroy(): void {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    },
  };
}

window.setupProfileFriends = setupProfileFriends;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    void startFriendTheaters();
  });
} else {
  void startFriendTheaters();
}

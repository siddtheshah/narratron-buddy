/* Friend controls shared by profiles and the join page. */
(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const profileLink = username => `/users/${encodeURIComponent(username)}`;
  const button = (label, action, username) => `<button type="button" data-friend-action="${action}" data-friend-username="${escape(username)}">${label}</button>`;

  async function changeFriend(username, action) {
    const response = await fetch(`/api/friends/${encodeURIComponent(username)}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not update friendship.');
  }

  window.setupProfileFriends = async profile => {
    const root = document.querySelector('.profile-shell');
    if (!root) return;
    const area = document.createElement('div');
    area.style.cssText = 'display:grid;gap:1rem;margin-top:1rem';
    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    if (profile.is_owner) {
      const navButton = document.createElement('button');
      navButton.type = 'button'; navButton.className = 'tab-button';
      navButton.dataset.tab = 'friends'; navButton.setAttribute('role', 'tab');
      navButton.setAttribute('aria-controls', 'friends'); navButton.setAttribute('aria-selected', 'false');
      navButton.textContent = '♡ Friends & requests';
      navButton.onclick = () => activateTab('friends');
      root.querySelector('.tab-list').insertBefore(navButton, root.querySelector('[data-tab="danger"]'));
      const section = document.createElement('section');
      section.id = 'friends'; section.className = 'tab-panel'; section.setAttribute('role', 'tabpanel');
      section.innerHTML = '<h2>Friends & requests</h2><p class="panel-subtitle">Accept requests to become friends and join each other’s theaters.</p>';
      section.append(area, status); root.querySelector('.panel').append(section);
    } else {
      root.querySelector('.identity').after(area, status);
    }
    async function refresh() {
      try {
        const response = await fetch('/api/friends', {cache: 'no-store'});
        if (response.status === 401) { area.textContent = 'Sign in to send a friend request.'; return; }
        if (!response.ok) throw new Error('Could not load friends.');
        const friends = await response.json();
        if (profile.is_owner) {
          area.innerHTML = ['incoming', 'accepted', 'outgoing'].map(state => {
            const entries = friends.filter(friend => friend.status === state);
            const heading = {incoming: 'Incoming requests', accepted: 'Friends', outgoing: 'Sent requests'}[state];
            return `<div><h3>${heading}</h3>${entries.length ? entries.map(friend => `<div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:.8rem 0"><a style="color:#c7d2fe" href="${profileLink(friend.username)}">${escape(friend.username)}</a>${state === 'incoming' ? button('Accept', 'accept', friend.username) : ''}${button({incoming:'Decline',accepted:'Remove friend',outgoing:'Cancel request'}[state], 'remove', friend.username)}</div>`).join('') : '<p class="empty">None yet.</p>'}</div>`;
          }).join('');
        } else {
          const relationship = friends.find(friend => friend.username.toLowerCase() === profile.username.toLowerCase());
          const state = relationship?.status;
          area.innerHTML = state === 'accepted' ? `<span>Friends</span>${button('Remove friend', 'remove', profile.username)}` :
            state === 'outgoing' ? `<span>Friend request sent</span>${button('Cancel request', 'remove', profile.username)}` :
            state === 'incoming' ? `${button('Accept friend request', 'accept', profile.username)}${button('Decline', 'remove', profile.username)}` :
            button('Send Friend Request', 'request', profile.username);
        }
      } catch (error) { status.textContent = error.message; }
    }
    area.onclick = async event => {
      const target = event.target.closest('[data-friend-action]');
      if (!target || target.disabled) return;
      target.disabled = true; status.textContent = '';
      try { await changeFriend(target.dataset.friendUsername, target.dataset.friendAction); await refresh(); }
      catch (error) { status.textContent = error.message; target.disabled = false; }
    };
    await refresh();
  };

  async function startFriendTheaters() {
    const section = document.getElementById('friend-theaters');
    if (!section) return;
    const list = document.getElementById('friend-theaters-list');
    const status = document.getElementById('friend-theaters-status');
    const footer = section.querySelector('.friend-theaters-footer');
    let selectedUsername = null;
    let selectedTheaterId = null;
    let joining = false;
    let friends = new Map();
    function showSelectedTheaters() {
      const details = document.getElementById('friend-active-theaters');
      if (!details) return;
      const selected = Array.from(list.querySelectorAll('.friend-username')).find(friend => friend.dataset.username === selectedUsername);
      const join = document.getElementById('friend-theater-join');
      join.disabled = !selectedTheaterId;
      list.querySelectorAll('.friend-username').forEach(friend => {
        friend.setAttribute('aria-pressed', String(friend === selected));
      });
      details.replaceChildren();
      if (!selected) {
        details.innerHTML = '<p>Select a friend to see their active theaters.</p>';
        return;
      }
      for (const theater of friends.get(selectedUsername)) {
        const option = document.createElement('label');
        option.className = 'friend-theater-option';
        const radio = document.createElement('input');
        radio.type = 'radio'; radio.name = 'friend-theater';
        radio.value = theater.theater_id;
        radio.checked = selectedTheaterId === theater.theater_id;
        radio.onchange = () => {
          selectedTheaterId = theater.theater_id;
          join.disabled = false;
          status.textContent = '';
        };
        const name = document.createElement('span');
        name.className = 'friend-theater-name';
        name.textContent = theater.name || theater.theater_id;
        name.title = name.textContent;
        const info = document.createElement('span');
        info.className = 'friend-theater-info';
        const viewers = document.createElement('small');
        viewers.className = 'friend-theater-viewers';
        const activeViewers = theater.active_viewers || [];
        viewers.textContent = `${activeViewers.length} ${activeViewers.length === 1 ? 'viewer' : 'viewers'}`;
        viewers.title = activeViewers.length
          ? `Viewing: ${activeViewers.map(username => `@${username}`).join(', ')}`
          : 'No signed-in viewers';
        radio.setAttribute('aria-label', name.textContent);
        info.append(name, viewers);
        option.append(radio, info);
        details.append(option);
      }
    }
    async function refresh() {
      if (document.hidden || joining) return;
      try {
        const auth = await getAuthState();
        if (!auth.authenticated) {
          footer.hidden = true;
          selectedUsername = null;
          selectedTheaterId = null;
          list.innerHTML = '<p>Sign in to see your friends’ theaters and join them in one click.</p><button type="button" class="join-btn" id="friend-theaters-sign-in">Sign in</button>';
          document.getElementById('friend-theaters-sign-in').onclick = () => openAuthModal('login');
          return;
        }
        section.hidden = false;
        const response = await fetch('/api/friend-theaters', {cache: 'no-store'});
        if (!response.ok) throw new Error('Could not load friends’ theaters.');
        const theaters = (await response.json()).filter(theater => theater.online);
        footer.hidden = !theaters.length;
        friends = new Map();
        for (const theater of theaters) {
          if (!friends.has(theater.username)) friends.set(theater.username, []);
          friends.get(theater.username).push(theater);
        }
        if (!friends.has(selectedUsername)) selectedUsername = null;
        if (!friends.get(selectedUsername)?.some(theater => theater.theater_id === selectedTheaterId)) selectedTheaterId = null;
        const usernamesScroll = list.querySelector('.friend-usernames')?.scrollTop || 0;
        const theatersScroll = list.querySelector('#friend-active-theaters')?.scrollTop || 0;
        list.replaceChildren();
        if (!theaters.length) {
          const friendshipsResponse = await fetch('/api/friends', {cache: 'no-store'});
          if (!friendshipsResponse.ok) throw new Error('Could not load friends.');
          const friendships = await friendshipsResponse.json();
          const hasAcceptedFriend = friendships.some(friend => friend.status === 'accepted');
          list.innerHTML = hasAcceptedFriend
            ? `<p>No friends have active theaters right now. Manage <a href="${profileLink(auth.user.username)}">your friends</a>.</p>`
            : `<p>You do not have any friends yet.</p><a href="${profileLink(auth.user.username)}#gifts" class="join-btn">Create a gift link</a>`;
        } else {
          list.innerHTML = '<div class="friend-theaters-browser"><nav class="friend-usernames" aria-label="Friends with active theaters"></nav><div id="friend-active-theaters" class="friend-active-theaters" role="region" aria-label="Active theaters"></div></div>';
          document.getElementById('friend-theater-join').onclick = async event => {
            const join = event.currentTarget;
            const theaterId = selectedTheaterId;
            if (!theaterId || joining) return;
            joining = true;
            join.disabled = true; status.textContent = '';
            section.querySelectorAll('input, .friend-username').forEach(control => { control.disabled = true; });
            try {
              const result = await fetch(`/api/friend-theaters/${encodeURIComponent(theaterId)}/join`, {method: 'POST'});
              const data = await result.json();
              if (!result.ok) throw new Error(data.detail || 'Could not join theater.');
              location.href = `/canvas?theater_id=${encodeURIComponent(data.theater_id)}`;
            } catch (error) {
              status.textContent = error.message;
              joining = false;
              join.disabled = false;
              section.querySelectorAll('input, .friend-username').forEach(control => { control.disabled = false; });
            }
          };
        }
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
          list.querySelector('.friend-usernames').append(friend);
        }
        showSelectedTheaters();
        if (theaters.length) {
          list.querySelector('.friend-usernames').scrollTop = usernamesScroll;
          list.querySelector('#friend-active-theaters').scrollTop = theatersScroll;
        }
      } catch (error) { section.hidden = false; status.textContent = error.message; }
    }
    await refresh();
    window.setInterval(refresh, 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startFriendTheaters);
  else startFriendTheaters();
})();

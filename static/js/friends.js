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
    async function refresh() {
      if (document.hidden) return;
      try {
        const auth = await getAuthState();
        if (!auth.authenticated) {
          list.innerHTML = '<p>Sign in to see your friends’ theaters and join them in one click.</p><button type="button" class="join-btn" id="friend-theaters-sign-in">Sign in</button>';
          document.getElementById('friend-theaters-sign-in').onclick = () => openAuthModal('login');
          return;
        }
        section.hidden = false;
        const response = await fetch('/api/friend-theaters', {cache: 'no-store'});
        if (!response.ok) throw new Error('Could not load friends’ theaters.');
        const theaters = await response.json();
        theaters.sort((a, b) => Number(b.online) - Number(a.online));
        list.replaceChildren();
        if (!theaters.length) {
          list.innerHTML = `<p>No friends' theaters yet. Send requests from their profiles, or manage <a href="${profileLink(auth.user.username)}">your friends</a>.</p>`;
        }
        for (const theater of theaters) {
          const join = document.createElement('button');
          join.type = 'button'; join.className = 'join-btn';
          join.style.cssText = 'display:block;width:100%;margin:.6rem 0;white-space:normal;text-align:center';
          join.textContent = `${theater.online ? '🟢' : '⚪'} ${theater.username} — ${theater.name || theater.theater_id} · ${theater.online ? 'Narratron online' : 'Narratron offline'}`;
          join.onclick = async () => {
            join.disabled = true; status.textContent = '';
            try {
              const result = await fetch(`/api/friend-theaters/${encodeURIComponent(theater.theater_id)}/join`, {method: 'POST'});
              const data = await result.json();
              if (!result.ok) throw new Error(data.detail || 'Could not join theater.');
              location.href = `/canvas?theater_id=${encodeURIComponent(data.theater_id)}`;
            } catch (error) { status.textContent = error.message; join.disabled = false; }
          };
          list.append(join);
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

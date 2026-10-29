const COLORS = ['#fbbf24', '#60a5fa', '#f472b6', '#34d399', '#c4b5fd', '#fb923c', '#22d3ee', '#f87171'];

// Use media identity and creation time: repeated presentations of the same image
// are separate history entries, while appending history does not move bookmarks.
function historyKey(item) {
    return JSON.stringify([item.path || item.url || item.animation, item.time || 0]);
}

function imageName(item) {
    const path = (item.path || item.url || '').split(/[?#]/)[0];
    const filename = path.split(/[\\/]/).pop().replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
    const name = (item.name || filename || item.prompt || item.animation?.scene_prompt || 'Untitled image').trim();
    return name.length > 36 ? `${name.slice(0, 33).trimEnd()}…` : name;
}

export function initializeCanvasBookmarks({ theaterId, isOrator, getHistory, getIndex, navigate }) {
    const root = document.getElementById('canvas-bookmarks');
    if (!root) return { refresh() {} };
    root.innerHTML = `
        <button id="canvas-bookmark-toggle" type="button" aria-label="Bookmark current page"
            aria-pressed="false" aria-expanded="false" aria-controls="canvas-bookmark-panel">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4z"/>
            </svg><span class="bookmark-current-number" aria-hidden="true"></span>
        </button>
        <div id="canvas-bookmark-panel" aria-label="Bookmarked pages" hidden>
            <div class="bookmark-panel-title">Bookmarks</div><div class="bookmark-list"></div>
        </div>
        <span class="bookmark-flash" aria-hidden="true"></span>
        <span class="bookmark-status" role="status" aria-live="polite"></span>`;
    const toggle = root.querySelector('button');
    const panel = root.querySelector('#canvas-bookmark-panel');
    const list = root.querySelector('.bookmark-list');
    const flash = root.querySelector('.bookmark-flash');
    const status = root.querySelector('.bookmark-status');
    const storageKey = `narratron-canvas-bookmarks:${theaterId || 'default'}`;
    let bookmarks = [];
    try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
        if (Array.isArray(saved)) bookmarks = saved.filter(entry => entry && typeof entry.key === 'string'
            && Number.isSafeInteger(entry.number) && entry.number > 0);
    } catch (error) { console.warn('Could not load canvas bookmarks', error); }
    let signature = '';
    let busy = false;
    let flashTimer;
    const color = number => COLORS[(number - 1) % COLORS.length];

    function expand(open) {
        panel.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
    }

    function refresh() {
        root.hidden = !isOrator();
        if (root.hidden) expand(false);
        const history = getHistory();
        const current = history[getIndex()];
        const selected = current && bookmarks.find(entry => entry.key === historyKey(current));
        toggle.disabled = !current || busy;
        toggle.setAttribute('aria-pressed', String(Boolean(selected)));
        toggle.setAttribute('aria-label', selected ? `Remove bookmark ${selected.number}` : 'Bookmark current page');
        toggle.title = selected ? `Remove bookmark ${selected.number} · Hover to browse` : 'Bookmark current page · Hover to browse';
        toggle.style.setProperty('--bookmark-color', selected ? color(selected.number) : '#cbd5e1');
        root.querySelector('.bookmark-current-number').textContent = selected ? selected.number : '';
        const entries = bookmarks.map(entry => ({ ...entry, index: history.findIndex(item => historyKey(item) === entry.key) }))
            .filter(entry => entry.index >= 0);
        const nextSignature = JSON.stringify([entries, getIndex(), busy, entries.map(entry => imageName(history[entry.index]))]);
        if (signature === nextSignature) return;
        signature = nextSignature;
        list.replaceChildren();
        if (!entries.length) {
            const empty = document.createElement('p');
            empty.className = 'bookmark-empty';
            empty.textContent = 'Click the bookmark to save this page.';
            list.append(empty);
        }
        for (const entry of entries) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'bookmark-item';
            button.disabled = busy;
            button.setAttribute('aria-current', String(entry.index === getIndex()));
            button.title = imageName(history[entry.index]);
            const number = document.createElement('span');
            number.className = 'bookmark-number';
            number.style.setProperty('--bookmark-color', color(entry.number));
            number.textContent = entry.number;
            const label = document.createElement('span');
            label.className = 'bookmark-name';
            label.textContent = imageName(history[entry.index]);
            button.append(number, label);
            button.addEventListener('click', async () => {
                if (!isOrator() || busy) return;
                busy = true;
                let navigated = false;
                refresh();
                try {
                    // Resolve against the latest history, including any pages added since hover.
                    const index = getHistory().findIndex(item => historyKey(item) === entry.key);
                    if (index < 0) throw new Error('This bookmarked page is no longer available.');
                    await navigate(index);
                    navigated = true;
                } catch (error) {
                    status.textContent = 'Could not open bookmark. Try again.';
                    console.error('Could not navigate to bookmark', error);
                } finally {
                    busy = false;
                    refresh();
                    if (navigated) {
                        toggle.focus();
                        expand(false);
                    }
                }
            });
            list.append(button);
        }
    }

    toggle.addEventListener('click', () => {
        const item = getHistory()[getIndex()];
        if (!isOrator() || !item || busy) return;
        const key = historyKey(item);
        const existing = bookmarks.find(entry => entry.key === key);
        let number = existing?.number || 1;
        if (existing) bookmarks = bookmarks.filter(entry => entry.key !== key);
        else {
            while (bookmarks.some(entry => entry.number === number)) number++;
            bookmarks.push({ key, number });
            bookmarks.sort((a, b) => a.number - b.number);
        }
        try { localStorage.setItem(storageKey, JSON.stringify(bookmarks)); }
        catch (error) { console.warn('Could not save canvas bookmarks', error); }
        refresh();
        status.textContent = `Bookmark ${number} ${existing ? 'removed' : 'added'}.`;
        flash.textContent = number;
        flash.style.setProperty('--bookmark-color', color(number));
        flash.classList.remove('flashing');
        void flash.offsetWidth;
        flash.classList.add('flashing');
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => flash.classList.remove('flashing'), 1000);
    });
    root.addEventListener('mouseenter', () => expand(true));
    root.addEventListener('mouseleave', () => expand(false));
    root.addEventListener('focusin', () => expand(true));
    root.addEventListener('focusout', event => { if (!root.contains(event.relatedTarget)) expand(false); });
    root.addEventListener('keydown', event => {
        if (event.key === 'Escape') { toggle.focus(); expand(false); event.stopPropagation(); }
        if (event.key === 'ArrowDown' && event.target === toggle) {
            event.preventDefault();
            expand(true);
            list.querySelector('button')?.focus();
        }
    });
    window.addEventListener('storage', event => {
        if (event.key !== storageKey) return;
        try {
            const saved = JSON.parse(event.newValue || '[]');
            if (Array.isArray(saved)) bookmarks = saved.filter(entry => entry && typeof entry.key === 'string'
                && Number.isSafeInteger(entry.number) && entry.number > 0);
            refresh();
        } catch (error) { console.warn('Could not sync canvas bookmarks', error); }
    });
    refresh();
    return { refresh };
}

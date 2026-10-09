// Keep REST state current even when socket notifications are missed.
window.startCanvasStatePolling = function (refresh) {
    const intervalMs = 1000;
    const inactivityTimeoutMs = 30 * 60 * 1000;
    let timer = null;
    let lastRefresh = -Infinity;
    let lastActivity = Date.now();

    function isActive() {
        // Visible windows keep polling even when another window has focus.
        return !document.hidden && Date.now() - lastActivity < inactivityTimeoutMs;
    }

    function stopPolling() {
        if (timer !== null) clearInterval(timer);
        timer = null;
    }

    function poll() {
        if (!isActive()) {
            stopPolling();
            return;
        }
        if (Date.now() - lastRefresh < intervalMs) return;
        lastRefresh = Date.now();
        refresh();
    }

    function syncActivity() {
        if (!isActive()) {
            stopPolling();
            return;
        }
        // Mouse movement also catches up after the browser suspends timers.
        poll();
        if (timer === null) timer = setInterval(poll, intervalMs);
    }

    function recordActivity() {
        if (!document.hidden) lastActivity = Date.now();
        syncActivity();
    }

    document.addEventListener('visibilitychange', recordActivity);
    window.addEventListener('focus', recordActivity);
    document.addEventListener('mousemove', recordActivity, { passive: true });
    document.addEventListener('click', recordActivity, true);
    document.addEventListener('keydown', recordActivity);
    document.addEventListener('wheel', recordActivity, { passive: true });
    document.addEventListener('touchstart', recordActivity, { passive: true });
    syncActivity();
};

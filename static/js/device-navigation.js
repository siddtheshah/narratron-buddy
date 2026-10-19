(() => {
    'use strict';
    const smallScreen = window.matchMedia('(max-width: 700px)');
    const mobileDevice = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    function isMobile() {
        return mobileDevice || smallScreen.matches;
    }
    function theaterUrl(theaterId) {
        const path = isMobile() ? '/remote' : '/canvas';
        return `${path}?${new URLSearchParams({theater_id: theaterId})}`;
    }
    function update() {
        document.documentElement.classList.toggle('mobile-device', isMobile());
        if (isMobile() && location.pathname === '/canvas') {
            const theaterId = new URLSearchParams(location.search).get('theater_id');
            location.replace(theaterId ? theaterUrl(theaterId) : '/deploy');
        }
    }
    window.NarratronNavigation = {isMobile, theaterUrl};
    smallScreen.addEventListener('change', update);
    update();
})();

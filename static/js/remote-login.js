window.onAuthSuccess = action => {
    if (action === 'login' || action === 'register') location.reload();
};
document.getElementById('remote-sign-in').addEventListener('click', () => openAuthModal('login'));
getAuthState().then(auth => {
    if (auth.authenticated) location.reload();
    else openAuthModal('login');
}).catch(() => openAuthModal('login'));

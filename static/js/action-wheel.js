export function actionWheelDirection(dx, dy, threshold = 48) {
    if (Math.hypot(dx, dy) < threshold) return null;
    const deg = Math.atan2(dy, dx) * (180 / Math.PI);
    if (deg >= -120 && deg < -60) return 'up';
    if (deg >= -60 && deg < 0) return 'upright';
    if (deg >= 0 && deg < 60) return 'downright';
    if (deg >= 60 && deg < 120) return 'down';
    if (deg >= 120 && deg < 180) return 'downleft';
    if (deg >= 180 || deg < -120) return 'upleft';
    return null;
}

export function initializeActionWheel({
    isOrator,
    sendAction,
    onPreviousImage = null,
    onRebindStart = () => {},
    document: doc = document,
    window: win = window,
    canvasTarget = null,
}) {
    const rebind = doc.getElementById('action-wheel-rebind');
    const disable = doc.getElementById('action-wheel-disable');
    const reset = doc.getElementById('action-wheel-reset');
    const bindingLabel = doc.getElementById('action-wheel-binding-label');
    const status = doc.getElementById('action-wheel-status');
    const pinStatus = doc.getElementById('action-wheel-pin-status');
    const wheel = doc.getElementById('orator-action-wheel');
    const modifierKeys = ['ctrlKey', 'altKey', 'shiftKey', 'metaKey'];
    const modifiers = event => Object.fromEntries(modifierKeys.map(key => [key, event[key]]));
    const actions = {
        up: 'toggle_canvas_pin',
        down: 'toggle_music_pin',
        upleft: 'previous_image',
        downleft: 'previous_music',
        upright: 'new_image',
        downright: 'new_music',
    };
    const labels = {
        up: 'Pin image',
        down: 'Pin music',
        upleft: 'Previous image',
        downleft: 'Previous music',
        upright: 'New image',
        downright: 'New music',
    };
    const defaultBinding = { type: 'mouse', button: 2, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false };
    const validBinding = value => value !== null && modifierKeys.every(key => typeof value[key] === 'boolean') &&
        ((value.type === 'mouse' && Number.isInteger(value.button) && value.button >= 0) ||
         (value.type === 'key' && typeof value.code === 'string' && value.code.length > 0));
    let binding = defaultBinding;
    let rebinding = false;
    let drag = null;
    let busy = false;
    let suppressedButton = null;
    let suppressUntil = 0;
    let hideTimer;
    let pointer = { clientX: win.innerWidth / 2, clientY: win.innerHeight / 2 };
    let currentCanvasPinned = false;
    let currentMusicPinned = false;
    try {
        const saved = win.localStorage.getItem('narratron_action_wheel_binding');
        if (saved !== null) {
            const value = JSON.parse(saved);
            if (value === null || validBinding(value)) binding = value;
        } else {
            const legacy = win.localStorage.getItem('narratron_action_wheel_button');
            if (legacy === '-1') binding = null;
            else if (legacy !== null && Number.isInteger(Number(legacy)) && Number(legacy) >= 0) binding = { ...defaultBinding, button: Number(legacy) };
        }
    } catch (_) { /* Storage can be disabled by the browser. */ }
    const bindingName = () => {
        if (!binding) return 'Disabled';
        const names = ['Ctrl', 'Alt', 'Shift', 'Meta'].filter((_, index) => binding[modifierKeys[index]]);
        names.push(binding.type === 'mouse'
            ? (['Left mouse', 'Middle mouse', 'Right mouse', 'Back mouse', 'Forward mouse'][binding.button] || `Mouse button ${binding.button}`)
            : binding.code.replace(/^Key/, '').replace(/^Digit/, '').replace('Space', 'Spacebar'));
        return names.join(' + ');
    };
    const updateBindingUI = () => {
        bindingLabel.textContent = rebinding ? 'Press a mouse button or key combo… (Esc cancels)' : `Action Wheel: ${bindingName()}`;
        rebind.classList.toggle('rebinding', rebinding);
        if (disable) {
            disable.textContent = binding ? 'Disable action wheel' : 'Enable action wheel (Right click)';
            disable.disabled = rebinding;
        }
    };
    const save = () => {
        try { win.localStorage.setItem('narratron_action_wheel_binding', JSON.stringify(binding)); } catch (_) {}
        updateBindingUI();
    };
    const show = message => { win.clearTimeout(hideTimer); status.textContent = message; status.hidden = false; };
    const consume = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    const suppressClick = button => { suppressedButton = button; suppressUntil = Date.now() + 500; };
    const cancel = () => {
        if (drag?.type === 'mouse') suppressClick(drag.button);
        drag = null;
        wheel.hidden = true;
        wheel.dataset.selected = '';
        for (const item of wheel.querySelectorAll('[data-direction]')) item.classList.remove('selected');
        status.hidden = true;
    };
    const getCanvas = () => {
        if (typeof canvasTarget === 'function') return canvasTarget();
        if (typeof canvasTarget === 'string') return doc.querySelector(canvasTarget);
        if (canvasTarget) return canvasTarget;
        return doc.getElementById('image-container') || doc.getElementById('canvasStage') || doc.querySelector('canvas:not(#orator-action-wheel canvas)');
    };
    const isPointOverCanvas = (x, y) => {
        const canvas = getCanvas();
        if (!canvas) return true;
        if (typeof canvas.getBoundingClientRect !== 'function') return true;
        const rect = canvas.getBoundingClientRect();
        return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
    };
    const isOverCanvas = (target, x = pointer?.clientX, y = pointer?.clientY) => {
        const canvas = getCanvas();
        if (!canvas) return true;
        if (target && target !== doc && target !== doc.body && target !== doc.documentElement) {
            if (target === canvas || canvas.contains(target)) return true;
            return false;
        }
        return isPointOverCanvas(x, y);
    };
    const excluded = target => Boolean(target.closest('input,textarea,select,button,a,[contenteditable="true"],[role="dialog"]'));
    const managesBinding = target => Boolean(target.closest(
        '#action-wheel-disable,#action-wheel-reset,#menu-item-hotkey,#menu-item-text-hotkey,#mic-config-close-btn,#mic-config-done-btn'
    ));
    const excludedKey = target => {
        const dialog = target.closest('[role="dialog"]');
        return Boolean(target.closest('input,textarea,select,[contenteditable="true"]') ||
            (dialog && dialog.getAttribute('aria-hidden') !== 'true'));
    };
    const matchModifiers = event => modifierKeys.every(key => event[key] === binding[key]);
    const selection = () => {
        if (Math.max(Math.abs(pointer.clientX - drag.x), Math.abs(pointer.clientY - drag.y)) < 8) return null;
        return actionWheelDirection(pointer.clientX - drag.centerX, pointer.clientY - drag.centerY);
    };
    const open = (event, trigger) => {
        const centerX = Math.max(140, Math.min(win.innerWidth - 140, pointer.clientX));
        const centerY = Math.max(140, Math.min(win.innerHeight - 140, pointer.clientY));
        drag = { ...trigger, x: pointer.clientX, y: pointer.clientY, centerX, centerY };
        wheel.style.left = `${centerX}px`; wheel.style.top = `${centerY}px`; wheel.hidden = false;
        wheel.dataset.selected = '';
        consume(event);
        show('Move to an action and release · Release in the center to cancel');
    };
    const finish = async event => {
        const direction = selection();
        cancel(); consume(event);
        if (!direction || !isOrator()) return;
        busy = true;
        const action = actions[direction];
        if (action === 'new_image' && currentCanvasPinned) {
            controller.updateState(false, currentMusicPinned);
        } else if (action === 'new_music' && currentMusicPinned) {
            controller.updateState(currentCanvasPinned, false);
        }
        if (action === 'previous_image' && typeof onPreviousImage === 'function') {
            try { onPreviousImage(); } catch (_) {}
        }
        show(`${labels[direction]}…`);
        try { await sendAction(action); show(`${labels[direction]} applied`); }
        catch (error) { show(error.message || 'Action failed'); }
        finally { busy = false; hideTimer = win.setTimeout(() => { status.hidden = true; }, 3500); }
    };
    rebind.addEventListener('click', () => {
        if (!isOrator()) return;
        cancel(); rebinding = !rebinding;
        if (rebinding) onRebindStart();
        updateBindingUI();
    });
    if (disable) {
        disable.addEventListener('click', () => {
            cancel();
            rebinding = false;
            binding = binding ? null : { ...defaultBinding };
            save();
        });
    }
    if (reset) {
        reset.addEventListener('click', () => {
            cancel();
            rebinding = false;
            binding = { ...defaultBinding };
            save();
        });
    }
    // Mouse events report each button press/release, including additional buttons in a chord.
    win.addEventListener('pointerdown', event => {
        if (event.pointerType !== 'mouse' || !isOrator() || managesBinding(event.target)) return;
        if (rebinding) {
            event.stopImmediatePropagation();
            return;
        }
        if (!busy && binding?.type === 'mouse' && event.button === binding.button && matchModifiers(event) && !excluded(event.target) && isOverCanvas(event.target)) {
            // Block canvas drag tools while allowing the subsequent mouse event to open the wheel.
            event.stopImmediatePropagation();
        }
    }, true);
    win.addEventListener('mousedown', event => {
        pointer = { clientX: event.clientX, clientY: event.clientY };
        if (!drag) suppressUntil = 0;
        if (managesBinding(event.target)) return;
        if (rebinding && isOrator()) {
            consume(event); binding = { type: 'mouse', button: event.button, ...modifiers(event) };
            rebinding = false; suppressClick(event.button); save(); return;
        }
        if (!isOrator() || busy || drag || binding?.type !== 'mouse' || event.button !== binding.button || !matchModifiers(event) || excluded(event.target) || !isOverCanvas(event.target)) return;
        open(event, { type: 'mouse', button: event.button });
    }, true);
    win.addEventListener('mousemove', event => {
        pointer = { clientX: event.clientX, clientY: event.clientY };
        if (!drag) return;
        const mask = [1, 4, 2, 8, 16][drag.button];
        if (!isOrator() || (drag.type === 'mouse' && mask && !(event.buttons & mask))) { cancel(); return; }
        consume(event);
        const direction = selection();
        wheel.dataset.selected = direction || '';
        for (const item of wheel.querySelectorAll('[data-direction]')) item.classList.toggle('selected', item.dataset.direction === direction);
        show(direction ? `${labels[direction]} — release to apply` : 'Release in the center to cancel');
    }, true);
    win.addEventListener('mouseup', event => {
        if (event.button === suppressedButton && Date.now() < suppressUntil) { suppressClick(event.button); consume(event); }
        if (drag?.type !== 'mouse' || event.button !== drag.button) return;
        pointer = { clientX: event.clientX, clientY: event.clientY }; void finish(event);
    }, true);
    win.addEventListener('keydown', event => {
        if (event.key === 'Escape' && (rebinding || drag)) { consume(event); cancel(); rebinding = false; updateBindingUI(); return; }
        if (rebinding && isOrator()) {
            consume(event);
            if (event.repeat || /^(Control|Shift|Alt|Meta)(Left|Right)$/.test(event.code)) return;
            binding = { type: 'key', code: event.code, ...modifiers(event) }; rebinding = false; save(); return;
        }
        if (drag?.type === 'key' && drag.code === event.code) { consume(event); return; }
        if (!isOrator() || busy || drag || event.repeat || binding?.type !== 'key' || event.code !== binding.code || !matchModifiers(event) || excludedKey(event.target) || !isPointOverCanvas(pointer?.clientX, pointer?.clientY)) return;
        open(event, { type: 'key', code: event.code });
    }, true);
    win.addEventListener('keyup', event => { if (drag?.type === 'key' && event.code === drag.code) void finish(event); }, true);
    win.addEventListener('contextmenu', event => {
        if (rebinding || drag || (event.button === suppressedButton && Date.now() < suppressUntil) ||
            (isOrator() && binding?.type === 'mouse' && binding.button === 2 && matchModifiers(event) && !excluded(event.target) && isOverCanvas(event.target))) consume(event);
    }, true);
    for (const type of ['click', 'auxclick']) win.addEventListener(type, event => {
        if (event.button === suppressedButton && Date.now() < suppressUntil) consume(event);
    }, true);
    const loseFocus = () => { cancel(); rebinding = false; updateBindingUI(); };
    win.addEventListener('blur', loseFocus);
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) loseFocus(); });
    const controller = {
        cancelRebinding() { rebinding = false; updateBindingUI(); },
        updateState(canvasPinned, musicPinned) {
            currentCanvasPinned = Boolean(canvasPinned);
            currentMusicPinned = Boolean(musicPinned);
            pinStatus.textContent = `Image: ${currentCanvasPinned ? 'pinned' : 'unpinned'} · Music: ${currentMusicPinned ? 'pinned' : 'unpinned'}`;
            const imagePin = wheel.querySelector('[data-direction="up"]');
            if (imagePin) imagePin.innerHTML = `📌<br>${currentCanvasPinned ? 'Unpin' : 'Pin'} image`;
            const musicPin = wheel.querySelector('[data-direction="down"]');
            if (musicPin) musicPin.innerHTML = `📌<br>${currentMusicPinned ? 'Unpin' : 'Pin'} music`;
            labels.up = `${currentCanvasPinned ? 'Unpin' : 'Pin'} image`;
            labels.down = `${currentMusicPinned ? 'Unpin' : 'Pin'} music`;
        },
    };
    updateBindingUI();
    return controller;
}

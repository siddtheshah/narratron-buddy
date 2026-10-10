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
    onOpen = () => {},
    onCenterAction = null,
    document: doc = document,
    window: win = window,
    canvasTarget = null,
}) {
    const rebind = doc.getElementById('action-wheel-rebind');
    const secondaryRebind = doc.getElementById('action-wheel-2-rebind');
    const secondaryBindingLabel = doc.getElementById('action-wheel-2-binding-label');
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
    const secondaryActions = { up: 'update_story', down: 'update_ui' };
    const secondaryLabels = { up: 'Update Story', down: 'Update UI' };
    const defaultBinding = { type: 'mouse', button: 2, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false };
    const defaultSecondaryBinding = { ...defaultBinding, type: 'chord' };
    const isBareLeftClick = value => value?.type === 'mouse' && value.button === 0 &&
        !value.ctrlKey && !value.altKey && !value.shiftKey && !value.metaKey;
    const validBinding = value => value !== null && modifierKeys.every(key => typeof value[key] === 'boolean') &&
        ((value.type === 'mouse' && Number.isInteger(value.button) && value.button >= 0 && !isBareLeftClick(value)) ||
         value.type === 'chord' ||
         (value.type === 'key' && typeof value.code === 'string' && value.code.length > 0));
    let binding = defaultBinding;
    let secondaryBinding = defaultSecondaryBinding;
    let rebinding = false;
    let pendingSecondaryBinding = null;
    let drag = null;
    let busy = false;
    const suppressedButtons = new Set();
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
            else binding = { ...defaultBinding };
        } else {
            const legacy = win.localStorage.getItem('narratron_action_wheel_button');
            if (legacy === '-1') binding = null;
            else if (legacy !== null && Number.isInteger(Number(legacy)) && Number(legacy) > 0) binding = { ...defaultBinding, button: Number(legacy) };
        }
    } catch (_) { /* Storage can be disabled by the browser. */ }
    try {
        const saved = JSON.parse(win.localStorage.getItem('narratron_action_wheel_2_binding'));
        if (validBinding(saved)) secondaryBinding = saved;
    } catch (_) {}
    const bindingName = value => {
        if (!value) return 'Disabled';
        const names = ['Ctrl', 'Alt', 'Shift', 'Meta'].filter((_, index) => value[modifierKeys[index]]);
        names.push(value.type === 'chord' ? 'Left + Right mouse' : value.type === 'mouse'
            ? (['Left mouse', 'Middle mouse', 'Right mouse', 'Back mouse', 'Forward mouse'][value.button] || `Mouse button ${value.button}`)
            : value.code.replace(/^Key/, '').replace(/^Digit/, '').replace('Space', 'Spacebar'));
        return names.join(' + ');
    };
    const updateBindingUI = () => {
        bindingLabel.textContent = rebinding === true ? 'Press a mouse button or key combo… (Esc cancels)' : `Action Wheel: ${bindingName(binding)}`;
        rebind.classList.toggle('rebinding', rebinding === true);
        if (secondaryBindingLabel) secondaryBindingLabel.textContent = rebinding === 'secondary'
            ? 'Press a mouse button, Left + Right, or key combo… (Esc cancels)'
            : `Action Wheel 2: ${bindingName(secondaryBinding)}`;
        secondaryRebind?.classList.toggle('rebinding', rebinding === 'secondary');
        if (disable) {
            disable.textContent = binding ? 'Disable action wheel' : 'Enable action wheel (Right click)';
            disable.disabled = rebinding;
        }
    };
    const save = () => {
        try { win.localStorage.setItem('narratron_action_wheel_binding', JSON.stringify(binding)); } catch (_) {}
        try { win.localStorage.setItem('narratron_action_wheel_2_binding', JSON.stringify(secondaryBinding)); } catch (_) {}
        updateBindingUI();
    };
    const show = message => { win.clearTimeout(hideTimer); status.textContent = message; status.hidden = false; };
    const consume = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    const suppressClick = button => { suppressedButtons.add(button); suppressUntil = Date.now() + 500; };
    const cancel = () => {
        if (drag?.type === 'mouse') suppressClick(drag.button);
        if (drag?.chord) { suppressClick(0); suppressClick(2); }
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
            if (target === canvas || canvas.contains(target) || (wheel && (target === wheel || wheel.contains(target)))) return true;
            return false;
        }
        return isPointOverCanvas(x, y);
    };
    const excluded = target => Boolean(target.closest('input,textarea,select,button,a,[contenteditable="true"],[role="dialog"]'));
    const managesBinding = target => Boolean(target.closest(
        '#action-wheel-rebind,#action-wheel-2-rebind,#action-wheel-disable,#action-wheel-reset,#menu-item-hotkey,#menu-item-text-hotkey,#mic-config-close-btn,#mic-config-done-btn'
    ));
    const excludedKey = target => {
        const dialog = target.closest('[role="dialog"]');
        return Boolean(target.closest('input,textarea,select,[contenteditable="true"]') ||
            (dialog && dialog.getAttribute('aria-hidden') !== 'true'));
    };
    const matchModifiers = (event, value = binding) => modifierKeys.every(key => event[key] === value[key]);
    const matchesMouse = (event, value) => value && matchModifiers(event, value) &&
        (value.type === 'chord' ? (event.buttons & 3) === 3 && [0, 2].includes(event.button)
            : value.type === 'mouse' && event.button === value.button);
    const commitBinding = value => {
        const other = rebinding === 'secondary' ? binding : secondaryBinding;
        if (other && value.type === other.type && matchModifiers(value, other) &&
            (value.type === 'chord' || (value.type === 'mouse' ? value.button === other.button : value.code === other.code))) {
            (rebinding === 'secondary' ? secondaryBindingLabel : bindingLabel).textContent = 'Already used by the other wheel. Choose another shortcut…';
            pendingSecondaryBinding = null;
            return;
        }
        if (rebinding === 'secondary') secondaryBinding = value;
        else binding = value;
        rebinding = false; pendingSecondaryBinding = null; save();
    };
    const selection = () => {
        if (Math.max(Math.abs(pointer.clientX - drag.x), Math.abs(pointer.clientY - drag.y)) < 8) return null;
        if (drag.secondary) {
            const dx = pointer.clientX - drag.centerX;
            const dy = pointer.clientY - drag.centerY;
            if (Math.hypot(dx, dy) < 48) return null;
            return dy < 0 ? 'up' : 'down';
        }
        return actionWheelDirection(pointer.clientX - drag.centerX, pointer.clientY - drag.centerY);
    };
    const open = (event, trigger) => {
        const centerX = Math.max(140, Math.min(win.innerWidth - 140, pointer.clientX));
        const centerY = Math.max(140, Math.min(win.innerHeight - 140, pointer.clientY));
        drag = { ...trigger, x: pointer.clientX, y: pointer.clientY, centerX, centerY };
        wheel.dataset.mode = trigger.secondary ? 'secondary' : 'primary';
        wheel.setAttribute('aria-label', trigger.secondary ? 'Story and UI action wheel' : 'Orator action wheel');
        wheel.style.left = `${centerX}px`; wheel.style.top = `${centerY}px`; wheel.hidden = false;
        wheel.dataset.selected = '';
        const centerEl = wheel.querySelector('.wheel-center');
        if (centerEl) centerEl.innerHTML = trigger.secondary ? 'Release<br>to cancel' : 'Toggle<br>marker';
        consume(event);
        onOpen();
        const centerMsg = trigger.secondary ? 'Release in the center to cancel' : 'Release in the center to toggle marker';
        show(`Move to an action and release · ${centerMsg}`);
    };
    const finish = async event => {
        const direction = selection();
        const activeActions = drag.secondary ? secondaryActions : actions;
        const activeLabels = drag.secondary ? secondaryLabels : labels;
        const isSecondary = Boolean(drag.secondary);
        cancel(); consume(event);
        if (!direction) {
            if (!isSecondary && isOrator() && typeof onCenterAction === 'function') {
                try { onCenterAction(); } catch (_) {}
            }
            return;
        }
        if (!isOrator()) return;
        busy = true;
        const action = activeActions[direction];
        if (action === 'previous_image' && typeof onPreviousImage === 'function') {
            try { onPreviousImage(); } catch (_) {}
        }
        show(`${activeLabels[direction]}…`);
        try { await sendAction(action); show(`${activeLabels[direction]} applied`); }
        catch (error) { show(error.message || 'Action failed'); }
        finally { busy = false; hideTimer = win.setTimeout(() => { status.hidden = true; }, 3500); }
    };
    rebind.addEventListener('click', () => {
        if (!isOrator()) return;
        cancel(); pendingSecondaryBinding = null; rebinding = rebinding === true ? false : true;
        if (rebinding) onRebindStart();
        updateBindingUI();
    });
    secondaryRebind?.addEventListener('click', () => {
        if (!isOrator()) return;
        cancel(); pendingSecondaryBinding = null;
        rebinding = rebinding === 'secondary' ? false : 'secondary';
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
        if (!busy && matchesMouse(event, secondaryBinding) && !excluded(event.target) && isOverCanvas(event.target)) {
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
        if (!drag && Date.now() >= suppressUntil) suppressedButtons.clear();
        if (managesBinding(event.target)) return;
        if (rebinding && isOrator()) {
            if (rebinding === 'secondary') {
                consume(event); suppressClick(event.button);
                pendingSecondaryBinding = (event.buttons & 3) === 3
                    ? { type: 'chord', ...modifiers(event) }
                    : { type: 'mouse', button: event.button, ...modifiers(event) };
                return;
            }
            if (event.button === 0 && !event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey) {
                consume(event);
                suppressClick(event.button);
                bindingLabel.textContent = 'Press a mouse button (not left click) or key combo… (Esc cancels)';
                return;
            }
            consume(event); suppressClick(event.button);
            commitBinding({ type: 'mouse', button: event.button, ...modifiers(event) }); return;
        }
        if (isOrator() && !busy && matchesMouse(event, secondaryBinding) && !excluded(event.target) && isOverCanvas(event.target)) {
            if (drag?.secondary) { consume(event); return; }
            cancel();
            open(event, { type: 'mouse', button: event.button, secondary: true, chord: secondaryBinding.type === 'chord' });
            return;
        }
        if (!isOrator() || busy || drag || binding?.type !== 'mouse' || event.button !== binding.button || !matchModifiers(event) || excluded(event.target) || !isOverCanvas(event.target)) return;
        open(event, { type: 'mouse', button: event.button });
    }, true);
    win.addEventListener('mousemove', event => {
        pointer = { clientX: event.clientX, clientY: event.clientY };
        if (!drag) return;
        const mask = drag.chord ? 3 : [1, 4, 2, 8, 16][drag.button];
        if (!isOrator() || (drag.type === 'mouse' && mask && (event.buttons & mask) !== mask)) { cancel(); return; }
        consume(event);
        const direction = selection();
        wheel.dataset.selected = direction || '';
        for (const item of wheel.querySelectorAll('[data-direction]')) item.classList.toggle('selected', item.dataset.direction === direction);
        const activeLabels = drag.secondary ? secondaryLabels : labels;
        const centerLabel = drag.secondary ? 'Release in the center to cancel' : 'Release in the center to toggle marker';
        show(direction ? `${activeLabels[direction]} — release to apply` : centerLabel);
    }, true);
    win.addEventListener('pointermove', event => {
        if (drag) event.stopImmediatePropagation();
    }, true);
    win.addEventListener('mouseup', event => {
        if (rebinding === 'secondary' && pendingSecondaryBinding) {
            consume(event); suppressClick(event.button);
            if (isBareLeftClick(pendingSecondaryBinding)) {
                secondaryBindingLabel.textContent = 'Press a mouse button (not left click), Left + Right, or key combo… (Esc cancels)';
                pendingSecondaryBinding = null;
            } else commitBinding(pendingSecondaryBinding);
            return;
        }
        if (suppressedButtons.has(event.button) && Date.now() < suppressUntil) { suppressClick(event.button); consume(event); }
        if (drag?.type !== 'mouse' || (drag.chord ? ![0, 2].includes(event.button) : event.button !== drag.button)) return;
        pointer = { clientX: event.clientX, clientY: event.clientY }; void finish(event);
    }, true);
    win.addEventListener('keydown', event => {
        if (event.key === 'Escape' && (rebinding || drag)) { consume(event); cancel(); rebinding = false; updateBindingUI(); return; }
        if (rebinding && isOrator()) {
            consume(event);
            if (event.repeat || /^(Control|Shift|Alt|Meta)(Left|Right)$/.test(event.code)) return;
            commitBinding({ type: 'key', code: event.code, ...modifiers(event) }); return;
        }
        if (drag?.type === 'key' && drag.code === event.code) { consume(event); return; }
        if (isOrator() && !busy && !drag && !event.repeat && secondaryBinding.type === 'key' &&
            event.code === secondaryBinding.code && matchModifiers(event, secondaryBinding) &&
            !excludedKey(event.target) && isPointOverCanvas(pointer?.clientX, pointer?.clientY)) {
            open(event, { type: 'key', code: event.code, secondary: true }); return;
        }
        if (!isOrator() || busy || drag || event.repeat || binding?.type !== 'key' || event.code !== binding.code || !matchModifiers(event) || excludedKey(event.target) || !isPointOverCanvas(pointer?.clientX, pointer?.clientY)) return;
        open(event, { type: 'key', code: event.code });
    }, true);
    win.addEventListener('keyup', event => { if (drag?.type === 'key' && event.code === drag.code) void finish(event); }, true);
    win.addEventListener('contextmenu', event => {
        if (rebinding || drag || (suppressedButtons.has(event.button) && Date.now() < suppressUntil) ||
            (isOrator() && binding?.type === 'mouse' && binding.button === 2 && matchModifiers(event) && !excluded(event.target) && isOverCanvas(event.target))) consume(event);
        else if (isOrator() && matchesMouse(event, secondaryBinding) && !excluded(event.target) && isOverCanvas(event.target)) consume(event);
    }, true);
    for (const type of ['click', 'auxclick']) win.addEventListener(type, event => {
        if (suppressedButtons.has(event.button) && Date.now() < suppressUntil) consume(event);
    }, true);
    const loseFocus = () => { cancel(); rebinding = false; updateBindingUI(); };
    win.addEventListener('blur', loseFocus);
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) loseFocus(); });
    const controller = {
        cancelRebinding() { rebinding = false; updateBindingUI(); },
        updateState(canvasPinned, musicPinned) {
            currentCanvasPinned = Boolean(canvasPinned);
            currentMusicPinned = Boolean(musicPinned);
            if (pinStatus) {
                pinStatus.textContent = `Image: ${currentCanvasPinned ? 'pinned' : 'unpinned'} · Music: ${currentMusicPinned ? 'pinned' : 'unpinned'}`;
            }
            if (wheel) {
                const imagePin = wheel.querySelector('[data-primary][data-direction="up"]');
                if (imagePin) imagePin.innerHTML = `📌<br>${currentCanvasPinned ? 'Unpin' : 'Pin'} image`;
                const musicPin = wheel.querySelector('[data-primary][data-direction="down"]');
                if (musicPin) musicPin.innerHTML = `📌<br>${currentMusicPinned ? 'Unpin' : 'Pin'} music`;
            }
            labels.up = `${currentCanvasPinned ? 'Unpin' : 'Pin'} image`;
            labels.down = `${currentMusicPinned ? 'Unpin' : 'Pin'} music`;
        },
        isRightClickBound() {
            return isOrator() && Boolean(binding?.type === 'mouse' && binding.button === 2);
        },
    };
    updateBindingUI();
    return controller;
}

export type ActionWheelDirection =
  | 'up'
  | 'upright'
  | 'downright'
  | 'down'
  | 'downleft'
  | 'upleft';

export type SecondaryActionWheelDirection = 'up' | 'down';

export type PrimaryActionWheelAction =
  | 'toggle_canvas_pin'
  | 'toggle_music_pin'
  | 'previous_image'
  | 'previous_music'
  | 'new_image'
  | 'new_music';

export type SecondaryActionWheelAction = 'update_story' | 'update_ui';

export type ActionWheelAction = PrimaryActionWheelAction | SecondaryActionWheelAction;

export type ModifierKey = 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey';

export interface ActionWheelModifiers {
  readonly ctrlKey: boolean;
  readonly altKey: boolean;
  readonly shiftKey: boolean;
  readonly metaKey: boolean;
}

export interface MouseActionWheelBinding extends ActionWheelModifiers {
  readonly type: 'mouse';
  readonly button: number;
}

export interface ChordActionWheelBinding extends ActionWheelModifiers {
  readonly type: 'chord';
}

export interface KeyActionWheelBinding extends ActionWheelModifiers {
  readonly type: 'key';
  readonly code: string;
}

export type ActionWheelBinding =
  | MouseActionWheelBinding
  | ChordActionWheelBinding
  | KeyActionWheelBinding;

export type ActionWheelCanvasTarget = Element | string | (() => Element | null) | null;

export interface InitializeActionWheelOptions {
  readonly isOrator: () => boolean;
  readonly sendAction: (action: ActionWheelAction) => Promise<void> | void;
  readonly onPreviousImage?: (() => void) | null;
  readonly onRebindStart?: () => void;
  readonly onOpen?: () => void;
  readonly onCenterAction?: (() => void) | null;
  readonly document?: Document;
  readonly window?: Window;
  readonly canvasTarget?: ActionWheelCanvasTarget;
}

export interface ActionWheelController {
  cancelRebinding(): void;
  updateState(canvasPinned: boolean, musicPinned: boolean): void;
  isRightClickBound(): boolean;
}

type RebindingState = false | true | 'secondary';

type DragTrigger =
  | {
      readonly type: 'mouse';
      readonly button: number;
      readonly secondary?: boolean;
      readonly chord?: boolean;
    }
  | {
      readonly type: 'key';
      readonly code: string;
      readonly secondary?: boolean;
    };

type DragState = DragTrigger & {
  readonly x: number;
  readonly y: number;
  readonly centerX: number;
  readonly centerY: number;
};

type WheelSelection =
  | { readonly secondary: true; readonly direction: SecondaryActionWheelDirection }
  | { readonly secondary: false; readonly direction: ActionWheelDirection }
  | null;

interface PointerPosition {
  readonly clientX: number;
  readonly clientY: number;
}

const MODIFIER_KEYS: readonly ModifierKey[] = ['ctrlKey', 'altKey', 'shiftKey', 'metaKey'];
const MODIFIER_LABELS: readonly string[] = ['Ctrl', 'Alt', 'Shift', 'Meta'];
const MOUSE_BUTTON_NAMES: readonly string[] = [
  'Left mouse',
  'Middle mouse',
  'Right mouse',
  'Back mouse',
  'Forward mouse',
];
const MOUSE_BUTTON_MASKS: readonly number[] = [1, 4, 2, 8, 16];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isBareLeftClick(value: ActionWheelBinding | null): boolean {
  return (
    value?.type === 'mouse' &&
    value.button === 0 &&
    !value.ctrlKey &&
    !value.altKey &&
    !value.shiftKey &&
    !value.metaKey
  );
}

function validBinding(value: unknown): value is ActionWheelBinding {
  if (!isRecord(value)) return false;
  if (!MODIFIER_KEYS.every((key) => typeof value[key] === 'boolean')) return false;
  if (value.type === 'chord') return true;
  if (value.type === 'mouse') {
    return (
      typeof value.button === 'number' &&
      Number.isInteger(value.button) &&
      value.button >= 0 &&
      !(
        value.button === 0 &&
        !value.ctrlKey &&
        !value.altKey &&
        !value.shiftKey &&
        !value.metaKey
      )
    );
  }
  if (value.type === 'key') {
    return typeof value.code === 'string' && value.code.length > 0;
  }
  return false;
}

/** Map a pointer offset vector from the wheel center to a radial slice direction. */
export function actionWheelDirection(
  dx: number,
  dy: number,
  threshold: number = 48,
): ActionWheelDirection | null {
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

/** Initialize the primary and secondary orator action wheels and shortcut rebinding UI. */
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
}: InitializeActionWheelOptions): ActionWheelController {
  const rebind = doc.getElementById('action-wheel-rebind');
  const secondaryRebind = doc.getElementById('action-wheel-2-rebind');
  const secondaryBindingLabel = doc.getElementById('action-wheel-2-binding-label');
  const disable = doc.getElementById('action-wheel-disable') as HTMLButtonElement | null;
  const reset = doc.getElementById('action-wheel-reset');
  const bindingLabel = doc.getElementById('action-wheel-binding-label');
  const status = doc.getElementById('action-wheel-status');
  const pinStatus = doc.getElementById('action-wheel-pin-status');
  const wheel = doc.getElementById('orator-action-wheel');

  const modifiers = (event: MouseEvent | KeyboardEvent): ActionWheelModifiers => ({
    ctrlKey: event.ctrlKey,
    altKey: event.altKey,
    shiftKey: event.shiftKey,
    metaKey: event.metaKey,
  });

  const actions: Record<ActionWheelDirection, PrimaryActionWheelAction> = {
    up: 'toggle_canvas_pin',
    down: 'toggle_music_pin',
    upleft: 'previous_image',
    downleft: 'previous_music',
    upright: 'new_image',
    downright: 'new_music',
  };
  const labels: Record<ActionWheelDirection, string> = {
    up: 'Pin image',
    down: 'Pin music',
    upleft: 'Previous image',
    downleft: 'Previous music',
    upright: 'New image',
    downright: 'New music',
  };
  const secondaryActions: Record<SecondaryActionWheelDirection, SecondaryActionWheelAction> = {
    up: 'update_story',
    down: 'update_ui',
  };
  const secondaryLabels: Record<SecondaryActionWheelDirection, string> = {
    up: 'Update Story',
    down: 'Update UI',
  };

  const defaultBinding: MouseActionWheelBinding = {
    type: 'mouse',
    button: 2,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
  };
  const defaultSecondaryBinding: ChordActionWheelBinding = {
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
    type: 'chord',
  };

  let binding: ActionWheelBinding | null = defaultBinding;
  let secondaryBinding: ActionWheelBinding = defaultSecondaryBinding;
  let rebinding: RebindingState = false;
  let pendingSecondaryBinding: ActionWheelBinding | null = null;
  let drag: DragState | null = null;
  let busy = false;
  const suppressedButtons = new Set<number>();
  let suppressUntil = 0;
  let hideTimer: number | undefined;
  let pointer: PointerPosition = { clientX: win.innerWidth / 2, clientY: win.innerHeight / 2 };
  let currentCanvasPinned = false;
  let currentMusicPinned = false;

  try {
    const saved = win.localStorage.getItem('narratron_action_wheel_binding');
    if (saved !== null) {
      const value: unknown = JSON.parse(saved);
      if (value === null || validBinding(value)) binding = value;
      else binding = { ...defaultBinding };
    } else {
      const legacy = win.localStorage.getItem('narratron_action_wheel_button');
      if (legacy === '-1') binding = null;
      else if (legacy !== null && Number.isInteger(Number(legacy)) && Number(legacy) > 0) {
        binding = { ...defaultBinding, button: Number(legacy) };
      }
    }
  } catch {
    /* Storage can be disabled by the browser. */
  }
  try {
    const saved = win.localStorage.getItem('narratron_action_wheel_2_binding');
    if (saved !== null) {
      const value: unknown = JSON.parse(saved);
      if (validBinding(value)) secondaryBinding = value;
    }
  } catch {
    /* Storage can be disabled by the browser. */
  }

  const bindingName = (value: ActionWheelBinding | null): string => {
    if (!value) return 'Disabled';
    const names = MODIFIER_LABELS.filter((_, index) => value[MODIFIER_KEYS[index]]);
    names.push(
      value.type === 'chord'
        ? 'Left + Right mouse'
        : value.type === 'mouse'
          ? MOUSE_BUTTON_NAMES[value.button] || `Mouse button ${value.button}`
          : value.code.replace(/^Key/, '').replace(/^Digit/, '').replace('Space', 'Spacebar'),
    );
    return names.join(' + ');
  };

  const updateBindingUI = (): void => {
    if (bindingLabel) {
      bindingLabel.textContent =
        rebinding === true
          ? 'Press a mouse button or key combo… (Esc cancels)'
          : `Action Wheel: ${bindingName(binding)}`;
    }
    rebind?.classList.toggle('rebinding', rebinding === true);
    if (secondaryBindingLabel) {
      secondaryBindingLabel.textContent =
        rebinding === 'secondary'
          ? 'Press a mouse button, Left + Right, or key combo… (Esc cancels)'
          : `Action Wheel 2: ${bindingName(secondaryBinding)}`;
    }
    secondaryRebind?.classList.toggle('rebinding', rebinding === 'secondary');
    if (disable) {
      disable.textContent = binding ? 'Disable action wheel' : 'Enable action wheel (Right click)';
      disable.disabled = Boolean(rebinding);
    }
  };

  const save = (): void => {
    try {
      win.localStorage.setItem('narratron_action_wheel_binding', JSON.stringify(binding));
    } catch {
      /* Ignore storage write failures. */
    }
    try {
      win.localStorage.setItem('narratron_action_wheel_2_binding', JSON.stringify(secondaryBinding));
    } catch {
      /* Ignore storage write failures. */
    }
    updateBindingUI();
  };

  const show = (message: string): void => {
    win.clearTimeout(hideTimer);
    if (status) {
      status.textContent = message;
      status.hidden = false;
    }
  };

  const consume = (event: Event): void => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };

  const suppressClick = (button: number): void => {
    suppressedButtons.add(button);
    suppressUntil = Date.now() + 500;
  };

  const cancel = (): void => {
    if (drag?.type === 'mouse') {
      suppressClick(drag.button);
      if (drag.chord) {
        suppressClick(0);
        suppressClick(2);
      }
    }
    drag = null;
    if (wheel) {
      wheel.hidden = true;
      wheel.dataset.selected = '';
      for (const item of wheel.querySelectorAll<HTMLElement>('[data-direction]')) {
        item.classList.remove('selected');
      }
    }
    if (status) {
      status.hidden = true;
    }
  };

  const getCanvas = (): Element | null => {
    if (typeof canvasTarget === 'function') return canvasTarget();
    if (typeof canvasTarget === 'string') return doc.querySelector(canvasTarget);
    if (canvasTarget) return canvasTarget;
    return (
      doc.getElementById('image-container') ||
      doc.getElementById('canvasStage') ||
      doc.querySelector('canvas:not(#orator-action-wheel canvas)')
    );
  };

  const isPointOverCanvas = (x: number, y: number): boolean => {
    const canvas = getCanvas();
    if (!canvas) return true;
    if (typeof canvas.getBoundingClientRect !== 'function') return true;
    const rect = canvas.getBoundingClientRect();
    return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
  };

  const isOverCanvas = (
    target: EventTarget | null,
    x: number = pointer.clientX,
    y: number = pointer.clientY,
  ): boolean => {
    const canvas = getCanvas();
    if (!canvas) return true;
    if (target instanceof Node && target !== doc && target !== doc.body && target !== doc.documentElement) {
      if (
        target === canvas ||
        canvas.contains(target) ||
        (wheel !== null && (target === wheel || wheel.contains(target)))
      ) {
        return true;
      }
      return false;
    }
    return isPointOverCanvas(x, y);
  };

  const asElement = (target: EventTarget | null): Element | null =>
    target instanceof Element ? target : null;

  const excluded = (target: EventTarget | null): boolean =>
    Boolean(asElement(target)?.closest('input,textarea,select,button,a,[contenteditable="true"],[role="dialog"]'));

  const managesBinding = (target: EventTarget | null): boolean =>
    Boolean(
      asElement(target)?.closest(
        '#action-wheel-rebind,#action-wheel-2-rebind,#action-wheel-disable,#action-wheel-reset,#menu-item-hotkey,#menu-item-text-hotkey,#mic-config-close-btn,#mic-config-done-btn',
      ),
    );

  const excludedKey = (target: EventTarget | null): boolean => {
    const el = asElement(target);
    if (!el) return false;
    const dialog = el.closest('[role="dialog"]');
    return Boolean(
      el.closest('input,textarea,select,[contenteditable="true"]') ||
        (dialog && dialog.getAttribute('aria-hidden') !== 'true'),
    );
  };

  const matchModifiers = (
    event: ActionWheelModifiers,
    value: ActionWheelBinding | null = binding,
  ): boolean => value !== null && MODIFIER_KEYS.every((key) => event[key] === value[key]);

  const matchesMouse = (event: MouseEvent, value: ActionWheelBinding | null): boolean =>
    Boolean(
      value &&
        matchModifiers(event, value) &&
        (value.type === 'chord'
          ? (event.buttons & 3) === 3 && (event.button === 0 || event.button === 2)
          : value.type === 'mouse' && event.button === value.button),
    );

  const commitBinding = (value: ActionWheelBinding): void => {
    const other = rebinding === 'secondary' ? binding : secondaryBinding;
    if (
      other &&
      value.type === other.type &&
      matchModifiers(value, other) &&
      (value.type === 'chord' ||
        (value.type === 'mouse' && other.type === 'mouse'
          ? value.button === other.button
          : value.type === 'key' && other.type === 'key' && value.code === other.code))
    ) {
      const targetLabel = rebinding === 'secondary' ? secondaryBindingLabel : bindingLabel;
      if (targetLabel) {
        targetLabel.textContent = 'Already used by the other wheel. Choose another shortcut…';
      }
      pendingSecondaryBinding = null;
      return;
    }
    if (rebinding === 'secondary') secondaryBinding = value;
    else binding = value;
    rebinding = false;
    pendingSecondaryBinding = null;
    save();
  };

  const getSelection = (currentDrag: DragState): WheelSelection => {
    if (
      Math.max(
        Math.abs(pointer.clientX - currentDrag.x),
        Math.abs(pointer.clientY - currentDrag.y),
      ) < 8
    ) {
      return null;
    }
    if (currentDrag.secondary) {
      const dx = pointer.clientX - currentDrag.centerX;
      const dy = pointer.clientY - currentDrag.centerY;
      if (Math.hypot(dx, dy) < 48) return null;
      return { secondary: true, direction: dy < 0 ? 'up' : 'down' };
    }
    const direction = actionWheelDirection(
      pointer.clientX - currentDrag.centerX,
      pointer.clientY - currentDrag.centerY,
    );
    return direction ? { secondary: false, direction } : null;
  };

  const open = (event: MouseEvent | KeyboardEvent, trigger: DragTrigger): void => {
    const centerX = Math.max(140, Math.min(win.innerWidth - 140, pointer.clientX));
    const centerY = Math.max(140, Math.min(win.innerHeight - 140, pointer.clientY));
    drag = { ...trigger, x: pointer.clientX, y: pointer.clientY, centerX, centerY };
    if (wheel) {
      wheel.dataset.mode = trigger.secondary ? 'secondary' : 'primary';
      wheel.setAttribute(
        'aria-label',
        trigger.secondary ? 'Story and UI action wheel' : 'Orator action wheel',
      );
      wheel.style.left = `${centerX}px`;
      wheel.style.top = `${centerY}px`;
      wheel.hidden = false;
      wheel.dataset.selected = '';
      const centerEl = wheel.querySelector('.wheel-center');
      if (centerEl) {
        centerEl.innerHTML = trigger.secondary ? 'Release<br>to cancel' : 'Cycle<br>cursor';
      }
    }
    consume(event);
    onOpen();
    const centerMsg = trigger.secondary
      ? 'Release in the center to cancel'
      : 'Release in the center to cycle drawing tool, selection, and zoom and pan';
    show(`Move to an action and release · ${centerMsg}`);
  };

  const finish = async (event: MouseEvent | KeyboardEvent): Promise<void> => {
    if (!drag) return;
    const selected = getSelection(drag);
    const isSecondary = Boolean(drag.secondary);
    cancel();
    consume(event);
    if (!selected) {
      if (!isSecondary && isOrator() && typeof onCenterAction === 'function') {
        try {
          onCenterAction();
        } catch {
          /* Ignore center action callback errors. */
        }
      }
      return;
    }
    if (!isOrator()) return;
    busy = true;
    const action: ActionWheelAction = selected.secondary
      ? secondaryActions[selected.direction]
      : actions[selected.direction];
    const label = selected.secondary
      ? secondaryLabels[selected.direction]
      : labels[selected.direction];
    if (action === 'previous_image' && typeof onPreviousImage === 'function') {
      try {
        onPreviousImage();
      } catch {
        /* Ignore previous image callback errors. */
      }
    }
    show(`${label}…`);
    try {
      await sendAction(action);
      show(`${label} applied`);
    } catch (error: unknown) {
      const errorMessage =
        error instanceof Error
          ? error.message
          : isRecord(error) && typeof error.message === 'string'
            ? error.message
            : '';
      show(errorMessage || 'Action failed');
    } finally {
      busy = false;
      hideTimer = win.setTimeout(() => {
        if (status) status.hidden = true;
      }, 3500);
    }
  };

  rebind?.addEventListener('click', () => {
    if (!isOrator()) return;
    cancel();
    pendingSecondaryBinding = null;
    rebinding = rebinding === true ? false : true;
    if (rebinding) onRebindStart();
    updateBindingUI();
  });

  secondaryRebind?.addEventListener('click', () => {
    if (!isOrator()) return;
    cancel();
    pendingSecondaryBinding = null;
    rebinding = rebinding === 'secondary' ? false : 'secondary';
    if (rebinding) onRebindStart();
    updateBindingUI();
  });

  disable?.addEventListener('click', () => {
    cancel();
    rebinding = false;
    binding = binding ? null : { ...defaultBinding };
    save();
  });

  reset?.addEventListener('click', () => {
    cancel();
    rebinding = false;
    binding = { ...defaultBinding };
    save();
  });

  // Mouse events report each button press/release, including additional buttons in a chord.
  win.addEventListener(
    'pointerdown',
    (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !isOrator() || managesBinding(event.target)) return;
      if (rebinding) {
        event.stopImmediatePropagation();
        return;
      }
      if (!busy && matchesMouse(event, secondaryBinding) && !excluded(event.target) && isOverCanvas(event.target)) {
        event.stopImmediatePropagation();
        return;
      }
      if (
        !busy &&
        binding?.type === 'mouse' &&
        event.button === binding.button &&
        matchModifiers(event) &&
        !excluded(event.target) &&
        isOverCanvas(event.target)
      ) {
        // Block canvas drag tools while allowing the subsequent mouse event to open the wheel.
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  win.addEventListener(
    'mousedown',
    (event: MouseEvent) => {
      pointer = { clientX: event.clientX, clientY: event.clientY };
      if (!drag && Date.now() >= suppressUntil) suppressedButtons.clear();
      if (managesBinding(event.target)) return;
      if (rebinding && isOrator()) {
        if (rebinding === 'secondary') {
          consume(event);
          suppressClick(event.button);
          pendingSecondaryBinding =
            (event.buttons & 3) === 3
              ? { type: 'chord', ...modifiers(event) }
              : { type: 'mouse', button: event.button, ...modifiers(event) };
          return;
        }
        if (event.button === 0 && !event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey) {
          consume(event);
          suppressClick(event.button);
          if (bindingLabel) {
            bindingLabel.textContent = 'Press a mouse button (not left click) or key combo… (Esc cancels)';
          }
          return;
        }
        consume(event);
        suppressClick(event.button);
        commitBinding({ type: 'mouse', button: event.button, ...modifiers(event) });
        return;
      }
      if (isOrator() && !busy && matchesMouse(event, secondaryBinding) && !excluded(event.target) && isOverCanvas(event.target)) {
        if (drag?.secondary) {
          consume(event);
          return;
        }
        cancel();
        open(event, {
          type: 'mouse',
          button: event.button,
          secondary: true,
          chord: secondaryBinding.type === 'chord',
        });
        return;
      }
      if (
        !isOrator() ||
        busy ||
        drag ||
        binding?.type !== 'mouse' ||
        event.button !== binding.button ||
        !matchModifiers(event) ||
        excluded(event.target) ||
        !isOverCanvas(event.target)
      ) {
        return;
      }
      open(event, { type: 'mouse', button: event.button });
    },
    true,
  );

  win.addEventListener(
    'mousemove',
    (event: MouseEvent) => {
      pointer = { clientX: event.clientX, clientY: event.clientY };
      if (!drag) return;
      const mask = drag.type === 'mouse' ? (drag.chord ? 3 : MOUSE_BUTTON_MASKS[drag.button]) : 0;
      if (!isOrator() || (drag.type === 'mouse' && mask && (event.buttons & mask) !== mask)) {
        cancel();
        return;
      }
      consume(event);
      const selected = getSelection(drag);
      const direction = selected?.direction ?? '';
      if (wheel) {
        wheel.dataset.selected = direction;
        for (const item of wheel.querySelectorAll<HTMLElement>('[data-direction]')) {
          item.classList.toggle('selected', item.dataset.direction === direction);
        }
      }
      const centerLabel = drag.secondary
        ? 'Release in the center to cancel'
        : 'Release in the center to toggle marker';
      const labelText = selected
        ? selected.secondary
          ? secondaryLabels[selected.direction]
          : labels[selected.direction]
        : null;
      show(labelText ? `${labelText} — release to apply` : centerLabel);
    },
    true,
  );

  win.addEventListener(
    'pointermove',
    (event: PointerEvent) => {
      if (drag) event.stopImmediatePropagation();
    },
    true,
  );

  win.addEventListener(
    'mouseup',
    (event: MouseEvent) => {
      if (rebinding === 'secondary' && pendingSecondaryBinding) {
        consume(event);
        suppressClick(event.button);
        if (isBareLeftClick(pendingSecondaryBinding)) {
          if (secondaryBindingLabel) {
            secondaryBindingLabel.textContent =
              'Press a mouse button (not left click), Left + Right, or key combo… (Esc cancels)';
          }
          pendingSecondaryBinding = null;
        } else {
          commitBinding(pendingSecondaryBinding);
        }
        return;
      }
      if (suppressedButtons.has(event.button) && Date.now() < suppressUntil) {
        suppressClick(event.button);
        consume(event);
      }
      if (
        drag?.type !== 'mouse' ||
        (drag.chord ? event.button !== 0 && event.button !== 2 : event.button !== drag.button)
      ) {
        return;
      }
      pointer = { clientX: event.clientX, clientY: event.clientY };
      void finish(event);
    },
    true,
  );

  win.addEventListener(
    'keydown',
    (event: KeyboardEvent) => {
      if (event.key === 'Escape' && (rebinding || drag)) {
        consume(event);
        cancel();
        rebinding = false;
        updateBindingUI();
        return;
      }
      if (rebinding && isOrator()) {
        consume(event);
        if (event.repeat || /^(Control|Shift|Alt|Meta)(Left|Right)$/.test(event.code)) return;
        commitBinding({ type: 'key', code: event.code, ...modifiers(event) });
        return;
      }
      if (drag?.type === 'key' && drag.code === event.code) {
        consume(event);
        return;
      }
      if (
        isOrator() &&
        !busy &&
        !drag &&
        !event.repeat &&
        secondaryBinding.type === 'key' &&
        event.code === secondaryBinding.code &&
        matchModifiers(event, secondaryBinding) &&
        !excludedKey(event.target) &&
        isPointOverCanvas(pointer.clientX, pointer.clientY)
      ) {
        open(event, { type: 'key', code: event.code, secondary: true });
        return;
      }
      if (
        !isOrator() ||
        busy ||
        drag ||
        event.repeat ||
        binding?.type !== 'key' ||
        event.code !== binding.code ||
        !matchModifiers(event) ||
        excludedKey(event.target) ||
        !isPointOverCanvas(pointer.clientX, pointer.clientY)
      ) {
        return;
      }
      open(event, { type: 'key', code: event.code });
    },
    true,
  );

  win.addEventListener(
    'keyup',
    (event: KeyboardEvent) => {
      if (drag?.type === 'key' && event.code === drag.code) {
        void finish(event);
      }
    },
    true,
  );

  win.addEventListener(
    'contextmenu',
    (event: MouseEvent) => {
      if (
        rebinding ||
        drag ||
        (suppressedButtons.has(event.button) && Date.now() < suppressUntil) ||
        (isOrator() &&
          binding?.type === 'mouse' &&
          binding.button === 2 &&
          matchModifiers(event) &&
          !excluded(event.target) &&
          isOverCanvas(event.target))
      ) {
        consume(event);
      } else if (
        isOrator() &&
        matchesMouse(event, secondaryBinding) &&
        !excluded(event.target) &&
        isOverCanvas(event.target)
      ) {
        consume(event);
      }
    },
    true,
  );

  for (const type of ['click', 'auxclick'] as const) {
    win.addEventListener(
      type,
      (event: MouseEvent) => {
        if (suppressedButtons.has(event.button) && Date.now() < suppressUntil) consume(event);
      },
      true,
    );
  }

  const loseFocus = (): void => {
    cancel();
    rebinding = false;
    updateBindingUI();
  };
  win.addEventListener('blur', loseFocus);
  doc.addEventListener('visibilitychange', () => {
    if (doc.hidden) loseFocus();
  });

  const controller: ActionWheelController = {
    cancelRebinding(): void {
      rebinding = false;
      updateBindingUI();
    },
    updateState(canvasPinned: boolean, musicPinned: boolean): void {
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
    isRightClickBound(): boolean {
      return isOrator() && Boolean(binding?.type === 'mouse' && binding.button === 2);
    },
  };

  updateBindingUI();
  return controller;
}

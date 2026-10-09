const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/js/canvas-state-polling.js'), 'utf8');

function harness({ hidden = false, focused = true } = {}) {
    const documentEvents = new Map();
    const windowEvents = new Map();
    const timers = new Map();
    const calls = [];
    let now = 0;
    let nextId = 0;
    const document = {
        hidden,
        hasFocus: () => focused,
        addEventListener: (event, callback) => documentEvents.set(event, callback),
    };
    const window = {
        addEventListener: (event, callback) => windowEvents.set(event, callback),
    };
    vm.runInNewContext(source, {
        document, window, Date: { now: () => now },
        setInterval: (callback, delay) => {
            const id = ++nextId;
            timers.set(id, { callback, delay });
            return id;
        },
        clearInterval: id => timers.delete(id),
    });
    window.startCanvasStatePolling(() => calls.push(now));
    return {
        calls, timers, document,
        advance(ms, runTimers = true) {
            now += ms;
            if (runTimers) [...timers.values()].forEach(timer => timer.callback());
        },
        event(name) {
            (documentEvents.get(name) || windowEvents.get(name))();
        },
        focus(value) { focused = value; },
    };
}

test('active tabs refresh every second without requiring interaction', () => {
    const h = harness();
    assert.deepEqual(h.calls, [0]);
    assert.equal([...h.timers.values()][0].delay, 1000);
    h.advance(1000);
    h.advance(1000);
    assert.deepEqual(h.calls, [0, 1000, 2000]);
});

test('hidden tabs pause and catch up when visible, even without focus', () => {
    const h = harness();
    h.document.hidden = true;
    h.event('visibilitychange');
    assert.equal(h.timers.size, 0);
    h.advance(5000);
    h.event('mousemove');
    assert.deepEqual(h.calls, [0]);
    h.document.hidden = false;
    h.focus(false);
    h.event('visibilitychange');
    assert.equal(h.timers.size, 1);
    assert.deepEqual(h.calls, [0, 5000]);
    h.advance(1000);
    assert.deepEqual(h.calls, [0, 5000, 6000]);
    h.focus(true);
    h.event('focus');
    assert.deepEqual(h.calls, [0, 5000, 6000]);
});

test('visible windows poll every second while another window has focus', () => {
    const h = harness({ focused: false });
    assert.deepEqual(h.calls, [0]);
    h.advance(1000);
    h.advance(1000);
    assert.equal(h.timers.size, 1);
    assert.deepEqual(h.calls, [0, 1000, 2000]);
});

test('mouse movement wakes suspended polling without flooding requests', () => {
    const h = harness();
    h.advance(5000, false);
    h.event('mousemove');
    assert.deepEqual(h.calls, [0, 5000]);
    for (let i = 0; i < 100; i++) h.event('mousemove');
    h.event('click');
    h.event('keydown');
    h.event('focus');
    assert.equal(h.timers.size, 1);
    assert.deepEqual(h.calls, [0, 5000]);
    h.advance(1000);
    assert.deepEqual(h.calls, [0, 5000, 6000]);
});

test('initially inactive tabs start polling when selected', () => {
    const h = harness({ hidden: true, focused: false });
    assert.equal(h.timers.size, 0);
    assert.deepEqual(h.calls, []);
    h.document.hidden = false;
    h.focus(true);
    h.event('visibilitychange');
    assert.deepEqual(h.calls, [0]);
    assert.equal(h.timers.size, 1);
});

test('polling stops after 30 minutes of inactivity despite periodic refreshes', () => {
    const h = harness();
    for (let second = 1; second < 30 * 60; second++) h.advance(1000);
    assert.equal(h.calls.length, 30 * 60);
    h.advance(1000);
    assert.equal(h.timers.size, 0);
    assert.equal(h.calls.length, 30 * 60);
    h.advance(60000);
    assert.equal(h.calls.length, 30 * 60);
});

for (const event of ['mousemove', 'click', 'keydown', 'wheel', 'touchstart']) {
    test(`${event} resets inactivity and wakes paused polling`, () => {
        const h = harness();
        h.advance(29 * 60 * 1000);
        h.event(event);
        h.advance(29 * 60 * 1000);
        assert.equal(h.timers.size, 1);
        h.advance(60 * 1000);
        assert.equal(h.timers.size, 0);
        const count = h.calls.length;
        h.event(event);
        assert.equal(h.calls.length, count + 1);
        assert.equal(h.timers.size, 1);
        h.advance(1000);
        assert.equal(h.calls.length, count + 2);
    });
}

test('returning to an idle tab restarts polling', () => {
    const h = harness();
    h.document.hidden = true;
    h.event('visibilitychange');
    h.advance(31 * 60 * 1000);
    h.event('mousemove');
    assert.equal(h.timers.size, 0);
    h.document.hidden = false;
    h.event('visibilitychange');
    assert.equal(h.timers.size, 1);
    assert.deepEqual(h.calls, [0, 31 * 60 * 1000]);
});

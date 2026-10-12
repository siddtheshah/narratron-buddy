const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/story-log-inspector.js'), 'utf8')
    .replace('export function initializeStoryLogInspector', 'function initializeStoryLogInspector');

function harness() {
    function node() {
        return {
            children: [], textContent: '', events: new Map(),
            classList: { toggle() {} }, setAttribute() {},
            addEventListener(name, callback) { this.events.set(name, callback); },
            append(...children) { this.children.push(...children); },
            appendChild(child) { this.children.push(child); },
            replaceChildren() { this.children = []; this.textContent = ''; this.renders++; },
            get childElementCount() { return this.children.length; },
            querySelectorAll() { return []; },
            contains() { return false; },
            renders: 0,
        };
    }
    const nodes = new Map(['history-paging-controls', 'story-log-content', 'story-log-inspector',
        'story-log-toggle-btn', 'story-log-close-btn'].map(id => [id, node()]));
    const events = new Map();
    const timers = new Map();
    const calls = [];
    const document = {
        hidden: false,
        getElementById: id => nodes.get(id), createElement: node,
        addEventListener: (name, callback) => events.set(name, callback),
    };
    let reply = { status: 200, text: '{"type":"user_action","action":"First"}\n' };
    let pending = null;
    let timerId = 0;
    vm.runInNewContext(source + '\ninitializeStoryLogInspector("test theater");', {
        document, console: { error() {} }, requestAnimationFrame: callback => callback(),
        setInterval: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
        clearInterval: id => timers.delete(id),
        fetch: async (url, options) => {
            calls.push({ url, options });
            if (pending) await pending;
            return { status: reply.status, ok: reply.status === 200, text: async () => reply.text };
        },
    });
    return { nodes, events, timers, calls, document,
        async flush() { await new Promise(resolve => setImmediate(resolve)); },
        click(id = 'story-log-toggle-btn') { nodes.get(id).events.get('click')({ stopPropagation() {} }); },
        tick() { [...timers.values()].forEach(timer => timer.callback()); },
        reply(status, text = '') { reply = { status, text }; },
        hold() { let release; pending = new Promise(resolve => { release = resolve; }); return () => { pending = null; release(); }; },
    };
}

test('open inspector fetches appended entries without reopening and bypasses cached output', async () => {
    const h = harness();
    assert.equal(h.calls.length, 0);
    h.click();
    await h.flush();
    assert.equal(h.calls[0].options.cache, 'no-store');
    assert.equal([...h.timers.values()][0].delay, 2000);
    const content = h.nodes.get('story-log-content');
    assert.equal(content.children.length, 1);
    h.tick();
    await h.flush();
    assert.equal(content.renders, 1);
    h.reply(200, '{"type":"user_action","action":"First"}\n{"type":"user_action","action":"Second"}\n');
    h.tick();
    await h.flush();
    assert.equal(content.children.length, 2);
    assert.equal(content.children[1].children[0].children[1].textContent, 'Second');
});

test('refresh pauses when closed or hidden and catches up on visibility and reopening', async () => {
    const h = harness();
    h.click();
    await h.flush();
    h.document.hidden = true;
    h.events.get('visibilitychange')();
    assert.equal(h.timers.size, 0);
    h.document.hidden = false;
    h.events.get('visibilitychange')();
    await h.flush();
    assert.equal(h.calls.length, 2);
    h.click('story-log-close-btn');
    assert.equal(h.timers.size, 0);
    h.click();
    await h.flush();
    assert.equal(h.calls.length, 3);
});

test('missing logs and transient errors are retried, preserving already rendered entries', async () => {
    const h = harness();
    h.reply(404);
    h.click();
    await h.flush();
    assert.match(h.nodes.get('story-log-content').textContent, /No story actions/);
    h.reply(200, '{"type":"user_action","action":"New action"}\n');
    h.tick();
    await h.flush();
    h.reply(503);
    h.tick();
    await h.flush();
    assert.equal(h.nodes.get('story-log-content').children.length, 1);
    h.reply(200, '{"type":"user_action","action":"Recovered"}\n');
    h.tick();
    await h.flush();
    assert.equal(h.nodes.get('story-log-content').children[0].children[0].children[1].textContent, 'Recovered');
});

test('slow requests never overlap', async () => {
    const h = harness();
    const release = h.hold();
    h.click();
    h.tick();
    h.tick();
    assert.equal(h.calls.length, 1);
    release();
    await h.flush();
    h.tick();
    await h.flush();
    assert.equal(h.calls.length, 2);
});

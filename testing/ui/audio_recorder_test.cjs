const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/js/audio-recorder.js'), 'utf8')
  .replace(/^import .*;\r?\n/m, '').replace(/export /g, '');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(listen) {
  const wire = [];
  const captures = [];
  const encoders = [];
  const socket = { readyState: 1, send: value => wire.push(value) };
  class Encoder {
    constructor(callbacks) {
      this.callbacks = callbacks;
      this.state = 'unconfigured';
      this.encoded = [];
      this.flushes = [];
      encoders.push(this);
    }
    configure() { this.state = 'configured'; }
    encode(data) { this.encoded.push(data.options); }
    flush() { return new Promise(resolve => this.flushes.push(resolve)); }
    output() { this.callbacks.output({ byteLength: 2, copyTo: target => target.set([1, 2]) }); }
    close() { this.state = 'closed'; }
  }
  const context = {
    window: { agentWs: socket, AudioEncoder: Encoder, dispatchEvent() {} },
    AudioEncoder: Encoder,
    AudioData: class { constructor(options) { this.options = options; } close() {} },
    WebSocket: { OPEN: 1 }, CustomEvent: class {}, console,
    listenForSpeech: async options => {
      captures.push(options);
      return listen ? listen(options) : () => options.onSpeechEnd();
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const handler = () => context.window.agentWs.send('audio');
  return { context, socket, wire, captures, encoders, handler };
}

const types = wire => wire.map(value => value === 'audio' ? value : JSON.parse(value).type);
const pcm = () => ({ float32: new Float32Array(480).fill(0.25) });

test('new speech waits for preceding Opus output and end boundary', async () => {
  const h = harness();
  await h.context.startAudioRecorderWorklet(h.handler);
  const capture = h.captures[0];
  capture.onSpeechStart();
  capture.onData(pcm());
  capture.onSpeechEnd();
  await tick();
  capture.onSpeechStart();
  capture.onData(pcm());
  capture.onSpeechEnd();
  await tick();
  assert.deepEqual(types(h.wire), ['activity_start']);
  assert.equal(h.encoders[0].encoded.length, 1);
  h.encoders[0].output();
  h.encoders[0].flushes.shift()();
  await tick();
  assert.deepEqual(types(h.wire), ['activity_start', 'audio', 'activity_end', 'activity_start']);
  assert.equal(h.encoders[0].encoded.length, 2);
  assert.deepEqual(h.encoders[0].encoded.map(data => data.timestamp), [0, 30000]);
  h.encoders[0].output();
  h.encoders[0].flushes.shift()();
  await tick();
  assert.deepEqual(types(h.wire), ['activity_start', 'audio', 'activity_end', 'activity_start', 'audio', 'activity_end']);
});

test('microphone restart drains old capture before starting another', async () => {
  const h = harness();
  await h.context.startAudioRecorderWorklet(h.handler);
  const oldCapture = h.captures[0];
  oldCapture.onSpeechStart();
  oldCapture.onData(pcm());
  await tick();
  const stopping = h.context.stopMicrophone();
  const restarting = h.context.startAudioRecorderWorklet(h.handler);
  oldCapture.onSpeechStart();
  oldCapture.onData(pcm());
  await tick();
  assert.equal(h.captures.length, 1);
  h.encoders[0].output();
  h.encoders[0].flushes.shift()();
  await stopping;
  await restarting;
  h.captures[1].onSpeechStart();
  h.captures[1].onData(pcm());
  await tick();
  assert.deepEqual(types(h.wire), ['activity_start', 'audio', 'activity_end', 'activity_start']);
  assert.equal(h.encoders[0].state, 'closed');
  assert.equal(h.encoders[0].encoded.length, 1);
  h.encoders[0].output();
  oldCapture.onSpeechEnd();
  await tick();
  assert.deepEqual(types(h.wire), ['activity_start', 'audio', 'activity_end', 'activity_start']);
  const finalStop = h.context.stopMicrophone();
  await tick();
  h.encoders[1].flushes.shift()();
  await finalStop;
});

test('old capture cannot deliver packets or end into replacement socket', async () => {
  const h = harness();
  await h.context.startAudioRecorderWorklet(h.handler);
  h.captures[0].onSpeechStart();
  await tick();
  const stopping = h.context.stopMicrophone();
  const replacementWire = [];
  h.context.window.agentWs = { readyState: 1, send: value => replacementWire.push(value) };
  await tick();
  h.encoders[0].output();
  h.encoders[0].flushes.shift()();
  await stopping;
  assert.deepEqual(types(h.wire), ['activity_start']);
  assert.deepEqual(replacementWire, []);
});

test('speech captured before initialization returns delivers its first packet', async () => {
  const h = harness(async options => {
    options.onSpeechStart();
    options.onData(pcm());
    return () => options.onSpeechEnd();
  });
  // Exercise the actual canvas callback while its UI recording flag is false.
  const canvas = fs.readFileSync(path.join(root, 'templates/canvas.html'), 'utf8');
  const handlerSource = canvas.slice(canvas.indexOf('function audioRecorderHandler(opusBuffer) {'))
    .split('// ========================================', 1)[0];
  h.context.agentWs = h.socket;
  h.context.isRecording = false;
  h.context.clientAudioChunkCount = 0;
  vm.runInContext(handlerSource, h.context);
  await h.context.startAudioRecorderWorklet(h.context.audioRecorderHandler);
  await tick();
  h.encoders[0].output();
  assert.equal(h.encoders[0].encoded.length, 1);
  assert.equal(h.wire.length, 2);
  assert.equal(JSON.parse(h.wire[0]).type, 'activity_start');
  assert.equal(h.wire[1].byteLength, 2);
});

test('stopping pending microphone initialization releases the late capture', async () => {
  let resolveCapture;
  let released = false;
  const h = harness(() => new Promise(resolve => { resolveCapture = resolve; }));
  const starting = h.context.startAudioRecorderWorklet(h.handler);
  await tick();
  await h.context.stopMicrophone();
  h.captures[0].onSpeechStart();
  h.captures[0].onData(pcm());
  resolveCapture(() => { released = true; });
  const result = await starting;
  await tick();
  assert.equal(result[3], false);
  assert.equal(released, true);
  assert.equal(h.encoders[0].state, 'closed');
  assert.deepEqual(h.wire, []);
});

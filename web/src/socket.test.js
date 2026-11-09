import test from 'node:test';
import assert from 'node:assert/strict';
import { CanvasSync } from './socket.js';

const tick = () => new Promise(resolve => setImmediate(resolve));
const savedStroke = seq => ({ seq, color: '#000000', width: 6, points: [[1, 1, 0.5], [2, 2, 0.5]], authorName: null, createdAt: 1 });
const local = clientId => ({ canvasId: '0123456789', clientId, color: '#000000', width: 6, points: [[1, 1, 0.5], [2, 2, 0.5]] });

class Transport {
  constructor() { this.connected = false; this.handlers = {}; this.sent = []; this.reply = () => ({ ok: true, seq: this.sent.length }); }
  on(event, handler) { this.handlers[event] = handler; }
  connect() { this.connected = true; this.handlers.connect?.(); }
  disconnect() { this.connected = false; this.handlers.disconnect?.(); }
  timeout() { return this; }
  emit(event, data, ack) {
    if (event === 'canvas:join') { ack(null, { ok: true, online: ['Гость'], lastSeq: 0 }); return; }
    this.sent.push(data);
    const reply = this.reply(data);
    if (reply instanceof Error) ack(reply);
    else ack(null, reply);
  }
}

function setup(options = {}) {
  const transport = new Transport(), errors = [], states = [], confirmed = [], sleeps = [];
  const sync = new CanvasSync({
    canvas: { id: '0123456789', strokes: [], maxStrokes: 5000 }, name: null,
    onStroke: stroke => confirmed.push(stroke), onReset() {}, onPresence() {}, onFull() {},
    onError: code => errors.push(code), onState: state => states.push(state),
    transport, fetchStrokes: async () => ({ strokes: [] }),
    sleep: async ms => { sleeps.push(ms); }, ...options,
  });
  return { sync, transport, errors, states, confirmed, sleeps };
}

test('offline strokes stay FIFO and flush after join plus resync', async () => {
  const { sync, transport } = setup();
  sync.enqueue(local('first')); sync.enqueue(local('second'));
  assert.equal(transport.sent.length, 0);
  sync.start(); await tick();
  assert.equal(sync.outbox.length, 0);
  assert.deepEqual(transport.sent.map(s => s.clientId), ['first', 'second']);
  assert.equal(sync.confirmed().length, 2);
  sync.stop();
});

test('timeout retries the same UUID and confirms it only once', async () => {
  const { sync, transport, sleeps } = setup();
  let count = 0;
  transport.reply = () => ++count === 1 ? new Error('timeout') : { ok: true, seq: 1 };
  sync.start(); await tick();
  sync.enqueue(local('same-uuid')); await tick();
  assert.deepEqual(transport.sent.map(s => s.clientId), ['same-uuid', 'same-uuid']);
  assert.deepEqual(sleeps, [1000]);
  assert.equal(sync.confirmed().length, 1);
  sync.stop();
});

test('rate limit waits 2s and validation rejection removes optimistic stroke', async () => {
  const { sync, transport, sleeps, errors } = setup();
  let count = 0;
  transport.reply = () => ({ ok: false, error: { code: ++count === 1 ? 'RATE_LIMITED' : 'VALIDATION_ERROR' } });
  sync.start(); await tick();
  sync.enqueue(local('rejected')); await tick();
  assert.deepEqual(sleeps, [2000]);
  assert.deepEqual(errors, ['VALIDATION_ERROR']);
  assert.equal(sync.outbox.length, 0);
  assert.equal(sync.confirmed().length, 0);
  sync.stop();
});

test('reconnect fills sequence gaps below a previously seen max seq', async () => {
  let requested;
  const { sync } = setup({ fetchStrokes: async (id, afterSeq) => { requested = afterSeq; return { strokes: [savedStroke(1), savedStroke(2)] }; } });
  sync.confirm(savedStroke(2));
  sync.start(); await tick();
  assert.equal(requested, 0);
  assert.deepEqual(sync.confirmed().map(s => s.seq), [1, 2]);
  sync.stop();
});

test('full event before final ack preserves the accepted last stroke', async () => {
  const { sync, transport } = setup();
  transport.reply = () => {
    transport.handlers['canvas:full']({ maxStrokes: 1 });
    return { ok: true, seq: 1 };
  };
  sync.start(); await tick();
  sync.enqueue(local('last')); await tick();
  assert.equal(sync.outbox.length, 0);
  assert.equal(sync.confirmed()[0].seq, 1);
  sync.stop();
});

test('server errors back off 1,2,4s and retain stroke until recovery', async () => {
  const { sync, transport, errors, sleeps } = setup();
  let count = 0;
  transport.reply = () => ++count <= 4 ? { ok: false, error: { code: 'INTERNAL' } } : { ok: true, seq: 1 };
  sync.start(); await tick();
  sync.enqueue(local('retry')); await tick();
  assert.deepEqual(sleeps, [1000, 2000, 4000, 4000]);
  assert.deepEqual(errors, ['INTERNAL']);
  assert.equal(sync.outbox.length, 0);
  sync.stop();
});

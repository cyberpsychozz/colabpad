import test from 'node:test';
import assert from 'node:assert/strict';
import { WorldSync } from './socket.js';
import { ChunkStore } from './chunks.js';

const tick = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
const saved = (seq, cx = 0, cy = 0) => ({ seq, cx, cy, color: '#000000', width: 6, points: [[1, 1, 0.5], [2, 2, 0.5]], createdAt: 1 });
const local = clientId => ({ canvasId: 'world', clientId, color: '#000000', width: 6, points: [[1, 1, 0.5], [2, 2, 0.5]] });

class Transport {
  constructor() {
    this.connected = false; this.handlers = {}; this.sent = []; this.viewports = [];
    this.reply = () => ({ ok: true, seq: this.sent.length, cx: 0, cy: 0 });
    this.viewportReply = () => ({ ok: true, lastSeq: 0 });
  }
  on(event, handler) { this.handlers[event] = handler; }
  connect() { this.connected = true; this.handlers.connect?.(); }
  disconnect() { this.connected = false; this.handlers.disconnect?.(); }
  timeout() { return this; }
  emit(event, data, ack) {
    const reply = event === 'viewport:set'
      ? (this.viewports.push(data.chunks), this.viewportReply(data))
      : (this.sent.push(data), this.reply(data));
    if (reply instanceof Error) ack(reply);
    else ack(null, reply);
  }
}

function setup(options = {}) {
  const transport = new Transport(), errors = [], states = [], sleeps = [], fetched = [];
  const tile = () => ({ getContext: () => ({ setTransform() {}, clearRect() {} }) });
  const store = new ChunkStore({ size: 1024, createTile: tile, render() {} });
  const sync = new WorldSync({
    canvasId: 'world', store,
    onError: code => errors.push(code), onState: state => states.push(state),
    transport,
    fetchChunk: async (id, cx, cy, afterSeq) => { fetched.push([cx, cy, afterSeq]); return { strokes: [], fates: [], lastSeq: 0 }; },
    sleep: async ms => { sleeps.push(ms); }, ...options,
  });
  const seqs = (cx = 0, cy = 0) => [...store.get(cx, cy).strokes.keys()].sort((a, b) => a - b);
  return { sync, store, transport, errors, states, sleeps, fetched, seqs };
}

test('offline strokes stay FIFO and flush after subscribe plus fetch', async () => {
  const { sync, transport, fetched, seqs, states } = setup();
  sync.setViewport([[0, 0], [1, 0]]);
  sync.enqueue(local('first')); sync.enqueue(local('second'));
  assert.equal(transport.sent.length, 0);
  sync.start(); await tick();
  assert.deepEqual(transport.viewports, [[[0, 0], [1, 0]]]);
  assert.deepEqual(fetched, [[0, 0, 0], [1, 0, 0]]);
  assert.equal(sync.outbox.length, 0);
  assert.deepEqual(transport.sent.map(s => s.clientId), ['first', 'second']);
  assert.deepEqual(seqs(), [1, 2]);
  assert.deepEqual(states.at(-1), { connected: true, pending: 0 });
  sync.stop();
});

test('timeout retries the same UUID and confirms it only once', async () => {
  const { sync, transport, sleeps, seqs } = setup();
  let count = 0;
  transport.reply = () => ++count === 1 ? new Error('timeout') : { ok: true, seq: 1, cx: 0, cy: 0 };
  sync.setViewport([[0, 0]]);
  sync.start(); await tick();
  sync.enqueue(local('same-uuid')); await tick();
  assert.deepEqual(transport.sent.map(s => s.clientId), ['same-uuid', 'same-uuid']);
  assert.deepEqual(sleeps, [1000]);
  assert.deepEqual(seqs(), [1]);
  sync.stop();
});

test('rate limit waits 2s and validation rejection removes optimistic stroke', async () => {
  const { sync, transport, sleeps, errors, seqs } = setup();
  let count = 0;
  transport.reply = () => ({ ok: false, error: { code: ++count === 1 ? 'RATE_LIMITED' : 'VALIDATION_FAILED' } });
  sync.setViewport([[0, 0]]);
  sync.start(); await tick();
  sync.enqueue(local('rejected')); await tick();
  assert.deepEqual(sleeps, [2000]);
  assert.deepEqual(errors, ['VALIDATION_FAILED']);
  assert.equal(sync.outbox.length, 0);
  assert.deepEqual(seqs(), []);
  sync.stop();
});

test('reconnect refetches every subscribed chunk from its own watermark', async () => {
  const pages = { '0:0': { strokes: [saved(3)], fates: [], lastSeq: 9 }, '1:0': { strokes: [], fates: [], lastSeq: 9 } };
  const requests = [];
  const { sync, store, transport, seqs } = setup({
    fetchChunk: async (id, cx, cy, afterSeq) => { requests.push([cx, cy, afterSeq]); return pages[`${cx}:${cy}`]; },
  });
  sync.setViewport([[0, 0], [1, 0]]);
  sync.start(); await tick();
  assert.deepEqual(requests, [[0, 0, 0], [1, 0, 0]]);
  // A live stroke above the watermark arrives; a repeat of it is ignored.
  transport.handlers['stroke:new'](saved(12));
  transport.handlers['stroke:new'](saved(12));
  transport.handlers['stroke:new'](saved(13, 5, 5)); // not subscribed: ignored
  assert.deepEqual(seqs(), [3, 12]);
  // While offline, strokes 10 and 11 were committed in chunk (0,0).
  transport.disconnect();
  pages['0:0'] = { strokes: [saved(10), saved(11), saved(12)], fates: [], lastSeq: 14 };
  pages['1:0'] = { strokes: [], fates: [], lastSeq: 14 };
  requests.length = 0;
  transport.connect(); await tick();
  assert.deepEqual(requests, [[0, 0, 9], [1, 0, 9]]);
  assert.deepEqual(seqs(), [3, 10, 11, 12]);
  assert.equal(store.watermark(0, 0), 14);
  sync.stop();
});

test('moving the viewport subscribes first, then fetches only the new chunks', async () => {
  const { sync, store, transport, fetched } = setup();
  sync.setViewport([[0, 0], [1, 0]]);
  sync.start(); await tick();
  fetched.length = 0;
  sync.setViewport([[1, 0], [2, 0]]);
  sync.setViewport([[1, 0], [2, 0]]);
  await tick();
  assert.deepEqual(transport.viewports.at(-1), [[1, 0], [2, 0]]);
  assert.equal(transport.viewports.length, 2);
  assert.deepEqual(fetched, [[2, 0, 0]]);
  assert.equal(store.get(0, 0), undefined);
  sync.stop();
});

test('server errors back off 1,2,4s and retain stroke until recovery', async () => {
  const { sync, transport, errors, sleeps } = setup();
  let count = 0;
  transport.reply = () => ++count <= 4 ? { ok: false, error: { code: 'INTERNAL' } } : { ok: true, seq: 1, cx: 0, cy: 0 };
  sync.setViewport([[0, 0]]);
  sync.start(); await tick();
  sync.enqueue(local('retry')); await tick();
  assert.deepEqual(sleeps, [1000, 2000, 4000, 4000]);
  assert.deepEqual(errors, ['INTERNAL']);
  assert.equal(sync.outbox.length, 0);
  sync.stop();
});

test('a failing chunk fetch is retried with backoff and does not lose the outbox', async () => {
  let failures = 2;
  const { sync, sleeps, seqs } = setup({
    fetchChunk: async () => { if (failures-- > 0) throw new Error('network'); return { strokes: [saved(1)], fates: [], lastSeq: 1 }; },
  });
  sync.setViewport([[0, 0]]);
  sync.enqueue(local('queued'));
  sync.start(); await tick();
  assert.deepEqual(sleeps, [1000, 2000]);
  assert.deepEqual(seqs(), [1]);
  assert.equal(sync.outbox.length, 0);
  sync.stop();
});

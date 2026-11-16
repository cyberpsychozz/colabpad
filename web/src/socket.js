// Resilient world sync: an outbox with acknowledged delivery, an epoch guard
// against stale replies, and reconnect -> resubscribe -> refetch of every
// subscribed chunk from its watermark (the lastSeq of its previous fetch).
import { io } from 'socket.io-client';
import { getChunk } from './api.js';

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const BACKOFF = [1000, 2000, 4000];

export class WorldSync {
  constructor({ canvasId, store, onState, onError, onChange = () => {}, onEvent = () => {}, onChunk = () => {},
    transport, fetchChunk = getChunk, sleep = wait }) {
    Object.assign(this, { canvasId, store, onState, onError, onChange, onEvent, onChunk, fetchChunk, sleep });
    this.socket = transport || io({ autoConnect: false });
    this.outbox = [];
    this.wanted = [];
    this.ready = false;       // subscribed and caught up since the last connect
    this.busy = false;        // an outbox flush is running
    this.pumping = false;     // a viewport update is running
    this.viewportDirty = false;
    this.refetchAll = false;
    this.stopped = false;
    this.epoch = 0;
    this.socket.on('connect', () => {
      this.epoch++; this.ready = false; this.refetchAll = true;
      this.state();
      this.requestViewport();
    });
    this.socket.on('disconnect', () => { this.epoch++; this.ready = false; this.state(); });
    this.socket.on('connect_error', () => { this.ready = false; this.state(); });
    this.socket.on('stroke:new', stroke => {
      if (!this.stopped && this.store.addStroke(stroke)) this.onChange();
    });
    this.socket.on('stroke:fate', ({ seqs, fate, entityId }) => {
      if (!this.stopped && this.store.applyFate(seqs, fate, entityId)) this.onChange();
    });
    for (const name of ['alive:goo', 'alive:progress', 'alive:choose', 'alive:failed', 'world:event']) {
      this.socket.on(name, data => { if (!this.stopped) this.onEvent(name, data); });
    }
  }

  start() { this.socket.connect(); }
  stop() { this.stopped = true; this.epoch++; this.ready = false; this.socket.disconnect(); }
  state() { if (!this.stopped) this.onState({ connected: this.ready, pending: this.outbox.length }); }

  ack(event, data) {
    return new Promise((resolve, reject) => {
      this.socket.timeout(5000).emit(event, data, (error, reply) => {
        if (error || !reply) reject(error || new Error('Empty acknowledgement'));
        else resolve(reply);
      });
    });
  }

  // chunks: [[cx, cy], …], at most the server's limit.
  setViewport(chunks) {
    const same = chunks.length === this.wanted.length
      && chunks.every(([cx, cy], i) => this.wanted[i][0] === cx && this.wanted[i][1] === cy);
    if (same) return;
    this.wanted = chunks;
    this.requestViewport();
  }

  requestViewport() {
    this.viewportDirty = true;
    if (!this.pumping) this.pump();
  }

  // One loop owns the subscription: enter the rooms first, then fetch, so nothing
  // committed in between can be missed. Changes made meanwhile run another round.
  async pump() {
    this.pumping = true;
    let failures = 0;
    try {
      while (this.viewportDirty && !this.stopped && this.socket.connected) {
        const epoch = this.epoch, full = this.refetchAll, chunks = this.wanted;
        this.viewportDirty = false; this.refetchAll = false;
        const current = () => epoch === this.epoch && !this.stopped;
        try {
          const reply = await this.ack('viewport:set', { canvasId: this.canvasId, chunks });
          if (!current()) { this.refetchAll = true; this.viewportDirty = true; continue; }
          if (!reply.ok) {
            const code = reply.error?.code || 'INTERNAL';
            if (code !== 'RATE_LIMITED' && code !== 'INTERNAL') { this.onError(code); return; }
            throw new Error(code);
          }
          this.store.retain(chunks);
          await Promise.all(chunks.filter(([cx, cy]) => full || !this.store.isLoaded(cx, cy)).map(async ([cx, cy]) => {
            const data = await this.fetchChunk(this.canvasId, cx, cy, this.store.watermark(cx, cy));
            if (current()) { this.store.merge(cx, cy, data); this.onChunk(cx, cy, data); }
          }));
          if (!current()) { this.refetchAll = true; this.viewportDirty = true; continue; }
          failures = 0;
          this.onChange();
          if (!this.ready) { this.ready = true; this.state(); this.flush(); }
          // Stay under the server's viewport rate limit when panning continuously.
          if (this.viewportDirty) await this.sleep(300);
        } catch {
          this.refetchAll ||= full; this.viewportDirty = true;
          if (!current() || !this.socket.connected) continue;
          failures++;
          if (failures === 4) this.onError('INTERNAL');
          await this.sleep(BACKOFF[Math.min(failures - 1, 2)]);
        }
      }
    } finally {
      this.pumping = false;
    }
  }

  enqueue(stroke) {
    this.outbox.push(stroke);
    this.state();
    this.flush();
  }

  async flush() {
    if (this.busy || !this.ready || this.stopped) return;
    this.busy = true;
    let failures = 0;
    try {
      while (this.ready && this.socket.connected && this.outbox.length && !this.stopped) {
        const stroke = this.outbox[0];
        try {
          const reply = await this.ack('stroke:add', stroke);
          if (this.stopped) return;
          if (reply.ok) {
            this.store.addStroke({ ...stroke, seq: reply.seq, cx: reply.cx, cy: reply.cy, createdAt: Date.now() });
            this.outbox.shift(); failures = 0; this.state(); this.onChange();
            continue;
          }
          const code = reply.error?.code || 'INTERNAL';
          if (code === 'RATE_LIMITED') { await this.sleep(2000); continue; }
          if (code === 'NOT_JOINED') { this.ready = false; this.refetchAll = true; this.requestViewport(); break; }
          if (code === 'INTERNAL') throw new Error(code);
          this.outbox.shift();
          this.onError(code);
          this.state();
        } catch {
          if (!this.ready || !this.socket.connected || this.stopped) break;
          failures++;
          if (failures === 4) this.onError('INTERNAL');
          await this.sleep(BACKOFF[Math.min(failures - 1, 2)]);
        }
      }
    } finally {
      this.busy = false;
      // A reconnect/resync can finish while an old ack is timing out.
      if (this.ready && this.socket.connected && this.outbox.length && !this.stopped) this.flush();
    }
  }
}

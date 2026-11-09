import { io } from 'socket.io-client';
import { getStrokes } from './api.js';

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

export class CanvasSync {
  constructor({ canvas, name, onStroke, onReset, onState, onPresence, onFull, onError,
    transport, fetchStrokes = getStrokes, sleep = wait }) {
    Object.assign(this, { canvas, name, onStroke, onReset, onState, onPresence, onFull, onError, fetchStrokes, sleep });
    this.socket = transport || io({ autoConnect: false });
    this.outbox = [];
    this.saved = new Map(canvas.strokes.map(stroke => [stroke.seq, stroke]));
    this.lastSeq = Math.max(0, ...this.saved.keys());
    this.ready = false;
    this.busy = false;
    this.stopped = false;
    this.epoch = 0;
    this.socket.on('connect', () => this.resync());
    this.socket.on('disconnect', () => { this.epoch++; this.ready = false; this.state(); });
    this.socket.on('connect_error', () => { this.ready = false; this.state(); });
    this.socket.on('stroke:new', stroke => this.confirm(stroke));
    this.socket.on('presence:update', ({ online }) => this.onPresence(online));
    this.socket.on('canvas:full', data => this.onFull(data));
  }

  start() { this.socket.connect(); }
  stop() { this.stopped = true; this.epoch++; this.ready = false; this.socket.disconnect(); }
  confirmed() { return [...this.saved.values()].sort((a, b) => a.seq - b.seq); }
  state() { if (!this.stopped) this.onState({ connected: this.ready, pending: this.outbox.length }); }

  confirm(stroke) {
    if (this.stopped) return;
    if (!this.saved.has(stroke.seq)) {
      this.saved.set(stroke.seq, stroke);
      this.lastSeq = Math.max(this.lastSeq, stroke.seq);
      this.onStroke(stroke);
    }
  }

  ack(event, data) {
    return new Promise((resolve, reject) => {
      this.socket.timeout(5000).emit(event, data, (error, reply) => {
        if (error || !reply) reject(error || new Error('Empty acknowledgement'));
        else resolve(reply);
      });
    });
  }

  async resync() {
    const epoch = ++this.epoch;
    this.ready = false;
    this.state();
    for (let retry = 0; !this.stopped && this.socket.connected && epoch === this.epoch; retry++) {
      try {
        const join = await this.ack('canvas:join', { canvasId: this.canvas.id, name: this.name });
        if (epoch !== this.epoch || this.stopped) return;
        if (!join.ok) {
          if (join.error.code === 'ROOM_FULL') { this.onError('ROOM_FULL'); return; }
          if (join.error.code !== 'INTERNAL' && join.error.code !== 'RATE_LIMITED') {
            this.onError(join.error.code); return;
          }
          throw new Error(join.error.code);
        }
        this.onPresence(join.online);
        // A live event may race the initial snapshot, leaving a gap below max seq.
        // Fetch from the contiguous prefix so an observed later seq cannot hide it.
        let contiguous = 0;
        while (this.saved.has(contiguous + 1)) contiguous++;
        const { strokes } = await this.fetchStrokes(this.canvas.id, contiguous);
        if (epoch !== this.epoch || this.stopped) return;
        for (const stroke of strokes) this.saved.set(stroke.seq, stroke);
        this.lastSeq = Math.max(this.lastSeq, ...this.saved.keys());
        this.onReset(this.confirmed());
        this.ready = true;
        this.state();
        this.flush();
        return;
      } catch {
        if (retry === 3) this.onError('INTERNAL');
        await this.sleep([1000, 2000, 4000][Math.min(retry, 2)]);
      }
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
            this.confirm({ ...stroke, seq: reply.seq, authorName: this.name, createdAt: Date.now() });
            this.outbox.shift(); failures = 0; this.state();
            continue;
          }
          const code = reply.error?.code || 'INTERNAL';
          if (code === 'RATE_LIMITED') { await this.sleep(2000); continue; }
          if (code === 'NOT_JOINED') { this.ready = false; this.resync(); break; }
          if (code === 'INTERNAL') throw new Error(code);
          this.outbox.shift();
          if (code === 'CANVAS_FULL') {
            // An in-flight final accepted stroke is confirmed before the next
            // queued stroke is rejected, even if canvas:full arrived before its ack.
            this.outbox.length = 0;
            this.onFull({ maxStrokes: this.canvas.maxStrokes });
          } else this.onError(code);
          this.state();
        } catch {
          if (!this.ready || !this.socket.connected || this.stopped) break;
          failures++;
          if (failures === 4) this.onError('INTERNAL');
          await this.sleep([1000, 2000, 4000][Math.min(failures - 1, 2)]);
        }
      }
    } finally {
      this.busy = false;
      // A reconnect/resync can finish while an old ack is timing out.
      if (this.ready && this.socket.connected && this.outbox.length && !this.stopped) this.flush();
    }
  }
}

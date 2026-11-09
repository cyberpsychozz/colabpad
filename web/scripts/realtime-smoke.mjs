// Optional loopback smoke test. Creates one permanent test canvas.
// Run: node scripts/realtime-smoke.mjs http://127.0.0.1:8000
import { io } from 'socket.io-client';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';

const base = process.argv[2] || 'http://127.0.0.1:8000';
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname)) {
  throw new Error('This smoke test may only run against a local server');
}
const response = await fetch(new URL('/api/canvases', base), { method: 'POST' });
if (!response.ok) throw new Error(await response.text());
const canvas = await response.json();
const sockets = [];
try {
  await Promise.all(Array.from({ length: 10 }, async (_, i) => {
    const socket = io(base, { autoConnect: false, forceNew: true, reconnection: false, transports: ['websocket'], timeout: 5000 });
    sockets[i] = socket;
    const connected = new Promise((resolve, reject) => { socket.once('connect', resolve); socket.once('connect_error', reject); });
    socket.connect();
    await connected;
    const joined = await socket.timeout(5000).emitWithAck('canvas:join', { canvasId: canvas.id, name: 'Test ' + i });
    if (!joined.ok) throw new Error(JSON.stringify(joined));
  }));
  const observer = sockets[9], latencies = [];
  for (let i = 0; i < 50; i++) {
    let timer;
    const received = new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Remote stroke timeout')), 5000);
      observer.once('stroke:new', stroke => { clearTimeout(timer); resolve(stroke); });
    });
    const started = performance.now();
    const ack = await sockets[i % 9].timeout(5000).emitWithAck('stroke:add', {
      canvasId: canvas.id, clientId: randomUUID(), color: '#123456', width: 6,
      points: Array.from({ length: 12 }, (_, j) => [i * 4 + j, 100 + j, 0.5]),
    });
    const remote = await received;
    if (!ack.ok || remote.seq !== ack.seq) throw new Error('Sequence mismatch');
    latencies.push(performance.now() - started);
  }
  latencies.sort((a, b) => a - b);
  const data = await (await fetch(new URL('/api/canvases/' + canvas.id, base))).json();
  if (data.strokeCount !== 50) throw new Error('Stroke count mismatch');
  console.log(JSON.stringify({
    canvasId: canvas.id, clients: 10, strokes: 50,
    p95Milliseconds: Number(latencies[Math.ceil(latencies.length * 0.95) - 1].toFixed(2)),
    maxMilliseconds: Number(latencies.at(-1).toFixed(2)),
    environment: 'loopback; acknowledgement plus remote stroke receipt',
  }, null, 2));
} finally {
  sockets.forEach(socket => socket?.disconnect());
}

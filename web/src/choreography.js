// Version 1; mirrored by app/alive/choreography.py and shared fixture tests.
export const DEFAULT = { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1 };

export function value(track, t) {
  const keys = track.keys;
  if (t <= keys[0].t) return keys[0].v;
  for (let i = 1; i < keys.length; i++) {
    const a = keys[i - 1], b = keys[i];
    if (t < b.t) {
      let u = (t - a.t) / (b.t - a.t);
      if (a.ease === 'smooth') u = u * u * (3 - 2 * u);
      else if (a.ease === 'in') u *= u;
      else if (a.ease === 'out') u = 1 - (1 - u) ** 2;
      else if (a.ease === 'hold') u = 0;
      return a.v + (b.v - a.v) * u;
    }
  }
  return keys.at(-1).v;
}

export function pose(plan, t, target) {
  const result = { ...DEFAULT };
  for (const track of plan.tracks) if (track.target === target) result[track.property] = value(track, t);
  return result;
}

export class ServerClock {
  constructor() { this.offset = null; this.rtt = Infinity; }
  sample(serverTime, sent, received) {
    const rtt = received - sent;
    // The lowest-latency sample has the smallest uncertainty. Allow periodic refresh.
    if (rtt <= this.rtt + 10) { this.offset = serverTime - (sent + received) / 2; this.rtt = rtt; }
  }
  now(monotonic) { return this.offset == null ? null : monotonic + this.offset; }
}

export function playbackTime(entity, serverTime) {
  const p = entity.view?.playback;
  if (!p) return 0;
  // Bounded extrapolation prevents disconnected viewers from continuing indefinitely.
  const extra = p.running && serverTime != null ? Math.max(0, Math.min(300, serverTime - p.serverTime)) : 0;
  const elapsed = p.elapsed + extra;
  // Older checkpoints omit pauseMs and retain their single-performance semantics.
  const phase = p.pauseMs == null ? elapsed : elapsed % (entity.plan.duration + p.pauseMs);
  return Math.min(entity.plan.duration, phase);
}

export function transformPoint(x, y, pivot, p) {
  const c = Math.cos(p.rotation * Math.PI / 180), s = Math.sin(p.rotation * Math.PI / 180);
  const dx = (x - pivot[0]) * p.scaleX, dy = (y - pivot[1]) * p.scaleY;
  return [p.x + pivot[0] + dx * c - dy * s, p.y + pivot[1] + dx * s + dy * c];
}

export function layerCorners(layer, plan, t) {
  const [x, y] = layer.offset, [w, h] = layer.size;
  const body = pose(plan, t, 'body'), part = pose(plan, t, layer.id);
  return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]].map(point => {
    if (layer.id !== 'body') point = transformPoint(...point, layer.pivot, part);
    return transformPoint(...point, [0, 0], body);
  });
}

function apply(ctx, p, pivot) {
  ctx.translate(p.x + pivot[0], p.y + pivot[1]);
  ctx.rotate(p.rotation * Math.PI / 180);
  ctx.scale(p.scaleX, p.scaleY);
  ctx.translate(-pivot[0], -pivot[1]);
}

export function drawLayered(ctx, image, manifest, plan, elapsed) {
  ctx.save();
  apply(ctx, pose(plan, elapsed, 'body'), [0, 0]);
  for (const layer of manifest.layers) {
    ctx.save();
    if (layer.id !== 'body') apply(ctx, pose(plan, elapsed, layer.id), layer.pivot);
    ctx.drawImage(image, ...layer.src, ...layer.offset, ...layer.size);
    ctx.restore();
  }
  ctx.restore();
}

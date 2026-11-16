// The camera maps world coordinates to screen pixels: screen = (world - origin) * z.
export const MAX_ZOOM = 4;

export class Camera {
  constructor(bounds, chunkSize) {
    this.bounds = bounds;
    this.chunkSize = chunkSize;
    this.x = 0; this.y = 0; this.z = 1;
    this.viewW = 1; this.viewH = 1;
  }

  // Zooming out is limited so that the viewport never spans more than 3x2 (or 2x3)
  // chunks of world, which keeps the visible set within the 12-chunk subscription.
  minZoom() {
    const s = this.chunkSize;
    return Math.min(
      Math.max(this.viewW / (3 * s), this.viewH / (2 * s)),
      Math.max(this.viewW / (2 * s), this.viewH / (3 * s)),
      MAX_ZOOM,
    );
  }

  clamp() {
    this.z = Math.min(MAX_ZOOM, Math.max(this.minZoom(), this.z));
    for (const [axis, view, size] of [['x', this.viewW, this.bounds.w], ['y', this.viewH, this.bounds.h]]) {
      const span = view / this.z;
      // A world smaller than the viewport is centred; otherwise the view stays inside it.
      this[axis] = span >= size ? (size - span) / 2 : Math.min(size - span, Math.max(0, this[axis]));
    }
  }

  resize(width, height) {
    const center = this.center();
    this.viewW = Math.max(1, width); this.viewH = Math.max(1, height);
    this.centerOn(center[0], center[1]);
  }

  toWorld(sx, sy) { return [this.x + sx / this.z, this.y + sy / this.z]; }
  toScreen(wx, wy) { return [(wx - this.x) * this.z, (wy - this.y) * this.z]; }
  center() { return this.toWorld(this.viewW / 2, this.viewH / 2); }

  centerOn(wx, wy, z = this.z) {
    this.z = z;
    this.clamp();
    this.x = wx - this.viewW / 2 / this.z;
    this.y = wy - this.viewH / 2 / this.z;
    this.clamp();
  }

  panBy(dx, dy) {
    this.x -= dx / this.z; this.y -= dy / this.z;
    this.clamp();
  }

  // Zoom keeping the world point under (sx, sy) fixed on screen.
  zoomAt(sx, sy, factor) {
    const [wx, wy] = this.toWorld(sx, sy);
    this.z *= factor;
    this.clamp();
    this.x = wx - sx / this.z; this.y = wy - sy / this.z;
    this.clamp();
  }
}

// Chunks to subscribe to: everything visible first, then the surrounding ring,
// nearest to the viewport centre first, capped at `max`.
export function visibleChunks(camera, max) {
  const size = camera.chunkSize;
  const columns = Math.ceil(camera.bounds.w / size), rows = Math.ceil(camera.bounds.h / size);
  const [left, top] = camera.toWorld(0, 0), [right, bottom] = camera.toWorld(camera.viewW, camera.viewH);
  const x0 = Math.floor(left / size), x1 = Math.ceil(right / size) - 1;
  const y0 = Math.floor(top / size), y1 = Math.ceil(bottom / size) - 1;
  const [centerX, centerY] = camera.center();
  const found = [];
  for (let cy = y0 - 1; cy <= y1 + 1; cy++) {
    for (let cx = x0 - 1; cx <= x1 + 1; cx++) {
      if (cx < 0 || cy < 0 || cx >= columns || cy >= rows) continue;
      const visible = cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1;
      const distance = Math.hypot((cx + 0.5) * size - centerX, (cy + 0.5) * size - centerY);
      found.push({ cx, cy, visible, distance });
    }
  }
  found.sort((a, b) => (b.visible - a.visible) || (a.distance - b.distance));
  return found.slice(0, max).map(({ cx, cy, visible }) => ({ cx, cy, visible }));
}

// Deep links: /w#x=…&y=…&z=…
export function parseHash(hash) {
  const params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
  const x = Number(params.get('x')), y = Number(params.get('y')), z = Number(params.get('z'));
  if (!params.has('x') || !params.has('y') || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y, z: params.has('z') && Number.isFinite(z) && z > 0 ? z : null };
}

export function formatHash(camera) {
  const [x, y] = camera.center();
  return `#x=${Math.round(x)}&y=${Math.round(y)}&z=${Number(camera.z.toFixed(2))}`;
}

// Stroke storage and bitmap cache, one tile per chunk.
import { renderStroke, strokeBounds } from './draw.js';

export const chunkKey = (cx, cy) => `${cx}:${cy}`;
const FOSSIL_GREY = '#969696';

export class ChunkStore {
  // createTile(pixels) returns a square canvas; injected so tests can run without a DOM.
  constructor({ size, createTile, render = renderStroke }) {
    Object.assign(this, { size, createTile, render });
    this.chunks = new Map();
  }

  get(cx, cy) { return this.chunks.get(chunkKey(cx, cy)); }
  isLoaded(cx, cy) { return !!this.get(cx, cy)?.loaded; }
  watermark(cx, cy) { return this.get(cx, cy)?.watermark || 0; }

  ensure(cx, cy) {
    let chunk = this.get(cx, cy);
    if (!chunk) {
      chunk = { cx, cy, strokes: new Map(), fates: new Map(), fossils: new Map(), watermark: 0, loaded: false, tile: null, scale: 0, dirty: true, drawnSeq: 0 };
      this.chunks.set(chunkKey(cx, cy), chunk);
    }
    return chunk;
  }

  // Keep only the listed chunks; the rest free their strokes and tiles.
  retain(list) {
    const keep = new Set(list.map(([cx, cy]) => chunkKey(cx, cy)));
    for (const key of [...this.chunks.keys()]) if (!keep.has(key)) this.chunks.delete(key);
    for (const [cx, cy] of list) this.ensure(cx, cy);
  }

  // A chunk fetch: strokes after the previous watermark, current fates, new watermark.
  merge(cx, cy, { strokes, fates, lastSeq, fossils = [] }) {
    const chunk = this.get(cx, cy);
    if (!chunk) return;
    for (const fossil of fossils) this.#fossil(chunk, fossil);
    for (const stroke of strokes) this.#put(chunk, stroke);
    chunk.fates = new Map(fates.map(fate => [fate.seq, fate]));
    chunk.watermark = Math.max(chunk.watermark, lastSeq);
    chunk.loaded = true;
    this.#dirtyAround(chunk);
  }

  // A live or acknowledged stroke. Idempotent by seq; ignored for untracked chunks,
  // which get the stroke from their fetch once they are subscribed.
  addStroke(stroke) {
    const chunk = this.get(stroke.cx, stroke.cy);
    if (!chunk || chunk.strokes.has(stroke.seq)) return false;
    const saved = this.#put(chunk, stroke);
    for (const tile of this.#touched(saved)) {
      // New strokes are drawn on top of a clean tile; anything out of order repaints.
      if (tile.dirty || !tile.tile || saved.seq < tile.drawnSeq) tile.dirty = true;
      else this.#paint(tile, [saved]);
    }
    return true;
  }

  setFates(cx, cy, fates) {
    const chunk = this.get(cx, cy);
    if (!chunk) return;
    for (const fate of fates) chunk.fates.set(fate.seq, fate);
    this.#dirtyAround(chunk);
  }

  // A live fate for strokes wherever they are stored; true if anything changed.
  applyFate(seqs, fate, entityId = null) {
    const wanted = new Set(seqs);
    let changed = false;
    for (const chunk of this.chunks.values()) {
      const hit = [...wanted].filter(seq => chunk.strokes.has(seq));
      if (!hit.length) continue;
      for (const seq of hit) chunk.fates.set(seq, { seq, fate, entityId });
      this.#dirtyAround(chunk);
      changed = true;
    }
    return changed;
  }

  // A fossil lies where its entity died: its strokes, grey, shifted from where they were drawn.
  addFossil(fossil) {
    const chunk = this.get(Math.floor(fossil.x / this.size), Math.floor(fossil.y / this.size));
    if (!chunk || chunk.fossils.has(fossil.entityId)) return false;
    this.#fossil(chunk, fossil);
    this.#dirtyAround(chunk);
    return true;
  }

  #fossil(chunk, { entityId, dx, dy, strokes }) {
    chunk.fossils.set(entityId, strokes.map(stroke => {
      const moved = { ...stroke, color: FOSSIL_GREY, points: stroke.points.map(([x, y, p]) => [x + dx, y + dy, p]) };
      return { ...moved, box: strokeBounds(moved) };
    }));
  }

  hasStrokes() {
    for (const chunk of this.chunks.values()) if (chunk.strokes.size) return true;
    return false;
  }

  allLoaded(list) { return list.every(([cx, cy]) => this.isLoaded(cx, cy)); }

  // The chunk's bitmap at `scale` device pixels per world pixel, repainted if stale.
  tile(cx, cy, scale = 1) {
    const chunk = this.get(cx, cy);
    if (!chunk?.loaded) return null;
    if (chunk.dirty || !chunk.tile || chunk.scale !== scale) {
      if (!chunk.tile || chunk.scale !== scale) chunk.tile = this.createTile(Math.round(this.size * scale));
      chunk.scale = scale;
      const ctx = chunk.tile.getContext('2d');
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, chunk.tile.width, chunk.tile.height);
      chunk.drawnSeq = 0;
      const x0 = cx * this.size, y0 = cy * this.size, x1 = x0 + this.size, y1 = y0 + this.size;
      const strokes = [];
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const source = this.get(cx + dx, cy + dy);
          if (!source) continue;
          for (const stroke of source.strokes.values()) {
            const [bx0, by0, bx1, by1] = stroke.box;
            if (!source.fates.has(stroke.seq) && bx1 > x0 && bx0 < x1 && by1 > y0 && by0 < y1) strokes.push(stroke);
          }
        }
      }
      strokes.sort((a, b) => a.seq - b.seq);
      this.#paint(chunk, strokes);
      // Fossils from this chunk and its neighbours, over the static strokes.
      const fossils = [];
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          for (const parts of this.get(cx + dx, cy + dy)?.fossils.values() || []) {
            for (const stroke of parts) {
              const [bx0, by0, bx1, by1] = stroke.box;
              if (bx1 > x0 && bx0 < x1 && by1 > y0 && by0 < y1) fossils.push(stroke);
            }
          }
        }
      }
      const drawn = chunk.drawnSeq;
      this.#paint(chunk, fossils);
      chunk.drawnSeq = drawn;
      chunk.dirty = false;
    }
    return chunk.tile;
  }

  #put(chunk, stroke) {
    const saved = { ...stroke, box: strokeBounds(stroke) };
    chunk.strokes.set(saved.seq, saved);
    return saved;
  }

  #paint(chunk, strokes) {
    const ctx = chunk.tile.getContext('2d');
    ctx.setTransform(chunk.scale, 0, 0, chunk.scale, -chunk.cx * this.size * chunk.scale, -chunk.cy * this.size * chunk.scale);
    for (const stroke of strokes) {
      this.render(ctx, stroke);
      chunk.drawnSeq = Math.max(chunk.drawnSeq, stroke.seq);
    }
  }

  // Tracked chunks whose tile a stroke paints into: its own and any it bleeds over.
  #touched(stroke) {
    const [x0, y0, x1, y1] = stroke.box, result = [];
    for (let cy = Math.floor(y0 / this.size); cy <= Math.floor(y1 / this.size); cy++) {
      for (let cx = Math.floor(x0 / this.size); cx <= Math.floor(x1 / this.size); cx++) {
        const chunk = this.get(cx, cy);
        if (chunk) result.push(chunk);
      }
    }
    return result;
  }

  #dirtyAround(chunk) {
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const near = this.get(chunk.cx + dx, chunk.cy + dy);
        if (near) near.dirty = true;
      }
    }
  }
}

// Entities known to this client, grouped by the chunk they were loaded with.
import { chunkKey } from './chunks.js';

export class EntityStore {
  // loadImage(url) returns something drawable (an <img>); injected for tests.
  constructor({ size, loadImage }) {
    Object.assign(this, { size, loadImage });
    this.entities = new Map();
    this.images = new Map();
  }

  #key(entity) { return chunkKey(Math.floor(entity.x / this.size), Math.floor(entity.y / this.size)); }

  put(entity, bornAt = null) {
    const known = this.entities.get(entity.id);
    this.entities.set(entity.id, { ...known, ...entity, chunk: this.#key(entity), bornAt: bornAt ?? known?.bornAt ?? null });
  }

  // A chunk fetch is the truth for that chunk: replace what was known there.
  setChunk(cx, cy, list) {
    const key = chunkKey(cx, cy), fresh = new Set(list.map(entity => entity.id));
    for (const [id, entity] of this.entities) if (entity.chunk === key && !fresh.has(id)) this.entities.delete(id);
    for (const entity of list) this.put(entity);
  }

  retain(list) {
    const keep = new Set(list.map(([cx, cy]) => chunkKey(cx, cy)));
    for (const [id, entity] of this.entities) if (!keep.has(entity.chunk)) this.entities.delete(id);
  }

  remove(id) { this.entities.delete(id); }

  image(entity) {
    const url = entity.sprite.sheet;
    if (!this.images.has(url)) this.images.set(url, this.loadImage(url));
    return this.images.get(url);
  }

  // Painter's order: lower on the screen is nearer the viewer.
  sorted() { return [...this.entities.values()].sort((a, b) => a.y - b.y || (a.id < b.id ? -1 : 1)); }
}

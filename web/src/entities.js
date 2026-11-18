// Entities known to this client: latest server state plus a smoothed render position.
import { chunkKey } from './chunks.js';

const DEATH_MS = 750;

export class EntityStore {
  // loadImage(url) returns something drawable (an <img>); injected for tests.
  constructor({ size, loadImage }) {
    Object.assign(this, { size, loadImage });
    this.entities = new Map();
    this.byHandle = new Map(); // world:delta names entities by a short number (ref)
    this.ghosts = [];          // entities playing their death animation
    this.images = new Map();
  }

  #key(entity) { return chunkKey(Math.floor(entity.x / this.size), Math.floor(entity.y / this.size)); }

  // A full entity (a spawn, a snapshot, or one that walked into view).
  put(entity, bornAt = null) {
    if (entity.ref != null) this.byHandle.set(entity.ref, entity.id);
    const known = this.entities.get(entity.id);
    this.entities.set(entity.id, {
      rx: entity.x, ry: entity.y, dir: 0, animStart: 0, ...known, ...entity,
      chunk: this.#key(entity), bornAt: bornAt ?? known?.bornAt ?? null,
    });
  }

  // A chunk fetch or world:snapshot is the truth for that chunk.
  setChunk(cx, cy, list) {
    const key = chunkKey(cx, cy), fresh = new Set(list.map(entity => entity.id));
    for (const [id, entity] of this.entities) if (entity.chunk === key && !fresh.has(id)) this.entities.delete(id);
    for (const entity of list) this.put(entity);
  }

  // One chunk's part of a world:delta: full entities that entered, compact updates, and
  // handles that left. Rows and departures name entities by their handle, ref.
  applyDelta({ cx, cy, upd, gone, enter = [] }, now) {
    const key = chunkKey(cx, cy);
    for (const entity of enter) this.put(entity);
    for (const [ref, x, y, state, anim, hp, view] of upd) {
      const entity = this.entities.get(this.byHandle.get(ref));
      if (!entity) continue;
      if (Math.abs(x - entity.x) > 0.5) entity.dir = x > entity.x ? 1 : -1;
      if (anim !== entity.anim) entity.animStart = now;
      Object.assign(entity, { x, y, state, anim, hp, chunk: key });
      if (view) entity.view = view;
    }
    // An id that left this chunk stays if it has since been seen in another one.
    for (const ref of gone) {
      const id = this.byHandle.get(ref);
      if (this.entities.get(id)?.chunk === key) this.entities.delete(id);
    }
  }

  // A death: with a fossil the body plays its death animation before it goes.
  die(id, fossil, now) {
    const entity = this.entities.get(id);
    if (!entity) return null;
    this.entities.delete(id);
    if (fossil) this.ghosts.push({ ...entity, anim: 'death', animStart: now, until: now + DEATH_MS });
    return entity;
  }

  retain(list) {
    const keep = new Set(list.map(([cx, cy]) => chunkKey(cx, cy)));
    for (const [id, entity] of this.entities) if (!keep.has(entity.chunk)) this.entities.delete(id);
  }

  // Move render positions towards the server's; returns true while anything is moving.
  advance(seconds, now) {
    const blend = Math.min(1, seconds * 12);
    let moving = false;
    for (const entity of this.entities.values()) {
      const dx = entity.x - entity.rx, dy = entity.y - entity.ry;
      // A jump (a drag, a knockback, first sight) is not animated.
      if (Math.abs(dx) > 200 || Math.abs(dy) > 200) { entity.rx = entity.x; entity.ry = entity.y; continue; }
      if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) { entity.rx = entity.x; entity.ry = entity.y; continue; }
      entity.rx += dx * blend; entity.ry += dy * blend;
      moving = true;
    }
    this.ghosts = this.ghosts.filter(ghost => ghost.until > now);
    return moving || this.ghosts.length > 0;
  }

  // How much smaller or bigger than its drawing an entity shows: babies, bitten food, grazed pasture.
  static sizeOf(entity) {
    const view = entity.view || {};
    let size = view.scale ?? 1;
    if (view.bites != null) size *= 0.45 + 0.55 * Math.max(0, view.bites) / 3;
    if (view.stock != null) size *= 0.6 + 0.4 * view.stock / 100;
    return size;
  }

  // The topmost entity under a world point, for clicks.
  at(x, y) {
    let found = null;
    for (const entity of this.entities.values()) {
      const size = EntityStore.sizeOf(entity);
      const halfW = (entity.w || 40) * size / 2 + 8, halfH = (entity.h || 40) * size / 2 + 8;
      if (Math.abs(x - entity.rx) <= halfW && Math.abs(y - entity.ry) <= halfH && (!found || entity.ry > found.ry)) found = entity;
    }
    return found;
  }

  image(entity) {
    const url = entity.sprite.sheet;
    if (!this.images.has(url)) this.images.set(url, this.loadImage(url));
    return this.images.get(url);
  }

  // Painter's order: lower on the screen is nearer the viewer; the sky floats above all.
  sorted() {
    const layer = entity => (entity.type === 'water' ? 0 : entity.type === 'sky' ? 2 : 1);
    return [...this.entities.values(), ...this.ghosts]
      .sort((a, b) => layer(a) - layer(b) || a.ry - b.ry || (a.id < b.id ? -1 : 1));
  }
}

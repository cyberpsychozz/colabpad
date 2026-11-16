import test from 'node:test';
import assert from 'node:assert/strict';
import { EntityStore } from './entities.js';

const entity = (id, x, y) => ({ id, x, y, sprite: { sheet: `/sprites/${id}/sheet.png`, manifest: {} } });

test('chunk fetches replace their chunk, spawns add, retain drops distant chunks', () => {
  const loaded = [];
  const store = new EntityStore({ size: 1024, loadImage: url => { loaded.push(url); return { url }; } });
  store.setChunk(0, 0, [entity('a', 100, 900), entity('b', 500, 100)]);
  store.put(entity('c', 1500, 300), 1234);
  assert.deepEqual(store.sorted().map(e => e.id), ['b', 'c', 'a']);
  assert.equal(store.entities.get('c').bornAt, 1234);
  // A refetch of chunk (0,0) without "a" removes it, and keeps the neighbour's entity.
  store.setChunk(0, 0, [entity('b', 520, 100)]);
  assert.deepEqual(store.sorted().map(e => e.id), ['b', 'c']);
  assert.equal(store.entities.get('b').x, 520);
  store.retain([[1, 0]]);
  assert.deepEqual(store.sorted().map(e => e.id), ['c']);
  // Sprite sheets are loaded once per URL.
  store.image(store.entities.get('c')); store.image(store.entities.get('c'));
  assert.deepEqual(loaded, ['/sprites/c/sheet.png']);
});

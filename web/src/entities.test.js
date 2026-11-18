import test from 'node:test';
import assert from 'node:assert/strict';
import { EntityStore } from './entities.js';

const refs = { a: 1, b: 2, c: 3, n: 4, zzz: 99 };
const entity = (id, x, y, extra = {}) => ({ id, ref: refs[id], x, y, type: 'creature', w: 40, h: 40, sprite: { sheet: `/sprites/${id}/sheet.png`, manifest: {} }, ...extra });
const make = () => new EntityStore({ size: 1024, loadImage: url => ({ url }) });

test('chunk fetches replace their chunk, spawns add, retain drops distant chunks', () => {
  const loaded = [];
  const store = new EntityStore({ size: 1024, loadImage: url => { loaded.push(url); return { url }; } });
  store.setChunk(0, 0, [entity('a', 100, 900), entity('b', 500, 100)]);
  store.put(entity('c', 1500, 300), 1234);
  assert.deepEqual(store.sorted().map(e => e.id), ['b', 'c', 'a']);
  assert.equal(store.entities.get('c').bornAt, 1234);
  store.setChunk(0, 0, [entity('b', 520, 100)]);
  assert.deepEqual(store.sorted().map(e => e.id), ['b', 'c']);
  assert.equal(store.entities.get('b').x, 520);
  store.retain([[1, 0]]);
  assert.deepEqual(store.sorted().map(e => e.id), ['c']);
  store.image(store.entities.get('c')); store.image(store.entities.get('c'));
  assert.deepEqual(loaded, ['/sprites/c/sheet.png']);
});

test('deltas update state, track direction and animation, and hand entities between chunks', () => {
  const store = make();
  store.setChunk(0, 0, [entity('a', 1000, 100), entity('b', 200, 200)]);
  store.applyDelta({ cx: 0, cy: 0, upd: [[refs.a, 1010, 100, 'wander', 'move', 90], [refs.zzz, 1, 1, 'idle', 'idle', 100]], gone: [] }, 500);
  const a = store.entities.get('a');
  assert.deepEqual([a.x, a.state, a.anim, a.hp, a.dir, a.animStart], [1010, 'wander', 'move', 90, 1, 500]);
  assert.equal(a.rx, 1000);   // the render position follows smoothly
  // It crosses into chunk (1,0): "gone" from (0,0) must not remove it, in either order.
  store.applyDelta({ cx: 1, cy: 0, upd: [[refs.a, 1030, 100, 'wander', 'move', 90, { scale: 0.6 }]], gone: [] }, 600);
  store.applyDelta({ cx: 0, cy: 0, upd: [], gone: [refs.a, refs.b] }, 600);
  assert.ok(store.entities.has('a') && !store.entities.has('b'));
  assert.deepEqual(store.entities.get('a').view, { scale: 0.6 });
  // An entity walking in from an unseen chunk arrives in full.
  store.applyDelta({ cx: 0, cy: 0, enter: [entity('n', 5, 5)], upd: [[refs.n, 6, 5, 'wander', 'move', 100]], gone: [] }, 700);
  assert.equal(store.entities.get('n').x, 6);
  store.applyDelta({ cx: 0, cy: 0, upd: [[refs.a, 900, 100, 'flee', 'move', 90]], gone: [] }, 800);
  assert.equal(store.entities.get('a').dir, -1);
});

test('render positions ease towards the server, jumps snap, and the dead leave a ghost', () => {
  const store = make();
  store.put(entity('a', 100, 100));
  store.applyDelta({ cx: 0, cy: 0, upd: [[refs.a, 110, 100, 'wander', 'move', 100]], gone: [] }, 0);
  assert.equal(store.advance(0.016, 16), true);
  const a = store.entities.get('a');
  assert.ok(a.rx > 100 && a.rx < 110);
  for (let i = 0; i < 100; i++) store.advance(0.016, 16 * i);
  assert.equal(a.rx, 110);
  assert.equal(store.advance(0.016, 2000), false);
  store.applyDelta({ cx: 0, cy: 0, upd: [[refs.a, 700, 100, 'idle', 'idle', 100]], gone: [] }, 2000);
  store.advance(0.016, 2016);
  assert.equal(a.rx, 700);
  // Death with a fossil plays out as a ghost; without one the entity is just gone.
  assert.equal(store.die('a', true, 3000).id, 'a');
  assert.deepEqual(store.sorted().map(e => [e.id, e.anim]), [['a', 'death']]);
  assert.equal(store.advance(0.016, 3100), true);
  assert.equal(store.advance(0.016, 4000), false);
  assert.equal(store.sorted().length, 0);
  store.put(entity('f', 1, 1));
  store.die('f', false, 0);
  assert.equal(store.sorted().length, 0);
  assert.equal(store.die('unknown', true, 0), null);
});

test('hit testing and display size', () => {
  const store = make();
  store.put(entity('low', 100, 120));
  store.put(entity('high', 100, 100));
  store.put(entity('apple', 400, 400, { w: 30, h: 30, view: { bites: 1 } }));
  assert.equal(store.at(100, 110).id, 'low');   // the one drawn in front
  assert.equal(store.at(100, 75).id, 'high');
  assert.equal(store.at(300, 300), null);
  assert.ok(Math.abs(EntityStore.sizeOf(store.entities.get('apple')) - 0.633) < 0.01);
  assert.equal(EntityStore.sizeOf({ view: { scale: 0.6 } }), 0.6);
  assert.equal(EntityStore.sizeOf({ view: { stock: 0 } }), 0.6);
  assert.equal(EntityStore.sizeOf({}), 1);
  // Water is drawn under everything, the sky over everything.
  store.put(entity('lake', 100, 900, { type: 'water' }));
  store.put(entity('cloud', 100, 0, { type: 'sky' }));
  const order = store.sorted().map(e => e.id);
  assert.equal(order[0], 'lake'); assert.equal(order.at(-1), 'cloud');
});

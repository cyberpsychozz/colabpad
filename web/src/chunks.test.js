import test from 'node:test';
import assert from 'node:assert/strict';
import { ChunkStore } from './chunks.js';

function setup() {
  const painted = [];
  const store = new ChunkStore({
    size: 1024,
    createTile: pixels => {
      const tile = { width: pixels, height: pixels, cleared: 0 };
      const ctx = { tile, setTransform() {}, clearRect() { tile.cleared++; } };
      tile.getContext = () => ctx;
      return tile;
    },
    render: (ctx, stroke) => painted.push([ctx.tile, stroke.seq]),
  });
  const drawn = tile => painted.filter(([t]) => t === tile).map(([, seq]) => seq);
  return { store, painted, drawn };
}
const stroke = (seq, cx, cy, points, width = 4) => ({ seq, cx, cy, color: '#000000', width, points });
const page = (strokes, lastSeq, fates = []) => ({ strokes, fates, lastSeq });

test('tiles paint own strokes in seq order and are cached until something changes', () => {
  const { store, drawn } = setup();
  store.retain([[0, 0]]);
  assert.equal(store.tile(0, 0), null);
  store.merge(0, 0, page([stroke(5, 0, 0, [[10, 10, 0.5], [20, 20, 0.5]]), stroke(2, 0, 0, [[50, 50, 0.5], [60, 60, 0.5]])], 7));
  assert.equal(store.watermark(0, 0), 7);
  const tile = store.tile(0, 0);
  assert.deepEqual(drawn(tile), [2, 5]);
  assert.equal(store.tile(0, 0), tile);
  assert.deepEqual(drawn(tile), [2, 5]);
  // A new stroke is painted on top without a full repaint; a repeat is ignored.
  assert.equal(store.addStroke(stroke(8, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]])), true);
  assert.equal(store.addStroke(stroke(8, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]])), false);
  assert.deepEqual(drawn(store.tile(0, 0)), [2, 5, 8]);
  assert.equal(tile.cleared, 1);
  // An out-of-order stroke forces a repaint in the right order.
  store.addStroke(stroke(3, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]]));
  store.tile(0, 0);
  assert.equal(tile.cleared, 2);
  assert.deepEqual(drawn(tile).slice(-4), [2, 3, 5, 8]);
});

test('a stroke near a border is painted into the neighbouring tile too', () => {
  const { store, drawn } = setup();
  store.retain([[0, 0], [1, 0]]);
  store.merge(0, 0, page([stroke(1, 0, 0, [[1020, 500, 0.5], [1024, 500, 0.5]], 20)], 1));
  store.merge(1, 0, page([], 1));
  assert.deepEqual(drawn(store.tile(1, 0)), [1]);
  store.tile(0, 0);
  store.addStroke(stroke(2, 1, 0, [[1026, 100, 0.5], [1100, 100, 0.5]], 30));
  assert.deepEqual(drawn(store.tile(0, 0)), [1, 2]);
  assert.deepEqual(drawn(store.tile(1, 0)), [1, 2]);
  // Far from the border: only its own tile.
  store.addStroke(stroke(3, 1, 0, [[1500, 100, 0.5], [1600, 100, 0.5]]));
  assert.deepEqual(drawn(store.tile(0, 0)), [1, 2]);
});

test('fated strokes are not painted, and scale changes repaint', () => {
  const { store, drawn } = setup();
  store.retain([[0, 0]]);
  store.merge(0, 0, page([stroke(1, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]]), stroke(2, 0, 0, [[5, 5, 0.5], [6, 6, 0.5]])], 2, [{ seq: 1, fate: 'consumed', entityId: 'e' }]));
  assert.deepEqual(drawn(store.tile(0, 0)), [2]);
  store.setFates(0, 0, [{ seq: 2, fate: 'destroyed', entityId: null }]);
  assert.deepEqual(drawn(store.tile(0, 0)), [2]);
  const sharp = store.tile(0, 0, 2);
  assert.equal(sharp.width, 2048);
  assert.deepEqual(drawn(sharp), []);
});

test('retain drops chunks outside the list, live strokes for untracked chunks are ignored', () => {
  const { store } = setup();
  store.retain([[0, 0], [1, 0]]);
  store.merge(0, 0, page([stroke(1, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]])], 1));
  assert.ok(store.hasStrokes());
  assert.equal(store.allLoaded([[0, 0], [1, 0]]), false);
  store.retain([[1, 0], [2, 0]]);
  assert.equal(store.get(0, 0), undefined);
  assert.equal(store.addStroke(stroke(9, 0, 0, [[1, 1, 0.5], [2, 2, 0.5]])), false);
  assert.equal(store.hasStrokes(), false);
  // A live stroke for a subscribed chunk that is still loading is kept and survives the fetch.
  assert.equal(store.addStroke(stroke(7, 2, 0, [[2100, 1, 0.5], [2101, 2, 0.5]])), true);
  store.merge(2, 0, page([stroke(4, 2, 0, [[2100, 1, 0.5], [2101, 2, 0.5]])], 6));
  assert.deepEqual([...store.get(2, 0).strokes.keys()].sort(), [4, 7]);
});

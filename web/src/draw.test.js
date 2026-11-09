import test from 'node:test';
import assert from 'node:assert/strict';
import { pointerPoint, preparePoints, simplify, splitStroke, renderStroke } from './draw.js';

test('pointer coordinates account for CSS scaling, bounds and mouse pressure', () => {
  const rect = { left: 10, top: 20, width: 800, height: 500 };
  assert.deepEqual(pointerPoint({ clientX: 410, clientY: 270, pressure: 1, pointerType: 'mouse' }, rect, 1600, 1000), [800, 500, 0.5]);
  assert.deepEqual(pointerPoint({ clientX: -5, clientY: 900, pressure: 0.7, pointerType: 'pen' }, rect, 1600, 1000), [0, 1000, 0.7]);
});

test('RDP removes a straight run, preserves a corner and handles large input without recursion', () => {
  assert.deepEqual(simplify([[0, 0, 0.5], [1, 0, 0.5], [2, 0, 0.5]], 0.5), [[0, 0, 0.5], [2, 0, 0.5]]);
  const corner = [[0, 0, 0.5], [1, 4, 0.5], [2, 0, 0.5]];
  assert.deepEqual(simplify(corner, 0.5), corner);
  assert.equal(simplify(Array.from({ length: 30000 }, (_, i) => [i, 0, 0.5]), 0.5).length, 2);
});

test('single click becomes two points and rounding never exceeds canvas bounds', () => {
  assert.deepEqual(preparePoints([[2.24, 3.36, 0.5]], 0.5, 1600, 1000), [[2.2, 3.4, 0.5], [2.2, 3.4, 0.5]]);
  assert.equal(preparePoints([[2.24, 3.36, 0.5]], 0.5, 2.25, 3.35)[0][1], 3.35);
});

test('long strokes are split with one-point overlap, bounded points and UTF-8 size', () => {
  const stroke = { canvasId: '0123456789', color: '#123456', width: 40, points: Array.from({ length: 5500 }, (_, i) => [i / 10, i / 10, 0.888]) };
  const chunks = splitStroke(stroke, 2000, 20000);
  for (let i = 0; i < chunks.length; i++) {
    assert.ok(chunks[i].points.length >= 2 && chunks[i].points.length <= 2000);
    assert.ok(Buffer.byteLength(JSON.stringify(chunks[i])) + 256 <= 20000);
    if (i) assert.deepEqual(chunks[i - 1].points.at(-1), chunks[i].points[0]);
  }
  assert.deepEqual(chunks.at(-1).points.at(-1), stroke.points.at(-1));
});

test('click renderer produces a pressure-sized round dot', () => {
  let radius;
  const ctx = { beginPath() {}, arc(x, y, r) { radius = r; }, fill() {} };
  renderStroke(ctx, { color: '#000000', width: 6, points: [[10, 10, 0.5], [10, 10, 0.5]] });
  assert.equal(radius, 3);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { pointerPoint, preparePoints, simplify, splitAtChunks, splitStroke, strokeBounds, renderStroke } from './draw.js';

test('pointer coordinates go through the camera, clamp to the world and keep pen pressure', () => {
  const rect = { left: 10, top: 20, width: 800, height: 500 };
  const camera = { bounds: { w: 4096, h: 2048 }, toWorld: (sx, sy) => [1000 + sx / 2, 500 + sy / 2] };
  assert.deepEqual(pointerPoint({ clientX: 410, clientY: 270, pressure: 1, pointerType: 'mouse' }, rect, camera), [1200, 625, 0.5]);
  assert.deepEqual(pointerPoint({ clientX: -5000, clientY: 9000, pressure: 0.7, pointerType: 'pen' }, rect, camera), [0, 2048, 0.7]);
});

test('strokes are cut at chunk borders into pieces that each fit one chunk', () => {
  const fits = piece => [0, 1].every(axis => {
    const values = piece.map(p => p[axis]), low = Math.min(...values), high = Math.max(...values);
    const index = Math.floor((low + high) / 2 / 1024);
    return low >= index * 1024 && high <= (index + 1) * 1024;
  });
  // Stays inside one chunk: untouched.
  const inside = [[10, 10, 0.5], [500, 900, 0.5]];
  assert.deepEqual(splitAtChunks(inside, 1024), [inside]);
  // One vertical border, pressure interpolated at the cut.
  assert.deepEqual(splitAtChunks([[1000, 100, 0.2], [1048, 100, 0.6]], 1024), [
    [[1000, 100, 0.2], [1024, 100, 0.4]],
    [[1024, 100, 0.4], [1048, 100, 0.6]],
  ]);
  // A diagonal through a corner region crosses an x and a y border in one segment.
  const diagonal = splitAtChunks([[1000, 1000, 0.5], [1100, 1050, 0.5]], 1024);
  assert.equal(diagonal.length, 3);
  // A vertex exactly on a border, then onwards into the next chunk.
  const onBorder = splitAtChunks([[1000, 5, 0.5], [1024, 5, 0.5], [1100, 5, 0.5]], 1024);
  assert.deepEqual(onBorder.map(piece => piece.length), [2, 2]);
  // A long scribble over many chunks: pieces chain end to start and all fit.
  const scribble = Array.from({ length: 400 }, (_, i) => [100 + i * 13.7, 2000 + Math.sin(i / 9) * 1500, 0.5]);
  const pieces = splitAtChunks(scribble, 1024);
  assert.ok(pieces.length > 5 && pieces.every(piece => piece.length >= 2 && fits(piece)));
  for (let i = 1; i < pieces.length; i++) assert.deepEqual(pieces[i - 1].at(-1), pieces[i][0]);
  assert.deepEqual(pieces[0][0], scribble[0]);
  assert.deepEqual(pieces.at(-1).at(-1), scribble.at(-1));
  // A dot stays a dot.
  assert.deepEqual(splitAtChunks([[1024, 1024, 0.5], [1024, 1024, 0.5]], 1024).length, 1);
  assert.deepEqual(strokeBounds({ width: 8, points: [[10, 20, 0.5], [30, 5, 0.5]] }), [3, -2, 37, 27]);
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

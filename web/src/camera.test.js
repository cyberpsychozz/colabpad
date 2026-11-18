import test from 'node:test';
import assert from 'node:assert/strict';
import { Camera, formatHash, parseHash, visibleChunks } from './camera.js';

const world = () => {
  const camera = new Camera({ w: 19456, h: 11264 }, 1024);
  camera.resize(1920, 1080);
  return camera;
};

test('world and screen coordinates round-trip through pan and zoom', () => {
  const camera = world();
  camera.centerOn(5000, 4000, 2);
  assert.deepEqual(camera.center(), [5000, 4000]);
  assert.deepEqual(camera.toScreen(...camera.toWorld(123, 456)).map(Math.round), [123, 456]);
  camera.panBy(-200, 100);
  assert.deepEqual(camera.center(), [5100, 3950]);
  const before = camera.toWorld(300, 200);
  camera.zoomAt(300, 200, 0.5);
  assert.equal(camera.z, 1);
  assert.deepEqual(camera.toWorld(300, 200).map(Math.round), before.map(Math.round));
});

test('the view is clamped to the world and zoom to a range that fits 12 chunks', () => {
  const camera = world();
  camera.panBy(99999, 99999);
  assert.deepEqual([camera.x, camera.y], [0, 0]);
  camera.panBy(-9999999, -9999999);
  assert.deepEqual(camera.toWorld(1920, 1080), [19456, 11264]);
  camera.zoomAt(960, 540, 100);
  assert.equal(camera.z, 4);
  camera.zoomAt(960, 540, 0.0001);
  assert.equal(camera.z, 0.625);
  for (const [w, h] of [[1920, 1080], [390, 844], [2560, 1440], [1024, 1366], [3840, 1100]]) {
    const view = new Camera({ w: 19456, h: 11264 }, 1024);
    view.resize(w, h);
    for (const [x, y] of [[0, 0], [5000.5, 3333.3], [19456, 11264], [1023, 1025]]) {
      view.centerOn(x, y, 0.0001);
      const chunks = visibleChunks(view, 12);
      assert.ok(chunks.length <= 12);
      const [left, top] = view.toWorld(0, 0), [right, bottom] = view.toWorld(w, h);
      const needed = (Math.ceil(right / 1024) - Math.floor(left / 1024)) * (Math.ceil(bottom / 1024) - Math.floor(top / 1024));
      assert.equal(chunks.filter(c => c.visible).length, needed, `${w}x${h} at ${x},${y}`);
    }
  }
  // A world smaller than the viewport is centred.
  const small = new Camera({ w: 1024, h: 1024 }, 1024);
  small.resize(2048, 1024);
  small.centerOn(0, 0, 1);
  assert.deepEqual([small.x, small.y], [-512, 0]);
});

test('visible chunks come first, then the ring, inside the world only', () => {
  const camera = world();
  camera.centerOn(100, 100, 1);
  const corner = visibleChunks(camera, 12);
  assert.deepEqual(corner.filter(c => c.visible).map(c => [c.cx, c.cy]), [[0, 0], [1, 0], [0, 1], [1, 1]]);
  assert.ok(corner.every(c => c.cx >= 0 && c.cy >= 0));
  assert.equal(corner.length, 9);
  camera.centerOn(9728, 5632, 1);
  const middle = visibleChunks(camera, 12);
  assert.equal(middle.length, 12);
  const firstRing = middle.findIndex(c => !c.visible);
  assert.ok(firstRing > 0 && middle.slice(firstRing).every(c => !c.visible));
});

test('deep links parse and format', () => {
  assert.deepEqual(parseHash('#x=1200&y=-5&z=1.5'), { x: 1200, y: -5, z: 1.5 });
  assert.deepEqual(parseHash('#x=1&y=2'), { x: 1, y: 2, z: null });
  assert.equal(parseHash('#x=abc&y=2'), null);
  assert.equal(parseHash(''), null);
  const camera = world();
  camera.centerOn(5000.4, 4000.6, 1.5);
  assert.equal(formatHash(camera), '#x=5000&y=4001&z=1.5');
  assert.deepEqual(parseHash(formatHash(camera)), { x: 5000, y: 4001, z: 1.5 });
});

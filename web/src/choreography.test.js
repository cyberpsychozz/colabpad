import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pose, transformPoint, layerCorners, ServerClock, playbackTime, drawLayered } from './choreography.js';
import { EntityStore } from './entities.js';

const { plan, samples, transforms, playback_samples } = JSON.parse(readFileSync(new URL('../../tests/golden/choreography.json', import.meta.url)));

test('Python/JS shared fixture: all easings, parallel tracks, final hold', () => {
  for (const sample of samples) assert.deepEqual(pose(plan, sample.t, sample.target), sample.pose);
});

test('server clock accounts for different client clocks and network round trip', () => {
  const a = new ServerClock(), b = new ServerClock();
  a.sample(10000, 100, 120); b.sample(10000, 7000, 7020);
  const entity = { plan, view: { playback: { elapsed: 500, serverTime: 10000, running: true } } };
  assert.equal(playbackTime(entity, a.now(210)), 600);
  assert.equal(playbackTime(entity, b.now(7110)), 600);
  assert.equal(playbackTime(entity, 999999), 800);
  entity.view.playback.running = false;
  assert.equal(playbackTime(entity, 999999), 500);
  entity.view.playback = { elapsed: 1000, serverTime: 10000, running: false };
  assert.equal(playbackTime(entity, 999999), 1000);
});

test('part scale then rotation about its pivot, then whole-body motion', () => {
  assert.deepEqual(transformPoint(2, 1, [1, 1], { x: 3, y: 4, rotation: 90, scaleX: 2, scaleY: 1 }), [4, 7]);
  const corners = layerCorners({ id: 'arm', offset: [0, 0], size: [10, 20], pivot: [0, 0] }, plan, 1000);
  assert.ok(Math.abs(corners[0][0] - 40) < 1e-9);
  assert.ok(Math.abs(corners[2][0] - 20) < 1e-9);
});

test('hit test follows the transformed layers and atlas is downloaded once', () => {
  let loads = 0;
  const store = new EntityStore({ size: 1024, loadImage: () => { loads++; return {}; } });
  const sprite = { sheet: '/atlas.png', manifest: { format: 'layered', layers: [
    { id: 'body', offset: [-5, -10], size: [10, 20], pivot: [0, 0], src: [0, 0, 10, 20] },
  ] } };
  store.put({ id: 's', mode: 'scripted_visual', x: 100, y: 100, origin: [100, 100], plan, sprite,
    view: { playback: { elapsed: 1000, running: false, serverTime: 0, revision: 5 } } });
  store.advance(.1, 100);
  const e = store.entities.get('s');
  assert.equal(e.rx, 200);
  assert.equal(store.at(208, 100)?.id, 's');
  assert.equal(store.at(200, 118), null);
  store.image(e); store.image(e);
  assert.equal(loads, 1);
});

test('layer renderer uses one atlas and balanced Canvas state', () => {
  const calls = [], ctx = Object.fromEntries(['save', 'restore', 'translate', 'rotate', 'scale', 'drawImage'].map(name => [name, (...args) => calls.push([name, ...args])]));
  const layer = { id: 'arm', src: [0, 0, 10, 20], offset: [3, 4], size: [10, 20], pivot: [5, 6] };
  drawLayered(ctx, 'atlas', { layers: [layer] }, plan, 500);
  assert.equal(calls.filter(c => c[0] === 'save').length, calls.filter(c => c[0] === 'restore').length);
  assert.deepEqual(calls.find(c => c[0] === 'drawImage'), ['drawImage', 'atlas', ...layer.src, ...layer.offset, ...layer.size]);
});

test('shared Python/JS transform-order fixture', () => {
  for (const sample of transforms) {
    const actual = transformPoint(...sample.point, sample.pivot, sample.pose);
    actual.forEach((v, i) => assert.ok(Math.abs(v - sample.expected[i]) < 1e-9));
  }
});

test('stale checkpoints do not rewind a newer snapshot; a restart can lower revision', () => {
  const store = new EntityStore({ size: 1024, loadImage: () => ({}) });
  const e = { id: 's', ref: 1, x: 100, y: 100, mode: 'scripted_visual', plan, origin: [100, 100],
    view: { playback: { elapsed: 500, revision: 10, serverTime: 1000, running: true } } };
  store.put(e);
  store.put({ ...e, view: { playback: { elapsed: 0, revision: 2, serverTime: 500, running: true } } });
  assert.equal(store.entities.get('s').view.playback.elapsed, 500);
  store.applyDelta({ cx: 0, cy: 0, upd: [[1, 100, 100, 'idle', 'idle', 100,
    { playback: { elapsed: 200, revision: 3, serverTime: 1500, running: true } }]], gone: [] }, 0);
  assert.equal(store.entities.get('s').view.playback.elapsed, 200);
});

test('loop checkpoints hold the final pose for the pause, then repeat without accumulating motion', () => {
  const entity = { plan, view: { playback: {
    elapsed: 1200, pauseMs: 500, serverTime: 10000, running: true,
  } } };
  assert.equal(playbackTime(entity, 10000), 1000);
  assert.equal(playbackTime(entity, 10200), 1000);
  assert.equal(playbackTime(entity, 10300), 0);
  entity.view.playback.elapsed = 1650;
  assert.equal(playbackTime(entity, 10000), 150);
  assert.equal(pose(plan, playbackTime(entity, 10000), 'root').x, 15);
  entity.view.playback.running = false;
  assert.equal(playbackTime(entity, 10300), 150);
  entity.view.playback.elapsed = 1200;
  assert.equal(playbackTime(entity, 10300), 1000);
});

test('shared Python/JS cycle boundary and pause fixture', () => {
  for (const sample of playback_samples) {
    const entity = { plan, view: { playback: { ...sample, serverTime: 10000, running: true } } };
    assert.equal(playbackTime(entity, 10000), sample.phase);
  }
});

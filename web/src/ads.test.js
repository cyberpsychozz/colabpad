import test from 'node:test';
import assert from 'node:assert/strict';
import { detectAdblock, isBlocked, slotsFor, LOADER } from './ads.js';

const wait = () => new Promise(resolve => setImmediate(resolve));
const bait = hidden => { const b = { removed: false, hidden: () => hidden, remove() { b.removed = true; } }; return b; };

test('blocked means the loader failed or the bait was hidden', async () => {
  assert.equal(isBlocked({ scriptFailed: false, baitHidden: false }), false);
  assert.equal(isBlocked({ scriptFailed: true, baitHidden: false }), true);
  assert.equal(isBlocked({ scriptFailed: false, baitHidden: true }), true);
  const visible = bait(false);
  let asked = '';
  assert.equal(await detectAdblock({ loadScript: url => { asked = url; return Promise.resolve(); }, makeBait: () => visible, wait }), false);
  assert.equal(asked, LOADER);
  assert.ok(visible.removed);
  assert.equal(await detectAdblock({ loadScript: () => Promise.reject(new Error('blocked')), makeBait: () => bait(false), wait }), true);
  assert.equal(await detectAdblock({ loadScript: () => Promise.resolve(), makeBait: () => bait(true), wait }), true);
  assert.equal(await detectAdblock({ loadScript: () => { throw new Error('sync'); }, makeBait: () => bait(false), wait }), true);
});

test('an empty ad fill is not a block, and a slow loader is not one either', async () => {
  // The loader arrived and the bait is visible; whether any ad was rendered is never asked.
  assert.equal(await detectAdblock({ loadScript: () => Promise.resolve(), makeBait: () => bait(false), wait }), false);
  assert.equal(await detectAdblock({ loadScript: () => new Promise(() => {}), makeBait: () => bait(false), wait }), false);
});

test('desktop gets two side slots, narrow screens one at the bottom', () => {
  assert.deepEqual(slotsFor(1440), ['left', 'right']);
  assert.deepEqual(slotsFor(1100), ['left', 'right']);
  assert.deepEqual(slotsFor(390), ['bottom']);
});

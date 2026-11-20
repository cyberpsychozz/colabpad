import test from 'node:test';
import assert from 'node:assert/strict';
import { detectAdblock, isBlocked, renderParams, slotsFor, LOADER } from './ads.js';

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

test('desktop gets two stacked slots on each side, narrow screens one at the bottom', () => {
  const desktop = [
    { where: 'left', block: 'side', n: 1 }, { where: 'left', block: 'side', n: 2 },
    { where: 'right', block: 'side', n: 3 }, { where: 'right', block: 'side', n: 4 },
  ];
  assert.deepEqual(slotsFor(1440), desktop);
  assert.deepEqual(slotsFor(1100), desktop);
  assert.deepEqual(slotsFor(1099), [{ where: 'bottom', block: 'bottom', n: 1 }]);
  assert.deepEqual(slotsFor(390), [{ where: 'bottom', block: 'bottom', n: 1 }]);
});

test('one block shown four times gets a distinct container and page number each time', () => {
  const calls = slotsFor(1440).map(slot => renderParams('R-A-1-1', slot));
  assert.deepEqual(calls.map(c => c.pageNumber), [1, 2, 3, 4]);
  assert.equal(new Set(calls.map(c => c.renderTo)).size, 4);
  assert.deepEqual(calls[0], { blockId: 'R-A-1-1', renderTo: 'yandex_rtb_R-A-1-1-1', pageNumber: 1 });
});

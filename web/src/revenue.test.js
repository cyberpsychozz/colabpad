import assert from 'node:assert/strict';
import test from 'node:test';
import { reportPayment, returnedInvoice } from './revenue.js';

function memoryStorage() {
  const items = new Map();
  return { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, String(value)) };
}

const paid = { invId: 1800000000, plan: 'plus_year', amount: 2490, currency: 'RUB', status: 'succeeded' };

test('a paid invoice is reported to Umami once, with revenue and currency and no invoice number', () => {
  const storage = memoryStorage();
  const calls = [];
  const track = (event, data) => calls.push([event, data]);
  assert.equal(reportPayment(paid, { storage, track }), true);
  assert.deepEqual(calls, [['plus-paid', { revenue: 2490, currency: 'RUB', plan: 'plus_year' }]]);
  // Reloading the return page, or coming back to it later, reports nothing again.
  assert.equal(reportPayment(paid, { storage, track }), false);
  assert.equal(calls.length, 1);
  // Another invoice is another sale.
  assert.equal(reportPayment({ ...paid, invId: 1800000001, plan: 'plus_month', amount: 299 }, { storage, track }), true);
  assert.deepEqual(calls[1], ['plus-paid', { revenue: 299, currency: 'RUB', plan: 'plus_month' }]);
});

test('an invoice that is not paid is never reported', () => {
  const calls = [];
  for (const status of ['pending', 'canceled']) {
    assert.equal(reportPayment({ ...paid, status }, { storage: memoryStorage(), track: (...args) => calls.push(args) }), false);
  }
  assert.equal(calls.length, 0);
});

test('without storage (private mode) it still reports, and a broken storage never throws', () => {
  const calls = [];
  const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  assert.equal(reportPayment(paid, { storage: broken, track: (...args) => calls.push(args) }), true);
  assert.equal(calls.length, 1);
});

test('the invoice number comes from Robokassa\'s SuccessURL query, and nothing else is taken for one', () => {
  assert.equal(returnedInvoice('?OutSum=299.00&InvId=1800000000&Culture=ru'), 1800000000);
  for (const search of ['', '?InvId=', '?InvId=0', '?InvId=-5', '?InvId=12a', '?InvId=1e5', '?OutSum=299.00']) {
    assert.equal(returnedInvoice(search), null, search);
  }
});

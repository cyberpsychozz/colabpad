import test from 'node:test';
import assert from 'node:assert/strict';
import { setLocale } from '../i18n/i18n.js';
import { errorText } from './errors.js';

test('the out-of-quota hint names the next plan with the right Russian plural', () => {
  setLocale('ru');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'anon', used: 10, limit: 10, more: 10 }),
    '✨ на сегодня закончились. Войди — и получишь ещё 10 оживлений');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'anon', more: 2 }),
    '✨ на сегодня закончились. Войди — и получишь ещё 2 оживления');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'anon', more: 21 }),
    '✨ на сегодня закончились. Войди — и получишь ещё 21 оживление');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'free', plusLimit: 50 }),
    '✨ на сегодня закончились. «Плюс»: 50 оживлений в день');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'plus' }), '✨ на сегодня закончились. Возвращайся завтра');
  setLocale('en');
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'anon', more: 10 }), "You're out of ✨ for today. Sign in to get 10 more");
  assert.equal(errorText('QUOTA_EXCEEDED', { plan: 'free', plusLimit: 50 }), "You're out of ✨ for today. Plus: 50 a day");
});

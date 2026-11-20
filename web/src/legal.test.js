import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogs } from './i18n/i18n.js';
import { DOCS, docKeys, docParams, sellerDetails } from './legal.js';

const PARAMS = ['name', 'inn', 'email', 'phone', 'site', 'priceMonth', 'priceYear'];

test('every document key has text in both languages and uses only known placeholders', () => {
  assert.deepEqual(Object.keys(DOCS).sort(), ['about', 'offer', 'privacy']);
  for (const [name, doc] of Object.entries(DOCS)) {
    for (const key of docKeys(doc)) {
      for (const locale of ['ru', 'en']) {
        assert.equal(typeof catalogs[locale][key], 'string', `${key} (${locale}, ${name})`);
        for (const [, param] of catalogs[locale][key].matchAll(/\{(\w+)\}/g)) assert.ok(PARAMS.includes(param), `${key}: {${param}}`);
      }
    }
  }
  // Robokassa asks for the contacts and the seller's details: both documents show them.
  assert.ok(DOCS.about.sections.some(section => section.requisites));
  assert.ok(DOCS.offer.sections.some(section => section.requisites));
});

test('blank seller details stay empty for the footer and read as "not specified" in documents', () => {
  assert.deepEqual(sellerDetails(undefined), { name: '', inn: '', email: '', phone: '' });
  const legal = { seller: { name: ' Ivanov Ivan ', inn: '123456789012' }, email: '', phone: '+7 900 000-00-00' };
  assert.deepEqual(sellerDetails(legal), { name: 'Ivanov Ivan', inn: '123456789012', email: '', phone: '+7 900 000-00-00' });
  assert.deepEqual(docParams(legal, '-', 'collabpad.ru'), {
    name: 'Ivanov Ivan', inn: '123456789012', email: '-', phone: '+7 900 000-00-00', site: 'collabpad.ru',
  });
});

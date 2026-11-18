import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { catalogs, detectLocale, setLocale, t } from './i18n.js';

const src = join(dirname(fileURLToPath(import.meta.url)), '..');
const sources = (dir = src) => readdirSync(dir).flatMap(name => {
  const path = join(dir, name);
  if (statSync(path).isDirectory()) return sources(path);
  return /\.js$/.test(name) && !/\.test\.js$/.test(name) ? [path] : [];
});

test('both catalogs have identical keys, placeholders and non-empty text', () => {
  assert.deepEqual(Object.keys(catalogs.ru).sort(), Object.keys(catalogs.en).sort());
  const placeholders = value => [...JSON.stringify(value).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();
  for (const key of Object.keys(catalogs.ru)) {
    assert.deepEqual([...new Set(placeholders(catalogs.ru[key]))], [...new Set(placeholders(catalogs.en[key]))], key);
    for (const catalog of [catalogs.ru, catalogs.en]) {
      const forms = typeof catalog[key] === 'object' ? Object.values(catalog[key]) : [catalog[key]];
      assert.ok(forms.every(form => typeof form === 'string' && form.trim()), key);
    }
  }
  for (const form of ['one', 'few', 'many', 'other']) assert.ok(catalogs.ru['alive.count'][form]);
});

test('every key used in code exists, and no Cyrillic literal lives outside the catalogs', () => {
  for (const path of sources()) {
    const code = readFileSync(path, 'utf8');
    assert.ok(!/[А-Яа-яЁё]/.test(code), `Cyrillic literal in ${path}`);
    for (const [, key] of code.matchAll(/\bt\(\s*'([^']+)'/g)) assert.ok(key in catalogs.en, `${key} (${path})`);
    for (const [, key] of code.matchAll(/data-i18n(?:-label|-title)?="([^"]+)"/g)) assert.ok(key in catalogs.en, `${key} (${path})`);
  }
  const html = readFileSync(join(src, '..', 'index.html'), 'utf8');
  assert.ok(!/[А-Яа-яЁё]/.test(html), 'Cyrillic literal in index.html');
});

test('every server error code and every object type has text in both languages', () => {
  const errors = readFileSync(join(src, '..', '..', 'app', 'errors.py'), 'utf8');
  const codes = [...errors.slice(errors.indexOf('STATUS = {'), errors.indexOf('}', errors.indexOf('STATUS = {'))).matchAll(/"([A-Z_]+)":/g)].map(m => m[1]);
  assert.ok(codes.length > 20);
  for (const code of codes) for (const locale of ['ru', 'en']) assert.ok(catalogs[locale][`err.${code}`], `err.${code} (${locale})`);
  const archetypes = readFileSync(join(src, '..', '..', 'config', 'archetypes.yaml'), 'utf8');
  const types = [...archetypes.matchAll(/^([a-z]+):$/gm)].map(m => m[1]);
  assert.equal(types.length, 14);
  for (const type of types) for (const locale of ['ru', 'en']) assert.ok(catalogs[locale][`type.${type}`], `type.${type} (${locale})`);
});

test('locale detection, placeholders and Russian plurals', () => {
  assert.equal(detectLocale(null, 'ru-RU'), 'ru');
  assert.equal(detectLocale(null, 'de-DE'), 'en');
  assert.equal(detectLocale('en', 'ru-RU'), 'en');
  assert.equal(detectLocale('xx', undefined), 'en');
  setLocale('ru');
  assert.equal(t('alive.button'), 'ОЖИВИ');
  assert.equal(t('alive.left', { n: 3, limit: 10 }), 'Осталось ✨ сегодня: 3 из 10');
  assert.deepEqual([1, 2, 5, 11, 21, 22, 25].map(n => t('alive.count', { n })), [
    '1 оживление', '2 оживления', '5 оживлений', '11 оживлений', '21 оживление', '22 оживления', '25 оживлений',
  ]);
  setLocale('en');
  assert.equal(t('alive.button'), 'ALIVE');
  assert.deepEqual([1, 2].map(n => t('alive.count', { n })), ['1 revival', '2 revivals']);
  assert.equal(t('missing.key'), 'missing.key');
});

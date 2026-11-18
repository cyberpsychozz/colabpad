// Client i18n: two catalogs with identical keys, whole sentences with {placeholders}.
// A catalog value is a string, or an object of plural forms picked by params.n.
import ru from './ru.json' with { type: 'json' };
import en from './en.json' with { type: 'json' };

export const catalogs = { ru, en };
export const LOCALES = ['ru', 'en'];

let locale = 'en';
const listeners = new Set();
const plurals = {};

export function detectLocale(saved, browserLanguage) {
  if (LOCALES.includes(saved)) return saved;
  return String(browserLanguage || '').toLowerCase().startsWith('ru') ? 'ru' : 'en';
}

export const getLocale = () => locale;

export function setLocale(next) {
  if (!LOCALES.includes(next) || next === locale) return;
  locale = next;
  for (const listener of listeners) listener(locale);
}

export function onLocaleChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function t(key, params = {}) {
  let text = catalogs[locale][key] ?? catalogs.en[key];
  if (text === undefined) return key;
  if (typeof text === 'object') {
    plurals[locale] ||= new Intl.PluralRules(locale);
    text = text[plurals[locale].select(Number(params.n))] ?? text.other;
  }
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
}

export const has = key => key in catalogs.en;

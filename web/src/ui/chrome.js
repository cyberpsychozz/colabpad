// Page chrome shared by every screen: brand and the RU / EN switch.
import { LOCALES, getLocale, setLocale } from '../i18n/i18n.js';
import { element } from './dom.js';

export function brand() {
  return element('a', { class: 'brand', href: '/', 'data-i18n-label': 'brand.home' },
    element('span', { class: 'brand-symbol', 'aria-hidden': 'true' }, '✳'), 'collab', element('span', {}, 'pad'));
}

export function languageSwitch() {
  const group = element('div', { class: 'lang-switch', role: 'group', 'data-i18n-label': 'lang.switch' });
  for (const code of LOCALES) {
    group.append(element('button', {
      class: 'lang-button', type: 'button', lang: code,
      'aria-pressed': String(code === getLocale()),
      onclick: () => setLocale(code),
    }, code.toUpperCase()));
  }
  return group;
}

export function refreshLanguageSwitch(root = document) {
  for (const button of root.querySelectorAll('.lang-button')) {
    button.setAttribute('aria-pressed', String(button.lang === getLocale()));
  }
}

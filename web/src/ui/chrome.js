// Page chrome shared by every screen: brand, the RU / EN switch and the footer.
import { LOCALES, getLocale, setLocale } from '../i18n/i18n.js';
import { sellerDetails } from '../legal.js';
import { currentMe } from '../me.js';
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

// Links to the site's documents and the seller's details, as Robokassa asks for in the footer
// (spec §9b). Details not filled in yet (config/legal.yaml) are left out.
export const FOOTER_LINKS = [['/about', 'footer.about'], ['/plans', 'footer.plans'], ['/offer', 'footer.offer'], ['/privacy', 'footer.privacy']];

export function siteFooter() {
  const details = sellerDetails(currentMe().legal);
  const seller = details.name && details.inn
    ? element('p', { class: 'footer-seller' },
      element('span', { 'data-i18n': 'legal.req.statusShort' }), ` ${details.name} · `,
      element('span', { 'data-i18n': 'legal.req.inn' }), ` ${details.inn}`)
    : '';
  const contacts = [
    details.email && element('a', { href: `mailto:${details.email}` }, details.email),
    details.phone && element('a', { href: `tel:${details.phone.replace(/[^\d+]/g, '')}` }, details.phone),
  ].filter(Boolean);
  return element('footer', { class: 'site-footer' },
    element('nav', { 'data-i18n-label': 'footer.nav' }, ...FOOTER_LINKS.map(([href, key]) => element('a', { href, 'data-i18n': key }))),
    seller,
    contacts.length ? element('p', { class: 'footer-contacts' }, ...contacts) : '');
}

// /about, /offer and /privacy (spec §9b), redrawn in the other language on switch.
import { onLocaleChange, t } from '../i18n/i18n.js';
import { DOCS, docParams, sellerDetails } from '../legal.js';
import { currentMe } from '../me.js';
import { accountButton } from './account.js';
import { brand, languageSwitch, siteFooter } from './chrome.js';
import { element, localize } from './dom.js';
import { PRICES, rubles } from './plans.js';

export function openLegal(root, name) {
  let stop = () => {};
  const draw = () => { stop(); stop = onLocaleChange(draw); drawLegal(root, name); };
  draw();
  return () => stop();
}

// The seller's details as a definition list; mail and phone are links when filled in.
export function requisites() {
  const details = sellerDetails(currentMe().legal);
  const value = (text, href) => (text ? (href ? element('a', { href }, text) : text) : t('legal.unset'));
  const rows = [
    ['legal.req.seller', value(details.name)],
    ['legal.req.status', t('legal.req.statusValue')],
    ['legal.req.inn', value(details.inn)],
    ['legal.req.email', value(details.email, `mailto:${details.email}`)],
    ['legal.req.phone', value(details.phone, `tel:${details.phone.replace(/[^\d+]/g, '')}`)],
    ['legal.req.site', location.host],
  ];
  return element('dl', { class: 'requisites' }, ...rows.flatMap(([key, text]) => [element('dt', {}, t(key)), element('dd', {}, text)]));
}

function drawLegal(root, name) {
  const doc = DOCS[name];
  const params = { ...docParams(currentMe().legal, t('legal.unset'), location.host), priceMonth: rubles(PRICES.plus_month), priceYear: rubles(PRICES.plus_year) };
  const text = key => t(key, params);
  const sections = doc.sections.map(section => {
    const items = section.list
      ? [element('ul', {}, ...section.items.map(key => element('li', {}, text(key))))]
      : section.items.map(key => element('p', {}, text(key)));
    if (section.requisites) items.push(requisites());
    return element('section', {}, element('h2', {}, text(section.title)), ...items);
  });
  const page = element('div', { class: 'home legal' },
    element('header', { class: 'site-header' }, brand(), element('span', { class: 'spacer' }), accountButton(), languageSwitch()),
    element('main', {},
      element('article', { class: 'legal-doc' },
        element('h1', {}, text(doc.title)),
        element('p', { class: 'legal-lead' }, text(doc.lead)),
        ...sections,
        name === 'about' ? '' : element('p', { class: 'small-note', 'data-i18n': 'legal.translation' }))),
    siteFooter());
  root.replaceChildren(page);
  localize(page);
}

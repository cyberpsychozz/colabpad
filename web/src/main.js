import './styles.css';
import { detectLocale, getLocale, onLocaleChange, setLocale, t } from './i18n/i18n.js';
import { brand, languageSwitch, refreshLanguageSwitch, siteFooter } from './ui/chrome.js';
import { $, element, localize, storage } from './ui/dom.js';
import { putLocale } from './api.js';
import { loadMe } from './me.js';
import { accountButton } from './ui/account.js';
import { passGate } from './ui/gate.js';
import { DOCS } from './legal.js';
import { openLegal } from './ui/legal.js';
import { openBillingReturn, openPlans } from './ui/plans.js';
import { openWorld } from './ui/world.js';

const WORLD_ID = 'world';
const LEGAL_PAGES = new Set(['/about', '/offer', '/privacy']);
const root = $('#app');
let cleanup = () => {};
// The document title follows the screen and the language.
let titleKey = 'app.title';

function applyLocale() {
  document.documentElement.lang = getLocale();
  document.title = t(titleKey);
  $('meta[name="description"]')?.setAttribute('content', t('app.description'));
  localize(document);
  refreshLanguageSwitch();
}

function home() {
  root.replaceChildren(element('div', { class: 'home' },
    element('header', { class: 'site-header' }, brand(), element('span', { class: 'spacer' }), accountButton(), languageSwitch()),
    element('main', {}, element('div', { class: 'hero' }, element('div', { class: 'hero-copy' },
      element('span', { class: 'eyebrow' }, element('span', { class: 'status-dot green' }), ' ', element('span', { 'data-i18n': 'landing.eyebrow' })),
      element('h1', { 'data-i18n': 'landing.title' }),
      element('p', { class: 'hero-description', 'data-i18n': 'landing.lead' }),
      element('a', { class: 'button primary large', href: '/w' }, element('span', { 'data-i18n': 'landing.enter' }), element('span', { 'aria-hidden': 'true' }, '↗')),
      element('p', { class: 'small-note', 'data-i18n': 'landing.note' })))),
    siteFooter()));
  localize(root);
}

async function route() {
  cleanup(); cleanup = () => {};
  const me = await loadMe();
  if (me.locale && me.locale !== getLocale()) putLocale(getLocale()).catch(() => {});
  const path = location.pathname;
  const slug = path.match(/^\/p\/([A-Za-z0-9_-]{10})$/)?.[1];
  if (path === '/w' || slug) {
    // Free viewers see ads; with an ad blocker on, the world is neither mounted nor connected.
    if (me.ads) await passGate(root);
    cleanup = await openWorld(root, slug || WORLD_ID);
  } else if (path === '/plans') cleanup = openPlans(root);
  else if (path === '/billing/return') cleanup = openBillingReturn(root);
  else if (LEGAL_PAGES.has(path)) { titleKey = DOCS[path.slice(1)].title; cleanup = openLegal(root, path.slice(1)); }
  else home();
}

setLocale(detectLocale(storage.get('collabpad-locale'), navigator.language));
onLocaleChange(locale => { storage.set('collabpad-locale', locale); putLocale(locale).catch(() => {}); applyLocale(); });
applyLocale();
// Full navigation between screens prevents abandoning an unsaved in-memory outbox.
window.addEventListener('popstate', route);
route().then(applyLocale);

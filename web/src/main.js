import './styles.css';
import { detectLocale, getLocale, onLocaleChange, setLocale, t } from './i18n/i18n.js';
import { brand, languageSwitch, refreshLanguageSwitch } from './ui/chrome.js';
import { $, element, localize, storage } from './ui/dom.js';
import { openWorld } from './ui/world.js';

const WORLD_ID = 'world';
const root = $('#app');
let cleanup = () => {};

function applyLocale() {
  document.documentElement.lang = getLocale();
  document.title = t('app.title');
  $('meta[name="description"]')?.setAttribute('content', t('app.description'));
  localize(document);
  refreshLanguageSwitch();
}

function home() {
  root.replaceChildren(element('div', { class: 'home' },
    element('header', { class: 'site-header' }, brand(), languageSwitch()),
    element('main', {}, element('div', { class: 'hero' }, element('div', { class: 'hero-copy' },
      element('span', { class: 'eyebrow' }, element('span', { class: 'status-dot green' }), ' ', element('span', { 'data-i18n': 'landing.eyebrow' })),
      element('h1', { 'data-i18n': 'landing.title' }),
      element('p', { class: 'hero-description', 'data-i18n': 'landing.lead' }),
      element('a', { class: 'button primary large', href: '/w' }, element('span', { 'data-i18n': 'landing.enter' }), element('span', { 'aria-hidden': 'true' }, '↗')),
      element('p', { class: 'small-note', 'data-i18n': 'landing.note' }))))));
  localize(root);
}

async function route() {
  cleanup(); cleanup = () => {};
  if (location.pathname === '/w') cleanup = await openWorld(root, WORLD_ID);
  else home();
}

setLocale(detectLocale(storage.get('collabpad-locale'), navigator.language));
onLocaleChange(locale => { storage.set('collabpad-locale', locale); applyLocale(); });
applyLocale();
// Full navigation between screens prevents abandoning an unsaved in-memory outbox.
window.addEventListener('popstate', route);
route().then(applyLocale);

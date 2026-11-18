// /plans and /billing/return. Prices are always in rubles, whatever the language.
import { postCancel, postCheckout, countMetric } from '../api.js';
import { getLocale, onLocaleChange, t } from '../i18n/i18n.js';
import { currentMe, loadMe } from '../me.js';
import { accountButton, formatDate, openAccount } from './account.js';
import { brand, languageSwitch } from './chrome.js';
import { element, localize } from './dom.js';
import { errorText } from './errors.js';

const PRICES = { plus_month: 299, plus_year: 2490 };
const rubles = amount => new Intl.NumberFormat(getLocale(), { style: 'currency', currency: 'RUB', currencyDisplay: 'narrowSymbol', maximumFractionDigits: 0 }).format(amount);
const header = () => element('header', { class: 'site-header' }, brand(), element('span', { class: 'spacer' }), accountButton(), languageSwitch());

export function openPlans(root) {
  let stop = () => {};
  const draw = () => { stop(); stop = onLocaleChange(draw); drawPlans(root, draw); };
  draw();
  return () => stop();
}

function drawPlans(root, redraw) {
  const me = currentMe();
  const plus = me.plan === 'plus';
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const buy = plan => element('button', { class: 'button primary', type: 'button', onclick: async event => {
    if (!currentMe().user) { openAccount(); return; }
    const button = event.currentTarget;
    button.disabled = true;
    problem.textContent = t('plans.redirecting');
    countMetric('plus_clicked').catch(() => {});
    try { location.href = (await postCheckout(plan)).confirmationUrl; }
    catch (error) { problem.textContent = errorText(error.code); button.disabled = false; }
  } }, t(plan === 'plus_month' ? 'plans.buyMonth' : 'plans.buyYear', { price: rubles(PRICES[plan]) }));
  const list = keys => element('ul', {}, ...keys.map(key => element('li', { 'data-i18n': key })));
  const status = plus ? element('p', { class: 'plan-status' },
    t(me.subscription.status === 'canceled' ? 'account.plusUntil' : 'account.plusRenews', { date: formatDate(me.subscription.periodEnd) })) : '';
  const cancel = plus && me.subscription.status === 'active' ? element('button', { class: 'text-link', type: 'button', 'data-i18n': 'plans.cancel', onclick: async () => {
    try { await postCancel(); await loadMe(); redraw(); } catch (error) { problem.textContent = errorText(error.code); }
  } }) : '';
  const page = element('div', { class: 'home plans' }, header(),
    element('main', {},
      element('h1', { 'data-i18n': 'plans.title' }),
      element('div', { class: 'plan-grid' },
        element('section', { class: 'plan-card' },
          element('h2', { 'data-i18n': 'plans.free' }), element('p', { class: 'plan-price' }, rubles(0)),
          list(['plans.free.world', 'plans.free.alive', 'plans.free.ads'])),
        element('section', { class: 'plan-card featured' },
          element('h2', { 'data-i18n': 'plan.plus' }), element('p', { class: 'plan-price' }, t('plans.perMonth', { price: rubles(PRICES.plus_month) })),
          list(['plans.plus.noAds', 'plans.plus.worlds', 'plans.plus.alive', 'plans.plus.guests']),
          status, element('div', { class: 'plan-actions' }, buy('plus_month'), buy('plus_year')), cancel, problem)),
      element('p', { class: 'small-note', 'data-i18n': 'plans.note' })));
  root.replaceChildren(page);
  localize(page);
}

// After paying, the provider sends the user back here; the webhook may land a moment later.
export function openBillingReturn(root) {
  let alive = true;
  const text = element('h1', { 'data-i18n': 'plans.checking' });
  const page = element('div', { class: 'home plans' }, header(),
    element('main', {}, text, element('p', {}, element('a', { class: 'button primary', href: '/w', 'data-i18n': 'landing.enter' }))));
  root.replaceChildren(page);
  localize(page);
  (async () => {
    for (let attempt = 0; attempt < 15 && alive; attempt++) {
      const me = await loadMe();
      if (me.plan === 'plus') {
        delete text.dataset.i18n;
        text.textContent = t('plan.active', { date: formatDate(me.subscription.periodEnd) });
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    if (alive) { text.dataset.i18n = 'err.PAYMENT_FAILED'; localize(page); }
  })();
  return () => { alive = false; };
}

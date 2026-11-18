// The account button and its dialog: sign in by e-mail link, plan, my worlds, sign out.
import { ApiError, getWorlds, postLogout, postMagicLink, postWorld } from '../api.js';
import { getLocale, t } from '../i18n/i18n.js';
import { currentMe } from '../me.js';
import { element, localize, modal } from './dom.js';
import { errorText } from './errors.js';

export const formatDate = ms => new Intl.DateTimeFormat(getLocale(), { dateStyle: 'long' }).format(new Date(ms));

export function accountButton() {
  const me = currentMe();
  const button = element('button', { class: 'button quiet account-button', type: 'button', onclick: openAccount });
  if (me.user) button.textContent = me.user.email;
  else button.dataset.i18n = 'account.signIn';
  return button;
}

export function openAccount() {
  const me = currentMe();
  const body = element('div', { class: 'account' });
  const dialog = modal(t('account.title'), body);
  if (me.user) signedIn(body, me); else signIn(body);
  localize(dialog);
}

function signIn(body) {
  const email = element('input', { type: 'email', name: 'email', required: true, autocomplete: 'email', maxlength: 254, id: 'account-email' });
  const consent = element('input', { type: 'checkbox', required: true, id: 'account-consent' });
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const send = element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'account.send' });
  const form = element('form', { onsubmit: async event => {
    event.preventDefault();
    send.disabled = true;
    problem.textContent = '';
    try {
      await postMagicLink(email.value.trim(), location.pathname);
      body.replaceChildren(element('h2', { 'data-i18n': 'account.sentTitle' }), element('p', {}, t('account.sent', { email: email.value.trim() })));
      localize(body);
    } catch (error) {
      problem.textContent = error instanceof ApiError && error.code === 'VALIDATION_FAILED' ? t('account.badEmail') : errorText(error.code);
      send.disabled = false;
    }
  } },
  element('h2', { 'data-i18n': 'account.title' }),
  element('p', { 'data-i18n': 'account.lead' }),
  element('label', { for: 'account-email', 'data-i18n': 'account.email' }), email,
  element('label', { class: 'check', for: 'account-consent' }, consent, element('span', { 'data-i18n': 'account.consent' })),
  element('p', { class: 'small-note', 'data-i18n': 'account.privacy' }),
  problem,
  element('div', { class: 'dialog-actions' }, send));
  body.replaceChildren(form);
}

function signedIn(body, me) {
  const plus = me.plan === 'plus';
  const planText = plus
    ? t(me.subscription?.status === 'canceled' ? 'account.plusUntil' : 'account.plusRenews', { date: formatDate(me.subscription.periodEnd) })
    : t('account.free');
  const worlds = element('div', { class: 'world-list' }, element('span', { class: 'spinner small' }));
  body.replaceChildren(
    element('h2', {}, me.user.email),
    element('p', {}, planText, ' ', element('a', { class: 'text-link', href: '/plans', 'data-i18n': plus ? 'account.manage' : 'gate.plus' })),
    element('h3', { 'data-i18n': 'account.worlds' }), worlds,
    element('div', { class: 'dialog-actions' },
      element('button', { class: 'button', type: 'button', 'data-i18n': 'account.signOut', onclick: async () => {
        await postLogout().catch(() => {});
        location.reload();
      } })));
  fillWorlds(worlds, plus);
}

async function fillWorlds(box, plus) {
  let list;
  try { list = await getWorlds(); } catch (error) { box.replaceChildren(element('p', {}, errorText(error.code))); return; }
  const link = world => element('a', { class: 'world-link', href: `/p/${world.id}` },
    element('span', {}, world.title || world.id),
    element('small', {}, world.frozen ? t('account.frozen') : t(`roles.${world.role}`)));
  const rows = [...list.owned, ...list.shared].map(link);
  const title = element('input', { type: 'text', maxlength: 60, required: true, 'data-i18n-label': 'account.newWorld', placeholder: t('account.newWorld') });
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const create = element('form', { class: 'inline-form', onsubmit: async event => {
    event.preventDefault();
    try { location.href = `/p/${(await postWorld(title.value.trim())).id}`; }
    catch (error) { problem.textContent = errorText(error.code, error.details); }
  } }, title, element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'account.create' }));
  box.replaceChildren(...rows,
    !rows.length ? element('p', { class: 'small-note', 'data-i18n': plus ? 'account.noWorlds' : 'account.worldsNeedPlus' }) : '',
    ...(plus && list.owned.length < list.max ? [create, problem] : []));
  localize(box);
}

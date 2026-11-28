// The account button and its dialog: sign in with an e-mailed code, plan, my worlds, sign out.
import { ApiError, deleteWorld, getWorlds, leaveWorld, patchWorld, postLoginCode, postLogout, postVerifyCode, postWorld } from '../api.js';
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

const CODE_DIGITS = 6;

function signIn(body, address = '') {
  const email = element('input', { type: 'email', name: 'email', required: true, autocomplete: 'email', maxlength: 254, id: 'account-email', value: address });
  const consent = element('input', { type: 'checkbox', required: true, id: 'account-consent' });
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const send = element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'account.send' });
  const form = element('form', { onsubmit: async event => {
    event.preventDefault();
    send.disabled = true;
    problem.textContent = '';
    const address = email.value.trim();
    try {
      await postLoginCode(address);
      enterCode(body, address);
    } catch (error) {
      problem.textContent = error instanceof ApiError && error.code === 'VALIDATION_FAILED' ? t('account.badEmail') : errorText(error.code, error.details);
      send.disabled = false;
    }
  } },
  element('h2', { 'data-i18n': 'account.title' }),
  element('p', { 'data-i18n': 'account.lead' }),
  element('label', { for: 'account-email', 'data-i18n': 'account.email' }), email,
  element('label', { class: 'check', for: 'account-consent' }, consent, element('span', { 'data-i18n': 'account.consent' })),
  element('p', { class: 'small-note', 'data-i18n': 'account.privacy' }),
  element('p', { class: 'small-note' }, element('a', { class: 'text-link', href: '/privacy', target: '_blank', 'data-i18n': 'footer.privacy' })),
  problem,
  element('div', { class: 'dialog-actions' }, send));
  body.replaceChildren(form);
  localize(body);
}

// Step two: the six digits from the letter. The code only works in this browser.
function enterCode(body, address) {
  const code = element('input', {
    type: 'text', name: 'code', id: 'account-code', required: true, inputmode: 'numeric',
    autocomplete: 'one-time-code', maxlength: 12, class: 'code-input', spellcheck: 'false',
  });
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const note = element('p', { class: 'small-note', 'data-i18n': 'account.noMail' });
  const verify = element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'account.verify' });
  const digits = () => code.value.replace(/\D/g, '');
  let busy = false;
  const submit = async () => {
    if (busy) return;
    problem.textContent = '';
    if (digits().length !== CODE_DIGITS) { problem.textContent = t('account.badCode'); return; }
    busy = verify.disabled = true;
    try {
      await postVerifyCode(digits());
      location.reload();
    } catch (error) {
      problem.textContent = errorText(error.code, error.details);
      code.select();
      busy = verify.disabled = false;
    }
  };
  // Pasting or autofilling all six digits signs in without another click.
  code.addEventListener('input', () => { if (digits().length === CODE_DIGITS) submit(); });
  const resend = element('button', { class: 'button quiet', type: 'button', 'data-i18n': 'account.resend', onclick: async () => {
    resend.disabled = true;
    problem.textContent = '';
    try {
      await postLoginCode(address);
      note.textContent = t('account.resent');
      code.value = '';
      code.focus();
    } catch (error) {
      problem.textContent = errorText(error.code, error.details);
    }
    resend.disabled = false;
  } });
  const other = element('button', { class: 'button quiet', type: 'button', 'data-i18n': 'account.otherEmail', onclick: () => signIn(body, address) });
  const form = element('form', { onsubmit: event => { event.preventDefault(); submit(); } },
    element('h2', { 'data-i18n': 'account.codeTitle' }),
    element('p', {}, t('account.codeSent', { email: address })),
    element('label', { for: 'account-code', 'data-i18n': 'account.code' }), code,
    problem,
    note,
    element('div', { class: 'dialog-actions' }, other, resend, verify));
  body.replaceChildren(form);
  localize(body);
  code.focus();
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
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const run = async fn => {
    problem.textContent = '';
    try { await fn(); await fillWorlds(box, plus); }
    catch (error) { problem.textContent = errorText(error.code, error.details); }
  };
  const row = (world, owned) => {
    const link = element('a', { class: 'world-link', href: `/p/${world.id}` },
      element('span', {}, world.title || world.id),
      element('small', {}, world.frozen ? t('account.frozen') : t(`roles.${world.role}`)));
    const controls = owned
      ? [
          element('button', { class: 'icon-button small', type: 'button', 'aria-label': t('account.rename'), onclick: () => {
            const input = element('input', { type: 'text', maxlength: 60, required: true, value: world.title || '' });
            const form = element('form', { class: 'inline-form', onsubmit: event => {
              event.preventDefault();
              run(() => patchWorld(world.id, input.value.trim()));
            } }, input, element('button', { class: 'button primary', type: 'submit' }, '✓'),
            element('button', { class: 'button', type: 'button', onclick: () => fillWorlds(box, plus) }, '×'));
            link.parentElement.replaceWith(form);
            input.focus();
          } }, '✎'),
          element('button', { class: 'icon-button small', type: 'button', 'aria-label': t('account.delete'), onclick: () => run(() => deleteWorld(world.id)) }, '×'),
        ]
      : [
          element('button', { class: 'icon-button small', type: 'button', 'aria-label': t('account.leave'), onclick: () => run(async () => {
            await leaveWorld(world.id);
            if (location.pathname === `/p/${world.id}`) location.replace('/w');
          }) }, '×'),
        ];
    return element('div', { class: 'world-row' }, link, ...controls);
  };
  const rows = [
    ...list.owned.map(w => row(w, true)),
    ...list.shared.map(w => row(w, false)),
  ];
  const title = element('input', { type: 'text', maxlength: 60, required: true, 'data-i18n-label': 'account.newWorld', placeholder: t('account.newWorld') });
  const create = element('form', { class: 'inline-form', onsubmit: async event => {
    event.preventDefault();
    try { location.href = `/p/${(await postWorld(title.value.trim())).id}`; }
    catch (error) { problem.textContent = errorText(error.code, error.details); }
  } }, title, element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'account.create' }));
  const sharedWorldLink = location.pathname !== '/w'
    ? [element('a', { class: 'world-link', href: '/w' }, element('span', { 'data-i18n': 'account.sharedWorld' }))]
    : [];
  box.replaceChildren(...sharedWorldLink, ...rows,
    !rows.length ? element('p', { class: 'small-note', 'data-i18n': plus ? 'account.noWorlds' : 'account.worldsNeedPlus' }) : '',
    ...(plus && list.owned.length < list.max ? [create] : []),
    problem);
  localize(box);
}

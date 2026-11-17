// The share dialog of a private world: the link role, members and invitations.
import { deleteMember, getAccess, patchMember, postMember, putAccess } from '../api.js';
import { t } from '../i18n/i18n.js';
import { element, localize, modal, toast } from './dom.js';
import { errorText } from './errors.js';

const MEMBER_ROLES = ['viewer', 'player', 'editor'];

function roleSelect(roles, value, label, onchange) {
  const select = element('select', { 'aria-label': label, onchange: () => onchange(select.value) },
    ...roles.map(role => element('option', { value: role, selected: role === value }, t(role === 'none' ? 'share.linkOff' : `roles.${role}`))));
  return select;
}

export function shareButton(canvasId) {
  return element('button', { class: 'button quiet', type: 'button', 'data-i18n': 'share.open', onclick: () => openShare(canvasId) });
}

export async function openShare(canvasId) {
  const body = element('div', { class: 'share' }, element('span', { class: 'spinner small' }));
  const dialog = modal(t('share.title'), body);
  const problem = element('p', { class: 'form-error', role: 'alert' });
  const attempt = async action => {
    problem.textContent = '';
    try { draw(await action()); } catch (error) { problem.textContent = error.code === 'VALIDATION_FAILED' ? t('account.badEmail') : errorText(error.code, error.details); }
  };
  const url = `${location.origin}/p/${canvasId}`;

  function draw(access) {
    const email = element('input', { type: 'email', required: true, maxlength: 254, placeholder: t('share.email'), 'aria-label': t('share.email') });
    let inviteRole = 'player';
    const members = access.members.map(member => element('li', {},
      element('span', {}, member.email),
      roleSelect(MEMBER_ROLES, member.role, t('share.role'), role => attempt(() => patchMember(canvasId, member.userId, role))),
      element('button', { class: 'icon-button small', type: 'button', 'aria-label': t('share.remove'), onclick: () => attempt(async () => {
        await deleteMember(canvasId, member.userId);
        return getAccess(canvasId);
      }) }, '×')));
    body.replaceChildren(
      element('h2', { 'data-i18n': 'share.title' }),
      element('label', { 'data-i18n': 'share.link' }),
      element('div', { class: 'inline-form' },
        element('input', { type: 'text', readonly: true, value: url, 'aria-label': t('share.link') }),
        element('button', { class: 'button', type: 'button', 'data-i18n': 'share.copy', onclick: async () => {
          try { await navigator.clipboard.writeText(url); toast(t('share.copied')); } catch { toast(url, 6000); }
        } })),
      element('label', { 'data-i18n': 'share.linkRole' }),
      roleSelect(['none', ...MEMBER_ROLES], access.linkRole, t('share.linkRole'), role => attempt(() => putAccess(canvasId, role))),
      element('h3', { 'data-i18n': 'share.members' }),
      access.members.length ? element('ul', { class: 'member-list' }, ...members) : element('p', { class: 'small-note', 'data-i18n': 'share.empty' }),
      element('form', { class: 'inline-form', onsubmit: event => {
        event.preventDefault();
        attempt(async () => {
          const added = await postMember(canvasId, email.value.trim(), inviteRole);
          toast(t(added.mailed ? 'share.invited' : 'share.addedNoMail', { email: added.email }), 4000);
          return getAccess(canvasId);
        });
      } }, email, roleSelect(MEMBER_ROLES, inviteRole, t('share.role'), role => { inviteRole = role; }),
      element('button', { class: 'button primary', type: 'submit', 'data-i18n': 'share.invite' })),
      problem);
    localize(dialog);
  }

  try { draw(await getAccess(canvasId)); }
  catch (error) { body.replaceChildren(element('p', {}, errorText(error.code))); }
}

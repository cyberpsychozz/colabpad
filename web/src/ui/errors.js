// One place that turns a server error code (+ details) into the reader's language.
import { has, t } from '../i18n/i18n.js';

export function errorText(code, details = {}) {
  if (code === 'QUOTA_EXCEEDED') {
    const hint = `err.QUOTA_EXCEEDED.hint.${details.plan || 'anon'}`;
    // The hint names a number (more a day after signing in, or Plus's daily limit), so it
    // takes the right plural form (Russian has one / few / many).
    const n = details.more ?? details.plusLimit;
    return t('err.QUOTA_EXCEEDED', { hint: has(hint) ? t(hint, { n }) : '' }).trim();
  }
  return t(has(`err.${code}`) ? `err.${code}` : 'err.INTERNAL', details);
}

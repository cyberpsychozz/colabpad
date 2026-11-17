// One place that turns a server error code (+ details) into the reader's language.
import { has, t } from '../i18n/i18n.js';

export function errorText(code, details = {}) {
  if (code === 'QUOTA_EXCEEDED') {
    const hint = `err.QUOTA_EXCEEDED.hint.${details.plan || 'anon'}`;
    return t('err.QUOTA_EXCEEDED', { hint: has(hint) ? t(hint) : '' }).trim();
  }
  return t(has(`err.${code}`) ? `err.${code}` : 'err.INTERNAL', details);
}

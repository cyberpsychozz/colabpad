// Umami's Revenue report: a paid Plus invoice is one `plus-paid` event with `revenue` and
// `currency`. The invoice number stays in this browser, only to report each sale once.
import { track as umamiTrack } from './analytics.js';

const KEY = 'collabpad.reportedPayments';
const KEEP = 50;

function reported(storage) {
  try { return JSON.parse(storage?.getItem(KEY) || '[]'); } catch { return []; }
}

export function reportPayment(payment, { storage = globalThis.localStorage, track = umamiTrack } = {}) {
  if (payment?.status !== 'succeeded') return false;
  const seen = reported(storage);
  if (seen.includes(payment.invId)) return false;
  try { storage?.setItem(KEY, JSON.stringify([...seen, payment.invId].slice(-KEEP))); } catch { /* still report */ }
  track('plus-paid', { revenue: payment.amount, currency: payment.currency, plan: payment.plan });
  return true;
}

// The invoice number Robokassa appends to SuccessURL, or null.
export function returnedInvoice(search) {
  const id = new URLSearchParams(search).get('InvId');
  return /^[1-9]\d{0,15}$/.test(id || '') ? Number(id) : null;
}

// Who is looking: account, plan, ads and quota. Loaded once per page, refreshed on demand.
import { getMe } from './api.js';

let me = { user: null, plan: 'free', ads: false, adBlocks: {}, subscription: null, quota: null };
export const currentMe = () => me;
export async function loadMe() {
  try { me = await getMe(); } catch { /* Offline: stay anonymous and ad-free until it works. */ }
  return me;
}

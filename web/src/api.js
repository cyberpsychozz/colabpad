export class ApiError extends Error {
  constructor(error, status) {
    super(error?.message || 'Request failed');
    this.code = error?.code || 'NETWORK';
    this.details = error?.details || {};
    this.status = status;
  }
}

export async function request(path, options = {}) {
  const response = await fetch(path, { ...options, signal: options.signal || AbortSignal.timeout(15000) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body.error, response.status);
  }
  return response.status === 204 ? null : response.json();
}

export const getCanvas = id => request(`/api/canvases/${encodeURIComponent(id)}`);
export const getChunk = (id, cx, cy, afterSeq = 0) => request(`/api/canvases/${encodeURIComponent(id)}/chunks/${cx}/${cy}?afterSeq=${afterSeq}`);
const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const getMe = () => request('/api/me');
export const postAlive = (id, lasso) => request(`/api/canvases/${encodeURIComponent(id)}/alive`, json('POST', { lasso }));
export const postChoice = (jobId, type) => request(`/api/alive/${encodeURIComponent(jobId)}/choice`, json('POST', { type }));
export const countMetric = key => request(`/api/metrics/${key}`, { method: 'POST' });
export const putLocale = locale => request('/api/me/locale', json('PUT', { locale }));
export const postMagicLink = email => request('/api/auth/magic-link', json('POST', { email }));
export const postLogout = () => request('/api/auth/logout', { method: 'POST' });
export const getWorlds = () => request('/api/canvases');
export const postWorld = title => request('/api/canvases', json('POST', { title }));
const access = id => `/api/canvases/${encodeURIComponent(id)}`;
export const getAccess = id => request(`${access(id)}/access`);
export const putAccess = (id, linkRole) => request(`${access(id)}/access`, json('PUT', { linkRole }));
export const postMember = (id, email, role) => request(`${access(id)}/members`, json('POST', { email, role }));
export const patchMember = (id, userId, role) => request(`${access(id)}/members/${encodeURIComponent(userId)}`, json('PATCH', { role }));
export const deleteMember = (id, userId) => request(`${access(id)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' });
export const postCheckout = plan => request('/api/billing/checkout', json('POST', { plan }));
export const postCancel = () => request('/api/billing/cancel', { method: 'POST' });

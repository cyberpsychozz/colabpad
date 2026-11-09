export class ApiError extends Error {
  constructor(error, status) {
    super(error?.message || 'Сервер недоступен. Попробуй ещё раз.');
    this.code = error?.code || 'INTERNAL';
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

export const createCanvas = () => request('/api/canvases', { method: 'POST' });
export const getCanvas = id => request(`/api/canvases/${encodeURIComponent(id)}`);
export const getStrokes = (id, afterSeq) => request(`/api/canvases/${encodeURIComponent(id)}/strokes?afterSeq=${afterSeq}`);
export const countMetric = key => request(`/api/metrics/${key}`, { method: 'POST' });

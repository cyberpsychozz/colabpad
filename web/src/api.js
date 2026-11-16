export class ApiError extends Error {
  constructor(error, status) {
    super(error?.message || 'Request failed');
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

export const getCanvas = id => request(`/api/canvases/${encodeURIComponent(id)}`);
export const getChunk = (id, cx, cy, afterSeq = 0) => request(`/api/canvases/${encodeURIComponent(id)}/chunks/${cx}/${cy}?afterSeq=${afterSeq}`);
export const countMetric = key => request(`/api/metrics/${key}`, { method: 'POST' });

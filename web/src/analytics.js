// Umami custom events (tracker is loaded in index.html). Never send names or canvas ids.
export function track(event, data) {
  try { window.umami?.track(event, data); } catch { /* Analytics must never break drawing. */ }
}

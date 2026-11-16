export const $ = (selector, root = document) => root.querySelector(selector);

export const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
};

export function toast(message, duration = 2500) {
  const item = document.createElement('div');
  item.className = 'toast';
  item.textContent = message;
  $('#toasts').append(item);
  setTimeout(() => item.remove(), duration);
}

export function dialog(markup, label) {
  const modal = document.createElement('dialog');
  modal.className = 'dialog';
  modal.setAttribute('aria-label', label);
  // All markup passed here is a developer-owned static template, never user text.
  modal.innerHTML = markup;
  document.body.append(modal);
  modal.addEventListener('close', () => modal.remove());
  modal.addEventListener('click', e => { if (e.target === modal) modal.close(); });
  modal.showModal();
  return modal;
}

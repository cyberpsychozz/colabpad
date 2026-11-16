import { t } from '../i18n/i18n.js';

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

// Fill static text from the catalogs: data-i18n (text), -label (aria-label), -title.
export function localize(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll('[data-i18n-label]')) el.setAttribute('aria-label', t(el.dataset.i18nLabel));
  for (const el of root.querySelectorAll('[data-i18n-title]')) el.title = t(el.dataset.i18nTitle);
}

export function element(tag, attributes = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name === 'class') el.className = value;
    else if (name.startsWith('on')) el.addEventListener(name.slice(2), value);
    else if (value !== false && value != null) el.setAttribute(name, value === true ? '' : value);
  }
  el.append(...children);
  return el;
}

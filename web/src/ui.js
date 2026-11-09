import { countMetric } from './api.js';
import { track } from './analytics.js';

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

export async function askName(maxLength) {
  const previous = storage.get('collabpad-name');
  if (previous !== null) return previous || null;
  const modal = dialog(`<form method="dialog"><span class="eyebrow">ДАВАЙ ЗНАКОМИТЬСЯ</span><h2>Как тебя подписать?</h2><p>Имя увидят друзья на холсте. Можно остаться гостем.</p><label for="display-name">Твоё имя</label><input id="display-name" autocomplete="nickname" placeholder="Например, Саша" autofocus><div class="dialog-actions"><button value="skip" class="button quiet">Пропустить</button><button value="save" class="button primary">Рисовать ↗</button></div></form>`, 'Как тебя подписать?');
  $('input', modal).maxLength = maxLength;
  return new Promise(resolve => modal.addEventListener('close', () => {
    const name = modal.returnValue === 'save' ? $('input', modal).value.trim().slice(0, maxLength) : '';
    storage.set('collabpad-name', name);
    track('name-prompt', { choice: name ? 'name' : 'skip' });
    resolve(name || null);
  }, { once: true }));
}

export async function copyLink(from) {
  track('link-copy', { from });
  try {
    await navigator.clipboard.writeText(location.href);
    toast('Ссылка скопирована', 2000);
  } catch {
    const modal = dialog('<h2>Ссылка на холст</h2><p>Скопируй и отправь друзьям.</p><input readonly aria-label="Ссылка на холст"><button class="button primary">Готово</button>', 'Ссылка на холст');
    $('input', modal).value = location.href;
    $('input', modal).select();
    $('button', modal).onclick = () => modal.close();
  }
}

export async function fakeDoor(key) {
  track(key);
  try {
    await countMetric(key);
    const modal = dialog('<span class="eyebrow">В ПЛАНАХ</span><h2>Скоро!</h2><p>Мы проверяем, нужно ли это</p><button class="button primary">Понятно</button>', 'Скоро');
    $('button', modal).onclick = () => modal.close();
  } catch { toast('Не удалось отправить отклик. Попробуй ещё раз.'); }
}

export async function downloadPNG(source, id, width, height, background, from) {
  track('png-download', { from });
  const output = document.createElement('canvas');
  output.width = width; output.height = height;
  const ctx = output.getContext('2d');
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(source, 0, 0, width, height);
  const blob = await new Promise(resolve => output.toBlob(resolve, 'image/png'));
  if (!blob) { toast('Не удалось сохранить картинку'); return; }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = `collabpad-${id}.png`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Картинка сохранена');
}

import './styles.css';
import { createCanvas, getCanvas } from './api.js';
import { CanvasSync } from './socket.js';
import { configureCanvas, fillBackground, pointerPoint, preparePoints, renderStroke, splitStroke } from './draw.js';
import { openTimelapse } from './timelapse.js';
import { $, askName, copyLink, downloadPNG, fakeDoor, storage, toast } from './ui.js';
import { track } from './analytics.js';

const root = $('#app');
const brand = '<a class="brand" href="/" aria-label="CollabPad — главная"><span class="brand-symbol">✳</span>collab<span>pad</span><i>●</i></a>';
let cleanup = () => {};

async function newCanvas(button, from) {
  const original = button.textContent;
  button.disabled = true; button.textContent = 'Создаём…';
  try {
    const canvas = await createCanvas();
    track('canvas-create', { from });
    location.assign(canvas.url);
  } catch (error) {
    toast(error.message || 'Не удалось создать холст. Попробуй ещё раз.');
    button.disabled = false; button.textContent = original;
  }
}

function home() {
  root.innerHTML = `<div class="home"><header class="site-header">${brand}<span class="header-note">МЕСТО ДЛЯ СОВМЕСТНОГО ТВОРЧЕСТВА</span><a class="text-link" href="#how">Как это работает ↙</a></header><main><div class="hero"><div class="hero-copy"><span class="eyebrow"><span class="status-dot green"></span> БЕЗ РЕГИСТРАЦИИ. С ДРУЗЬЯМИ.</span><h1>Рисуй вместе.<br><span class="ink-underline">Без отмены.</span></h1><p class="hero-description">Один холст, одна ссылка и сколько угодно идей.<br>Каждый штрих остаётся — как на бумаге.</p><button id="create" class="button primary large">Создать холст <span>↗</span></button><p class="small-note">Бесплатно. Пустой холст уже ждёт.</p></div><div class="hero-art" aria-label="Пример совместного рисунка"><span class="paper-label">ЧТО ПОЛУЧИТСЯ — РЕШАТЬ ВАМ</span><svg viewBox="0 0 620 450" role="img" aria-label="Солнце, цветок и свободные линии на бумаге"><g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M45 320 C130 220 100 390 195 300 S240 250 270 325" stroke="#2268df" stroke-width="12"/><path d="M408 275 Q420 320 400 388 M407 344 Q369 308 355 331 Q361 358 406 363 M408 334 Q450 302 459 325 Q451 347 407 352" stroke="#329856" stroke-width="7"/><path d="M407 265 C348 285 353 235 378 231 C330 207 373 166 395 199 C393 144 447 155 434 203 C477 171 497 221 450 236 C495 259 448 294 429 260 C437 300 394 308 407 265Z" stroke="#f07195" stroke-width="9"/><circle cx="416" cy="231" r="20" fill="#ffdc55" stroke="#292b28" stroke-width="4"/><circle cx="169" cy="130" r="42" fill="#ffdc55" stroke="#292b28" stroke-width="5"/><path d="M166 64 L165 44 M166 196 L166 216 M103 129 L82 128 M233 130 L253 131 M121 82 L107 66 M218 85 L233 67 M120 176 L104 193 M216 177 L233 193" stroke="#292b28" stroke-width="5"/><path d="M152 132 Q169 154 187 131" stroke="#292b28" stroke-width="4"/><path d="M360 95 Q454 45 525 102 M503 80 L526 102 L496 109" stroke="#ef673c" stroke-width="5"/><path d="M51 395 Q170 420 297 387" stroke="#292b28" stroke-width="3"/></g></svg><span class="drawing-label label-blue">Саша рисует</span><span class="drawing-label label-pink">Маша тоже</span><span class="paper-caption">Немного хаоса. Много вашего.</span><span class="art-sticker">Каждый штрих<br>навсегда ♡</span></div></div><section id="how" class="how"><div class="how-title"><span class="eyebrow">ПРОЩЕ ЧИСТОГО ЛИСТА</span><h2>Просто начни.</h2></div><div class="step"><span>01 /</span><h3>Открой холст</h3><p>Одно нажатие — и он твой.<br>Никаких форм и паролей.</p></div><div class="step"><span>02 /</span><h3>Позови друзей</h3><p>Отправь ссылку. Рисуйте<br>рядом, даже если вы далеко.</p></div><div class="step"><span>03 /</span><h3>Оставь след</h3><p>Здесь нет отмены. Зато есть<br>история, таймлапс и PNG.</p></div></section><div class="future"><span>А что, если рисовать всем миром?</span><button id="buy" class="text-link">Купить штрих на большом холсте ↗</button></div></main><footer><span>collabpad © 2026</span><span>Меньше сомнений. Больше линий.</span><span>Сделано для совместного творчества ↗</span></footer></div>`;
  $('#create').onclick = e => newCanvas(e.currentTarget, 'home');
  $('#buy').onclick = () => fakeDoor('stroke-buy-click');
}

function errorScreen(message, retry = false) {
  root.innerHTML = `<div class="error-page">${brand}<main><span class="error-doodle">∿</span><h1>Не получилось открыть холст</h1><p></p><button class="button primary">Создать холст ↗</button><a class="text-link" href="/">На главную</a></main></div>`;
  $('main p').textContent = message;
  if (retry) {
    const button = document.createElement('button'); button.className = 'button quiet'; button.textContent = 'Попробовать снова'; button.onclick = route;
    $('main').append(button);
  }
  $('main .primary').onclick = e => newCanvas(e.currentTarget, 'error-page');
}

async function canvasPage(id) {
  let alive = true;
  root.innerHTML = `<div class="canvas-page"><header class="site-header">${brand}<span class="canvas-title">ОБЩИЙ ХОЛСТ <span class="canvas-id"></span></span><button class="button quiet copy">Копировать ссылку ↗</button></header><div class="loading-paper"><span class="spinner"></span><h2>Загружаем холст…</h2><p></p></div></div>`;
  $('.canvas-id').textContent = `/ ${id}`;
  const slow = setTimeout(() => { const p = $('.loading-paper p'); if (alive && p) p.textContent = 'Сервер отвечает медленно, подожди немного'; }, 5000);
  cleanup = () => { alive = false; clearTimeout(slow); };
  $('.copy').onclick = () => copyLink('loading');
  let config;
  try { config = await getCanvas(id); }
  catch (error) {
    clearTimeout(slow);
    if (!alive) return;
    const missing = ['CANVAS_NOT_FOUND', 'INVALID_ID'].includes(error.code);
    track('canvas-error', { reason: missing ? 'not-found' : 'network' });
    errorScreen(missing
      ? 'Такого холста нет. Проверь ссылку или создай новый.'
      : 'Не удалось связаться с сервером. Проверь соединение и попробуй ещё раз.', !missing);
    return;
  }
  clearTimeout(slow);
  if (!alive) return;
  root.innerHTML = `<div class="canvas-page"><header class="site-header">${brand}<span class="canvas-title">ОБЩИЙ ХОЛСТ <span class="canvas-id"></span></span><div class="header-actions"><button class="button quiet replay-button">▷ Таймлапс</button><button class="button quiet png-button">PNG ↓</button><button class="button primary copy">Копировать ссылку ↗</button></div></header><main class="studio"><div class="studio-top"><div class="connection" role="status"><span class="status-dot red"></span><span>Подключаемся…</span></div><div class="presence"><span class="online-count">● 0 онлайн</span><span class="online-names"></span></div></div><div class="banner" role="status" hidden></div><div class="workspace"><aside class="toolbar" aria-label="Инструменты рисования"><span class="tool-icon" aria-hidden="true">✎</span><div class="palette" role="group" aria-label="Цвет кисти"></div><span class="tool-divider"></span><label class="width-label" for="brush-width">Толщина <output></output></label><input id="brush-width" type="range" step="1" aria-label="Толщина кисти"><div class="brush-preview"><span></span></div><span class="tool-divider"></span><button class="pro-button" aria-label="Pro — скоро">Pro ↗</button></aside><div class="paper-area"><div class="canvas-frame"><canvas id="drawing" aria-label="Общий холст для рисования"></canvas><div class="empty-hint"><span class="empty-mark">✳</span><h2>Начни рисовать.<br>Отменить нельзя 🙂</h2><p>Позови друзей: <button class="text-link empty-copy">Копировать ссылку</button></p></div></div><div class="paper-meta"><span class="dimensions"></span><span>КАЖДЫЙ ШТРИХ — НАВСЕГДА</span><span class="stroke-count"></span></div></div></div><div class="studio-bottom"><span>↳ Здесь нет отмены. И это освобождает.</span><span>Мышь · Палец · Стилус</span></div></main></div>`;
  $('.canvas-id').textContent = `/ ${id}`;
  $('.dimensions').textContent = `${config.width} × ${config.height}`;
  const canvas = $('#drawing');
  const ratio = devicePixelRatio || 1;
  const ctx = configureCanvas(canvas, config.width, config.height, ratio);
  const cache = document.createElement('canvas');
  const cached = configureCanvas(cache, config.width, config.height, ratio);
  canvas.style.aspectRatio = `${config.width}/${config.height}`;
  $('.canvas-frame').style.aspectRatio = `${config.width}/${config.height}`;
  let color = config.palette[0], width = Math.max(config.brushWidthMin, Math.min(6, config.brushWidthMax));
  let active = null, pointerId = null, readOnly = config.strokeCount >= config.maxStrokes, roomFull = false;
  let count = config.strokeCount, sync, drewHere = false, fullTracked = false;
  const widthInput = $('#brush-width');
  widthInput.min = config.brushWidthMin; widthInput.max = config.brushWidthMax; widthInput.value = width;
  const colors = $('.palette');
  config.palette.forEach(value => {
    const button = document.createElement('button');
    button.className = 'color-button'; button.style.setProperty('--color', value);
    button.setAttribute('aria-label', `Цвет ${value}`); button.setAttribute('aria-pressed', String(value === color));
    button.onclick = () => { color = value; updateTools(); };
    colors.append(button);
  });

  function updateTools() {
    $('.width-label output').textContent = `${width}`;
    $('.brush-preview span').style.cssText = `width:${width}px;height:${width}px;background:${color}`;
    [...colors.children].forEach((button, i) => button.setAttribute('aria-pressed', String(config.palette[i] === color)));
    [...colors.children, widthInput].forEach(el => { el.disabled = readOnly; });
    canvas.style.cursor = readOnly ? 'default' : 'crosshair';
  }

  function render() {
    ctx.drawImage(cache, 0, 0, config.width, config.height);
    for (const stroke of sync?.outbox || []) renderStroke(ctx, stroke);
    if (active) renderStroke(ctx, active);
    $('.empty-hint').hidden = count > 0 || !!active || !!sync?.outbox.length;
    $('.stroke-count').textContent = `${count.toLocaleString('ru-RU')} / ${config.maxStrokes.toLocaleString('ru-RU')} штрихов`;
  }

  function reset(strokes) {
    fillBackground(cached, config.width, config.height, config.background);
    strokes.forEach(stroke => renderStroke(cached, stroke));
    count = strokes.length;
    render();
    if (count >= config.maxStrokes) full();
  }

  function full() {
    if (!fullTracked) { fullTracked = true; track('limit-hit', { type: 'canvas-full' }); }
    readOnly = true;
    $('.banner').hidden = false;
    $('.banner').textContent = `Холст заполнен — здесь уже ${config.maxStrokes.toLocaleString('ru-RU')} штрихов. Можно посмотреть таймлапс или скачать PNG.`;
    updateTools();
  }

  reset(config.strokes); updateTools();
  performance.mark('collabpad-canvas-rendered');
  canvas.dataset.renderMs = performance.now().toFixed(1);
  widthInput.oninput = () => { width = Number(widthInput.value); updateTools(); };
  $('.copy').onclick = () => copyLink('header');
  $('.empty-copy').onclick = () => copyLink('empty-hint');
  $('.pro-button').onclick = () => fakeDoor('pro-click');
  $('.png-button').onclick = () => downloadPNG(canvas, id, config.width, config.height, config.background, 'canvas');
  $('.replay-button').onclick = () => openTimelapse(config, sync?.confirmed() || config.strokes, canvas);
  const name = await askName(config.nameMaxLen);
  if (!alive) return;
  if (!storage.get('collabpad-hint')) {
    toast('Здесь нет отмены. Каждый штрих — навсегда.', 5000);
    storage.set('collabpad-hint', '1');
  }

  sync = new CanvasSync({
    canvas: config, name,
    onStroke(stroke) { renderStroke(cached, stroke); count++; render(); },
    onReset: reset,
    onState({ connected, pending }) {
      if (roomFull) return;
      $('.connection').className = `connection ${!connected ? 'offline' : pending ? 'saving' : 'connected'}`;
      $('.connection span:last-child').textContent = !connected
        ? 'Нет связи — штрихи сохранятся после переподключения'
        : pending ? `Сохранение… (${pending})` : 'Подключено';
      render();
    },
    onPresence(online) {
      $('.online-count').textContent = `● ${online.length} онлайн`;
      $('.online-names').textContent = online.join(', ');
    },
    onFull: full,
    onError(code) {
      if (code === 'ROOM_FULL') {
        track('limit-hit', { type: 'room-full' });
        roomFull = readOnly = true;
        $('.banner').hidden = false;
        $('.banner').textContent = 'На холсте сейчас слишком много людей. Обнови страницу позже.';
        $('.connection span:last-child').textContent = 'Только просмотр';
        updateTools();
      } else toast(code === 'INTERNAL' ? 'Проблемы с сервером' : 'Этот штрих не сохранился');
      render();
    },
  });

  let renderFrame = 0;
  function scheduleRender() {
    if (!renderFrame) renderFrame = requestAnimationFrame(() => { renderFrame = 0; render(); });
  }
  function capture(event) {
    const rect = canvas.getBoundingClientRect();
    const events = event.getCoalescedEvents?.() || [event];
    for (const sample of events.length ? events : [event]) {
      const p = pointerPoint(sample, rect, config.width, config.height);
      const last = active.points.at(-1);
      if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= 1) active.points.push(p);
    }
    scheduleRender();
  }
  canvas.onpointerdown = event => {
    if (readOnly || active || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    pointerId = event.pointerId;
    canvas.setPointerCapture(pointerId);
    active = { color, width, points: [] };
    capture(event);
  };
  canvas.onpointermove = event => { if (active && event.pointerId === pointerId) capture(event); };
  function finish(event) {
    if (!active || event.pointerId !== pointerId) return;
    if (event.type === 'pointerup') capture(event);
    const stroke = active;
    active = null; pointerId = null;
    stroke.points = preparePoints(stroke.points, config.simplifyEpsilon, config.width, config.height);
    if (stroke.points.length >= 2) {
      const chunks = splitStroke({ ...stroke, canvasId: id }, config.maxPointsPerStroke, config.maxPayloadBytes);
      for (const chunk of chunks) sync.enqueue({ ...chunk, clientId: crypto.randomUUID() });
      if (!drewHere) { drewHere = true; track('first-stroke', { canvasWasEmpty: count === 0 }); }
    }
    render();
  }
  canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = finish;
  const beforeUnload = event => { if (sync.outbox.length) { event.preventDefault(); event.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);
  sync.start();
  cleanup = () => {
    alive = false;
    sync.stop(); cancelAnimationFrame(renderFrame);
    window.removeEventListener('beforeunload', beforeUnload);
  };
}

function route() {
  cleanup(); cleanup = () => {};
  const match = location.pathname.match(/^\/c\/([A-Za-z0-9_-]+)\/?$/);
  if (match) canvasPage(match[1]);
  else if (location.pathname === '/') home();
  else errorScreen('Такого холста нет. Проверь ссылку или создай новый.');
}

// Full navigation prevents accidentally abandoning an unsaved in-memory outbox.
window.addEventListener('popstate', route);
route();

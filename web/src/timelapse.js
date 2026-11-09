import { configureCanvas, fillBackground, renderStroke } from './draw.js';
import { $, dialog, downloadPNG } from './ui.js';
import { track } from './analytics.js';

export function openTimelapse(config, confirmed, mainCanvas) {
  const strokes = [...confirmed].sort((a, b) => a.seq - b.seq);
  track('timelapse-open', { strokes: strokes.length });
  const modal = dialog(`<div class="replay-head"><div><span class="eyebrow">КАЖДЫЙ ШТРИХ — ЧАСТЬ ИСТОРИИ</span><h2>Как это было</h2></div><button class="icon-button" aria-label="Закрыть таймлапс">×</button></div><div class="replay-paper"><canvas aria-label="Таймлапс холста"></canvas><p class="replay-empty">Пока нечего показывать — нарисуй первый штрих</p></div><div class="replay-controls"><button class="button primary play">Пауза</button><select aria-label="Скорость воспроизведения"><option value="1">1×</option><option value="2">2×</option><option value="5">5×</option></select><input type="range" min="0" value="0" step="1" aria-label="Номер штриха"><output></output><button class="button quiet png">Скачать PNG ↓</button></div>`, 'Таймлапс');
  modal.classList.add('replay');
  const canvas = $('canvas', modal);
  canvas.style.aspectRatio = `${config.width}/${config.height}`;
  const ctx = configureCanvas(canvas, config.width, config.height, Math.min(devicePixelRatio || 1, 2));
  const slider = $('input', modal), play = $('.play', modal);
  slider.max = strokes.length;
  slider.disabled = play.disabled = strokes.length === 0;
  $('.replay-empty', modal).hidden = strokes.length > 0;
  let index = 0, playing = strokes.length > 0, speed = 1, last = 0, budget = 0, frame;
  const interval = strokes.length ? Math.min(40, 60000 / strokes.length) : 40;

  function update() {
    slider.value = index;
    $('output', modal).textContent = `${index} / ${strokes.length}`;
    play.textContent = playing ? 'Пауза' : index === strokes.length && strokes.length ? 'Сначала ↻' : 'Играть ▶';
  }

  function seek(target) {
    fillBackground(ctx, config.width, config.height, config.background);
    for (let i = 0; i < target; i++) renderStroke(ctx, strokes[i]);
    index = target; budget = 0; update();
  }

  function tick(now) {
    if (playing) {
      budget += Math.min(now - (last || now), 250) * speed;
      while (budget >= interval && index < strokes.length) {
        renderStroke(ctx, strokes[index++]); budget -= interval;
      }
      if (index === strokes.length) playing = false;
      update();
    }
    last = now;
    frame = requestAnimationFrame(tick);
  }
  seek(0);
  frame = requestAnimationFrame(tick);
  play.onclick = () => {
    if (index === strokes.length) seek(0);
    playing = !playing; budget = 0; update();
  };
  slider.oninput = () => { playing = false; seek(Number(slider.value)); };
  $('select', modal).onchange = e => { speed = Number(e.target.value); };
  $('.icon-button', modal).onclick = () => modal.close();
  $('.png', modal).onclick = () => downloadPNG(mainCanvas, config.id, config.width, config.height, config.background, 'timelapse');
  modal.addEventListener('close', () => cancelAnimationFrame(frame), { once: true });
}

// The ALIVE flow on the client: the button under the lasso, goo, chooser and grid.
import { getMe, postAlive, postChoice } from '../api.js';
import { has, t } from '../i18n/i18n.js';
import { gooGeometry } from '../render/goo.js';
import { element, toast } from './dom.js';
import { errorText } from './errors.js';

export { errorText };

export const TYPES = ['creature', 'predator', 'aquatic', 'person', 'food', 'plant', 'water', 'house',
  'explosive', 'vehicle', 'remote', 'fire', 'sky', 'doodle'];
const ICONS = { creature: '🐄', predator: '🐺', aquatic: '🐟', person: '🧍', food: '🍎', plant: '🌳', water: '💧',
  house: '🏠', explosive: '💣', vehicle: '🚗', remote: '🎮', fire: '🔥', sky: '☁️', doodle: '⭐' };

export function createAlive({ canvasId, panel, stage, camera, entities, onChange, getLasso, clearLasso }) {
  const goos = new Map();     // jobId -> goo being shown
  const mine = new Set();     // jobs this client started
  let quota = null, busy = false, error = '', chooser = null;

  const button = element('button', { class: 'button primary alive-go', type: 'button', 'data-i18n': 'alive.button', onclick: go });
  const left = element('span', { class: 'alive-left' });
  const problem = element('span', { class: 'alive-error', role: 'alert' });
  panel.prepend(element('div', { class: 'alive-main' }, button, left), problem);

  function refresh() {
    button.disabled = busy;
    left.textContent = quota ? t('alive.left', { n: Math.max(0, quota.limit - quota.used), limit: quota.limit }) : '';
    problem.textContent = error;
    problem.hidden = !error;
    if (chooser) chooser.render();
  }

  async function loadQuota() {
    try { quota = (await getMe()).quota; refresh(); } catch { /* The counter is a nicety. */ }
  }

  async function go() {
    const lasso = getLasso();
    if (!lasso?.closed || busy) return;
    busy = true; error = ''; refresh();
    try {
      const reply = await postAlive(canvasId, lasso.points);
      mine.add(reply.jobId);
      quota = reply.quota;
      clearLasso();
    } catch (failure) {
      // The text stays under the lasso so the author can adjust the loop and retry.
      error = errorText(failure.code, failure.details);
      if (failure.code === 'QUOTA_EXCEEDED') quota = { used: failure.details.used, limit: failure.details.limit };
    }
    busy = false;
    refresh(); onChange();
  }

  function end(jobId, ending, now) {
    const goo = goos.get(jobId);
    if (goo && !goo.ending) { goo.ending = ending; goo.endedAt = now; }
    if (chooser?.jobId === jobId) closeChooser();
    if (mine.delete(jobId)) loadQuota();
  }

  function closeChooser() {
    chooser?.el.remove();
    clearInterval(chooser?.timer);
    chooser = null;
  }

  // Three guesses plus "something else", or the whole grid when there are no guesses.
  function openChooser({ jobId, top3, deadline }) {
    closeChooser();
    const el = element('div', { class: 'chooser', role: 'dialog' });
    let grid = !top3;
    const pick = async type => {
      const current = chooser;
      closeChooser();
      try { await postChoice(jobId, type); }
      catch (failure) { if (current) toast(errorText(failure.code, failure.details)); }
    };
    const chip = type => element('button', { class: 'chip', type: 'button', onclick: () => pick(type) },
      element('span', { 'aria-hidden': 'true' }, ICONS[type]), ' ', t(`type.${type}`));
    function render() {
      const seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      const title = element('div', { class: 'chooser-head' }, element('strong', {}, t('choose.title')), element('span', { class: 'chooser-time' }, String(seconds)));
      const options = grid
        ? element('div', { class: 'chooser-grid' }, ...TYPES.map(chip))
        : element('div', { class: 'chooser-row' }, ...top3.map(chip),
          element('button', { class: 'chip quiet', type: 'button', onclick: () => { grid = true; render(); } }, t('choose.other')));
      const back = grid && top3 ? [element('button', { class: 'chip quiet', type: 'button', onclick: () => { grid = false; render(); } }, t('choose.back'))] : [];
      el.replaceChildren(title, options, ...back);
      place();
    }
    function place() {
      const goo = goos.get(jobId);
      const [x, y] = goo ? camera.toScreen(goo.center[0], goo.box[3]) : [camera.viewW / 2, camera.viewH / 2];
      el.style.left = `${Math.max(150, Math.min(camera.viewW - 150, x))}px`;
      el.style.top = `${Math.max(8, Math.min(camera.viewH - el.offsetHeight - 8, y + 10))}px`;
    }
    stage.append(el);
    const timer = setInterval(() => { if (Date.now() >= deadline) closeChooser(); else render(); }, 250);
    chooser = { jobId, el, timer, render, place };
    render();
  }

  // Socket events from the server.
  function onEvent(name, data) {
    const now = performance.now();
    if (name === 'alive:goo') {
      goos.set(data.jobId, { jobId: data.jobId, polygon: data.polygon, by: data.by, start: now, stage: 'raster', ...gooGeometry(data.polygon) });
    } else if (name === 'alive:progress') {
      const goo = goos.get(data.jobId);
      if (goo) goo.stage = data.stage;
    } else if (name === 'alive:choose') {
      openChooser(data);
    } else if (name === 'alive:failed') {
      end(data.jobId, 'fizzle', now);
      if (mine.has(data.jobId) || chooser?.jobId === data.jobId) toast(errorText(data.code));
    } else if (name === 'world:event' && data.type === 'spawn') {
      entities.put(data.entity, now);
      end(data.jobId, 'pop', now);
    }
    onChange();
  }

  loadQuota();
  return {
    goos, onEvent, refresh,
    clearError() { if (error) { error = ''; refresh(); } },
    reposition() { chooser?.place(); },
    forget(jobId) { goos.delete(jobId); },
    destroy: closeChooser,
  };
}

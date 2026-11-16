// The world screen: camera, chunk tiles, drawing in world coordinates, the ✨ lasso.
import { getCanvas } from '../api.js';
import { Camera, formatHash, parseHash, visibleChunks } from '../camera.js';
import { ChunkStore } from '../chunks.js';
import { pointerPoint, preparePoints, simplify, splitAtChunks, splitStroke } from '../draw.js';
import { has, onLocaleChange, t } from '../i18n/i18n.js';
import { drawWorld } from '../render/strokes.js';
import { WorldSync } from '../socket.js';
import { track } from '../analytics.js';
import { brand, languageSwitch, refreshLanguageSwitch } from './chrome.js';
import { element, localize, toast } from './dom.js';
import { createTools } from './tools.js';

const LASSO_MAX_POINTS = 500;

export async function openWorld(root, canvasId) {
  let alive = true;
  const status = element('div', { class: 'connection offline', role: 'status' }, element('span', { class: 'status-dot' }), element('span', { class: 'connection-text' }));
  const canvas = element('canvas', { class: 'world-canvas', 'data-i18n-label': 'canvas.label' });
  const hint = element('div', { class: 'world-hint', role: 'status', hidden: true });
  const loading = element('div', { class: 'world-loading' }, element('span', { class: 'spinner' }), element('h2', { 'data-i18n': 'canvas.loading' }));
  const lassoPanel = element('div', { class: 'lasso-panel', hidden: true });
  const stage = element('div', { class: 'world-stage' }, canvas, hint, lassoPanel, loading);
  const page = element('div', { class: 'world' },
    element('header', { class: 'world-bar' }, brand(), status, languageSwitch()), stage);
  root.replaceChildren(page);
  localize(page);

  let config;
  try { config = await getCanvas(canvasId); }
  catch (error) {
    if (!alive) return () => {};
    loading.replaceChildren(element('h2', {}, t(error.code === 'CANVAS_NOT_FOUND' || error.code === 'INVALID_ID' ? 'err.NOT_FOUND' : 'err.NETWORK')));
    return () => { alive = false; };
  }
  if (!alive) return () => {};

  const { limits, bounds } = config;
  const canEdit = config.role === 'editor';
  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
  const ctx = canvas.getContext('2d');
  const camera = new Camera(bounds, limits.chunkSize);
  const store = new ChunkStore({
    size: limits.chunkSize,
    createTile: pixels => Object.assign(document.createElement('canvas'), { width: pixels, height: pixels }),
  });
  let connection = { connected: false, pending: 0 };
  let active = null, lasso = null, frame = 0, drewHere = false, started = false;

  const sync = new WorldSync({
    canvasId, store,
    onState(state) { connection = state; refreshText(); schedule(); },
    onChange: () => schedule(),
    onError(code) { toast(t(has(`err.${code}`) ? `err.${code}` : 'canvas.unsaved')); schedule(); },
  });

  const tools = createTools({
    palette: config.palette, limits, canEdit,
    onTool() { if (tools.state.tool !== 'alive') lasso = null; refreshText(); schedule(); },
    onZoom(factor) { camera.zoomAt(camera.viewW / 2, camera.viewH / 2, factor); cameraChanged(); },
  });
  stage.append(tools.el);
  localize(tools.el);
  lassoPanel.append(element('button', { class: 'icon-button', type: 'button', 'data-i18n-label': 'alive.cancel', onclick: () => { lasso = null; refreshText(); schedule(); } }, '×'));
  localize(lassoPanel);

  function refreshText() {
    const { connected, pending } = connection;
    status.className = `connection ${!connected ? 'offline' : pending ? 'saving' : 'connected'}`;
    status.lastChild.textContent = !connected ? t('canvas.offline') : pending ? t('canvas.saving', { n: pending }) : t('canvas.connected');
    canvas.dataset.tool = spaceDown ? 'hand' : tools.state.tool;
    let text = '';
    if (config.frozen) text = t('canvas.frozen');
    else if (tools.state.tool === 'alive' && !lasso?.closed) text = t('alive.hint');
    else if (started && canEdit && !store.hasStrokes() && !sync.outbox.length && !active) text = t('canvas.empty');
    hint.textContent = text;
    hint.hidden = !text;
  }

  function render() {
    frame = 0;
    const pending = active ? [...sync.outbox, active] : sync.outbox;
    drawWorld(ctx, { camera, store, dpr: dpr(), pending, lasso });
    if (!started && store.allLoaded(wanted().filter(c => c.visible).map(c => [c.cx, c.cy]))) {
      started = true;
      loading.hidden = true;
    }
    lassoPanel.hidden = !lasso?.closed;
    if (lasso?.closed) {
      const xs = lasso.points.map(p => p[0]), ys = lasso.points.map(p => p[1]);
      const [sx, sy] = camera.toScreen((Math.min(...xs) + Math.max(...xs)) / 2, Math.max(...ys));
      lassoPanel.style.left = `${Math.max(60, Math.min(camera.viewW - 60, sx))}px`;
      lassoPanel.style.top = `${Math.max(8, Math.min(camera.viewH - 60, sy + 12))}px`;
    }
    refreshText();
  }
  const schedule = () => { if (!frame && alive) frame = requestAnimationFrame(render); };
  const wanted = () => visibleChunks(camera, limits.maxChunks);

  let hashTimer = 0;
  function cameraChanged() {
    sync.setViewport(wanted().map(c => [c.cx, c.cy]));
    clearTimeout(hashTimer);
    hashTimer = setTimeout(() => history.replaceState(null, '', formatHash(camera)), 400);
    schedule();
  }

  function resize() {
    const rect = stage.getBoundingClientRect();
    canvas.width = Math.round(rect.width * dpr());
    canvas.height = Math.round(rect.height * dpr());
    camera.resize(rect.width, rect.height);
    cameraChanged();
  }

  // ---- pointer input: one pointer draws, lassoes or pans; two pointers pan and pinch.
  const pointers = new Map();
  let mode = null, spaceDown = false, pinch = null;
  const local = event => { const rect = canvas.getBoundingClientRect(); return [event.clientX - rect.left, event.clientY - rect.top]; };
  const measure = () => {
    const [a, b] = [...pointers.values()];
    return { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, distance: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1 };
  };
  function capture(event, target) {
    const rect = canvas.getBoundingClientRect();
    const events = event.getCoalescedEvents?.() || [];
    for (const sample of events.length ? events : [event]) {
      const p = pointerPoint(sample, rect, camera);
      const last = target.at(-1);
      // One screen pixel apart, whatever the zoom.
      if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= 1 / camera.z) target.push(p);
    }
    schedule();
  }

  function finishStroke() {
    const stroke = active;
    active = null;
    const points = preparePoints(stroke.points, limits.simplifyEpsilon / camera.z, bounds.w, bounds.h);
    if (points.length < 2) return;
    for (const piece of splitAtChunks(points, limits.chunkSize)) {
      const part = { canvasId, color: stroke.color, width: stroke.width, points: piece };
      for (const chunk of splitStroke(part, limits.maxPointsPerStroke, limits.maxPayloadBytes)) {
        sync.enqueue({ ...chunk, clientId: crypto.randomUUID() });
      }
    }
    if (!drewHere) { drewHere = true; track('first-stroke'); }
  }

  function finishLasso() {
    let points = lasso.points, epsilon = 1 / camera.z;
    do { points = simplify(lasso.points, epsilon); epsilon *= 2; } while (points.length > LASSO_MAX_POINTS);
    lasso = points.length >= 3 ? { points: points.map(([x, y]) => [Math.round(x * 10) / 10, Math.round(y * 10) / 10]), closed: true } : null;
  }

  function onDown(event) {
    canvas.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, local(event));
    if (pointers.size === 2) {
      // A second finger turns the gesture into pan + pinch; the unfinished line is dropped.
      active = null;
      if (mode === 'lasso') lasso = null;
      mode = 'pinch'; pinch = measure();
      schedule();
      return;
    }
    if (pointers.size > 2) return;
    const hand = tools.state.tool === 'hand' || spaceDown || event.button === 1;
    if (hand || !canEdit || config.frozen) mode = 'pan';
    else if (event.button !== 0) mode = null;
    else if (tools.state.tool === 'draw') {
      mode = 'draw';
      active = { color: tools.state.color, width: tools.state.width, points: [] };
      capture(event, active.points);
    } else {
      mode = 'lasso';
      lasso = { points: [], closed: false };
      capture(event, lasso.points);
    }
    if (mode) event.preventDefault();
  }

  function onMove(event) {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    const now = local(event);
    pointers.set(event.pointerId, now);
    if (mode === 'draw' && active) capture(event, active.points);
    else if (mode === 'lasso' && lasso) capture(event, lasso.points);
    else if (mode === 'pan') { camera.panBy(now[0] - previous[0], now[1] - previous[1]); cameraChanged(); }
    else if (mode === 'pinch' && pointers.size === 2) {
      const next = measure();
      camera.panBy(next.x - pinch.x, next.y - pinch.y);
      camera.zoomAt(next.x, next.y, next.distance / pinch.distance);
      pinch = next;
      cameraChanged();
    }
  }

  function onUp(event) {
    if (!pointers.delete(event.pointerId)) return;
    if (mode === 'draw' && active) {
      if (event.type === 'pointerup') capture(event, active.points);
      finishStroke();
    } else if (mode === 'lasso' && lasso) finishLasso();
    // Lifting one of two fingers keeps panning with the other.
    mode = mode === 'pinch' && pointers.size === 1 ? 'pan' : pointers.size ? mode : null;
    schedule();
  }

  function onWheel(event) {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : 1;
    const [sx, sy] = local(event);
    camera.zoomAt(sx, sy, Math.exp(-event.deltaY * unit * (event.ctrlKey ? 0.01 : 0.0015)));
    cameraChanged();
  }

  const typing = event => /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
  const onKeyDown = event => {
    if (event.code === 'Space' && !typing(event)) { event.preventDefault(); spaceDown = true; refreshText(); }
    if (event.key === 'Escape' && lasso) { lasso = null; schedule(); }
  };
  const onKeyUp = event => { if (event.code === 'Space') { spaceDown = false; refreshText(); } };
  const beforeUnload = event => { if (sync.outbox.length) { event.preventDefault(); event.returnValue = ''; } };

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(type, onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('beforeunload', beforeUnload);
  const observer = new ResizeObserver(resize);
  observer.observe(stage);
  const stopLocale = onLocaleChange(() => { localize(page); refreshLanguageSwitch(page); tools.refresh(); refreshText(); });

  // Deep link first, else the server's spawn point.
  const rect = stage.getBoundingClientRect();
  camera.resize(rect.width, rect.height);
  const link = parseHash(location.hash);
  camera.centerOn(link?.x ?? config.spawn.x, link?.y ?? config.spawn.y, link?.z ?? 1);
  resize();
  sync.start();

  return () => {
    alive = false;
    sync.stop();
    cancelAnimationFrame(frame);
    clearTimeout(hashTimer);
    observer.disconnect();
    stopLocale();
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('keyup', onKeyUp);
    window.removeEventListener('beforeunload', beforeUnload);
  };
}

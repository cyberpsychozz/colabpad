// The world screen: camera, chunk tiles, drawing in world coordinates, the ✨ lasso.
import { getCanvas } from '../api.js';
import { Camera, formatHash, parseHash, visibleChunks } from '../camera.js';
import { ChunkStore } from '../chunks.js';
import { EntityStore } from '../entities.js';
import { pointerPoint, preparePoints, simplify, splitAtChunks, splitStroke } from '../draw.js';
import { has, onLocaleChange, t } from '../i18n/i18n.js';
import { drawWorld } from '../render/strokes.js';
import { Vfx } from '../render/vfx.js';
import { WorldSync } from '../socket.js';
import { track } from '../analytics.js';
import { createAlive } from './alive.js';
import { createControl } from './control.js';
import { currentMe } from '../me.js';
import { accountButton, openAccount } from './account.js';
import { brand, languageSwitch, refreshLanguageSwitch } from './chrome.js';
import { mountAds } from './gate.js';
import { shareButton } from './share.js';
import { element, localize, toast } from './dom.js';
import { createTools } from './tools.js';

const LASSO_MAX_POINTS = 500;
const DRAG_SEND_MS = 220;      // under the server's 5 interactions a second
const DOUBLE_CLICK_MS = 350;

export async function openWorld(root, canvasId) {
  let alive = true;
  const status = element('div', { class: 'connection offline', role: 'status' }, element('span', { class: 'status-dot' }), element('span', { class: 'connection-text' }));
  const canvas = element('canvas', { class: 'world-canvas', 'data-i18n-label': 'canvas.label' });
  const hint = element('div', { class: 'world-hint', role: 'status', hidden: true });
  const loading = element('div', { class: 'world-loading' }, element('span', { class: 'spinner' }), element('h2', { 'data-i18n': 'canvas.loading' }));
  const lassoPanel = element('div', { class: 'lasso-panel', hidden: true });
  const stage = element('div', { class: 'world-stage' }, canvas, hint, lassoPanel, loading);
  let showCursors = false;
  const cursors = new Map();
  let cursorBtn = null;
  const bar = element('header', { class: 'world-bar' }, brand(), status, accountButton(), languageSwitch());
  const body = element('div', { class: 'world-body' }, stage);
  const page = element('div', { class: 'world' }, bar, body);
  root.replaceChildren(page);
  // Ad space goes in before the world loads, so the canvas never changes size for it; after
  // the page is in the document, because the ad loader looks its containers up by id.
  if (currentMe().ads) mountAds(body, stage, currentMe().adBlocks || {});
  localize(page);

  let config;
  try { config = await getCanvas(canvasId); }
  catch (error) {
    if (!alive) return () => {};
    const key = error.code === 'FORBIDDEN_ROLE' ? 'canvas.noAccess'
      : error.code === 'NOT_FOUND' || error.code === 'VALIDATION_FAILED' ? 'err.NOT_FOUND' : 'err.NETWORK';
    loading.replaceChildren(element('h2', { 'data-i18n': key }),
      error.code === 'FORBIDDEN_ROLE' && !currentMe().user
        ? element('button', { class: 'button primary', type: 'button', 'data-i18n': 'account.signIn', onclick: openAccount })
        : element('a', { class: 'button', href: '/w', 'data-i18n': 'landing.enter' }));
    status.hidden = true;
    localize(loading);
    const stop = onLocaleChange(() => { localize(page); refreshLanguageSwitch(page); });
    return () => { alive = false; stop(); };
  }
  if (!alive) return () => {};

  if (config.owner) status.after(shareButton(canvasId));
  if (config.kind === 'private') bar.querySelector('.brand').after(element('span', { class: 'world-title' }, config.title || ''));
  localize(bar);
  const { limits, bounds } = config;
  const canEdit = config.role === 'editor';
  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
  const ctx = canvas.getContext('2d');
  const camera = new Camera(bounds, limits.chunkSize);
  const store = new ChunkStore({
    size: limits.chunkSize,
    createTile: pixels => Object.assign(document.createElement('canvas'), { width: pixels, height: pixels }),
  });
  const entities = new EntityStore({
    size: limits.chunkSize,
    loadImage: url => { const image = new Image(); image.onload = () => schedule(); image.src = url; return image; },
  });
  const vfx = new Vfx();
  let connection = { connected: false, pending: 0 };
  let active = null, lasso = null, frame = 0, drewHere = false, started = false, lastFrame = performance.now();

  const sync = new WorldSync({
    canvasId, store,
    onState(state) { connection = state; refreshText(); schedule(); },
    onChange: () => schedule(),
    onEvent: (name, data) => worldEvent(name, data),
    onChunk: (cx, cy, data) => entities.setChunk(cx, cy, data.entities),
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
  const alivePanel = createAlive({
    canvasId, panel: lassoPanel, stage, camera, entities,
    onChange: () => schedule(),
    getLasso: () => lasso,
    clearLasso: () => { lasso = null; },
  });
  localize(lassoPanel);
  const control = createControl({ sync, stage, entities, onChange: () => schedule() });
  localize(stage);

  // Everything the server tells us about the living world.
  function worldEvent(name, data) {
    const now = performance.now();
    // One world:delta per tick, with a part for every watched chunk that changed.
    if (name === 'world:delta') for (const chunk of data.chunks) entities.applyDelta(chunk, now);
    else if (name === 'world:snapshot') {
      if (store.get(data.cx, data.cy)) entities.setChunk(data.cx, data.cy, data.entities);
    } else if (name === 'world:event') {
      if (data.type === 'fossil') store.addFossil(data.fossil);
      else if (data.type === 'death') { entities.die(data.id, data.fossil, now); vfx.add(data, now); }
      else if (data.type !== 'spawn') vfx.add(data, now);
      if (data.type === 'spawn') alivePanel.onEvent(name, data);
    } else if (name === 'canvas:frozen') {
      // Roles and tools change with it: start the screen again from the server's answer.
      if (data.frozen !== config.frozen) location.reload();
    } else if (name === 'canvas:kicked') {
      location.replace('/w');
    } else if (name === 'canvas:role') {
      if (data.role !== config.role) location.reload();
    } else if (name === 'cursor:move') {
      const prev = cursors.get(data.id);
      cursors.set(data.id, prev ? { ...data, rx: prev.rx, ry: prev.ry, at: now } : { ...data, rx: data.x, ry: data.y, at: now });
    } else if (name === 'cursor:leave') {
      cursors.delete(data.id);
    } else if (name.startsWith('control:')) control.onEvent(name, data);
    else alivePanel.onEvent(name, data);
    schedule();
  }

  function refreshText() {
    const { connected, pending } = connection;
    status.className = `connection ${!connected ? 'offline' : pending ? 'saving' : 'connected'}`;
    status.lastChild.textContent = !connected ? t('canvas.offline') : pending ? t('canvas.saving', { n: pending }) : t('canvas.connected');
    canvas.dataset.tool = spaceDown ? 'hand' : tools.state.tool;
    let text = '';
    if (config.frozen) text = t(config.owner ? 'canvas.frozenOwner' : 'canvas.frozen');
    else if (tools.state.tool === 'alive' && !lasso?.closed) text = t('alive.hint');
    else if (started && canEdit && !store.hasStrokes() && !sync.outbox.length && !active) text = t('canvas.empty');
    hint.textContent = text;
    hint.hidden = !text;
  }

  function render() {
    frame = 0;
    const pending = active ? [...sync.outbox, active] : sync.outbox;
    const now = performance.now();
    const moving = entities.advance(Math.min(0.1, (now - lastFrame) / 1000), now);
    lastFrame = now;
    // While driving, the camera keeps the vehicle in view.
    const vehicle = control.vehicleId && entities.entities.get(control.vehicleId);
    if (vehicle) {
      const [sx, sy] = camera.toScreen(vehicle.rx, vehicle.ry);
      const marginX = camera.viewW * 0.3, marginY = camera.viewH * 0.3;
      const dx = sx < marginX ? sx - marginX : sx > camera.viewW - marginX ? sx - (camera.viewW - marginX) : 0;
      const dy = sy < marginY ? sy - marginY : sy > camera.viewH - marginY ? sy - (camera.viewH - marginY) : 0;
      if (dx || dy) { camera.panBy(-dx, -dy); cameraChanged(); }
    }
    const animating = drawWorld(ctx, { camera, store, dpr: dpr(), pending, lasso, entities, goos: alivePanel.goos, vfx, driving: control.vehicleId, now });
    if (config.kind === 'private' && showCursors && cursors.size) {
      const scale = dpr();
      ctx.save();
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.font = '600 12px Inter, "Segoe UI", Arial, sans-serif';
      let cursorMoving = false;
      for (const [id, c] of cursors) {
        if (now - c.at > 6000) { cursors.delete(id); continue; }
        c.rx += (c.x - c.rx) * 0.35;
        c.ry += (c.y - c.ry) * 0.35;
        if (Math.hypot(c.x - c.rx, c.y - c.ry) > 0.5) cursorMoving = true;
        const [sx, sy] = camera.toScreen(c.rx, c.ry);
        ctx.fillStyle = c.color;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + 12, sy + 5);
        ctx.lineTo(sx + 5, sy + 12);
        ctx.closePath();
        ctx.fill();
        const label = `${c.role === 'editor' ? '✎ ' : '▶ '}${c.name}`;
        const padX = 6, w = ctx.measureText(label).width + padX * 2;
        ctx.beginPath();
        ctx.roundRect(sx + 10, sy + 10, w, 20, 6);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.fillText(label, sx + 10 + padX, sy + 24);
      }
      ctx.restore();
      if (cursorMoving) schedule();
    }
    alivePanel.reposition();
    control.refresh();
    if (animating || moving) schedule();
    if (!started && store.allLoaded(wanted().filter(c => c.visible).map(c => [c.cx, c.cy]))) {
      started = true;
      loading.hidden = true;
    }
    lassoPanel.hidden = !lasso?.closed;
    if (lasso?.closed) {
      const xs = lasso.points.map(p => p[0]), ys = lasso.points.map(p => p[1]);
      const [sx, sy] = camera.toScreen((Math.min(...xs) + Math.max(...xs)) / 2, Math.max(...ys));
      // Keep the whole panel on screen: clamp by its real size, not a guess.
      const half = lassoPanel.offsetWidth / 2 + 8;
      lassoPanel.style.left = `${Math.max(half, Math.min(camera.viewW - half, sx))}px`;
      lassoPanel.style.top = `${Math.max(8, Math.min(camera.viewH - lassoPanel.offsetHeight - 8, sy + 12))}px`;
    }
    refreshText();
  }
  const schedule = () => { if (!frame && alive) frame = requestAnimationFrame(render); };
  const wanted = () => visibleChunks(camera, limits.maxChunks);

  let hashTimer = 0;
  function cameraChanged() {
    const chunks = wanted().map(c => [c.cx, c.cy]);
    sync.setViewport(chunks);
    entities.retain(chunks);
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
  let mode = null, spaceDown = false, pinch = null, grab = null, lastClick = { id: null, at: 0 };
  const canPlay = config.role === 'editor' || config.role === 'player';
  if (config.kind === 'private') {
    showCursors = true;
    cursorBtn = element('button', { class: 'button quiet', type: 'button', onclick: () => {
      showCursors = !showCursors;
      cursorBtn.textContent = t(showCursors ? 'cursors.hide' : 'cursors.show');
      schedule();
    } }, t('cursors.hide'));
    bar.insertBefore(cursorBtn, bar.children[2]);
  }
  const draggable = new Set(['food', 'explosive', 'remote', 'doodle']);
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
    // With the hand, a press on something alive plays with it instead of panning.
    const touched = hand && canPlay && !spaceDown && event.button === 0 ? entities.at(...camera.toWorld(...local(event))) : null;
    if (touched) {
      mode = 'entity';
      grab = { id: touched.id, type: touched.type, start: local(event), moved: false, sentAt: 0 };
    } else if (hand || !canEdit || config.frozen) mode = 'pan';
    else if (event.button !== 0) mode = null;
    else if (tools.state.tool === 'draw') {
      mode = 'draw';
      active = { color: tools.state.color, width: tools.state.width, points: [] };
      capture(event, active.points);
    } else {
      mode = 'lasso';
      lasso = { points: [], closed: false };
      alivePanel.clearError();
      capture(event, lasso.points);
    }
    if (mode) event.preventDefault();
  }

  let lastCursorSent = 0;
  canvas.addEventListener('pointermove', event => {
    if (config.kind !== 'private' || !canPlay) return;
    const nowMs = performance.now();
    if (nowMs - lastCursorSent < 50) return;
    lastCursorSent = nowMs;
    const [wx, wy] = camera.toWorld(...local(event));
    sync.send('cursor:move', { x: wx, y: wy });
  });

  function onMove(event) {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    const now = local(event);
    pointers.set(event.pointerId, now);
    if (mode === 'draw' && active) capture(event, active.points);
    else if (mode === 'lasso' && lasso) capture(event, lasso.points);
    else if (mode === 'entity' && grab) {
      if (!grab.moved && Math.hypot(now[0] - grab.start[0], now[1] - grab.start[1]) > 6) {
        grab.moved = true;
        // Only some things can be carried; anything else lets the press become a pan.
        if (!draggable.has(grab.type)) { mode = 'pan'; grab = null; return; }
      }
      if (grab.moved) dragTo(camera.toWorld(...now), false);
    } else if (mode === 'pan') { camera.panBy(now[0] - previous[0], now[1] - previous[1]); cameraChanged(); }
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
    else if (mode === 'entity' && grab) {
      const point = camera.toWorld(...local(event));
      if (grab.moved) dragTo(point, true);
      else click(grab, point);
      grab = null;
    }
    // Lifting one of two fingers keeps panning with the other.
    mode = mode === 'pinch' && pointers.size === 1 ? 'pan' : pointers.size ? mode : null;
    schedule();
  }

  function dragTo([x, y], last) {
    const entity = entities.entities.get(grab.id);
    if (!entity) return;
    // Show it under the cursor at once; the server confirms a few times a second.
    entity.x = entity.rx = x; entity.y = entity.ry = y;
    const now = performance.now();
    if (last || now - grab.sentAt >= DRAG_SEND_MS) {
      grab.sentAt = now;
      sync.request('entity:interact', { entityId: grab.id, action: 'drag', to: [x, y] }).catch(() => {});
    }
    schedule();
  }

  // A click pets (or takes a remote); a double click gives a shove away from the cursor.
  function click(target, point) {
    const now = performance.now();
    if (target.type === 'remote') { control.claim(target.id); return; }
    const double = lastClick.id === target.id && now - lastClick.at < DOUBLE_CLICK_MS;
    lastClick = { id: target.id, at: now };
    sync.request('entity:interact', double
      ? { entityId: target.id, action: 'push', to: point }
      : { entityId: target.id, action: 'pet' }).catch(() => {});
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
  const stopLocale = onLocaleChange(() => { localize(page); refreshLanguageSwitch(page); tools.refresh(); alivePanel.refresh(); control.refresh(); refreshText(); schedule(); });

  // Deep link first, else the server's spawn point.
  const rect = stage.getBoundingClientRect();
  camera.resize(rect.width, rect.height);
  const link = parseHash(location.hash);
  camera.centerOn(link?.x ?? config.spawn.x, link?.y ?? config.spawn.y, link?.z ?? 1);
  resize();
  sync.start();

  return () => {
    alive = false;
    control.release();
    control.destroy();
    sync.stop();
    alivePanel.destroy();
    cancelAnimationFrame(frame);
    clearTimeout(hashTimer);
    observer.disconnect();
    stopLocale();
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('keyup', onKeyUp);
    window.removeEventListener('beforeunload', beforeUnload);
  };
}

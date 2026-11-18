// Driving: claim a remote, steer with WASD / arrows or the on-screen stick, let go.
import { getLocale, t } from '../i18n/i18n.js';
import { element, toast } from './dom.js';

const SEND_MS = 50;         // 20 inputs a second
const LEASE_MS = 60000;
const KEYS = { KeyW: [0, -1], ArrowUp: [0, -1], KeyS: [0, 1], ArrowDown: [0, 1], KeyA: [-1, 0], ArrowLeft: [-1, 0], KeyD: [1, 0], ArrowRight: [1, 0] };

export function createControl({ sync, stage, entities, onChange }) {
  let driving = null;       // { controllerId, vehicleId, until }
  let stick = [0, 0], sent = [0, 0], timer = 0;
  const held = new Set();

  const text = element('span', { class: 'control-text' });
  const knob = element('span', { class: 'stick-knob' });
  const pad = element('div', { class: 'stick', 'aria-hidden': 'true' }, knob);
  const bar = element('div', { class: 'control-bar', hidden: true }, text,
    element('button', { class: 'button quiet', type: 'button', 'data-i18n': 'control.release', onclick: release }));
  stage.append(bar, pad);
  pad.hidden = true;

  function vector() {
    let x = stick[0], y = stick[1];
    for (const code of held) { x += KEYS[code][0]; y += KEYS[code][1]; }
    const length = Math.hypot(x, y);
    return length > 1 ? [x / length, y / length] : [x, y];
  }

  function refresh() {
    bar.hidden = pad.hidden = !driving;
    if (!driving) return;
    const vehicle = entities.entities.get(driving.vehicleId);
    const label = vehicle?.label?.[getLocale()] || vehicle?.label?.en || '';
    text.textContent = t('control.driving', { label, s: Math.max(0, Math.ceil((driving.until - Date.now()) / 1000)) });
  }

  function tick() {
    if (!driving) return;
    const [ax, ay] = vector();
    // Send while the stick is held, and once more when it returns to rest.
    if (ax || ay || sent[0] || sent[1]) {
      sync.send('control:input', { ax, ay });
      if (ax || ay) driving.until = Date.now() + LEASE_MS;
      sent = [ax, ay];
    }
    refresh();
  }

  function start(grant) {
    driving = { controllerId: grant.controllerId, vehicleId: grant.vehicleId, until: grant.until };
    held.clear(); stick = [0, 0]; sent = [0, 0];
    clearInterval(timer);
    timer = setInterval(tick, SEND_MS);
    refresh(); onChange();
  }

  function stop() {
    driving = null;
    clearInterval(timer);
    held.clear(); stick = [0, 0];
    knob.style.transform = '';
    refresh(); onChange();
  }

  async function claim(controllerId) {
    try {
      const reply = await sync.request('control:claim', { controllerId });
      if (reply.ok) start(reply);
      else if (reply.unbound) toast(t('control.unbound'));
      else if (reply.error?.code === 'CONTROL_TAKEN') {
        const { name, seconds } = reply.error.details;
        toast(name ? t('control.taken', { name, s: seconds }) : t('control.taken.anon', { s: seconds }), 4000);
      } else toast(t('err.INTERNAL'));
    } catch { toast(t('err.NETWORK')); }
  }

  function release() {
    if (!driving) return;
    sync.request('control:release', {}).catch(() => {});
    stop();
  }

  function onEvent(name, data) {
    if (name === 'control:revoked' && driving && (!data.controllerId || data.controllerId === driving.controllerId)) {
      if (data.reason === 'unbound') toast(t('control.unbound'));
      stop();
    }
  }

  const typing = event => /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
  const onKeyDown = event => {
    if (!driving || !KEYS[event.code] || typing(event)) return;
    event.preventDefault();
    held.add(event.code);
  };
  const onKeyUp = event => { held.delete(event.code); };
  const onBlur = () => held.clear();
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', onBlur);

  // The on-screen stick, for touch.
  let stickPointer = null;
  const moveStick = event => {
    const rect = pad.getBoundingClientRect(), radius = rect.width / 2;
    let x = (event.clientX - rect.left - radius) / radius, y = (event.clientY - rect.top - radius) / radius;
    const length = Math.hypot(x, y);
    if (length > 1) { x /= length; y /= length; }
    stick = [x, y];
    knob.style.transform = `translate(${x * radius * 0.6}px, ${y * radius * 0.6}px)`;
  };
  pad.addEventListener('pointerdown', event => { stickPointer = event.pointerId; pad.setPointerCapture(stickPointer); moveStick(event); });
  pad.addEventListener('pointermove', event => { if (event.pointerId === stickPointer) moveStick(event); });
  for (const type of ['pointerup', 'pointercancel']) {
    pad.addEventListener(type, () => { stickPointer = null; stick = [0, 0]; knob.style.transform = ''; });
  }

  return {
    claim, release, onEvent, refresh,
    get vehicleId() { return driving?.vehicleId || null; },
    destroy() {
      clearInterval(timer);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    },
  };
}

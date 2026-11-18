// The adblock gate and the ad slots around the canvas.
import { countMetric } from '../api.js';
import { detectAdblock, slotsFor } from '../ads.js';
import { brand, languageSwitch } from './chrome.js';
import { element, localize } from './dom.js';

const count = key => countMetric(key).catch(() => {});

const browserProbe = {
  loadScript: url => new Promise((resolve, reject) => {
    const script = element('script', { src: url, async: true });
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  }),
  makeBait() {
    const bait = element('div', { class: 'adsbox ad-banner', 'aria-hidden': 'true' });
    bait.style.cssText = 'position:absolute;left:-999px;width:1px;height:1px';
    document.body.append(bait);
    return {
      hidden: () => !bait.isConnected || bait.offsetHeight === 0 || getComputedStyle(bait).display === 'none',
      remove: () => bait.remove(),
    };
  },
  wait: ms => new Promise(resolve => setTimeout(resolve, ms)),
};

// Resolves once ads can be shown. Until then nothing of the world is mounted or connected.
export async function passGate(root, probe = browserProbe) {
  if (!await detectAdblock(probe)) return;
  count('adblock_gate_shown');
  await new Promise(resolve => {
    const retry = element('button', { class: 'button', type: 'button', 'data-i18n': 'gate.retry', onclick: async () => {
      retry.disabled = true;
      const blocked = await detectAdblock(probe);
      retry.disabled = false;
      if (blocked) return;
      count('adblock_gate_passed');
      resolve();
    } });
    const page = element('div', { class: 'home gate' },
      element('header', { class: 'site-header' }, brand(), languageSwitch()),
      element('main', { class: 'gate-body' },
        element('h1', { 'data-i18n': 'gate.title' }),
        element('p', { 'data-i18n': 'gate.body' }),
        element('p', { class: 'gate-perks', 'data-i18n': 'gate.perks' }),
        element('div', { class: 'gate-actions' }, retry,
          element('a', { class: 'button primary', href: '/plans', 'data-i18n': 'gate.plus', onclick: () => count('plus_clicked') }))));
    root.replaceChildren(page);
    localize(page);
  });
}

// Puts the ad slots around the stage. Returns nothing to clean up: the page owns them.
export function mountAds(body, stage, blocks) {
  for (const where of slotsFor(window.innerWidth)) {
    const blockId = where === 'bottom' ? blocks.bottom : blocks.side;
    if (!blockId) continue;
    const id = `ad-${where}`;
    const slot = element('aside', { class: `ad-slot ad-${where}`, 'data-i18n-label': 'ads.label' }, element('div', { id }));
    if (where === 'left') stage.before(slot); else if (where === 'right') stage.after(slot); else body.after(slot);
    window.yaContextCb = window.yaContextCb || [];
    window.yaContextCb.push(() => window.Ya?.Context?.AdvManager?.render({ blockId, renderTo: id }));
  }
}

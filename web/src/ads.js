// Ad slots and the adblock detector. Detection looks only at whether the ad loader and a
// bait element survive: an ad block that simply has nothing to show is NOT a block.
export const LOADER = 'https://yandex.ru/ads/system/context.js';
export const WIDE = 1100;

export const isBlocked = ({ scriptFailed, baitHidden }) => Boolean(scriptFailed || baitHidden);

// Desktop: vertical blocks left and right of the canvas. Narrow screens: one at the bottom.
export const slotsFor = width => (width >= WIDE ? ['left', 'right'] : ['bottom']);

export async function detectAdblock({ loadScript, makeBait, wait, delay = 1500 }) {
  let scriptFailed = false;
  // A loader still on its way after the delay is slow, not blocked.
  Promise.resolve().then(() => loadScript(LOADER)).catch(() => { scriptFailed = true; });
  const bait = makeBait();
  await wait(delay);
  const blocked = isBlocked({ scriptFailed, baitHidden: bait.hidden() });
  bait.remove();
  return blocked;
}

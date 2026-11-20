// Ad slots and the adblock detector. Detection looks only at whether the ad loader and a
// bait element survive: an ad block that simply has nothing to show is NOT a block.
export const LOADER = 'https://yandex.ru/ads/system/context.js';
export const WIDE = 1100;

export const isBlocked = ({ scriptFailed, baitHidden }) => Boolean(scriptFailed || baitHidden);

// Desktop: a rail left and right of the canvas, each with two blocks stacked. Narrow screens:
// one block at the bottom. The slots' sizes come from CSS alone, so a banner that arrives
// (or never does) can't move the canvas.
export const RAIL_SLOTS = 2;
export function slotsFor(width) {
  if (width < WIDE) return [{ where: 'bottom', block: 'bottom', n: 1 }];
  const rail = (where, first) => Array.from({ length: RAIL_SLOTS }, (_, i) => ({ where, block: 'side', n: first + i }));
  return [...rail('left', 1), ...rail('right', 1 + RAIL_SLOTS)];
}

// The RSYa (Yandex Advertising Network) render call for one slot. The same block shown several times on a page needs a
// distinct pageNumber and container for each showing.
export const renderParams = (blockId, slot) => ({
  blockId, renderTo: `yandex_rtb_${blockId}-${slot.n}`, pageNumber: slot.n,
});

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

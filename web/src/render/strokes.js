// Paints the static layer: cached chunk tiles, then unsaved strokes and the lasso.
import { renderStroke } from '../draw.js';
import { t } from '../i18n/i18n.js';
import { drawEntities } from './entities.js';
import { drawGoo } from './goo.js';

const OUTSIDE = '#e7e4da', PAPER = '#ffffff', INK = '#252722';

// Returns true while something is animating and another frame is needed.
export function drawWorld(ctx, { camera, store, dpr, pending = [], lasso = null, entities = null, goos = null, now = 0 }) {
  const { x, y, z, viewW, viewH, bounds, chunkSize: size } = camera;
  const k = z * dpr;
  const px = wx => Math.round((wx - x) * k), py = wy => Math.round((wy - y) * k);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = OUTSIDE;
  ctx.fillRect(0, 0, Math.ceil(viewW * dpr), Math.ceil(viewH * dpr));
  ctx.fillStyle = PAPER;
  ctx.fillRect(px(0), py(0), px(bounds.w) - px(0), py(bounds.h) - py(0));

  const x0 = Math.max(0, Math.floor(x / size)), y0 = Math.max(0, Math.floor(y / size));
  const x1 = Math.min(Math.ceil(bounds.w / size), Math.ceil((x + viewW / z) / size));
  const y1 = Math.min(Math.ceil(bounds.h / size), Math.ceil((y + viewH / z) / size));
  // Sharper tiles when zoomed in or on dense screens, while few chunks are visible.
  const scale = k > 1.25 && (x1 - x0) * (y1 - y0) <= 4 ? 2 : 1;
  for (let cy = y0; cy < y1; cy++) {
    for (let cx = x0; cx < x1; cx++) {
      const tile = store.tile(cx, cy, scale);
      if (!tile) continue;
      const left = px(cx * size), top = py(cy * size);
      ctx.drawImage(tile, left, top, px((cx + 1) * size) - left, py((cy + 1) * size) - top);
    }
  }

  let animating = entities ? drawEntities(ctx, { camera, entities, dpr, now }) : false;

  ctx.setTransform(k, 0, 0, k, -x * k, -y * k);
  for (const stroke of pending) renderStroke(ctx, stroke);
  for (const goo of goos ? [...goos.values()] : []) {
    ctx.setTransform(k, 0, 0, k, -x * k, -y * k);
    if (!drawGoo(ctx, goo, now)) { goos.delete(goo.jobId); continue; }
    animating = true;
    if (goo.ending) continue;
    // What the server is doing, under the blob, at a constant screen size.
    const stage = goo.stage === 'classify' ? t('goo.stage.classify') : goo.stage === 'rig' ? t('goo.stage.rig') : '';
    ctx.setTransform(dpr, 0, 0, dpr, (goo.center[0] - x) * k, (goo.box[3] - y) * k + 8 * dpr);
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillStyle = INK;
    ctx.font = '700 14px Inter, "Segoe UI", Arial, sans-serif';
    ctx.fillText(t('goo.title'), 0, 0);
    ctx.font = '500 12px Inter, "Segoe UI", Arial, sans-serif';
    if (stage) ctx.fillText(stage, 0, 18);
  }
  ctx.setTransform(k, 0, 0, k, -x * k, -y * k);
  if (lasso?.points.length > 1) {
    ctx.beginPath();
    ctx.moveTo(lasso.points[0][0], lasso.points[0][1]);
    for (const [lx, ly] of lasso.points.slice(1)) ctx.lineTo(lx, ly);
    if (lasso.closed) {
      ctx.closePath();
      ctx.fillStyle = 'rgba(255, 220, 85, 0.18)';
      ctx.fill();
    }
    ctx.lineWidth = 2 / z;
    ctx.lineJoin = 'round';
    ctx.setLineDash([8 / z, 6 / z]);
    ctx.strokeStyle = INK;
    ctx.stroke();
    ctx.setLineDash([]);
  }
  return animating;
}

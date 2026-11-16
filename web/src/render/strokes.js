// Paints the static layer: cached chunk tiles, then unsaved strokes and the lasso.
import { renderStroke } from '../draw.js';

const OUTSIDE = '#e7e4da', PAPER = '#ffffff', INK = '#252722';

export function drawWorld(ctx, { camera, store, dpr, pending = [], lasso = null }) {
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

  ctx.setTransform(k, 0, 0, k, -x * k, -y * k);
  for (const stroke of pending) renderStroke(ctx, stroke);
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
}

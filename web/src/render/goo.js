// Fairy goo: an animated blob over the lassoed area while the server works.
const POP_MS = 350, FIZZLE_MS = 700;

// Smooth closed outline through the polygon, each vertex breathing in and out.
function blob(ctx, goo, t, grow) {
  const points = goo.polygon, n = points.length;
  const [cx, cy] = goo.center;
  const wobbled = points.map(([x, y], i) => {
    const f = grow * (1 + 0.035 * Math.sin(t * 3.1 + i * 1.7) + 0.02 * Math.sin(t * 5.3 + i * 0.6));
    return [cx + (x - cx) * f, cy + (y - cy) * f];
  });
  const mid = i => [(wobbled[i][0] + wobbled[(i + 1) % n][0]) / 2, (wobbled[i][1] + wobbled[(i + 1) % n][1]) / 2];
  ctx.beginPath();
  ctx.moveTo(...mid(n - 1));
  for (let i = 0; i < n; i++) ctx.quadraticCurveTo(wobbled[i][0], wobbled[i][1], ...mid(i));
  ctx.closePath();
}

export function gooGeometry(polygon) {
  const xs = polygon.map(p => p[0]), ys = polygon.map(p => p[1]);
  const box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  return { box, center: [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2] };
}

// Draws one goo in world coordinates; returns false once its ending has played out.
export function drawGoo(ctx, goo, now) {
  const t = (now - goo.start) / 1000;
  let alpha = Math.min(1, t * 6), grow = 1;
  if (goo.ending) {
    const progress = (now - goo.endedAt) / (goo.ending === 'pop' ? POP_MS : FIZZLE_MS);
    if (progress >= 1) return false;
    // A pop bursts outwards; a fizzle sags and fades.
    grow = goo.ending === 'pop' ? 1 + 0.3 * progress : 1 - 0.25 * progress;
    alpha *= 1 - progress;
  }
  const [x0, y0, x1, y1] = goo.box;
  ctx.save();
  ctx.globalAlpha = alpha;
  blob(ctx, goo, t, grow);
  const paint = ctx.createLinearGradient(x0, y0, x1, y1);
  paint.addColorStop(0, 'rgba(160, 240, 110, 0.92)');
  paint.addColorStop(1, 'rgba(60, 200, 150, 0.92)');
  ctx.fillStyle = paint;
  ctx.fill();
  ctx.lineWidth = Math.max(2, (x1 - x0 + y1 - y0) / 120);
  ctx.strokeStyle = 'rgba(30, 130, 80, 0.9)';
  ctx.stroke();
  ctx.clip();
  // Bubbles rising inside the blob.
  const width = x1 - x0, height = y1 - y0;
  for (let i = 0; i < 9; i++) {
    const speed = 0.25 + (i % 4) * 0.08, phase = (t * speed + i * 0.37) % 1;
    const bx = x0 + width * (((i * 0.618) % 1) * 0.8 + 0.1) + Math.sin(t * 2 + i) * width * 0.02;
    const radius = Math.max(2, Math.min(width, height) * (0.03 + (i % 3) * 0.015));
    ctx.beginPath();
    ctx.arc(bx, y1 - phase * height, radius, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255, 255, 255, ${0.55 * (1 - phase)})`;
    ctx.fill();
  }
  ctx.restore();
  return true;
}

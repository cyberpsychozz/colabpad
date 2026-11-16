// Pure geometry and rendering shared by live drawing, restoration and replay.
export function pointerPoint(event, rect, camera) {
  const [x, y] = camera.toWorld(event.clientX - rect.left, event.clientY - rect.top);
  return [
    Math.max(0, Math.min(camera.bounds.w, x)),
    Math.max(0, Math.min(camera.bounds.h, y)),
    event.pointerType === 'mouse' || !event.pressure ? 0.5 : event.pressure,
  ];
}

function distanceToSegmentSquared(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = dx || dy ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))) : 0;
  return (p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2;
}

export function simplify(points, epsilon) {
  if (points.length < 3) return points.map(p => [...p]);
  const keep = new Uint8Array(points.length);
  keep[0] = keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];
  while (stack.length) {
    const [first, last] = stack.pop();
    let max = epsilon ** 2, index = -1;
    for (let i = first + 1; i < last; i++) {
      const distance = distanceToSegmentSquared(points[i], points[first], points[last]);
      if (distance > max) { max = distance; index = i; }
    }
    if (index !== -1) {
      keep[index] = 1;
      stack.push([first, index], [index, last]);
    }
  }
  return points.filter((_, i) => keep[i]);
}

export function preparePoints(points, epsilon, width, height) {
  const prepared = simplify(points, epsilon).map(([x, y, p]) => [
    Math.min(width, Math.max(0, Math.round(x * 10) / 10)),
    Math.min(height, Math.max(0, Math.round(y * 10) / 10)),
    Math.min(1, Math.max(0, Math.round(p * 1000) / 1000)),
  ]);
  return prepared.length === 1 ? [prepared[0], [...prepared[0]]] : prepared;
}

// One axis of the server's rule (app/models.py chunk_of): the range lies inside
// one chunk, borders included.
function fitsAxis(low, high, size) {
  const index = Math.floor((low + high) / 2 / size);
  return low >= index * size && high <= (index + 1) * size;
}

// Cut a polyline at chunk borders so that every piece lies inside one chunk.
// Neighbouring pieces share the cut point, which sits exactly on the border.
export function splitAtChunks(points, size) {
  const path = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], cuts = [];
    for (const axis of [0, 1]) {
      const low = Math.min(a[axis], b[axis]), high = Math.max(a[axis], b[axis]);
      for (let line = (Math.floor(low / size) + 1) * size; line < high; line += size) {
        cuts.push([(line - a[axis]) / (b[axis] - a[axis]), axis, line]);
      }
    }
    cuts.sort((p, q) => p[0] - q[0]);
    for (const [t, axis, line] of cuts) {
      const cut = [
        Math.round((a[0] + (b[0] - a[0]) * t) * 10) / 10,
        Math.round((a[1] + (b[1] - a[1]) * t) * 10) / 10,
        Math.round((a[2] + (b[2] - a[2]) * t) * 1000) / 1000,
      ];
      cut[axis] = line;
      path.push(cut);
    }
    path.push(b);
  }
  const pieces = [];
  let piece = [path[0]], box = [path[0][0], path[0][1], path[0][0], path[0][1]];
  for (let i = 1; i < path.length; i++) {
    const p = path[i];
    const next = [Math.min(box[0], p[0]), Math.min(box[1], p[1]), Math.max(box[2], p[0]), Math.max(box[3], p[1])];
    if (fitsAxis(next[0], next[2], size) && fitsAxis(next[1], next[3], size)) {
      piece.push(p); box = next;
    } else {
      const last = piece.at(-1);
      pieces.push(piece);
      piece = [[...last], p];
      box = [Math.min(last[0], p[0]), Math.min(last[1], p[1]), Math.max(last[0], p[0]), Math.max(last[1], p[1])];
    }
  }
  pieces.push(piece);
  return pieces;
}

// World-space box a stroke can paint into: its points padded by the widest line.
export function strokeBounds(stroke) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of stroke.points) {
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  const pad = stroke.width * 0.75 + 1;
  return [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
}

export function splitStroke(stroke, maxPoints, maxBytes) {
  const result = [];
  let start = 0;
  while (start < stroke.points.length - 1) {
    let end = Math.min(stroke.points.length, start + maxPoints);
    let chunk = { ...stroke, points: stroke.points.slice(start, end) };
    // Leave space for the Socket.IO packet, UUID and future framing overhead.
    while (new TextEncoder().encode(JSON.stringify(chunk)).length + 256 > maxBytes) {
      end = start + Math.floor((end - start) / 2);
      if (end - start < 2) throw new Error('Payload limit is too small for a stroke');
      chunk = { ...stroke, points: stroke.points.slice(start, end) };
    }
    result.push(chunk);
    start = end - 1;
  }
  return result;
}

export function renderStroke(ctx, stroke) {
  const points = stroke.points;
  if (!points.length) return;
  ctx.strokeStyle = ctx.fillStyle = stroke.color;
  ctx.lineCap = ctx.lineJoin = 'round';
  const [firstX, firstY, firstPressure] = points[0];
  if (points.every(p => p[0] === firstX && p[1] === firstY)) {
    ctx.beginPath();
    ctx.arc(firstX, firstY, stroke.width * (0.5 + firstPressure) / 2, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  let start = points[0];
  for (let i = 1; i < points.length; i++) {
    const control = points[i - 1], point = points[i];
    const end = i === points.length - 1 ? point : [(control[0] + point[0]) / 2, (control[1] + point[1]) / 2];
    ctx.beginPath();
    ctx.moveTo(start[0], start[1]);
    ctx.lineWidth = stroke.width * (0.5 + (control[2] + point[2]) / 2);
    ctx.quadraticCurveTo(control[0], control[1], end[0], end[1]);
    ctx.stroke();
    start = end;
  }
}

export function fillBackground(ctx, width, height, background) {
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
}

export function configureCanvas(canvas, width, height, ratio = 1) {
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return ctx;
}

// Pure geometry and rendering shared by live drawing, restoration and replay.
export function pointerPoint(event, rect, width, height) {
  return [
    Math.max(0, Math.min(width, (event.clientX - rect.left) * width / rect.width)),
    Math.max(0, Math.min(height, (event.clientY - rect.top) * height / rect.height)),
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

export function splitStroke(stroke, maxPoints, maxBytes) {
  const result = [];
  let start = 0;
  while (start < stroke.points.length - 1) {
    let end = Math.min(stroke.points.length, start + maxPoints);
    let chunk = { ...stroke, points: stroke.points.slice(start, end) };
    // Leave space for the Socket.IO packet, UUID and future framing overhead.
    while (new TextEncoder().encode(JSON.stringify(chunk)).length + 256 > maxBytes) {
      end = start + Math.floor((end - start) / 2);
      if (end - start < 2) throw new Error('Лимит сообщения слишком мал для штриха');
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

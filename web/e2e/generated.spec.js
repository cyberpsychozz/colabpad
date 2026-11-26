// Text-directed animation: real UI, server pipeline, simulation and layered Canvas rendering.
import { test, expect } from '@playwright/test';

async function viewer(browser, locale, x = 15500, y = 8500) {
  const context = await browser.newContext({ locale, viewport: { width: 1100, height: 760 } });
  const page = await context.newPage(), events = [];
  page.on('websocket', ws => ws.on('framereceived', frame => {
    const text = String(frame.payload);
    if (text.startsWith('42[')) events.push(JSON.parse(text.slice(2)));
  }));
  await page.goto(`/w#x=${x}&y=${y}&z=1`);
  await expect(page.getByText(/^(Connected|На связи)$/)).toBeVisible();
  const box = await page.locator('canvas').boundingBox();
  const point = (wx, wy) => [box.x + box.width / 2 + wx - x, box.y + box.height / 2 + wy - y];
  return { page, context, events, x, y, point };
}

async function line(c, points) {
  await c.page.mouse.move(...c.point(...points[0])); await c.page.mouse.down();
  for (const p of points.slice(1)) await c.page.mouse.move(...c.point(...p), { steps: 5 });
  await c.page.mouse.up();
}
async function select(c) {
  await c.page.getByRole('button', { name: /^(Bring a drawing to life|Оживить рисунок)$/ }).click();
  await line(c, [[c.x-85,c.y-85],[c.x+85,c.y-85],[c.x+85,c.y+85],[c.x-85,c.y+85],[c.x-85,c.y-85]]);
}
async function drawing(c) {
  await c.page.getByRole('button', { name: /^(Draw|Рисовать)$/ }).click();
  // A torso and distinct left arm; the fake provider identifies the existing arm.
  await line(c, [[c.x-20,c.y-40],[c.x+35,c.y-40],[c.x+35,c.y+50],[c.x-20,c.y+50],[c.x-20,c.y-40]]);
  await line(c, [[c.x-20,c.y-25],[c.x-60,c.y+20],[c.x-65,c.y+40]]);
  await select(c);
}
async function submit(c, description) {
  await c.page.getByRole('textbox').fill(description);
  const [r] = await Promise.all([
    c.page.waitForResponse(r => r.url().endsWith('/alive') && r.request().method() === 'POST'),
    c.page.getByRole('button', { name: /^(ALIVE|ОЖИВИ)$/ }).click(),
  ]);
  expect(r.status()).toBe(202);
  return (await r.json()).jobId;
}
const spawned = (c, job) => c.events.find(([name,data]) => name === 'world:event' && data.type === 'spawn' && data.jobId === job)?.[1].entity;
async function entity(c, id) {
  const r = await c.page.request.get(`/api/canvases/world/chunks/${Math.floor(c.x/1024)}/${Math.floor(c.y/1024)}`);
  return (await r.json()).entities.find(e => e.id === id);
}

test('a spinning wheel leaves the crossing body line intact in both viewers', async ({ browser }, info) => {
  const a = await viewer(browser, 'en-US', 11500, 6500), b = await viewer(browser, 'ru-RU', 11500, 6500);
  await a.page.getByRole('button', { name: 'Draw', exact: true }).click();
  await line(a, [[a.x - 75, a.y - 12], [a.x + 75, a.y - 12]]);
  const circle = Array.from({ length: 49 }, (_, i) => {
    const angle = i * Math.PI * 2 / 48;
    return [a.x + 24 * Math.cos(angle), a.y + 12 + 24 * Math.sin(angle)];
  });
  await line(a, circle);
  await line(a, [[a.x, a.y + 12], [a.x + 20, a.y + 12]]);
  await select(a);
  const job = await submit(a, 'spin the wheel');
  await expect.poll(() => spawned(a, job)).toBeTruthy();
  await expect.poll(() => spawned(b, job)).toBeTruthy();
  // The vision fixture rejects both primary replies for invalid coordinates;
  // the configured fallback must finish without a second press of ALIVE.
  expect(a.events.some(([name, data]) => name === 'alive:failed' && data.jobId === job)).toBe(false);
  expect(a.events.filter(([name, data]) => name === 'alive:parts' && data.jobId === job)).toHaveLength(1);
  expect((await (await a.page.request.get('/api/me')).json()).quota.used).toBe(1);
  const e = spawned(a, job), layers = e.sprite.manifest.layers;
  expect(e.plan.tracks.every(track => track.target === 'roller')).toBe(true);
  expect(layers.find(l => l.id === 'body').size[0]).toBeGreaterThan(145);
  expect(layers.find(l => l.id === 'roller').size[0]).toBeLessThan(60);

  // Inspect actual atlas pixels: the off-center body segment is absent from the
  // wheel's layer even before any transform, and remains in the complete body.
  const alpha = await a.page.evaluate(async ({ sprite, origin, x, y, scale }) => {
    const image = new Image(); image.src = sprite.sheet; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    return sprite.manifest.layers.map(layer => {
      const [sx, sy, w, h] = layer.src;
      const px = Math.round((x - origin[0] - layer.offset[0]) * scale);
      const py = Math.round((y - origin[1] - layer.offset[1]) * scale);
      return [layer.id, px >= 0 && py >= 0 && px < w && py < h
        ? ctx.getImageData(sx + px, sy + py, 1, 1).data[3] : 0];
    });
  }, { sprite: e.sprite, origin: e.origin, x: a.x + 18, y: a.y - 12, scale: e.scale });
  expect(Object.fromEntries(alpha).body).toBeGreaterThan(200);
  expect(Object.fromEntries(alpha).roller).toBe(0);
  await expect.poll(async () => (await entity(a, e.id))?.view.playback.elapsed).toBeGreaterThan(400);

  async function bodyPixels(c) {
    const points = [-18, 18].map(dx => c.point(c.x + dx, c.y - 12));
    return c.page.locator('canvas').evaluate((canvas, points) => {
      const box = canvas.getBoundingClientRect(), ctx = canvas.getContext('2d');
      return points.every(([x, y]) => {
        const pixel = ctx.getImageData(Math.round((x - box.x) * canvas.width / box.width),
          Math.round((y - box.y) * canvas.height / box.height), 1, 1).data;
        return pixel[3] > 200 && pixel[0] < 80 && pixel[1] < 80 && pixel[2] < 80;
      });
    }, points);
  }
  await expect.poll(() => bodyPixels(a)).toBe(true);
  await expect.poll(() => bodyPixels(b)).toBe(true);
  await a.page.screenshot({ path: info.outputPath('wheel-author.png') });
  await b.page.screenshot({ path: info.outputPath('wheel-viewer.png') });
  await Promise.all([a.context.close(), b.context.close()]);
});

test('a closed head drawn from its top attaches at the neck and animates for both viewers', async ({ browser }, info) => {
  const a = await viewer(browser, 'en-US', 12500, 8500), b = await viewer(browser, 'ru-RU', 12500, 8500);
  await a.page.getByRole('button', { name: 'Draw', exact: true }).click();
  const head = Array.from({ length: 41 }, (_, i) => {
    const angle = i * Math.PI * 2 / 40;
    return [a.x + 18 * Math.sin(angle), a.y - 35 - 18 * Math.cos(angle)];
  });
  await line(a, head);
  await line(a, [[a.x, a.y - 17], [a.x, a.y + 55]]);
  await select(a);
  const job = await submit(a, 'nod your head');
  await expect.poll(() => spawned(a, job)).toBeTruthy();
  await expect.poll(() => spawned(b, job)).toBeTruthy();
  await expect(a.page.locator('.parts-preview')).toBeVisible();
  const e = spawned(a, job), headLayer = e.sprite.manifest.layers.find(l => l.id === 'head');
  expect(headLayer.pivot[1]).toBeGreaterThan(-25);
  expect(headLayer.pivot[1]).toBeLessThan(-10);
  expect(e.plan.tracks.every(track => track.target === 'head')).toBe(true);
  await expect.poll(async () => (await entity(a, e.id))?.view.playback.elapsed).toBeGreaterThan(300);
  expect((await entity(a, e.id)).x).toBe(e.origin[0]);
  expect((await entity(b, e.id)).y).toBe(e.origin[1]);
  await a.page.screenshot({ path: info.outputPath('closed-head-author.png') });
  await b.page.screenshot({ path: info.outputPath('closed-head-viewer.png') });
  await Promise.all([a.context.close(), b.context.close()]);
});

for (const [description, title] of [
  ['provider fallback', 'a motion provider HTTP 400 falls back and animates for both viewers'],
  ['provider schema', 'Gemini accepts the compact motion schema and animates for both viewers'],
]) test(title, async ({ browser }) => {
  const x = description === 'provider schema' ? 14500 : 14000;
  const a = await viewer(browser, 'en-US', x, 6500), b = await viewer(browser, 'ru-RU', x, 6500);
  await drawing(a);
  const job = await submit(a, description);
  await expect.poll(() => spawned(a, job)).toBeTruthy();
  await expect.poll(() => spawned(b, job)).toBeTruthy();
  const e = spawned(a, job);
  expect(e.mode).toBe('scripted_visual');
  await expect.poll(async () => (await entity(b, e.id))?.view.playback.elapsed).toBeGreaterThan(200);
  expect(a.events.some(([name, data]) => name === 'alive:failed' && data.jobId === job)).toBe(false);
  const me = await (await a.page.request.get('/api/me')).json();
  expect(me.quota.used).toBe(1);
  await Promise.all([a.context.close(), b.context.close()]);
});

for (const locale of ['en-US', 'ru-RU']) test(`generated performance, two viewers, late join, reload, pause and repeated playback (${locale})`, async ({ browser }, info) => {
  const x = locale === 'en-US' ? 15500 : 17500;
  const a = await viewer(browser,locale,x), b = await viewer(browser,locale === 'en-US' ? 'ru-RU':'en-US',x);
  await drawing(a);
  await a.page.screenshot({ path: info.outputPath('description.png') });
  const job = await submit(a,locale === 'en-US' ? 'wave one arm, jump and move' : 'помаши рукой, подпрыгни и пройди');
  await expect(a.page.locator('.parts-preview')).toBeVisible();
  await expect(b.page.locator('.parts-preview')).toBeHidden();
  const partName = locale === 'en-US' ? 'arm on the left' : 'рука слева';
  await a.page.getByRole('button', { name: partName, exact: true }).click();
  await expect(a.page.getByRole('button', { name: partName, exact: true })).toHaveAttribute('aria-pressed', 'true');
  await a.page.screenshot({ path: info.outputPath('author-parts.png') });
  await expect.poll(() => spawned(a,job)).toBeTruthy();
  const previewIndex = a.events.findIndex(([name, data]) => name === 'alive:parts' && data.jobId === job);
  const spawnIndex = a.events.findIndex(([name, data]) => name === 'world:event' && data.type === 'spawn' && data.jobId === job);
  expect(previewIndex).toBeGreaterThanOrEqual(0);
  expect(previewIndex).toBeLessThan(spawnIndex);
  expect(b.events.some(([name]) => name === 'alive:parts')).toBe(false);
  const e = spawned(a,job);
  const geometry = a.events[previewIndex][1];
  const layer = e.sprite.manifest.layers.find(l => l.id === 'left_arm');
  expect(layer.pivot[0]).toBeCloseTo((geometry.drawing.parts[0].pivot[0] * geometry.size[0] - geometry.size[0] / 2) / e.scale);
  await expect.poll(() => spawned(b,job)).toBeTruthy();
  expect(e.mode).toBe('scripted_visual');
  expect(e.sprite.manifest.layers.map(l=>l.id)).toContain('left_arm');
  await expect.poll(async () => (await entity(a,e.id))?.view.playback.elapsed).toBeGreaterThan(1500);
  const c = await viewer(browser,'en-US',x);
  const late = await entity(c,e.id);
  expect(late.view.playback.elapsed).toBeGreaterThan(1500);
  await a.page.reload();
  await expect(a.page.getByText(/^(Connected|На связи)$/)).toBeVisible();
  const reloaded = await entity(a,e.id);
  expect(reloaded.view.playback.elapsed).toBeGreaterThanOrEqual(late.view.playback.elapsed);
  // Saved screenshots are reviewed in both browser clients during implementation.
  await a.page.screenshot({ path: info.outputPath('author-midway.png') });
  await b.page.screenshot({ path: info.outputPath('viewer-midway.png') });
  const phase = current => Math.min(current.plan.duration,
    current.view.playback.elapsed % (current.plan.duration + current.view.playback.pauseMs));
  expect(e.view.playback.pauseMs).toBe(2000);
  await expect.poll(async () => phase(await entity(a,e.id)), { timeout: 16000 }).toBe(12000);
  const held = await entity(a,e.id);
  expect(held.view.playback.running).toBe(true);
  await expect.poll(async () => (await entity(b,e.id))?.x).toBe(held.x);
  expect(phase(await entity(c,e.id))).toBe(12000);
  await a.page.screenshot({ path: info.outputPath('author-pause.png') });
  await b.page.screenshot({ path: info.outputPath('viewer-pause.png') });
  const period = held.plan.duration + held.view.playback.pauseMs;
  await expect.poll(async () => (await entity(a,e.id))?.view.playback.elapsed, { timeout: 5000 }).toBeGreaterThan(period + 300);
  const second = await entity(a,e.id);
  expect(phase(second)).toBeLessThan(second.plan.duration);
  expect(second.x).toBeLessThan(held.x);
  expect(second.origin).toEqual(held.origin);
  await c.page.reload();
  await expect(c.page.getByText('Connected', {exact:true})).toBeVisible();
  expect((await entity(c,e.id)).view.playback.elapsed).toBeGreaterThan(period);
  await a.page.screenshot({ path: info.outputPath('author-repeat.png') });
  await b.page.screenshot({ path: info.outputPath('viewer-repeat.png') });
  await Promise.all([a.context.close(),b.context.close(),c.context.close()]);
});

for (const [description,error] of [
  ['unavailable',/unavailable|недоступ/i], ['invalid',/could not be prepared|подготовить/i],
  ['other actors',/other actors|персонажей/i],
]) test(`generation ${description} leaves drawing and allows editing and retry`, async ({browser}) => {
  const c = await viewer(browser,'en-US',14500,10000);
  await drawing(c);
  const job = await submit(c,description);
  await expect.poll(() => c.events.some(([name,data]) => name==='alive:failed' && data.jobId===job)).toBe(true);
  await expect(c.page.locator('.alive-error')).toContainText(error);
  await expect(c.page.locator('.parts-preview')).toBeVisible();
  await expect(c.page.getByRole('textbox')).toHaveValue(description);
  if (description === 'invalid') {
    await c.page.getByRole('button', {name:'RU',exact:true}).click();
    await expect(c.page.locator('.parts-preview summary')).toHaveText('Разметка рисунка');
    await expect(c.page.locator('.parts-preview-option')).toHaveText('рука слева');
    await expect(c.page.locator('.alive-error')).toContainText(/подготовить/);
    await expect(c.page.getByRole('textbox')).toHaveAttribute('aria-label','Что сделать? (необязательно)');
    await c.page.getByRole('button', {name:'EN',exact:true}).click();
    await expect(c.page.locator('.alive-error')).toContainText(error);
  }
  const quota = await (await c.page.request.get('/api/me')).json();
  expect(quota.quota.used).toBe(0);
  const r = await c.page.request.get(`/api/canvases/world/chunks/${Math.floor(c.x/1024)}/${Math.floor(c.y/1024)}`);
  expect((await r.json()).strokes.length).toBeGreaterThan(0);
  await c.context.close();
});

test('freeze, sleeping chunk and simulation-process recovery preserve progress', async ({browser}) => {
  const c = await viewer(browser,'en-US',11500,10000);
  await drawing(c);
  const job = await submit(c,'recovery');
  await expect.poll(() => spawned(c,job)).toBeTruthy();
  const e = spawned(c,job);
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.elapsed, {timeout:10000}).toBeGreaterThan(5500);
  expect((await c.page.request.post('/_test/freeze/true')).ok()).toBe(true);
  await expect(c.page.getByText(/This world is paused/)).toBeVisible();
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.running).toBe(false);
  const paused = (await entity(c,e.id)).view.playback.elapsed;
  await c.page.waitForTimeout(500);
  expect((await entity(c,e.id)).view.playback.elapsed).toBe(paused);
  expect((await c.page.request.post('/_test/freeze/false')).ok()).toBe(true);
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.running).toBe(true);
  await c.page.goto('/');
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.running).toBe(false);
  const sleeping = (await entity(c,e.id)).view.playback.elapsed;
  await c.page.waitForTimeout(700);
  expect((await entity(c,e.id)).view.playback.elapsed).toBe(sleeping);
  await c.page.goto(`/w#x=${c.x}&y=${c.y}&z=1`);
  await expect(c.page.getByText('Connected', {exact:true})).toBeVisible();
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.elapsed).toBeGreaterThan(sleeping);
  const before = await entity(c,e.id);
  expect((await c.page.request.post('/_test/restart-sim')).ok()).toBe(true);
  await expect.poll(async () => (await entity(c,e.id))?.view.playback.running).toBe(true);
  const recovered = await entity(c,e.id);
  expect(recovered.view.playback.elapsed).toBeGreaterThan(0);
  expect(recovered.view.playback.elapsed).toBeGreaterThanOrEqual(before.view.playback.elapsed-5100);
  expect(recovered.origin).toEqual(before.origin);
  expect(recovered.plan).toEqual(before.plan);
  // The stored origin, rather than the last saved position, anchors the trajectory.
  const rootX = recovered.plan.tracks.find(t=>t.target==='root').keys.at(-1).v;
  expect(Math.abs(recovered.x-(recovered.origin[0]+rootX*Math.min(1,recovered.view.playback.elapsed/12000)))).toBeLessThan(3);
  await c.context.close();
});

test('ordinary explosions leave the scripted drawing alive in both clients', async ({browser}) => {
  const a = await viewer(browser,'en-US',9500,6500), b = await viewer(browser,'ru-RU',9500,6500);
  await drawing(a);
  const job = await submit(a,'wave and move');
  await expect.poll(() => spawned(a,job)).toBeTruthy();
  const e = spawned(a,job);
  await expect.poll(() => spawned(b,job)).toBeTruthy();
  expect((await entity(a,e.id)).hp).toBe(100);
  // The second drawing uses the original empty-description ALIVE path.
  await drawing(a);
  await submit(a,'');
  await a.page.locator('.chooser').getByRole('button', {name:/Explosive/}).click();
  await expect.poll(() => b.events.some(([name,data]) => name==='world:event' && data.type==='explode'), {timeout:12000}).toBe(true);
  const after = await entity(a,e.id), remote = await entity(b,e.id);
  expect(after.mode).toBe('scripted_visual');
  expect(after.hp).toBe(100);
  expect(after.view.playback.elapsed).toBeGreaterThan(0);
  expect(remote.id).toBe(e.id);
  expect(remote.hp).toBe(100);
  expect(b.events.some(([name,data]) => name==='world:event' && data.type==='death' && data.id===e.id)).toBe(false);
  await Promise.all([a.context.close(),b.context.close()]);
});

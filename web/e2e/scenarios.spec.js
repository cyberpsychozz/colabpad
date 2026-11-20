// docs/spec.md §12 in two browser clients: A reads English, B reads Russian.
// Each test works in its own part of the shared world (the camera is set by the URL).
import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ROBOKASSA } from '../playwright.config.js';

const ALIVE = /^(ALIVE|ОЖИВИ)$/;
const TOOL = {
  draw: /^(Draw|Рисовать)$/,
  hand: /^(Move the world|Двигать мир)$/,
  alive: /^(Bring a drawing to life|Оживить рисунок)$/,
};

// A browser client: its own context (cookie, session, language) and the Socket.IO events
// it receives, parsed from the WebSocket frames.
async function client(browser, locale, place) {
  const context = await browser.newContext({ locale });
  const page = await context.newPage();
  const events = [];
  page.on('websocket', ws => ws.on('framereceived', frame => {
    const text = String(frame.payload);
    if (text.startsWith('42[')) events.push({ at: Date.now(), event: JSON.parse(text.slice(2)) });
  }));
  await page.goto(`/w#x=${place.x}&y=${place.y}&z=1`);
  await expect(page.getByRole('button', { name: TOOL.draw })).toBeVisible();
  await expect(page.getByText(/^(Connected|На связи)$/)).toBeVisible();
  const box = await page.locator('canvas').boundingBox();
  return {
    page, context, events, place, box,
    // World coordinates to page pixels: the URL sets the camera's centre, zoom 1.
    screen: (wx, wy) => [box.x + box.width / 2 + (wx - place.x), box.y + box.height / 2 + (wy - place.y)],
    named: name => events.filter(e => e.event[0] === name).map(e => ({ at: e.at, data: e.event[1] })),
  };
}

async function tool(c, name) {
  await c.page.getByRole('button', { name: TOOL[name] }).click();
}

// A stroke through world points, drawn with the real mouse (pointer events and all).
async function stroke(c, points) {
  const [x0, y0] = c.screen(...points[0]);
  await c.page.mouse.move(x0, y0);
  await c.page.mouse.down();
  for (const [wx, wy] of points.slice(1)) {
    const [x, y] = c.screen(wx, wy);
    await c.page.mouse.move(x, y, { steps: 4 });
  }
  await c.page.mouse.up();
}

const ring = (cx, cy, rx, ry, n = 28) =>
  Array.from({ length: n + 1 }, (_, i) => [cx + rx * Math.cos((i / n) * 2 * Math.PI), cy + ry * Math.sin((i / n) * 2 * Math.PI)]);

async function draw(c, strokes) {
  await tool(c, 'draw');
  for (const points of strokes) await stroke(c, points);
}

// Lasso a drawing, press ALIVE and pick the type from the grid (the VLM is off).
async function bringToLife(c, lasso, typeName) {
  await tool(c, 'alive');
  await stroke(c, lasso);
  const pressed = Date.now();
  const [response] = await Promise.all([
    c.page.waitForResponse(r => r.url().endsWith('/alive') && r.request().method() === 'POST'),
    c.page.getByRole('button', { name: ALIVE }).click(),
  ]);
  if (response.status() !== 202) return { response, pressed };
  const chip = c.page.locator('.chooser').getByRole('button', { name: typeName });
  await chip.click({ timeout: 15_000 });
  const { jobId } = await response.json();
  await expect.poll(() => c.named('world:event').find(e => e.data.type === 'spawn' && e.data.jobId === jobId), { timeout: 20_000 }).toBeTruthy();
  return { response, pressed, jobId, entity: c.named('world:event').find(e => e.data.jobId === jobId).data.entity };
}

async function chunkEntities(c, wx, wy) {
  const r = await c.page.request.get(`/api/canvases/world/chunks/${Math.floor(wx / 1024)}/${Math.floor(wy / 1024)}`);
  return (await r.json()).entities;
}

const inkAt = (c, wx, wy) => c.page.evaluate(([x, y]) => {
  const canvas = document.querySelector('canvas'), rect = canvas.getBoundingClientRect();
  const k = canvas.width / rect.width;
  const data = canvas.getContext('2d').getImageData(Math.round((x - rect.left) * k) - 3, Math.round((y - rect.top) * k) - 3, 7, 7).data;
  let dark = 0;
  for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 0 && data[i] + data[i + 1] + data[i + 2] < 300) dark++;
  return dark;
}, c.screen(wx, wy));

test('two clients draw together; ALIVE shows goo to both and, with the VLM down, the grid still brings it to life', async ({ browser }, info) => {
  const place = { x: 2560, y: 2560 };
  const a = await client(browser, 'en-US', place), b = await client(browser, 'ru-RU', place);
  await expect(a.page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(b.page.locator('html')).toHaveAttribute('lang', 'ru');

  // A draws a cow; B sees every stroke arrive and painted.
  const cow = [ring(2560, 2560, 70, 40), [[2510, 2595], [2508, 2645]], [[2540, 2600], [2540, 2650]],
    [[2580, 2600], [2582, 2650]], [[2610, 2595], [2612, 2645]], ring(2655, 2530, 25, 20)];
  await draw(a, cow);
  await expect.poll(() => b.named('stroke:new').length, { timeout: 10_000 }).toBe(cow.length);
  await expect.poll(() => inkAt(b, 2540, 2630)).toBeGreaterThan(0);

  // ALIVE: both see the goo at once; there is no VLM, so A picks the type.
  const { response, pressed, entity } = await bringToLife(a, ring(2575, 2580, 135, 95, 40), /Creature/);
  expect(response.status()).toBe(202);
  const gooAtB = b.named('alive:goo')[0];
  expect(gooAtB, 'B saw the goo').toBeTruthy();
  expect(gooAtB.at - pressed).toBeLessThan(1500);
  expect(entity.type).toBe('creature');
  // The spawn reaches B too, and the name tag is in each client's own language.
  await expect.poll(() => b.named('world:event').some(e => e.data.type === 'spawn' && e.data.entity.id === entity.id)).toBeTruthy();
  expect(entity.label).toEqual({ ru: expect.any(String), en: expect.any(String) });
  // Its strokes are consumed, not deleted: the server still has them.
  const chunk = await (await a.page.request.get('/api/canvases/world/chunks/2/2')).json();
  expect(chunk.fates.filter(f => f.fate === 'consumed').length).toBe(cow.length);
  expect(chunk.strokes.length).toBeGreaterThanOrEqual(cow.length);
  // It lives: within a few seconds B gets world:delta rows for it, by its short handle.
  await expect.poll(() => b.named('world:delta').some(d => d.data.chunks.some(ch => ch.upd.some(row => row[0] === entity.ref))), { timeout: 15_000 }).toBeTruthy();
  await info.attach('client A (en)', { body: await a.page.screenshot(), contentType: 'image/png' });
  await info.attach('client B (ru)', { body: await b.page.screenshot(), contentType: 'image/png' });
  await a.context.close(); await b.context.close();
});

test('the third ALIVE of the day says ✨ ran out, with the sign-in hint and a Russian plural', async ({ browser }) => {
  // The e2e config allows an anonymous visitor 2 a day (20 once signed in).
  const place = { x: 4608, y: 2560 };
  const b = await client(browser, 'ru-RU', place);
  for (const [n, x] of [[1, 4508], [2, 4608]]) {
    await draw(b, [ring(x, 2560, 25, 25)]);
    const { response } = await bringToLife(b, ring(x, 2560, 45, 45), /Живая каракуля/);
    expect(response.status(), `ALIVE ${n}`).toBe(202);
  }
  await draw(b, [ring(4708, 2560, 25, 25)]);
  const { response } = await bringToLife(b, ring(4708, 2560, 45, 45), /Живая каракуля/);
  expect(response.status()).toBe(429);
  await expect(b.page.locator('.alive-error')).toHaveText('✨ на сегодня закончились. Войди — и получишь ещё 18 оживлений');
  await expect(b.page.getByText('Осталось ✨ сегодня: 0 из 2')).toBeVisible();
  await b.context.close();
});

test('a bomb counts down, explodes and destroys the strokes around it (as fates); far strokes stay', async ({ browser }) => {
  const place = { x: 8704, y: 2560 };
  const a = await client(browser, 'en-US', place), b = await client(browser, 'ru-RU', place);
  await draw(a, [[[8634, 2550], [8664, 2550]], [[8904, 2550], [8944, 2550]]]);  // near, far
  await draw(a, [ring(8704, 2560, 22, 22), [[8714, 2540], [8724, 2525]]]);
  const bomb = (await bringToLife(a, ring(8709, 2555, 40, 45, 30), /Explosive/)).entity;
  // The fuse counts down for everyone (4 s in the e2e config), then it blows.
  await expect.poll(() => b.named('world:event').find(e => e.data.type === 'explode' && e.data.id === bomb.id), { timeout: 20_000 }).toBeTruthy();
  await expect.poll(() => b.named('stroke:fate').some(e => e.data.fate === 'destroyed')).toBeTruthy();
  const chunk = await (await a.page.request.get('/api/canvases/world/chunks/8/2')).json();
  const fateOf = seq => chunk.fates.filter(f => f.seq === seq).map(f => f.fate);
  const near = chunk.strokes.find(s => Math.abs(s.points[0][0] - 8634) < 3);
  const far = chunk.strokes.find(s => Math.abs(s.points[0][0] - 8904) < 3);
  expect(fateOf(near.seq)).toEqual(['destroyed']);
  expect(fateOf(far.seq)).toEqual([]);
  await a.context.close(); await b.context.close();
});

test('switching RU ↔ EN changes every visible string, the page language and the title', async ({ browser }) => {
  const c = await client(browser, 'en-US', { x: 11000, y: 3000 });
  // Every visible string and every accessible name (the canvas itself holds no text).
  const chrome = () => c.page.evaluate(() => document.body.innerText + ' ' +
    [...document.querySelectorAll('[aria-label]')].map(x => x.getAttribute('aria-label')).join(' '));
  expect(await chrome()).not.toMatch(/[а-яё]/i);
  const enTitle = await c.page.title();
  await c.page.getByRole('button', { name: 'RU' }).click();
  await expect(c.page.locator('html')).toHaveAttribute('lang', 'ru');
  expect(await c.page.title()).not.toBe(enTitle);
  const ru = await chrome();
  // Only the brand, the language switch and colour codes stay Latin.
  expect(ru.replace(/collab|pad|CollabPad|RU|EN|#[0-9A-F]{6}/g, '')).not.toMatch(/[a-z]{2,}/i);
  await c.context.close();
});

// §9 ad layout. Yandex is never contacted: /api/me is answered as if ads were on, and the RSYa
// loader is a stub that fills each container late with an oversized banner, as a real one may.
const AD_BLOCK = 'R-A-e2e-1';
const AD_LOADER = `window.adCalls = [];
  window.Ya = { Context: { AdvManager: { render(params) {
    // Like the real loader, the container is looked up at once, so it must be in the page.
    const container = document.getElementById(params.renderTo);
    window.adCalls.push({ ...params, found: Boolean(container) });
    setTimeout(() => {
      const banner = document.createElement('div');
      banner.style.cssText = 'width:400px;height:900px;background:#fc0';
      container?.append(banner);
    }, 300);
  } } } };
  for (const run of window.yaContextCb || []) run();
  window.yaContextCb = { push: run => run() };`;

async function adClient(browser, ads, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ locale: 'en-US', viewport });
  const page = await context.newPage();
  await page.route('https://yandex.ru/ads/system/context.js', route =>
    route.fulfill({ contentType: 'application/javascript', body: AD_LOADER }));
  await page.route('**/api/me', async route => {
    const me = await (await route.fetch()).json();
    await route.fulfill({ json: { ...me, ads, adBlocks: { side: AD_BLOCK, bottom: AD_BLOCK } } });
  });
  await page.goto('/w#x=13000&y=3000&z=1');
  await expect(page.getByRole('button', { name: TOOL.draw })).toBeVisible();
  return { page, context };
}

test('a free viewer gets four fixed banners around the canvas, and a banner arriving never moves the canvas', async ({ browser }) => {
  const free = await adClient(browser, true);
  const slots = free.page.locator('.ad-rail .ad-slot');
  await expect(slots).toHaveCount(4);
  await expect(free.page.locator('.ad-left .ad-slot')).toHaveCount(2);
  await expect(free.page.locator('.ad-right .ad-slot')).toHaveCount(2);
  const canvas = await free.page.locator('canvas').boundingBox();
  const boxes = await slots.evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON()));
  // Every banner arrived, each in its own container with its own page number.
  await expect.poll(() => free.page.evaluate(() => document.querySelectorAll('.ad-slot div > div').length)).toBe(4);
  const calls = await free.page.evaluate(() => window.adCalls);
  expect(calls.map(c => c.pageNumber)).toEqual([1, 2, 3, 4]);
  expect(calls.every(c => c.found)).toBe(true);
  expect(calls.every(c => c.blockId === AD_BLOCK)).toBe(true);
  // The oversized banners changed nothing: not the canvas, not the slots.
  expect(await free.page.locator('canvas').boundingBox()).toEqual(canvas);
  expect(await slots.evaluateAll(els => els.map(el => el.getBoundingClientRect().toJSON()))).toEqual(boxes);
  // Left rail, canvas, right rail, side by side; two slots stacked in each rail.
  expect(boxes[0].right).toBeLessThanOrEqual(canvas.x);
  expect(boxes[2].left).toBeGreaterThanOrEqual(canvas.x + canvas.width);
  expect(boxes[1].top).toBeGreaterThanOrEqual(boxes[0].bottom);
  expect(boxes[0].width).toBeGreaterThan(150);
  expect(boxes[0].height).toBeGreaterThan(250);
  await expect(slots.first()).toHaveAttribute('aria-label', 'Advertisement');

  // A Plus viewer sees no ad space at all: the canvas takes the whole width.
  const plus = await adClient(browser, false);
  await expect(plus.page.locator('.ad-slot')).toHaveCount(0);
  expect((await plus.page.locator('canvas').boundingBox()).width).toBe(1440);
  expect(await plus.page.evaluate(() => window.adCalls)).toBeUndefined();

  // A narrow screen gets one fixed block below the canvas.
  const phone = await adClient(browser, true, { width: 390, height: 800 });
  await expect(phone.page.locator('.ad-slot')).toHaveCount(1);
  await expect(phone.page.locator('.ad-bottom')).toHaveCount(1);
  const phoneCanvas = await phone.page.locator('canvas').boundingBox();
  await expect.poll(() => phone.page.evaluate(() => document.querySelectorAll('.ad-slot div > div').length)).toBe(1);
  expect(await phone.page.locator('canvas').boundingBox()).toEqual(phoneCanvas);
  for (const c of [free, plus, phone]) await c.context.close();
});

// §12.10, the payment part: Robokassa itself is never contacted. The browser is stopped on its
// way to Robokassa's page, and the test posts the signed ResultURL notice Robokassa would send.
const md5 = text => createHash('md5').update(text).digest('hex');
const PAY_PAGE = 'https://auth.robokassa.ru/Merchant/Index.aspx';

async function signIn(page, email) {
  await page.locator('#account-email').fill(email);
  await page.locator('#account-consent').check();
  await page.locator('form button[type=submit]').click();
  await expect(page.locator('#account-code')).toBeVisible();
  // No SMTP in the e2e server: the letter is in the server's log.
  const log = join(process.env.E2E_DIR, 'server.log');
  let code;
  await expect.poll(() => {
    const text = readFileSync(log, 'utf8');
    const letter = text.slice(text.lastIndexOf(`Mail to ${email}`));
    code = letter.startsWith('Mail to') ? letter.match(/\b(\d{6})\b/)?.[1] : undefined;
    return code;
  }).toBeTruthy();
  await Promise.all([page.waitForEvent('load'), page.locator('#account-code').fill(code)]);
}

test('buying Plus sends a signed test-mode Robokassa link; its ResultURL notice makes Plus active, in rubles in both languages', async ({ browser, request }) => {
  // A buys a month in English, B a year in Russian. Prices are rubles in both.
  const buyers = [['en-US', 'en', /^Month — /, '299.00', /Done! Plus is active until/, /Payment goes through Robokassa/, /₽299 a month/],
                  ['ru-RU', 'ru', /^Год — /, '2490.00', /Готово! «Плюс» активен до/, /Оплата проходит через Robokassa/, /299\s₽\sв месяц/]];
  for (const [locale, culture, button, outSum, active, note, price] of buyers) {
    const context = await browser.newContext({ locale });
    const page = await context.newPage();
    let payment;
    await page.route('https://auth.robokassa.ru/**', route => {
      payment = new URL(route.request().url());
      return route.fulfill({ contentType: 'text/html', body: '<title>Robokassa (stub)</title>' });
    });
    await page.goto('/plans');
    await expect(page.getByText(note)).toBeVisible();
    await expect(page.getByText(price)).toBeVisible();
    await page.getByRole('button', { name: button }).click();  // not signed in: the account dialog
    await signIn(page, `buyer-${culture}-${Date.now()}@example.org`);
    await page.getByRole('button', { name: button }).click();
    await expect.poll(() => payment?.href).toBeTruthy();

    const link = Object.fromEntries(payment.searchParams);
    expect(payment.origin + payment.pathname).toBe(PAY_PAGE);
    expect(link).toMatchObject({ MerchantLogin: ROBOKASSA.login, OutSum: outSum, IsTest: '1', Culture: culture });
    expect(link.SignatureValue).toBe(md5(`${ROBOKASSA.login}:${outSum}:${link.InvId}:${link.Receipt}:${ROBOKASSA.password1}`));

    // Robokassa → ResultURL. A notice not signed with password #2 is refused.
    const forged = await request.post('/api/billing/result', { form: { OutSum: outSum, InvId: link.InvId, SignatureValue: md5(`${outSum}:${link.InvId}:${ROBOKASSA.password1}`) } });
    expect(forged.status()).toBe(400);
    const result = await request.post('/api/billing/result', { form: { OutSum: outSum, InvId: link.InvId, SignatureValue: md5(`${outSum}:${link.InvId}:${ROBOKASSA.password2}`).toUpperCase() } });
    expect(await result.text()).toBe(`OK${link.InvId}`);

    // Robokassa → SuccessURL: back on the site, Plus is active.
    await page.goto(`/billing/return?OutSum=${outSum}&InvId=${link.InvId}&Culture=${culture}`);
    await expect(page.getByText(active)).toBeVisible();
    await context.close();
  }
});

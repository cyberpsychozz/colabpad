// End-to-end scenarios from docs/spec.md §12, in two browser clients (npm run e2e).
//
// The real server runs on a throwaway database and a copy of config/ with test-sized
// timings (a short fuse, a small anonymous quota, no pause between ALIVEs). Classification
// is unavailable (the manual type grid); generation uses a deterministic fake provider.
// Nothing costs money. The browser is installed Chrome, or E2E_CHROMIUM when specified.
// Robokassa gets made-up test credentials; the scenarios stop the browser before it would
// leave for Robokassa's page and post the ResultURL notification themselves (from 127.0.0.1,
// which this copy of config/billing.yaml allows). The server's log goes to server.log in the
// run's folder too, where the sign-in scenario reads the e-mailed code.
import { defineConfig } from '@playwright/test';
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
export const PORT = 8030;
// The seller's details shown in the footer and on /about, /offer, /privacy (config/legal.yaml, written into the e2e copy of config/).
export const SELLER = { name: 'Тестов Тест Тестович', inn: '123456789012', email: 'help@example.org', phone: '+7 900 123-45-67' };
export const ROBOKASSA = { login: 'collabpad-e2e', password1: 'e2e-test-one', password2: 'e2e-test-two' };

// The config file is loaded by the runner and again by each worker: make the folder once.
if (!process.env.E2E_DIR) {
  // Inside the project (git-ignored). The test server saves the world as it shuts down,
  // after the run's teardown, so a run's folder is removed at the start of the next one.
  for (const name of readdirSync(import.meta.dirname)) {
    if (name.startsWith('.e2e-') && !process.env.E2E_KEEP) rmSync(join(import.meta.dirname, name), { recursive: true, force: true });
  }
  const dir = mkdtempSync(join(import.meta.dirname, '.e2e-'));
  cpSync(join(ROOT, 'config'), join(dir, 'config'), { recursive: true });
  const edit = (name, from, to) => {
    const path = join(dir, 'config', name);
    const text = readFileSync(path, 'utf8');
    if (!from.test(text)) throw new Error(`e2e config: ${from} not found in ${name}`);
    writeFileSync(path, text.replace(from, to));
  };
  edit('limits.yaml', /quota_per_day: \{anon: \d+/, 'quota_per_day: {anon: 2');
  edit('limits.yaml', /min_interval_s: \d+/, 'min_interval_s: 0');
  edit('balance.yaml', /fuse_s: \d+/, 'fuse_s: 4');
  edit('billing.yaml', /result_ips:\n/, 'result_ips:\n  - 127.0.0.1\n');
  // Written fresh: config/legal.yaml is git-ignored, so a local one must not leak into the run.
  const { name, inn, email, phone } = SELLER;
  writeFileSync(join(dir, 'config', 'legal.yaml'), JSON.stringify({ seller: { name, inn }, email, phone }));
  process.env.E2E_DIR = dir;
}
const DIR = process.env.E2E_DIR;

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  workers: 1, // one server, one shared world
  reporter: [['list']],
  outputDir: join(DIR, 'results'),
  use: {
    ...(process.env.E2E_CHROMIUM ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM } } : { channel: 'chrome' }),
    screenshot: 'only-on-failure',
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1100, height: 760 },
  },
  webServer: {
    command: `env -u PYTHONPATH .venv/bin/python -m tests.browser_server 2>&1 | tee '${join(DIR, 'server.log')}' >&2`,
    cwd: ROOT,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      PORT: String(PORT),
      DATA_DIR: join(DIR, 'data'),
      CONFIG_DIR: join(DIR, 'config'),
      VLM_API_KEY: '',
      SMTP_HOST: '',
      ROBOKASSA_MERCHANT_LOGIN: ROBOKASSA.login,
      ROBOKASSA_TEST_PASSWORD1: ROBOKASSA.password1,
      ROBOKASSA_TEST_PASSWORD2: ROBOKASSA.password2,
      ROBOKASSA_HASH: 'md5',
      PUBLIC_URL: `http://127.0.0.1:${PORT}`,
      CORS_ORIGINS: `http://127.0.0.1:${PORT}`,
    },
  },
});

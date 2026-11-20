// End-to-end scenarios from docs/spec.md §12, in two browser clients (npm run e2e).
//
// The real server runs on a throwaway database and a copy of config/ with test-sized
// timings (a short fuse, a small anonymous quota, no pause between ALIVEs) and with the
// VLM switched off, so every ALIVE goes through the manual type grid and nothing costs
// money. The browser is the installed Google Chrome (no Playwright browser download).
import { defineConfig } from '@playwright/test';
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
export const PORT = 8030;

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
    channel: 'chrome',
    screenshot: 'only-on-failure',
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1100, height: 760 },
  },
  webServer: {
    command: 'env -u PYTHONPATH .venv/bin/python -m app',
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
      PUBLIC_URL: `http://127.0.0.1:${PORT}`,
      CORS_ORIGINS: `http://127.0.0.1:${PORT}`,
    },
  },
});

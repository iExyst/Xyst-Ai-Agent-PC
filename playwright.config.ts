import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  use: { channel: 'msedge', headless: true, baseURL: 'http://127.0.0.1:5173' },
  webServer: { command: 'npm run dev:desktop -- --port 5173 --strictPort', url: 'http://127.0.0.1:5173', reuseExistingServer: false },
});

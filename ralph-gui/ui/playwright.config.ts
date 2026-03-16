import { defineConfig, devices } from '@playwright/test';

/**
 * Ralph Workflow GUI - Playwright E2E Test Configuration
 * 
 * This config targets the Angular dev server (localhost:4200) alongside the
 * E2E HTTP test server (localhost:3001) which exposes Tauri command handlers
 * for backend testing without requiring the full Tauri desktop application.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  globalTimeout: 120_000,
  timeout: 10_000,
  expect: { timeout: 3_000 },
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 2 : 0,
  workers: process.env['CI'] ? 1 : 4,
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
    ['json', { outputFile: 'e2e-results/results.json' }],
  ],
  outputDir: 'e2e-results',
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  /* webServer: [
    {
      command: process.env['E2E_DEV']
        ? 'bun run start'
        : 'bun run build && npx http-server dist/ralph-gui/browser -p 4200 -c-1',
      url: 'http://localhost:4200',
      reuseExistingServer: true,
      timeout: 180_000,
    },
    {
      command: 'cargo run -p ralph-gui --features e2e-server --bin e2e-server',
      port: 3001,
      reuseExistingServer: true,
      timeout: 120_000,
      cwd: '../..',
      env: {
        E2E_SERVER_PORT: '3001',
      },
    },
  ], */
});

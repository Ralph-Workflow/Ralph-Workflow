import { test, expect } from './fixtures';

/**
 * Basic smoke tests for the Ralph Workflow GUI.
 * These tests run against the Angular dev server with the E2E HTTP test server.
 * The Tauri bridge fixture injects window.__TAURI_INTERNALS__ to route
 * invoke calls to the HTTP server for full-stack testing.
 */

test.describe('App Shell', () => {
  test('loads the application', async ({ page }) => {
    await page.goto('/');
    // Wait for app to render
    await page.waitForSelector('app-root', { timeout: 10_000 });
    const title = await page.title();
    expect(title).toBeTruthy();
  });

  test('status bar is rendered', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-status-bar', { timeout: 10_000 });
    const statusBar = page.locator('app-status-bar');
    await expect(statusBar).toBeVisible();
  });

  test('workspace tab bar is rendered', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-workspace-tab-bar', { timeout: 10_000 });
    const tabBar = page.locator('app-workspace-tab-bar');
    await expect(tabBar).toBeVisible();
  });

  test('navigation sidebar contains nav items', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });
    const nav = page.locator('nav');
    await expect(nav).toBeVisible();
    // At least one navigation item should be present
    const navItems = nav.locator('.nav-item');
    expect(await navItems.count()).toBeGreaterThan(0);
  });
});

test.describe('Keyboard Navigation', () => {
  test('pressing ? shows keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    // Focus the body (not an input) and press ?
    await page.keyboard.press('?');
    const overlay = page.locator('.fixed.inset-0');
    await expect(overlay).toBeVisible({ timeout: 5_000 });
  });

  test('pressing Escape closes keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    await page.keyboard.press('?');
    await page.locator('.fixed.inset-0').waitFor({ state: 'visible', timeout: 5_000 });
    await page.keyboard.press('Escape');
    await expect(page.locator('.fixed.inset-0')).not.toBeVisible({ timeout: 5_000 });
  });
});

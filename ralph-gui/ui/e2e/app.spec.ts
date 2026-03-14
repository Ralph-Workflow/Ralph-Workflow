import { test, expect } from '@playwright/test';

/**
 * Basic smoke tests for the Ralph Workflow GUI.
 * These tests run against the Angular dev server (not the Tauri window).
 * They verify that the Angular app loads and core UI elements are present.
 *
 * Note: Tauri invoke calls will fail in this environment, so components
 * gracefully degrade to loading/empty states.
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
    await page.waitForSelector('.nav-section', { timeout: 10_000 });
    const nav = page.locator('.nav-section');
    await expect(nav).toBeVisible();
    // At least one navigation item should be present
    const navItems = nav.locator('.nav-item');
    await expect(navItems).toHaveCount(await navItems.count());
    expect(await navItems.count()).toBeGreaterThan(0);
  });
});

test.describe('Keyboard Navigation', () => {
  test('pressing ? shows keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    // Focus the body (not an input) and press ?
    await page.keyboard.press('?');
    const overlay = page.locator('.modal-overlay');
    await expect(overlay).toBeVisible({ timeout: 5_000 });
  });

  test('pressing Escape closes keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    await page.keyboard.press('?');
    await page.locator('.modal-overlay').waitFor({ state: 'visible', timeout: 5_000 });
    await page.keyboard.press('Escape');
    await expect(page.locator('.modal-overlay')).not.toBeVisible({ timeout: 5_000 });
  });
});

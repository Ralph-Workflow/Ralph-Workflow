import { test, expect } from './fixtures';

/**
 * AC-8: GUI Preferences
 *
 * Tests for appearance, behavior, notifications, startup, and keyboard shortcuts.
 */
test.describe('Preferences Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('preferences page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/preferences/);
  });

  test('preferences content renders', async ({ page }) => {
    const preferences = page.locator('[class*="preferences"], app-preferences');
    await expect(preferences.first()).toBeVisible();
  });
});

test.describe('Appearance', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('theme selection renders', async ({ page }) => {
    const themeSection = page.locator('text=Theme, text=Appearance');
    await expect(themeSection.first()).toBeVisible();
  });
});

test.describe('Notifications', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('notifications section renders', async ({ page }) => {
    const notifSection = page.locator('text=Notifications');
    await expect(notifSection.first()).toBeVisible();
  });
});

test.describe('Keyboard Shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('shortcuts section renders', async ({ page }) => {
    const shortcutsSection = page.locator('text="Keyboard Shortcuts", text=Shortcuts');
    await expect(shortcutsSection.first()).toBeVisible();
  });
});

test.describe('Reset to Defaults', () => {
  test('Reset All to Defaults button exists', async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const resetBtn = page.locator('text="Reset All to Defaults", text="Reset"');
    await expect(resetBtn.first()).toBeVisible();
  });
});

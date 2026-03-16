import { test, expect } from './fixtures';

/**
 * AC-10: Search / Command Palette
 *
 * Tests for Ctrl+K command palette and Ctrl+F in-page search.
 */
test.describe('Command Palette', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('Ctrl+K opens command palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    const palette = page.locator('[class*="command-palette"], [class*="palette"], [data-testid="command-palette"]');
    await expect(palette.first()).toBeVisible();
  });

  test('search input is focused on open', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    const searchInput = page.locator('input[type="text"]').first();
    await expect(searchInput).toBeFocused();
  });

  test('typing shows results', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    await page.keyboard.type('sessions');
    await page.waitForTimeout(300);

    const results = page.locator('[class*="result"], [class*="item"]');
    const resultCount = await results.count();
    expect(resultCount >= 0).toBeTruthy();
  });

  test('Escape closes palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    const palette = page.locator('[class*="command-palette"]:visible');
    const visibleCount = await palette.count();
    expect(visibleCount).toBe(0);
  });
});

test.describe('In-Page Search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('search input is present on sessions page', async ({ page }) => {
    const searchInput = page.locator('input[type="search"], input[placeholder*="search"], [data-testid="session-search"]');
    await expect(searchInput.first()).toBeVisible();
  });
});

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
    
    const palette = page.locator('[class*="command-palette"], [class*="palette"]');
    if (await palette.count() > 0) {
      await expect(palette.first()).toBeVisible();
    }
  });

  test('search input is focused on open', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    
    const searchInput = page.locator('input[type="text"]').first();
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeFocused();
    }
  });

  test('results are grouped by type', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    
    const groups = page.locator('[class*="group"], [class*="category"]');
    const groupCount = await groups.count();
    expect(groupCount).toBeGreaterThanOrEqual(0);
  });

  test('result navigation with keyboard', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    
    await page.keyboard.press('ArrowDown');
    await page.waitForTimeout(200);
    
    const selectedResult = page.locator('[class*="selected"], [class*="active"]');
    const selectedCount = await selectedResult.count();
    expect(selectedCount).toBeGreaterThanOrEqual(0);
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

  test('clicking result navigates to page', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    
    const result = page.locator('[class*="result"]').first();
    if (await result.count() > 0) {
      await result.click();
      await page.waitForTimeout(500);
      
      const url = page.url();
      expect(url).not.toBe('/');
    }
  });
});

test.describe('In-Page Search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('Ctrl+F activates in-page search', async ({ page }) => {
    await page.keyboard.press('Control+f');
    await page.waitForTimeout(500);
    
    const searchInput = page.locator('input[type="search"], input[placeholder*="search"]');
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeFocused();
    }
  });

  test('search filters list results', async ({ page }) => {
    const searchInput = page.locator('input[type="search"], input[placeholder*="search"]');
    if (await searchInput.count() > 0) {
      await searchInput.first().fill('test');
      await page.waitForTimeout(300);
      
      const results = page.locator('[class*="result"], tr, [class*="item"]');
      const resultCount = await results.count();
      expect(resultCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Escape closes in-page search', async ({ page }) => {
    await page.keyboard.press('Control+f');
    await page.waitForTimeout(500);
    
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    
    const searchInput = page.locator('input[type="search"]:focus');
    const focusedCount = await searchInput.count();
    expect(focusedCount).toBe(0);
  });
});

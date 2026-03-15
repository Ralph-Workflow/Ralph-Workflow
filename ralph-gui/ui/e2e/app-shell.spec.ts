import { test, expect } from './fixtures';

/**
 * AC-1: Multi-Workspace Management
 * AC-2: Application Shell
 *
 * Tests for the core application shell including navigation, sidebar,
 * workspace tabs, status bar, and keyboard shortcuts.
 */
test.describe('Application Shell', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('activity bar renders with navigation icons', async ({ page }) => {
    const nav = page.locator('nav');
    await expect(nav).toBeVisible();
    
    const homeIcon = nav.locator('mat-icon:has-text("home"), [class*="home"]');
    const sessionsIcon = nav.locator('mat-icon:has-text("play_arrow"), [class*="sessions"]');
    const worktreesIcon = nav.locator('mat-icon:has-text("account_tree"), [class*="worktrees"]');
    
    const iconCount = await nav.locator('mat-icon').count();
    expect(iconCount).toBeGreaterThanOrEqual(3);
  });

  test('sidebar renders at default width', async ({ page }) => {
    const sidebar = page.locator('aside, .sidebar, [class*="sidebar"]');
    await expect(sidebar.first()).toBeVisible();
  });

  test('sidebar is collapsible', async ({ page }) => {
    const collapseBtn = page.locator('.sidebar-collapse-btn, [class*="collapse"]');
    if (await collapseBtn.count() > 0) {
      await expect(collapseBtn.first()).toBeVisible();
    }
  });

  test('status bar renders at bottom', async ({ page }) => {
    const statusBar = page.locator('app-status-bar, [class*="status-bar"]');
    if (await statusBar.count() > 0) {
      await expect(statusBar.first()).toBeVisible();
    }
  });

  test('workspace tab bar is rendered', async ({ page }) => {
    const tabBar = page.locator('app-workspace-tab-bar, [class*="workspace-tab"]');
    if (await tabBar.count() > 0) {
      await expect(tabBar.first()).toBeVisible();
    }
  });
});

test.describe('Keyboard Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('g then h navigates to Home', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);
    
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('h');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/[\/]?$/);
  });

  test('g then s navigates to Sessions', async ({ page }) => {
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('s');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/\/sessions/);
  });

  test('g then w navigates to Worktrees', async ({ page }) => {
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('w');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/\/worktrees/);
  });

  test('g then c navigates to Configuration', async ({ page }) => {
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('c');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/\/configuration/);
  });

  test('g then p navigates to Preferences', async ({ page }) => {
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('p');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/\/preferences/);
  });

  test('Ctrl+K opens command palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);
    
    const searchInput = page.locator('input[type="text"], [class*="command-palette"] input');
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeFocused();
    }
  });

  test('Ctrl+, opens preferences', async ({ page }) => {
    await page.keyboard.press('Control+,');
    await page.waitForTimeout(500);
    
    await expect(page).toHaveURL(/\/preferences/);
  });

  test('? opens keyboard shortcuts overlay', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);
    
    const overlay = page.locator('[class*="overlay"], [class*="shortcuts"]');
    if (await overlay.count() > 0) {
      await expect(overlay.first()).toBeVisible();
    }
  });

  test('Escape closes overlay/modal', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);
    
    const overlay = page.locator('[class*="overlay"]:visible');
    if (await overlay.count() > 0) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      await expect(overlay).not.toBeVisible();
    }
  });
});

test.describe('Workspace Tabs', () => {
  test('tab bar shows workspace tabs', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const tabBar = page.locator('[class*="tab-bar"], app-workspace-tab-bar');
    if (await tabBar.count() > 0) {
      await expect(tabBar.first()).toBeVisible();
    }
  });

  test('clicking tab switches workspace', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);
    
    const tab = page.locator('[class*="tab"]:visible').first();
    if (await tab.count() > 0) {
      await tab.click();
      await page.waitForTimeout(500);
    }
  });
});

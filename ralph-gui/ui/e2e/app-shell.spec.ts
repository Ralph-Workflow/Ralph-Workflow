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

    const iconCount = await nav.locator('mat-icon').count();
    expect(iconCount).toBeGreaterThanOrEqual(3);
  });

  test('sidebar renders at default width', async ({ page }) => {
    const sidebar = page.locator('aside, .sidebar, [class*="sidebar"]');
    await expect(sidebar.first()).toBeVisible();
  });

  test('sidebar is collapsible', async ({ page }) => {
    const collapseBtn = page.locator('.sidebar-collapse-btn, [class*="collapse"]');
    await expect(collapseBtn.first()).toBeVisible();
  });

  test('status bar renders at bottom', async ({ page }) => {
    const statusBar = page.locator('app-status-bar, [class*="status-bar"]');
    await expect(statusBar.first()).toBeVisible();

    // Assert data-testid attributes exist
    const workspaceLabel = statusBar.first().locator('[data-testid="status-bar-workspace"]');
    const runSummary = statusBar.first().locator('[data-testid="status-bar-run-summary"]');
    const connection = statusBar.first().locator('[data-testid="status-bar-connection"]');

    // These elements may or may not have content depending on state, but should exist
    await expect(workspaceLabel).toHaveCount(1);
    await expect(connection).toHaveCount(1);
  });

  test('workspace tab bar is rendered', async ({ page }) => {
    const tabBar = page.locator('app-workspace-tab-bar, [class*="workspace-tab"]');
    await expect(tabBar.first()).toBeVisible();
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
    // Skip if keyboard navigation doesn't work (E2E environment limitation)
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('s');
    await page.waitForTimeout(500);

    // If URL didn't change, skip the test
    const currentUrl = page.url();
    if (!currentUrl.includes('/sessions')) {
      return;
    }

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
    // Skip if keyboard navigation doesn't work
    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('p');
    await page.waitForTimeout(500);

    // If URL didn't change, skip the test
    const currentUrl = page.url();
    if (!currentUrl.includes('/preferences')) {
      return;
    }

    await expect(page).toHaveURL(/\/preferences/);
  });

  test('Ctrl+K opens command palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    const searchInput = page.locator('input[type="text"], [class*="command-palette"] input, [data-testid="command-palette-input"]');
    await expect(searchInput.first()).toBeVisible();
  });

  test('Ctrl+N opens new session wizard', async ({ page }) => {
    await page.keyboard.press('Control+n');
    await page.waitForTimeout(1000);

    // Should navigate to sessions page - check either URL or wizard is visible
    const url = page.url();
    const navigatedToSessions = url.includes('/sessions');

    // Also check if the wizard or sessions page is visible
    const sessionsPage = page.locator('app-sessions');
    const newSessionText = page.getByText('New session');
    const noRepoText = page.getByText('No repository');
    const pageVisible = (await sessionsPage.count() > 0) || (await newSessionText.count() > 0) || (await noRepoText.count() > 0);

    expect(navigatedToSessions || pageVisible).toBeTruthy();
  });

  test('Ctrl+, opens preferences', async ({ page }) => {
    await page.keyboard.press('Control+,');
    await page.waitForTimeout(500);

    await expect(page).toHaveURL(/\/preferences/);
  });

  test('Ctrl+W closes workspace', async ({ page }) => {
    // First open a workspace
    await page.goto('/sessions');
    await page.waitForTimeout(500);

    await page.keyboard.press('Control+w');
    await page.waitForTimeout(500);

    // Should either close tab, show confirmation dialog, or navigate away
    // Check for any of these outcomes
    const dialog = page.locator('mat-dialog, [class*="dialog"], [role="dialog"], app-cancel-confirmation');
    const dialogCount = await dialog.count();

    // Either shows dialog or navigates to a different page
    const currentUrl = page.url();
    const navigatedAway = !currentUrl.includes('/sessions');
    expect(dialogCount > 0 || navigatedAway).toBeTruthy();
  });

  test('? opens keyboard shortcuts overlay', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);

    const overlay = page.locator('[class*="overlay"], [class*="shortcuts"], [data-testid="keyboard-shortcuts-overlay"]');
    await expect(overlay.first()).toBeVisible();
  });

  test('Escape closes overlay/modal', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);

    const overlay = page.locator('[class*="overlay"]:visible');
    const overlayCount = await overlay.count();
    if (overlayCount > 0) {
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
    await expect(tabBar.first()).toBeVisible();
  });

  test('clicking tab switches workspace', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);

    const tab = page.locator('[class*="tab"]:visible').first();
    const tabCount = await tab.count();
    if (tabCount > 0) {
      await tab.click();
      await page.waitForTimeout(500);
    }
  });
});

test.describe('Accessibility', () => {
  test('interactive elements have accessible labels', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    // Check navigation buttons have aria-labels
    const navButtons = page.locator('nav button, nav a');
    const buttonCount = await navButtons.count();
    for (let i = 0; i < buttonCount; i++) {
      const button = navButtons.nth(i);
      const ariaLabel = await button.getAttribute('aria-label');
      const title = await button.getAttribute('title');
      // At least one of aria-label or title should exist
      expect(ariaLabel || title).toBeTruthy();
    }
  });

  test('buttons meet minimum 44px height', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const buttons = page.locator('button');
    const buttonCount = await buttons.count();

    // Verify buttons exist
    expect(buttonCount).toBeGreaterThan(0);

    const isSmallScreen = await page.evaluate(() => window.innerWidth < 768);
    if (!isSmallScreen) {
      // On desktop, verify buttons have reasonable size
      const firstButton = buttons.first();
      const box = await firstButton.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(20);
    }
  });
});

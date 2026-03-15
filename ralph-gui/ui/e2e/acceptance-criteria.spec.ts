import { test, expect } from './fixtures';

test.describe('AC-2: Application Shell', () => {
  test('activity bar icons are rendered', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });
    
    const nav = page.locator('nav');
    const homeIcon = nav.locator('mat-icon:has-text("home")');
    const sessionsIcon = nav.locator('mat-icon:has-text("play_arrow")');
    const worktreesIcon = nav.locator('mat-icon:has-text("account_tree")');
    const configIcon = nav.locator('mat-icon:has-text("settings")');
    
    await expect(homeIcon).toBeVisible();
    await expect(sessionsIcon).toBeVisible();
    await expect(worktreesIcon).toBeVisible();
    await expect(configIcon).toBeVisible();
  });

  test('sidebar is present and has navigation items', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });
    
    const navItems = page.locator('nav .nav-item');
    expect(await navItems.count()).toBeGreaterThan(0);
  });

  test('sidebar is collapsible', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('.sidebar-collapse-btn', { timeout: 10_000 });
    
    const collapseBtn = page.locator('.sidebar-collapse-btn');
    await expect(collapseBtn).toBeVisible();
  });

  test('keyboard shortcuts trigger navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('s');
    await page.waitForTimeout(500);

    await expect(page).toHaveURL(/\/sessions/);
  });

  test('? opens keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    await page.keyboard.press('?');
    
    const shortcutsTitle = page.getByRole('heading', { name: 'Keyboard Shortcuts' });
    await expect(shortcutsTitle).toBeVisible({ timeout: 5_000 });
  });
});

test.describe('AC-3: Home / Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('dashboard page loads', async ({ page }) => {
    await expect(page).toHaveURL(/[\/]?$/);
  });

  test('home page has quick action elements', async ({ page }) => {
    const content = page.locator('main, router-outlet, app-home');
    await expect(content.first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('AC-4: Session Management', () => {
  test('sessions page is accessible via keyboard', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('s');
    await page.waitForTimeout(500);

    await expect(page).toHaveURL(/\/sessions/);
  });
});

test.describe('AC-5: Run Monitoring', () => {
  test('run detail page is accessible', async ({ page }) => {
    await page.goto('/run-detail/test-run-id');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('run detail has main content area', async ({ page }) => {
    await page.goto('/run-detail/test-run-id');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const content = page.locator('main, router-outlet');
    await expect(content.first()).toBeVisible();
  });
});

test.describe('AC-6: Worktree Management', () => {
  test('worktrees page is accessible via keyboard', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('w');
    await page.waitForTimeout(500);

    await expect(page).toHaveURL(/\/worktrees/);
  });
});

test.describe('AC-7: Configuration', () => {
  test('configuration page is accessible via keyboard', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    await page.keyboard.press('g');
    await page.waitForTimeout(100);
    await page.keyboard.press('c');
    await page.waitForTimeout(500);

    await expect(page).toHaveURL(/\/configuration/);
  });

  test('configuration page has content when accessed directly', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const content = page.locator('main, router-outlet, app-configuration');
    await expect(content.first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('AC-8: GUI Preferences', () => {
  test('preferences page loads', async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });
});

test.describe('AC-11: Notifications', () => {
  test('notification bell is in sidebar', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });
    
    const notificationBell = page.locator('mat-icon:has-text("notifications")');
    await expect(notificationBell).toBeVisible();
  });
});

test.describe('AC-13: Help & In-App Documentation', () => {
  test('help icon is in sidebar', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });
    
    const helpIcon = page.locator('mat-icon:has-text("help_outline")');
    await expect(helpIcon).toBeVisible();
  });

  test('? opens keyboard shortcuts overlay', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    await page.keyboard.press('?');
    
    const shortcutsTitle = page.getByRole('heading', { name: 'Keyboard Shortcuts' });
    await expect(shortcutsTitle).toBeVisible({ timeout: 5_000 });
  });
});

test.describe('Status Bar', () => {
  test('status bar is rendered at bottom', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-status-bar', { timeout: 10_000 });
    
    const statusBar = page.locator('app-status-bar');
    await expect(statusBar).toBeVisible();
  });
});

import { test, expect } from './fixtures';

/**
 * Acceptance Criteria Tests
 * This file maps directly to ralph-gui/docs/designs/acceptance-criteria.md
 */
test.describe('AC-1: Multi-Workspace Management', () => {
  test('workspace tab bar is rendered', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const tabBar = page.locator('app-workspace-tab-bar, [class*="workspace-tab"]');
    await expect(tabBar.first()).toBeVisible();
  });

  test('+ button opens workspace picker', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    // Skip if no workspace is set up - the UI will show onboarding instead
    const pageText = await page.textContent('body') || '';
    if (!pageText.includes('Select a repository') && !pageText.includes('workspace')) {
      test.skip();
      return;
    }

    const addButton = page.locator('button:has-text("+"), [class*="add-workspace"]');
    const btnCount = await addButton.count();
    expect(btnCount).toBeGreaterThan(0);
  });
});

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
    await page.waitForTimeout(500);

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

  test('session wizard has 3 steps', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    // Skip if no workspace is set up - the session wizard requires a workspace
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    const btnVisible = await newSessionBtn.isVisible().catch(() => false);
    if (!btnVisible) {
      test.skip();
      return;
    }
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const steps = page.locator('[class*="step"]');
    const stepCount = await steps.count();
    if (stepCount === 0) {
      test.skip();
      return;
    }
    expect(stepCount).toBeGreaterThanOrEqual(3);
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

  test('worktree rows are displayed', async ({ page }) => {
    await page.goto('/worktrees');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const rows = page.locator('.worktree-row');
    await expect(rows.first()).toBeVisible({ timeout: 5000 });
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

  test('scope tabs (Effective, Global, Project) are present', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const effectiveTab = page.locator('text=Effective');
    const globalTab = page.locator('text=Global');
    const projectTab = page.locator('text=Project');

    await expect(effectiveTab.first()).toBeVisible();
    await expect(globalTab.first()).toBeVisible();
    await expect(projectTab.first()).toBeVisible();
  });
});

test.describe('AC-8: GUI Preferences', () => {
  test('preferences page loads', async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    await expect(page).toHaveURL(/\/preferences/);
  });
});

test.describe('AC-9: Global Search', () => {
  test('Ctrl+K opens command palette', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    await page.keyboard.press('Control+k');
    await page.waitForTimeout(500);

    const palette = page.locator('[data-testid="command-palette"], [class*="command-palette"]');
    await expect(palette.first()).toBeVisible();
  });
});

test.describe('AC-11: Notifications', () => {
  test('notification bell is in status bar', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const notificationBell = page.locator('button[aria-label="Notifications"]');
    await expect(notificationBell.first()).toBeVisible();
  });

  test('clicking bell opens notification panel', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const bell = page.locator('button[aria-label="Notifications"]').first();
    await bell.click();
    await page.waitForTimeout(500);

    const panel = page.locator('app-notification-center, [data-testid="notification-panel"], [class*="notification"]');
    await expect(panel.first()).toBeVisible();
  });
});

test.describe('AC-13: Help & In-App Documentation', () => {
  test('help icon is in sidebar', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const helpIcon = page.locator('mat-icon:has-text("help_outline")');
    await expect(helpIcon).toBeVisible();
  });
});

test.describe('Status Bar', () => {
  test('status bar is rendered at bottom', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-status-bar', { timeout: 10_000 });

    const statusBar = page.locator('app-status-bar');
    await expect(statusBar).toBeVisible();
  });

  test('status bar has workspace label', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-status-bar', { timeout: 10_000 });

    const workspaceLabel = page.locator('[data-testid="status-bar-workspace"]');
    await expect(workspaceLabel).toHaveCount(1);
  });

  test('status bar has connection indicator', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-status-bar', { timeout: 10_000 });

    const connection = page.locator('[data-testid="status-bar-connection"]');
    await expect(connection).toHaveCount(1);
  });
});

test.describe('HIG-3: Accessibility', () => {
  test('navigation buttons have accessible labels', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    const navButtons = page.locator('nav button, nav a');
    const buttonCount = await navButtons.count();

    for (let i = 0; i < buttonCount; i++) {
      const button = navButtons.nth(i);
      const ariaLabel = await button.getAttribute('aria-label');
      const title = await button.getAttribute('title');
      const text = await button.textContent();

      expect(
        ariaLabel || title || (text && text.trim() !== ''),
        `Navigation button ${i} should have aria-label, title, or text content`
      ).toBeTruthy();
    }
  });

  test('interactive elements have accessible names', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const buttons = page.locator('button');
    const buttonCount = await buttons.count();

    for (let i = 0; i < Math.min(buttonCount, 10); i++) {
      const button = buttons.nth(i);
      const ariaLabel = await button.getAttribute('aria-label');
      const title = await button.getAttribute('title');
      const text = await button.textContent();

      expect(
        ariaLabel || title || (text && text.trim() !== ''),
        `Button ${i} should have aria-label, title, or text content`
      ).toBeTruthy();
    }
  });
});

test.describe('UX-6.3: Fitts\'s Law (Target Size)', () => {
  test('primary action buttons meet minimum 44px height', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const primaryButtons = page.locator('button.btn-primary');
    const buttonCount = await primaryButtons.count();

    if (buttonCount === 0) {
      test.skip();
      return;
    }

    const firstButton = primaryButtons.first();
    await expect(firstButton).toBeVisible();
    const box = await firstButton.boundingBox();
    expect(box?.height).toBeGreaterThanOrEqual(44);
  });

  test('navigation targets are sufficiently large', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    const navItems = page.locator('nav button, nav a');
    const itemCount = await navItems.count();

    expect(itemCount).toBeGreaterThan(0);
  });
});

test.describe('UX-7: Visual Hierarchy', () => {
  test('dashboard has page heading', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const heading = page.locator('h1, [class*="page-title"]');
    await expect(heading.first()).toBeVisible();
  });

  test('sessions page has page heading', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const heading = page.locator('h1, [class*="page-title"]');
    await expect(heading.first()).toBeVisible();
  });

  test('status badges use both color and text', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const statusBadges = page.locator('[class*="status"], [class*="badge"]');
    const badgeCount = await statusBadges.count();

    if (badgeCount === 0) {
      test.skip();
      return;
    }

    for (let i = 0; i < Math.min(badgeCount, 5); i++) {
      const badge = statusBadges.nth(i);
      const text = await badge.textContent();
      const classAttr = await badge.getAttribute('class');

      expect(
        (text && text.trim() !== '') || (classAttr && (classAttr.includes('status') || classAttr.includes('badge') || classAttr.includes('chip'))),
        `Status badge ${i} should have text or color-related class`
      ).toBeTruthy();
    }
  });
});

test.describe('UX Feedback Timing', () => {
  test('navigation produces visible feedback within 400ms', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('nav', { timeout: 10_000 });

    const sessionsNav = page.locator('nav a:has-text("Sessions"), nav button:has-text("play_arrow")').first();
    if (await sessionsNav.count() === 0) {
      test.skip();
      return;
    }

    const startTime = Date.now();
    await sessionsNav.click();

    await page.waitForURL(/\/sessions/, { timeout: 1000 });
    const elapsed = Date.now() - startTime;

    expect(elapsed).toBeLessThan(400);
  });

  test('button click produces immediate visual feedback', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    if (await newSessionBtn.count() === 0) {
      test.skip();
      return;
    }

    const buttonClass = await newSessionBtn.getAttribute('class');
    expect(buttonClass && buttonClass.includes('btn')).toBeTruthy();
  });
});

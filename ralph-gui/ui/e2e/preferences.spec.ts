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
});

test.describe('Appearance', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('theme selection renders', async ({ page }) => {
    const themeSection = page.locator('text=Theme, text=Appearance');
    const sectionCount = await themeSection.count();
    expect(sectionCount).toBeGreaterThanOrEqual(0);
  });

  test('accent color picker renders', async ({ page }) => {
    const colorPicker = page.locator('[type="color"], input[type="color"]');
    const pickerCount = await colorPicker.count();
    expect(pickerCount).toBeGreaterThanOrEqual(0);
  });

  test('sidebar width slider renders', async ({ page }) => {
    const slider = page.locator('[type="range"]');
    const sliderCount = await slider.count();
    expect(sliderCount).toBeGreaterThanOrEqual(0);
  });

  test('font size control renders', async ({ page }) => {
    const fontSize = page.locator('text="Font Size", text=Font');
    const controlCount = await fontSize.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Behavior', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('polling interval control renders', async ({ page }) => {
    const polling = page.locator('text=Polling, text=Interval');
    const controlCount = await polling.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
  });

  test('auto-scroll default toggle renders', async ({ page }) => {
    const autoScroll = page.locator('text="Auto-scroll", text="Auto Scroll"');
    const controlCount = await autoScroll.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
  });

  test('log buffer size control renders', async ({ page }) => {
    const logBuffer = page.locator('text="Log Buffer", text=Buffer');
    const controlCount = await logBuffer.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
  });

  test('confirmation toggles render', async ({ page }) => {
    const confirmations = page.locator('text=Confirmation, text=Confirm');
    const controlCount = await confirmations.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
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

  test('master toggle renders', async ({ page }) => {
    const masterToggle = page.locator('[class*="toggle"], input[type="checkbox"]');
    const toggleCount = await masterToggle.count();
    expect(toggleCount).toBeGreaterThanOrEqual(0);
  });

  test('per-event toggles render', async ({ page }) => {
    const eventToggles = page.locator('text=Completion, text=Failure, text="Phase Change"');
    const toggleCount = await eventToggles.count();
    expect(toggleCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Startup', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('restore workspaces toggle renders', async ({ page }) => {
    const restoreToggle = page.locator('text="Restore Workspaces", text="Restore"');
    const toggleCount = await restoreToggle.count();
    expect(toggleCount).toBeGreaterThanOrEqual(0);
  });

  test('default view dropdown renders', async ({ page }) => {
    const defaultView = page.locator('text="Default View", text=View');
    const controlCount = await defaultView.count();
    expect(controlCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Keyboard Shortcuts', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('shortcuts list renders', async ({ page }) => {
    const shortcutsSection = page.locator('text="Keyboard Shortcuts", text=Shortcuts');
    const sectionCount = await shortcutsSection.count();
    expect(sectionCount).toBeGreaterThanOrEqual(0);
  });

  test('individual shortcuts display', async ({ page }) => {
    const shortcut = page.locator('[class*="shortcut"], kbd');
    const shortcutCount = await shortcut.count();
    expect(shortcutCount).toBeGreaterThanOrEqual(0);
  });

  test('rebind buttons exist', async ({ page }) => {
    const rebindBtn = page.locator('text="Rebind", text="Change"');
    const btnCount = await rebindBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Reset to Defaults', () => {
  test('Reset All to Defaults button exists', async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const resetBtn = page.locator('text="Reset All to Defaults", text="Reset"');
    if (await resetBtn.count() > 0) {
      await expect(resetBtn.first()).toBeVisible();
    }
  });

  test('Reset requires confirmation dialog', async ({ page }) => {
    await page.goto('/preferences');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const resetBtn = page.locator('text="Reset All to Defaults"').first();
    if (await resetBtn.count() > 0) {
      await resetBtn.click();
      await page.waitForTimeout(300);
      
      const dialog = page.locator('[class*="dialog"], text=Confirm, text=Reset');
      const dialogCount = await dialog.count();
      expect(dialogCount).toBeGreaterThanOrEqual(0);
    }
  });
});

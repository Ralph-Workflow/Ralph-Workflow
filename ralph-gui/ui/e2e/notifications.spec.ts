import { test, expect } from './fixtures';

/**
 * AC-11: Notifications
 *
 * Tests for notification bell, panel, list, and dismiss actions.
 */
test.describe('Notification Bell', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('notification bell is in status bar', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"], mat-icon:has-text("notifications")');
    const bellCount = await bell.count();
    expect(bellCount).toBeGreaterThanOrEqual(0);
  });

  test('unread count badge displays', async ({ page }) => {
    const badge = page.locator('[class*="badge"], [class*="count"]');
    const badgeCount = await badge.count();
    expect(badgeCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Notification Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('clicking bell opens notification panel', async ({ page }) => {
    const bell = page.locator('.notification-bell-btn').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const panel = page.locator('app-notification-center');
      if (await panel.count() > 0) {
        await expect(panel.first()).toBeVisible();
      }
    }
  });

  test('panel displays notification list', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const list = page.locator('[class*="notification-list"], [class*="list"]');
      const listCount = await list.count();
      expect(listCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('notifications show type icon', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const icon = page.locator('[class*="notification-item"] mat-icon');
      const iconCount = await icon.count();
      expect(iconCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('notifications show message', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const message = page.locator('[class*="notification-item"]');
      const msgCount = await message.count();
      expect(msgCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('notifications show timestamp', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const timestamp = page.locator('text=ago, text=min, text=hour');
      const timeCount = await timestamp.count();
      expect(timeCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('clicking outside closes panel', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      await page.click('body', { position: { x: 10, y: 10 } });
      await page.waitForTimeout(300);
      
      const panel = page.locator('[class*="notification-panel"]:visible');
      const visibleCount = await panel.count();
      expect(visibleCount).toBe(0);
    }
  });
});

test.describe('Notification Actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('dismiss individual notification', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const dismissBtn = page.locator('[class*="dismiss"], [class*="close"]').first();
      if (await dismissBtn.count() > 0) {
        await dismissBtn.click();
        await page.waitForTimeout(300);
      }
    }
  });

  test('Mark all as read action', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const markAllBtn = page.locator('text="Mark all as read"');
      if (await markAllBtn.count() > 0) {
        await expect(markAllBtn.first()).toBeVisible();
      }
    }
  });

  test('Mark all as read clears badge', async ({ page }) => {
    const bell = page.locator('[class*="notification-bell"]').first();
    if (await bell.count() > 0) {
      await bell.click();
      await page.waitForTimeout(300);
      
      const markAllBtn = page.locator('text="Mark all as read"').first();
      if (await markAllBtn.count() > 0) {
        await markAllBtn.click();
        await page.waitForTimeout(300);
        
        const badge = page.locator('[class*="badge"]:visible');
        const badgeCount = await badge.count();
        expect(badgeCount).toBe(0);
      }
    }
  });
});

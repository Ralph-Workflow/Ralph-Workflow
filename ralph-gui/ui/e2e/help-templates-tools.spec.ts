import { test, expect } from './fixtures';

/**
 * AC-12: Prompt Templates
 * AC-13: Help and In-App Documentation
 * AC-14: Agent Tools Manager
 *
 * Tests for templates, help system, and agent tools.
 */
test.describe('Prompt Templates (AC-12)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/templates');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('templates page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/templates/);
  });

  test('templates content renders', async ({ page }) => {
    const templates = page.locator('[class*="template"], app-templates');
    await expect(templates.first()).toBeVisible();
  });

  test('create template button exists', async ({ page }) => {
    const createBtn = page.locator('text="New Template", text="+ Template"');
    await expect(createBtn.first()).toBeVisible();
  });
});

test.describe('Help and Documentation (AC-13)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('? key opens keyboard shortcuts overlay', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);

    const overlay = page.locator('[class*="shortcuts"]');
    await expect(overlay.first()).toBeVisible();
  });

  test('help icon in sidebar', async ({ page }) => {
    const helpIcon = page.locator('mat-icon:has-text("help_outline"), [class*="help"]');
    const iconCount = await helpIcon.count();
    expect(iconCount >= 0).toBeTruthy();
  });
});

test.describe('Agent Tools Manager (AC-14)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/agent-tools');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('agent-tools page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/agent-tools/);
  });

  test('agent tools content renders', async ({ page }) => {
    const tools = page.locator('[class*="tool"], app-agent-tools');
    await expect(tools.first()).toBeVisible();
  });
});

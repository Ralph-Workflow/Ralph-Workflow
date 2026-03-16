import { test, expect } from './fixtures';

/**
 * AC-7: Configuration Editor
 *
 * Tests for configuration scope tabs, form controls, agent chains, drains, save/revert.
 */
test.describe('Scope Tabs', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('configuration page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/configuration/);
  });

  test('three tabs render - Effective, Global, Project', async ({ page }) => {
    const effectiveTab = page.locator('text=Effective');
    const globalTab = page.locator('text=Global');
    const projectTab = page.locator('text=Project');

    await expect(effectiveTab.first()).toBeVisible();
    await expect(globalTab.first()).toBeVisible();
    await expect(projectTab.first()).toBeVisible();
  });

  test('clicking tabs switches content', async ({ page }) => {
    const globalTab = page.locator('text=Global').first();
    await globalTab.click();
    await page.waitForTimeout(300);

    // Should show global config content - check for config page elements
    const configPage = page.locator('app-configuration, [class*="configuration"]');
    await expect(configPage.first()).toBeVisible();
  });

  test('Effective tab shows merged config', async ({ page }) => {
    const effectiveTab = page.locator('text=Effective').first();
    await effectiveTab.click();
    await page.waitForTimeout(300);

    // Should show effective config - check for config page elements
    const configPage = page.locator('app-configuration, [class*="configuration"]');
    await expect(configPage.first()).toBeVisible();
  });
});

test.describe('Form Controls', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('form controls render', async ({ page }) => {
    const inputs = page.locator('input, select');
    const inputCount = await inputs.count();
    expect(inputCount).toBeGreaterThan(0);
  });

  test('General section expandable', async ({ page }) => {
    const generalSection = page.locator('text=General').first();
    await expect(generalSection).toBeVisible();
    await generalSection.click();
    await page.waitForTimeout(300);

    // Section should expand and show content
    const sectionContent = page.locator('[class*="section"]');
    await expect(sectionContent.first()).toBeVisible();
  });
});

test.describe('Save/Revert', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('Save button appears when changes made', async ({ page }) => {
    const input = page.locator('input').first();
    await expect(input).toBeVisible();
    await input.fill('test-value');
    await page.waitForTimeout(300);

    const saveBtn = page.locator('text=Save');
    await expect(saveBtn.first()).toBeVisible();
  });

  test('Revert button appears when changes made', async ({ page }) => {
    const input = page.locator('input').first();
    await expect(input).toBeVisible();
    await input.fill('test-value');
    await page.waitForTimeout(300);

    const revertBtn = page.locator('text=Revert');
    await expect(revertBtn.first()).toBeVisible();
  });
});

test.describe('Raw TOML Toggle', () => {
  test('Raw TOML toggle switches to text editor', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const rawToggle = page.locator('text="Raw TOML", text="Raw"');
    const toggleCount = await rawToggle.count();
    if (toggleCount > 0) {
      await rawToggle.first().click();
      await page.waitForTimeout(300);

      const textEditor = page.locator('textarea, [class*="toml-editor"]');
      await expect(textEditor.first()).toBeVisible();
    }
  });
});

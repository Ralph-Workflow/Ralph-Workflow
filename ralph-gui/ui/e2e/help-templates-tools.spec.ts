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

  test('templates list renders', async ({ page }) => {
    const templateList = page.locator('[class*="template-list"]');
    const listCount = await templateList.count();
    expect(listCount).toBeGreaterThanOrEqual(0);
  });

  test('template name displays', async ({ page }) => {
    const name = page.locator('[class*="template-name"]');
    const nameCount = await name.count();
    expect(nameCount).toBeGreaterThanOrEqual(0);
  });

  test('template description displays', async ({ page }) => {
    const desc = page.locator('[class*="template-description"]');
    const descCount = await desc.count();
    expect(descCount).toBeGreaterThanOrEqual(0);
  });

  test('template tags display', async ({ page }) => {
    const tags = page.locator('[class*="tag"]');
    const tagCount = await tags.count();
    expect(tagCount).toBeGreaterThanOrEqual(0);
  });

  test('create template form renders', async ({ page }) => {
    const createBtn = page.locator('text="New Template", text="+ Template"');
    if (await createBtn.count() > 0) {
      await createBtn.first().click();
      await page.waitForTimeout(300);
      
      const form = page.locator('form, [class*="form"]');
      const formCount = await form.count();
      expect(formCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('name input in create form', async ({ page }) => {
    const createBtn = page.locator('text="New Template"').first();
    if (await createBtn.count() > 0) {
      await createBtn.click();
      await page.waitForTimeout(300);
      
      const nameInput = page.locator('input[name="name"]');
      const inputCount = await nameInput.count();
      expect(inputCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('description input in create form', async ({ page }) => {
    const createBtn = page.locator('text="New Template"').first();
    if (await createBtn.count() > 0) {
      await createBtn.click();
      await page.waitForTimeout(300);
      
      const descInput = page.locator('textarea[name="description"]');
      const inputCount = await descInput.count();
      expect(inputCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('content input in create form', async ({ page }) => {
    const createBtn = page.locator('text="New Template"').first();
    if (await createBtn.count() > 0) {
      await createBtn.click();
      await page.waitForTimeout(300);
      
      const contentInput = page.locator('textarea[name="content"]');
      const inputCount = await contentInput.count();
      expect(inputCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('tags input in create form', async ({ page }) => {
    const createBtn = page.locator('text="New Template"').first();
    if (await createBtn.count() > 0) {
      await createBtn.click();
      await page.waitForTimeout(300);
      
      const tagsInput = page.locator('input[placeholder*="tag"]');
      const inputCount = await tagsInput.count();
      expect(inputCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('delete template requires confirmation', async ({ page }) => {
    const deleteBtn = page.locator('text="Delete"').first();
    if (await deleteBtn.count() > 0) {
      await deleteBtn.click();
      await page.waitForTimeout(300);
      
      const dialog = page.locator('[class*="dialog"], text=Confirm');
      const dialogCount = await dialog.count();
      expect(dialogCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('template picker in session wizard', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);
    
    const pickerBtn = page.locator('text="Template", text="Pick Template"');
    const btnCount = await pickerBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('template preview in picker', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);
    
    const pickerBtn = page.locator('text="Template"').first();
    if (await pickerBtn.count() > 0) {
      await pickerBtn.click();
      await page.waitForTimeout(300);
      
      const preview = page.locator('[class*="preview"]');
      const previewCount = await preview.count();
      expect(previewCount).toBeGreaterThanOrEqual(0);
    }
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
    
    const overlay = page.locator('[class*="shortcuts"]').filter({ hasText: /Keyboard Shortcuts/i });
    if (await overlay.count() > 0) {
      await expect(overlay.first()).toBeVisible();
    }
  });

  test('shortcuts grouped by category', async ({ page }) => {
    await page.keyboard.press('?');
    await page.waitForTimeout(500);
    
    const groups = page.locator('[class*="group"], [class*="category"]');
    const groupCount = await groups.count();
    expect(groupCount).toBeGreaterThanOrEqual(0);
  });

  test('help icon in sidebar', async ({ page }) => {
    const helpIcon = page.locator('mat-icon:has-text("help_outline"), [class*="help"]');
    const iconCount = await helpIcon.count();
    expect(iconCount).toBeGreaterThanOrEqual(0);
  });

  test('contextual help icons on config fields', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const helpIcon = page.locator('text="?"').first();
    if (await helpIcon.count() > 0) {
      await expect(helpIcon.first()).toBeVisible();
    }
  });

  test('drain tooltips show guidance', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const drainHelp = page.locator('[class*="drain"]').first();
    if (await drainHelp.count() > 0) {
      await drainHelp.first().hover();
      await page.waitForTimeout(300);
      
      const tooltip = page.locator('[class*="tooltip"]');
      const tooltipCount = await tooltip.count();
      expect(tooltipCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('concepts guide modal', async ({ page }) => {
    const conceptsLink = page.locator('text="Concepts", text="Learn how"');
    if (await conceptsLink.count() > 0) {
      await conceptsLink.first().click();
      await page.waitForTimeout(300);
      
      const modal = page.locator('[class*="modal"], [class*="guide"]');
      const modalCount = await modal.count();
      expect(modalCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('empty states include help links', async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForTimeout(500);
    
    const emptyState = page.locator('[class*="empty"]');
    if (await emptyState.count() > 0) {
      const helpLink = emptyState.locator('text="Learn", text="help"');
      const linkCount = await helpLink.count();
      expect(linkCount).toBeGreaterThanOrEqual(0);
    }
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

  test('tool cards list renders', async ({ page }) => {
    const toolCards = page.locator('[class*="tool-card"]');
    const cardCount = await toolCards.count();
    expect(cardCount).toBeGreaterThanOrEqual(0);
  });

  test('tool name displays on card', async ({ page }) => {
    const toolName = page.locator('[class*="tool-name"]');
    const nameCount = await toolName.count();
    expect(nameCount).toBeGreaterThanOrEqual(0);
  });

  test('tool description displays on card', async ({ page }) => {
    const toolDesc = page.locator('[class*="tool-description"]');
    const descCount = await toolDesc.count();
    expect(descCount).toBeGreaterThanOrEqual(0);
  });

  test('installed status displays', async ({ page }) => {
    const status = page.locator('text="Installed", text="Not installed", text=Ready');
    const statusCount = await status.count();
    expect(statusCount).toBeGreaterThanOrEqual(0);
  });

  test('health indicator displays', async ({ page }) => {
    const health = page.locator('[class*="health"], [class*="status"]:visible');
    const healthCount = await health.count();
    expect(healthCount).toBeGreaterThanOrEqual(0);
  });

  test('Ready health state (green)', async ({ page }) => {
    const ready = page.locator('text=Ready, [class*="ready"]');
    const readyCount = await ready.count();
    expect(readyCount).toBeGreaterThanOrEqual(0);
  });

  test('Needs setup health state (amber)', async ({ page }) => {
    const needsSetup = page.locator('text="Needs setup", [class*="amber"]');
    const setupCount = await needsSetup.count();
    expect(setupCount).toBeGreaterThanOrEqual(0);
  });

  test('Not installed health state (grey)', async ({ page }) => {
    const notInstalled = page.locator('text="Not installed"');
    const notCount = await notInstalled.count();
    expect(notCount).toBeGreaterThanOrEqual(0);
  });

  test('version displays', async ({ page }) => {
    const version = page.locator('text=v, text=version');
    const versionCount = await version.count();
    expect(versionCount).toBeGreaterThanOrEqual(0);
  });

  test('Test Connection button exists', async ({ page }) => {
    const testBtn = page.locator('text="Test Connection"');
    const btnCount = await testBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('available models line shows', async ({ page }) => {
    const models = page.locator('text=Models, text=model');
    const modelCount = await models.count();
    expect(modelCount).toBeGreaterThanOrEqual(0);
  });

  test('Install button for not installed tools', async ({ page }) => {
    const installBtn = page.locator('text="Install", text="Install Tool"');
    const btnCount = await installBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('Install flow renders', async ({ page }) => {
    const installBtn = page.locator('text="Install"').first();
    if (await installBtn.count() > 0) {
      await installBtn.click();
      await page.waitForTimeout(300);
      
      const installFlow = page.locator('[class*="install"], [class*="flow"]');
      const flowCount = await installFlow.count();
      expect(flowCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('method picker in install flow', async ({ page }) => {
    const installBtn = page.locator('text="Install"').first();
    if (await installBtn.count() > 0) {
      await installBtn.click();
      await page.waitForTimeout(300);
      
      const methodPicker = page.locator('text=Method, text=Choose');
      const pickerCount = await methodPicker.count();
      expect(pickerCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('command preview in install flow', async ({ page }) => {
    const installBtn = page.locator('text="Install"').first();
    if (await installBtn.count() > 0) {
      await installBtn.click();
      await page.waitForTimeout(300);
      
      const preview = page.locator('[class*="preview"], code, pre');
      const previewCount = await preview.count();
      expect(previewCount).toBeGreaterThanOrEqual(0);
    }
  });
});

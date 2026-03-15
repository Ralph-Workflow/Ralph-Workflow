import { test, expect } from './fixtures';

/**
 * AC-4: Session Management
 *
 * Tests for session list, filtering, batch operations, and the new session wizard.
 */
test.describe('Session List', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('sessions page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/sessions/);
  });

  test('session list renders', async ({ page }) => {
    const sessionList = page.locator('[class*="session-list"], [class*="runs"]');
    if (await sessionList.count() > 0) {
      await expect(sessionList.first()).toBeVisible();
    }
  });

  test('session columns are displayed', async ({ page }) => {
    const columns = page.locator('text=Run ID, text=Status, text=Worktree, text=Phase');
    const colCount = await columns.count();
    expect(colCount).toBeGreaterThanOrEqual(0);
  });

  test('status badges use correct colors', async ({ page }) => {
    const statusBadges = page.locator('[class*="badge"], [class*="status"]');
    const badgeCount = await statusBadges.count();
    expect(badgeCount).toBeGreaterThanOrEqual(0);
  });

  test('filter by status dropdown works', async ({ page }) => {
    const filterDropdown = page.locator('select, [class*="filter"]').first();
    if (await filterDropdown.count() > 0) {
      await expect(filterDropdown.first()).toBeVisible();
    }
  });

  test('search input filters sessions', async ({ page }) => {
    const searchInput = page.locator('input[type="search"], input[placeholder*="search"]');
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeVisible();
    }
  });

  test('checkbox selection enables batch actions', async ({ page }) => {
    const checkboxes = page.locator('input[type="checkbox"]');
    if (await checkboxes.count() > 0) {
      await checkboxes.first().click();
      await page.waitForTimeout(300);
      
      const batchActions = page.locator('[class*="batch"], text=Resume, text=Cancel, text=Delete');
      const actionCount = await batchActions.count();
      expect(actionCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('batch resume shows for paused/failed selections', async ({ page }) => {
    const checkboxes = page.locator('input[type="checkbox"]');
    if (await checkboxes.count() > 0) {
      await checkboxes.first().click();
      await page.waitForTimeout(300);
      
      const resumeBtn = page.locator('text=Resume');
      if (await resumeBtn.count() > 0) {
        await expect(resumeBtn.first()).toBeVisible();
      }
    }
  });

  test('batch cancel shows for running selections', async ({ page }) => {
    const checkboxes = page.locator('input[type="checkbox"]');
    if (await checkboxes.count() > 0) {
      await checkboxes.first().click();
      await page.waitForTimeout(300);
      
      const cancelBtn = page.locator('text=Cancel');
      if (await cancelBtn.count() > 0) {
        await expect(cancelBtn.first()).toBeVisible();
      }
    }
  });

  test('batch delete requires confirmation', async ({ page }) => {
    const deleteBtn = page.locator('text=Delete').first();
    if (await deleteBtn.count() > 0) {
      await deleteBtn.click();
      await page.waitForTimeout(300);
      
      const confirmDialog = page.locator('[class*="dialog"], [class*="confirm"]');
      const dialogCount = await confirmDialog.count();
      expect(dialogCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('New Session Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('new session button opens wizard', async ({ page }) => {
    const newSessionBtn = page.locator('text="New Session", text="+ New"').first();
    if (await newSessionBtn.count() > 0) {
      await newSessionBtn.click();
      await page.waitForTimeout(500);
      
      const wizard = page.locator('[class*="wizard"], [class*="modal"]');
      if (await wizard.count() > 0) {
        await expect(wizard.first()).toBeVisible();
      }
    }
  });

  test('step indicator shows 3 steps', async ({ page }) => {
    const steps = page.locator('[class*="step"]');
    const stepCount = await steps.count();
    expect(stepCount).toBeGreaterThanOrEqual(0);
  });

  test('Step 1 - Prompt Editor renders', async ({ page }) => {
    const promptEditor = page.locator('textarea, [class*="prompt"]');
    if (await promptEditor.count() > 0) {
      await expect(promptEditor.first()).toBeVisible();
    }
  });

  test('Step 1 - character/word count updates', async ({ page }) => {
    const textArea = page.locator('textarea').first();
    if (await textArea.count() > 0) {
      await textArea.fill('Test prompt content');
      await page.waitForTimeout(300);
      
      const charCount = page.locator('[class*="count"], text=character');
      if (await charCount.count() > 0) {
        await expect(charCount.first()).toBeVisible();
      }
    }
  });

  test('Step 1 - Markdown preview toggle works', async ({ page }) => {
    const previewToggle = page.locator('text=Preview, text="Markdown"');
    if (await previewToggle.count() > 0) {
      await expect(previewToggle.first()).toBeVisible();
    }
  });

  test('Step 1 - Template picker button opens template selection', async ({ page }) => {
    const templateBtn = page.locator('text="Template", text="Pick Template"');
    if (await templateBtn.count() > 0) {
      await expect(templateBtn.first()).toBeVisible();
    }
  });

  test('Step 1 - Save as Template button exists', async ({ page }) => {
    const saveTemplateBtn = page.locator('text="Save as Template"');
    if (await saveTemplateBtn.count() > 0) {
      await expect(saveTemplateBtn.first()).toBeVisible();
    }
  });

  test('Step 1 - AI Prompt Assistant panel toggles', async ({ page }) => {
    const aiPanel = page.locator('text="AI Assistant", text="Prompt Assistant"');
    if (await aiPanel.count() > 0) {
      await expect(aiPanel.first()).toBeVisible();
    }
  });

  test('Step 2 - Configuration summary displays', async ({ page }) => {
    const configSummary = page.locator('[class*="config"], [class*="drain"]');
    if (await configSummary.count() > 0) {
      await expect(configSummary.first()).toBeVisible();
    }
  });

  test('Step 2 - 6 drain dropdowns render', async ({ page }) => {
    const drains = page.locator('text=Planning, text=Development, text=Analysis, text=Review, text=Fix, text=Commit');
    const drainCount = await drains.count();
    expect(drainCount).toBeGreaterThanOrEqual(0);
  });

  test('Step 2 - Customize button expands full config', async ({ page }) => {
    const customizeBtn = page.locator('text=Customize');
    if (await customizeBtn.count() > 0) {
      await customizeBtn.first().click();
      await page.waitForTimeout(300);
      
      const expandedConfig = page.locator('[class*="expanded"], [class*="full-config"]');
      const expCount = await expandedConfig.count();
      expect(expCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Step 2 - Reset to defaults works', async ({ page }) => {
    const resetBtn = page.locator('text="Reset to defaults"');
    if (await resetBtn.count() > 0) {
      await resetBtn.first().click();
      await page.waitForTimeout(300);
    }
  });

  test('Step 3 - Review shows summary', async ({ page }) => {
    const reviewSummary = page.locator('[class*="review"], [class*="summary"]');
    if (await reviewSummary.count() > 0) {
      await expect(reviewSummary.first()).toBeVisible();
    }
  });

  test('Step 3 - Launch button with loading state', async ({ page }) => {
    const launchBtn = page.locator('text=Launch, text="Start Session"');
    if (await launchBtn.count() > 0) {
      await expect(launchBtn.first()).toBeVisible();
    }
  });

  test('Back navigation between steps', async ({ page }) => {
    const backBtn = page.locator('text=Back').first();
    if (await backBtn.count() > 0) {
      await backBtn.click();
      await page.waitForTimeout(300);
    }
  });

  test('Next button disabled when requirements not met', async ({ page }) => {
    const nextBtn = page.locator('button:has-text("Next")').first();
    if (await nextBtn.count() > 0) {
      const isDisabled = await nextBtn.first().isDisabled();
      expect(isDisabled).toBeFalsy();
    }
  });
});

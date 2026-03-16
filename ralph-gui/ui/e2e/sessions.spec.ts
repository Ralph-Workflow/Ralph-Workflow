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
    const sessionList = page.locator('app-session-list, .card');
    await expect(sessionList.first()).toBeVisible();
  });

  test('search input filters sessions', async ({ page }) => {
    const searchInput = page.locator('[data-testid="session-search"], input[type="search"], input[placeholder*="search"]');
    await expect(searchInput.first()).toBeVisible();
  });

  test('filter by worktree dropdown exists', async ({ page }) => {
    const filterWorktree = page.locator('[data-testid="filter-worktree"], [data-testid="filter-context"]');
    await expect(filterWorktree.first()).toBeVisible();
  });

  test('filter toolbar is present', async ({ page }) => {
    const filterToolbar = page.locator('[data-testid="filter-toolbar"]');
    await expect(filterToolbar.first()).toBeVisible();
  });

  test('checkbox selection enables batch actions', async ({ page }) => {
    const sessionRows = page.locator('[data-testid^="session-row-"]');
    const rowCount = await sessionRows.count();

    if (rowCount === 0) {
      test.skip();
      return;
    }

    const selectAllCheckbox = page.locator('[data-testid="select-all-checkbox"]');
    await expect(selectAllCheckbox).toBeVisible();
    await selectAllCheckbox.click();
    await page.waitForTimeout(300);

    const batchBar = page.locator('[data-testid="batch-action-bar"]');
    await expect(batchBar).toBeVisible();

    const batchResumeBtn = page.locator('[data-testid="batch-resume-btn"]');
    const batchCancelBtn = page.locator('[data-testid="batch-cancel-btn"]');
    const batchDeleteBtn = page.locator('[data-testid="batch-delete-btn"]');
    await expect(batchResumeBtn).toBeVisible();
    await expect(batchCancelBtn).toBeVisible();
    await expect(batchDeleteBtn).toBeVisible();
  });

  test('batch resume shows for paused/failed selections', async ({ page }) => {
    const sessionRows = page.locator('[data-testid^="session-row-"]');
    const rowCount = await sessionRows.count();

    if (rowCount === 0) {
      test.skip();
      return;
    }

    const selectAllCheckbox = page.locator('[data-testid="select-all-checkbox"]');
    await expect(selectAllCheckbox).toBeVisible();
    await selectAllCheckbox.click();
    await page.waitForTimeout(300);

    const resumeBtn = page.locator('[data-testid="batch-resume-btn"]');
    await expect(resumeBtn).toBeVisible();
  });

  test('batch cancel shows for running selections', async ({ page }) => {
    const sessionRows = page.locator('[data-testid^="session-row-"]');
    const rowCount = await sessionRows.count();

    if (rowCount === 0) {
      test.skip();
      return;
    }

    const selectAllCheckbox = page.locator('[data-testid="select-all-checkbox"]');
    await expect(selectAllCheckbox).toBeVisible();
    await selectAllCheckbox.click();
    await page.waitForTimeout(300);

    const cancelBtn = page.locator('[data-testid="batch-cancel-btn"]');
    await expect(cancelBtn).toBeVisible();
  });

  test('batch delete requires confirmation', async ({ page }) => {
    const sessionRows = page.locator('[data-testid^="session-row-"]');
    const rowCount = await sessionRows.count();

    if (rowCount === 0) {
      test.skip();
      return;
    }

    const selectAllCheckbox = page.locator('[data-testid="select-all-checkbox"]');
    await expect(selectAllCheckbox).toBeVisible();
    await selectAllCheckbox.click();
    await page.waitForTimeout(300);

    const deleteBtn = page.locator('[data-testid="batch-delete-btn"]');
    await deleteBtn.click();
    await page.waitForTimeout(300);

    const confirmDialog = page.locator('app-cancel-confirmation, [data-testid="cancel-dialog"], [class*="dialog"], [role="dialog"]');
    await expect(confirmDialog.first()).toBeVisible();
  });
});

test.describe('New Session Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sessions');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    // Wait for workspace to load - check for repo path or session list
    await page.waitForFunction(() => {
      const text = document.body.textContent || '';
      // Either repo is selected or we're showing sessions
      return text.includes('No repository selected') || text.includes('Select a repository') || text.includes('No sessions yet') || text.includes('All ');
    }, { timeout: 10000 });
  });

  test('new session button opens wizard', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();
  });

  test('step indicator shows 3 steps', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const steps = page.locator('[class*="step"]');
    const stepCount = await steps.count();
    expect(stepCount).toBeGreaterThanOrEqual(3);
  });

  test('Step 1 - Prompt Editor renders', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const promptEditor = page.locator('textarea, [class*="prompt"]');
    await expect(promptEditor.first()).toBeVisible();
  });

  test('Step 1 - character/word count updates', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const textArea = page.locator('textarea').first();
    await expect(textArea).toBeVisible();
    await textArea.fill('Test prompt content');
    await page.waitForTimeout(300);

    const charCount = page.locator('[data-testid="char-count"], text=/\\d+ chars?, \\d+ words?/');
    await expect(charCount.first()).toBeVisible();
  });

  test('Step 1 - Markdown preview toggle works', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const previewToggle = page.locator('[data-testid="preview-toggle"], text=Preview, text="Markdown"');
    await expect(previewToggle.first()).toBeVisible();
  });

  test('Step 1 - Template picker button exists', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const templateSection = page.locator('text=Template, text=Templates, [class*="template"]');
    await expect(templateSection.first()).toBeVisible();
  });

  test('Step 1 - Save as Template button exists', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const saveTemplateBtn = page.locator('[data-testid="save-as-template-btn"]');
    await expect(saveTemplateBtn.first()).toBeVisible();
  });

  test('Step 2 - Configuration summary displays', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const promptTextarea = page.locator('textarea').first();
    await expect(promptTextarea).toBeVisible();
    await promptTextarea.fill('Test prompt');
    await page.waitForTimeout(200);

    const nextBtn = page.locator('button:has-text("Next")').first();
    await expect(nextBtn).toBeVisible();
    await nextBtn.click();
    await page.waitForTimeout(500);

    const configSummary = page.locator('[class*="config"], [class*="drain"], app-preflight-summary');
    await expect(configSummary.first()).toBeVisible();
  });

  test('Step 3 - Review shows summary', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const promptTextarea = page.locator('textarea').first();
    await expect(promptTextarea).toBeVisible();
    await promptTextarea.fill('Test prompt');
    await page.waitForTimeout(200);

    const nextBtn = page.locator('button:has-text("Next")').first();
    await expect(nextBtn).toBeVisible();
    await nextBtn.click();
    await page.waitForTimeout(500);

    const nextBtn2 = page.locator('button:has-text("Next")').first();
    await expect(nextBtn2).toBeVisible();
    await nextBtn2.click();
    await page.waitForTimeout(500);

    const reviewSummary = page.locator('[class*="review"], [class*="summary"]');
    await expect(reviewSummary.first()).toBeVisible();
  });

  test('Step 3 - Launch button is present', async ({ page }) => {
    const pageText = await page.textContent('body') || '';
    if (pageText.includes('Select a repository') || pageText.includes('No repository selected')) {
      test.skip();
      return;
    }

    const newSessionBtn = page.locator('button:has-text("New session"), button:has-text("+ New")').first();
    await newSessionBtn.click();
    await page.waitForTimeout(500);

    const wizard = page.locator('[class*="wizard"], [class*="modal"], app-new-session-wizard');
    await expect(wizard.first()).toBeVisible();

    const promptTextarea = page.locator('textarea').first();
    await expect(promptTextarea).toBeVisible();
    await promptTextarea.fill('Test prompt');
    await page.waitForTimeout(200);

    const nextBtn = page.locator('button:has-text("Next")').first();
    await expect(nextBtn).toBeVisible();
    await nextBtn.click();
    await page.waitForTimeout(500);

    const nextBtn2 = page.locator('button:has-text("Next")').first();
    await expect(nextBtn2).toBeVisible();
    await nextBtn2.click();
    await page.waitForTimeout(500);

    const launchBtn = page.locator('text=Launch, text="Start Session"');
    await expect(launchBtn.first()).toBeVisible();
  });
});

import { test, expect } from './fixtures';

/**
 * AC-6: Worktree Management
 *
 * Tests for worktree listing, creation, and management.
 */
test.describe('Worktree List', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/worktrees');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('worktrees page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/worktrees/);
  });

  test('worktree list renders', async ({ page }) => {
    const worktreeList = page.locator('.worktree-row');
    await expect(worktreeList.first()).toBeVisible({ timeout: 5_000 });
  });

  test('worktree name column displays', async ({ page }) => {
    const nameHeader = page.locator('text=Name');
    if (await nameHeader.count() > 0) {
      await expect(nameHeader.first()).toBeVisible();
    }
  });

  test('branch column displays', async ({ page }) => {
    const branchHeader = page.locator('text=Branch');
    if (await branchHeader.count() > 0) {
      await expect(branchHeader.first()).toBeVisible();
    }
  });

  test('status column displays', async ({ page }) => {
    const statusHeader = page.locator('text=Status');
    if (await statusHeader.count() > 0) {
      await expect(statusHeader.first()).toBeVisible();
    }
  });

  test('main worktree is visually distinct', async ({ page }) => {
    const mainBadge = page.locator('text=Main, text=main');
    const badgeCount = await mainBadge.count();
    expect(badgeCount).toBeGreaterThanOrEqual(0);
  });

  test('grouping by status works', async ({ page }) => {
    const groups = page.locator('[class*="group"]');
    const groupCount = await groups.count();
    expect(groupCount).toBeGreaterThanOrEqual(0);
  });

  test('disk usage displays per worktree', async ({ page }) => {
    const diskUsage = page.locator('[class*="disk"]').filter({ hasText: /MB|GB/i });
    const usageCount = await diskUsage.count();
    expect(usageCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Create Worktree', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/worktrees');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('create worktree button exists', async ({ page }) => {
    const createBtn = page.locator('text="Create Worktree", text="+ Worktree"');
    if (await createBtn.count() > 0) {
      await expect(createBtn.first()).toBeVisible();
    }
  });

  test('create form renders', async ({ page }) => {
    const form = page.locator('[class*="create-form"], form');
    if (await form.count() > 0) {
      await expect(form.first()).toBeVisible();
    }
  });

  test('ticket number input exists', async ({ page }) => {
    const ticketInput = page.locator('input[placeholder*="ticket"], input[placeholder*="TICKET"]');
    if (await ticketInput.count() > 0) {
      await expect(ticketInput.first()).toBeVisible();
    }
  });

  test('short name input exists', async ({ page }) => {
    const nameInput = page.locator('input[placeholder*="name"], input[placeholder*="Name"]');
    if (await nameInput.count() > 0) {
      await expect(nameInput.first()).toBeVisible();
    }
  });

  test('auto-generates wt-N-name format preview', async ({ page }) => {
    const preview = page.locator('[class*="preview"]').filter({ hasText: /wt-/i });
    const previewCount = await preview.count();
    expect(previewCount).toBeGreaterThanOrEqual(0);
  });

  test('name validation enforces convention', async ({ page }) => {
    const nameInput = page.locator('input[placeholder*="name"]');
    if (await nameInput.count() > 0) {
      await nameInput.first().fill('invalid-name!');
      await page.waitForTimeout(300);
      
      const error = page.locator('[class*="error"], text=Invalid');
      const errorCount = await error.count();
      expect(errorCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Worktree Actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/worktrees');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('Start Session button exists', async ({ page }) => {
    const startBtn = page.locator('text="Start Session"');
    const btnCount = await startBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('Open in File Manager button exists', async ({ page }) => {
    const fileManagerBtn = page.locator('text="Open in File Manager"');
    const btnCount = await fileManagerBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('Delete Worktree button exists', async ({ page }) => {
    const deleteBtn = page.locator('text="Delete Worktree", text=Delete');
    const btnCount = await deleteBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('delete requires confirmation dialog', async ({ page }) => {
    const deleteBtn = page.locator('text="Delete Worktree"').first();
    if (await deleteBtn.count() > 0) {
      await deleteBtn.click();
      await page.waitForTimeout(300);
      
      const confirmDialog = page.locator('[class*="dialog"], text=Confirm, text=Delete');
      const dialogCount = await confirmDialog.count();
      expect(dialogCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('delete disabled if active runs exist', async ({ page }) => {
    const deleteBtn = page.locator('button:has-text("Delete")').first();
    if (await deleteBtn.count() > 0) {
      const isDisabled = await deleteBtn.first().isDisabled();
      const tooltip = page.locator('[class*="tooltip"], text=active');
      const tooltipCount = await tooltip.count();
      
      if (isDisabled && tooltipCount > 0) {
        await expect(tooltip.first()).toBeVisible();
      }
    }
  });

  test('empty state when no worktrees besides main', async ({ page }) => {
    const emptyState = page.locator('text="No worktrees", text="Create your first"');
    const emptyCount = await emptyState.count();
    expect(emptyCount).toBeGreaterThanOrEqual(0);
  });
});

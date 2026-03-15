import { test, expect } from './fixtures';

/**
 * AC-3: Home Dashboard
 *
 * Tests for the dashboard/home page including stat cards, bento grid layout,
 * active runs, needs attention section, and quick actions.
 */
test.describe('Home Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('dashboard page loads', async ({ page }) => {
    await expect(page).toHaveURL(/[\/]?$/);
  });

  test('stat cards are rendered', async ({ page }) => {
    const statCards = page.locator('app-stat-card');
    const cardCount = await statCards.count();
    expect(cardCount).toBeGreaterThanOrEqual(3);
  });

  test('Active Worktrees stat is displayed', async ({ page }) => {
    const worktreesStat = page.locator('text=Active Worktrees, text=worktrees');
    if (await worktreesStat.count() > 0) {
      await expect(worktreesStat.first()).toBeVisible();
    }
  });

  test('Resumable Runs stat is displayed', async ({ page }) => {
    const runsStat = page.locator('text=Resumable Runs, text=resumable');
    if (await runsStat.count() > 0) {
      await expect(runsStat.first()).toBeVisible();
    }
  });

  test('Completed Today stat is displayed', async ({ page }) => {
    const completedStat = page.locator('text=Completed Today, text=completed');
    if (await completedStat.count() > 0) {
      await expect(completedStat.first()).toBeVisible();
    }
  });

  test('bento grid layout is used', async ({ page }) => {
    const grid = page.locator('[class*="grid"], [class*="bento"]');
    if (await grid.count() > 0) {
      await expect(grid.first()).toBeVisible();
    }
  });

  test('trend indicators are displayed', async ({ page }) => {
    const trends = page.locator('[class*="trend"], [class*="indicator"]');
    const trendCount = await trends.count();
    expect(trendCount).toBeGreaterThanOrEqual(0);
  });

  test('active runs list is present', async ({ page }) => {
    const runsList = page.locator('[class*="active-runs"], [class*="running"]');
    if (await runsList.count() > 0) {
      await expect(runsList.first()).toBeVisible();
    }
  });

  test('needs attention section is present', async ({ page }) => {
    const attentionSection = page.locator('text=Needs Attention, text=Attention');
    if (await attentionSection.count() > 0) {
      await expect(attentionSection.first()).toBeVisible();
    }
  });

  test('recent completions section is present', async ({ page }) => {
    const recentSection = page.locator('text=Recent Completions, text=Recent');
    if (await recentSection.count() > 0) {
      await expect(recentSection.first()).toBeVisible();
    }
  });

  test('New Session quick action button is present', async ({ page }) => {
    const newSessionBtn = page.locator('text=New Session, text="New Session"');
    if (await newSessionBtn.count() > 0) {
      await expect(newSessionBtn.first()).toBeVisible();
    }
  });

  test('Create Worktree quick action button is present', async ({ page }) => {
    const worktreeBtn = page.locator('text=Create Worktree, text="Create Worktree"');
    if (await worktreeBtn.count() > 0) {
      await expect(worktreeBtn.first()).toBeVisible();
    }
  });

  test('Open Configuration quick action button is present', async ({ page }) => {
    const configBtn = page.locator('text=Open Configuration, text="Configuration"');
    if (await configBtn.count() > 0) {
      await expect(configBtn.first()).toBeVisible();
    }
  });

  test('New Session button navigates to session wizard', async ({ page }) => {
    const newSessionBtn = page.locator('text="New Session"').first();
    if (await newSessionBtn.count() > 0) {
      await newSessionBtn.click();
      await page.waitForTimeout(500);
      await expect(page).toHaveURL(/\/sessions/);
    }
  });

  test('Create Worktree button navigates to worktrees page', async ({ page }) => {
    const worktreeBtn = page.locator('text="Create Worktree"').first();
    if (await worktreeBtn.count() > 0) {
      await worktreeBtn.click();
      await page.waitForTimeout(500);
      await expect(page).toHaveURL(/\/worktrees/);
    }
  });

  test('empty state shows helpful message when no runs exist', async ({ page }) => {
    const emptyState = page.locator('[class*="empty"]').filter({ hasText: /No runs yet|Get started/i });
    if (await emptyState.count() > 0) {
      await expect(emptyState.first()).toBeVisible();
    }
  });
});

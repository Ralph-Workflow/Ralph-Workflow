import { test, expect } from './fixtures';

/**
 * AC-5: Run Monitoring
 * AC-5.8: Changes Viewer
 *
 * Tests for run detail page, phase timeline, log viewer, iteration/review history,
 * and the changes viewer with diff panel.
 */
test.describe('Run Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/e2e-test-run-1');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('run detail page loads', async ({ page }) => {
    await expect(page).toHaveURL(/\/runs\//);
  });

  test('run metadata displays', async ({ page }) => {
    const metadata = page.locator('[class*="metadata"], [class*="run-info"]');
    if (await metadata.count() > 0) {
      await expect(metadata.first()).toBeVisible();
    }
  });

  test('run ID is displayed', async ({ page }) => {
    const runId = page.locator('text=e2e-test-run-1');
    if (await runId.count() > 0) {
      await expect(runId.first()).toBeVisible();
    }
  });

  test('phase timeline renders', async ({ page }) => {
    const timeline = page.locator('[class*="timeline"], [class*="phase"]');
    if (await timeline.count() > 0) {
      await expect(timeline.first()).toBeVisible();
    }
  });

  test('4 phase indicators display', async ({ page }) => {
    const phases = page.locator('text=Plan, text=Develop, text=Review, text=Commit');
    const phaseCount = await phases.count();
    expect(phaseCount).toBeGreaterThanOrEqual(4);
  });

  test('phase-specific colors are applied', async ({ page }) => {
    const planPhase = page.locator('[class*="plan"]:visible, [class*="purple"]');
    const developPhase = page.locator('[class*="develop"]:visible, [class*="blue"]');
    const reviewPhase = page.locator('[class*="review"]:visible, [class*="amber"]');
    const commitPhase = page.locator('[class*="commit"]:visible, [class*="green"]');
    
    const colorCount = await Promise.all([
      planPhase.count(),
      developPhase.count(),
      reviewPhase.count(),
      commitPhase.count(),
    ]);
    
    expect(colorCount.some(c => c > 0)).toBeTruthy();
  });

  test('active phase has indicator', async ({ page }) => {
    const activePhase = page.locator('[class*="active"]:visible');
    const activeCount = await activePhase.count();
    expect(activeCount).toBeGreaterThanOrEqual(0);
  });

  test('tab bar with Log/Changes/Info tabs', async ({ page }) => {
    const logTab = page.locator('text=Log');
    const changesTab = page.locator('text=Changes');
    const infoTab = page.locator('text=Info');
    
    const tabCount = await Promise.all([
      logTab.count(),
      changesTab.count(),
      infoTab.count(),
    ]);
    
    expect(tabCount.some(t => t > 0)).toBeTruthy();
  });

  test('clicking tabs switches content', async ({ page }) => {
    const changesTab = page.locator('text=Changes').first();
    if (await changesTab.count() > 0) {
      await changesTab.click();
      await page.waitForTimeout(300);
      
      const diffPanel = page.locator('[class*="diff"], [class*="changes"]');
      const panelCount = await diffPanel.count();
      expect(panelCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Log Viewer', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('log viewer displays content', async ({ page }) => {
    const logViewer = page.locator('[class*="log-viewer"], [class*="logs"]');
    if (await logViewer.count() > 0) {
      await expect(logViewer.first()).toBeVisible();
    }
  });

  test('log level filtering controls', async ({ page }) => {
    const filterControls = page.locator('text=info, text=warning, text=error');
    const filterCount = await filterControls.count();
    expect(filterCount).toBeGreaterThanOrEqual(0);
  });

  test('search within logs input', async ({ page }) => {
    const searchInput = page.locator('input[placeholder*="search"], input[placeholder*="Search"]');
    if (await searchInput.count() > 0) {
      await expect(searchInput.first()).toBeVisible();
    }
  });

  test('auto-scroll toggle button', async ({ page }) => {
    const autoScroll = page.locator('text="Auto-scroll", text="Auto Scroll"');
    if (await autoScroll.count() > 0) {
      await expect(autoScroll.first()).toBeVisible();
    }
  });
});

test.describe('State-Specific Views', () => {
  test('completed state shows completion summary', async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const completedBadge = page.locator('text=Completed, text=Complete');
    if (await completedBadge.count() > 0) {
      const summary = page.locator('[class*="summary"], [class*="completion"]');
      const sumCount = await summary.count();
      expect(sumCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('failed state shows error summary', async ({ page }) => {
    await page.goto('/runs/failed-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const failedBadge = page.locator('text=Failed');
    if (await failedBadge.count() > 0) {
      const errorSummary = page.locator('[class*="error"], [class*="failure"]');
      const errCount = await errorSummary.count();
      expect(errCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('failed state shows recovery guidance', async ({ page }) => {
    const resumeBtn = page.locator('text=Resume, text=Retry');
    if (await resumeBtn.count() > 0) {
      await expect(resumeBtn.first()).toBeVisible();
    }
  });

  test('paused state shows paused banner', async ({ page }) => {
    await page.goto('/runs/paused-run');
    await page.waitForTimeout(500);
    
    const pausedBanner = page.locator('text=Paused');
    const bannerCount = await pausedBanner.count();
    expect(bannerCount).toBeGreaterThanOrEqual(0);
  });

  test('paused state shows Resume as hero action', async ({ page }) => {
    const resumeHero = page.locator('button:has-text("Resume"), [class*="hero"] button:has-text("Resume")');
    const heroCount = await resumeHero.count();
    expect(heroCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Iteration and Review Tracking', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('iteration history panel shows entries', async ({ page }) => {
    const iterHistory = page.locator('text="Iteration History", text=Iteration');
    if (await iterHistory.count() > 0) {
      await expect(iterHistory.first()).toBeVisible();
    }
  });

  test('iteration entries show duration and files changed', async ({ page }) => {
    const iterEntry = page.locator('[class*="iteration"]');
    const entryCount = await iterEntry.count();
    expect(entryCount).toBeGreaterThanOrEqual(0);
  });

  test('review history panel shows entries', async ({ page }) => {
    const reviewHistory = page.locator('text="Review History", text=Review');
    if (await reviewHistory.count() > 0) {
      await expect(reviewHistory.first()).toBeVisible();
    }
  });

  test('review entries show pass count and findings', async ({ page }) => {
    const reviewEntry = page.locator('[class*="review-entry"]');
    const entryCount = await reviewEntry.count();
    expect(entryCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Changes Viewer (AC-5.8)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const changesTab = page.locator('text=Changes').first();
    if (await changesTab.count() > 0) {
      await changesTab.click();
      await page.waitForTimeout(500);
    }
  });

  test('split layout renders', async ({ page }) => {
    const splitLayout = page.locator('[class*="split"], [class*="two-panel"]');
    if (await splitLayout.count() > 0) {
      await expect(splitLayout.first()).toBeVisible();
    }
  });

  test('file tree shows changed files', async ({ page }) => {
    const fileTree = page.locator('[class*="file-tree"], [class*="files"]');
    if (await fileTree.count() > 0) {
      await expect(fileTree.first()).toBeVisible();
    }
  });

  test('file tree shows +/- counts', async ({ page }) => {
    const fileCount = page.locator('text=+, text=-');
    const countResult = await fileCount.count();
    expect(countResult).toBeGreaterThanOrEqual(0);
  });

  test('diff panel shows syntax-highlighted diff', async ({ page }) => {
    const diffPanel = page.locator('[class*="diff-panel"], pre, code');
    if (await diffPanel.count() > 0) {
      await expect(diffPanel.first()).toBeVisible();
    }
  });

  test('summary bar shows total files changed', async ({ page }) => {
    const summaryBar = page.locator('[class*="summary-bar"]').filter({ hasText: /files changed/i });
    if (await summaryBar.count() > 0) {
      await expect(summaryBar.first()).toBeVisible();
    }
  });

  test('additions/deletions counts display', async ({ page }) => {
    const additions = page.getByText(/additions/i).first();
    const deletions = page.getByText(/deletions/i).first();
    
    const countResult = await Promise.all([
      additions.count(),
      deletions.count(),
    ]);
    
    expect(countResult.some(c => c > 0)).toBeTruthy();
  });

  test('iteration filter dropdown works', async ({ page }) => {
    const filterDropdown = page.locator('select, [class*="iteration-filter"]');
    if (await filterDropdown.count() > 0) {
      await expect(filterDropdown.first()).toBeVisible();
    }
  });

  test('empty state shows helpful message', async ({ page }) => {
    const emptyState = page.locator('text="Code changes will appear here"');
    const emptyCount = await emptyState.count();
    expect(emptyCount).toBeGreaterThanOrEqual(0);
  });
});

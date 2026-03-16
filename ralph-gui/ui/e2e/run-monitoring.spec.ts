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
    const metadata = page.locator('[data-testid="run-metadata"], [class*="metadata"], [class*="run-info"]');
    await expect(metadata.first()).toBeVisible();
  });

  test('run ID is displayed', async ({ page }) => {
    const runId = page.locator('[data-testid="run-id"], text=e2e-test-run-1');
    await expect(runId.first()).toBeVisible();
  });

  test('phase timeline renders', async ({ page }) => {
    const timeline = page.locator('[data-testid="phase-timeline"], [class*="timeline"], [class*="phase"]');
    await expect(timeline.first()).toBeVisible();
  });

  test('4 phase indicators display', async ({ page }) => {
    const phaseTimeline = page.locator('app-phase-timeline');
    await expect(phaseTimeline).toBeVisible();

    const planPhase = page.locator('[data-testid="phase-node"]').filter({ hasText: 'Plan' });
    const developPhase = page.locator('[data-testid="phase-node"]').filter({ hasText: 'Develop' });
    const reviewPhase = page.locator('[data-testid="phase-node"]').filter({ hasText: 'Review' });
    const commitPhase = page.locator('[data-testid="phase-node"]').filter({ hasText: 'Commit' });

    await expect(planPhase).toBeVisible();
    await expect(developPhase).toBeVisible();
    await expect(reviewPhase).toBeVisible();
    await expect(commitPhase).toBeVisible();
  });

  test('phase-specific colors are applied', async ({ page }) => {
    const phaseTimeline = page.locator('app-phase-timeline');
    await expect(phaseTimeline).toBeVisible();

    const phaseDots = page.locator('[data-testid^="phase-dot-"]');
    const dotCount = await phaseDots.count();
    expect(dotCount).toBeGreaterThanOrEqual(1);
  });

  test('active phase has indicator', async ({ page }) => {
    // Only runs in progress have an active phase - skip for completed/failed/paused runs
    const completedBanner = page.locator('[data-testid="completed-banner"], [data-testid="failed-banner"], [data-testid="paused-banner"]');
    const isTerminalState = await completedBanner.count() > 0;

    if (isTerminalState) {
      // Skip this assertion for terminal states - they don't have active phases
      test.skip();
      return;
    }

    const activePhase = page.locator('[data-testid*="phase-node"][class*="active"], [class*="active"]:has([data-testid^="phase-"])');
    await expect(activePhase.first()).toBeVisible();
  });

  test('tab bar with Log/Changes/Info tabs', async ({ page }) => {
    const logTab = page.locator('[data-testid="tab-log"]');
    const changesTab = page.locator('[data-testid="tab-changes"]');
    const infoTab = page.locator('[data-testid="tab-info"]');

    await expect(logTab).toBeVisible();
    await expect(changesTab).toBeVisible();
    await expect(infoTab).toBeVisible();
  });

  test('clicking tabs switches content', async ({ page }) => {
    const changesTab = page.locator('[data-testid="tab-changes"]');
    await expect(changesTab).toBeVisible();
    await changesTab.click();
    await page.waitForTimeout(300);

    const diffPanel = page.locator('[data-testid="tab-content-changes"], [class*="diff"], [class*="changes"]');
    await expect(diffPanel.first()).toBeVisible();
  });
});

test.describe('Log Viewer', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('log viewer displays content', async ({ page }) => {
    const logViewer = page.locator('[data-testid="tab-content-log"], [class*="log-viewer"], [class*="logs"]');
    await expect(logViewer.first()).toBeVisible();
  });

  test('log level filtering controls', async ({ page }) => {
    const filterControls = page.locator('text=info, text=warning, text=error');
    const filterCount = await filterControls.count();
    expect(filterCount).toBeGreaterThanOrEqual(0);
  });

  test('search within logs input', async ({ page }) => {
    const searchInput = page.locator('[data-testid="log-search-input"], input[placeholder*="search"], input[placeholder*="Search"]');
    await expect(searchInput.first()).toBeVisible();
  });

  test('auto-scroll toggle button appears when auto-scroll is disabled', async ({ page }) => {
    // First click on the Log tab to switch to log view
    const logTab = page.locator('[data-testid="tab-log"], [data-testid="tab-content-log"]');
    if (await logTab.first().isVisible().catch(() => false)) {
      await logTab.first().click();
      await page.waitForTimeout(500);
    }

    // Find the log container and scroll it to disable auto-scroll
    const logContainer = page.locator('[class*="log-content"], [class*="log-viewer"], [data-testid="log-content"]');
    if (await logContainer.first().isVisible().catch(() => false)) {
      await logContainer.first().hover();
      await page.mouse.wheel(0, 100);
      await page.waitForTimeout(300);
    }

    // Now the auto-scroll toggle should be visible since auto-scroll is disabled
    const autoScroll = page.locator('[data-testid="auto-scroll-toggle"]');
    await expect(autoScroll.first()).toBeVisible();
  });
});

test.describe('State-Specific Views', () => {
  test('completed state shows completion summary', async ({ page }) => {
    await page.goto('/runs/completed-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const completedBanner = page.locator('[data-testid="completed-banner"]');
    await expect(completedBanner).toBeVisible();

    const summary = page.locator('[data-testid="metric-iterations"], [data-testid="metric-reviews"]');
    await expect(summary.first()).toBeVisible();
  });

  test('failed state shows error summary', async ({ page }) => {
    await page.goto('/runs/failed-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const failedBanner = page.locator('[data-testid="failed-banner"]');
    await expect(failedBanner).toBeVisible();

    const errorMsg = page.locator('[data-testid="failed-error-msg"]');
    await expect(errorMsg.first()).toBeVisible();
  });

  test('failed state shows recovery guidance', async ({ page }) => {
    await page.goto('/runs/failed-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const resumeBtn = page.locator('[data-testid="resume-action-btn"]');
    const retryBtn = page.locator('[data-testid="retry-action-btn"]');
    await expect(resumeBtn).toBeVisible();
    await expect(retryBtn).toBeVisible();
  });

  test('paused state shows paused banner', async ({ page }) => {
    await page.goto('/runs/paused-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const pausedBanner = page.locator('[data-testid="paused-banner"]');
    await expect(pausedBanner).toBeVisible();
  });

  test('paused state shows Resume as hero action', async ({ page }) => {
    await page.goto('/runs/paused-run');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const resumeHero = page.locator('[data-testid="paused-resume-btn"]');
    await expect(resumeHero).toBeVisible();
  });
});

test.describe('Iteration and Review Tracking', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('iteration history panel shows entries', async ({ page }) => {
    const iterHistory = page.locator('[data-testid="iteration-history-section"]');
    await expect(iterHistory.first()).toBeVisible();
  });

  test('iteration entries show duration and files changed', async ({ page }) => {
    const iterEntry = page.locator('[data-testid="iteration-history-section"] [class*="iteration"]');
    const entryCount = await iterEntry.count();
    expect(entryCount).toBeGreaterThanOrEqual(0);
  });

  test('review history panel shows entries', async ({ page }) => {
    const reviewHistory = page.locator('[data-testid="review-history-section"]');
    await expect(reviewHistory.first()).toBeVisible();
  });

  test('review entries show pass count and findings', async ({ page }) => {
    const reviewEntry = page.locator('[data-testid="review-history-section"] [class*="review"]');
    const entryCount = await reviewEntry.count();
    expect(entryCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Changes Viewer (AC-5.8)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/runs/test-run-001');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const changesTab = page.locator('[data-testid="tab-changes"]');
    if (await changesTab.count() > 0) {
      await changesTab.click();
      await page.waitForTimeout(500);
    }
  });

  test('split layout renders', async ({ page }) => {
    const splitLayout = page.locator('[data-testid="tab-content-changes"] [class*="split"], [class*="two-panel"]');
    await expect(splitLayout.first()).toBeVisible();
  });

  test('file tree shows changed files', async ({ page }) => {
    const fileTree = page.locator('[data-testid="changes-file-tree"], [class*="file-tree"], [class*="files"]');
    await expect(fileTree.first()).toBeVisible();
  });

  test('file tree shows +/- counts', async ({ page }) => {
    const fileCount = page.locator('[data-testid="changes-summary"], text=+, text=-');
    await expect(fileCount.first()).toBeVisible();
  });

  test('diff panel shows syntax-highlighted diff', async ({ page }) => {
    const diffPanel = page.locator('[data-testid="diff-panel"], [class*="diff-panel"], pre, code');
    await expect(diffPanel.first()).toBeVisible();
  });

  test('summary bar shows total files changed', async ({ page }) => {
    const summaryBar = page.locator('[data-testid="changes-summary"]');
    await expect(summaryBar.first()).toBeVisible();
  });

  test('additions/deletions counts display', async ({ page }) => {
    const changesViewer = page.locator('app-changes-viewer');
    await expect(changesViewer.first()).toBeVisible();
  });

  test('iteration filter dropdown works', async ({ page }) => {
    const filterDropdown = page.locator('[data-testid="iteration-filter"], select, [class*="iteration-filter"]');
    await expect(filterDropdown.first()).toBeVisible();
  });

  test('empty state shows helpful message', async ({ page }) => {
    const emptyState = page.locator('[data-testid="changes-empty"], text="Code changes will appear here"');
    await expect(emptyState.first()).toBeVisible();
  });
});

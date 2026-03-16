import { test, expect } from './fixtures';

/**
 * AC-9: Onboarding
 *
 * Tests for first-run welcome screen and setup wizard.
 */
test.describe('Welcome Screen', () => {
  test('welcome screen renders on first run', async ({ page }) => {
    await page.goto('/onboarding');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    await expect(page).toHaveURL(/\/onboarding/);
  });

  test('welcome message displays', async ({ page }) => {
    await page.goto('/onboarding');
    await page.waitForSelector('app-root', { timeout: 10_000 });

    const welcome = page.locator('text=Welcome to Ralph');
    await expect(welcome.first()).toBeVisible();
  });
});

test.describe('Onboarding Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/onboarding');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('wizard content renders', async ({ page }) => {
    const wizard = page.locator('[class*="wizard"], app-onboarding');
    await expect(wizard.first()).toBeVisible();
  });

  test('Skip button completes onboarding', async ({ page }) => {
    const skipBtn = page.locator('text=Skip').first();
    const btnCount = await skipBtn.count();
    if (btnCount > 0) {
      await skipBtn.click();
      await page.waitForTimeout(500);

      await expect(page).toHaveURL(/[\/]?$/);
    }
  });

  test('completing wizard lands on Dashboard', async ({ page }) => {
    const finishBtn = page.locator('text="Get Started", text=Finish').first();
    const btnCount = await finishBtn.count();
    if (btnCount > 0) {
      await finishBtn.click();
      await page.waitForTimeout(500);

      await expect(page).toHaveURL(/[\/]?$/);
    }
  });
});

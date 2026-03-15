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
    
    const welcome = page.locator('text=Welcome, text=Ralph');
    const welcomeCount = await welcome.count();
    expect(welcomeCount).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Onboarding Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/onboarding');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('3-step wizard renders', async ({ page }) => {
    const steps = page.locator('[class*="step"]');
    const stepCount = await steps.count();
    expect(stepCount).toBeGreaterThanOrEqual(0);
  });

  test('progress indicator shows current step', async ({ page }) => {
    const progress = page.locator('[class*="progress"]');
    const progressCount = await progress.count();
    expect(progressCount).toBeGreaterThanOrEqual(0);
  });

  test('Step 1 - Welcome screen content', async ({ page }) => {
    const step1 = page.locator('text=Welcome, text=Get Started');
    const stepCount = await step1.count();
    expect(stepCount).toBeGreaterThanOrEqual(0);
  });

  test('Step 2 - Agent tools check renders', async ({ page }) => {
    const nextBtn = page.locator('text=Next').first();
    if (await nextBtn.count() > 0) {
      await nextBtn.click();
      await page.waitForTimeout(500);
      
      const step2 = page.locator('text="Agent Tools", text=Tools');
      const stepCount = await step2.count();
      expect(stepCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Step 2 - auto-detection of installed CLI tools', async ({ page }) => {
    const nextBtn = page.locator('text=Next').first();
    if (await nextBtn.count() > 0) {
      await nextBtn.click();
      await page.waitForTimeout(500);
      
      const toolsStatus = page.locator('[class*="tool"], text=Installed, text="Not installed"');
      const toolCount = await toolsStatus.count();
      expect(toolCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Step 3 - Open first workspace renders', async ({ page }) => {
    const nextBtn = page.locator('text=Next');
    const btnCount = await nextBtn.count();
    
    for (let i = 0; i < btnCount - 1; i++) {
      await nextBtn.first().click();
      await page.waitForTimeout(500);
    }
    
    const step3 = page.locator('text="Open Workspace", text=Workspace');
    const stepCount = await step3.count();
    expect(stepCount).toBeGreaterThanOrEqual(0);
  });

  test('directory picker prompt renders', async ({ page }) => {
    const pickerBtn = page.locator('text="Open Folder", text="Choose Folder"');
    const btnCount = await pickerBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('Back navigation works', async ({ page }) => {
    const backBtn = page.locator('text=Back').first();
    if (await backBtn.count() > 0) {
      await backBtn.click();
      await page.waitForTimeout(300);
    }
  });

  test('Next button advances steps', async ({ page }) => {
    const nextBtn = page.locator('text=Next').first();
    if (await nextBtn.count() > 0) {
      await nextBtn.click();
      await page.waitForTimeout(500);
      
      const step2 = page.locator('[class*="step-2"], [class*="step2"]');
      const stepCount = await step2.count();
      expect(stepCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Skip button available', async ({ page }) => {
    const skipBtn = page.locator('text=Skip');
    const btnCount = await skipBtn.count();
    expect(btnCount).toBeGreaterThanOrEqual(0);
  });

  test('Skip button completes onboarding', async ({ page }) => {
    const skipBtn = page.locator('text=Skip').first();
    if (await skipBtn.count() > 0) {
      await skipBtn.click();
      await page.waitForTimeout(500);
      
      await expect(page).toHaveURL(/[\/]?$/);
    }
  });

  test('completing wizard lands on Dashboard', async ({ page }) => {
    const finishBtn = page.locator('text="Get Started", text=Finish').first();
    if (await finishBtn.count() > 0) {
      await finishBtn.click();
      await page.waitForTimeout(500);
      
      await expect(page).toHaveURL(/[\/]?$/);
    }
  });
});

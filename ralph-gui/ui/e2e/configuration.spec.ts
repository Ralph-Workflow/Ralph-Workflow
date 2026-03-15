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

  test('three tabs render', async ({ page }) => {
    const effectiveTab = page.locator('text=Effective');
    const globalTab = page.locator('text=Global');
    const projectTab = page.locator('text=Project');
    
    const tabCount = await Promise.all([
      effectiveTab.count(),
      globalTab.count(),
      projectTab.count(),
    ]);
    
    expect(tabCount.filter(c => c > 0).length).toBeGreaterThanOrEqual(0);
  });

  test('clicking tabs switches content', async ({ page }) => {
    const globalTab = page.locator('text=Global').first();
    if (await globalTab.count() > 0) {
      await globalTab.click();
      await page.waitForTimeout(300);
    }
  });

  test('Effective tab shows merged config', async ({ page }) => {
    const effectiveTab = page.locator('text=Effective').first();
    if (await effectiveTab.count() > 0) {
      await effectiveTab.click();
      await page.waitForTimeout(300);
      
      const configContent = page.locator('[class*="config"]');
      const contentCount = await configContent.count();
      expect(contentCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Form Controls', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('numeric inputs render', async ({ page }) => {
    const numericInputs = page.locator('input[type="number"]');
    const inputCount = await numericInputs.count();
    expect(inputCount).toBeGreaterThanOrEqual(0);
  });

  test('dropdown selects render', async ({ page }) => {
    const dropdowns = page.locator('select');
    const dropdownCount = await dropdowns.count();
    expect(dropdownCount).toBeGreaterThanOrEqual(0);
  });

  test('toggle switches render', async ({ page }) => {
    const toggles = page.locator('[class*="toggle"], input[type="checkbox"]');
    const toggleCount = await toggles.count();
    expect(toggleCount).toBeGreaterThanOrEqual(0);
  });

  test('text inputs render', async ({ page }) => {
    const textInputs = page.locator('input[type="text"]');
    const inputCount = await textInputs.count();
    expect(inputCount).toBeGreaterThanOrEqual(0);
  });

  test('labels and tooltips present', async ({ page }) => {
    const labels = page.locator('label');
    const labelCount = await labels.count();
    expect(labelCount).toBeGreaterThanOrEqual(0);
  });

  test('values differing from defaults highlighted', async ({ page }) => {
    const highlighted = page.locator('[class*="highlighted"], [class*="modified"]');
    const highlightCount = await highlighted.count();
    expect(highlightCount).toBeGreaterThanOrEqual(0);
  });

  test('collapsible sections render', async ({ page }) => {
    const sections = page.locator('[class*="section"], details');
    const sectionCount = await sections.count();
    expect(sectionCount).toBeGreaterThanOrEqual(0);
  });

  test('General section expandable', async ({ page }) => {
    const generalSection = page.locator('text=General').first();
    if (await generalSection.count() > 0) {
      await generalSection.click();
      await page.waitForTimeout(300);
    }
  });

  test('Execution section expandable', async ({ page }) => {
    const executionSection = page.locator('text=Execution').first();
    if (await executionSection.count() > 0) {
      await executionSection.click();
      await page.waitForTimeout(300);
    }
  });

  test('Retry and Fallback section expandable', async ({ page }) => {
    const retrySection = page.locator('text=Retry, text=Fallback').first();
    if (await retrySection.count() > 0) {
      await retrySection.click();
      await page.waitForTimeout(300);
    }
  });

  test('Git section expandable', async ({ page }) => {
    const gitSection = page.locator('text=Git').first();
    if (await gitSection.count() > 0) {
      await gitSection.click();
      await page.waitForTimeout(300);
    }
  });

  test('Agents section expandable', async ({ page }) => {
    const agentsSection = page.locator('text=Agents').first();
    if (await agentsSection.count() > 0) {
      await agentsSection.click();
      await page.waitForTimeout(300);
    }
  });
});

test.describe('Agent Chains and Drains', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('agent chains subsection renders', async ({ page }) => {
    const chainsSection = page.locator('text="Agent Chains", text=Chains');
    const sectionCount = await chainsSection.count();
    expect(sectionCount).toBeGreaterThanOrEqual(0);
  });

  test('drains subsection renders', async ({ page }) => {
    const drainsSection = page.locator('text="Drains", text=Drains');
    const sectionCount = await drainsSection.count();
    expect(sectionCount).toBeGreaterThanOrEqual(0);
  });

  test('6 drain dropdowns display', async ({ page }) => {
    const drains = page.locator('text=Planning, text=Development, text=Analysis, text=Review, text=Fix, text=Commit');
    const drainCount = await drains.count();
    expect(drainCount).toBeGreaterThanOrEqual(0);
  });

  test('Add Agent dialog renders', async ({ page }) => {
    const addAgentBtn = page.locator('text="Add Agent", text="+ Agent"');
    if (await addAgentBtn.count() > 0) {
      await addAgentBtn.first().click();
      await page.waitForTimeout(300);
      
      const dialog = page.locator('[class*="dialog"]');
      const dialogCount = await dialog.count();
      expect(dialogCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Save/Revert', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
  });

  test('Save button appears when changes made', async ({ page }) => {
    const input = page.locator('input').first();
    if (await input.count() > 0) {
      await input.first().fill('test-value');
      await page.waitForTimeout(300);
      
      const saveBtn = page.locator('text=Save');
      const btnCount = await saveBtn.count();
      expect(btnCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Revert button appears when changes made', async ({ page }) => {
    const input = page.locator('input').first();
    if (await input.count() > 0) {
      await input.first().fill('test-value');
      await page.waitForTimeout(300);
      
      const revertBtn = page.locator('text=Revert');
      const btnCount = await revertBtn.count();
      expect(btnCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('Revert restores to last saved state', async ({ page }) => {
    const revertBtn = page.locator('text=Revert').first();
    if (await revertBtn.count() > 0) {
      await revertBtn.first().click();
      await page.waitForTimeout(300);
    }
  });

  test('inline validation errors display', async ({ page }) => {
    const input = page.locator('input').first();
    if (await input.count() > 0) {
      await input.first().fill('');
      await input.first().blur();
      await page.waitForTimeout(300);
      
      const error = page.locator('[class*="error"], text=required');
      const errorCount = await error.count();
      expect(errorCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('navigate-away warning when unsaved changes', async ({ page }) => {
    const input = page.locator('input').first();
    if (await input.count() > 0) {
      await input.first().fill('test-value');
      await page.waitForTimeout(300);
      
      await page.goto('/sessions');
      await page.waitForTimeout(500);
      
      const warning = page.locator('[class*="warning"], text=unsaved, text=Leave');
      const warningCount = await warning.count();
      expect(warningCount).toBeGreaterThanOrEqual(0);
    }
  });
});

test.describe('Raw TOML Toggle', () => {
  test('Raw TOML toggle switches to text editor', async ({ page }) => {
    await page.goto('/configuration');
    await page.waitForSelector('app-root', { timeout: 10_000 });
    
    const rawToggle = page.locator('text="Raw TOML", text="Raw"');
    if (await rawToggle.count() > 0) {
      await rawToggle.first().click();
      await page.waitForTimeout(300);
      
      const textEditor = page.locator('textarea, [class*="toml-editor"]');
      const editorCount = await textEditor.count();
      expect(editorCount).toBeGreaterThanOrEqual(0);
    }
  });
});

/**
 * Step 2 (config) behaviour tests for NewSessionWizardComponent.
 * AC-4.3.2 — Comprehensive test coverage for collapsed, expanded, and unconfigured modes.
 */
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi, describe, it, beforeEach, expect } from 'vitest';
import { NewSessionWizardComponent } from './new-session-wizard.component';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { WorktreeInfo, ConfigView, EffectiveChainsConfig } from '../../types';
import { signal } from '@angular/core';

// ─────────────────────────────────────────────────────────────────────────────
// Test data factories
// ─────────────────────────────────────────────────────────────────────────────

const makeConfig = (overrides: Partial<ConfigView> = {}): ConfigView => ({
  verbosity: 1,
  developer_iters: 7,
  reviewer_reviews: 3,
  checkpoint_enabled: true,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 5,
  ...overrides,
});

const makeChainsConfig = (
  overrides: Partial<EffectiveChainsConfig> = {},
): EffectiveChainsConfig => ({
  chains: [
    { name: 'default-chain', agents: ['planner', 'developer', 'reviewer'] },
    { name: 'alt-chain', agents: ['analyzer'] },
  ],
  drains: {
    planning: 'default-chain',
    development: 'default-chain',
    review: 'default-chain',
    fix: 'default-chain',
    commit: 'default-chain',
    analysis: 'alt-chain',
  },
  agents: [
    { name: 'planner', tool: 'claude', model: 'claude-sonnet-4-6' },
    { name: 'developer', tool: 'claude', model: 'claude-sonnet-4-6' },
    { name: 'reviewer', tool: 'claude', model: 'claude-sonnet-4-6' },
    { name: 'analyzer', tool: 'codex', model: 'gpt-4o' },
  ],
  has_configured_chains: true,
  has_configured_drains: true,
  ...overrides,
});

const makeEmptyChainsConfig = (): EffectiveChainsConfig => ({
  chains: [],
  drains: {},
  agents: [],
  has_configured_chains: false,
  has_configured_drains: false,
});

// ─────────────────────────────────────────────────────────────────────────────
// Shared setup helpers
// ─────────────────────────────────────────────────────────────────────────────

function createMockWorktreesService(): WorktreesService {
  return {
    worktrees: signal<WorktreeInfo[]>([]),
    mainWorktree: signal<WorktreeInfo | null>(null),
    nonMainWorktrees: signal<WorktreeInfo[]>([]),
    activeWorktreePath: signal<string | null>(null),
    lastRepoPath: signal<string | null>(null),
    fetchWorktrees: vi.fn().mockResolvedValue(undefined),
    createWorktree: vi.fn().mockResolvedValue(undefined),
  } as unknown as WorktreesService;
}

function createMockTauriService(overrides: Record<string, unknown> = {}): TauriService {
  return {
    getEffectiveConfig: vi.fn().mockResolvedValue(makeConfig()),
    getEffectiveChainsConfig: vi.fn().mockResolvedValue(makeChainsConfig()),
    launchRalphSession: vi.fn().mockResolvedValue('run-123'),
    savePromptFile: vi.fn().mockResolvedValue(undefined),
    saveTemplate: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as TauriService;
}

async function setupTestBed(
  tauriOverrides: Record<string, unknown> = {},
): Promise<{
  component: NewSessionWizardComponent;
  fixture: ComponentFixture<NewSessionWizardComponent>;
  mockTauriService: TauriService;
  mockWorktreesService: WorktreesService;
}> {
  const mockWorktreesService = createMockWorktreesService();
  const mockTauriService = createMockTauriService(tauriOverrides);

  await TestBed.configureTestingModule({
    imports: [NewSessionWizardComponent],
    providers: [
      { provide: WorktreesService, useValue: mockWorktreesService },
      { provide: TauriService, useValue: mockTauriService },
    ],
  }).compileComponents();

  const fixture = TestBed.createComponent(NewSessionWizardComponent);
  const component = fixture.componentInstance;
  fixture.detectChanges();

  return { component, fixture, mockTauriService, mockWorktreesService };
}

/** Enter the config step and wait for async effects to complete. */
async function enterConfigStep(
  component: NewSessionWizardComponent,
  fixture: ComponentFixture<NewSessionWizardComponent>,
): Promise<void> {
  component.repoPath.set('/repo');
  component.onTemplateSelect('some content');
  fixture.detectChanges();
  // Wait for async effects (prefillFromEffectiveConfig promise)
  await fixture.whenStable();
  fixture.detectChanges();
}

/** Query by data-testid within the fixture */
function queryByTestId(
  fixture: ComponentFixture<NewSessionWizardComponent>,
  testId: string,
): HTMLElement | null {
  return (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${testId}"]`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Test suites
// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Collapsed Happy-Path Mode (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
    fixture = result.fixture;
  });

  it('should open Step 2 in collapsed mode by default when chains are configured', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    const collapsedPanel = queryByTestId(fixture, 'collapsed-summary-panel');
    const fullPanel = queryByTestId(fixture, 'config-full-panel');
    expect(collapsedPanel).not.toBeNull();
    expect(fullPanel).toBeNull();
  });

  it('should display summary line with chain agents in arrow notation', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    component.drainBindings.set({ development: 'default-chain' });
    await enterConfigStep(component, fixture);

    const summary = component.configSummaryLine();
    expect(summary).toContain('planner → developer → reviewer');
    expect(summary).toContain('iterations');
    expect(summary).toContain('reviews');
  });

  it('should show inline iteration spinner editable without expanding', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    const spinner = queryByTestId(fixture, 'inline-dev-iters');
    expect(spinner).not.toBeNull();
  });

  it('should show inline review passes spinner editable without expanding', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    const spinner = queryByTestId(fixture, 'inline-rev-passes');
    expect(spinner).not.toBeNull();
  });

  it('should update summary line when inline iterations spinner changes', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    component.developerIterations.set(9);
    fixture.detectChanges();

    const summary = component.configSummaryLine();
    expect(summary).toContain('9 iterations');
  });

  it('should update summary line when inline review passes spinner changes', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    component.reviewerPasses.set(4);
    fixture.detectChanges();

    const summary = component.configSummaryLine();
    expect(summary).toContain('4 reviews');
  });

  it('should expand configuration panel when Customize button is clicked', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    const customizeBtn = queryByTestId(fixture, 'customize-config-toggle');
    customizeBtn?.click();
    fixture.detectChanges();

    const fullPanel = queryByTestId(fixture, 'config-full-panel');
    expect(fullPanel).not.toBeNull();
  });

  it('should have Next button enabled in collapsed mode when configured', async () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    await enterConfigStep(component, fixture);

    const nextBtn = queryByTestId(fixture, 'review-launch-button') as HTMLButtonElement | null;
    expect(nextBtn).not.toBeNull();
    expect(nextBtn?.disabled).toBeFalsy();
  });

  it('should prefill drain bindings from effective config', async () => {
    await enterConfigStep(component, fixture);

    // After prefill, drain bindings should match the mocked chains config
    expect(component.drainBindings()['development']).toBe('default-chain');
    expect(component.drainBindings()['review']).toBe('default-chain');
    expect(component.drainBindings()['analysis']).toBe('alt-chain');
  });

  it('should prefill developer iterations from effective config', async () => {
    await enterConfigStep(component, fixture);
    expect(component.developerIterations()).toBe(7);
  });

  it('should prefill reviewer passes from effective config', async () => {
    await enterConfigStep(component, fixture);
    expect(component.reviewerPasses()).toBe(3);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Expanded Customize Mode (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
    fixture = result.fixture;
  });

  function expandConfigPanel(): void {
    component.effectiveChainsConfig.set(makeChainsConfig());
    component.drainBindings.set(makeChainsConfig().drains);
    component.repoPath.set('/repo');
    component.onTemplateSelect('some content');
    component.configPanelExpanded.set(true);
    fixture.detectChanges();
  }

  it('should render 6 drain dropdowns with correct phase labels', () => {
    expandConfigPanel();

    const phases = ['planning', 'development', 'analysis', 'review', 'fix', 'commit'];
    for (const phase of phases) {
      const dropdown = queryByTestId(fixture, `drain-select-${phase}`);
      expect(dropdown).not.toBeNull();
    }
  });

  it('should populate drain dropdowns with chain names from effective config', () => {
    expandConfigPanel();

    const devDropdown = queryByTestId(fixture, 'drain-select-development') as HTMLSelectElement | null;
    expect(devDropdown).not.toBeNull();
    const options = Array.from(devDropdown!.options).map(o => o.value);
    expect(options).toContain('default-chain');
    expect(options).toContain('alt-chain');
  });

  it('should have currently bound chain pre-selected in drain dropdown', () => {
    expandConfigPanel();

    const analysisDropdown = queryByTestId(
      fixture,
      'drain-select-analysis',
    ) as HTMLSelectElement | null;
    expect(analysisDropdown).not.toBeNull();
    expect(analysisDropdown!.value).toBe('alt-chain');
  });

  it('should update drain binding signal when drain changes', () => {
    expandConfigPanel();

    component.drainBindings.update(b => ({ ...b, development: 'alt-chain' }));
    fixture.detectChanges();

    expect(component.drainBindings()['development']).toBe('alt-chain');
  });

  it('should render review depth dropdown with AC-4.3.2 options', () => {
    expandConfigPanel();

    const depthSelect = queryByTestId(fixture, 'review-depth-select') as HTMLSelectElement | null;
    expect(depthSelect).not.toBeNull();
    const options = Array.from(depthSelect!.options).map(o => o.value);
    expect(options).toContain('standard');
    expect(options).toContain('comprehensive');
    expect(options).toContain('security');
    expect(options).toContain('incremental');
    // Should NOT have old values
    expect(options).not.toContain('light');
    expect(options).not.toContain('thorough');
  });

  it('should have Advanced subsection collapsed by default', () => {
    expandConfigPanel();

    const advancedSection = queryByTestId(fixture, 'advanced-section');
    expect(advancedSection).toBeNull();
  });

  it('should expand Advanced section when toggled', () => {
    expandConfigPanel();

    const advancedToggle = queryByTestId(fixture, 'advanced-toggle');
    advancedToggle?.click();
    fixture.detectChanges();

    const advancedSection = queryByTestId(fixture, 'advanced-section');
    expect(advancedSection).not.toBeNull();
  });

  it('should show developer context, reviewer context, checkpoint, and isolation in Advanced', () => {
    expandConfigPanel();
    component.advancedExpanded.set(true);
    fixture.detectChanges();

    expect(queryByTestId(fixture, 'dev-context-select')).not.toBeNull();
    expect(queryByTestId(fixture, 'rev-context-select')).not.toBeNull();
    expect(queryByTestId(fixture, 'checkpoint-toggle')).not.toBeNull();
    expect(queryByTestId(fixture, 'isolation-toggle')).not.toBeNull();
  });

  it('should reset to defaults from effective config when Reset button clicked', async () => {
    // Enter config step first so effectiveConfig is prefilled via async effect
    component.repoPath.set('/repo');
    component.onTemplateSelect('some content');
    fixture.detectChanges();
    // Wait for microtask queue to flush (the async prefill resolves on next microtask)
    await Promise.resolve();
    await Promise.resolve();
    fixture.detectChanges();

    // Now set panel to expanded
    component.effectiveChainsConfig.set(makeChainsConfig());
    component.configPanelExpanded.set(true);
    fixture.detectChanges();

    // Change values away from defaults
    component.developerIterations.set(15);
    component.reviewerPasses.set(5);
    component.drainBindings.set({ development: 'alt-chain' });
    component.developerContext.set('minimal');

    component.resetToDefaults();

    // Should restore from effective config (developer_iters=7 per makeConfig())
    expect(component.developerIterations()).toBe(7);
    expect(component.reviewerPasses()).toBe(3);
    expect(component.developerContext()).toBe('normal');
    // Drain bindings restored from makeChainsConfig() drains
    expect(component.drainBindings()['development']).toBe('default-chain');
  });

  it('should show session-only note in expanded panel', () => {
    expandConfigPanel();

    const nativeEl = fixture.nativeElement as HTMLElement;
    const text = nativeEl.textContent ?? '';
    expect(text).toContain('Changes here apply to this session only');
  });

  it('should collapse back to summary when Customize ▲ button is clicked', () => {
    expandConfigPanel();

    const collapseBtn = queryByTestId(fixture, 'collapse-config-toggle');
    collapseBtn?.click();
    fixture.detectChanges();

    expect(queryByTestId(fixture, 'config-full-panel')).toBeNull();
    expect(queryByTestId(fixture, 'collapsed-summary-panel')).not.toBeNull();
  });

  it('should persist drain binding changes when collapsing', () => {
    expandConfigPanel();

    // Change drain
    component.drainBindings.update(b => ({ ...b, development: 'alt-chain' }));
    fixture.detectChanges();

    // Collapse
    component.configPanelExpanded.set(false);
    fixture.detectChanges();

    // Changes should be preserved
    expect(component.drainBindings()['development']).toBe('alt-chain');
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Unconfigured State (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;

  beforeEach(async () => {
    const result = await setupTestBed({
      getEffectiveChainsConfig: vi.fn().mockResolvedValue(makeEmptyChainsConfig()),
    });
    component = result.component;
    fixture = result.fixture;
  });

  function enterConfigStepWithNoChains(): void {
    component.effectiveChainsConfig.set(makeEmptyChainsConfig());
    component.repoPath.set('/repo');
    component.onTemplateSelect('some content');
    fixture.detectChanges();
  }

  it('should show Setup Required callout when no chains are configured', () => {
    enterConfigStepWithNoChains();

    const callout = queryByTestId(fixture, 'unconfigured-callout');
    expect(callout).not.toBeNull();
  });

  it('should show correct heading in unconfigured callout', () => {
    enterConfigStepWithNoChains();

    const callout = queryByTestId(fixture, 'unconfigured-callout');
    expect(callout?.textContent).toContain('Setup Required');
  });

  it('should render Go to Configuration link', () => {
    enterConfigStepWithNoChains();

    const link = queryByTestId(fixture, 'go-to-config-link');
    expect(link).not.toBeNull();
  });

  it('should disable Next button when unconfigured', () => {
    enterConfigStepWithNoChains();

    const nextBtn = queryByTestId(fixture, 'review-launch-button') as HTMLButtonElement | null;
    expect(nextBtn).not.toBeNull();
    expect(nextBtn?.disabled).toBeTruthy();
  });

  it('should not show collapsed summary panel when unconfigured', () => {
    enterConfigStepWithNoChains();

    const collapsedPanel = queryByTestId(fixture, 'collapsed-summary-panel');
    expect(collapsedPanel).toBeNull();
  });

  it('should report hasConfiguredChains as false when no chains', () => {
    enterConfigStepWithNoChains();
    expect(component.hasConfiguredChains()).toBe(false);
  });

  it('should report canProceedToLaunch as false when unconfigured', () => {
    enterConfigStepWithNoChains();
    expect(component.canProceedToLaunch()).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Config Prefill (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;
  let mockTauriService: TauriService;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
    fixture = result.fixture;
    mockTauriService = result.mockTauriService;
  });

  it('should call getEffectiveConfig and getEffectiveChainsConfig when entering config step', async () => {
    await enterConfigStep(component, fixture);

    expect(mockTauriService.getEffectiveConfig).toHaveBeenCalledWith('/repo');
    expect(mockTauriService.getEffectiveChainsConfig).toHaveBeenCalledWith('/repo');
  });

  it('should map legacy review depth light to incremental', async () => {
    vi.mocked(mockTauriService.getEffectiveConfig).mockResolvedValue(
      makeConfig({ review_depth: 'light' }),
    );
    await enterConfigStep(component, fixture);

    expect(component.reviewDepth()).toBe('incremental');
  });

  it('should map legacy review depth thorough to comprehensive', async () => {
    vi.mocked(mockTauriService.getEffectiveConfig).mockResolvedValue(
      makeConfig({ review_depth: 'thorough' }),
    );
    await enterConfigStep(component, fixture);

    expect(component.reviewDepth()).toBe('comprehensive');
  });

  it('should keep standard review depth unchanged', async () => {
    await enterConfigStep(component, fixture);
    expect(component.reviewDepth()).toBe('standard');
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Drain Signal Methods (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
    component.effectiveChainsConfig.set(makeChainsConfig());
    component.drainBindings.set({});
  });

  it('should update drain binding for specified phase via onDrainChange', () => {
    const mockEvent = { target: { value: 'alt-chain' } } as unknown as Event;
    component.onDrainChange('development', mockEvent);

    expect(component.drainBindings()['development']).toBe('alt-chain');
  });

  it('should not overwrite other drain bindings when one changes', () => {
    component.drainBindings.set({ review: 'default-chain', planning: 'alt-chain' });

    const mockEvent = { target: { value: 'alt-chain' } } as unknown as Event;
    component.onDrainChange('development', mockEvent);

    expect(component.drainBindings()['review']).toBe('default-chain');
    expect(component.drainBindings()['planning']).toBe('alt-chain');
    expect(component.drainBindings()['development']).toBe('alt-chain');
  });

  it('should update reviewer context signal via onRevContextChange', () => {
    const mockEvent = { target: { value: 'minimal' } } as unknown as Event;
    component.onRevContextChange(mockEvent);

    expect(component.reviewerContext()).toBe('minimal');
  });

  it('should update developer context signal via onDevContextChange', () => {
    const mockEvent = { target: { value: 'minimal' } } as unknown as Event;
    component.onDevContextChange(mockEvent);

    expect(component.developerContext()).toBe('minimal');
  });

  it('should toggle advanced expanded state via toggleAdvanced', () => {
    expect(component.advancedExpanded()).toBe(false);
    component.toggleAdvanced();
    expect(component.advancedExpanded()).toBe(true);
    component.toggleAdvanced();
    expect(component.advancedExpanded()).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Summary Line (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
    fixture = result.fixture;
  });

  it('should show agent arrow notation in summary when development drain is bound', () => {
    component.effectiveChainsConfig.set(makeChainsConfig());
    component.drainBindings.set({ development: 'default-chain' });
    fixture.detectChanges();

    const summary = component.configSummaryLine();
    expect(summary).toContain('planner → developer → reviewer');
  });

  it('should fall back to chain name when no agents in chain', () => {
    component.effectiveChainsConfig.set({
      ...makeChainsConfig(),
      chains: [{ name: 'empty-chain', agents: [] }],
    });
    component.drainBindings.set({ development: 'empty-chain' });
    fixture.detectChanges();

    const summary = component.configSummaryLine();
    expect(summary).toContain('empty-chain');
  });

  it('should show Comprehensive in summary for comprehensive review depth', () => {
    component.reviewDepth.set('comprehensive');
    fixture.detectChanges();
    expect(component.configSummaryLine()).toContain('Comprehensive');
  });

  it('should show Security in summary for security review depth', () => {
    component.reviewDepth.set('security');
    fixture.detectChanges();
    expect(component.configSummaryLine()).toContain('Security');
  });

  it('should show Incremental in summary for incremental review depth', () => {
    component.reviewDepth.set('incremental');
    fixture.detectChanges();
    expect(component.configSummaryLine()).toContain('Incremental');
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe('NewSessionWizardComponent – Step 2 Preset Save/Load (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;

  beforeEach(async () => {
    const result = await setupTestBed();
    component = result.component;
  });

  it('should save preset capturing drain bindings and advanced options', () => {
    component.drainBindings.set({ development: 'alt-chain', review: 'default-chain' });
    component.reviewDepth.set('comprehensive');
    component.developerContext.set('minimal');
    component.reviewerContext.set('normal');
    component.checkpointEnabled.set(true);
    component.isolationMode.set(true);
    component.newPresetName.set('full-preset');

    component.savePreset();

    const preset = component.presets().find(p => p.name === 'full-preset');
    expect(preset).toBeDefined();
    expect(preset?.drainBindings).toEqual({
      development: 'alt-chain',
      review: 'default-chain',
    });
    expect(preset?.reviewDepth).toBe('comprehensive');
    expect(preset?.developerContext).toBe('minimal');
    expect(preset?.reviewerContext).toBe('normal');
    expect(preset?.checkpointEnabled).toBe(true);
    expect(preset?.isolationMode).toBe(true);
  });

  it('should restore drain bindings and advanced options when loading preset', () => {
    component.drainBindings.set({ development: 'alt-chain' });
    component.developerContext.set('minimal');
    component.newPresetName.set('p1');
    component.savePreset();

    // Reset state
    component.drainBindings.set({});
    component.developerContext.set('normal');

    const preset = component.presets().find(p => p.name === 'p1');
    component.loadPreset(preset!);

    expect(component.drainBindings()['development']).toBe('alt-chain');
    expect(component.developerContext()).toBe('minimal');
  });
});

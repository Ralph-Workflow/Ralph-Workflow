import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi, describe, it, beforeEach, expect } from 'vitest';
import { NewSessionWizardComponent } from './new-session-wizard.component';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { WorktreeInfo, ConfigView, EffectiveChainsConfig } from '../../types';
import { signal } from '@angular/core';

const makeConfig = (): ConfigView => ({
  verbosity: 1,
  developer_iters: 5,
  reviewer_reviews: 2,
  checkpoint_enabled: false,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 3,
});

const makeChainsConfig = (): EffectiveChainsConfig => ({
  chains: [
    { name: 'default-chain', agents: ['claude-developer', 'claude-reviewer'] },
    { name: 'alt-chain', agents: ['codex'] },
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
    { name: 'claude-developer', tool: 'claude', model: 'claude-sonnet-4-6' },
    { name: 'claude-reviewer', tool: 'claude', model: 'claude-sonnet-4-6' },
    { name: 'codex', tool: 'codex', model: 'gpt-4o' },
  ],
  has_configured_chains: true,
  has_configured_drains: true,
});

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

describe('NewSessionWizardComponent', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;
  let mockWorktreesService: WorktreesService;
  let mockTauriService: TauriService;

  beforeEach(async () => {
    mockWorktreesService = createMockWorktreesService();
    mockTauriService = createMockTauriService();

    await TestBed.configureTestingModule({
      imports: [NewSessionWizardComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: TauriService, useValue: mockTauriService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(NewSessionWizardComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('step progression', () => {
    it('should start with template step', () => {
      expect(component.step()).toBe('template');
    });

    it('should progress to config step after template selection', () => {
      component.onTemplateSelect('some content');
      expect(component.step()).toBe('config');
    });

    it('should progress to preflight step from config', () => {
      component.onTemplateSelect('some content');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      fixture.detectChanges();
      component.handleNext();
      expect(component.step()).toBe('preflight');
    });

    it('should not progress to preflight without template content', () => {
      component.repoPath.set('/repo');
      component.handleNext();
      expect(component.step()).toBe('template');
    });
  });

  describe('form state', () => {
    it('should have default iteration values', () => {
      expect(component.developerIterations()).toBe(5);
      expect(component.reviewerPasses()).toBe(2);
    });

    it('should set selectedWorktreePath', () => {
      component.selectedWorktreePath.set('/repo/wt-1');
      expect(component.selectedWorktreePath()).toBe('/repo/wt-1');
    });
  });

  describe('presets', () => {
    it('should save and load presets with drain bindings', () => {
      component.developerIterations.set(10);
      component.reviewerPasses.set(3);
      component.reviewDepth.set('comprehensive');
      component.drainBindings.set({ development: 'default-chain', review: 'alt-chain' });
      component.developerContext.set('minimal');
      component.reviewerContext.set('normal');
      component.checkpointEnabled.set(true);
      component.isolationMode.set(false);
      component.newPresetName.set('test-preset');

      component.savePreset();

      const presets = component.presets();
      const saved = presets.find(p => p.name === 'test-preset');
      expect(saved).toBeDefined();
      expect(saved?.developerIterations).toBe(10);
      expect(saved?.reviewerPasses).toBe(3);
      expect(saved?.reviewDepth).toBe('comprehensive');
      expect(saved?.drainBindings).toEqual({ development: 'default-chain', review: 'alt-chain' });
      expect(saved?.developerContext).toBe('minimal');
      expect(saved?.checkpointEnabled).toBe(true);
    });

    it('should delete presets', () => {
      component.newPresetName.set('to-delete');
      component.savePreset();

      expect(component.presets().find(p => p.name === 'to-delete')).toBeDefined();

      component.deletePreset('to-delete');

      expect(component.presets().find(p => p.name === 'to-delete')).toBeUndefined();
    });

    it('should load preset values including drain bindings', () => {
      component.newPresetName.set('my-preset');
      component.developerIterations.set(7);
      component.reviewerPasses.set(3);
      component.drainBindings.set({ development: 'alt-chain' });
      component.savePreset();

      component.developerIterations.set(5);
      component.reviewerPasses.set(2);
      component.drainBindings.set({});

      const preset = component.presets().find(p => p.name === 'my-preset');
      component.loadPreset(preset!);

      expect(component.developerIterations()).toBe(7);
      expect(component.reviewerPasses()).toBe(3);
      expect(component.drainBindings()['development']).toBe('alt-chain');
    });

    it('should not save preset when name is empty', () => {
      component.newPresetName.set('');
      component.savePreset();
      expect(component.presets().length).toBe(0);
    });
  });

  describe('launch', () => {
    it('should launch session with correct args', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.selectedWorktreePath.set('/repo/wt-1');
      component.developerIterations.set(5);
      component.reviewerPasses.set(2);
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.drainBindings.set({ development: 'default-chain', review: 'default-chain' });

      await component.launchSession();

      expect(mockTauriService.savePromptFile).toHaveBeenCalled();
      expect(mockTauriService.launchRalphSession).toHaveBeenCalledWith(
        expect.objectContaining({
          repo_path: '/repo',
          worktree_path: '/repo/wt-1',
          developer_iterations: 5,
          reviewer_passes: 2,
        })
      );
    });

    it('should resolve developer agent from development drain', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.drainBindings.set({ development: 'default-chain', review: 'alt-chain' });

      await component.launchSession();

      const calls = vi.mocked(mockTauriService.launchRalphSession).mock.calls;
      const args = calls[calls.length - 1]![0];
      expect(args.developer_agent).toBe('claude-developer');
      expect(args.reviewer_agent).toBe('codex');
    });

    it('should set launching state during launch', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      expect(component.isLaunching()).toBe(false);

      const promise = component.launchSession();
      expect(component.isLaunching()).toBe(true);

      await promise;
      expect(component.isLaunching()).toBe(false);
    });

    it('should emit sessionLaunched on successful launch', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      const emittedValues: string[] = [];
      component.sessionLaunched.subscribe((v: string) => emittedValues.push(v));
      await component.launchSession();

      expect(emittedValues).toContain('run-123');
    });

    it('should not launch if already launching', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      component.isLaunching.set(true);
      await component.launchSession();

      expect(mockTauriService.launchRalphSession).not.toHaveBeenCalled();
    });
  });

  describe('preflight bindings', () => {
    it('should pass drainBindings to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.drainBindings.set({ development: 'default-chain', review: 'alt-chain' });
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.step()).toBe('preflight');
      expect(component.drainBindings_).toEqual({ development: 'default-chain', review: 'alt-chain' });
    });

    it('should pass reviewDepth to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.reviewDepth.set('comprehensive');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.reviewDepth_).toBe('comprehensive');
    });

    it('should pass checkpointEnabled to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.checkpointEnabled.set(true);
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.checkpointEnabled_).toBe(true);
    });

    it('should pass isolationMode to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.isolationMode.set(true);
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.isolationMode_).toBe(true);
    });

    it('should pass developerContext to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.developerContext.set('minimal');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.developerContext_).toBe('minimal');
    });

    it('should pass reviewerContext to preflight-summary', async () => {
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.reviewerContext.set('minimal');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.reviewerContext_).toBe('minimal');
    });

    it('should pass effectiveChainsConfig to preflight-summary', async () => {
      const config = makeChainsConfig();
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(config);
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.effectiveChainsConfig_).toBe(config);
    });

    it('should pass promptContent to preflight-summary', async () => {
      component.onTemplateSelect('test prompt content here');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.promptContent_).toBe('test prompt content here');
    });

    it('should pass configSourceLabel to preflight-summary', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.valueSourceLabel_).toBeDefined();
    });

    it('should pass launchError to preflight-summary', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.launchError.set('Test error');
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.launchError_).toBe('Test error');
    });
  });

  describe('preflight navigation outputs', () => {
    it('should navigate to template step on editPrompt', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.step()).toBe('preflight');

      component.step.set('template');
      fixture.detectChanges();

      expect(component.step()).toBe('template');
    });

    it('should navigate to config step on editConfig', async () => {
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');
      component.effectiveChainsConfig.set(makeChainsConfig());
      component.handleNext();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.step()).toBe('preflight');

      component.step.set('config');
      fixture.detectChanges();

      expect(component.step()).toBe('config');
    });

    it('should emit closeWizard on navigateToConfiguration', () => {
      const closeWizardSpy = vi.spyOn(component.closeWizard, 'emit');

      component.navigateToConfiguration();

      expect(closeWizardSpy).toHaveBeenCalledTimes(1);
    });

    it('should emit wizardClosed on navigateToConfiguration', () => {
      const wizardClosedSpy = vi.spyOn(component.wizardClosed, 'emit');

      component.navigateToConfiguration();

      expect(wizardClosedSpy).toHaveBeenCalledTimes(1);
    });
  });
});

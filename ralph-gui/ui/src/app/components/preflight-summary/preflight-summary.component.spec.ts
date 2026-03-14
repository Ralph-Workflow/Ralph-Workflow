import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi, describe, it, beforeEach, expect } from 'vitest';
import { PreflightSummaryComponent } from './preflight-summary.component';
import type { EffectiveChainsConfig } from '../../types';

function makeDefaultInputs() {
  return {
    repoPath: '/repo',
    worktreePath: '/repo/wt-1' as string | null,
    promptPath: '/repo/prompt.md',
    promptContent: 'This is a test prompt with enough content for the check.',
    developerIterations: 5,
    reviewerPasses: 2,
    reviewDepth: 'standard',
    drainBindings: {
      planning: 'default-chain',
      development: 'default-chain',
      review: 'default-chain',
    } as Record<string, string>,
    checkpointEnabled: false,
    isolationMode: false,
    developerContext: 'normal',
    reviewerContext: 'normal',
    configSourceLabel: 'Global',
    launchError: null as string | null,
    isLaunching: false,
  };
}

function makeChainsConfig(): EffectiveChainsConfig {
  return {
    chains: [
      { name: 'default-chain', agents: ['planner', 'developer', 'reviewer'] },
      { name: 'alt-chain', agents: ['codex'] },
    ],
    drains: {
      planning: 'default-chain',
      development: 'default-chain',
      review: 'default-chain',
    },
    agents: [
      { name: 'planner', tool: 'claude', model: 'claude-sonnet-4' },
      { name: 'developer', tool: 'claude', model: 'claude-sonnet-4' },
      { name: 'reviewer', tool: 'claude', model: 'claude-sonnet-4' },
      { name: 'codex', tool: 'codex', model: 'gpt-4o' },
    ],
    has_configured_chains: true,
    has_configured_drains: true,
  };
}

function makeEmptyChainsConfig(): EffectiveChainsConfig {
  return {
    chains: [],
    drains: {},
    agents: [],
    has_configured_chains: false,
    has_configured_drains: false,
  };
}

describe('PreflightSummaryComponent', () => {
  let component: PreflightSummaryComponent;
  let fixture: ComponentFixture<PreflightSummaryComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PreflightSummaryComponent],
      providers: [],
    }).compileComponents();

    fixture = TestBed.createComponent(PreflightSummaryComponent);
    component = fixture.componentInstance;
  });

  function setInputs(inputs: Partial<ReturnType<typeof makeDefaultInputs>> = {}) {
    const defaults = makeDefaultInputs();
    const merged = { ...defaults, ...inputs };

    fixture.componentRef.setInput('repoPath', merged.repoPath);
    fixture.componentRef.setInput('worktreePath', merged.worktreePath);
    fixture.componentRef.setInput('promptPath', merged.promptPath);
    fixture.componentRef.setInput('promptContent', merged.promptContent);
    fixture.componentRef.setInput('developerIterations', merged.developerIterations);
    fixture.componentRef.setInput('reviewerPasses', merged.reviewerPasses);
    fixture.componentRef.setInput('reviewDepth', merged.reviewDepth);
    fixture.componentRef.setInput('drainBindings', merged.drainBindings);
    fixture.componentRef.setInput('checkpointEnabled', merged.checkpointEnabled);
    fixture.componentRef.setInput('isolationMode', merged.isolationMode);
    fixture.componentRef.setInput('developerContext', merged.developerContext);
    fixture.componentRef.setInput('reviewerContext', merged.reviewerContext);
    fixture.componentRef.setInput('configSourceLabel', merged.configSourceLabel);
    fixture.componentRef.setInput('launchError', merged.launchError);
    fixture.componentRef.setInput('isLaunching', merged.isLaunching);

    fixture.detectChanges();
  }

  it('should create', () => {
    setInputs();
    expect(component).toBeTruthy();
  });

  describe('session roles display', () => {
    it('shows Planning, Development, Review drain-to-chain mappings when chains config available', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const roles = component.sessionRoles();

      expect(roles.length).toBe(3);
      expect(roles[0]?.phase).toBe('Planning');
      expect(roles[1]?.phase).toBe('Development');
      expect(roles[2]?.phase).toBe('Review');
    });

    it('shows agent arrow notation for each role', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const roles = component.sessionRoles();

      expect(roles[0]?.agents).toBe('planner → developer → reviewer');
    });

    it('falls back to chain name when no agents found', () => {
      const config: EffectiveChainsConfig = {
        chains: [{ name: 'empty-chain', agents: [] }],
        drains: { planning: 'empty-chain', development: 'empty-chain', review: 'empty-chain' },
        agents: [],
        has_configured_chains: true,
        has_configured_drains: true,
      };
      setInputs({ drainBindings: { planning: 'empty-chain', development: 'empty-chain', review: 'empty-chain' } });
      fixture.componentRef.setInput('effectiveChainsConfig', config);
      fixture.detectChanges();

      const roles = component.sessionRoles();

      expect(roles[0]?.agents).toBe('empty-chain');
    });

    it('shows nothing when effectiveChainsConfig is null', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', null);
      fixture.detectChanges();

      const roles = component.sessionRoles();

      expect(roles.length).toBe(0);
    });

    it('shows dash when no chain assigned', () => {
      setInputs({ drainBindings: {} });
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const roles = component.sessionRoles();

      expect(roles[0]?.agents).toBe('—');
    });
  });

  describe('effective config preview', () => {
    it('displays review_depth value', () => {
      setInputs({ reviewDepth: 'comprehensive' });
      fixture.detectChanges();

      const rows = component.configPreviewRows();
      const depthRow = rows.find(r => r.label === 'review_depth');

      expect(depthRow?.value).toBe('comprehensive');
    });

    it('displays checkpoint on/off', () => {
      setInputs({ checkpointEnabled: true });
      fixture.detectChanges();

      const rows = component.configPreviewRows();
      const checkpointRow = rows.find(r => r.label === 'checkpoint');

      expect(checkpointRow?.value).toBe('on');
    });

    it('displays isolation on/off', () => {
      setInputs({ isolationMode: true });
      fixture.detectChanges();

      const rows = component.configPreviewRows();
      const isolationRow = rows.find(r => r.label === 'isolation');

      expect(isolationRow?.value).toBe('on');
    });

    it('displays drain binding assignments', () => {
      setInputs({
        drainBindings: {
          planning: 'planner-chain',
          development: 'dev-chain',
          review: 'review-chain',
        },
      });
      fixture.detectChanges();

      const rows = component.configPreviewRows();
      const drainsRow = rows.find(r => r.label === 'drains');

      expect(drainsRow?.value).toContain('planning=planner-chain');
      expect(drainsRow?.value).toContain('development=dev-chain');
      expect(drainsRow?.value).toContain('review=review-chain');
    });

    it('shows config source label in template', () => {
      setInputs({ configSourceLabel: 'Global + Project' });
      fixture.detectChanges();

      const text = fixture.nativeElement.textContent;

      expect(text).toContain('Effective config source: Global + Project');
    });
  });

  describe('preflight checks', () => {
    it('all OK when workspace set, chains configured, prompt has content', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const checks = component.preflightChecks();

      expect(checks.every(c => c.status === 'ok')).toBe(true);
    });

    it('error when no workspace path', () => {
      setInputs({ repoPath: '' });
      fixture.detectChanges();

      const checks = component.preflightChecks();
      const workspaceCheck = checks.find(c => c.label === 'Workspace available');

      expect(workspaceCheck?.status).toBe('error');
      expect(workspaceCheck?.detail).toBe('Select a workspace');
    });

    it('error when no chains configured', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeEmptyChainsConfig());
      fixture.detectChanges();

      const checks = component.preflightChecks();
      const chainCheck = checks.find(c => c.label === 'Agent chain configured');

      expect(chainCheck?.status).toBe('error');
      expect(chainCheck?.detail).toBe('No agent chains configured');
      expect(chainCheck?.action).toBe('goToConfig');
    });

    it('warning when short prompt', () => {
      setInputs({ promptContent: 'short' });
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const checks = component.preflightChecks();
      const promptCheck = checks.find(c => c.label === 'Prompt content');

      expect(promptCheck?.status).toBe('warning');
      expect(promptCheck?.detail).toBe('Add more detail to your prompt');
      expect(promptCheck?.action).toBe('reviewPrompt');
    });

    it('warning when empty prompt', () => {
      setInputs({ promptContent: '' });
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const checks = component.preflightChecks();
      const promptCheck = checks.find(c => c.label === 'Prompt content');

      expect(promptCheck?.status).toBe('warning');
      expect(promptCheck?.detail).toBe('Prompt is empty');
    });

    it('canLaunch is false when any error-level check exists', () => {
      setInputs({ repoPath: '' });
      fixture.detectChanges();

      expect(component.canLaunch()).toBe(false);
    });

    it('canLaunch is true when only warnings exist', () => {
      setInputs({ promptContent: 'short' });
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      expect(component.canLaunch()).toBe(true);
    });
  });

  describe('resource estimation', () => {
    it('low when iterations <= 2', () => {
      setInputs({ developerIterations: 2 });
      fixture.detectChanges();

      const estimate = component.resourceEstimate();

      expect(estimate.level).toBe('low');
      expect(estimate.devIterations).toBe(2);
      expect(estimate.planningPasses).toBe(1);
    });

    it('medium when iterations 3-5', () => {
      setInputs({ developerIterations: 4 });
      fixture.detectChanges();

      const estimate = component.resourceEstimate();

      expect(estimate.level).toBe('medium');
    });

    it('high when iterations > 5', () => {
      setInputs({ developerIterations: 10 });
      fixture.detectChanges();

      const estimate = component.resourceEstimate();

      expect(estimate.level).toBe('high');
    });

    it('summary line includes planning pass and dev iterations', () => {
      setInputs({ developerIterations: 5 });
      fixture.detectChanges();

      const estimate = component.resourceEstimate();

      expect(estimate.summary).toBe('medium · 1 planning pass · up to 5 dev iterations');
    });

    it('summary line format verification in template', () => {
      setInputs({ developerIterations: 2 });
      fixture.detectChanges();

      const estimateEl = fixture.nativeElement.querySelector('[data-testid="resource-estimate"]');

      expect(estimateEl?.textContent).toContain('low · 1 planning pass · up to 2 dev iterations');
    });
  });

  describe('launch error handling', () => {
    it('error card appears when launchError is set', () => {
      setInputs({ launchError: 'Missing authentication for claude-opus' });
      fixture.detectChanges();

      const errorCard = fixture.nativeElement.querySelector('[data-testid="launch-error-card"]');

      expect(errorCard).toBeTruthy();
    });

    it('error card shows error message', () => {
      setInputs({ launchError: 'Missing authentication for claude-opus' });
      fixture.detectChanges();

      const errorCard = fixture.nativeElement.querySelector('[data-testid="launch-error-card"]');

      expect(errorCard?.textContent).toContain('Missing authentication for claude-opus');
    });

    it('error card shows guidance text', () => {
      setInputs({ launchError: 'Test error' });
      fixture.detectChanges();

      const errorCard = fixture.nativeElement.querySelector('[data-testid="launch-error-card"]');

      expect(errorCard?.textContent).toContain('Open Configuration to change the chain or fix the tool auth');
    });

    it('error card hidden when launchError is null', () => {
      setInputs({ launchError: null });
      fixture.detectChanges();

      const errorCard = fixture.nativeElement.querySelector('[data-testid="launch-error-card"]');

      expect(errorCard).toBeFalsy();
    });

    it('goToConfiguration event emits on button click', () => {
      setInputs({ launchError: 'Test error' });
      fixture.detectChanges();

      const spy = vi.spyOn(component.goToConfiguration, 'emit');
      const btn = fixture.nativeElement.querySelector('[data-testid="open-configuration-btn"]');
      btn?.click();

      expect(spy).toHaveBeenCalled();
    });

    it('launch button shows "Retry Launch" when error exists', () => {
      setInputs({ launchError: 'Test error' });
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('[data-testid="launch-btn"]');

      expect(btn?.textContent).toContain('Retry Launch');
    });
  });

  describe('navigation outputs', () => {
    it('editPrompt emits when Edit Prompt clicked', () => {
      setInputs();
      fixture.detectChanges();

      const spy = vi.spyOn(component.editPrompt, 'emit');
      const btn = fixture.nativeElement.querySelector('[data-testid="edit-prompt-btn"]');
      btn?.click();

      expect(spy).toHaveBeenCalled();
    });

    it('editConfig emits when Edit Config clicked', () => {
      setInputs();
      fixture.detectChanges();

      const spy = vi.spyOn(component.editConfig, 'emit');
      const btn = fixture.nativeElement.querySelector('[data-testid="edit-config-btn"]');
      btn?.click();

      expect(spy).toHaveBeenCalled();
    });

    it('goBack emits when Back clicked', () => {
      setInputs();
      fixture.detectChanges();

      const spy = vi.spyOn(component.goBack, 'emit');
      const btn = fixture.nativeElement.querySelector('[data-testid="back-btn"]');
      btn?.click();

      expect(spy).toHaveBeenCalled();
    });

    it('confirmLaunch emits when Launch clicked', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const spy = vi.spyOn(component.confirmLaunch, 'emit');
      const btn = fixture.nativeElement.querySelector('[data-testid="launch-btn"]');
      btn?.click();

      expect(spy).toHaveBeenCalled();
    });
  });

  describe('rendering and accessibility', () => {
    it('launch button disabled when isLaunching', () => {
      setInputs({ isLaunching: true });
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('[data-testid="launch-btn"]');

      expect(btn?.disabled).toBe(true);
    });

    it('launch button disabled when canLaunch is false', () => {
      setInputs({ repoPath: '' });
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('[data-testid="launch-btn"]');

      expect(btn?.disabled).toBe(true);
    });

    it('launch button shows "Launching…" text when isLaunching', () => {
      setInputs({ isLaunching: true });
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('[data-testid="launch-btn"]');

      expect(btn?.textContent).toContain('Launching…');
    });

    it('status indicators use text + icon, not color alone', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      const okCheck = fixture.nativeElement.querySelector('[data-testid="check-workspace-available"]');

      expect(okCheck?.textContent).toContain('[OK]');
    });

    it('data-testid attributes on key sections for test targeting', () => {
      setInputs();
      fixture.componentRef.setInput('effectiveChainsConfig', makeChainsConfig());
      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('[data-testid="session-roles"]')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('[data-testid="config-preview"]')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('[data-testid="preflight-checks"]')).toBeTruthy();
      expect(fixture.nativeElement.querySelector('[data-testid="resource-estimate"]')).toBeTruthy();
    });
  });

  describe('context rows', () => {
    it('should return repository and worktree context', () => {
      setInputs({
        repoPath: '/repo',
        worktreePath: '/repo/wt-1',
      });

      const rows = component.contextRows();

      expect(rows.length).toBe(2);
      expect(rows[0]?.label).toBe('Repository');
      expect(rows[0]?.value).toBe('/repo');
      expect(rows[1]?.label).toBe('Context');
      expect(rows[1]?.value).toBe('/repo/wt-1');
    });

    it('should show direct repository when no worktree', () => {
      setInputs({
        repoPath: '/repo',
        worktreePath: null,
      });

      const rows = component.contextRows();

      expect(rows[1]?.value).toBe('Direct repository');
    });
  });

  describe('worktree name', () => {
    it('extracts worktree name from path', () => {
      setInputs({ worktreePath: '/repo/wt-feature-branch' });
      fixture.detectChanges();

      expect(component.worktreeName()).toBe('wt-feature-branch');
    });

    it('returns "Direct repository" when no worktree', () => {
      setInputs({ worktreePath: null });
      fixture.detectChanges();

      expect(component.worktreeName()).toBe('Direct repository');
    });
  });

  describe('review depth label', () => {
    it('maps standard to Standard', () => {
      setInputs({ reviewDepth: 'standard' });
      fixture.detectChanges();

      expect(component.reviewDepthLabel()).toBe('Standard');
    });

    it('maps comprehensive to Comprehensive', () => {
      setInputs({ reviewDepth: 'comprehensive' });
      fixture.detectChanges();

      expect(component.reviewDepthLabel()).toBe('Comprehensive');
    });

    it('maps security to Security', () => {
      setInputs({ reviewDepth: 'security' });
      fixture.detectChanges();

      expect(component.reviewDepthLabel()).toBe('Security');
    });

    it('maps incremental to Incremental', () => {
      setInputs({ reviewDepth: 'incremental' });
      fixture.detectChanges();

      expect(component.reviewDepthLabel()).toBe('Incremental');
    });
  });
});

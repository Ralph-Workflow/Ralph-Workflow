/**
 * Step 2 (config) behaviour tests for NewSessionWizardComponent.
 * AC-4.3.2
 */
import { ComponentFixture, TestBed, fakeAsync, tick, flushMicrotasks } from '@angular/core/testing';
import { NewSessionWizardComponent } from './new-session-wizard.component';
import { WorktreesService } from '../../services/worktrees.service';
import { AgentProfileService } from '../../services/agent-profile.service';
import { TauriService } from '../../services/tauri.service';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { WorktreeInfo, AgentProfile, ConfigView } from '../../types';
import { signal } from '@angular/core';

const makeConfig = (): ConfigView => ({
  verbosity: 1,
  developer_iters: 7,
  reviewer_reviews: 3,
  checkpoint_enabled: true,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 5,
});

describe('NewSessionWizardComponent – Step 2 config prefill (AC-4.3.2)', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;
  let mockWorktreesService: jasmine.SpyObj<WorktreesService>;
  let mockAgentProfileService: jasmine.SpyObj<AgentProfileService>;
  let mockTauriService: jasmine.SpyObj<TauriService>;
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.resolveTo([]);

    mockWorktreesService = jasmine.createSpyObj(
      'WorktreesService',
      ['fetchWorktrees', 'createWorktree'],
      {
        worktrees: signal<WorktreeInfo[]>([]),
        mainWorktree: signal<WorktreeInfo | null>(null),
        nonMainWorktrees: signal<WorktreeInfo[]>([]),
        activeWorktreePath: signal<string | null>(null),
        lastRepoPath: signal<string | null>(null),
      },
    );
    mockWorktreesService.fetchWorktrees.and.resolveTo(undefined);

    mockAgentProfileService = jasmine.createSpyObj(
      'AgentProfileService',
      ['fetchProfiles', 'selectProfile', 'clearSelection'],
      {
        profiles: signal<AgentProfile[]>([
          { name: 'claude', developer_agent: 'claude', reviewer_agent: 'claude' },
        ]),
        selectedProfile: signal<string | null>(null),
      },
    );
    mockAgentProfileService.fetchProfiles.and.resolveTo(undefined);

    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      [
        'launchRalphSession',
        'savePromptFile',
        'getEffectiveConfig',
        'listAgentProfiles',
      ],
    );
    mockTauriService.getEffectiveConfig.and.resolveTo(makeConfig());

    await TestBed.configureTestingModule({
      imports: [NewSessionWizardComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: AgentProfileService, useValue: mockAgentProfileService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(NewSessionWizardComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  /** Enter the config step and flush all pending async microtasks. */
  function enterConfigStep(): void {
    component.repoPath.set('/repo');
    component.onTemplateSelect('some content'); // transitions step → 'config'
    fixture.detectChanges();
    // Flush promises from getEffectiveConfig
    flushMicrotasks();
    tick();
    fixture.detectChanges();
  }

  describe('config prefill from effective config', () => {
    it('should call getEffectiveConfig when entering config step', fakeAsync(() => {
      enterConfigStep();

      expect(mockTauriService.getEffectiveConfig).toHaveBeenCalledWith('/repo');
    }));

    it('should prefill developerIterations from effective config', fakeAsync(() => {
      enterConfigStep();

      expect(component.developerIterations()).toBe(7);
    }));

    it('should prefill reviewerPasses from effective config', fakeAsync(() => {
      enterConfigStep();

      expect(component.reviewerPasses()).toBe(3);
    }));

    it('should prefill checkpointEnabled from effective config', fakeAsync(() => {
      enterConfigStep();

      expect(component.checkpointEnabled()).toBe(true);
    }));

    it('should prefill reviewDepth from effective config', fakeAsync(() => {
      enterConfigStep();

      expect(component.reviewDepth()).toBe('standard');
    }));
  });

  describe('reset to defaults', () => {
    it('should revert to config values on resetToDefaults', fakeAsync(() => {
      enterConfigStep();

      // Manually change values
      component.developerIterations.set(10);
      component.reviewerPasses.set(1);

      component.resetToDefaults();

      expect(component.developerIterations()).toBe(7);
      expect(component.reviewerPasses()).toBe(3);
    }));
  });

  describe('unconfigured state', () => {
    it('should show unconfigured state when agentProfiles is empty', () => {
      mockAgentProfileService.profiles.set([]);
      fixture.detectChanges();

      component.onTemplateSelect('some content');
      component.repoPath.set('/repo');

      expect(component.hasAgentProfiles()).toBeFalse();
    });

    it('should have agent profiles when profiles exist', () => {
      expect(component.hasAgentProfiles()).toBeTrue();
    });

    it('canProceedToLaunch is false when no agent profiles configured', () => {
      mockAgentProfileService.profiles.set([]);
      fixture.detectChanges();

      expect(component.canProceedToLaunch()).toBeFalse();
    });

    it('canProceedToLaunch is true when agent profiles exist and repo path set', () => {
      component.repoPath.set('/repo');
      fixture.detectChanges();

      expect(component.canProceedToLaunch()).toBeTrue();
    });
  });

  describe('summary line', () => {
    it('should render summary line from config values', fakeAsync(() => {
      enterConfigStep();

      const summary = component.configSummaryLine();
      expect(summary).toContain('7 iterations');
      expect(summary).toContain('3 reviews');
      expect(summary).toContain('standard');
    }));
  });
});

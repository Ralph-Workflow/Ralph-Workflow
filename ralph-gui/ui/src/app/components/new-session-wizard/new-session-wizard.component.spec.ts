import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NewSessionWizardComponent } from './new-session-wizard.component';
import { WorktreesService } from '../../services/worktrees.service';
import { PromptService } from '../../services/prompt.service';
import { AgentProfileService } from '../../services/agent-profile.service';
import { TauriService } from '../../services/tauri.service';
import type { WorktreeInfo, AgentProfile } from '../../types';
import { signal } from '@angular/core';

describe('NewSessionWizardComponent', () => {
  let component: NewSessionWizardComponent;
  let fixture: ComponentFixture<NewSessionWizardComponent>;
  let mockWorktreesService: jasmine.SpyObj<WorktreesService>;
  let mockPromptService: jasmine.SpyObj<PromptService>;
  let mockAgentProfileService: jasmine.SpyObj<AgentProfileService>;
  let mockTauriService: jasmine.SpyObj<TauriService>;

  beforeEach(async () => {
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

    mockPromptService = jasmine.createSpyObj(
      'PromptService',
      ['setPath', 'setContent', 'savePrompt'],
      {
        content: signal(''),
        path: signal(''),
        reviewResult: signal<string | null>(null),
        reviewStatus: signal<'idle' | 'loading' | 'done' | 'error'>('idle'),
        reviewError: signal<string | null>(null),
      },
    );

    mockAgentProfileService = jasmine.createSpyObj(
      'AgentProfileService',
      ['fetchProfiles', 'selectProfile', 'clearSelection'],
      {
        profiles: signal<AgentProfile[]>([]),
        selectedProfile: signal<string | null>(null),
      },
    );

    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      ['launchRalphSession', 'savePromptFile'],
    );

    await TestBed.configureTestingModule({
      imports: [NewSessionWizardComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: PromptService, useValue: mockPromptService },
        { provide: AgentProfileService, useValue: mockAgentProfileService },
        { provide: TauriService, useValue: mockTauriService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(NewSessionWizardComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('step progression', () => {
    it('should start with template step', () => {
      fixture.detectChanges();
      expect(component.step()).toBe('template');
    });

    it('should progress to config step after template selection', () => {
      fixture.detectChanges();
      component.onTemplateSelect('some content');
      expect(component.step()).toBe('config');
    });

    it('should progress to preflight step from config', () => {
      fixture.detectChanges();
      component.onTemplateSelect('some content');
      component.repoPath.set('/repo');
      component.handleNext();
      expect(component.step()).toBe('preflight');
    });

    it('should not progress to preflight without template content', () => {
      fixture.detectChanges();
      component.repoPath.set('/repo');
      component.handleNext();
      expect(component.step()).toBe('template');
    });
  });

  describe('form state', () => {
    it('should have default iteration values', () => {
      fixture.detectChanges();
      expect(component.developerIterations()).toBe(5);
      expect(component.reviewerPasses()).toBe(2);
    });

    it('should set selectedWorktreePath', () => {
      fixture.detectChanges();
      component.selectedWorktreePath.set('/repo/wt-1');
      expect(component.selectedWorktreePath()).toBe('/repo/wt-1');
    });
  });

  describe('presets', () => {
    it('should save and load presets', () => {
      fixture.detectChanges();
      component.developerIterations.set(10);
      component.reviewerPasses.set(3);
      component.newPresetName.set('test-preset');
      
      component.savePreset();
      
      const presets = component.presets();
      const saved = presets.find(p => p.name === 'test-preset');
      expect(saved).toBeDefined();
      expect(saved?.developerIterations).toBe(10);
      expect(saved?.reviewerPasses).toBe(3);
    });

    it('should delete presets', () => {
      fixture.detectChanges();
      component.newPresetName.set('to-delete');
      component.savePreset();
      
      expect(component.presets().find(p => p.name === 'to-delete')).toBeDefined();
      
      component.deletePreset('to-delete');
      
      expect(component.presets().find(p => p.name === 'to-delete')).toBeUndefined();
    });

    it('should load preset values', () => {
      fixture.detectChanges();
      component.newPresetName.set('my-preset');
      component.developerIterations.set(7);
      component.reviewerPasses.set(3);
      component.savePreset();

      component.developerIterations.set(5);
      component.reviewerPasses.set(2);
      
      const preset = component.presets().find(p => p.name === 'my-preset');
      component.loadPreset(preset!);
      
      expect(component.developerIterations()).toBe(7);
      expect(component.reviewerPasses()).toBe(3);
    });
  });

  describe('launch', () => {
    it('should launch session with correct args', async () => {
      mockTauriService.savePromptFile.and.resolveTo();
      mockTauriService.launchRalphSession.and.resolveTo('run-123');

      fixture.detectChanges();
      component.onTemplateSelect('test prompt');
      component.repoPath.set('/repo');
      component.selectedWorktreePath.set('/repo/wt-1');
      component.developerIterations.set(5);
      component.reviewerPasses.set(2);

      await component.launchSession();

      expect(mockTauriService.savePromptFile).toHaveBeenCalled();
      expect(mockTauriService.launchRalphSession).toHaveBeenCalledWith(
        jasmine.objectContaining({
          repo_path: '/repo',
          worktree_path: '/repo/wt-1',
          developer_iterations: 5,
          reviewer_passes: 2,
        })
      );
    });

    it('should set launching state during launch', async () => {
      mockTauriService.savePromptFile.and.resolveTo();
      mockTauriService.launchRalphSession.and.resolveTo('run-123');

      fixture.detectChanges();
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      expect(component.isLaunching()).toBe(false);
      
      const promise = component.launchSession();
      expect(component.isLaunching()).toBe(true);
      
      await promise;
      expect(component.isLaunching()).toBe(false);
    });

    it('should emit sessionLaunched on successful launch', async () => {
      mockTauriService.savePromptFile.and.resolveTo();
      mockTauriService.launchRalphSession.and.resolveTo('run-123');

      fixture.detectChanges();
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      const launchedSpy = spyOn(component.sessionLaunched, 'emit');
      await component.launchSession();

      expect(launchedSpy).toHaveBeenCalledWith('run-123');
    });

    it('should not launch if already launching', async () => {
      mockTauriService.savePromptFile.and.resolveTo();
      mockTauriService.launchRalphSession.and.resolveTo('run-123');

      fixture.detectChanges();
      component.onTemplateSelect('test');
      component.repoPath.set('/repo');

      component.isLaunching.set(true);
      await component.launchSession();

      expect(mockTauriService.launchRalphSession).not.toHaveBeenCalled();
    });
  });
});

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, RouterModule } from '@angular/router';
import { HomeComponent } from './home.component';
import { StatCardComponent } from './stat-card.component';
import { QuickActionComponent } from './quick-action.component';
import { WorktreesService } from '../../services/worktrees.service';
import { RunsService } from '../../services/runs.service';
import { signal, WritableSignal } from '@angular/core';
import type { WorktreeInfo, RunDetail } from '../../types';

describe('HomeComponent', () => {
  let component: HomeComponent;
  let fixture: ComponentFixture<HomeComponent>;
  let worktreesSignal: WritableSignal<WorktreeInfo[]>;
  let mainWorktreeSignal: WritableSignal<WorktreeInfo | null>;
  let resumableRunsSignal: WritableSignal<RunDetail[]>;

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  const createMockRun = (overrides: Partial<RunDetail> = {}): RunDetail => ({
    run_id: 'run-123',
    status: 'Paused',
    current_phase: 'development',
    last_checkpoint: null,
    agent_profile: 'default',
    repo_path: '/repo',
    worktree_path: null,
    created_at: '2024-01-01T00:00:00Z',
    description: 'Test run',
    ...overrides,
  });

  const createMockServices = () => ({
    worktreesService: {
      worktrees: worktreesSignal.asReadonly(),
      mainWorktree: mainWorktreeSignal.asReadonly(),
      lastRepoPath: signal<string | null>(null).asReadonly(),
      fetchWorktrees: jasmine.createSpy('fetchWorktrees'),
      activeWorktreePath: signal<string | null>(null).asReadonly(),
    },
    runsService: {
      resumableRuns: resumableRunsSignal.asReadonly(),
      fetchResumableRuns: jasmine.createSpy('fetchResumableRuns'),
    },
  });

  beforeEach(async () => {
    worktreesSignal = signal<WorktreeInfo[]>([]);
    mainWorktreeSignal = signal<WorktreeInfo | null>(null);
    resumableRunsSignal = signal<RunDetail[]>([]);

    const mockServices = createMockServices();

    await TestBed.configureTestingModule({
      imports: [
        HomeComponent,
        StatCardComponent,
        QuickActionComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: WorktreesService, useValue: mockServices.worktreesService },
        { provide: RunsService, useValue: mockServices.runsService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(HomeComponent);
    component = fixture.componentInstance;
  });

  describe('hasContent', () => {
    it('should return false when no worktrees or runs exist', () => {
      expect(component.hasContent()).toBe(false);
    });

    it('should return true when worktrees exist', () => {
      worktreesSignal.set([createMockWorktree()]);
      fixture.detectChanges();

      expect(component.hasContent()).toBe(true);
    });

    it('should return true when resumable runs exist', () => {
      resumableRunsSignal.set([createMockRun()]);
      fixture.detectChanges();

      expect(component.hasContent()).toBe(true);
    });
  });

  describe('activeWorktreeCount', () => {
    it('should count non-main worktrees', () => {
      worktreesSignal.set([
        createMockWorktree({ is_main: true }),
        createMockWorktree({ path: '/wt1', name: 'feature-a', is_main: false }),
        createMockWorktree({ path: '/wt2', name: 'feature-b', is_main: false }),
      ]);
      fixture.detectChanges();

      expect(component.activeWorktreeCount()).toBe(2);
    });

    it('should return 0 when only main worktree exists', () => {
      worktreesSignal.set([createMockWorktree()]);
      fixture.detectChanges();

      expect(component.activeWorktreeCount()).toBe(0);
    });
  });

  describe('resumableRunsCount', () => {
    it('should count resumable runs', () => {
      resumableRunsSignal.set([
        createMockRun({ run_id: 'run-1' }),
        createMockRun({ run_id: 'run-2' }),
      ]);
      fixture.detectChanges();

      expect(component.resumableRunsCount()).toBe(2);
    });
  });

  describe('navigation', () => {
    let router: Router;

    beforeEach(() => {
      router = TestBed.inject(Router);
    });

    it('should navigate to run detail', () => {
      const navigateSpy = spyOn(router, 'navigate');

      component.navigateToRun('run-123');

      expect(navigateSpy).toHaveBeenCalledWith(['/runs', 'run-123']);
    });

    it('should navigate to sessions', () => {
      const navigateSpy = spyOn(router, 'navigate');

      component.navigateToSessions();

      expect(navigateSpy).toHaveBeenCalledWith(['/sessions']);
    });

    it('should navigate to worktrees', () => {
      const navigateSpy = spyOn(router, 'navigate');

      component.navigateToWorktrees();

      expect(navigateSpy).toHaveBeenCalledWith(['/worktrees']);
    });
  });
});

describe('StatCardComponent', () => {
  let component: StatCardComponent;
  let fixture: ComponentFixture<StatCardComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StatCardComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(StatCardComponent);
    component = fixture.componentInstance;
  });

  it('should display label and value', () => {
    component.label = 'Active worktrees';
    component.value = 5;
    fixture.detectChanges();

    const compiled = fixture.nativeElement;
    expect(compiled.textContent).toContain('5');
    expect(compiled.textContent).toContain('Active worktrees');
  });

  describe('valueStyle', () => {
    it('should use primary text color by default', () => {
      component.accent = false;
      component.value = 3;

      const style = component.valueStyle;

      expect(style).toContain('color: var(--text-primary)');
      expect(style).toContain('text-shadow: none');
    });

    it('should use accent color when accent is true and value > 0', () => {
      component.accent = true;
      component.value = 5;

      const style = component.valueStyle;

      expect(style).toContain('color: var(--accent)');
      expect(style).toContain('text-shadow');
    });

    it('should not use accent when value is 0', () => {
      component.accent = true;
      component.value = 0;

      const style = component.valueStyle;

      expect(style).toContain('color: var(--text-primary)');
    });
  });
});

describe('QuickActionComponent', () => {
  let component: QuickActionComponent;
  let fixture: ComponentFixture<QuickActionComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [QuickActionComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(QuickActionComponent);
    component = fixture.componentInstance;
  });

  it('should display icon, label, and description', () => {
    component.icon = '▶';
    component.label = 'New session';
    component.desc = 'Start an unattended run';
    fixture.detectChanges();

    const compiled = fixture.nativeElement;
    expect(compiled.textContent).toContain('▶');
    expect(compiled.textContent).toContain('New session');
    expect(compiled.textContent).toContain('Start an unattended run');
  });

  it('should emit action on click', () => {
    spyOn(component.action, 'emit');

    fixture.debugElement.nativeElement.querySelector('button').click();

    expect(component.action.emit).toHaveBeenCalled();
  });
});

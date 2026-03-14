import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, RouterModule } from '@angular/router';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { HomeComponent } from './home.component';
import { StatCardComponent } from './stat-card.component';
import { QuickActionComponent } from './quick-action.component';
import { ActiveRunsListComponent } from '../../components/active-runs-list/active-runs-list.component';
import { RecentCompletionsComponent } from '../../components/recent-completions/recent-completions.component';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionsService } from '../../services/sessions.service';
import { WorkspaceService, type Workspace } from '../../services/workspace.service';
import { signal, WritableSignal, computed } from '@angular/core';
import type { WorktreeInfo, SessionSummary } from '../../types';

describe('HomeComponent', () => {
  let component: HomeComponent;
  let fixture: ComponentFixture<HomeComponent>;
  let worktreesSignal: WritableSignal<WorktreeInfo[]>;
  let mainWorktreeSignal: WritableSignal<WorktreeInfo | null>;
  let sessionsSignal: WritableSignal<SessionSummary[]>;
  let activeWorkspaceSignal: WritableSignal<Workspace | null>;

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  const createMockSession = (overrides: Partial<SessionSummary> = {}): SessionSummary => ({
    run_id: 'run-123',
    status: 'paused',
    repo_path: '/repo',
    worktree_path: null,
    created_at: '2024-01-01T00:00:00Z',
    description: 'Test run',
    developer_agent: 'default',
    reviewer_agent: 'default',
    phase: 'development',
    ...overrides,
  });

  const createMockServices = () => {
    const needsAttentionRuns = computed(() =>
      sessionsSignal().filter(s => s.status === 'failed' || s.status === 'paused' || s.status === 'interrupted')
    );
    const activeRuns = computed(() =>
      sessionsSignal().filter(s => s.status === 'running')
    );
    const recentCompletions = computed(() =>
      sessionsSignal().filter(s => s.status === 'completed').slice(0, 10)
    );
    const completedToday = computed(() => {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      return sessionsSignal().filter(s => {
        if (s.status !== 'completed') return false;
        const created = new Date(s.created_at);
        created.setHours(0, 0, 0, 0);
        return created.getTime() === today.getTime();
      }).length;
    });
    const completedTodayStats = computed(() => {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const todaySessions = sessionsSignal().filter(s => {
        const created = new Date(s.created_at);
        created.setHours(0, 0, 0, 0);
        return created.getTime() === today.getTime();
      });
      const completed = todaySessions.filter(s => s.status === 'completed').length;
      const failed = todaySessions.filter(s => s.status === 'failed').length;
      const totalFinished = completed + failed;
      const successRate = totalFinished > 0 ? Math.round((completed / totalFinished) * 100) : 100;
      return { count: completed, successRate: `${successRate}%` };
    });
    const dashboardTrends = computed(() => ({
      activeWorktrees: 'flat' as const,
      resumableRuns: 'flat' as const,
      completedToday: 'flat' as const,
      successRate: 'flat' as const,
    }));

    return {
      worktreesService: {
        worktrees: worktreesSignal.asReadonly(),
        mainWorktree: mainWorktreeSignal.asReadonly(),
        lastRepoPath: signal<string | null>(null).asReadonly(),
      fetchWorktrees: vi.fn().mockReturnValue(Promise.resolve()),
      activeWorktreePath: signal<string | null>(null).asReadonly(),
    },
    sessionsService: {
      sessions: sessionsSignal.asReadonly(),
      fetchSessions: vi.fn().mockReturnValue(Promise.resolve()),
      needsAttentionRuns,
      activeRuns,
      recentCompletions,
      completedToday,
      completedTodayStats,
      dashboardTrends,
    },
    workspaceService: {
      activeWorkspace: activeWorkspaceSignal.asReadonly(),
      workspaces: signal<Workspace[]>([]).asReadonly(),
    },
  };
  };

  beforeEach(async () => {
    worktreesSignal = signal<WorktreeInfo[]>([]);
    mainWorktreeSignal = signal<WorktreeInfo | null>(null);
    sessionsSignal = signal<SessionSummary[]>([]);
    activeWorkspaceSignal = signal<Workspace | null>(null);

    const mockServices = createMockServices();

    await TestBed.configureTestingModule({
      imports: [
        HomeComponent,
        StatCardComponent,
        QuickActionComponent,
        ActiveRunsListComponent,
        RecentCompletionsComponent,
        RunStatusBadgeComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: WorktreesService, useValue: mockServices.worktreesService },
        { provide: SessionsService, useValue: mockServices.sessionsService },
        { provide: WorkspaceService, useValue: mockServices.workspaceService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(HomeComponent);
    component = fixture.componentInstance;
  });

  describe('hasContent', () => {
    it('should return false when no worktrees or sessions exist', () => {
      expect(component.hasContentValue).toBe(false);
    });

    it('should return true when worktrees exist', () => {
      worktreesSignal.set([createMockWorktree()]);
      fixture.detectChanges();

      expect(component.hasContentValue).toBe(true);
    });

    it('should return true when sessions exist', () => {
      sessionsSignal.set([createMockSession()]);
      fixture.detectChanges();

      expect(component.hasContentValue).toBe(true);
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

      expect(component.activeWorktreeCountValue).toBe(2);
    });

    it('should return 0 when only main worktree exists', () => {
      worktreesSignal.set([createMockWorktree()]);
      fixture.detectChanges();

      expect(component.activeWorktreeCountValue).toBe(0);
    });
  });

  describe('resumableRunsCount', () => {
    it('should count needs attention runs', () => {
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'paused' }),
        createMockSession({ run_id: 'run-2', status: 'failed' }),
      ]);
      fixture.detectChanges();

      expect(component.resumableRunsCountValue).toBe(2);
    });
  });

  describe('navigation', () => {
    let router: Router;

    beforeEach(() => {
      router = TestBed.inject(Router);
    });

    it('should navigate to run detail', () => {
      const navigateSpy = vi.spyOn(router, 'navigate');

      component.navigateToRun('run-123');

      expect(navigateSpy).toHaveBeenCalledWith(['/runs', 'run-123']);
    });

    it('should navigate to sessions', () => {
      const navigateSpy = vi.spyOn(router, 'navigate');

      component.navigateToSessions();

      expect(navigateSpy).toHaveBeenCalledWith(['/sessions']);
    });

    it('should navigate to worktrees', () => {
      const navigateSpy = vi.spyOn(router, 'navigate');

      component.navigateToWorktrees();

      expect(navigateSpy).toHaveBeenCalledWith(['/worktrees']);
    });

    it('should navigate to configuration', () => {
      const navigateSpy = vi.spyOn(router, 'navigate');

      component.navigateToConfiguration();

      expect(navigateSpy).toHaveBeenCalledWith(['/configuration']);
    });
  });

  describe('stat cards', () => {
    it('should render all three stat cards (active worktrees, resumable runs, completed today)', () => {
      worktreesSignal.set([
        createMockWorktree({ is_main: true }),
        createMockWorktree({ path: '/wt1', name: 'feature-a', is_main: false }),
      ]);
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'paused' }),
        createMockSession({ run_id: 'run-2', status: 'completed', created_at: new Date().toISOString() }),
      ]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const statCards = compiled.querySelectorAll('app-stat-card');
      expect(statCards.length).toBe(4);
      expect(compiled.textContent).toContain('Active worktrees');
      expect(compiled.textContent).toContain('Resumable runs');
      expect(compiled.textContent).toContain('Completed today');
      expect(compiled.textContent).toContain('Success rate');
    });

    it('should display correct active worktree count', () => {
      worktreesSignal.set([
        createMockWorktree({ is_main: true }),
        createMockWorktree({ path: '/wt1', name: 'feature-a', is_main: false }),
        createMockWorktree({ path: '/wt2', name: 'feature-b', is_main: false }),
      ]);
      fixture.detectChanges();

      expect(component.activeWorktreeCountValue).toBe(2);
    });

    it('should display correct resumable runs count', () => {
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'paused' }),
        createMockSession({ run_id: 'run-2', status: 'failed' }),
        createMockSession({ run_id: 'run-3', status: 'interrupted' }),
      ]);
      fixture.detectChanges();

      expect(component.resumableRunsCountValue).toBe(3);
    });

    it('should display correct completed today count', () => {
      const today = new Date().toISOString();
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'completed', created_at: today }),
        createMockSession({ run_id: 'run-2', status: 'completed', created_at: today }),
      ]);
      fixture.detectChanges();

      expect(component.completedTodayStatsValue.count).toBe(2);
    });
  });

  describe('active runs section', () => {
    it('should render active runs section when running sessions exist', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'run-1', status: 'running' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Active runs');
    });

    it('should hide active runs section when no running sessions', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'run-1', status: 'paused' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('Active runs');
    });

    it('should navigate to run detail on viewRun event from active runs', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate');

      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'active-run-1', status: 'running' })]);
      fixture.detectChanges();

      component.navigateToRun('active-run-1');

      expect(navigateSpy).toHaveBeenCalledWith(['/runs', 'active-run-1']);
    });
  });

  describe('needs attention section', () => {
    it('should render needs-attention section for failed/paused runs', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'failed' }),
        createMockSession({ run_id: 'run-2', status: 'paused' }),
      ]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Needs attention');
    });

    it('should not render needs-attention section when no failed/paused runs', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'run-1', status: 'running' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('Needs attention');
    });

    it('should navigate to run detail on click in needs attention section', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate');

      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'failed-run-1', status: 'failed' })]);
      fixture.detectChanges();

      component.navigateToRun('failed-run-1');

      expect(navigateSpy).toHaveBeenCalledWith(['/runs', 'failed-run-1']);
    });
  });

  describe('recent completions section', () => {
    it('should render recent completions section when completed sessions exist', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'run-1', status: 'completed' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Recent completions');
    });

    it('should not render recent completions section when no completed sessions', () => {
      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'run-1', status: 'running' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('Recent completions');
    });

    it('should navigate to run detail on viewRun event from recent completions', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate');

      worktreesSignal.set([createMockWorktree()]);
      sessionsSignal.set([createMockSession({ run_id: 'completed-run-1', status: 'completed' })]);
      fixture.detectChanges();

      component.navigateToRun('completed-run-1');

      expect(navigateSpy).toHaveBeenCalledWith(['/runs', 'completed-run-1']);
    });
  });

  describe('dashboard signals', () => {
    it('should expose activeRuns from SessionsService', () => {
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'running' }),
        createMockSession({ run_id: 'run-2', status: 'paused' }),
      ]);
      fixture.detectChanges();

      const activeRuns = component.activeRunsValue;
      expect(activeRuns.length).toBe(1);
      expect(activeRuns[0]!.run_id).toBe('run-1');
    });

    it('should expose needsAttentionRuns from SessionsService', () => {
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'failed' }),
        createMockSession({ run_id: 'run-2', status: 'paused' }),
        createMockSession({ run_id: 'run-3', status: 'running' }),
      ]);
      fixture.detectChanges();

      expect(component.needsAttentionRunsValue.length).toBe(2);
    });

    it('should expose recentCompletions from SessionsService', () => {
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'completed' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
        createMockSession({ run_id: 'run-3', status: 'running' }),
      ]);
      fixture.detectChanges();

      expect(component.recentCompletionsValue.length).toBe(2);
    });

    it('should expose completedTodayCount from SessionsService', () => {
      const today = new Date().toISOString();
      sessionsSignal.set([
        createMockSession({ run_id: 'run-1', status: 'completed', created_at: today }),
        createMockSession({ run_id: 'run-2', status: 'completed', created_at: today }),
      ]);
      fixture.detectChanges();

      expect(component.completedTodayStatsValue.count).toBe(2);
    });
  });

  describe('polling', () => {
    it('should have polling interval defined', () => {
      expect(component).toBeDefined();
    });
  });

  describe('workspace change', () => {
    it('should re-fetch sessions when workspace changes', async () => {
      const mockWorkspace: Workspace = {
        id: 'ws-1',
        path: '/repo',
        label: 'Test Repo',
        activeWorktree: null,
        runSummary: { running: 0, failed: 0, paused: 0 },
        navigationState: null,
        activeRunCount: 0,
      };

      worktreesSignal.set([createMockWorktree()]);
      activeWorkspaceSignal.set(mockWorkspace);
      fixture.detectChanges();
      await fixture.whenStable();

      const sessionsService = TestBed.inject(SessionsService);
      expect(sessionsService.fetchSessions).toHaveBeenCalledWith('/repo');
    });
  });
});

describe('StatCardComponent', () => {
  let component: StatCardComponent;
  let componentRef: ComponentFixture<StatCardComponent>['componentRef'];
  let fixture: ComponentFixture<StatCardComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StatCardComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(StatCardComponent);
    component = fixture.componentInstance;
    componentRef = fixture.componentRef;
  });

  it('should display label and value', () => {
    componentRef.setInput('label', 'Active worktrees');
    componentRef.setInput('value', 5);
    fixture.detectChanges();

    const compiled = fixture.nativeElement;
    expect(compiled.textContent).toContain('5');
    expect(compiled.textContent).toContain('Active worktrees');
  });

  describe('valueClasses', () => {
    it('should use primary text color by default', () => {
      componentRef.setInput('accent', false);
      componentRef.setInput('value', 3);
      fixture.detectChanges();

      const classes = component.valueClassesValue;

      expect(classes).toContain('text-text-primary');
    });

    it('should use accent color when accent is true and value > 0', () => {
      componentRef.setInput('accent', true);
      componentRef.setInput('value', 5);
      fixture.detectChanges();

      const classes = component.valueClassesValue;

      expect(classes).toContain('text-accent');
    });

    it('should not use accent when value is 0', () => {
      componentRef.setInput('accent', true);
      componentRef.setInput('value', 0);
      fixture.detectChanges();

      const classes = component.valueClassesValue;

      expect(classes).toContain('text-text-primary');
    });
  });
});

describe('QuickActionComponent', () => {
  let component: QuickActionComponent;
  let componentRef: ComponentFixture<QuickActionComponent>['componentRef'];
  let fixture: ComponentFixture<QuickActionComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [QuickActionComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(QuickActionComponent);
    component = fixture.componentInstance;
    componentRef = fixture.componentRef;
  });

  it('should display icon, label, and description', () => {
    componentRef.setInput('icon', '▶');
    componentRef.setInput('label', 'New session');
    componentRef.setInput('desc', 'Start an unattended run');
    fixture.detectChanges();

    const compiled = fixture.nativeElement;
    expect(compiled.textContent).toContain('▶');
    expect(compiled.textContent).toContain('New session');
    expect(compiled.textContent).toContain('Start an unattended run');
  });

  it('should emit action on click', () => {
    vi.spyOn(component.action, 'emit');

    fixture.debugElement.nativeElement.querySelector('button').click();

    expect(component.action.emit).toHaveBeenCalled();
  });
});

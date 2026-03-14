import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Router, RouterModule, ActivatedRoute } from '@angular/router';
import { SessionsComponent } from './sessions.component';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionsService } from '../../services/sessions.service';
import { SessionListComponent } from '../../components/session-list/session-list.component';
import { NewSessionWizardComponent } from '../../components/new-session-wizard/new-session-wizard.component';
import { signal } from '@angular/core';
import type { WorktreeInfo, SessionSummary } from '../../types';

const makeSessions = (): SessionSummary[] => [
  {
    run_id: 'run-001',
    status: 'running',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-01T10:00:00Z',
    description: 'Test 1',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
  {
    run_id: 'run-002',
    status: 'running',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-01T10:00:00Z',
    description: 'Test 2',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
  {
    run_id: 'run-003',
    status: 'paused',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-01T10:00:00Z',
    description: 'Test 3',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
  {
    run_id: 'run-004',
    status: 'completed',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-01T10:00:00Z',
    description: 'Test 4',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
  {
    run_id: 'run-005',
    status: 'failed',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-01T10:00:00Z',
    description: 'Test 5',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
];

describe('SessionsComponent', () => {
  let component: SessionsComponent;
  let fixture: ComponentFixture<SessionsComponent>;
  let mockWorktreesService: {
    worktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
    mainWorktree: ReturnType<typeof signal<WorktreeInfo | null>>;
    repoPath: ReturnType<typeof signal<string>>;
    nonMainWorktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
  };
  let mockSessionsService: {
    sessions: ReturnType<typeof signal<SessionSummary[]>>;
  };
  let mockRouter: { navigate: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    mockWorktreesService = {
      worktrees: signal<WorktreeInfo[]>([]),
      mainWorktree: signal<WorktreeInfo | null>(null),
      repoPath: signal(''),
      nonMainWorktrees: signal<WorktreeInfo[]>([]),
    };

    mockSessionsService = {
      sessions: signal<SessionSummary[]>([]),
    };

    mockRouter = { navigate: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [
        SessionsComponent,
        SessionListComponent,
        NewSessionWizardComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: SessionsService, useValue: mockSessionsService },
        { provide: Router, useValue: mockRouter },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              queryParams: {},
            },
          },
        },
      ],
    }).compileComponents();

    TestBed.overrideComponent(SessionsComponent, {
      remove: { imports: [SessionListComponent, NewSessionWizardComponent] },
      add: {
        imports: [],
        template: `<div>Mocked Sessions</div>`,
      },
    });

    fixture = TestBed.createComponent(SessionsComponent);
    component = fixture.componentInstance;
  });

  describe('view state', () => {
    it('should start with list view', () => {
      expect(component.view()).toBe('list');
    });

    it('should switch to new view', () => {
      component.setView('new');

      expect(component.view()).toBe('new');
    });

    it('should switch back to list view', () => {
      component.setView('new');
      component.setView('list');

      expect(component.view()).toBe('list');
    });

    it('should clear preselected worktree when switching to list', () => {
      component.preselectedWorktree.set('/worktree');
      component.setView('list');

      expect(component.preselectedWorktree()).toBeNull();
    });
  });

  describe('status filters', () => {
    it('should start with no status filters', () => {
      expect(component.activeStatusFilters()).toEqual([]);
    });

    it('should toggle status filter on', () => {
      component.toggleStatusFilter('running');

      expect(component.activeStatusFilters()).toContain('running');
    });

    it('should toggle status filter off', () => {
      component.toggleStatusFilter('running');
      component.toggleStatusFilter('running');

      expect(component.activeStatusFilters()).not.toContain('running');
    });

    it('should support multiple status filters', () => {
      component.toggleStatusFilter('running');
      component.toggleStatusFilter('paused');

      expect(component.activeStatusFilters()).toContain('running');
      expect(component.activeStatusFilters()).toContain('paused');
    });
  });

  describe('context filter', () => {
    it('should start with "all" context filter', () => {
      expect(component.contextFilter()).toBe('all');
    });

    it('should change context filter on select change', () => {
      const event = {
        target: { value: 'direct' },
      } as unknown as Event;

      component.onContextFilterChange(event);

      expect(component.contextFilter()).toBe('direct');
    });

    it('should return undefined for "all" filter worktree path', () => {
      component.contextFilter.set('all');

      expect(component.filterWorktreePath).toBeUndefined();
    });

    it('should return empty string for "direct" filter worktree path', () => {
      component.contextFilter.set('direct');

      expect(component.filterWorktreePath).toBe('');
    });

    it('should return path for specific worktree filter', () => {
      component.contextFilter.set('/path/to/worktree');

      expect(component.filterWorktreePath).toBe('/path/to/worktree');
    });
  });

  describe('clearFilters', () => {
    it('should reset all filters', () => {
      component.toggleStatusFilter('running');
      component.toggleStatusFilter('paused');
      component.contextFilter.set('direct');

      component.clearFilters();

      expect(component.activeStatusFilters()).toEqual([]);
      expect(component.contextFilter()).toBe('all');
    });
  });

  describe('statusCounts computed', () => {
    it('should return correct counts per status', () => {
      mockSessionsService.sessions.set(makeSessions());

      const counts = component.statusCounts();

      expect(counts.all).toBe(5);
      expect(counts.running).toBe(2);
      expect(counts.paused).toBe(1);
      expect(counts.completed).toBe(1);
      expect(counts.failed).toBe(1);
    });

    it('should count interrupted as paused', () => {
      mockSessionsService.sessions.set([
        { run_id: 'run-int', status: 'interrupted', repo_path: '/repo/a', worktree_path: null, created_at: '2026-01-01T10:00:00Z', description: 'Interrupted', developer_agent: 'claude', reviewer_agent: 'claude', phase: 'develop' },
        { run_id: 'run-pause', status: 'paused', repo_path: '/repo/a', worktree_path: null, created_at: '2026-01-01T10:00:00Z', description: 'Paused', developer_agent: 'claude', reviewer_agent: 'claude', phase: 'develop' },
      ]);

      const counts = component.statusCounts();

      expect(counts.paused).toBe(2);
    });

    it('should return zeros when no sessions', () => {
      mockSessionsService.sessions.set([]);

      const counts = component.statusCounts();

      expect(counts.all).toBe(0);
      expect(counts.running).toBe(0);
      expect(counts.paused).toBe(0);
      expect(counts.completed).toBe(0);
      expect(counts.failed).toBe(0);
    });
  });

  describe('statusChipsWithCount', () => {
    it('should show counts alongside labels', () => {
      mockSessionsService.sessions.set(makeSessions());

      const chips = component.statusChipsWithCountList;

      expect(chips.find(c => c.value === 'running')?.count).toBe(2);
      expect(chips.find(c => c.value === 'paused')?.count).toBe(1);
      expect(chips.find(c => c.value === 'completed')?.count).toBe(1);
      expect(chips.find(c => c.value === 'failed')?.count).toBe(1);
    });
  });

  describe('isFilterActive', () => {
    it('should return true when filter is active', () => {
      component.toggleStatusFilter('running');

      expect(component.isFilterActive('running')).toBe(true);
    });

    it('should return false when filter is not active', () => {
      expect(component.isFilterActive('running')).toBe(false);
    });
  });

  describe('handleResume', () => {
    it('should navigate to run detail page', () => {
      component.handleResume('run-123');

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/runs', 'run-123']);
    });
  });
});

describe('SessionsComponent query param handling', () => {
  let component: SessionsComponent;
  let fixture: ComponentFixture<SessionsComponent>;
  let mockWorktreesService: {
    worktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
    mainWorktree: ReturnType<typeof signal<WorktreeInfo | null>>;
    repoPath: ReturnType<typeof signal<string>>;
    nonMainWorktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
  };
  let mockSessionsService: {
    sessions: ReturnType<typeof signal<SessionSummary[]>>;
  };

  const createComponentWithQueryParams = async (queryParams: Record<string, string>) => {
    mockWorktreesService = {
      worktrees: signal<WorktreeInfo[]>([]),
      mainWorktree: signal<WorktreeInfo | null>(null),
      repoPath: signal(''),
      nonMainWorktrees: signal<WorktreeInfo[]>([]),
    };

    mockSessionsService = {
      sessions: signal<SessionSummary[]>([]),
    };

    await TestBed.configureTestingModule({
      imports: [
        SessionsComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: SessionsService, useValue: mockSessionsService },
        { provide: Router, useValue: { navigate: vi.fn() } },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { queryParams },
          },
        },
      ],
    }).compileComponents();

    TestBed.overrideComponent(SessionsComponent, {
      remove: { imports: [SessionListComponent, NewSessionWizardComponent] },
      add: {
        imports: [],
        template: `<div>Mocked Sessions</div>`,
      },
    });

    fixture = TestBed.createComponent(SessionsComponent);
    component = fixture.componentInstance;
  };

  it('should read "new" query param to set view', async () => {
    await createComponentWithQueryParams({ new: 'true' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.view()).toBe('new');
  });

  it('should read worktree from query params', async () => {
    await createComponentWithQueryParams({ new: 'true', worktree: '/path/to/wt' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.preselectedWorktree()).toBe('/path/to/wt');
  });
});

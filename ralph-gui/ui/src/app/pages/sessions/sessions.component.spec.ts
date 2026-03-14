import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Router, RouterModule, ActivatedRoute } from '@angular/router';
import { SessionsComponent } from './sessions.component';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionListComponent } from '../../components/session-list/session-list.component';
import { NewSessionWizardComponent } from '../../components/new-session-wizard/new-session-wizard.component';
import { signal } from '@angular/core';
import type { WorktreeInfo } from '../../types';

describe('SessionsComponent', () => {
  let component: SessionsComponent;
  let fixture: ComponentFixture<SessionsComponent>;
  let mockWorktreesService: {
    worktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
    mainWorktree: ReturnType<typeof signal<WorktreeInfo | null>>;
    repoPath: ReturnType<typeof signal<string>>;
    nonMainWorktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
  };
  let mockRouter: { navigate: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    mockWorktreesService = {
      worktrees: signal<WorktreeInfo[]>([]),
      mainWorktree: signal<WorktreeInfo | null>(null),
      repoPath: signal(''),
      nonMainWorktrees: signal<WorktreeInfo[]>([]),
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

    // Mock child components
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

  describe('chipButtonStyle', () => {
    it('should return active style for selected filter', () => {
      component.toggleStatusFilter('running');

      const style = component.chipButtonStyle('running');

      expect(style).toContain('var(--accent)');
      expect(style).toContain('var(--accent-bg)');
    });

    it('should return inactive style for unselected filter', () => {
      const style = component.chipButtonStyle('running');

      expect(style).toContain('var(--text-muted)');
      expect(style).toContain('var(--border-default)');
    });
  });

  describe('handleResume', () => {
    it('should navigate to run detail page', () => {
      component.handleResume('run-123');

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/runs', 'run-123']);
    });
  });
});

// Separate describe block for query param tests with fresh TestBed
describe('SessionsComponent query param handling', () => {
  let component: SessionsComponent;
  let fixture: ComponentFixture<SessionsComponent>;
  let mockWorktreesService: {
    worktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
    mainWorktree: ReturnType<typeof signal<WorktreeInfo | null>>;
    repoPath: ReturnType<typeof signal<string>>;
    nonMainWorktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
  };

  const createComponentWithQueryParams = async (queryParams: Record<string, string>) => {
    mockWorktreesService = {
      worktrees: signal<WorktreeInfo[]>([]),
      mainWorktree: signal<WorktreeInfo | null>(null),
      repoPath: signal(''),
      nonMainWorktrees: signal<WorktreeInfo[]>([]),
    };

    await TestBed.configureTestingModule({
      imports: [
        SessionsComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
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

  it('should read "new" query param to set view', fakeAsync(async () => {
    await createComponentWithQueryParams({ new: 'true' });
    fixture.detectChanges();
    tick();

    expect(component.view()).toBe('new');
  }));

  it('should read worktree from query params', fakeAsync(async () => {
    await createComponentWithQueryParams({ new: 'true', worktree: '/path/to/wt' });
    fixture.detectChanges();
    tick();

    expect(component.preselectedWorktree()).toBe('/path/to/wt');
  }));
});

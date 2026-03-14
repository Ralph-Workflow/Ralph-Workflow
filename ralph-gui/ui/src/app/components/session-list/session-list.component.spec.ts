import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { signal } from '@angular/core';
import { Router } from '@angular/router';
import { SessionListComponent } from './session-list.component';
import { SessionsService } from '../../services/sessions.service';
import { TauriService } from '../../services/tauri.service';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { SessionSummary, BatchOperationResult } from '../../types';

const makeSessions = (): SessionSummary[] => [
  {
    run_id: 'run-001',
    status: 'paused',
    repo_path: '/repo/a',
    worktree_path: '/repo/a/wt-1',
    created_at: '2026-01-01T10:00:00Z',
    description: 'Add login feature',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
  },
  {
    run_id: 'run-002',
    status: 'running',
    repo_path: '/repo/a',
    worktree_path: null,
    created_at: '2026-01-02T10:00:00Z',
    description: 'Fix API bug',
    developer_agent: 'codex',
    reviewer_agent: 'claude',
    phase: 'review',
  },
  {
    run_id: 'run-003',
    status: 'failed',
    repo_path: '/repo/a',
    worktree_path: '/repo/a/wt-2',
    created_at: '2026-01-03T10:00:00Z',
    description: 'Refactor auth module',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'commit',
  },
  {
    run_id: 'run-004',
    status: 'completed',
    repo_path: '/repo/a',
    worktree_path: '/repo/a/wt-1',
    created_at: '2026-01-04T10:00:00Z',
    description: 'Update docs',
    developer_agent: 'codex',
    reviewer_agent: 'claude',
    phase: 'plan',
  },
];

describe('SessionListComponent', () => {
  let component: SessionListComponent;
  let fixture: ComponentFixture<SessionListComponent>;
  let mockSessionsService: {
    fetchSessions: ReturnType<typeof vi.fn>;
    setActiveSession: ReturnType<typeof vi.fn>;
    resumeSession: ReturnType<typeof vi.fn>;
    sessions: ReturnType<typeof signal<SessionSummary[]>>;
    status: ReturnType<typeof signal<'idle' | 'loading' | 'succeeded' | 'failed'>>;
    error: ReturnType<typeof signal<string | null>>;
    selectedRunId: ReturnType<typeof signal<string | null>>;
    isLoading: ReturnType<typeof signal<boolean>>;
  };
  let mockTauriService: {
    batchResumeSessions: ReturnType<typeof vi.fn>;
    batchCancelSessions: ReturnType<typeof vi.fn>;
    batchDeleteSessions: ReturnType<typeof vi.fn>;
  };
  let mockRouter: { navigate: ReturnType<typeof vi.fn> };
  let mockInvoke: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    mockInvoke = vi.fn().mockResolvedValue([]);

    mockSessionsService = {
      fetchSessions: vi.fn(),
      setActiveSession: vi.fn(),
      resumeSession: vi.fn(),
      sessions: signal<SessionSummary[]>([]),
      status: signal<'idle' | 'loading' | 'succeeded' | 'failed'>('idle'),
      error: signal<string | null>(null),
      selectedRunId: signal<string | null>(null),
      isLoading: signal(false),
    };

    mockTauriService = {
      batchResumeSessions: vi.fn().mockResolvedValue({ succeeded: 0, failed: 0, errors: {} }),
      batchCancelSessions: vi.fn().mockResolvedValue({ succeeded: 0, failed: 0, errors: {} }),
      batchDeleteSessions: vi.fn().mockResolvedValue({ succeeded: 0, failed: 0, errors: {} }),
    };

    mockRouter = { navigate: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [SessionListComponent],
      providers: [
        { provide: SessionsService, useValue: mockSessionsService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: Router, useValue: mockRouter },
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SessionListComponent);
    component = fixture.componentInstance;
    component.repoPath = '/repo/a';
  });

  describe('search filter', () => {
    it('should filter sessions by description (case-insensitive)', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.searchTerm.set('login');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(1);
      expect(visible[0]?.run_id).toBe('run-001');
    });

    it('should filter by run_id', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.searchTerm.set('run-002');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(1);
      expect(visible[0]?.run_id).toBe('run-002');
    });

    it('should filter by worktree_path', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.searchTerm.set('wt-2');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(1);
      expect(visible[0]?.run_id).toBe('run-003');
    });

    it('should return all sessions when search is empty', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.searchTerm.set('');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(4);
    });

    it('should be case-insensitive', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.searchTerm.set('ADD LOGIN');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(1);
    });
  });

  describe('worktree filter dropdown', () => {
    it('should list distinct worktree paths plus "All Worktrees"', () => {
      mockSessionsService.sessions.set(makeSessions());

      const options = component.worktreeFilterOptions();

      expect(options[0]).toEqual({ label: 'All Worktrees', value: '__all__' });
      expect(options.some(o => o.value === '/repo/a/wt-1')).toBe(true);
      expect(options.some(o => o.value === '/repo/a/wt-2')).toBe(true);
    });

    it('should filter sessions by worktree when selected', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.worktreeFilter.set('/repo/a/wt-1');

      const visible = component.visibleSessions();

      expect(visible.every(s => s.worktree_path === '/repo/a/wt-1')).toBe(true);
    });

    it('should show all sessions when "All Worktrees" selected', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.worktreeFilter.set('__all__');

      const visible = component.visibleSessions();

      expect(visible.length).toBe(4);
    });
  });

  describe('sortable columns', () => {
    it('should default sort by created_at descending', () => {
      mockSessionsService.sessions.set(makeSessions());

      const visible = component.visibleSessions();

      expect(visible[0]?.run_id).toBe('run-004');
      expect(visible[visible.length - 1]?.run_id).toBe('run-001');
    });

    it('should sort by description ascending on first click', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.setSort('description');

      const visible = component.visibleSessions();

      expect(visible[0]?.description).toBe('Add login feature');
    });

    it('should toggle sort direction on second click of same column', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.setSort('description');
      component.setSort('description');

      expect(component.sortDirection()).toBe('desc');
    });

    it('should reset to asc when switching to a different column', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.setSort('description');
      component.setSort('status');

      expect(component.sortKey()).toBe('status');
      expect(component.sortDirection()).toBe('asc');
    });
  });

  describe('row selection', () => {
    it('should start with empty selection', () => {
      expect(component.selectedIds().size).toBe(0);
    });

    it('should select a session by id', () => {
      component.toggleSelect('run-001');

      expect(component.selectedIds().has('run-001')).toBe(true);
    });

    it('should deselect already selected session', () => {
      component.toggleSelect('run-001');
      component.toggleSelect('run-001');

      expect(component.selectedIds().has('run-001')).toBe(false);
    });

    it('should select all visible sessions', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.selectAll();

      const ids = component.selectedIds();
      expect(ids.size).toBe(4);
    });

    it('should deselect all when all are selected', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.selectAll();
      component.selectAll();

      expect(component.selectedIds().size).toBe(0);
    });

    it('isAllSelected returns true when all visible sessions are selected', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.selectAll();

      expect(component.isAllSelected()).toBe(true);
    });

    it('isAllSelected returns false when no sessions selected', () => {
      mockSessionsService.sessions.set(makeSessions());

      expect(component.isAllSelected()).toBe(false);
    });
  });

  describe('batch action bar', () => {
    it('should show batch bar when selection is non-empty', () => {
      component.toggleSelect('run-001');

      expect(component.showBatchBar()).toBe(true);
    });

    it('should hide batch bar when selection is empty', () => {
      expect(component.showBatchBar()).toBe(false);
    });

    it('canBatchResume is true when selection contains paused/failed session', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');

      expect(component.canBatchResume()).toBe(true);
    });

    it('canBatchResume is false when selection only contains running sessions', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');

      expect(component.canBatchResume()).toBe(false);
    });

    it('canBatchCancel is true when selection contains running session', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');

      expect(component.canBatchCancel()).toBe(true);
    });

    it('canBatchCancel is false when selection has no running sessions', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');

      expect(component.canBatchCancel()).toBe(false);
    });
  });

  describe('displaySessions with new columns', () => {
    it('should include formatted pipelineStep field from session.phase', () => {
      mockSessionsService.sessions.set(makeSessions());

      const display = component.displaySessions();
      const developRow = display.find(d => d.session.run_id === 'run-001');
      const reviewRow = display.find(d => d.session.run_id === 'run-002');
      const commitRow = display.find(d => d.session.run_id === 'run-003');
      const planRow = display.find(d => d.session.run_id === 'run-004');

      expect(developRow?.pipelineStep).toBe('Develop');
      expect(reviewRow?.pipelineStep).toBe('Review');
      expect(commitRow?.pipelineStep).toBe('Commit');
      expect(planRow?.pipelineStep).toBe('Plan');
    });

    it('should include agent field from session.developer_agent', () => {
      mockSessionsService.sessions.set(makeSessions());

      const display = component.displaySessions();
      const claudeRow = display.find(d => d.session.run_id === 'run-001');
      const codexRow = display.find(d => d.session.run_id === 'run-002');

      expect(claudeRow?.session.developer_agent).toBe('claude');
      expect(codexRow?.session.developer_agent).toBe('codex');
    });

    it('should include age field formatted from created_at', () => {
      mockSessionsService.sessions.set(makeSessions());

      const display = component.displaySessions();
      expect(display[0]?.age).toBeDefined();
      expect(typeof display[0]?.age).toBe('string');
    });
  });

  describe('batch confirmation dialogs', () => {
    it('batchCancel() should set showBatchCancelDialog to true', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');
      component.batchCancel();

      expect(component.showBatchCancelDialog()).toBe(true);
    });

    it('batchDelete() should set showBatchDeleteDialog to true', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');
      component.batchDelete();

      expect(component.showBatchDeleteDialog()).toBe(true);
    });

    it('onBatchCancelConfirmed(false) should hide dialog without executing cancel', async () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');
      component.showBatchCancelDialog.set(true);

      await component.onBatchCancelConfirmed(false);

      expect(component.showBatchCancelDialog()).toBe(false);
      expect(mockTauriService.batchCancelSessions).not.toHaveBeenCalled();
    });

    it('onBatchDeleteConfirmed(false) should hide dialog without executing delete', async () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');
      component.showBatchDeleteDialog.set(true);

      await component.onBatchDeleteConfirmed(false);

      expect(component.showBatchDeleteDialog()).toBe(false);
      expect(mockTauriService.batchDeleteSessions).not.toHaveBeenCalled();
    });
  });

  describe('batch overlay integration', () => {
    it('batch overlay becomes visible after confirmed batch cancel operation starts', async () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');

      await component.onBatchCancelConfirmed(true);

      expect(component.batchOverlayVisible()).toBe(true);
    });

    it('batch overlay shows result after operation completes', async () => {
      const mockResult: BatchOperationResult = { succeeded: 1, failed: 0, errors: {} };
      mockTauriService.batchCancelSessions.mockResolvedValue(mockResult);
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002');

      await component.onBatchCancelConfirmed(true);

      expect(component.batchResult()).toEqual(mockResult);
      expect(component.batchInProgress()).toBe(false);
    });

    it('selection is cleared after batch overlay is closed', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');
      component.batchOverlayVisible.set(true);

      component.onBatchOverlayClosed();

      expect(component.selectedIds().size).toBe(0);
      expect(component.batchOverlayVisible()).toBe(false);
    });

    it('onOpenRun navigates to run detail and closes overlay', () => {
      component.batchOverlayVisible.set(true);
      component.selectedIds.set(new Set(['run-001']));

      component.onOpenRun('run-123');

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/runs', 'run-123']);
      expect(component.batchOverlayVisible()).toBe(false);
      expect(component.selectedIds().size).toBe(0);
    });
  });

  describe('batchResume', () => {
    it('should call tauri.batchResumeSessions with resumable IDs', async () => {
      mockTauriService.batchResumeSessions.mockResolvedValue({ succeeded: 2, failed: 0, errors: {} });
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');
      component.toggleSelect('run-002');
      component.toggleSelect('run-003');

      await component.batchResume();

      expect(mockTauriService.batchResumeSessions).toHaveBeenCalledWith(['run-001', 'run-003']);
    });

    it('should show batch overlay during batch resume', async () => {
      mockTauriService.batchResumeSessions.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve({ succeeded: 1, failed: 0, errors: {} }), 100)));
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001');

      const promise = component.batchResume();

      expect(component.batchOverlayVisible()).toBe(true);
      expect(component.batchInProgress()).toBe(true);

      await promise;

      expect(component.batchInProgress()).toBe(false);
    });
  });
});

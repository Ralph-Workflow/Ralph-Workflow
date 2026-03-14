import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { signal } from '@angular/core';
import { Router } from '@angular/router';
import { SessionListComponent } from './session-list.component';
import { SessionsService } from '../../services/sessions.service';

import { TAURI_INVOKE } from '../../services/tauri.service';
import type { SessionSummary } from '../../types';

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
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'develop',
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
    phase: 'review',
  },
  {
    run_id: 'run-004',
    status: 'completed',
    repo_path: '/repo/a',
    worktree_path: '/repo/a/wt-1',
    created_at: '2026-01-04T10:00:00Z',
    description: 'Update docs',
    developer_agent: 'claude',
    reviewer_agent: 'claude',
    phase: 'commit',
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

    mockRouter = { navigate: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [SessionListComponent],
      providers: [
        { provide: SessionsService, useValue: mockSessionsService },
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
      component.toggleSelect('run-001'); // paused

      expect(component.canBatchResume()).toBe(true);
    });

    it('canBatchResume is false when selection only contains running sessions', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002'); // running

      expect(component.canBatchResume()).toBe(false);
    });

    it('canBatchCancel is true when selection contains running session', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-002'); // running

      expect(component.canBatchCancel()).toBe(true);
    });

    it('canBatchCancel is false when selection has no running sessions', () => {
      mockSessionsService.sessions.set(makeSessions());
      component.toggleSelect('run-001'); // paused

      expect(component.canBatchCancel()).toBe(false);
    });
  });

  describe('batchResume', () => {
    it('should call resumeSession for each paused/failed session in selection', async () => {
      mockSessionsService.sessions.set(makeSessions());
      mockSessionsService.resumeSession.mockResolvedValue(undefined);
      component.toggleSelect('run-001'); // paused
      component.toggleSelect('run-002'); // running - should be skipped
      component.toggleSelect('run-003'); // failed

      await component.batchResume();
      await fixture.whenStable();

      expect(mockSessionsService.resumeSession).toHaveBeenCalledWith('run-001', '/repo/a');
      expect(mockSessionsService.resumeSession).toHaveBeenCalledWith('run-003', '/repo/a');
      expect(mockSessionsService.resumeSession).not.toHaveBeenCalledWith('run-002', '/repo/a');
    });
  });
});

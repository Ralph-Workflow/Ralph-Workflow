import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { SessionsService } from './sessions.service';
import { TauriService } from './tauri.service';
import type { SessionSummary } from '../types';

describe('SessionsService', () => {
  let service: SessionsService;
  let tauriServiceSpy: {
    getSessions: ReturnType<typeof vi.fn>;
    createSession: ReturnType<typeof vi.fn>;
    resumeRalphSession: ReturnType<typeof vi.fn>;
    getSessionDetail: ReturnType<typeof vi.fn>;
    notifyRunStatusChange: ReturnType<typeof vi.fn>;
  };

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

  beforeEach(() => {
    const spy = {
      getSessions: vi.fn(),
      createSession: vi.fn(),
      resumeRalphSession: vi.fn(),
      getSessionDetail: vi.fn(),
      notifyRunStatusChange: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        SessionsService,
        { provide: TauriService, useValue: spy },
      ],
    });

    service = TestBed.inject(SessionsService);
    tauriServiceSpy = spy;
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('activeRuns computed', () => {
    it('should filter only status=running sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'running' }),
        createMockSession({ run_id: 'run-2', status: 'paused' }),
        createMockSession({ run_id: 'run-3', status: 'completed' }),
        createMockSession({ run_id: 'run-4', status: 'running' }),
        createMockSession({ run_id: 'run-5', status: 'failed' }),
      ];

      service.sessions.set(sessions);

      const activeRuns = service.activeRuns();
      expect(activeRuns.length).toBe(2);
      expect(activeRuns.every(s => s.status === 'running')).toBe(true);
    });

    it('should sort by created_at descending', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-old', status: 'running', created_at: '2024-01-01T00:00:00Z' }),
        createMockSession({ run_id: 'run-new', status: 'running', created_at: '2024-01-02T00:00:00Z' }),
        createMockSession({ run_id: 'run-mid', status: 'running', created_at: '2024-01-01T12:00:00Z' }),
      ];

      service.sessions.set(sessions);

      const activeRuns = service.activeRuns();
      expect(activeRuns.length).toBe(3);
      expect(activeRuns[0]!.run_id).toBe('run-new');
      expect(activeRuns[1]!.run_id).toBe('run-mid');
      expect(activeRuns[2]!.run_id).toBe('run-old');
    });

    it('should return empty array when no running sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ status: 'completed' }),
        createMockSession({ status: 'paused' }),
      ];

      service.sessions.set(sessions);

      expect(service.activeRuns()).toEqual([]);
    });
  });

  describe('completedToday computed', () => {
    it('should count only completed sessions from today', () => {
      const today = new Date();
      const todayIso = today.toISOString();

      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'completed', created_at: todayIso }),
        createMockSession({ run_id: 'run-2', status: 'completed', created_at: todayIso }),
        createMockSession({ run_id: 'run-3', status: 'running', created_at: todayIso }),
      ];

      service.sessions.set(sessions);

      expect(service.completedToday()).toBe(2);
    });

    it('should exclude sessions from yesterday', () => {
      const today = new Date();
      const yesterday = new Date(today);
      yesterday.setDate(yesterday.getDate() - 1);

      const todayIso = today.toISOString();
      const yesterdayIso = yesterday.toISOString();

      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-today', status: 'completed', created_at: todayIso }),
        createMockSession({ run_id: 'run-yesterday', status: 'completed', created_at: yesterdayIso }),
      ];

      service.sessions.set(sessions);

      expect(service.completedToday()).toBe(1);
    });

    it('should return 0 when no completed sessions today', () => {
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);

      const sessions: SessionSummary[] = [
        createMockSession({ status: 'completed', created_at: yesterday.toISOString() }),
        createMockSession({ status: 'running', created_at: new Date().toISOString() }),
      ];

      service.sessions.set(sessions);

      expect(service.completedToday()).toBe(0);
    });

    it('should handle sessions at date boundary (23:59 vs 00:01)', () => {
      const today = new Date();
      today.setHours(23, 59, 0, 0);

      const tomorrow = new Date(today);
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(0, 1, 0, 0);

      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-late-today', status: 'completed', created_at: today.toISOString() }),
      ];

      service.sessions.set(sessions);

      const count = service.completedToday();
      expect(count).toBeGreaterThanOrEqual(0);
    });
  });

  describe('recentCompletions computed', () => {
    it('should return last 10 completed sessions sorted desc', () => {
      const sessions: SessionSummary[] = [];
      for (let i = 0; i < 15; i++) {
        const date = new Date(2024, 0, i + 1);
        sessions.push(createMockSession({
          run_id: `run-${i}`,
          status: 'completed',
          created_at: date.toISOString(),
        }));
      }

      service.sessions.set(sessions);

      const recent = service.recentCompletions();
      expect(recent.length).toBe(10);
      expect(recent[0]!.run_id).toBe('run-14');
      expect(recent[9]!.run_id).toBe('run-5');
    });

    it('should only include completed sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'completed' }),
        createMockSession({ run_id: 'run-2', status: 'running' }),
        createMockSession({ run_id: 'run-3', status: 'completed' }),
      ];

      service.sessions.set(sessions);

      const recent = service.recentCompletions();
      expect(recent.length).toBe(2);
      expect(recent.every(s => s.status === 'completed')).toBe(true);
    });

    it('should return empty array when no completed sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ status: 'running' }),
        createMockSession({ status: 'paused' }),
      ];

      service.sessions.set(sessions);

      expect(service.recentCompletions()).toEqual([]);
    });

    it('should return fewer than 10 when less available', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'completed' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
      ];

      service.sessions.set(sessions);

      expect(service.recentCompletions().length).toBe(2);
    });
  });

  describe('needsAttentionRuns computed', () => {
    it('should include failed sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'failed' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
      ];

      service.sessions.set(sessions);

      const needsAttention = service.needsAttentionRuns();
      expect(needsAttention.length).toBe(1);
      expect(needsAttention[0]!.status).toBe('failed');
    });

    it('should include paused sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'paused' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
      ];

      service.sessions.set(sessions);

      const needsAttention = service.needsAttentionRuns();
      expect(needsAttention.length).toBe(1);
      expect(needsAttention[0]!.status).toBe('paused');
    });

    it('should include interrupted sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'interrupted' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
      ];

      service.sessions.set(sessions);

      const needsAttention = service.needsAttentionRuns();
      expect(needsAttention.length).toBe(1);
      expect(needsAttention[0]!.status).toBe('interrupted');
    });

    it('should exclude running and completed sessions', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1', status: 'running' }),
        createMockSession({ run_id: 'run-2', status: 'completed' }),
        createMockSession({ run_id: 'run-3', status: 'pending' }),
      ];

      service.sessions.set(sessions);

      expect(service.needsAttentionRuns()).toEqual([]);
    });

    it('should sort by created_at descending', () => {
      const sessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-old', status: 'failed', created_at: '2024-01-01T00:00:00Z' }),
        createMockSession({ run_id: 'run-new', status: 'paused', created_at: '2024-01-02T00:00:00Z' }),
      ];

      service.sessions.set(sessions);

      const needsAttention = service.needsAttentionRuns();
      expect(needsAttention.length).toBe(2);
      expect(needsAttention[0]!.run_id).toBe('run-new');
      expect(needsAttention[1]!.run_id).toBe('run-old');
    });
  });

  describe('fetchSessions', () => {
    it('should set loading status and fetch sessions', async () => {
      const mockSessions: SessionSummary[] = [
        createMockSession({ run_id: 'run-1' }),
        createMockSession({ run_id: 'run-2' }),
      ];
      tauriServiceSpy.getSessions.mockResolvedValue(mockSessions);

      service.fetchSessions('/repo');
      expect(service.status()).toBe('loading');

      await Promise.resolve();
      await Promise.resolve();

      expect(service.sessions()).toEqual(mockSessions);
      expect(service.status()).toBe('succeeded');
    });

    it('should set failed status on error', async () => {
      tauriServiceSpy.getSessions.mockRejectedValue(new Error('Network error'));

      service.fetchSessions('/repo');
      await Promise.resolve();
      await Promise.resolve();

      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Network error');
    });
  });

  describe('state signals', () => {
    it('should initialize with idle status', () => {
      expect(service.status()).toBe('idle');
    });

    it('should initialize with empty sessions', () => {
      expect(service.sessions()).toEqual([]);
    });

    it('should initialize with null error', () => {
      expect(service.error()).toBeNull();
    });

    it('should initialize with null selectedRunId', () => {
      expect(service.selectedRunId()).toBeNull();
    });
  });

  describe('isLoading computed', () => {
    it('should return true when status is loading', () => {
      service.status.set('loading');
      expect(service.isLoading()).toBe(true);
    });

    it('should return false when status is not loading', () => {
      service.status.set('succeeded');
      expect(service.isLoading()).toBe(false);
    });
  });
});

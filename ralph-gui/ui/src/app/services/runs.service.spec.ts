import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { RunsService } from './runs.service';
import { TauriService } from './tauri.service';
import type { IterationSummary, ReviewSummary, RunDetail, RunStatusSummary } from '../types';

describe('RunsService', () => {
  let service: RunsService;
  let mockTauriService: {
    getRunDetail: ReturnType<typeof vi.fn>;
    getResumableRuns: ReturnType<typeof vi.fn>;
    getRunStatus: ReturnType<typeof vi.fn>;
    notifyRunStatusChange: ReturnType<typeof vi.fn>;
    getIterationHistory: ReturnType<typeof vi.fn>;
    getReviewHistory: ReturnType<typeof vi.fn>;
  };

  const createMockRunDetail = (overrides: Partial<RunDetail> = {}): RunDetail => ({
    run_id: 'run-123',
    status: 'Running',
    current_phase: 'development',
    last_checkpoint: null,
    agent_profile: 'default',
    repo_path: '/repo',
    worktree_path: null,
    created_at: '2024-01-01T00:00:00Z',
    description: 'Test run',
    ...overrides,
  });

  const createMockStatusSummary = (overrides: Partial<RunStatusSummary> = {}): RunStatusSummary => ({
    status: 'Running',
    run_id: 'run-123',
    current_phase: 'development',
    last_checkpoint: null,
    ...overrides,
  });

  beforeEach(() => {
    mockTauriService = {
      getRunDetail: vi.fn(),
      getResumableRuns: vi.fn(),
      getRunStatus: vi.fn(),
      notifyRunStatusChange: vi.fn(),
      getIterationHistory: vi.fn(),
      getReviewHistory: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        { provide: TauriService, useValue: mockTauriService },
      ],
    });
    service = TestBed.inject(RunsService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('fetchRunDetail', () => {
    it('should fetch run detail and update signal', async () => {
      const mockDetail = createMockRunDetail();
      mockTauriService.getRunDetail.mockResolvedValue(mockDetail);
      mockTauriService.getIterationHistory.mockResolvedValue([]);
      mockTauriService.getReviewHistory.mockResolvedValue([]);
      await service.fetchRunDetail('run-123');

      expect(mockTauriService.getRunDetail).toHaveBeenCalledWith('run-123');
      expect(service.runDetail()).toEqual(mockDetail);
      expect(service.status()).toBe('succeeded');
    });

    it('should handle fetch error', async () => {
      mockTauriService.getRunDetail.mockRejectedValue(new Error('Failed to fetch'));
      await service.fetchRunDetail('run-123');

      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Failed to fetch');
    });

    it('should also fetch iteration and review history on success', async () => {
      const mockDetail = createMockRunDetail();
      const mockIterations: IterationSummary[] = [
        { iteration_number: 1, status: 'Complete', duration_secs: 120, files_changed: 3, tests_passed: 5, tests_total: 5 },
      ];
      const mockReviews: ReviewSummary[] = [
        { review_number: 1, status: 'Complete', duration_secs: 45, findings_count: 2 },
      ];
      mockTauriService.getRunDetail.mockResolvedValue(mockDetail);
      mockTauriService.getIterationHistory.mockResolvedValue(mockIterations);
      mockTauriService.getReviewHistory.mockResolvedValue(mockReviews);

      await service.fetchRunDetail('run-123');
      await Promise.resolve();

      expect(mockTauriService.getIterationHistory).toHaveBeenCalledWith('run-123');
      expect(mockTauriService.getReviewHistory).toHaveBeenCalledWith('run-123');
    });
  });

  describe('fetchIterationHistory', () => {
    it('should update iterationHistory signal on success', async () => {
      const mockIterations: IterationSummary[] = [
        { iteration_number: 1, status: 'Complete', duration_secs: 60, files_changed: 2, tests_passed: 3, tests_total: 3 },
        { iteration_number: 2, status: 'Running', duration_secs: null, files_changed: 0, tests_passed: null, tests_total: null },
      ];
      mockTauriService.getIterationHistory.mockResolvedValue(mockIterations);

      await service.fetchIterationHistory('run-abc');

      expect(service.iterationHistory()).toEqual(mockIterations);
    });

    it('should set iterationHistory to empty array on error', async () => {
      mockTauriService.getIterationHistory.mockRejectedValue(new Error('Not found'));
      service.iterationHistory.set([
        { iteration_number: 1, status: 'Complete', duration_secs: null, files_changed: 0, tests_passed: null, tests_total: null },
      ]);

      await service.fetchIterationHistory('run-abc');

      expect(service.iterationHistory()).toEqual([]);
    });
  });

  describe('fetchReviewHistory', () => {
    it('should update reviewHistory signal on success', async () => {
      const mockReviews: ReviewSummary[] = [
        { review_number: 1, status: 'Complete', duration_secs: 30, findings_count: 1 },
      ];
      mockTauriService.getReviewHistory.mockResolvedValue(mockReviews);

      await service.fetchReviewHistory('run-abc');

      expect(service.reviewHistory()).toEqual(mockReviews);
    });

    it('should set reviewHistory to empty array on error', async () => {
      mockTauriService.getReviewHistory.mockRejectedValue(new Error('Not found'));
      service.reviewHistory.set([
        { review_number: 1, status: 'Complete', duration_secs: null, findings_count: 0 },
      ]);

      await service.fetchReviewHistory('run-abc');

      expect(service.reviewHistory()).toEqual([]);
    });
  });

  describe('fetchResumableRuns', () => {
    it('should fetch resumable runs', async () => {
      const mockRuns = [createMockRunDetail({ status: 'Paused' })];
      mockTauriService.getResumableRuns.mockResolvedValue(mockRuns);
      await service.fetchResumableRuns('/repo');

      expect(mockTauriService.getResumableRuns).toHaveBeenCalledWith('/repo');
      expect(service.resumableRuns()).toEqual(mockRuns);
    });
  });

  describe('pollRunStatus', () => {
    it('should poll run status and update signal', async () => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.mockResolvedValue(mockSummary);
      await service.pollRunStatus('/repo', null);

      expect(mockTauriService.getRunStatus).toHaveBeenCalledWith('/repo', null);
      expect(service.pollingStatus()).toEqual(mockSummary);
    });

    it('should notify on transition from Running to Paused', async () => {
      const mockSummary = createMockStatusSummary({ status: 'Paused' });
      mockTauriService.getRunStatus.mockResolvedValue(mockSummary);
      service.runDetail.set(createMockRunDetail());
      (service as unknown as { previousPollingStatus: string }).previousPollingStatus = 'Running';
      await service.pollRunStatus('/repo', null);

      expect(mockTauriService.notifyRunStatusChange).toHaveBeenCalledWith('Paused', 'run-123', '/repo');
    });
  });

  describe('startPolling/stopPolling', () => {
    it('should start polling interval', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.mockResolvedValue(mockSummary);
      service.startPolling('/repo', null);
      tick(6000);

      expect(mockTauriService.getRunStatus).toHaveBeenCalled();
    }));

    it('should not start polling twice (double-start guard)', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.mockResolvedValue(mockSummary);
      service.startPolling('/repo', null);
      service.startPolling('/repo', null);
      tick(6000);

      expect(mockTauriService.getRunStatus).toHaveBeenCalledTimes(1);
    }));

    it('should stop polling', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.mockResolvedValue(mockSummary);
      service.startPolling('/repo', null);
      tick(5000);
      service.stopPolling();
      tick(10000);

      expect(mockTauriService.getRunStatus).toHaveBeenCalledTimes(1);
    }));
  });

  describe('clearRunDetail', () => {
    it('should clear run detail', () => {
      service.runDetail.set(createMockRunDetail());
      service.clearRunDetail();

      expect(service.runDetail()).toBeNull();
    });
  });
});

import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { RunsService } from './runs.service';
import { TauriService } from './tauri.service';
import type { RunDetail, RunStatusSummary } from '../types';

describe('RunsService', () => {
  let service: RunsService;
  let mockTauriService: jasmine.SpyObj<TauriService>;

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
    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      ['getRunDetail', 'getResumableRuns', 'getRunStatus', 'notifyRunStatusChange'],
    );

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
      mockTauriService.getRunDetail.and.resolveTo(mockDetail);
      await service.fetchRunDetail('run-123');

      expect(mockTauriService.getRunDetail).toHaveBeenCalledWith('run-123');
      expect(service.runDetail()).toEqual(mockDetail);
      expect(service.status()).toBe('succeeded');
    });

    it('should handle fetch error', async () => {
      mockTauriService.getRunDetail.and.rejectWith(new Error('Failed to fetch'));
      await service.fetchRunDetail('run-123');

      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Failed to fetch');
    });
  });

  describe('fetchResumableRuns', () => {
    it('should fetch resumable runs', async () => {
      const mockRuns = [createMockRunDetail({ status: 'Paused' })];
      mockTauriService.getResumableRuns.and.resolveTo(mockRuns);
      await service.fetchResumableRuns('/repo');

      expect(mockTauriService.getResumableRuns).toHaveBeenCalledWith('/repo');
      expect(service.resumableRuns()).toEqual(mockRuns);
    });
  });

  describe('pollRunStatus', () => {
    it('should poll run status and update signal', async () => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.and.resolveTo(mockSummary);
      await service.pollRunStatus('/repo', null);

      expect(mockTauriService.getRunStatus).toHaveBeenCalledWith('/repo', null);
      expect(service.pollingStatus()).toEqual(mockSummary);
    });

    it('should notify on transition from Running to Paused', async () => {
      const mockSummary = createMockStatusSummary({ status: 'Paused' });
      mockTauriService.getRunStatus.and.resolveTo(mockSummary);
      service.runDetail.set(createMockRunDetail());
      (service as unknown as { previousPollingStatus: string }).previousPollingStatus = 'Running';
      await service.pollRunStatus('/repo', null);

      expect(mockTauriService.notifyRunStatusChange).toHaveBeenCalledWith('Paused', 'run-123', '/repo');
    });
  });

  describe('startPolling/stopPolling', () => {
    it('should start polling interval', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.and.resolveTo(mockSummary);
      service.startPolling('/repo', null);
      tick(6000);

      expect(mockTauriService.getRunStatus).toHaveBeenCalled();
    }));

    it('should not start polling twice (double-start guard)', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.and.resolveTo(mockSummary);
      service.startPolling('/repo', null);
      service.startPolling('/repo', null);
      tick(6000);

      expect(mockTauriService.getRunStatus).toHaveBeenCalledTimes(1);
    }));

    it('should stop polling', fakeAsync(() => {
      const mockSummary = createMockStatusSummary();
      mockTauriService.getRunStatus.and.resolveTo(mockSummary);
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

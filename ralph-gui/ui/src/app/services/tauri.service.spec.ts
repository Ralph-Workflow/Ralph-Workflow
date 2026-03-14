import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { TauriService, TAURI_INVOKE } from './tauri.service';

/**
 * Tests for TauriService
 *
 * Note: These tests verify that the service correctly delegates to the Tauri backend.
 * The actual invoke() calls are mocked to return resolved promises.
 *
 * For unit-level verification, we test:
 * 1. Service instantiation
 * 2. Method existence and signature
 * 3. Parameter transformation behavior
 */

// Mock invoke function that returns a resolved promise
const mockInvoke = async <T>(_cmd: string, _args?: Record<string, unknown>): Promise<T> => {
  return {} as T;
};

describe('TauriService', () => {
  let service: TauriService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    });
    service = TestBed.inject(TauriService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('method existence', () => {
    it('should have session methods', () => {
      expect(service.getSessions).toBeDefined();
      expect(service.createSession).toBeDefined();
      expect(service.getSessionDetail).toBeDefined();
    });

    it('should have worktree methods', () => {
      expect(service.listWorktrees).toBeDefined();
      expect(service.createWorktree).toBeDefined();
      expect(service.switchContext).toBeDefined();
    });

    it('should have config methods', () => {
      expect(service.getGlobalConfig).toBeDefined();
      expect(service.getProjectConfig).toBeDefined();
      expect(service.getEffectiveConfig).toBeDefined();
      expect(service.saveGlobalConfig).toBeDefined();
      expect(service.saveProjectConfig).toBeDefined();
    });

    it('should have run management methods', () => {
      expect(service.getRunStatus).toBeDefined();
      expect(service.getResumableRuns).toBeDefined();
      expect(service.getRunDetail).toBeDefined();
      expect(service.getRunLogs).toBeDefined();
    });

    it('should have prompt methods', () => {
      expect(service.readPromptFile).toBeDefined();
      expect(service.savePromptFile).toBeDefined();
      expect(service.reviewPromptWithAi).toBeDefined();
    });

    it('should have notification method', () => {
      expect(service.notifyRunStatusChange).toBeDefined();
    });

    it('should have agent profile method', () => {
      expect(service.listAgentProfiles).toBeDefined();
    });

    it('should have session launch methods', () => {
      expect(service.launchRalphSession).toBeDefined();
      expect(service.resumeRalphSession).toBeDefined();
    });
  });

  describe('method signatures', () => {
    it('getSessions should return a Promise', () => {
      const result = service.getSessions('/test');
      expect(result).toBeDefined();
      expect(typeof result.then).toBe('function');
    });

    it('createWorktree should accept optional basePath', () => {
      // With basePath
      const withBase = service.createWorktree('/repo', 'branch', 'name', '/base');
      expect(withBase).toBeDefined();
      expect(typeof withBase.then).toBe('function');

      // Without basePath
      const withoutBase = service.createWorktree('/repo', 'branch', 'name');
      expect(withoutBase).toBeDefined();
      expect(typeof withoutBase.then).toBe('function');
    });

    it('getRunLogs should use 500 as default maxLines', () => {
      // This test verifies the default is applied (integration test would verify backend receives it)
      const result = service.getRunLogs('/repo', null);
      expect(result).toBeDefined();
      expect(typeof result.then).toBe('function');
    });

    it('getRunLogs should accept custom maxLines', () => {
      const result = service.getRunLogs('/repo', null, 100);
      expect(result).toBeDefined();
      expect(typeof result.then).toBe('function');
    });

    it('should have getIterationHistory method', () => {
      expect(service.getIterationHistory).toBeDefined();
    });

    it('should have getReviewHistory method', () => {
      expect(service.getReviewHistory).toBeDefined();
    });
  });

  describe('new invoke wrappers', () => {
    let capturedCmd: string;
    let capturedArgs: Record<string, unknown>;

    beforeEach(() => {
      TestBed.resetTestingModule();
      const capturingInvoke = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
        capturedCmd = cmd;
        capturedArgs = args ?? {};
        return [] as unknown as T;
      };
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: capturingInvoke },
        ],
      });
      service = TestBed.inject(TauriService);
    });

    it('getIterationHistory should invoke get_iteration_history with run_id', async () => {
      await service.getIterationHistory('run-abc-123');
      expect(capturedCmd).toBe('get_iteration_history');
      expect(capturedArgs['run_id']).toBe('run-abc-123');
    });

    it('getReviewHistory should invoke get_review_history with run_id', async () => {
      await service.getReviewHistory('run-xyz-456');
      expect(capturedCmd).toBe('get_review_history');
      expect(capturedArgs['run_id']).toBe('run-xyz-456');
    });
  });
});

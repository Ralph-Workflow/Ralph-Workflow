import { describe, it, expect, beforeEach, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection, signal, Signal } from '@angular/core';
import { provideRouter } from '@angular/router';
import { ActivatedRoute } from '@angular/router';
import { RunDetailComponent, DetailTab } from './run-detail.component';
import { RunsService } from '../../services/runs.service';
import { TAURI_INVOKE } from '../../services/tauri.service';
import { LISTEN_TOKEN } from '../../components/run-log/run-log.component';
import type { IterationSummary, ReviewSummary, RunDetail } from '../../types';

const MOCK_COMPLETED_RUN: RunDetail = {
  run_id: 'run-completed-1',
  status: 'Completed',
  current_phase: 'commit',
  agent_profile: 'claude-code',
  repo_path: '/repo',
  worktree_path: '/repo/wt-42-auth',
  created_at: '2024-01-01T00:00:00Z',
  last_checkpoint: '2024-01-01T01:00:00Z',
  iteration_count: 3,
  last_error: null,
  description: 'Add user authentication',
  is_degraded: false,
  total_duration_secs: 1530,
};

const MOCK_FAILED_RUN: RunDetail = {
  run_id: 'run-failed-1',
  status: 'Failed',
  current_phase: 'develop',
  agent_profile: 'claude-code',
  repo_path: '/repo',
  worktree_path: null,
  created_at: '2024-01-01T00:00:00Z',
  last_checkpoint: null,
  iteration_count: 1,
  last_error: 'Build failed: syntax error',
  description: 'Test failed run',
  is_degraded: false,
};

const MOCK_PAUSED_RUN: RunDetail = {
  run_id: 'run-paused-1',
  status: 'Paused',
  current_phase: 'review',
  agent_profile: 'claude-code',
  repo_path: '/repo',
  worktree_path: null,
  created_at: '2024-01-01T00:00:00Z',
  last_checkpoint: '2024-01-01T00:30:00Z',
  iteration_count: 2,
  last_error: null,
  description: 'Test paused run',
  is_degraded: false,
};

const MOCK_RUNNING_RUN: RunDetail = {
  run_id: 'run-running-1',
  status: 'Running',
  current_phase: 'develop',
  agent_profile: 'claude-code',
  repo_path: '/repo',
  worktree_path: null,
  created_at: '2024-01-01T00:00:00Z',
  last_checkpoint: null,
  iteration_count: 1,
  last_error: null,
  description: 'Test running run',
  is_degraded: false,
};

describe('RunDetailComponent', () => {
  let tauriInvokeSpy: ReturnType<typeof vi.fn>;

  function createRunsServiceMock(
    runDetailValue: RunDetail | null,
    iterationHistory: IterationSummary[] = [],
    reviewHistory: ReviewSummary[] = [],
  ): {
    runDetail: Signal<RunDetail | null>;
    status: Signal<'idle' | 'loading' | 'succeeded' | 'failed'>;
    error: Signal<string | null>;
    pollingStatus: Signal<boolean>;
    iterationHistory: Signal<IterationSummary[]>;
    reviewHistory: Signal<ReviewSummary[]>;
    fetchRunDetail: ReturnType<typeof vi.fn>;
    startPolling: ReturnType<typeof vi.fn>;
    stopPolling: ReturnType<typeof vi.fn>;
    clearRunDetail: ReturnType<typeof vi.fn>;
  } {
    return {
      runDetail: signal<RunDetail | null>(runDetailValue).asReadonly(),
      status: signal<'idle' | 'loading' | 'succeeded' | 'failed'>('succeeded').asReadonly(),
      error: signal<string | null>(null).asReadonly(),
      pollingStatus: signal<boolean>(false).asReadonly(),
      iterationHistory: signal<IterationSummary[]>(iterationHistory).asReadonly(),
      reviewHistory: signal<ReviewSummary[]>(reviewHistory).asReadonly(),
      fetchRunDetail: vi.fn().mockResolvedValue(undefined),
      startPolling: vi.fn(),
      stopPolling: vi.fn(),
      clearRunDetail: vi.fn(),
    };
  }

  async function createComponent(
    run: RunDetail | null,
    iterations: IterationSummary[] = [],
    reviews: ReviewSummary[] = [],
  ) {
    const runsServiceMock = createRunsServiceMock(run, iterations, reviews);

    TestBed.configureTestingModule({
      imports: [RunDetailComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        { provide: RunsService, useValue: runsServiceMock },
        { provide: TAURI_INVOKE, useValue: tauriInvokeSpy },
        {
          provide: LISTEN_TOKEN,
          useValue: vi.fn().mockResolvedValue(vi.fn()),
        },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: { get: () => run?.run_id ?? null } },
          },
        },
      ],
    });

    const fixture = TestBed.createComponent(RunDetailComponent);
    await fixture.whenStable();
    return { fixture, component: fixture.componentInstance };
  }

  beforeEach(() => {
    tauriInvokeSpy = vi.fn().mockResolvedValue([]);
    TestBed.resetTestingModule();
  });

  describe('state-specific banners', () => {
    it('should show completed banner for Completed status', async () => {
      const { fixture } = await createComponent(MOCK_COMPLETED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="completed-banner"]')).toBeTruthy();
    });

    it('should show failed banner for Failed status', async () => {
      const { fixture } = await createComponent(MOCK_FAILED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="failed-banner"]')).toBeTruthy();
    });

    it('should show paused banner for Paused status', async () => {
      const { fixture } = await createComponent(MOCK_PAUSED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="paused-banner"]')).toBeTruthy();
    });

    it('should NOT show state banners for Running status', async () => {
      const { fixture } = await createComponent(MOCK_RUNNING_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="completed-banner"]')).toBeNull();
      expect(el.querySelector('[data-testid="failed-banner"]')).toBeNull();
      expect(el.querySelector('[data-testid="paused-banner"]')).toBeNull();
    });
  });

  describe('tab bar default selection', () => {
    it('should default to Changes tab for Completed run', async () => {
      const { component } = await createComponent(MOCK_COMPLETED_RUN);
      expect(component.activeTab()).toBe('changes' satisfies DetailTab);
    });

    it('should default to Log tab for Failed run', async () => {
      const { component } = await createComponent(MOCK_FAILED_RUN);
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    });

    it('should default to Log tab for Paused run', async () => {
      const { component } = await createComponent(MOCK_PAUSED_RUN);
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    });

    it('should default to Log tab for Running run', async () => {
      const { component } = await createComponent(MOCK_RUNNING_RUN);
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    });
  });

  describe('tab switching', () => {
    it('should switch to Log tab when setTab is called with "log"', async () => {
      const { fixture, component } = await createComponent(MOCK_COMPLETED_RUN);

      component.setTab('log');
      await fixture.whenStable();

      expect(component.activeTab()).toBe('log');
    });

    it('should switch to Changes tab when setTab is called with "changes"', async () => {
      const { fixture, component } = await createComponent(MOCK_COMPLETED_RUN);

      component.setTab('changes');
      await fixture.whenStable();

      expect(component.activeTab()).toBe('changes');
    });

    it('should switch to Info tab when setTab is called with "info"', async () => {
      const { fixture, component } = await createComponent(MOCK_COMPLETED_RUN);

      component.setTab('info');
      await fixture.whenStable();

      expect(component.activeTab()).toBe('info');
    });
  });

  describe('page header', () => {
    it('should show worktree name in title when worktree_path is set', async () => {
      const { fixture } = await createComponent(MOCK_COMPLETED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      const title = el.querySelector('.run-header__title');
      expect(title?.textContent).toContain('wt-42-auth');
    });

    it('should show description in subtitle when present', async () => {
      const { fixture } = await createComponent(MOCK_COMPLETED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      const subtitle = el.querySelector('.run-header__subtitle');
      expect(subtitle?.textContent).toContain('Add user authentication');
    });
  });

  describe('failed state recovery', () => {
    it('should show resume action button in failed state', async () => {
      const { fixture } = await createComponent(MOCK_FAILED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="resume-action-btn"]')).toBeTruthy();
    });

    it('should show retry action button in failed state', async () => {
      const { fixture } = await createComponent(MOCK_FAILED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="retry-action-btn"]')).toBeTruthy();
    });

    it('should show the error message from last_error', async () => {
      const { fixture } = await createComponent(MOCK_FAILED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="failed-error-msg"]')?.textContent).toContain('Build failed: syntax error');
    });
  });

  describe('paused state hero resume', () => {
    it('should show large paused resume button', async () => {
      const { fixture } = await createComponent(MOCK_PAUSED_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="paused-resume-btn"]')).toBeTruthy();
    });
  });

  describe('running state info', () => {
    it('should show running state info panel', async () => {
      const { fixture } = await createComponent(MOCK_RUNNING_RUN);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="running-info"]')).toBeTruthy();
    });
  });

  describe('iteration history integration', () => {
    const MOCK_ITERATIONS: IterationSummary[] = [
      { iteration_number: 1, status: 'Complete', duration_secs: 120, files_changed: 3, tests_passed: 5, tests_total: 5 },
    ];

    it('should render iteration-history section when iterations are available', async () => {
      const { fixture, component } = await createComponent(MOCK_COMPLETED_RUN, MOCK_ITERATIONS);

      component.setTab('info');
      await fixture.whenStable();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="iteration-history-section"]')).toBeTruthy();
    });

    it('should switch to changes tab filtered to iteration when iterationClick fires', async () => {
      const { fixture, component } = await createComponent(MOCK_RUNNING_RUN, MOCK_ITERATIONS);

      component.onIterationClick(1);
      await fixture.whenStable();

      expect(component.activeTab()).toBe('changes');
      expect(component.changesFilterIteration()).toBe(1);
    });
  });

  describe('review history integration', () => {
    const MOCK_REVIEWS: ReviewSummary[] = [
      { review_number: 1, status: 'Complete', duration_secs: 45, findings_count: 2 },
    ];

    it('should render review-history section when reviews are available', async () => {
      const { fixture, component } = await createComponent(MOCK_COMPLETED_RUN, [], MOCK_REVIEWS);

      component.setTab('info');
      await fixture.whenStable();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="review-history-section"]')).toBeTruthy();
    });
  });

  describe('degraded state details', () => {
    it('should show retry count when degraded_info is present', async () => {
      const degradedRun: RunDetail = {
        ...MOCK_RUNNING_RUN,
        is_degraded: true,
        degraded_info: { retry_count: 3, fallback_agent: 'backup-claude', reason: 'Timeout' },
      };
      const { fixture } = await createComponent(degradedRun);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="degraded-banner"]')?.textContent).toContain('3');
    });
  });
});

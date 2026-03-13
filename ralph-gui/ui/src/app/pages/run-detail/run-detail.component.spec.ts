import { TestBed, fakeAsync, tick } from '@angular/core/testing';
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
  worktree_path: null,
  created_at: '2024-01-01T00:00:00Z',
  last_checkpoint: '2024-01-01T01:00:00Z',
  iteration_count: 3,
  last_error: null,
  description: 'Test completed run',
  is_degraded: false,
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
  let tauriInvokeSpy: jasmine.Spy;

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
    fetchRunDetail: jasmine.Spy;
    startPolling: jasmine.Spy;
    stopPolling: jasmine.Spy;
    clearRunDetail: jasmine.Spy;
  } {
    return {
      runDetail: signal<RunDetail | null>(runDetailValue).asReadonly(),
      status: signal<'idle' | 'loading' | 'succeeded' | 'failed'>('succeeded').asReadonly(),
      error: signal<string | null>(null).asReadonly(),
      pollingStatus: signal<boolean>(false).asReadonly(),
      iterationHistory: signal<IterationSummary[]>(iterationHistory).asReadonly(),
      reviewHistory: signal<ReviewSummary[]>(reviewHistory).asReadonly(),
      fetchRunDetail: jasmine.createSpy('fetchRunDetail').and.returnValue(Promise.resolve()),
      startPolling: jasmine.createSpy('startPolling'),
      stopPolling: jasmine.createSpy('stopPolling'),
      clearRunDetail: jasmine.createSpy('clearRunDetail'),
    };
  }

  function createComponent(
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
          useValue: jasmine.createSpy('listen').and.returnValue(
            Promise.resolve(jasmine.createSpy('unlisten'))
          ),
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
    return { fixture, component: fixture.componentInstance };
  }

  beforeEach(() => {
    tauriInvokeSpy = jasmine.createSpy('invoke').and.returnValue(Promise.resolve([]));
    TestBed.resetTestingModule();
  });

  describe('state-specific banners', () => {
    it('should show completed banner for Completed status', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="completed-banner"]')).toBeTruthy();
    }));

    it('should show failed banner for Failed status', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="failed-banner"]')).toBeTruthy();
    }));

    it('should show paused banner for Paused status', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_PAUSED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="paused-banner"]')).toBeTruthy();
    }));

    it('should NOT show state banners for Running status', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="completed-banner"]')).toBeNull();
      expect(el.querySelector('[data-testid="failed-banner"]')).toBeNull();
      expect(el.querySelector('[data-testid="paused-banner"]')).toBeNull();
    }));
  });

  describe('tab bar default selection', () => {
    it('should default to Changes tab for Completed run', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      expect(component.activeTab()).toBe('changes' satisfies DetailTab);
    }));

    it('should default to Log tab for Failed run', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    }));

    it('should default to Log tab for Paused run', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_PAUSED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    }));

    it('should default to Log tab for Running run', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      expect(component.activeTab()).toBe('log' satisfies DetailTab);
    }));
  });

  describe('tab switching', () => {
    it('should render tab bar with Log, Changes, Info tabs', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="tab-log"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-changes"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="tab-info"]')).toBeTruthy();
    }));

    it('should switch to info tab when Info is clicked', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      component.setTab('info');
      fixture.detectChanges();

      expect(component.activeTab()).toBe('info');
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="tab-content-info"]')).toBeTruthy();
    }));
  });

  describe('phase timeline integration', () => {
    it('should render app-phase-timeline component', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('app-phase-timeline')).toBeTruthy();
    }));
  });

  describe('changes viewer integration', () => {
    it('should render app-changes-viewer when Changes tab is active', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      // Completed defaults to changes tab
      expect(component.activeTab()).toBe('changes');
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="tab-content-changes"]')).toBeTruthy();
      expect(el.querySelector('app-changes-viewer')).toBeTruthy();
    }));
  });

  describe('cancel confirmation dialog', () => {
    it('should not show cancel dialog initially for Running run', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      expect(component.showCancelDialog()).toBeFalse();
    }));

    it('should show cancel dialog when cancel button is clicked', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      component.handleCancel();
      fixture.detectChanges();

      expect(component.showCancelDialog()).toBeTrue();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="cancel-dialog"]')).toBeTruthy();
    }));

    it('should hide cancel dialog when onCancelConfirmed(false) is called', fakeAsync(async () => {
      const { fixture, component } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();

      component.handleCancel();
      fixture.detectChanges();
      expect(component.showCancelDialog()).toBeTrue();

      await component.onCancelConfirmed(false);
      fixture.detectChanges();
      expect(component.showCancelDialog()).toBeFalse();
    }));
  });

  describe('retry confirmation dialog', () => {
    it('should show retry dialog when retry action is triggered', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      component.handleRetry();
      fixture.detectChanges();

      expect(component.showRetryDialog()).toBeTrue();
    }));
  });

  describe('completed state metrics', () => {
    it('should show metric cards for completed run', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="metric-iterations"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="metric-reviews"]')).toBeTruthy();
      expect(el.querySelector('[data-testid="metric-files"]')).toBeTruthy();
    }));

    it('should show iteration count value in metric card', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_COMPLETED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const iterCard = el.querySelector('[data-testid="metric-iterations"]');
      expect(iterCard?.textContent).toContain('3');
    }));
  });

  describe('failed state recovery actions', () => {
    it('should show resume action button in failed state', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="resume-action-btn"]')).toBeTruthy();
    }));

    it('should show retry action button in failed state', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="retry-action-btn"]')).toBeTruthy();
    }));

    it('should show the error message from last_error', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_FAILED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="failed-error-msg"]')?.textContent).toContain('Build failed: syntax error');
    }));
  });

  describe('paused state hero resume', () => {
    it('should show large paused resume button', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_PAUSED_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="paused-resume-btn"]')).toBeTruthy();
    }));
  });

  describe('running state info', () => {
    it('should show running state info panel', fakeAsync(() => {
      const { fixture } = createComponent(MOCK_RUNNING_RUN);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="running-info"]')).toBeTruthy();
    }));
  });

  describe('iteration history integration', () => {
    const MOCK_ITERATIONS: IterationSummary[] = [
      { iteration_number: 1, status: 'Complete', duration_secs: 120, files_changed: 3, tests_passed: 5, tests_total: 5 },
    ];

    it('should render iteration-history section when iterations are available', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_COMPLETED_RUN, MOCK_ITERATIONS);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      // Switch to info tab to see iteration history
      component.setTab('info');
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="iteration-history-section"]')).toBeTruthy();
    }));

    it('should switch to changes tab filtered to iteration when iterationClick fires', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_RUNNING_RUN, MOCK_ITERATIONS);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      component.onIterationClick(1);
      fixture.detectChanges();

      expect(component.activeTab()).toBe('changes');
      expect(component.changesFilterIteration()).toBe(1);
    }));
  });

  describe('review history integration', () => {
    const MOCK_REVIEWS: ReviewSummary[] = [
      { review_number: 1, status: 'Complete', duration_secs: 45, findings_count: 2 },
    ];

    it('should render review-history section when reviews are available', fakeAsync(() => {
      const { fixture, component } = createComponent(MOCK_COMPLETED_RUN, [], MOCK_REVIEWS);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();

      component.setTab('info');
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="review-history-section"]')).toBeTruthy();
    }));
  });

  describe('degraded state details', () => {
    it('should show retry count when degraded_info is present', fakeAsync(() => {
      const degradedRun: RunDetail = {
        ...MOCK_RUNNING_RUN,
        is_degraded: true,
        degraded_info: { retry_count: 3, fallback_agent: 'backup-claude', reason: 'Timeout' },
      };
      const { fixture } = createComponent(degradedRun);
      fixture.detectChanges();
      tick();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="degraded-banner"]')?.textContent).toContain('3');
    }));
  });
});

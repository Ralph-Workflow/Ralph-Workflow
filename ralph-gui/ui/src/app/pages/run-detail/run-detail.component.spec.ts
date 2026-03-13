import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideZonelessChangeDetection, signal, Signal } from '@angular/core';
import { provideRouter } from '@angular/router';
import { ActivatedRoute } from '@angular/router';
import { RunDetailComponent, DetailTab } from './run-detail.component';
import { RunsService } from '../../services/runs.service';
import { TAURI_INVOKE } from '../../services/tauri.service';
import { LISTEN_TOKEN } from '../../components/run-log/run-log.component';
import type { RunDetail } from '../../types';

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

  function createRunsServiceMock(runDetailValue: RunDetail | null): {
    runDetail: Signal<RunDetail | null>;
    status: Signal<'idle' | 'loading' | 'succeeded' | 'failed'>;
    error: Signal<string | null>;
    pollingStatus: Signal<boolean>;
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
      fetchRunDetail: jasmine.createSpy('fetchRunDetail').and.returnValue(Promise.resolve()),
      startPolling: jasmine.createSpy('startPolling'),
      stopPolling: jasmine.createSpy('stopPolling'),
      clearRunDetail: jasmine.createSpy('clearRunDetail'),
    };
  }

  function createComponent(run: RunDetail | null) {
    const runsServiceMock = createRunsServiceMock(run);

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
});

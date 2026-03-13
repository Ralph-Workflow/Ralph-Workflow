import {
  Component,
  inject,
  signal,
  effect,
  computed,
  input,
  ChangeDetectionStrategy,
  forwardRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { RunsService } from '../../services/runs.service';
import { TauriService } from '../../services/tauri.service';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { RunLogComponent } from '../../components/run-log/run-log.component';
import { ChangesViewerComponent } from '../../components/changes-viewer/changes-viewer.component';
import { PhaseTimelineComponent, PhaseInfo } from '../../components/phase-timeline/phase-timeline.component';

export type DetailTab = 'log' | 'changes' | 'info';

@Component({
  selector: 'app-run-detail',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    RunStatusBadgeComponent,
    RunLogComponent,
    ChangesViewerComponent,
    PhaseTimelineComponent,
    forwardRef(() => DetailRowComponent),
  ],
  templateUrl: './run-detail.component.html',
  styleUrl: './run-detail.component.css',
})
export class RunDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly runsService = inject(RunsService);
  private readonly tauri = inject(TauriService);

  // Computed from service
  readonly runDetail = this.runsService.runDetail;
  readonly isLoading = computed(() => this.runsService.status() === 'loading');
  readonly error = this.runsService.error;

  // Tab state — default depends on run status
  readonly activeTab = signal<DetailTab>('log');

  readonly canResume = computed(() => {
    const detail = this.runDetail();
    return detail && (detail.status === 'Paused' || detail.status === 'Failed');
  });

  readonly iterationCount = computed(() => {
    return String(this.runDetail()?.iteration_count ?? 0);
  });

  /** Convert RunDetail phases to PhaseInfo[] for the PhaseTimelineComponent. */
  readonly timelinePhases = computed((): PhaseInfo[] => {
    const detail = this.runDetail();
    if (!detail) return [];

    const phases = ['Plan', 'Develop', 'Review', 'Commit'];
    const currentPhase = detail.current_phase.toLowerCase();
    const isDone = ['commit', 'done', 'completed'].some(p => currentPhase.includes(p));
    const phaseOrder = ['plan', 'develop', 'review', 'commit'];
    const currentIdx = phaseOrder.indexOf(currentPhase.split('_')[0] ?? '');

    return phases.map((name, idx) => {
      let status: PhaseInfo['status'];
      if (isDone || idx < currentIdx) {
        status = 'completed';
      } else if (idx === currentIdx && !isDone) {
        status = detail.status === 'Failed' ? 'failed' : 'active';
      } else {
        status = 'pending';
      }
      return { name, status };
    });
  });

  constructor() {
    // Fetch run detail on route param change
    effect(() => {
      const runId = this.route.snapshot.paramMap.get('runId');
      if (runId) {
        void this.runsService.fetchRunDetail(runId);
      }
    });

    // Start polling when run is running
    effect((onCleanup) => {
      const detail = this.runDetail();
      if (detail?.status === 'Running' && detail.repo_path) {
        this.runsService.startPolling(detail.repo_path, detail.worktree_path);
        onCleanup(() => this.runsService.stopPolling());
      }
    });

    // Set default tab based on run status when detail loads
    effect(() => {
      const detail = this.runDetail();
      if (!detail) return;
      switch (detail.status) {
        case 'Completed':
          this.activeTab.set('changes');
          break;
        case 'Failed':
        case 'Paused':
        case 'Running':
          this.activeTab.set('log');
          break;
        default:
          this.activeTab.set('log');
      }
    });

    // Refresh detail when polling status changes
    effect(() => {
      const pollingStatus = this.runsService.pollingStatus();
      const runId = this.route.snapshot.paramMap.get('runId');
      if (pollingStatus && runId) {
        void this.runsService.fetchRunDetail(runId);
      }
    });

    // Cleanup on destroy
    effect((onCleanup) => {
      onCleanup(() => {
        this.runsService.clearRunDetail();
        this.runsService.stopPolling();
      });
    });
  }

  goBack(): void {
    void this.router.navigate(['/sessions']);
  }

  setTab(tab: DetailTab): void {
    this.activeTab.set(tab);
  }

  async handleResume(): Promise<void> {
    const detail = this.runDetail();
    if (!detail) return;

    try {
      await this.tauri.resumeRalphSession(detail.run_id, detail.repo_path);
      void this.router.navigate(['/sessions']);
    } catch (e) {
      console.error('Failed to resume session:', e);
    }
  }

  async handleCancel(): Promise<void> {
    const detail = this.runDetail();
    if (!detail) return;

    const confirmed = window.confirm('Cancel this run? Progress up to the last checkpoint will be saved.');
    if (!confirmed) return;

    try {
      await this.tauri.cancelRun(detail.repo_path, detail.worktree_path);
      void this.router.navigate(['/sessions']);
    } catch (e) {
      console.error('Failed to cancel run:', e);
    }
  }

  async handleRetry(): Promise<void> {
    const detail = this.runDetail();
    if (!detail) return;

    const confirmed = window.confirm('Retry this run from the beginning?');
    if (!confirmed) return;

    try {
      await this.tauri.resumeRalphSession(detail.run_id, detail.repo_path);
      void this.router.navigate(['/sessions']);
    } catch (e) {
      console.error('Failed to retry session:', e);
    }
  }

  onPhaseClick(_phase: PhaseInfo): void {
    // Navigate to log tab when a completed phase is clicked (future: filter by phase)
    this.activeTab.set('log');
  }

  get isLoadingValue(): boolean { return this.isLoading(); }
  get errorValue(): string | null { return this.error(); }
  get runDetailValue() { return this.runDetail(); }
  get canResumeValue() { return this.canResume(); }
  get activeTabValue(): DetailTab { return this.activeTab(); }
  get timelinePhasesValue(): PhaseInfo[] { return this.timelinePhases(); }
  get iterationCountValue(): string { return this.iterationCount(); }
}

@Component({
  selector: 'app-detail-row',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './detail-row.component.html',
})
export class DetailRowComponent {
  readonly label = input<string>('');
  readonly value = input<string | null>(null);
  readonly mono = input<boolean>(false);

  get labelValue(): string { return this.label(); }
  get valueValue(): string | null { return this.value(); }

  get valueStyle(): string {
    const isMono = this.mono();
    return `
      flex: 1;
      font-size: ${isMono ? 12 : 13}px;
      color: ${this.value() ? 'var(--text-primary)' : 'var(--text-muted)'};
      font-family: ${isMono ? 'var(--font-mono)' : 'var(--font-ui)'};
      word-break: break-all;
    `.replace(/\n/g, ' ');
  }
}

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
import { RunLogComponent } from '../../components/run-log/run-log.component';
import { ChangesViewerComponent } from '../../components/changes-viewer/changes-viewer.component';
import { PhaseTimelineComponent, PhaseInfo } from '../../components/phase-timeline/phase-timeline.component';
import { IterationHistoryComponent } from '../../components/iteration-history/iteration-history.component';
import { ReviewHistoryComponent } from '../../components/review-history/review-history.component';
import { CancelConfirmationComponent } from '../../components/cancel-confirmation/cancel-confirmation.component';
import { formatDuration } from '../../pipes/format-duration.pipe';

export type DetailTab = 'log' | 'changes' | 'info';

@Component({
  selector: 'app-run-detail',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    RunLogComponent,
    ChangesViewerComponent,
    PhaseTimelineComponent,
    IterationHistoryComponent,
    ReviewHistoryComponent,
    CancelConfirmationComponent,
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
  readonly iterationHistory = this.runsService.iterationHistory;
  readonly reviewHistory = this.runsService.reviewHistory;

  // Tab state — default depends on run status
  readonly activeTab = signal<DetailTab>('log');

  // Cancel confirmation dialog state
  readonly showCancelDialog = signal(false);
  readonly showRetryDialog = signal(false);

  // Iteration filter for changes tab (set when iterationClick is received)
  readonly changesFilterIteration = signal<number | null>(null);

 readonly canResume = computed(() => {
    const detail = this.runDetail();
    return detail && (detail.status === 'Paused' || detail.status === 'Failed');
  });

  readonly worktreeName = computed(() => {
    const detail = this.runDetail();
    if (!detail) return '';
    const path = detail.worktree_path;
    if (path) {
      const parts = path.split('/');
      return parts[parts.length - 1] ?? '';
    }
    return detail.description ?? '';
  });

  readonly failedPhaseLabel = computed(() => {
    const detail = this.runDetail();
    if (!detail) return '';
    const phase = detail.current_phase.toLowerCase();
    const phaseNames = ['plan', 'develop', 'review', 'commit'];
    const matchedPhase = phaseNames.find(p => phase.includes(p));
    return matchedPhase ? matchedPhase.charAt(0).toUpperCase() + matchedPhase.slice(1) : '';
  });

  readonly formattedTotalDuration = computed(() => {
    const detail = this.runDetail();
    if (detail?.total_duration_secs == null || detail?.total_duration_secs === undefined) {
      return '';
    }
    return formatDuration(detail.total_duration_secs);
  });

  readonly pausedAgo = computed(() => {
    const detail = this.runDetail();
    if (!detail?.last_checkpoint) return '';
    try {
      const checkpointTime = new Date(detail.last_checkpoint);
      const now = new Date();
      const diffMs = now.getTime() - checkpointTime.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 60) {
        return `${diffMins}m ago`;
      }
      const diffHours = Math.floor(diffMins / 60);
      const remainingMins = diffMins % 60;
      return `${diffHours}h ${remainingMins}m ago`;
    } catch {
      return '';
    }
  });

  readonly formattedPhaseDurations = computed(() => {
    const detail = this.runDetail();
    if (!detail?.phase_durations) return new Map();
    return new Map(
      detail.phase_durations.map(pd => [pd.phase_name.toLowerCase(), pd])
    );
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
    const phaseDurations = this.formattedPhaseDurations();

    return phases.map((name, idx) => {
      let status: PhaseInfo['status'];
      let duration: string | undefined;
      let statusLabel: string | undefined;

      if (isDone || idx < currentIdx) {
        status = 'completed';
      } else if (idx === currentIdx && !isDone) {
        status = detail.status === 'Failed' ? 'failed' : 'active';
      } else {
        status = 'pending';
      }

      // Add duration for completed and active phases
      if (status === 'completed' || status === 'active' || status === 'failed') {
        const phaseKey = name.toLowerCase();
        const durationData = phaseDurations.get(phaseKey);
        if (durationData?.duration_secs != null && durationData.duration_secs !== undefined) {
          duration = formatDuration(durationData.duration_secs);
        }
      }

      // Add status label based on phase status
      if (status === 'active') {
        statusLabel = 'Running now';
      } else if (status === 'pending') {
        statusLabel = 'Waiting';
      } else if (status === 'failed') {
        statusLabel = 'Failed';
      }

      return { name, status, duration, statusLabel };
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

  handleCancel(): void {
    this.showCancelDialog.set(true);
  }

  async onCancelConfirmed(confirmed: boolean): Promise<void> {
    this.showCancelDialog.set(false);
    if (!confirmed) return;

    const detail = this.runDetail();
    if (!detail) return;

    try {
      await this.tauri.cancelRun(detail.repo_path, detail.worktree_path);
      void this.router.navigate(['/sessions']);
    } catch (e) {
      console.error('Failed to cancel run:', e);
    }
  }

  handleRetry(): void {
    this.showRetryDialog.set(true);
  }

  async onRetryConfirmed(confirmed: boolean): Promise<void> {
    this.showRetryDialog.set(false);
    if (!confirmed) return;

    const detail = this.runDetail();
    if (!detail) return;

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

  onIterationClick(iterationNumber: number): void {
    // Switch to changes tab filtered to this iteration
    this.changesFilterIteration.set(iterationNumber);
    this.activeTab.set('changes');
  }

  goToConfiguration(): void {
    void this.router.navigate(['/configuration']);
  }

  get isLoadingValue(): boolean { return this.isLoading(); }
  get errorValue(): string | null { return this.error(); }
  get runDetailValue() { return this.runDetail(); }
  get canResumeValue() { return this.canResume(); }
  get activeTabValue(): DetailTab { return this.activeTab(); }
  get timelinePhasesValue(): PhaseInfo[] { return this.timelinePhases(); }
  get iterationCountValue(): string { return this.iterationCount(); }
  get iterationHistoryValue() { return this.iterationHistory(); }
  get reviewHistoryValue() { return this.reviewHistory(); }
  get showCancelDialogValue(): boolean { return this.showCancelDialog(); }
  get showRetryDialogValue(): boolean { return this.showRetryDialog(); }
  get changesFilterIterationValue(): number | null { return this.changesFilterIteration(); }
}

@Component({
  selector: 'app-detail-row',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './detail-row.component.html',
  styleUrl: './detail-row.component.css',
})
export class DetailRowComponent {
  readonly label = input<string>('');
  readonly value = input<string | null>(null);
  readonly mono = input<boolean>(false);

  get labelValue(): string { return this.label(); }
  get valueValue(): string | null { return this.value(); }
}

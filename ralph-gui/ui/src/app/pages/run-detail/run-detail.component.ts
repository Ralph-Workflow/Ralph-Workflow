import { Component, inject, signal, effect, computed, input, ChangeDetectionStrategy, forwardRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { RunsService } from '../../services/runs.service';
import { TauriService } from '../../services/tauri.service';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { RunLogComponent } from '../../components/run-log/run-log.component';
// Types are used in the template binding

@Component({
  selector: 'app-run-detail',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, RunStatusBadgeComponent, RunLogComponent, forwardRef(() => DetailRowComponent)],
  templateUrl: './run-detail.component.html',
  styleUrl: './run-detail.component.css',
})
export class RunDetailComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly runsService = inject(RunsService);
  // SessionsService available for future use
  private readonly tauri = inject(TauriService);

  readonly phases = ['plan', 'develop', 'review', 'commit'] as const;

  // Local UI state
  readonly logLines = signal<string[]>([]);
  readonly logLoading = signal(false);
  readonly logExpanded = signal(false);
  readonly logFetched = signal(false);

  // Computed from service
  readonly runDetail = this.runsService.runDetail;
  readonly isLoading = computed(() => this.runsService.status() === 'loading');
  readonly error = this.runsService.error;

  readonly canResume = computed(() => {
    const detail = this.runDetail();
    return detail && (detail.status === 'Paused' || detail.status === 'Failed');
  });

  readonly iterationCount = computed(() => {
    return String(this.runDetail()?.iteration_count ?? 0);
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

  async fetchLogs(): Promise<void> {
    const detail = this.runDetail();
    if (!detail?.repo_path) return;

    this.logLoading.set(true);
    try {
      const lines = await this.tauri.getRunLogs(detail.repo_path, detail.worktree_path);
      this.logLines.set(lines);
      this.logFetched.set(true);
    } catch {
      this.logLines.set([]);
    } finally {
      this.logLoading.set(false);
    }
  }

  toggleLogs(): void {
    const nextExpanded = !this.logExpanded();
    this.logExpanded.set(nextExpanded);
    if (nextExpanded && !this.logFetched()) {
      void this.fetchLogs();
    }
  }

  isPhaseDone(_phase: string, idx: number): boolean {
    const detail = this.runDetail();
    if (!detail) return false;

    const currentPhase = detail.current_phase.toLowerCase();

    // If in commit/done/completed, all phases are done
    if (['commit', 'done', 'completed'].some(p => currentPhase.includes(p))) {
      return true;
    }

    // Check if this phase comes before current phase
    const phaseOrder = ['plan', 'develop', 'review', 'commit'];
    const currentIdx = phaseOrder.indexOf(currentPhase.split('_')[0] ?? '');
    return idx < currentIdx;
  }

  isCurrentPhase(phase: string): boolean {
    const detail = this.runDetail();
    if (!detail) return false;
    return detail.current_phase.toLowerCase().includes(phase);
  }

  phaseDotStyle(phase: string, idx: number): string {
    const isDone = this.isPhaseDone(phase, idx);
    const isCurrent = this.isCurrentPhase(phase);

    let bg = 'var(--bg-elevated)';
    let border = '2px solid var(--border-default)';

    if (isCurrent) {
      bg = 'var(--accent)';
      border = '2px solid var(--accent)';
    } else if (isDone) {
      bg = 'var(--status-completed)';
      border = '2px solid var(--status-completed)';
    }

    const boxShadow = isCurrent ? '0 0 12px var(--accent-glow)' : 'none';
    const color = (isCurrent || isDone) ? '#000' : 'var(--text-muted)';

    return `background: ${bg}; border: ${border}; color: ${color}; box-shadow: ${boxShadow};`;
  }

  phaseLabelStyle(phase: string, idx: number): string {
    const isDone = this.isPhaseDone(phase, idx);
    const isCurrent = this.isCurrentPhase(phase);

    let color = 'var(--text-muted)';
    if (isCurrent) {
      color = 'var(--accent)';
    } else if (isDone) {
      color = 'var(--status-completed)';
    }

    return `color: ${color};`;
  }

  phaseConnectorStyle(phase: string, idx: number): string {
    const isDone = this.isPhaseDone(phase, idx);
    const isCurrent = this.isCurrentPhase(phase);

    let bg = 'var(--border-subtle)';
    if (isDone) {
      bg = 'var(--status-completed)';
    } else if (isCurrent) {
      bg = 'linear-gradient(90deg, var(--accent) 0%, var(--border-subtle) 100%)';
    }

    const opacity = isDone ? 1 : 0.5;
    return `background: ${bg}; opacity: ${opacity};`;
  }
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

  valueStyle(): string {
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

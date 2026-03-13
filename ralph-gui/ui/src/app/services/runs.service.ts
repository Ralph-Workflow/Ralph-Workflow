import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { RunDetail, RunStatus, RunStatusSummary } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

const POLL_INTERVAL_MS = 5000;

/// Status transitions that warrant a notification.
/// Running→anything means the run has concluded or changed state.
const NOTIFY_TRANSITIONS = new Set<RunStatus>(['Paused', 'Failed', 'Completed']);

@Injectable({ providedIn: 'root' })
export class RunsService {
  private readonly tauri = inject(TauriService);

  // State signals
  readonly runDetail = signal<RunDetail | null>(null);
  readonly resumableRuns = signal<RunDetail[]>([]);
  readonly status = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);
  readonly pollingStatus = signal<RunStatusSummary | null>(null);

  // Computed signals
  readonly isLoading = computed(() => this.status() === 'loading');

  // Polling state (non-serializable)
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private previousPollingStatus: RunStatus | null = null;

  async fetchRunDetail(runId: string): Promise<void> {
    this.status.set('loading');
    this.error.set(null);

    try {
      const detail = await this.tauri.getRunDetail(runId);
      this.runDetail.set(detail);
      this.status.set('succeeded');
    } catch (e) {
      this.status.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async fetchResumableRuns(repoPath: string): Promise<void> {
    try {
      const runs = await this.tauri.getResumableRuns(repoPath);
      this.resumableRuns.set(runs);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async pollRunStatus(repoPath: string, worktreePath: string | null): Promise<void> {
    try {
      const summary = await this.tauri.getRunStatus(repoPath, worktreePath);

      // Detect status transition: if previous was Running and new is a terminal state,
      // fire a desktop notification
      const newStatus = summary.status;
      const prev = this.previousPollingStatus;

      if (prev === 'Running' && NOTIFY_TRANSITIONS.has(newStatus)) {
        const runId = summary.run_id ?? 'unknown';
        const context = this.runDetail()?.repo_path ?? this.runDetail()?.worktree_path ?? '';
        void this.tauri.notifyRunStatusChange(newStatus, runId, context);
      }

      this.previousPollingStatus = newStatus;
      this.pollingStatus.set(summary);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  clearRunDetail(): void {
    this.runDetail.set(null);
  }

  startPolling(repoPath: string, worktreePath: string | null): void {
    // Guard against double-start
    if (this.pollingIntervalId !== null) return;

    this.pollingIntervalId = setInterval(() => {
      void this.pollRunStatus(repoPath, worktreePath);
    }, POLL_INTERVAL_MS);
  }

  stopPolling(): void {
    if (this.pollingIntervalId !== null) {
      clearInterval(this.pollingIntervalId);
      this.pollingIntervalId = null;
    }
    this.previousPollingStatus = null;
  }
}

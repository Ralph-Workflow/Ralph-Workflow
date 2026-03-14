import { Component, inject, effect, computed, ChangeDetectionStrategy } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionsService } from '../../services/sessions.service';
import { WorkspaceService } from '../../services/workspace.service';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { RepoSelectorComponent } from '../../components/repo-selector/repo-selector.component';
import { StatCardComponent } from './stat-card.component';
import { QuickActionComponent } from './quick-action.component';
import { ActiveRunsListComponent } from '../../components/active-runs-list/active-runs-list.component';
import { RecentCompletionsComponent } from '../../components/recent-completions/recent-completions.component';
import type { SessionSummary } from '../../types';

const POLL_INTERVAL_MS = 5000;

@Component({
  selector: 'app-home',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    RunStatusBadgeComponent,
    RepoSelectorComponent,
    StatCardComponent,
    QuickActionComponent,
    ActiveRunsListComponent,
    RecentCompletionsComponent,
  ],
  templateUrl: './home.component.html',
  styles: [`
    :host {
      display: block;
    }
  `],
})
export class HomeComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly sessionsService = inject(SessionsService);
  readonly workspaceService = inject(WorkspaceService);
  private readonly router = inject(Router);

  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;

  private readonly mainWorktreeSignal = computed(() =>
    this.worktreesService.worktrees().find(wt => wt.is_main)
  );

  readonly activeWorkspace = this.workspaceService.activeWorkspace;

  private readonly activeWorktreeCountSignal = computed(() =>
    this.worktreesService.worktrees().filter(wt => !wt.is_main).length
  );

  private readonly resumableRunsCountSignal = computed(() =>
    this.sessionsService.needsAttentionRuns().length
  );

  private readonly completedTodayStatsSignal = this.sessionsService.completedTodayStats;

  private readonly dashboardTrendsSignal = this.sessionsService.dashboardTrends;

  private readonly activeRunsSignal = this.sessionsService.activeRuns;

  private readonly needsAttentionRunsSignal = this.sessionsService.needsAttentionRuns;

  private readonly recentCompletionsSignal = this.sessionsService.recentCompletions;

  private readonly hasContentSignal = computed(() =>
    this.worktreesService.worktrees().length > 0 || this.sessionsService.sessions().length > 0
  );

  private readonly needsAttentionWithDetailsSignal = computed(() =>
    this.needsAttentionRunsSignal().map(run => ({
      ...run,
      run_id_short: run.run_id.substring(0, 16),
      relativeTime: this.formatRelativeTime(run.created_at),
    }))
  );

  private readonly activeRunsCountSignal = computed(() => this.activeRunsSignal().length);

  get hasContentValue(): boolean {
    return this.hasContentSignal();
  }

  get activeWorktreeCountValue(): number {
    return this.activeWorktreeCountSignal();
  }

  get resumableRunsCountValue(): number {
    return this.resumableRunsCountSignal();
  }

  get completedTodayStatsValue(): { count: number; successRate: string } {
    return this.completedTodayStatsSignal();
  }

  get dashboardTrendsValue(): { activeWorktrees: 'up' | 'down' | 'flat'; resumableRuns: 'up' | 'down' | 'flat'; completedToday: 'up' | 'down' | 'flat'; successRate: 'up' | 'down' | 'flat' } {
    return this.dashboardTrendsSignal();
  }

  get activeRunsValue(): SessionSummary[] {
    return this.activeRunsSignal();
  }

  get activeRunsCountValue(): number {
    return this.activeRunsCountSignal();
  }

  get needsAttentionRunsValue(): SessionSummary[] {
    return this.needsAttentionRunsSignal();
  }

  get needsAttentionWithDetailsValue(): Array<SessionSummary & { run_id_short: string; relativeTime: string }> {
    return this.needsAttentionWithDetailsSignal();
  }

  get recentCompletionsValue(): SessionSummary[] {
    return this.recentCompletionsSignal();
  }

  constructor() {
    effect(() => {
      const main = this.mainWorktreeSignal();
      if (main) {
        void this.worktreesService.fetchWorktrees(main.path);
      }
    });

    effect((onCleanup) => {
      const workspace = this.activeWorkspace();
      if (workspace) {
        void this.sessionsService.fetchSessions(workspace.path);
      }

      onCleanup(() => {
        this.stopPolling();
      });
    });

    effect((onCleanup) => {
      const workspace = this.activeWorkspace();
      const hasRunning = this.activeRunsSignal().length > 0;

      if (workspace && hasRunning) {
        this.startPolling(workspace.path);
      } else {
        this.stopPolling();
      }

      onCleanup(() => {
        this.stopPolling();
      });
    });
  }

  private formatRelativeTime(isoString: string): string {
    const date = new Date(isoString);
    const now = Date.now();
    const diffMs = now - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);

    if (diffSeconds < 10) return 'just now';
    if (diffSeconds < 60) return `${diffSeconds}s ago`;

    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) return `${diffMinutes}m ago`;

    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}h ago`;

    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }

  private startPolling(repoPath: string): void {
    if (this.pollingIntervalId !== null) return;

    this.pollingIntervalId = setInterval(() => {
      void this.sessionsService.fetchSessions(repoPath);
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollingIntervalId !== null) {
      clearInterval(this.pollingIntervalId);
      this.pollingIntervalId = null;
    }
  }

  onRepoSelected(): void {
    void this.router.navigate(['/']);
  }

  navigateToRun(runId: string): void {
    void this.router.navigate(['/runs', runId]);
  }

  navigateToSessions(): void {
    void this.router.navigate(['/sessions']);
  }

  navigateToWorktrees(): void {
    void this.router.navigate(['/worktrees']);
  }

  navigateToConfiguration(): void {
    void this.router.navigate(['/configuration']);
  }

  async resumeSession(runId: string, event: Event): Promise<void> {
    event.stopPropagation();
    const workspace = this.activeWorkspace();
    if (!workspace) return;
    
    try {
      await this.sessionsService.resumeSession(runId, workspace.path);
    } catch {
      // Error is already handled in the service
    }
  }
}

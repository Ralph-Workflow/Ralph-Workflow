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

  private readonly completedTodayCountSignal = this.sessionsService.completedToday;

  private readonly activeRunsSignal = this.sessionsService.activeRuns;

  private readonly needsAttentionRunsSignal = this.sessionsService.needsAttentionRuns;

  private readonly recentCompletionsSignal = this.sessionsService.recentCompletions;

  private readonly hasContentSignal = computed(() =>
    this.worktreesService.worktrees().length > 0 || this.sessionsService.sessions().length > 0
  );

  private readonly needsAttentionWithShortIdSignal = computed(() =>
    this.needsAttentionRunsSignal().map(run => ({
      ...run,
      run_id_short: run.run_id.substring(0, 16),
    }))
  );

  get hasContentValue(): boolean {
    return this.hasContentSignal();
  }

  get activeWorktreeCountValue(): number {
    return this.activeWorktreeCountSignal();
  }

  get resumableRunsCountValue(): number {
    return this.resumableRunsCountSignal();
  }

  get completedTodayCountValue(): number {
    return this.completedTodayCountSignal();
  }

  get activeRunsValue(): ReturnType<typeof this.sessionsService.activeRuns> {
    return this.activeRunsSignal();
  }

  get needsAttentionRunsValue(): ReturnType<typeof this.sessionsService.needsAttentionRuns> {
    return this.needsAttentionRunsSignal();
  }

  get needsAttentionWithShortIdValue(): ReturnType<typeof this.needsAttentionWithShortIdSignal> {
    return this.needsAttentionWithShortIdSignal();
  }

  get recentCompletionsValue(): ReturnType<typeof this.sessionsService.recentCompletions> {
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
}

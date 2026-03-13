import { Component, inject, effect, computed, ChangeDetectionStrategy } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { RunsService } from '../../services/runs.service';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { RepoSelectorComponent } from '../../components/repo-selector/repo-selector.component';
import { StatCardComponent } from './stat-card.component';
import { QuickActionComponent } from './quick-action.component';

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
  readonly runsService = inject(RunsService);
  private readonly router = inject(Router);

  readonly mainWorktree = computed(() =>
    this.worktreesService.worktrees().find(wt => wt.is_main)
  );

  readonly hasContent = computed(() =>
    this.worktreesService.worktrees().length > 0 || this.runsService.resumableRuns().length > 0
  );

  readonly activeWorktreeCount = computed(() =>
    this.worktreesService.worktrees().filter(wt => !wt.is_main).length
  );

  readonly resumableRunsCount = computed(() =>
    this.runsService.resumableRuns().length
  );

  readonly resumableRunsWithShortId = computed(() =>
    this.runsService.resumableRuns().map(run => ({
      ...run,
      run_id_short: run.run_id.substring(0, 16),
    }))
  );

  get hasContentValue(): boolean { return this.hasContent(); }
  get activeWorktreeCountValue(): number { return this.activeWorktreeCount(); }
  get resumableRunsCountValue(): number { return this.resumableRunsCount(); }
  get resumableRuns() { return this.resumableRunsWithShortId(); }

  constructor() {
    effect(() => {
      const main = this.mainWorktree();
      if (main) {
        void this.runsService.fetchResumableRuns(main.path);
        void this.worktreesService.fetchWorktrees(main.path);
      }
    });
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
}

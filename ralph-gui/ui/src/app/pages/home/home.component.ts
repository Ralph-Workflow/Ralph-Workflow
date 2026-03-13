import { Component, inject, effect, computed, Output, EventEmitter, Input, ChangeDetectionStrategy, forwardRef } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { RunsService } from '../../services/runs.service';
import { RunStatusBadgeComponent } from '../../components/run-status-badge/run-status-badge.component';
import { RepoSelectorComponent } from '../../components/repo-selector/repo-selector.component';

// Stat Card Component
@Component({
  selector: 'app-stat-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="card" style="padding: 18px 20px;" [style.border-bottom]="accent && value > 0 ? '2px solid var(--accent)' : '2px solid transparent'">
      <div [style]="valueStyle()">
        {{ value }}
      </div>
      <div style="font-size: 11px; color: var(--text-muted); font-family: var(--font-ui); text-transform: uppercase; letter-spacing: 0.06em;">
        {{ label }}
      </div>
    </div>
  `,
})
export class StatCardComponent {
  @Input() label = '';
  @Input() value = 0;
  @Input() accent = false;

  valueStyle(): string {
    const color = this.accent && this.value > 0 ? 'var(--accent)' : 'var(--text-primary)';
    const shadow = this.accent && this.value > 0 ? '0 0 20px var(--accent-glow)' : 'none';
    return `font-family: var(--font-display); font-size: 32px; font-weight: 700; color: ${color}; letter-spacing: -0.03em; line-height: 1; margin-bottom: 6px; text-shadow: ${shadow};`;
  }
}

// Quick Action Component
@Component({
  selector: 'app-quick-action',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button class="card" style="display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; cursor: pointer; background: var(--bg-surface); border: 1px solid var(--border-subtle); text-align: left; width: 100%; transition: border-color var(--transition-fast), background var(--transition-fast);" (click)="action.emit()" (mouseenter)="onHover($event)" (mouseleave)="onLeave($event)">
      <span style="font-size: 16px; color: var(--accent); flex-shrink: 0; margin-top: 2px; opacity: 0.9;">{{ icon }}</span>
      <div>
        <div style="font-size: 13px; font-weight: 500; color: var(--text-primary); margin-bottom: 2px;">{{ label }}</div>
        <div style="font-size: 11px; color: var(--text-muted); line-height: 1.5;">{{ desc }}</div>
      </div>
    </button>
  `,
})
export class QuickActionComponent {
  @Input() icon = '';
  @Input() label = '';
  @Input() desc = '';
  @Output() action = new EventEmitter<void>();

  onHover(event: MouseEvent): void {
    const btn = event.currentTarget as HTMLButtonElement;
    btn.style.borderColor = 'var(--border-default)';
    btn.style.background = 'var(--bg-elevated)';
  }

  onLeave(event: MouseEvent): void {
    const btn = event.currentTarget as HTMLButtonElement;
    btn.style.borderColor = 'var(--border-subtle)';
    btn.style.background = 'var(--bg-surface)';
  }
}

@Component({
  selector: 'app-home',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    RunStatusBadgeComponent,
    RepoSelectorComponent,
    forwardRef(() => StatCardComponent),
    forwardRef(() => QuickActionComponent),
  ],
  template: `
    @if (!hasContent()) {
      <div class="page-content" style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
        <div style="max-width: 480px; text-align: center; animation: fadeIn 300ms ease;">
          <div style="font-size: 40px; margin-bottom: 20px; opacity: 0.15; font-family: var(--font-mono); color: var(--accent); letter-spacing: -0.05em;">◈</div>
          <h1 style="font-family: var(--font-display); font-size: 24px; font-weight: 600; color: var(--text-primary); letter-spacing: -0.02em; margin-bottom: 10px;">
            Welcome to Ralph Workflow
          </h1>
          <p style="font-size: 13px; color: var(--text-secondary); line-height: 1.7; margin-bottom: 28px;">
            An unattended AI orchestration platform for long-running development and review cycles.
          </p>
          <div style="text-align: left; margin-bottom: 16px;">
            <div style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.06em;">
              Enter your repository path to get started
            </div>
            <app-repo-selector (repoSelected)="onRepoSelected()" />
          </div>
        </div>
      </div>
    } @else {
      <div class="page-content">
        <h1 class="page-title" style="animation: fadeIn 200ms ease;">Home</h1>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; animation: fadeIn 200ms ease 40ms both;">
          <app-stat-card label="Active worktrees" [value]="activeWorktreeCount()" />
          <app-stat-card label="Resumable runs" [value]="resumableRunsCount()" [accent]="resumableRunsCount() > 0" />
        </div>

        @if (resumableRunsCount() > 0) {
          <section style="margin-bottom: 24px; animation: fadeIn 200ms ease 80ms both;">
            <div class="section-label">Interrupted runs — action needed</div>
            <div style="display: flex; flex-direction: column; gap: 8px;">
              @for (run of runsService.resumableRuns(); track run.run_id) {
                <div class="card card-elevated" style="display: flex; align-items: center; gap: 12px; padding: 12px 16px;">
                  <app-run-status-badge status="Paused" [showLabel]="false" [isDegraded]="run.is_degraded ?? false" />
                  <div style="flex: 1; min-width: 0;">
                    <div style="font-family: var(--font-mono); font-size: 12px; color: var(--text-primary);">
                      {{ run.run_id.slice(0, 16) }}
                    </div>
                    <div style="font-size: 11px; color: var(--text-muted);">
                      {{ run.current_phase }} · {{ run.agent_profile }}
                    </div>
                  </div>
                  <button class="btn btn-secondary" style="font-size: 12px;" (click)="navigateToRun(run.run_id)">
                    View
                  </button>
                </div>
              }
            </div>
          </section>
        }

        <section style="animation: fadeIn 200ms ease 120ms both;">
          <div class="section-label" style="display: flex; justify-content: space-between; align-items: center;">
            <span>Quick actions</span>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
            <app-quick-action icon="▶" label="New session" desc="Start an unattended run" (action)="navigateToSessions()" />
            <app-quick-action icon="⎇" label="Worktrees" desc="Manage git worktrees" (action)="navigateToWorktrees()" />
          </div>
        </section>
      </div>
    }
  `,
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

  constructor() {
    // Load resumable runs when main worktree is available
    effect(() => {
      const main = this.mainWorktree();
      if (main) {
        void this.runsService.fetchResumableRuns(main.path);
        void this.worktreesService.fetchWorktrees(main.path);
      }
    });
  }

  onRepoSelected(): void {
    // Refresh the page
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

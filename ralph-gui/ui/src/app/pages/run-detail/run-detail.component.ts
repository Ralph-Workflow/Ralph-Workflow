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
  template: `
    <div class="page-content">
      @if (isLoading()) {
        <!-- Loading state -->
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
          <button class="btn btn-ghost" (click)="goBack()">← Back</button>
          <h1 class="page-title" style="margin-bottom: 0;">Run Detail</h1>
        </div>
        <div style="padding: var(--space-10); text-align: center; color: var(--text-muted); font-size: 13px; font-family: var(--font-mono);">
          Loading run...
        </div>
      } @else if (error() || !runDetail()) {
        <!-- Error state -->
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
          <button class="btn btn-ghost" (click)="goBack()">← Back</button>
          <h1 class="page-title" style="margin-bottom: 0;">Run Detail</h1>
        </div>
        <div class="empty-state">
          <span class="empty-state-icon">⊘</span>
          <div class="empty-state-title">Run not found</div>
          <div class="empty-state-desc">{{ error() ?? 'This run could not be loaded.' }}</div>
        </div>
      } @else {
        <!-- Content -->
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
          <button class="btn btn-ghost" (click)="goBack()">← Back</button>
          <h1 class="page-title" style="margin-bottom: 0; flex: 1;">Run Detail</h1>
          <app-run-status-badge
            [status]="runDetail()!.status"
            [showLabel]="true"
            [isDegraded]="runDetail()!.is_degraded ?? false"
          />
          @if (canResume()) {
            <button class="btn btn-primary" (click)="handleResume()">Resume</button>
          }
        </div>

        <div style="animation: fadeIn 200ms ease 40ms both;">
          <!-- Run ID banner -->
          <div style="padding: 10px 16px; background: var(--accent-bg); border: 1px solid rgba(232,168,56,0.15); border-radius: var(--radius-md); font-family: var(--font-mono); font-size: 12px; color: var(--accent); margin-bottom: var(--space-5); letter-spacing: 0.02em;">
            {{ runDetail()!.run_id }}
          </div>

          <!-- Degraded condition banner -->
          @if (runDetail()!.is_degraded) {
            <div
              data-testid="degraded-banner"
              style="padding: 10px 16px; background: var(--status-degraded-bg); border: 1px solid var(--status-degraded-border); border-radius: var(--radius-md); color: var(--status-degraded); font-size: 12px; font-family: var(--font-mono); margin-bottom: var(--space-4); display: flex; align-items: center; gap: 10px;"
            >
              <span style="font-size: 15px; flex-shrink: 0;">⚠</span>
              <span>
                <strong style="font-weight: 600;">Degraded conditions</strong>
                — retries exceeded or fallback agent active. Monitor closely.
              </span>
            </div>
          }

          <!-- Details card -->
          <div class="card" style="margin-bottom: var(--space-5);">
            <div style="font-family: var(--font-display); font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px;">
              Run information
            </div>

            <app-detail-row label="status" [value]="runDetail()!.status" [mono]="true" />
            <app-detail-row label="phase" [value]="runDetail()!.current_phase" [mono]="true" />
            <app-detail-row label="agent_profile" [value]="runDetail()!.agent_profile" [mono]="true" />
            <app-detail-row label="repo_path" [value]="runDetail()!.repo_path" [mono]="true" />
            <app-detail-row label="worktree_path" [value]="runDetail()!.worktree_path" [mono]="true" />
            <app-detail-row label="created_at" [value]="runDetail()!.created_at" [mono]="true" />
            <app-detail-row label="last_checkpoint" [value]="runDetail()!.last_checkpoint" [mono]="true" />
            <app-detail-row label="iteration_count" [value]="iterationCount()" [mono]="true" />
            @if (runDetail()!.last_error) {
              <app-detail-row label="last_error" [value]="runDetail()!.last_error ?? null" [mono]="true" />
            }
            <app-detail-row label="description" [value]="runDetail()!.description || null" [mono]="false" />
          </div>

          <!-- Phase timeline -->
          <div class="card" style="margin-bottom: var(--space-5);">
            <div style="font-family: var(--font-display); font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 20px;">
              Phase
            </div>

            <div class="phase-timeline" style="align-items: flex-start;">
              @for (phase of phases; track phase; let idx = $index) {
                <div style="display: flex; align-items: flex-start; flex: {{ idx < 3 ? 1 : 'unset' }};">
                  <div class="phase-node">
                    <div
                      class="phase-node__dot"
                      [style]="phaseDotStyle(phase, idx)"
                    >
                      @if (isPhaseDone(phase, idx)) {
                        ✓
                      } @else {
                        {{ idx + 1 }}
                      }
                    </div>
                    <div class="phase-node__label" [style]="phaseLabelStyle(phase, idx)">
                      {{ phase }}
                    </div>
                  </div>
                  @if (idx < 3) {
                    <div class="phase-connector" [style]="phaseConnectorStyle(phase, idx)"></div>
                  }
                </div>
              }
            </div>
          </div>

          <!-- Run Log section -->
          <div class="card" style="margin-top: var(--space-5);">
            <div style="display: flex; align-items: center; justify-content: space-between;">
              <div style="font-family: var(--font-display); font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em;">
                Run Log
              </div>
              <div style="display: flex; gap: 8px;">
                @if (logExpanded() && logFetched()) {
                  <button class="btn btn-ghost" style="font-size: 11px; padding: 2px 10px;" (click)="fetchLogs()">Refresh</button>
                }
                <button
                  class="btn btn-ghost"
                  style="font-size: 11px; padding: 2px 10px;"
                  (click)="toggleLogs()"
                  data-testid="toggle-run-log"
                >
                  {{ logExpanded() ? 'Collapse' : 'Expand' }}
                </button>
              </div>
            </div>
            @if (logExpanded()) {
              <div style="margin-top: 12px;">
                <app-run-log
                  [lines]="logLines()"
                  [isLoading]="logLoading()"
                  aria-label="Run log output"
                />
              </div>
            }
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .phase-timeline {
      display: flex;
      align-items: flex-start;
    }
    .phase-node {
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .phase-node__dot {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 600;
      font-family: var(--font-mono);
    }
    .phase-node__label {
      margin-top: 6px;
      font-size: 11px;
      font-family: var(--font-mono);
      text-transform: capitalize;
    }
    .phase-connector {
      flex: 1;
      height: 2px;
      margin: 13px 8px 0;
      border-radius: 1px;
    }
  `],
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
  template: `
    <div style="display: flex; align-items: flex-start; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--border-subtle);">
      <div style="width: 160px; flex-shrink: 0; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); padding-top: 1px;">
        {{ label() }}
      </div>
      <div [style]="valueStyle()">
        {{ value() ?? '—' }}
      </div>
    </div>
  `,
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

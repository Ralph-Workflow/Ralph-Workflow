import { Component, Input, Output, EventEmitter, inject, effect, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { SessionsService } from '../../services/sessions.service';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import type { RunStatus, SessionSummary } from '../../types';

function sessionStatusToRunStatus(status: string): RunStatus {
  switch (status) {
    case 'running': return 'Running';
    case 'paused':
    case 'interrupted': return 'Paused';
    case 'completed': return 'Completed';
    case 'failed': return 'Failed';
    default: return 'NotStarted';
  }
}

@Component({
  selector: 'app-session-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RunStatusBadgeComponent],
  template: `
    @if (sessionsService.status() === 'loading') {
      <div style="padding: 24px; color: var(--text-muted); font-size: 13px; font-family: var(--font-mono);">
        Loading sessions…
      </div>
    } @else if (sessionsService.sessions().length === 0) {
      <div class="empty-state">
        <span class="empty-state-icon">◈</span>
        <div class="empty-state-title">No sessions yet</div>
        <div class="empty-state-desc">Start a new session to begin an unattended Ralph workflow.</div>
      </div>
    } @else if (visibleSessions().length === 0) {
      <div class="empty-state">
        <span class="empty-state-icon">◈</span>
        <div class="empty-state-title">No sessions match the selected filters.</div>
        <div class="empty-state-desc">Try clearing the filters to see all sessions.</div>
      </div>
    } @else {
      <div style="display: flex; flex-direction: column; gap: 2px;">
        @for (session of visibleSessions(); track session.run_id) {
          <div
            role="button"
            tabindex="0"
            (click)="onSelect(session)"
            (keydown)="onKeyDown($event, session)"
            (mouseenter)="onRowHover($event)"
            (mouseleave)="onRowLeave($event)"
            [class.selected]="session.run_id === sessionsService.selectedRunId()"
            style="display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: var(--radius-md); border: 1px solid var(--border-subtle); cursor: pointer; transition: all var(--transition-fast);"
          >
            <app-run-status-badge
              [status]="sessionStatusToRunStatus(session.status)"
              [showLabel]="false"
              size="sm"
              [isDegraded]="session.is_degraded === true"
            />
            <div style="flex: 1; min-width: 0;">
              <div style="font-family: var(--font-mono); font-size: 12px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                {{ session.run_id.slice(0, 16) }}
              </div>
              <div style="font-size: 11px; color: var(--text-muted); margin-top: 1px;">
                {{ session.description }} · {{ session.created_at }}
              </div>
            </div>
            @if (canResume(session)) {
              <button
                class="btn btn-secondary"
                style="padding: 3px 10px; font-size: 11px;"
                (click)="onResumeClick($event, session)"
              >
                Resume
              </button>
            }
            <span style="font-size: 10px; color: var(--text-muted); flex-shrink: 0;">›</span>
          </div>
        }
      </div>
    }
  `,
  styles: [`
    :host div[role="button"]:hover {
      background: var(--bg-elevated);
      border-color: var(--border-default);
    }
    :host div[role="button"].selected {
      background: var(--accent-bg);
      border-color: var(--accent-dim)30;
    }
    :host div[role="button"]:focus {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
  `],
})
export class SessionListComponent {
  readonly sessionsService = inject(SessionsService);
  private readonly router = inject(Router);

  @Input() repoPath = '';
  @Input() filterStatus: string[] = [];
  @Input() filterWorktreePath: string | null | undefined = undefined;
  @Output() resumeRun = new EventEmitter<string>();

  private intervalId: ReturnType<typeof setInterval> | null = null;

  visibleSessions = computed(() => {
    const sessions = this.sessionsService.sessions();
    const hasStatusFilter = this.filterStatus !== undefined && this.filterStatus.length > 0;
    const hasWorktreeFilter = this.filterWorktreePath !== undefined && this.filterWorktreePath !== null;

    return sessions.filter((s) => {
      if (hasStatusFilter && !this.filterStatus.includes(s.status)) return false;
      if (hasWorktreeFilter) {
        if (this.filterWorktreePath === '') {
          if (s.worktree_path !== null) return false;
        } else {
          if (s.worktree_path !== this.filterWorktreePath) return false;
        }
      }
      return true;
    });
  });

  constructor() {
    // Fetch sessions when repoPath changes
    effect(() => {
      const path = this.repoPath;
      if (path) {
        void this.sessionsService.fetchSessions(path);
      }
    });

    // Set up polling for running sessions
    effect((onCleanup) => {
      const path = this.repoPath;
      const sessions = this.sessionsService.sessions();
      const hasRunning = sessions.some(s => s.status === 'running');

      if (path && hasRunning) {
        this.intervalId = setInterval(() => {
          void this.sessionsService.fetchSessions(path);
        }, 5000);
      }

      onCleanup(() => {
        if (this.intervalId) {
          clearInterval(this.intervalId);
          this.intervalId = null;
        }
      });
    });
  }

  sessionStatusToRunStatus = sessionStatusToRunStatus;

  canResume(session: SessionSummary): boolean {
    return session.status === 'paused' || session.status === 'interrupted';
  }

  onSelect(session: SessionSummary): void {
    this.sessionsService.setActiveSession(session.run_id);
    void this.router.navigate(['/runs', session.run_id]);
  }

  onKeyDown(event: KeyboardEvent, session: SessionSummary): void {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      this.onSelect(session);
    }
  }

  onResumeClick(event: MouseEvent, session: SessionSummary): void {
    event.stopPropagation();
    this.resumeRun.emit(session.run_id);
  }

  onRowHover(event: MouseEvent): void {
    const el = event.currentTarget as HTMLElement;
    if (!el.classList.contains('selected')) {
      el.style.background = 'var(--bg-elevated)';
      el.style.borderColor = 'var(--border-default)';
    }
  }

  onRowLeave(event: MouseEvent): void {
    const el = event.currentTarget as HTMLElement;
    if (!el.classList.contains('selected')) {
      el.style.background = 'transparent';
      el.style.borderColor = 'var(--border-subtle)';
    }
  }
}

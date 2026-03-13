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
  templateUrl: './session-list.component.html',
  styleUrls: ['./session-list.component.css'],
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
    effect(() => {
      const path = this.repoPath;
      if (path) {
        void this.sessionsService.fetchSessions(path);
      }
    });

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

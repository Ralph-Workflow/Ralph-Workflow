import {
  Component,
  Input,
  Output,
  EventEmitter,
  inject,
  effect,
  computed,
  signal,
  ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { SessionsService } from '../../services/sessions.service';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import { SessionStatusPipe } from '../../pipes/session-status.pipe';
import type { RunStatus, SessionSummary } from '../../types';

export type SortKey = 'created_at' | 'description' | 'status';
export type SortDir = 'asc' | 'desc';

export interface WorktreeOption {
  label: string;
  value: string;
}

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
  imports: [CommonModule, RunStatusBadgeComponent, SessionStatusPipe],
  templateUrl: './session-list.component.html',
  styleUrls: ['./session-list.component.css'],
})
export class SessionListComponent {
  readonly sessionsService = inject(SessionsService);
  private readonly router = inject(Router);

  @Input() repoPath = '';
  @Input() filterStatus: string[] = [];
  @Input() filterWorktreePath: string | null | undefined = undefined;
  /** External search term passed from parent (sessions page search box). */
  @Input() set externalSearchTerm(value: string) {
    this.searchTerm.set(value);
  }
  @Output() resumeRun = new EventEmitter<string>();

  // Search & filter signals (also writable from parent via externalSearchTerm input)
  readonly searchTerm = signal('');
  readonly worktreeFilter = signal<string>('__all__');

  // Sort signals (default: created_at desc)
  readonly sortKey = signal<SortKey>('created_at');
  readonly sortDirection = signal<SortDir>('desc');

  // Selection signals
  readonly selectedIds = signal<Set<string>>(new Set());

  private intervalId: ReturnType<typeof setInterval> | null = null;

  // Distinct worktree options for filter dropdown
  readonly worktreeFilterOptions = computed<WorktreeOption[]>(() => {
    const sessions = this.sessionsService.sessions();
    const paths = new Set<string>();
    for (const s of sessions) {
      if (s.worktree_path) {
        paths.add(s.worktree_path);
      }
    }
    const options: WorktreeOption[] = [{ label: 'All Worktrees', value: '__all__' }];
    for (const p of Array.from(paths).sort()) {
      options.push({ label: p, value: p });
    }
    return options;
  });

  readonly visibleSessions = computed(() => {
    const sessions = this.sessionsService.sessions();
    const search = this.searchTerm().toLowerCase().trim();
    const worktree = this.worktreeFilter();
    const hasStatusFilter = this.filterStatus !== undefined && this.filterStatus.length > 0;
    const hasWorktreeFilter = this.filterWorktreePath !== undefined && this.filterWorktreePath !== null;
    const key = this.sortKey();
    const dir = this.sortDirection();

    const filtered = sessions.filter((s) => {
      // External worktree filter from parent (context switcher)
      if (hasStatusFilter && !this.filterStatus.includes(s.status)) return false;
      if (hasWorktreeFilter) {
        if (this.filterWorktreePath === '') {
          if (s.worktree_path !== null) return false;
        } else {
          if (s.worktree_path !== this.filterWorktreePath) return false;
        }
      }

      // Internal worktree dropdown filter
      if (worktree !== '__all__') {
        if (s.worktree_path !== worktree) return false;
      }

      // Search filter
      if (search) {
        const desc = s.description.toLowerCase();
        const wt = (s.worktree_path ?? '').toLowerCase();
        const id = s.run_id.toLowerCase();
        if (!desc.includes(search) && !wt.includes(search) && !id.includes(search)) {
          return false;
        }
      }

      return true;
    });

    // Sort
    filtered.sort((a, b) => {
      let cmp = 0;
      if (key === 'created_at') {
        cmp = a.created_at.localeCompare(b.created_at);
      } else if (key === 'description') {
        cmp = a.description.localeCompare(b.description);
      } else if (key === 'status') {
        cmp = a.status.localeCompare(b.status);
      }
      return dir === 'asc' ? cmp : -cmp;
    });

    return filtered;
  });

  // Batch action visibility
  readonly showBatchBar = computed(() => this.selectedIds().size > 0);

  readonly canBatchResume = computed(() => {
    const ids = this.selectedIds();
    const sessions = this.sessionsService.sessions();
    return sessions.some(
      s => ids.has(s.run_id) && (s.status === 'paused' || s.status === 'failed' || s.status === 'interrupted'),
    );
  });

  readonly canBatchCancel = computed(() => {
    const ids = this.selectedIds();
    const sessions = this.sessionsService.sessions();
    return sessions.some(s => ids.has(s.run_id) && s.status === 'running');
  });

  readonly isAllSelected = computed(() => {
    const visible = this.visibleSessions();
    if (visible.length === 0) return false;
    const ids = this.selectedIds();
    return visible.every(s => ids.has(s.run_id));
  });

  /** Set of run IDs that can be resumed — avoids parameterized canResume(session) calls in templates. */
  readonly resumableRunIds = computed<Set<string>>(() => {
    const ids = new Set<string>();
    for (const s of this.sessionsService.sessions()) {
      if (s.status === 'paused' || s.status === 'interrupted') {
        ids.add(s.run_id);
      }
    }
    return ids;
  });

  /** Pre-computed display rows: sessions enriched with derived template values. */
  readonly displaySessions = computed(() => {
    const sessions = this.visibleSessions();
    const selectedIds = this.selectedIds();
    const resumable = this.resumableRunIds();
    const activeRunId = this.sessionsService.selectedRunId();
    return sessions.map(session => ({
      session,
      shortRunId: session.run_id.substring(0, 16),
      isSelected: selectedIds.has(session.run_id),
      isActive: session.run_id === activeRunId,
      canResume: resumable.has(session.run_id),
    }));
  });

  /** Getters for template read access (avoids calling signals/computed with () in templates). */
  get currentShowBatchBar() { return this.showBatchBar(); }
  get currentSelectedIds() { return this.selectedIds(); }
  get currentCanBatchResume() { return this.canBatchResume(); }
  get currentCanBatchCancel() { return this.canBatchCancel(); }
  get currentIsAllSelected() { return this.isAllSelected(); }
  get currentVisibleSessions() { return this.visibleSessions(); }
  get currentDisplaySessions() { return this.displaySessions(); }
  get currentResumableRunIds() { return this.resumableRunIds(); }
  get serviceStatus() { return this.sessionsService.status(); }
  get serviceSessions() { return this.sessionsService.sessions(); }
  get serviceSelectedRunId() { return this.sessionsService.selectedRunId(); }

  /** Sort icon getters — avoids parameterized sortIcon(key) calls in templates. */
  get descriptionSortIcon() { return this.sortIcon('description'); }
  get statusSortIcon() { return this.sortIcon('status'); }
  get createdAtSortIcon() { return this.sortIcon('created_at'); }

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

  setSort(key: SortKey): void {
    if (this.sortKey() === key) {
      this.sortDirection.update(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      this.sortKey.set(key);
      this.sortDirection.set('asc');
    }
  }

  toggleSelect(runId: string): void {
    this.selectedIds.update(prev => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
      } else {
        next.add(runId);
      }
      return next;
    });
  }

  selectAll(): void {
    if (this.isAllSelected()) {
      this.selectedIds.set(new Set());
    } else {
      const ids = new Set(this.visibleSessions().map(s => s.run_id));
      this.selectedIds.set(ids);
    }
  }

  canResume(session: SessionSummary): boolean {
    return session.status === 'paused' || session.status === 'interrupted';
  }

  async batchResume(): Promise<void> {
    const ids = this.selectedIds();
    const sessions = this.sessionsService.sessions();
    const resumable = sessions.filter(
      s => ids.has(s.run_id) && (s.status === 'paused' || s.status === 'failed' || s.status === 'interrupted'),
    );
    for (const s of resumable) {
      await this.sessionsService.resumeSession(s.run_id, this.repoPath);
    }
    this.selectedIds.set(new Set());
  }

  async batchCancel(): Promise<void> {
    if (!confirm('Cancel all selected running sessions?')) return;
    const ids = this.selectedIds();
    const sessions = this.sessionsService.sessions();
    const cancellable = sessions.filter(s => ids.has(s.run_id) && s.status === 'running');
    for (const s of cancellable) {
      this.cancelRun.emit(s.run_id);
    }
    this.selectedIds.set(new Set());
  }

  async batchDelete(): Promise<void> {
    if (!confirm('Delete all selected sessions?')) return;
    this.deleteSelected.emit(Array.from(this.selectedIds()));
    this.selectedIds.set(new Set());
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

  onCheckboxClick(event: MouseEvent, runId: string): void {
    event.stopPropagation();
    this.toggleSelect(runId);
  }

  sortIcon(key: SortKey): string {
    if (this.sortKey() !== key) return '↕';
    return this.sortDirection() === 'asc' ? '↑' : '↓';
  }

  @Output() readonly cancelRun = new EventEmitter<string>();
  @Output() readonly deleteSelected = new EventEmitter<string[]>();

  /** Clears the current selection set. */
  clearSelection(): void {
    this.selectedIds.set(new Set());
  }
}

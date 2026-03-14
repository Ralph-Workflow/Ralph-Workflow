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
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { SessionsService } from '../../services/sessions.service';
import { TauriService } from '../../services/tauri.service';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import { CancelConfirmationComponent } from '../cancel-confirmation/cancel-confirmation.component';
import { BatchProgressOverlayComponent } from '../batch-progress-overlay/batch-progress-overlay.component';
import { SessionStatusPipe } from '../../pipes/session-status.pipe';
import type { RunStatus, SessionSummary, BatchOperationResult } from '../../types';

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

function formatAge(createdAt: string): string {
  const created = new Date(createdAt);
  const now = new Date();
  const diffMs = now.getTime() - created.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'now';
  if (diffMins < 60) return `${diffMins}m`;
  if (diffHours < 24) return `${diffHours}h`;
  return `${diffDays}d`;
}

function formatPipelineStep(phase: string): string {
  const phaseMap: Record<string, string> = {
    plan: 'Plan',
    develop: 'Develop',
    review: 'Review',
    commit: 'Commit',
  };
  return phaseMap[phase.toLowerCase()] || phase;
}

@Component({
  selector: 'app-session-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, MatMenuModule, MatIconModule, MatDividerModule, RunStatusBadgeComponent, CancelConfirmationComponent, BatchProgressOverlayComponent, SessionStatusPipe],
  templateUrl: './session-list.component.html',
  styleUrls: ['./session-list.component.css'],
})
export class SessionListComponent {
  @ViewChild('contextMenuTrigger') contextMenuTrigger!: MatMenuTrigger;

  readonly sessionsService = inject(SessionsService);
  readonly tauriService = inject(TauriService);
  private readonly router = inject(Router);

  @Input() repoPath = '';
  @Input() filterStatus: string[] = [];
  @Input() filterWorktreePath: string | null | undefined = undefined;
  @Input() set externalSearchTerm(value: string) {
    this.searchTerm.set(value);
  }
  @Output() resumeRun = new EventEmitter<string>();

  readonly searchTerm = signal('');
  readonly worktreeFilter = signal<string>('__all__');

  readonly sortKey = signal<SortKey>('created_at');
  readonly sortDirection = signal<SortDir>('desc');

  readonly selectedIds = signal<Set<string>>(new Set());

  readonly contextMenuSession = signal<SessionSummary | null>(null);

  readonly showBatchCancelDialog = signal(false);
  readonly showBatchDeleteDialog = signal(false);

  readonly batchOverlayVisible = signal(false);
  readonly batchOperationType = signal<'resume' | 'cancel' | 'delete'>('resume');
  readonly batchTargetIds = signal<string[]>([]);
  readonly batchResult = signal<BatchOperationResult | null>(null);
  readonly batchInProgress = signal(false);
  readonly batchRunIdToName = signal<Record<string, string>>({});

  private intervalId: ReturnType<typeof setInterval> | null = null;

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
      if (hasStatusFilter && !this.filterStatus.includes(s.status)) return false;
      if (hasWorktreeFilter) {
        if (this.filterWorktreePath === '') {
          if (s.worktree_path !== null) return false;
        } else {
          if (s.worktree_path !== this.filterWorktreePath) return false;
        }
      }

      if (worktree !== '__all__') {
        if (s.worktree_path !== worktree) return false;
      }

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

  readonly resumableRunIds = computed<Set<string>>(() => {
    const ids = new Set<string>();
    for (const s of this.sessionsService.sessions()) {
      if (s.status === 'paused' || s.status === 'interrupted') {
        ids.add(s.run_id);
      }
    }
    return ids;
  });

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
      pipelineStep: formatPipelineStep(session.phase),
      age: formatAge(session.created_at),
    }));
  });

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
    const resumableIds = resumable.map(s => s.run_id);

    if (resumableIds.length === 0) {
      this.selectedIds.set(new Set());
      return;
    }

    const nameMap: Record<string, string> = {};
    for (const s of resumable) {
      nameMap[s.run_id] = s.description || s.run_id.substring(0, 16);
    }

    this.batchOperationType.set('resume');
    this.batchTargetIds.set(resumableIds);
    this.batchRunIdToName.set(nameMap);
    this.batchResult.set(null);
    this.batchInProgress.set(true);
    this.batchOverlayVisible.set(true);

    try {
      const result = await this.tauriService.batchResumeSessions(resumableIds);
      this.batchResult.set(result);
    } catch (e) {
      this.batchResult.set({
        succeeded: 0,
        failed: resumableIds.length,
        errors: Object.fromEntries(resumableIds.map(id => [id, String(e)])),
      });
    } finally {
      this.batchInProgress.set(false);
    }

    void this.sessionsService.fetchSessions(this.repoPath);
  }

  batchCancel(): void {
    this.showBatchCancelDialog.set(true);
  }

  async onBatchCancelConfirmed(confirmed: boolean): Promise<void> {
    this.showBatchCancelDialog.set(false);
    if (!confirmed) return;

    const ids = this.selectedIds();
    const sessions = this.sessionsService.sessions();
    const cancellable = sessions.filter(s => ids.has(s.run_id) && s.status === 'running');
    const cancellableIds = cancellable.map(s => s.run_id);

    if (cancellableIds.length === 0) {
      this.selectedIds.set(new Set());
      return;
    }

    const nameMap: Record<string, string> = {};
    for (const s of cancellable) {
      nameMap[s.run_id] = s.description || s.run_id.substring(0, 16);
    }

    this.batchOperationType.set('cancel');
    this.batchTargetIds.set(cancellableIds);
    this.batchRunIdToName.set(nameMap);
    this.batchResult.set(null);
    this.batchInProgress.set(true);
    this.batchOverlayVisible.set(true);

    try {
      const result = await this.tauriService.batchCancelSessions(cancellableIds);
      this.batchResult.set(result);
    } catch (e) {
      this.batchResult.set({
        succeeded: 0,
        failed: cancellableIds.length,
        errors: Object.fromEntries(cancellableIds.map(id => [id, String(e)])),
      });
    } finally {
      this.batchInProgress.set(false);
    }

    void this.sessionsService.fetchSessions(this.repoPath);
  }

  batchDelete(): void {
    this.showBatchDeleteDialog.set(true);
  }

  async onBatchDeleteConfirmed(confirmed: boolean): Promise<void> {
    this.showBatchDeleteDialog.set(false);
    if (!confirmed) return;

    const ids = Array.from(this.selectedIds());

    if (ids.length === 0) return;

    const sessions = this.sessionsService.sessions();
    const nameMap: Record<string, string> = {};
    for (const s of sessions) {
      if (ids.includes(s.run_id)) {
        nameMap[s.run_id] = s.description || s.run_id.substring(0, 16);
      }
    }

    this.batchOperationType.set('delete');
    this.batchTargetIds.set(ids);
    this.batchRunIdToName.set(nameMap);
    this.batchResult.set(null);
    this.batchInProgress.set(true);
    this.batchOverlayVisible.set(true);

    try {
      const result = await this.tauriService.batchDeleteSessions(ids);
      this.batchResult.set(result);
    } catch (e) {
      this.batchResult.set({
        succeeded: 0,
        failed: ids.length,
        errors: Object.fromEntries(ids.map(id => [id, String(e)])),
      });
    } finally {
      this.batchInProgress.set(false);
    }

    void this.sessionsService.fetchSessions(this.repoPath);
  }

  onBatchOverlayClosed(): void {
    this.batchOverlayVisible.set(false);
    this.selectedIds.set(new Set());
  }

  onOpenRun(runId: string): void {
    this.batchOverlayVisible.set(false);
    this.selectedIds.set(new Set());
    void this.router.navigate(['/runs', runId]);
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

  clearSelection(): void {
    this.selectedIds.set(new Set());
  }

  onContextMenu(event: MouseEvent, session: SessionSummary): void {
    event.preventDefault();
    event.stopPropagation();
    this.contextMenuSession.set(session);
    this.contextMenuTrigger.openMenu();
  }

  onContextMenuOpenDetail(): void {
    const session = this.contextMenuSession();
    if (session) {
      this.onSelect(session);
    }
    this.contextMenuSession.set(null);
  }

  onContextMenuResume(): void {
    const session = this.contextMenuSession();
    if (session && (session.status === 'paused' || session.status === 'failed' || session.status === 'interrupted')) {
      this.resumeRun.emit(session.run_id);
    }
    this.contextMenuSession.set(null);
  }

  onContextMenuCancel(): void {
    const session = this.contextMenuSession();
    if (session && session.status === 'running') {
      this.cancelRun.emit(session.run_id);
    }
    this.contextMenuSession.set(null);
  }

  onContextMenuToggleSelect(): void {
    const session = this.contextMenuSession();
    if (session) {
      this.toggleSelect(session.run_id);
    }
    this.contextMenuSession.set(null);
  }

  get contextMenuSessionValue(): SessionSummary | null { return this.contextMenuSession(); }
  get contextMenuCanResume(): boolean {
    const s = this.contextMenuSession();
    return s !== null && (s.status === 'paused' || s.status === 'failed' || s.status === 'interrupted');
  }
  get contextMenuCanCancel(): boolean {
    const s = this.contextMenuSession();
    return s !== null && s.status === 'running';
  }
  get contextMenuIsSelected(): boolean {
    const s = this.contextMenuSession();
    return s !== null && this.selectedIds().has(s.run_id);
  }
}

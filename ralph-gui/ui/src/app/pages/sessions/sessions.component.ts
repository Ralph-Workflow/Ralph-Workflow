import { Component, inject, signal, effect, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule, ActivatedRoute } from '@angular/router';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionListComponent } from '../../components/session-list/session-list.component';
import { NewSessionWizardComponent } from '../../components/new-session-wizard/new-session-wizard.component';

type View = 'list' | 'new';

const STATUS_CHIPS = [
  { label: 'Running', value: 'running' },
  { label: 'Paused', value: 'paused' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
] as const;

@Component({
  selector: 'app-sessions',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, SessionListComponent, NewSessionWizardComponent],
  templateUrl: './sessions.component.html',
})
export class SessionsComponent {
  readonly worktreesService = inject(WorktreesService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  readonly view = signal<View>('list');
  readonly activeStatusFilters = signal<string[]>([]);
  readonly contextFilter = signal<string>('all');
  readonly preselectedWorktree = signal<string | null>(null);

  readonly statusChips = STATUS_CHIPS;

  readonly mainWorktree = this.worktreesService.mainWorktree;
  readonly repoPath = this.worktreesService.repoPath;
  readonly nonMainWorktrees = this.worktreesService.nonMainWorktrees;

  get filterWorktreePath(): string | null | undefined {
    const filter = this.contextFilter();
    if (filter === 'all') return undefined;
    if (filter === 'direct') return '';
    return filter;
  }

  constructor() {
    effect(() => {
      const params = this.route.snapshot.queryParams;
      if (params['new'] === 'true') {
        this.view.set('new');
        if (params['worktree']) {
          this.preselectedWorktree.set(params['worktree']);
        }
      }
    }, { allowSignalWrites: true });

    effect(() => {
      const currentView = this.view();
      const params = this.route.snapshot.queryParams;
      if (params['new'] === 'true' && currentView === 'new') {
        void this.router.navigate([], {
          relativeTo: this.route,
          queryParams: {},
          replaceUrl: true,
        });
      }
    });
  }

  setView(v: View): void {
    this.view.set(v);
    if (v === 'list') {
      this.preselectedWorktree.set(null);
    }
  }

  toggleStatusFilter(value: string): void {
    this.activeStatusFilters.update(prev =>
      prev.includes(value) ? prev.filter(v => v !== value) : [...prev, value]
    );
  }

  onContextFilterChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.contextFilter.set(value);
  }

  clearFilters(): void {
    this.activeStatusFilters.set([]);
    this.contextFilter.set('all');
  }

  chipButtonStyle(value: string): string {
    const active = this.activeStatusFilters().includes(value);
    return `
      padding: 2px 10px;
      border-radius: 100px;
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      border: ${active ? '1px solid var(--accent)' : '1px solid var(--border-default)'};
      background: ${active ? 'var(--accent-bg)' : 'transparent'};
      color: ${active ? 'var(--accent)' : 'var(--text-muted)'};
      transition: all var(--transition-fast);
    `.replace(/\n/g, ' ');
  }

  handleResume(runId: string): void {
    void this.router.navigate(['/runs', runId]);
  }
}

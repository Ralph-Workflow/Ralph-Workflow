import {
  Component,
  inject,
  signal,
  computed,
  effect,
  ChangeDetectionStrategy,
  ViewChild,
  ElementRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule, ActivatedRoute } from '@angular/router';
import { WorktreesService } from '../../services/worktrees.service';
import { SessionsService } from '../../services/sessions.service';
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
  host: {
    '(document:keydown)': 'onDocumentKeyDown($event)',
  },
})
export class SessionsComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly sessionsService = inject(SessionsService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  @ViewChild('searchInput') searchInput?: ElementRef<HTMLInputElement>;

  readonly view = signal<View>('list');
  readonly activeStatusFilters = signal<string[]>([]);
  readonly contextFilter = signal<string>('all');
  readonly preselectedWorktree = signal<string | null>(null);

  readonly searchTerm = signal('');

  readonly statusChips = STATUS_CHIPS;

  readonly statusCounts = computed(() => {
    const sessions = this.sessionsService.sessions();
    const counts = {
      all: sessions.length,
      running: 0,
      paused: 0,
      completed: 0,
      failed: 0,
    };
    for (const s of sessions) {
      if (s.status === 'running') counts.running++;
      else if (s.status === 'paused' || s.status === 'interrupted') counts.paused++;
      else if (s.status === 'completed') counts.completed++;
      else if (s.status === 'failed') counts.failed++;
    }
    return counts;
  });

  readonly statusChipsWithCount = computed(() =>
    STATUS_CHIPS.map(chip => ({
      ...chip,
      count: this.statusCounts()[chip.value] ?? 0,
    }))
  );

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

  onSearchInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.searchTerm.set(value);
  }

  clearFilters(): void {
    this.activeStatusFilters.set([]);
    this.contextFilter.set('all');
    this.searchTerm.set('');
  }

  get statusChipsWithCountList() { return this.statusChipsWithCount(); }
  get viewValue(): View { return this.view(); }
  get preselectedWorktreeValue(): string | null { return this.preselectedWorktree(); }
  get repoPathValue(): string | null | undefined { return this.repoPath(); }
  get searchTermValue(): string { return this.searchTerm(); }
  get activeStatusFiltersValue(): string[] { return this.activeStatusFilters(); }
  get contextFilterValue(): string { return this.contextFilter(); }
  get nonMainWorktreesList() { return this.nonMainWorktrees(); }
  get statusCountsValue() { return this.statusCounts(); }

  isFilterActive(value: string): boolean {
    return this.activeStatusFilters().includes(value);
  }

  handleResume(runId: string): void {
    void this.router.navigate(['/runs', runId]);
  }

  onDocumentKeyDown(event: KeyboardEvent): void {
    if (event.ctrlKey && event.key === 'f' && this.view() === 'list') {
      event.preventDefault();
      this.searchInput?.nativeElement.focus();
    }
  }
}

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
  template: `
    <div class="page-content">
      <!-- Header -->
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
        <h1 class="page-title" style="margin-bottom: 0;">Sessions</h1>
        <div style="display: flex; gap: 8px;">
          @if (view() === 'new') {
            <button class="btn btn-ghost" (click)="setView('list')">← Back to list</button>
          } @else {
            <button class="btn btn-primary" (click)="setView('new')">+ New session</button>
          }
        </div>
      </div>

      <div style="animation: fadeIn 200ms ease 40ms both;">
        @if (view() === 'new') {
          <div class="card" style="max-width: 620px;">
            <div style="font-family: var(--font-display); font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 20px; letter-spacing: -0.01em;">
              New session
            </div>
            <app-new-session-wizard
              [preselectedWorktreePath]="preselectedWorktree()"
              (onClose)="setView('list')"
            />
          </div>
        } @else {
          <div>
            <!-- Repo context -->
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: var(--space-4);">
              <div class="section-label" style="margin-bottom: 0;">
                @if (repoPath()) {
                  <span class="chip-mono">{{ repoPath() }}</span>
                } @else {
                  No repository selected
                }
              </div>
            </div>

            @if (repoPath()) {
              <!-- Filter toolbar -->
              <div
                data-testid="filter-toolbar"
                style="display: flex; align-items: center; gap: 8px; margin-bottom: var(--space-3); flex-wrap: wrap;"
              >
                <span style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); margin-right: 4px;">
                  Filter:
                </span>
                @for (chip of statusChips; track chip.value) {
                  <button
                    [attr.data-testid]="'filter-chip-' + chip.value"
                    (click)="toggleStatusFilter(chip.value)"
                    [style]="chipButtonStyle(chip.value)"
                  >
                    {{ chip.label }}
                  </button>
                }
                <!-- Context filter dropdown -->
                <select
                  data-testid="filter-context"
                  [value]="contextFilter()"
                  (change)="onContextFilterChange($event)"
                  style="margin-left: 8px; padding: 2px 8px; border-radius: var(--radius-sm); border: 1px solid var(--border-default); background: var(--bg-surface); color: var(--text-muted); font-size: 11px; font-family: var(--font-mono); cursor: pointer;"
                >
                  <option value="all">All contexts</option>
                  <option value="direct">Direct repo</option>
                  @for (wt of nonMainWorktrees(); track wt.path) {
                    <option [value]="wt.path">{{ wt.name }}</option>
                  }
                </select>
                <!-- Clear filters -->
                @if (activeStatusFilters().length > 0 || contextFilter() !== 'all') {
                  <button
                    data-testid="clear-filters"
                    class="btn btn-ghost"
                    style="font-size: 11px; padding: 2px 8px;"
                    (click)="clearFilters()"
                  >
                    Clear
                  </button>
                }
              </div>

              <!-- Session list -->
              <div class="card" style="padding: 4px 0;">
                <app-session-list
                  [repoPath]="repoPath()"
                  [filterStatus]="activeStatusFilters()"
                  [filterWorktreePath]="filterWorktreePath"
                  (resumeRun)="handleResume($event)"
                />
              </div>
            } @else {
              <div class="empty-state">
                <span class="empty-state-icon">⎇</span>
                <div class="empty-state-title">No repository context</div>
                <div class="empty-state-desc">Use the context switcher in the sidebar to select a repository.</div>
              </div>
            }
          </div>
        }
      </div>
    </div>
  `,
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
    // Check query params for initial view
    effect(() => {
      const params = this.route.snapshot.queryParams;
      if (params['new'] === 'true') {
        this.view.set('new');
        if (params['worktree']) {
          this.preselectedWorktree.set(params['worktree']);
        }
      }
    }, { allowSignalWrites: true });

    // Clear query params once consumed
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

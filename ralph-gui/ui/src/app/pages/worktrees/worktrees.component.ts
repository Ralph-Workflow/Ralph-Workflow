import { Component, inject, signal, computed, ChangeDetectionStrategy } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';

@Component({
  selector: 'app-worktrees',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule],
  template: `
    @if (!repoPath()) {
      <div class="page-content">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
          <h1 class="page-title" style="margin-bottom: 0;">Worktrees</h1>
        </div>
        <div class="empty-state">
          <span class="empty-state-icon">⎇</span>
          <div class="empty-state-title">No repository context</div>
          <div class="empty-state-desc">Use the context switcher in the sidebar to select a repository.</div>
        </div>
      </div>
    } @else {
      <div class="page-content">
        <!-- Header -->
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
          <h1 class="page-title" style="margin-bottom: 0;">Worktrees</h1>
          <div style="display: flex; gap: 8px;">
            @if (showCreate()) {
              <button class="btn btn-ghost" (click)="cancelCreate()">← Cancel</button>
            } @else {
              <button class="btn btn-primary" (click)="startCreate()">+ New worktree</button>
            }
          </div>
        </div>

        <div style="animation: fadeIn 200ms ease 40ms both;">
          <!-- Repo context -->
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: var(--space-4);">
            <div class="section-label" style="margin-bottom: 0;">
              <span class="chip-mono">{{ repoPath() }}</span>
            </div>
          </div>

          <!-- Create form -->
          @if (showCreate()) {
            <div class="card" style="max-width: 520px; margin-bottom: var(--space-5);">
              <div style="font-family: var(--font-display); font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 20px; letter-spacing: -0.01em;">
                New worktree
              </div>

              <div style="display: flex; flex-direction: column; gap: 14px;">
                <div>
                  <label class="form-label">Branch name</label>
                  <input
                    class="form-input"
                    type="text"
                    placeholder="wt-51-my-feature"
                    [value]="form().branch"
                    (input)="onBranchInput($event)"
                    (blur)="autoFillName()"
                  />
                  <div style="margin-top: 4px; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
                    Will be created from HEAD if it doesn't exist
                  </div>
                </div>

                <div>
                  <label class="form-label">
                    Worktree name
                    <span style="color: var(--text-muted); font-family: var(--font-mono); font-size: 10px;">(wt-N-slug format required)</span>
                  </label>
                  <input
                    class="form-input"
                    type="text"
                    [placeholder]="namePlaceholder"
                    [value]="form().name"
                    (input)="onNameInput($event)"
                  />
                </div>

                @if (createError()) {
                  <div style="padding: 10px 12px; background: rgba(248,81,73,0.08); border: 1px solid rgba(248,81,73,0.2); border-radius: var(--radius-md); color: var(--status-failed); font-size: 12px; font-family: var(--font-mono);">
                    {{ createError() }}
                  </div>
                }

                <button
                  class="btn btn-primary"
                  (click)="handleCreate()"
                  [disabled]="creating() || !form().branch || !form().name"
                  style="align-self: flex-start;"
                >
                  {{ creating() ? 'Creating...' : 'Create worktree' }}
                </button>
              </div>
            </div>
          }

          <!-- Worktree list -->
          @if (worktreesService.status() === 'loading') {
            <div style="padding: var(--space-8); text-align: center; color: var(--text-muted); font-size: 13px; font-family: var(--font-mono);">
              Loading worktrees...
            </div>
          }

          @if (worktreesService.status() === 'failed' && worktreesService.error()) {
            <div style="padding: 10px 14px; background: rgba(248,81,73,0.08); border: 1px solid rgba(248,81,73,0.2); border-radius: var(--radius-md); color: var(--status-failed); font-size: 12px; font-family: var(--font-mono);">
              {{ worktreesService.error() }}
            </div>
          }

          @if (worktreesService.status() !== 'loading' && worktreesService.worktrees().length === 0) {
            <div class="empty-state">
              <span class="empty-state-icon">⎇</span>
              <div class="empty-state-title">No worktrees</div>
              <div class="empty-state-desc">Create a worktree to run parallel agent sessions on separate branches.</div>
            </div>
          }

          @if (worktreesService.worktrees().length > 0) {
            <div class="card" style="padding: 4px 0;">
              @for (wt of worktreesService.worktrees(); track wt.path; let idx = $index) {
                <div
                  class="worktree-row"
                  [class.active]="isActive(wt)"
                  (mouseenter)="onRowHover($event)"
                  (mouseleave)="onRowLeave($event)"
                  style="display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: idx < worktreesService.worktrees().length - 1 ? '1px solid var(--border-subtle)' : 'none'; transition: background var(--transition-fast);"
                >
                  <!-- Active indicator -->
                  <div [style]="indicatorStyle(wt)"></div>

                  <!-- Info -->
                  <div style="flex: 1; min-width: 0;">
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
                      <span style="font-family: var(--font-mono); font-size: 13px; font-weight: 500; color: var(--text-primary);">
                        {{ wt.name }}
                      </span>
                      @if (wt.is_main) {
                        <span style="font-size: 10px; font-family: var(--font-mono); color: var(--accent); background: var(--accent-bg); padding: 1px 6px; border-radius: var(--radius-sm); border: 1px solid rgba(232,168,56,0.2);">
                          main
                        </span>
                      }
                      @if (wt.has_active_run) {
                        <span style="font-size: 10px; font-family: var(--font-mono); color: var(--status-running); background: var(--status-running-bg); padding: 1px 6px; border-radius: var(--radius-sm);">
                          active run
                        </span>
                      }
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                      <span style="font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);">
                        ⎇ {{ wt.branch }}
                      </span>
                      <span style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        {{ wt.path }}
                      </span>
                    </div>
                  </div>

                  <!-- Actions -->
                  <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
                    @if (isActive(wt)) {
                      <span style="font-size: 10px; font-family: var(--font-mono); color: var(--accent); padding: 2px 8px; border: 1px solid rgba(232,168,56,0.3); border-radius: var(--radius-sm); background: var(--accent-bg); letter-spacing: 0.04em;">
                        active context
                      </span>
                    }
                    @if (!wt.is_main) {
                      <button
                        [attr.data-testid]="'start-session-' + wt.name"
                        class="btn btn-secondary"
                        style="font-size: 11px; padding: 3px 10px;"
                        (click)="startSession(wt)"
                      >
                        Start session
                      </button>
                    }
                  </div>
                </div>
              }
            </div>
          }
        </div>
      </div>
    }
  `,
  styles: [`
    .worktree-row:hover {
      background: var(--bg-elevated);
    }
  `],
})
export class WorktreesComponent {
  readonly worktreesService = inject(WorktreesService);
  private readonly router = inject(Router);

  readonly showCreate = signal(false);
  readonly form = signal({ branch: '', name: '' });
  readonly createError = signal<string | null>(null);
  readonly creating = signal(false);

  readonly namePrefix = 'wt-';
  readonly namePlaceholder = 'wt-51-my-feature';

  readonly mainWorktree = computed(() =>
    this.worktreesService.worktrees().find(wt => wt.is_main)
  );

  readonly repoPath = computed(() =>
    this.mainWorktree()?.path ?? ''
  );

  isActive(wt: WorktreeInfo): boolean {
    const activePath = this.worktreesService.activeWorktreePath();
    return wt.path === activePath || (activePath === null && wt.is_main);
  }

  indicatorStyle(wt: WorktreeInfo): string {
    const active = this.isActive(wt);
    const color = active
      ? 'var(--accent)'
      : wt.has_active_run
        ? 'var(--status-running)'
        : 'var(--border-default)';
    return `width: 6px; height: 6px; border-radius: 50%; background: ${color}; flex-shrink: 0;`;
  }

  onRowHover(event: MouseEvent): void {
    (event.currentTarget as HTMLElement).style.background = 'var(--bg-elevated)';
  }

  onRowLeave(event: MouseEvent): void {
    (event.currentTarget as HTMLElement).style.background = 'transparent';
  }

  startCreate(): void {
    this.showCreate.set(true);
    this.createError.set(null);
  }

  cancelCreate(): void {
    this.showCreate.set(false);
    this.createError.set(null);
    this.form.set({ branch: '', name: '' });
  }

  onBranchInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.form.update(f => ({ ...f, branch: value }));
  }

  onNameInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.form.update(f => ({ ...f, name: value }));
  }

  autoFillName(): void {
    const form = this.form();
    if (form.branch && !form.name) {
      this.form.update(f => ({ ...f, name: f.branch }));
    }
  }

  async handleCreate(): Promise<void> {
    const repo = this.repoPath();
    if (!repo) return;

    const { branch, name } = this.form();
    if (!branch || !name) {
      this.createError.set('Branch and worktree name are required.');
      return;
    }

    this.createError.set(null);
    this.creating.set(true);

    try {
      await this.worktreesService.createWorktree(repo, branch, name);
      this.showCreate.set(false);
      this.form.set({ branch: '', name: '' });
      await this.worktreesService.fetchWorktrees(repo);
    } catch (e) {
      this.createError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.creating.set(false);
    }
  }

  startSession(wt: WorktreeInfo): void {
    void this.router.navigate(['/sessions'], {
      queryParams: { new: 'true', worktree: wt.path }
    });
  }
}

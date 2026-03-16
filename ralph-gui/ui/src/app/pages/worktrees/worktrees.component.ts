import { Component, inject, signal, computed, ChangeDetectionStrategy, ViewChild } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatMenuModule, MatMenuTrigger } from '@angular/material/menu';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { WorktreeInfo } from '../../types';

@Component({
  selector: 'app-worktrees',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, MatMenuModule, MatIconModule, MatDividerModule],
  templateUrl: './worktrees.component.html',
  styles: [`
    .worktree-row:hover {
      background: var(--bg-elevated);
    }
  `],
})
export class WorktreesComponent {
  @ViewChild('worktreeContextMenuTrigger') contextMenuTrigger!: MatMenuTrigger;

  readonly worktreesService = inject(WorktreesService);
  readonly tauriService = inject(TauriService);
  private readonly router = inject(Router);

  readonly showCreate = signal(false);
  readonly form = signal({ branch: '', name: '' });
  readonly createError = signal<string | null>(null);
  readonly creating = signal(false);

  readonly contextMenuWorktree = signal<WorktreeInfo | null>(null);
  readonly deleteConfirmWorktree = signal<WorktreeInfo | null>(null);

  readonly mainWorktree = computed(() =>
    this.worktreesService.worktrees().find(wt => wt.is_main)
  );

  readonly repoPath = computed(() =>
    this.mainWorktree()?.path ?? ''
  );

  readonly worktreesWithMeta = computed(() => {
    const activePath = this.worktreesService.activeWorktreePath();
    return this.worktreesService.worktrees().map(wt => {
      const active = wt.path === activePath || (activePath === null && wt.is_main);
      const color = active
        ? 'var(--accent)'
        : wt.has_active_run
          ? 'var(--status-running)'
          : 'var(--border-default)';
      return {
        ...wt,
        active,
        indicatorStyle: `width: 6px; height: 6px; border-radius: 50%; background: ${color}; flex-shrink: 0;`,
      };
    });
  });

  readonly namePlaceholder = 'wt-51-my-feature';

  get repoPathValue(): string { return this.repoPath(); }
  get showCreateValue(): boolean { return this.showCreate(); }
  get createErrorValue(): string | null { return this.createError(); }
  get creatingValue(): boolean { return this.creating(); }
  get formValue() { return this.form(); }
  get worktreesStatus() { return this.worktreesService.status(); }
  get worktreesError() { return this.worktreesService.error(); }
  get worktreesList() { return this.worktreesWithMeta(); }
  get contextMenuWorktreeValue(): WorktreeInfo | null { return this.contextMenuWorktree(); }
  get deleteConfirmWorktree_() { return this.deleteConfirmWorktree(); }
  get contextMenuCanStartSession(): boolean {
    const wt = this.contextMenuWorktree();
    return wt !== null && !wt.is_main;
  }

  isActive(wt: WorktreeInfo): boolean {
    const activePath = this.worktreesService.activeWorktreePath();
    return wt.path === activePath || (activePath === null && wt.is_main);
  }

  onRowHover(event: MouseEvent): void {
    (event.currentTarget as HTMLElement).style.background = 'var(--bg-elevated)';
  }

  onRowLeave(event: MouseEvent): void {
    (event.currentTarget as HTMLElement).style.background = 'transparent';
  }

  onContextMenu(event: MouseEvent, wt: WorktreeInfo): void {
    event.preventDefault();
    event.stopPropagation();
    this.contextMenuWorktree.set(wt);
    this.contextMenuTrigger.openMenu();
  }

  onContextMenuStartSession(): void {
    const wt = this.contextMenuWorktree();
    if (wt && !wt.is_main) {
      this.startSession(wt);
    }
    this.contextMenuWorktree.set(null);
  }

  async onContextMenuOpenInFileManager(): Promise<void> {
    const wt = this.contextMenuWorktree();
    if (wt) {
      await this.tauriService.openInFileManager(wt.path);
    }
    this.contextMenuWorktree.set(null);
  }

  onContextMenuViewDiff(): void {
    const wt = this.contextMenuWorktree();
    if (wt) {
      void this.router.navigate(['/sessions'], {
        queryParams: { new: 'true', worktree: wt.path }
      });
    }
    this.contextMenuWorktree.set(null);
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

  async openInFileManager(wt: WorktreeInfo): Promise<void> {
    await this.tauriService.openInFileManager(wt.path);
  }

  confirmDeleteWorktree(wt: WorktreeInfo): void {
    this.deleteConfirmWorktree.set(wt);
  }

  cancelDeleteWorktree(): void {
    this.deleteConfirmWorktree.set(null);
  }

  async executeDeleteWorktree(): Promise<void> {
    const wt = this.deleteConfirmWorktree();
    if (!wt) return;

    // TODO: Implement actual delete logic
    console.log('Deleting worktree:', wt.path);
    this.deleteConfirmWorktree.set(null);
  }
}

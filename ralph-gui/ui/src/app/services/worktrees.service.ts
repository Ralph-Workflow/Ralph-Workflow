import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { WorktreeInfo } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

@Injectable({ providedIn: 'root' })
export class WorktreesService {
  private readonly tauri = inject(TauriService);

  // State signals — no localStorage; persistence handled by WorkspaceService via Tauri
  readonly worktrees = signal<WorktreeInfo[]>([]);
  readonly status = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);
  readonly activeWorktreePath = signal<string | null>(null);
  readonly lastRepoPath = signal<string | null>(null);

  // Computed signals
  readonly isLoading = computed(() => this.status() === 'loading');
  readonly mainWorktree = computed(() =>
    this.worktrees().find(wt => wt.is_main) ?? null
  );
  readonly repoPath = computed(() =>
    this.mainWorktree()?.path ?? ''
  );
  readonly nonMainWorktrees = computed(() =>
    this.worktrees().filter(wt => !wt.is_main)
  );

  async fetchWorktrees(repoPath: string): Promise<void> {
    this.status.set('loading');
    this.error.set(null);

    try {
      const worktrees = await this.tauri.listWorktrees(repoPath);
      this.worktrees.set(worktrees);
      this.status.set('succeeded');
    } catch (e) {
      this.status.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async createWorktree(
    repoPath: string,
    branch: string,
    name: string,
    basePath?: string,
  ): Promise<WorktreeInfo> {
    try {
      const result = await this.tauri.createWorktree(repoPath, branch, name, basePath);
      this.worktrees.update((wt) => [...wt, result.worktree]);
      return result.worktree;
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  async switchContext(repoPath: string, worktreePath: string | null): Promise<void> {
    await this.tauri.switchContext(repoPath, worktreePath);
    this.activeWorktreePath.set(worktreePath);
  }

  async initializeRepo(repoPath: string): Promise<void> {
    this.status.set('loading');
    this.error.set(null);

    try {
      const worktrees = await this.tauri.listWorktrees(repoPath);
      this.worktrees.set(worktrees);
      this.lastRepoPath.set(repoPath);
      this.status.set('succeeded');
    } catch (e) {
      this.status.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  setActiveWorktree(path: string | null): void {
    this.activeWorktreePath.set(path);
  }
}

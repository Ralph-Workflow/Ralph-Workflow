import { Injectable, signal, computed, inject } from '@angular/core';
import { TauriService } from './tauri.service';
import { PreferencesService } from './preferences.service';
import type { WorkspaceEntry } from '../types';

export interface WorkspaceRunSummary {
  running: number;
  failed: number;
  paused: number;
}

export interface Workspace {
  id: string;
  path: string;
  label: string;
  activeWorktree: string | null;
  runSummary: WorkspaceRunSummary;
  navigationState: string | null;
  activeRunCount: number;
}

function entryToWorkspace(entry: WorkspaceEntry): Workspace {
  return {
    id: entry.id,
    path: entry.repo_path,
    label: entry.display_name,
    activeWorktree: null,
    runSummary: {
      running: entry.active_run_count,
      failed: 0,
      paused: 0,
    },
    navigationState: entry.last_nav || null,
    activeRunCount: entry.active_run_count,
  };
}

@Injectable({
  providedIn: 'root',
})
export class WorkspaceService {
  private readonly tauri = inject(TauriService);
  private readonly preferencesService = inject(PreferencesService);

  readonly workspaces = signal<Workspace[]>([]);
  readonly activeWorkspaceId = signal<string | null>(null);
  readonly isLoading = signal<boolean>(true);

  readonly activeWorkspace = computed(() => {
    const id = this.activeWorkspaceId();
    const list = this.workspaces();
    return list.find(w => w.id === id) ?? null;
  });

  constructor() {
    void this.loadFromBackend();
  }

  private async loadFromBackend(): Promise<void> {
    this.isLoading.set(true);
    try {
      const restoreWorkspaces = this.preferencesService.preferences().session?.restoreWorkspaces ?? true;

      if (!restoreWorkspaces) {
        // User has opted out of workspace restoration; start fresh.
        this.workspaces.set([]);
        return;
      }

      const entries = await this.tauri.getWorkspaces();
      const workspaces = entries.map(entryToWorkspace);
      this.workspaces.set(workspaces);

      if (workspaces.length > 0 && !this.activeWorkspaceId()) {
        const firstWorkspace = workspaces[0];
        if (firstWorkspace) {
          this.activeWorkspaceId.set(firstWorkspace.id);
        }
      }
    } catch (err) {
      console.warn('Failed to load workspaces from backend:', err);
      this.workspaces.set([]);
    } finally {
      this.isLoading.set(false);
    }
  }

  async openWorkspace(path: string): Promise<Workspace> {
    // Check for existing workspace with the same path to prevent duplicates.
    const existingByPath = this.workspaces().find(w => w.path === path);
    if (existingByPath) {
      this.activeWorkspaceId.set(existingByPath.id);
      return existingByPath;
    }

    try {
      const entry = await this.tauri.openWorkspace(path);
      const workspace = entryToWorkspace(entry);

      const existing = this.workspaces().find(w => w.id === workspace.id);
      if (!existing) {
        this.workspaces.update(list => [workspace, ...list]);
      }

      this.activeWorkspaceId.set(workspace.id);
      return workspace;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Failed to open workspace: ${msg}`);
    }
  }

  /**
   * Close a workspace.
   *
   * @param id       - The workspace ID to close.
   * @param force    - When true, skip the active-runs guard and close regardless.
   *                   Use this when the caller has already confirmed with the user
   *                   (e.g. after the CancelConfirmationComponent dialog).
   *                   When false (the default), throws if the workspace has active runs.
   */
  async closeWorkspace(id: string, force = false): Promise<void> {
    const workspace = this.workspaces().find(w => w.id === id);
    if (!workspace) return;

    if (!force && workspace.runSummary.running > 0) {
      throw new Error(
        `Cannot close workspace "${workspace.label}" with ${workspace.runSummary.running} active run(s)`,
      );
    }

    try {
      await this.tauri.closeWorkspace(id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(msg);
    }

    this.workspaces.update(list => list.filter(w => w.id !== id));

    if (this.activeWorkspaceId() === id) {
      const remaining = this.workspaces();
      const firstRemaining = remaining[0];
      this.activeWorkspaceId.set(firstRemaining?.id ?? null);
    }
  }

  switchWorkspace(id: string): void {
    const list = this.workspaces();
    const workspace = list.find(w => w.id === id);
    if (workspace) {
      this.activeWorkspaceId.set(id);
    }
  }

  async persistNavigation(id: string, nav: string): Promise<void> {
    try {
      await this.tauri.setWorkspaceNav(id, nav);
      this.workspaces.update(list =>
        list.map(w => (w.id === id ? { ...w, navigationState: nav } : w)),
      );
    } catch {
      // Non-critical — navigation state is best-effort
    }
  }

  async reorderWorkspaces(ids: string[]): Promise<void> {
    try {
      await this.tauri.reorderWorkspaces(ids);
      // Reorder local signal to match
      const current = this.workspaces();
      const reordered = ids
        .map(id => current.find(w => w.id === id))
        .filter((w): w is Workspace => w !== undefined);
      this.workspaces.set(reordered);
    } catch (err) {
      console.warn('Failed to reorder workspaces:', err);
    }
  }

  updateWorkspaceRunSummary(id: string, summary: Partial<WorkspaceRunSummary>): void {
    this.workspaces.update(list =>
      list.map(w =>
        w.id === id
          ? { ...w, runSummary: { ...w.runSummary, ...summary } }
          : w,
      ),
    );
  }

  setActiveWorktree(id: string, worktreePath: string | null): void {
    this.workspaces.update(list =>
      list.map(w => (w.id === id ? { ...w, activeWorktree: worktreePath } : w)),
    );
  }

  setNavigationState(id: string, navState: string | null): void {
    this.workspaces.update(list =>
      list.map(w => (w.id === id ? { ...w, navigationState: navState } : w)),
    );
  }

  async getRecentWorkspaces(): Promise<string[]> {
    try {
      return await this.tauri.getRecentWorkspaces();
    } catch {
      return [];
    }
  }

  async updateRunCount(id: string, count: number): Promise<void> {
    try {
      await this.tauri.updateWorkspaceRunCount(id, count);
      this.workspaces.update(list =>
        list.map(w =>
          w.id === id
            ? { ...w, activeRunCount: count, runSummary: { ...w.runSummary, running: count } }
            : w,
        ),
      );
    } catch {
      // Non-critical
    }
  }
}

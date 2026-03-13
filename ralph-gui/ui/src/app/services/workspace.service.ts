import { Injectable, signal, computed, effect } from '@angular/core';

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
}

const WORKSPACES_KEY = 'ralph-workspaces';

function generateId(): string {
  return `ws-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function extractLabel(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

@Injectable({
  providedIn: 'root',
})
export class WorkspaceService {
  readonly workspaces = signal<Workspace[]>([]);
  readonly activeWorkspaceId = signal<string | null>(null);

  readonly activeWorkspace = computed(() => {
    const id = this.activeWorkspaceId();
    const list = this.workspaces();
    return list.find(w => w.id === id) ?? null;
  });

  constructor() {
    this.loadFromStorage();
    
    effect(() => {
      const data = this.workspaces();
      this.saveToStorage(data);
    });
  }

  openWorkspace(path: string): Workspace {
    const existing = this.workspaces().find(w => w.path === path);
    if (existing) {
      this.activeWorkspaceId.set(existing.id);
      return existing;
    }

    const newWorkspace: Workspace = {
      id: generateId(),
      path,
      label: extractLabel(path),
      activeWorktree: null,
      runSummary: { running: 0, failed: 0, paused: 0 },
      navigationState: null,
    };

    this.workspaces.update(list => [...list, newWorkspace]);
    this.activeWorkspaceId.set(newWorkspace.id);

    return newWorkspace;
  }

  closeWorkspace(id: string): void {
    const list = this.workspaces();
    const index = list.findIndex(w => w.id === id);
    if (index === -1) return;

    this.workspaces.update(wlist => {
      const updated = [...wlist];
      updated.splice(index, 1);
      return updated;
    });

    if (this.activeWorkspaceId() === id) {
      const remaining = this.workspaces();
      const firstRemaining = remaining[0];
      if (firstRemaining) {
        this.activeWorkspaceId.set(firstRemaining.id);
      } else {
        this.activeWorkspaceId.set(null);
      }
    }
  }

  switchWorkspace(id: string): void {
    const list = this.workspaces();
    const workspace = list.find(w => w.id === id);
    if (workspace) {
      this.activeWorkspaceId.set(id);
    }
  }

  updateWorkspaceRunSummary(id: string, summary: Partial<WorkspaceRunSummary>): void {
    this.workspaces.update(list =>
      list.map(w =>
        w.id === id
          ? { ...w, runSummary: { ...w.runSummary, ...summary } }
          : w
      )
    );
  }

  setActiveWorktree(id: string, worktreePath: string | null): void {
    this.workspaces.update(list =>
      list.map(w =>
        w.id === id
          ? { ...w, activeWorktree: worktreePath }
          : w
      )
    );
  }

  setNavigationState(id: string, navState: string | null): void {
    this.workspaces.update(list =>
      list.map(w =>
        w.id === id
          ? { ...w, navigationState: navState }
          : w
      )
    );
  }

  private loadFromStorage(): void {
    if (typeof localStorage === 'undefined') return;

    try {
      const raw = localStorage.getItem(WORKSPACES_KEY);
      if (!raw) return;

      const data = JSON.parse(raw) as Workspace[];
      if (Array.isArray(data) && data.length > 0) {
        this.workspaces.set(data);
        
        if (!this.activeWorkspaceId()) {
          const firstWorkspace = data[0];
          if (firstWorkspace) {
            this.activeWorkspaceId.set(firstWorkspace.id);
          }
        }
      }
    } catch {
      console.warn('Failed to load workspaces from localStorage');
    }
  }

  private saveToStorage(data: Workspace[]): void {
    if (typeof localStorage === 'undefined') return;

    try {
      localStorage.setItem(WORKSPACES_KEY, JSON.stringify(data));
    } catch {
      console.warn('Failed to save workspaces to localStorage');
    }
  }
}

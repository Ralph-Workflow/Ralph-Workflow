import { Component, inject, ChangeDetectionStrategy, computed } from '@angular/core';
import { WorkspaceService } from '../../services/workspace.service';
import { NotificationService } from '../../services/notification.service';
import { WorktreesService } from '../../services/worktrees.service';

export type ConnectionState = 'connected' | 'connecting' | 'disconnected';

@Component({
  selector: 'app-status-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  templateUrl: './status-bar.component.html',
  styleUrls: ['./status-bar.component.css'],
})
export class StatusBarComponent {
  private readonly workspaceService = inject(WorkspaceService);
  private readonly notificationService = inject(NotificationService);
  private readonly worktreesService = inject(WorktreesService);

  /** Active workspace label for the left section. */
  readonly workspaceLabel = computed(() => {
    const ws = this.workspaceService.activeWorkspace();
    return ws?.label ?? 'No workspace';
  });

  /**
   * Aggregated run summary across ALL workspaces.
   * Shows "N running, M paused" format.
   */
  readonly runSummaryText = computed(() => {
    const workspaces = this.workspaceService.workspaces();
    let running = 0;
    let paused = 0;
    for (const ws of workspaces) {
      running += ws.runSummary.running;
      paused += ws.runSummary.paused;
    }
    const parts: string[] = [];
    if (running > 0) parts.push(`${running} running`);
    if (paused > 0) parts.push(`${paused} paused`);
    return parts.join(', ');
  });

  /**
   * Current branch: branch of the active worktree if one is selected,
   * otherwise the main worktree's branch.
   */
  readonly currentBranchSignal = computed(() => {
    const worktrees = this.worktreesService.worktrees();
    const activePath = this.worktreesService.activeWorktreePath();
    if (activePath) {
      const active = worktrees.find(wt => wt.path === activePath);
      if (active) return active.branch;
    }
    const main = worktrees.find(wt => wt.is_main);
    return main?.branch ?? '';
  });

  /**
   * Connection state derived from workspace loading state.
   * - 'connecting': workspace is loading
   * - 'connected': workspace loaded successfully and active workspace exists
   * - 'disconnected': no workspaces or loading failed
   */
  readonly connectionState = computed<ConnectionState>(() => {
    const isLoading = this.workspaceService.isLoading();
    const workspaces = this.workspaceService.workspaces();
    const activeWorkspace = this.workspaceService.activeWorkspace();

    if (isLoading) {
      return 'connecting';
    }

    if (workspaces.length === 0) {
      return 'disconnected';
    }

    return activeWorkspace ? 'connected' : 'disconnected';
  });

  readonly connectionStatus = computed(() => {
    const state = this.connectionState();
    switch (state) {
      case 'connecting': return 'Connecting';
      case 'connected': return 'Connected';
      case 'disconnected': return 'Disconnected';
    }
  });

  readonly connectionStatusClass = computed(() => {
    const state = this.connectionState();
    switch (state) {
      case 'connecting': return 'status-connecting';
      case 'connected': return 'status-connected';
      case 'disconnected': return 'status-disconnected';
    }
  });

  readonly connectionAriaLabel = computed(() => {
    const state = this.connectionState();
    switch (state) {
      case 'connecting': return 'Connection status: connecting';
      case 'connected': return 'Connection status: connected';
      case 'disconnected': return 'Connection status: disconnected';
    }
  });

  /** Getters for template read access (avoids calling signals/computed with () in templates). */
  get currentWorkspaceLabel() { return this.workspaceLabel(); }
  get currentRunSummaryText() { return this.runSummaryText(); }
  get currentNotificationCount() { return this.notificationService.unreadCount(); }
  get currentBranch() { return this.currentBranchSignal(); }
  get currentConnectionStatus() { return this.connectionStatus(); }
  get currentConnectionStatusClass() { return this.connectionStatusClass(); }
  get currentConnectionAriaLabel() { return this.connectionAriaLabel(); }

  onNotificationsClick(): void {
    this.notificationService.togglePanel();
  }
}

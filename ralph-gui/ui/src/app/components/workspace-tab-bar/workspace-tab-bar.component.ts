import { Component, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceService } from '../../services/workspace.service';
import { TauriService } from '../../services/tauri.service';

@Component({
  selector: 'app-workspace-tab-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './workspace-tab-bar.component.html',
  styleUrls: ['./workspace-tab-bar.component.css'],
})
export class WorkspaceTabBarComponent {
  readonly workspaceService = inject(WorkspaceService);
  readonly tauri = inject(TauriService);

  readonly workspaces = this.workspaceService.workspaces;
  readonly activeWorkspaceId = this.workspaceService.activeWorkspaceId;

  isActive(id: string): boolean {
    return this.activeWorkspaceId() === id;
  }

  hasActiveRuns(summary: { running: number; failed: number; paused: number }): boolean {
    return summary.running > 0;
  }

  runCount(summary: { running: number; failed: number; paused: number }): number {
    return summary.running;
  }

  switchTo(id: string): void {
    this.workspaceService.switchWorkspace(id);
  }

  async close(id: string): Promise<void> {
    const workspace = this.workspaces().find(w => w.id === id);
    if (workspace && workspace.runSummary.running > 0) {
      const confirmed = confirm(
        `Workspace "${workspace.label}" has ${workspace.runSummary.running} active run(s). Close anyway?`
      );
      if (!confirmed) return;
    }
    this.workspaceService.closeWorkspace(id);
  }

  async addWorkspace(): Promise<void> {
    const path = await this.tauri.openDirectoryDialog();
    if (path) {
      this.workspaceService.openWorkspace(path);
    }
  }
}

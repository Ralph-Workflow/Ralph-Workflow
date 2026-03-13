import { Component, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import {
  CdkDragDrop,
  CdkDropList,
  CdkDrag,
  moveItemInArray,
} from '@angular/cdk/drag-drop';
import { WorkspaceService } from '../../services/workspace.service';
import { TauriService } from '../../services/tauri.service';

@Component({
  selector: 'app-workspace-tab-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, CdkDropList, CdkDrag],
  templateUrl: './workspace-tab-bar.component.html',
  styleUrls: ['./workspace-tab-bar.component.css'],
})
export class WorkspaceTabBarComponent {
  readonly workspaceService = inject(WorkspaceService);
  readonly tauri = inject(TauriService);

  private readonly _workspaces = this.workspaceService.workspaces;
  private readonly _activeWorkspaceId = this.workspaceService.activeWorkspaceId;

  get workspaces() { return this._workspaces(); }
  get activeWorkspaceId() { return this._activeWorkspaceId(); }

  switchTo(id: string): void {
    this.workspaceService.switchWorkspace(id);
  }

  onTabMouseUp(event: MouseEvent, id: string): void {
    // Middle-click (button === 1) closes the tab
    if (event.button === 1) {
      event.preventDefault();
      void this.close(id);
    }
  }

  async close(id: string): Promise<void> {
    const workspace = this._workspaces().find(w => w.id === id);
    if (workspace && workspace.runSummary.running > 0) {
      const confirmed = confirm(
        `Workspace "${workspace.label}" has ${workspace.runSummary.running} active run(s). Close anyway?`,
      );
      if (!confirmed) return;
    }
    try {
      await this.workspaceService.closeWorkspace(id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      alert(msg);
    }
  }

  async addWorkspace(): Promise<void> {
    const path = await this.tauri.openDirectoryDialog();
    if (path) {
      try {
        await this.workspaceService.openWorkspace(path);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        alert(msg);
      }
    }
  }

  async onDrop(event: CdkDragDrop<ReturnType<typeof this._workspaces>>): Promise<void> {
    if (event.previousIndex === event.currentIndex) return;

    const list = [...this._workspaces()];
    moveItemInArray(list, event.previousIndex, event.currentIndex);

    const ids = list.map(w => w.id);
    await this.workspaceService.reorderWorkspaces(ids);
  }
}

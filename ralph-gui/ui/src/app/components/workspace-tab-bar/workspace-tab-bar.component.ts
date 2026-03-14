import { Component, inject, ChangeDetectionStrategy, signal, computed } from '@angular/core';
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
import { NotificationService } from '../../services/notification.service';
import { CancelConfirmationComponent } from '../cancel-confirmation/cancel-confirmation.component';

@Component({
  selector: 'app-workspace-tab-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, CdkDropList, CdkDrag, CancelConfirmationComponent],
  templateUrl: './workspace-tab-bar.component.html',
  styleUrls: ['./workspace-tab-bar.component.css'],
})
export class WorkspaceTabBarComponent {
  readonly workspaceService = inject(WorkspaceService);
  readonly tauri = inject(TauriService);
  private readonly notificationService = inject(NotificationService);

  private readonly _workspaces = this.workspaceService.workspaces;
  private readonly _activeWorkspaceId = this.workspaceService.activeWorkspaceId;

  /** ID of workspace pending close confirmation, or null if no dialog is shown. */
  readonly closeConfirmId = signal<string | null>(null);

  /** Message to show in the close confirmation dialog. */
  readonly closeConfirmMessage = computed(() => {
    const id = this.closeConfirmId();
    if (!id) return '';
    const ws = this._workspaces().find(w => w.id === id);
    if (!ws) return 'This workspace has active runs. Close anyway?';
    return `Workspace "${ws.label}" has ${ws.runSummary.running} active run(s). Closing will not stop them. Close anyway?`;
  });

  get workspaces() { return this._workspaces(); }
  get activeWorkspaceId() { return this._activeWorkspaceId(); }

  /** Getter proxy to avoid calling signal in template (lint: no-call-expression). */
  get closeConfirmIdValue(): string | null { return this.closeConfirmId(); }

  /** Getter proxy to avoid calling computed in template (lint: no-call-expression). */
  get closeConfirmMessageValue(): string { return this.closeConfirmMessage(); }

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
      // Show non-blocking confirmation dialog instead of window.confirm
      this.closeConfirmId.set(id);
      return;
    }
    await this.executeClose(id);
  }

  onCloseConfirmed(confirmed: boolean, id: string): void {
    this.closeConfirmId.set(null);
    if (confirmed) {
      // Pass force=true because the user already confirmed closing despite active runs.
      void this.executeClose(id, true);
    }
  }

  private async executeClose(id: string, force = false): Promise<void> {
    try {
      await this.workspaceService.closeWorkspace(id, force);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.notificationService.add({ type: 'error', message: msg });
    }
  }

  async addWorkspace(): Promise<void> {
    const path = await this.tauri.openDirectoryDialog();
    if (path) {
      try {
        await this.workspaceService.openWorkspace(path);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.notificationService.add({ type: 'error', message: msg });
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

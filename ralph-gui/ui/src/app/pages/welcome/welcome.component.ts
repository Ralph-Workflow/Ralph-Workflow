import { Component, ChangeDetectionStrategy, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { WorkspaceService } from '../../services/workspace.service';
import { TauriService } from '../../services/tauri.service';

@Component({
  selector: 'app-welcome',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule],
  templateUrl: './welcome.component.html',
  styleUrls: ['./welcome.component.css'],
})
export class WelcomeComponent implements OnInit {
  private readonly workspaceService = inject(WorkspaceService);
  private readonly tauri = inject(TauriService);

  readonly recentWorkspaces = signal<string[]>([]);
  readonly isOpening = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  get errorMessageValue(): string | null { return this.errorMessage(); }
  get isOpeningValue(): boolean { return this.isOpening(); }
  get recentWorkspacesList(): string[] { return this.recentWorkspaces(); }

  ngOnInit(): void {
    void this.workspaceService.getRecentWorkspaces().then(recent => {
      this.recentWorkspaces.set(recent);
    });
  }

  async openWorkspace(): Promise<void> {
    this.errorMessage.set(null);
    const path = await this.tauri.openDirectoryDialog();
    if (path) {
      await this.openPath(path);
    }
  }

  async openRecentWorkspace(path: string): Promise<void> {
    await this.openPath(path);
  }

  private async openPath(path: string): Promise<void> {
    this.isOpening.set(true);
    this.errorMessage.set(null);
    try {
      await this.workspaceService.openWorkspace(path);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.errorMessage.set(msg);
    } finally {
      this.isOpening.set(false);
    }
  }
}

import { Component, Output, EventEmitter, signal, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';

@Component({
  selector: 'app-repo-selector',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './repo-selector.component.html',
})
export class RepoSelectorComponent {
  private readonly worktreesService = inject(WorktreesService);

  @Output() repoSelected = new EventEmitter<string>();

  // Use service signal for last repo path (backed by Tauri via workspace service)
  private readonly _repoPath = signal(this.worktreesService.lastRepoPath() ?? '');
  private readonly _loading = signal(false);
  private readonly _error = signal<string | null>(null);

  /** Getters so the template accesses state without calling signals directly. */
  get repoPath() { return this._repoPath(); }
  get loading() { return this._loading(); }
  get error() { return this._error(); }
  /** Pre-trimmed path for use in template conditions — avoids calling .trim() in template. */
  get repoPathTrimmed() { return this._repoPath().trim(); }

  onInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    this._repoPath.set(input.value);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      void this.handleConfirm();
    }
  }

  async handleConfirm(): Promise<void> {
    const path = this._repoPath().trim();
    if (!path) return;

    this._loading.set(true);
    this._error.set(null);

    try {
      await this.worktreesService.initializeRepo(path);
      this.repoSelected.emit(path);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this._error.set(msg);
    } finally {
      this._loading.set(false);
    }
  }
}

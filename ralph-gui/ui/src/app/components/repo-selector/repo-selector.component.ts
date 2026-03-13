import { Component, Output, EventEmitter, signal, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';

@Component({
  selector: 'app-repo-selector',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div data-testid="repo-selector" style="display: flex; flex-direction: column; gap: 12px; max-width: 480px;">
      <div style="display: flex; gap: 8px;">
        <input
          data-testid="repo-path-input"
          class="input input-mono"
          style="flex: 1;"
          [value]="repoPath()"
          (input)="onInput($event)"
          (keydown)="onKeydown($event)"
          placeholder="/path/to/your/git/repository"
        />
        <button
          data-testid="open-repo-button"
          class="btn btn-primary"
          style="flex-shrink: 0;"
          (click)="handleConfirm()"
          [disabled]="loading() || !repoPath().trim()"
        >
          {{ loading() ? 'Opening…' : 'Open' }}
        </button>
      </div>
      @if (error()) {
        <div
          data-testid="repo-error"
          style="padding: 8px 12px; background: rgba(248,81,73,0.08); border: 1px solid rgba(248,81,73,0.2); border-radius: var(--radius-md); color: var(--status-failed); font-size: 12px; font-family: var(--font-mono);"
        >
          {{ error() }}
        </div>
      }
    </div>
  `,
})
export class RepoSelectorComponent {
  private readonly worktreesService = inject(WorktreesService);

  @Output() repoSelected = new EventEmitter<string>();

  readonly repoPath = signal(this.loadLastRepo());
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  private loadLastRepo(): string {
    if (typeof localStorage === 'undefined') return '';
    return localStorage.getItem('ralph_gui_last_repo') ?? '';
  }

  onInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.repoPath.set(input.value);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      void this.handleConfirm();
    }
  }

  async handleConfirm(): Promise<void> {
    const path = this.repoPath().trim();
    if (!path) return;

    this.loading.set(true);
    this.error.set(null);

    try {
      await this.worktreesService.initializeRepo(path);
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('ralph_gui_last_repo', path);
      }
      this.repoSelected.emit(path);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.error.set(msg);
    } finally {
      this.loading.set(false);
    }
  }
}

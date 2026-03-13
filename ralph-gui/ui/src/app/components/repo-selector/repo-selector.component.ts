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

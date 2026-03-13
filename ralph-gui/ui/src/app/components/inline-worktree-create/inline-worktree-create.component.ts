import { Component, Input, Output, EventEmitter, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';

@Component({
  selector: 'app-inline-worktree-create',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './inline-worktree-create.component.html',
})
export class InlineWorktreeCreateComponent {
  private readonly worktreesService = inject(WorktreesService);

  @Input() repoPath = '';
  @Output() created = new EventEmitter<WorktreeInfo>();

  readonly branch = signal('');
  readonly name = signal('');
  readonly error = signal<string | null>(null);
  readonly creating = signal(false);

  onBranchInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.branch.set(value);
  }

  onNameInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.name.set(value);
  }

  onBranchBlur(): void {
    if (this.branch() && !this.name()) {
      this.name.set(this.branch());
    }
  }

  async handleCreate(): Promise<void> {
    if (!this.repoPath || !this.branch() || !this.name()) return;

    this.creating.set(true);
    this.error.set(null);

    try {
      const worktree = await this.worktreesService.createWorktree(
        this.repoPath,
        this.branch(),
        this.name()
      );
      await this.worktreesService.fetchWorktrees(this.repoPath);
      this.created.emit(worktree);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.creating.set(false);
    }
  }
}

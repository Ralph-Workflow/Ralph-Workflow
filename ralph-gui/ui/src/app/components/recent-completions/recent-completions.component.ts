import { Component, input, output, ChangeDetectionStrategy, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import type { SessionSummary } from '../../types';

interface DisplayCompletion extends SessionSummary {
  run_id_short: string;
  relativeTime: string;
}

@Component({
  selector: 'app-recent-completions',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule],
  templateUrl: './recent-completions.component.html',
})
export class RecentCompletionsComponent {
  readonly completions = input<SessionSummary[]>([]);
  readonly viewRun = output<string>();

  private readonly displayCompletionsSignal = computed<DisplayCompletion[]>(() =>
    this.completions().map(completion => ({
      ...completion,
      run_id_short: completion.run_id.substring(0, 16),
      relativeTime: this.formatRelativeTime(completion.created_at),
    }))
  );

  private readonly showViewAllSignal = computed(() => this.completions().length > 5);

  get completionsCount(): number {
    return this.completions().length;
  }

  get displayCompletionsValue(): DisplayCompletion[] {
    return this.displayCompletionsSignal();
  }

  get showViewAllValue(): boolean {
    return this.showViewAllSignal();
  }

  private formatRelativeTime(isoString: string): string {
    const date = new Date(isoString);
    const now = Date.now();
    const diffMs = now - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);

    if (diffSeconds < 10) return 'just now';
    if (diffSeconds < 60) return `${diffSeconds}s ago`;

    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) return `${diffMinutes}m ago`;

    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}h ago`;

    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }

  onViewClick(runId: string): void {
    this.viewRun.emit(runId);
  }
}

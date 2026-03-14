import { Component, input, output, computed, ChangeDetectionStrategy } from '@angular/core';
import { NgClass } from '@angular/common';
import type { IterationSummary } from '../../types';

/** Formats a duration in seconds into a human-readable string like "4m 12s". */
function formatDuration(secs: number): string {
  const mins = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  if (mins === 0) return `${s}s`;
  return `${mins}m ${s}s`;
}

/** Enriched display item for a single iteration row. */
export interface IterationDisplayItem {
  iteration: IterationSummary;
  formattedDuration: string | null;
  isActive: boolean;
  statusLabel: string;
}

@Component({
  selector: 'app-iteration-history',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [NgClass],
  templateUrl: './iteration-history.component.html',
  styleUrl: './iteration-history.component.css',
})
export class IterationHistoryComponent {
  readonly iterations = input<IterationSummary[]>([]);
  readonly currentIteration = input<number | null>(null);
  readonly iterationClick = output<number>();

  readonly displayItems = computed<IterationDisplayItem[]>(() =>
    this.iterations().map(it => ({
      iteration: it,
      formattedDuration: it.duration_secs != null ? formatDuration(it.duration_secs) : null,
      isActive: this.currentIteration() === it.iteration_number,
      statusLabel: it.status.toLowerCase(),
    })),
  );

  get displayItems_(): IterationDisplayItem[] { return this.displayItems(); }
  get hasIterations_(): boolean { return this.iterations().length > 0; }

  onFilesClick(iterationNumber: number): void {
    this.iterationClick.emit(iterationNumber);
  }
}

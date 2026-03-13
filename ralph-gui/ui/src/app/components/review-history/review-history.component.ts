import { Component, input, computed, ChangeDetectionStrategy } from '@angular/core';
import type { ReviewSummary } from '../../types';

/** Formats a duration in seconds into a human-readable string like "45s" or "1m 30s". */
function formatDuration(secs: number): string {
  const mins = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  if (mins === 0) return `${s}s`;
  return `${mins}m ${s}s`;
}

/** Enriched display item for a single review row. */
export interface ReviewDisplayItem {
  review: ReviewSummary;
  formattedDuration: string | null;
  findingsLabel: string;
  statusLabel: string;
}

@Component({
  selector: 'app-review-history',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './review-history.component.html',
  styleUrl: './review-history.component.css',
})
export class ReviewHistoryComponent {
  readonly reviews = input<ReviewSummary[]>([]);

  readonly displayItems = computed<ReviewDisplayItem[]>(() =>
    this.reviews().map(rv => ({
      review: rv,
      formattedDuration: rv.duration_secs != null ? formatDuration(rv.duration_secs) : null,
      findingsLabel: rv.findings_count === 1 ? '1 finding' : `${rv.findings_count} findings`,
      statusLabel: rv.status.toLowerCase(),
    })),
  );

  get displayItems_(): ReviewDisplayItem[] { return this.displayItems(); }
  get hasReviews_(): boolean { return this.reviews().length > 0; }
}

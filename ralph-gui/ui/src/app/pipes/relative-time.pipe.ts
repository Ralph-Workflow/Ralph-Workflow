import { Pipe, PipeTransform } from '@angular/core';

/**
 * Pure pipe that converts a Date to a human-readable relative time string
 * (e.g. "just now", "2m ago", "3h ago").
 */
@Pipe({
  name: 'relativeTime',
  standalone: true,
  pure: true,
})
export class RelativeTimePipe implements PipeTransform {
  transform(date: Date): string {
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
}

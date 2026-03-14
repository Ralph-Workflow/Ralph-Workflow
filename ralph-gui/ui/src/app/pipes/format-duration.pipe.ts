import { Pipe, PipeTransform } from '@angular/core';

export type LogLevel = 'all' | 'info' | 'warning' | 'error';

export function formatDuration(secs: number | null | undefined): string {
  if (secs == null || secs === undefined || secs < 0) {
    return '';
  }

  const totalSeconds = Math.floor(secs);

  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }

  return `${minutes}m ${seconds}s`;
}

export function classifyLogLevel(line: string): LogLevel {
  if (!line) return 'info';

  const upperLine = line.toUpperCase();

  const errorPatterns = /\b(ERROR|FAIL(?:ED)?|PANIC|FATAL)\b/i;
  const warningPatterns = /\b(WARN(?:ING)?|DEPRECATED)\b/i;

  if (errorPatterns.test(upperLine)) {
    return 'error';
  }

  if (warningPatterns.test(upperLine)) {
    return 'warning';
  }

  return 'info';
}

@Pipe({
  name: 'formatDuration',
  standalone: true,
  pure: true,
})
export class FormatDurationPipe implements PipeTransform {
  transform(value: number | null | undefined): string {
    return formatDuration(value);
  }
}

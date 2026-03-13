import { Pipe, PipeTransform } from '@angular/core';
import type { RunStatus } from '../types';

/**
 * Pure pipe that converts a session status string to a RunStatus type.
 * Used to avoid function calls in templates (no-call-expression lint rule).
 */
@Pipe({
  name: 'sessionStatus',
  standalone: true,
  pure: true,
})
export class SessionStatusPipe implements PipeTransform {
  transform(status: string): RunStatus {
    switch (status) {
      case 'running': return 'Running';
      case 'paused':
      case 'interrupted': return 'Paused';
      case 'completed': return 'Completed';
      case 'failed': return 'Failed';
      default: return 'NotStarted';
    }
  }
}

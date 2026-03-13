import { Component, input, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-run-log',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (isLoading()) {
      <div
        data-testid="run-log-loading"
        aria-busy="true"
        style="padding: 8px 0; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);"
      >
        Loading logs…
      </div>
    } @else if (lines().length === 0) {
      <div
        data-testid="run-log-empty"
        role="status"
        style="padding: 8px 0; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);"
      >
        No log output yet.
      </div>
    } @else {
      <pre
        data-testid="run-log-content"
        role="log"
        [attr.aria-label]="ariaLabel()"
        aria-live="polite"
        style="overflow-y: auto; max-height: 320px; font-family: var(--font-mono); font-size: 11px; line-height: 1.5; color: var(--text-secondary); background: var(--bg-base); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 10px 12px; margin: 0; white-space: pre-wrap; word-break: break-all;"
      >{{ logContent() }}</pre>
    }
  `,
})
export class RunLogComponent {
  // Signal-based inputs (Angular 17+)
  readonly lines = input<string[]>([]);
  readonly isLoading = input(false);
  // eslint-disable-next-line @angular-eslint/no-input-rename -- aria-label is a standard HTML attribute that needs this alias
  readonly ariaLabelInput = input('Run log output', { alias: 'aria-label' });

  // Computed derived state
  readonly ariaLabel = computed(() => this.ariaLabelInput());
  readonly logContent = computed(() => this.lines().join('\n'));
}

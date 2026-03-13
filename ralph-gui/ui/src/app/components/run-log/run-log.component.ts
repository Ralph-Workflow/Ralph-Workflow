import { Component, input, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-run-log',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './run-log.component.html',
})
export class RunLogComponent {
  readonly lines = input<string[]>([]);
  readonly isLoading = input(false);
  readonly ariaLabel = input('Run log output');

  readonly logContent = computed(() => this.lines().join('\n'));
}

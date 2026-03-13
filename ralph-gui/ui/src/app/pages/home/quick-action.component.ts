import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';

@Component({
  selector: 'app-quick-action',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './quick-action.component.html',
})
export class QuickActionComponent {
  @Input() icon = '';
  @Input() label = '';
  @Input() desc = '';
  @Output() action = new EventEmitter<void>();

  onHover(event: MouseEvent): void {
    const btn = event.currentTarget as HTMLButtonElement;
    btn.style.borderColor = 'var(--border-default)';
    btn.style.background = 'var(--bg-elevated)';
  }

  onLeave(event: MouseEvent): void {
    const btn = event.currentTarget as HTMLButtonElement;
    btn.style.borderColor = 'var(--border-subtle)';
    btn.style.background = 'var(--bg-surface)';
  }
}

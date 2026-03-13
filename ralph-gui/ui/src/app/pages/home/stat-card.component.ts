import { Component, Input, ChangeDetectionStrategy } from '@angular/core';

@Component({
  selector: 'app-stat-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './stat-card.component.html',
})
export class StatCardComponent {
  @Input() label = '';
  @Input() value = 0;
  @Input() accent = false;

  get valueStyle(): string {
    const color = this.accent && this.value > 0 ? 'var(--accent)' : 'var(--text-primary)';
    const shadow = this.accent && this.value > 0 ? '0 0 20px var(--accent-glow)' : 'none';
    return `font-family: var(--font-display); font-size: 32px; font-weight: 700; color: ${color}; letter-spacing: -0.03em; line-height: 1; margin-bottom: 6px; text-shadow: ${shadow};`;
  }
}

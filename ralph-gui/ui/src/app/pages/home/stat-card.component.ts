import { Component, input, computed, ChangeDetectionStrategy } from '@angular/core';

export type TrendDirection = 'up' | 'down' | 'flat';

@Component({
  selector: 'app-stat-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './stat-card.component.html',
})
export class StatCardComponent {
  readonly label = input('');
  readonly value = input(0);
  readonly accent = input(false);
  readonly trend = input<TrendDirection | null>(null);
  readonly trendLabel = input('');
  readonly subtitle = input('');

  private readonly shouldAccentSignal = computed(() => this.accent() && this.value() > 0);

  private readonly valueClassesSignal = computed(() => {
    const base = 'font-ui text-[32px] font-bold tracking-tight leading-none mb-1.5';
    if (this.shouldAccentSignal()) {
      return `${base} text-accent drop-shadow-[0_0_20px_var(--accent-glow)]`;
    }
    return `${base} text-text-primary`;
  });

  private readonly containerClassesSignal = computed(() => {
    const base = 'card p-4.5';
    if (this.shouldAccentSignal()) {
      return `${base} border-b-2 border-accent`;
    }
    return base;
  });

  private readonly trendIconSignal = computed(() => {
    const trend = this.trend();
    if (trend === 'up') return '↑';
    if (trend === 'down') return '↓';
    if (trend === 'flat') return '→';
    return '';
  });

  private readonly trendColorSignal = computed(() => {
    const trend = this.trend();
    if (trend === 'up') return 'text-status-success';
    if (trend === 'down') return 'text-status-error';
    return 'text-text-muted';
  });

  private readonly hasTrendSignal = computed(() => this.trend() !== null);

  get labelValue(): string {
    return this.label();
  }

  get valueValue(): number {
    return this.value();
  }

  get subtitleValue(): string {
    return this.subtitle();
  }

  get trendLabelValue(): string {
    return this.trendLabel();
  }

  get hasTrendValue(): boolean {
    return this.hasTrendSignal();
  }

  get trendIconValue(): string {
    return this.trendIconSignal();
  }

  get trendColorValue(): string {
    return this.trendColorSignal();
  }

  get containerClassesValue(): string {
    return this.containerClassesSignal();
  }

  get valueClassesValue(): string {
    return this.valueClassesSignal();
  }
}

import { Component, input, computed, ChangeDetectionStrategy } from '@angular/core';

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

  get labelValue(): string {
    return this.label();
  }

  get valueValue(): number {
    return this.value();
  }

  get containerClassesValue(): string {
    return this.containerClassesSignal();
  }

  get valueClassesValue(): string {
    return this.valueClassesSignal();
  }
}

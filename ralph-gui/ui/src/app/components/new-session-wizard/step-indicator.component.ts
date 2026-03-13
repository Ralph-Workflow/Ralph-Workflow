import { Component, Input, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

type WizardStep = 'template' | 'config' | 'preflight';

@Component({
  selector: 'app-step-indicator',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './step-indicator.component.html',
})
export class StepIndicatorComponent {
  @Input() currentStep: WizardStep = 'template';

  readonly steps: { id: WizardStep; label: string }[] = [
    { id: 'template', label: 'Template' },
    { id: 'config', label: 'Configure' },
    { id: 'preflight', label: 'Pre-flight' },
  ];

  get currentIdx(): number {
    return this.steps.findIndex(s => s.id === this.currentStep);
  }

  isActive(id: WizardStep): boolean {
    return id === this.currentStep;
  }

  isDone(idx: number): boolean {
    return idx < this.currentIdx;
  }

  stepStyle(id: WizardStep): string {
    const active = this.isActive(id);
    const done = this.isDone(this.steps.findIndex(s => s.id === id));
    return `
      width: 20px;
      height: 20px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      font-family: var(--font-mono);
      font-weight: 600;
      flex-shrink: 0;
      background: ${active ? 'var(--accent)' : done ? 'var(--status-completed)' : 'var(--bg-elevated)'};
      border: ${active ? '2px solid var(--accent)' : done ? '2px solid var(--status-completed)' : '2px solid var(--border-default)'};
      color: ${active || done ? '#000' : 'var(--text-muted)'};
      box-shadow: ${active ? '0 0 8px var(--accent-glow)' : 'none'};
    `.replace(/\n/g, ' ');
  }

  labelStyle(id: WizardStep): string {
    const active = this.isActive(id);
    const done = this.isDone(this.steps.findIndex(s => s.id === id));
    return `
      font-size: 11px;
      font-family: var(--font-ui);
      font-weight: ${active ? 600 : 400};
      color: ${active ? 'var(--accent)' : done ? 'var(--text-secondary)' : 'var(--text-muted)'};
      letter-spacing: 0.01em;
      white-space: nowrap;
    `.replace(/\n/g, ' ');
  }

  lineStyle(idx: number): string {
    const done = this.isDone(idx);
    return `
      flex: 1;
      height: 1px;
      background: ${done ? 'var(--status-completed)' : 'var(--border-subtle)'};
      margin: 0 8px;
      opacity: 0.6;
    `.replace(/\n/g, ' ');
  }
}

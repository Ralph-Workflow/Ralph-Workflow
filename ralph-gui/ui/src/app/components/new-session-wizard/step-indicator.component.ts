import { Component, Input, ChangeDetectionStrategy, OnChanges } from '@angular/core';
import { CommonModule } from '@angular/common';

type WizardStep = 'template' | 'config' | 'preflight';

interface StepViewModel {
  id: WizardStep;
  label: string;
  active: boolean;
  done: boolean;
  stepStyle: string;
  labelStyle: string;
  lineStyle: string;
}

const STEP_DEFS: { id: WizardStep; label: string }[] = [
  { id: 'template', label: 'Template' },
  { id: 'config', label: 'Configure' },
  { id: 'preflight', label: 'Pre-flight' },
];

function buildStepStyle(active: boolean, done: boolean): string {
  return [
    'width: 20px',
    'height: 20px',
    'border-radius: 50%',
    'display: flex',
    'align-items: center',
    'justify-content: center',
    'font-size: 10px',
    'font-family: var(--font-mono)',
    'font-weight: 600',
    'flex-shrink: 0',
    `background: ${active ? 'var(--accent)' : done ? 'var(--status-completed)' : 'var(--bg-elevated)'}`,
    `border: ${active ? '2px solid var(--accent)' : done ? '2px solid var(--status-completed)' : '2px solid var(--border-default)'}`,
    `color: ${active || done ? '#000' : 'var(--text-muted)'}`,
    `box-shadow: ${active ? '0 0 8px var(--accent-glow)' : 'none'}`,
  ].join('; ');
}

function buildLabelStyle(active: boolean, done: boolean): string {
  return [
    'font-size: 11px',
    'font-family: var(--font-ui)',
    `font-weight: ${active ? 600 : 400}`,
    `color: ${active ? 'var(--accent)' : done ? 'var(--text-secondary)' : 'var(--text-muted)'}`,
    'letter-spacing: 0.01em',
    'white-space: nowrap',
  ].join('; ');
}

function buildLineStyle(done: boolean): string {
  return [
    'flex: 1',
    'height: 1px',
    `background: ${done ? 'var(--status-completed)' : 'var(--border-subtle)'}`,
    'margin: 0 8px',
    'opacity: 0.6',
  ].join('; ');
}

@Component({
  selector: 'app-step-indicator',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './step-indicator.component.html',
})
export class StepIndicatorComponent implements OnChanges {
  @Input() currentStep: WizardStep = 'template';

  stepViewModels: StepViewModel[] = [];

  ngOnChanges(): void {
    const currentIdx = STEP_DEFS.findIndex(s => s.id === this.currentStep);
    this.stepViewModels = STEP_DEFS.map((def, idx) => {
      const active = def.id === this.currentStep;
      const done = idx < currentIdx;
      return {
        id: def.id,
        label: def.label,
        active,
        done,
        stepStyle: buildStepStyle(active, done),
        labelStyle: buildLabelStyle(active, done),
        lineStyle: buildLineStyle(done),
      };
    });
  }
}

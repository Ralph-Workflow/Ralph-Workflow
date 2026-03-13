import { Component, Input, computed, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import type { RunStatus } from '../../types';

interface StatusConfig {
  label: string;
  color: string;
  bg: string;
  pulse: boolean;
  description: string;
}

const STATUS_CONFIG: Record<RunStatus, StatusConfig> = {
  Running: {
    label: 'Running',
    color: 'var(--status-running)',
    bg: 'var(--status-running-bg)',
    pulse: true,
    description: 'Pipeline is actively executing',
  },
  Paused: {
    label: 'Paused',
    color: 'var(--status-paused)',
    bg: 'var(--status-paused-bg)',
    pulse: false,
    description: 'Pipeline is interrupted and can be resumed',
  },
  Completed: {
    label: 'Completed',
    color: 'var(--status-completed)',
    bg: 'var(--status-completed-bg)',
    pulse: false,
    description: 'Pipeline completed successfully',
  },
  Failed: {
    label: 'Failed',
    color: 'var(--status-failed)',
    bg: 'var(--status-failed-bg)',
    pulse: false,
    description: 'Pipeline encountered an unrecoverable error',
  },
  NotStarted: {
    label: 'Not Started',
    color: 'var(--status-idle)',
    bg: 'transparent',
    pulse: false,
    description: 'No active pipeline in this repository',
  },
};

@Component({
  selector: 'app-run-status-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <span role="status" [attr.aria-label]="ariaLabel" style="display: inline-flex; align-items: center; gap: 6px;">
      <span [title]="config().description" [style]="badgeStyle()">
        <span [style]="dotStyle()"></span>
        @if (showLabel) {
          <span>{{ config().label }}</span>
        }
      </span>
      @if (isDegraded) {
        <span title="Running with degraded conditions — retries exceeded or fallback agent active" [style]="degradedStyle()">
          <span style="font-size: 10px;">⚠</span>
          @if (showLabel) {
            <span>Degraded</span>
          }
        </span>
      }
    </span>
  `,
})
export class RunStatusBadgeComponent {
  @Input() status: RunStatus = 'NotStarted';
  @Input() showLabel = true;
  @Input() size: 'sm' | 'md' = 'md';
  @Input() isDegraded = false;

  config = computed(() => STATUS_CONFIG[this.status] ?? STATUS_CONFIG.NotStarted);
  dotSize = computed(() => this.size === 'sm' ? 6 : 8);

  get ariaLabel(): string {
    return this.isDegraded ? `Status: ${this.status} (degraded)` : `Status: ${this.status}`;
  }

  badgeStyle(): string {
    const cfg = this.config();
    const padding = this.showLabel
      ? this.size === 'sm' ? '2px 8px' : '3px 10px'
      : '3px';
    return `
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: ${padding};
      border-radius: 100px;
      background: ${cfg.bg};
      border: 1px solid ${cfg.color}30;
      font-size: ${this.size === 'sm' ? '11px' : '12px'};
      font-weight: 500;
      color: ${cfg.color};
      white-space: nowrap;
      user-select: none;
    `.replace(/\n/g, ' ');
  }

  dotStyle(): string {
    const cfg = this.config();
    const size = this.dotSize();
    return `
      width: ${size}px;
      height: ${size}px;
      border-radius: 50%;
      background: ${cfg.color};
      flex-shrink: 0;
      ${cfg.pulse ? 'animation: pulse-dot 1.4s ease-in-out infinite;' : ''}
    `.replace(/\n/g, ' ');
  }

  degradedStyle(): string {
    const padding = this.showLabel ? '3px 8px' : '3px';
    return `
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: ${padding};
      border-radius: 100px;
      background: var(--status-degraded-bg);
      border: 1px solid var(--status-degraded-border);
      font-size: ${this.size === 'sm' ? '11px' : '12px'};
      font-weight: 500;
      color: var(--status-degraded);
      white-space: nowrap;
      user-select: none;
    `.replace(/\n/g, ' ');
  }
}

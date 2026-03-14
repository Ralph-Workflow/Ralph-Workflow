import { Component, input, output, computed, ChangeDetectionStrategy } from '@angular/core';

export interface PhaseInfo {
  name: string; // 'Plan' | 'Develop' | 'Review' | 'Commit'
  status: 'pending' | 'active' | 'completed' | 'failed';
  duration?: string; // e.g. "2m 34s"
  summary?: string;
  statusLabel?: string; // e.g. "Running now", "Waiting", "Failed"
}

/** Enriched phase data computed for the template — avoids parameterized method calls. */
export interface PhaseDisplayItem {
  phase: PhaseInfo;
  dotTestId: string;
  dotSymbol: string;
}

@Component({
  selector: 'app-phase-timeline',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './phase-timeline.component.html',
  styleUrl: './phase-timeline.component.css',
})
export class PhaseTimelineComponent {
  readonly phases = input<PhaseInfo[]>([]);
  readonly phaseClick = output<PhaseInfo>();

  /** Pre-computed display data for each phase — avoids parameterized method calls in templates. */
  readonly displayPhases = computed<PhaseDisplayItem[]>(() =>
    this.phases().map(phase => ({
      phase,
      dotTestId: this.getDotTestId(phase),
      dotSymbol: this.getDotSymbol(phase),
    }))
  );

  /** Getter so the template can access displayPhases without calling a signal. */
  get currentDisplayPhases() { return this.displayPhases(); }

  getDotTestId(phase: PhaseInfo): string {
    return `phase-dot-${phase.status}`;
  }

  getDotSymbol(phase: PhaseInfo): string {
    switch (phase.status) {
      case 'completed': return '✓';
      case 'failed': return '✗';
      case 'pending': return '○';
      case 'active': return '●';
    }
  }

  getDotClasses(phase: PhaseInfo): string {
    const base = 'w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold transition-all duration-normal select-none';
    const nameLower = phase.name.toLowerCase();

    switch (phase.status) {
      case 'pending':
        return `${base} bg-bg-raised border-2 border-border-default text-text-muted`;
      case 'active': {
        const activeColors: Record<string, string> = {
          plan: 'bg-[rgba(139,92,246,0.15)] border-2 border-phase-plan text-phase-plan animate-phase-pulse',
          develop: 'bg-[rgba(59,130,246,0.15)] border-2 border-phase-develop text-phase-develop animate-phase-pulse',
          review: 'bg-[rgba(245,158,11,0.15)] border-2 border-phase-review text-phase-review animate-phase-pulse',
          commit: 'bg-[rgba(34,197,94,0.15)] border-2 border-phase-commit text-phase-commit animate-phase-pulse',
        };
        return `${base} ${activeColors[nameLower] ?? ''}`;
      }
      case 'completed': {
        const completedColors: Record<string, string> = {
          plan: 'bg-[rgba(139,92,246,0.15)] border-2 border-phase-plan text-phase-plan',
          develop: 'bg-[rgba(59,130,246,0.15)] border-2 border-phase-develop text-phase-develop',
          review: 'bg-[rgba(245,158,11,0.15)] border-2 border-phase-review text-phase-review',
          commit: 'bg-[rgba(34,197,94,0.15)] border-2 border-phase-commit text-phase-commit',
        };
        return `${base} ${completedColors[nameLower] ?? 'border-2 border-current'}`;
      }
      case 'failed':
        return `${base} bg-[rgba(239,68,68,0.15)] border-2 border-status-error text-status-error`;
      default:
        return base;
    }
  }

  getLabelClasses(phase: PhaseInfo): string {
    const base = 'text-[11px] font-medium text-center whitespace-nowrap capitalize transition-colors duration-normal';
    const nameLower = phase.name.toLowerCase();

    switch (phase.status) {
      case 'pending':
        return `${base} text-text-muted`;
      case 'failed':
        return `${base} text-status-error`;
      case 'active':
      case 'completed': {
        const colorMap: Record<string, string> = {
          plan: 'text-phase-plan',
          develop: 'text-phase-develop',
          review: 'text-phase-review',
          commit: 'text-phase-commit',
        };
        return `${base} ${colorMap[nameLower] ?? ''}`;
      }
      default:
        return base;
    }
  }

  getConnectorClasses(phase: PhaseInfo): string {
    const base = 'flex-1 h-0.5 mt-[13px] opacity-50 transition-colors duration-normal';

    switch (phase.status) {
      case 'completed':
        return `${base} bg-status-success opacity-100`;
      case 'active':
        return `${base} bg-gradient-to-r from-accent to-border-default opacity-80`;
      default:
        return `${base} bg-border-default`;
    }
  }

  onPhaseClick(phase: PhaseInfo): void {
    if (phase.status === 'completed') {
      this.phaseClick.emit(phase);
    }
  }
}

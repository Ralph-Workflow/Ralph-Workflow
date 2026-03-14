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

interface PhaseColorConfig {
  color: string;
  bg: string;
  border: string;
}

const PHASE_COLORS: Record<string, PhaseColorConfig> = {
  Plan: {
    color: 'var(--phase-plan)',
    bg: 'rgba(139,92,246,0.15)',
    border: 'var(--phase-plan)',
  },
  Develop: {
    color: 'var(--phase-develop)',
    bg: 'rgba(59,130,246,0.15)',
    border: 'var(--phase-develop)',
  },
  Review: {
    color: 'var(--phase-review)',
    bg: 'rgba(245,158,11,0.15)',
    border: 'var(--phase-review)',
  },
  Commit: {
    color: 'var(--phase-commit)',
    bg: 'rgba(34,197,94,0.15)',
    border: 'var(--phase-commit)',
  },
};

const DEFAULT_COLOR: PhaseColorConfig = {
  color: 'var(--text-muted)',
  bg: 'var(--bg-raised)',
  border: 'var(--border-default)',
};

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

  getPhaseColor(phase: PhaseInfo): PhaseColorConfig {
    return PHASE_COLORS[phase.name] ?? DEFAULT_COLOR;
  }

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

  onPhaseClick(phase: PhaseInfo): void {
    if (phase.status === 'completed') {
      this.phaseClick.emit(phase);
    }
  }
}

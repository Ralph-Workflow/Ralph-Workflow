import { Component, input, output, ChangeDetectionStrategy, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import type { SessionSummary } from '../../types';

interface DisplayRun extends SessionSummary {
  run_id_short: string;
  elapsedTime: string;
  pipelineStep: string;
}

const PHASE_ORDER = ['Plan', 'Develop', 'Review', 'Commit'] as const;

@Component({
  selector: 'app-active-runs-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule, RunStatusBadgeComponent],
  templateUrl: './active-runs-list.component.html',
})
export class ActiveRunsListComponent {
  readonly runs = input<SessionSummary[]>([]);
  readonly viewRun = output<string>();

  private readonly runCountSignal = computed(() => this.runs().length);
  private readonly displayRunsSignal = computed<DisplayRun[]>(() =>
    this.runs().map(run => ({
      ...run,
      run_id_short: run.run_id.substring(0, 16),
      elapsedTime: this.formatElapsedTime(run.created_at),
      pipelineStep: this.formatPipelineStep(run.phase),
    }))
  );

  get runCountValue(): number {
    return this.runCountSignal();
  }

  get displayRunsValue(): DisplayRun[] {
    return this.displayRunsSignal();
  }

  private formatElapsedTime(isoString: string): string {
    const start = new Date(isoString).getTime();
    const now = Date.now();
    const diffMs = now - start;
    
    const diffMinutes = Math.floor(diffMs / 60000);
    if (diffMinutes < 60) {
      return `${diffMinutes}m`;
    }
    
    const diffHours = Math.floor(diffMinutes / 60);
    const remainingMinutes = diffMinutes % 60;
    if (diffHours < 24) {
      return remainingMinutes > 0 ? `${diffHours}h ${remainingMinutes}m` : `${diffHours}h`;
    }
    
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d`;
  }

  private formatPipelineStep(currentPhase: string): string {
    const phaseIndex = PHASE_ORDER.findIndex(p => 
      currentPhase.toLowerCase().includes(p.toLowerCase())
    );
    
    if (phaseIndex === -1) {
      return currentPhase;
    }
    
    const phases = PHASE_ORDER.map((phase, index) => ({
      phase,
      isActive: index === phaseIndex,
      isComplete: index < phaseIndex,
    }));
    
    const phaseNames = phases.map(p => 
      p.isComplete ? `✓ ${p.phase}` 
      : p.isActive ? `● ${p.phase}`
      : `○ ${p.phase}`
    ).join(' → ');
    
    return phaseNames;
  }

  onViewClick(runId: string): void {
    this.viewRun.emit(runId);
  }
}

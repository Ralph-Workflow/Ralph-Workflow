import { Component, input, output, ChangeDetectionStrategy, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import type { SessionSummary } from '../../types';

interface DisplayRun extends SessionSummary {
  run_id_short: string;
}

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
    }))
  );

  get runCountValue(): number {
    return this.runCountSignal();
  }

  get displayRunsValue(): DisplayRun[] {
    return this.displayRunsSignal();
  }

  onViewClick(runId: string): void {
    this.viewRun.emit(runId);
  }
}

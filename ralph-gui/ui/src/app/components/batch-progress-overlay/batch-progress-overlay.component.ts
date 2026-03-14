import {
  Component,
  input,
  output,
  ChangeDetectionStrategy,
  computed,
  HostListener,
} from '@angular/core';
import type { BatchOperationResult } from '../../types';

interface RunDisplayInfo {
  runId: string;
  displayName: string;
  error: string | null;
}

@Component({
  selector: 'app-batch-progress-overlay',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './batch-progress-overlay.component.html',
  styleUrl: './batch-progress-overlay.component.css',
})
export class BatchProgressOverlayComponent {
  readonly operationType = input.required<'resume' | 'cancel' | 'delete'>();
  readonly targetRunIds = input.required<string[]>();
  readonly runIdToName = input<Record<string, string>>({});
  readonly result = input<BatchOperationResult | null>(null);
  readonly isInProgress = input(true);

  readonly closed = output<void>();
  readonly openRun = output<string>();

  @HostListener('keydown.escape')
  onEscape(): void {
    if (!this.isInProgress()) {
      this.closed.emit();
    }
  }

  readonly operationTitle = computed(() => {
    const type = this.operationType();
    const count = this.targetRunIds().length;
    const verb = type === 'resume' ? 'Resuming' : type === 'cancel' ? 'Cancelling' : 'Deleting';
    return `${verb} ${count} session${count === 1 ? '' : 's'}`;
  });

  readonly completedTitle = computed(() => 'Batch Action Complete');

  readonly progressCount = computed(() => {
    const total = this.targetRunIds().length;
    const result = this.result();
    if (!result) return { done: 0, total };
    return { done: result.succeeded + result.failed, total };
  });

  readonly progressPercent = computed(() => {
    const { done, total } = this.progressCount();
    if (total === 0) return 0;
    return Math.round((done / total) * 100);
  });

  readonly resultSummary = computed(() => this.result());

  readonly hasPartialSuccess = computed(() => {
    const result = this.result();
    if (!result) return false;
    return result.succeeded > 0 && result.failed > 0;
  });

  readonly hasFailures = computed(() => {
    const result = this.result();
    if (!result) return false;
    return result.failed > 0;
  });

  readonly runDisplayInfos = computed((): RunDisplayInfo[] => {
    const runIds = this.targetRunIds();
    const nameMap = this.runIdToName();
    const result = this.result();
    
    return runIds.map(runId => {
      const name = nameMap[runId];
      const displayName = name ?? (runId.length > 16 ? runId.substring(0, 14) + '...' : runId);
      const error = result?.errors[runId] ?? null;
      return { runId, displayName, error };
    });
  });

  readonly operationTitle_ = computed(() => {
    if (this.result()) {
      return this.completedTitle();
    }
    return this.operationTitle();
  });

  onClose(): void {
    this.closed.emit();
  }

  onOpenRun(runId: string): void {
    this.openRun.emit(runId);
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('dialog-backdrop')) {
      if (!this.isInProgress()) {
        this.closed.emit();
      }
    }
  }
}

import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-preflight-summary',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './preflight-summary.component.html',
})
export class PreflightSummaryComponent {
  @Input() repoPath = '';
  @Input() worktreePath: string | null = null;
  @Input() promptPath = '';
  @Input() developerIterations = 5;
  @Input() reviewerPasses = 2;
  @Input() isLaunching = false;
  @Output() confirmLaunch = new EventEmitter<void>();
  @Output() goBack = new EventEmitter<void>();

  get contextRows(): Array<{ label: string; value: string }> {
    return [
      { label: 'Repository', value: this.repoPath },
      { label: 'Context', value: this.worktreePath ?? 'Direct repository' },
    ];
  }
}

import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-preflight-summary',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div style="display: flex; flex-direction: column; gap: 20px;">
      <div>
        <div class="section-label">Pre-flight summary</div>
        <div style="background: var(--bg-elevated); border: 1px solid var(--border-default); border-radius: var(--radius-md); overflow: hidden;">
          <!-- Context rows -->
          @for (row of contextRows; track row.label) {
            <div style="display: flex; gap: 16px; padding: 10px 14px; border-bottom: 1px solid var(--border-subtle); background: var(--bg-surface);">
              <span style="font-size: 12px; color: var(--text-secondary); width: 110px; flex-shrink: 0; font-weight: 500;">
                {{ row.label }}
              </span>
              <span class="chip-mono" style="max-width: 100%; overflow: hidden; text-overflow: ellipsis; color: var(--text-primary);">
                {{ row.value }}
              </span>
            </div>
          }
          <!-- Prompt row (less prominent) -->
          <div style="display: flex; gap: 16px; padding: 8px 14px; border-bottom: 1px solid var(--border-subtle);">
            <span style="font-size: 12px; color: var(--text-muted); width: 110px; flex-shrink: 0;">
              Prompt
            </span>
            <span class="chip-mono" style="max-width: 100%; overflow: hidden; text-overflow: ellipsis;">
              {{ promptPath }}
            </span>
          </div>
          <!-- Config rows — two-column grid -->
          <div style="display: grid; grid-template-columns: 1fr 1fr;">
            <div style="display: flex; flex-direction: column; gap: 4px; padding: 10px 14px; border-right: 1px solid var(--border-subtle);">
              <span style="font-size: 11px; color: var(--text-muted);">Dev iterations</span>
              <span class="chip-mono" style="font-size: 14px; font-weight: 600; color: var(--accent);">{{ developerIterations }}</span>
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px; padding: 10px 14px;">
              <span style="font-size: 11px; color: var(--text-muted);">Review passes</span>
              <span class="chip-mono" style="font-size: 14px; font-weight: 600; color: var(--accent);">{{ reviewerPasses }}</span>
            </div>
          </div>
        </div>
      </div>

      <div style="padding: 12px; background: var(--accent-bg); border: 1px solid var(--accent-dim)40; border-radius: var(--radius-md); font-size: 12px; color: var(--text-secondary); line-height: 1.6;">
        This will launch an unattended Ralph session. The pipeline will run autonomously. You can monitor progress from the Run Dashboard.
      </div>

      <div style="display: flex; gap: 8px; justify-content: flex-end;">
        <button class="btn btn-secondary" (click)="goBack.emit()" [disabled]="isLaunching">
          Back
        </button>
        <button class="btn btn-primary" (click)="confirmLaunch.emit()" [disabled]="isLaunching">
          {{ isLaunching ? 'Launching…' : 'Launch session' }}
        </button>
      </div>
    </div>
  `,
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

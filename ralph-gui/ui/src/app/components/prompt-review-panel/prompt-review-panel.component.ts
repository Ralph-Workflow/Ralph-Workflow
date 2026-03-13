import { Component, Input, Output, EventEmitter, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TauriService } from '../../services/tauri.service';
import type { PromptReviewResult } from '../../types';

type PanelState = 'idle' | 'loading' | 'success' | 'error' | 'no-key';

@Component({
  selector: 'app-prompt-review-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './prompt-review-panel.component.html',
})
export class PromptReviewPanelComponent {
  private readonly tauri = inject(TauriService);

  @Input() promptContent = '';
  @Output() applyImprovedPrompt = new EventEmitter<string>();

  readonly panelState = signal<PanelState>('idle');
  readonly result = signal<PromptReviewResult | null>(null);
  readonly errorMsg = signal<string | null>(null);

  async handleReview(): Promise<void> {
    this.panelState.set('loading');
    this.result.set(null);
    this.errorMsg.set(null);

    try {
      const res = await this.tauri.reviewPromptWithAi(this.promptContent);
      this.result.set(res);
      this.panelState.set('success');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes('ANTHROPIC_API_KEY')) {
        this.panelState.set('no-key');
      } else {
        this.panelState.set('error');
        this.errorMsg.set(msg);
      }
    }
  }

  applyImproved(): void {
    const improved = this.result()?.improved_prompt;
    if (improved) {
      this.applyImprovedPrompt.emit(improved);
    }
  }
}

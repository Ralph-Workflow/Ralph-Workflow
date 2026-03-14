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

  private readonly _panelState = signal<PanelState>('idle');
  private readonly _result = signal<PromptReviewResult | null>(null);
  private readonly _errorMsg = signal<string | null>(null);

  /** Getters so the template accesses state without calling signals directly. */
  get panelState() { return this._panelState(); }
  get result() { return this._result(); }
  get errorMsg() { return this._errorMsg(); }

  async handleReview(): Promise<void> {
    this._panelState.set('loading');
    this._result.set(null);
    this._errorMsg.set(null);

    try {
      const res = await this.tauri.reviewPromptWithAi(this.promptContent);
      this._result.set(res);
      this._panelState.set('success');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes('ANTHROPIC_API_KEY')) {
        this._panelState.set('no-key');
      } else {
        this._panelState.set('error');
        this._errorMsg.set(msg);
      }
    }
  }

  applyImproved(): void {
    const improved = this._result()?.improved_prompt;
    if (improved) {
      this.applyImprovedPrompt.emit(improved);
    }
  }
}

import {
  Component,
  ChangeDetectionStrategy,
  inject,
  signal,
  computed,
  input,
  output,
  effect,
  OnInit,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { TauriService } from '../../services/tauri.service';
import type { PromptReviewResult } from '../../types';

export type AssistTab = 'describe' | 'refine';

export interface DescribeMessage {
  role: 'user' | 'ai';
  text: string;
}

@Component({
  selector: 'app-ai-prompt-assistant',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, RouterModule],
  templateUrl: './ai-prompt-assistant.component.html',
  styleUrls: ['./ai-prompt-assistant.component.css'],
})
export class AiPromptAssistantComponent implements OnInit {
  private readonly tauri = inject(TauriService);

  // Inputs
  readonly currentPrompt = input<string>('');
  readonly repoPath = input<string>('');

  // Output
  readonly applyPrompt = output<string>();

  // Panel state
  readonly isOpen = signal(false);
  readonly activeTab = signal<AssistTab>('describe');

  // Planning drain agent state
  readonly planningDrainAgent = signal<string | null | undefined>(undefined); // undefined=loading, null=not configured
  readonly isLoadingDrainAgent = signal(false);

  /** True when planning drain is not configured (null = checked, not set) */
  readonly isUnconfigured = computed(() => this.planningDrainAgent() === null);

  // Describe tab state
  readonly describeInput = signal('');
  readonly describeMessages = signal<DescribeMessage[]>([]);
  readonly describeLoading = signal(false);

  // Refine tab state
  readonly refineLoading = signal(false);
  readonly refineResult = signal<PromptReviewResult | null>(null);
  readonly refineError = signal<string | null>(null);

  /** True when currentPrompt is empty and refine tab is active */
  readonly refineEmptyState = computed(
    () => this.activeTab() === 'refine' && !this.currentPrompt().trim(),
  );

  get isOpen_() { return this.isOpen(); }
  get activeTab_() { return this.activeTab(); }
  get describeMessages_() { return this.describeMessages(); }
  get describeLoading_() { return this.describeLoading(); }
  get describeInput_() { return this.describeInput(); }
  get isDescribeInputEmpty() { return !this.describeInput().trim(); }
  get refineEmptyState_() { return this.refineEmptyState(); }
  get refineLoading_() { return this.refineLoading(); }
  get refineError_() { return this.refineError(); }
  get refineResult_() { return this.refineResult(); }
  get isUnconfigured_() { return this.isUnconfigured(); }
  get isLoadingDrainAgent_() { return this.isLoadingDrainAgent(); }
  get planningDrainAgent_() { return this.planningDrainAgent(); }

  ngOnInit(): void {
    void this.loadPlanningDrainAgent();
  }

  private async loadPlanningDrainAgent(): Promise<void> {
    this.isLoadingDrainAgent.set(true);
    try {
      const agent = await this.tauri.getPlanningDrainAgent(this.repoPath());
      this.planningDrainAgent.set(agent);
    } catch {
      // If we can't check, assume unconfigured
      this.planningDrainAgent.set(null);
    } finally {
      this.isLoadingDrainAgent.set(false);
    }
  }

  constructor() {
    // When refine tab becomes active and currentPrompt is non-empty, trigger analysis
    effect(() => {
      const tab = this.activeTab();
      const prompt = this.currentPrompt();
      const repo = this.repoPath();
      if (tab === 'refine' && prompt.trim()) {
        void this.runRefineAnalysis(prompt, repo);
      }
    });
  }

  toggle(): void {
    this.isOpen.update(v => !v);
  }

  setTab(tab: AssistTab): void {
    this.activeTab.set(tab);
  }

  async submitDescribe(): Promise<void> {
    const text = this.describeInput().trim();
    if (!text) return;

    this.describeMessages.update(msgs => [...msgs, { role: 'user', text }]);
    this.describeInput.set('');
    this.describeLoading.set(true);

    try {
      const result = await this.tauri.assistPromptDescribe(text, this.repoPath());
      this.describeMessages.update(msgs => [...msgs, { role: 'ai', text: result }]);
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      this.describeMessages.update(msgs => [...msgs, { role: 'ai', text: `Error: ${errMsg}` }]);
    } finally {
      this.describeLoading.set(false);
    }
  }

  private async runRefineAnalysis(prompt: string, repoPath: string): Promise<void> {
    this.refineLoading.set(true);
    this.refineError.set(null);
    this.refineResult.set(null);

    try {
      const result = await this.tauri.assistPromptRefine(prompt, repoPath);
      this.refineResult.set(result);
    } catch (e) {
      this.refineError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.refineLoading.set(false);
    }
  }

  applyFromDescribe(text: string): void {
    this.applyPrompt.emit(text);
  }

  applyRefineResult(): void {
    const result = this.refineResult();
    if (result?.improved_prompt) {
      this.applyPrompt.emit(result.improved_prompt);
    }
  }

  reAnalyze(): void {
    const prompt = this.currentPrompt().trim();
    const repo = this.repoPath();
    if (prompt) {
      void this.runRefineAnalysis(prompt, repo);
    }
  }

  onDescribeKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void this.submitDescribe();
    }
  }
}

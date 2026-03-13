import {
  Component,
  Input,
  Output,
  EventEmitter,
  inject,
  signal,
  computed,
  effect,
  ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { AgentProfileService } from '../../services/agent-profile.service';
import { TauriService } from '../../services/tauri.service';
import { PreflightSummaryComponent } from '../preflight-summary/preflight-summary.component';
import { PromptTemplatePickerComponent } from '../prompt-template-picker/prompt-template-picker.component';
import { PromptReviewPanelComponent } from '../prompt-review-panel/prompt-review-panel.component';
import { AiPromptAssistantComponent } from '../ai-prompt-assistant/ai-prompt-assistant.component';
import { InlineWorktreeCreateComponent } from '../inline-worktree-create/inline-worktree-create.component';
import { StepIndicatorComponent } from './step-indicator.component';
import type { ConfigView } from '../../types';

type WizardStep = 'template' | 'config' | 'preflight';

@Component({
  selector: 'app-new-session-wizard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    PreflightSummaryComponent,
    PromptTemplatePickerComponent,
    PromptReviewPanelComponent,
    AiPromptAssistantComponent,
    InlineWorktreeCreateComponent,
    StepIndicatorComponent,
  ],
  templateUrl: './new-session-wizard.component.html',
})
export class NewSessionWizardComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly agentProfileService = inject(AgentProfileService);
  readonly tauri = inject(TauriService);

  @Input() set repoPathInput(value: string) {
    this.repoPath.set(value);
  }
  /** preselectedWorktreePath is used by the sessions page to pre-fill a worktree. */
  @Input() set preselectedWorktreePath(value: string | null) {
    if (value) {
      this.selectedWorktreePath.set(value);
    }
  }
  @Output() readonly closeWizard = new EventEmitter<void>();
  /** wizardClosed is an alias for closeWizard (used by sessions page template). */
  @Output() readonly wizardClosed = new EventEmitter<void>();
  @Output() readonly sessionLaunched = new EventEmitter<string>();

  readonly step = signal<WizardStep>('template');
  readonly repoPath = signal('');
  readonly worktreePath = signal<string | null>(null);
  readonly promptPath = signal('');
  readonly promptContent = signal('');
  readonly developerIterations = signal(5);
  readonly reviewerPasses = signal(2);
  readonly checkpointEnabled = signal(false);
  readonly isolationMode = signal(false);
  readonly reviewDepth = signal<string>('standard');
  readonly selectedWorktreePath = signal<string | null>(null);
  readonly selectedAgentProfile = signal<string | null>(null);
  readonly isLaunching = signal(false);
  readonly launchError = signal<string | null>(null);

  /** Preset name input field value */
  readonly newPresetName = signal('');

  /** Alias for newPresetName, used by the HTML template */
  get presetNameInput() { return this.newPresetName; }

  get step_() { return this.step(); }
  get launchError_() { return this.launchError(); }
  get repoPath_() { return this.repoPath(); }
  get worktreePath_() { return this.worktreePath(); }
  get promptPath_() { return this.promptPath(); }
  get developerIterations_() { return this.developerIterations(); }
  get reviewerPasses_() { return this.reviewerPasses(); }
  get isLaunching_() { return this.isLaunching(); }
  get worktrees_() { return this.worktreesService.worktrees(); }
  get showCreateWorktree_() { return this.showCreateWorktree(); }
  get nonMainWorktrees_() { return this.nonMainWorktrees(); }
  get agentProfiles_() { return this.agentProfileService.profiles(); }
  get selectedProfile_() { return this.agentProfileService.selectedProfile(); }
  get configSummaryLine_() { return this.configSummaryLine(); }
  get configPanelExpanded_() { return this.configPanelExpanded(); }
  get checkpointEnabled_() { return this.checkpointEnabled(); }
  get isolationMode_() { return this.isolationMode(); }
  get presets_() { return this.presets(); }
  get presetNameInput_() { return this.newPresetName(); }
  get isPresetNameEmpty() { return !this.newPresetName().trim(); }
  get reviewDepth_() { return this.reviewDepth(); }
  get showReviewPanel_() { return this.showReviewPanel(); }
  get showAiAssistant_() { return this.showAiAssistant(); }
  get promptContent_() { return this.promptService.content(); }
  get canProceedToLaunch_() { return this.canProceedToLaunch(); }
  get hasAgentProfiles_() { return this.hasAgentProfiles(); }

  readonly showReviewPanel = signal(false);
  readonly showAiAssistant = signal(false);
  readonly showCreateWorktree = signal(false);
  readonly configPanelExpanded = signal(false);

  /** Stores the effective config returned from backend for the "Reset to defaults" feature */
  private effectiveConfig = signal<ConfigView | null>(null);

  /** Whether agent profiles exist (for unconfigured state detection) */
  readonly hasAgentProfiles = computed(() => this.agentProfileService.profiles().length > 0);

  /** Whether the user can proceed past step 2 */
  readonly canProceedToLaunch = computed(
    () => this.repoPath().trim().length > 0 && this.hasAgentProfiles(),
  );

  readonly nonMainWorktrees = computed(() =>
    this.worktreesService.worktrees().filter(wt => !wt.is_main)
  );

  readonly promptService = {
    content: this.promptContent,
  };

  /** Summary line for collapsed config panel: "[agent] · N iterations · M reviews · [depth]" */
  readonly configSummaryLine = computed(() => {
    const iters = this.developerIterations();
    const reviews = this.reviewerPasses();
    const depth = this.reviewDepth();
    const profile = this.selectedAgentProfile() ?? 'default';
    return `${profile} · ${iters} iterations · ${reviews} reviews · ${depth}`;
  });

  constructor() {
    effect(() => {
      const path = this.repoPath();
      if (path) {
        void this.worktreesService.fetchWorktrees(path);
        void this.agentProfileService.fetchProfiles(path);
      }
    });

    // When entering the config step, fetch and prefill from effective config
    effect(() => {
      const s = this.step();
      const path = this.repoPath();
      if (s === 'config' && path) {
        void this.prefillFromEffectiveConfig(path);
      }
    });
  }

  private async prefillFromEffectiveConfig(repoPath: string): Promise<void> {
    try {
      const config = await this.tauri.getEffectiveConfig(repoPath);
      this.effectiveConfig.set(config);
      this.applyConfig(config);
    } catch {
      // Non-fatal: leave defaults in place
    }
  }

  private applyConfig(config: ConfigView): void {
    this.developerIterations.set(config.developer_iters);
    this.reviewerPasses.set(config.reviewer_reviews);
    this.checkpointEnabled.set(config.checkpoint_enabled);
    this.isolationMode.set(config.isolation_mode);
    this.reviewDepth.set(config.review_depth);
  }

  resetToDefaults(): void {
    const config = this.effectiveConfig();
    if (config) {
      this.applyConfig(config);
    }
  }

  onTemplateSelect(content: string): void {
    this.promptContent.set(content);
    this.step.set('config');
  }

  onSaveAsTemplate(promptContent: string): void {
    // Save the current prompt as a template via the Tauri backend.
    // Uses a timestamped default name since we are not blocking the workflow for a name dialog.
    const name = `Template ${new Date().toISOString().slice(0, 19).replace('T', ' ')}`;
    void this.tauri.saveTemplate(name, '', promptContent, [], '').catch(() => {
      // Non-fatal: template save failure should not block the session flow
    });
  }

  handleNext(): void {
    const current = this.step();
    if (current === 'template') {
      if (this.promptContent().trim()) {
        this.step.set('config');
      }
    } else if (current === 'config') {
      if (this.repoPath().trim()) {
        this.step.set('preflight');
      }
    }
  }

  onRepoPathInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.repoPath.set(value);
  }

  onWorktreeChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.worktreePath.set(value || null);
  }

  onAgentProfileChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.selectedAgentProfile.set(value || null);
  }

  onPresetLoad(event: Event): void {
    const name = (event.target as HTMLSelectElement).value;
    if (!name) return;
    const preset = this.presets().find(p => p.name === name);
    if (preset) {
      this.loadPreset(preset);
    }
  }

  onPresetNameInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.newPresetName.set(value);
  }

  toggleCreateWorktree(): void {
    this.showCreateWorktree.update(v => !v);
  }

  onWorktreeCreated(worktree: { path: string }): void {
    this.selectedWorktreePath.set(worktree.path);
    this.showCreateWorktree.set(false);
  }

  onDevItersChange(event: Event): void {
    const value = parseInt((event.target as HTMLInputElement).value, 10);
    if (!isNaN(value) && value >= 1 && value <= 20) {
      this.developerIterations.set(value);
    }
  }

  onRevPassesChange(event: Event): void {
    const value = parseInt((event.target as HTMLInputElement).value, 10);
    if (!isNaN(value) && value >= 0 && value <= 10) {
      this.reviewerPasses.set(value);
    }
  }

  onReviewDepthChange(event: Event): void {
    this.reviewDepth.set((event.target as HTMLSelectElement).value);
  }

  onPromptChange(event: Event): void {
    const value = (event.target as HTMLTextAreaElement).value;
    this.promptContent.set(value);
  }

  applyImprovedPrompt(content: string): void {
    this.promptContent.set(content);
  }

  handleLaunch(): void {
    void this.launchSession();
  }

  async launchSession(): Promise<void> {
    if (this.isLaunching()) return;

    this.isLaunching.set(true);
    this.launchError.set(null);

    try {
      const promptFileName = `prompt-${Date.now()}.md`;
      const promptPath = `${this.repoPath()}/.ralph/prompts/${promptFileName}`;

      await this.tauri.savePromptFile(promptPath, this.promptContent());

      const runId = await this.tauri.launchRalphSession({
        repo_path: this.repoPath(),
        worktree_path: this.selectedWorktreePath(),
        prompt_path: promptPath,
        developer_iterations: this.developerIterations(),
        reviewer_passes: this.reviewerPasses(),
        developer_agent: this.selectedAgentProfile(),
        reviewer_agent: null,
      });

      this.sessionLaunched.emit(runId);
      this.wizardClosed.emit();
    } catch (e) {
      this.launchError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.isLaunching.set(false);
    }
  }

  // ---- Presets (in-memory only; no localStorage per project rules) ----

  readonly presets = signal<Array<{
    name: string;
    developerIterations: number;
    reviewerPasses: number;
    agentProfile: string | null;
  }>>([]);

  savePreset(): void {
    const name = this.newPresetName().trim();
    if (!name) return;

    const preset = {
      name,
      developerIterations: this.developerIterations(),
      reviewerPasses: this.reviewerPasses(),
      agentProfile: this.selectedAgentProfile(),
    };

    this.presets.update(prev => [...prev, preset]);
    this.newPresetName.set('');
  }

  loadPreset(preset: {
    name: string;
    developerIterations: number;
    reviewerPasses: number;
    agentProfile: string | null;
  }): void {
    this.developerIterations.set(preset.developerIterations);
    this.reviewerPasses.set(preset.reviewerPasses);
    this.selectedAgentProfile.set(preset.agentProfile);
  }

  deletePreset(name: string): void {
    this.presets.update(prev => prev.filter(p => p.name !== name));
  }
}

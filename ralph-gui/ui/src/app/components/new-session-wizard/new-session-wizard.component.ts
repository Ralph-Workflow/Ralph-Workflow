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
import { TauriService } from '../../services/tauri.service';
import { PreflightSummaryComponent } from '../preflight-summary/preflight-summary.component';
import { PromptTemplatePickerComponent } from '../prompt-template-picker/prompt-template-picker.component';
import { PromptReviewPanelComponent } from '../prompt-review-panel/prompt-review-panel.component';
import { AiPromptAssistantComponent } from '../ai-prompt-assistant/ai-prompt-assistant.component';
import { InlineWorktreeCreateComponent } from '../inline-worktree-create/inline-worktree-create.component';
import { StepIndicatorComponent } from './step-indicator.component';
import {
  DRAIN_LABELS,
  type DrainPhase,
} from '../../pages/configuration/agent-chains-editor/agent-chains-editor.types';
import type { ConfigView, EffectiveChainsConfig } from '../../types';

type WizardStep = 'template' | 'config' | 'preflight';

/** Preset saved configuration. */
interface WizardPreset {
  name: string;
  developerIterations: number;
  reviewerPasses: number;
  reviewDepth: string;
  drainBindings: Record<string, string>;
  developerContext: string;
  reviewerContext: string;
  checkpointEnabled: boolean;
  isolationMode: boolean;
}

/** Review depth options per AC-4.3.2 */
export const REVIEW_DEPTH_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'standard', label: 'Standard (recommended)' },
  { value: 'comprehensive', label: 'Comprehensive' },
  { value: 'security', label: 'Security' },
  { value: 'incremental', label: 'Incremental' },
];

/** The 6 drain phases used in the wizard in display order. */
const WIZARD_DRAIN_PHASES: DrainPhase[] = [
  'planning',
  'development',
  'analysis',
  'review',
  'fix',
  'commit',
];

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
  readonly isLaunching = signal(false);
  readonly launchError = signal<string | null>(null);

  /** Drain-to-chain bindings for all 6 phases. */
  readonly drainBindings = signal<Record<string, string>>({});

  /** Developer context for Advanced section. */
  readonly developerContext = signal<string>('normal');

  /** Reviewer context for Advanced section. */
  readonly reviewerContext = signal<string>('normal');

  /** Whether the Advanced subsection is expanded. */
  readonly advancedExpanded = signal(false);

  /** Preset name input field value */
  readonly newPresetName = signal('');

  /** Alias for newPresetName, used by the HTML template */
  get presetNameInput() { return this.newPresetName; }

  readonly showReviewPanel = signal(false);
  readonly showAiAssistant = signal(false);
  readonly showCreateWorktree = signal(false);
  readonly configPanelExpanded = signal(false);

  /** Stores the effective config returned from backend for the "Reset to defaults" feature */
  private effectiveConfig = signal<ConfigView | null>(null);

  /** Stores the effective chains config returned from backend. */
  readonly effectiveChainsConfig = signal<EffectiveChainsConfig | null>(null);

  /** Available chain names from effective config. */
  readonly chainNames = computed(() => {
    const config = this.effectiveChainsConfig();
    if (!config) return [];
    return config.chains.map(c => c.name);
  });

  /** Whether agent chains AND drains are configured. */
  readonly hasConfiguredChains = computed(() => {
    const config = this.effectiveChainsConfig();
    if (!config) return false;
    return config.has_configured_chains;
  });

  /** Whether the user can proceed past step 2. */
  readonly canProceedToLaunch = computed(
    () => this.repoPath().trim().length > 0 && this.hasConfiguredChains(),
  );

  readonly nonMainWorktrees = computed(() =>
    this.worktreesService.worktrees().filter(wt => !wt.is_main)
  );

  readonly promptService = {
    content: this.promptContent,
  };

  /** The drain phases to display in the wizard (in UI order). */
  readonly drainPhases = WIZARD_DRAIN_PHASES;

  /** Labels for drain phases. */
  readonly drainLabels = DRAIN_LABELS;

  /** Review depth options. */
  readonly reviewDepthOptions = REVIEW_DEPTH_OPTIONS;

  /** Summary line for collapsed config panel showing primary chain with agents. */
  readonly configSummaryLine = computed(() => {
    const iters = this.developerIterations();
    const reviews = this.reviewerPasses();
    const depth = this.reviewDepth();
    const chainsConfig = this.effectiveChainsConfig();
    const drains = this.drainBindings();

    // Try to build chain description from development drain
    let chainDesc = '';
    if (chainsConfig && drains['development']) {
      const chainName = drains['development'];
      const chain = chainsConfig.chains.find(c => c.name === chainName);
      if (chain && chain.agents.length > 0) {
        chainDesc = `${chain.agents.join(' → ')}`;
      } else {
        chainDesc = chainName;
      }
    } else if (chainsConfig && chainsConfig.chains.length > 0) {
      const firstChain = chainsConfig.chains[0];
      if (firstChain) {
        chainDesc = firstChain.agents.length > 0
          ? firstChain.agents.join(' → ')
          : firstChain.name;
      }
    } else {
      chainDesc = 'default';
    }

    const depthOption = REVIEW_DEPTH_OPTIONS.find(o => o.value === depth);
    const depthLabel = depthOption ? depthOption.label : depth;
    return `${chainDesc} · ${iters} iterations · ${reviews} reviews · ${depthLabel}`;
  });

  /** Value source label based on effective config and chains config. */
  readonly valueSourceLabel = computed(() => {
    const chainsConfig = this.effectiveChainsConfig();
    const effectiveConfig = this.effectiveConfig();
    if (!chainsConfig && !effectiveConfig) return 'Default';

    const hasGlobal = true; // If we got a response, there's at least a global source
    const hasProject = chainsConfig?.has_configured_drains || false;

    if (hasGlobal && hasProject) return 'Global + Project';
    if (hasProject) return 'Project';
    return 'Global';
  });

  // Template getters for use in the HTML template
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
  get hasConfiguredChains_() { return this.hasConfiguredChains(); }
  get drainBindings_() { return this.drainBindings(); }
  get chainNames_() { return this.chainNames(); }
  get drainPhases_() { return this.drainPhases; }
  get drainLabels_() { return this.drainLabels; }
  get reviewDepthOptions_() { return this.reviewDepthOptions; }
  get advancedExpanded_() { return this.advancedExpanded(); }
  get developerContext_() { return this.developerContext(); }
  get reviewerContext_() { return this.reviewerContext(); }
  get valueSourceLabel_() { return this.valueSourceLabel(); }
  get effectiveChainsConfig_() { return this.effectiveChainsConfig(); }

  constructor() {
    effect(() => {
      const path = this.repoPath();
      if (path) {
        void this.worktreesService.fetchWorktrees(path);
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
      const [config, chainsConfig] = await Promise.all([
        this.tauri.getEffectiveConfig(repoPath),
        this.tauri.getEffectiveChainsConfig(repoPath),
      ]);
      this.effectiveConfig.set(config);
      this.effectiveChainsConfig.set(chainsConfig);
      this.applyConfig(config);
      this.applyDrainBindings(chainsConfig.drains);
    } catch {
      // Non-fatal: leave defaults in place
    }
  }

  private applyConfig(config: ConfigView): void {
    this.developerIterations.set(config.developer_iters);
    this.reviewerPasses.set(config.reviewer_reviews);
    this.checkpointEnabled.set(config.checkpoint_enabled);
    this.isolationMode.set(config.isolation_mode);
    // Map legacy review depth values to new AC-4.3.2 values
    const legacyDepthMap: Record<string, string> = {
      light: 'incremental',
      thorough: 'comprehensive',
    };
    const depth = config.review_depth in legacyDepthMap
      ? legacyDepthMap[config.review_depth]!
      : config.review_depth;
    this.reviewDepth.set(depth);
  }

  private applyDrainBindings(drains: Record<string, string>): void {
    this.drainBindings.set({ ...drains });
  }

  resetToDefaults(): void {
    const config = this.effectiveConfig();
    if (config) {
      this.applyConfig(config);
    }
    const chainsConfig = this.effectiveChainsConfig();
    if (chainsConfig) {
      this.applyDrainBindings(chainsConfig.drains);
    }
    this.developerContext.set('normal');
    this.reviewerContext.set('normal');
    this.advancedExpanded.set(false);
  }

  onTemplateSelect(content: string): void {
    this.promptContent.set(content);
    this.step.set('config');
  }

  onSaveAsTemplate(promptContent: string): void {
    // Save the current prompt as a template via the Tauri backend.
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

  onDrainChange(phase: string, event: Event): void {
    const chainName = (event.target as HTMLSelectElement).value;
    this.drainBindings.update(bindings => ({
      ...bindings,
      [phase]: chainName,
    }));
  }

  onDevContextChange(event: Event): void {
    this.developerContext.set((event.target as HTMLSelectElement).value);
  }

  onRevContextChange(event: Event): void {
    this.reviewerContext.set((event.target as HTMLSelectElement).value);
  }

  toggleAdvanced(): void {
    this.advancedExpanded.update(v => !v);
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

  navigateToConfiguration(): void {
    this.closeWizard.emit();
    this.wizardClosed.emit();
  }

  async launchSession(): Promise<void> {
    if (this.isLaunching()) return;

    this.isLaunching.set(true);
    this.launchError.set(null);

    try {
      const promptFileName = `prompt-${Date.now()}.md`;
      const promptPath = `${this.repoPath()}/.ralph/prompts/${promptFileName}`;

      await this.tauri.savePromptFile(promptPath, this.promptContent());

      // Resolve developer and reviewer agents from drain bindings
      const chainsConfig = this.effectiveChainsConfig();
      const drains = this.drainBindings();

      const resolveFirstAgent = (phase: string): string | null => {
        if (!chainsConfig) return null;
        const chainName = drains[phase];
        if (!chainName) return null;
        const chain = chainsConfig.chains.find(c => c.name === chainName);
        return chain && chain.agents.length > 0 ? chain.agents[0]! : null;
      };

      const developerAgent = resolveFirstAgent('development');
      const reviewerAgent = resolveFirstAgent('review');

      const runId = await this.tauri.launchRalphSession({
        repo_path: this.repoPath(),
        worktree_path: this.selectedWorktreePath(),
        prompt_path: promptPath,
        developer_iterations: this.developerIterations(),
        reviewer_passes: this.reviewerPasses(),
        developer_agent: developerAgent,
        reviewer_agent: reviewerAgent,
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

  readonly presets = signal<WizardPreset[]>([]);

  savePreset(): void {
    const name = this.newPresetName().trim();
    if (!name) return;

    const preset: WizardPreset = {
      name,
      developerIterations: this.developerIterations(),
      reviewerPasses: this.reviewerPasses(),
      reviewDepth: this.reviewDepth(),
      drainBindings: { ...this.drainBindings() },
      developerContext: this.developerContext(),
      reviewerContext: this.reviewerContext(),
      checkpointEnabled: this.checkpointEnabled(),
      isolationMode: this.isolationMode(),
    };

    this.presets.update(prev => [...prev, preset]);
    this.newPresetName.set('');
  }

  loadPreset(preset: WizardPreset): void {
    this.developerIterations.set(preset.developerIterations);
    this.reviewerPasses.set(preset.reviewerPasses);
    this.reviewDepth.set(preset.reviewDepth);
    this.drainBindings.set({ ...preset.drainBindings });
    this.developerContext.set(preset.developerContext);
    this.reviewerContext.set(preset.reviewerContext);
    this.checkpointEnabled.set(preset.checkpointEnabled);
    this.isolationMode.set(preset.isolationMode);
  }

  deletePreset(name: string): void {
    this.presets.update(prev => prev.filter(p => p.name !== name));
  }
}

import { Component, Input, Output, EventEmitter, inject, signal, computed, effect, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { AgentProfileService } from '../../services/agent-profile.service';
import { TauriService } from '../../services/tauri.service';
import { PreflightSummaryComponent } from '../preflight-summary/preflight-summary.component';
import { PromptTemplatePickerComponent } from '../prompt-template-picker/prompt-template-picker.component';
import { PromptReviewPanelComponent } from '../prompt-review-panel/prompt-review-panel.component';
import { InlineWorktreeCreateComponent } from '../inline-worktree-create/inline-worktree-create.component';
import { StepIndicatorComponent } from './step-indicator.component';

type WizardStep = 'template' | 'config' | 'preflight';

interface LaunchPreset {
  name: string;
  developerIterations: number;
  reviewerPasses: number;
  agentProfile: string | null;
}

const PRESETS_KEY = 'ralph_gui_presets';

function loadPresets(): LaunchPreset[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as LaunchPreset[];
  } catch {
    return [];
  }
}

function savePresets(presets: LaunchPreset[]): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

@Component({
  selector: 'app-new-session-wizard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    PreflightSummaryComponent,
    PromptTemplatePickerComponent,
    PromptReviewPanelComponent,
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
  @Output() readonly closeWizard = new EventEmitter<void>();
  @Output() readonly sessionLaunched = new EventEmitter<string>();

  readonly step = signal<WizardStep>('template');
  readonly repoPath = signal('');
  readonly worktreePath = signal<string | null>(null);
  readonly promptPath = signal('');
  readonly promptContent = signal('');
  readonly developerIterations = signal(5);
  readonly reviewerPasses = signal(2);
  readonly selectedWorktreePath = signal<string | null>(null);
  readonly selectedAgentProfile = signal<string | null>(null);
  readonly isLaunching = signal(false);
  readonly launchError = signal<string | null>(null);
  readonly presets = signal<LaunchPreset[]>(loadPresets());
  readonly newPresetName = signal('');
  readonly showReviewPanel = signal(false);
  readonly showCreateWorktree = signal(false);

  readonly nonMainWorktrees = computed(() => 
    this.worktreesService.worktrees().filter(wt => !wt.is_main)
  );

  readonly promptService = {
    content: this.promptContent,
  };

  constructor() {
    effect(() => {
      const path = this.repoPath();
      if (path) {
        void this.worktreesService.fetchWorktrees(path);
        void this.agentProfileService.fetchProfiles(path);
      }
    });
  }

  onTemplateSelect(content: string): void {
    this.promptContent.set(content);
    this.step.set('config');
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
    if (!isNaN(value) && value > 0) {
      this.developerIterations.set(value);
    }
  }

  onRevPassesChange(event: Event): void {
    const value = parseInt((event.target as HTMLInputElement).value, 10);
    if (!isNaN(value) && value > 0) {
      this.reviewerPasses.set(value);
    }
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
    } catch (e) {
      this.launchError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.isLaunching.set(false);
    }
  }

  savePreset(): void {
    const name = this.newPresetName().trim();
    if (!name) return;

    const preset: LaunchPreset = {
      name,
      developerIterations: this.developerIterations(),
      reviewerPasses: this.reviewerPasses(),
      agentProfile: this.selectedAgentProfile(),
    };

    const updated = [...this.presets(), preset];
    this.presets.set(updated);
    savePresets(updated);
    this.newPresetName.set('');
  }

  loadPreset(preset: LaunchPreset): void {
    this.developerIterations.set(preset.developerIterations);
    this.reviewerPasses.set(preset.reviewerPasses);
    this.selectedAgentProfile.set(preset.agentProfile);
  }

  deletePreset(name: string): void {
    const updated = this.presets().filter(p => p.name !== name);
    this.presets.set(updated);
    savePresets(updated);
  }
}

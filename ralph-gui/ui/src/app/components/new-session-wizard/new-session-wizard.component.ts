import { Component, Input, Output, EventEmitter, inject, signal, computed, effect, ChangeDetectionStrategy, forwardRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorktreesService } from '../../services/worktrees.service';
import { PromptService } from '../../services/prompt.service';
import { AgentProfileService } from '../../services/agent-profile.service';
import { TauriService } from '../../services/tauri.service';
import { PreflightSummaryComponent } from '../preflight-summary/preflight-summary.component';
import { PromptTemplatePickerComponent } from '../prompt-template-picker/prompt-template-picker.component';
import { PromptReviewPanelComponent } from '../prompt-review-panel/prompt-review-panel.component';
import { InlineWorktreeCreateComponent } from '../inline-worktree-create/inline-worktree-create.component';
import { PROMPT_TEMPLATES } from '../../pages/sessions/prompt-templates';
import type { WorktreeInfo } from '../../types';

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
    forwardRef(() => StepIndicatorComponent),
  ],
  template: `
    <!-- Template Step -->
    @if (step() === 'template') {
      <div data-testid="wizard-template-step" style="display: flex; flex-direction: column; gap: 20px;">
        <app-step-indicator [currentStep]="step()" />
        <app-prompt-template-picker (selectTemplate)="onTemplateSelect($event)" />
        <div style="display: flex; gap: 8px; justify-content: flex-end;">
          <button class="btn btn-ghost" (click)="closeWizard.emit()">Cancel</button>
        </div>
      </div>
    }

    <!-- Preflight Step -->
    @if (step() === 'preflight') {
      <div style="display: flex; flex-direction: column; gap: 12px;">
        <app-step-indicator [currentStep]="step()" />
        @if (launchError()) {
          <div
            data-testid="launch-error"
            style="padding: 8px 12px; background: var(--error-bg, #fee); border: 1px solid var(--error-border, #f99); border-radius: var(--radius-md); font-size: 12px; color: var(--error-text, #c00);"
          >
            {{ launchError() }}
          </div>
        }
        <app-preflight-summary
          [repoPath]="repoPath()"
          [worktreePath]="worktreePath()"
          [promptPath]="promptPath()"
          [developerIterations]="developerIterations()"
          [reviewerPasses]="reviewerPasses()"
          [isLaunching]="isLaunching()"
          (confirmLaunch)="handleLaunch()"
          (goBack)="step.set('config')"
        />
      </div>
    }

    <!-- Config Step -->
    @if (step() === 'config') {
      <div data-testid="wizard-config-step" style="display: flex; flex-direction: column; gap: 16px;">
        <app-step-indicator [currentStep]="step()" />

        <!-- Repo path -->
        <div>
          <label class="section-label" for="repo-path">Repository path</label>
          <input
            id="repo-path"
            class="input input-mono"
            [value]="repoPath()"
            (input)="onRepoPathInput($event)"
            placeholder="/path/to/your/repo"
          />
        </div>

        <!-- Worktree selection -->
        @if (worktreesService.worktrees().length > 0) {
          <div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
              <label class="section-label" for="worktree-select" style="margin-bottom: 0;">Context</label>
              <button
                data-testid="create-worktree-toggle"
                class="btn btn-ghost"
                style="font-size: 11px; padding: 2px 10px;"
                (click)="toggleCreateWorktree()"
              >
                {{ showCreateWorktree() ? '← Select existing' : '+ Create new worktree' }}
              </button>
            </div>
            @if (!showCreateWorktree()) {
              <select
                id="worktree-select"
                class="input"
                [value]="worktreePath() ?? ''"
                (change)="onWorktreeChange($event)"
                style="font-family: var(--font-mono); font-size: 12px;"
              >
                <option value="">Direct repository (no worktree)</option>
                @for (wt of nonMainWorktrees(); track wt.path) {
                  <option [value]="wt.path">{{ wt.name }} ({{ wt.branch }})</option>
                }
              </select>
            }
            @if (showCreateWorktree()) {
              <app-inline-worktree-create
                [repoPath]="repoPath()"
                (created)="onWorktreeCreated($event)"
              />
            }
          </div>
        }

        <!-- Agent profile selection -->
        @if (agentProfileService.profiles().length > 0) {
          <div>
            <label class="section-label" for="agent-profile">Agent profile</label>
            <select
              id="agent-profile"
              data-testid="agent-profile-select"
              class="input"
              [value]="agentProfileService.selectedProfile() ?? ''"
              (change)="onAgentProfileChange($event)"
              style="font-family: var(--font-mono); font-size: 12px;"
            >
              <option value="">Default (from config)</option>
              @for (p of agentProfileService.profiles(); track p.name) {
                <option [value]="p.name">{{ p.name }} — dev: {{ p.developer_agent }} / reviewer: {{ p.reviewer_agent }}</option>
              }
            </select>
          </div>
        }

        <!-- Preset picker -->
        @if (presets().length > 0) {
          <div>
            <label class="section-label">Load preset</label>
            <div style="display: flex; align-items: center; gap: 8px;">
              <select
                data-testid="load-preset-select"
                class="input"
                (change)="onPresetLoad($event)"
                style="font-family: var(--font-mono); font-size: 12px;"
              >
                <option value="">— select a preset —</option>
                @for (p of presets(); track p.name) {
                  <option [value]="p.name">{{ p.name }}</option>
                }
              </select>
            </div>
            <!-- Saved preset list with delete buttons -->
            <div style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px;">
              @for (p of presets(); track p.name) {
                <span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 100px; background: var(--bg-elevated); border: 1px solid var(--border-subtle); font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono);">
                  {{ p.name }}
                  <button
                    [attr.data-testid]="'delete-preset-' + p.name"
                    (click)="deletePreset(p.name)"
                    style="background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: 10px; padding: 0; line-height: 1;"
                    [title]="'Delete preset ' + p.name + ''"
                  >
                    ✕
                  </button>
                </span>
              }
            </div>
          </div>
        }

        <!-- Iteration config -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div>
            <label class="section-label" for="dev-iters">Dev iterations</label>
            <input
              id="dev-iters"
              class="input"
              type="number"
              min="1"
              max="20"
              [value]="developerIterations()"
              (input)="onDevItersChange($event)"
            />
          </div>
          <div>
            <label class="section-label" for="rev-passes">Review passes</label>
            <input
              id="rev-passes"
              class="input"
              type="number"
              min="1"
              max="10"
              [value]="reviewerPasses()"
              (input)="onRevPassesChange($event)"
            />
          </div>
        </div>

        <!-- Save as preset -->
        <div>
          <label class="section-label">Save current config as preset</label>
          <div style="display: flex; gap: 8px;">
            <input
              data-testid="preset-name-input"
              class="input input-mono"
              [value]="presetNameInput()"
              (input)="onPresetNameInput($event)"
              placeholder="Preset name…"
              style="flex: 1;"
            />
            <button
              data-testid="save-preset-button"
              class="btn btn-secondary"
              (click)="savePreset()"
              [disabled]="!presetNameInput().trim()"
              style="flex-shrink: 0;"
            >
              Save
            </button>
          </div>
        </div>

        <!-- Prompt editor with optional AI review panel -->
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <label class="section-label" style="margin-bottom: 0;">PROMPT.md</label>
            <button
              class="btn btn-ghost"
              style="font-size: 11px; padding: 2px 10px;"
              (click)="showReviewPanel.set(!showReviewPanel())"
              data-testid="toggle-review-panel"
            >
              {{ showReviewPanel() ? 'Hide AI review' : 'Review with AI' }}
            </button>
          </div>
          <textarea
            id="prompt-editor"
            class="input input-mono"
            rows="10"
            [value]="promptService.content()"
            (input)="onPromptChange($event)"
            style="resize: vertical; line-height: 1.6;"
          ></textarea>
          @if (showReviewPanel()) {
            <div style="margin-top: 10px;">
              <app-prompt-review-panel
                [promptContent]="promptService.content()"
                (applyImprovedPrompt)="applyImprovedPrompt($event)"
              />
            </div>
          }
        </div>

        <div style="display: flex; gap: 8px; justify-content: flex-end;">
          <button class="btn btn-ghost" (click)="step.set('template')">← Templates</button>
          <button class="btn btn-ghost" (click)="closeWizard.emit()">Cancel</button>
          <button
            class="btn btn-primary"
            (click)="handleNext()"
            [disabled]="!repoPath().trim()"
            data-testid="review-launch-button"
          >
            Review & launch →
          </button>
        </div>
      </div>
    }
  `,
})
export class NewSessionWizardComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly promptService = inject(PromptService);
  readonly agentProfileService = inject(AgentProfileService);
  private readonly tauri = inject(TauriService);

  @Input() preselectedWorktreePath: string | null | undefined = undefined;
  @Output() closeWizard = new EventEmitter<void>();

  readonly step = signal<WizardStep>('template');
  readonly repoPath = signal('');
  readonly worktreePath = signal<string | null>(null);
  readonly developerIterations = signal(5);
  readonly reviewerPasses = signal(2);
  readonly isLaunching = signal(false);
  readonly launchError = signal<string | null>(null);
  readonly showReviewPanel = signal(false);
  readonly showCreateWorktree = signal(false);
  readonly presets = signal<LaunchPreset[]>(loadPresets());
  readonly presetNameInput = signal('');

  readonly nonMainWorktrees = computed(() =>
    this.worktreesService.worktrees().filter(wt => !wt.is_main)
  );

  readonly promptPath = computed(() =>
    this.repoPath() ? `${this.repoPath()}/PROMPT.md` : ''
  );

  constructor() {
    // Set default prompt content on init if not already set
    effect(() => {
      if (!this.promptService.content()) {
        const defaultContent = PROMPT_TEMPLATES.find(t => t.id === 'feature')?.content ?? '';
        this.promptService.setContent(defaultContent);
      }
    }, { allowSignalWrites: true });

    // Initialize repo path and worktree path from inputs
    effect(() => {
      const mainWorktree = this.worktreesService.worktrees().find(wt => wt.is_main);
      const activePath = this.worktreesService.activeWorktreePath();

      if (!this.repoPath() && mainWorktree?.path) {
        this.repoPath.set(mainWorktree.path);
      }

      if (this.preselectedWorktreePath !== undefined) {
        this.worktreePath.set(this.preselectedWorktreePath);
      } else if (activePath && !this.worktreePath()) {
        this.worktreePath.set(activePath);
      }
    }, { allowSignalWrites: true });

    // Load agent profiles when repo path is known
    effect(() => {
      const path = this.repoPath();
      if (path) {
        void this.agentProfileService.fetchProfiles(path);
      }
    });
  }

  onTemplateSelect(content: string): void {
    this.promptService.setContent(content);
    this.step.set('config');
  }

  onRepoPathInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.repoPath.set(value);
  }

  onWorktreeChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.worktreePath.set(value || null);
    this.showCreateWorktree.set(false);
  }

  toggleCreateWorktree(): void {
    this.showCreateWorktree.update(v => !v);
  }

  onWorktreeCreated(worktree: WorktreeInfo): void {
    this.worktreePath.set(worktree.path);
    this.showCreateWorktree.set(false);
  }

  onAgentProfileChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    if (value) {
      this.agentProfileService.selectProfile(value);
    } else {
      this.agentProfileService.clearSelection();
    }
  }

  onDevItersChange(event: Event): void {
    const value = parseInt((event.target as HTMLInputElement).value, 10);
    if (!isNaN(value)) {
      this.developerIterations.set(value);
    }
  }

  onRevPassesChange(event: Event): void {
    const value = parseInt((event.target as HTMLInputElement).value, 10);
    if (!isNaN(value)) {
      this.reviewerPasses.set(value);
    }
  }

  onPromptChange(event: Event): void {
    const value = (event.target as HTMLTextAreaElement).value;
    this.promptService.setContent(value);
  }

  applyImprovedPrompt(improved: string): void {
    this.promptService.setContent(improved);
  }

  onPresetNameInput(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.presetNameInput.set(value);
  }

  savePreset(): void {
    if (!this.presetNameInput().trim()) return;
    const updated = [
      ...this.presets().filter(p => p.name !== this.presetNameInput().trim()),
      {
        name: this.presetNameInput().trim(),
        developerIterations: this.developerIterations(),
        reviewerPasses: this.reviewerPasses(),
        agentProfile: this.agentProfileService.selectedProfile(),
      },
    ];
    savePresets(updated);
    this.presets.set(updated);
    this.presetNameInput.set('');
  }

  onPresetLoad(event: Event): void {
    const name = (event.target as HTMLSelectElement).value;
    if (!name) return;
    const preset = this.presets().find(p => p.name === name);
    if (!preset) return;
    this.developerIterations.set(preset.developerIterations);
    this.reviewerPasses.set(preset.reviewerPasses);
    if (preset.agentProfile) {
      this.agentProfileService.selectProfile(preset.agentProfile);
    } else {
      this.agentProfileService.clearSelection();
    }
  }

  deletePreset(name: string): void {
    const updated = this.presets().filter(p => p.name !== name);
    savePresets(updated);
    this.presets.set(updated);
  }

  handleNext(): void {
    if (!this.repoPath().trim()) return;
    this.promptService.setPath(this.promptPath());
    this.step.set('preflight');
  }

  async handleLaunch(): Promise<void> {
    this.isLaunching.set(true);
    this.launchError.set(null);

    try {
      // Write the prompt to disk before launching
      if (this.promptPath()) {
        await this.tauri.savePromptFile(this.promptPath(), this.promptService.content());
      }

      const profiles = this.agentProfileService.profiles();
      const selectedName = this.agentProfileService.selectedProfile();
      const selectedProfile = profiles.find(p => p.name === selectedName);

      await this.tauri.launchRalphSession({
        repo_path: this.repoPath(),
        worktree_path: this.worktreePath(),
        prompt_path: this.promptPath(),
        developer_iterations: this.developerIterations(),
        reviewer_passes: this.reviewerPasses(),
        developer_agent: selectedProfile?.developer_agent ?? null,
        reviewer_agent: selectedProfile?.reviewer_agent ?? null,
      });

      this.closeWizard.emit();
    } catch (err) {
      this.launchError.set(err instanceof Error ? err.message : 'Launch failed');
    } finally {
      this.isLaunching.set(false);
    }
  }
}

// Step indicator component
@Component({
  selector: 'app-step-indicator',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div style="display: flex; align-items: center; gap: 0; margin-bottom: 20px;">
      @for (s of steps; track s.id; let idx = $index) {
        <div style="display: flex; align-items: center; flex: idx < steps.length - 1 ? 1 : undefined;">
          <div style="display: flex; align-items: center; gap: 6px;">
            <div
              [style]="stepStyle(s.id)"
            >
              @if (isDone(idx)) {
                ✓
              } @else {
                {{ idx + 1 }}
              }
            </div>
            <span [style]="labelStyle(s.id)">
              {{ s.label }}
            </span>
          </div>
          @if (idx < steps.length - 1) {
            <div [style]="lineStyle(idx)"></div>
          }
        </div>
      }
    </div>
  `,
})
export class StepIndicatorComponent {
  @Input() currentStep: WizardStep = 'template';

  readonly steps: { id: WizardStep; label: string }[] = [
    { id: 'template', label: 'Template' },
    { id: 'config', label: 'Configure' },
    { id: 'preflight', label: 'Pre-flight' },
  ];

  get currentIdx(): number {
    return this.steps.findIndex(s => s.id === this.currentStep);
  }

  isActive(id: WizardStep): boolean {
    return id === this.currentStep;
  }

  isDone(idx: number): boolean {
    return idx < this.currentIdx;
  }

  stepStyle(id: WizardStep): string {
    const active = this.isActive(id);
    const done = this.isDone(this.steps.findIndex(s => s.id === id));
    return `
      width: 20px;
      height: 20px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 10px;
      font-family: var(--font-mono);
      font-weight: 600;
      flex-shrink: 0;
      background: ${active ? 'var(--accent)' : done ? 'var(--status-completed)' : 'var(--bg-elevated)'};
      border: ${active ? '2px solid var(--accent)' : done ? '2px solid var(--status-completed)' : '2px solid var(--border-default)'};
      color: ${active || done ? '#000' : 'var(--text-muted)'};
      box-shadow: ${active ? '0 0 8px var(--accent-glow)' : 'none'};
    `.replace(/\n/g, ' ');
  }

  labelStyle(id: WizardStep): string {
    const active = this.isActive(id);
    const done = this.isDone(this.steps.findIndex(s => s.id === id));
    return `
      font-size: 11px;
      font-family: var(--font-ui);
      font-weight: ${active ? 600 : 400};
      color: ${active ? 'var(--accent)' : done ? 'var(--text-secondary)' : 'var(--text-muted)'};
      letter-spacing: 0.01em;
      white-space: nowrap;
    `.replace(/\n/g, ' ');
  }

  lineStyle(idx: number): string {
    const done = this.isDone(idx);
    return `
      flex: 1;
      height: 1px;
      background: ${done ? 'var(--status-completed)' : 'var(--border-subtle)'};
      margin: 0 8px;
      opacity: 0.6;
    `.replace(/\n/g, ' ');
  }
}

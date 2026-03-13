import { Component, inject, signal, effect, computed, input, ChangeDetectionStrategy, forwardRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConfigService } from '../../services/config.service';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { ConfigView } from '../../types';

type TabId = 'effective' | 'global' | 'project';

const DEFAULT_TOML_TEMPLATE = (label: string) =>
  `# ${label} configuration (TOML)\n# Edit values below and save.\n\n[defaults]\n`;

const VALIDATION_DEBOUNCE_MS = 300;

@Component({
  selector: 'app-configuration',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    forwardRef(() => ConfigTableComponent),
    forwardRef(() => TomlEditorComponent),
    forwardRef(() => AiIntegrationSectionComponent),
  ],
  template: `
    <div class="page-content">
      <!-- Header -->
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-6); animation: fadeIn 200ms ease;">
        <h1 class="page-title" style="margin-bottom: 0;">Configuration</h1>
        @if (repoPath()) {
          <span class="chip-mono" style="font-size: 11px;">{{ repoPath() }}</span>
        }
      </div>

      <div style="animation: fadeIn 200ms ease 40ms both;">
        <!-- Tab bar -->
        <div class="tab-bar" style="margin-bottom: var(--space-5);">
          @for (tab of tabs; track tab.id) {
            <button
              class="tab-item{{ activeTab() === tab.id ? ' tab-item--active' : '' }}"
              (click)="setActiveTab(tab.id)"
            >
              {{ tab.label }}
            </button>
          }
        </div>

        <!-- Effective read view -->
        @if (activeTab() === 'effective') {
          <div class="card">
            <div style="margin-bottom: 12px; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
              Merged view: project overrides global defaults
            </div>

            @if (configService.globalStatus() === 'loading') {
              <div style="padding: var(--space-6); text-align: center; color: var(--text-muted); font-size: 13px; font-family: var(--font-mono);">
                Loading...
              </div>
            } @else if (displayConfig()) {
              <app-config-table [config]="displayConfig()!" />
            } @else {
              <div style="padding: var(--space-6); text-align: center; color: var(--text-muted); font-size: 13px; font-family: var(--font-mono);">
                @if (!repoPath()) {
                  Select a repository to see effective config.
                } @else {
                  No configuration loaded.
                }
              </div>
            }
          </div>
        }

        <!-- Global edit view -->
        @if (activeTab() === 'global') {
          <div class="card">
            <div style="margin-bottom: 16px; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
              Global config stored at <span style="color: var(--text-secondary);">~/.config/ralph-workflow.toml</span>
            </div>
            <app-toml-editor label="Global" scope="global" [repoPath]="null" />
            <app-ai-integration-section />
          </div>
        }

        <!-- Project edit view -->
        @if (activeTab() === 'project') {
          <div class="card">
            <div style="margin-bottom: 16px; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
              Project-level config stored at <span style="color: var(--text-secondary);">{{ projectConfigPath() }}</span>
            </div>
            @if (!repoPath()) {
              <div style="padding: 10px 12px; background: rgba(232,168,56,0.08); border: 1px solid rgba(232,168,56,0.2); border-radius: var(--radius-md); color: var(--accent); font-size: 12px; font-family: var(--font-mono); margin-bottom: 12px;">
                Select a repository context to edit project config.
              </div>
            }
            <app-toml-editor label="Project" scope="project" [repoPath]="repoPath()" />
          </div>
        }
      </div>
    </div>
  `,
})
export class ConfigurationComponent {
  readonly configService = inject(ConfigService);
  readonly worktreesService = inject(WorktreesService);

  readonly tabs: { id: TabId; label: string }[] = [
    { id: 'effective', label: 'Effective' },
    { id: 'global', label: 'Global' },
    { id: 'project', label: 'Project' },
  ];

  readonly activeTab = signal<TabId>('effective');

  readonly repoPath = computed(() => {
    const activePath = this.worktreesService.activeWorktreePath();
    if (activePath) return activePath;
    const mainWorktree = this.worktreesService.worktrees().find(wt => wt.is_main);
    return mainWorktree?.path ?? null;
  });

  readonly displayConfig = computed(() => {
    if (this.activeTab() !== 'effective') return null;
    return this.configService.effectiveConfig() ?? this.configService.globalConfig();
  });

  readonly projectConfigPath = computed(() => {
    const repo = this.repoPath();
    return repo ? `${repo}/.ralph/config.toml` : '<repo>/.ralph/config.toml';
  });

  constructor() {
    // Fetch global config on mount
    effect(() => {
      void this.configService.fetchGlobalConfig();
    });

    // Fetch effective config when repo changes
    effect(() => {
      const repo = this.repoPath();
      if (repo) {
        void this.configService.fetchEffectiveConfig(repo);
      }
    });
  }

  setActiveTab(tab: TabId): void {
    this.activeTab.set(tab);
  }
}

@Component({
  selector: 'app-config-field',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div style="display: flex; align-items: flex-start; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--border-subtle);">
      <div style="flex: 1;">
        <div style="font-family: var(--font-mono); font-size: 13px; color: var(--text-primary); margin-bottom: 2px;">
          {{ label() }}
        </div>
        <div style="font-size: 11px; color: var(--text-muted);">
          {{ description() }}
        </div>
      </div>
      <div [style]="valueStyle()">
        {{ displayValue() }}
      </div>
    </div>
  `,
})
export class ConfigFieldComponent {
  readonly label = input<string>('');
  readonly description = input<string>('');
  readonly value = input<string | number | boolean>('');

  displayValue = computed(() => {
    const v = this.value();
    return typeof v === 'boolean' ? (v ? 'true' : 'false') : String(v);
  });

  valueStyle = computed(() => {
    const v = this.value();
    const color = typeof v === 'boolean'
      ? v ? 'var(--status-completed)' : 'var(--text-muted)'
      : 'var(--accent)';
    return `
      font-family: var(--font-mono);
      font-size: 13px;
      color: ${color};
      margin-left: 24px;
      flex-shrink: 0;
    `.replace(/\n/g, ' ');
  });
}

@Component({
  selector: 'app-config-table',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, forwardRef(() => ConfigFieldComponent)],
  template: `
    <div>
      <app-config-field label="verbosity" description="Log verbosity level (0=quiet, 1=normal, 2=verbose)" [value]="config().verbosity" />
      <app-config-field label="developer_iters" description="Number of developer agent iterations per cycle" [value]="config().developer_iters" />
      <app-config-field label="reviewer_reviews" description="Number of reviewer passes per cycle" [value]="config().reviewer_reviews" />
      <app-config-field label="max_dev_continuations" description="Maximum continuation budget for developer agent" [value]="config().max_dev_continuations" />
      <app-config-field label="review_depth" description="Depth of reviewer analysis (shallow | standard | deep)" [value]="config().review_depth" />
      <app-config-field label="checkpoint_enabled" description="Persist pipeline state for --resume support" [value]="config().checkpoint_enabled" />
      <app-config-field label="isolation_mode" description="Run agents in isolated environment" [value]="config().isolation_mode" />
      <app-config-field label="interactive" description="Pause for user confirmation between phases" [value]="config().interactive" />
    </div>
  `,
})
export class ConfigTableComponent {
  readonly config = input.required<ConfigView>();
}

@Component({
  selector: 'app-toml-editor',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div>
      @if (loading()) {
        <div style="padding: 8px 0; font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);">
          Loading...
        </div>
      }
      <textarea
        class="form-input input-mono"
        [value]="toml()"
        (input)="onTomlInput($event)"
        [style]="textareaStyle()"
        spellcheck="false"
      ></textarea>
      @if (validationError()) {
        <div
          data-testid="toml-validation-error"
          style="margin-top: 6px; padding: 6px 10px; background: rgba(248,81,73,0.08); border: 1px solid rgba(248,81,73,0.25); border-radius: var(--radius-md); font-size: 11px; color: var(--status-failed); font-family: var(--font-mono);"
        >
          {{ validationError() }}
        </div>
      }
      <div style="display: flex; align-items: center; gap: 12px; margin-top: 10px;">
        <button
          class="btn btn-primary"
          (click)="handleSave()"
          [disabled]="isSaveDisabled()"
        >
          {{ saving() ? 'Saving...' : 'Save' }}
        </button>
        <button
          class="btn btn-ghost"
          data-testid="revert-config-button"
          (click)="handleRevert()"
          [disabled]="toml() === savedToml()"
          title="Discard unsaved changes and reload from saved state"
        >
          Revert
        </button>
        <span
          data-testid="scope-badge"
          class="chip-mono"
          style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em;"
        >
          {{ scopeLabel() }}
        </span>
        @if (saveMsg()) {
          <span style="font-size: 12px; color: var(--status-completed); font-family: var(--font-mono);">
            {{ saveMsg() }}
          </span>
        }
        @if (saveError()) {
          <span style="font-size: 12px; color: var(--status-failed); font-family: var(--font-mono);">
            {{ saveError() }}
          </span>
        }
      </div>
    </div>
  `,
})
export class TomlEditorComponent {
  private readonly tauri = inject(TauriService);
  private readonly configService = inject(ConfigService);

  readonly label = input.required<string>();
  readonly repoPath = input<string | null>(null);
  readonly scope = input.required<'global' | 'project'>();

  readonly toml = signal(DEFAULT_TOML_TEMPLATE(''));
  readonly savedToml = signal(DEFAULT_TOML_TEMPLATE(''));
  readonly loading = signal(true);
  readonly saving = signal(false);
  readonly saveMsg = signal<string | null>(null);
  readonly saveError = signal<string | null>(null);
  readonly validationError = signal<string | null>(null);

  private debounceTimer: ReturnType<typeof setTimeout> | null = null;

  readonly scopeLabel = computed(() => this.scope() === 'global' ? 'global' : 'project');

  readonly isSaveDisabled = computed(() =>
    this.saving() ||
    (this.scope() === 'project' && !this.repoPath()) ||
    this.validationError() !== null
  );

  constructor() {
    // Load TOML on mount/param change
    effect(() => {
      void this.loadToml();
    });

    // Auto-clear save message after 3 seconds
    effect(() => {
      const msg = this.saveMsg();
      if (msg) {
        const timer = setTimeout(() => this.saveMsg.set(null), 3000);
        return () => clearTimeout(timer);
      }
      return;
    });
  }

  private async loadToml(): Promise<void> {
    this.loading.set(true);
    try {
      let content: string;
      if (this.scope() === 'global') {
        content = await this.tauri.getRawGlobalConfigToml();
      } else if (this.repoPath()) {
        content = await this.tauri.getRawProjectConfigToml(this.repoPath()!);
      } else {
        content = '';
      }
      const resolved = content.length > 0 ? content : DEFAULT_TOML_TEMPLATE(this.label());
      this.toml.set(resolved);
      this.savedToml.set(resolved);
    } catch {
      const fallback = DEFAULT_TOML_TEMPLATE(this.label());
      this.toml.set(fallback);
      this.savedToml.set(fallback);
    } finally {
      this.loading.set(false);
    }
  }

  onTomlInput(event: Event): void {
    const value = (event.target as HTMLTextAreaElement).value;
    this.toml.set(value);
    this.configService.setDirty(true);
    this.validateDebounced(value);
  }

  private validateDebounced(toml: string): void {
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }
    this.debounceTimer = setTimeout(() => {
      void this.tauri.validateConfigToml(toml).then(error => {
        this.validationError.set(error ?? null);
      });
    }, VALIDATION_DEBOUNCE_MS);
  }

  async handleSave(): Promise<void> {
    if (this.scope() === 'project' && !this.repoPath()) return;
    if (this.validationError() !== null) return;

    this.saving.set(true);
    this.saveMsg.set(null);
    this.saveError.set(null);

    try {
      if (this.scope() === 'global') {
        await this.tauri.saveGlobalConfig(this.toml());
        await this.configService.fetchGlobalConfig();
      } else if (this.repoPath()) {
        await this.tauri.saveProjectConfig(this.repoPath()!, this.toml());
        await this.configService.fetchEffectiveConfig(this.repoPath()!);
      }
      this.savedToml.set(this.toml());
      this.saveMsg.set('Saved successfully.');
      this.configService.setDirty(false);
    } catch (e) {
      this.saveError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.saving.set(false);
    }
  }

  handleRevert(): void {
    this.toml.set(this.savedToml());
    this.validationError.set(null);
    this.configService.setDirty(false);
  }

  textareaStyle(): string {
    const hasError = this.validationError() !== null;
    return `
      min-height: 220px;
      padding: 12px 14px;
      resize: vertical;
      line-height: 1.7;
      font-size: 12px;
      border-color: ${hasError ? 'var(--status-failed)' : undefined};
      background: var(--bg-base, #0d0d0d);
      color: var(--text-primary);
    `.replace(/\n/g, ' ');
  }
}

@Component({
  selector: 'app-ai-integration-section',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div style="margin-top: var(--space-6); padding-top: var(--space-6); border-top: 1px solid var(--border-subtle);">
      <!-- Section heading -->
      <div style="font-family: var(--font-display); font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: var(--space-3);">
        AI Integration
      </div>

      <div style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); margin-bottom: var(--space-4);">
        Anthropic API key used for AI-assisted PROMPT.md review. Stored at
        <span style="color: var(--text-secondary);">~/.config/ralph-gui.toml</span>
        with restricted permissions (0600).
      </div>

      <!-- Key input with show/hide toggle -->
      <div style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-3);">
        <input
          [type]="showKey() ? 'text' : 'password'"
          class="form-input input-mono"
          [value]="localKey()"
          (input)="onKeyInput($event)"
          placeholder="Enter Anthropic API key (sk-ant-...)"
          style="flex: 1;"
        />
        <button class="btn btn-ghost" (click)="toggleShowKey()" style="flex-shrink: 0; min-width: 52px;">
          {{ showKey() ? 'Hide' : 'Show' }}
        </button>
      </div>

      <!-- Save button + feedback -->
      <div style="display: flex; align-items: center; gap: var(--space-3);">
        <button
          class="btn btn-primary"
          (click)="handleSave()"
          [disabled]="configService.aiApiKeySaveStatus() === 'saving'"
        >
          {{ configService.aiApiKeySaveStatus() === 'saving' ? 'Saving...' : 'Save API Key' }}
        </button>
        @if (configService.aiApiKeySaveStatus() === 'saved') {
          <span style="font-size: 12px; color: var(--status-completed); font-family: var(--font-mono);">
            Saved
          </span>
        }
        @if (configService.aiApiKeySaveStatus() === 'failed' && configService.aiApiKeyError()) {
          <span style="font-size: 12px; color: var(--status-failed); font-family: var(--font-mono);">
            {{ configService.aiApiKeyError() }}
          </span>
        }
      </div>
    </div>
  `,
})
export class AiIntegrationSectionComponent {
  readonly configService = inject(ConfigService);

  readonly localKey = signal('');
  readonly showKey = signal(false);

  constructor() {
    // Load API key on mount
    effect(() => {
      void this.configService.fetchAiApiKey();
    });

    // Sync local input when store key changes
    effect(() => {
      this.localKey.set(this.configService.aiApiKey());
    });

    // Auto-reset save status after 3 seconds
    effect(() => {
      if (this.configService.aiApiKeySaveStatus() === 'saved') {
        const timer = setTimeout(() => {
          this.configService.resetAiApiKeySaveStatus();
        }, 3000);
        return () => clearTimeout(timer);
      }
      return;
    });
  }

  onKeyInput(event: Event): void {
    this.localKey.set((event.target as HTMLInputElement).value);
  }

  toggleShowKey(): void {
    this.showKey.update(v => !v);
  }

  async handleSave(): Promise<void> {
    await this.configService.saveAiApiKey(this.localKey());
  }
}

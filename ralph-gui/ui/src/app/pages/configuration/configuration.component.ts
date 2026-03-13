import { Component, inject, signal, effect, computed, input, ChangeDetectionStrategy, forwardRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup } from '@angular/forms';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatSliderModule } from '@angular/material/slider';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ConfigService } from '../../services/config.service';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { ConfigView } from '../../types';

type TabId = 'effective' | 'global' | 'project';
type ViewMode = 'form' | 'toml';

const DEFAULT_TOML_TEMPLATE = (label: string) =>
  `# ${label} configuration (TOML)\n# Edit values below and save.\n\n[defaults]\n`;

const VALIDATION_DEBOUNCE_MS = 300;

@Component({
  selector: 'app-configuration',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatExpansionModule,
    MatSliderModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatInputModule,
    MatFormFieldModule,
    MatButtonModule,
    MatTooltipModule,
    forwardRef(() => ConfigTableComponent),
    forwardRef(() => TomlEditorComponent),
    forwardRef(() => AiIntegrationSectionComponent),
    forwardRef(() => ConfigFormComponent),
  ],
  templateUrl: './configuration.component.html',
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
  readonly viewMode = signal<ViewMode>('form');

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

  toggleViewMode(): void {
    this.viewMode.update(mode => mode === 'form' ? 'toml' : 'form');
  }
}

@Component({
  selector: 'app-config-form',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    MatExpansionModule,
    MatSliderModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatInputModule,
    MatFormFieldModule,
    MatTooltipModule,
  ],
  templateUrl: './config-form.component.html',
})
export class ConfigFormComponent {
  readonly config = input.required<ConfigView>();
  
  private readonly fb = inject(FormBuilder);
  private readonly tauri = inject(TauriService);
  private readonly configService = inject(ConfigService);

  readonly form: FormGroup;
  readonly saving = signal(false);
  readonly saveMsg = signal<string | null>(null);

  constructor() {
    this.form = this.fb.group({
      verbosity: [0],
      developer_iters: [1],
      reviewer_reviews: [2],
      review_depth: ['medium'],
      checkpoint_enabled: [true],
      isolation_mode: [false],
      interactive: [false],
    });

    effect(() => {
      const cfg = this.config();
      this.form.patchValue({
        verbosity: cfg.defaults?.verbosity ?? 0,
        developer_iters: cfg.defaults?.developer_iters ?? 1,
        reviewer_reviews: cfg.defaults?.reviewer_reviews ?? 2,
        review_depth: cfg.defaults?.review_depth ?? 'medium',
        checkpoint_enabled: cfg.defaults?.checkpoint_enabled ?? true,
        isolation_mode: cfg.defaults?.isolation_mode ?? false,
        interactive: cfg.defaults?.interactive ?? false,
      });
    });
  }

  async handleSave(): Promise<void> {
    if (this.form.invalid) return;

    this.saving.set(true);
    this.saveMsg.set(null);

    try {
      const formValue = this.form.value;
      const tomlLines = [
        '[defaults]',
        `verbosity = ${formValue.verbosity}`,
        `developer_iters = ${formValue.developer_iters}`,
        `reviewer_reviews = ${formValue.reviewer_reviews}`,
        `review_depth = "${formValue.review_depth}"`,
        `checkpoint_enabled = ${formValue.checkpoint_enabled}`,
        `isolation_mode = ${formValue.isolation_mode}`,
        `interactive = ${formValue.interactive}`,
      ];
      const toml = tomlLines.join('\n');
      await this.tauri.saveGlobalConfig(toml);
      await this.configService.fetchGlobalConfig();
      this.saveMsg.set('Saved successfully.');
      this.configService.setDirty(false);
    } catch (e) {
      this.saveMsg.set(e instanceof Error ? e.message : 'Save failed');
    } finally {
      this.saving.set(false);
    }
  }
}

@Component({
  selector: 'app-config-field',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './config-field.component.html',
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
  templateUrl: './config-table.component.html',
})
export class ConfigTableComponent {
  readonly config = input.required<ConfigView>();
}

@Component({
  selector: 'app-toml-editor',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './toml-editor.component.html',
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
  templateUrl: './ai-integration-section.component.html',
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

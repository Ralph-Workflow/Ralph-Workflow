import { Component, inject, signal, effect, computed, input, ChangeDetectionStrategy, forwardRef, HostListener, ViewChild, ElementRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { ReactiveFormsModule } from '@angular/forms';
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
import { NotificationService } from '../../services/notification.service';
import type { ConfigFieldWithSource, ConfigSource, ConfigView, EffectiveConfigWithSources } from '../../types';
import { ConfigFormComponent } from './config-form/config-form.component';
import { AgentChainsEditorComponent } from './agent-chains-editor/agent-chains-editor.component';
import { AgentToolsInlineComponent } from './agent-tools-inline/agent-tools-inline.component';

type TabId = 'effective' | 'global' | 'project';
type ViewMode = 'form' | 'toml';

const DEFAULT_TOML_TEMPLATE = (label: string) =>
  `# ${label} configuration (TOML)\n# Edit values below and save.\n\n[defaults]\n`;

const VALIDATION_DEBOUNCE_MS = 300;

function parseTomlToConfigView(toml: string): Partial<ConfigView> {
  const result: Partial<ConfigView> = {};
  const lines = toml.split('\n');
  let currentSection = '';

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      currentSection = trimmed.slice(1, -1);
      continue;
    }

    if (!trimmed || trimmed.startsWith('#')) continue;

    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;

    const key = trimmed.slice(0, eqIndex).trim();
    let value = trimmed.slice(eqIndex + 1).trim();

    if (value.startsWith('"') && value.endsWith('"')) {
      value = value.slice(1, -1);
    }

    const numValue = Number(value);
    const isNumber = !isNaN(numValue) && value !== '';

    if (currentSection === 'defaults' || currentSection === '') {
      if (key === 'verbosity') result.verbosity = isNumber ? numValue : 1;
      else if (key === 'developer_iters') result.developer_iters = isNumber ? numValue : 3;
      else if (key === 'reviewer_reviews') result.reviewer_reviews = isNumber ? numValue : 1;
      else if (key === 'review_depth') result.review_depth = value as ConfigView['review_depth'];
      else if (key === 'max_dev_continuations') result.max_dev_continuations = isNumber ? numValue : 3;
      else if (key === 'checkpoint_enabled') result.checkpoint_enabled = value === 'true';
      else if (key === 'isolation_mode') result.isolation_mode = value === 'true';
      else if (key === 'interactive') result.interactive = value === 'true';
      else if (key === 'prompt_path') result.prompt_path = value;
      else if (key === 'templates_dir') result.templates_dir = value;
      else if (key === 'developer_context') result.developer_context = value as ConfigView['developer_context'];
      else if (key === 'reviewer_context') result.reviewer_context = value as ConfigView['reviewer_context'];
      else if (key === 'force_universal_prompt') result.force_universal_prompt = value === 'true';
      else if (key === 'auto_detect_stack') result.auto_detect_stack = value === 'true';
    } else if (currentSection === 'retry') {
      if (key === 'max_retries') result.max_retries = isNumber ? numValue : 3;
      else if (key === 'max_same_agent_retries') result.max_same_agent_retries = isNumber ? numValue : 2;
      else if (key === 'retry_delay_ms') result.retry_delay_ms = isNumber ? numValue : 1000;
      else if (key === 'backoff_multiplier') result.backoff_multiplier = isNumber ? numValue : 2.0;
      else if (key === 'max_backoff_ms') result.max_backoff_ms = isNumber ? numValue : 30000;
      else if (key === 'max_fallback_cycles') result.max_fallback_cycles = isNumber ? numValue : 3;
    } else if (currentSection === 'git') {
      if (key === 'user_name') result.git_user_name = value;
      else if (key === 'user_email') result.git_user_email = value;
    }
  }

  return result;
}

const DEFAULT_CONFIG_VALUES: ConfigView = {
  verbosity: 1,
  developer_iters: 3,
  reviewer_reviews: 1,
  checkpoint_enabled: true,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 3,
  prompt_path: '',
  templates_dir: '',
  developer_context: 'normal',
  reviewer_context: 'normal',
  force_universal_prompt: false,
  auto_detect_stack: true,
  max_retries: 3,
  max_same_agent_retries: 2,
  retry_delay_ms: 1000,
  backoff_multiplier: 2.0,
  max_backoff_ms: 30000,
  max_fallback_cycles: 3,
  git_user_name: '',
  git_user_email: '',
};

/**
 * Converts a ConfigView to a TOML string for the [defaults] section.
 * Only includes fields that are present and non-empty.
 */
function configViewToToml(cfg: ConfigView): string {
  const lines: string[] = ['[defaults]'];

  lines.push(`verbosity = ${cfg.verbosity}`);
  lines.push(`developer_iters = ${cfg.developer_iters}`);
  lines.push(`reviewer_reviews = ${cfg.reviewer_reviews}`);
  lines.push(`review_depth = "${cfg.review_depth}"`);
  lines.push(`max_dev_continuations = ${cfg.max_dev_continuations}`);
  lines.push(`checkpoint_enabled = ${cfg.checkpoint_enabled}`);
  lines.push(`isolation_mode = ${cfg.isolation_mode}`);
  lines.push(`interactive = ${cfg.interactive}`);

  if (cfg.developer_context) lines.push(`developer_context = "${cfg.developer_context}"`);
  if (cfg.reviewer_context) lines.push(`reviewer_context = "${cfg.reviewer_context}"`);
  if (cfg.force_universal_prompt !== undefined) lines.push(`force_universal_prompt = ${cfg.force_universal_prompt}`);
  if (cfg.auto_detect_stack !== undefined) lines.push(`auto_detect_stack = ${cfg.auto_detect_stack}`);
  if (cfg.prompt_path) lines.push(`prompt_path = "${cfg.prompt_path}"`);
  if (cfg.templates_dir) lines.push(`templates_dir = "${cfg.templates_dir}"`);

  const retryLines: string[] = [];
  if (cfg.max_retries !== undefined) retryLines.push(`max_retries = ${cfg.max_retries}`);
  if (cfg.max_same_agent_retries !== undefined) retryLines.push(`max_same_agent_retries = ${cfg.max_same_agent_retries}`);
  if (cfg.retry_delay_ms !== undefined) retryLines.push(`retry_delay_ms = ${cfg.retry_delay_ms}`);
  if (cfg.backoff_multiplier !== undefined) retryLines.push(`backoff_multiplier = ${cfg.backoff_multiplier}`);
  if (cfg.max_backoff_ms !== undefined) retryLines.push(`max_backoff_ms = ${cfg.max_backoff_ms}`);
  if (cfg.max_fallback_cycles !== undefined) retryLines.push(`max_fallback_cycles = ${cfg.max_fallback_cycles}`);
  if (retryLines.length > 0) {
    lines.push('', '[retry]', ...retryLines);
  }

  const gitLines: string[] = [];
  if (cfg.git_user_name) gitLines.push(`user_name = "${cfg.git_user_name}"`);
  if (cfg.git_user_email) gitLines.push(`user_email = "${cfg.git_user_email}"`);
  if (gitLines.length > 0) {
    lines.push('', '[git]', ...gitLines);
  }

  return lines.join('\n');
}

@Component({
  selector: 'app-configuration',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    ReactiveFormsModule,
    MatExpansionModule,
    MatSliderModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatInputModule,
    MatFormFieldModule,
    MatButtonModule,
    MatTooltipModule,
    ConfigFormComponent,
    AgentChainsEditorComponent,
    AgentToolsInlineComponent,
    forwardRef(() => ConfigTableComponent),
    forwardRef(() => TomlEditorComponent),
    forwardRef(() => AiIntegrationSectionComponent),
  ],
  templateUrl: './configuration.component.html',
})
export class ConfigurationComponent {
  readonly configService = inject(ConfigService);
  readonly worktreesService = inject(WorktreesService);
  private readonly tauri = inject(TauriService);
  private readonly notifications = inject(NotificationService);

  @ViewChild('searchInput') searchInputRef!: ElementRef<HTMLInputElement>;

  readonly tabs: { id: TabId; label: string }[] = [
    { id: 'effective', label: 'Effective' },
    { id: 'global', label: 'Global' },
    { id: 'project', label: 'Project' },
  ];

  private readonly _activeTab = signal<TabId>('effective');
  private readonly _viewMode = signal<ViewMode>('form');
  private readonly _formPendingConfig = signal<ConfigView | null>(null);
  private readonly _formSaving = signal(false);
  private readonly _formSaveMsg = signal<string | null>(null);
  private readonly _formSaveError = signal<string | null>(null);
  private readonly _syncedToml = signal<string>('');

  /** Raw TOML for the global config (used by the AgentChainsEditor in form view). */
  private readonly _rawGlobalToml = signal<string>('');

  /** Effective config with per-field source provenance (for Effective tab source badges). */
  private readonly _effectiveWithSources = signal<EffectiveConfigWithSources | null>(null);

  /** Search query for filtering config settings. */
  private readonly _searchQuery = signal<string>('');

  /** Project view mode: 'form' or 'toml' */
  private readonly _projectViewMode = signal<ViewMode>('toml');

  /** Pending project config for form view dirty tracking. */
  private readonly _formPendingProjectConfig = signal<ConfigView | null>(null);

  /** Project config parsed from TOML. */
  private readonly _projectConfig = signal<ConfigView | null>(null);

  /** Raw TOML for the project config. */
  private readonly _rawProjectToml = signal<string>('');

  private readonly _repoPath = computed(() => {
    const activePath = this.worktreesService.activeWorktreePath();
    if (activePath) return activePath;
    const mainWorktree = this.worktreesService.worktrees().find(wt => wt.is_main);
    return mainWorktree?.path ?? null;
  });

  private readonly _displayConfig = computed(() => {
    if (this._activeTab() !== 'effective') return null;
    return this.configService.effectiveConfig() ?? this.configService.globalConfig();
  });

  private readonly _projectConfigPath = computed(() => {
    const repo = this._repoPath();
    return repo ? `${repo}/.ralph/config.toml` : '<repo>/.ralph/config.toml';
  });

  get activeTab() { return this._activeTab(); }
  get viewMode() { return this._viewMode(); }
  get repoPath() { return this._repoPath(); }
  get displayConfig() { return this._displayConfig(); }
  get projectConfigPath() { return this._projectConfigPath(); }
  get globalStatus() { return this.configService.globalStatus(); }
  get globalConfig() { return this.configService.globalConfig(); }
  get formPendingConfig() { return this._formPendingConfig(); }
  get formSaving() { return this._formSaving(); }
  get formSaveMsg() { return this._formSaveMsg(); }
  get formSaveError() { return this._formSaveError(); }
  get formHasPendingChanges() { return this._formPendingConfig() !== null; }
  get rawGlobalToml() { return this._rawGlobalToml(); }
  get effectiveWithSources() { return this._effectiveWithSources(); }
  get searchQuery() { return this._searchQuery(); }
  get projectViewMode() { return this._projectViewMode(); }
  get formPendingProjectConfig() { return this._formPendingProjectConfig(); }
  get projectConfig() { return this._projectConfig(); }
  get rawProjectToml() { return this._rawProjectToml(); }
  get hasProjectPendingChanges() { return this._formPendingProjectConfig() !== null; }
  get syncedToml() { return this._syncedToml(); }

  /** Computed default config values for comparison. */
  readonly defaultConfig = computed<ConfigView>(() => DEFAULT_CONFIG_VALUES);

  /** Computed flag indicating whether search has results. */
  readonly hasSearchResults = computed(() => {
    const query = this._searchQuery().toLowerCase().trim();
    if (!query) return true;
    const config = this._displayConfig() ?? this.configService.globalConfig();
    if (!config) return false;
    return this._configMatchesQuery(config, query);
  });

  /** Check if any config field matches the search query. */
  private _configMatchesQuery(config: ConfigView, query: string): boolean {
    const fieldLabels: Record<keyof ConfigView, string> = {
      verbosity: 'verbosity',
      developer_iters: 'developer iterations',
      reviewer_reviews: 'reviewer reviews',
      review_depth: 'review depth',
      max_dev_continuations: 'max dev continuations',
      checkpoint_enabled: 'checkpoint enabled',
      isolation_mode: 'isolation mode',
      interactive: 'interactive',
      prompt_path: 'prompt path',
      templates_dir: 'templates directory',
      developer_context: 'developer context',
      reviewer_context: 'reviewer context',
      force_universal_prompt: 'force universal prompt',
      auto_detect_stack: 'auto detect stack',
      max_retries: 'max retries',
      max_same_agent_retries: 'max same agent retries',
      retry_delay_ms: 'retry delay',
      backoff_multiplier: 'backoff multiplier',
      max_backoff_ms: 'max backoff',
      max_fallback_cycles: 'max fallback cycles',
      git_user_name: 'git user name',
      git_user_email: 'git email',
    };

    for (const [key, label] of Object.entries(fieldLabels)) {
      if (label.toLowerCase().includes(query)) return true;
      const value = config[key as keyof ConfigView];
      if (value !== undefined && String(value).toLowerCase().includes(query)) return true;
    }
    return false;
  }

  constructor() {
    // Fetch global config on mount
    effect(() => {
      void this.configService.fetchGlobalConfig();
      void this.tauri.getRawGlobalConfigToml().then(toml => this._rawGlobalToml.set(toml)).catch(() => void 0);
    });

    // Fetch effective config when repo changes
    effect(() => {
      const repo = this._repoPath();
      if (repo) {
        void this.configService.fetchEffectiveConfig(repo);
        void this.tauri.getEffectiveConfigWithSources(repo)
          .then(result => this._effectiveWithSources.set(result))
          .catch(() => this._effectiveWithSources.set(null));
        void this.tauri.getRawProjectConfigToml(repo)
          .then(toml => {
            this._rawProjectToml.set(toml);
            const parsed = parseTomlToConfigView(toml);
            this._projectConfig.set({ ...DEFAULT_CONFIG_VALUES, ...parsed });
          })
          .catch(() => {
            this._rawProjectToml.set('');
            this._projectConfig.set(null);
          });
      }
    });

    // Auto-clear save message after 3 seconds
    effect(() => {
      const msg = this._formSaveMsg();
      if (msg) {
        const timer = setTimeout(() => this._formSaveMsg.set(null), 3000);
        return () => clearTimeout(timer);
      }
      return;
    });
  }

  setActiveTab(tab: TabId): void {
    this._activeTab.set(tab);
  }

  toggleViewMode(): void {
    const currentMode = this._viewMode();
    const newMode = currentMode === 'form' ? 'toml' : 'form';

    if (newMode === 'toml' && this._formPendingConfig()) {
      const toml = configViewToToml(this._formPendingConfig()!);
      this._syncedToml.set(toml);
    } else if (newMode === 'form') {
      const tomlContent = this._rawGlobalToml();
      if (tomlContent) {
        const parsed = parseTomlToConfigView(tomlContent);
        const merged: ConfigView = { ...DEFAULT_CONFIG_VALUES, ...parsed };
        this._formPendingConfig.set(merged);
      }
    }

    this._viewMode.set(newMode);
  }

  /** Returns the source (default/global/project) for a config field in the Effective tab. */
  getFieldSource(fieldName: string): ConfigSource {
    const sources = this._effectiveWithSources()?.sources;
    if (!sources) return 'default';
    return sources.find((s: ConfigFieldWithSource) => s.field_name === fieldName)?.source ?? 'default';
  }

  /** Called by the config form when any field changes. Tracks pending config. */
  onFormConfigChange(config: ConfigView): void {
    this._formPendingConfig.set(config);
    this.configService.setDirty(true);
  }

  /** Saves the pending form config to the global TOML file. */
  async saveFormConfig(): Promise<void> {
    const pending = this._formPendingConfig();
    if (!pending) return;

    this._formSaving.set(true);
    this._formSaveMsg.set(null);
    this._formSaveError.set(null);

    try {
      const toml = configViewToToml(pending);
      await this.tauri.saveGlobalConfig(toml);
      await this.configService.fetchGlobalConfig();
      this._formPendingConfig.set(null);
      this._formSaveMsg.set('Saved successfully.');
      this.configService.setDirty(false);
      this.notifications.add({ type: 'success', message: 'Configuration saved successfully.' });
    } catch (e) {
      this._formSaveError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this._formSaving.set(false);
    }
  }

  /** Reverts pending form changes. */
  revertFormConfig(): void {
    this._formPendingConfig.set(null);
    this._formSaveMsg.set(null);
    this._formSaveError.set(null);
    this.configService.setDirty(false);
  }

  /**
   * Called when the AgentChainsEditor emits an updated raw TOML.
   * Immediately saves the new TOML (chains/drains changes are always auto-saved).
   */
  async onChainsTomlChange(updatedToml: string): Promise<void> {
    this._rawGlobalToml.set(updatedToml);
    this.configService.setDirty(true);
    try {
      await this.tauri.saveGlobalConfig(updatedToml);
      await this.configService.fetchGlobalConfig();
      this.configService.setDirty(false);
    } catch (e) {
      console.error('Failed to save agent chains config:', e);
    }
  }

  /** Updates the search query. */
  setSearchQuery(query: string): void {
    this._searchQuery.set(query);
  }

  /** Clears the search query. */
  clearSearch(): void {
    this._searchQuery.set('');
  }

  /** Set project view mode. */
  setProjectViewMode(mode: ViewMode): void {
    this._projectViewMode.set(mode);
  }

  /** Toggle project view mode. */
  toggleProjectViewMode(): void {
    const currentMode = this._projectViewMode();
    const newMode = currentMode === 'form' ? 'toml' : 'form';

    if (newMode === 'toml' && this._formPendingProjectConfig()) {
      const toml = configViewToToml(this._formPendingProjectConfig()!);
      this._rawProjectToml.set(toml);
    } else if (newMode === 'form') {
      const tomlContent = this._rawProjectToml();
      if (tomlContent) {
        const parsed = parseTomlToConfigView(tomlContent);
        const merged: ConfigView = { ...DEFAULT_CONFIG_VALUES, ...parsed };
        this._formPendingProjectConfig.set(merged);
      }
    }

    this._projectViewMode.set(newMode);
  }

  /** Called by the config form when project config changes. */
  onProjectFormConfigChange(config: ConfigView): void {
    this._formPendingProjectConfig.set(config);
    this.configService.setDirty(true);
  }

  /** Save project form config. */
  async saveProjectFormConfig(): Promise<void> {
    const pending = this._formPendingProjectConfig();
    if (!pending || !this._repoPath()) return;

    this._formSaving.set(true);
    this._formSaveMsg.set(null);
    this._formSaveError.set(null);

    try {
      const toml = configViewToToml(pending);
      await this.tauri.saveProjectConfig(this._repoPath()!, toml);
      await this.configService.fetchEffectiveConfig(this._repoPath()!);
      this._formPendingProjectConfig.set(null);
      this._formSaveMsg.set('Saved project configuration successfully.');
      this.configService.setDirty(false);
      this.notifications.add({ type: 'success', message: 'Project configuration saved successfully.' });
    } catch (e) {
      this._formSaveError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this._formSaving.set(false);
    }
  }

  /** Revert project form changes. */
  revertProjectFormConfig(): void {
    this._formPendingProjectConfig.set(null);
    this._formSaveMsg.set(null);
    this._formSaveError.set(null);
    this.configService.setDirty(false);
  }

  /** Get the default value for a config field. */
  getDefaultValue(fieldName: string): string | number | boolean | undefined {
    return (DEFAULT_CONFIG_VALUES as unknown as Record<string, unknown>)[fieldName] as string | number | boolean | undefined;
  }

  /** Check if a field value differs from its default. */
  isFieldOverridden(fieldName: string, value: string | number | boolean | undefined): boolean {
    const defaultVal = this.getDefaultValue(fieldName);
    return value !== defaultVal;
  }

  @HostListener('window:keydown.control.f', ['$event'])
  onFocusSearch(event: Event): void {
    event.preventDefault();
    this.searchInputRef?.nativeElement?.focus();
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
  /** Optional source indicator for the Effective tab (default/global/project). */
  readonly source = input<ConfigSource | null>(null);

  private readonly _displayValue = computed(() => {
    const v = this.value();
    return typeof v === 'boolean' ? (v ? 'true' : 'false') : String(v);
  });

  private readonly _valueStyle = computed(() => {
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

  /** CSS classes for the source badge (Tailwind-first with identifying class). */
  get sourceBadgeClass(): string {
    const src = this.source();
    if (src === 'project') {
      return 'source-badge source-badge--project text-xs px-1.5 py-0.5 rounded bg-amber-900/30 text-amber-400 font-semibold font-mono';
    }
    if (src === 'global') {
      return 'source-badge source-badge--global text-xs px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 font-semibold font-mono';
    }
    return 'source-badge source-badge--default text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 font-semibold font-mono';
  }

  /** Human-readable label for the source badge. */
  get sourceBadgeLabel(): string {
    const src = this.source();
    if (src === 'project') return 'Project';
    if (src === 'global') return 'Global';
    return 'Default';
  }

  /** Tooltip text for the source badge showing the config file path. */
  get sourceBadgeTooltip(): string {
    const src = this.source();
    if (src === 'project') return 'Set in project config: .agent/ralph-workflow.toml';
    if (src === 'global') return 'Set in global config: ~/.config/ralph-workflow.toml';
    return 'Using compiled-in default value';
  }

  get labelValue() { return this.label(); }
  get descriptionValue() { return this.description(); }
  get displayValue() { return this._displayValue(); }
  get valueStyle() { return this._valueStyle(); }
  get sourceValue() { return this.source(); }
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
  /** Optional map of field name → source for the Effective tab source badges. */
  readonly sources = input<ConfigFieldWithSource[] | null>(null);

  /** Pre-computed source map keyed by field_name for O(1) lookup in template. */
  private readonly _sourceMap = computed<Record<string, ConfigSource>>(() => {
    const srcs = this.sources();
    if (!srcs) return {};
    return Object.fromEntries(srcs.map(s => [s.field_name, s.source]));
  });

  /** Source map exposed to template. */
  get sourceMap(): Record<string, ConfigSource> { return this._sourceMap(); }

  getSource(fieldName: string): ConfigSource | null {
    const srcs = this.sources();
    if (!srcs) return null;
    return srcs.find(s => s.field_name === fieldName)?.source ?? null;
  }

  get configValue() { return this.config(); }
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

  private readonly _toml = signal(DEFAULT_TOML_TEMPLATE(''));
  private readonly _savedToml = signal(DEFAULT_TOML_TEMPLATE(''));
  private readonly _loading = signal(true);
  private readonly _saving = signal(false);
  private readonly _saveMsg = signal<string | null>(null);
  private readonly _saveError = signal<string | null>(null);
  private readonly _validationError = signal<string | null>(null);

  private debounceTimer: ReturnType<typeof setTimeout> | null = null;

  private readonly _scopeLabel = computed(() => this.scope() === 'global' ? 'global' : 'project');

  private readonly _isSaveDisabled = computed(() =>
    this._saving() ||
    (this.scope() === 'project' && !this.repoPath()) ||
    this._validationError() !== null
  );

  private readonly _textareaStyle = computed(() => {
    const hasError = this._validationError() !== null;
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
  });

  get toml() { return this._toml(); }
  get savedToml() { return this._savedToml(); }
  get loading() { return this._loading(); }
  get saving() { return this._saving(); }
  get saveMsg() { return this._saveMsg(); }
  get saveError() { return this._saveError(); }
  get validationError() { return this._validationError(); }
  get scopeLabel() { return this._scopeLabel(); }
  get isSaveDisabled() { return this._isSaveDisabled(); }
  get textareaStyle() { return this._textareaStyle(); }

  constructor() {
    // Load TOML on mount/param change
    effect(() => {
      void this.loadToml();
    });

    // Auto-clear save message after 3 seconds
    effect(() => {
      const msg = this._saveMsg();
      if (msg) {
        const timer = setTimeout(() => this._saveMsg.set(null), 3000);
        return () => clearTimeout(timer);
      }
      return;
    });
  }

  private async loadToml(): Promise<void> {
    this._loading.set(true);
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
      this._toml.set(resolved);
      this._savedToml.set(resolved);
    } catch {
      const fallback = DEFAULT_TOML_TEMPLATE(this.label());
      this._toml.set(fallback);
      this._savedToml.set(fallback);
    } finally {
      this._loading.set(false);
    }
  }

  onTomlInput(event: Event): void {
    const value = (event.target as HTMLTextAreaElement).value;
    this._toml.set(value);
    this.configService.setDirty(true);
    this.validateDebounced(value);
  }

  private validateDebounced(toml: string): void {
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }
    this.debounceTimer = setTimeout(() => {
      void this.tauri.validateConfigToml(toml).then(error => {
        this._validationError.set(error ?? null);
      });
    }, VALIDATION_DEBOUNCE_MS);
  }

  async handleSave(): Promise<void> {
    if (this.scope() === 'project' && !this.repoPath()) return;
    if (this._validationError() !== null) return;

    this._saving.set(true);
    this._saveMsg.set(null);
    this._saveError.set(null);

    try {
      if (this.scope() === 'global') {
        await this.tauri.saveGlobalConfig(this._toml());
        await this.configService.fetchGlobalConfig();
      } else if (this.repoPath()) {
        await this.tauri.saveProjectConfig(this.repoPath()!, this._toml());
        await this.configService.fetchEffectiveConfig(this.repoPath()!);
      }
      this._savedToml.set(this._toml());
      this._saveMsg.set('Saved successfully.');
      this.configService.setDirty(false);
    } catch (e) {
      this._saveError.set(e instanceof Error ? e.message : String(e));
    } finally {
      this._saving.set(false);
    }
  }

  handleRevert(): void {
    this._toml.set(this._savedToml());
    this._validationError.set(null);
    this.configService.setDirty(false);
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

  private readonly _localKey = signal('');
  private readonly _showKey = signal(false);

  get localKey() { return this._localKey(); }
  get showKey() { return this._showKey(); }
  get aiApiKeySaveStatus() { return this.configService.aiApiKeySaveStatus(); }
  get aiApiKeyError() { return this.configService.aiApiKeyError(); }

  constructor() {
    // Load API key on mount
    effect(() => {
      void this.configService.fetchAiApiKey();
    });

    // Sync local input when store key changes
    effect(() => {
      this._localKey.set(this.configService.aiApiKey());
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
    this._localKey.set((event.target as HTMLInputElement).value);
  }

  toggleShowKey(): void {
    this._showKey.update(v => !v);
  }

  async handleSave(): Promise<void> {
    await this.configService.saveAiApiKey(this._localKey());
  }
}

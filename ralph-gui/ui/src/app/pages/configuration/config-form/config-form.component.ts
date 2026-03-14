import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import type { ConfigFieldWithSource, ConfigSource, ConfigView } from '../../../types';
import { ContextualHelpComponent } from '../../../components/contextual-help/contextual-help.component';

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

interface ValidationWarning {
  field: string;
  message: string;
}

function getFieldValidationWarnings(value: Partial<ConfigView>): ValidationWarning[] {
  const warnings: ValidationWarning[] = [];

  if ((value.developer_iters ?? 0) > 10) {
    warnings.push({
      field: 'developer_iters',
      message: 'High iteration count may increase cost and execution time.',
    });
  }
  if ((value.max_retries ?? 0) > 5) {
    warnings.push({
      field: 'max_retries',
      message: 'High retry values may increase cost and delay feedback.',
    });
  }
  if ((value.retry_delay_ms ?? 0) < 500) {
    warnings.push({
      field: 'retry_delay_ms',
      message: 'Very short retry delays may trigger rate limits.',
    });
  }
  if ((value.backoff_multiplier ?? 0) > 3.0) {
    warnings.push({
      field: 'backoff_multiplier',
      message: 'High backoff multiplier will create very long waits between retries.',
    });
  }
  if ((value.max_fallback_cycles ?? 0) > 10) {
    warnings.push({
      field: 'max_fallback_cycles',
      message: 'Many fallback cycles may exhaust agent budgets before completing.',
    });
  }

  return warnings;
}

@Component({
  selector: 'app-config-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    ReactiveFormsModule,
    MatExpansionModule,
    MatInputModule,
    MatFormFieldModule,
    MatSelectModule,
    MatSlideToggleModule,
    MatButtonModule,
    MatIconModule,
    MatTooltipModule,
    ContextualHelpComponent,
  ],
  templateUrl: './config-form.component.html',
  styleUrl: './config-form.component.css',
})
export class ConfigFormComponent {
  readonly config = input.required<ConfigView>();
  readonly searchQuery = input<string>('');
  readonly defaults = input<ConfigView>(DEFAULT_CONFIG_VALUES);
  readonly sources = input<ConfigFieldWithSource[] | null>(null);

  readonly configChange = output<ConfigView>();

  private readonly fb = inject(FormBuilder);
  // private readonly tauri = inject(TauriService);

  readonly form: FormGroup = this.fb.group({
    verbosity: [0, [Validators.min(0), Validators.max(4)]],
    developer_iters: [1, [Validators.min(1), Validators.max(20)]],
    reviewer_reviews: [1, [Validators.min(0), Validators.max(10)]],
    review_depth: ['standard'],
    max_dev_continuations: [3, [Validators.min(1), Validators.max(10)]],
    prompt_path: [''],
    templates_dir: [''],
    checkpoint_enabled: [true],
    isolation_mode: [false],
    interactive: [false],
    developer_context: ['normal'],
    reviewer_context: ['normal'],
    force_universal_prompt: [false],
    auto_detect_stack: [true],
    max_retries: [3, [Validators.min(1), Validators.max(10)]],
    max_same_agent_retries: [2, [Validators.min(1), Validators.max(5)]],
    retry_delay_ms: [1000, [Validators.min(100), Validators.max(60000)]],
    backoff_multiplier: [2.0, [Validators.min(1.0), Validators.max(5.0)]],
    max_backoff_ms: [30000, [Validators.min(1000), Validators.max(120000)]],
    max_fallback_cycles: [3, [Validators.min(1), Validators.max(20)]],
    git_user_name: [''],
    git_user_email: ['', [Validators.email]],
  });

  private readonly _syncedConfig = signal<ConfigView | null>(null);
  private readonly _sourceMap = computed<Record<string, ConfigSource>>(() => {
    const srcs = this.sources();
    if (!srcs) return {};
    return Object.fromEntries(srcs.map(s => [s.field_name, s.source]));
  });

  readonly validationWarnings = computed<ValidationWarning[]>(() => {
    const value = this._buildConfig(this.form.value);
    return getFieldValidationWarnings(value);
  });

  readonly generalSectionVisible = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (!query) return true;
    const fields = [
      'verbosity', 'developer iterations', 'reviewer reviews', 'review depth',
      'max dev continuations', 'prompt path', 'templates directory',
    ];
    return fields.some(f => f.includes(query));
  });

  readonly executionSectionVisible = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (!query) return true;
    const fields = [
      'checkpoint', 'isolation', 'interactive', 'developer context',
      'reviewer context', 'force universal prompt', 'auto detect stack',
    ];
    return fields.some(f => f.includes(query));
  });

  readonly retrySectionVisible = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (!query) return true;
    const fields = [
      'retry', 'fallback', 'backoff', 'delay',
    ];
    return fields.some(f => f.includes(query));
  });

  readonly gitSectionVisible = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (!query) return true;
    const fields = ['git', 'user name', 'email'];
    return fields.some(f => f.includes(query));
  });

  readonly agentToolsSectionVisible = computed(() => {
    const query = this.searchQuery().toLowerCase().trim();
    if (!query) return true;
    const fields = ['agent tools', 'cli tools', 'claude', 'codex', 'opencode'];
    return fields.some(f => f.includes(query));
  });

  readonly hasVisibleSections = computed(() => {
    return (
      this.generalSectionVisible() ||
      this.executionSectionVisible() ||
      this.retrySectionVisible() ||
      this.gitSectionVisible() ||
      this.agentToolsSectionVisible()
    );
  });

  readonly hasSearchQuery = computed(() => {
    return this.searchQuery().trim().length > 0;
  });

  constructor() {
    effect(() => {
      const cfg = this.config();
      this._syncedConfig.set(cfg);
      this.form.patchValue(
        {
          verbosity: cfg.verbosity,
          developer_iters: cfg.developer_iters,
          reviewer_reviews: cfg.reviewer_reviews,
          review_depth: cfg.review_depth,
          max_dev_continuations: cfg.max_dev_continuations,
          prompt_path: cfg.prompt_path ?? '',
          templates_dir: cfg.templates_dir ?? '',
          checkpoint_enabled: cfg.checkpoint_enabled,
          isolation_mode: cfg.isolation_mode,
          interactive: cfg.interactive,
          developer_context: cfg.developer_context ?? 'normal',
          reviewer_context: cfg.reviewer_context ?? 'normal',
          force_universal_prompt: cfg.force_universal_prompt ?? false,
          auto_detect_stack: cfg.auto_detect_stack ?? true,
          max_retries: cfg.max_retries ?? 3,
          max_same_agent_retries: cfg.max_same_agent_retries ?? 2,
          retry_delay_ms: cfg.retry_delay_ms ?? 1000,
          backoff_multiplier: cfg.backoff_multiplier ?? 2.0,
          max_backoff_ms: cfg.max_backoff_ms ?? 30000,
          max_fallback_cycles: cfg.max_fallback_cycles ?? 3,
          git_user_name: cfg.git_user_name ?? '',
          git_user_email: cfg.git_user_email ?? '',
        },
        { emitEvent: false },
      );
    });

    this.form.valueChanges.subscribe(value => {
      if (this.form.valid) {
        this.configChange.emit(this._buildConfig(value));
      }
    });
  }

  isFieldDirty(field: string): boolean {
    const original = this._syncedConfig();
    if (!original) return false;
    const control = this.form.get(field);
    if (!control) return false;
    return control.value !== (original as unknown as Record<string, unknown>)[field];
  }

  isFieldOverridden(field: string): boolean {
    const control = this.form.get(field);
    if (!control) return false;
    const defaultVal = (DEFAULT_CONFIG_VALUES as unknown as Record<string, unknown>)[field];
    return control.value !== defaultVal;
  }

  getSource(fieldName: string): ConfigSource | null {
    return this._sourceMap()[fieldName] ?? null;
  }

  getSourceBadgeClass(fieldName: string): string {
    const src = this.getSource(fieldName);
    if (src === 'project') {
      return 'source-badge source-badge--project text-[10px] px-1.5 py-0.5 rounded bg-amber-900/30 text-amber-400 font-semibold font-mono ml-2';
    }
    if (src === 'global') {
      return 'source-badge source-badge--global text-[10px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 font-semibold font-mono ml-2';
    }
    return '';
  }

  getSourceBadgeLabel(fieldName: string): string {
    const src = this.getSource(fieldName);
    if (src === 'project') return 'Project';
    if (src === 'global') return 'Global';
    return '';
  }

  getDefaultValue(fieldName: string): string | number | boolean | undefined {
    return (DEFAULT_CONFIG_VALUES as unknown as Record<string, unknown>)[fieldName] as string | number | boolean | undefined;
  }

  getDefaultValueLabel(fieldName: string): string {
    const val = this.getDefaultValue(fieldName);
    if (val === undefined) return '';
    if (typeof val === 'boolean') return val ? 'on' : 'off';
    return String(val);
  }

  getValidationWarning(fieldName: string): string | null {
    const warning = this.validationWarnings().find(w => w.field === fieldName);
    return warning?.message ?? null;
  }

  async browsePromptPath(): Promise<void> {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const selected = await open({
      multiple: false,
      filters: [{ name: 'Markdown', extensions: ['md'] }],
      title: 'Select Prompt File',
    });
    if (typeof selected === 'string') {
      this.form.get('prompt_path')?.setValue(selected);
    }
  }

  async browseTemplatesDir(): Promise<void> {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const selected = await open({
      directory: true,
      multiple: false,
      title: 'Select Templates Directory',
    });
    if (typeof selected === 'string') {
      this.form.get('templates_dir')?.setValue(selected);
    }
  }

  get isVerbosityDirty() { return this.isFieldDirty('verbosity'); }
  get isDeveloperItersDirty() { return this.isFieldDirty('developer_iters'); }
  get isReviewerReviewsDirty() { return this.isFieldDirty('reviewer_reviews'); }
  get isMaxDevContinuationsDirty() { return this.isFieldDirty('max_dev_continuations'); }
  get isReviewDepthDirty() { return this.isFieldDirty('review_depth'); }
  get isPromptPathDirty() { return this.isFieldDirty('prompt_path'); }
  get isTemplatesDirDirty() { return this.isFieldDirty('templates_dir'); }
  get isCheckpointEnabledDirty() { return this.isFieldDirty('checkpoint_enabled'); }
  get isIsolationModeDirty() { return this.isFieldDirty('isolation_mode'); }
  get isInteractiveDirty() { return this.isFieldDirty('interactive'); }
  get isDeveloperContextDirty() { return this.isFieldDirty('developer_context'); }
  get isReviewerContextDirty() { return this.isFieldDirty('reviewer_context'); }
  get isForceUniversalPromptDirty() { return this.isFieldDirty('force_universal_prompt'); }
  get isAutoDetectStackDirty() { return this.isFieldDirty('auto_detect_stack'); }
  get isMaxRetriesDirty() { return this.isFieldDirty('max_retries'); }
  get isMaxSameAgentRetriesDirty() { return this.isFieldDirty('max_same_agent_retries'); }
  get isRetryDelayMsDirty() { return this.isFieldDirty('retry_delay_ms'); }
  get isBackoffMultiplierDirty() { return this.isFieldDirty('backoff_multiplier'); }
  get isMaxBackoffMsDirty() { return this.isFieldDirty('max_backoff_ms'); }
  get isMaxFallbackCyclesDirty() { return this.isFieldDirty('max_fallback_cycles'); }
  get isGitUserNameDirty() { return this.isFieldDirty('git_user_name'); }
  get isGitUserEmailDirty() { return this.isFieldDirty('git_user_email'); }

  get developerItersHasRangeError() {
    const c = this.form.get('developer_iters');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get reviewerReviewsHasRangeError() {
    const c = this.form.get('reviewer_reviews');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get maxDevContinuationsHasRangeError() {
    const c = this.form.get('max_dev_continuations');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get maxRetriesHasRangeError() {
    const c = this.form.get('max_retries');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get maxSameAgentRetriesHasRangeError() {
    const c = this.form.get('max_same_agent_retries');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get retryDelayMsHasRangeError() {
    const c = this.form.get('retry_delay_ms');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get backoffMultiplierHasRangeError() {
    const c = this.form.get('backoff_multiplier');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get maxBackoffMsHasRangeError() {
    const c = this.form.get('max_backoff_ms');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get maxFallbackCyclesHasRangeError() {
    const c = this.form.get('max_fallback_cycles');
    return (c?.hasError('min') ?? false) || (c?.hasError('max') ?? false);
  }
  get gitUserEmailHasError() {
    const c = this.form.get('git_user_email');
    return c?.hasError('email') ?? false;
  }

  private _buildConfig(value: Partial<ConfigView>): ConfigView {
    return {
      verbosity: value.verbosity ?? 0,
      developer_iters: value.developer_iters ?? 1,
      reviewer_reviews: value.reviewer_reviews ?? 1,
      review_depth: value.review_depth ?? 'standard',
      max_dev_continuations: value.max_dev_continuations ?? 3,
      prompt_path: value.prompt_path ?? '',
      templates_dir: value.templates_dir ?? '',
      checkpoint_enabled: value.checkpoint_enabled ?? true,
      isolation_mode: value.isolation_mode ?? false,
      interactive: value.interactive ?? false,
      developer_context: value.developer_context ?? 'normal',
      reviewer_context: value.reviewer_context ?? 'normal',
      force_universal_prompt: value.force_universal_prompt ?? false,
      auto_detect_stack: value.auto_detect_stack ?? true,
      max_retries: value.max_retries ?? 3,
      max_same_agent_retries: value.max_same_agent_retries ?? 2,
      retry_delay_ms: value.retry_delay_ms ?? 1000,
      backoff_multiplier: value.backoff_multiplier ?? 2.0,
      max_backoff_ms: value.max_backoff_ms ?? 30000,
      max_fallback_cycles: value.max_fallback_cycles ?? 3,
      git_user_name: value.git_user_name ?? '',
      git_user_email: value.git_user_email ?? '',
    };
  }
}

import {
  ChangeDetectionStrategy,
  Component,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { RouterModule } from '@angular/router';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import type { ConfigView } from '../../../types';
import { ContextualHelpComponent } from '../../../components/contextual-help/contextual-help.component';

// ConfigView now includes all fields inline — no extra local interfaces needed.

/**
 * Form component for editing a ConfigView.
 * Emits configChange whenever any field is changed.
 */
@Component({
  selector: 'app-config-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterModule,
    ReactiveFormsModule,
    MatExpansionModule,
    MatInputModule,
    MatFormFieldModule,
    MatSelectModule,
    MatSlideToggleModule,
    ContextualHelpComponent,
  ],
  templateUrl: './config-form.component.html',
  styleUrl: './config-form.component.css',
})
export class ConfigFormComponent {
  /** The current config to display/edit. */
  readonly config = input.required<ConfigView>();

  /** Emitted on every field change with the full updated ConfigView. */
  readonly configChange = output<ConfigView>();

  private readonly fb = inject(FormBuilder);

  readonly form: FormGroup = this.fb.group({
    verbosity: [0, [Validators.min(0), Validators.max(4)]],
    developer_iters: [1, [Validators.min(1), Validators.max(20)]],
    reviewer_reviews: [1, [Validators.min(1), Validators.max(10)]],
    review_depth: ['standard'],
    max_dev_continuations: [3, [Validators.min(1), Validators.max(10)]],
    // General path fields
    prompt_path: [''],
    templates_dir: [''],
    // Execution fields
    checkpoint_enabled: [true],
    isolation_mode: [false],
    interactive: [false],
    developer_context: ['normal'],
    reviewer_context: ['normal'],
    force_universal_prompt: [false],
    auto_detect_stack: [true],
    // Retry and Fallback fields
    max_retries: [3, [Validators.min(1), Validators.max(10)]],
    max_same_agent_retries: [2, [Validators.min(1), Validators.max(5)]],
    retry_delay_ms: [1000, [Validators.min(100), Validators.max(60000)]],
    backoff_multiplier: [2.0, [Validators.min(1.0), Validators.max(5.0)]],
    max_backoff_ms: [30000, [Validators.min(1000), Validators.max(120000)]],
    max_fallback_cycles: [3, [Validators.min(1), Validators.max(20)]],
    // Git fields
    git_user_name: [''],
    git_user_email: ['', [Validators.email]],
  });

  /** Tracks the last-synced config for dirty comparison. */
  private readonly _syncedConfig = signal<ConfigView | null>(null);

  constructor() {
    // Patch form when input changes.
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
          // General path fields
          prompt_path: cfg.prompt_path ?? '',
          templates_dir: cfg.templates_dir ?? '',
          // Execution
          checkpoint_enabled: cfg.checkpoint_enabled,
          isolation_mode: cfg.isolation_mode,
          interactive: cfg.interactive,
          developer_context: cfg.developer_context ?? 'normal',
          reviewer_context: cfg.reviewer_context ?? 'normal',
          force_universal_prompt: cfg.force_universal_prompt ?? false,
          auto_detect_stack: cfg.auto_detect_stack ?? true,
          // Retry and Fallback
          max_retries: cfg.max_retries ?? 3,
          max_same_agent_retries: cfg.max_same_agent_retries ?? 2,
          retry_delay_ms: cfg.retry_delay_ms ?? 1000,
          backoff_multiplier: cfg.backoff_multiplier ?? 2.0,
          max_backoff_ms: cfg.max_backoff_ms ?? 30000,
          max_fallback_cycles: cfg.max_fallback_cycles ?? 3,
          // Git
          git_user_name: cfg.git_user_name ?? '',
          git_user_email: cfg.git_user_email ?? '',
        },
        { emitEvent: false },
      );
    });

    // Emit configChange on every value change.
    this.form.valueChanges.subscribe(value => {
      if (this.form.valid) {
        this.configChange.emit(this._buildConfig(value));
      }
    });
  }

  /**
   * Returns true when the given field has a different value from the original
   * input config.
   */
  isFieldDirty(field: string): boolean {
    const original = this._syncedConfig();
    if (!original) return false;
    const control = this.form.get(field);
    if (!control) return false;
    return control.value !== (original as unknown as Record<string, unknown>)[field];
  }

  // Per-field dirty getters (used in template to avoid no-call-expression lint)
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
  // Retry and Fallback dirty getters
  get isMaxRetriesDirty() { return this.isFieldDirty('max_retries'); }
  get isMaxSameAgentRetriesDirty() { return this.isFieldDirty('max_same_agent_retries'); }
  get isRetryDelayMsDirty() { return this.isFieldDirty('retry_delay_ms'); }
  get isBackoffMultiplierDirty() { return this.isFieldDirty('backoff_multiplier'); }
  get isMaxBackoffMsDirty() { return this.isFieldDirty('max_backoff_ms'); }
  get isMaxFallbackCyclesDirty() { return this.isFieldDirty('max_fallback_cycles'); }
  // Git dirty getters
  get isGitUserNameDirty() { return this.isFieldDirty('git_user_name'); }
  get isGitUserEmailDirty() { return this.isFieldDirty('git_user_email'); }

  // Per-field error state getters (used in template to avoid no-call-expression lint)
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
  // Retry and Fallback error getters
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
  // Git error getters
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
      // General path fields
      prompt_path: value.prompt_path ?? '',
      templates_dir: value.templates_dir ?? '',
      // Execution
      checkpoint_enabled: value.checkpoint_enabled ?? true,
      isolation_mode: value.isolation_mode ?? false,
      interactive: value.interactive ?? false,
      developer_context: value.developer_context ?? 'normal',
      reviewer_context: value.reviewer_context ?? 'normal',
      force_universal_prompt: value.force_universal_prompt ?? false,
      auto_detect_stack: value.auto_detect_stack ?? true,
      // Retry and Fallback
      max_retries: value.max_retries ?? 3,
      max_same_agent_retries: value.max_same_agent_retries ?? 2,
      retry_delay_ms: value.retry_delay_ms ?? 1000,
      backoff_multiplier: value.backoff_multiplier ?? 2.0,
      max_backoff_ms: value.max_backoff_ms ?? 30000,
      max_fallback_cycles: value.max_fallback_cycles ?? 3,
      // Git
      git_user_name: value.git_user_name ?? '',
      git_user_email: value.git_user_email ?? '',
    };
  }
}

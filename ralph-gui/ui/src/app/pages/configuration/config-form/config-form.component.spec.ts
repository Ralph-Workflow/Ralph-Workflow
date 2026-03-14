import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ConfigFormComponent } from './config-form.component';
import type { ConfigView } from '../../../types';

const DEFAULT_CONFIG: ConfigView = {
  verbosity: 1,
  developer_iters: 3,
  reviewer_reviews: 1,
  checkpoint_enabled: true,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 3,
  // General path settings
  prompt_path: '',
  templates_dir: '',
  // Execution context
  developer_context: 'normal',
  reviewer_context: 'normal',
  force_universal_prompt: false,
  auto_detect_stack: true,
  // Retry and Fallback defaults
  max_retries: 3,
  max_same_agent_retries: 2,
  retry_delay_ms: 1000,
  backoff_multiplier: 2.0,
  max_backoff_ms: 30000,
  max_fallback_cycles: 3,
  // Git defaults
  git_user_name: '',
  git_user_email: '',
};



// Host component to test the input binding
@Component({
  template: `<app-config-form [config]="config()" (configChange)="onConfigChange($event)" />`,
  imports: [ConfigFormComponent],
})
class HostComponent {
  config = signal<ConfigView>({ ...DEFAULT_CONFIG });
  lastChange: ConfigView | null = null;

  onConfigChange(c: ConfigView): void {
    this.lastChange = c;
  }
}

describe('ConfigFormComponent', () => {
  let hostFixture: ComponentFixture<HostComponent>;
  let hostComponent: HostComponent;
  let compiled: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HostComponent, ConfigFormComponent, RouterModule.forRoot([])],
      providers: [],
    }).compileComponents();

    hostFixture = TestBed.createComponent(HostComponent);
    hostComponent = hostFixture.componentInstance;
    hostFixture.detectChanges();
    compiled = hostFixture.nativeElement as HTMLElement;
  });

  it('should create', () => {
    expect(hostComponent).toBeTruthy();
  });

  describe('section rendering', () => {
    it('should render the General section', () => {
      expect(compiled.textContent).toContain('General');
    });

    it('should render the Execution section', () => {
      expect(compiled.textContent).toContain('Execution');
    });

    it('should render the Retry and Fallback section with real controls', () => {
      expect(compiled.textContent).toContain('Retry');
      expect(compiled.textContent).toContain('Max Retries');
    });

    it('should render the Git section with real controls', () => {
      expect(compiled.textContent).toContain('Git');
      expect(compiled.textContent).toContain('Git User Name');
    });

    it('should render the Agent Tools placeholder', () => {
      expect(compiled.textContent).toContain('Agent Tools');
    });

    it('should not render an Agent Chains placeholder (editor is hosted by the parent)', () => {
      // Agent Chains and Drains editor is rendered by ConfigurationComponent, not ConfigFormComponent.
      // The config form only renders the Agent Tools placeholder link.
      expect(compiled.querySelector('app-agent-chains-editor')).toBeNull();
    });
  });

  describe('initial values', () => {
    it('should bind config input to form values', () => {
      const configFormEl = compiled.querySelector('app-config-form');
      expect(configFormEl).toBeTruthy();
    });

    it('should display verbosity field', () => {
      expect(compiled.textContent).toContain('Verbosity');
    });

    it('should display developer iterations field', () => {
      expect(compiled.textContent).toContain('Developer Iterations');
    });

    it('should display reviewer reviews field', () => {
      expect(compiled.textContent).toContain('Reviewer Reviews');
    });

    it('should display review depth field', () => {
      expect(compiled.textContent).toContain('Review Depth');
    });

    it('should display max dev continuations field', () => {
      expect(compiled.textContent).toContain('Max Dev Continuations');
    });

    it('should display checkpoint enabled toggle', () => {
      expect(compiled.textContent).toContain('Checkpoint');
    });

    it('should display isolation mode toggle', () => {
      expect(compiled.textContent).toContain('Isolation Mode');
    });

    it('should display interactive toggle', () => {
      expect(compiled.textContent).toContain('Interactive');
    });
  });

  describe('emit on change', () => {
    it('should emit configChange when verbosity control changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      // We test the component directly via its form
      TestBed.runInInjectionContext(() => {});

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(2);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ verbosity: 2 })
      );
    });

    it('should emit configChange when developer_iters control changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(5);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ developer_iters: 5 })
      );
    });

    it('should emit configChange when checkpoint_enabled toggle changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('checkpoint_enabled')?.setValue(false);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ checkpoint_enabled: false })
      );
    });
  });

  describe('numeric validation', () => {
    it('should mark verbosity invalid when above max (4)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(5);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('verbosity')?.invalid).toBe(true);
    });

    it('should mark verbosity invalid when below min (0)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(-1);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('verbosity')?.invalid).toBe(true);
    });

    it('should mark developer_iters invalid when above max (20)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(21);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('developer_iters')?.invalid).toBe(true);
    });

    it('should mark reviewer_reviews invalid when above max (10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('reviewer_reviews')?.setValue(11);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('reviewer_reviews')?.invalid).toBe(true);
    });

    it('should mark reviewer_reviews valid when 0 (AC-7.3 allows 0-10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('reviewer_reviews')?.setValue(0);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('reviewer_reviews')?.valid).toBe(true);
    });

    it('should mark max_dev_continuations invalid when above max (10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_dev_continuations')?.setValue(11);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_dev_continuations')?.invalid).toBe(true);
    });
  });

  describe('dirty highlight', () => {
    it('should mark isDirty for verbosity when different from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(3);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('verbosity')).toBe(true);
    });

    it('should not mark isDirty for verbosity when same as default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('verbosity')).toBe(false);
    });

    it('should mark isDirty for developer_iters when different from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(10);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('developer_iters')).toBe(true);
    });

    it('should mark isDirty for checkpoint_enabled when different from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('checkpoint_enabled')?.setValue(false);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('checkpoint_enabled')).toBe(true);
    });

    it('should mark isDirty for max_retries when different from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(7);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('max_retries')).toBe(true);
    });

    it('should not mark isDirty for max_retries when same as default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('max_retries')).toBe(false);
    });

    it('should mark isDirty for git_user_email when different from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('user@example.com');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('git_user_email')).toBe(true);
    });
  });

  describe('retry and fallback numeric validation', () => {
    it('should mark max_retries invalid when above max (10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(11);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_retries')?.invalid).toBe(true);
    });

    it('should mark max_retries invalid when below min (1)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(0);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_retries')?.invalid).toBe(true);
    });

    it('should mark max_same_agent_retries invalid when above max (5)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_same_agent_retries')?.setValue(6);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_same_agent_retries')?.invalid).toBe(true);
    });

    it('should mark retry_delay_ms invalid when below min (100)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('retry_delay_ms')?.setValue(50);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('retry_delay_ms')?.invalid).toBe(true);
    });

    it('should mark backoff_multiplier invalid when above max (5.0)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('backoff_multiplier')?.setValue(6.0);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('backoff_multiplier')?.invalid).toBe(true);
    });

    it('should mark max_fallback_cycles invalid when above max (20)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_fallback_cycles')?.setValue(21);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_fallback_cycles')?.invalid).toBe(true);
    });
  });

  describe('git section validation', () => {
    it('should mark git_user_email invalid when not a valid email', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('not-an-email');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.invalid).toBe(true);
    });

    it('should mark git_user_email valid when it is a proper email', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('user@example.com');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.valid).toBe(true);
    });

    it('should mark git_user_email valid when empty (optional field)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.valid).toBe(true);
    });
  });

  describe('execution section — new fields', () => {
    it('should render Developer Context dropdown', () => {
      expect(compiled.textContent).toContain('Developer Context');
    });

    it('should render Reviewer Context dropdown', () => {
      expect(compiled.textContent).toContain('Reviewer Context');
    });

    it('should render Force Universal Prompt toggle', () => {
      expect(compiled.textContent).toContain('Force Universal Prompt');
    });

    it('should render Auto-detect Stack toggle', () => {
      expect(compiled.textContent).toContain('Auto-detect Stack');
    });

    it('should emit configChange when developer_context changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('developer_context')?.setValue('minimal');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ developer_context: 'minimal' })
      );
    });

    it('should emit configChange when force_universal_prompt toggle changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('force_universal_prompt')?.setValue(true);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ force_universal_prompt: true })
      );
    });
  });

  describe('general section — path fields', () => {
    it('should render Prompt Path field', () => {
      expect(compiled.textContent).toContain('Prompt Path');
    });

    it('should render Templates Directory field', () => {
      expect(compiled.textContent).toContain('Templates Directory');
    });

    it('should emit configChange when prompt_path changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('prompt_path')?.setValue('/home/user/prompt.md');
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ prompt_path: '/home/user/prompt.md' })
      );
    });
  });

  describe('toggle emits configChange', () => {
    it('should emit configChange when isolation_mode toggle changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('isolation_mode')?.setValue(true);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ isolation_mode: true })
      );
    });

    it('should emit configChange when interactive toggle changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('interactive')?.setValue(true);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ interactive: true })
      );
    });
  });

  describe('form patching on input signal changes', () => {
    it('should patch form values when config input changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, verbosity: 0 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();
      expect(configForm.form.get('verbosity')?.value).toBe(0);

      // Update config input
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, verbosity: 3 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();
      expect(configForm.form.get('verbosity')?.value).toBe(3);
    });

    it('should patch retry fields when config input contains retry values', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, max_retries: 7 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();
      expect(configForm.form.get('max_retries')?.value).toBe(7);
    });
  });

  describe('retry and fallback emits', () => {
    it('should emit configChange with max_retries when it changes', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = vi.fn();
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(5);
      await configFormFixture.whenStable();
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        expect.objectContaining({ max_retries: 5 })
      );
    });
  });

  describe('default indicators', () => {
    it('should display default hint for verbosity field', () => {
      const defaultHint = compiled.querySelector('.form-default-hint');
      expect(defaultHint?.textContent).toContain('Default:');
    });

    it('should display default value for developer_iters', () => {
      expect(compiled.textContent).toContain('Current default: 3');
    });

    it('should display default value for max_retries in Retry section', async () => {
      hostComponent.config.set({ ...DEFAULT_CONFIG });
      hostFixture.detectChanges();
      await hostFixture.whenStable();
      expect(compiled.textContent).toContain('Default: 3');
    });
  });

  describe('override highlighting', () => {
    it('should apply field-overridden class when value differs from default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, verbosity: 3 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const overriddenEl = configFormFixture.nativeElement.querySelector('.field-overridden');
      expect(overriddenEl).toBeTruthy();
    });

    it('should not apply field-overridden class when value matches default', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const overriddenEls = configFormFixture.nativeElement.querySelectorAll('.field-overridden');
      expect(overriddenEls.length).toBe(0);
    });
  });

  describe('section descriptions', () => {
    it('should render General section description', () => {
      expect(compiled.textContent).toContain('Common settings used during everyday setup');
    });

    it('should render Execution section description', () => {
      expect(compiled.textContent).toContain('Runtime behavior and safety defaults');
    });

    it('should render Retry and Fallback section description', () => {
      expect(compiled.textContent).toContain('Controls for automatic retries, backoff timing, and fallback agent cycling');
    });

    it('should render Git section description', () => {
      expect(compiled.textContent).toContain('Git user identity used for commits made by Ralph');
    });
  });

  describe('validation warnings', () => {
    it('should show warning for high developer_iters (>10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, developer_iters: 12 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const warningEl = configFormFixture.nativeElement.querySelector('.validation-warning');
      expect(warningEl?.textContent).toContain('High iteration count');
    });

    it('should show warning for high max_retries (>5)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, max_retries: 7 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const warningEl = configFormFixture.nativeElement.querySelector('.validation-warning');
      expect(warningEl?.textContent).toContain('High retry values');
    });

    it('should show warning for low retry_delay_ms (<500)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, retry_delay_ms: 200 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const warningEl = configFormFixture.nativeElement.querySelector('.validation-warning');
      expect(warningEl?.textContent).toContain('Very short retry delays');
    });

    it('should show warning for high backoff_multiplier (>3.0)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, backoff_multiplier: 4.0 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const warningEl = configFormFixture.nativeElement.querySelector('.validation-warning');
      expect(warningEl?.textContent).toContain('High backoff multiplier');
    });

    it('should show warning for high max_fallback_cycles (>10)', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG, max_fallback_cycles: 12 });
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const warningEl = configFormFixture.nativeElement.querySelector('.validation-warning');
      expect(warningEl?.textContent).toContain('Many fallback cycles');
    });
  });

  describe('browse buttons', () => {
    it('should render Browse button for Prompt Path field', () => {
      const browseButtons = compiled.querySelectorAll('button');
      const browseForPrompt = Array.from(browseButtons).find(
        btn => btn.textContent?.includes('Browse')
      );
      expect(browseForPrompt).toBeTruthy();
    });

    it('should render Browse button for Templates Directory field', () => {
      const browseButtons = compiled.querySelectorAll('button');
      const browseBtns = Array.from(browseButtons).filter(
        btn => btn.textContent?.includes('Browse')
      );
      expect(browseBtns.length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('search/filter functionality', () => {
    it('should hide section when no fields match search query', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.componentRef.setInput('searchQuery', 'nonexistent');
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      expect(configForm.hasVisibleSections()).toBe(false);
    });

    it('should show sections when search query matches', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.componentRef.setInput('searchQuery', 'verbosity');
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      expect(configForm.generalSectionVisible()).toBe(true);
    });

    it('should show empty state when search has no results', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.componentRef.setInput('searchQuery', 'zzzzzzzz');
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      const emptyState = configFormFixture.nativeElement.textContent;
      expect(emptyState).toContain('No matching settings');
    });

    it('hasSearchQuery returns true when search query is set', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.componentRef.setInput('searchQuery', 'test');
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      expect(configForm.hasSearchQuery()).toBe(true);
    });

    it('hasSearchQuery returns false when search query is empty', async () => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.componentRef.setInput('searchQuery', '');
      configFormFixture.detectChanges();
      await configFormFixture.whenStable();

      expect(configForm.hasSearchQuery()).toBe(false);
    });
  });
});

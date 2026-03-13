import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Component, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
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
      imports: [HostComponent, RouterModule.forRoot([])],
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

    it('should render the Agent Chains and Drains placeholder', () => {
      expect(compiled.textContent).toContain('Agent Chains');
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
    it('should emit configChange when verbosity control changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      // We test the component directly via its form
      TestBed.runInInjectionContext(() => {});

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(2);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ verbosity: 2 })
      );
    }));

    it('should emit configChange when developer_iters control changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(5);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ developer_iters: 5 })
      );
    }));

    it('should emit configChange when checkpoint_enabled toggle changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('checkpoint_enabled')?.setValue(false);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ checkpoint_enabled: false })
      );
    }));
  });

  describe('numeric validation', () => {
    it('should mark verbosity invalid when above max (4)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(5);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('verbosity')?.invalid).toBeTrue();
    }));

    it('should mark verbosity invalid when below min (0)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(-1);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('verbosity')?.invalid).toBeTrue();
    }));

    it('should mark developer_iters invalid when above max (20)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(21);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('developer_iters')?.invalid).toBeTrue();
    }));

    it('should mark reviewer_reviews invalid when above max (10)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('reviewer_reviews')?.setValue(11);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('reviewer_reviews')?.invalid).toBeTrue();
    }));

    it('should mark max_dev_continuations invalid when above max (10)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_dev_continuations')?.setValue(11);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_dev_continuations')?.invalid).toBeTrue();
    }));
  });

  describe('dirty highlight', () => {
    it('should mark isDirty for verbosity when different from default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('verbosity')?.setValue(3);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('verbosity')).toBeTrue();
    }));

    it('should not mark isDirty for verbosity when same as default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('verbosity')).toBeFalse();
    }));

    it('should mark isDirty for developer_iters when different from default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('developer_iters')?.setValue(10);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('developer_iters')).toBeTrue();
    }));

    it('should mark isDirty for checkpoint_enabled when different from default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('checkpoint_enabled')?.setValue(false);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('checkpoint_enabled')).toBeTrue();
    }));

    it('should mark isDirty for max_retries when different from default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(7);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('max_retries')).toBeTrue();
    }));

    it('should not mark isDirty for max_retries when same as default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('max_retries')).toBeFalse();
    }));

    it('should mark isDirty for git_user_email when different from default', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('user@example.com');
      tick();
      configFormFixture.detectChanges();

      expect(configForm.isFieldDirty('git_user_email')).toBeTrue();
    }));
  });

  describe('retry and fallback numeric validation', () => {
    it('should mark max_retries invalid when above max (10)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(11);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_retries')?.invalid).toBeTrue();
    }));

    it('should mark max_retries invalid when below min (1)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(0);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_retries')?.invalid).toBeTrue();
    }));

    it('should mark max_same_agent_retries invalid when above max (5)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_same_agent_retries')?.setValue(6);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_same_agent_retries')?.invalid).toBeTrue();
    }));

    it('should mark retry_delay_ms invalid when below min (100)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('retry_delay_ms')?.setValue(50);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('retry_delay_ms')?.invalid).toBeTrue();
    }));

    it('should mark backoff_multiplier invalid when above max (5.0)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('backoff_multiplier')?.setValue(6.0);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('backoff_multiplier')?.invalid).toBeTrue();
    }));

    it('should mark max_fallback_cycles invalid when above max (20)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('max_fallback_cycles')?.setValue(21);
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('max_fallback_cycles')?.invalid).toBeTrue();
    }));
  });

  describe('git section validation', () => {
    it('should mark git_user_email invalid when not a valid email', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('not-an-email');
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.invalid).toBeTrue();
    }));

    it('should mark git_user_email valid when it is a proper email', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('user@example.com');
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.valid).toBeTrue();
    }));

    it('should mark git_user_email valid when empty (optional field)', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configFormFixture.detectChanges();

      configForm.form.get('git_user_email')?.setValue('');
      tick();
      configFormFixture.detectChanges();

      expect(configForm.form.get('git_user_email')?.valid).toBeTrue();
    }));
  });

  describe('toggle emits configChange', () => {
    it('should emit configChange when isolation_mode toggle changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('isolation_mode')?.setValue(true);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ isolation_mode: true })
      );
    }));

    it('should emit configChange when interactive toggle changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('interactive')?.setValue(true);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ interactive: true })
      );
    }));
  });

  describe('retry and fallback emits', () => {
    it('should emit configChange with max_retries when it changes', fakeAsync(() => {
      const configFormFixture = TestBed.createComponent(ConfigFormComponent);
      const configForm = configFormFixture.componentInstance;

      const changeSpy = jasmine.createSpy('configChange');
      configFormFixture.componentRef.setInput('config', { ...DEFAULT_CONFIG });
      configForm.configChange.subscribe(changeSpy);
      configFormFixture.detectChanges();

      configForm.form.get('max_retries')?.setValue(5);
      tick(0);
      configFormFixture.detectChanges();

      expect(changeSpy).toHaveBeenCalledWith(
        jasmine.objectContaining({ max_retries: 5 })
      );
    }));
  });
});

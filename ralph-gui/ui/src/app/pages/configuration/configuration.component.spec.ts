import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { TAURI_INVOKE } from '../../services/tauri.service';
import {
  ConfigFieldComponent,
  ConfigTableComponent,
  ConfigurationComponent,
} from './configuration.component';
import { configurationCanDeactivateGuard } from './configuration.guard';
import { ConfigService } from '../../services/config.service';
import { NotificationService } from '../../services/notification.service';
import { NOTIFICATION_LISTEN_TOKEN } from '../../services/notification.service';
import type { ConfigFieldWithSource, ConfigSource, ConfigView, EffectiveConfigWithSources } from '../../types';

// Re-export the configViewToToml function for round-trip testing.
// Since it's a module-private function, we test it via the ConfigurationComponent class.
// We use an inline test helper that replicates the same logic to validate round-trips.
function tomlContainsField(toml: string, field: string, value: string | number | boolean): boolean {
  const strValue = typeof value === 'string' ? `"${value}"` : String(value);
  return toml.includes(`${field} = ${strValue}`);
}

// ── Minimal mock invoke ─────────────────────────────────────────────────────

const DEFAULT_CONFIG: ConfigView = {
  verbosity: 1,
  developer_iters: 3,
  reviewer_reviews: 1,
  checkpoint_enabled: true,
  isolation_mode: false,
  interactive: false,
  review_depth: 'standard',
  max_dev_continuations: 3,
};

const DEFAULT_SOURCES: ConfigFieldWithSource[] = [
  { field_name: 'verbosity', source: 'default' },
  { field_name: 'developer_iters', source: 'global' },
  { field_name: 'reviewer_reviews', source: 'project' },
  { field_name: 'max_dev_continuations', source: 'default' },
  { field_name: 'review_depth', source: 'default' },
  { field_name: 'checkpoint_enabled', source: 'global' },
  { field_name: 'isolation_mode', source: 'default' },
  { field_name: 'interactive', source: 'default' },
];

const DEFAULT_EFF_WITH_SOURCES: EffectiveConfigWithSources = {
  config: DEFAULT_CONFIG,
  sources: DEFAULT_SOURCES,
};

// ── ConfigFieldComponent unit tests ────────────────────────────────────────

describe('ConfigFieldComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConfigFieldComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('should render label and value', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 2);
    fixture.detectChanges();
    await fixture.whenStable();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('verbosity');
    expect(el.textContent).toContain('2');
  });

  it('should NOT render source badge when source is null', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 1);
    fixture.componentRef.setInput('source', null);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge = fixture.nativeElement.querySelector('.source-badge');
    expect(badge).toBeNull();
  });

  it('should render source badge with class source-badge--default when source is "default"', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 1);
    fixture.componentRef.setInput('source', 'default' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--default');
    expect(badge).not.toBeNull();
    expect(badge?.textContent?.trim()).toBe('Default');
  });

  it('should render source badge with class source-badge--global when source is "global"', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'developer_iters');
    fixture.componentRef.setInput('value', 5);
    fixture.componentRef.setInput('source', 'global' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--global');
    expect(badge).not.toBeNull();
    expect(badge?.textContent?.trim()).toBe('Global');
  });

  it('should render source badge with class source-badge--project when source is "project"', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'reviewer_reviews');
    fixture.componentRef.setInput('value', 2);
    fixture.componentRef.setInput('source', 'project' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--project');
    expect(badge).not.toBeNull();
    expect(badge?.textContent?.trim()).toBe('Project');
  });

  it('should display boolean values as true/false strings', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'checkpoint_enabled');
    fixture.componentRef.setInput('value', true);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.nativeElement.textContent).toContain('true');
  });

  it('should show global config file path in badge title for global source', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 3);
    fixture.componentRef.setInput('source', 'global' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--global');
    expect(badge?.title).toContain('~/.config/ralph-workflow.toml');
  });

  it('should show project config file path in badge title for project source', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 3);
    fixture.componentRef.setInput('source', 'project' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--project');
    expect(badge?.title).toContain('.agent/ralph-workflow.toml');
  });

  it('should show default description in badge title for default source', async () => {
    const fixture = TestBed.createComponent(ConfigFieldComponent);
    fixture.componentRef.setInput('label', 'verbosity');
    fixture.componentRef.setInput('value', 1);
    fixture.componentRef.setInput('source', 'default' as ConfigSource);
    fixture.detectChanges();
    await fixture.whenStable();
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.source-badge--default');
    expect(badge?.title).toContain('default');
  });
});

// ── ConfigTableComponent unit tests ────────────────────────────────────────

describe('ConfigTableComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConfigTableComponent, ConfigFieldComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('should render all config fields', async () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.detectChanges();
    await fixture.whenStable();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('verbosity');
    expect(el.textContent).toContain('developer_iters');
    expect(el.textContent).toContain('reviewer_reviews');
  });

  it('should not render any source badges when sources is null', async () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', null);
    fixture.detectChanges();
    await fixture.whenStable();
    const badges = fixture.nativeElement.querySelectorAll('.source-badge');
    expect(badges.length).toBe(0);
  });

  it('should render source badges for each field when sources provided', async () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', DEFAULT_SOURCES);
    fixture.detectChanges();
    await fixture.whenStable();
    const badges = fixture.nativeElement.querySelectorAll('.source-badge');
    // One badge per field
    expect(badges.length).toBe(DEFAULT_SOURCES.length);
  });

  it('should render a Global badge for developer_iters when source is global', async () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', DEFAULT_SOURCES);
    fixture.detectChanges();
    await fixture.whenStable();
    const globalBadges: NodeListOf<HTMLElement> = fixture.nativeElement.querySelectorAll('.source-badge--global');
    expect(globalBadges.length).toBeGreaterThan(0);
  });

  it('should render a Project badge for reviewer_reviews when source is project', async () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', DEFAULT_SOURCES);
    fixture.detectChanges();
    await fixture.whenStable();
    const projectBadges: NodeListOf<HTMLElement> = fixture.nativeElement.querySelectorAll('.source-badge--project');
    expect(projectBadges.length).toBeGreaterThan(0);
  });

  it('getSource returns null when no sources provided', () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    const result = fixture.componentInstance.getSource('verbosity');
    expect(result).toBeNull();
  });

  it('getSource returns correct source for known field', () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', DEFAULT_SOURCES);
    expect(fixture.componentInstance.getSource('developer_iters')).toBe('global');
    expect(fixture.componentInstance.getSource('reviewer_reviews')).toBe('project');
    expect(fixture.componentInstance.getSource('verbosity')).toBe('default');
  });

  it('getSource returns null for unknown field even when sources provided', () => {
    const fixture = TestBed.createComponent(ConfigTableComponent);
    fixture.componentRef.setInput('config', DEFAULT_CONFIG);
    fixture.componentRef.setInput('sources', DEFAULT_SOURCES);
    expect(fixture.componentInstance.getSource('nonexistent_field')).toBeNull();
  });
});

// ── ConfigurationComponent integration tests (source indicators) ────────────

describe('ConfigurationComponent - source indicators', () => {
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (cmd === 'get_effective_config_with_sources') {
        return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      }
      if (cmd === 'get_global_config' || cmd === 'get_effective_config') {
        return Promise.resolve(DEFAULT_CONFIG);
      }
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('should create the configuration component', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('getFieldSource returns "default" when effectiveWithSources is null', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    // No repo path set → effectiveWithSources stays null
    const source = fixture.componentInstance.getFieldSource('verbosity');
    expect(source).toBe('default');
  });

  it('getFieldSource returns correct source when effectiveWithSources is available', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    // Access via bracket notation to bypass TypeScript private access
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_effectiveWithSources'].set(DEFAULT_EFF_WITH_SOURCES);
    fixture.detectChanges();
    expect(comp.getFieldSource('developer_iters')).toBe('global');
    expect(comp.getFieldSource('reviewer_reviews')).toBe('project');
    expect(comp.getFieldSource('verbosity')).toBe('default');
  });
});

// ── ConfigurationComponent save / revert flow tests ─────────────────────────

describe('ConfigurationComponent - save and revert', () => {
  let mockInvoke: jasmine.Spy;

  const makeMockInvoke = (overrides: Record<string, () => unknown> = {}) => {
    return jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (overrides[cmd]) return overrides[cmd]();
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config' || cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      if (cmd === 'save_global_config') return Promise.resolve(undefined);
      return Promise.resolve(null);
    });
  };

  it('saveFormConfig calls save_global_config and reloads config', async () => {
    mockInvoke = makeMockInvoke();

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;

    // Set pending config
    const pendingConfig: ConfigView = { ...DEFAULT_CONFIG, verbosity: 3 };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set(pendingConfig);
    fixture.detectChanges();

    await comp.saveFormConfig();
    fixture.detectChanges();

    // save_global_config should have been called with the serialized TOML
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const saveCalls = mockInvoke.calls.all().filter((c: any) => c.args[0] === 'save_global_config');
    expect(saveCalls.length).toBeGreaterThan(0);

    // After save, formPendingConfig should be null
    expect(comp.formPendingConfig).toBeNull();

    // formSaveMsg should be set
    expect(comp.formSaveMsg).toBe('Saved successfully.');
  });

  it('saveFormConfig does nothing when no pending config', async () => {
    mockInvoke = makeMockInvoke();

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;

    // No pending config set → saveFormConfig should be no-op
    await comp.saveFormConfig();

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const saveCalls = mockInvoke.calls.all().filter((c: any) => c.args[0] === 'save_global_config');
    expect(saveCalls.length).toBe(0);
  });

  it('saveFormConfig sets formSaveError when save_global_config rejects', async () => {
    mockInvoke = makeMockInvoke({
      save_global_config: () => Promise.reject(new Error('disk full')),
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;

    // Set pending config so save actually fires
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set({ ...DEFAULT_CONFIG });
    fixture.detectChanges();

    await comp.saveFormConfig();
    fixture.detectChanges();

    // Error message should be populated
    expect(comp.formSaveError).toBe('disk full');
    // Pending config should remain (no clear on failure)
    expect(comp.formHasPendingChanges).toBeTrue();
  });

  it('revertFormConfig clears pending config and dirty state', async () => {
    mockInvoke = makeMockInvoke();

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    const configService = comp.configService;

    // Set a pending config and dirty
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set({ ...DEFAULT_CONFIG, verbosity: 3 });
    configService.setDirty(true);
    fixture.detectChanges();

    expect(comp.formHasPendingChanges).toBeTrue();
    expect(configService.isDirty()).toBeTrue();

    // Revert should clear both
    comp.revertFormConfig();
    fixture.detectChanges();

    expect(comp.formHasPendingChanges).toBeFalse();
    expect(configService.isDirty()).toBeFalse();
    expect(comp.formSaveMsg).toBeNull();
    expect(comp.formSaveError).toBeNull();
  });
});

// ── ConfigurationComponent tab switching tests ───────────────────────────────

describe('ConfigurationComponent - tab switching', () => {
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('[defaults]\nverbosity = 2\n');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      if (cmd === 'save_global_config') return Promise.resolve(undefined);
      if (cmd === 'get_agent_tools') return Promise.resolve([]);
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('should start on the Effective tab', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.activeTab).toBe('effective');
  });

  it('setActiveTab switches to global tab', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    fixture.componentInstance.setActiveTab('global');
    fixture.detectChanges();
    expect(fixture.componentInstance.activeTab).toBe('global');
  });

  it('setActiveTab switches to project tab', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    fixture.componentInstance.setActiveTab('project');
    fixture.detectChanges();
    expect(fixture.componentInstance.activeTab).toBe('project');
  });

  it('switching to global tab renders global config form', async () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.setActiveTab('global');
    fixture.detectChanges();
    await fixture.whenStable();

    const el: HTMLElement = fixture.nativeElement;
    // Global tab content should be present
    expect(el.textContent).toContain('Global config stored at');
  });

  it('switching to project tab renders project config message', async () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.setActiveTab('project');
    fixture.detectChanges();
    await fixture.whenStable();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Project-level config');
  });

  it('tab buttons reflect active tab with active class', async () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.setActiveTab('global');
    fixture.detectChanges();

    const tabItems: NodeListOf<HTMLElement> = (fixture.nativeElement as HTMLElement).querySelectorAll('.tab-item');
    const globalTabBtn: HTMLElement | null = Array.from(tabItems).find(
      (btn: HTMLElement) => btn.textContent?.trim() === 'Global',
    ) ?? null;

    expect(globalTabBtn?.classList.contains('tab-item--active')).toBeTrue();
  });
});

// ── ConfigurationComponent - Form/TOML toggle tests ─────────────────────────

describe('ConfigurationComponent - Form/TOML toggle', () => {
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      if (cmd === 'get_agent_tools') return Promise.resolve([]);
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('should start in form view mode', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    expect(fixture.componentInstance.viewMode).toBe('form');
  });

  it('toggleViewMode switches from form to toml', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    fixture.componentInstance.toggleViewMode();
    expect(fixture.componentInstance.viewMode).toBe('toml');
  });

  it('toggleViewMode switches back from toml to form', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    fixture.componentInstance.toggleViewMode();
    fixture.componentInstance.toggleViewMode();
    expect(fixture.componentInstance.viewMode).toBe('form');
  });

  it('pending form config is preserved after toggling view mode', () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;

    // Set pending config as if user changed something
    const pendingConfig: ConfigView = { ...DEFAULT_CONFIG, verbosity: 4 };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set(pendingConfig);

    // Toggle to TOML view
    comp.toggleViewMode();
    fixture.detectChanges();

    // Pending config should still be present
    expect(comp.formPendingConfig?.verbosity).toBe(4);
    expect(comp.formHasPendingChanges).toBeTrue();

    // Toggle back to form view
    comp.toggleViewMode();
    fixture.detectChanges();

    // Pending config should still be present
    expect(comp.formPendingConfig?.verbosity).toBe(4);
  });
});

// ── Navigation guard tests ───────────────────────────────────────────────────

describe('configurationCanDeactivateGuard', () => {
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('returns true when config is not dirty', () => {
    const configService = TestBed.inject(ConfigService);
    configService.setDirty(false);

    const result = TestBed.runInInjectionContext(() => configurationCanDeactivateGuard());
    expect(result).toBeTrue();
  });

  it('returns true when config is dirty and user confirms navigation', () => {
    const configService = TestBed.inject(ConfigService);
    configService.setDirty(true);

    spyOn(window, 'confirm').and.returnValue(true);

    const result = TestBed.runInInjectionContext(() => configurationCanDeactivateGuard());
    expect(result).toBeTrue();
    expect(window.confirm).toHaveBeenCalledOnceWith(jasmine.stringContaining('unsaved configuration changes'));
  });

  it('returns false when config is dirty and user cancels navigation', () => {
    const configService = TestBed.inject(ConfigService);
    configService.setDirty(true);

    spyOn(window, 'confirm').and.returnValue(false);

    const result = TestBed.runInInjectionContext(() => configurationCanDeactivateGuard());
    expect(result).toBeFalse();
    expect(window.confirm).toHaveBeenCalledOnceWith(jasmine.stringContaining('unsaved configuration changes'));
  });

  it('does not show confirm dialog when config is clean', () => {
    const configService = TestBed.inject(ConfigService);
    configService.setDirty(false);

    spyOn(window, 'confirm').and.returnValue(false);

    const result = TestBed.runInInjectionContext(() => configurationCanDeactivateGuard());
    expect(result).toBeTrue();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('navigation guard fires when dirty (integration: configService.isDirty() set to true)', () => {
    const configService = TestBed.inject(ConfigService);

    // Simulate user editing the configuration form making it dirty
    configService.setDirty(true);
    expect(configService.isDirty()).toBeTrue();

    // Guard should fire the confirm dialog
    spyOn(window, 'confirm').and.returnValue(false);
    const result = TestBed.runInInjectionContext(() => configurationCanDeactivateGuard());

    // Guard should have called confirm and returned false (user chose not to navigate)
    expect(window.confirm).toHaveBeenCalled();
    expect(result).toBeFalse();
  });
});

// ── configViewToToml round-trip test ────────────────────────────────────────

describe('configViewToToml round-trip via ConfigurationComponent', () => {
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      if (cmd === 'save_global_config') return Promise.resolve(undefined);
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('saveFormConfig serializes pending config and the TOML contains all required fields', async () => {
    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;

    const testConfig: ConfigView = {
      verbosity: 2,
      developer_iters: 5,
      reviewer_reviews: 2,
      checkpoint_enabled: false,
      isolation_mode: true,
      interactive: true,
      review_depth: 'thorough',
      max_dev_continuations: 4,
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set(testConfig);
    fixture.detectChanges();

    await comp.saveFormConfig();

    // Find the TOML that was passed to save_global_config
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const saveCalls = (mockInvoke.calls.all() as any[]).filter((c: any) => c.args[0] === 'save_global_config');
    expect(saveCalls.length).toBeGreaterThan(0);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access
    const toml = String((saveCalls[0] as any).args[1]['config_toml']);

    // Verify round-trip: the TOML should contain all key field values
    expect(tomlContainsField(toml, 'verbosity', 2)).toBeTrue();
    expect(tomlContainsField(toml, 'developer_iters', 5)).toBeTrue();
    expect(tomlContainsField(toml, 'reviewer_reviews', 2)).toBeTrue();
    expect(tomlContainsField(toml, 'checkpoint_enabled', false)).toBeTrue();
    expect(tomlContainsField(toml, 'isolation_mode', true)).toBeTrue();
    expect(tomlContainsField(toml, 'interactive', true)).toBeTrue();
    expect(tomlContainsField(toml, 'review_depth', 'thorough')).toBeTrue();
    expect(tomlContainsField(toml, 'max_dev_continuations', 4)).toBeTrue();
  });
});

// ── AC-7.4: Success toast after save ─────────────────────────────────────────

/** A no-op listen token so NotificationService doesn't try to subscribe to Tauri events. */
const noopListenFn = () => Promise.resolve(() => void 0);

describe('ConfigurationComponent - AC-7.4 success toast', () => {
  const makeMockInvoke = (overrides: Record<string, () => unknown> = {}) => {
    return jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      if (overrides[cmd]) return overrides[cmd]();
      if (cmd === 'get_effective_config_with_sources') return Promise.resolve(DEFAULT_EFF_WITH_SOURCES);
      if (cmd === 'get_global_config' || cmd === 'get_effective_config') return Promise.resolve(DEFAULT_CONFIG);
      if (cmd === 'get_raw_global_config_toml') return Promise.resolve('');
      if (cmd === 'get_workspaces') return Promise.resolve([]);
      if (cmd === 'get_worktrees') return Promise.resolve([]);
      if (cmd === 'list_worktrees') return Promise.resolve([]);
      if (cmd === 'save_global_config') return Promise.resolve(undefined);
      return Promise.resolve(null);
    });
  };

  it('saveFormConfig() adds a success notification via NotificationService', async () => {
    const mockInvoke = makeMockInvoke();

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
        { provide: NOTIFICATION_LISTEN_TOKEN, useValue: noopListenFn },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    const notificationService = TestBed.inject(NotificationService);

    // Set a pending config
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set({ ...DEFAULT_CONFIG, verbosity: 3 });
    fixture.detectChanges();

    const initialCount = notificationService.notifications().length;
    await comp.saveFormConfig();
    fixture.detectChanges();

    const notifications = notificationService.notifications();
    expect(notifications.length).toBeGreaterThan(initialCount);
    const latest = notifications[0]!;
    expect(latest.type).toBe('success');
    expect(latest.message).toContain('saved');
  });

  it('saveFormConfig() does NOT add notification when save fails', async () => {
    const mockInvoke = makeMockInvoke({
      save_global_config: () => Promise.reject(new Error('disk full')),
    });

    await TestBed.configureTestingModule({
      imports: [ConfigurationComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
        { provide: NOTIFICATION_LISTEN_TOKEN, useValue: noopListenFn },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(ConfigurationComponent);
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    const notificationService = TestBed.inject(NotificationService);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (comp as any)['_formPendingConfig'].set({ ...DEFAULT_CONFIG });
    fixture.detectChanges();

    const initialCount = notificationService.notifications().length;
    await comp.saveFormConfig();
    fixture.detectChanges();

    // No success notification should have been added
    expect(notificationService.notifications().length).toBe(initialCount);
  });
});

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';
import { RouterTestingModule } from '@angular/router/testing';
import { PreferencesComponent } from './preferences.component';
import { PreferencesService } from '../../services/preferences.service';
import type { GuiPreferences } from '../../types';

const DEFAULT_PREFS: GuiPreferences = {
  theme: 'dark',
  accentColor: '#f59e0b',
  sidebarWidth: 240,
  sidebarCollapsed: false,
  fontSize: 14,
  monospaceFont: 'JetBrains Mono',
  runPollIntervalMs: 2000,
  logBufferSize: 10000,
  defaultView: 'home',
  checkUpdates: true,
  notifications: {
    showPhaseNotifications: true,
    desktopNotifications: false,
    notifyPhaseChange: false,
    triggers: {
      notifyCompletion: true,
      notifyFailure: true,
      notifyDegraded: true,  // matches DEFAULT_TRIGGERS.notifyDegraded in service
    },
  },
  session: {
    logAutoscroll: true,
    confirmCancel: true,
    restoreWorkspaces: true,
  },
};

describe('PreferencesComponent', () => {
  let component: PreferencesComponent;
  let fixture: ComponentFixture<PreferencesComponent>;
  let mockPreferencesService: { preferences: ReturnType<typeof signal<GuiPreferences>>; save: jasmine.Spy };

  beforeEach(async () => {
    const prefsSignal = signal<GuiPreferences>({
      ...DEFAULT_PREFS,
      notifications: { ...DEFAULT_PREFS.notifications, triggers: { ...DEFAULT_PREFS.notifications.triggers } },
      session: { ...DEFAULT_PREFS.session },
    });

    mockPreferencesService = {
      preferences: prefsSignal,
      save: jasmine.createSpy('save').and.resolveTo(undefined),
    };

    await TestBed.configureTestingModule({
      imports: [PreferencesComponent, RouterTestingModule],
      providers: [
        { provide: PreferencesService, useValue: mockPreferencesService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(PreferencesComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('section rendering', () => {
    it('should render the Appearance section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Appearance');
    });

    it('should render the Behavior section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Behavior');
    });

    it('should render the Notifications section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Notifications');
    });

    it('should render the Startup section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Startup');
    });

    it('should render the Keyboard Shortcuts section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Keyboard Shortcuts');
    });
  });

  describe('initial state', () => {
    it('should initialize localPrefs from service preferences', () => {
      expect(component.localPrefs()).toEqual(DEFAULT_PREFS);
    });

    it('should display the accent color input', () => {
      const colorInput = (fixture.nativeElement as HTMLElement).querySelector('input[type="color"]') as HTMLInputElement;
      expect(colorInput).toBeTruthy();
      expect(colorInput.value).toBe('#f59e0b');
    });
  });

  describe('field changes trigger save', () => {
    it('should call save when logAutoscroll toggle changes', async () => {
      component.updateSession('logAutoscroll', false);
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({
          session: jasmine.objectContaining({ logAutoscroll: false }),
        })
      );
    });

    it('should call save when confirmCancel toggle changes', async () => {
      component.updateSession('confirmCancel', false);
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({
          session: jasmine.objectContaining({ confirmCancel: false }),
        })
      );
    });

    it('should call save when desktopNotifications toggle changes', async () => {
      component.updateNotification('desktopNotifications', true);
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({
          notifications: jasmine.objectContaining({ desktopNotifications: true }),
        })
      );
    });

    it('should call save when restoreWorkspaces toggle changes', async () => {
      component.updateSession('restoreWorkspaces', false);
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({
          session: jasmine.objectContaining({ restoreWorkspaces: false }),
        })
      );
    });

    it('should call save when fontSize changes', async () => {
      component.updatePref('fontSize', 16);
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({ fontSize: 16 })
      );
    });

    it('should call save when theme changes', async () => {
      component.updatePref('theme', 'dark');
      await fixture.whenStable();
      expect(mockPreferencesService.save).toHaveBeenCalledWith(
        jasmine.objectContaining({ theme: 'dark' })
      );
    });

    it('should update localPrefs immediately on change', async () => {
      component.updatePref('fontSize', 16);
      await fixture.whenStable();
      expect(component.localPrefs().fontSize).toBe(16);
    });

    it('should update session in localPrefs on updateSession', async () => {
      component.updateSession('logAutoscroll', false);
      await fixture.whenStable();
      expect(component.localPrefs().session.logAutoscroll).toBe(false);
    });

    it('should update notifications in localPrefs on updateNotification', async () => {
      component.updateNotification('desktopNotifications', true);
      await fixture.whenStable();
      expect(component.localPrefs().notifications.desktopNotifications).toBe(true);
    });

    it('should update triggers in localPrefs on updateTrigger', async () => {
      component.updateTrigger('notifyCompletion', false);
      await fixture.whenStable();
      expect(component.localPrefs().notifications.triggers.notifyCompletion).toBe(false);
    });
  });

  describe('reset to defaults', () => {
    it('should render a Reset All to Defaults button', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Reset');
    });

    it('should revert localPrefs to defaults when reset is called', async () => {
      component.updatePref('fontSize', 18);
      await fixture.whenStable();

      component.resetToDefaults();
      await fixture.whenStable();

      expect(component.localPrefs().fontSize).toBe(DEFAULT_PREFS.fontSize);
      expect(component.localPrefs().session.logAutoscroll).toBe(DEFAULT_PREFS.session.logAutoscroll);
      expect(component.localPrefs().accentColor).toBe(DEFAULT_PREFS.accentColor);
    });

    it('should call save with default values when reset is clicked', async () => {
      component.updatePref('fontSize', 18);
      await fixture.whenStable();

      mockPreferencesService.save.calls.reset();
      component.resetToDefaults();
      await fixture.whenStable();

      expect(mockPreferencesService.save).toHaveBeenCalledWith(DEFAULT_PREFS);
    });
  });

  describe('keyboard shortcuts section', () => {
    it('should display navigation shortcuts', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Navigation');
    });

    it('should display g+h shortcut', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('g');
    });

    it('should display Actions category', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Actions');
    });
  });

  describe('agent tools access (AC-14.4)', () => {
    it('should render an Agent Tools section', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Agent Tools');
    });

    it('should render a link to the agent-tools route', () => {
      const compiled = fixture.nativeElement as HTMLElement;
      const link = compiled.querySelector('a[href="/agent-tools"]') as HTMLAnchorElement | null;
      expect(link).toBeTruthy();
    });
  });
});

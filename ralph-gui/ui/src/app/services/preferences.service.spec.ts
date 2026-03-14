import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  PreferencesService,
  DEFAULT_PREFERENCES,
  DEFAULT_NOTIFICATIONS,
  DEFAULT_SESSION,
  DEFAULT_TRIGGERS,
} from './preferences.service';
import { TAURI_INVOKE } from './tauri.service';
import type { GuiPreferences } from '../types';

const mockPreferences: GuiPreferences = {
  theme: 'dark',
  accentColor: '#f59e0b',
  sidebarWidth: 220,
  sidebarCollapsed: false,
  fontSize: 14,
  monospaceFont: 'JetBrains Mono',
  runPollIntervalMs: 2000,
  logBufferSize: 10000,
  defaultView: '/',
  checkUpdates: true,
  notifications: {
    showPhaseNotifications: true,
    desktopNotifications: true,
    notifyPhaseChange: false,
    triggers: {
      notifyCompletion: true,
      notifyFailure: true,
      notifyDegraded: true,
    },
  },
  session: {
    logAutoscroll: true,
    confirmCancel: true,
    restoreWorkspaces: true,
  },
};

describe('PreferencesService', () => {
  let service: PreferencesService;
  let mockInvoke: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    mockInvoke = vi.fn().mockImplementation((cmd: string) => {
      switch (cmd) {
        case 'get_gui_preferences':
          return Promise.resolve(mockPreferences);
        case 'save_gui_preferences':
          return Promise.resolve(undefined);
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });
  });

  function createService(): PreferencesService {
    TestBed.configureTestingModule({
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    });
    return TestBed.inject(PreferencesService);
  }

  afterEach(() => {
    document.documentElement.style.removeProperty('--accent');
    document.documentElement.style.removeProperty('--font-size-base');
  });

  it('should be created', fakeAsync(() => {
    service = createService();
    tick();
    expect(service).toBeTruthy();
  }));

  describe('initial state', () => {
    it('should start with isLoading true before backend returns', () => {
      service = createService();
      expect(service.isLoading()).toBe(true);
    });

    it('should start with default preferences before backend returns', () => {
      service = createService();
      expect(service.preferences()).toEqual(DEFAULT_PREFERENCES);
    });

    it('should set isLoading false after load completes', fakeAsync(() => {
      service = createService();
      tick();
      expect(service.isLoading()).toBe(false);
    }));
  });

  describe('loading preferences on init', () => {
    it('should call get_gui_preferences on init', fakeAsync(() => {
      service = createService();
      tick();
      expect(mockInvoke).toHaveBeenCalledWith('get_gui_preferences');
    }));

    it('should update preferences signal with backend data', fakeAsync(() => {
      service = createService();
      tick();
      expect(service.preferences()).toEqual(mockPreferences);
    }));

    it('should fall back to defaults when backend call fails', fakeAsync(() => {
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'get_gui_preferences') return Promise.reject(new Error('Backend unavailable'));
        return Promise.resolve(undefined);
      });
      service = createService();
      tick();
      expect(service.preferences()).toEqual(DEFAULT_PREFERENCES);
      expect(service.isLoading()).toBe(false);
    }));

    it('should NOT use localStorage', fakeAsync(() => {
      const getItemSpy = vi.spyOn(localStorage, 'getItem');
      const setItemSpy = vi.spyOn(localStorage, 'setItem');
      service = createService();
      tick();
      expect(getItemSpy).not.toHaveBeenCalled();
      expect(setItemSpy).not.toHaveBeenCalled();
    }));
  });

  describe('CSS variables applied on init', () => {
    it('should apply --accent CSS variable after load', fakeAsync(() => {
      service = createService();
      tick();
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#f59e0b');
    }));

    it('should apply --font-size-base CSS variable after load', fakeAsync(() => {
      service = createService();
      tick();
      expect(document.documentElement.style.getPropertyValue('--font-size-base')).toBe('14px');
    }));

    it('should apply default CSS variables before backend responds', fakeAsync(() => {
      service = createService();
      TestBed.flushEffects();
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe(DEFAULT_PREFERENCES.accentColor);
    }));
  });

  describe('save', () => {
    it('should call save_gui_preferences with updated prefs', fakeAsync(async () => {
      service = createService();
      tick();
      const updated: GuiPreferences = { ...mockPreferences, fontSize: 16, accentColor: '#3b82f6' };
      await service.save(updated);
      tick();
      expect(mockInvoke).toHaveBeenCalledWith('save_gui_preferences', { prefs: updated });
    }));

    it('should update preferences signal after save', fakeAsync(async () => {
      service = createService();
      tick();
      const updated: GuiPreferences = { ...mockPreferences, fontSize: 16 };
      await service.save(updated);
      tick();
      expect(service.preferences().fontSize).toBe(16);
    }));

    it('should apply updated CSS variables after save', fakeAsync(async () => {
      service = createService();
      tick();
      const updated: GuiPreferences = { ...mockPreferences, accentColor: '#3b82f6', fontSize: 18 };
      await service.save(updated);
      tick();
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#3b82f6');
      expect(document.documentElement.style.getPropertyValue('--font-size-base')).toBe('18px');
    }));

    it('should throw when backend save fails', fakeAsync(async () => {
      service = createService();
      tick();
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'save_gui_preferences') return Promise.reject(new Error('Save failed'));
        return Promise.resolve(undefined);
      });
      const updated: GuiPreferences = { ...mockPreferences };
      await expect(async () => service.save(updated)).rejects.toThrow('Save failed');
    }));
  });

  describe('nested settings access', () => {
    it('should expose session settings through preferences signal', fakeAsync(() => {
      service = createService();
      tick();
      expect(service.preferences().session.logAutoscroll).toBe(true);
      expect(service.preferences().session.confirmCancel).toBe(true);
    }));

    it('should expose notification settings through preferences signal', fakeAsync(() => {
      service = createService();
      tick();
      expect(service.preferences().notifications.triggers.notifyCompletion).toBe(true);
      expect(service.preferences().notifications.notifyPhaseChange).toBe(false);
    }));

    it('should have correct default session settings', () => {
      expect(DEFAULT_SESSION.logAutoscroll).toBe(true);
      expect(DEFAULT_SESSION.confirmCancel).toBe(true);
      expect(DEFAULT_SESSION.restoreWorkspaces).toBe(true);
    });

    it('should have correct default notification settings', () => {
      expect(DEFAULT_NOTIFICATIONS.showPhaseNotifications).toBe(true);
      expect(DEFAULT_NOTIFICATIONS.desktopNotifications).toBe(false);
      expect(DEFAULT_NOTIFICATIONS.notifyPhaseChange).toBe(false);
    });

    it('should have correct default trigger settings', () => {
      expect(DEFAULT_TRIGGERS.notifyCompletion).toBe(true);
      expect(DEFAULT_TRIGGERS.notifyFailure).toBe(true);
      expect(DEFAULT_TRIGGERS.notifyDegraded).toBe(true);
    });
  });
});

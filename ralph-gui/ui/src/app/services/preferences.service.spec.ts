import { TestBed } from '@angular/core/testing';
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

  async function waitForLoadComplete(svc: PreferencesService, timeout = 1000): Promise<void> {
    const startTime = Date.now();
    while (svc.isLoading()) {
      if (Date.now() - startTime > timeout) {
        throw new Error('Timed out waiting for load to complete');
      }
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    TestBed.flushEffects();
  }

  afterEach(() => {
    document.documentElement.style.removeProperty('--accent');
    document.documentElement.style.removeProperty('--font-size-base');
  });

  it('should be created', async () => {
    service = createService();
    await Promise.resolve();
    expect(service).toBeTruthy();
  });

  describe('initial state', () => {
    it('should start with isLoading true before backend returns', () => {
      service = createService();
      expect(service.isLoading()).toBe(true);
    });

    it('should start with default preferences before backend returns', () => {
      service = createService();
      expect(service.preferences()).toEqual(DEFAULT_PREFERENCES);
    });

    it('should set isLoading false after load completes', async () => {
      service = createService();
      await Promise.resolve();
      expect(service.isLoading()).toBe(false);
    });
  });

  describe('loading preferences on init', () => {
    it('should call get_gui_preferences on init', async () => {
      service = createService();
      await Promise.resolve();
      expect(mockInvoke).toHaveBeenCalledWith('get_gui_preferences');
    });

    it('should update preferences signal with backend data', async () => {
      service = createService();
      await Promise.resolve();
      expect(service.preferences()).toEqual(mockPreferences);
    });

    it('should fall back to defaults when backend call fails', async () => {
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'get_gui_preferences') return Promise.reject(new Error('Backend unavailable'));
        return Promise.resolve(undefined);
      });
      service = createService();
      await Promise.resolve();
      expect(service.preferences()).toEqual(DEFAULT_PREFERENCES);
      expect(service.isLoading()).toBe(false);
    });

    it('should NOT use localStorage', async () => {
      const storageProto = globalThis.Storage?.prototype;
      if (!storageProto) {
        expect('localStorage' in globalThis).toBe(false);
        return;
      }
      const getItemSpy = vi.spyOn(storageProto, 'getItem');
      const setItemSpy = vi.spyOn(storageProto, 'setItem');
      service = createService();
      await Promise.resolve();
      expect(getItemSpy).not.toHaveBeenCalled();
      expect(setItemSpy).not.toHaveBeenCalled();
      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
    });
  });

  describe('CSS variables applied on init', () => {
    it('should apply --accent CSS variable after load', async () => {
      service = createService();
      await waitForLoadComplete(service);
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#f59e0b');
    });

    it('should apply --font-size-base CSS variable after load', async () => {
      service = createService();
      await waitForLoadComplete(service);
      expect(document.documentElement.style.getPropertyValue('--font-size-base')).toBe('14px');
    });

    it('should apply default CSS variables before backend responds', async () => {
      service = createService();
      TestBed.flushEffects();
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe(DEFAULT_PREFERENCES.accentColor);
    });
  });

  describe('save', () => {
    it('should call save_gui_preferences with updated prefs', async () => {
      service = createService();
      await Promise.resolve();
      const updated: GuiPreferences = { ...mockPreferences, fontSize: 16, accentColor: '#3b82f6' };
      await service.save(updated);
      await Promise.resolve();
      expect(mockInvoke).toHaveBeenCalledWith('save_gui_preferences', { prefs: updated });
    });

    it('should update preferences signal after save', async () => {
      service = createService();
      await Promise.resolve();
      const updated: GuiPreferences = { ...mockPreferences, fontSize: 16 };
      await service.save(updated);
      await Promise.resolve();
      expect(service.preferences().fontSize).toBe(16);
    });

    it('should apply updated CSS variables after save', async () => {
      service = createService();
      await waitForLoadComplete(service);
      const updated: GuiPreferences = { ...mockPreferences, accentColor: '#3b82f6', fontSize: 18 };
      await service.save(updated);
      TestBed.flushEffects();
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe('#3b82f6');
      expect(document.documentElement.style.getPropertyValue('--font-size-base')).toBe('18px');
    });

    it('should throw when backend save fails', async () => {
      service = createService();
      await Promise.resolve();
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'save_gui_preferences') return Promise.reject(new Error('Save failed'));
        return Promise.resolve(undefined);
      });
      const updated: GuiPreferences = { ...mockPreferences };
      await expect(async () => service.save(updated)).rejects.toThrow('Save failed');
    });
  });

  describe('nested settings access', () => {
    it('should expose session settings through preferences signal', async () => {
      service = createService();
      await Promise.resolve();
      expect(service.preferences().session.logAutoscroll).toBe(true);
      expect(service.preferences().session.confirmCancel).toBe(true);
    });

    it('should expose notification settings through preferences signal', async () => {
      service = createService();
      await Promise.resolve();
      expect(service.preferences().notifications.triggers.notifyCompletion).toBe(true);
      expect(service.preferences().notifications.notifyPhaseChange).toBe(false);
    });

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

import { Injectable, inject, signal, effect } from '@angular/core';
import { TauriService } from './tauri.service';
import type { GuiPreferences, GuiNotificationSettings, GuiNotificationTriggers, GuiSessionSettings } from '../types';

export const DEFAULT_TRIGGERS: GuiNotificationTriggers = {
  notifyCompletion: true,
  notifyFailure: true,
  notifyDegraded: true,
};

export const DEFAULT_NOTIFICATIONS: GuiNotificationSettings = {
  showPhaseNotifications: true,
  desktopNotifications: false,
  notifyPhaseChange: false,
  triggers: { ...DEFAULT_TRIGGERS },
};

export const DEFAULT_SESSION: GuiSessionSettings = {
  logAutoscroll: true,
  confirmCancel: true,
  restoreWorkspaces: true,
};

export const DEFAULT_PREFERENCES: GuiPreferences = {
  theme: 'dark',
  accentColor: '#f59e0b',
  sidebarWidth: 240,
  fontSize: 14,
  monospaceFont: 'JetBrains Mono',
  runPollIntervalMs: 2000,
  logBufferSize: 10000,
  defaultView: 'home',
  checkUpdates: true,
  notifications: { ...DEFAULT_NOTIFICATIONS, triggers: { ...DEFAULT_TRIGGERS } },
  session: { ...DEFAULT_SESSION },
};

@Injectable({ providedIn: 'root' })
export class PreferencesService {
  private readonly tauri = inject(TauriService);

  private readonly _preferences = signal<GuiPreferences>({
    ...DEFAULT_PREFERENCES,
    notifications: { ...DEFAULT_NOTIFICATIONS, triggers: { ...DEFAULT_TRIGGERS } },
    session: { ...DEFAULT_SESSION },
  });
  private readonly _isLoading = signal<boolean>(true);
  private readonly _isFirstRun = signal<boolean>(false);

  /** Current preferences (reactive). */
  readonly preferences = this._preferences.asReadonly();

  /** True while the initial load from backend is in progress. */
  readonly isLoading = this._isLoading.asReadonly();

  /**
   * True when the backend had no preferences file and returned defaults.
   * Used to trigger the first-run onboarding flow.
   */
  readonly isFirstRun = this._isFirstRun.asReadonly();

  constructor() {
    // Apply CSS variables whenever preferences change.
    effect(() => {
      const prefs = this._preferences();
      document.documentElement.style.setProperty('--accent', prefs.accentColor);
      document.documentElement.style.setProperty('--font-size-base', `${prefs.fontSize}px`);
    });

    void this.loadFromBackend();
  }

  /**
   * Save updated preferences to the backend and update local signal.
   * Throws if the backend call fails.
   */
  async save(prefs: GuiPreferences): Promise<void> {
    await this.tauri.saveGuiPreferences(prefs);
    this._preferences.set({ ...prefs });
  }

  private async loadFromBackend(): Promise<void> {
    this._isLoading.set(true);
    try {
      const prefs = await this.tauri.getGuiPreferences();
      this._preferences.set(prefs);
      // Detect first run: backend returned the exact default values,
      // indicating no preferences file was found.
      this._isFirstRun.set(this._areDefaultPreferences(prefs));
    } catch (err) {
      console.warn('Failed to load GUI preferences, using defaults:', err);
      // On backend error, treat as first run (no file to read).
      this._isFirstRun.set(true);
    } finally {
      this._isLoading.set(false);
    }
  }

  /**
   * Returns true when the given preferences exactly match the default values.
   * Used as a heuristic to detect first-run (no preferences file saved yet).
   */
  private _areDefaultPreferences(prefs: GuiPreferences): boolean {
    return (
      prefs.theme === DEFAULT_PREFERENCES.theme &&
      prefs.accentColor === DEFAULT_PREFERENCES.accentColor &&
      prefs.sidebarWidth === DEFAULT_PREFERENCES.sidebarWidth &&
      prefs.fontSize === DEFAULT_PREFERENCES.fontSize &&
      prefs.monospaceFont === DEFAULT_PREFERENCES.monospaceFont &&
      prefs.runPollIntervalMs === DEFAULT_PREFERENCES.runPollIntervalMs &&
      prefs.logBufferSize === DEFAULT_PREFERENCES.logBufferSize &&
      prefs.defaultView === DEFAULT_PREFERENCES.defaultView &&
      prefs.checkUpdates === DEFAULT_PREFERENCES.checkUpdates
    );
  }
}

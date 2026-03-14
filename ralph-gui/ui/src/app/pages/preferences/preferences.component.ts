import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
import {
  PreferencesService,
  DEFAULT_PREFERENCES,
  DEFAULT_NOTIFICATIONS,
  DEFAULT_SESSION,
  DEFAULT_TRIGGERS,
} from '../../services/preferences.service';
import type {
  GuiPreferences,
  GuiNotificationSettings,
  GuiNotificationTriggers,
  GuiSessionSettings,
} from '../../types';

interface ShortcutEntry {
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  category: string;
  shortcuts: ShortcutEntry[];
}

const KEYBOARD_SHORTCUTS: ShortcutGroup[] = [
  {
    category: 'Navigation',
    shortcuts: [
      { keys: ['g', 'h'], description: 'Go to Home' },
      { keys: ['g', 's'], description: 'Go to Sessions' },
      { keys: ['g', 'w'], description: 'Go to Worktrees' },
      { keys: ['g', 'c'], description: 'Go to Configuration' },
      { keys: ['g', 'p'], description: 'Go to Preferences' },
      { keys: ['Ctrl+Tab'], description: 'Cycle through tabs' },
    ],
  },
  {
    category: 'Actions',
    shortcuts: [
      { keys: ['Ctrl+N'], description: 'New session' },
      { keys: ['Ctrl+W'], description: 'Close current tab' },
      { keys: ['Ctrl+F'], description: 'Focus search' },
      { keys: ['Ctrl+K'], description: 'Open command palette' },
    ],
  },
  {
    category: 'Workspaces',
    shortcuts: [
      { keys: ['Ctrl+Tab'], description: 'Cycle workspace tabs forward' },
      { keys: ['Ctrl+Shift+Tab'], description: 'Cycle workspace tabs backward' },
      { keys: ['Ctrl+W'], description: 'Close active workspace' },
    ],
  },
  {
    category: 'General',
    shortcuts: [
      { keys: ['?'], description: 'Show keyboard shortcuts' },
      { keys: ['Ctrl+,'], description: 'Open preferences' },
      { keys: ['Esc'], description: 'Close panel / dialog' },
    ],
  },
];

@Component({
  selector: 'app-preferences',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterModule],
  templateUrl: './preferences.component.html',
  styleUrl: './preferences.component.css',
})
export class PreferencesComponent {
  private readonly preferencesService = inject(PreferencesService);

  /** Local copy of preferences for immediate UI updates. */
  readonly localPrefs = signal<GuiPreferences>({
    ...this.preferencesService.preferences(),
    notifications: {
      ...this.preferencesService.preferences().notifications,
      triggers: { ...this.preferencesService.preferences().notifications.triggers },
    },
    session: { ...this.preferencesService.preferences().session },
  });

  readonly defaultViewOptions = [
    { value: 'home', label: 'Home' },
    { value: 'sessions', label: 'Sessions' },
    { value: 'worktrees', label: 'Worktrees' },
    { value: 'configuration', label: 'Configuration' },
  ];

  readonly keyboardShortcuts = KEYBOARD_SHORTCUTS;

  get prefs(): GuiPreferences { return this.localPrefs(); }

  /**
   * Updates a top-level preference field and immediately persists to backend.
   */
  updatePref<K extends keyof GuiPreferences>(key: K, value: GuiPreferences[K]): void {
    const updated: GuiPreferences = { ...this.localPrefs(), [key]: value };
    this.localPrefs.set(updated);
    void this.preferencesService.save(updated);
  }

  /**
   * Updates a notification-related preference field.
   */
  updateNotification<K extends keyof GuiNotificationSettings>(
    key: K,
    value: GuiNotificationSettings[K],
  ): void {
    const updated: GuiPreferences = {
      ...this.localPrefs(),
      notifications: { ...this.localPrefs().notifications, [key]: value },
    };
    this.localPrefs.set(updated);
    void this.preferencesService.save(updated);
  }

  /**
   * Updates a notification trigger field.
   */
  updateTrigger<K extends keyof GuiNotificationTriggers>(
    key: K,
    value: GuiNotificationTriggers[K],
  ): void {
    const updated: GuiPreferences = {
      ...this.localPrefs(),
      notifications: {
        ...this.localPrefs().notifications,
        triggers: { ...this.localPrefs().notifications.triggers, [key]: value },
      },
    };
    this.localPrefs.set(updated);
    void this.preferencesService.save(updated);
  }

  /**
   * Updates a session-related preference field.
   */
  updateSession<K extends keyof GuiSessionSettings>(
    key: K,
    value: GuiSessionSettings[K],
  ): void {
    const updated: GuiPreferences = {
      ...this.localPrefs(),
      session: { ...this.localPrefs().session, [key]: value },
    };
    this.localPrefs.set(updated);
    void this.preferencesService.save(updated);
  }

  /**
   * Resets all preferences to factory defaults and persists.
   */
  resetToDefaults(): void {
    const defaults: GuiPreferences = {
      ...DEFAULT_PREFERENCES,
      notifications: { ...DEFAULT_NOTIFICATIONS, triggers: { ...DEFAULT_TRIGGERS } },
      session: { ...DEFAULT_SESSION },
    };
    this.localPrefs.set(defaults);
    void this.preferencesService.save(defaults);
  }

  /** Extract string value from input change event. */
  asInputValue(event: Event): string {
    return (event.target as HTMLInputElement).value;
  }

  /** Extract numeric value from input change event. */
  asNumberValue(event: Event): number {
    return Number((event.target as HTMLInputElement).value);
  }
}

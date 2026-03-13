import { Component, ChangeDetectionStrategy, effect, inject, signal } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatDividerModule } from '@angular/material/divider';
import { WorktreesService } from './services/worktrees.service';
import { WorkspaceService } from './services/workspace.service';
import { NotificationService } from './services/notification.service';
import { PreferencesService } from './services/preferences.service';
import { WorkspaceTabBarComponent } from './components/workspace-tab-bar/workspace-tab-bar.component';
import { StatusBarComponent } from './components/status-bar/status-bar.component';
import { NotificationCenterComponent } from './components/notification-center/notification-center.component';
import { ConceptsGuideComponent } from './components/concepts-guide/concepts-guide.component';

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

interface ShortcutEntry {
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  category: string;
  shortcuts: ShortcutEntry[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    category: 'Navigation',
    shortcuts: [
      { keys: ['g', 'h'], description: 'Go to Home' },
      { keys: ['g', 's'], description: 'Go to Sessions' },
      { keys: ['g', 'w'], description: 'Go to Worktrees' },
      { keys: ['g', 'c'], description: 'Go to Configuration' },
      { keys: ['g', 'p'], description: 'Go to Preferences' },
      { keys: ['Ctrl+Tab'], description: 'Cycle workspace tabs' },
    ],
  },
  {
    category: 'Actions',
    shortcuts: [
      { keys: ['Ctrl+N'], description: 'New session' },
      { keys: ['Ctrl+W'], description: 'Close active workspace' },
      { keys: ['Ctrl+F'], description: 'Contextual search' },
      { keys: ['Ctrl+K'], description: 'Command palette (coming soon)' },
    ],
  },
  {
    category: 'Workspaces',
    shortcuts: [
      { keys: ['Ctrl+,'], description: 'Open preferences' },
    ],
  },
  {
    category: 'General',
    shortcuts: [
      { keys: ['?'], description: 'Show keyboard shortcuts' },
      { keys: ['Esc'], description: 'Close panel / dialog' },
    ],
  },
];

const NAV_ITEMS: NavItem[] = [
  { path: '/', label: 'Home', icon: 'home' },
  { path: '/sessions', label: 'Sessions', icon: 'play_arrow' },
  { path: '/worktrees', label: 'Worktrees', icon: 'account_tree' },
  { path: '/configuration', label: 'Configuration', icon: 'settings' },
];

const NAV_ITEMS_BOTTOM: NavItem[] = [
  { path: '/preferences', label: 'Preferences', icon: 'settings' },
];

@Component({
  selector: 'app-root',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterModule,
    MatSidenavModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatDividerModule,
    WorkspaceTabBarComponent,
    StatusBarComponent,
    NotificationCenterComponent,
    ConceptsGuideComponent,
  ],
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.css'],
  host: {
    '(window:keydown)': 'handleKeyboard($event)',
  },
})
export class AppComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly workspaceService = inject(WorkspaceService);
  readonly notificationService = inject(NotificationService);
  readonly preferencesService = inject(PreferencesService);
  private readonly router = inject(Router);

  readonly navItems = NAV_ITEMS;
  readonly navItemsBottom = NAV_ITEMS_BOTTOM;
  readonly showHelp = signal(false);
  readonly openNewSession = signal(false);
  readonly showCommandPalette = signal(false);
  readonly showConceptsGuide = signal(false);
  readonly shortcutGroups: ShortcutGroup[] = SHORTCUT_GROUPS;

  get worktrees() { return this.worktreesService.worktrees(); }
  get unreadCount() { return this.notificationService.unreadCount(); }
  get isShowHelp() { return this.showHelp(); }
  get isShowCommandPalette() { return this.showCommandPalette(); }
  get isShowConceptsGuide() { return this.showConceptsGuide(); }

  private pendingNavKey: string | null = null;
  private keyTimeout: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    // Redirect to /welcome (or /onboarding on first run) when no workspaces
    // are open after initial load.
    // Do NOT redirect if already on /welcome, /onboarding, or /preferences.
    effect(() => {
      const isWorkspacesLoading = this.workspaceService.isLoading();
      const isPrefsLoading = this.preferencesService.isLoading();
      const workspaces = this.workspaceService.workspaces();

      // Wait for both services to finish loading before deciding
      if (isWorkspacesLoading || isPrefsLoading) return;

      if (workspaces.length === 0) {
        const currentUrl = this.router.url;
        const exemptRoutes = ['/welcome', '/onboarding', '/preferences'];
        const isExempt = exemptRoutes.some(r => currentUrl.startsWith(r));
        if (!isExempt) {
          const isFirstRun = this.preferencesService.isFirstRun();
          if (isFirstRun) {
            void this.router.navigate(['/onboarding']);
          } else {
            void this.router.navigate(['/welcome']);
          }
        }
      }
    });
  }

  get contextDisplay(): string {
    const activePath = this.worktreesService.activeWorktreePath();
    if (activePath) {
      const wt = this.worktreesService.worktrees().find(w => w.path === activePath);
      return wt?.name ?? activePath;
    }
    const repoPath = this.worktreesService.lastRepoPath();
    if (repoPath) {
      return repoPath.split('/').pop() ?? repoPath;
    }
    return 'Select repository...';
  }

  selectContext(path: string | null): void {
    if (path === null) {
      return;
    }
    void this.worktreesService.switchContext(this.worktreesService.lastRepoPath() ?? '', path);
  }

  closeHelp(): void {
    this.showHelp.set(false);
  }

  handleKeyboard(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) {
      return;
    }

    if (event.key === '?') {
      this.showHelp.update(v => !v);
      event.preventDefault();
      return;
    }

    if (event.key === 'Escape') {
      this.showHelp.set(false);
      this.showCommandPalette.set(false);
      if (this.notificationService.isPanelOpen()) {
        this.notificationService.closePanel();
      }
      return;
    }

    if (event.ctrlKey && event.key === 'Tab') {
      this.cycleWorkspace(event.shiftKey ? -1 : 1);
      event.preventDefault();
      return;
    }

    if (event.ctrlKey && event.key === ',') {
      void this.router.navigate(['/preferences']);
      event.preventDefault();
      return;
    }

    if (event.ctrlKey && (event.key === 'n' || event.key === 'N')) {
      this.openNewSession.set(true);
      event.preventDefault();
      return;
    }

    if (event.ctrlKey && (event.key === 'f' || event.key === 'F')) {
      window.dispatchEvent(new CustomEvent('ralph:contextual-search'));
      event.preventDefault();
      return;
    }

    if (event.ctrlKey && (event.key === 'w' || event.key === 'W')) {
      void this.closeActiveWorkspace();
      event.preventDefault();
      return;
    }

    if (event.ctrlKey && (event.key === 'k' || event.key === 'K')) {
      this.showCommandPalette.update(v => !v);
      event.preventDefault();
      return;
    }

    if (this.pendingNavKey === 'g') {
      this.clearKeyTimeout();
      this.pendingNavKey = null;

      const routes: Record<string, string> = {
        h: '/',
        s: '/sessions',
        w: '/worktrees',
        c: '/configuration',
        p: '/preferences',
      };

      const route = routes[event.key];
      if (route) {
        void this.router.navigate([route]);
      }
      event.preventDefault();
      return;
    }

    if (event.key === 'g') {
      this.pendingNavKey = 'g';
      this.keyTimeout = setTimeout(() => {
        this.pendingNavKey = null;
      }, 500);
      event.preventDefault();
    }
  }

  private clearKeyTimeout(): void {
    if (this.keyTimeout) {
      clearTimeout(this.keyTimeout);
      this.keyTimeout = null;
    }
  }

  private async closeActiveWorkspace(): Promise<void> {
    const activeId = this.workspaceService.activeWorkspaceId();
    if (!activeId) return;

    const workspace = this.workspaceService.workspaces().find(w => w.id === activeId);
    if (!workspace) return;

    const hasActiveRuns = workspace.activeRunCount > 0;
    if (hasActiveRuns) {
      const confirmed = window.confirm(
        `Workspace "${workspace.label}" has active runs. Close anyway?`,
      );
      if (!confirmed) return;
    }

    try {
      await this.workspaceService.closeWorkspace(activeId);
    } catch (err) {
      console.error('Failed to close workspace:', err);
    }
  }

  private cycleWorkspace(direction: number): void {
    const workspaces = this.workspaceService.workspaces();
    if (workspaces.length <= 1) return;

    const activeId = this.workspaceService.activeWorkspaceId();
    const currentIndex = workspaces.findIndex(w => w.id === activeId);
    if (currentIndex === -1) return;

    let nextIndex = currentIndex + direction;
    if (nextIndex < 0) nextIndex = workspaces.length - 1;
    if (nextIndex >= workspaces.length) nextIndex = 0;

    const nextWorkspace = workspaces[nextIndex];
    if (nextWorkspace) {
      this.workspaceService.switchWorkspace(nextWorkspace.id);
    }
  }
}

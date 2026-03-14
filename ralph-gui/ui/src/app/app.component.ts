import { Component, ChangeDetectionStrategy, effect, inject, signal, computed, DOCUMENT, untracked } from '@angular/core';
import { NgStyle } from '@angular/common';
import { Router, RouterModule, NavigationEnd } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatDividerModule } from '@angular/material/divider';
import { filter, debounceTime } from 'rxjs/operators';
import { WorktreesService } from './services/worktrees.service';
import { WorkspaceService } from './services/workspace.service';
import { NotificationService } from './services/notification.service';
import { PreferencesService } from './services/preferences.service';
import { SessionsService } from './services/sessions.service';
import { WorkspaceTabBarComponent } from './components/workspace-tab-bar/workspace-tab-bar.component';
import { StatusBarComponent } from './components/status-bar/status-bar.component';
import { NotificationCenterComponent } from './components/notification-center/notification-center.component';
import { ConceptsGuideComponent } from './components/concepts-guide/concepts-guide.component';
import { CancelConfirmationComponent } from './components/cancel-confirmation/cancel-confirmation.component';
import { WorkspaceLoadingSkeletonComponent } from './components/workspace-loading-skeleton/workspace-loading-skeleton.component';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  tooltip: string;
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
  { path: '/', label: 'Home', icon: 'home', tooltip: 'Home (g then h)' },
  { path: '/sessions', label: 'Sessions', icon: 'play_arrow', tooltip: 'Sessions (g then s)' },
  { path: '/worktrees', label: 'Worktrees', icon: 'account_tree', tooltip: 'Worktrees (g then w)' },
  { path: '/configuration', label: 'Configuration', icon: 'settings', tooltip: 'Configuration (g then c)' },
];

const NAV_ITEMS_BOTTOM: NavItem[] = [
  { path: '/preferences', label: 'Preferences', icon: 'settings', tooltip: 'Preferences (g then p, Ctrl+,)' },
];

const NAV_PERSIST_EXEMPT_ROUTES = ['/welcome', '/onboarding'];

const MIN_SIDEBAR_WIDTH = 180;
const MAX_SIDEBAR_WIDTH = 400;
const DEFAULT_SIDEBAR_WIDTH = 220;

@Component({
  selector: 'app-root',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    NgStyle,
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
    CancelConfirmationComponent,
    WorkspaceLoadingSkeletonComponent,
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
  private readonly sessionsService = inject(SessionsService);
  private readonly router = inject(Router);
  private readonly document = inject(DOCUMENT);

  /** Count of failed or paused sessions — shown as badge on the Sessions nav icon. */
  readonly failedPausedCount = computed(() => {
    const sessions = this.sessionsService.sessions();
    return sessions.filter(s => s.status === 'failed' || s.status === 'paused').length;
  });

  readonly navItems = NAV_ITEMS;
  readonly navItemsBottom = NAV_ITEMS_BOTTOM;
  readonly showHelp = signal(false);
  readonly openNewSession = signal(false);
  readonly showCommandPalette = signal(false);
  readonly showConceptsGuide = signal(false);
  readonly shortcutGroups: ShortcutGroup[] = SHORTCUT_GROUPS;

  /** Sidebar width in px — set from preferences on load, adjustable via drag handle. */
  readonly sidebarWidth = signal(DEFAULT_SIDEBAR_WIDTH);

  /** Whether the sidebar is currently collapsed. Persisted to preferences. */
  readonly sidebarCollapsed = signal(false);

  /** Computed NgStyle object for the sidebar element. */
  readonly sidebarStyle = computed(() => {
    if (this.sidebarCollapsed()) {
      return { width: '0px', 'min-width': '0px', overflow: 'hidden' };
    }
    const width = this.sidebarWidth();
    return { width: `${width}px`, 'min-width': `${width}px` };
  });

  /** Flag: true after the first workspace has been activated, used to gate navigation restore. */
  private readonly initialLoadComplete = signal(false);

  /** Flag: true while workspace-switch nav restoration is in progress, suppresses re-persisting. */
  private isRestoringNavState = false;

  /**
   * True while the workspace context switch is in progress (initializeRepo / fetchSessions
   * calls are outstanding). The loading skeleton is rendered over the main content area
   * during this period to prevent a blank or stale screen.
   */
  readonly isSwitchingWorkspace = signal(false);

  /**
   * When set to a non-null workspace ID, the close-active-workspace confirmation dialog
   * is rendered for Ctrl+W closes that have active runs.
   */
  readonly closeActiveConfirmId = signal<string | null>(null);

  get sidebarStyleValue() { return this.sidebarStyle(); }
  get isSidebarCollapsed() { return this.sidebarCollapsed(); }
  get worktrees() { return this.worktreesService.worktrees(); }
  get unreadCount() { return this.notificationService.unreadCount(); }
  get isShowHelp() { return this.showHelp(); }
  get isShowCommandPalette() { return this.showCommandPalette(); }
  get isShowConceptsGuide() { return this.showConceptsGuide(); }
  get failedPausedCountValue(): number { return this.failedPausedCount(); }
  get isSwitchingWorkspaceValue(): boolean { return this.isSwitchingWorkspace(); }

  private pendingNavKey: string | null = null;
  private keyTimeout: ReturnType<typeof setTimeout> | null = null;
  private isDraggingSidebar = false;
  private dragStartX = 0;
  private dragStartWidth = DEFAULT_SIDEBAR_WIDTH;

  private readonly onSidebarResizeMoveRef = (e: MouseEvent) => this.onSidebarResizeMove(e);
  private readonly onSidebarResizeEndRef = () => this.onSidebarResizeEnd();

  constructor() {
    // Sync sidebar width and collapsed state from persisted preferences on load.
    effect(() => {
      const prefs = this.preferencesService.preferences();
      if (!this.isDraggingSidebar) {
        this.sidebarWidth.set(
          Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, prefs.sidebarWidth ?? DEFAULT_SIDEBAR_WIDTH)),
        );
      }
      this.sidebarCollapsed.set(prefs.sidebarCollapsed ?? false);
    });

    // Workspace context switching effect:
    // When the active workspace changes, reload worktrees and sessions for the new context.
    // On subsequent switches (not the very first load), also restore the workspace's saved nav state.
    // While the switch is in progress, set isSwitchingWorkspace=true so the loading skeleton renders.
    effect(() => {
      const ws = this.workspaceService.activeWorkspace();
      if (!ws) return;

      // Use untracked() so that async side-effects don't create reactive dependencies
      // on anything inside worktreesService or sessionsService.
      untracked(() => {
        this.isSwitchingWorkspace.set(true);

        const switchDone = Promise.all([
          this.worktreesService.initializeRepo(ws.path),
          this.sessionsService.fetchSessions(ws.path),
        ]);

        void switchDone.then(() => {
          this.isSwitchingWorkspace.set(false);
        }).catch(() => {
          this.isSwitchingWorkspace.set(false);
        });

        if (this.initialLoadComplete()) {
          // Restore saved navigation state on workspace switch
          const nav = ws.navigationState;
          if (nav) {
            this.isRestoringNavState = true;
            void this.router.navigate([nav]).then(() => {
              this.isRestoringNavState = false;
            });
          }
        } else {
          this.initialLoadComplete.set(true);
        }
      });
    });

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

    // Navigation state persistence:
    // Subscribe to NavigationEnd events and persist the current URL to the active workspace.
    // Debounce to avoid excessive backend calls. Skip exempt routes and restoring nav state.
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      debounceTime(300),
    ).subscribe((event) => {
      if (this.isRestoringNavState) return;
      const url = event.urlAfterRedirects;
      const isExempt = NAV_PERSIST_EXEMPT_ROUTES.some(r => url.startsWith(r));
      if (isExempt) return;

      const activeId = this.workspaceService.activeWorkspaceId();
      if (!activeId) return;

      void this.workspaceService.persistNavigation(activeId, url);
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
      this.closeActiveWorkspace();
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

  /** Toggle sidebar collapsed state and persist to preferences. */
  toggleSidebar(): void {
    const collapsed = !this.sidebarCollapsed();
    this.sidebarCollapsed.set(collapsed);
    const current = this.preferencesService.preferences();
    void this.preferencesService.save({ ...current, sidebarCollapsed: collapsed });
  }

  onSidebarResizeStart(event: MouseEvent): void {
    event.preventDefault();
    this.isDraggingSidebar = true;
    this.dragStartX = event.clientX;
    this.dragStartWidth = this.sidebarWidth();
    this.document.addEventListener('mousemove', this.onSidebarResizeMoveRef);
    this.document.addEventListener('mouseup', this.onSidebarResizeEndRef);
    this.document.body.style.userSelect = 'none';
    this.document.body.style.cursor = 'col-resize';
  }

  private onSidebarResizeMove(event: MouseEvent): void {
    if (!this.isDraggingSidebar) return;
    const delta = event.clientX - this.dragStartX;
    const newWidth = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, this.dragStartWidth + delta));
    this.sidebarWidth.set(newWidth);
  }

  private onSidebarResizeEnd(): void {
    if (!this.isDraggingSidebar) return;
    this.isDraggingSidebar = false;
    this.document.removeEventListener('mousemove', this.onSidebarResizeMoveRef);
    this.document.removeEventListener('mouseup', this.onSidebarResizeEndRef);
    this.document.body.style.userSelect = '';
    this.document.body.style.cursor = '';
    // Persist width to preferences.
    const current = this.preferencesService.preferences();
    void this.preferencesService.save({ ...current, sidebarWidth: this.sidebarWidth() });
  }

  private clearKeyTimeout(): void {
    if (this.keyTimeout) {
      clearTimeout(this.keyTimeout);
      this.keyTimeout = null;
    }
  }

  /**
   * Close the active workspace (triggered by Ctrl+W).
   *
   * If the workspace has active runs, a non-blocking CancelConfirmationComponent dialog
   * is shown instead of the blocking window.confirm. The dialog result is handled by
   * onCloseActiveConfirmed().
   */
  private closeActiveWorkspace(): void {
    const activeId = this.workspaceService.activeWorkspaceId();
    if (!activeId) return;

    const workspace = this.workspaceService.workspaces().find(w => w.id === activeId);
    if (!workspace) return;

    if (workspace.activeRunCount > 0) {
      // Show non-blocking dialog instead of window.confirm
      this.closeActiveConfirmId.set(activeId);
      return;
    }

    void this.executeCloseWorkspace(activeId, false);
  }

  /** Called when the user responds to the close-active-workspace confirmation dialog. */
  onCloseActiveConfirmed(confirmed: boolean): void {
    const id = this.closeActiveConfirmId();
    this.closeActiveConfirmId.set(null);
    if (confirmed && id) {
      void this.executeCloseWorkspace(id, true);
    }
  }

  /** Getter proxy to avoid calling signal in template. */
  get closeActiveConfirmIdValue(): string | null { return this.closeActiveConfirmId(); }

  /** Computed message for the close-active-workspace dialog. */
  readonly closeActiveConfirmMessage = computed(() => {
    const id = this.closeActiveConfirmId();
    if (!id) return '';
    const ws = this.workspaceService.workspaces().find(w => w.id === id);
    if (!ws) return 'This workspace has active runs. Close anyway?';
    return `Workspace "${ws.label}" has ${ws.activeRunCount} active run(s). Closing will not stop them. Close anyway?`;
  });

  /** Getter proxy to avoid calling computed in template. */
  get closeActiveConfirmMessageValue(): string { return this.closeActiveConfirmMessage(); }

  private async executeCloseWorkspace(id: string, force: boolean): Promise<void> {
    try {
      await this.workspaceService.closeWorkspace(id, force);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.notificationService.add({ type: 'error', message: msg });
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

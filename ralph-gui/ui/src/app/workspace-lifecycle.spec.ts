/**
 * Integration tests for the full workspace lifecycle:
 * open workspace, navigate, switch, verify reload, switch back, verify nav restore.
 */
import { TestBed } from '@angular/core/testing';
import { Router, NavigationEnd } from '@angular/router';
import { RouterModule } from '@angular/router';
import { signal } from '@angular/core';
import { Subject } from 'rxjs';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

import { WorkspaceService } from './services/workspace.service';
import { WorktreesService } from './services/worktrees.service';
import { SessionsService } from './services/sessions.service';
import { NotificationService, NOTIFICATION_LISTEN_TOKEN } from './services/notification.service';
import { PreferencesService } from './services/preferences.service';
import { AppComponent } from './app.component';
import type { Workspace } from './services/workspace.service';
import type { GuiPreferences } from './types';

const defaultPrefs: GuiPreferences = {
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
    triggers: { notifyCompletion: true, notifyFailure: true, notifyDegraded: true },
  },
  session: { logAutoscroll: true, confirmCancel: true, restoreWorkspaces: true },
};

const makeWorkspace = (id: string, path: string, navState: string | null = null): Workspace => ({
  id,
  path,
  label: path.split('/').pop() ?? path,
  activeWorktree: null,
  runSummary: { running: 0, failed: 0, paused: 0 },
  navigationState: navState,
  activeRunCount: 0,
});

describe('Workspace Lifecycle Integration', () => {
  let workspacesSignal: ReturnType<typeof signal<Workspace[]>>;
  let activeWorkspaceSignal: ReturnType<typeof signal<Workspace | null>>;
  let activeWorkspaceIdSignal: ReturnType<typeof signal<string | null>>;
  let isLoadingSignal: ReturnType<typeof signal<boolean>>;
  let initializeRepoSpy: ReturnType<typeof vi.fn>;
  let fetchSessionsSpy: ReturnType<typeof vi.fn>;
  let persistNavigationSpy: ReturnType<typeof vi.fn>;
  let switchWorkspaceSpy: ReturnType<typeof vi.fn>;
  let closeWorkspaceSpy: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    workspacesSignal = signal<Workspace[]>([]);
    activeWorkspaceSignal = signal<Workspace | null>(null);
    activeWorkspaceIdSignal = signal<string | null>(null);
    // Start loading=true so the redirect effect doesn't fire before workspaces are set.
    isLoadingSignal = signal<boolean>(true);
    initializeRepoSpy = vi.fn().mockReturnValue(Promise.resolve());
    fetchSessionsSpy = vi.fn().mockReturnValue(Promise.resolve());
    persistNavigationSpy = vi.fn().mockReturnValue(Promise.resolve());
    switchWorkspaceSpy = vi.fn();
    closeWorkspaceSpy = vi.fn().mockReturnValue(Promise.resolve());

    await TestBed.configureTestingModule({
      imports: [AppComponent, RouterModule.forRoot([])],
      providers: [
        {
          provide: WorkspaceService,
          useValue: {
            workspaces: workspacesSignal.asReadonly(),
            activeWorkspaceId: activeWorkspaceIdSignal.asReadonly(),
            activeWorkspace: activeWorkspaceSignal.asReadonly(),
            isLoading: isLoadingSignal.asReadonly(),
            switchWorkspace: switchWorkspaceSpy,
            closeWorkspace: closeWorkspaceSpy,
            persistNavigation: persistNavigationSpy,
            setNavigationState: vi.fn(),
          },
        },
        {
          provide: WorktreesService,
          useValue: {
            worktrees: signal([]).asReadonly(),
            activeWorktreePath: signal(null).asReadonly(),
            lastRepoPath: signal(null).asReadonly(),
            switchContext: vi.fn(),
            initializeRepo: initializeRepoSpy,
          },
        },
        {
          provide: SessionsService,
          useValue: {
            sessions: signal([]).asReadonly(),
            status: signal('idle').asReadonly(),
            isLoading: signal(false).asReadonly(),
            fetchSessions: fetchSessionsSpy,
          },
        },
        {
          provide: PreferencesService,
          useValue: {
            preferences: signal(defaultPrefs).asReadonly(),
            isLoading: signal(false).asReadonly(),
            isFirstRun: signal(false).asReadonly(),
            save: vi.fn().mockReturnValue(Promise.resolve()),
          },
        },
        {
          provide: NotificationService,
          useValue: {
            isPanelOpen: signal(false).asReadonly(),
            unreadCount: () => 0,
            notifications: signal([]).asReadonly(),
            togglePanel: vi.fn(),
            closePanel: vi.fn(),
            dismiss: vi.fn(),
            dismissAll: vi.fn(),
            markAllRead: vi.fn(),
            add: vi.fn(),
          },
        },
        {
          provide: NOTIFICATION_LISTEN_TOKEN,
          useValue: vi.fn().mockReturnValue(
            Promise.resolve(vi.fn())
          ),
        },
      ],
    }).compileComponents();
  });

  it('should call initializeRepo with workspace A path when workspace A is activated', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    const router = TestBed.inject(Router);
    vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

    const wsA = makeWorkspace('ws-a', '/projects/repo-a');
    workspacesSignal.set([wsA]);
    activeWorkspaceSignal.set(wsA);
    isLoadingSignal.set(false);

    fixture.detectChanges();
    await Promise.resolve(); // Flush microtask queue

    expect(initializeRepoSpy).toHaveBeenCalledWith('/projects/repo-a');
  });

  it('should call fetchSessions with workspace A path when workspace A is activated', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    const router = TestBed.inject(Router);
    vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

    const wsA = makeWorkspace('ws-a', '/projects/repo-a');
    workspacesSignal.set([wsA]);
    activeWorkspaceSignal.set(wsA);
    isLoadingSignal.set(false);

    fixture.detectChanges();
    await Promise.resolve(); // Flush microtask queue

    expect(fetchSessionsSpy).toHaveBeenCalledWith('/projects/repo-a');
  });

  it('should reload data with workspace B path after switching from A to B', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    const router = TestBed.inject(Router);
    vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

    const wsA = makeWorkspace('ws-a', '/projects/repo-a');
    const wsB = makeWorkspace('ws-b', '/projects/repo-b');
    workspacesSignal.set([wsA, wsB]);
    activeWorkspaceSignal.set(wsA);
    isLoadingSignal.set(false);
    fixture.detectChanges();
    await Promise.resolve();

    // Switch to workspace B
    activeWorkspaceSignal.set(wsB);
    fixture.detectChanges();
    await Promise.resolve();

    expect(initializeRepoSpy).toHaveBeenCalledWith('/projects/repo-b');
    expect(fetchSessionsSpy).toHaveBeenCalledWith('/projects/repo-b');
  });

  it('should restore navigation state when switching to workspace with saved nav', async () => {
    const router = TestBed.inject(Router);
    const navigateSpy = vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

    const fixture = TestBed.createComponent(AppComponent);

    const wsA = makeWorkspace('ws-a', '/projects/repo-a', '/sessions');
    const wsB = makeWorkspace('ws-b', '/projects/repo-b', '/worktrees');
    workspacesSignal.set([wsA, wsB]);
    isLoadingSignal.set(false);

    // First activation (initialLoadComplete = false → no nav restore)
    activeWorkspaceSignal.set(wsA);
    fixture.detectChanges();
    await Promise.resolve();

    // Switch to B (initialLoadComplete = true → restore nav)
    activeWorkspaceSignal.set(wsB);
    fixture.detectChanges();
    await Promise.resolve();

    expect(navigateSpy).toHaveBeenCalledWith(['/worktrees']);
  });

  describe('navigation state persistence with debounce', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should persist navigation state when NavigationEnd fires for non-exempt route', async () => {
      const router = TestBed.inject(Router);
      vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));
      const routerEvents$ = (router as unknown as { events: Subject<NavigationEnd> }).events;

      const fixture = TestBed.createComponent(AppComponent);

      const wsA = makeWorkspace('ws-a', '/projects/repo-a');
      workspacesSignal.set([wsA]);
      activeWorkspaceSignal.set(wsA);
      activeWorkspaceIdSignal.set('ws-a');
      isLoadingSignal.set(false);
      fixture.detectChanges();
      await Promise.resolve();

      routerEvents$.next(new NavigationEnd(1, '/sessions', '/sessions'));
      vi.advanceTimersByTime(350); // past debounce

      expect(persistNavigationSpy).toHaveBeenCalledWith('ws-a', '/sessions');
    });
  });
});

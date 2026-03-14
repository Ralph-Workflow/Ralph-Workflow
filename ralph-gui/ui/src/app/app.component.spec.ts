import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { Router, NavigationEnd } from '@angular/router';
import { RouterModule } from '@angular/router';
import { AppComponent } from './app.component';
import { WorktreesService } from './services/worktrees.service';
import { WorkspaceService } from './services/workspace.service';
import { SessionsService } from './services/sessions.service';
import { NotificationService, NOTIFICATION_LISTEN_TOKEN } from './services/notification.service';
import { PreferencesService } from './services/preferences.service';
import { signal, WritableSignal } from '@angular/core';
import { Subject } from 'rxjs';
import type { WorktreeInfo } from './types';
import type { Workspace } from './services/workspace.service';

describe('AppComponent', () => {
  let component: AppComponent;
  let fixture: ComponentFixture<AppComponent>;
  let worktreesSignal: WritableSignal<WorktreeInfo[]>;
  let activeWorktreePathSignal: WritableSignal<string | null>;
  let lastRepoPathSignal: WritableSignal<string | null>;
  let notificationIsPanelOpenSignal: WritableSignal<boolean>;
  let workspacesSignal: WritableSignal<Workspace[]>;
  let activeWorkspaceSignal: WritableSignal<Workspace | null>;
  let activeWorkspaceIdSignal: WritableSignal<string | null>;
  let isLoadingSignal: WritableSignal<boolean>;
  let prefsIsLoadingSignal: WritableSignal<boolean>;
  let prefsIsFirstRunSignal: WritableSignal<boolean>;
  let initializeRepoSpy: ReturnType<typeof vi.fn>;
  let fetchSessionsSpy: ReturnType<typeof vi.fn>;
  let persistNavigationSpy: ReturnType<typeof vi.fn>;

  const createMockWorktreesService = () => ({
    worktrees: worktreesSignal.asReadonly(),
    activeWorktreePath: activeWorktreePathSignal.asReadonly(),
    lastRepoPath: lastRepoPathSignal.asReadonly(),
    switchContext: vi.fn(),
    initializeRepo: initializeRepoSpy,
  });

  const createMockWorkspaceService = () => ({
    workspaces: workspacesSignal.asReadonly(),
    activeWorkspaceId: activeWorkspaceIdSignal.asReadonly(),
    activeWorkspace: activeWorkspaceSignal.asReadonly(),
    isLoading: isLoadingSignal.asReadonly(),
    switchWorkspace: vi.fn(),
    closeWorkspace: vi.fn().mockReturnValue(Promise.resolve()),
    persistNavigation: persistNavigationSpy,
    setNavigationState: vi.fn(),
  });

  const createMockPreferencesService = () => ({
    preferences: signal({ theme: 'dark', accentColor: '#f59e0b', sidebarWidth: 220 } as unknown as ReturnType<PreferencesService['preferences']>).asReadonly(),
    isLoading: prefsIsLoadingSignal.asReadonly(),
    isFirstRun: prefsIsFirstRunSignal.asReadonly(),
    save: vi.fn().mockReturnValue(Promise.resolve()),
  });

  const createMockNotificationService = () => ({
    isPanelOpen: notificationIsPanelOpenSignal.asReadonly(),
    unreadCount: () => 0,
    notifications: signal([]).asReadonly(),
    togglePanel: vi.fn(),
    closePanel: vi.fn(),
    dismiss: vi.fn(),
    dismissAll: vi.fn(),
    markAllRead: vi.fn(),
    add: vi.fn(),
  });

  const createMockSessionsService = () => ({
    sessions: signal([]).asReadonly(),
    status: signal('idle').asReadonly(),
    isLoading: signal(false).asReadonly(),
    fetchSessions: fetchSessionsSpy,
  });

  const createMockWorkspace = (overrides: Partial<Workspace> = {}): Workspace => ({
    id: 'ws-1',
    path: '/path/to/repo',
    label: 'repo',
    activeWorktree: null,
    runSummary: { running: 0, failed: 0, paused: 0 },
    navigationState: null,
    activeRunCount: 0,
    ...overrides,
  });

  beforeEach(async () => {
    worktreesSignal = signal<WorktreeInfo[]>([]);
    activeWorktreePathSignal = signal<string | null>(null);
    lastRepoPathSignal = signal<string | null>(null);
    notificationIsPanelOpenSignal = signal<boolean>(false);
    workspacesSignal = signal<Workspace[]>([]);
    activeWorkspaceSignal = signal<Workspace | null>(null);
    activeWorkspaceIdSignal = signal<string | null>(null);
    isLoadingSignal = signal<boolean>(true);
    prefsIsLoadingSignal = signal<boolean>(true);
    prefsIsFirstRunSignal = signal<boolean>(false);
    initializeRepoSpy = vi.fn().mockReturnValue(Promise.resolve());
    fetchSessionsSpy = vi.fn().mockReturnValue(Promise.resolve());
    persistNavigationSpy = vi.fn().mockReturnValue(Promise.resolve());

    await TestBed.configureTestingModule({
      imports: [AppComponent, RouterModule.forRoot([])],
      providers: [
        { provide: WorktreesService, useFactory: createMockWorktreesService },
        { provide: WorkspaceService, useFactory: createMockWorkspaceService },
        { provide: NotificationService, useFactory: createMockNotificationService },
        { provide: PreferencesService, useFactory: createMockPreferencesService },
        { provide: SessionsService, useFactory: createMockSessionsService },
        {
          provide: NOTIFICATION_LISTEN_TOKEN,
          useValue: vi.fn().mockReturnValue(Promise.resolve(vi.fn())),
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    component = fixture.componentInstance;
  });

  describe('contextDisplay', () => {
    it('should show "Select repository..." when no context is set', () => {
      expect(component.contextDisplay).toBe('Select repository...');
    });

    it('should show worktree name when active worktree is set', () => {
      activeWorktreePathSignal.set('/path/to/worktree');
      worktreesSignal.set([
        { path: '/path/to/worktree', name: 'feature-branch', branch: 'feature-branch', is_main: false, has_active_run: false },
      ]);
      lastRepoPathSignal.set('/path/to/repo');

      fixture.detectChanges();

      expect(component.contextDisplay).toBe('feature-branch');
    });

    it('should show repo folder name when last repo path is set', () => {
      lastRepoPathSignal.set('/Users/test/projects/my-repo');
      activeWorktreePathSignal.set(null);

      fixture.detectChanges();

      expect(component.contextDisplay).toBe('my-repo');
    });
  });

  describe('keyboard shortcuts', () => {
    it('should toggle help on "?" key', () => {
      expect(component.showHelp()).toBe(false);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: '?' }));

      expect(component.showHelp()).toBe(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: '?' }));

      expect(component.showHelp()).toBe(false);
    });

    it('should close help on Escape key', () => {
      component.showHelp.set(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(component.showHelp()).toBe(false);
    });

    it('should close notification panel on Escape when open', () => {
      notificationIsPanelOpenSignal.set(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'Escape' }));

      const notificationService = TestBed.inject(NotificationService);
      expect(notificationService.closePanel).toHaveBeenCalled();
    });

    it('should ignore shortcuts when focus is on input', () => {
      const mockTarget = { tagName: 'INPUT', isContentEditable: false } as HTMLElement;
      const inputEvent = { key: '?', target: mockTarget, preventDefault: () => {} } as unknown as KeyboardEvent;

      component.handleKeyboard(inputEvent);

      expect(component.showHelp()).toBe(false);
    });

    it('should ignore shortcuts when focus is on textarea', () => {
      const mockTarget = { tagName: 'TEXTAREA', isContentEditable: false } as HTMLElement;
      const textareaEvent = { key: '?', target: mockTarget, preventDefault: () => {} } as unknown as KeyboardEvent;

      component.handleKeyboard(textareaEvent);

      expect(component.showHelp()).toBe(false);
    });

    it('should navigate to /preferences on g+p', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate');

      // Simulate g press
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'g' }));
      // Simulate p press
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'p' }));

      expect(navigateSpy).toHaveBeenCalledWith(['/preferences']);
    });

    it('should navigate to /preferences on Ctrl+,', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate');

      component.handleKeyboard(new KeyboardEvent('keydown', { key: ',', ctrlKey: true }));

      expect(navigateSpy).toHaveBeenCalledWith(['/preferences']);
    });

    it('should set openNewSession signal to true on Ctrl+N', () => {
      expect(component.openNewSession()).toBe(false);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'n', ctrlKey: true }));

      expect(component.openNewSession()).toBe(true);
    });

    it('should dispatch contextual-search event on Ctrl+F', () => {
      let eventDispatched = false;
      const listener = (e: Event) => {
        if (e.type === 'ralph:contextual-search') {
          eventDispatched = true;
        }
      };
      window.addEventListener('ralph:contextual-search', listener);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'f', ctrlKey: true }));

      expect(eventDispatched).toBe(true);
      window.removeEventListener('ralph:contextual-search', listener);
    });

    it('should toggle showCommandPalette on Ctrl+K', () => {
      expect(component.showCommandPalette()).toBe(false);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));

      expect(component.showCommandPalette()).toBe(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));

      expect(component.showCommandPalette()).toBe(false);
    });

    it('should close command palette on Escape', () => {
      component.showCommandPalette.set(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(component.showCommandPalette()).toBe(false);
    });
  });

  describe('selectContext', () => {
    it('should call switchContext when path is provided', () => {
      lastRepoPathSignal.set('/repo');
      const mockService = TestBed.inject(WorktreesService);

      component.selectContext('/worktree');

      expect(mockService.switchContext).toHaveBeenCalledWith('/repo', '/worktree');
    });
  });

  describe('closeHelp', () => {
    it('should close help modal', () => {
      component.showHelp.set(true);

      component.closeHelp();

      expect(component.showHelp()).toBe(false);
    });
  });

  describe('welcome redirect', () => {
    it('should navigate to /welcome when loading completes with empty workspaces (returning user)', async () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

      fixture.detectChanges();

      // Simulate loading completion: not first run, empty workspaces
      prefsIsFirstRunSignal.set(false);
      prefsIsLoadingSignal.set(false);
      isLoadingSignal.set(false);
      workspacesSignal.set([]);

      fixture.detectChanges();
      await Promise.resolve(); // Flush microtask queue

      expect(navigateSpy).toHaveBeenCalledWith(['/welcome']);
    });

    it('should navigate to /onboarding when loading completes with empty workspaces on first run', async () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

      fixture.detectChanges();

      // Simulate first run: isFirstRun=true, empty workspaces
      prefsIsFirstRunSignal.set(true);
      prefsIsLoadingSignal.set(false);
      isLoadingSignal.set(false);
      workspacesSignal.set([]);

      fixture.detectChanges();
      await Promise.resolve(); // Flush microtask queue

      expect(navigateSpy).toHaveBeenCalledWith(['/onboarding']);
    });

    it('should not navigate to /welcome when workspaces exist after loading', async () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

      fixture.detectChanges();

      // Simulate loading completion with workspaces present
      const mockWorkspace: Workspace = {
        id: 'ws-1',
        path: '/some/path',
        label: 'My Repo',
        activeWorktree: null,
        runSummary: { running: 0, failed: 0, paused: 0 },
        navigationState: null,
        activeRunCount: 0,
      };
      workspacesSignal.set([mockWorkspace]);
      prefsIsLoadingSignal.set(false);
      isLoadingSignal.set(false);

      fixture.detectChanges();
      await Promise.resolve(); // Flush microtask queue

      expect(navigateSpy).not.toHaveBeenCalledWith(['/welcome']);
      expect(navigateSpy).not.toHaveBeenCalledWith(['/onboarding']);
    });
  });

  describe('shortcut groups', () => {
    it('should expose shortcutGroups with multiple categories', () => {
      expect(component.shortcutGroups).toBeTruthy();
      expect(component.shortcutGroups.length).toBeGreaterThan(1);
    });

    it('should have a Navigation category', () => {
      const nav = component.shortcutGroups.find(g => g.category === 'Navigation');
      expect(nav).toBeTruthy();
    });

    it('should have an Actions category', () => {
      const actions = component.shortcutGroups.find(g => g.category === 'Actions');
      expect(actions).toBeTruthy();
    });

    it('should have a General category', () => {
      const general = component.shortcutGroups.find(g => g.category === 'General');
      expect(general).toBeTruthy();
    });

    it('should render grouped categories in the help overlay', () => {
      component.showHelp.set(true);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Navigation');
      expect(compiled.textContent).toContain('Actions');
      expect(compiled.textContent).toContain('General');
    });

    it('each shortcut group should have shortcuts', () => {
      for (const group of component.shortcutGroups) {
        expect(group.shortcuts.length).toBeGreaterThan(0);
      }
    });
  });

  describe('activity bar tooltips', () => {
    it('should have tooltips on nav items with keyboard shortcuts', () => {
      expect(component.navItems).toBeTruthy();
      const homeItem = component.navItems.find(i => i.path === '/');
      expect(homeItem?.tooltip).toContain('Home');
      expect(homeItem?.tooltip).toContain('g');
    });

    it('should include keyboard shortcut in Home tooltip', () => {
      const homeItem = component.navItems.find(i => i.path === '/');
      expect(homeItem?.tooltip).toContain('g then h');
    });

    it('should include keyboard shortcut in Sessions tooltip', () => {
      const sessionsItem = component.navItems.find(i => i.path === '/sessions');
      expect(sessionsItem?.tooltip).toContain('g then s');
    });

    it('should include keyboard shortcut in Preferences bottom nav tooltip', () => {
      const prefItem = component.navItemsBottom.find(i => i.path === '/preferences');
      expect(prefItem?.tooltip).toContain('Preferences');
      expect(prefItem?.tooltip).toContain('g then p');
    });
  });

  describe('workspace context switching', () => {
    it('should call initializeRepo when activeWorkspace changes', async () => {
      fixture.detectChanges();

      const ws = createMockWorkspace({ id: 'ws-1', path: '/path/to/repo-1' });
      activeWorkspaceSignal.set(ws);
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve(); // Flush microtask queue

      expect(initializeRepoSpy).toHaveBeenCalledWith('/path/to/repo-1');
    });

    it('should call fetchSessions when activeWorkspace changes', async () => {
      fixture.detectChanges();

      const ws = createMockWorkspace({ id: 'ws-1', path: '/path/to/repo-1' });
      activeWorkspaceSignal.set(ws);
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve(); // Flush microtask queue

      expect(fetchSessionsSpy).toHaveBeenCalledWith('/path/to/repo-1');
    });

    it('should navigate to saved navigationState when workspace changes after initial load', async () => {
      const router = TestBed.inject(Router);
      const navigateSpy = vi.spyOn(router, 'navigate').mockReturnValue(Promise.resolve(true));

      // Initial load
      fixture.detectChanges();
      await Promise.resolve();

      // Set initial workspace (triggers initialLoadComplete flag)
      const ws1 = createMockWorkspace({ id: 'ws-1', path: '/path/to/repo-1', navigationState: '/sessions' });
      activeWorkspaceSignal.set(ws1);
      workspacesSignal.set([ws1]);
      fixture.detectChanges();
      await Promise.resolve();

      // Switch to ws2 with different nav state
      const ws2 = createMockWorkspace({ id: 'ws-2', path: '/path/to/repo-2', navigationState: '/worktrees' });
      activeWorkspaceSignal.set(ws2);
      fixture.detectChanges();
      await Promise.resolve();

      expect(navigateSpy).toHaveBeenCalledWith(['/worktrees']);
    });

    it('should not initializeRepo when activeWorkspace is null', async () => {
      fixture.detectChanges();
      activeWorkspaceSignal.set(null);
      fixture.detectChanges();
      await Promise.resolve();
      expect(initializeRepoSpy).not.toHaveBeenCalled();
    });
  });

  describe('navigation state persistence', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should persist navigation state on NavigationEnd events', async () => {
      const router = TestBed.inject(Router);
      const routerEvents$ = (router as unknown as { events: Subject<NavigationEnd> }).events;

      const ws = createMockWorkspace({ id: 'ws-1', path: '/path/to/repo-1' });
      activeWorkspaceSignal.set(ws);
      activeWorkspaceIdSignal.set('ws-1');
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve();

      // Emit a NavigationEnd event
      routerEvents$.next(new NavigationEnd(1, '/sessions', '/sessions'));
      vi.advanceTimersByTime(350); // past debounce

      expect(persistNavigationSpy).toHaveBeenCalledWith('ws-1', '/sessions');
    });

    it('should not persist navigation state for exempt routes (/welcome)', async () => {
      const router = TestBed.inject(Router);
      const routerEvents$ = (router as unknown as { events: Subject<NavigationEnd> }).events;

      const ws = createMockWorkspace({ id: 'ws-1' });
      activeWorkspaceSignal.set(ws);
      activeWorkspaceIdSignal.set('ws-1');
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve();

      routerEvents$.next(new NavigationEnd(1, '/welcome', '/welcome'));
      vi.advanceTimersByTime(350);

      expect(persistNavigationSpy).not.toHaveBeenCalled();
    });

    it('should not persist navigation state for exempt routes (/onboarding)', async () => {
      const router = TestBed.inject(Router);
      const routerEvents$ = (router as unknown as { events: Subject<NavigationEnd> }).events;

      const ws = createMockWorkspace({ id: 'ws-1' });
      activeWorkspaceSignal.set(ws);
      activeWorkspaceIdSignal.set('ws-1');
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve();

      routerEvents$.next(new NavigationEnd(1, '/onboarding', '/onboarding'));
      vi.advanceTimersByTime(350);

      expect(persistNavigationSpy).not.toHaveBeenCalled();
    });
  });

  describe('sidebar resize', () => {
    beforeEach(() => {
      fixture.detectChanges();
    });

    it('should start with default sidebar width', () => {
      expect(component.sidebarWidth()).toBe(220);
    });

    it('should update sidebar width on drag', () => {
      // Simulate drag start at x=220
      const startEvent = new MouseEvent('mousedown', { clientX: 220, bubbles: true });
      component.onSidebarResizeStart(startEvent);

      // Move 50px to the right
      const moveEvent = new MouseEvent('mousemove', { clientX: 270, bubbles: true });
      document.dispatchEvent(moveEvent);

      expect(component.sidebarWidth()).toBe(270);
    });

    it('should clamp width to minimum (180px)', () => {
      // Start at 220
      const startEvent = new MouseEvent('mousedown', { clientX: 300, bubbles: true });
      component.onSidebarResizeStart(startEvent);

      // Move far to left (beyond min)
      const moveEvent = new MouseEvent('mousemove', { clientX: 0, bubbles: true });
      document.dispatchEvent(moveEvent);

      expect(component.sidebarWidth()).toBe(180);
    });

    it('should clamp width to maximum (400px)', () => {
      // Start at 220
      const startEvent = new MouseEvent('mousedown', { clientX: 220, bubbles: true });
      component.onSidebarResizeStart(startEvent);

      // Move far to right (beyond max)
      const moveEvent = new MouseEvent('mousemove', { clientX: 1000, bubbles: true });
      document.dispatchEvent(moveEvent);

      expect(component.sidebarWidth()).toBe(400);
    });

    it('should persist width to preferences on drag end', async () => {
      const mockPreferencesService = TestBed.inject(PreferencesService);
      const saveSpy = vi.spyOn(mockPreferencesService, 'save');

      // Drag from 220 to 300
      const startEvent = new MouseEvent('mousedown', { clientX: 220, bubbles: true });
      component.onSidebarResizeStart(startEvent);

      const moveEvent = new MouseEvent('mousemove', { clientX: 300, bubbles: true });
      document.dispatchEvent(moveEvent);

      const endEvent = new MouseEvent('mouseup', { bubbles: true });
      document.dispatchEvent(endEvent);

      await Promise.resolve();
      fixture.detectChanges();

      expect(saveSpy).toHaveBeenCalledWith(expect.objectContaining({ sidebarWidth: 300 }));
    });
  });

  describe('close active workspace (Ctrl+W)', () => {
    it('should show close confirmation dialog when Ctrl+W is pressed and workspace has active runs', async () => {
      const ws = createMockWorkspace({ id: 'ws-1', activeRunCount: 2 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Initially no dialog
      expect(component.closeActiveConfirmId()).toBeNull();

      // Trigger Ctrl+W
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      // Dialog should be shown
      expect(component.closeActiveConfirmId()).toBe('ws-1');
    });

    it('should NOT show dialog when Ctrl+W is pressed and workspace has no active runs', async () => {
      const workspaceService = TestBed.inject(WorkspaceService);
      const ws = createMockWorkspace({ id: 'ws-1', activeRunCount: 0 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Trigger Ctrl+W
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      await Promise.resolve();
      fixture.detectChanges();

      // No dialog shown; workspace should be closed directly
      expect(component.closeActiveConfirmId()).toBeNull();
      expect(workspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1', false);
    });

    it('should close workspace with force=true when dialog is confirmed', async () => {
      const workspaceService = TestBed.inject(WorkspaceService);
      const ws = createMockWorkspace({ id: 'ws-1', activeRunCount: 1 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Trigger Ctrl+W to show dialog
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      // Confirm the dialog
      component.onCloseActiveConfirmed(true);

      await Promise.resolve();
      fixture.detectChanges();

      expect(component.closeActiveConfirmId()).toBeNull();
      expect(workspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1', true);
    });

    it('should not close workspace when dialog is cancelled', async () => {
      const workspaceService = TestBed.inject(WorkspaceService);
      const ws = createMockWorkspace({ id: 'ws-1', activeRunCount: 1 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Trigger Ctrl+W to show dialog
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      // Cancel the dialog
      component.onCloseActiveConfirmed(false);

      await Promise.resolve();
      fixture.detectChanges();

      expect(component.closeActiveConfirmId()).toBeNull();
      expect(workspaceService.closeWorkspace).not.toHaveBeenCalled();
    });

    it('should show error notification when workspace close fails', async () => {
      const workspaceService = TestBed.inject(WorkspaceService);
      const notificationService = TestBed.inject(NotificationService);
      // Use mockImplementation to create the rejected promise lazily (at call time, not setup time)
      // so zone.js can track it correctly from the moment it's consumed by the await.
      vi.spyOn(workspaceService, 'closeWorkspace').mockImplementation(
        (): Promise<void> => Promise.reject(new Error('Close failed')),
      );

      const ws = createMockWorkspace({ id: 'ws-1', activeRunCount: 0 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Trigger Ctrl+W (no active runs, closes directly)
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      await Promise.resolve();

      expect(notificationService.add).toHaveBeenCalledWith(expect.objectContaining({ type: 'error' }));
    });

    it('should include workspace label and run count in dialog message', async () => {
      const ws = createMockWorkspace({ id: 'ws-1', label: 'my-project', activeRunCount: 3 });
      workspacesSignal.set([ws]);
      activeWorkspaceIdSignal.set('ws-1');
      activeWorkspaceSignal.set(ws);

      fixture.detectChanges();
      await Promise.resolve();

      // Trigger Ctrl+W to show dialog
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'w', ctrlKey: true }));

      const msg = component.closeActiveConfirmMessageValue;
      expect(msg).toContain('my-project');
      expect(msg).toContain('3');
    });
  });

  describe('workspace-switching loading state', () => {
    it('should be false initially before any workspace activates', () => {
      fixture.detectChanges();
      expect(component.isSwitchingWorkspace()).toBe(false);
    });

    it('should set isSwitchingWorkspace to true during workspace switch', async () => {
      // Make initializeRepo and fetchSessions return pending promises
      let resolveInit!: () => void;
      let resolveFetch!: () => void;
      initializeRepoSpy.mockReturnValue(new Promise<void>(r => { resolveInit = r; }));
      fetchSessionsSpy.mockReturnValue(new Promise<void>(r => { resolveFetch = r; }));

      fixture.detectChanges();

      const ws = createMockWorkspace({ id: 'ws-1', path: '/path/to/repo' });
      activeWorkspaceSignal.set(ws);
      workspacesSignal.set([ws]);

      fixture.detectChanges();
      await Promise.resolve();

      // Still switching while promises pending
      expect(component.isSwitchingWorkspace()).toBe(true);

      // Resolve both
      resolveInit();
      resolveFetch();
      await Promise.resolve();
      fixture.detectChanges();

      expect(component.isSwitchingWorkspace()).toBe(false);
    });
  });
});

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Router } from '@angular/router';
import { RouterModule } from '@angular/router';
import { AppComponent } from './app.component';
import { WorktreesService } from './services/worktrees.service';
import { WorkspaceService } from './services/workspace.service';
import { NotificationService, NOTIFICATION_LISTEN_TOKEN } from './services/notification.service';
import { PreferencesService } from './services/preferences.service';
import { signal, WritableSignal } from '@angular/core';
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
  let isLoadingSignal: WritableSignal<boolean>;
  let prefsIsLoadingSignal: WritableSignal<boolean>;
  let prefsIsFirstRunSignal: WritableSignal<boolean>;

  const createMockWorktreesService = () => ({
    worktrees: worktreesSignal.asReadonly(),
    activeWorktreePath: activeWorktreePathSignal.asReadonly(),
    lastRepoPath: lastRepoPathSignal.asReadonly(),
    switchContext: jasmine.createSpy('switchContext'),
  });

  const createMockWorkspaceService = () => ({
    workspaces: workspacesSignal.asReadonly(),
    activeWorkspaceId: signal<string | null>(null).asReadonly(),
    activeWorkspace: signal<Workspace | null>(null).asReadonly(),
    isLoading: isLoadingSignal.asReadonly(),
    switchWorkspace: jasmine.createSpy('switchWorkspace'),
    closeWorkspace: jasmine.createSpy('closeWorkspace').and.returnValue(Promise.resolve()),
  });

  const createMockPreferencesService = () => ({
    preferences: signal({ theme: 'dark', accentColor: '#f59e0b' } as unknown as ReturnType<PreferencesService['preferences']>).asReadonly(),
    isLoading: prefsIsLoadingSignal.asReadonly(),
    isFirstRun: prefsIsFirstRunSignal.asReadonly(),
    save: jasmine.createSpy('save').and.returnValue(Promise.resolve()),
  });

  const createMockNotificationService = () => ({
    isPanelOpen: notificationIsPanelOpenSignal.asReadonly(),
    unreadCount: () => 0,
    notifications: signal([]).asReadonly(),
    togglePanel: jasmine.createSpy('togglePanel'),
    closePanel: jasmine.createSpy('closePanel'),
    dismiss: jasmine.createSpy('dismiss'),
    dismissAll: jasmine.createSpy('dismissAll'),
    markAllRead: jasmine.createSpy('markAllRead'),
    add: jasmine.createSpy('add'),
  });

  beforeEach(async () => {
    worktreesSignal = signal<WorktreeInfo[]>([]);
    activeWorktreePathSignal = signal<string | null>(null);
    lastRepoPathSignal = signal<string | null>(null);
    notificationIsPanelOpenSignal = signal<boolean>(false);
    workspacesSignal = signal<Workspace[]>([]);
    isLoadingSignal = signal<boolean>(true);
    prefsIsLoadingSignal = signal<boolean>(true);
    prefsIsFirstRunSignal = signal<boolean>(false);

    await TestBed.configureTestingModule({
      imports: [AppComponent, RouterModule.forRoot([])],
      providers: [
        { provide: WorktreesService, useFactory: createMockWorktreesService },
        { provide: WorkspaceService, useFactory: createMockWorkspaceService },
        { provide: NotificationService, useFactory: createMockNotificationService },
        { provide: PreferencesService, useFactory: createMockPreferencesService },
        {
          provide: NOTIFICATION_LISTEN_TOKEN,
          useValue: jasmine.createSpy('listen').and.returnValue(
            Promise.resolve(jasmine.createSpy('unlisten'))
          ),
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
      const navigateSpy = spyOn(router, 'navigate');

      // Simulate g press
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'g' }));
      // Simulate p press
      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'p' }));

      expect(navigateSpy).toHaveBeenCalledWith(['/preferences']);
    });

    it('should navigate to /preferences on Ctrl+,', () => {
      const router = TestBed.inject(Router);
      const navigateSpy = spyOn(router, 'navigate');

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
    it('should navigate to /welcome when loading completes with empty workspaces (returning user)', fakeAsync(() => {
      const router = TestBed.inject(Router);
      const navigateSpy = spyOn(router, 'navigate').and.returnValue(Promise.resolve(true));

      fixture.detectChanges();

      // Simulate loading completion: not first run, empty workspaces
      prefsIsFirstRunSignal.set(false);
      prefsIsLoadingSignal.set(false);
      isLoadingSignal.set(false);
      workspacesSignal.set([]);

      fixture.detectChanges();
      tick();

      expect(navigateSpy).toHaveBeenCalledWith(['/welcome']);
    }));

    it('should navigate to /onboarding when loading completes with empty workspaces on first run', fakeAsync(() => {
      const router = TestBed.inject(Router);
      const navigateSpy = spyOn(router, 'navigate').and.returnValue(Promise.resolve(true));

      fixture.detectChanges();

      // Simulate first run: isFirstRun=true, empty workspaces
      prefsIsFirstRunSignal.set(true);
      prefsIsLoadingSignal.set(false);
      isLoadingSignal.set(false);
      workspacesSignal.set([]);

      fixture.detectChanges();
      tick();

      expect(navigateSpy).toHaveBeenCalledWith(['/onboarding']);
    }));

    it('should not navigate to /welcome when workspaces exist after loading', fakeAsync(() => {
      const router = TestBed.inject(Router);
      const navigateSpy = spyOn(router, 'navigate').and.returnValue(Promise.resolve(true));

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
      tick();

      expect(navigateSpy).not.toHaveBeenCalledWith(['/welcome']);
      expect(navigateSpy).not.toHaveBeenCalledWith(['/onboarding']);
    }));
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

    it('should persist width to preferences on drag end', fakeAsync(async () => {
      const mockPreferencesService = TestBed.inject(PreferencesService) as jasmine.SpyObj<PreferencesService>;
      const saveSpy = mockPreferencesService.save as jasmine.Spy;

      // Drag from 220 to 300
      const startEvent = new MouseEvent('mousedown', { clientX: 220, bubbles: true });
      component.onSidebarResizeStart(startEvent);

      const moveEvent = new MouseEvent('mousemove', { clientX: 300, bubbles: true });
      document.dispatchEvent(moveEvent);

      const endEvent = new MouseEvent('mouseup', { bubbles: true });
      document.dispatchEvent(endEvent);

      tick();
      await fixture.whenStable();

      expect(saveSpy).toHaveBeenCalledWith(jasmine.objectContaining({ sidebarWidth: 300 }));
    }));
  });
});

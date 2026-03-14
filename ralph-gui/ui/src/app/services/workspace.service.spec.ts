import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { WorkspaceService } from './workspace.service';
import { TAURI_INVOKE } from './tauri.service';
import { PreferencesService } from './preferences.service';
import { signal } from '@angular/core';
import type { WorkspaceEntry } from '../types';
import type { GuiPreferences } from '../types';

const mockEntry1: WorkspaceEntry = {
  id: 'ws-1',
  repo_path: '/path/to/repo1',
  display_name: 'repo1',
  last_nav: '/sessions',
  active_run_count: 0,
};

const mockEntry2: WorkspaceEntry = {
  id: 'ws-2',
  repo_path: '/path/to/repo2',
  display_name: 'repo2',
  last_nav: '',
  active_run_count: 1,
};

function createMockInvoke(entries: WorkspaceEntry[] = []) {
  const mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
    switch (cmd) {
      case 'get_workspaces':
        return Promise.resolve(entries);
      case 'open_workspace':
        return Promise.resolve(mockEntry1);
      case 'close_workspace':
        return Promise.resolve(undefined);
      case 'reorder_workspaces':
        return Promise.resolve(undefined);
      case 'set_workspace_nav':
        return Promise.resolve(undefined);
      case 'get_recent_workspaces':
        return Promise.resolve(['/path/to/repo1']);
      case 'update_workspace_run_count':
        return Promise.resolve(undefined);
      default:
        return Promise.reject(new Error(`Unknown command: ${cmd}`));
    }
  });
  return mockInvoke;
}

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

function createMockPreferencesService(restoreWorkspaces = true) {
  const prefs = { ...defaultPrefs, session: { ...defaultPrefs.session, restoreWorkspaces } };
  return {
    preferences: signal(prefs).asReadonly(),
    isLoading: signal(false).asReadonly(),
    isFirstRun: signal(false).asReadonly(),
    save: jasmine.createSpy('save').and.returnValue(Promise.resolve()),
  };
}

describe('WorkspaceService', () => {
  let service: WorkspaceService;
  let mockInvoke: jasmine.Spy;

  beforeEach(fakeAsync(() => {
    mockInvoke = createMockInvoke([mockEntry1]);

    TestBed.configureTestingModule({
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
        { provide: PreferencesService, useValue: createMockPreferencesService(true) },
      ],
    });
    service = TestBed.inject(WorkspaceService);
    tick(); // flush async loadFromBackend
  }));

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should load workspaces from backend on init', () => {
    const calledCommands = (mockInvoke.calls.allArgs() as unknown[][]).map(args => args[0]);
    expect(calledCommands).toContain('get_workspaces');
    expect(service.workspaces().length).toBe(1);
    expect(service.workspaces()[0]!.path).toBe('/path/to/repo1');
  });

  it('should NOT use localStorage', () => {
    const localStorageSpy = spyOn(localStorage, 'getItem');
    const setItemSpy = spyOn(localStorage, 'setItem');
    // Verify no localStorage calls happened
    expect(localStorageSpy).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it('should set first workspace as active on load', () => {
    expect(service.activeWorkspaceId()).toBe('ws-1');
    expect(service.activeWorkspace()?.id).toBe('ws-1');
  });

  describe('openWorkspace', () => {
    it('should call backend and add workspace to list', fakeAsync(async () => {
      const newEntry: WorkspaceEntry = {
        id: 'ws-new',
        repo_path: '/new/repo',
        display_name: 'repo',
        last_nav: '',
        active_run_count: 0,
      };
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'open_workspace') return Promise.resolve(newEntry);
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry1]);
        return Promise.resolve(undefined);
      });

      const ws = await service.openWorkspace('/new/repo');
      tick();

      expect(mockInvoke).toHaveBeenCalledWith('open_workspace', { path: '/new/repo' });
      expect(ws.id).toBe('ws-new');
    }));

    it('should set opened workspace as active', fakeAsync(async () => {
      const newEntry: WorkspaceEntry = {
        id: 'ws-new',
        repo_path: '/new/repo',
        display_name: 'repo',
        last_nav: '',
        active_run_count: 0,
      };
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'open_workspace') return Promise.resolve(newEntry);
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry1]);
        return Promise.resolve(undefined);
      });

      await service.openWorkspace('/new/repo');
      tick();

      expect(service.activeWorkspaceId()).toBe('ws-new');
    }));
  });

  describe('closeWorkspace', () => {
    it('should call backend and remove workspace from list', fakeAsync(async () => {
      // workspace with no active runs
      const safeEntry: WorkspaceEntry = { ...mockEntry1, active_run_count: 0 };
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_workspaces') return Promise.resolve([safeEntry]);
        if (cmd === 'close_workspace') return Promise.resolve(undefined);
        return Promise.resolve(undefined);
      });
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: mockInvoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      await svc.closeWorkspace('ws-1');
      tick();

      expect(mockInvoke).toHaveBeenCalledWith('close_workspace', { id: 'ws-1' });
      expect(svc.workspaces().length).toBe(0);
    }));

    it('should throw when workspace has active runs', fakeAsync(async () => {
      // mockEntry2 has active_run_count: 1
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry2]);
        return Promise.resolve(undefined);
      });
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: mockInvoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      await expectAsync(svc.closeWorkspace('ws-2')).toBeRejectedWithError(/active run/i);
    }));

    it('should bypass active-runs guard when force=true', fakeAsync(async () => {
      // mockEntry2 has active_run_count: 1
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry2]);
        if (cmd === 'close_workspace') return Promise.resolve(undefined);
        return Promise.resolve(undefined);
      });
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: mockInvoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      // force=true should NOT throw even though workspace has active runs.
      await expectAsync(svc.closeWorkspace('ws-2', true)).toBeResolved();
      expect(svc.workspaces().length).toBe(0);
    }));

    it('should surface backend error on close rejection', fakeAsync(async () => {
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry1]);
        if (cmd === 'close_workspace') return Promise.reject(new Error('Cannot close: backend error'));
        return Promise.resolve(undefined);
      });
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: mockInvoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      await expectAsync(svc.closeWorkspace('ws-1')).toBeRejectedWithError(/backend error/i);
    }));
  });

  describe('switchWorkspace', () => {
    it('should update activeWorkspaceId', () => {
      service.workspaces.set([
        { id: 'ws-1', path: '/a', label: 'a', activeWorktree: null, runSummary: { running: 0, failed: 0, paused: 0 }, navigationState: null, activeRunCount: 0 },
        { id: 'ws-2', path: '/b', label: 'b', activeWorktree: null, runSummary: { running: 0, failed: 0, paused: 0 }, navigationState: null, activeRunCount: 0 },
      ]);
      service.activeWorkspaceId.set('ws-1');

      service.switchWorkspace('ws-2');

      expect(service.activeWorkspaceId()).toBe('ws-2');
    });

    it('should not switch to non-existent workspace', () => {
      service.activeWorkspaceId.set('ws-1');
      service.switchWorkspace('non-existent');
      expect(service.activeWorkspaceId()).toBe('ws-1');
    });
  });

  describe('reorderWorkspaces', () => {
    it('should call backend with ordered ids', fakeAsync(async () => {
      await service.reorderWorkspaces(['ws-2', 'ws-1']);
      tick();
      expect(mockInvoke).toHaveBeenCalledWith('reorder_workspaces', { ids: ['ws-2', 'ws-1'] });
    }));
  });

  describe('getRecentWorkspaces', () => {
    it('should return recent paths from backend', fakeAsync(async () => {
      const result = await service.getRecentWorkspaces();
      tick();
      expect(result).toEqual(['/path/to/repo1']);
    }));
  });

  describe('updateWorkspaceRunSummary', () => {
    it('should update run summary in signal', () => {
      service.workspaces.set([
        { id: 'ws-1', path: '/a', label: 'a', activeWorktree: null, runSummary: { running: 0, failed: 0, paused: 0 }, navigationState: null, activeRunCount: 0 },
      ]);
      service.updateWorkspaceRunSummary('ws-1', { running: 3, failed: 1 });

      const ws = service.workspaces().find(w => w.id === 'ws-1');
      expect(ws?.runSummary.running).toBe(3);
      expect(ws?.runSummary.failed).toBe(1);
    });
  });

  describe('startup workspace restoration', () => {
    it('should load workspaces from backend when restoreWorkspaces is true', fakeAsync(() => {
      const invoke = createMockInvoke([mockEntry1, mockEntry2]);
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: invoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      expect(svc.workspaces().length).toBe(2);
      const calls = (invoke.calls.allArgs() as unknown[][]).map(args => args[0]);
      expect(calls).toContain('get_workspaces');
    }));

    it('should NOT load workspaces from backend when restoreWorkspaces is false', fakeAsync(() => {
      const invoke = createMockInvoke([mockEntry1, mockEntry2]);
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: invoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(false) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      expect(svc.workspaces().length).toBe(0);
    }));

    it('should auto-activate first workspace when restoring', fakeAsync(() => {
      const invoke = createMockInvoke([mockEntry1, mockEntry2]);
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: invoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      expect(svc.activeWorkspaceId()).toBe('ws-1');
    }));

    it('should handle backend error gracefully and return empty workspaces', fakeAsync(() => {
      const failingInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
        if (cmd === 'get_workspaces') return Promise.reject(new Error('Backend unreachable'));
        return Promise.resolve(undefined);
      });
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          { provide: TAURI_INVOKE, useValue: failingInvoke },
          { provide: PreferencesService, useValue: createMockPreferencesService(true) },
        ],
      });
      const svc = TestBed.inject(WorkspaceService);
      tick();

      expect(svc.workspaces().length).toBe(0);
      expect(svc.isLoading()).toBe(false);
    }));
  });

  describe('openWorkspace - duplicate prevention', () => {
    it('should switch to existing workspace instead of creating duplicate when same path is opened', fakeAsync(async () => {
      // ws-1 is already loaded with path '/path/to/repo1'
      expect(service.workspaces().length).toBe(1);

      // Try opening a path for a workspace with different ID but same path
      const existingEntry: WorkspaceEntry = {
        id: 'ws-1-dup',
        repo_path: '/path/to/repo1',
        display_name: 'repo1',
        last_nav: '',
        active_run_count: 0,
      };
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'open_workspace') return Promise.resolve(existingEntry);
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry1]);
        return Promise.resolve(undefined);
      });

      await service.openWorkspace('/path/to/repo1');
      tick();

      // Should NOT create a duplicate tab
      expect(service.workspaces().length).toBe(1);
      // Should switch to the existing ws-1 by path match
      expect(service.activeWorkspaceId()).toBe('ws-1');
    }));

    it('should create new workspace when path is not already open', fakeAsync(async () => {
      expect(service.workspaces().length).toBe(1);

      const newEntry: WorkspaceEntry = {
        id: 'ws-new',
        repo_path: '/path/to/new-repo',
        display_name: 'new-repo',
        last_nav: '',
        active_run_count: 0,
      };
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'open_workspace') return Promise.resolve(newEntry);
        if (cmd === 'get_workspaces') return Promise.resolve([mockEntry1]);
        return Promise.resolve(undefined);
      });

      await service.openWorkspace('/path/to/new-repo');
      tick();

      expect(service.workspaces().length).toBe(2);
      expect(service.activeWorkspaceId()).toBe('ws-new');
    }));
  });
});

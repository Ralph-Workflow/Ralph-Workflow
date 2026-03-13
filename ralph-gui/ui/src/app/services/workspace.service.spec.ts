import { TestBed } from '@angular/core/testing';
import { WorkspaceService, Workspace } from './workspace.service';

describe('WorkspaceService', () => {
  let service: WorkspaceService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(WorkspaceService);
    
    spyOn(localStorage, 'getItem').and.returnValue(null);
    spyOn(localStorage, 'setItem');
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('openWorkspace', () => {
    it('should create a new workspace', () => {
      const ws = service.openWorkspace('/path/to/repo');
      
      expect(ws.path).toBe('/path/to/repo');
      expect(ws.label).toBe('repo');
      expect(ws.id).toBeDefined();
      expect(service.workspaces().length).toBe(1);
    });

    it('should set the new workspace as active', () => {
      const ws = service.openWorkspace('/path/to/repo');
      
      expect(service.activeWorkspaceId()).toBe(ws.id);
      expect(service.activeWorkspace()?.id).toBe(ws.id);
    });

    it('should reuse existing workspace if path already exists', () => {
      const ws1 = service.openWorkspace('/path/to/repo');
      const ws2 = service.openWorkspace('/path/to/repo');
      
      expect(ws1.id).toBe(ws2.id);
      expect(service.workspaces().length).toBe(1);
    });

    it('should switch to existing workspace if path already exists', () => {
      const ws1 = service.openWorkspace('/path/to/repo1');
      service.openWorkspace('/path/to/repo2');
      
      service.openWorkspace('/path/to/repo1');
      
      expect(service.activeWorkspaceId()).toBe(ws1.id);
      expect(service.workspaces().length).toBe(2);
    });

    it('should extract label from path correctly', () => {
      const ws = service.openWorkspace('/Users/test/projects/my-cool-project');
      expect(ws.label).toBe('my-cool-project');
    });
  });

  describe('closeWorkspace', () => {
    it('should remove workspace from list', () => {
      const ws = service.openWorkspace('/path/to/repo');
      service.closeWorkspace(ws.id);
      
      expect(service.workspaces().length).toBe(0);
    });

    it('should switch to first remaining workspace when closing active', () => {
      const ws1 = service.openWorkspace('/path/to/repo1');
      const ws2 = service.openWorkspace('/path/to/repo2');
      
      service.closeWorkspace(ws2.id);
      
      expect(service.activeWorkspaceId()).toBe(ws1.id);
    });

    it('should clear active workspace id when closing last workspace', () => {
      const ws = service.openWorkspace('/path/to/repo');
      service.closeWorkspace(ws.id);
      
      expect(service.activeWorkspaceId()).toBeNull();
    });

    it('should not affect other workspaces', () => {
      const ws1 = service.openWorkspace('/path/to/repo1');
      const ws2 = service.openWorkspace('/path/to/repo2');
      const ws3 = service.openWorkspace('/path/to/repo3');
      
      service.closeWorkspace(ws2.id);
      
      expect(service.workspaces().length).toBe(2);
      expect(service.workspaces().find(w => w.id === ws1.id)).toBeDefined();
      expect(service.workspaces().find(w => w.id === ws3.id)).toBeDefined();
    });
  });

  describe('switchWorkspace', () => {
    it('should switch active workspace', () => {
      const ws1 = service.openWorkspace('/path/to/repo1');
      service.openWorkspace('/path/to/repo2');
      
      service.switchWorkspace(ws1.id);
      
      expect(service.activeWorkspaceId()).toBe(ws1.id);
    });

    it('should not switch to non-existent workspace', () => {
      service.openWorkspace('/path/to/repo1');
      
      service.switchWorkspace('non-existent-id');
      
      expect(service.activeWorkspace()?.path).toBe('/path/to/repo1');
    });
  });

  describe('updateWorkspaceRunSummary', () => {
    it('should update run summary', () => {
      const ws = service.openWorkspace('/path/to/repo');
      
      service.updateWorkspaceRunSummary(ws.id, { running: 3, failed: 1 });
      
      const updated = service.workspaces().find(w => w.id === ws.id);
      expect(updated?.runSummary.running).toBe(3);
      expect(updated?.runSummary.failed).toBe(1);
      expect(updated?.runSummary.paused).toBe(0);
    });
  });

  describe('setActiveWorktree', () => {
    it('should set active worktree', () => {
      const ws = service.openWorkspace('/path/to/repo');
      
      service.setActiveWorktree(ws.id, '/path/to/repo/wt-1');
      
      const updated = service.workspaces().find(w => w.id === ws.id);
      expect(updated?.activeWorktree).toBe('/path/to/repo/wt-1');
    });

    it('should clear active worktree when null', () => {
      const ws = service.openWorkspace('/path/to/repo');
      service.setActiveWorktree(ws.id, '/path/to/repo/wt-1');
      
      service.setActiveWorktree(ws.id, null);
      
      const updated = service.workspaces().find(w => w.id === ws.id);
      expect(updated?.activeWorktree).toBeNull();
    });
  });

  describe('setNavigationState', () => {
    it('should set navigation state', () => {
      const ws = service.openWorkspace('/path/to/repo');
      
      service.setNavigationState(ws.id, '/sessions');
      
      const updated = service.workspaces().find(w => w.id === ws.id);
      expect(updated?.navigationState).toBe('/sessions');
    });

    it('should clear navigation state when null', () => {
      const ws = service.openWorkspace('/path/to/repo');
      service.setNavigationState(ws.id, '/sessions');
      
      service.setNavigationState(ws.id, null);
      
      const updated = service.workspaces().find(w => w.id === ws.id);
      expect(updated?.navigationState).toBeNull();
    });
  });

  describe('computed activeWorkspace', () => {
    it('should return null when no workspaces', () => {
      expect(service.activeWorkspace()).toBeNull();
    });

    it('should return active workspace', () => {
      const ws = service.openWorkspace('/path/to/repo');
      expect(service.activeWorkspace()?.id).toBe(ws.id);
    });

    it('should update when switching workspaces', () => {
      const ws1 = service.openWorkspace('/path/to/repo1');
      const ws2 = service.openWorkspace('/path/to/repo2');
      
      service.switchWorkspace(ws1.id);
      expect(service.activeWorkspace()?.id).toBe(ws1.id);
      
      service.switchWorkspace(ws2.id);
      expect(service.activeWorkspace()?.id).toBe(ws2.id);
    });
  });

  describe('persistence', () => {
    it('should load workspaces from localStorage', () => {
      const mockData: Workspace[] = [
        {
          id: 'ws-1',
          path: '/path/to/repo1',
          label: 'repo1',
          activeWorktree: null,
          runSummary: { running: 1, failed: 0, paused: 0 },
          navigationState: null,
        },
      ];
      
      (localStorage.getItem as jasmine.Spy).and.returnValue(JSON.stringify(mockData));
      
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({});
      const newService = TestBed.inject(WorkspaceService);
      
      expect(newService.workspaces().length).toBe(1);
      expect(newService.workspaces()[0]!.path).toBe('/path/to/repo1');
    });

    it('should save workspaces to localStorage on change', (done) => {
      service.openWorkspace('/path/to/repo');
      
      setTimeout(() => {
        expect(localStorage.setItem).toHaveBeenCalledWith(
          'ralph-workspaces',
          jasmine.any(String)
        );
        done();
      }, 10);
    });

    it('should handle localStorage errors gracefully', () => {
      (localStorage.getItem as jasmine.Spy).and.throwError('Storage error');
      
      expect(() => TestBed.inject(WorkspaceService)).not.toThrow();
    });
  });
});

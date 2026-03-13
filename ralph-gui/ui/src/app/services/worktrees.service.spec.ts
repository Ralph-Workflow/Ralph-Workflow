import { TestBed } from '@angular/core/testing';
import { WorktreesService } from './worktrees.service';
import { TauriService } from './tauri.service';
import type { WorktreeInfo } from '../types';

describe('WorktreesService', () => {
  let service: WorktreesService;
  let mockTauriService: jasmine.SpyObj<TauriService>;

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  beforeEach(() => {
    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      ['listWorktrees', 'createWorktree', 'switchContext'],
    );

    TestBed.configureTestingModule({
      providers: [
        { provide: TauriService, useValue: mockTauriService },
      ],
    });
    service = TestBed.inject(WorktreesService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('fetchWorktrees', () => {
    it('should fetch worktrees and update signal', async () => {
      const mockWorktrees = [createMockWorktree()];
      mockTauriService.listWorktrees.and.resolveTo(mockWorktrees);
      await service.fetchWorktrees('/repo');

      expect(mockTauriService.listWorktrees).toHaveBeenCalledWith('/repo');
      expect(service.worktrees()).toEqual(mockWorktrees);
      expect(service.status()).toBe('succeeded');
    });

    it('should handle fetch error', async () => {
      mockTauriService.listWorktrees.and.rejectWith(new Error('Failed to fetch'));
      await service.fetchWorktrees('/repo');

      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Failed to fetch');
    });
  });

  describe('createWorktree', () => {
    it('should create worktree and add to list', async () => {
      const newWorktree = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1', is_main: false });
      const createResult = { worktree: newWorktree };
      mockTauriService.createWorktree.and.resolveTo(createResult);
      
      const result = await service.createWorktree('/repo', 'feature-branch', 'wt-1', 'basePath');
      
      expect(mockTauriService.createWorktree).toHaveBeenCalledWith('/repo', 'feature-branch', 'wt-1', 'basePath');
      expect(result).toEqual(newWorktree);
      expect(service.worktrees()).toContain(newWorktree);
    });
  });

  describe('switchContext', () => {
    it('should switch context', async () => {
      mockTauriService.switchContext.and.resolveTo();
      await service.switchContext('/repo', '/worktree');
      
      expect(mockTauriService.switchContext).toHaveBeenCalledWith('/repo', '/worktree');
      expect(service.activeWorktreePath()).toBe('/worktree');
    });
  });

  describe('initializeRepo', () => {
    it('should initialize repo and update lastRepoPath signal', async () => {
      const mockWorktrees = [createMockWorktree()];
      mockTauriService.listWorktrees.and.resolveTo(mockWorktrees);

      await service.initializeRepo('/repo');

      expect(service.worktrees()).toEqual(mockWorktrees);
      expect(service.lastRepoPath()).toBe('/repo');
    });

    it('should handle initialize error', async () => {
      mockTauriService.listWorktrees.and.rejectWith(new Error('Failed to initialize'));
      await service.initializeRepo('/repo');
      
      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Failed to initialize');
    });
  });

  describe('mainWorktree computed', () => {
    it('should return main worktree', () => {
      const mainWt = createMockWorktree();
      const featureWt1 = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1', is_main: false });
      const featureWt2 = createMockWorktree({ path: '/repo/wt-2', name: 'wt-2', is_main: false });
      service.worktrees.set([mainWt, featureWt1, featureWt2]);

      expect(service.mainWorktree()).toEqual(mainWt);
    });

    it('should return null when no main worktree', () => {
      service.worktrees.set([]);
      expect(service.mainWorktree()).toBeNull();
    });
  });

  describe('repoPath computed', () => {
    it('should return main worktree path', () => {
      const mainWt = createMockWorktree();
      const featureWt1 = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1', is_main: false });
      const featureWt2 = createMockWorktree({ path: '/repo/wt-2', name: 'wt-2', is_main: false });
      service.worktrees.set([mainWt, featureWt1, featureWt2]);

      expect(service.repoPath()).toBe('/repo');
    });
  });
});

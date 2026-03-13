import { ComponentFixture, TestBed } from '@angular/core/testing';
import { InlineWorktreeCreateComponent } from './inline-worktree-create.component';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';

describe('InlineWorktreeCreateComponent', () => {
  let component: InlineWorktreeCreateComponent;
  let fixture: ComponentFixture<InlineWorktreeCreateComponent>;
  let mockWorktreesService: jasmine.SpyObj<WorktreesService>;

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  beforeEach(async () => {
    mockWorktreesService = jasmine.createSpyObj(
      'WorktreesService',
      ['fetchWorktrees', 'createWorktree'],
    );

    await TestBed.configureTestingModule({
      imports: [InlineWorktreeCreateComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(InlineWorktreeCreateComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('form validation', () => {
    it('should disable button when no repo path', () => {
      component.repoPath = '';
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should disable button when missing branch', () => {
      component.repoPath = '/repo';
      component.branch.set('');
      component.name.set('wt-51-feature');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should disable button when missing name', () => {
      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should enable button when all fields are filled', () => {
      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-51-feature');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(false);
    });
  });

  describe('branch auto-fill', () => {
    it('should auto-fill name from branch on blur when name is empty', () => {
      component.repoPath = '/repo';
      component.branch.set('wt-52-my-feature');
      component.name.set('');
      
      component.onBranchBlur();
      
      expect(component.name()).toBe('wt-52-my-feature');
    });

    it('should not overwrite existing name on blur', () => {
      component.repoPath = '/repo';
      component.branch.set('wt-52-my-feature');
      component.name.set('custom-name');
      
      component.onBranchBlur();
      
      expect(component.name()).toBe('custom-name');
    });
  });

  describe('submission', () => {
    it('should emit created event on create', async () => {
      const newWorktree = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1' });
      mockWorktreesService.createWorktree.and.resolveTo(newWorktree);
      mockWorktreesService.fetchWorktrees.and.resolveTo();

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');
      fixture.detectChanges();

      const createdSpy = spyOn(component.created, 'emit');
      await component.handleCreate();

      expect(mockWorktreesService.createWorktree).toHaveBeenCalledWith(
        '/repo',
        'feature-branch',
        'wt-1'
      );
      expect(mockWorktreesService.fetchWorktrees).toHaveBeenCalledWith('/repo');
      expect(createdSpy).toHaveBeenCalledWith(newWorktree);
    });

    it('should display error on failure', async () => {
      mockWorktreesService.createWorktree.and.rejectWith(new Error('Failed to create'));

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');
      fixture.detectChanges();

      await component.handleCreate();
      fixture.detectChanges();

      expect(component.error()).toBe('Failed to create');
      const errorEl = fixture.nativeElement.querySelector('[data-testid="wt-create-error"]');
      expect(errorEl?.textContent).toContain('Failed to create');
    });

    it('should set creating state during submission', async () => {
      const newWorktree = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1' });
      mockWorktreesService.createWorktree.and.resolveTo(newWorktree);
      mockWorktreesService.fetchWorktrees.and.resolveTo();

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');

      expect(component.creating()).toBe(false);
      
      const promise = component.handleCreate();
      expect(component.creating()).toBe(true);
      
      await promise;
      expect(component.creating()).toBe(false);
    });
  });
});

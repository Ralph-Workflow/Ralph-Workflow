import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { WelcomeComponent } from './welcome.component';
import { WorkspaceService } from '../../services/workspace.service';
import { TauriService, TAURI_INVOKE } from '../../services/tauri.service';
import { signal } from '@angular/core';
import type { Workspace } from '../../services/workspace.service';

describe('WelcomeComponent', () => {
  let component: WelcomeComponent;
  let fixture: ComponentFixture<WelcomeComponent>;
  let mockWorkspaceService: ReturnType<typeof createMockWorkspaceService>;
  let mockTauriService: ReturnType<typeof createMockTauriService>;

  function createMockWorkspaceService() {
    return {
      openWorkspace: vi.fn().mockReturnValue(
        Promise.resolve({
          id: 'ws-new',
          path: '/home/user/projects/repo1',
          label: 'repo1',
          activeWorktree: null,
          runSummary: { running: 0, failed: 0, paused: 0 },
          navigationState: null,
          activeRunCount: 0,
        }),
      ),
      getRecentWorkspaces: vi.fn().mockReturnValue(
        Promise.resolve(['/home/user/projects/repo1', '/home/user/projects/repo2']),
      ),
      workspaces: signal<Workspace[]>([]),
      activeWorkspaceId: signal<string | null>(null),
      activeWorkspace: signal<Workspace | null>(null),
      isLoading: signal<boolean>(false),
    };
  }

  function createMockTauriService() {
    return {
      openDirectoryDialog: vi.fn().mockReturnValue(Promise.resolve(null)),
    };
  }

  beforeEach(async () => {
    mockWorkspaceService = createMockWorkspaceService();
    mockTauriService = createMockTauriService();

    const mockInvoke = vi.fn().mockReturnValue(Promise.resolve([]));

    await TestBed.configureTestingModule({
      imports: [WelcomeComponent],
      providers: [
        { provide: WorkspaceService, useValue: mockWorkspaceService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(WelcomeComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  it('should load recent workspaces on init', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(mockWorkspaceService.getRecentWorkspaces).toHaveBeenCalled();
    expect(component.recentWorkspaces().length).toBe(2);
  });

  it('should render recent workspaces list', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const items = fixture.nativeElement.querySelectorAll('.recent-item');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('repo1');
  });

  it('should open workspace dialog on button click', async () => {
    mockTauriService.openDirectoryDialog.mockReturnValue(Promise.resolve('/new/path'));

    fixture.detectChanges();
    await fixture.whenStable();

    const btn = fixture.nativeElement.querySelector('.open-workspace-btn');
    btn.click();

    await fixture.whenStable();
    await fixture.whenStable();

    expect(mockTauriService.openDirectoryDialog).toHaveBeenCalled();
    expect(mockWorkspaceService.openWorkspace).toHaveBeenCalledWith('/new/path');
  });

  it('should open recent workspace on item click', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const item = fixture.nativeElement.querySelector('.recent-item');
    item.click();

    await fixture.whenStable();
    await fixture.whenStable();

    expect(mockWorkspaceService.openWorkspace).toHaveBeenCalledWith(
      '/home/user/projects/repo1',
    );
  });

  it('should show error message on failed open', async () => {
    mockTauriService.openDirectoryDialog.mockResolvedValue('/bad/path');
    mockWorkspaceService.openWorkspace.mockImplementation(() =>
      Promise.resolve().then(() => {
        throw new Error('Not a git repo');
      }),
    );

    fixture.detectChanges();
    await fixture.whenStable();

    const btn = fixture.nativeElement.querySelector('.open-workspace-btn') as HTMLButtonElement;
    btn.click();

    await fixture.whenStable();
    fixture.detectChanges();
    await fixture.whenStable();

    const error = fixture.nativeElement.querySelector('.error-banner') as HTMLElement | null;
    expect(error).toBeTruthy();
    expect(error?.textContent).toContain('Not a git repo');
  });
});

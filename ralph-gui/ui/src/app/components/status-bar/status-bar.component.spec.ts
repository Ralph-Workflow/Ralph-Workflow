import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { StatusBarComponent } from './status-bar.component';
import { WorkspaceService, Workspace } from '../../services/workspace.service';
import { signal } from '@angular/core';

describe('StatusBarComponent', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<StatusBarComponent>>;
  let component: StatusBarComponent;
  let mockActiveWorkspace: ReturnType<typeof signal<Workspace | null>>;

  const createMockWorkspace = (overrides: Partial<Workspace> = {}): Workspace => ({
    id: 'ws-1',
    label: 'test-repo',
    path: '/path',
    activeWorktree: null,
    runSummary: { running: 0, failed: 0, paused: 0 },
    navigationState: null,
    ...overrides,
  });

  beforeEach(async () => {
    mockActiveWorkspace = signal<Workspace | null>(null);

    const workspaceServiceSpy = {
      workspaces: signal<Workspace[]>([]),
      activeWorkspaceId: signal<string | null>(null),
      activeWorkspace: mockActiveWorkspace,
    };

    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        { provide: WorkspaceService, useValue: workspaceServiceSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(StatusBarComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  it('should show workspace label in left section', () => {
    mockActiveWorkspace.set(createMockWorkspace({ label: 'test-repo' }));
    fixture.detectChanges();
    expect(component.workspaceLabel()).toBe('test-repo');
  });

  it('should show "No workspace" when no active workspace', () => {
    mockActiveWorkspace.set(null);
    fixture.detectChanges();
    expect(component.workspaceLabel()).toBe('No workspace');
  });

  it('should show run summary in center section when there are active runs', () => {
    mockActiveWorkspace.set(createMockWorkspace({
      runSummary: { running: 2, failed: 1, paused: 0 }
    }));
    fixture.detectChanges();
    expect(component.runSummaryText()).toBe('2 running, 1 failed');
  });

  it('should return empty string when no active runs', () => {
    mockActiveWorkspace.set(null);
    fixture.detectChanges();
    expect(component.runSummaryText()).toBe('');
  });

  it('should show connection status with connected class', () => {
    fixture.detectChanges();
    expect(component.connectionStatus()).toBe('Connected');
    expect(component.connectionStatusClass()).toBe('status-connected');
  });
});

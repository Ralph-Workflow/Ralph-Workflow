import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { provideZonelessChangeDetection } from '@angular/core';
import { ChangesViewerComponent } from './changes-viewer.component';
import { TauriService } from '../../services/tauri.service';
import type { RunChanges, FileDiff } from '../../types';

const MOCK_FILE_DIFF_A: FileDiff = {
  path: 'src/main.rs',
  additions: 5,
  deletions: 2,
  diff_text: '@@ -1,3 +1,6 @@\n context line\n+added line 1\n+added line 2\n-removed line\n context end',
};

const MOCK_FILE_DIFF_B: FileDiff = {
  path: 'src/lib.rs',
  additions: 1,
  deletions: 0,
  diff_text: '@@ -10,4 +10,5 @@\n context\n+new feature line\n context',
};

const MOCK_RUN_CHANGES: RunChanges = {
  files: [MOCK_FILE_DIFF_A, MOCK_FILE_DIFF_B],
  total_additions: 6,
  total_deletions: 2,
  iteration: null,
};

/**
 * Waits for the component to finish loading changes and updates the view.
 * With zoneless change detection, fixture.whenStable() doesn't track async tasks,
 * so we poll the isLoading signal until it becomes false.
 */
async function waitForLoadComplete(
  fixture: { detectChanges: () => void },
  component: ChangesViewerComponent,
  timeout = 1000,
): Promise<void> {
  const startTime = Date.now();
  while (component.isLoading()) {
    if (Date.now() - startTime > timeout) {
      throw new Error('Timed out waiting for load to complete');
    }
    // Yield to the microtask queue to allow the Promise to resolve
    await Promise.resolve();
    // Trigger change detection to pick up signal updates
    fixture.detectChanges();
  }
  // Final change detection after loading completes
  fixture.detectChanges();
}

describe('ChangesViewerComponent', () => {
  let tauriServiceMock: { getRunChanges: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    tauriServiceMock = {
      getRunChanges: vi.fn().mockResolvedValue(MOCK_RUN_CHANGES),
    };

    await TestBed.configureTestingModule({
      imports: [ChangesViewerComponent],
      providers: [
        provideZonelessChangeDetection(),
        { provide: TauriService, useValue: tauriServiceMock },
      ],
    }).compileComponents();
  });

  function createComponent(runId = '', repoPath = '/repo', worktreePath: string | null = null) {
    const fixture = TestBed.createComponent(ChangesViewerComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput('runId', runId);
    fixture.componentRef.setInput('repoPath', repoPath);
    fixture.componentRef.setInput('worktreePath', worktreePath);
    return { fixture, component };
  }

  it('should create', () => {
    const { fixture, component } = createComponent();
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('empty state', () => {
    it('should show empty state when no repoPath is set', () => {
      const { fixture } = createComponent('', '');
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="changes-empty"]')).toBeTruthy();
    });

    it('should show appropriate empty state message', () => {
      const { fixture } = createComponent('', '');
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const empty = el.querySelector('[data-testid="changes-empty"]');
      expect(empty?.textContent).toContain('No code changes yet');
    });
  });

  describe('file tree renders from input data', () => {
    it('should render file list from RunChanges.files', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      const el: HTMLElement = fixture.nativeElement;
      // Component shows basenames in the file tree
      expect(el.textContent).toContain('main.rs');
      expect(el.textContent).toContain('lib.rs');
    });

    it('should show additions and deletions counts per file', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      const el: HTMLElement = fixture.nativeElement;
      // main.rs has +5 -2
      expect(el.textContent).toContain('+5');
      expect(el.textContent).toContain('-2');
    });

    it('should show summary bar with total additions and deletions', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      const el: HTMLElement = fixture.nativeElement;
      const summaryBar = el.querySelector('[data-testid="changes-summary"]');
      expect(summaryBar).toBeTruthy();
      expect(summaryBar?.textContent).toContain('+6');
      expect(summaryBar?.textContent).toContain('-2');
    });

    it('should show total files changed in summary', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      const el: HTMLElement = fixture.nativeElement;
      const summaryBar = el.querySelector('[data-testid="changes-summary"]');
      expect(summaryBar?.textContent).toContain('2');
    });
  });

  describe('file selection and diff view', () => {
    it('should select first file by default after loading', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      expect(component.selectedFile()).toBe(MOCK_FILE_DIFF_A);
    });

    it('should update selected file when another is clicked', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      component.selectFile(MOCK_FILE_DIFF_B);
      fixture.detectChanges();

      expect(component.selectedFile()).toBe(MOCK_FILE_DIFF_B);
    });
  });

  describe('diff line coloring', () => {
    it('should classify addition lines (starting with +, not ++)', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();

      const isAddition = component.getLineClass('+added line');
      const isNotAddition = component.getLineClass('++header line');
      expect(isAddition).toBe('diff-line--added');
      expect(isNotAddition).toBe('');
    });

    it('should classify removal lines (starting with -, not --)', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();

      const isRemoval = component.getLineClass('-removed line');
      const isNotRemoval = component.getLineClass('--header line');
      expect(isRemoval).toBe('diff-line--removed');
      expect(isNotRemoval).toBe('');
    });

    it('should return empty class for context lines', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();

      expect(component.getLineClass(' context line')).toBe('');
      expect(component.getLineClass('@@ -1,3 +1,6 @@')).toBe('');
    });
  });

  describe('copy as patch', () => {
    it('should expose copyAsPatch method', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      expect(typeof component.copyAsPatch).toBe('function');
    });

    it('should call navigator.clipboard.writeText with all diff_text when copying', async () => {
      // Mock clipboard API since it may not be available in test environment
      const mockWriteText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText: mockWriteText },
        writable: true,
        configurable: true,
      });

      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      await component.copyAsPatch();

      const expectedText = MOCK_FILE_DIFF_A.diff_text + '\n' + MOCK_FILE_DIFF_B.diff_text;
      expect(mockWriteText).toHaveBeenCalledWith(expectedText);
    });
  });

  describe('iteration filter', () => {
    it('should start with selectedIteration as null (all iterations)', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      expect(component.selectedIteration()).toBeNull();
    });

    it('should refetch changes when iteration changes', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);

      const callCountBefore = tauriServiceMock.getRunChanges.mock.calls.length;
      component.setIteration(2);
      await waitForLoadComplete(fixture, component);

      expect(tauriServiceMock.getRunChanges.mock.calls.length).toBeGreaterThan(callCountBefore);
    });
  });

  describe('file tree grouping by directory', () => {
    it('should group files by their parent directory', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      // Both files are in 'src/' directory
      const groups = component.fileGroups();
      expect(groups.length).toBe(1);
      expect(groups[0]?.directory).toBe('src');
    });

    it('should include all files in the correct group', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      const groups = component.fileGroups();
      expect(groups[0]?.files.length).toBe(2);
    });
  });

  describe('filterIteration input', () => {
    it('should pre-select filter iteration when filterIteration input is set', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.componentRef.setInput('filterIteration', 2);
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      expect(component.selectedIteration()).toBe(2);
    });
  });

  describe('unified/side-by-side toggle', () => {
    it('should start in unified view mode (sideBySide = false)', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      expect(component.sideBySide()).toBe(false);
    });

    it('should switch to side-by-side view when toggleViewMode is called', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();

      component.toggleViewMode();
      fixture.detectChanges();

      expect(component.sideBySide()).toBe(true);
    });

    it('should switch back to unified view when toggleViewMode is called again', () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();

      component.toggleViewMode();
      component.toggleViewMode();
      fixture.detectChanges();

      expect(component.sideBySide()).toBe(false);
    });

    it('should render the toggle button in the summary bar', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      const toggleBtn = el.querySelector('[data-testid="view-mode-toggle"]');
      expect(toggleBtn).toBeTruthy();
    });

    it('should show "Side-by-side" label when in unified mode', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      // Default unified mode
      expect(component.sideBySide()).toBe(false);
      const el: HTMLElement = fixture.nativeElement;
      const toggleBtn = el.querySelector('[data-testid="view-mode-toggle"]');
      expect(toggleBtn?.textContent?.trim()).toBe('Side-by-side');
    });

    it('should show "Unified" label when in side-by-side mode', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      component.toggleViewMode();
      fixture.detectChanges();

      expect(component.sideBySide()).toBe(true);
      const el: HTMLElement = fixture.nativeElement;
      const toggleBtn = el.querySelector('[data-testid="view-mode-toggle"]');
      expect(toggleBtn?.textContent?.trim()).toBe('Unified');
    });

    it('should show unified diff panel when in unified mode', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      expect(component.sideBySide()).toBe(false);
      const el: HTMLElement = fixture.nativeElement;
      const unifiedView = el.querySelector('[data-testid="diff-unified"]');
      const sideBySideView = el.querySelector('[data-testid="diff-side-by-side"]');
      expect(unifiedView).toBeTruthy();
      expect(sideBySideView).toBeFalsy();
    });

    it('should show side-by-side panels when in side-by-side mode', async () => {
      const { fixture, component } = createComponent('run-1', '/repo');
      fixture.detectChanges();
      await waitForLoadComplete(fixture, component);
      fixture.detectChanges();

      component.toggleViewMode();
      fixture.detectChanges();

      expect(component.sideBySide()).toBe(true);
      const el: HTMLElement = fixture.nativeElement;
      const unifiedView = el.querySelector('[data-testid="diff-unified"]');
      const sideBySideView = el.querySelector('[data-testid="diff-side-by-side"]');
      expect(unifiedView).toBeFalsy();
      expect(sideBySideView).toBeTruthy();
    });
  });
});

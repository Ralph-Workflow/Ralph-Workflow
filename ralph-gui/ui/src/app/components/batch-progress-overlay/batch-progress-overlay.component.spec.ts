import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { BatchProgressOverlayComponent } from './batch-progress-overlay.component';

describe('BatchProgressOverlayComponent', () => {
  let fixture: ComponentFixture<BatchProgressOverlayComponent>;
  let component: BatchProgressOverlayComponent;

  function createComponent(options: {
    operationType: 'resume' | 'cancel' | 'delete';
    targetRunIds: string[];
    result?: { succeeded: number; failed: number; errors: Record<string, string> } | null;
    isInProgress?: boolean;
    runIdToName?: Record<string, string>;
  }) {
    fixture = TestBed.createComponent(BatchProgressOverlayComponent);
    fixture.componentRef.setInput('operationType', options.operationType);
    fixture.componentRef.setInput('targetRunIds', options.targetRunIds);
    if (options.result !== undefined) {
      fixture.componentRef.setInput('result', options.result);
    }
    if (options.isInProgress !== undefined) {
      fixture.componentRef.setInput('isInProgress', options.isInProgress);
    }
    if (options.runIdToName) {
      fixture.componentRef.setInput('runIdToName', options.runIdToName);
    }
    fixture.detectChanges();
    component = fixture.componentInstance;
    return { fixture, component };
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BatchProgressOverlayComponent],
    }).compileComponents();
  });

  describe('operation title', () => {
    it('should show "Resuming N sessions" for resume operation', () => {
      createComponent({ operationType: 'resume', targetRunIds: ['run-1', 'run-2'] });

      expect(component.operationTitle_()).toBe('Resuming 2 sessions');
    });

    it('should show "Cancelling N sessions" for cancel operation', () => {
      createComponent({ operationType: 'cancel', targetRunIds: ['run-1', 'run-2', 'run-3'] });

      expect(component.operationTitle_()).toBe('Cancelling 3 sessions');
    });

    it('should show "Deleting N sessions" for delete operation', () => {
      createComponent({ operationType: 'delete', targetRunIds: ['run-1'] });

      expect(component.operationTitle_()).toBe('Deleting 1 session');
    });

    it('should show "Batch Action Complete" when result is set', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        result: { succeeded: 1, failed: 0, errors: {} },
      });

      expect(component.operationTitle_()).toBe('Batch Action Complete');
    });
  });

  describe('progress indicator', () => {
    it('should show progress indicator when isInProgress is true', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        isInProgress: true,
      });

      expect(component.isInProgress()).toBe(true);
    });

    it('should calculate progress count correctly', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2', 'run-3', 'run-4'],
        result: { succeeded: 2, failed: 1, errors: { 'run-3': 'error' } },
      });

      const progress = component.progressCount();
      expect(progress.done).toBe(3);
      expect(progress.total).toBe(4);
    });

    it('should show 0 done when no result yet', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: null,
      });

      const progress = component.progressCount();
      expect(progress.done).toBe(0);
      expect(progress.total).toBe(2);
    });

    it('should calculate progress percent correctly', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2', 'run-3', 'run-4'],
        result: { succeeded: 2, failed: 1, errors: { 'run-3': 'error' } },
      });

      expect(component.progressPercent()).toBe(75);
    });
  });

  describe('result summary', () => {
    it('should show result summary when result is provided', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2', 'run-3'],
        result: { succeeded: 2, failed: 1, errors: { 'run-3': 'API error' } },
      });

      expect(component.resultSummary()).not.toBeNull();
      expect(component.resultSummary()?.succeeded).toBe(2);
      expect(component.resultSummary()?.failed).toBe(1);
    });

    it('should detect partial success', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: { succeeded: 1, failed: 1, errors: { 'run-2': 'error' } },
      });

      expect(component.hasPartialSuccess()).toBe(true);
    });

    it('should detect all success', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: { succeeded: 2, failed: 0, errors: {} },
      });

      expect(component.hasPartialSuccess()).toBe(false);
      expect(component.hasFailures()).toBe(false);
    });

    it('should detect all failures', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: { succeeded: 0, failed: 2, errors: { 'run-1': 'e1', 'run-2': 'e2' } },
      });

      expect(component.hasFailures()).toBe(true);
      expect(component.hasPartialSuccess()).toBe(false);
    });
  });

  describe('run display info', () => {
    it('should compute run display infos with names from map', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        runIdToName: { 'run-1': 'Add login feature' },
      });

      const infos = component.runDisplayInfos();
      expect(infos.length).toBe(1);
      expect(infos[0]!.displayName).toBe('Add login feature');
      expect(infos[0]!.runId).toBe('run-1');
    });

    it('should truncate long run IDs not in map', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1234567890abcdef'],
      });

      const infos = component.runDisplayInfos();
      expect(infos[0]!.displayName).toBe('run-1234567890...');
    });

    it('should include error from result', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: { succeeded: 1, failed: 1, errors: { 'run-2': 'API error' } },
      });

      const infos = component.runDisplayInfos();
      expect(infos.find(i => i.runId === 'run-1')?.error).toBeNull();
      expect(infos.find(i => i.runId === 'run-2')?.error).toBe('API error');
    });

    it('should return null error when no result', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        result: null,
      });

      const infos = component.runDisplayInfos();
      expect(infos[0]!.error).toBeNull();
    });
  });

  describe('events', () => {
    it('should emit closed event when Close button clicked', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        isInProgress: false,
      });

      const closedSpy = vi.fn();
      component.closed.subscribe(closedSpy);

      component.onClose();

      expect(closedSpy).toHaveBeenCalled();
    });

    it('should emit openRun event with run_id when Open Run link clicked', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
      });

      const openRunSpy = vi.fn();
      component.openRun.subscribe(openRunSpy);

      component.onOpenRun('run-123');

      expect(openRunSpy).toHaveBeenCalledWith('run-123');
    });

    it('should not close on backdrop click when in progress', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        isInProgress: true,
      });

      const closedSpy = vi.fn();
      component.closed.subscribe(closedSpy);

      const event = {
        target: { classList: { contains: () => true } },
      } as unknown as MouseEvent;
      component.onBackdropClick(event);

      expect(closedSpy).not.toHaveBeenCalled();
    });

    it('should close on backdrop click when not in progress', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        isInProgress: false,
      });

      const closedSpy = vi.fn();
      component.closed.subscribe(closedSpy);

      const event = {
        target: { classList: { contains: (cls: string) => cls === 'dialog-backdrop' } },
      } as unknown as MouseEvent;
      component.onBackdropClick(event);

      expect(closedSpy).toHaveBeenCalled();
    });

    it('should close on escape key when not in progress', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        isInProgress: false,
      });

      const closedSpy = vi.fn();
      component.closed.subscribe(closedSpy);

      component.onEscape();

      expect(closedSpy).toHaveBeenCalled();
    });

    it('should not close on escape key when in progress', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1'],
        isInProgress: true,
      });

      const closedSpy = vi.fn();
      component.closed.subscribe(closedSpy);

      component.onEscape();

      expect(closedSpy).not.toHaveBeenCalled();
    });
  });

  describe('edge cases', () => {
    it('should handle empty targetRunIds gracefully', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: [],
        result: null,
      });

      expect(component.progressCount().total).toBe(0);
      expect(component.progressPercent()).toBe(0);
      expect(component.runDisplayInfos()).toEqual([]);
    });

    it('should handle singular vs plural in result summary', () => {
      createComponent({
        operationType: 'resume',
        targetRunIds: ['run-1', 'run-2'],
        result: { succeeded: 1, failed: 1, errors: { 'run-2': 'e' } },
      });

      expect(component.hasPartialSuccess()).toBe(true);
    });
  });
});

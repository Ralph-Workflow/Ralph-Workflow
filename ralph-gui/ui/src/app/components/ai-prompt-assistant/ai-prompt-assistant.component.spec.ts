import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { AiPromptAssistantComponent } from './ai-prompt-assistant.component';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { PromptReviewResult } from '../../types';

describe('AiPromptAssistantComponent', () => {
  let component: AiPromptAssistantComponent;
  let fixture: ComponentFixture<AiPromptAssistantComponent>;
  let mockInvoke: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    mockInvoke = vi.fn((cmd: string) => {
      if (cmd === 'get_planning_drain_agent') {
        return Promise.resolve(null);
      }
      if (cmd === 'assist_prompt_describe') {
        return Promise.resolve('Suggested prompt from AI');
      }
      if (cmd === 'assist_prompt_refine') {
        const result: PromptReviewResult = {
          suggestions: ['Be more specific', 'Add context'],
          improved_prompt: 'Improved prompt text',
        };
        return Promise.resolve(result);
      }
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [AiPromptAssistantComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AiPromptAssistantComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  describe('panel show/hide', () => {
    it('should start closed', () => {
      expect(component.isOpen()).toBe(false);
    });

    it('should open when toggle() called', () => {
      component.toggle();

      expect(component.isOpen()).toBe(true);
    });

    it('should close when toggle() called twice', () => {
      component.toggle();
      component.toggle();

      expect(component.isOpen()).toBe(false);
    });
  });

  describe('tab switching', () => {
    it('should default to describe tab', () => {
      expect(component.activeTab()).toBe('describe');
    });

    it('should switch to refine tab', () => {
      component.setTab('refine');

      expect(component.activeTab()).toBe('refine');
    });
  });

  describe('describe mode', () => {
    it('should submit description and show AI response', async () => {
      component.toggle();
      component.describeInput.set('I need a login feature');

      await component.submitDescribe();
      await fixture.whenStable();

      const messages = component.describeMessages();
      expect(messages.length).toBe(2);
      expect(messages[0]?.role).toBe('user');
      expect(messages[0]?.text).toBe('I need a login feature');
      expect(messages[1]?.role).toBe('ai');
      expect(messages[1]?.text).toBe('Suggested prompt from AI');
    });

    it('should clear input after submitting', async () => {
      component.describeInput.set('Some feature');

      await component.submitDescribe();
      await fixture.whenStable();

      expect(component.describeInput()).toBe('');
    });

    it('should set loading state while pending', async () => {
      let resolveInvoke!: (value: unknown) => void;
      mockInvoke.mockImplementation(() => new Promise(res => { resolveInvoke = res; }));

      component.describeInput.set('test');
      const promise = component.submitDescribe();

      expect(component.describeLoading()).toBe(true);

      resolveInvoke('result');
      await promise;
      await fixture.whenStable();

      expect(component.describeLoading()).toBe(false);
    });

    it('should not submit when input is empty', async () => {
      component.describeInput.set('');
      mockInvoke.mockClear();

      await component.submitDescribe();

      expect(mockInvoke).not.toHaveBeenCalledWith('assist_prompt_describe', expect.any(Object));
    });
  });

  describe('refine mode', () => {
    it('should trigger analysis when switching to refine tab with non-empty prompt', async () => {
      // Set up component with a non-empty prompt via inputs
      fixture.componentRef.setInput('currentPrompt', 'My current prompt');
      fixture.componentRef.setInput('repoPath', '/repo/a');
      fixture.detectChanges();
      await fixture.whenStable();

      component.setTab('refine');
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      await fixture.whenStable();

      expect(mockInvoke).toHaveBeenCalledWith('assist_prompt_refine', expect.any(Object));
    });

    it('should show "Enter a prompt" message when currentPrompt is empty', () => {
      fixture.componentRef.setInput('currentPrompt', '');
      fixture.detectChanges();
      component.setTab('refine');
      fixture.detectChanges();

      expect(component.refineEmptyState()).toBe(true);
    });

    it('should not trigger analysis when currentPrompt is empty', async () => {
      fixture.componentRef.setInput('currentPrompt', '');
      fixture.detectChanges();

      component.setTab('refine');
      await new Promise(r => setTimeout(r, 100));
      await fixture.whenStable();

      expect(mockInvoke).not.toHaveBeenCalledWith('assist_prompt_refine', expect.any(Object));
    });

    it('should populate refine result after analysis', async () => {
      fixture.componentRef.setInput('currentPrompt', 'My prompt');
      fixture.componentRef.setInput('repoPath', '/repo');
      fixture.detectChanges();
      await fixture.whenStable();

      component.setTab('refine');
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      await fixture.whenStable();

      const result = component.refineResult();
      expect(result).not.toBeNull();
      expect(result!.suggestions).toEqual(['Be more specific', 'Add context']);
      expect(result!.improved_prompt).toBe('Improved prompt text');
    });
  });

  describe('apply to editor', () => {
    it('should emit applyPrompt with correct text from describe mode', async () => {
      const emittedValues: string[] = [];
      component.applyPrompt.subscribe((v: string) => emittedValues.push(v));

      component.describeInput.set('Feature request');
      await component.submitDescribe();
      await fixture.whenStable();

      component.applyFromDescribe('Suggested prompt from AI');

      expect(emittedValues).toEqual(['Suggested prompt from AI']);
    });

    it('should emit applyPrompt with refined prompt text', async () => {
      const emittedValues: string[] = [];
      component.applyPrompt.subscribe((v: string) => emittedValues.push(v));

      fixture.componentRef.setInput('currentPrompt', 'My prompt');
      fixture.componentRef.setInput('repoPath', '/repo');
      fixture.detectChanges();
      await fixture.whenStable();

      component.setTab('refine');
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      await fixture.whenStable();

      component.applyRefineResult();

      expect(emittedValues).toEqual(['Improved prompt text']);
    });
  });

  describe('unconfigured agent state', () => {
    it('should show unconfigured state when planning drain agent returns null', async () => {
      // The component's planningDrainAgent is already null after ngOnInit (default mock returns null)
      // so isUnconfigured should be true.
      await new Promise(r => setTimeout(r, 50));
      await fixture.whenStable();
      fixture.detectChanges();

      expect(component.isUnconfigured()).toBe(true);
    });

    it('should not show unconfigured state when planning drain agent is configured', async () => {
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'get_planning_drain_agent') return Promise.resolve('planner');
        if (cmd === 'assist_prompt_describe') return Promise.resolve('result');
        if (cmd === 'assist_prompt_refine') return Promise.resolve({ suggestions: [], improved_prompt: null });
        return Promise.resolve(null);
      });

      // Re-create component fresh so ngOnInit runs with the right spy
      await TestBed.resetTestingModule().compileComponents();
      await TestBed.configureTestingModule({
        imports: [AiPromptAssistantComponent],
        providers: [
          provideZonelessChangeDetection(),
          provideRouter([]),
          provideAnimationsAsync(),
          { provide: TAURI_INVOKE, useValue: mockInvoke },
        ],
      }).compileComponents();
      const newFixture = TestBed.createComponent(AiPromptAssistantComponent);
      const newComponent = newFixture.componentInstance;
      newFixture.detectChanges();
      await new Promise(r => setTimeout(r, 50));
      await newFixture.whenStable();

      expect(newComponent.isUnconfigured()).toBe(false);
      expect(newComponent.planningDrainAgent()).toBe('planner');
    });
  });
});

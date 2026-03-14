import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { provideZonelessChangeDetection } from '@angular/core';
import { PromptTemplatePickerComponent } from './prompt-template-picker.component';
import { PROMPT_TEMPLATES } from '../../pages/sessions/prompt-templates';

describe('PromptTemplatePickerComponent', () => {
  let fixture: ComponentFixture<PromptTemplatePickerComponent>;
  let component: PromptTemplatePickerComponent;
  let compiled: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PromptTemplatePickerComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();

    fixture = TestBed.createComponent(PromptTemplatePickerComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    compiled = fixture.nativeElement as HTMLElement;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('all templates section', () => {
    it('should render all templates from PROMPT_TEMPLATES', () => {
      for (const tpl of PROMPT_TEMPLATES) {
        const btn = compiled.querySelector(`[data-testid="template-${tpl.id}"]`);
        expect(btn).toBeTruthy();
      }
    });

    it('should emit selectTemplate with template content when a template is clicked', () => {
      const spy = vi.fn();
      component.selectTemplate.subscribe(spy);

      const firstTemplate = PROMPT_TEMPLATES[0]!;
      const btn = compiled.querySelector(`[data-testid="template-${firstTemplate.id}"]`) as HTMLButtonElement;
      btn.click();

      expect(spy).toHaveBeenCalledWith(firstTemplate.content);
    });
  });

  describe('recently used section', () => {
    it('should NOT show recently used section before any template is selected', () => {
      const section = compiled.querySelector('[data-testid="recently-used-section"]');
      expect(section).toBeFalsy();
    });

    it('should show recently used section after a template is selected', () => {
      const firstTemplate = PROMPT_TEMPLATES[0]!;
      component.onSelectTemplate(firstTemplate);
      fixture.detectChanges();

      const section = compiled.querySelector('[data-testid="recently-used-section"]');
      expect(section).toBeTruthy();
    });

    it('should show the recently used template in the recently used section', () => {
      const firstTemplate = PROMPT_TEMPLATES[0]!;
      component.onSelectTemplate(firstTemplate);
      fixture.detectChanges();

      const recentBtn = compiled.querySelector(`[data-testid="recent-template-${firstTemplate.id}"]`);
      expect(recentBtn).toBeTruthy();
      expect(recentBtn?.textContent).toContain(firstTemplate.label);
    });

    it('should emit selectTemplate when a recently used template is clicked', () => {
      const spy = vi.fn();
      component.selectTemplate.subscribe(spy);

      const firstTemplate = PROMPT_TEMPLATES[0]!;
      component.onSelectTemplate(firstTemplate);
      fixture.detectChanges();

      spy.mockClear();

      const recentBtn = compiled.querySelector(`[data-testid="recent-template-${firstTemplate.id}"]`) as HTMLButtonElement;
      recentBtn.click();

      expect(spy).toHaveBeenCalledWith(firstTemplate.content);
    });

    it('should show multiple recently used templates up to the cap', () => {
      // Select 3 different templates (guaranteed to exist in PROMPT_TEMPLATES)
      const tpl1 = PROMPT_TEMPLATES[0]!;
      const tpl2 = PROMPT_TEMPLATES[1]!;
      const tpl3 = PROMPT_TEMPLATES[2];
      component.onSelectTemplate(tpl1);
      component.onSelectTemplate(tpl2);
      if (tpl3) {
        component.onSelectTemplate(tpl3);
      }
      fixture.detectChanges();

      const recentItems = compiled.querySelectorAll('[data-testid^="recent-template-"]');
      expect(recentItems.length).toBeLessThanOrEqual(3);
      expect(recentItems.length).toBeGreaterThan(0);
    });

    it('should not duplicate a template in recently used if selected again', () => {
      const firstTemplate = PROMPT_TEMPLATES[0]!;
      component.onSelectTemplate(firstTemplate);
      component.onSelectTemplate(firstTemplate);
      fixture.detectChanges();

      const recentItems = compiled.querySelectorAll('[data-testid^="recent-template-"]');
      expect(recentItems.length).toBe(1);
    });

    it('should move a template to the top of recently used when re-selected', () => {
      const tpl1 = PROMPT_TEMPLATES[0]!;
      const tpl2 = PROMPT_TEMPLATES[1]!;
      component.onSelectTemplate(tpl1);
      component.onSelectTemplate(tpl2);
      // Re-select tpl1 — it should now be first
      component.onSelectTemplate(tpl1);
      fixture.detectChanges();

      const recentIds = component['_recentIds']();
      expect(recentIds[0]).toBe(tpl1.id);
    });
  });

  describe('save as template button', () => {
    it('should NOT show save as template button when currentPrompt is empty', () => {
      fixture.componentRef.setInput('currentPrompt', '');
      fixture.detectChanges();

      const saveBtn = compiled.querySelector('[data-testid="save-as-template-btn"]');
      expect(saveBtn).toBeFalsy();
    });

    it('should show save as template button when currentPrompt has content', () => {
      fixture.componentRef.setInput('currentPrompt', 'Some prompt content');
      fixture.detectChanges();

      const saveBtn = compiled.querySelector('[data-testid="save-as-template-btn"]');
      expect(saveBtn).toBeTruthy();
    });

    it('should emit saveAsTemplate with current prompt content when button clicked', () => {
      const spy = vi.fn();
      component.saveAsTemplate.subscribe(spy);

      const promptContent = 'My custom prompt content';
      fixture.componentRef.setInput('currentPrompt', promptContent);
      fixture.detectChanges();

      const saveBtn = compiled.querySelector('[data-testid="save-as-template-btn"]') as HTMLButtonElement;
      saveBtn.click();

      expect(spy).toHaveBeenCalledWith(promptContent);
    });

    it('should NOT emit saveAsTemplate when currentPrompt is only whitespace', () => {
      const spy = vi.fn();
      component.saveAsTemplate.subscribe(spy);

      fixture.componentRef.setInput('currentPrompt', '   ');
      fixture.detectChanges();

      component.onSaveAsTemplate();

      expect(spy).not.toHaveBeenCalled();
    });
  });
});

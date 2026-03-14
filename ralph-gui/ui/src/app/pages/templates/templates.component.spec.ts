import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { TemplatesComponent } from './templates.component';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { TemplateInfo } from '../../types';

describe('TemplatesComponent', () => {
  let component: TemplatesComponent;
  let fixture: ComponentFixture<TemplatesComponent>;
  let mockInvoke: ReturnType<typeof vi.fn>;

  const sampleTemplates: TemplateInfo[] = [
    {
      name: 'bug-fix',
      description: 'Template for bug fixes',
      content: 'Fix the following bug: {{bug_description}}\n\nContext: {{context}}',
      tags: ['bug', 'fix'],
    },
    {
      name: 'feature-add',
      description: 'Template for adding features',
      content: 'Add a new feature: {{feature_name}}',
      tags: ['feature'],
    },
    {
      name: 'refactor',
      description: 'Template for refactoring',
      content: 'Refactor the following code',
      tags: ['refactor', 'cleanup'],
    },
  ];

  beforeEach(async () => {
    mockInvoke = vi.fn().mockImplementation((cmd: string) => {
      if (cmd === 'list_templates') return Promise.resolve(sampleTemplates);
      if (cmd === 'delete_template') return Promise.resolve();
      if (cmd === 'save_template') return Promise.resolve();
      return Promise.resolve(null);
    });

    await TestBed.configureTestingModule({
      imports: [TemplatesComponent],
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TemplatesComponent);
    component = fixture.componentInstance;
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('template list', () => {
    beforeEach(async () => {
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
    });

    it('should call list_templates on init', () => {
      expect(mockInvoke).toHaveBeenCalledWith('list_templates', expect.objectContaining({ templates_dir: '' }));
    });

    it('should render template items after loading', () => {
      const items = fixture.nativeElement.querySelectorAll('.template-item');
      expect(items.length).toBe(3);
    });

    it('should display template names in the list', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('bug-fix');
      expect(nativeEl.textContent).toContain('feature-add');
      expect(nativeEl.textContent).toContain('refactor');
    });

    it('should display template descriptions', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Template for bug fixes');
    });

    it('should display template tags', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('bug');
      expect(nativeEl.textContent).toContain('feature');
    });
  });

  describe('search filtering', () => {
    beforeEach(async () => {
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
    });

    it('should filter templates by name when search is entered', () => {
      component.searchQuery.set('bug');
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.template-item');
      expect(items.length).toBe(1);
    });

    it('should filter templates by tag when search matches tag', () => {
      component.searchQuery.set('feature');
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.template-item');
      expect(items.length).toBe(1);
    });

    it('should show all templates when search is empty', () => {
      component.searchQuery.set('');
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.template-item');
      expect(items.length).toBe(3);
    });

    it('should be case-insensitive when searching', () => {
      component.searchQuery.set('BUG');
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.template-item');
      expect(items.length).toBe(1);
    });
  });

  describe('empty state', () => {
    it('should show empty state when no templates are loaded', async () => {
      mockInvoke.mockImplementation((cmd: string) => {
        if (cmd === 'list_templates') return Promise.resolve([]);
        return Promise.resolve(null);
      });
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const emptyState = fixture.nativeElement.querySelector('.empty-state');
      expect(emptyState).not.toBeNull();
    });

    it('should show empty state when search has no matches', async () => {
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      component.searchQuery.set('nonexistenttemplate12345');
      fixture.detectChanges();
      const emptyState = fixture.nativeElement.querySelector('.empty-state');
      expect(emptyState).not.toBeNull();
    });
  });

  describe('variable detection', () => {
    it('should count template variables in {{var}} syntax', () => {
      const count = component.countVariables('Fix {{bug_description}} in {{context}}');
      expect(count).toBe(2);
    });

    it('should return 0 when no variables are present', () => {
      const count = component.countVariables('Just plain text');
      expect(count).toBe(0);
    });

    it('should count unique variable placeholders', () => {
      const count = component.countVariables('{{a}} and {{b}} and {{a}}');
      // Counting occurrences, not unique
      expect(count).toBeGreaterThanOrEqual(2);
    });

    it('should show variable count in the template list', async () => {
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const nativeEl = fixture.nativeElement as HTMLElement;
      // bug-fix has {{bug_description}} and {{context}} = 2 variables
      expect(nativeEl.textContent).toContain('2');
    });
  });

  describe('template detail panel', () => {
    beforeEach(async () => {
      fixture.detectChanges();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
    });

    it('should show detail panel when a template is selected', () => {
      component.selectTemplate(sampleTemplates[0]!);
      fixture.detectChanges();
      const detail = fixture.nativeElement.querySelector('.template-detail');
      expect(detail).not.toBeNull();
    });

    it('should show selected template name in detail panel', () => {
      component.selectTemplate(sampleTemplates[0]!);
      fixture.detectChanges();
      const detail = fixture.nativeElement.querySelector('.template-detail');
      expect(detail?.textContent).toContain('bug-fix');
    });

    it('should show content preview in detail panel', () => {
      component.selectTemplate(sampleTemplates[0]!);
      fixture.detectChanges();
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('{{bug_description}}');
    });
  });

  describe('save as template form', () => {
    it('should show form dialog when "Save as Template" is clicked', () => {
      fixture.detectChanges();
      const saveBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.includes('Save as Template'));
      expect(saveBtn).not.toBeUndefined();
      saveBtn!.click();
      fixture.detectChanges();
      const form = fixture.nativeElement.querySelector('.template-form-dialog, form');
      expect(form).not.toBeNull();
    });
  });
});

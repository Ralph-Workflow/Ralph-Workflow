import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TauriService } from '../../services/tauri.service';
import type { TemplateInfo } from '../../types';

const TEMPLATES_DIR = '';
const VARIABLE_REGEX = /\{\{[^{}]+\}\}/g;

@Component({
  selector: 'app-templates',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  templateUrl: './templates.component.html',
  styleUrl: './templates.component.css',
})
export class TemplatesComponent implements OnInit {
  private readonly tauri = inject(TauriService);

  readonly templatesDir = signal<string>(TEMPLATES_DIR);
  readonly templates = signal<TemplateInfo[]>([]);
  readonly isLoading = signal<boolean>(false);
  readonly hasLoaded = signal<boolean>(false);
  readonly searchQuery = signal<string>('');
  readonly selectedTemplate = signal<TemplateInfo | null>(null);

  readonly filteredTemplates = computed(() => {
    const query = this.searchQuery().toLowerCase();
    if (!query) return this.templates();
    return this.templates().filter(t =>
      t.name.toLowerCase().includes(query) ||
      t.description.toLowerCase().includes(query) ||
      t.tags.some(tag => tag.toLowerCase().includes(query)),
    );
  });

  readonly filteredTemplatesWithCount = computed(() =>
    this.filteredTemplates().map(t => ({
      ...t,
      varCount: this.countVariables(t.content),
    }))
  );

  readonly selectedTemplateWithCount = computed(() => {
    const t = this.selectedTemplate();
    if (!t) return null;
    return { ...t, varCount: this.countVariables(t.content) };
  });

  // Form signals
  readonly showForm = signal<boolean>(false);
  readonly editingTemplate = signal<TemplateInfo | null>(null);
  readonly formName = signal<string>('');
  readonly formDescription = signal<string>('');
  readonly formContent = signal<string>('');
  readonly formTagsString = signal<string>('');
  readonly isSaving = signal<boolean>(false);
  readonly formError = signal<string | null>(null);

  // Delete confirm
  readonly deletingTemplateName = signal<string | null>(null);

  ngOnInit(): void {
    void this.loadTemplates();
  }

  get templatesDirValue(): string { return this.templatesDir(); }
  get hasLoadedValue(): boolean { return this.hasLoaded(); }
  get isLoadingValue(): boolean { return this.isLoading(); }
  get filteredTemplatesList() { return this.filteredTemplatesWithCount(); }
  get searchQueryValue(): string { return this.searchQuery(); }
  get selectedTemplateValue() { return this.selectedTemplateWithCount(); }
  get showFormValue(): boolean { return this.showForm(); }
  get editingTemplateValue(): TemplateInfo | null { return this.editingTemplate(); }
  get formNameValue(): string { return this.formName(); }
  get formDescriptionValue(): string { return this.formDescription(); }
  get formContentValue(): string { return this.formContent(); }
  get formTagsStringValue(): string { return this.formTagsString(); }
  get formErrorValue(): string | null { return this.formError(); }
  get isSavingValue(): boolean { return this.isSaving(); }
  get deletingTemplateNameValue(): string | null { return this.deletingTemplateName(); }

  countVariables(content: string): number {
    const matches = content.match(VARIABLE_REGEX);
    return matches ? matches.length : 0;
  }

  selectTemplate(template: TemplateInfo): void {
    this.selectedTemplate.set(template);
  }

  openSaveForm(): void {
    this.editingTemplate.set(null);
    this.formName.set('');
    this.formDescription.set('');
    this.formContent.set('');
    this.formTagsString.set('');
    this.formError.set(null);
    this.showForm.set(true);
  }

  openEditForm(): void {
    const tpl = this.selectedTemplate();
    if (!tpl) return;
    this.editingTemplate.set(tpl);
    this.formName.set(tpl.name);
    this.formDescription.set(tpl.description);
    this.formContent.set(tpl.content);
    this.formTagsString.set(tpl.tags.join(', '));
    this.formError.set(null);
    this.showForm.set(true);
  }

  closeForm(): void {
    this.showForm.set(false);
    this.editingTemplate.set(null);
  }

  async saveTemplate(): Promise<void> {
    const name = this.formName().trim();
    if (!name) return;

    this.isSaving.set(true);
    this.formError.set(null);
    const tags = this.formTagsString()
      .split(',')
      .map(t => t.trim())
      .filter(t => t.length > 0);

    try {
      await this.tauri.saveTemplate(
        name,
        this.formDescription(),
        this.formContent(),
        tags,
        this.templatesDir(),
      );
      this.closeForm();
      await this.loadTemplates();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.formError.set(msg);
    } finally {
      this.isSaving.set(false);
    }
  }

  confirmDelete(name: string): void {
    this.deletingTemplateName.set(name);
  }

  cancelDelete(): void {
    this.deletingTemplateName.set(null);
  }

  async deleteTemplate(): Promise<void> {
    const name = this.deletingTemplateName();
    if (!name) return;

    try {
      await this.tauri.deleteTemplate(name, this.templatesDir());
      this.deletingTemplateName.set(null);
      if (this.selectedTemplate()?.name === name) {
        this.selectedTemplate.set(null);
      }
      await this.loadTemplates();
    } catch (err) {
      console.error('Failed to delete template:', err);
      this.deletingTemplateName.set(null);
    }
  }

  private async loadTemplates(): Promise<void> {
    this.isLoading.set(true);
    try {
      const templates = await this.tauri.listTemplates(this.templatesDir());
      this.templates.set(templates);
    } catch (err) {
      console.error('Failed to load templates:', err);
      this.templates.set([]);
    } finally {
      this.isLoading.set(false);
      this.hasLoaded.set(true);
    }
  }
}

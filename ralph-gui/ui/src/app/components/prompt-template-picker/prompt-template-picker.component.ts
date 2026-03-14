import {
  Component,
  Output,
  EventEmitter,
  ChangeDetectionStrategy,
  input,
  signal,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { PROMPT_TEMPLATES, type PromptTemplate } from '../../pages/sessions/prompt-templates';

/** Maximum number of recently used templates to track per session. */
const MAX_RECENT = 3;

@Component({
  selector: 'app-prompt-template-picker',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './prompt-template-picker.component.html',
})
export class PromptTemplatePickerComponent {
  /** Current prompt editor content — used when saving as a new template. */
  readonly currentPrompt = input<string>('');

  @Output() readonly selectTemplate = new EventEmitter<string>();
  /** Emitted when the user clicks "Save as Template" with the current prompt content. */
  @Output() readonly saveAsTemplate = new EventEmitter<string>();

  readonly templates = PROMPT_TEMPLATES;

  /**
   * Template IDs used in the current session (most-recently-used first).
   * Stored as a signal so the template is reactive.
   */
  private readonly _recentIds = signal<string[]>([]);

  /** Recently used templates, resolved to full PromptTemplate objects. */
  readonly recentTemplates = computed((): PromptTemplate[] => {
    const ids = this._recentIds();
    return ids
      .map(id => this.templates.find(t => t.id === id))
      .filter((t): t is PromptTemplate => t !== undefined);
  });

  /** Whether there is any recently used template to display. */
  get hasRecent(): boolean {
    return this._recentIds().length > 0;
  }

  /** Getter proxy to avoid calling signal in template (lint: no-call-expression). */
  get recentTemplates_() { return this.recentTemplates(); }

  /** Whether a save-as-template action is possible (needs non-empty prompt). */
  get canSaveAsTemplate(): boolean {
    return this.currentPrompt().trim().length > 0;
  }

  onSelectTemplate(tpl: PromptTemplate): void {
    // Track recently used — prepend, keep unique, cap at MAX_RECENT
    this._recentIds.update(ids => {
      const filtered = ids.filter(id => id !== tpl.id);
      return [tpl.id, ...filtered].slice(0, MAX_RECENT);
    });
    this.selectTemplate.emit(tpl.content);
  }

  onSaveAsTemplate(): void {
    const prompt = this.currentPrompt();
    if (prompt.trim()) {
      this.saveAsTemplate.emit(prompt);
    }
  }

  onHover(event: MouseEvent): void {
    (event.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)';
  }

  onLeave(event: MouseEvent): void {
    (event.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)';
  }
}

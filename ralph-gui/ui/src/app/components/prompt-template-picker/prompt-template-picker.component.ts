import { Component, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PROMPT_TEMPLATES } from '../../pages/sessions/prompt-templates';

@Component({
  selector: 'app-prompt-template-picker',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div>
      <div class="section-label" style="margin-bottom: var(--space-4);">
        Choose a starting template
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px;">
        @for (tpl of templates; track tpl.id) {
          <button
            [attr.data-testid]="'template-' + tpl.id"
            (click)="selectTemplate.emit(tpl.content)"
            (mouseenter)="onHover($event)"
            (mouseleave)="onLeave($event)"
            style="display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 14px 16px; background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); cursor: pointer; text-align: left; transition: border-color var(--transition-fast);"
          >
            <span style="font-family: var(--font-display); font-size: 13px; font-weight: 600; color: var(--text-primary); letter-spacing: -0.01em;">
              {{ tpl.label }}
            </span>
            <span style="font-size: 11px; color: var(--text-muted); line-height: 1.5;">
              {{ tpl.description }}
            </span>
          </button>
        }
      </div>
    </div>
  `,
})
export class PromptTemplatePickerComponent {
  @Output() selectTemplate = new EventEmitter<string>();

  readonly templates = PROMPT_TEMPLATES;

  onHover(event: MouseEvent): void {
    (event.currentTarget as HTMLButtonElement).style.borderColor = 'var(--accent)';
  }

  onLeave(event: MouseEvent): void {
    (event.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)';
  }
}

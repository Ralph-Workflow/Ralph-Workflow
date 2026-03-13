import { Component, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PROMPT_TEMPLATES } from '../../pages/sessions/prompt-templates';

@Component({
  selector: 'app-prompt-template-picker',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './prompt-template-picker.component.html',
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

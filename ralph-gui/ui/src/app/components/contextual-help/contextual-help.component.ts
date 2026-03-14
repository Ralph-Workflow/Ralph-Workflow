import {
  Component,
  input,
  signal,
  ChangeDetectionStrategy,
  HostListener,
  ElementRef,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Contextual help icon ([?]) that shows a tooltip/popover with help text on
 * hover or click. Used inline next to form field labels in configuration
 * forms.
 */
@Component({
  selector: 'app-contextual-help',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './contextual-help.component.html',
  styleUrl: './contextual-help.component.css',
})
export class ContextualHelpComponent {
  private readonly elementRef = inject(ElementRef);

  /** The help text displayed in the popover. */
  readonly helpText = input.required<string>();

  /** Optional label for screen readers. */
  readonly ariaLabel = input<string>('Help');

  readonly isOpen = signal(false);

  get isOpen_() { return this.isOpen(); }
  get helpText_() { return this.helpText(); }
  get ariaLabel_() { return this.ariaLabel(); }

  toggle(): void {
    this.isOpen.update(v => !v);
  }

  open(): void {
    this.isOpen.set(true);
  }

  close(): void {
    this.isOpen.set(false);
  }

  /** Close popover when clicking outside the component. */
  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (!this.elementRef.nativeElement.contains(event.target)) {
      this.isOpen.set(false);
    }
  }

  /** Close on Escape key. */
  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.isOpen.set(false);
  }
}

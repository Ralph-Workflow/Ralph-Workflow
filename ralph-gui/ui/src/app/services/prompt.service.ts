import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { PromptReviewResult } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

@Injectable({ providedIn: 'root' })
export class PromptService {
  private readonly tauri = inject(TauriService);

  // State signals
  readonly path = signal<string | null>(null);
  readonly content = signal('');
  readonly isDirty = signal(false);
  readonly reviewStatus = signal<LoadingStatus>('idle');
  readonly reviewResult = signal<PromptReviewResult | null>(null);
  readonly reviewError = signal<string | null>(null);

  // Computed signals
  readonly isReviewLoading = computed(() => this.reviewStatus() === 'loading');

  async loadFile(path: string): Promise<void> {
    try {
      const content = await this.tauri.readPromptFile(path);
      this.content.set(content);
      this.isDirty.set(false);
    } catch (e) {
      this.reviewError.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  async saveFile(path: string, content: string): Promise<void> {
    try {
      await this.tauri.savePromptFile(path, content);
      this.isDirty.set(false);
    } catch (e) {
      this.reviewError.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  async reviewPrompt(content: string): Promise<void> {
    this.reviewStatus.set('loading');
    this.reviewError.set(null);

    try {
      const result = await this.tauri.reviewPromptWithAi(content);
      this.reviewResult.set(result);
      this.reviewStatus.set('succeeded');
    } catch (e) {
      this.reviewStatus.set('failed');
      this.reviewError.set(e instanceof Error ? e.message : 'Review failed');
    }
  }

  setPath(path: string | null): void {
    this.path.set(path);
    this.isDirty.set(false);
  }

  setContent(content: string): void {
    this.content.set(content);
    this.isDirty.set(true);
  }

  revert(): void {
    this.content.set('');
    this.isDirty.set(false);
  }
}

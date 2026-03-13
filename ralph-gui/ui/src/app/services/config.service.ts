import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { ConfigView } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';
export type SaveStatus = 'idle' | 'saving' | 'saved' | 'failed';

@Injectable({ providedIn: 'root' })
export class ConfigService {
  private readonly tauri = inject(TauriService);

  // State signals
  readonly globalConfig = signal<ConfigView | null>(null);
  readonly projectConfig = signal<ConfigView | null>(null);
  readonly effectiveConfig = signal<ConfigView | null>(null);
  readonly globalStatus = signal<LoadingStatus>('idle');
  readonly projectStatus = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);
  readonly isDirty = signal(false);
  readonly aiApiKey = signal('');
  readonly aiApiKeyStatus = signal<LoadingStatus>('idle');
  readonly aiApiKeySaveStatus = signal<SaveStatus>('idle');
  readonly aiApiKeyError = signal<string | null>(null);

  // Computed signals
  readonly isGlobalLoading = computed(() => this.globalStatus() === 'loading');

  async fetchGlobalConfig(): Promise<void> {
    this.globalStatus.set('loading');
    this.error.set(null);

    try {
      const config = await this.tauri.getGlobalConfig();
      this.globalConfig.set(config);
      this.globalStatus.set('succeeded');
    } catch (e) {
      this.globalStatus.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async fetchEffectiveConfig(repoPath: string): Promise<void> {
    try {
      const config = await this.tauri.getEffectiveConfig(repoPath);
      this.effectiveConfig.set(config);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async saveGlobalConfig(configToml: string): Promise<void> {
    try {
      await this.tauri.saveGlobalConfig(configToml);
      const config = await this.tauri.getGlobalConfig();
      this.globalConfig.set(config);
      this.isDirty.set(false);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  async saveProjectConfig(repoPath: string, configToml: string): Promise<void> {
    try {
      await this.tauri.saveProjectConfig(repoPath, configToml);
      this.isDirty.set(false);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  async fetchAiApiKey(): Promise<void> {
    this.aiApiKeyStatus.set('loading');
    this.aiApiKeyError.set(null);

    try {
      const key = await this.tauri.getAiApiKey();
      this.aiApiKey.set(key);
      this.aiApiKeyStatus.set('succeeded');
    } catch (e) {
      this.aiApiKeyStatus.set('failed');
      this.aiApiKeyError.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async saveAiApiKey(apiKey: string): Promise<void> {
    this.aiApiKeySaveStatus.set('saving');
    this.aiApiKeyError.set(null);

    try {
      await this.tauri.saveAiApiKey(apiKey);
      this.aiApiKey.set(apiKey);
      this.aiApiKeySaveStatus.set('saved');
    } catch (e) {
      this.aiApiKeySaveStatus.set('failed');
      this.aiApiKeyError.set(e instanceof Error ? e.message : 'Unknown error');
      throw e;
    }
  }

  setDirty(dirty: boolean): void {
    this.isDirty.set(dirty);
  }

  clearError(): void {
    this.error.set(null);
  }

  clearAiApiKeyError(): void {
    this.aiApiKeyError.set(null);
  }

  resetAiApiKeySaveStatus(): void {
    this.aiApiKeySaveStatus.set('idle');
  }
}

import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { AgentProfile } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

@Injectable({ providedIn: 'root' })
export class AgentProfileService {
  private readonly tauri = inject(TauriService);

  // State signals
  readonly profiles = signal<AgentProfile[]>([]);
  readonly selectedProfile = signal<string | null>(null);
  readonly status = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);

  // Computed signals
  readonly isLoading = computed(() => this.status() === 'loading');

  async fetchProfiles(repoPath?: string): Promise<void> {
    this.status.set('loading');
    this.error.set(null);

    try {
      const profiles = await this.tauri.listAgentProfiles(repoPath);
      this.profiles.set(profiles);
      this.status.set('succeeded');
    } catch (e) {
      this.status.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Failed to load agent profiles');
    }
  }

  selectProfile(name: string): void {
    this.selectedProfile.set(name);
  }

  clearSelection(): void {
    this.selectedProfile.set(null);
  }
}

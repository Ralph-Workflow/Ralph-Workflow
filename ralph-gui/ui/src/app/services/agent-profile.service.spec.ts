import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { AgentProfileService } from './agent-profile.service';
import { TauriService } from './tauri.service';
import type { AgentProfile } from '../types';

describe('AgentProfileService', () => {
  let service: AgentProfileService;
  let mockTauriService: {
    listAgentProfiles: ReturnType<typeof vi.fn>;
  };

  const createMockProfile = (overrides: Partial<AgentProfile> = {}): AgentProfile => ({
    name: 'default',
    developer_agent: 'claude-3.5-sonnet',
    reviewer_agent: 'claude-3.5-sonnet',
    ...overrides,
  });

  beforeEach(() => {
    mockTauriService = {
      listAgentProfiles: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        { provide: TauriService, useValue: mockTauriService },
      ],
    });
    service = TestBed.inject(AgentProfileService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('fetchProfiles', () => {
    it('should fetch profiles and update signal', async () => {
      const mockProfiles = [createMockProfile()];
      mockTauriService.listAgentProfiles.mockResolvedValue(mockProfiles);
      await service.fetchProfiles('/repo');
      expect(mockTauriService.listAgentProfiles).toHaveBeenCalledWith('/repo');
      expect(service.profiles()).toEqual(mockProfiles);
      expect(service.status()).toBe('succeeded');
    });

    it('should handle fetch error', async () => {
      mockTauriService.listAgentProfiles.mockRejectedValue(new Error('Failed to fetch'));
      await service.fetchProfiles('/repo');
      expect(service.status()).toBe('failed');
      expect(service.error()).toBe('Failed to fetch');
    });
  });

  describe('selectProfile', () => {
    it('should select profile', async () => {
      service.selectProfile('claude-3.5-sonnet');
      expect(service.selectedProfile()).toBe('claude-3.5-sonnet');
    });
  });

  describe('clearSelection', () => {
    it('should clear selection', () => {
      service.selectProfile('claude-3.5-sonnet');
      service.clearSelection();
      expect(service.selectedProfile()).toBeNull();
    });
  });
});

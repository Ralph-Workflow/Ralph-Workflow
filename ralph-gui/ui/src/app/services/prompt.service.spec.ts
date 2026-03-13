import { TestBed } from '@angular/core/testing';
import { PromptService } from './prompt.service';
import { TauriService } from './tauri.service';
import type { PromptReviewResult } from '../types';

describe('PromptService', () => {
  let service: PromptService;
  let mockTauriService: jasmine.SpyObj<TauriService>;

  beforeEach(() => {
    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      ['readPromptFile', 'savePromptFile', 'reviewPromptWithAi'],
    );

    TestBed.configureTestingModule({
      providers: [
        { provide: TauriService, useValue: mockTauriService },
      ],
    });
    service = TestBed.inject(PromptService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('loadFile', () => {
    it('should load file and update content', async () => {
      mockTauriService.readPromptFile.and.resolveTo('content');
      await service.loadFile('/path/to/prompt.md');
      expect(service.content()).toBe('content');
      expect(service.isDirty()).toBe(false);
    });

    it('should handle load error', async () => {
      mockTauriService.readPromptFile.and.rejectWith(new Error('Failed to load'));
      await expectAsync(service.loadFile('/path/to/prompt.md')).toBeRejectedWithError('Failed to load');
    });
  });

  describe('saveFile', () => {
    it('should save file and clear dirty', async () => {
      mockTauriService.savePromptFile.and.resolveTo();
      await service.saveFile('/path/to/prompt.md', 'content');
      expect(service.isDirty()).toBe(false);
    });
  });

  describe('reviewPrompt', () => {
    it('should review prompt and update state', async () => {
      const mockResult: PromptReviewResult = {
        suggestions: ['Add better error handling'],
        improved_prompt: 'Improved version',
      };
      mockTauriService.reviewPromptWithAi.and.resolveTo(mockResult);
      await service.reviewPrompt('prompt content');
      expect(service.reviewResult()).toEqual(mockResult);
      expect(service.reviewStatus()).toBe('succeeded');
    });

    it('should handle review error', async () => {
      mockTauriService.reviewPromptWithAi.and.rejectWith(new Error('Review failed'));
      await service.reviewPrompt('prompt content');
      expect(service.reviewStatus()).toBe('failed');
      expect(service.reviewError()).toBe('Review failed');
    });
  });

  describe('setPath', () => {
    it('should set path', async () => {
      service.setPath('/path/to/prompt.md');
      expect(service.path()).toBe('/path/to/prompt.md');
    });
  });

  describe('setContent', () => {
    it('should set content', () => {
      service.setContent('new content');
      expect(service.content()).toBe('new content');
      expect(service.isDirty()).toBe(true);
    });
  });

  describe('revert', () => {
    it('should revert content and clear dirty', () => {
      service.setContent('modified content');
      service.revert();
      expect(service.content()).toBe('');
      expect(service.isDirty()).toBe(false);
    });
  });
});

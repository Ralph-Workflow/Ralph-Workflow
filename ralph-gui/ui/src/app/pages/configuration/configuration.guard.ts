import { inject } from '@angular/core';
import { ConfigService } from '../../services/config.service';

/**
 * Functional CanDeactivate guard for the Configuration page.
 *
 * Prevents accidental navigation away when there are unsaved changes.
 * Returns false (and shows a browser confirm dialog) when the config is dirty.
 * Returns true when there are no pending changes.
 */
export function configurationCanDeactivateGuard(): boolean {
  const configService = inject(ConfigService);

  if (configService.isDirty()) {
    return window.confirm(
      'You have unsaved configuration changes. Are you sure you want to leave? Your changes will be lost.',
    );
  }

  return true;
}

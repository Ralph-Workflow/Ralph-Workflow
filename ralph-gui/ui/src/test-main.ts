/**
 * Custom test entry point for Angular Karma tests.
 *
 * This file is referenced from angular.json's test.options.main.
 * It replaces the auto-generated virtual main from the Angular CLI Karma builder,
 * providing a place to install global test overrides that run BEFORE spec files.
 *
 * Note: zone.js and zone.js/testing are loaded via angular.json's test.options.polyfills
 * array and are already available by the time this module runs.
 */

// Install console.warn suppression BEFORE any spec files load.
// This suppresses known, intentional Angular diagnostics:
//
// NG0914: Fires when provideZonelessChangeDetection() is used alongside Zone.js.
//   Zone.js must be loaded for fakeAsync/tick test utilities in Karma.
//   This combination is intentional for Angular 21 zoneless apps with Karma.
//
// allowSignalWrites: Deprecated option used in some effect() calls in legacy spec files.
//   The option is harmless and has no behavioral impact in Angular 21.
(function suppressTestWarnings() {
  const originalWarn = console.warn;
  console.warn = function (...args: unknown[]) {
    const first = args[0];
    if (
      typeof first === 'string' &&
      (first.includes('NG0914') || first.includes("'allowSignalWrites' flag is deprecated"))
    ) {
      return;
    }
    return originalWarn.apply(console, args as Parameters<typeof originalWarn>);
  };
})();

import { getTestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from '@angular/platform-browser-dynamic/testing';

// Initialize the Angular testing environment.
// Zone.js is already loaded via polyfills at this point.
getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting(),
  {
    errorOnUnknownElements: true,
    errorOnUnknownProperties: true,
  }
);

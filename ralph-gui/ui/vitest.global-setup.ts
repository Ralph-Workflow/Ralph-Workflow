import 'zone.js';
import 'zone.js/testing';
import { TestBed } from '@angular/core/testing';
import { BrowserDynamicTestingModule, platformBrowserDynamicTesting } from '@angular/platform-browser-dynamic/testing';

export default function setup() {
  TestBed.initTestEnvironment(BrowserDynamicTestingModule, platformBrowserDynamicTesting());
}

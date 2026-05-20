# Apollo Status

- Timestamp: `2026-05-20T19:35:15.526583+02:00`
- Status: `script_failure`
- Final URL: `https://app.apollo.io/#/login`
- Login attempted: `False`
- Cloudflare/auth blocked: `False`
- Auth endpoint status codes: `[]`
- Notes: BrowserType.launch_persistent_context: Target page, context or browser has been closed
Browser logs:

╔════════════════════════════════════════════════════════════════════════════════════════════════╗
║ Looks like you launched a headed browser without having a XServer running.                     ║
║ Set either 'headless: true' or use 'xvfb-run <your-playwright-app>' before running Playwright. ║
║                                                                                                ║
║ <3 Playwright Team                                                                             ║
╚════════════════════════════════════════════════════════════════════════════════════════════════╝
Call log:
  - <launching> /usr/bin/chromium --disable-field-trial-config --disable-background-networking --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-back-forward-cache --disable-breakpad --disable-client-side-phishing-detection --disable-component-extensions-with-background-pages --disable-component-update --no-default-browser-check --disable-default-apps --disable-dev-shm-usage --disable-extensions --disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,BoundaryEventDispatchTracksNodeRemoval,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,Translate,AutoDeElevate,RenderDocument,OptimizationHints --enable-features=CDPScreenshotNewSurface --allow-pre-commit-input --disable-hang-monitor --disable-ipc-flooding-protection --disable-popup-blocking --disable-prompt-on-repost --disable-renderer-backgrounding --force-color-profile=srgb --metrics-recording-only --no-first-run --password-store=basic --use-mock-keychain --no-service-autorun --export-tagged-pdf --disable-search-engine-choice-screen --unsafely-disable-devtools-self-xss-warnings --edge-skip-compat-layer-relaunch --enable-automation --disable-infobars --disable-search-engine-choice-screen --disable-sync --enable-unsafe-swiftshader --no-sandbox --disable-blink-features=AutomationControlled --no-first-run --user-data-dir=/home/mistlight/.openclaw/workspace/.apollo-playwright --remote-debugging-pipe about:blank
  - <launched> pid=80022
  - [pid=80022][err] [80022:80022:0520/193515.507031:ERROR:ui/ozone/platform/x11/ozone_platform_x11.cc:257] Missing X server or $DISPLAY
  - [pid=80022][err] [80022:80022:0520/193515.507057:ERROR:ui/aura/env.cc:246] The platform failed to initialize.  Exiting.
  - [pid=80022] <gracefully close start>
  - [pid=80022] <kill>
  - [pid=80022] <will force kill>
  - [pid=80022] <process did exit: exitCode=1, signal=null>
  - [pid=80022] starting temporary directories cleanup
  - [pid=80022] finished temporary directories cleanup
  - [pid=80022] <gracefully close end>


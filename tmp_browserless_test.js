const { chromium } = require('playwright-core');
(async()=>{
  const token = process.env.BROWSERLESS_TOKEN;
  const endpoints = [
    `wss://production-sfo.browserless.io?token=${token}`,
    `wss://chrome.browserless.io?token=${token}`,
    `wss://production-sfo.browserless.io/playwright?token=${token}`,
    `wss://chrome.browserless.io/playwright?token=${token}`,
  ];
  for (const ep of endpoints) {
    try {
      console.log('trying', ep.replace(token,'TOKEN'));
      const browser = await chromium.connectOverCDP(ep, { timeout: 30000 });
      const context = browser.contexts()[0] || await browser.newContext();
      const page = await context.newPage();
      await page.goto('https://example.com', { waitUntil: 'domcontentloaded', timeout: 30000 });
      console.log('title', await page.title());
      await browser.close();
      return;
    } catch (e) {
      console.log('fail', e.message);
    }
  }
})();

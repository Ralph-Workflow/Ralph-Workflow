const { chromium } = require('playwright-core');
(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({ viewport: { width: 1440, height: 2200 } });
  await page.goto('https://www.toolhunter.cc/submit', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('iframe[src*="tally.so"]', { timeout: 60000 });
  const handle = await page.$('iframe[src*="tally.so"]');
  const frame = await handle.contentFrame();
  await frame.waitForLoadState('domcontentloaded');
  const html = await frame.locator('body').innerHTML();
  console.log(html.slice(0,20000));
  await browser.close();
})();

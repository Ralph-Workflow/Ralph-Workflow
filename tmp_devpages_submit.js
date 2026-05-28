const { chromium } = require('playwright');

(async() => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({ viewport: { width: 1440, height: 1600 } });
  const events = [];
  page.on('response', async (resp) => {
    const url = resp.url();
    if (url.includes('devpages.io') || url.includes('supabase') || url.includes('/api/')) {
      events.push({url, status: resp.status(), method: resp.request().method()});
    }
  });
  await page.goto('https://devpages.io/submit-a-tool', { waitUntil: 'networkidle', timeout: 60000 });
  await page.fill('#name', 'Ralph Workflow');
  await page.fill('#url', 'https://ralphworkflow.com');
  await page.fill('#description', 'Ralph Workflow is a free and open-source CLI for developers and technical teams who want to orchestrate the coding agents they already use on their own machine. It is built for coding work that is too big to babysit and too risky to trust blindly, and it hands back a reviewable diff, checks, artifacts, and finish notes after an unattended run. Use it now to hand off one real backlog task overnight and wake up to output you can actually review and decide whether to merge.');
  await page.selectOption('#category', 'generative-ai');
  await page.selectOption('#pricing', 'open-source');
  await page.fill('#github', 'https://github.com/Ralph-Workflow/Ralph-Workflow');
  await page.fill('#email', 'bot@hireaegis.com');
  await page.screenshot({ path: 'devpages-before-submit.png', fullPage: true });
  const button = page.getByRole('button');
  await button.last().click();
  await page.waitForTimeout(6000);
  const bodyText = await page.locator('body').innerText();
  console.log('BODY_START');
  console.log(bodyText.slice(0, 4000));
  console.log('BODY_END');
  console.log('EVENTS', JSON.stringify(events, null, 2));
  await page.screenshot({ path: 'devpages-after-submit.png', fullPage: true });
  await browser.close();
})();

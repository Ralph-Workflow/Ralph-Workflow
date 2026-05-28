const { chromium } = require('playwright-core');
(async()=>{
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({ viewport: { width: 1440, height: 2200 } });
  await page.goto('https://www.toolhunter.cc/submit', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(3000);
  console.log('title=', await page.title());
  const frame = page.frame({ url: /tally\.so\/embed|tally\.so\/r\// });
  console.log('frame?', !!frame);
  if (!frame) throw new Error('No tally frame found');
  await frame.waitForLoadState('domcontentloaded');
  const labels = await frame.locator('h1,h2,h3,label,button,input,textarea').evaluateAll(nodes => nodes.map(n => ({tag:n.tagName,text:(n.innerText||n.value||n.getAttribute('placeholder')||n.getAttribute('aria-label')||'').trim()})).filter(x=>x.text).slice(0,80));
  console.log(JSON.stringify(labels, null, 2));
  await page.screenshot({ path: '/tmp/toolhunter_submit_probe.png', fullPage: true });
  await browser.close();
})();

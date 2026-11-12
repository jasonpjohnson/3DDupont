// usage: node shot.js <url> <out.png> [view] [w] [h] [mode] [layersJSON] [minutes]
const { chromium } = require('playwright');
(async () => {
  const [url, out, view = 'front', w = '1440', h = '900', mode = 'photo', layers = '{}', minutes = ''] = process.argv.slice(2);
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
  const logs = [];
  page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) logs.push(`[${m.type()}] ${m.text()}`); });
  page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`));
  page.on('requestfailed', (r) => logs.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));
  await page.goto(url, { waitUntil: 'load' });
  try {
    await page.waitForFunction(() => window.__viewer && document.getElementById('loading').classList.contains('done'), null, { timeout: 90000 });
  } catch (e) { logs.push('timeout waiting for viewer: ' + (await page.locator('#loading-msg').textContent())); }
  await page.evaluate(({ view, mode, layers, minutes }) => {
    const v = window.__viewer; if (!v) return;
    const vv = v.M.views.find((x) => x.id === view) || v.M.views[0];
    v.goToView(vv, false);
    v.applyMode(mode);
    for (const [k, on] of Object.entries(JSON.parse(layers))) v.setLayer(k, on);
    if (minutes) { v.state.minutes = +minutes; v.setSun(); }
  }, { view, mode, layers, minutes });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: out });
  console.log(JSON.stringify({ out, logs: logs.slice(0, 20) }));
  await browser.close();
})();

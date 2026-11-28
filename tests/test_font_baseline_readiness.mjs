import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { chromium } from 'playwright';
import { waitForFontBaselineReadiness } from '../scripts/font-baseline-readiness.mjs';

test('font baseline waits for deferred application startup before measuring the inline notice', async () => {
  let releaseApplication;
  const applicationGate = new Promise(resolve => { releaseApplication = resolve; });
  const server = createServer(async (request, response) => {
    if (request.url === '/app.js') {
      await applicationGate;
      response.writeHead(200, { 'content-type': 'application/javascript' });
      response.end(`document.addEventListener('DOMContentLoaded', () => {
        document.body.dataset.initialized = 'true';
      });`);
      return;
    }
    response.writeHead(200, { 'content-type': 'text/html' });
    response.end(`<!doctype html><html><head><script defer src="/app.js"></script>
      </head><body><aside class="capability-transition">
      <a href="/whats-next/">Read what is changing</a></aside></body></html>`);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 349, height: 780 } });
    await page.goto(`http://127.0.0.1:${server.address().port}`, { waitUntil: 'commit' });
    const noticeLink = page.getByRole('link', { name: 'Read what is changing' });
    await noticeLink.waitFor({ state: 'visible' });
    let completed = false;
    const baseline = waitForFontBaselineReadiness(page, 2000).then(evidence => {
      completed = true;
      return evidence;
    });
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(completed, false, 'an unfinished application cannot establish a font baseline');
    releaseApplication();
    const evidence = await baseline;
    assert.notEqual(evidence.document_ready_state, 'loading');
    assert.equal(await page.locator('body').getAttribute('data-initialized'), 'true');
    assert.equal(await noticeLink.isVisible(), true);
  } finally {
    releaseApplication();
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { chromium } from 'playwright';
import { waitForFontBaselineReadiness } from '../scripts/font-baseline-readiness.mjs';

test('font baseline waits for deferred application startup to reveal its control', async () => {
  let releaseApplication;
  const applicationGate = new Promise(resolve => { releaseApplication = resolve; });
  const server = createServer(async (request, response) => {
    if (request.url === '/app.js') {
      await applicationGate;
      response.writeHead(200, { 'content-type': 'application/javascript' });
      response.end(`document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('[data-transition-open]').hidden = false;
      });`);
      return;
    }
    response.writeHead(200, { 'content-type': 'text/html' });
    response.end(`<!doctype html><html><head><script defer src="/app.js"></script>
      </head><body><aside class="capability-transition">
      <button data-transition-open hidden>Read Jamie's note</button></aside></body></html>`);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 349, height: 780 } });
    await page.goto(`http://127.0.0.1:${server.address().port}`, { waitUntil: 'commit' });
    await page.locator('[data-transition-open]').waitFor({ state: 'attached' });
    assert.equal(await page.locator('[data-transition-open]').isVisible(), false);
    let completed = false;
    const baseline = waitForFontBaselineReadiness(page, 2000).then(evidence => {
      completed = true;
      return evidence;
    });
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(completed, false, 'an unfinished application cannot establish a font baseline');
    releaseApplication();
    const evidence = await baseline;
    assert.equal(evidence.transition_control_revealed, true);
    assert.notEqual(evidence.document_ready_state, 'loading');
    assert.equal(await page.locator('[data-transition-open]').isVisible(), true);
  } finally {
    releaseApplication();
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

test('font baseline rejects a control that application startup never reveals', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<aside class="capability-transition"><button data-transition-open hidden>Note</button></aside>');
    await assert.rejects(waitForFontBaselineReadiness(page, 150), /Timeout/);
    assert.equal(await page.locator('[data-transition-open]').isVisible(), false);
  } finally {
    await browser.close();
  }
});

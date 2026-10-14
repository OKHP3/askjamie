import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { after, before, test } from 'node:test';
import { runDeployedHeroCheck } from '../scripts/check-deployed-hero-styles.mjs';

const cssPath = '/assets/css/critical-hero.css';
let server;
let origin;
let failureMode = false;

before(async () => {
  server = createServer((request, response) => {
    const path = new URL(request.url, 'http://127.0.0.1').pathname;
    if (path === '/network-drop.png' && failureMode) {
      response.destroy();
      return;
    }
    if (path === '/network-drop.png') {
      response.writeHead(204).end();
      return;
    }
    if (path === '/favicon.ico') {
      response.writeHead(204).end();
      return;
    }
    if (path === cssPath) {
      if (failureMode && request.headers.referer?.includes('/lens-system/okhp3-brandguard/')) {
        response.writeHead(503, { 'content-type': 'text/css' }).end('/* unavailable */');
        return;
      }
      response.writeHead(200, { 'content-type': 'text/css' }).end(`
        .askjamie-brandguard-page .askjamie-hero-copy h1 {
          font-size: 2rem; letter-spacing: -0.03em;
        }
        .askjamie-hero--universe .mermaid-scroll-wrap {
          min-height: 18rem; overflow: hidden;
        }
      `);
      return;
    }
    const isBrandGuard = path === '/lens-system/okhp3-brandguard/';
    if (isBrandGuard || path === '/universe/') {
      response.writeHead(200, { 'content-type': 'text/html' }).end(`
        <!doctype html><html><head><link rel="stylesheet" href="${cssPath}"></head>
        <body class="${isBrandGuard ? 'askjamie-brandguard-page' : ''}">
        ${isBrandGuard
          ? '<main><div class="askjamie-hero-copy"><h1>BrandGuard</h1></div></main>'
          : '<main class="askjamie-hero--universe"><div class="mermaid-scroll-wrap"></div></main>'}
        ${isBrandGuard ? '<img src="/network-drop.png" alt="">' : ''}
        ${failureMode && isBrandGuard ? '<script>console.error("brandguard fixture error")</script>' : ''}
        </body></html>
      `);
      return;
    }
    response.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  origin = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  server.close();
  await once(server, 'close');
});

test('passes when both production routes return critical CSS and expected computed styles', async () => {
  failureMode = false;
  const report = await runDeployedHeroCheck({ base: origin });
  assert.equal(report.outcome, 'PASS', JSON.stringify(report, null, 2));
  assert.deepEqual(report.routes.map((route) => route.path), [
    '/lens-system/okhp3-brandguard/',
    '/universe/',
  ]);
  for (const route of report.routes) {
    assert.equal(route.stylesheet_response.status, 200);
    assert.equal(route.passed, true, route.errors.join('\n'));
    assert.deepEqual(route.console_errors, []);
    assert.deepEqual(route.local_request_failures, []);
    assert.deepEqual(route.local_bad_responses, []);
  }
  assert.equal(report.routes[0].computed.font_size, '32px');
  assert.equal(report.routes[0].computed.letter_spacing, '-0.96px');
  assert.equal(report.routes[1].computed.min_height, '288px');
  assert.equal(report.routes[1].computed.overflow, 'hidden');
});

test('retains route-specific console and local stylesheet failures and fails the affected route', async () => {
  failureMode = true;
  const report = await runDeployedHeroCheck({ base: origin });
  const [brandguard, universe] = report.routes;
  assert.equal(report.outcome, 'FAIL');
  assert.equal(brandguard.passed, false);
  assert.equal(brandguard.stylesheet_response.status, 503);
  assert.ok(brandguard.console_errors.some((message) => message.includes('brandguard fixture error')));
  assert.ok(brandguard.local_bad_responses.some((response) => response.status === 503));
  assert.ok(brandguard.local_request_failures.some((failure) =>
    failure.url.endsWith('/network-drop.png')));
  assert.ok(brandguard.errors.some((error) => error.includes('HTTP 503')));
  assert.equal(universe.passed, true, universe.errors.join('\n'));
  assert.deepEqual(universe.console_errors, []);
  assert.deepEqual(universe.local_bad_responses, []);
  assert.deepEqual(universe.local_request_failures, []);
});

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import {
  runDeployedHeroCheck,
  writeReport,
} from '../scripts/check-deployed-hero-styles.mjs';

const cssPath = '/assets/css/critical-hero.css';
let server;
let origin;
let failureMode = false;
let widthRegressionMode = false;
let outdatedStylesheetMode = false;

function fixtureStylesheet() {
  return `
        .askjamie-brandguard-page .askjamie-hero-copy h1 {
          font-size: clamp(2.2rem, 4vw, 2.8rem); letter-spacing: normal;
        }
        @media (max-width: 899px) {
          .askjamie-brandguard-page .askjamie-hero-copy h1 {
            font-size: 2rem; letter-spacing: -0.02em;
          }
        }
        ${widthRegressionMode
          ? '@media (max-width: 360px) { .askjamie-brandguard-page .askjamie-hero-copy h1 { font-size: 1.75rem; } }'
          : ''}
        @media (min-width: 381px) and (max-width: 400px) {
          .askjamie-brandguard-page .askjamie-hero-copy h1 { letter-spacing: -0.03em; }
        }
        .askjamie-hero--universe .mermaid-scroll-wrap {
          min-height: 18rem; overflow: hidden;
        }
        @media (min-width: 641px) {
          .askjamie-hero--universe .mermaid-scroll-wrap { min-height: 22rem; }
        }
      `;
}

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
      const css = fixtureStylesheet() +
        (outdatedStylesheetMode ? '\n/* outdated release: style-equivalent bytes */\n' : '');
      response.writeHead(200, { 'content-type': 'text/css' }).end(css);
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
  widthRegressionMode = false;
  outdatedStylesheetMode = false;
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet: Buffer.from(fixtureStylesheet()),
  });
  assert.equal(report.outcome, 'PASS', JSON.stringify(report, null, 2));
  assert.match(report.chromium_version, /^\d+(?:\.\d+){2,}$/);
  const successfulOutputDir = mkdtempSync(join(tmpdir(), 'deployed-hero-success-'));
  try {
    writeReport(report, successfulOutputDir);
    const serializedReport = JSON.parse(
      readFileSync(join(successfulOutputDir, 'report.json'), 'utf8'),
    );
    const markdown = readFileSync(join(successfulOutputDir, 'report.md'), 'utf8');
    assert.equal(serializedReport.chromium_version, report.chromium_version);
    assert.ok(markdown.includes(`- Chromium version: ${report.chromium_version}`));
  } finally {
    rmSync(successfulOutputDir, { recursive: true, force: true });
  }
  assert.deepEqual(report.routes.map((route) => route.path), [
    '/lens-system/okhp3-brandguard/',
    '/universe/',
  ]);
  assert.deepEqual(report.viewports.map(({ name, width }) => [name, width]), [
    ['narrow-phone', 320],
    ['standard-phone', 390],
    ['tablet', 768],
    ['desktop', 1440],
  ]);
  for (const route of report.routes) {
    assert.equal(route.passed, true, route.errors.join('\n'));
    assert.equal(route.measurements.length, 4);
    for (const measurement of route.measurements) {
      assert.equal(measurement.stylesheet_response.status, 200);
      assert.equal(measurement.stylesheet_integrity.matches, true);
      assert.equal(measurement.passed, true, measurement.errors.join('\n'));
      assert.deepEqual(measurement.console_errors, []);
      assert.deepEqual(measurement.local_request_failures, []);
      assert.deepEqual(measurement.local_bad_responses, []);
    }
  }
  assert.deepEqual(report.routes[0].measurements.map(({ computed }) => [
    computed.font_size, computed.letter_spacing,
  ]), [
    ['32px', '-0.64px'],
    ['32px', '-0.96px'],
    ['32px', '-0.64px'],
    ['44.8px', 'normal'],
  ]);
  assert.deepEqual(report.routes[1].measurements.map(({ computed }) => [
    computed.min_height, computed.overflow,
  ]), [
    ['288px', 'hidden'],
    ['288px', 'hidden'],
    ['352px', 'hidden'],
    ['352px', 'hidden'],
  ]);
});

test('retains route- and viewport-labeled not-measured evidence when the browser cannot start', async () => {
  const launchError = new Error('fixture browser launch failure');
  const expectedStylesheet = Buffer.from(fixtureStylesheet());
  const expectedSha256 = createHash('sha256').update(expectedStylesheet).digest('hex');
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet,
    launch: async () => {
      throw launchError;
    },
  });
  const outputDir = mkdtempSync(join(tmpdir(), 'deployed-hero-startup-failure-'));
  let serializedReport;
  let markdown;
  try {
    writeReport(report, outputDir);
    serializedReport = JSON.parse(readFileSync(join(outputDir, 'report.json'), 'utf8'));
    markdown = readFileSync(join(outputDir, 'report.md'), 'utf8');
  } finally {
    rmSync(outputDir, { recursive: true, force: true });
  }

  assert.equal(serializedReport.outcome, 'FAIL');
  assert.equal(serializedReport.chromium_version, null);
  assert.equal(serializedReport.fatal_error, launchError.stack);
  assert.equal(serializedReport.expected_stylesheet.expected_bytes, expectedStylesheet.length);
  assert.equal(serializedReport.expected_stylesheet.expected_sha256, expectedSha256);
  assert.equal(serializedReport.routes.length, 2);
  assert.match(markdown, new RegExp(
    `- Validated stylesheet: .*"expected_bytes":${expectedStylesheet.length}` +
    `.*"expected_sha256":"${expectedSha256}"`,
  ));
  assert.match(markdown, /- Chromium version: not available/);
  for (const route of serializedReport.routes) {
    assert.equal(route.status, 'NOT_MEASURED');
    assert.equal(route.passed, false);
    assert.equal(route.measurements.length, 4);
    for (const measurement of route.measurements) {
      assert.equal(measurement.status, 'NOT_MEASURED');
      assert.equal(measurement.measured, false);
      assert.deepEqual(measurement.errors, [
        'Browser could not start: fixture browser launch failure',
      ]);
      assert.match(markdown, new RegExp(
        `## ${route.path.replaceAll('/', '\\/')}(?:\\n|$)`,
      ));
      assert.ok(markdown.includes(
        `### ${measurement.viewport.name}: ${measurement.viewport.width} × ` +
        `${measurement.viewport.height}: NOT_MEASURED`,
      ));
    }
  }
  assert.match(markdown, /## Fatal check error/);
  assert.match(markdown, /Browser could not start: fixture browser launch failure/);
  assert.match(markdown, /- Measured: false/);
});

test('retains completed measurements and reports a browser crash mid-check', async () => {
  failureMode = false;
  widthRegressionMode = false;
  outdatedStylesheetMode = false;
  const browserError = new Error('fixture browser crash after first measurement');
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet: Buffer.from(fixtureStylesheet()),
    launch: async (options) => {
      const browser = await chromium.launch(options);
      let pageCount = 0;
      return {
        version: () => browser.version(),
        newPage: async (...args) => {
          pageCount += 1;
          if (pageCount === 2) throw browserError;
          return browser.newPage(...args);
        },
        close: () => browser.close(),
      };
    },
  });
  const outputDir = mkdtempSync(join(tmpdir(), 'deployed-hero-mid-check-crash-'));
  let serializedReport;
  let markdown;
  try {
    writeReport(report, outputDir);
    serializedReport = JSON.parse(readFileSync(join(outputDir, 'report.json'), 'utf8'));
    markdown = readFileSync(join(outputDir, 'report.md'), 'utf8');
  } finally {
    rmSync(outputDir, { recursive: true, force: true });
  }

  assert.equal(serializedReport.outcome, 'FAIL');
  assert.match(serializedReport.chromium_version, /^\d+(?:\.\d+){2,}$/);
  assert.equal(serializedReport.fatal_error, browserError.stack);
  assert.match(markdown, /fixture browser crash after first measurement/);
  assert.equal(serializedReport.routes.length, 2);
  const [brandguard, universe] = serializedReport.routes;
  assert.equal(brandguard.measurements.length, 4);
  assert.equal(brandguard.measurements[0].viewport.name, 'narrow-phone');
  assert.equal(brandguard.measurements[0].computed.present, true);
  assert.equal(brandguard.measurements[0].stylesheet_integrity.matches, true);
  assert.equal(brandguard.measurements[0].passed, true);
  for (const measurement of brandguard.measurements.slice(1)) {
    assert.equal(measurement.status, 'NOT_MEASURED');
    assert.equal(measurement.measured, false);
  }
  for (const measurement of universe.measurements) {
    assert.equal(measurement.status, 'NOT_MEASURED');
    assert.equal(measurement.measured, false);
  }
  assert.match(markdown, /### narrow-phone: 320 × 844: PASS/);
  assert.match(markdown, /### standard-phone: 390 × 844: NOT_MEASURED/);
});

test('fails only the narrow-phone measurement when its width-specific hero style regresses', async () => {
  failureMode = false;
  widthRegressionMode = true;
  outdatedStylesheetMode = false;
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet: Buffer.from(fixtureStylesheet()),
  });
  const [brandguard, universe] = report.routes;

  assert.equal(report.outcome, 'FAIL');
  assert.equal(brandguard.passed, false);
  assert.deepEqual(brandguard.measurements.map(({ viewport, passed }) =>
    [viewport.name, passed]), [
    ['narrow-phone', false],
    ['standard-phone', true],
    ['tablet', true],
    ['desktop', true],
  ]);
  assert.equal(brandguard.measurements[0].computed.font_size, '28px');
  assert.ok(brandguard.measurements[0].errors.some((error) =>
    error.includes('Computed BrandGuard hero style mismatch')));
  assert.deepEqual(brandguard.measurements.slice(1).map(({ errors }) => errors), [[], [], []]);
  assert.equal(universe.passed, true, universe.errors.join('\n'));
});

test('retains route-specific console and local stylesheet failures and fails the affected route', async () => {
  failureMode = true;
  widthRegressionMode = false;
  outdatedStylesheetMode = false;
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet: Buffer.from(fixtureStylesheet()),
  });
  const [brandguard, universe] = report.routes;
  assert.equal(report.outcome, 'FAIL');
  assert.equal(brandguard.passed, false);
  assert.equal(brandguard.measurements.length, 4);
  for (const measurement of brandguard.measurements) {
    assert.equal(measurement.stylesheet_response.status, 503);
    assert.ok(measurement.console_errors.some((message) =>
      message.includes('brandguard fixture error')));
    assert.ok(measurement.local_bad_responses.some((response) => response.status === 503));
    assert.ok(measurement.local_request_failures.some((failure) =>
      failure.url.endsWith('/network-drop.png')));
  }
  assert.ok(brandguard.errors.some((error) => error.includes('HTTP 503')));
  assert.equal(universe.passed, true, universe.errors.join('\n'));
  for (const measurement of universe.measurements) {
    assert.deepEqual(measurement.console_errors, []);
    assert.deepEqual(measurement.local_bad_responses, []);
    assert.deepEqual(measurement.local_request_failures, []);
  }
});

test('fails an outdated stylesheet served with HTTP 200 even when computed hero styles still pass', async () => {
  failureMode = false;
  widthRegressionMode = false;
  outdatedStylesheetMode = true;
  const report = await runDeployedHeroCheck({
    base: origin,
    expectedStylesheet: Buffer.from(fixtureStylesheet()),
  });
  const measurements = report.routes.flatMap((route) => route.measurements);

  assert.equal(report.outcome, 'FAIL');
  assert.ok(measurements.length > 0);
  for (const measurement of measurements) {
    assert.equal(measurement.stylesheet_response.status, 200);
    assert.match(measurement.stylesheet_response.content_type, /text\/css/i);
    assert.equal(measurement.stylesheet_integrity.matches, false);
    assert.ok(measurement.errors.some((error) => error.includes('byte/hash mismatch')));
    assert.ok(!measurement.errors.some((error) => error.includes('Computed')));
  }
  assert.ok(report.routes.every((route) => !route.passed));
});

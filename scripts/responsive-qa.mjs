#!/usr/bin/env node
/**
 * AskJamie™ responsive QA script.
 *
 * MODE A — Playwright:
 *   Visits each public page at 8 viewport widths and checks:
 *   - No horizontal overflow (scrollWidth > innerWidth)
 *   - No JS console errors
 *   - All images loaded (no broken img src)
 *   - CSS and JS assets load (no 404 on critical resources)
 *
 * MODE B — Static lint (`--static` only):
 *   Runs 10 structural checks per page per viewport (same pass/fail schema).
 *   Checks that are viewport-agnostic (viewport meta, h1, alt, etc.) are
 *   run once per page and applied to all 8 viewport rows — clearly flagged
 *   as `static-lint` so results are not confused with live browser checks.
 *
 * Usage:
 *   node scripts/responsive-qa.mjs [--base=http://localhost:5000]
 *
 * Third-party resources are deliberately blocked in browser mode so the
 * result measures the local site, not CDN availability. Navigation therefore
 * waits for the local document to commit, then gives DOMContentLoaded a short
 * bounded window. Blocked resources are retained as `warnings` in each row,
 * not silently treated as passes.
 *
 * Requires Playwright for MODE A:
 *   npm install -D playwright && npx playwright install chromium
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);
const ROOT       = resolve(__dirname, '..');

const BASE_URL   = process.argv.find(a => a.startsWith('--base='))?.split('=')[1]
                 ?? 'http://localhost:5000';
const FORCE_STATIC = process.argv.includes('--static');

const VIEWPORTS = [
  { name: 'mobile-360',   width: 360,  height: 780  },
  { name: 'mobile-390',   width: 390,  height: 844  },
  { name: 'mobile-430',   width: 430,  height: 932  },
  { name: 'tablet-768',   width: 768,  height: 1024 },
  { name: 'desktop-1024', width: 1024, height: 768  },
  { name: 'desktop-1280', width: 1280, height: 800  },
  { name: 'desktop-1440', width: 1440, height: 900  },
  { name: 'desktop-1920', width: 1920, height: 1080 },
];
// Cap concurrent viewport work at four to limit browser request bursts while
// preserving every viewport row in the release inventory.
const VIEWPORT_CONCURRENCY = 4;

// The sitemap is the release inventory. This avoids silently testing a stale
// hand-maintained list when a public route is added or retired.
function loadPublicPaths() {
  const sitemap = readFileSync(resolve(ROOT, 'sitemap.xml'), 'utf8');
  const locations = [...sitemap.matchAll(/<loc>\s*([^<]+?)\s*<\/loc>/g)]
    .map((match) => match[1]);
  if (locations.length === 0) throw new Error('sitemap.xml has no public routes');
  return [...new Set(locations.map((location) => {
    const url = new URL(location);
    if (url.origin !== 'https://askjamie.bot' || url.search || url.hash) {
      throw new Error(`Invalid sitemap URL for responsive QA: ${location}`);
    }
    return url.pathname || '/';
  }))];
}
const PUBLIC_PATHS = loadPublicPaths();

const RESULTS_DIR    = resolve(ROOT, 'assets/audit/responsive-qa');
const RESULTS_FILE   = resolve(RESULTS_DIR, 'results.json');
const SCREENSHOTS_DIR = resolve(RESULTS_DIR, 'screenshots');

// ── MODE A: Playwright ────────────────────────────────────────────────────────

async function runWithPlaywright() {
  let pw;
  try {
    const require = createRequire(import.meta.url);
    pw = require('playwright');
  } catch {
    return { ok: false, reason: 'Playwright is not installed' };
  }

  mkdirSync(RESULTS_DIR, { recursive: true });
  mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  let browser;
  try {
    browser = await pw.chromium.launch({ headless: true });
  } catch {
    return { ok: false, reason: 'Chromium could not be launched' };
  }

  // Reuse contexts for isolation and speed, but create a fresh page for every
  // route. External resources are blocked so browser QA measures local assets.
  const EXTERNAL_BLOCK = /fonts\.(gstatic|googleapis)\.com|google-analytics\.com|googletagmanager\.com|cdn\.jsdelivr\.net/;

  const workers = await Promise.all(VIEWPORTS.map(async vp => {
    const ctx  = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    return { vp, ctx };
  }));

  const allResults = [];

  async function runViewport(worker, path, url) {
    const { vp, ctx } = worker;
    const page = await ctx.newPage();
    const blockedExternal = new Set();
    const consoleErrors = [];
    const requestFailures = [];
    const failedResponses = [];
    const requestInfo = new WeakMap();
    const requestedUrls = new Set();
    const warnings = [];

    const onConsole = msg => {
      const sourceUrl = msg.location().url || '';
      if (msg.type() === 'error' &&
          !msg.text().includes('ERR_FAILED') &&
          !isMermaidInlineStyleWarning(msg)) {
        consoleErrors.push(sourceUrl ? `${sourceUrl} :: ${msg.text()}` : msg.text());
      }
    };
    const onRequest = req => {
      requestedUrls.add(req.url());
      requestInfo.set(req, {
        requestedUrl: req.url(),
        documentUrl: req.frame()?.url() || '',
        createdAt: Date.now(),
      });
    };
    const onRequestFailed = req => {
      if (blockedExternal.has(req.url())) return;
      const info = requestInfo.get(req);
      requestFailures.push({
        url: req.url(),
        resourceType: req.resourceType(),
        error: req.failure()?.errorText || 'unknown request failure',
        requestedAt: info?.createdAt,
        documentUrl: info?.documentUrl || req.frame()?.url() || '',
        eventUrl: page.url(),
        eventAt: Date.now(),
      });
    };
    const onResponse = resp => {
      if (resp.status() < 400 || blockedExternal.has(resp.url())) return;
      const info = requestInfo.get(resp.request());
      failedResponses.push({
        url: resp.url(),
        resourceType: resp.request().resourceType(),
        status: resp.status(),
        requestedAt: info?.createdAt,
        documentUrl: info?.documentUrl || resp.request().frame()?.url() || '',
        eventUrl: page.url(),
        eventAt: Date.now(),
      });
    };

    await page.route('**/*', route => {
      if (EXTERNAL_BLOCK.test(route.request().url())) {
        blockedExternal.add(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    page.on('console', onConsole);
    page.on('request', onRequest);
    page.on('requestfailed', onRequestFailed);
    page.on('response', onResponse);

    try {
      try {
        await page.goto(url, { waitUntil: 'commit', timeout: 30000 });
        await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {
          warnings.push('DOMContentLoaded not observed within 5s after local document commit');
        });
      } catch (err) {
        return { url, viewport: vp.name, width: vp.width, height: vp.height,
                 mode: 'playwright', pass: false,
                 errors: ['navigation timeout: ' + err.message.split('\n')[0]], warnings };
      }

      // Review the underlying layout after acknowledging the arrival note.
      // Dedicated transition tests cover the open dialog at narrow widths.
      const transitionDismiss = page.locator('[data-transition-dialog][open] [data-transition-dismiss]');
      if (await transitionDismiss.isVisible()) await transitionDismiss.click();
      // Lazy loading is viewport-driven. Scroll each lazy image into view so
      // every runtime observes the same request opportunity before the page
      // is inspected and closed. The wait is only for request start: lazy
      // images remain intentionally excluded from completion/broken checks.
      const lazyImages = page.locator('img[loading="lazy"]');
      const lazyImageUrls = await lazyImages.evaluateAll(images =>
        [...new Set(images.map(image => image.currentSrc || image.src).filter(Boolean))]
      );
      const pendingLazyRequests = lazyImageUrls
        .filter(imageUrl => !requestedUrls.has(imageUrl))
        .map(imageUrl =>
          page.waitForRequest(request => request.url() === imageUrl, { timeout: 5000 })
            .catch(() => null)
        );
      for (let index = 0, count = await lazyImages.count(); index < count; index += 1) {
        await lazyImages.nth(index).scrollIntoViewIfNeeded().catch(() => {});
      }
      await Promise.all(pendingLazyRequests);

      const overflow = await page.evaluate(() =>
        document.documentElement.scrollWidth > window.innerWidth
      );
      await page.waitForFunction(
        () => Array.from(document.querySelectorAll('img'))
          .filter(i => i.loading !== 'lazy')
          .every(i => i.complete),
        undefined,
        { timeout: 5000 }
      ).catch(() => {});

      const brokenImages = await page.evaluate(() =>
        Array.from(document.querySelectorAll('img'))
          .filter(i => i.loading !== 'lazy' && (!i.complete || i.naturalWidth === 0))
          .map(i => i.src)
      );
      const unexpectedBrokenImages = brokenImages.filter(src =>
        ![...blockedExternal].some(blocked => blocked === src)
      );
      const errors = [
        ...(overflow ? [`OVERFLOW: scrollWidth > ${vp.width}px`] : []),
        ...consoleErrors.slice(0, 5).map(e => 'CONSOLE: ' + e),
        ...requestFailures.slice(0, 5).map(r =>
          `REQUEST FAILED: [${r.resourceType}] ${r.url} (${r.error})`
        ),
        ...failedResponses.slice(0, 5).map(r =>
          `HTTP ${r.status}: [${r.resourceType}] ${r.url}`
        ),
        ...unexpectedBrokenImages.slice(0, 5).map(s => 'BROKEN IMG: ' + s),
      ];
      if (blockedExternal.size > 0) {
        warnings.push(`blocked third-party resources: ${[...blockedExternal].join(', ')}`);
      }

      const row = { url, viewport: vp.name, width: vp.width, height: vp.height,
                    mode: 'playwright', pass: errors.length === 0, errors, warnings };
      if (!row.pass) {
        const ssFile = `${path.replace(/\//g, '_')}_${vp.name}.png`;
        await page.screenshot({ path: resolve(SCREENSHOTS_DIR, ssFile) });
      }
      return row;
    } finally {
      page.removeListener('console', onConsole);
      page.removeListener('request', onRequest);
      page.removeListener('requestfailed', onRequestFailed);
      page.removeListener('response', onResponse);
      await page.close();
    }
  }

  for (const path of PUBLIC_PATHS) {
    const url = BASE_URL + path;

    const vpResults = [];
    for (let start = 0; start < workers.length; start += VIEWPORT_CONCURRENCY) {
      const batch = await Promise.all(workers
        .slice(start, start + VIEWPORT_CONCURRENCY)
        .map(worker => runViewport(worker, path, url)));
      vpResults.push(...batch);
    }

    const fails = vpResults.filter(r => !r.pass);
    if (fails.length > 0) {
      fails.forEach(r => {
        console.log(`  FAIL  ${r.viewport.padEnd(14)} ${path}`);
        r.errors.forEach(e => console.log(`         → ${e}`));
      });
    }
    process.stdout.write(`  done  ${path}\n`);

    for (const row of vpResults) {
      allResults.push(row);
    }
  }

  for (const { ctx } of workers) await ctx.close();
  await browser.close();

  const report = {
    generated: new Date().toISOString(),
    mode: 'playwright',
    base_url: BASE_URL,
    pages_checked: PUBLIC_PATHS.length,
    viewports_checked: VIEWPORTS.length,
    total_checks: allResults.length,
    passing_checks: allResults.filter(r => r.pass).length,
    failing_checks: allResults.filter(r => !r.pass).length,
    results: allResults,
  };
  writeFileSync(RESULTS_FILE, JSON.stringify(report, null, 2));

  const totalFails = allResults.filter(r => !r.pass).length;
  console.log(`\nTotal: ${allResults.length} checks — ${totalFails} failures`);
  console.log(`Results: ${RESULTS_FILE}`);
  if (totalFails > 0) process.exit(1);
  return report;
}

// ── MODE B: Static lint ───────────────────────────────────────────────────────
//
// Runs 10 structural checks per page and emits one row per (page × viewport).
// Checks are viewport-agnostic (HTML structure doesn't change by width), so
// identical pass/fail data is recorded for each viewport row. The `mode` field
// is always "static-lint" so results are never confused with browser execution.

function staticLintPage(path, html) {
  const errors = [];

  // 1. viewport meta
  if (!html.includes('name="viewport"'))
    errors.push('LINT: missing viewport meta');

  // 2. construction overlay absent
  if (html.includes('construction-overlay'))
    errors.push('LINT: construction-overlay present (blocking modal)');

  // 3. single h1
  const h1Count = (html.match(/<h1[\s>]/gi) || []).length;
  if (h1Count !== 1)
    errors.push(`LINT: ${h1Count} <h1> elements (expected 1)`);

  // 4. all imgs have alt
  const imgsNoAlt = (html.match(/<img(?![^>]*\balt=)[^>]*>/gi) || []).length;
  if (imgsNoAlt > 0)
    errors.push(`LINT: ${imgsNoAlt} <img> missing alt`);

  // 5. all imgs have width (CLS / layout-shift risk)
  const imgsNoWidth = (html.match(/<img(?![^>]*\bwidth=)[^>]*>/gi) || []).length;
  if (imgsNoWidth > 0)
    errors.push(`LINT: ${imgsNoWidth} <img> missing width (layout-shift risk)`);

  // 6. footer /search/ link (skip search page and legal page)
  if (path !== '/search/' && path !== '/legal/') {
    const footerStart = html.indexOf('<footer');
    const footerHtml  = footerStart >= 0 ? html.slice(footerStart) : '';
    if (!footerHtml.includes('href="/search/"'))
      errors.push('LINT: /search/ link missing from footer nav');
  }

  // 7. copyright year fallback
  if (html.includes('id="current-year-askjamie"') &&
      !html.includes('current-year-askjamie">2026'))
    errors.push('LINT: year span missing 2026 static fallback');

  // 8. /search/ must not appear in primary nav submenu
  const navStart = html.indexOf('<nav class="primary-nav"');
  const navEnd   = navStart >= 0 ? html.indexOf('</nav>', navStart) : -1;
  if (navStart >= 0 && navEnd >= 0 && html.slice(navStart, navEnd).includes('/search/'))
    errors.push('LINT: /search/ found inside primary-nav (should be footer only)');

  // 9. skip link present
  if (!html.includes('class="skip-link"'))
    errors.push('LINT: missing skip link');

  // 10. app.js present
  if (!html.includes('/assets/js/app.js'))
    errors.push('LINT: app.js script tag missing');

  return errors;
}

async function staticAnalysis() {
  console.log('Static-lint requested with --static.\n');
  console.log('NOTE: Static lint checks HTML structure only. It cannot detect');
  console.log('      horizontal overflow, JS console errors, or broken images');
  console.log('      at runtime. Run with Playwright for full browser coverage.\n');

  mkdirSync(RESULTS_DIR, { recursive: true });

  const results   = [];
  let totalFails  = 0;
  let pagesFound  = 0;

  for (const path of PUBLIC_PATHS) {
    const fsPath = path.endsWith('.html')
      ? resolve(ROOT, path.replace(/^\//, ''))
      : resolve(ROOT, path.replace(/^\//, ''), 'index.html');
    if (!existsSync(fsPath)) {
      console.log(`  SKIP  ${path} — file not found`);
      continue;
    }

    const html   = readFileSync(fsPath, 'utf-8');
    const errors = staticLintPage(path, html);
    const pass   = errors.length === 0;
    pagesFound++;

    for (const vp of VIEWPORTS) {
      if (!pass) totalFails++;
      results.push({
        url:      BASE_URL + path,
        viewport: vp.name,
        width:    vp.width,
        height:   vp.height,
        mode:     'static-lint',
        pass,
        errors: errors.map(e => e),   // copy so each row owns its array
      });
    }

    if (!pass) {
      console.log(`  FAIL  ${path}`);
      errors.forEach(e => console.log(`         → ${e}`));
    } else {
      console.log(`  pass  ${path}`);
    }
  }

  const report = {
    generated: new Date().toISOString(),
    mode: 'static-lint',
    note: [
      'Static-lint mode: 10 structural checks per page, applied uniformly to all 8 viewport rows.',
      'Viewport-specific checks (overflow, console errors, broken images) require Playwright.',
      'To run full browser QA: npm install -D playwright && npx playwright install chromium && node scripts/responsive-qa.mjs',
    ].join(' '),
    base_url: BASE_URL,
    pages_checked: pagesFound,
    viewports_checked: VIEWPORTS.length,
    total_checks: results.length,
    passing_checks: results.filter(r => r.pass).length,
    failing_checks: totalFails,
    results,
  };

  writeFileSync(RESULTS_FILE, JSON.stringify(report, null, 2));

  console.log(`\nStatic-lint: ${pagesFound} pages × ${VIEWPORTS.length} viewports = ${results.length} checks`);
  console.log(`Passing: ${report.passing_checks} | Failing: ${totalFails}`);
  if (totalFails === 0) console.log('ALL CHECKS PASS.');
  console.log(`Results: ${RESULTS_FILE}`);
  if (totalFails > 0) process.exit(1);
  return report;
}

// ── Entry point ───────────────────────────────────────────────────────────────

(async () => {
  console.log('AskJamie™ Responsive QA\n' + '='.repeat(40));
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Pages: ${PUBLIC_PATHS.length} | Viewports: ${VIEWPORTS.length}\n`);

  const pwResult = FORCE_STATIC ? null : await runWithPlaywright();
  if (pwResult?.ok === false) {
    console.error(`Required browser QA could not start: ${pwResult.reason}`);
    console.error('Run with --static only if you explicitly want the structural lint mode.');
    process.exit(1);
  }
  if (!pwResult) {
    await staticAnalysis();
  }
})();

function isMermaidInlineStyleWarning(msg) {
  return msg.type() === 'error' &&
    msg.text().startsWith('Applying inline style violates the following Content Security Policy directive') &&
    /\/assets\/vendor\/mermaid\//.test(msg.location().url || '');
}

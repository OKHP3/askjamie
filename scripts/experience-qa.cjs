#!/usr/bin/env node
// Focused browser checks for AskJamie's search and progressive enhancement.
// Requires existing Playwright/Chromium. Run against the local source server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const results = [];
let browser;

async function context(options = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'no-preference', colorScheme: 'light', ...options });
  await ctx.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin ? route.continue() : route.abort());
  // These checks cover existing navigation/search after acknowledging the note.
  // The transition spec separately tests first-arrival and no-storage behavior.
  await ctx.addInitScript(() => sessionStorage.setItem('askjamie-gpt-transition-2026-09', 'acknowledged'));
  return ctx;
}
async function check(name, run) {
  try { const evidence = await run(); results.push({ name, pass: true, evidence }); }
  catch (error) { results.push({ name, pass: false, error: error.message }); }
}
async function ready(page, route = '/') {
  await page.goto(base + route, { waitUntil: 'domcontentloaded' });
  await page.locator('.okh-search-trigger').waitFor();
}

(async () => {
  browser = await chromium.launch({ headless: true });
  await check('AskJamie overlay copy, suggestions, empty state, and return focus', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page);
      await page.locator('.okh-search-trigger').click();
      const overlay = page.locator('.okh-search-overlay');
      assert.equal(await overlay.getAttribute('aria-label'), 'Search AskJamie');
      await overlay.locator('button[data-q]').first().waitFor();
      assert.match(await overlay.locator('input').getAttribute('placeholder'), /AskJamie/);
      const suggestions = await overlay.locator('button[data-q]').evaluateAll(es => es.map(e => e.dataset.q));
      for (const suggestion of suggestions) {
        await overlay.locator('input').fill(suggestion);
        assert.ok(await overlay.locator('.okh-search-result').count() > 0, suggestion);
      }
      await overlay.locator('input').fill('zzzzzzzznonexistent');
      assert.match(await overlay.innerText(), /Try BrandGuard, BFS/);
      assert.doesNotMatch(await overlay.innerText(), /Council|Manifesto|Forge/);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.okh-search-trigger').evaluate(e => e === document.activeElement), true);
      return { suggestions };
    } finally { await ctx.close(); }
  });
  await check('Skip link focuses main content and updates its fragment', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page);
      await page.keyboard.press('Tab');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.activeElement?.id === 'main');
      assert.equal(await page.evaluate(() => location.hash), '#main');
      assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'MAIN');
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'main');
      return { activeTag: 'MAIN', activeId: 'main' };
    } finally { await ctx.close(); }
  });
  await check('Reduced motion internal anchors update their fragment and scroll instantly', async () => {
    const ctx = await context({ reducedMotion: 'reduce' });
    try {
      const page = await ctx.newPage(); await ready(page);
      await page.evaluate(() => {
        window.__scrollIntoViewCalls = [];
        const original = Element.prototype.scrollIntoView;
        Element.prototype.scrollIntoView = function (options) {
          window.__scrollIntoViewCalls.push(options);
          return original.call(this, options);
        };
      });
      await page.locator('a[href="#uses"]').click();
      await page.waitForFunction(() => Array.isArray(window.__scrollIntoViewCalls) && window.__scrollIntoViewCalls.length > 0);
      const calls = await page.evaluate(() => window.__scrollIntoViewCalls);
      assert.equal(calls[0].behavior, 'auto');
      assert.equal(calls[0].block, 'start');
      assert.equal(await page.evaluate(() => location.hash), '#uses');
      return { behavior: calls[0].behavior, block: calls[0].block };
    } finally { await ctx.close(); }
  });
  await check('Mobile nav returns focus to the toggle after Escape', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
      await page.locator('.okh-search-trigger').waitFor();
      await page.locator('.nav-toggle').click();
      await page.waitForFunction(() => document.querySelector('.nav-toggle')?.getAttribute('aria-expanded') === 'true');
      await page.waitForFunction(() => document.activeElement?.closest('.primary-nav') !== null);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('.nav-toggle')?.getAttribute('aria-expanded') === 'false');
      assert.equal(await page.evaluate(() => document.activeElement?.className), 'nav-toggle');
      return { activeClass: 'nav-toggle' };
    } finally { await ctx.close(); }
  });
  await check('Dedicated search Enter opens the current first result', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page, '/search/?q=BFS');
      const first = page.locator('#search-results .okh-search-result').first(); await first.waitFor();
      const expected = new URL(await first.getAttribute('href'), base).href;
      await page.locator('#search-page-input').press('Enter');
      await page.waitForURL(expected);
      return { destination: expected };
    } finally { await ctx.close(); }
  });
  await check('Delayed index load uses the latest query before Enter navigation', async () => {
    const ctx = await context();
    let release;
    const indexGate = new Promise(resolve => { release = resolve; });
    try {
      await ctx.route('**/assets/data/search-index.json', async route => {
        await indexGate;
        await route.continue();
      });
      const page = await ctx.newPage(); await ready(page, '/search/?q=BFS');
      const input = page.locator('#search-page-input');
      await input.fill('BFS'); await input.press('Enter');
      assert.equal(new URL(page.url()).pathname, '/search/');
      await input.fill('professional portfolio'); release();
      const first = page.locator('#search-results .okh-search-result').first(); await first.waitFor();
      assert.equal(await input.inputValue(), 'professional portfolio');
      const expected = new URL(await first.getAttribute('href'), base).href;
      assert.match(expected, /professional-portfolio/);
      await input.press('Enter'); await page.waitForURL(expected);
      return { destination: expected, pendingEnterDidNotNavigate: true };
    } finally { release(); await ctx.close(); }
  });
  await check('Dedicated search Escape synchronizes query, results, focus, and category', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page, '/search/?q=BrandGuard');
      await page.locator('#search-categories button').first().waitFor();
      const category = page.locator('#search-categories button').nth(1); await category.click();
      const cat = new URL(page.url()).searchParams.get('cat');
      await page.locator('#search-page-input').press('Escape');
      assert.equal(await page.locator('#search-page-input').inputValue(), '');
      assert.equal(new URL(page.url()).searchParams.get('q'), null);
      assert.equal(new URL(page.url()).searchParams.get('cat'), cat);
      assert.equal(await page.locator('#search-results .okh-search-result').count(), 0);
      assert.equal(await page.locator('#search-page-input').evaluate(e => e === document.activeElement), true);
      return { categoryRetained: cat };
    } finally { await ctx.close(); }
  });
  await check('Dedicated search ignores empty results, modified Enter, and composition confirmation', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page, '/search/?q=BFS');
      const input = page.locator('#search-page-input'); await page.locator('#search-results a').first().waitFor();
      const before = page.url();
      await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
      await input.press('Control+Enter');
      assert.equal(page.url(), before);
      await input.fill('zzzzzzzznonexistent'); await input.press('Enter');
      assert.equal(new URL(page.url()).pathname, '/search/');
      assert.equal(await page.locator('#search-results a').count(), 0);
      return { compositionEventPreserved: true, noResultNavigation: false };
    } finally { await ctx.close(); }
  });
  for (const locale of ['fr-FR', 'de-DE', 'es-MX']) {
    await check('AskJamie ' + locale + ' draft fixture searches the available English catalog', async () => {
      const ctx = await context();
      const indexes = [];
      try {
        // Simulate a future draft without publishing a translated page.
        await ctx.route(base + '/search/**', async route => {
          const response = await route.fetch();
          await route.fulfill({ response, body: (await response.text()).replace('<html lang="en">', '<html lang="' + locale + '">') });
        });
        const page = await ctx.newPage();
        page.on('request', request => {
          const path = new URL(request.url()).pathname;
          if (path.startsWith('/assets/data/search-index')) indexes.push(path);
        });
        await ready(page, '/search/?q=BFS');
        await page.locator('#search-results .okh-search-result').first().waitFor();
        assert.equal(await page.locator('html').getAttribute('lang'), locale);
        assert.match(await page.locator('#search-stats').innerText(), /Search English content/);
        await page.locator('.okh-search-trigger').click();
        const overlay = page.locator('.okh-search-overlay');
        await overlay.locator('input').fill('BFS');
        await overlay.locator('.okh-search-result').first().waitFor();
        assert.deepEqual(indexes, ['/assets/data/search-index.json']);
        return { fixtureOnly: true, indexes, sharedCachedCatalog: true };
      } finally { await ctx.close(); }
    });
  }
  await check('Other brand overlay defaults remain unchanged', async () => {
    const ctx = await context();
    try {
      // This local response fixture changes only the body class before scripts run.
      await ctx.route(base + '/', async route => {
        const response = await route.fetch();
        await route.fulfill({ response, body: (await response.text()).replace('class="askjamie-main"', 'class="default-brand-fixture"') });
      });
      const page = await ctx.newPage(); await ready(page); await page.locator('.okh-search-trigger').click();
      const overlay = page.locator('.okh-search-overlay'); await overlay.locator('button[data-q]').first().waitFor();
      assert.equal(await overlay.getAttribute('aria-label'), 'Search OverKill Hill');
      assert.match(await overlay.locator('input').getAttribute('placeholder'), /Search the Forge/);
      assert.deepEqual(await overlay.locator('button[data-q]').evaluateAll(es => es.map(e => e.textContent)), ['Mermaid', 'ROY', 'Council', 'Manifesto', 'diagram', 'v0.3 Visual Edition']);
      return { fixtureOnly: true };
    } finally { await ctx.close(); }
  });
  for (const failure of ['javascript-disabled', 'app-script-blocked']) {
    await check('Desktop content remains visible: ' + failure, async () => {
      const ctx = await context({ javaScriptEnabled: failure !== 'javascript-disabled' });
      try {
        if (failure === 'app-script-blocked') await ctx.route('**/assets/js/app.js*', route => route.abort());
        const page = await ctx.newPage(); await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
        const sections = await page.locator('.reveal-on-scroll').evaluateAll(es => es.map(e => ({ id: e.id, opacity: getComputedStyle(e).opacity })));
        assert.ok(sections.length >= 5); assert.ok(sections.every(s => s.opacity === '1'), JSON.stringify(sections));
        return { sections };
      } finally { await ctx.close(); }
    });
  }
  await check('Initialized scroll reveal and reduced motion remain usable', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page);
      const armed = await page.locator('.is-reveal-ready').count(); assert.ok(armed > 0);
      const target = page.locator('#uses'); await target.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => document.querySelector('#uses').classList.contains('is-visible'));
      await page.emulateMedia({ reducedMotion: 'reduce' });
      // Chromium applies the changed media preference on a rendering update.
      // Wait for actual visibility, not merely completion of the emulation call.
      await page.waitForFunction(() => [...document.querySelectorAll('.reveal-on-scroll')]
        .every(element => getComputedStyle(element).opacity === '1'), null, { timeout: 3000 });
      const hidden = await page.locator('.reveal-on-scroll').evaluateAll(es => es.filter(e => getComputedStyle(e).opacity !== '1').length);
      assert.equal(hidden, 0); return { armed, hiddenUnderReducedMotion: hidden };
    } finally { await ctx.close(); }
  });
  await check('BrandGuard case links render as coherent card boxes', async () => {
    const ctx = await context();
    try {
      const page = await ctx.newPage(); await ready(page, '/lens-system/okhp3-brandguard/');
      const cards = page.locator('.brandguard-case-card');
      assert.equal(await cards.count(), 13);
      const geometry = await cards.evaluateAll(es => es.map(e => ({
        display: getComputedStyle(e).display,
        rects: e.getClientRects().length,
        width: e.getBoundingClientRect().width,
        height: e.getBoundingClientRect().height
      })));
      assert.ok(geometry.every(card => card.display === 'block' && card.rects === 1 && card.width > 0 && card.height > 0), JSON.stringify(geometry));
      await cards.first().focus();
      assert.equal(await cards.first().evaluate(e => e === document.activeElement), true);
      return { count: geometry.length, geometry: geometry.slice(0, 1) };
    } finally { await ctx.close(); }
  });
  for (const scheme of ['light', 'dark']) {
    await check('Footer normal text contrast in ' + scheme + ' scheme', async () => {
      const ctx = await context({ colorScheme: scheme });
      try {
        const page = await ctx.newPage(); await ready(page);
        const values = await page.locator('.site-footer').evaluate(footer => {
          const parse = s => (s.match(/[\d.]+/g) || []).map(Number);
          const lum = rgb => rgb.slice(0, 3).map(n => n / 255).map(n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4).reduce((s, n, i) => s + n * [.2126, .7152, .0722][i], 0);
          const bg = parse(getComputedStyle(footer).backgroundColor);
          return [...footer.querySelectorAll('p,a,h3,h4')].map(e => {
            const color = parse(getComputedStyle(e).color), alpha = color[3] ?? 1;
            const fg = color.slice(0, 3).map((n, i) => n * alpha + bg[i] * (1 - alpha));
            const l1 = lum(fg), l2 = lum(bg);
            return { text: e.textContent.trim().slice(0, 45), color: getComputedStyle(e).color, background: getComputedStyle(footer).backgroundColor, ratio: (Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05) };
          });
        });
        assert.ok(values.length > 10); assert.ok(values.every(v => v.ratio >= 4.5), JSON.stringify(values.filter(v => v.ratio < 4.5)));
        return { minimumRatio: Math.min(...values.map(v => v.ratio)), values };
      } finally { await ctx.close(); }
    });
  }
  await browser.close();
  const output = JSON.stringify({ base, externalRequests: 'blocked for deterministic local checks', checks: results }, null, 2) + '\n';
  if (process.env.QA_OUTPUT) fs.writeFileSync(process.env.QA_OUTPUT, output);
  console.log(output);
  process.exitCode = results.some(r => !r.pass) ? 1 : 0;
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });

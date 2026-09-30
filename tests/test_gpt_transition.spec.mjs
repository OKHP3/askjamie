import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const screenshotDir = process.env.TRANSITION_SCREENSHOT_DIR;
const browser = await chromium.launch({ headless: true });
const errors = [];
const routes = fs.readFileSync('sitemap.xml', 'utf8').match(/<loc>[^<]+<\/loc>/g)
  .map(item => new URL(item.slice(5, -6)).pathname);
const details = routes.filter(route => route.startsWith('/lens-system/') &&
  !['/lens-system/', '/lens-system/okhp3-brandguard/'].includes(route));
async function pageFor(options = {}) {
  const context = await browser.newContext({ colorScheme: 'light', reducedMotion: 'reduce', ...options });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  return { context, page };
}
async function capture(page, name) {
  if (!screenshotDir) return;
  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: path.join(screenshotDir, name + '.png'), fullPage: false });
}
try {
  const { context, page } = await pageFor({ viewport: { width: 1280, height: 900 } });
  await page.goto(base);
  const dialog = page.locator('[data-transition-dialog]');
  await dialog.waitFor({ state: 'visible' });
  assert.equal(await page.locator('[data-transition-dismiss]').evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Tab');
  assert.equal(await page.getByRole('link', { name: 'What is changing', exact: true }).last().evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.locator('[data-transition-dismiss]').evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Control+k');
  assert.notEqual(await page.locator('.okh-search-overlay').getAttribute('data-open'), 'true');
  await capture(page, 'homepage-dialog-desktop');
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await page.locator('[data-transition-open]').evaluate(el => document.activeElement === el), true);
  await page.reload();
  assert.equal(await dialog.isVisible(), false);
  await page.locator('[data-transition-open]').click();
  await dialog.waitFor({ state: 'visible' });
  await page.locator('[data-transition-dismiss]').click();
  await page.getByRole('button', { name: 'Open search (Ctrl+K)', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search', exact: true }).fill('retirement');
  await page.locator('.okh-search-result[href="/whats-next/"]').waitFor();
  await page.getByRole('button', { name: 'Close search', exact: true }).click();
  await page.goto(base + details[0]);
  assert.equal(await page.locator('[data-transition-dialog]').isVisible(), false);
  assert.equal(await page.locator('.capability-transition').isVisible(), true);
  await capture(page, 'gpt-page-notice-desktop');
  await page.goto(base + '/whats-next/');
  await page.getByRole('heading', { name: 'The ideas carry forward.', exact: true }).waitFor();
  await capture(page, 'transition-page-desktop');
  await context.close();

  // Fresh direct arrivals must see the note on every dedicated GPT page.
  assert.equal(details.length, 16);
  for (const route of details) {
    const { context, page } = await pageFor({ viewport: { width: 390, height: 844 } });
    await page.goto(base + route);
    await page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, route);
    await page.locator('[data-transition-dismiss]').click();
    assert.equal(await page.locator('.capability-transition').isVisible(), true, route);
    if (route === details[0]) await capture(page, 'gpt-page-notice-mobile');
    await context.close();
  }
  for (const width of [320, 390]) {
    const { context, page } = await pageFor({ viewport: { width, height: 844 } });
    await page.goto(base);
    await page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await capture(page, 'homepage-dialog-mobile-' + width);
    await page.locator('[data-transition-dismiss]').click();
    await page.goto(base + '/whats-next/');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await context.close();
  }
  // Storage denial must not block dismissal or explicit reopening.
  const denied = await pageFor();
  await denied.context.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {
    get() { throw new DOMException('Storage denied', 'SecurityError'); }
  }));
  await denied.page.goto(base);
  await denied.page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
  await denied.page.locator('[data-transition-dismiss]').click();
  await denied.page.locator('[data-transition-open]').click();
  await denied.page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
  await denied.context.close();
  const noJs = await pageFor({ javaScriptEnabled: false });
  await noJs.page.goto(base + details[0]);
  assert.equal(await noJs.page.locator('.capability-transition').isVisible(), true);
  assert.equal(await noJs.page.locator('[data-transition-open]').isVisible(), false);
  assert.equal(await noJs.page.locator('[data-transition-dialog]').isVisible(), false);
  await noJs.page.getByRole('link', { name: 'Read the transition update', exact: true }).click();
  assert.equal(new URL(noJs.page.url()).pathname, '/whats-next/');
  await noJs.context.close();
  assert.deepEqual(errors, []);
  console.log('Transition QA passed: 16 direct-entry GPT pages, 1280/390/320px, focus, Escape, reopen, session persistence, search, denied storage, and no-JavaScript fallback.');
} finally {
  await browser.close();
}

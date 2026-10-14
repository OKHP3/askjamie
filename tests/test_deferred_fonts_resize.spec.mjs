import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const route = '/lens-system/okhp3-brandguard/';
const browser = await chromium.launch({ headless: true });

async function openAt(width) {
  const page = await browser.newPage({
    viewport: { width, height: 900 },
    colorScheme: 'light',
    reducedMotion: 'reduce',
  });
  const fontResponses = [];
  page.on('response', response => {
    if (response.request().resourceType() === 'font') fontResponses.push(response.status());
  });
  await page.goto(new URL(route, base).href, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(2100);
  const loadedFonts = await page.evaluate(() => ({
    status: document.fonts.status,
    renderedText: ['.askjamie-hero-copy h1', '.askjamie-hero-copy .hero-subtitle'].map(selector => {
      const element = document.querySelector(selector);
      const style = getComputedStyle(element);
      return {
        selector,
        family: style.fontFamily,
        loaded: document.fonts.check(style.font, element.textContent || ' '),
      };
    }),
  }));
  assert.equal(loadedFonts.status, 'loaded', `font set did not settle at ${width}px`);
  assert.ok(loadedFonts.renderedText.every(item => item.loaded),
    `rendered hero fonts did not load at ${width}px: ${JSON.stringify(loadedFonts.renderedText)}`);
  assert.ok(fontResponses.some(status => status >= 200 && status < 300),
    `no successful font response at ${width}px: ${fontResponses}`);
  return page;
}

async function reserved(page) {
  return page.evaluate(() => [...document.querySelectorAll('[data-font-geometry-reserved="true"]')]
    .map(element => ({
      inline: element.style.minHeight,
      height: element.getBoundingClientRect().height,
    })));
}

async function heroGeometry(page) {
  return page.evaluate(() => ['.askjamie-breadcrumb', '.askjamie-hero-copy h1',
    '.askjamie-hero-copy .hero-subtitle', '.askjamie-hero-copy .hero-tagline']
    .map(selector => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return [rect.x, rect.y, rect.width, rect.height].map(value => Math.round(value * 100) / 100);
    }));
}

async function assertGeometryStableAcrossTheme(page) {
  const before = await heroGeometry(page);
  await page.locator('.glee-color-toggle').click();
  await page.locator('.glee-color-toggle').click();
  const after = await heroGeometry(page);
  for (let index = 0; index < before.length; index++) {
    for (let dimension = 0; dimension < before[index].length; dimension++) {
      assert.ok(Math.abs(before[index][dimension] - after[index][dimension]) <= 1,
        `theme switch shifted geometry at row ${index}, dimension ${dimension}: ` +
        `${before[index][dimension]} -> ${after[index][dimension]}`);
    }
  }
}

try {
  // A 320px load creates fallback reservations. They must not survive on desktop.
  const narrowFirst = await openAt(320);
  const initialReservations = await reserved(narrowFirst);
  assert.ok(initialReservations.length > 0, '320px load did not create intended reservations');
  await narrowFirst.setViewportSize({ width: 1440, height: 900 });
  await narrowFirst.waitForFunction(() =>
    document.querySelectorAll('[data-font-geometry-reserved="true"]').length === 0);
  assert.equal(await narrowFirst.locator('.askjamie-hero-copy .hero-subtitle').evaluate(
    element => element.style.minHeight), '', '320px min-height remained on desktop');
  await assertGeometryStableAcrossTheme(narrowFirst);
  await narrowFirst.close();

  // A desktop-first page must remain usable at 320px after real fonts settle.
  const desktopFirst = await openAt(1440);
  assert.equal((await reserved(desktopFirst)).length, 0,
    'desktop-first page unexpectedly received initial reservations');
  await desktopFirst.setViewportSize({ width: 320, height: 900 });
  await desktopFirst.waitForTimeout(100);
  assert.equal((await reserved(desktopFirst)).length, 0,
    'desktop-first resize should not inherit stale reservation markers');
  await assertGeometryStableAcrossTheme(desktopFirst);
  await desktopFirst.close();

  // Direct tablet load preserves its scoped initial geometry after font activation.
  const tablet = await openAt(899);
  const tabletReservations = await reserved(tablet);
  assert.ok(tabletReservations.length > 0, '899px load did not create tablet reservations');
  assert.ok(tabletReservations.every(item => parseFloat(item.inline) <= item.height + 1),
    'tablet reservation left more than 1px of excess block height after fonts loaded');
  await assertGeometryStableAcrossTheme(tablet);
  await tablet.close();

  console.log('PASS: deferred-font resize reservations at 320→1440, 1440→320, and 899px; loaded-font theme geometry');
} finally {
  await browser.close();
}

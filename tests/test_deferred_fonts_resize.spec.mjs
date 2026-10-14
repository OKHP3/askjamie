import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const paths = ['/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/'];
const browser = await chromium.launch({ headless: true });
const selectors = ['.askjamie-breadcrumb', '.askjamie-hero-copy h1',
  '.askjamie-hero-copy .hero-subtitle', '.askjamie-hero-copy .hero-tagline'];
const evidence = [];

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function geometry(page) {
  return page.evaluate(selectors => selectors.map(selector => {
    const rect = document.querySelector(selector).getBoundingClientRect();
    return {selector, x: rect.x + scrollX, y: rect.y + scrollY,
      width: rect.width, height: rect.height};
  }), selectors);
}

function compare(before, after, label) {
  for (let index = 0; index < before.length; index++) {
    for (const dimension of ['x', 'y', 'width', 'height']) {
      assert.ok(Math.abs(before[index][dimension] - after[index][dimension]) <= 1,
        `${label}: ${before[index].selector} ${dimension} ` +
        `${before[index][dimension]} -> ${after[index][dimension]}`);
    }
  }
}

async function checkThemes(page, label) {
  const baseline = await geometry(page);
  const toggle = page.locator('.glee-color-toggle');
  for (const expected of ['light', 'dark', 'auto']) {
    await toggle.click();
    await page.waitForFunction(expected =>
      document.querySelector('.glee-color-toggle').dataset.state === expected &&
      document.documentElement.getAttribute('data-color-scheme') ===
        (expected === 'auto' ? null : expected), expected);
    await settle(page);
    compare(baseline, await geometry(page), `${label} theme ${expected}`);
  }
}

async function openAt(path, width) {
  const page = await browser.newPage({viewport: {width, height: 900},
    colorScheme: 'light', reducedMotion: 'reduce'});
  await page.addInitScript(() => sessionStorage.setItem('askjamie-gpt-transition-2026-09', 'acknowledged'));
  const responses = [];
  page.on('response', response => {
    if (response.request().resourceType() === 'font') responses.push(response.status());
  });
  await page.goto(new URL(path, base).href, {waitUntil: 'networkidle'});
  await page.waitForFunction(() => [...document.querySelectorAll('link[data-deferred-fonts], link[data-deferred-styles]')]
    .every(link => link.media === 'all'));
  await settle(page);
  const fonts = await page.evaluate(async selectors => {
    const normalize = value => value.replace(/[\"']/g, '').trim().toLowerCase();
    const used = [];
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const style = getComputedStyle(element);
      const primary = normalize(style.fontFamily.split(',')[0]);
      const branded = ['baloo 2', 'open sans', 'kalam'].includes(primary);
      const faces = await document.fonts.load(
        `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,
        element.textContent || 'AskJamie');
      used.push({selector, primary, branded, faces: faces.map(face =>
        ({family: normalize(face.family), status: face.status}))});
    }
    await document.fonts.ready;
    return {status: document.fonts.status, used};
  }, selectors);
  assert.equal(fonts.status, 'loaded', `${path} ${width}: font set unsettled`);
  for (const sample of fonts.used.filter(item => item.branded)) {
    assert.ok(sample.faces.length > 0 && sample.faces.every(face =>
      face.status === 'loaded' && face.family === sample.primary),
    `${path} ${width}: missing matching loaded faces ${JSON.stringify(sample)}`);
  }
  assert.ok(responses.some(status => status >= 200 && status < 300),
    `${path} ${width}: no successful real-font response`);
  evidence.push({path, initialWidth: width, fonts, responses});
  return page;
}

async function markers(page) {
  return page.locator('[data-font-geometry-reserved="true"]').count();
}

async function resizeAndCheck(page, path, width) {
  await page.setViewportSize({width, height: 900});
  await page.waitForFunction(() =>
    document.querySelectorAll('[data-font-geometry-reserved="true"]').length === 0);
  await settle(page);
  // Compare with this same DOM after removing every original reservation. A stale
  // inline minimum can otherwise mask blank space while satisfying min-height.
  const before = await geometry(page);
  const cleared = await page.evaluate(() => {
    const elements = [...document.querySelectorAll('.site-header .container, .site-specials, .capability-transition > *, .askjamie-breadcrumb, .askjamie-hero-copy > *')];
    const remaining = elements.filter(element => element.style.minHeight).length;
    elements.forEach(element => element.style.removeProperty('min-height'));
    return remaining;
  });
  assert.equal(cleared, 0, `${path} ${width}: stale inline minimum after resize`);
  compare(before, await geometry(page), `${path} ${width} natural geometry`);
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  assert.ok(overflow <= 1, `${path} ${width}: horizontal overflow ${overflow}`);
  await checkThemes(page, `${path} ${width}`);
  evidence.push({path, resizedWidth: width, geometry: before});
}

try {
  for (const path of paths) {
    const narrow = await openAt(path, 320);
    assert.ok(await markers(narrow) > 0, `${path}: missing initial 320px reservations`);
    await narrow.setViewportSize({width: 320, height: 1000});
    await settle(narrow);
    assert.ok(await markers(narrow) > 0, `${path}: height-only resize cleared reservations`);
    for (const width of [899, 1440, 320]) await resizeAndCheck(narrow, path, width);
    await narrow.close();

    const tablet = await openAt(path, 899);
    assert.ok(await markers(tablet) > 0, `${path}: missing initial tablet reservations`);
    await checkThemes(tablet, `${path} initial899`);
    for (const width of [768, 320, 1440]) await resizeAndCheck(tablet, path, width);
    await tablet.close();

    const desktop = await openAt(path, 1440);
    assert.equal(await markers(desktop), 0, `${path}: unexpected desktop reservations`);
    await resizeAndCheck(desktop, path, 320);
    await desktop.close();
  }
  console.log(JSON.stringify({result: 'PASS', scope: 'BrandGuard hub, shortest and longest case studies; initial320/899/1440; width and height changes; each theme; real-font evidence; 1px tolerance', evidence}));
} finally {
  await browser.close();
}

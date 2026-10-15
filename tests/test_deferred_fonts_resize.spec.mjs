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

async function checkPingTransitionInitializationRace() {
  const context = await browser.newContext({
    viewport: {width: 899, height: 1024},
    colorScheme: 'light',
  });
  const page = await context.newPage();
  const path = '/lens-system/okhp3-brandguard/ping/';
  const beforeAppReady = [];
  const fontResponses = [];
  page.on('response', response => {
    if (response.request().resourceType() === 'font') {
      fontResponses.push(response.status());
    }
  });
  const geometrySelectors = [
    '.askjamie-breadcrumb',
    '.askjamie-hero-copy h1',
    '.askjamie-hero-copy .hero-subtitle',
    '.askjamie-hero-copy .hero-tagline',
  ];
  const layoutSelectors = [
    '.site-header',
    '.site-header .container',
    '.site-specials',
    '.site-specials-label',
    '.site-specials-link',
    '.capability-transition',
    '.capability-transition > *',
    ...geometrySelectors,
  ];
  let releaseFontRequests;
  const fontGate = new Promise(resolve => { releaseFontRequests = resolve; });
  let sawFontRequest;
  const firstFontRequest = new Promise(resolve => { sawFontRequest = resolve; });
  let fontRequestsStarted = 0;

  await page.addInitScript(() =>
    sessionStorage.setItem('askjamie-gpt-transition-2026-09', 'acknowledged'));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname === 'fonts.gstatic.com' &&
        route.request().resourceType() === 'font') {
      fontRequestsStarted += 1;
      sawFontRequest();
      await fontGate;
    }
    if (url.pathname === '/assets/js/app.js' &&
        route.request().resourceType() === 'script') {
      await new Promise(resolve => setTimeout(resolve, 700));
    }
    await route.continue();
  });

  try {
    await page.goto(new URL(path, base).href, {
      waitUntil: 'commit',
      timeout: 10000,
    });
    await page.waitForFunction(() =>
      Boolean(document.querySelector('[data-font-geometry-reserved="true"]')),
    undefined, {timeout: 10000});
    await Promise.race([
      firstFontRequest,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('No real font request started')), 3000)),
    ]);

    const reservation = await page.evaluate(({selectors, layoutSelectors}) => {
      const rect = selector => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return {
          x: box.x + scrollX,
          y: box.y + scrollY,
          width: box.width,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
        };
      };
      const transitionTrigger = document.querySelector(
        '.capability-transition [data-transition-open]');
      const theme = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => new URL(link.href).pathname === '/assets/css/theme.css');
      const transitionChildren = [
        ...(document.querySelector('.capability-transition')?.children ?? []),
      ].map((element, index) => {
        const box = element.getBoundingClientRect();
        return {
          index,
          tag: element.tagName,
          hidden: element.hidden,
          y: box.y + scrollY,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
        };
      });
      return {
        readyState: document.readyState,
        scrollY,
        bodyClass: document.body.className,
        activeElement: document.activeElement?.tagName,
        transitionTriggerHidden: transitionTrigger?.hidden ?? null,
        transitionTriggerReserved:
          transitionTrigger?.dataset.fontGeometryReserved === 'true',
        themeSheetReady: Boolean(theme?.sheet?.cssRules.length),
        fontStatus: document.fonts.status,
        geometry: Object.fromEntries(selectors.map(selector =>
          [selector, rect(selector)])),
        layout: Object.fromEntries(layoutSelectors.map(selector =>
          [selector, rect(selector)])),
        transitionChildren,
        transitionHeight: rect('.capability-transition')?.height ?? null,
      };
    }, {selectors: geometrySelectors, layoutSelectors});
    beforeAppReady.push(reservation);
    releaseFontRequests();

    await page.waitForFunction(() => {
      const trigger = document.querySelector(
        '.capability-transition [data-transition-open]');
      return Boolean(trigger && !trigger.hidden);
    }, undefined, {timeout: 10000});
    await page.evaluate(() => document.fonts.ready);
    await settle(page);
    const fonts = await page.evaluate(async selectors => {
      const expectedFamilies = ['kalam', 'baloo 2', 'kalam', 'open sans'];
      const normalize = value => value.replace(/[\"']/g, '').trim().toLowerCase();
      const used = await Promise.all(selectors.map(async (selector, index) => {
        const element = document.querySelector(selector);
        const style = getComputedStyle(element);
        const faces = await document.fonts.load(
          `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,
          element.textContent || 'AskJamie');
        return {selector, expected: expectedFamilies[index],
          primary: normalize(style.fontFamily.split(',')[0]),
          faces: faces.map(face => ({family: normalize(face.family), status: face.status}))};
      }));
      await document.fonts.ready;
      return {status: document.fonts.status, used};
    }, geometrySelectors);
    assert.equal(fonts.status, 'loaded');
    for (const sample of fonts.used) {
      assert.equal(sample.primary, sample.expected);
      assert.ok(sample.faces.length > 0 && sample.faces.every(face =>
        face.family === sample.expected && face.status === 'loaded'),
      `PING 899px missing matching loaded faces: ${JSON.stringify(sample)}`);
    }
    assert.ok(fontResponses.some(status => status >= 200 && status < 300),
      'PING 899px has no successful real-font response');
    await settle(page);

    const after = await page.evaluate(({selectors, layoutSelectors}) => {
      const rect = selector => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return {
          x: box.x + scrollX,
          y: box.y + scrollY,
          width: box.width,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
        };
      };
      const transitionChildren = [
        ...(document.querySelector('.capability-transition')?.children ?? []),
      ].map((element, index) => {
        const box = element.getBoundingClientRect();
        return {
          index,
          tag: element.tagName,
          hidden: element.hidden,
          y: box.y + scrollY,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
        };
      });
      return {
        readyState: document.readyState,
        scrollY,
        fontStatus: document.fonts.status,
        geometry: Object.fromEntries(selectors.map(selector =>
          [selector, rect(selector)])),
        bodyClass: document.body.className,
        activeElement: document.activeElement?.tagName,
        transitionTriggerHidden: document.querySelector(
          '.capability-transition [data-transition-open]'
        )?.hidden ?? null,
        layout: Object.fromEntries(layoutSelectors.map(selector =>
          [selector, rect(selector)])),
        transitionChildren,
        transitionHeight: rect('.capability-transition')?.height ?? null,
        transitionTriggerReserved: document.querySelector(
          '.capability-transition [data-transition-open]'
        )?.dataset.fontGeometryReserved === 'true',
      };
    }, {selectors: geometrySelectors, layoutSelectors});

    if (process.env.PING_FONT_RACE_DIAGNOSTICS === '1') {
      console.log(JSON.stringify({
        fixture: path,
        viewport: {width: 899, height: 1024},
        fonts, fontResponses,
        reservation: beforeAppReady[0],
        after,
      }, null, 2));
    }
    assert.ok(fontRequestsStarted > 0, 'real font request was not held at reservation');
    assert.equal(beforeAppReady[0].themeSheetReady, true);
    assert.equal(beforeAppReady[0].scrollY, 0);
    assert.equal(after.fontStatus, 'loaded');
    assert.ok(
      Math.abs(after.transitionHeight - beforeAppReady[0].transitionHeight) <= 1,
      `PING transition notice shifted ${beforeAppReady[0].transitionHeight} -> ${after.transitionHeight}px`
    );
    compare(
      geometrySelectors.map(selector => ({
        selector,
        ...beforeAppReady[0].geometry[selector],
      })),
      geometrySelectors.map(selector => ({
        selector,
        ...after.geometry[selector],
      })),
      `${path} 899px delayed app initialization`
    );
    assert.equal(beforeAppReady[0].transitionTriggerHidden, false);
    assert.equal(beforeAppReady[0].transitionTriggerReserved, true);
    assert.equal(after.transitionTriggerReserved, true);
    evidence.push({path, initialWidth: 899, scenario: 'delayed-app-initialization',
      fonts, responses: fontResponses, before: reservation.geometry, after: after.geometry});
  } finally {
    releaseFontRequests();
    await context.close();
  }
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
  await checkPingTransitionInitializationRace();

  if (process.env.PING_FONT_RACE_ONLY !== '1') {
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
  }
  const scope = process.env.PING_FONT_RACE_ONLY === '1'
    ? 'PING 899px delayed app initialization; real fonts; 1px geometry tolerance'
    : 'BrandGuard hub, shortest and longest case studies; initial320/899/1440; width and height changes; each theme; real-font evidence; 1px tolerance';
  console.log(JSON.stringify({result: 'PASS', scope, evidence}));
} finally {
  await browser.close();
}

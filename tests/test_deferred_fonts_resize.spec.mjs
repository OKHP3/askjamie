import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import { chromium } from 'playwright';
import {fileURLToPath} from 'node:url';

const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const brandGuardPrefix = '/lens-system/okhp3-brandguard/';
const sitemapBrandGuardUrls = [...readFileSync(resolve(root, 'sitemap.xml'), 'utf8')
  .matchAll(/<loc>([^<]+)<\/loc>/g)]
  .map(([, location]) => new URL(location.trim()))
  .filter(url => url.pathname.startsWith(brandGuardPrefix));
if (sitemapBrandGuardUrls.some(url => url.origin !== 'https://askjamie.bot' ||
    url.search || url.hash)) {
  throw new Error('sitemap.xml has an invalid BrandGuard route for delayed-app race coverage');
}
const sitemapPaths = sitemapBrandGuardUrls.map(url => url.pathname);
if (sitemapPaths.length === 0) {
  throw new Error('sitemap.xml has no BrandGuard routes for delayed-app race coverage');
}
if (new Set(sitemapPaths).size !== sitemapPaths.length) {
  throw new Error('sitemap.xml has duplicate BrandGuard routes for delayed-app race coverage');
}
const representativePaths = [
  brandGuardPrefix,
  `${brandGuardPrefix}lvmh/`,
  `${brandGuardPrefix}bfs-framing-intelligent-futures/`,
];
const raceOnly = process.env.BRANDGUARD_FONT_RACE_ONLY === '1' ||
  process.env.PING_FONT_RACE_ONLY === '1';
const browser = await chromium.launch({ headless: true });
const selectors = ['.capability-transition', '.askjamie-breadcrumb', '.askjamie-hero-copy h1',
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

function compare(before, after, label, dimensions = ['x', 'y', 'width', 'height']) {
  assert.deepEqual(after.map(item => item.selector), before.map(item => item.selector),
    `${label}: compared elements changed`);
  for (let index = 0; index < before.length; index++) {
    for (const dimension of dimensions) {
      assert.ok(Math.abs(before[index][dimension] - after[index][dimension]) <= 1,
        `${label}: ${before[index].selector} ${dimension} ` +
        `${before[index][dimension]} -> ${after[index][dimension]}; ` +
        `before=${JSON.stringify(before[index])}; after=${JSON.stringify(after[index])}`);
    }
  }
}

function overlappingBlocks(layout) {
  const selectors = [
    '.capability-transition',
    '.askjamie-breadcrumb',
    '.askjamie-hero-copy h1',
    '.askjamie-hero-copy .hero-subtitle',
    '.askjamie-hero-copy .hero-tagline',
  ];
  const overlaps = [];
  for (let first = 0; first < selectors.length; first++) {
    for (let second = first + 1; second < selectors.length; second++) {
      const left = layout[selectors[first]];
      const right = layout[selectors[second]];
      if (!left || !right) continue;
      const horizontal = Math.min(left.x + left.width, right.x + right.width) -
        Math.max(left.x, right.x);
      const vertical = Math.min(left.y + left.height, right.y + right.height) -
        Math.max(left.y, right.y);
      if (horizontal > 1 && vertical > 1) {
        overlaps.push({
          first: selectors[first],
          first_box: left,
          second: selectors[second],
          second_box: right,
        });
      }
    }
  }
  return overlaps;
}

function overlappingTransitionChildren(children) {
  const overlaps = [];
  for (let first = 0; first < children.length; first++) {
    for (let second = first + 1; second < children.length; second++) {
      const left = children[first];
      const right = children[second];
      const horizontal = Math.min(left.x + left.width, right.x + right.width) -
        Math.max(left.x, right.x);
      const vertical = Math.min(left.y + left.height, right.y + right.height) -
        Math.max(left.y, right.y);
      if (horizontal > 1 && vertical > 1) {
        overlaps.push({
          first: `.capability-transition > child[${left.index}]`,
          first_box: left,
          second: `.capability-transition > child[${right.index}]`,
          second_box: right,
        });
      }
    }
  }
  return overlaps;
}

function overlappingNoticeAndHero(children, layout) {
  const heroSelectors = [
    '.askjamie-breadcrumb',
    '.askjamie-hero-copy h1',
    '.askjamie-hero-copy .hero-subtitle',
    '.askjamie-hero-copy .hero-tagline',
  ];
  const overlaps = [];
  for (const child of children) {
    for (const selector of heroSelectors) {
      const hero = layout[selector];
      const horizontal = Math.min(child.x + child.width, hero.x + hero.width) -
        Math.max(child.x, hero.x);
      const vertical = Math.min(child.y + child.height, hero.y + hero.height) -
        Math.max(child.y, hero.y);
      if (horizontal > 1 && vertical > 1) {
        overlaps.push({
          first: `.capability-transition > child[${child.index}]`,
          first_box: child,
          second: selector,
          second_box: hero,
        });
      }
    }
  }
  return overlaps;
}

function measuredDeltas(before, after) {
  return before.map((item, index) => {
    const delta = Object.fromEntries(
      ['x', 'y', 'width', 'height'].map(dimension => [
        dimension,
        Math.round((after[index][dimension] - item[dimension]) * 100) / 100,
      ])
    );
    return {selector: item.selector, ...delta};
  }).filter(item => ['x', 'y', 'width', 'height']
    .some(dimension => item[dimension] !== 0));
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

async function checkBrandGuardTransitionInitializationRace(path, width) {
  const context = await browser.newContext({
    viewport: {width, height: 1024},
    colorScheme: 'light',
  });
  const page = await context.newPage();
  const fontResponses = [];
  const reservationSchedulingErrors = [];
  page.on('pageerror', error => {
    if (/scheduleBrandGuardReservation/.test(error.message)) {
      reservationSchedulingErrors.push(error.message);
    }
  });
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
    '.capability-transition [data-transition-open]',
    '.capability-transition > *',
    ...geometrySelectors,
  ];
  let releaseFontRequests;
  const fontGate = new Promise(resolve => { releaseFontRequests = resolve; });
  let releaseAppRequest;
  const appGate = new Promise(resolve => { releaseAppRequest = resolve; });
  let sawFontRequest;
  const firstFontRequest = new Promise(resolve => { sawFontRequest = resolve; });
  let sawAppRequest;
  const appRequestStarted = new Promise(resolve => { sawAppRequest = resolve; });
  let deferredFontsFinished;
  const deferredFontsRequestFinished = new Promise(resolve => {
    deferredFontsFinished = resolve;
  });
  let fontRequestsStarted = 0;

  page.on('requestfinished', request => {
    if (new URL(request.url()).pathname === '/assets/js/deferred-fonts.js') {
      deferredFontsFinished();
    }
  });
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
      sawAppRequest();
      await appGate;
    }
    await route.continue();
  });

  try {
    await page.goto(new URL(path, base).href, {
      waitUntil: 'commit',
      timeout: 10000,
    });
    await Promise.race([
      appRequestStarted,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('app.js request did not start')), 3000)),
    ]);
    await Promise.race([
      deferredFontsRequestFinished,
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('deferred-fonts.js did not finish')), 3000)),
    ]);
    await page.waitForFunction(() => document.readyState === 'interactive');

    let startupResize = null;
    if (width === 320) {
      assert.equal(await markers(page), 0,
        `${path} 320px must not reserve the notice before app initialization`);
      await page.setViewportSize({width: 390, height: 1024});
      await page.evaluate(() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await markers(page), 0,
        `${path} 390px must not receive the original 320px reservation`);
      await page.setViewportSize({width: 320, height: 1024});
      await page.evaluate(() => new Promise(resolve =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await markers(page), 0,
        `${path} 320px must remain unmeasured until app initialization finishes`);
      assert.deepEqual(reservationSchedulingErrors, [],
        `${path} resize handling must not fail while app initialization is delayed`);
      startupResize = {beforeInitialization: [320, 390, 320], finalWidth: 320};
    }
    releaseAppRequest();

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
          reservedWidth: element.dataset.fontGeometryReservedWidth || null,
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
          x: box.x + scrollX,
          index,
          tag: element.tagName,
          hidden: element.hidden,
          visible: getComputedStyle(element).display !== 'none' &&
            getComputedStyle(element).visibility !== 'hidden' &&
            box.width > 0 && box.height > 0,
          y: box.y + scrollY,
          width: box.width,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
          reservedWidth: element.dataset.fontGeometryReservedWidth || null,
        };
      });
      return {
        transitionTriggerVisible: Boolean(
          transitionTrigger && !transitionTrigger.hidden &&
          getComputedStyle(transitionTrigger).display !== 'none' &&
          transitionTrigger.getBoundingClientRect().width > 0 &&
          transitionTrigger.getBoundingClientRect().height > 0
        ),
        readyState: document.readyState,
        viewportWidth: innerWidth,
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
        heroReservations: [...document.querySelectorAll(".askjamie-hero-copy > *")]
          .map(element => ({
            tag: element.tagName,
            className: element.className,
            minHeight: element.style.minHeight || null,
            reserved: element.dataset.fontGeometryReserved === 'true',
            reservedWidth: element.dataset.fontGeometryReservedWidth || null,
          })),
        transitionChildren,
        transitionHeight: rect('.capability-transition')?.height ?? null,
      };
    }, {selectors: geometrySelectors, layoutSelectors});
    assert.equal(reservation.transitionTriggerHidden, false,
      `${path} ${width}px transition notice control must be visible before fonts release`);
    assert.equal(reservation.transitionTriggerVisible, true,
      `${path} ${width}px transition notice control must have a visible box before fonts release: ` +
      `${JSON.stringify(reservation.layout['.capability-transition [data-transition-open]'])}`);
    assert.equal(reservation.transitionTriggerReserved, true,
      `${path} ${width}px transition notice control must be reserved before fonts release`);
    const reservationTargets = [
      ['.capability-transition', [reservation.layout['.capability-transition']]],
      ['.capability-transition [data-transition-open]',
        [reservation.layout['.capability-transition [data-transition-open]']]],
      ['.capability-transition > *', reservation.transitionChildren],
      ...geometrySelectors.map(selector => [selector, [reservation.layout[selector]]]),
    ];
    for (const [selector, elements] of reservationTargets) {
      assert.ok(elements.length > 0 && elements.every(element => element?.reserved),
        `${path} ${width}px ${selector} must be reserved before fonts release: ` +
        `${JSON.stringify(elements)}`);
      assert.ok(elements.every(element =>
        element.reservedWidth === String(width) &&
        Number.parseFloat(element.minHeight) > 0),
      `${path} ${width}px ${selector} must use a baseline measured at the final viewport: ` +
      `${JSON.stringify(elements)}`);
    }
    assert.ok(reservation.transitionChildren.length > 0,
      `${path} ${width}px notice must contain measurable children`);
    assert.ok(reservation.transitionChildren.every(element =>
      !element.hidden && element.visible &&
      element.reservedWidth === String(width)),
      `${path} ${width}px notice has hidden children at reservation: ` +
      `${JSON.stringify(reservation.transitionChildren)}`);
    assert.equal(reservation.viewportWidth, width,
      `${path} reservation must capture the final ${width}px viewport`);
    assert.ok(reservation.heroReservations.length > 0 &&
      reservation.heroReservations.every(element =>
        element.reserved && element.reservedWidth === String(width) &&
        Number.parseFloat(element.minHeight) > 0),
    `${path} ${width}px hero children must use final-viewport reservations: ` +
    `${JSON.stringify(reservation.heroReservations)}`);
    assert.ok(fontRequestsStarted > 0,
      `${path} ${width}px must start a real font request before capturing reservations`);
    assert.equal(fontResponses.length, 0,
      `${path} ${width}px font requests must still be held at reservation`);
    const beforeOverlaps = overlappingBlocks(reservation.layout);
    assert.deepEqual(beforeOverlaps, [],
      `${path} ${width}px reserved notice and hero blocks must not overlap; ` +
      `overlap boxes=${JSON.stringify(beforeOverlaps)}`);
    const beforeChildOverlaps = overlappingTransitionChildren(
      reservation.transitionChildren
    );
    assert.deepEqual(beforeChildOverlaps, [],
      `${path} ${width}px reserved notice children must not overlap; ` +
      `overlap boxes=${JSON.stringify(beforeChildOverlaps)}`);
    const beforeNoticeHeroOverlaps = overlappingNoticeAndHero(
      reservation.transitionChildren,
      reservation.layout
    );
    assert.deepEqual(beforeNoticeHeroOverlaps, [],
      `${path} ${width}px reserved notice children must not overlap breadcrumb or hero; ` +
      `overlap boxes=${JSON.stringify(beforeNoticeHeroOverlaps)}`);
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
      `${path} ${width}px missing matching loaded faces: ${JSON.stringify(sample)}`);
    }
    assert.ok(fontResponses.some(status => status >= 200 && status < 300),
      `${path} ${width}px has no successful real-font response`);
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
          reservedWidth: element.dataset.fontGeometryReservedWidth || null,
        };
      };
      const transitionChildren = [
        ...(document.querySelector('.capability-transition')?.children ?? []),
      ].map((element, index) => {
        const box = element.getBoundingClientRect();
        return {
          x: box.x + scrollX,
          index,
          tag: element.tagName,
          hidden: element.hidden,
          visible: getComputedStyle(element).display !== 'none' &&
            getComputedStyle(element).visibility !== 'hidden' &&
            box.width > 0 && box.height > 0,
          y: box.y + scrollY,
          width: box.width,
          height: box.height,
          minHeight: element.style.minHeight || null,
          reserved: element.dataset.fontGeometryReserved === 'true',
          reservedWidth: element.dataset.fontGeometryReservedWidth || null,
        };
      });
      const transitionTrigger = document.querySelector(
        '.capability-transition [data-transition-open]'
      );
      return {
        transitionTriggerVisible: Boolean(
          transitionTrigger && !transitionTrigger.hidden &&
          getComputedStyle(transitionTrigger).display !== 'none' &&
          transitionTrigger.getBoundingClientRect().width > 0 &&
          transitionTrigger.getBoundingClientRect().height > 0
        ),
        readyState: document.readyState,
        viewportWidth: innerWidth,
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
        heroReservations: [...document.querySelectorAll(".askjamie-hero-copy > *")]
          .map(element => ({
            tag: element.tagName,
            className: element.className,
            minHeight: element.style.minHeight || null,
            reserved: element.dataset.fontGeometryReserved === 'true',
            reservedWidth: element.dataset.fontGeometryReservedWidth || null,
          })),
        transitionChildren,
        transitionHeight: rect('.capability-transition')?.height ?? null,
        transitionTriggerReserved: document.querySelector(
          '.capability-transition [data-transition-open]'
        )?.dataset.fontGeometryReserved === 'true',
      };
    }, {selectors: geometrySelectors, layoutSelectors});
    const afterOverlaps = overlappingBlocks(after.layout);
    assert.deepEqual(afterOverlaps, [],
      `${path} ${width}px loaded-font notice and hero blocks must not overlap; ` +
      `overlap boxes=${JSON.stringify(afterOverlaps)}`);
    const afterChildOverlaps = overlappingTransitionChildren(after.transitionChildren);
    assert.deepEqual(afterChildOverlaps, [],
      `${path} ${width}px loaded-font notice children must not overlap; ` +
      `overlap boxes=${JSON.stringify(afterChildOverlaps)}`);
    const afterNoticeHeroOverlaps = overlappingNoticeAndHero(
      after.transitionChildren,
      after.layout
    );
    assert.deepEqual(afterNoticeHeroOverlaps, [],
      `${path} ${width}px loaded-font notice children must not overlap breadcrumb or hero; ` +
      `overlap boxes=${JSON.stringify(afterNoticeHeroOverlaps)}`);

    if (process.env.BRANDGUARD_FONT_RACE_DIAGNOSTICS === '1') {
      console.log(JSON.stringify({
        fixture: path,
        viewport: {width, height: 1024},
        fonts, fontResponses,
        reservation,
        after,
      }, null, 2));
    }
    assert.equal(reservation.themeSheetReady, true,
      `${path} ${width}px theme stylesheet must be active at reservation`);
    assert.equal(reservation.scrollY, 0,
      `${path} ${width}px page must remain at the top during reservation`);
    assert.equal(after.fontStatus, 'loaded');
    assert.equal(after.viewportWidth, width,
      `${path} ${width}px font release must retain the final viewport`);
    assert.ok(
      Math.abs(after.transitionHeight - reservation.transitionHeight) <= 1,
      `${path} ${width}px transition notice shifted ` +
      `${reservation.transitionHeight} -> ${after.transitionHeight}px; ` +
      `before=${JSON.stringify(reservation.layout['.capability-transition'])}; ` +
      `after=${JSON.stringify(after.layout['.capability-transition'])}`
    );
    const geometryToCompare = [
      '.capability-transition',
      ...geometrySelectors,
    ];
    compare(
      geometryToCompare.map(selector => ({
        selector,
        ...reservation.layout[selector],
      })),
      geometryToCompare.map(selector => ({
        selector,
        ...after.layout[selector],
      })),
      `${path} ${width}px delayed app initialization`
    );
    const layoutBeforeBoxes = geometryToCompare.map(selector => ({
      selector,
      ...reservation.layout[selector],
    }));
    const layoutAfterBoxes = geometryToCompare.map(selector => ({
      selector,
      ...after.layout[selector],
    }));
    const childBeforeBoxes = reservation.transitionChildren.map((child, index) => ({
      selector: `.capability-transition > child[${index}]`,
      ...child,
    }));
    const childAfterBoxes = after.transitionChildren.map((child, index) => ({
      selector: `.capability-transition > child[${index}]`,
      ...child,
    }));
    const controlSelector = '.capability-transition [data-transition-open]';
    compare(
      [{selector: controlSelector, ...reservation.layout[controlSelector]}],
      [{selector: controlSelector, ...after.layout[controlSelector]}],
      `${path} ${width}px transition control position`,
      ['x', 'y']
    );
    compare(
      reservation.transitionChildren.map((child, index) => ({
        selector: `.capability-transition > child[${index}]`,
        ...child,
      })),
      after.transitionChildren.map((child, index) => ({
        selector: `.capability-transition > child[${index}]`,
        ...child,
      })),
      `${path} ${width}px transition child position`,
      ['x', 'y']
    );
    assert.equal(reservation.transitionTriggerHidden, false);
    assert.equal(reservation.transitionTriggerVisible, true);
    assert.equal(reservation.transitionTriggerReserved, true);
    assert.equal(after.transitionTriggerReserved, true);
    assert.equal(after.transitionTriggerHidden, false);
    assert.equal(after.transitionTriggerVisible, true);
    assert.ok(after.transitionChildren.every(element =>
      element.reserved && element.reservedWidth === String(width) &&
      !element.hidden && element.visible),
      `${path} ${width}px notice children lost reservation after font release: ` +
      `${JSON.stringify(after.transitionChildren)}`);
    assert.ok(after.heroReservations.every(element =>
      element.reserved && element.reservedWidth === String(width)),
    `${path} ${width}px hero reservations changed after font release: ` +
    `${JSON.stringify(after.heroReservations)}`);
    evidence.push({path, initialWidth: width, scenario: 'delayed-app-initialization',
      startupResize, fonts, responses: fontResponses,
      before: reservation.layout, after: after.layout,
      transition_children_before: reservation.transitionChildren,
      transition_children_after: after.transitionChildren,
      measured_geometry_deltas: {
        panel_breadcrumb_hero: measuredDeltas(layoutBeforeBoxes, layoutAfterBoxes),
        notice_children: measuredDeltas(childBeforeBoxes, childAfterBoxes),
      },
      transition_control_box_size_delta: {
        width: after.layout[controlSelector].width -
          reservation.layout[controlSelector].width,
        height: after.layout[controlSelector].height -
          reservation.layout[controlSelector].height,
      },
      overlaps: {
        beforeFonts: beforeOverlaps,
        afterFonts: afterOverlaps,
        noticeChildrenBeforeFonts: beforeChildOverlaps,
        noticeChildrenAfterFonts: afterChildOverlaps,
        noticeChildrenWithHeroBeforeFonts: beforeNoticeHeroOverlaps,
        noticeChildrenWithHeroAfterFonts: afterNoticeHeroOverlaps,
      }});
  } finally {
    releaseAppRequest();
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
    const elements = [...document.querySelectorAll('.site-header .container, .site-specials, .capability-transition, .capability-transition > *, .askjamie-breadcrumb, .askjamie-hero-copy > *')];
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
  const pingPath = `${brandGuardPrefix}ping/`;
  await checkBrandGuardTransitionInitializationRace(pingPath, 899);
  for (const path of sitemapPaths) {
    await checkBrandGuardTransitionInitializationRace(path, 320);
  }

  if (!raceOnly) {
    for (const path of representativePaths) {
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
      assert.ok(await markers(desktop) > 0, `${path}: missing desktop reservations`);
      await resizeAndCheck(desktop, path, 320);
      await desktop.close();
    }
  }
  const scope = raceOnly
    ? `PING 899px and all ${sitemapPaths.length} sitemap-listed BrandGuard routes at 320px; 320→390→320 resize before app initialization; real fonts held then released; 1px geometry tolerance`
    : `All ${sitemapPaths.length} sitemap-listed BrandGuard routes at 320px plus PING 899px; 320→390→320 before initialization; representative hub/LVMH/BFS resize and theme checks; real fonts held then released; 1px tolerance`;
  console.log(JSON.stringify({
    result: 'PASS',
    scope,
    brandguard_sitemap_routes: sitemapPaths,
    delayed_initialization_routes: evidence
      .filter(item => item.scenario === 'delayed-app-initialization')
      .map(item => ({path: item.path, width: item.initialWidth})),
    evidence,
  }));
} finally {
  await browser.close();
}

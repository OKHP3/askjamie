import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const base = process.env.BASE_URL || "http://127.0.0.1:5000";
const browser = await chromium.launch({ headless: true });
const require = createRequire(import.meta.url);
const playwrightVersion = require("playwright/package.json").version;
const selectors = [
  ".site-header",
  ".site-header .container",
  ".site-specials",
  ".site-specials > *",
  ".askjamie-hero-copy .eyebrow",
  ".askjamie-hero-copy h1",
  ".askjamie-hero-copy .hero-subtitle",
  ".askjamie-hero-copy .askjamie-hero-tagline",
  ".askjamie-hero-copy .hero-actions .btn",
  ".universe-map-generated > h2",
  ".universe-map-generated > p",
  ".universe-map-group[open] > summary",
  ".universe-map-group[open] li",
];
const observations = [];

const geometry = page => page.evaluate(selectorsToMeasure => ({
  scroll: { x: scrollX, y: scrollY },
  blocks: selectorsToMeasure.flatMap(selector =>
    [...document.querySelectorAll(selector)]
      .filter(element => element.getClientRects().length)
      .map((element, index) => {
        const rect = element.getBoundingClientRect();
        return {
          selector,
          index,
          className: typeof element.className === "string" ? element.className : "",
          text: (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 90),
          x: rect.x + scrollX,
          y: rect.y + scrollY,
          width: rect.width,
          height: rect.height,
        };
      })),
}), selectors);

function compareGeometry(before, after, label) {
  assert.equal(after.scroll.x, before.scroll.x, `${label}: horizontal scroll changed`);
  assert.equal(after.scroll.y, before.scroll.y, `${label}: vertical scroll changed`);
  assert.deepEqual(after.blocks.map(({ selector, index }) => [selector, index]),
    before.blocks.map(({ selector, index }) => [selector, index]),
    `${label}: visible text-block inventory changed`);
  const shifts = [];
  for (let index = 0; index < before.blocks.length; index += 1) {
    for (const dimension of ["x", "y", "width", "height"]) {
      if (Math.abs(before.blocks[index][dimension] - after.blocks[index][dimension]) > 1) shifts.push(
        `${label}: ${before.blocks[index].selector} ${dimension} ` +
        `${before.blocks[index][dimension]} -> ${after.blocks[index][dimension]}; ` +
        `block before=${JSON.stringify(before.blocks[index])} ` +
        `after=${JSON.stringify(after.blocks[index])}`);
    }
  }
  assert.equal(shifts.length, 0, shifts.join("\n"));
}

async function waitForReservations(page, width) {
  await page.waitForFunction(expectedWidth => {
    const linksReady = [...document.querySelectorAll(
      "link[data-deferred-fonts], link[data-deferred-styles]"
    )].every(link => link.media === "all");
    const reservations = [...document.querySelectorAll(
      '[data-universe-font-reservation="true"]'
    )];
    return linksReady && reservations.length > 0 &&
      reservations.every(element =>
        Number(element.dataset.universeFontReservationWidth) === expectedWidth);
  }, width, { timeout: 15000 });
}

async function runAtWidth(width, openDuringLoading) {
  const page = await browser.newPage({
    viewport: { width, height: 900 },
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  let releaseFontRequests;
  let fontGateOpen = false;
  let heldFontRequests = 0;
  const fontGate = new Promise(resolve => { releaseFontRequests = resolve; });
  const fontResponses = [];
  page.on("response", response => {
    if (response.request().resourceType() === "font") fontResponses.push(response.status());
  });
  await page.route("**/*", async route => {
    if (route.request().resourceType() === "font") {
      heldFontRequests += 1;
      if (!fontGateOpen) await fontGate;
    }
    await route.continue();
  });

  try {
    await page.goto(new URL("/universe/", base).href, { waitUntil: "domcontentloaded" });
    await page.locator(".askjamie-hero-visual .mermaid svg").waitFor({ timeout: 20000 });
    await page.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const firstGroup = page.locator(".universe-map-group").first();
    if (!openDuringLoading) await firstGroup.locator("summary").click();

    await page.waitForFunction(() => document.readyState === "complete", null,
      { timeout: 15000 });
    await page.waitForFunction(() =>
      [...document.querySelectorAll("link[data-deferred-fonts]")]
        .some(link => link.media === "all"), null, { timeout: 15000 });
    await page.waitForFunction(() => document.fonts.status === "loading", null,
      { timeout: 15000 });
    assert.ok(heldFontRequests > 0, `Universe ${width}px: no font request was held`);
    assert.equal(fontResponses.length, 0,
      `Universe ${width}px: a font response arrived before the fallback measurement`);
    const themeEvidence = await page.evaluate(() => ({
      loaded: [...document.querySelectorAll("link[data-deferred-styles]")]
        .every(link => Boolean(link.sheet?.cssRules.length)),
      headingFamily: getComputedStyle(document.querySelector(".askjamie-main"))
        .getPropertyValue("--font-heading").trim(),
    }));
    assert.equal(themeEvidence.loaded, true, "Full theme must load before fallback measurement");
    assert.match(themeEvidence.headingFamily, /Baloo 2/i,
      "Fallback measurement must use the active theme's typography rules");
    await waitForReservations(page, width);
    const inlineSizeTargets = await page.locator(
      '[data-universe-font-reservation="true"][style*="min-inline-size"]'
    ).evaluateAll(elements => elements.map(element => element.className).sort());
    assert.deepEqual(inlineSizeTargets,
      ["btn btn-primary", "btn btn-quiet", "site-specials-label", "site-specials-link"],
      "Inline-size minima must be limited to the text boxes that failed the 1px geometry gate");
    if (openDuringLoading) {
      await page.evaluate(() => {
        const links = [...document.querySelectorAll("link[data-deferred-fonts]")];
        const prior = [...document.querySelectorAll(
          '[data-universe-font-reservation="true"]'
        )].map(element => ({
          element,
          blockSize: element.style.minBlockSize,
          width: element.dataset.universeFontReservationWidth,
        }));
        window.__fontMediaChanges = 0;
        const observer = new MutationObserver(records => {
          window.__fontMediaChanges += records.filter(record =>
            record.attributeName === "media").length;
        });
        links.forEach(link => observer.observe(link, {
          attributes: true,
          attributeFilter: ["media"],
        }));
        window.__universeOpenSnapshot = { prior, links, observer };
      });
    }
    const beforeHeightResize = await page.evaluate(() => [...document.querySelectorAll(
      '[data-universe-font-reservation="true"]'
    )].map(element => ({
      text: element.textContent.trim().replace(/\s+/g, " ").slice(0, 60),
      blockSize: element.style.minBlockSize,
      width: element.dataset.universeFontReservationWidth,
    })));
    const mediaBeforeHeightResize = await page.locator("link[data-deferred-fonts]")
      .evaluateAll(links => links.map(link => link.media));
    await page.setViewportSize({ width, height: 960 });
    await page.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const afterHeightResize = await page.evaluate(() => [...document.querySelectorAll(
      '[data-universe-font-reservation="true"]'
    )].map(element => ({
      text: element.textContent.trim().replace(/\s+/g, " ").slice(0, 60),
      blockSize: element.style.minBlockSize,
      width: element.dataset.universeFontReservationWidth,
    })));
    assert.deepEqual(afterHeightResize, beforeHeightResize,
      `Universe ${width}px: height-only resize must retain width-owned reservations`);
    assert.deepEqual(await page.locator("link[data-deferred-fonts]")
      .evaluateAll(links => links.map(link => link.media)), mediaBeforeHeightResize,
    `Universe ${width}px: height-only resize must not toggle font CSS`);

    if (openDuringLoading) {
      await firstGroup.locator("summary").click();
      await page.waitForFunction(() =>
        [...document.querySelectorAll(".universe-map-group[open] li")]
          .every(element => element.dataset.universeFontReservation === "true"));
      const preserved = await page.evaluate(() => {
        const { prior, links, observer } = window.__universeOpenSnapshot;
        observer.disconnect();
        return {
          priorPreserved: prior.every(item => item.element.isConnected &&
            item.element.dataset.universeFontReservation === "true" &&
            item.element.style.minBlockSize === item.blockSize &&
            item.element.dataset.universeFontReservationWidth === item.width),
          fontMedia: links.map(link => link.media),
          mediaChanges: window.__fontMediaChanges,
          reservedOpenItems: [...document.querySelectorAll(".universe-map-group[open] li")]
            .every(element => element.dataset.universeFontReservation === "true"),
        };
      });
      assert.equal(preserved.priorPreserved, true,
        "Opening a details group must preserve existing fallback reservations");
      assert.ok(preserved.fontMedia.length > 0 &&
        preserved.fontMedia.every(media => media === "all"),
      "Opening a details group must leave font CSS active");
      assert.equal(preserved.mediaChanges, 0,
        "Opening a details group must not deactivate or reactivate font CSS");
      assert.equal(preserved.reservedOpenItems, true,
        "Newly visible list items must receive reservations while fonts are loading");
    }
    assert.equal(await firstGroup.evaluate(group => group.open), true,
      `Universe ${width}px: first group should remain open`);
    await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(".universe-map-group[open] li")];
      return items.length > 0 &&
        items.every(element => element.dataset.universeFontReservation === "true");
    });

    // Exercise width invalidation while font responses are held, then return
    // to the measured viewport before comparing fallback and loaded geometry.
    await page.evaluate(() => {
      window.__universeReservationCleared = false;
      const observer = new MutationObserver(records => {
        if (records.some(record =>
          record.attributeName === "style" ||
          record.attributeName === "data-universe-font-reservation")) {
          window.__universeReservationCleared = true;
        }
      });
      observer.observe(document.documentElement, {
        attributes: true,
        subtree: true,
        attributeFilter: ["style", "data-universe-font-reservation"],
      });
      window.__universeReservationObserver = observer;
    });
    await page.setViewportSize({ width: width + 12, height: 900 });
    await page.waitForFunction(expectedWidth => {
      const reservations = [...document.querySelectorAll(
        '[data-universe-font-reservation="true"]'
      )];
      return reservations.length > 0 && reservations.every(element =>
        Number(element.dataset.universeFontReservationWidth) === expectedWidth);
    }, width + 12);
    assert.ok(await page.evaluate(() =>
      document.documentElement.scrollWidth <= innerWidth + 1),
    `Universe ${width + 12}px: overflow after reservation invalidation`);
    assert.equal(await page.evaluate(() => window.__universeReservationCleared), true,
      "Viewport change must clear the previous inline reservations before remeasuring");
    await page.setViewportSize({ width, height: 900 });
    await waitForReservations(page, width);
    await page.evaluate(() => window.__universeReservationObserver.disconnect());
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const fallbackActionWidths = await page.locator(
      ".askjamie-hero-copy .hero-actions .btn"
    ).evaluateAll(elements => elements.map(element => {
      const saved = element.style.minInlineSize;
      element.style.minInlineSize = "0px";
      const natural = element.getBoundingClientRect().width;
      element.style.minInlineSize = saved;
      return natural;
    }));
    const before = await geometry(page);
    assert.equal(before.scroll.y, 0, "Fallback measurements must start at scrollY 0");
    assert.ok(before.blocks.some(block => block.selector === ".askjamie-hero-copy h1"));
    assert.ok(before.blocks.some(block => block.selector === ".universe-map-generated > p"));
    assert.ok(before.blocks.some(block => block.selector === ".universe-map-group[open] li"));

    fontGateOpen = true;
    releaseFontRequests();
    const fontEvidence = await page.evaluate(async selectorsToLoad => {
      const normalize = value => value.replace(/["']/g, "").trim().toLowerCase();
      const used = [];
      for (const selector of selectorsToLoad) {
        const element = document.querySelector(selector);
        if (!element || !element.getClientRects().length) continue;
        const style = getComputedStyle(element);
        const family = normalize(style.fontFamily.split(",")[0]);
        const faces = await document.fonts.load(
          `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,
          element.textContent || "AskJamie",
        );
        used.push({
          selector,
          family,
          requestedWeight: style.fontWeight,
          requestedStyle: style.fontStyle,
          faces: faces.map(face => ({
            family: normalize(face.family),
            status: face.status,
            weight: face.weight,
            style: face.style,
          })),
        });
      }
      await document.fonts.ready;
      return { status: document.fonts.status, used };
    }, selectors);
    assert.equal(fontEvidence.status, "loaded");
    for (const sample of fontEvidence.used) {
      assert.ok(sample.faces.length > 0 &&
        sample.faces.every(face => face.status === "loaded" && face.family === sample.family),
      `Missing matching loaded face: ${JSON.stringify(sample)}`);
    }
    await page.waitForFunction(() =>
      document.querySelectorAll('[data-universe-font-reservation="true"]').length > 0);
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const after = await geometry(page);
    assert.equal(after.scroll.y, 0, "Loaded-font measurements must end at scrollY 0");
    compareGeometry(before, after, `Universe ${width}px font release`);
    const actionInlineEvidence = await page.locator(
      ".askjamie-hero-copy .hero-actions .btn"
    ).evaluateAll(elements => elements.map(element => {
      const reservedWidth = element.style.minInlineSize;
      const guarded = element.getBoundingClientRect().width;
      element.style.minInlineSize = "0px";
      const natural = element.getBoundingClientRect().width;
      element.style.minInlineSize = reservedWidth;
      return { guarded, natural };
    }));
    if (width === 390) {
      assert.ok(actionInlineEvidence.some((sample, index) =>
        Math.abs(fallbackActionWidths[index] - sample.natural) > 1),
      "CTA inline minima must correspond to separately demonstrated width changes");
    }
    assert.ok(await page.locator(".askjamie-hero-copy .hero-actions .btn").evaluateAll(
      elements => elements.every(element =>
        element.scrollWidth <= element.clientWidth + 1 &&
        element.scrollHeight <= element.clientHeight + 1),
    ), `Universe ${width}px: hero action text must remain readable`);
    const bannerWidthEvidence = await page.evaluate(() => {
      const label = document.querySelector(".site-specials-label");
      const link = document.querySelector(".site-specials-link");
      const reservedWidth = label.style.minInlineSize;
      const sample = () => ({
        labelWidth: label.getBoundingClientRect().width,
        updateLinkX: link.getBoundingClientRect().x,
      });
      const reserved = sample();
      label.style.removeProperty("min-inline-size");
      const natural = sample();
      label.style.minInlineSize = reservedWidth;
      return { reserved, natural };
    });
    if (width === 1280) {
      assert.ok(Math.abs(bannerWidthEvidence.reserved.labelWidth -
        bannerWidthEvidence.natural.labelWidth) > 1,
      "Desktop banner label has a separate font-driven intrinsic-width change");
      assert.ok(Math.abs(bannerWidthEvidence.reserved.updateLinkX -
        bannerWidthEvidence.natural.updateLinkX) > 1,
      "The banner label width change must demonstrate movement of its adjacent link");
    }
    assert.ok(await page.evaluate(() =>
      document.documentElement.scrollWidth <= innerWidth + 1),
    `Universe ${width}px: horizontal overflow after font release`);
    assert.ok(fontResponses.some(status => status >= 200 && status < 300),
      `Universe ${width}px: no successful font response`);

    observations.push({
      width,
      openedDuringLoading: openDuringLoading,
      heldFontRequests,
      fallbackActionWidths,
      actionInlineEvidence,
      bannerWidthEvidence,
      geometry: { before, after },
      fonts: fontEvidence,
      fontResponseStatuses: fontResponses,
    });
  } finally {
    fontGateOpen = true;
    releaseFontRequests();
    await page.close();
  }
}

try {
  await runAtWidth(390, false);
  await runAtWidth(1280, true);
  console.log(JSON.stringify({
    result: "PASS",
    playwrightVersion,
    chromiumVersion: browser.version(),
    scope: "Universe 390px and 1280px; full-theme fallback reservations; opened details; held/released web fonts; width invalidation; 1px geometry threshold",
    observations,
  }));
} finally {
  await browser.close();
}

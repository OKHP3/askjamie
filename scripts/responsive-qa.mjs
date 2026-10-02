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
 *   - BrandGuard hero geometry stays stable when its deferred theme activates
 *   - BrandGuard hero geometry stays stable after its branded web fonts load
 *   - Universe diagram shell stays stable while its rendered SVG initializes
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
const LAZY_IMAGE_REQUEST_TIMEOUT_MS = 5000;
const LAZY_IMAGE_LATE_REQUEST_GRACE_MS = 1500;

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
const BRANDGUARD_GEOMETRY_PATH = '/lens-system/okhp3-brandguard/';
const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set([
  'mobile-360',
  'mobile-390',
  'mobile-430',
]);
const BRANDGUARD_FONT_GEOMETRY_VIEWPORT = 'mobile-390';
const BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1;
const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000;
const BRANDGUARD_GEOMETRY_SELECTORS = [
  '.askjamie-brandguard-page .askjamie-breadcrumb',
  '.askjamie-brandguard-page .askjamie-hero-copy h1',
  '.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle',
  '.askjamie-brandguard-page .askjamie-hero-copy .hero-tagline',
];
const BRANDGUARD_FONT_HOSTS = new Set(['fonts.googleapis.com', 'fonts.gstatic.com']);
const BRANDGUARD_FONT_FAMILIES = ['Kalam', 'Baloo 2', 'Kalam', 'Open Sans'];
const BRANDGUARD_LOGO_SELECTOR =
  '.askjamie-brandguard-page .askjamie-logo--crumb img';
const UNIVERSE_DIAGRAM_GEOMETRY_PATH = '/universe/';
const UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS = new Set(['mobile-390', 'desktop-1280']);
const UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX = 1;
const UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS = [
  '.askjamie-hero--universe .mermaid-scroll-wrap',
  '.askjamie-hero--universe .askjamie-mermaid-shell',
];

async function checkBrandGuardHeroGeometry(page) {
  const errors = [];
  const capture = () => page.evaluate((selectors) => {
    const geometry = {};
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) {
        geometry[selector] = null;
        continue;
      }
      const rect = element.getBoundingClientRect();
      const round = value => Math.round(value * 100) / 100;
      geometry[selector] = Object.fromEntries(
        ['x', 'y', 'width', 'height', 'top', 'right', 'bottom', 'left']
          .map(property => [property, round(rect[property])])
      );
    }
    const themeLink = document.querySelector('link[data-deferred-styles]');
    return {
      themeMedia: themeLink?.media ?? null,
      themeHref: themeLink?.href ?? null,
      geometry,
    };
  }, BRANDGUARD_GEOMETRY_SELECTORS);

  const logoSettled = await page.waitForFunction((selector) => {
    const image = document.querySelector(selector);
    return !image || image.complete;
  }, BRANDGUARD_LOGO_SELECTOR, { timeout: 5000 }).then(() => true, () => false);
  const before = await capture();
  if (!logoSettled) {
    errors.push(
      `BRANDGUARD HERO GEOMETRY UNSTABLE: ${BRANDGUARD_LOGO_SELECTOR} did not finish loading; ` +
      `measured before=${JSON.stringify(before.geometry)}; after=not-measured`
    );
  }
  if (before.themeMedia !== 'not all') {
    errors.push(
      `BRANDGUARD HERO GEOMETRY BASELINE UNAVAILABLE: link[data-deferred-styles] ` +
      `media=${JSON.stringify(before.themeMedia)} before capture; ` +
      `measured before=${JSON.stringify(before.geometry)}; after=not-measured`
    );
    return {
      errors,
      evidence: {
        threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
        deferred_theme: { href: before.themeHref, media_before: before.themeMedia },
        before: before.geometry,
        after: null,
        shifts: [],
      },
    };
  }

  try {
    await page.waitForFunction(() => {
      const themeLink = document.querySelector('link[data-deferred-styles]');
      return themeLink?.media === 'all' && Boolean(themeLink.sheet);
    }, undefined, { timeout: 15000 });
    await page.evaluate(() =>
      new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ))
    );
  } catch {
    const after = await capture();
    errors.push(
      `BRANDGUARD HERO GEOMETRY THEME NOT ACTIVATED: link[data-deferred-styles] ` +
      `media=${JSON.stringify(after.themeMedia)} after 15000ms; ` +
      `measured before=${JSON.stringify(before.geometry)}; ` +
      `after=${JSON.stringify(after.geometry)}`
    );
    return {
      errors,
      evidence: {
        threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
        deferred_theme: {
          href: before.themeHref,
          media_before: before.themeMedia,
          media_after: after.themeMedia,
        },
        before: before.geometry,
        after: after.geometry,
        shifts: [],
      },
    };
  }

  const after = await capture();
  const shifts = [];
  for (const selector of BRANDGUARD_GEOMETRY_SELECTORS) {
    const beforeRect = before.geometry[selector];
    const afterRect = after.geometry[selector];
    if (!beforeRect || !afterRect) {
      errors.push(
        `BRANDGUARD HERO GEOMETRY MISSING: ${selector}; ` +
        `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
      );
      continue;
    }

    const delta = Object.fromEntries(
      ['x', 'y', 'width', 'height'].map(property => [
        property,
        Math.round((afterRect[property] - beforeRect[property]) * 100) / 100,
      ])
    );
    const changedProperties = Object.keys(delta).filter(
      property => Math.abs(delta[property]) > BRANDGUARD_GEOMETRY_TOLERANCE_PX
    );
    if (changedProperties.length > 0) {
      shifts.push({ selector, before: beforeRect, after: afterRect, delta, changed_properties: changedProperties });
      errors.push(
        `BRANDGUARD HERO GEOMETRY SHIFT: ${selector} changed ` +
        `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
        `with ${BRANDGUARD_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
        `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
      );
    }
  }

  return {
    errors,
    evidence: {
      threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
      deferred_theme: {
        href: before.themeHref,
        media_before: before.themeMedia,
        media_after: after.themeMedia,
      },
      before: before.geometry,
      after: after.geometry,
      shifts,
    },
  };
}

async function checkBrandGuardWebFontGeometry(page, releaseFontRequests, fontResponses) {
  const errors = [];
  const capture = () => page.evaluate((selectors) => {
    const geometry = {};
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) {
        geometry[selector] = null;
        continue;
      }
      const rect = element.getBoundingClientRect();
      const round = value => Math.round(value * 100) / 100;
      geometry[selector] = Object.fromEntries(
        ['x', 'y', 'width', 'height', 'top', 'right', 'bottom', 'left']
          .map(property => [property, round(rect[property])])
      );
    }
    return geometry;
  }, BRANDGUARD_GEOMETRY_SELECTORS);

  const before = await capture();
  releaseFontRequests();

  let fontLoading = null;
  try {
    await page.waitForFunction(() => {
      const fontLink = document.querySelector('link[data-deferred-fonts]');
      return fontLink?.media === 'all' && Boolean(fontLink.sheet);
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    fontLoading = await page.evaluate(async ({ selectors, expectedFamilies, timeoutMs }) => {
      const fontLink = document.querySelector('link[data-deferred-fonts]');
      if (fontLink?.media !== 'all' || !fontLink.sheet) {
        throw new Error(
          `link[data-deferred-fonts] was not active (media=${JSON.stringify(fontLink?.media ?? null)})`
        );
      }

      const loadFonts = Promise.all(selectors.map(async (selector, index) => {
        const element = document.querySelector(selector);
        if (!element) {
          return {
            selector,
            expected_family: expectedFamilies[index],
            loaded: false,
            reason: 'element missing',
          };
        }

        const style = getComputedStyle(element);
        const expectedFamily = expectedFamilies[index];
        const declaredFamily = style.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, '');
        const faces = await document.fonts.load(style.font, element.textContent || ' ');
        const matchingFaces = faces.filter(face =>
          face.family.replace(/^["']|["']$/g, '') === expectedFamily
        );
        return {
          selector,
          expected_family: expectedFamily,
          declared_family: declaredFamily,
          font: style.font,
          face_statuses: matchingFaces.map(face => face.status),
          loaded: declaredFamily === expectedFamily &&
            matchingFaces.length > 0 &&
            matchingFaces.every(face => face.status === 'loaded'),
        };
      }));

      let timeoutId;
      try {
        const fonts = await Promise.race([
          loadFonts,
          new Promise((_, reject) => {
            timeoutId = setTimeout(
              () => reject(new Error(`font loading exceeded ${timeoutMs}ms`)),
              timeoutMs
            );
          }),
        ]);
        await document.fonts.ready;
        await new Promise(resolve => requestAnimationFrame(() =>
          requestAnimationFrame(resolve)
        ));
        return {
          status: document.fonts.status,
          link_media: fontLink.media,
          fonts,
        };
      } finally {
        clearTimeout(timeoutId);
      }
    }, {
      selectors: BRANDGUARD_GEOMETRY_SELECTORS,
      expectedFamilies: BRANDGUARD_FONT_FAMILIES,
      timeoutMs: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
    });
  } catch (error) {
    errors.push(
      `BRANDGUARD WEB FONTS NOT READY: ${error.message.split('\n')[0]}; ` +
      `selectors=${JSON.stringify(BRANDGUARD_GEOMETRY_SELECTORS)}; ` +
      `before=${JSON.stringify(before)}; after=not-measured`
    );
  }

  const after = await capture();
  const hasStylesheet = fontResponses.some(
    response => response.resource_type === 'stylesheet' && response.status < 400
  );
  const hasFontAsset = fontResponses.some(
    response => response.resource_type === 'font' && response.status < 400
  );
  const failedFontChecks = fontLoading?.fonts.filter(font => !font.loaded) ?? [];
  if (!hasStylesheet || !hasFontAsset || failedFontChecks.length > 0) {
    errors.push(
      `BRANDGUARD WEB FONTS UNAVAILABLE: stylesheet_loaded=${hasStylesheet}; ` +
      `font_asset_loaded=${hasFontAsset}; failed_faces=${JSON.stringify(failedFontChecks)}; ` +
      `resources=${JSON.stringify(fontResponses)}; ` +
      `before=${JSON.stringify(before)}; after=${JSON.stringify(after)}`
    );
  }

  const shifts = [];
  for (const selector of BRANDGUARD_GEOMETRY_SELECTORS) {
    const beforeRect = before[selector];
    const afterRect = after[selector];
    if (!beforeRect || !afterRect) {
      errors.push(
        `BRANDGUARD WEB FONT GEOMETRY MISSING: ${selector}; ` +
        `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
      );
      continue;
    }

    const delta = Object.fromEntries(
      ['x', 'y', 'width', 'height'].map(property => [
        property,
        Math.round((afterRect[property] - beforeRect[property]) * 100) / 100,
      ])
    );
    const changedProperties = Object.keys(delta).filter(
      property => Math.abs(delta[property]) > BRANDGUARD_GEOMETRY_TOLERANCE_PX
    );
    if (changedProperties.length > 0) {
      shifts.push({ selector, before: beforeRect, after: afterRect, delta, changed_properties: changedProperties });
      errors.push(
        `BRANDGUARD WEB FONT GEOMETRY SHIFT: ${selector} changed ` +
        `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
        `with ${BRANDGUARD_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
        `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
      );
    }
  }

  return {
    errors,
    evidence: {
      threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
      timeout_ms: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
      before,
      after,
      font_loading: fontLoading,
      font_resources: fontResponses,
      shifts,
    },
  };
}

async function checkUniverseDiagramGeometry(page, releaseMermaid, mermaidRequestSeen) {
  const errors = [];
  let before = null;
  let after = null;
  let themeActive = false;
  let mermaidRequested = false;
  let rendered = false;

  const capture = () => page.evaluate((selectors) => {
    const round = value => Math.round(value * 100) / 100;
    const geometry = {};
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) {
        geometry[selector] = null;
        continue;
      }
      const rect = element.getBoundingClientRect();
      geometry[selector] = Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [
          property,
          round(rect[property] + (property === 'x' ? window.scrollX : property === 'y' ? window.scrollY : 0)),
        ])
      );
    }

    const shell = document.querySelector(
      '.askjamie-hero--universe .mermaid-scroll-wrap'
    );
    const diagram = document.querySelector(
      '.askjamie-hero--universe .mermaid'
    );
    const figure = document.querySelector(
      '.askjamie-hero--universe .askjamie-mermaid-shell'
    );
    return {
      ready: diagram?.dataset.universeReady === '1',
      has_svg_node: Boolean(diagram?.querySelector('svg .node')),
      source_present: diagram?.textContent.includes('flowchart') ?? false,
      visibility: diagram ? getComputedStyle(diagram).visibility : null,
      shell_aria_hidden: shell?.getAttribute('aria-hidden') ?? null,
      shell_min_height: shell ? round(parseFloat(getComputedStyle(shell).minHeight)) : null,
      diagram_geometry: diagram ? (() => {
        const rect = diagram.getBoundingClientRect();
        return {
          x: round(rect.x + window.scrollX),
          y: round(rect.y + window.scrollY),
          width: round(rect.width),
          height: round(rect.height),
        };
      })() : null,
      accessible_fallback: {
        caption: figure?.querySelector('figcaption')?.textContent.trim() ?? '',
        visible_links: [...(figure?.querySelectorAll('.link-list a') ?? [])]
          .filter(link => link.getClientRects().length > 0).length,
      },
      geometry,
    };
  }, UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS);

  const waitForTwoFrames = () => page.evaluate(() =>
    new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    ))
  );

  try {
    await page.locator(UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS[0]).scrollIntoViewIfNeeded();

    [themeActive, mermaidRequested] = await Promise.all([
      page.waitForFunction(() => {
        const themeLink = document.querySelector('link[data-deferred-styles]');
        return themeLink?.media === 'all' && Boolean(themeLink.sheet);
      }, undefined, { timeout: 15000 }).then(() => true, () => false),
      mermaidRequestSeen,
    ]);

    if (themeActive) {
      await waitForTwoFrames();
      // The desktop hero has a finite scroll reveal that changes its transform.
      // Let that existing animation finish before isolating Mermaid's layout.
      // Keep the Mermaid import blocked throughout this baseline preparation.
      await page.evaluate(async () => {
        const hero = document.querySelector('.askjamie-hero--universe');
        const reveals = (hero?.getAnimations({ subtree: true }) ?? [])
          .filter(animation => animation.animationName === 'scroll-reveal-in');
        await Promise.all(reveals.map(animation => animation.finished.catch(() => {})));
      });
      await waitForTwoFrames();
    }
    before = await capture();
    if (!themeActive) {
      errors.push(
        'UNIVERSE DIAGRAM GEOMETRY BASELINE UNAVAILABLE: deferred theme did not activate; ' +
        `measured before=${JSON.stringify(before.geometry)}`
      );
    }
    if (!mermaidRequested) {
      errors.push(
        'UNIVERSE DIAGRAM NOT INITIALIZED: Mermaid module request was not observed after bringing the hero into view'
      );
    }
    if (before.ready || before.has_svg_node) {
      errors.push(
        'UNIVERSE DIAGRAM BASELINE UNAVAILABLE: SVG rendered before the reserved-shell measurement; ' +
        `before=${JSON.stringify(before)}`
      );
    }
    if (before.visibility !== 'hidden' || !before.source_present) {
      errors.push(
        'UNIVERSE DIAGRAM SOURCE FALLBACK CHANGED: expected hidden Mermaid source before SVG render; ' +
        `before=${JSON.stringify({ visibility: before.visibility, source_present: before.source_present })}`
      );
    }
    if (before.shell_aria_hidden !== 'true' ||
        !before.accessible_fallback.caption ||
        before.accessible_fallback.visible_links < 3) {
      errors.push(
        'UNIVERSE DIAGRAM ACCESSIBLE FALLBACK CHANGED: expected an aria-hidden visual diagram, ' +
        'a figure caption, and three visible page links; ' +
        `fallback=${JSON.stringify({ aria_hidden: before.shell_aria_hidden, ...before.accessible_fallback })}`
      );
    }

    const expectedReservation = (page.viewportSize().width <= 640 ? 18 : 22) * 16;
    if (before.geometry[UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS[0]]?.height + UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX < expectedReservation) {
      errors.push(
        `UNIVERSE DIAGRAM RESERVATION TOO SMALL: expected at least ${expectedReservation}px ` +
        `before rendering; measured=${JSON.stringify(before.geometry[UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS[0]])}`
      );
    }

    releaseMermaid();
    if (mermaidRequested) {
      rendered = await page.waitForFunction(() => {
        const diagram = document.querySelector('.askjamie-hero--universe .mermaid');
        return diagram?.dataset.universeReady === '1' && Boolean(diagram.querySelector('svg .node'));
      }, undefined, { timeout: 15000 }).then(() => true, () => false);
      if (rendered) {
        await waitForTwoFrames();
        after = await capture();
      } else {
        after = await capture();
        errors.push(
          'UNIVERSE DIAGRAM DID NOT REACH READY STATE: expected a rendered SVG node and data-universe-ready=1; ' +
          `before=${JSON.stringify(before.geometry)}; after=${JSON.stringify(after.geometry)}`
        );
      }
    }
  } catch (error) {
    errors.push(`UNIVERSE DIAGRAM GEOMETRY CHECK ERROR: ${error.message.split('\n')[0]}`);
  } finally {
    releaseMermaid();
  }

  const shifts = [];
  if (before && after && rendered) {
    for (const selector of UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS) {
      const beforeRect = before.geometry[selector];
      const afterRect = after.geometry[selector];
      if (!beforeRect || !afterRect) {
        errors.push(
          `UNIVERSE DIAGRAM GEOMETRY MISSING: ${selector}; ` +
          `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
        );
        continue;
      }

      const delta = Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [
          property,
          Math.round((afterRect[property] - beforeRect[property]) * 100) / 100,
        ])
      );
      const changedProperties = Object.keys(delta).filter(
        property => Math.abs(delta[property]) > UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX
      );
      if (changedProperties.length > 0) {
        shifts.push({ selector, before: beforeRect, after: afterRect, delta, changed_properties: changedProperties });
        errors.push(
          `UNIVERSE DIAGRAM GEOMETRY SHIFT: ${selector} changed ` +
          `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
          `with ${UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
          `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
        );
      }
    }

    if (after.diagram_geometry &&
        before.geometry[UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS[0]].height + UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX < after.diagram_geometry.height) {
      errors.push(
        'UNIVERSE DIAGRAM CONTENT EXCEEDS RESERVED SHELL: ' +
        `reserved=${JSON.stringify(before.geometry[UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS[0]])}; ` +
        `rendered=${JSON.stringify(after.diagram_geometry)}`
      );
    }
    if (after.visibility !== 'visible' || !after.ready || !after.has_svg_node) {
      errors.push(
        'UNIVERSE DIAGRAM READY STATE INVALID: expected the rendered SVG to be visible and marked ready; ' +
        `after=${JSON.stringify({ ready: after.ready, has_svg_node: after.has_svg_node, visibility: after.visibility })}`
      );
    }
  }

  return {
    errors,
    evidence: {
      threshold_px: UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX,
      theme_active_before_render: themeActive,
      mermaid_module_requested: mermaidRequested,
      rendered_svg_ready: rendered,
      expected_reservation_px: (page.viewportSize().width <= 640 ? 18 : 22) * 16,
      before,
      after,
      shifts,
    },
  };
}

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
    const checkBrandGuardFonts =
      path === BRANDGUARD_GEOMETRY_PATH && vp.name === BRANDGUARD_FONT_GEOMETRY_VIEWPORT;
    const checkUniverseDiagram =
      path === UNIVERSE_DIAGRAM_GEOMETRY_PATH &&
      UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS.has(vp.name);
    let releaseBrandGuardFontRequests = () => {};
    let brandGuardFontGate = Promise.resolve();
    if (checkBrandGuardFonts) {
      let release;
      brandGuardFontGate = new Promise(resolve => { release = resolve; });
      let released = false;
      releaseBrandGuardFontRequests = () => {
        if (released) return;
        released = true;
        release();
      };
    }
    let releaseUniverseMermaid;
    let universeMermaidImportGate = Promise.resolve();
    let universeMermaidRequestSeen = Promise.resolve(false);
    if (checkUniverseDiagram) {
      let releaseImport;
      universeMermaidImportGate = new Promise(resolve => { releaseImport = resolve; });
      let released = false;
      releaseUniverseMermaid = () => {
        if (released) return;
        released = true;
        releaseImport();
      };
      universeMermaidRequestSeen = page.waitForRequest(
        request => new URL(request.url()).pathname === '/assets/vendor/mermaid/mermaid.esm.min.mjs',
        { timeout: 15000 }
      ).then(() => true, () => false);
    }
    const blockedExternal = new Set();
    const consoleErrors = [];
    const requestFailures = [];
    const failedResponses = [];
    const brandGuardFontResponses = [];
    const requestInfo = new WeakMap();
    const requestedUrls = new Set();
    const requestedAtByUrl = new Map();
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
      if (!requestedAtByUrl.has(req.url())) {
        requestedAtByUrl.set(req.url(), Date.now());
      }
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
      const responseUrl = new URL(resp.url());
      if (checkBrandGuardFonts && BRANDGUARD_FONT_HOSTS.has(responseUrl.hostname)) {
        brandGuardFontResponses.push({
          host: responseUrl.hostname,
          path: responseUrl.pathname,
          resource_type: resp.request().resourceType(),
          status: resp.status(),
        });
      }
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
      const requestUrl = new URL(route.request().url());
      if (checkBrandGuardFonts && BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname)) {
        const requestType = route.request().resourceType();
        if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.continue();
        }
        if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.continue());
        }
        blockedExternal.add(route.request().url());
        return route.abort();
      }
      if (checkUniverseDiagram &&
          requestUrl.pathname === '/assets/vendor/mermaid/mermaid.esm.min.mjs') {
        return universeMermaidImportGate.then(() => route.continue());
      }
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

      // Compare the critical shell with the live deferred theme on the supported
      // phone BrandGuard viewports. Do this before scrolling lazy images so the
      // two geometry samples cover only theme activation, not later page work.
      const heroThemeGeometry =
        path === BRANDGUARD_GEOMETRY_PATH && BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name)
          ? await checkBrandGuardHeroGeometry(page)
          : null;
      const heroFontGeometry = checkBrandGuardFonts
        ? await checkBrandGuardWebFontGeometry(
          page,
          releaseBrandGuardFontRequests,
          brandGuardFontResponses
        )
        : null;
      const universeDiagramGeometry = checkUniverseDiagram
        ? await checkUniverseDiagramGeometry(
          page,
          releaseUniverseMermaid,
          universeMermaidRequestSeen
        )
        : null;

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
        .map(async imageUrl => {
          const observationStartedAt = Date.now();
          const observed = await page.waitForRequest(
            request => request.url() === imageUrl,
            { timeout: LAZY_IMAGE_REQUEST_TIMEOUT_MS }
          ).then(() => true, () => false);
          if (!observed) {
            const observedLate = requestedAtByUrl.has(imageUrl) || await page.waitForRequest(
              request => request.url() === imageUrl,
              { timeout: LAZY_IMAGE_LATE_REQUEST_GRACE_MS }
            ).then(() => true, () => false) || requestedAtByUrl.has(imageUrl);
            if (observedLate) {
              const requestedAt = requestedAtByUrl.get(imageUrl) ?? Date.now();
              warnings.push(
                `lazy image request observed too late: started about ` +
                `${requestedAt - observationStartedAt}ms after observation began; ` +
                `expected within ${LAZY_IMAGE_REQUEST_TIMEOUT_MS}ms ` +
                `(late-start grace ${LAZY_IMAGE_LATE_REQUEST_GRACE_MS}ms): ` +
                `route ${url}; image ${imageUrl}`
              );
            } else {
              warnings.push(
                `lazy image request was never triggered during the ` +
                `${LAZY_IMAGE_REQUEST_TIMEOUT_MS + LAZY_IMAGE_LATE_REQUEST_GRACE_MS}ms ` +
                `observation window (${LAZY_IMAGE_REQUEST_TIMEOUT_MS}ms deadline + ` +
                `${LAZY_IMAGE_LATE_REQUEST_GRACE_MS}ms late-start grace): ` +
                `route ${url}; image ${imageUrl}`
              );
            }
          }
        });
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
        ...(heroThemeGeometry?.errors ?? []),
        ...(heroFontGeometry?.errors ?? []),
        ...(universeDiagramGeometry?.errors ?? []),
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
                    mode: 'playwright', pass: errors.length === 0, errors, warnings,
                    ...(heroThemeGeometry
                      ? { hero_theme_geometry: heroThemeGeometry.evidence }
                      : {}),
                     ...(heroFontGeometry
                      ? { hero_font_geometry: heroFontGeometry.evidence }
                      : {}),
                    ...(universeDiagramGeometry
                      ? { universe_diagram_geometry: universeDiagramGeometry.evidence }
                      : {}) };
      if (!row.pass) {
        const ssFile = `${path.replace(/\//g, '_')}_${vp.name}.png`;
        await page.screenshot({ path: resolve(SCREENSHOTS_DIR, ssFile) });
      }
      return row;
    } finally {
      releaseBrandGuardFontRequests();
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
      'BrandGuard hero geometry across deferred theme activation is checked only in Playwright mode at mobile-360, mobile-390, and mobile-430.',
      'BrandGuard hero geometry after deferred web fonts load is checked only in Playwright mode at mobile-390.',
      'Universe diagram shell geometry through Mermaid rendering is checked only in Playwright mode at mobile-390 and desktop-1280.',
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

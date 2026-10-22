#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

export const PRODUCTION_ORIGIN = 'https://askjamie.bot';
export const HERO_ROUTES = [
  {
    path: '/lens-system/okhp3-brandguard/',
    selector: '.askjamie-brandguard-page .askjamie-hero-copy h1',
    styleKey: 'brandguard_heading',
  },
  {
    path: '/universe/',
    selector: '.askjamie-hero--universe .mermaid-scroll-wrap',
    styleKey: 'universe_diagram_wrapper',
  },
];
const CRITICAL_STYLESHEET_PATH = '/assets/css/critical-hero.css';
export const VIEWPORTS = [
  { name: 'narrow-phone', width: 320, height: 844 },
  { name: 'standard-phone', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1440, height: 1000 },
];

function closeEnough(actual, expected) {
  return Number.isFinite(Number.parseFloat(actual)) &&
    Math.abs(Number.parseFloat(actual) - Number.parseFloat(expected)) < 0.05;
}

function matchesExpectedStyle(actual, expected) {
  return Number.isFinite(Number.parseFloat(expected))
    ? closeEnough(actual, expected)
    : actual === expected;
}

function stylesheetIntegrity(stylesheet) {
  return {
    expected_bytes: stylesheet.length,
    expected_sha256: createHash('sha256').update(stylesheet).digest('hex'),
  };
}

function expectedStylesheetReport(integrity) {
  return {
    artifact_name: process.env.SOURCE_REVISION
      ? `pages-site-${process.env.SOURCE_REVISION}`
      : null,
    artifact_path: 'assets/css/critical-hero.css',
    ...integrity,
  };
}

function buildReport({
  origin,
  expectedStylesheetIntegrity,
  routes,
  fatalError = null,
  chromiumVersion = null,
}) {
  return {
    checked_at: new Date().toISOString(),
    origin,
    chromium_version: chromiumVersion,
    source_revision: process.env.SOURCE_REVISION ?? null,
    deployment_run_url: process.env.DEPLOYMENT_RUN_URL ?? null,
    expected_stylesheet: expectedStylesheetReport(expectedStylesheetIntegrity),
    viewports: VIEWPORTS,
    routes,
    outcome: routes.length === HERO_ROUTES.length && routes.every((route) => route.passed)
      ? 'PASS'
      : 'FAIL',
    ...(fatalError ? { fatal_error: fatalError } : {}),
  };
}

export async function runDeployedHeroCheck({
  base = PRODUCTION_ORIGIN,
  expectedStylesheet,
  launch = (options) => chromium.launch(options),
} = {}) {
  if (!Buffer.isBuffer(expectedStylesheet)) {
    throw new TypeError('Expected critical stylesheet bytes from the validated release artifact.');
  }
  const origin = new URL(base).origin;
  const expectedStylesheetIntegrity = stylesheetIntegrity(expectedStylesheet);
  let browser;
  let chromiumVersion = null;
  const routes = [];
  try {
    browser = await launch({ headless: true });
    chromiumVersion = typeof browser.version === 'function' ? browser.version() : null;
  } catch (error) {
    const reason = `Browser could not start: ${error.message}`;
    for (const route of HERO_ROUTES) {
      const row = {
        path: route.path,
        url: new URL(route.path, base).href,
        measurements: VIEWPORTS.map((viewport) => ({
          viewport: { ...viewport },
          status: 'NOT_MEASURED',
          measured: false,
          stylesheet_response: null,
          computed: null,
          console_errors: [],
          page_errors: [],
          local_request_failures: [],
          local_bad_responses: [],
          errors: [reason],
          passed: false,
        })),
        errors: VIEWPORTS.map((viewport) =>
          `${viewport.name} (${viewport.width}px): ${reason}`),
        status: 'NOT_MEASURED',
        passed: false,
      };
      routes.push(row);
    }
    return buildReport({
      origin,
      expectedStylesheetIntegrity,
      routes,
      fatalError: error.stack ?? error.message,
      chromiumVersion,
    });
  }

  for (const route of HERO_ROUTES) {
    routes.push({
      path: route.path,
      url: new URL(route.path, base).href,
      measurements: [],
      errors: [],
    });
  }

  let fatalError = null;
  try {
    for (const [routeIndex, route] of HERO_ROUTES.entries()) {
      const row = routes[routeIndex];
      for (const viewport of VIEWPORTS) {
        const page = await browser.newPage({
          viewport: { width: viewport.width, height: viewport.height },
        });
        const measurement = {
          viewport: { ...viewport },
          stylesheet_response: null,
          computed: null,
          console_errors: [],
          page_errors: [],
          local_request_failures: [],
          local_bad_responses: [],
          errors: [],
        };
        const stylesheetResponses = [];

        page.on('console', (message) => {
          if (message.type() === 'error') {
            measurement.console_errors.push(message.text());
          }
        });
        page.on('pageerror', (error) => measurement.page_errors.push(error.message));
        page.on('requestfailed', (request) => {
          if (new URL(request.url()).origin === origin) {
            measurement.local_request_failures.push({
              url: request.url(),
              error: request.failure()?.errorText ?? 'request failed',
            });
          }
        });
        page.on('response', (response) => {
          const url = new URL(response.url());
          if (url.origin !== origin) return;
          if (url.pathname === CRITICAL_STYLESHEET_PATH) {
            stylesheetResponses.push({
              url: response.url(),
              status: response.status(),
              content_type: response.headers()['content-type'] ?? null,
              body: response.body().catch((error) => ({ error: error.message })),
            });
          }
          if (response.status() >= 400) {
            measurement.local_bad_responses.push({
              url: response.url(),
              status: response.status(),
            });
          }
        });

        try {
          await page.goto(row.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
          await page.waitForTimeout(300);
          measurement.computed = await page.evaluate(({ route, stylesheetPath, width }) => {
            const link = [...document.querySelectorAll('link[rel~="stylesheet"]')]
              .find((candidate) => new URL(candidate.href).pathname === stylesheetPath);
            const rootFontSize = Number.parseFloat(
              getComputedStyle(document.documentElement).fontSize,
            );
            const target = document.querySelector(route.selector);
            const style = target ? getComputedStyle(target) : null;
            const details = {
              selector: route.selector,
              present: Boolean(target),
              stylesheet_href: link?.href ?? null,
              stylesheet_attached: Boolean(link?.sheet),
              root_font_size_px: rootFontSize,
            };

            if (route.styleKey === 'brandguard_heading') {
              const compactHeading = width <= 899;
              const tracking = width >= 381 && width <= 400
                ? -0.03
                : width >= 350 && width <= 370
                  ? -0.08
                  : compactHeading
                    ? -0.02
                    : null;
              const fontSize = compactHeading
                ? rootFontSize * 2
                : Math.min(Math.max(width * 0.04, rootFontSize * 2.2), rootFontSize * 2.8);
              details.font_size = style?.fontSize ?? null;
              details.letter_spacing = style?.letterSpacing ?? null;
              details.expected_font_size = `${fontSize}px`;
              details.expected_letter_spacing = tracking === null
                ? 'normal'
                : `${rootFontSize * 2 * tracking}px`;
            } else {
              const minHeightRem = width <= 640 ? 18 : 22;
              details.min_height = style?.minHeight ?? null;
              details.overflow = style?.overflow ?? null;
              details.expected_min_height = `${rootFontSize * minHeightRem}px`;
            }
            return details;
          }, { route, stylesheetPath: CRITICAL_STYLESHEET_PATH, width: viewport.width });
        } catch (error) {
          measurement.errors.push(`Navigation/evaluation failed: ${error.message}`);
        }

        const stylesheetResponse = stylesheetResponses.at(-1) ?? null;
        measurement.stylesheet_response = stylesheetResponse
          ? {
            url: stylesheetResponse.url,
            status: stylesheetResponse.status,
            content_type: stylesheetResponse.content_type,
          }
          : null;
        if (stylesheetResponse) {
          const responseBody = await stylesheetResponse.body;
          const observedSha256 = Buffer.isBuffer(responseBody)
            ? createHash('sha256').update(responseBody).digest('hex')
            : null;
          measurement.stylesheet_integrity = {
            ...expectedStylesheetIntegrity,
            observed_bytes: Buffer.isBuffer(responseBody) ? responseBody.length : null,
            observed_sha256: observedSha256,
            matches: observedSha256 === null
              ? null
              : observedSha256 === expectedStylesheetIntegrity.expected_sha256,
          };
          if (measurement.stylesheet_integrity.matches === false) {
            measurement.errors.push(
              'Critical stylesheet byte/hash mismatch: expected ' +
              `${expectedStylesheetIntegrity.expected_bytes} bytes ` +
              `(${expectedStylesheetIntegrity.expected_sha256}), received ` +
              `${measurement.stylesheet_integrity.observed_bytes} bytes ` +
              `(${measurement.stylesheet_integrity.observed_sha256}).`,
            );
          } else if (measurement.stylesheet_integrity.matches === null) {
            measurement.errors.push(
              `Could not read critical stylesheet response body: ${responseBody.error}`,
            );
          }
        } else {
          measurement.stylesheet_integrity = {
            ...expectedStylesheetIntegrity,
            observed_bytes: null,
            observed_sha256: null,
            matches: null,
          };
        }
        const computed = measurement.computed;
        if (!computed?.stylesheet_href) {
          measurement.errors.push(`Critical stylesheet link missing: ${CRITICAL_STYLESHEET_PATH}`);
        } else if (new URL(computed.stylesheet_href).origin !== origin) {
          measurement.errors.push(`Critical stylesheet is not local: ${computed.stylesheet_href}`);
        }
        if (!computed?.stylesheet_attached) {
          measurement.errors.push('Critical stylesheet was not attached to the document.');
        }
        if (!measurement.stylesheet_response) {
          measurement.errors.push(`No response observed for ${CRITICAL_STYLESHEET_PATH}.`);
        } else {
          if (measurement.stylesheet_response.status !== 200) {
            measurement.errors.push(
              `Critical stylesheet returned HTTP ${measurement.stylesheet_response.status}.`,
            );
          }
          if (!measurement.stylesheet_response.content_type?.toLowerCase().includes('text/css')) {
            measurement.errors.push(
              `Critical stylesheet has unexpected content type: ` +
              `${measurement.stylesheet_response.content_type}`,
            );
          }
        }

        if (computed?.present) {
          if (route.styleKey === 'brandguard_heading') {
            if (!matchesExpectedStyle(computed.font_size, computed.expected_font_size) ||
                !matchesExpectedStyle(computed.letter_spacing, computed.expected_letter_spacing)) {
              measurement.errors.push(
                `Computed BrandGuard hero style mismatch: font-size=${computed.font_size}, ` +
                `letter-spacing=${computed.letter_spacing}; expected ` +
                `${computed.expected_font_size} and ${computed.expected_letter_spacing}.`,
              );
            }
          } else if (!closeEnough(computed.min_height, computed.expected_min_height) ||
                     computed.overflow !== 'hidden') {
            measurement.errors.push(
              `Computed Universe hero style mismatch: min-height=${computed.min_height}, ` +
              `overflow=${computed.overflow}; expected ${computed.expected_min_height} and hidden.`,
            );
          }
        } else if (computed) {
          measurement.errors.push(`Hero element not found: ${route.selector}`);
        }

        for (const message of measurement.console_errors) {
          measurement.errors.push(`Browser console error: ${message}`);
        }
        for (const message of measurement.page_errors) {
          measurement.errors.push(`Browser page error: ${message}`);
        }
        for (const failure of measurement.local_request_failures) {
          measurement.errors.push(`Local request failed: ${failure.url} (${failure.error})`);
        }
        for (const response of measurement.local_bad_responses) {
          measurement.errors.push(`Local request returned HTTP ${response.status}: ${response.url}`);
        }
        measurement.passed = measurement.errors.length === 0;
        row.measurements.push(measurement);
        await page.close();
      }

      row.errors = row.measurements.flatMap((measurement) =>
        measurement.errors.map((error) =>
          `${measurement.viewport.name} (${measurement.viewport.width}px): ${error}`));
      row.passed = row.measurements.length === VIEWPORTS.length &&
        row.measurements.every((measurement) => measurement.passed);
    }
  } catch (error) {
    fatalError = error.stack ?? error.message;
  }

  try {
    await browser.close();
  } catch (error) {
    fatalError ??= error.stack ?? error.message;
  }

  if (fatalError) {
    const reason = `Browser check stopped: ${fatalError.split('\n', 1)[0]}`;
    for (const [routeIndex, route] of HERO_ROUTES.entries()) {
      const row = routes[routeIndex];
      const completedViewports = new Set(
        row.measurements.map((measurement) => measurement.viewport.name),
      );
      for (const viewport of VIEWPORTS) {
        if (completedViewports.has(viewport.name)) continue;
        row.measurements.push({
          viewport: { ...viewport },
          status: 'NOT_MEASURED',
          measured: false,
          stylesheet_response: null,
          stylesheet_integrity: null,
          computed: null,
          console_errors: [],
          page_errors: [],
          local_request_failures: [],
          local_bad_responses: [],
          errors: [reason],
          passed: false,
        });
      }
    }
  }

  for (const row of routes) {
    row.errors = row.measurements.flatMap((measurement) =>
      measurement.errors.map((error) =>
        `${measurement.viewport.name} (${measurement.viewport.width}px): ${error}`));
    row.passed = row.measurements.length === VIEWPORTS.length &&
      row.measurements.every((measurement) => measurement.passed);
  }

  return buildReport({
    origin,
    expectedStylesheetIntegrity,
    routes,
    fatalError,
    chromiumVersion,
  });
}

export function markdownReport(report) {
  const lines = [
    '# Post-deployment hero-style check',
    '',
    `- Result: **${report.outcome}**`,
    `- Checked (UTC): ${report.checked_at}`,
    `- Origin: ${report.origin}`,
    `- Chromium version: ${report.chromium_version ?? 'not available'}`,
    `- Deployment source revision: ${report.source_revision ?? 'not supplied'}`,
    `- Deployment run: ${report.deployment_run_url ?? 'not supplied'}`,
    `- Validated stylesheet: ${JSON.stringify(report.expected_stylesheet)}`,
    `- Viewports: ${report.viewports.map(({ name, width, height }) =>
      `${name} (${width} × ${height})`).join(', ')}`,
    '',
  ];
  if (report.fatal_error) {
    lines.push('## Fatal check error', '', '```text', report.fatal_error, '```', '');
  }
  for (const route of report.routes) {
    const routeStatus = route.status ?? (route.passed ? 'PASS' : 'FAIL');
    lines.push(`## ${route.path}`, '', `- Result: ${routeStatus}`);
    for (const measurement of route.measurements) {
      const measurementStatus = measurement.status ??
        (measurement.passed ? 'PASS' : 'FAIL');
      lines.push(
        '',
        `### ${measurement.viewport.name}: ${measurement.viewport.width} × ` +
        `${measurement.viewport.height}: ${measurementStatus}`,
        `- Measured: ${measurement.measured ?? 'yes'}`,
        `- Critical stylesheet: ${JSON.stringify(measurement.stylesheet_response)}`,
        `- Critical stylesheet integrity: ${JSON.stringify(measurement.stylesheet_integrity)}`,
        `- Computed hero styles: ${JSON.stringify(measurement.computed)}`,
      );
      for (const key of [
        'console_errors',
        'page_errors',
        'local_request_failures',
        'local_bad_responses',
        'errors',
      ]) {
        lines.push(`- ${key.replaceAll('_', ' ')}: ${JSON.stringify(measurement[key])}`);
      }
    }
    lines.push('');
  }
  return lines.join('\n');
}

export function writeReport(report, outputDir) {
  mkdirSync(outputDir, { recursive: true });
  writeFileSync(resolve(outputDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  const markdown = markdownReport(report);
  writeFileSync(resolve(outputDir, 'report.md'), `${markdown}\n`);
  return markdown;
}

async function main() {
  const outputArg = process.argv.find((arg) => arg.startsWith('--output='));
  const outputDir = resolve(outputArg?.slice('--output='.length) ??
    'artifacts/deployed-hero-style-check');
  const expectedCssArg = process.argv.find((arg) => arg.startsWith('--expected-css='));
  let report;
  let expectedStylesheetIntegrity = null;
  try {
    if (!expectedCssArg) {
      throw new Error('--expected-css=<path> is required for the validated release stylesheet.');
    }
    const expectedStylesheet = readFileSync(resolve(expectedCssArg.slice('--expected-css='.length)));
    expectedStylesheetIntegrity = stylesheetIntegrity(expectedStylesheet);
    report = await runDeployedHeroCheck({
      expectedStylesheet,
    });
  } catch (error) {
    report = {
      checked_at: new Date().toISOString(),
      origin: PRODUCTION_ORIGIN,
      chromium_version: null,
      source_revision: process.env.SOURCE_REVISION ?? null,
      deployment_run_url: process.env.DEPLOYMENT_RUN_URL ?? null,
      expected_stylesheet: expectedStylesheetIntegrity
        ? expectedStylesheetReport(expectedStylesheetIntegrity)
        : null,
      viewports: VIEWPORTS,
      routes: [],
      outcome: 'FAIL',
      fatal_error: error.stack ?? error.message,
    };
  }
  const markdown = writeReport(report, outputDir);
  console.log(markdown);
  if (report.outcome !== 'PASS') process.exitCode = 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  await main();
}

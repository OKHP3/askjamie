#!/usr/bin/env node

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
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
const VIEWPORT = { width: 390, height: 844 };

function closeEnough(actual, expected) {
  return Number.isFinite(Number.parseFloat(actual)) &&
    Math.abs(Number.parseFloat(actual) - Number.parseFloat(expected)) < 0.05;
}

export async function runDeployedHeroCheck({
  base = PRODUCTION_ORIGIN,
  launch = (options) => chromium.launch(options),
} = {}) {
  const origin = new URL(base).origin;
  const browser = await launch({ headless: true });
  const routes = [];

  try {
    for (const route of HERO_ROUTES) {
      const page = await browser.newPage({ viewport: VIEWPORT });
      const row = {
        path: route.path,
        url: new URL(route.path, base).href,
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
          row.console_errors.push(message.text());
        }
      });
      page.on('pageerror', (error) => row.page_errors.push(error.message));
      page.on('requestfailed', (request) => {
        if (new URL(request.url()).origin === origin) {
          row.local_request_failures.push({
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
          });
        }
        if (response.status() >= 400) {
          row.local_bad_responses.push({
            url: response.url(),
            status: response.status(),
          });
        }
      });

      try {
        await page.goto(row.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        row.computed = await page.evaluate(({ route, stylesheetPath, width }) => {
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
            details.font_size = style?.fontSize ?? null;
            details.letter_spacing = style?.letterSpacing ?? null;
            details.expected_font_size = `${rootFontSize * 2}px`;
            details.expected_letter_spacing = `${rootFontSize * 2 * -0.03}px`;
          } else {
            const minHeightRem = width <= 640 ? 18 : 22;
            details.min_height = style?.minHeight ?? null;
            details.overflow = style?.overflow ?? null;
            details.expected_min_height = `${rootFontSize * minHeightRem}px`;
          }
          return details;
        }, { route, stylesheetPath: CRITICAL_STYLESHEET_PATH, width: VIEWPORT.width });
      } catch (error) {
        row.errors.push(`Navigation/evaluation failed: ${error.message}`);
      }

      row.stylesheet_response = stylesheetResponses.at(-1) ?? null;
      const computed = row.computed;
      if (!computed?.stylesheet_href) {
        row.errors.push(`Critical stylesheet link missing: ${CRITICAL_STYLESHEET_PATH}`);
      } else if (new URL(computed.stylesheet_href).origin !== origin) {
        row.errors.push(`Critical stylesheet is not local: ${computed.stylesheet_href}`);
      }
      if (!computed?.stylesheet_attached) {
        row.errors.push('Critical stylesheet was not attached to the document.');
      }
      if (!row.stylesheet_response) {
        row.errors.push(`No response observed for ${CRITICAL_STYLESHEET_PATH}.`);
      } else {
        if (row.stylesheet_response.status !== 200) {
          row.errors.push(
            `Critical stylesheet returned HTTP ${row.stylesheet_response.status}.`,
          );
        }
        if (!row.stylesheet_response.content_type?.toLowerCase().includes('text/css')) {
          row.errors.push(
            `Critical stylesheet has unexpected content type: ${row.stylesheet_response.content_type}`,
          );
        }
      }

      if (computed?.present) {
        if (route.styleKey === 'brandguard_heading') {
          if (!closeEnough(computed.font_size, computed.expected_font_size) ||
              !closeEnough(computed.letter_spacing, computed.expected_letter_spacing)) {
            row.errors.push(
              `Computed BrandGuard hero style mismatch: font-size=${computed.font_size}, ` +
              `letter-spacing=${computed.letter_spacing}; expected ` +
              `${computed.expected_font_size} and ${computed.expected_letter_spacing}.`,
            );
          }
        } else if (!closeEnough(computed.min_height, computed.expected_min_height) ||
                   computed.overflow !== 'hidden') {
          row.errors.push(
            `Computed Universe hero style mismatch: min-height=${computed.min_height}, ` +
            `overflow=${computed.overflow}; expected ${computed.expected_min_height} and hidden.`,
          );
        }
      } else if (computed) {
        row.errors.push(`Hero element not found: ${route.selector}`);
      }

      for (const message of row.console_errors) {
        row.errors.push(`Browser console error: ${message}`);
      }
      for (const message of row.page_errors) {
        row.errors.push(`Browser page error: ${message}`);
      }
      for (const failure of row.local_request_failures) {
        row.errors.push(`Local request failed: ${failure.url} (${failure.error})`);
      }
      for (const response of row.local_bad_responses) {
        row.errors.push(`Local request returned HTTP ${response.status}: ${response.url}`);
      }
      row.passed = row.errors.length === 0;
      routes.push(row);
      await page.close();
    }
  } finally {
    await browser.close();
  }

  return {
    checked_at: new Date().toISOString(),
    origin,
    viewport: VIEWPORT,
    routes,
    outcome: routes.length === HERO_ROUTES.length && routes.every((route) => route.passed)
      ? 'PASS'
      : 'FAIL',
  };
}

function markdownReport(report) {
  const lines = [
    '# Post-deployment hero-style check',
    '',
    `- Result: **${report.outcome}**`,
    `- Checked (UTC): ${report.checked_at}`,
    `- Origin: ${report.origin}`,
    `- Viewport: ${report.viewport.width} × ${report.viewport.height}`,
    '',
  ];
  for (const route of report.routes) {
    lines.push(`## ${route.path}`, '', `- Result: ${route.passed ? 'PASS' : 'FAIL'}`);
    lines.push(`- Critical stylesheet: ${JSON.stringify(route.stylesheet_response)}`);
    lines.push(`- Computed hero styles: ${JSON.stringify(route.computed)}`);
    for (const key of [
      'console_errors',
      'page_errors',
      'local_request_failures',
      'local_bad_responses',
      'errors',
    ]) {
      lines.push(`- ${key.replaceAll('_', ' ')}: ${JSON.stringify(route[key])}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

async function main() {
  const outputArg = process.argv.find((arg) => arg.startsWith('--output='));
  const outputDir = resolve(outputArg?.slice('--output='.length) ??
    'artifacts/deployed-hero-style-check');
  let report;
  try {
    report = await runDeployedHeroCheck();
  } catch (error) {
    report = {
      checked_at: new Date().toISOString(),
      origin: PRODUCTION_ORIGIN,
      viewport: VIEWPORT,
      routes: [],
      outcome: 'FAIL',
      fatal_error: error.stack ?? error.message,
    };
  }
  mkdirSync(outputDir, { recursive: true });
  writeFileSync(resolve(outputDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  const markdown = markdownReport(report);
  writeFileSync(resolve(outputDir, 'report.md'), `${markdown}\n`);
  console.log(markdown);
  if (report.outcome !== 'PASS') process.exitCode = 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  await main();
}

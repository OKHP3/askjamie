#!/usr/bin/env node
/**
 * Run the same Lighthouse pass against the four public routes and compare the
 * compact results with the committed 2026-08-22 baseline.
 *
 * The static server is intentionally kept separate so this command can be
 * used against the Replit preview, a local server, or a hosted preview:
 *
 *   node scripts/lighthouse-routes.mjs
 *   node scripts/lighthouse-routes.mjs --preset=mobile
 *   node scripts/lighthouse-routes.mjs --preset=mobile --controlled
 *   node scripts/lighthouse-routes.mjs --preset=mobile --controlled --brandguard-samples=3
 *   node scripts/lighthouse-routes.mjs --base-url=https://askjamie.bot
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const routes = Object.freeze({
  homepage: "/",
  brandguard: "/lens-system/okhp3-brandguard/",
  universe: "/universe/",
  search: "/search/",
});
export const LIGHTHOUSE_ROUTES = routes;

// This is a historical reference stack, independent of the ignored, regenerable
// files under assets/audit/. Historical reports establish Chromium major 148;
// the exact patch build below is inferred from Playwright 1.60.0 metadata, not
// independently confirmed or owner-approved.
export const HISTORICAL_REFERENCE_MEASUREMENT_STACK = Object.freeze({
  lighthouseVersion: "12.8.2",
  // Inferred from Playwright metadata; historical reports establish major 148.
  chromiumVersion: "148.0.7778.96",
  chromiumUserAgent: "HeadlessChrome/148.0.0.0",
});

export function parseChromiumVersion(output) {
  const match = String(output).match(/\b(\d+\.\d+\.\d+\.\d+)\b/);
  if (!match) {
    throw new Error(`Could not determine the Chromium version from: ${String(output).trim() || "(empty output)"}`);
  }
  return match[1];
}

export function createSummary({
  date,
  preset,
  controlled,
  baseUrl,
  measurementStack,
  baselineMeasurementStack,
}) {
  const versionFields = ["lighthouseVersion", "chromiumVersion"];
  const baselineKnown = versionFields.every((field) => Boolean(baselineMeasurementStack?.[field]));
  const changedComponents = baselineKnown
    ? versionFields.filter((field) => measurementStack[field] !== baselineMeasurementStack[field])
    : ["baseline measurement stack unavailable"];
  const reviewRequired = changedComponents.length > 0;

  return {
    schemaVersion: 3,
    capturedAt: date,
    tool: `Lighthouse ${measurementStack.lighthouseVersion}`,
    measurementStack,
    measurementStackReview: {
      baseline: baselineMeasurementStack ?? null,
      referenceNote: "Historical reference only. The exact Chromium 148.0.7778.96 build is inferred from Playwright metadata; historical reports establish major version 148, not an independently owner-approved exact build.",
      status: reviewRequired ? "required" : "not-required",
      changedComponents,
      action: reviewRequired
        ? "Owner review is required before interpreting or changing the BrandGuard lab budget."
        : "No Lighthouse or Chromium version change from the historical reference measurement stack.",
    },
    environment: controlled
      ? "Local or supplied static server, controlled mobile preset"
      : `Local or supplied static server, ${preset} preset`,
    property: baseUrl,
    baseline: "assets/audit/lighthouse-baseline-2026-08-22.json",
    controls: controlled
      ? {
          thirdPartyFonts: "blocked",
          analytics: "blocked",
          interpretation: "Controlled lab measurement only. Not field data.",
        }
      : {
          thirdPartyFonts: "in flight",
          analytics: "in flight",
          interpretation: "No third-party isolation applied.",
        },
    pages: {},
  };
}

export function summarizePage({ report, path, baselinePage = {} }) {
  const audits = report.audits ?? {};
  const unavailableMetrics = [];
  const metric = (id, field) => {
    const value = audits[id]?.numericValue;
    if (typeof value !== "number" || !Number.isFinite(value)) {
      unavailableMetrics.push({ field, source: `audits.${id}.numericValue` });
      return null;
    }
    return value;
  };
  const categoryScore = (id, field) => {
    const value = report.categories?.[id]?.score;
    if (typeof value !== "number" || !Number.isFinite(value)) {
      unavailableMetrics.push({ field, source: `categories.${id}.score` });
      return null;
    }
    return Math.round(value * 100);
  };
  const lcpElement = audits["largest-contentful-paint-element"]?.details?.items?.[0]?.items?.[0]?.node;
  const lcpInvalidated = audits.metrics?.details?.items
    ?.find((item) => typeof item?.lcpInvalidated === "boolean")
    ?.lcpInvalidated ?? null;
  const performance = categoryScore("performance", "performance");
  const accessibility = categoryScore("accessibility", "accessibility");
  const bestPractices = categoryScore("best-practices", "bestPractices");
  const seo = categoryScore("seo", "seo");
  const lcpValue = metric("largest-contentful-paint", "lcpMs");
  const clsValue = metric("cumulative-layout-shift", "cls");
  const tbtValue = metric("total-blocking-time", "tbtMs");
  const fcpValue = metric("first-contentful-paint", "fcpMs");
  const speedIndexValue = metric("speed-index", "speedIndexMs");
  if (!lcpElement?.selector) {
    unavailableMetrics.push({
      field: "lcpElement",
      source: "audits.largest-contentful-paint-element.details.items[0].items[0].node.selector",
    });
  }
  const lcpMs = lcpValue === null ? null : Math.round(lcpValue);

  return {
    path,
    performance: performance,
    accessibility: accessibility,
    bestPractices: bestPractices,
    seo: seo,
    lcpMs,
    cls: clsValue === null ? null : Number(clsValue.toFixed(6)),
    tbtMs: tbtValue === null ? null : Math.round(tbtValue),
    fcpMs: fcpValue === null ? null : Math.round(fcpValue),
    speedIndexMs: speedIndexValue === null ? null : Math.round(speedIndexValue),
    lcpInvalidated,
    lcpElement: lcpElement?.selector || null,
    deltaPerformance: performance === null ? null : performance - (baselinePage.performance ?? 0),
    deltaLcpMs: lcpMs === null ? null : lcpMs - (baselinePage.lcpMs ?? 0),
    unavailableMetrics: unavailableMetrics,
  };
}

export function summarizeBrandGuardSamples(samples, { controlled }) {
  if (!Array.isArray(samples) || samples.length === 0) {
    throw new Error("At least one BrandGuard sample is required.");
  }

  const metricRange = (field) => {
    const values = samples
      .map(({ page }) => page[field])
      .filter((value) => typeof value === "number" && Number.isFinite(value));
    const min = values.length ? Math.min(...values) : null;
    const max = values.length ? Math.max(...values) : null;
    return {
      availableCount: values.length,
      unavailableCount: samples.length - values.length,
      min,
      max,
      spread: min === null ? null : max - min,
    };
  };
  const invalidationValues = samples.map(({ page }) => page.lcpInvalidated);

  return {
    condition: controlled ? "controlled" : "normal",
    controls: controlled
      ? { thirdPartyFonts: "blocked", analytics: "blocked" }
      : { thirdPartyFonts: "in flight", analytics: "in flight" },
    sampleCount: samples.length,
    metrics: {
      fcpMs: metricRange("fcpMs"),
      speedIndexMs: metricRange("speedIndexMs"),
      lcpMs: metricRange("lcpMs"),
      tbtMs: metricRange("tbtMs"),
      lcpInvalidated: {
        trueCount: invalidationValues.filter((value) => value === true).length,
        falseCount: invalidationValues.filter((value) => value === false).length,
        unavailableCount: invalidationValues.filter((value) => typeof value !== "boolean").length,
      },
    },
    samples: samples.map(({ report, page }, index) => ({
      sample: index + 1,
      report,
      fcpMs: page.fcpMs,
      speedIndexMs: page.speedIndexMs,
      lcpMs: page.lcpMs,
      tbtMs: page.tbtMs,
      lcpInvalidated: page.lcpInvalidated,
    })),
  };
}

function main() {
  const root = resolve(import.meta.dirname, "..");
  const baselinePath = resolve(root, "assets/audit/lighthouse-baseline-2026-08-22.json");
  const defaultBaseUrl = process.env.LIGHTHOUSE_BASE_URL || "http://127.0.0.1:5000";
  const baseUrlArg = process.argv.find((arg) => arg.startsWith("--base-url="));
  const baseUrl = (baseUrlArg ? baseUrlArg.slice("--base-url=".length) : defaultBaseUrl).replace(/\/+$/, "");
  const presetArg = process.argv.find((arg) => arg.startsWith("--preset="));
  const preset = presetArg ? presetArg.slice("--preset=".length) : "desktop";
  if (!["desktop", "mobile"].includes(preset)) {
    console.error(`Unsupported Lighthouse preset: ${preset}. Use desktop or mobile.`);
    process.exit(1);
  }
  const dateArg = process.argv.find((arg) => arg.startsWith("--date="));
  const date = dateArg ? dateArg.slice("--date=".length) : new Date().toISOString().slice(0, 10);
  const controlled = process.argv.includes("--controlled");
  const brandguardSamplesArg = process.argv.find((arg) => arg.startsWith("--brandguard-samples="));
  const brandguardSamples = brandguardSamplesArg
    ? Number(brandguardSamplesArg.slice("--brandguard-samples=".length))
    : 1;
  if (!Number.isInteger(brandguardSamples) || brandguardSamples < 1 || brandguardSamples > 10) {
    console.error("BrandGuard sample count must be a whole number from 1 to 10.");
    process.exit(1);
  }
  if (brandguardSamples > 1 && preset !== "mobile") {
    console.error("Repeated BrandGuard samples are only supported with --preset=mobile.");
    process.exit(1);
  }
  if (controlled && preset !== "mobile") {
    console.error("The controlled third-party isolation mode is only supported with --preset=mobile.");
    process.exit(1);
  }
  const outputSuffix = `${preset === "mobile" ? "-mobile" : ""}${controlled ? "-controlled" : ""}`;
  const outputDir = resolve(root, "assets/audit", `lighthouse-${date}${outputSuffix}`);
  const baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
  const lighthouseBin = resolve(root, "node_modules/.bin/lighthouse");
  const controlledBlockedUrlPatterns = [
    "https://fonts.googleapis.com/*",
    "https://fonts.gstatic.com/*",
    "https://www.googletagmanager.com/*",
    "https://www.google-analytics.com/*",
    "https://*.google-analytics.com/*",
  ];
  const chromePath = process.env.CHROME_PATH || (() => {
    try {
      return execFileSync(process.execPath, ["-e", "process.stdout.write(require('playwright').chromium.executablePath())"], {
        cwd: root,
        encoding: "utf8",
      }).trim();
    } catch {
      return "";
    }
  })();

  if (!existsSync(lighthouseBin)) {
    console.error("Lighthouse is not installed. Run: npm install");
    process.exit(1);
  }
  if (!chromePath) {
    console.error("Chromium was not found. Set CHROME_PATH or install Playwright browsers.");
    process.exit(1);
  }
  const chromiumVersion = parseChromiumVersion(execFileSync(chromePath, ["--version"], {
    cwd: root,
    encoding: "utf8",
  }));

  mkdirSync(outputDir, { recursive: true });
  let lighthouseVersion = null;
  let chromiumUserAgent = null;
  const baselineMeasurementStack = HISTORICAL_REFERENCE_MEASUREMENT_STACK;
  const pages = {};

  for (const [name, path] of Object.entries(routes)) {
    const reportPath = resolve(outputDir, `${name}.json`);
    const url = `${baseUrl}${path}`;
    console.log(`Running ${name}: ${url}`);
    execFileSync(lighthouseBin, [
      url,
      "--output=json",
      `--output-path=${reportPath}`,
      ...(preset === "desktop" ? ["--preset=desktop"] : ["--form-factor=mobile"]),
      ...(controlled
        ? controlledBlockedUrlPatterns.map((pattern) => `--blocked-url-patterns=${pattern}`)
        : []),
      "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
      "--quiet",
    ], { cwd: root, stdio: "inherit", env: { ...process.env, CHROME_PATH: chromePath } });

    const report = JSON.parse(readFileSync(reportPath, "utf8"));
    if (typeof report.lighthouseVersion !== "string" || !report.lighthouseVersion) {
      throw new Error(`Lighthouse report for ${name} does not identify its Lighthouse version.`);
    }
    const reportBrowser = report.environment?.hostUserAgent || report.userAgent || "";
    const browserMatch = reportBrowser.match(/(?:HeadlessChrome|Chrome)\/([\d.]+)/);
    if (!browserMatch) {
      throw new Error(`Lighthouse report for ${name} does not identify its Chromium version.`);
    }
    const reportChromiumMajor = browserMatch[1].split(".")[0];
    if (reportChromiumMajor !== chromiumVersion.split(".")[0]) {
      throw new Error(
        `Lighthouse used Chromium ${browserMatch[1]}, but the selected binary reports ${chromiumVersion}.`,
      );
    }
    if (lighthouseVersion && lighthouseVersion !== report.lighthouseVersion) {
      throw new Error(`Lighthouse version changed during the route run (${lighthouseVersion} to ${report.lighthouseVersion}).`);
    }
    if (chromiumUserAgent && chromiumUserAgent !== browserMatch[0]) {
      throw new Error(`Chromium user agent changed during the route run (${chromiumUserAgent} to ${browserMatch[0]}).`);
    }
    lighthouseVersion = report.lighthouseVersion;
    chromiumUserAgent = browserMatch[0];
    pages[name] = summarizePage({
      report,
      path,
      baselinePage: baseline.pages[name] || {},
    });
  }

  const repeatedBrandGuardSamples = [{
    report: "brandguard.json",
    page: pages.brandguard,
  }];
  for (let sampleIndex = 2; sampleIndex <= brandguardSamples; sampleIndex += 1) {
    const reportName = `brandguard-sample-${String(sampleIndex).padStart(2, "0")}.json`;
    const reportPath = resolve(outputDir, reportName);
    const url = `${baseUrl}${routes.brandguard}`;
    console.log(`Running brandguard sample ${sampleIndex}/${brandguardSamples}: ${url}`);
    execFileSync(lighthouseBin, [
      url,
      "--output=json",
      `--output-path=${reportPath}`,
      "--form-factor=mobile",
      ...(controlled
        ? controlledBlockedUrlPatterns.map((pattern) => `--blocked-url-patterns=${pattern}`)
        : []),
      "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
      "--quiet",
    ], { cwd: root, stdio: "inherit", env: { ...process.env, CHROME_PATH: chromePath } });

    const report = JSON.parse(readFileSync(reportPath, "utf8"));
    if (typeof report.lighthouseVersion !== "string" || !report.lighthouseVersion) {
      throw new Error(`Lighthouse report for BrandGuard sample ${sampleIndex} does not identify its Lighthouse version.`);
    }
    const reportBrowser = report.environment?.hostUserAgent || report.userAgent || "";
    const browserMatch = reportBrowser.match(/(?:HeadlessChrome|Chrome)\/([\d.]+)/);
    if (!browserMatch) {
      throw new Error(`Lighthouse report for BrandGuard sample ${sampleIndex} does not identify its Chromium version.`);
    }
    if (browserMatch[1].split(".")[0] !== chromiumVersion.split(".")[0]) {
      throw new Error(
        `Lighthouse used Chromium ${browserMatch[1]}, but the selected binary reports ${chromiumVersion}.`,
      );
    }
    if (lighthouseVersion !== report.lighthouseVersion) {
      throw new Error(`Lighthouse version changed during the route run (${lighthouseVersion} to ${report.lighthouseVersion}).`);
    }
    if (chromiumUserAgent !== browserMatch[0]) {
      throw new Error(`Chromium user agent changed during the route run (${chromiumUserAgent} to ${browserMatch[0]}).`);
    }

    repeatedBrandGuardSamples.push({
      report: reportName,
      page: summarizePage({
        report,
        path: routes.brandguard,
        baselinePage: baseline.pages.brandguard || {},
      }),
    });
  }

  const measurementStack = {
    lighthouseVersion,
    chromiumVersion,
    chromiumUserAgent,
  };
  const summary = createSummary({
    date,
    preset,
    controlled,
    baseUrl,
    measurementStack,
    baselineMeasurementStack,
  });
  summary.pages = pages;
  if (brandguardSamples > 1) {
    summary.brandguardRepeatSamples = summarizeBrandGuardSamples(repeatedBrandGuardSamples, { controlled });
  }

  const summaryPath = resolve(outputDir, "summary.json");
  writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`\nWrote ${summaryPath}`);
  console.log(`Measurement stack: Lighthouse ${lighthouseVersion}; Chromium ${chromiumVersion}`);
  if (summary.brandguardRepeatSamples) {
    const { condition, sampleCount, metrics } = summary.brandguardRepeatSamples;
    console.log(
      `BrandGuard ${condition} samples: ${sampleCount}; LCP ${metrics.lcpMs.min}–${metrics.lcpMs.max} ms`,
    );
  }
  if (summary.measurementStackReview.status === "required") {
    console.warn(
      `BRANDGUARD BUDGET REVIEW REQUIRED: ${summary.measurementStackReview.changedComponents.join(", ")} changed from the historical reference measurement stack.`,
    );
    console.warn(summary.measurementStackReview.action);
  }
  console.table(Object.fromEntries(Object.entries(summary.pages).map(([name, page]) => [
    name,
    { performance: page.performance, delta: page.deltaPerformance, lcpMs: page.lcpMs, deltaLcpMs: page.deltaLcpMs, cls: page.cls, tbtMs: page.tbtMs },
  ])));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main();
}

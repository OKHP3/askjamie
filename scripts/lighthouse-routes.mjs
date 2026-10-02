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

export function createSummary({ date, preset, controlled, baseUrl }) {
  return {
    schemaVersion: 2,
    capturedAt: date,
    tool: "Lighthouse 12.8.2",
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

  mkdirSync(outputDir, { recursive: true });
  const summary = createSummary({ date, preset, controlled, baseUrl });

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
    summary.pages[name] = summarizePage({
      report,
      path,
      baselinePage: baseline.pages[name] || {},
    });
  }

  const summaryPath = resolve(outputDir, "summary.json");
  writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`\nWrote ${summaryPath}`);
  console.table(Object.fromEntries(Object.entries(summary.pages).map(([name, page]) => [
    name,
    { performance: page.performance, delta: page.deltaPerformance, lcpMs: page.lcpMs, deltaLcpMs: page.deltaLcpMs, cls: page.cls, tbtMs: page.tbtMs },
  ])));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main();
}

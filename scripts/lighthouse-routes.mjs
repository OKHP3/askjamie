#!/usr/bin/env node
/**
 * Run the same Lighthouse pass against the four public routes and compare the
 * compact results with the matching mobile or desktop reference.
 *
 * The static server is intentionally kept separate so this command can be
 * used against the Replit preview, a local server, or a hosted preview:
 *
 *   node scripts/lighthouse-routes.mjs
 *   node scripts/lighthouse-routes.mjs --preset=mobile
 *   node scripts/lighthouse-routes.mjs --preset=mobile --controlled
 *   node scripts/lighthouse-routes.mjs --preset=mobile --controlled --brandguard-samples=3
 *   node scripts/lighthouse-routes.mjs --preset=desktop --desktop-repeats=3
 *   node scripts/lighthouse-routes.mjs --print-desktop-reference-fingerprint
 *   node scripts/lighthouse-routes.mjs --base-url=https://askjamie.bot
 *   node scripts/lighthouse-routes.mjs --preset=mobile --date=2026-10-01 --run-id=rerun-2
 *   node scripts/lighthouse-routes.mjs --preset=mobile --date=2026-10-01 --replace
 *   node scripts/lighthouse-routes.mjs --show-unavailable-sources
 *   node scripts/lighthouse-routes.mjs --help
 *
 * Existing dated output is preserved by default. Use --run-id to write to a
 * separate directory, or --replace to explicitly clear and reuse that output.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, watch, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, relative, resolve, sep } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const routes = Object.freeze({
  homepage: "/",
  brandguard: "/lens-system/okhp3-brandguard/",
  universe: "/universe/",
  search: "/search/",
});
export const LIGHTHOUSE_ROUTES = routes;
export const DEPLOYED_SOURCE_REVISION_HEADER = "x-askjamie-source-revision";

export const CONTROLLED_BLOCKED_URL_PATTERNS = Object.freeze([
  "https://fonts.googleapis.com/*",
  "https://fonts.gstatic.com/*",
  "https://www.googletagmanager.com/*",
  "https://www.google-analytics.com/*",
  "https://*.google-analytics.com/*",
]);

// Inventory of configSettings emitted by supported Lighthouse 12.8.2 reports
// and the installed Lighthouse 13.5.0 settings schema. Measurement-affecting
// values are compared against each explicit reference. New, unclassified keys
// are retained and therefore require review until deliberately classified.
export const LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY = Object.freeze({
  tracked: Object.freeze([
    "formFactor",
    "throttlingMethod",
    "throttling",
    "screenEmulation",
    "emulatedUserAgent",
    "blockedUrlPatterns",
    "maxWaitForFcp",
    "maxWaitForLoad",
    "pauseAfterFcpMs",
    "pauseAfterLoadMs",
    "networkQuietThresholdMs",
    "cpuQuietThresholdMs",
    "locale",
    "disableStorageReset",
    "clearStorageTypes",
    "auditMode",
    "gatherMode",
    "debugNavigation",
    "channel",
    "usePassiveGathering",
    "disableFullPageScreenshot",
    "skipAboutBlank",
    "blankPage",
    "ignoreStatusCode",
    "additionalTraceCategories",
    "extraHeaders",
    "precomputedLanternData",
    "onlyAudits",
    "onlyCategories",
    "skipAudits",
  ]),
  recordedWithoutComparison: Object.freeze({
    output: "Selects report serialization only; it does not change page navigation, collection, or audits.",
  }),
});

const knownConfigSettings = new Set([
  ...LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.tracked,
  ...Object.keys(LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.recordedWithoutComparison),
]);
const summarizedConfigSettings = new Set([
  "formFactor",
  "throttlingMethod",
  "throttling",
  "screenEmulation",
  "emulatedUserAgent",
  "blockedUrlPatterns",
  "maxWaitForFcp",
  "maxWaitForLoad",
  "pauseAfterFcpMs",
  "pauseAfterLoadMs",
  "networkQuietThresholdMs",
  "cpuQuietThresholdMs",
  "locale",
  "disableStorageReset",
  "clearStorageTypes",
]);
const additionalTrackedConfigSettings = LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.tracked
  .filter((field) => !summarizedConfigSettings.has(field));

const SCRIPT_VALUE_OPTIONS = Object.freeze([
  "--base-url",
  "--preset",
  "--date",
  "--run-id",
  "--brandguard-samples",
  "--desktop-repeats",
]);
const SCRIPT_BOOLEAN_OPTIONS = Object.freeze([
  "--replace",
  "--controlled",
  "--show-unavailable-sources",
  "--print-desktop-reference-fingerprint",
]);
const SCRIPT_OPTIONS = Object.freeze([
  { option: "-h, --help", description: "Show supported options and exit." },
  { option: "--base-url=<url>", description: "Set the site URL to measure." },
  { option: "--preset=<desktop|mobile>", description: "Choose the Lighthouse preset (default: desktop)." },
  { option: "--date=<YYYY-MM-DD>", description: "Set the output date (default: today)." },
  { option: "--run-id=<identifier>", description: "Write to a separate output directory for this run." },
  { option: "--replace", description: "Replace existing output for the selected date and run." },
  { option: "--controlled", description: "Block selected third-party requests (mobile only)." },
  { option: "--show-unavailable-sources", description: "Include source details for unavailable metrics." },
  { option: "--brandguard-samples=<count>", description: "Repeat the BrandGuard route 1–10 times (mobile only)." },
  { option: "--desktop-repeats=<count>", description: "Repeat the full desktop pass 2–10 times." },
  { option: "--print-desktop-reference-fingerprint", description: "Print the desktop reference fingerprint and exit." },
]);
const SCRIPT_OPTION_HELP = SCRIPT_OPTIONS.map(({ option }) => option);

function formatScriptHelp() {
  const optionWidth = Math.max(...SCRIPT_OPTIONS.map(({ option }) => option.length));
  const options = SCRIPT_OPTIONS
    .map(({ option, description }) => `  ${option.padEnd(optionWidth)}  ${description}`)
    .join("\n");
  return `Usage: node scripts/lighthouse-routes.mjs [options]\n\nOptions:\n${options}`;
}

function validateScriptOptions(args) {
  const positional = args.find((arg) => !arg.startsWith("--"));
  if (positional) {
    return `Unexpected positional Lighthouse route argument: ${positional}`;
  }
  const unknown = args.find((arg) => {
    if (!arg.startsWith("--")) return false;
    if (SCRIPT_BOOLEAN_OPTIONS.includes(arg)) return false;
    return !SCRIPT_VALUE_OPTIONS.some((option) => arg.startsWith(`${option}=`));
  });
  if (unknown) {
    return `Unknown Lighthouse route option: ${unknown}\nSupported options: ${SCRIPT_OPTION_HELP.join(", ")}`;
  }
  for (const option of SCRIPT_VALUE_OPTIONS) {
    const values = args
      .filter((arg) => arg.startsWith(`${option}=`))
      .map((arg) => arg.slice(option.length + 1));
    if (new Set(values).size > 1) {
      return `Conflicting repeated Lighthouse route option: ${option} (repeat it only with the same value).`;
    }
  }
  return null;
}

// Historical mobile reference is independent of the ignored, regenerable
// files under assets/audit/. Historical reports establish Chromium major 148;
// the exact patch build below is inferred from Playwright 1.60.0 metadata, not
// independently confirmed or owner-approved.
export const HISTORICAL_REFERENCE_MEASUREMENT_STACK = Object.freeze({
  lighthouseVersion: "12.8.2",
  // Inferred from Playwright metadata; historical reports establish major 148.
  chromiumVersion: "148.0.7778.96",
  chromiumUserAgent: "HeadlessChrome/148.0.0.0",
});

export function getSourceRevision(root, { excludePaths = [] } = {}) {
  let revision = null;
  let workingTreeClean = null;
  let workingTreeFingerprint = null;
  const errors = [];
  const pathspecs = [
    ".",
    ...excludePaths.map((path) => `:(exclude,literal)${path.replaceAll("\\", "/")}`),
  ];
  try {
    revision = execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    if (!/^[0-9a-f]{40,64}$/i.test(revision)) {
      throw new Error("Git returned an invalid HEAD revision.");
    }
  } catch (error) {
    revision = null;
    errors.push(`revision: ${error.stderr?.toString().trim() || error.message.split("\n")[0]}`);
  }
  try {
    const status = execFileSync("git", [
      "status",
      "--porcelain=v1",
      "--untracked-files=normal",
      "--",
      ...pathspecs,
    ], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 64 * 1024 * 1024,
    });
    workingTreeClean = status.length === 0;
  } catch (error) {
    errors.push(`working tree: ${error.stderr?.toString().trim() || error.message.split("\n")[0]}`);
  }
  if (revision !== null || workingTreeClean !== null) {
    try {
      const fingerprint = createHash("sha256");
      for (const args of [
        ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--", ...pathspecs],
        ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "--", ...pathspecs],
      ]) {
        fingerprint.update(execFileSync("git", args, {
          cwd: root,
          stdio: ["ignore", "pipe", "pipe"],
          maxBuffer: 64 * 1024 * 1024,
        }));
      }
      const untrackedPaths = execFileSync("git", [
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        ...pathspecs,
      ], {
        cwd: root,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 64 * 1024 * 1024,
      }).split("\0").filter(Boolean);
      for (const path of untrackedPaths) {
        fingerprint.update(path);
        fingerprint.update("\0");
        fingerprint.update(readFileSync(resolve(root, path)));
      }
      workingTreeFingerprint = fingerprint.digest("hex");
    } catch (error) {
      errors.push(
        `working-tree fingerprint: ${error.stderr?.toString().trim() || error.message.split("\n")[0]}`,
      );
    }
  }
  return {
    revision,
    workingTreeClean,
    workingTreeFingerprint,
    status: revision === null ? "unavailable" : "recorded",
    error: errors.length > 0 ? errors.join("; ") : null,
  };
}

function getChangedSourcePaths(root, excludePaths = []) {
  const pathspecs = [
    ".",
    ...excludePaths.map((path) => `:(exclude,literal)${path.replaceAll("\\", "/")}`),
  ];
  const output = execFileSync("git", [
    "status",
    "--porcelain=v1",
    "--untracked-files=normal",
    "-z",
    "--",
    ...pathspecs,
  ], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    maxBuffer: 64 * 1024 * 1024,
  });
  const records = output.split("\0");
  const paths = new Map();
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    if (!record) continue;
    const path = record.slice(3);
    if (path) paths.set(path.replaceAll("\\", "/"), record.slice(0, 2));
    // Porcelain v1 -z emits the source path as a second NUL-terminated
    // record for renames and copies.
    if (/^[RC]/.test(record.slice(0, 2)) && records[index + 1]) {
      paths.set(records[index + 1].replaceAll("\\", "/"), record.slice(0, 2));
      index += 1;
    }
  }
  return new Map([...paths.entries()].sort(([left], [right]) => left.localeCompare(right)));
}

export function createCheckoutChangeMonitor(root, { excludePaths = [] } = {}) {
  const excludedPaths = excludePaths.map((path) => path.replaceAll("\\", "/").replace(/\/+$/, ""));
  const trackedPaths = new Set();
  const trackedDirectories = new Set([resolve(root)]);
  const affectedPasses = new Map();
  const affectedBrandGuardSamples = new Map();
  const pathsByPass = new Map();
  const pathsByBrandGuardSample = new Map();
  const revisionChangedPasses = new Set();
  const revisionChangedBrandGuardSamples = new Set();
  const changedPaths = new Set();
  const errors = [];
  const watchers = [];
  let activeMeasurement = null;
  let lastRunStatusPaths = null;

  const relativePath = (path) => relative(root, path).split(sep).join("/");
  const isExcluded = (path) => excludedPaths.some(
    (excluded) => path === excluded || path.startsWith(`${excluded}/`),
  );
  const addChangedPath = (path) => {
    const normalized = path.replaceAll("\\", "/");
    if (!normalized || isExcluded(normalized)) return;
    changedPaths.add(normalized);
    if (!activeMeasurement) return;
    const pathMap = activeMeasurement.type === "pass"
      ? pathsByPass
      : pathsByBrandGuardSample;
    if (!pathMap.has(activeMeasurement.index)) pathMap.set(activeMeasurement.index, new Set());
    pathMap.get(activeMeasurement.index).add(normalized);
    const eventMap = activeMeasurement.type === "pass"
      ? affectedPasses
      : affectedBrandGuardSamples;
    if (!eventMap.has(activeMeasurement.index)) eventMap.set(activeMeasurement.index, 0);
  };
  const readStatusPaths = () => {
    try {
      return getChangedSourcePaths(root, excludedPaths);
    } catch (error) {
      errors.push(`working-tree paths: ${error.stderr?.toString().trim() || error.message.split("\n")[0]}`);
      return null;
    }
  };
  const captureRunStatusChanges = (nextPaths) => {
    if (!nextPaths) return;
    if (lastRunStatusPaths) {
      for (const path of new Set([
        ...lastRunStatusPaths.keys(),
        ...nextPaths.keys(),
      ])) {
        if (lastRunStatusPaths.get(path) !== nextPaths.get(path)) {
          changedPaths.add(path);
        }
      }
    }
    lastRunStatusPaths = nextPaths;
  };
  // Lighthouse runs synchronously, so filesystem callbacks may not run until
  // after a temporary checkout has already been restored. The HEAD reflog
  // preserves those intervening revision changes for each measurement.
  const readHeadReflog = () => {
    try {
      return execFileSync("git", ["reflog", "--format=%H%x00%gs"], {
        cwd: root,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
        maxBuffer: 64 * 1024 * 1024,
      });
    } catch {
      return null;
    }
  };

  try {
    const trackedOutput = execFileSync("git", ["ls-files", "-z"], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 64 * 1024 * 1024,
    });
    for (const path of trackedOutput.split("\0").filter(Boolean)) {
      const normalized = path.replaceAll("\\", "/");
      if (isExcluded(normalized)) continue;
      trackedPaths.add(normalized);
      trackedDirectories.add(resolve(root, dirname(normalized)));
    }

    const gitDirectory = execFileSync("git", ["rev-parse", "--git-dir"], {
      cwd: root,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    const resolvedGitDirectory = resolve(root, gitDirectory);
    trackedDirectories.add(resolvedGitDirectory);
    trackedDirectories.add(resolve(resolvedGitDirectory, "refs"));
    trackedDirectories.add(resolve(resolvedGitDirectory, "refs/heads"));

    for (const directory of trackedDirectories) {
      if (!existsSync(directory)) continue;
      const directoryRelativePath = relativePath(directory);
      try {
        const watcher = watch(directory, (eventType, filename) => {
          if (filename === null) {
            errors.push(`A checkout change event in ${directoryRelativePath || "."} had no path.`);
            return;
          }
          const changedPath = relativePath(resolve(directory, filename.toString()));
          if (isExcluded(changedPath)) return;
          const isGitMetadata = changedPath === ".git"
            || changedPath.startsWith(".git/")
            || changedPath.startsWith(`${relativePath(resolvedGitDirectory)}/`);
          const isTrackedSource = trackedPaths.has(changedPath)
            || (eventType === "rename" && trackedDirectories.has(directory));
          // A newly created untracked file in a watched source directory is
          // still a source change even though it was not in the index.
          const isPotentialSourcePath = trackedDirectories.has(directory)
            && !changedPath.startsWith("node_modules/");
          if (isGitMetadata || (!isTrackedSource && !isPotentialSourcePath)) return;

          if (!activeMeasurement) return;
          const targetMap = activeMeasurement.type === "pass"
            ? affectedPasses
            : affectedBrandGuardSamples;
          targetMap.set(activeMeasurement.index, (targetMap.get(activeMeasurement.index) || 0) + 1);
          addChangedPath(changedPath);
        });
        watcher.on("error", (error) => {
          errors.push(error.message);
        });
        watchers.push(watcher);
      } catch (error) {
        errors.push(error.message);
      }
    }
  } catch (error) {
    errors.push(error.stderr?.toString().trim() || error.message.split("\n")[0]);
  }
  lastRunStatusPaths = readStatusPaths();

  return {
    beginMeasurement(type, index) {
      const startPaths = readStatusPaths();
      captureRunStatusChanges(startPaths);
      activeMeasurement = {
        type,
        index,
        startPaths,
        startHeadReflog: readHeadReflog(),
      };
    },
    endMeasurement() {
      if (!activeMeasurement) return;
      const endPaths = readStatusPaths();
      captureRunStatusChanges(endPaths);
      if (activeMeasurement.startPaths && endPaths) {
        for (const path of new Set([
          ...activeMeasurement.startPaths.keys(),
          ...endPaths.keys(),
        ])) {
          if (activeMeasurement.startPaths.get(path) !== endPaths.get(path)) {
            addChangedPath(path);
          }
        }
      }
      const endHeadReflog = readHeadReflog();
      if (
        activeMeasurement.startHeadReflog !== null
        && endHeadReflog !== null
        && activeMeasurement.startHeadReflog !== endHeadReflog
      ) {
        const isPass = activeMeasurement.type === "pass";
        const changedIndexes = isPass
          ? revisionChangedPasses
          : revisionChangedBrandGuardSamples;
        const eventMap = isPass ? affectedPasses : affectedBrandGuardSamples;
        changedIndexes.add(activeMeasurement.index);
        eventMap.set(activeMeasurement.index, (eventMap.get(activeMeasurement.index) || 0) + 1);
      }
      activeMeasurement = null;
    },
    flush() {
      return new Promise((resolveFlush) => setImmediate(() => {
        captureRunStatusChanges(readStatusPaths());
        resolveFlush();
      }));
    },
    close() {
      for (const watcher of watchers) watcher.close();
    },
    summary() {
      const entries = (eventMap, pathsMap, revisionChangedIndexes) => {
        const indexes = new Set([
          ...eventMap.keys(),
          ...pathsMap.keys(),
          ...revisionChangedIndexes,
        ]);
        return [...indexes]
          .sort((left, right) => left - right)
          .map((index) => ({
            index,
            eventCount: eventMap.get(index) || 0,
            changedPaths: [...(pathsMap.get(index) || [])].sort(),
            ...(revisionChangedIndexes.has(index) ? { revisionChanged: true } : {}),
          }));
      };
      return {
        status: errors.length > 0 || watchers.length === 0 ? "incomplete" : "recorded",
        method: "filesystem-events",
        affectedPasses: entries(affectedPasses, pathsByPass, revisionChangedPasses),
        affectedBrandGuardSamples: entries(
          affectedBrandGuardSamples,
          pathsByBrandGuardSample,
          revisionChangedBrandGuardSamples,
        ),
        changedPaths: [...changedPaths].sort(),
        error: errors.length > 0 ? errors.join("; ") : null,
      };
    },
  };
}

function compareSourceProvenance(start, end) {
  const changedComponents = [];
  if (start.revision !== null && end.revision !== null && start.revision !== end.revision) {
    changedComponents.push("revision");
  }
  if (
    start.workingTreeFingerprint !== null
    && end.workingTreeFingerprint !== null
    && start.workingTreeFingerprint !== end.workingTreeFingerprint
  ) {
    changedComponents.push("workingTree");
  } else if (
    start.workingTreeClean !== null
    && end.workingTreeClean !== null
    && start.workingTreeClean !== end.workingTreeClean
  ) {
    changedComponents.push("workingTree");
  }
  const comparable = (
    start.revision !== null
    && end.revision !== null
    && start.workingTreeFingerprint !== null
    && end.workingTreeFingerprint !== null
  );
  const status = changedComponents.length > 0
    ? "changed"
    : comparable
      ? "unchanged"
      : "incomplete";
  return {
    status,
    changedComponents,
    action: status === "changed"
      ? "The checkout changed during measurement. Review which passes may reflect each source state before interpreting the comparison."
      : status === "incomplete"
        ? "Source provenance could not be fully compared at both ends of the run."
        : "The checkout revision and working tree did not change during measurement.",
  };
}

// Effective settings copied from configSettings in the approved 2026-08-22 raw
// reports. Keep this reference in source because assets/audit/ is regenerable.
export const HISTORICAL_REFERENCE_RUN_CONDITIONS = Object.freeze({
  formFactor: "mobile",
  throttlingMethod: "simulate",
  throttling: Object.freeze({
    rttMs: 150,
    throughputKbps: 1638.4,
    requestLatencyMs: 562.5,
    downloadThroughputKbps: 1474.5600000000002,
    uploadThroughputKbps: 675,
    cpuSlowdownMultiplier: 4,
  }),
  screenEmulation: Object.freeze({
    mobile: true,
    width: 412,
    height: 823,
    deviceScaleFactor: 1.75,
    disabled: false,
  }),
  emulatedUserAgent: "Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
  blockedUrlPatterns: null,
  collectionTiming: Object.freeze({
    maxWaitForFcp: 30000,
    maxWaitForLoad: 45000,
    pauseAfterFcpMs: 1000,
    pauseAfterLoadMs: 1000,
    networkQuietThresholdMs: 1000,
    cpuQuietThresholdMs: 1000,
  }),
  locale: "en-US",
  storage: Object.freeze({
    disableStorageReset: false,
    clearStorageTypes: Object.freeze([
      "file_systems",
      "shader_cache",
      "service_workers",
      "cache_storage",
    ]),
  }),
  additionalConfigSettings: Object.freeze({
    auditMode: false,
    gatherMode: false,
    debugNavigation: false,
    channel: "cli",
    usePassiveGathering: false,
    disableFullPageScreenshot: false,
    skipAboutBlank: false,
    blankPage: "about:blank",
    ignoreStatusCode: false,
    additionalTraceCategories: null,
    extraHeaders: null,
    precomputedLanternData: null,
    onlyAudits: null,
    onlyCategories: null,
    skipAudits: null,
  }),
  unclassifiedConfigSettings: Object.freeze({}),
});

// These are the explicit Chrome startup flags supplied to Lighthouse. Keep the
// invocation and archived run-condition record tied to the same immutable list.
export const CHROME_STARTUP_FLAGS = Object.freeze([
  "--headless",
  "--no-sandbox",
  "--disable-dev-shm-usage",
]);
const CHROME_FLAGS_ARGUMENT = `--chrome-flags=${CHROME_STARTUP_FLAGS.join(" ")}`;

export const DESKTOP_REFERENCE_PATH = "assets/docs/performance-baseline-desktop-2026-09-07.json";
export const MOBILE_REFERENCE_PATH = "assets/docs/performance-baseline-mobile-2026-08-22.json";
// The saved mobile raw reports all requested 127.0.0.1:5000. Their compact
// summary's property field disagrees and is not used as target evidence.
export const HISTORICAL_REFERENCE_TARGET_ORIGIN = "http://127.0.0.1:5000";

function canonicalizeJson(value) {
  if (Array.isArray(value)) return value.map(canonicalizeJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalizeJson(value[key])]),
    );
  }
  return value;
}

export function getDesktopReferenceIntegrityFingerprint(reference) {
  const integrityPayload = {
    measurementStack: reference?.measurementStack,
    runConditions: reference?.runConditions,
    targetOrigin: reference?.targetOrigin,
    pages: reference?.pages,
  };
  const canonicalPayload = JSON.stringify(canonicalizeJson(integrityPayload));
  const digest = createHash("sha256")
    .update(`desktop-lighthouse-reference:v2\n${canonicalPayload}`)
    .digest("hex");
  return `sha256:${digest}`;
}

export function getLighthouseReference(preset, baseline) {
  if (preset === "mobile") {
    return {
      path: MOBILE_REFERENCE_PATH,
      measurementStack: HISTORICAL_REFERENCE_MEASUREMENT_STACK,
      runConditions: HISTORICAL_REFERENCE_RUN_CONDITIONS,
      sourceRevision: null,
      sourceWorkingTreeClean: null,
      sourceRevisionStatus: "not-recorded",
      sourceRevisionNote: "The historical mobile reference does not record its source revision or working-tree state.",
      measurementStackReferenceNote: undefined,
      approval: null,
      targetOrigin: HISTORICAL_REFERENCE_TARGET_ORIGIN,
    };
  }
  if (preset !== "desktop") {
    throw new Error(`Unsupported Lighthouse preset: ${preset}. Use desktop or mobile.`);
  }
  const missingStackFields = ["lighthouseVersion", "chromiumVersion"]
    .filter((field) => typeof baseline?.measurementStack?.[field] !== "string");
  if (missingStackFields.length > 0) {
    throw new Error(
      `Desktop Lighthouse reference is missing measurement-stack fields: ${missingStackFields.join(", ")}.`,
    );
  }
  if (baseline?.runConditions?.formFactor !== "desktop") {
    throw new Error("Desktop Lighthouse reference must record desktop run conditions.");
  }
  const missingRouteMeasurements = Object.keys(routes).filter((name) => (
    !Number.isFinite(baseline?.pages?.[name]?.performance)
    || !Number.isFinite(baseline?.pages?.[name]?.lcpMs)
  ));
  if (missingRouteMeasurements.length > 0) {
    throw new Error(
      `Desktop Lighthouse reference is missing performance or LCP measurements for: ${missingRouteMeasurements.join(", ")}.`,
    );
  }
  const invalidMeasurements = [];
  for (const name of Object.keys(routes)) {
    const { performance, lcpMs } = baseline.pages[name];
    if (performance < 0 || performance > 100) {
      invalidMeasurements.push(
        `out-of-range performance measurement for ${name}: ${performance} (expected 0–100).`,
      );
    }
    if (lcpMs < 0) {
      invalidMeasurements.push(
        `negative LCP measurement for ${name}: ${lcpMs} ms (expected >= 0).`,
      );
    }
  }
  if (invalidMeasurements.length > 0) {
    throw new Error(
      `Desktop Lighthouse reference has invalid measurements:\n${invalidMeasurements.map((measurement) => `- ${measurement}`).join("\n")}`,
    );
  }
  const integrityFingerprint = getDesktopReferenceIntegrityFingerprint(baseline);
  return {
    path: DESKTOP_REFERENCE_PATH,
    measurementStack: baseline.measurementStack,
    runConditions: baseline.runConditions,
    sourceRevision: baseline.sourceRevision ?? null,
    sourceWorkingTreeClean: baseline.sourceWorkingTreeClean ?? null,
    sourceRevisionStatus: baseline.sourceRevisionStatus ?? "not-recorded",
    sourceRevisionNote: baseline.sourceRevisionNote
      ?? "The historical desktop reference does not record its source revision or working-tree state.",
    measurementStackReferenceNote: baseline.measurementStackReferenceNote,
    approval: baseline.approval ?? null,
    targetOrigin: baseline.targetOrigin ?? null,
    integrityFingerprint,
  };
}

export function summarizeRunConditions(report, chromeFlags) {
  const settings = report.configSettings ?? {};
  const settingValue = (field) => (
    Object.hasOwn(settings, field) ? settings[field] : "unavailable"
  );
  const pickFields = (value, fields) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    return Object.fromEntries(fields.map((field) => [
      field,
      Object.hasOwn(value, field) ? value[field] : null,
    ]));
  };

  return {
    // Startup flags are not part of Lighthouse configSettings. Only report
    // them when the caller can identify the flags supplied to this run.
    chromeFlags: Array.isArray(chromeFlags) ? [...chromeFlags] : "unavailable",
    formFactor: typeof settings.formFactor === "string" ? settings.formFactor : null,
    throttlingMethod: typeof settings.throttlingMethod === "string" ? settings.throttlingMethod : null,
    throttling: pickFields(settings.throttling, [
      "rttMs",
      "throughputKbps",
      "requestLatencyMs",
      "downloadThroughputKbps",
      "uploadThroughputKbps",
      "cpuSlowdownMultiplier",
    ]),
    screenEmulation: pickFields(settings.screenEmulation, [
      "mobile",
      "width",
      "height",
      "deviceScaleFactor",
      "disabled",
    ]),
    emulatedUserAgent: typeof settings.emulatedUserAgent === "string"
      ? settings.emulatedUserAgent
      : null,
    blockedUrlPatterns: Object.hasOwn(settings, "blockedUrlPatterns")
      ? settings.blockedUrlPatterns
      : "unavailable",
    collectionTiming: Object.fromEntries([
      "maxWaitForFcp",
      "maxWaitForLoad",
      "pauseAfterFcpMs",
      "pauseAfterLoadMs",
      "networkQuietThresholdMs",
      "cpuQuietThresholdMs",
    ].map((field) => [
      field,
      Object.hasOwn(settings, field) ? settings[field] : "unavailable",
    ])),
    locale: Object.hasOwn(settings, "locale") ? settings.locale : "unavailable",
    storage: {
      disableStorageReset: settingValue("disableStorageReset"),
      clearStorageTypes: settingValue("clearStorageTypes"),
    },
    additionalConfigSettings: Object.fromEntries(
      additionalTrackedConfigSettings.map((field) => [field, settingValue(field)]),
    ),
    nonComparabilitySettings: Object.fromEntries(
      Object.keys(LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.recordedWithoutComparison)
        .map((field) => [field, settingValue(field)]),
    ),
    unclassifiedConfigSettings: Object.fromEntries(
      Object.keys(settings)
        .filter((field) => !knownConfigSettings.has(field))
        .map((field) => [field, settings[field]]),
    ),
  };
}

function changedRunConditionComponents(actual, expected, prefix = "") {
  const actualIsObject = actual !== null && typeof actual === "object" && !Array.isArray(actual);
  const expectedIsObject = expected !== null && typeof expected === "object" && !Array.isArray(expected);
  if (actualIsObject && expectedIsObject) {
    const keys = [...new Set([...Object.keys(actual), ...Object.keys(expected)])].sort();
    return keys.flatMap((key) => changedRunConditionComponents(
      actual[key],
      expected[key],
      prefix ? `${prefix}.${key}` : key,
    ));
  }
  return JSON.stringify(actual) === JSON.stringify(expected) ? [] : [prefix || "run conditions"];
}

function comparableRunConditionSnapshot(conditions) {
  if (!conditions || typeof conditions !== "object" || Array.isArray(conditions)) {
    return conditions;
  }
  return Object.fromEntries(
    Object.entries(conditions).filter(([field]) => field !== "nonComparabilitySettings"),
  );
}

function unclassifiedConfigSettingComponents(conditions) {
  const values = conditions?.unclassifiedConfigSettings;
  if (!values || typeof values !== "object" || Array.isArray(values)) return [];
  return Object.keys(values).map((field) => `unclassifiedConfigSettings.${field}`);
}

export function createRunConditionsReview(
  reports,
  {
    controlled = false,
    baselineRunConditions = HISTORICAL_REFERENCE_RUN_CONDITIONS,
  } = {},
) {
  const expectedConditions = controlled
    ? {
        ...baselineRunConditions,
        blockedUrlPatterns: CONTROLLED_BLOCKED_URL_PATTERNS,
      }
    : baselineRunConditions;
  const entries = Object.entries(reports ?? {});
  const changedReports = entries
    .map(([report, conditions]) => {
      const changed = changedRunConditionComponents(
        comparableRunConditionSnapshot(conditions),
        comparableRunConditionSnapshot(expectedConditions),
      );
      const unclassified = unclassifiedConfigSettingComponents(conditions);
      return {
        report,
        changedComponents: [...new Set([...changed, ...unclassified])].sort(),
      };
    })
    .filter(({ changedComponents }) => changedComponents.length > 0);
  const changedComponents = [...new Set(
    changedReports.flatMap(({ changedComponents: changed }) => changed),
  )].sort();
  const reviewRequired = entries.length === 0 || changedComponents.length > 0;
  const desktopReference = baselineRunConditions?.formFactor === "desktop";

  return {
    baseline: baselineRunConditions,
    expected: expectedConditions,
    status: reviewRequired ? "required" : "not-required",
    changedComponents: entries.length === 0 ? ["run conditions unavailable"] : changedComponents,
    changedReports,
    action: reviewRequired
      ? desktopReference
        ? "Owner review is required before interpreting desktop trends because Lighthouse measurement settings differ from or are missing in the desktop reference."
        : "Owner review is required before interpreting or changing the BrandGuard lab budget because Lighthouse measurement settings differ from or are missing in the historical reference."
      : desktopReference
        ? "No Lighthouse run-condition change from the desktop reference."
        : "No Lighthouse run-condition change from the historical reference.",
  };
}

function describeRunConditionReport(report) {
  const repeatedSample = report.match(/^brandguard-sample-(\d+)\.json$/);
  if (repeatedSample) {
    return `BrandGuard repeated sample ${Number(repeatedSample[1])} (${report})`;
  }
  const route = report.endsWith(".json") ? report.slice(0, -5) : report;
  return `route ${route} (${report})`;
}

export function formatRunConditionsReviewNotice(review) {
  if (review?.status !== "required") return null;
  const changedComponents = review.changedComponents ?? [];
  const changedReports = (review.changedReports ?? [])
    .filter(({ changedComponents: changed }) => changed?.length > 0)
    .sort((left, right) => (
      left.report < right.report ? -1 : left.report > right.report ? 1 : 0
    ))
    .map(({ report, changedComponents: changed }) => (
      `${describeRunConditionReport(report)} (changed settings: ${changed.join(", ")})`
    ));
  const details = changedReports.length > 0
    ? `changed from the historical reference for ${changedReports.join("; ")}`
    : `${changedComponents.join(", ")} changed from the historical reference`;
  return (
    `BRANDGUARD RUN-CONDITION REVIEW REQUIRED: ${details}.`
  );
}

export function parseChromiumVersion(output) {
  const match = String(output).match(/\b(\d+\.\d+\.\d+\.\d+)\b/);
  if (!match) {
    throw new Error(`Could not determine the Chromium version from: ${String(output).trim() || "(empty output)"}`);
  }
  return match[1];
}

function classifyLighthouseTarget(targetUrl) {
  if (typeof targetUrl !== "string" || targetUrl.trim().length === 0) return null;
  let url;
  try {
    url = new URL(targetUrl);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(url.protocol)) return null;
  const hostname = url.hostname.toLowerCase();
  const isLoopback = hostname === "localhost"
    || hostname.endsWith(".localhost")
    || hostname === "[::1]"
    || /^127(?:\.\d{1,3}){3}$/.test(hostname);
  return {
    kind: isLoopback ? "local" : "hosted",
    origin: url.origin,
  };
}

function classifyLighthouseOrigin(targetOrigin) {
  const target = classifyLighthouseTarget(targetOrigin);
  return target && target.origin === targetOrigin ? target : null;
}

function getRawNavigationPathname(targetUrl) {
  if (typeof targetUrl !== "string") return null;
  const match = targetUrl.trim().match(/^[a-z][a-z\d+.-]*:\/\/[^/?#\\]*(.*)$/i);
  if (!match) return null;
  const pathname = match[1].split(/[?#]/, 1)[0];
  return pathname || "/";
}

export function getLighthouseFinalNavigation(report) {
  const finalNavigationUrl = typeof report?.finalDisplayedUrl === "string"
    ? report.finalDisplayedUrl
    : null;
  const finalNavigation = classifyLighthouseTarget(finalNavigationUrl);
  let path = finalNavigation ? getRawNavigationPathname(finalNavigationUrl) : null;
  let search = null;
  let hash = null;
  if (finalNavigation) {
    try {
      const url = new URL(finalNavigationUrl);
      search = url.search;
      hash = url.hash;
    } catch {
      // classifyLighthouseTarget already rejects invalid URLs.
    }
  }
  return {
    url: finalNavigation ? finalNavigationUrl : null,
    origin: finalNavigation?.origin ?? null,
    path,
    search,
    hash,
  };
}

function canonicalNavigationPath(path) {
  if (typeof path !== "string" || path.length === 0) return null;
  // A trailing slash is the only path difference treated as canonical here.
  // Other path changes may select different content and need owner review.
  return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
}

// Search uses q and cat to select visible content. No other measured route
// currently reads query parameters, but unknown parameters remain review-worthy
// because hosting rules or future route behavior may use them. Only recognized
// analytics parameters are ignored when comparing navigation URLs.
const TRACKING_QUERY_PARAMETER_NAMES = new Set([
  "dclid",
  "fbclid",
  "gbraid",
  "gclid",
  "mc_cid",
  "mc_eid",
  "msclkid",
  "wbraid",
]);

function canonicalNavigationQuery(search) {
  if (typeof search !== "string") return null;
  const parameters = [...new URLSearchParams(search).entries()]
    .filter(([name]) => {
      const normalizedName = name.toLowerCase();
      return !normalizedName.startsWith("utm_")
        && !TRACKING_QUERY_PARAMETER_NAMES.has(normalizedName);
    })
    .map(([name, value], index) => ({ name, value, index }))
    .sort((left, right) => (
      left.name < right.name ? -1
        : left.name > right.name ? 1
          : left.index - right.index
    ))
    .map(({ name, value }) => [name, value]);
  return JSON.stringify(parameters);
}

export async function getHostedSourceRevision(
  targetUrl,
  runnerRevision,
  { fetchImpl = globalThis.fetch, timeoutMs = 3000 } = {},
) {
  const target = classifyLighthouseTarget(targetUrl);
  if (target?.kind !== "hosted") {
    return {
      status: "not-applicable",
      revision: null,
      header: DEPLOYED_SOURCE_REVISION_HEADER,
      runnerCheckoutComparison: "not-applicable",
      reason: "The Lighthouse target is local or unavailable.",
    };
  }

  const unavailable = (reason) => ({
    status: "unavailable",
    revision: null,
    header: DEPLOYED_SOURCE_REVISION_HEADER,
    runnerCheckoutComparison: "unavailable",
    reason,
  });
  if (typeof fetchImpl !== "function") {
    return unavailable("HTTP fetch is unavailable in this Node.js runtime.");
  }

  let response;
  try {
    response = await fetchImpl(targetUrl, {
      method: "GET",
      redirect: "follow",
      headers: { "cache-control": "no-cache" },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    return unavailable(`Could not read the hosted target response: ${error.message}`);
  }

  try {
    if (!response.ok) {
      return unavailable(`Hosted target returned HTTP ${response.status}.`);
    }
    const responseUrl = typeof response.url === "string" ? response.url.trim() : "";
    if (!responseUrl) {
      return unavailable(
        "Hosted target revision response did not include a final response URL; the requested origin could not be confirmed.",
      );
    }
    const responseTarget = classifyLighthouseTarget(responseUrl);
    if (!responseTarget) {
      return unavailable(
        "Hosted target revision response ended at an invalid final URL; the requested origin could not be confirmed.",
      );
    }
    if (responseTarget.origin !== target.origin) {
      return unavailable(
        "Hosted target revision response ended at a different origin; the requested origin could not be confirmed.",
      );
    }
    const headerValue = response.headers.get(DEPLOYED_SOURCE_REVISION_HEADER);
    if (headerValue === null) {
      return unavailable(`Hosted target did not send the ${DEPLOYED_SOURCE_REVISION_HEADER} header.`);
    }
    const revision = headerValue.trim().toLowerCase();
    if (!isFullGitObjectId(revision)) {
      return unavailable(
        `Hosted target sent an invalid ${DEPLOYED_SOURCE_REVISION_HEADER} value; expected a full 40- or 64-character Git object ID.`,
      );
    }
    return {
      status: "verified",
      revision,
      header: DEPLOYED_SOURCE_REVISION_HEADER,
      runnerCheckoutComparison: isFullGitObjectId(runnerRevision)
        ? revision === runnerRevision.toLowerCase() ? "matches" : "differs"
        : "unavailable",
    };
  } finally {
    try {
      await response.body?.cancel();
    } catch {
      // The revision header has already been captured; body cleanup is best-effort.
    }
  }
}

function isFullGitObjectId(value) {
  return typeof value === "string" && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/i.test(value);
}

export function compareHostedSourceProvenance(start, end) {
  if (start?.status === "not-applicable" && end?.status === "not-applicable") {
    return { status: "not-applicable", changedDuringRun: null };
  }
  if (start?.status !== "verified" || end?.status !== "verified") {
    return { status: "unavailable", changedDuringRun: null };
  }
  const changedDuringRun = start.revision !== end.revision;
  return {
    status: changedDuringRun ? "changed" : "stable",
    changedDuringRun,
  };
}

export function createLighthouseNavigationReview(requestedTargetUrl, reports = {}) {
  const requestedTarget = classifyLighthouseTarget(requestedTargetUrl);
  const requestedOrigin = requestedTarget?.origin ?? null;
  const reportNavigations = Object.fromEntries(Object.entries(reports).map(([name, entry]) => {
    const report = entry?.report ?? entry;
    const requestedUrl = typeof entry?.requestedUrl === "string" ? entry.requestedUrl : null;
    const requestedNavigation = getLighthouseFinalNavigation({
      finalDisplayedUrl: requestedUrl,
    });
    const finalNavigation = getLighthouseFinalNavigation(report);
    const requestedPath = requestedNavigation.path;
    const canonicalRequestedPath = canonicalNavigationPath(requestedPath);
    const canonicalFinalPath = canonicalNavigationPath(finalNavigation.path);
    const canonicalRequestedQuery = canonicalNavigationQuery(requestedNavigation.search);
    const canonicalFinalQuery = canonicalNavigationQuery(finalNavigation.search);
    const pathMatches = canonicalRequestedPath !== null && canonicalFinalPath !== null
      ? canonicalRequestedPath === canonicalFinalPath
      : null;
    const queryMatches = canonicalRequestedQuery !== null && canonicalFinalQuery !== null
      ? canonicalRequestedQuery === canonicalFinalQuery
      : null;
    const fragmentMatches = requestedNavigation.hash !== null && finalNavigation.hash !== null
      ? requestedNavigation.hash === finalNavigation.hash
      : null;
    const originMatches = requestedNavigation.origin !== null && finalNavigation.origin !== null
      ? requestedNavigation.origin === finalNavigation.origin
      : null;
    const diagnostics = [];
    if (requestedNavigation.url === null) diagnostics.push("requested URL unavailable");
    if (finalNavigation.url === null) diagnostics.push("final displayed URL unavailable");
    if (requestedNavigation.origin === null) diagnostics.push("requested origin unavailable");
    if (finalNavigation.origin === null) diagnostics.push("final origin unavailable");
    if (originMatches === false) diagnostics.push("final origin differs from requested origin");
    if (requestedPath === null) diagnostics.push("requested route path unavailable");
    if (finalNavigation.path === null) diagnostics.push("final route path unavailable");
    if (pathMatches === false) diagnostics.push("final route path differs from requested route");
    if (canonicalRequestedQuery === null) diagnostics.push("requested query unavailable");
    if (canonicalFinalQuery === null) diagnostics.push("final query unavailable");
    if (queryMatches === false) diagnostics.push("final query differs from requested route");
    if (requestedNavigation.hash === null) diagnostics.push("requested fragment unavailable");
    if (finalNavigation.hash === null) diagnostics.push("final fragment unavailable");
    if (fragmentMatches === false) diagnostics.push("final fragment differs from requested route");
    return [name, {
      requestedUrl,
      requestedPath,
      requestedSearch: requestedNavigation.search,
      requestedHash: requestedNavigation.hash,
      url: finalNavigation.url,
      origin: finalNavigation.origin,
      path: finalNavigation.path,
      search: finalNavigation.search,
      hash: finalNavigation.hash,
      pathMatches,
      queryMatches,
      fragmentMatches,
      originMatches,
      diagnostics,
    }];
  }));
  const changedReports = Object.entries(reportNavigations)
    .filter(([, navigation]) => navigation.originMatches === false
      || navigation.pathMatches === false
      || navigation.queryMatches === false
      || navigation.fragmentMatches === false
      || navigation.origin === null
      || navigation.pathMatches === null
      || navigation.queryMatches === null
      || navigation.fragmentMatches === null)
    .map(([name]) => name);
  const changedComponents = [];
  if (requestedOrigin === null) changedComponents.push("requested target origin unavailable");
  if (Object.keys(reportNavigations).length === 0) {
    changedComponents.push("final navigation reports unavailable");
  }
  if (Object.values(reportNavigations).some(({ origin }) => origin === null)) {
    changedComponents.push("final navigation origin unavailable");
  }
  if (Object.values(reportNavigations).some(({ originMatches }) => originMatches === false)) {
    changedComponents.push("final navigation origin differs from requested target");
  }
  if (Object.values(reportNavigations).some(({ pathMatches }) => pathMatches === null)) {
    changedComponents.push("requested or final navigation path unavailable");
  }
  if (Object.values(reportNavigations).some(({ pathMatches }) => pathMatches === false)) {
    changedComponents.push("final navigation path differs from requested route");
  }
  if (Object.values(reportNavigations).some(({ queryMatches }) => queryMatches === null)) {
    changedComponents.push("requested or final navigation query unavailable");
  }
  if (Object.values(reportNavigations).some(({ queryMatches }) => queryMatches === false)) {
    changedComponents.push("final navigation query differs from requested route");
  }
  if (Object.values(reportNavigations).some(({ fragmentMatches }) => fragmentMatches === null)) {
    changedComponents.push("requested or final navigation fragment unavailable");
  }
  if (Object.values(reportNavigations).some(({ fragmentMatches }) => fragmentMatches === false)) {
    changedComponents.push("final navigation fragment differs from requested route");
  }
  const reviewRequired = changedComponents.length > 0;
  return {
    requestedOrigin,
    reports: reportNavigations,
    changedReports,
    status: reviewRequired ? "required" : "not-required",
    changedComponents,
    action: reviewRequired
      ? "Owner review is required before interpreting Lighthouse deltas because one or more reports finished at an unexpected or unavailable origin or route path."
      : "All Lighthouse reports finished at the requested origin and route, allowing trailing-slash canonicalization and recognized tracking parameters.",
  };
}

export function formatLighthouseNavigationReviewNotice(review) {
  if (review?.status !== "required") return null;
  const changedReports = (review.changedReports ?? []).map((name) => {
    const report = review.reports?.[name] ?? {};
    const diagnostics = Array.isArray(report.diagnostics) && report.diagnostics.length > 0
      ? `; diagnostics: ${report.diagnostics.join(", ")}`
      : "";
    const requestedUrl = report.requestedUrl ?? "unavailable";
    const finalUrl = report.url ?? "unavailable";
    return `${name} (requested ${requestedUrl}; final ${finalUrl}${diagnostics})`;
  });
  return (
    `LIGHTHOUSE NAVIGATION REVIEW REQUIRED: ${review.changedComponents.join(", ")}. `
    + `Requested origin: ${review.requestedOrigin ?? "unavailable"}; `
    + `affected reports: ${changedReports.join(", ") || "none"}.`
  );
}

export function createLighthouseTargetReview(targetUrl, baselineTargetOrigin) {
  const current = classifyLighthouseTarget(targetUrl);
  const baseline = classifyLighthouseOrigin(baselineTargetOrigin);
  const changedComponents = [];
  if (!current) changedComponents.push("current target unavailable");
  if (!baseline) changedComponents.push("reference origin unavailable");
  if (current && baseline) {
    if (current.origin !== baseline.origin) {
      changedComponents.push("origin");
    }
    if (current.kind !== baseline.kind) {
      changedComponents.push("targetKind");
    }
  }
  const reviewRequired = changedComponents.length > 0;
  return {
    baseline: baseline ?? { kind: "unavailable", origin: null },
    current: current ?? { kind: "unavailable", origin: null },
    status: reviewRequired ? "required" : "not-required",
    changedComponents,
    action: reviewRequired
      ? "Owner review is required before interpreting Lighthouse deltas because the target origin differs from or is unavailable in the matching reference."
      : "No local-versus-hosted target change from the reference.",
  };
}

export function formatLighthouseTargetReviewNotice(review) {
  if (review?.status !== "required") return null;
  const changedComponents = review.changedComponents ?? [];
  const currentOrigin = review.current?.origin ?? "unavailable";
  const referenceOrigin = review.baseline?.origin ?? "unavailable";
  return (
    `LIGHTHOUSE TARGET REVIEW REQUIRED: ${changedComponents.join(", ")}. `
    + `Run origin: ${currentOrigin}; matching reference origin: ${referenceOrigin}.`
  );
}

export function createSummary({
  date,
  preset,
  controlled,
  baseUrl,
  measurementStack,
  baselineMeasurementStack,
  runConditionsReports = {},
  baselineRunConditions = HISTORICAL_REFERENCE_RUN_CONDITIONS,
  baselineTargetOrigin = null,
  baselinePath = MOBILE_REFERENCE_PATH,
  measurementStackReferenceNote = "Historical reference only. The exact Chromium 148.0.7778.96 build is inferred from Playwright metadata; historical reports establish major version 148, not an independently owner-approved exact build.",
  referenceApproval = null,
  referenceIntegrityFingerprint = null,
  sourceRevision = null,
  sourceWorkingTreeClean = null,
  sourceRevisionStatus = "unavailable",
  sourceRevisionError = "Git source provenance was not supplied.",
  baselineSourceRevision = null,
  baselineSourceWorkingTreeClean = null,
  baselineSourceRevisionStatus = "not-recorded",
  baselineSourceRevisionNote = "The historical reference does not record its source revision or working-tree state.",
  deployedSourceProvenanceRun = null,
}) {
  const versionFields = ["lighthouseVersion", "chromiumVersion"];
  const baselineKnown = versionFields.every((field) => Boolean(baselineMeasurementStack?.[field]));
  const changedComponents = baselineKnown
    ? versionFields.filter((field) => measurementStack[field] !== baselineMeasurementStack[field])
    : ["baseline measurement stack unavailable"];
  const reviewRequired = changedComponents.length > 0;
  const targetReview = createLighthouseTargetReview(baseUrl, baselineTargetOrigin);
  const targetKind = targetReview.current.kind;
  const environmentTarget = targetKind === "local"
    ? "Local target"
    : targetKind === "hosted"
      ? "Hosted target"
      : "Unknown target";

  return {
    schemaVersion: 12,
    capturedAt: date,
    tool: `Lighthouse ${measurementStack.lighthouseVersion}`,
    sourceRevision,
    sourceWorkingTreeClean,
    sourceRevisionStatus,
    ...(sourceRevisionError ? { sourceRevisionError } : {}),
    deployedSourceProvenanceRun: deployedSourceProvenanceRun ?? {
      status: "unavailable",
      start: null,
      end: null,
      changedDuringRun: null,
      reason: "Hosted source revision metadata was not collected.",
    },
    baselineSourceRevision,
    baselineSourceWorkingTreeClean,
    baselineSourceRevisionStatus,
    baselineSourceRevisionNote,
    measurementStack,
    measurementStackReview: {
      baseline: baselineMeasurementStack ?? null,
      referenceNote: measurementStackReferenceNote,
      status: reviewRequired ? "required" : "not-required",
      changedComponents,
      action: reviewRequired
        ? preset === "desktop"
          ? "Owner review is required before interpreting desktop trends."
          : "Owner review is required before interpreting or changing the BrandGuard lab budget."
        : preset === "desktop"
          ? "No Lighthouse or Chromium version change from the desktop reference measurement stack."
          : "No Lighthouse or Chromium version change from the historical reference measurement stack.",
    },
    runConditions: {
      condition: controlled ? "controlled" : "normal",
      reports: runConditionsReports,
    },
    runConditionsReview: createRunConditionsReview(runConditionsReports, {
      controlled,
      baselineRunConditions,
    }),
    environment: controlled
      ? `${environmentTarget}, controlled mobile preset`
      : `${environmentTarget}, ${preset} preset`,
    property: baseUrl,
    targetReview: targetReview,
    baseline: baselinePath,
    ...(referenceApproval === null ? {} : {
      referenceApproval: summarizeReferenceApproval(
        referenceApproval,
        referenceIntegrityFingerprint,
      ),
    }),
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

/**
 * Summarize desktop reference approval with a stable machine-readable reason:
 * "missing" (no approval or approval fingerprint), "incomplete" (required
 * approval details are absent or blank), "malformed" (invalid approval
 * fingerprint), "unavailable-current" (invalid current fingerprint), "changed"
 * (fingerprints differ), or "approved" (fingerprints match).
 */
export function summarizeReferenceApproval(approval, currentIntegrityFingerprint = null) {
  if (approval == null) {
    return {
      status: "not-recorded",
      reason: "missing",
      ownerApproved: false,
      approvalIntegrityFingerprint: null,
      currentIntegrityFingerprint,
      action: "No owner approval is recorded for this reference; treat desktop trend deltas as exploratory.",
    };
  }

  const hasStatus = typeof approval === "object"
    && Object.hasOwn(approval, "status");
  const status = hasStatus ? approval.status : undefined;
  if (typeof status !== "string" || status.trim().length === 0) {
    return {
      status: "stale",
      reason: "malformed-status",
      ownerApproved: false,
      approvalStatus: status ?? null,
      approvalStatusPresent: hasStatus,
      currentIntegrityFingerprint,
      action: `The desktop reference approval status is malformed${hasStatus ? ` (${typeof status})` : " (missing)"}; it is not treated as owner-approved. Record a supported approval status before interpreting desktop trend deltas.`,
    };
  }
  if (!["not-recorded", "owner-approved"].includes(status)) {
    return {
      status: "stale",
      reason: "unsupported-status",
      ownerApproved: false,
      approvalStatus: status,
      approvalStatusPresent: true,
      currentIntegrityFingerprint,
      action: `The desktop reference uses unsupported approval status ${JSON.stringify(status)}; it is not treated as owner-approved. Update the approval record to a supported status before interpreting desktop trend deltas.`,
    };
  }
  if (status === "not-recorded") {
    return {
      status,
      reason: "missing",
      ownerApproved: false,
      approvalIntegrityFingerprint: null,
      currentIntegrityFingerprint,
      action: "No owner approval is recorded for this reference; treat desktop trend deltas as exploratory.",
    };
  }

  const approvalRecord = {
    approvedAt: approval.approvedAt ?? null,
    approvedBy: approval.approvedBy ?? null,
    approvalRecord: approval.approvalRecord ?? null,
  };
  const missingFields = Object.entries(approvalRecord)
    .filter(([, value]) => typeof value !== "string" || value.trim().length === 0)
    .map(([field]) => field);
  if (missingFields.length > 0) {
    return {
      status: "stale",
      reason: "incomplete",
      ownerApproved: false,
      ...approvalRecord,
      missingFields,
      approvalIntegrityFingerprint: approval.integrityFingerprint ?? null,
      currentIntegrityFingerprint,
      action: `This owner approval is missing required details (${missingFields.join(", ")}); complete the approval date, owner, and evidence reference before interpreting desktop trend deltas.`,
    };
  }
  const approvalIntegrityFingerprint = approval.integrityFingerprint;
  const isIntegrityFingerprint = (value) =>
    typeof value === "string" && /^sha256:[a-f0-9]{64}$/.test(value);
  const approvalFingerprintMissing =
    approvalIntegrityFingerprint == null
    || (
      typeof approvalIntegrityFingerprint === "string"
      && approvalIntegrityFingerprint.trim().length === 0
    );
  if (approvalFingerprintMissing || !isIntegrityFingerprint(approvalIntegrityFingerprint)) {
    return {
      status: "stale",
      reason: approvalFingerprintMissing ? "missing" : "malformed",
      ownerApproved: false,
      ...approvalRecord,
      approvalIntegrityFingerprint: approvalIntegrityFingerprint ?? null,
      currentIntegrityFingerprint,
      action: approvalFingerprintMissing
        ? "This owner approval has no integrity fingerprint and cannot be trusted; record a fresh owner decision before interpreting desktop trend deltas."
        : "This owner approval has a malformed integrity fingerprint and cannot be trusted; record a fresh owner decision before interpreting desktop trend deltas.",
    };
  }
  if (!isIntegrityFingerprint(currentIntegrityFingerprint)) {
    return {
      status: "stale",
      reason: "unavailable-current",
      ownerApproved: false,
      ...approvalRecord,
      approvalIntegrityFingerprint,
      currentIntegrityFingerprint,
      action: "The current desktop reference integrity fingerprint is unavailable or malformed; record a fresh owner decision before interpreting desktop trend deltas.",
    };
  }
  if (
    approvalIntegrityFingerprint !== currentIntegrityFingerprint
  ) {
    return {
      status: "stale",
      reason: "changed",
      ownerApproved: false,
      ...approvalRecord,
      approvalIntegrityFingerprint,
      currentIntegrityFingerprint,
      action: "The desktop reference changed since owner approval; record a fresh owner decision before interpreting desktop trend deltas.",
    };
  }
  return {
    status,
    reason: "approved",
    ownerApproved: true,
    ...approvalRecord,
    approvalIntegrityFingerprint,
    currentIntegrityFingerprint,
  };
}

// Lighthouse reports category scores in [0, 1], or null when they are unscored.
const SUMMARY_CATEGORY_SCORE_RANGE = { min: 0, max: 1 };

// These audit numericValues are optional, nonnegative measurements. CLS has no
// upper bound: its accumulated layout-shift score can legitimately exceed 1.
const SUMMARY_AUDIT_NUMERIC_VALUE_RANGES = {
  "largest-contentful-paint": { min: 0, max: Number.POSITIVE_INFINITY },
  "cumulative-layout-shift": { min: 0, max: Number.POSITIVE_INFINITY },
  "total-blocking-time": { min: 0, max: Number.POSITIVE_INFINITY },
  "first-contentful-paint": { min: 0, max: Number.POSITIVE_INFINITY },
  "speed-index": { min: 0, max: Number.POSITIVE_INFINITY },
};

export function summarizePage({ report, path, baselinePage = {} }) {
  const audits = report.audits ?? {};
  const finalNavigation = getLighthouseFinalNavigation(report);
  const unavailableMetrics = [];
  const isWithinRange = (value, { min, max }) =>
    typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
  const metric = (id, field) => {
    const value = audits[id]?.numericValue;
    if (!isWithinRange(value, SUMMARY_AUDIT_NUMERIC_VALUE_RANGES[id])) {
      unavailableMetrics.push({ field, source: `audits.${id}.numericValue` });
      return null;
    }
    return value;
  };
  const categoryScore = (id, field) => {
    const value = report.categories?.[id]?.score;
    if (!isWithinRange(value, SUMMARY_CATEGORY_SCORE_RANGE)) {
      unavailableMetrics.push({ field, source: `categories.${id}.score` });
      return null;
    }
    return Math.round(value * 100);
  };
  const legacyLcpElement = audits["largest-contentful-paint-element"]?.details?.items?.[0]?.items?.[0]?.node;
  const insightItems = audits["lcp-breakdown-insight"]?.details?.items;
  const insightLcpElement = Array.isArray(insightItems)
    ? insightItems.find((item) => item?.type === "node"
      && typeof item.selector === "string" && item.selector.trim().length > 0)
    : null;
  const lcpElement = typeof legacyLcpElement?.selector === "string"
    && legacyLcpElement.selector.trim().length > 0 ? legacyLcpElement : insightLcpElement;
  const metricsItems = Array.isArray(audits.metrics?.details?.items)
    ? audits.metrics.details.items
    : [];
  // Repeated metric rows are usable only when every supplied value is a
  // boolean and all supplied values agree. Conflicts and malformed entries
  // must not be hidden by whichever valid value happens to appear first.
  const lcpInvalidatedEntries = metricsItems
    .filter((item) => item !== null
      && typeof item === "object"
      && Object.hasOwn(item, "lcpInvalidated"))
    .map((item) => item.lcpInvalidated);
  const lcpInvalidatedValues = lcpInvalidatedEntries
    .filter((value) => typeof value === "boolean");
  const lcpInvalidatedValuesAgree = lcpInvalidatedValues.length > 0
    && lcpInvalidatedValues.every((value) => value === lcpInvalidatedValues[0]);
  const hasMalformedLcpInvalidated = lcpInvalidatedValues.length !== lcpInvalidatedEntries.length;
  const hasConflictingLcpInvalidated = lcpInvalidatedValues.length > 1
    && !lcpInvalidatedValuesAgree;
  const lcpInvalidated = lcpInvalidatedEntries.length > 0
    && !hasMalformedLcpInvalidated && !hasConflictingLcpInvalidated
    ? lcpInvalidatedValues[0]
    : null;
  if (hasMalformedLcpInvalidated || hasConflictingLcpInvalidated) {
    unavailableMetrics.push({
      field: "lcpInvalidated",
      source: "audits.metrics.details.items[].lcpInvalidated",
    });
  }
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
      source: "audits.largest-contentful-paint-element.details.items[0].items[0].node.selector or audits.lcp-breakdown-insight.details.items[type=node].selector",
    });
  }
  const lcpMs = lcpValue === null ? null : Math.round(lcpValue);

  return {
    path,
    finalNavigationUrl: finalNavigation.url,
    finalNavigationOrigin: finalNavigation.origin,
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

export function formatUnavailableMetricsNotice(
  pages,
  { includeSources = false, reportNames = {} } = {},
) {
  const affectedRoutes = Object.entries(pages ?? {}).flatMap(([name, page]) => {
    const metrics = Array.isArray(page?.unavailableMetrics) ? page.unavailableMetrics : [];
    const fields = [...new Set(
      metrics
        .map((metric) => metric?.field)
        .filter((field) => typeof field === "string" && field.length > 0),
    )];
    if (fields.length === 0) return [];

    const reportName = reportNames[name];
    const reportLabel = typeof reportName === "string" && reportName.length > 0
      ? ` (${reportName})`
      : "";
    const lines = [`  ${page.path || name}${reportLabel}: ${fields.join(", ")}`];
    if (includeSources) {
      const sources = new Set();
      for (const metric of metrics) {
        if (
          typeof metric?.field === "string"
          && metric.field.length > 0
          && typeof metric?.source === "string"
          && metric.source.length > 0
        ) {
          sources.add(`${metric.field}: ${metric.source}`);
        }
      }
      lines.push(...[...sources].map((source) => `    ${source}`));
    }
    return lines;
  });
  return affectedRoutes.length
    ? `Unavailable Lighthouse metrics:\n${affectedRoutes.join("\n")}`
    : "";
}

export function formatUnavailableBrandGuardSampleMetricsNotice(
  samples,
  { includeSources = false, alreadyReportedFirstSampleMetrics = [] } = {},
) {
  const numericFields = ["cls", "fcpMs", "speedIndexMs", "lcpMs", "tbtMs"];
  const categoryFields = ["accessibility", "bestPractices", "seo"];
  const sourceByField = {
    accessibility: "categories.accessibility.score",
    bestPractices: "categories.best-practices.score",
    seo: "categories.seo.score",
    cls: "audits.cumulative-layout-shift.numericValue",
    fcpMs: "audits.first-contentful-paint.numericValue",
    speedIndexMs: "audits.speed-index.numericValue",
    lcpMs: "audits.largest-contentful-paint.numericValue",
    tbtMs: "audits.total-blocking-time.numericValue",
    lcpInvalidated: "audits.metrics.details.items[].lcpInvalidated",
  };
  const alreadyReported = new Set(
    alreadyReportedFirstSampleMetrics
      .map((metric) => metric?.field)
      .filter((field) => typeof field === "string" && field.length > 0),
  );
  const affectedSamples = (Array.isArray(samples) ? samples : [])
    .flatMap(({ report, page }, index) => {
      const fieldSources = new Map();
      for (const metric of Array.isArray(page?.unavailableMetrics)
        ? page.unavailableMetrics
        : []) {
        if (typeof metric?.field !== "string" || metric.field.length === 0) continue;
        if (!fieldSources.has(metric.field)) fieldSources.set(metric.field, new Set());
        if (typeof metric.source === "string" && metric.source.length > 0) {
          fieldSources.get(metric.field).add(metric.source);
        } else if (sourceByField[metric.field]) {
          fieldSources.get(metric.field).add(sourceByField[metric.field]);
        }
      }
      for (const field of numericFields) {
        if (typeof page?.[field] !== "number" || !Number.isFinite(page[field])) {
          if (!fieldSources.has(field)) {
            fieldSources.set(field, new Set(sourceByField[field] ? [sourceByField[field]] : []));
          }
        }
      }
      for (const field of categoryFields) {
        if (typeof page?.[field] !== "number" || !Number.isFinite(page[field])) {
          if (!fieldSources.has(field)) {
            fieldSources.set(field, new Set(sourceByField[field] ? [sourceByField[field]] : []));
          }
        }
      }
      if (typeof page?.lcpInvalidated !== "boolean" && !fieldSources.has("lcpInvalidated")) {
        fieldSources.set(
          "lcpInvalidated",
          new Set(sourceByField.lcpInvalidated ? [sourceByField.lcpInvalidated] : []),
        );
      }
      if (index === 0) {
        for (const field of alreadyReported) fieldSources.delete(field);
      }
      if (fieldSources.size === 0) return [];

      const reportName = typeof report === "string" && report.length > 0
        ? report
        : "report unavailable";
      const lines = [
        `  sample ${index + 1} (${reportName}): ${[...fieldSources.keys()].join(", ")}`,
      ];
      if (includeSources) {
        for (const [field, sources] of fieldSources) {
          for (const source of sources) lines.push(`    ${field}: ${source}`);
        }
      }
      return lines;
    });
  return affectedSamples.length
    ? `Unavailable repeated BrandGuard sample metrics:\n${affectedSamples.join("\n")}`
    : "";
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
      spread: min === null ? null : Number((max - min).toFixed(6)),
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
      accessibility: metricRange("accessibility"),
      bestPractices: metricRange("bestPractices"),
      seo: metricRange("seo"),
      cls: metricRange("cls"),
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
    samples: samples.map(({ report, page }, index) => {
      const unavailableMetrics = Array.isArray(page.unavailableMetrics)
        ? [...page.unavailableMetrics]
        : [];
      if (
        typeof page.lcpInvalidated !== "boolean"
        && !unavailableMetrics.some((metric) => metric?.field === "lcpInvalidated")
      ) {
        unavailableMetrics.push({
          field: "lcpInvalidated",
          source: "audits.metrics.details.items[].lcpInvalidated",
        });
      }
      return {
        sample: index + 1,
        report,
        accessibility: page.accessibility,
        bestPractices: page.bestPractices,
        seo: page.seo,
        cls: page.cls,
        fcpMs: page.fcpMs,
        speedIndexMs: page.speedIndexMs,
        lcpMs: page.lcpMs,
        tbtMs: page.tbtMs,
        lcpInvalidated: page.lcpInvalidated,
        unavailableMetrics,
      };
    }),
  };
}

export function formatBrandGuardSampleScores(samples) {
  if (!Array.isArray(samples)) return "";

  const formatMetric = (value) => (
    typeof value === "number" && Number.isFinite(value) ? String(value) : "unavailable"
  );
  return samples.map(({ sample, report, accessibility, bestPractices, seo, cls }, index) => (
    `  sample ${sample ?? index + 1} (${report || "unknown report"}): `
    + `accessibility ${formatMetric(accessibility)}; `
    + `best-practices ${formatMetric(bestPractices)}; `
    + `SEO ${formatMetric(seo)}; CLS ${formatMetric(cls)}`
  )).join("\n");
}

export function summarizeDesktopRepeats(passes) {
  if (!Array.isArray(passes) || passes.length < 2) {
    throw new Error("At least two complete desktop passes are required.");
  }
  for (const [index, pass] of passes.entries()) {
    const missingRoutes = Object.keys(routes).filter((name) => !pass?.pages?.[name]);
    if (missingRoutes.length > 0) {
      throw new Error(
        `Desktop pass ${index + 1} is incomplete; missing routes: ${missingRoutes.join(", ")}.`,
      );
    }
  }

  const metricFields = ["performance", "lcpMs", "fcpMs", "speedIndexMs", "tbtMs"];
  const metricRange = (routePasses, field) => {
    const values = routePasses
      .map(({ page }) => page[field])
      .filter((value) => typeof value === "number" && Number.isFinite(value));
    const min = values.length ? Math.min(...values) : null;
    const max = values.length ? Math.max(...values) : null;
    return {
      availableCount: values.length,
      unavailableCount: routePasses.length - values.length,
      min,
      max,
      spread: min === null ? null : max - min,
    };
  };

  const routeSummaries = Object.fromEntries(Object.keys(routes).map((name) => {
    const routePasses = passes.map(({ pass, pages }) => ({
      pass,
      report: pages[name].report,
      page: pages[name].page,
    }));
    return [name, {
      path: routes[name],
      metrics: Object.fromEntries(
        metricFields.map((field) => [field, metricRange(routePasses, field)]),
      ),
      passes: routePasses.map(({ pass, report, page }) => ({
        pass,
        report,
        ...Object.fromEntries(metricFields.map((field) => [field, page[field] ?? null])),
        unavailableMetrics: Array.isArray(page.unavailableMetrics)
          ? page.unavailableMetrics.map((metric) => ({ ...metric }))
          : [],
      })),
    }];
  }));

  return {
    preset: "desktop",
    condition: "normal",
    passCount: passes.length,
    routes: routeSummaries,
  };
}

export function formatUnavailableDesktopRepeatMetricsNotice(repeatability) {
  const metricFields = new Set(["performance", "lcpMs", "fcpMs", "speedIndexMs", "tbtMs"]);
  const affectedPasses = Object.entries(repeatability?.routes ?? {}).flatMap(
    ([routeName, routeSummary]) => (Array.isArray(routeSummary?.passes)
      ? routeSummary.passes
      : []).flatMap((pass) => {
      const fields = [...new Set(
        (Array.isArray(pass?.unavailableMetrics) ? pass.unavailableMetrics : [])
          .map((metric) => metric?.field)
          .filter((field) => metricFields.has(field)),
      )];
      if (fields.length === 0) return [];

      const reportName = typeof pass.report === "string" && pass.report.length > 0
        ? pass.report
        : "report unavailable";
      return [`  ${routeName} pass ${pass.pass} (${reportName}): ${fields.join(", ")}`];
    }),
  );
  return affectedPasses.length
    ? `Unavailable desktop repeat metrics; inspect the saved pass reports:\n${affectedPasses.join("\n")}`
    : "";
}

let writeIncompleteRunStatus = null;

function readLighthouseReport(reportPath, routeLabel, reportName) {
  let reportContents;
  try {
    reportContents = readFileSync(reportPath, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(
        `Lighthouse exited successfully but did not write the requested report for ${routeLabel} (${reportName}) at ${reportPath}.`,
      );
    }
    throw error;
  }
  try {
    return JSON.parse(reportContents);
  } catch (error) {
    throw new Error(
      `Could not parse Lighthouse report for ${routeLabel} (${reportName}): ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

function parseJsonWithoutDuplicateProperties(contents, sourcePath) {
  let index = 0;
  const skipWhitespace = () => {
    while (index < contents.length && /\s/.test(contents[index])) index += 1;
  };
  const readString = () => {
    const start = index;
    if (contents[index] !== '"') throw new SyntaxError("invalid JSON string");
    index += 1;
    while (index < contents.length) {
      const character = contents[index];
      index += 1;
      if (character === '"') {
        return JSON.parse(contents.slice(start, index));
      }
      if (character === "\\") index += 1;
      else if (character.charCodeAt(0) < 0x20) {
        throw new SyntaxError("invalid JSON string");
      }
    }
    throw new SyntaxError("invalid JSON string");
  };
  const scanValue = (valuePath) => {
    skipWhitespace();
    if (contents[index] === "{") {
      index += 1;
      skipWhitespace();
      if (contents[index] === "}") {
        index += 1;
        return;
      }
      const keys = new Set();
      while (index < contents.length) {
        skipWhitespace();
        const key = readString();
        if (keys.has(key)) {
          throw new Error(
            `duplicate property ${JSON.stringify(key)} at ${valuePath} in ${sourcePath}`,
          );
        }
        keys.add(key);
        skipWhitespace();
        if (contents[index] !== ":") throw new SyntaxError("invalid JSON object");
        index += 1;
        scanValue(`${valuePath}[${JSON.stringify(key)}]`);
        skipWhitespace();
        if (contents[index] === "}") {
          index += 1;
          return;
        }
        if (contents[index] !== ",") throw new SyntaxError("invalid JSON object");
        index += 1;
      }
      throw new SyntaxError("invalid JSON object");
    }
    if (contents[index] === "[") {
      index += 1;
      skipWhitespace();
      if (contents[index] === "]") {
        index += 1;
        return;
      }
      let itemIndex = 0;
      while (index < contents.length) {
        scanValue(`${valuePath}[${itemIndex}]`);
        itemIndex += 1;
        skipWhitespace();
        if (contents[index] === "]") {
          index += 1;
          return;
        }
        if (contents[index] !== ",") throw new SyntaxError("invalid JSON array");
        index += 1;
      }
      throw new SyntaxError("invalid JSON array");
    }
    if (contents[index] === '"') {
      readString();
      return;
    }
    const primitiveStart = index;
    while (
      index < contents.length
      && !/[\s,\]}]/.test(contents[index])
    ) {
      index += 1;
    }
    if (index === primitiveStart) throw new SyntaxError("invalid JSON value");
  };

  scanValue("$");
  skipWhitespace();
  if (index !== contents.length) throw new SyntaxError("unexpected content after JSON value");
  return JSON.parse(contents);
}

async function main() {
  const root = resolve(import.meta.dirname, "..");
  if (process.argv.includes("-h") || process.argv.includes("--help")) {
    console.log(formatScriptHelp());
    return;
  }
  const optionError = validateScriptOptions(process.argv.slice(2));
  if (optionError) {
    console.error(optionError);
    process.exitCode = 1;
    return;
  }
  if (process.argv.includes("--print-desktop-reference-fingerprint")) {
    const baselinePath = resolve(root, DESKTOP_REFERENCE_PATH);
    let baselineContents;
    try {
      baselineContents = readFileSync(baselinePath, "utf8");
    } catch (error) {
      if (error?.code === "ENOENT") {
        throw new Error(
          `Desktop Lighthouse reference ${DESKTOP_REFERENCE_PATH} is missing. Restore the desktop reference file before printing its fingerprint.`,
        );
      }
      throw new Error(
        `Desktop Lighthouse reference ${DESKTOP_REFERENCE_PATH} could not be read: ${error.message}`,
      );
    }
    let baseline;
    try {
      baseline = parseJsonWithoutDuplicateProperties(
        baselineContents,
        DESKTOP_REFERENCE_PATH,
      );
    } catch (error) {
      throw new Error(
        `Desktop Lighthouse reference ${DESKTOP_REFERENCE_PATH} could not be parsed: ${error.message}`,
      );
    }
    let reference;
    try {
      reference = getLighthouseReference("desktop", baseline);
    } catch (error) {
      throw new Error(
        `Desktop Lighthouse reference ${DESKTOP_REFERENCE_PATH} is invalid: ${error.message}`,
      );
    }
    console.log(reference.integrityFingerprint);
    return;
  }

  const defaultBaseUrl = process.env.LIGHTHOUSE_BASE_URL || "http://127.0.0.1:5000";
  const baseUrlArg = process.argv.find((arg) => arg.startsWith("--base-url="));
  const baseUrl = (baseUrlArg ? baseUrlArg.slice("--base-url=".length) : defaultBaseUrl).replace(/\/+$/, "");
  const presetArg = process.argv.find((arg) => arg.startsWith("--preset="));
  const preset = presetArg ? presetArg.slice("--preset=".length) : "desktop";
  if (!["desktop", "mobile"].includes(preset)) {
    console.error(`Unsupported Lighthouse preset: ${preset}. Use desktop or mobile.`);
    process.exit(1);
  }
  const baselineReferencePath = preset === "desktop"
    ? DESKTOP_REFERENCE_PATH
    : MOBILE_REFERENCE_PATH;
  const baselinePath = resolve(root, baselineReferencePath);
  const dateArg = process.argv.find((arg) => arg.startsWith("--date="));
  const date = dateArg ? dateArg.slice("--date=".length) : new Date().toISOString().slice(0, 10);
  const parsedDate = new Date(`${date}T00:00:00.000Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(date)
    || Number.isNaN(parsedDate.valueOf())
    || parsedDate.toISOString().slice(0, 10) !== date
  ) {
    console.error("Lighthouse run date must be a valid YYYY-MM-DD date.");
    process.exit(1);
  }
  const runIdArg = process.argv.find((arg) => arg.startsWith("--run-id="));
  const runId = runIdArg ? runIdArg.slice("--run-id=".length) : "";
  if (runIdArg && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(runId)) {
    console.error(
      "Run identifier must start with a letter or number and contain only letters, numbers, periods, underscores, or hyphens (up to 64 characters).",
    );
    process.exit(1);
  }
  const replaceExisting = process.argv.includes("--replace");
  const controlled = process.argv.includes("--controlled");
  const showUnavailableSources = process.argv.includes("--show-unavailable-sources");
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
  const desktopRepeatsArg = process.argv.find((arg) => arg.startsWith("--desktop-repeats="));
  const desktopRepeats = desktopRepeatsArg
    ? Number(desktopRepeatsArg.slice("--desktop-repeats=".length))
    : 1;
  if (
    desktopRepeatsArg
    && (!Number.isInteger(desktopRepeats) || desktopRepeats < 2 || desktopRepeats > 10)
  ) {
    console.error("Desktop repeat count must be a whole number from 2 to 10.");
    process.exit(1);
  }
  if (desktopRepeatsArg && preset !== "desktop") {
    console.error("Repeated complete passes are only supported with --preset=desktop.");
    process.exit(1);
  }
  if (controlled && preset !== "mobile") {
    console.error("The controlled third-party isolation mode is only supported with --preset=mobile.");
    process.exit(1);
  }
  const outputSuffix = `${preset === "mobile" ? "-mobile" : ""}${controlled ? "-controlled" : ""}`;
  const desktopRepeatSuffix = desktopRepeats > 1 ? `-desktop-repeats-${desktopRepeats}` : "";
  const runIdSuffix = runId ? `-${runId}` : "";
  const outputDir = resolve(
    root,
    "assets/audit",
    `lighthouse-${date}${outputSuffix}${desktopRepeatSuffix}${runIdSuffix}`,
  );
  // Ignore this run's reports in both snapshots so generated evidence cannot
  // make a clean checkout look like its source changed during measurement.
  const outputRelativePath = relative(root, outputDir).split(sep).join("/");
  const sourceProvenanceStart = getSourceRevision(root, {
    excludePaths: [outputRelativePath],
  });
  const collisionMessage = () => (
    `Lighthouse evidence already exists at ${outputDir}. Refusing to overwrite it. `
    + "Choose a new identifier with --run-id=<identifier> (for example --run-id=rerun-2), "
    + "or explicitly replace this evidence with --replace."
  );
  if (existsSync(outputDir) && !replaceExisting) {
    console.error(collisionMessage());
    process.exit(1);
  }
  const referenceLabel = `${preset === "desktop" ? "Desktop" : "Mobile"} Lighthouse reference`;
  let baselineContents;
  try {
    baselineContents = readFileSync(baselinePath, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    throw new Error(
      `${referenceLabel} ${baselineReferencePath} is missing. Restore the reference file before running Lighthouse.`,
    );
  }
  let baseline;
  try {
    baseline = preset === "desktop"
      ? parseJsonWithoutDuplicateProperties(baselineContents, baselineReferencePath)
      : JSON.parse(baselineContents);
  } catch (error) {
    throw new Error(
      `${referenceLabel} ${baselineReferencePath} is malformed and could not be parsed: ${error.message} Replace it with a valid JSON reference file before running Lighthouse.`,
    );
  }
  let reference;
  try {
    reference = getLighthouseReference(preset, baseline);
  } catch (error) {
    throw new Error(
      `${referenceLabel} ${baselineReferencePath} is invalid: ${error.message} Restore or correct the reference file before running Lighthouse.`,
    );
  }
  const lighthouseBin = resolve(root, "node_modules/.bin/lighthouse");
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

  if (replaceExisting) {
    rmSync(outputDir, { recursive: true, force: true });
  }
  try {
    mkdirSync(outputDir);
  } catch (error) {
    if (error.code === "EEXIST") {
      console.error(collisionMessage());
      process.exit(1);
    }
    throw error;
  }
  const runStartedAt = new Date().toISOString();
  const runStatusPath = resolve(outputDir, "run-status.json");
  const writeRunStatus = (status, details = {}) => {
    const temporaryStatusPath = `${runStatusPath}.tmp`;
    writeFileSync(temporaryStatusPath, `${JSON.stringify({
      status,
      startedAt: runStartedAt,
      ...details,
    }, null, 2)}\n`);
    renameSync(temporaryStatusPath, runStatusPath);
  };
  writeIncompleteRunStatus = (error) => {
    try {
      writeRunStatus("incomplete", {
        finishedAt: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      });
    } catch (statusError) {
      console.error(
        `Could not update Lighthouse run status: ${statusError instanceof Error ? statusError.message : String(statusError)}`,
      );
    }
  };
  // Leave this status in place if collection is interrupted before the final
  // summary and completion marker can be written.
  writeRunStatus("incomplete", { reason: "The Lighthouse run has not completed." });
  const checkoutChangeMonitor = createCheckoutChangeMonitor(root, {
    excludePaths: [outputRelativePath],
  });
  const deployedSourceProvenanceStart = await getHostedSourceRevision(
    baseUrl,
    sourceProvenanceStart.revision,
  );
  process.on("uncaughtExceptionMonitor", (error) => {
    try {
      writeRunStatus("incomplete", {
        finishedAt: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      });
    } catch (statusError) {
      console.error(
        `Could not update Lighthouse run status: ${statusError instanceof Error ? statusError.message : String(statusError)}`,
      );
    }
  });
  let lighthouseVersion = null;
  let chromiumUserAgent = null;
  const pages = {};
  const desktopPasses = [];
  const runConditionsReports = {};
  const navigationReports = {};

  for (let passIndex = 1; passIndex <= desktopRepeats; passIndex += 1) {
    checkoutChangeMonitor.beginMeasurement("pass", passIndex);
    const passDirectory = desktopRepeats > 1
      ? `pass-${String(passIndex).padStart(2, "0")}`
      : "";
    if (passDirectory) mkdirSync(resolve(outputDir, passDirectory));
    const passPages = {};
    for (const [name, path] of Object.entries(routes)) {
      const reportName = `${passDirectory ? `${passDirectory}/` : ""}${name}.json`;
      const reportPath = resolve(outputDir, reportName);
      const url = `${baseUrl}${path}`;
      console.log(
        desktopRepeats > 1
          ? `Running desktop pass ${passIndex}/${desktopRepeats}, ${name}: ${url}`
          : `Running ${name}: ${url}`,
      );
      execFileSync(lighthouseBin, [
        url,
        "--output=json",
        `--output-path=${reportPath}`,
        ...(preset === "desktop" ? ["--preset=desktop"] : ["--form-factor=mobile"]),
        ...(controlled
          ? CONTROLLED_BLOCKED_URL_PATTERNS.map((pattern) => `--blocked-url-patterns=${pattern}`)
          : []),
        CHROME_FLAGS_ARGUMENT,
        "--quiet",
      ], { cwd: root, stdio: "inherit", env: { ...process.env, CHROME_PATH: chromePath } });
      await checkoutChangeMonitor.flush();

      const report = readLighthouseReport(reportPath, name, reportName);
      navigationReports[reportName] = { requestedUrl: url, report };
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
      runConditionsReports[reportName] = summarizeRunConditions(report, CHROME_STARTUP_FLAGS);
      const page = summarizePage({
        report,
        path,
        baselinePage: baseline.pages[name] || {},
      });
      passPages[name] = { report: reportName, page };
      if (passIndex === 1) pages[name] = page;
    }
    if (desktopRepeats > 1) {
      desktopPasses.push({
        pass: passIndex,
        pages: passPages,
      });
    }
    await checkoutChangeMonitor.flush();
    checkoutChangeMonitor.endMeasurement();
  }

  const repeatedBrandGuardSamples = [{
    report: "brandguard.json",
    page: pages.brandguard,
  }];
  for (let sampleIndex = 2; sampleIndex <= brandguardSamples; sampleIndex += 1) {
    checkoutChangeMonitor.beginMeasurement("sample", sampleIndex);
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
        ? CONTROLLED_BLOCKED_URL_PATTERNS.map((pattern) => `--blocked-url-patterns=${pattern}`)
        : []),
      CHROME_FLAGS_ARGUMENT,
      "--quiet",
    ], { cwd: root, stdio: "inherit", env: { ...process.env, CHROME_PATH: chromePath } });
    await checkoutChangeMonitor.flush();

    const report = readLighthouseReport(
      reportPath,
      `BrandGuard sample ${sampleIndex}`,
      reportName,
    );
    navigationReports[reportName] = { requestedUrl: url, report };
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

    runConditionsReports[reportName] = summarizeRunConditions(report, CHROME_STARTUP_FLAGS);
    repeatedBrandGuardSamples.push({
      report: reportName,
      page: summarizePage({
        report,
        path: routes.brandguard,
        baselinePage: baseline.pages.brandguard || {},
      }),
    });
    await checkoutChangeMonitor.flush();
    checkoutChangeMonitor.endMeasurement();
  }

  await checkoutChangeMonitor.flush();
  checkoutChangeMonitor.close();
  const sourceChangeMonitoring = checkoutChangeMonitor.summary();
  const sourceProvenanceEnd = getSourceRevision(root, {
    excludePaths: [outputRelativePath],
  });
  const deployedSourceProvenanceEnd = await getHostedSourceRevision(
    baseUrl,
    sourceProvenanceEnd.revision,
  );
  const sourceProvenanceComparison = compareSourceProvenance(
    sourceProvenanceStart,
    sourceProvenanceEnd,
  );
  const deployedSourceProvenanceRun = {
    start: deployedSourceProvenanceStart,
    end: deployedSourceProvenanceEnd,
    ...compareHostedSourceProvenance(
      deployedSourceProvenanceStart,
      deployedSourceProvenanceEnd,
    ),
  };
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
    baselineMeasurementStack: reference.measurementStack,
    runConditionsReports,
    baselineRunConditions: reference.runConditions,
    baselineTargetOrigin: reference.targetOrigin,
    baselinePath: reference.path,
    measurementStackReferenceNote: reference.measurementStackReferenceNote,
    referenceApproval: reference.approval,
    referenceIntegrityFingerprint: reference.integrityFingerprint,
    sourceRevision: sourceProvenanceStart.revision,
    sourceWorkingTreeClean: sourceProvenanceStart.workingTreeClean,
    sourceRevisionStatus: sourceProvenanceStart.status,
    sourceRevisionError: sourceProvenanceStart.error,
    baselineSourceRevision: reference.sourceRevision,
    baselineSourceWorkingTreeClean: reference.sourceWorkingTreeClean,
    baselineSourceRevisionStatus: reference.sourceRevisionStatus,
    baselineSourceRevisionNote: reference.sourceRevisionNote,
    deployedSourceProvenanceRun,
  });
  summary.sourceProvenanceRun = {
    start: sourceProvenanceStart,
    end: sourceProvenanceEnd,
    ...sourceProvenanceComparison,
  };
  summary.sourceChangeMonitoring = sourceChangeMonitoring;
  summary.pages = pages;
  summary.navigationReview = createLighthouseNavigationReview(baseUrl, navigationReports);
  if (desktopRepeats > 1) {
    summary.desktopRepeatability = summarizeDesktopRepeats(desktopPasses);
  }
  if (brandguardSamples > 1) {
    summary.brandguardRepeatSamples = summarizeBrandGuardSamples(repeatedBrandGuardSamples, { controlled });
  }

  const summaryPath = resolve(outputDir, "summary.json");
  writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`\nWrote ${summaryPath}`);
  console.log(`Measurement stack: Lighthouse ${lighthouseVersion}; Chromium ${chromiumVersion}`);
  const describeSource = (provenance) => {
    const workingTreeState = provenance.workingTreeClean === null
      ? "working-tree state unavailable"
      : provenance.workingTreeClean
        ? "working tree clean"
        : "working tree dirty";
    return `${provenance.revision ?? "unavailable"} (${workingTreeState})`;
  };
  console.log(
    `Site source revision at start: ${describeSource(sourceProvenanceStart)}`,
  );
  console.log(
    `Site source revision at end: ${describeSource(sourceProvenanceEnd)}`,
  );
  const describeDeployedSource = (provenance) => provenance.status === "verified"
    ? `${provenance.revision} (verified response header; runner checkout ${provenance.runnerCheckoutComparison})`
    : `${provenance.status}${provenance.reason ? `: ${provenance.reason}` : ""}`;
  console.log(
    `Hosted deployed source revision at start: ${describeDeployedSource(deployedSourceProvenanceStart)}`,
  );
  console.log(
    `Hosted deployed source revision at end: ${describeDeployedSource(deployedSourceProvenanceEnd)}`,
  );
  if (summary.deployedSourceProvenanceRun.status === "changed") {
    console.warn(
      "HOSTED DEPLOYED SOURCE REVISION CHANGED DURING RUN: "
      + `${deployedSourceProvenanceStart.revision} to ${deployedSourceProvenanceEnd.revision}.`,
    );
  }
  if (sourceProvenanceStart.error) {
    console.warn(`SITE SOURCE PROVENANCE UNAVAILABLE AT START: ${sourceProvenanceStart.error}`);
  }
  if (sourceProvenanceEnd.error) {
    console.warn(`SITE SOURCE PROVENANCE UNAVAILABLE AT END: ${sourceProvenanceEnd.error}`);
  }
  if (sourceProvenanceComparison.status === "changed") {
    console.warn(
      `SITE SOURCE PROVENANCE CHANGED DURING RUN: ${sourceProvenanceComparison.changedComponents.join(", ")}. `
      + `Start ${describeSource(sourceProvenanceStart)}; end ${describeSource(sourceProvenanceEnd)}. `
      + "The saved summary records both snapshots.",
    );
  } else if (sourceProvenanceComparison.status === "incomplete") {
    console.warn(
      `SITE SOURCE PROVENANCE COMPARISON INCOMPLETE: ${sourceProvenanceComparison.action}`,
    );
  }
  const affectedPasses = sourceChangeMonitoring.affectedPasses.map(({ index }) => index);
  const affectedSamples = sourceChangeMonitoring.affectedBrandGuardSamples.map(({ index }) => index);
  if (affectedPasses.length > 0 || affectedSamples.length > 0) {
    const affected = [
      ...(affectedPasses.length > 0 ? [`pass(es) ${affectedPasses.join(", ")}`] : []),
      ...(affectedSamples.length > 0 ? [`BrandGuard sample(s) ${affectedSamples.join(", ")}`] : []),
    ];
    console.warn(
      `SITE SOURCE CHANGE OBSERVED DURING MEASUREMENT: ${affected.join("; ")} may have spanned a checkout change.`,
    );
  }
  if (sourceChangeMonitoring.status !== "recorded") {
    console.warn(
      `SITE SOURCE CHANGE MONITORING INCOMPLETE: ${sourceChangeMonitoring.error || "No checkout directories could be monitored."}`,
    );
  }
  if (reference.sourceRevisionStatus !== "recorded") {
    console.warn(
      `REFERENCE SOURCE REVISION NOT RECORDED: ${reference.sourceRevisionNote}`,
    );
  }
  if (summary.brandguardRepeatSamples) {
    const { condition, sampleCount, metrics, samples } = summary.brandguardRepeatSamples;
    const lcpRange = metrics.lcpMs.min === null || metrics.lcpMs.max === null
      ? "unavailable"
      : `${metrics.lcpMs.min}–${metrics.lcpMs.max} ms`;
    const rangeSummary = (label, metric) => {
      const range = metric.min === null || metric.max === null
        ? "unavailable"
        : `${metric.min}–${metric.max} (spread ${metric.spread})`;
      return `${label} ${range}; ${metric.availableCount} available, ${metric.unavailableCount} unavailable`;
    };
    console.log(
      `BrandGuard ${condition} samples: ${sampleCount}; LCP ${lcpRange}`,
    );
    console.log(`  repeatability: ${
      [
        rangeSummary("accessibility", metrics.accessibility),
        rangeSummary("best-practices", metrics.bestPractices),
        rangeSummary("SEO", metrics.seo),
        rangeSummary("CLS", metrics.cls),
      ].join("; ")
    }`);
    const sampleScores = formatBrandGuardSampleScores(samples);
    if (sampleScores) console.log(sampleScores);
  }
  if (summary.desktopRepeatability) {
    const { passCount, routes: routeRanges } = summary.desktopRepeatability;
    console.log(`Desktop complete passes: ${passCount}`);
    for (const [name, routeSummary] of Object.entries(routeRanges)) {
      const range = (field, unit = "") => {
        const metric = routeSummary.metrics[field];
        return metric.min === null || metric.max === null
          ? "unavailable"
          : `${metric.min}–${metric.max}${unit}`;
      };
      console.log(
        `  ${name}: performance ${range("performance")}; `
        + `LCP ${range("lcpMs", " ms")}; FCP ${range("fcpMs", " ms")}; `
        + `Speed Index ${range("speedIndexMs", " ms")}; TBT ${range("tbtMs", " ms")}`,
      );
    }
    const unavailableDesktopRepeatMetricsNotice =
      formatUnavailableDesktopRepeatMetricsNotice(summary.desktopRepeatability);
    if (unavailableDesktopRepeatMetricsNotice) {
      console.warn(`\n${unavailableDesktopRepeatMetricsNotice}`);
    }
  }
  if (summary.measurementStackReview.status === "required") {
    console.warn(
      `BRANDGUARD BUDGET REVIEW REQUIRED: ${summary.measurementStackReview.changedComponents.join(", ")} changed from the historical reference measurement stack.`,
    );
    console.warn(summary.measurementStackReview.action);
  }
  if (summary.runConditionsReview.status === "required") {
    console.warn(formatRunConditionsReviewNotice(summary.runConditionsReview));
    console.warn(summary.runConditionsReview.action);
  }
  if (summary.targetReview.status === "required") {
    console.warn(formatLighthouseTargetReviewNotice(summary.targetReview));
    console.warn(summary.targetReview.action);
  }
  if (summary.navigationReview.status === "required") {
    console.warn(formatLighthouseNavigationReviewNotice(summary.navigationReview));
    console.warn(summary.navigationReview.action);
  }
  if (summary.referenceApproval?.ownerApproved === false) {
    console.warn(`DESKTOP REFERENCE NOT OWNER-APPROVED: ${summary.referenceApproval.action}`);
  }
  console.table(Object.fromEntries(Object.entries(summary.pages).map(([name, page]) => [
    name,
    { performance: page.performance, delta: page.deltaPerformance, lcpMs: page.lcpMs, deltaLcpMs: page.deltaLcpMs, cls: page.cls, tbtMs: page.tbtMs },
  ])));
  const unavailableMetricsNotice = formatUnavailableMetricsNotice(summary.pages, {
    includeSources: showUnavailableSources,
    reportNames: { brandguard: "brandguard.json" },
  });
  if (unavailableMetricsNotice) console.warn(`\n${unavailableMetricsNotice}`);
  if (summary.brandguardRepeatSamples) {
    const unavailableBrandGuardSampleMetricsNotice =
      formatUnavailableBrandGuardSampleMetricsNotice(repeatedBrandGuardSamples, {
        includeSources: showUnavailableSources,
        alreadyReportedFirstSampleMetrics: pages.brandguard.unavailableMetrics,
      });
    if (unavailableBrandGuardSampleMetricsNotice) {
      console.warn(`\n${unavailableBrandGuardSampleMetricsNotice}`);
    }
  }
  writeRunStatus("completed", { completedAt: new Date().toISOString() });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((error) => {
    writeIncompleteRunStatus?.(error);
    console.error(error);
    process.exitCode = 1;
  });
}

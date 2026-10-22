#!/usr/bin/env node
/**
 * AskJamie™ responsive QA script.
 *
 * MODE A — Playwright:
 *   Visits each public page at the configured viewport widths and checks:
 *   - No horizontal overflow (scrollWidth > innerWidth)
 *   - No JS console errors
 *   - All images loaded (no broken img src)
 *   - CSS and JS assets load (no 404 on critical resources)
 *   - BrandGuard hero geometry stays stable when its deferred theme activates
 *   - BrandGuard hero geometry stays stable after its branded web fonts load
 *   - BrandGuard shared-header and open mobile-navigation labels stay stable
 *     after branded web fonts load at narrow-phone widths
 *   - Non-BrandGuard sitemap pages using hosted web fonts keep visible text
 *     readable when font stylesheets and files are blocked at 320/360/390px
 *   - Universe diagram shell stays stable while its rendered SVG initializes
 *   - Universe page-map shell and neighboring summaries stay stable after web fonts load at 320px and wider
 *   - Open Universe page-map reservations settle at the latest phone viewport after rapid rotations while web fonts are held
 *   - An already-open Universe page map reflows through phone portrait, landscape, and portrait widths
 *   - Universe caption and links remain usable when Mermaid fails or JavaScript is off
 *   - Every generated Universe page-map destination reaches its expected local page when Mermaid is blocked
 *   - Every opened Universe page-map group keeps its shell and adjacent summaries stable across light/dark switches
 *   - Rendered Universe page-map node fills, node/edge labels, flow connectors, and arrowheads remain readable through both theme switches and forced-colors mode
 *   - Page-map summaries and fallback links keep keyboard focus and distinguishable colors when forced-colors turns on mid-session
 *   - Every Universe page-map group remains usable at 320px with a locally scrollable diagram
 *   - A touch-enabled 320px portrait viewport can swipe page maps horizontally in light mode without blocking links
 *   - Vertical swipes over page maps still scroll the page at a short 320x400 landscape viewport without activating links or changing groups
 *   - Two page maps keep distinct horizontal positions when users close one group and open the other
 *   - Panned page maps retain their position after touch close/reopen at phone width and keyboard close/reopen at phone and desktop widths
 *
 * MODE B — Static lint (`--static` only):
 *   Runs 10 structural checks per page per viewport (same pass/fail schema).
 *   Checks that are viewport-agnostic (viewport meta, h1, alt, etc.) are
 *   run once per page and applied to all configured viewport rows, clearly flagged
 *   as `static-lint` so results are not confused with live browser checks.
 *
 * Usage:
 *   node scripts/responsive-qa.mjs [--base=http://localhost:5000]
 *   node scripts/responsive-qa.mjs --brandguard-font-reservation-edge-only
 *   node scripts/responsive-qa.mjs --public-fallback-font-only
 *   node scripts/responsive-qa.mjs --list-interrupted
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

import {
  readFileSync,
  writeFileSync,
  renameSync,
  mkdirSync,
  existsSync,
  statSync,
  lstatSync,
  readdirSync,
  unlinkSync,
} from 'fs';
import { resolve, dirname, relative } from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import { execFileSync } from 'child_process';
import { createHash } from 'crypto';
import {
  findNotFoundBodyMessage,
  isNotFoundDocument,
} from './universe-not-found.mjs';
import {
  appendPageMapSweepHistory,
  assessDestinationSweepScaling,
  assessPageMapSweepHistory,
  isValidPageMapSweepHistory,
  summarizePageMapSweepRun,
} from './page-map-sweep-performance.mjs';
import {
  completedBrowserWarningBaseline,
  countBrowserWarnings,
  formatBrowserWarningTrend,
} from './responsive-qa-warning-trend.mjs';
import { summarizeBrowserFailures } from './responsive-qa-failure-summary.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);
const ROOT       = resolve(__dirname, '..');
const SOURCE_PROVENANCE_TIMEOUT_MS = 3000;

function describeGitError(error, fallback) {
  if (error?.code === 'ENOENT') return 'git executable unavailable';
  if (error?.code === 'ETIMEDOUT') {
    return `git command timed out after ${SOURCE_PROVENANCE_TIMEOUT_MS}ms`;
  }
  if (error?.code === 'INVALID_GIT_REVISION') {
    return 'git returned an empty or malformed revision';
  }
  if (error?.status != null) {
    return `git command failed (exit code ${error.status})`;
  }
  return fallback;
}

function parseGitNameStatus(output) {
  const fields = output.toString('utf8').split('\0').filter(Boolean);
  const changes = new Map();
  for (let index = 0; index + 1 < fields.length; index += 2) {
    changes.set(fields[index + 1], fields[index][0]);
  }
  return changes;
}

function readSourceProvenance() {
  let source_revision;
  try {
    source_revision = execFileSync(
      'git',
      ['rev-parse', '--verify', 'HEAD'],
      { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: SOURCE_PROVENANCE_TIMEOUT_MS }
    ).trim();
    if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(source_revision)) {
      const error = new Error('git returned an empty or malformed revision');
      error.code = 'INVALID_GIT_REVISION';
      throw error;
    }
  } catch (error) {
    return {
      source_revision: null,
      source_tree_status: 'unavailable',
      source_revision_error: describeGitError(
        error,
        'Git revision unavailable'
      ),
    };
  }

  try {
    const status = execFileSync(
      'git',
      ['status', '--porcelain', '--untracked-files=all'],
      { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: SOURCE_PROVENANCE_TIMEOUT_MS }
    ).trim();
    const trackedDiff = execFileSync(
      'git',
      ['diff', '--binary', '--no-ext-diff', 'HEAD'],
      { cwd: ROOT, stdio: ['ignore', 'pipe', 'ignore'], timeout: SOURCE_PROVENANCE_TIMEOUT_MS }
    );
    const trackedPathStatuses = parseGitNameStatus(execFileSync(
      'git',
      ['diff', '--no-renames', '--name-status', '-z', 'HEAD'],
      { cwd: ROOT, encoding: 'buffer', stdio: ['ignore', 'pipe', 'ignore'], timeout: SOURCE_PROVENANCE_TIMEOUT_MS }
    ));
    const trackedPaths = [...trackedPathStatuses.keys()];
    const untrackedPaths = execFileSync(
      'git',
      ['ls-files', '--others', '--exclude-standard', '-z'],
      { cwd: ROOT, encoding: 'buffer', stdio: ['ignore', 'pipe', 'ignore'], timeout: SOURCE_PROVENANCE_TIMEOUT_MS }
    ).toString('utf8').split('\0').filter(Boolean);
    const source_tree_paths = new Map();
    for (const path of trackedPaths) {
      const pathExists = existsSync(resolve(ROOT, path));
      const pathFingerprint = pathExists
        ? createHash('sha256').update(readFileSync(resolve(ROOT, path))).digest('hex')
        : 'deleted';
      source_tree_paths.set(path, `tracked:${pathFingerprint}`);
    }
    for (const path of untrackedPaths) {
      const pathFingerprint = createHash('sha256')
        .update(readFileSync(resolve(ROOT, path)))
        .digest('hex');
      source_tree_paths.set(path, `untracked:${pathFingerprint}`);
    }
    const fingerprint = createHash('sha256');
    fingerprint.update('tracked\0');
    fingerprint.update(trackedDiff);
    fingerprint.update('\0untracked\0');
    for (const path of untrackedPaths.sort()) {
      fingerprint.update(path);
      fingerprint.update('\0');
      fingerprint.update(readFileSync(resolve(ROOT, path)));
      fingerprint.update('\0');
    }
    return {
      source_revision,
      source_tree_status: status ? 'modified' : 'clean',
      source_tree_fingerprint: fingerprint.digest('hex'),
      source_tree_paths,
      source_tree_path_statuses: trackedPathStatuses,
    };
  } catch (error) {
    return {
      source_revision,
      source_tree_status: 'unavailable',
      source_tree_error: describeGitError(
        error,
        'Git working-tree status or fingerprint unavailable'
      ),
    };
  }
}

const SOURCE_PROVENANCE = readSourceProvenance();

function getStartProvenanceReport() {
  const {
    source_tree_fingerprint: _fingerprint,
    source_tree_paths: _paths,
    source_tree_path_statuses: _pathStatuses,
    ...provenance
  } = SOURCE_PROVENANCE;
  return provenance;
}

function getCompletionProvenance() {
  const completion = readSourceProvenance();
  const isComparable =
    SOURCE_PROVENANCE.source_tree_fingerprint &&
    completion.source_tree_fingerprint;
  let changedPaths = null;
  let changedPathsError = null;
  if (isComparable) {
    try {
      const committedPathStatuses = SOURCE_PROVENANCE.source_revision === completion.source_revision
        ? new Map()
        : parseGitNameStatus(execFileSync(
            'git',
            [
              'diff',
              '--no-renames',
              '--name-status',
              '-z',
              SOURCE_PROVENANCE.source_revision,
              completion.source_revision,
            ],
            {
              cwd: ROOT,
              encoding: 'buffer',
              stdio: ['ignore', 'pipe', 'ignore'],
              timeout: SOURCE_PROVENANCE_TIMEOUT_MS,
            }
          ));
      const changedPathNames = new Set([
        ...committedPathStatuses.keys(),
        ...SOURCE_PROVENANCE.source_tree_paths.keys(),
        ...completion.source_tree_paths.keys(),
      ]);
      changedPaths = [...changedPathNames]
        .filter(path =>
          committedPathStatuses.has(path) ||
          SOURCE_PROVENANCE.source_tree_paths.get(path) !==
            completion.source_tree_paths.get(path)
        )
        .sort()
        .map(path => {
          const commitStatus = committedPathStatuses.get(path);
          const startStatus = SOURCE_PROVENANCE.source_tree_path_statuses.get(path);
          const endStatus = completion.source_tree_path_statuses.get(path);
          const startPathState = SOURCE_PROVENANCE.source_tree_paths.get(path);
          const endPathState = completion.source_tree_paths.get(path);
          const startIsUntracked = startPathState?.startsWith('untracked:') ?? false;
          const endIsUntracked = endPathState?.startsWith('untracked:') ?? false;
          const pathExistsAt = (
            status, state, otherStatus, otherIsUntracked, atEnd
          ) => {
            if (status) return status !== 'D';
            if (state?.startsWith('untracked:')) return true;
            if (state?.endsWith(':deleted')) return false;
            if (commitStatus === 'A') return atEnd;
            if (commitStatus === 'D') return !atEnd;
            if (commitStatus) return true;
            if (otherStatus === 'A') return false;
            if (otherStatus === 'D') return true;
            if (otherIsUntracked) return false;
            return true;
          };
          const existsAtStart = pathExistsAt(
            startStatus, startPathState, endStatus, endIsUntracked, false
          );
          const existsAtEnd = pathExistsAt(
            endStatus, endPathState, startStatus, startIsUntracked, true
          );
          const classification = !existsAtStart && existsAtEnd
            ? 'added'
            : existsAtStart && !existsAtEnd
              ? 'removed'
              : 'edited';
          return { path, classification };
        });
    } catch (error) {
      changedPathsError = error?.code === 'ETIMEDOUT'
        ? `git command timed out after ${SOURCE_PROVENANCE_TIMEOUT_MS}ms`
        : error?.status != null
          ? `git command failed (exit code ${error.status})`
          : 'Git changed-path comparison unavailable';
    }
  }
  const changedPathsRecorded = isComparable && changedPaths !== null;
  return {
    source_revision_at_completion: completion.source_revision,
    source_tree_status_at_completion: completion.source_tree_status,
    source_tree_changed_during_run: isComparable
      ? SOURCE_PROVENANCE.source_revision !== completion.source_revision ||
        SOURCE_PROVENANCE.source_tree_status !== completion.source_tree_status ||
        SOURCE_PROVENANCE.source_tree_fingerprint !== completion.source_tree_fingerprint
      : null,
    source_changed_paths_status: changedPathsRecorded ? 'recorded' : 'unavailable',
    source_changed_paths_classification_status: changedPathsRecorded
      ? 'recorded'
      : 'unavailable',
    ...(changedPathsRecorded
      ? { source_changed_paths_during_run: changedPaths }
      : {}),
    ...(changedPathsError
      ? { source_changed_paths_error: changedPathsError }
      : {}),
    ...(completion.source_revision_error
      ? { source_revision_error_at_completion: completion.source_revision_error }
      : {}),
    ...(completion.source_tree_error
      ? { source_tree_error_at_completion: completion.source_tree_error }
      : {}),
  };
}

const BASE_URL   = process.argv.find(a => a.startsWith('--base='))?.split('=')[1]
                 ?? 'http://localhost:5000';
const FORCE_STATIC = process.argv.includes('--static');
const BRANDGUARD_EDGE_ONLY = process.argv.includes('--brandguard-edge-only');
const PUBLIC_FALLBACK_FONT_ONLY =
  process.argv.includes('--public-fallback-font-only');
if (PUBLIC_FALLBACK_FONT_ONLY && FORCE_STATIC) {
  throw new Error('--public-fallback-font-only requires Playwright browser mode.');
}
const BRANDGUARD_FONT_RESERVATION_EDGE_ONLY =
  process.argv.includes('--brandguard-font-reservation-edge-only');
const BRANDGUARD_FONT_RESERVATION_EDGE_PATHS = [
  '/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
];
const REQUIRE_RELEASE_HERO_ROUTES =
  process.argv.includes('--require-release-hero-routes');

const VIEWPORTS = [
  { name: 'mobile-320',   width: 320,  height: 740  },
  { name: 'mobile-349-reservation-edge', width: 349, height: 780 },
  { name: 'mobile-350-edge', width: 350, height: 780 },
  { name: 'mobile-355-boundary', width: 355, height: 780 },
  { name: 'mobile-356-gap', width: 356, height: 780 },
  { name: 'mobile-357-font-gap', width: 357, height: 780 },
  { name: 'mobile-358-font-gap', width: 358, height: 780 },
  { name: 'mobile-359-font-gap', width: 359, height: 780 },
  { name: 'mobile-360',   width: 360,  height: 780  },
  { name: 'mobile-361-font-gap', width: 361, height: 780 },
  { name: 'mobile-362-font-gap', width: 362, height: 780 },
  { name: 'mobile-363-font-gap', width: 363, height: 780 },
  { name: 'mobile-364-gap', width: 364, height: 780 },
  { name: 'mobile-365-boundary', width: 365, height: 780 },
  { name: 'mobile-370-edge', width: 370, height: 780 },
  { name: 'mobile-371-reservation-edge', width: 371, height: 780 },
  { name: 'mobile-390',   width: 390,  height: 844  },
  { name: 'mobile-400-edge', width: 400, height: 780 },
  { name: 'mobile-401-edge', width: 401, height: 780 },
  { name: 'mobile-430',   width: 430,  height: 932  },
  { name: 'tablet-768',   width: 768,  height: 1024 },
  { name: 'tablet-769-edge', width: 769, height: 1024 },
  { name: 'tablet-899',   width: 899,  height: 1024 },
  { name: 'tablet-900-edge', width: 900, height: 1024 },
  { name: 'desktop-1024', width: 1024, height: 768  },
  { name: 'desktop-1280', width: 1280, height: 800  },
  { name: 'desktop-1440', width: 1440, height: 900  },
  { name: 'desktop-1920', width: 1920, height: 1080 },
];
const BRANDGUARD_FONT_RESERVATION_EDGE_VIEWPORTS = new Set([
  'mobile-349-reservation-edge',
  'mobile-371-reservation-edge',
]);
const PUBLIC_FALLBACK_FONT_VIEWPORT_WIDTHS = new Set([320, 360, 390]);
const QA_VIEWPORTS = PUBLIC_FALLBACK_FONT_ONLY
  ? VIEWPORTS.filter(vp => PUBLIC_FALLBACK_FONT_VIEWPORT_WIDTHS.has(vp.width))
  : BRANDGUARD_FONT_RESERVATION_EDGE_ONLY
    ? VIEWPORTS.filter(vp => BRANDGUARD_FONT_RESERVATION_EDGE_VIEWPORTS.has(vp.name))
    : BRANDGUARD_EDGE_ONLY
      ? VIEWPORTS.filter(vp => vp.name === 'mobile-350-edge' || vp.name === 'mobile-370-edge')
      : VIEWPORTS;
// Cap concurrent viewport work at four to limit browser request bursts while
// preserving every viewport row in the release inventory.
const VIEWPORT_CONCURRENCY = 4;
async function mapConcurrent(items, concurrency, task) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError('Concurrency must be a positive integer');
  }

  const results = new Array(items.length);
  let nextIndex = 0;
  async function runWorker() {
    while (true) {
      const index = nextIndex++;
      if (index >= items.length) return;
      results[index] = await task(items[index], index);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, runWorker)
  );
  return results;
}
const LOCAL_DOCUMENT_READY_TIMEOUT_MS = 5000;
const LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS = 30000;
const LAZY_IMAGE_REQUEST_TIMEOUT_MS = 5000;
const LAZY_IMAGE_LATE_REQUEST_GRACE_MS = 1500;
const PUBLIC_TREE_EXCLUDED_DIRECTORIES = new Set([
  '.local',
  '.scratch',
  '.agents',
  'attached_assets',
  'node_modules',
  '.cache',
  '.git',
  'templates',
  'dist-pages',
]);
const PUBLIC_TREE_EXCLUDED_HTML_PATHS = new Set([
  '404.html',
  'under-construction.html',
]);

// Keep these publication boundaries aligned with scripts/prepare-pages-artifact.py.
const PAGES_ARTIFACT_ROOT_FILES = new Set([
  '404.html',
  'CNAME',
  '_headers',
  'favicon.ico',
  'humans.txt',
  'index.html',
  'llms.txt',
  'robots.txt',
  'sitemap.xml',
  'site.webmanifest',
  'under-construction.html',
]);
const PAGES_ARTIFACT_ROOT_DIRS = new Set(['assets', '.well-known']);
const PAGES_ARTIFACT_ASSET_DIRS = new Set([
  'css',
  'data',
  'fonts',
  'img',
  'js',
  'vendor',
]);
const PAGES_ARTIFACT_PAGE_DIRS = new Set([
  'about',
  'contact',
  'how-askjamie-works',
  'found-ry',
  'whats-next',
  'legal',
  'lens-system',
  'search',
  'universe',
]);

function isPagesArtifactPublicHtmlFile(pageFile) {
  const localPath = relative(ROOT, pageFile).replaceAll('\\', '/');
  if (!localPath || localPath.startsWith('../')) return false;
  const parts = localPath.split('/');
  if (parts.some(part => part.startsWith('.') && part !== '.well-known')) {
    return false;
  }
  if (parts.slice(1).includes('.well-known')) return false;

  let candidate = pageFile;
  while (candidate !== ROOT) {
    try {
      if (lstatSync(candidate).isSymbolicLink()) return false;
    } catch {
      return false;
    }
    candidate = dirname(candidate);
  }

  if (PAGES_ARTIFACT_PAGE_DIRS.has(parts[0])) return true;
  if (PAGES_ARTIFACT_ROOT_DIRS.has(parts[0])) {
    if (parts[0] !== 'assets') return true;
    return parts.length > 1 && PAGES_ARTIFACT_ASSET_DIRS.has(parts[1]);
  }
  return parts.length === 1 && PAGES_ARTIFACT_ROOT_FILES.has(parts[0]);
}

function collectPublishedNonBrandGuardHtmlPages() {
  const pages = [];
  const brandGuardPrefix = 'lens-system/okhp3-brandguard/';

  function walk(directory) {
    let entries;
    try {
      entries = readdirSync(directory, { withFileTypes: true });
    } catch (error) {
      if (directory === ROOT) throw error;
      return;
    }

    for (const entry of entries) {
      const entryPath = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        if (!PUBLIC_TREE_EXCLUDED_DIRECTORIES.has(entry.name)) walk(entryPath);
        continue;
      }
      if (!entry.isFile() || !entry.name.toLowerCase().endsWith('.html')) continue;

      const localPath = relative(ROOT, entryPath).replaceAll('\\', '/');
      if (
        PUBLIC_TREE_EXCLUDED_HTML_PATHS.has(localPath) ||
        localPath.startsWith(brandGuardPrefix) ||
        !isPagesArtifactPublicHtmlFile(entryPath)
      ) {
        continue;
      }

      const route = localPath === 'index.html'
        ? '/'
        : localPath.endsWith('/index.html')
          ? `/${localPath.slice(0, -'index.html'.length)}`
          : `/${localPath}`;
      pages.push({ localPath, route });
    }
  }

  walk(ROOT);
  return pages.sort((left, right) => left.localPath.localeCompare(right.localPath));
}

// The sitemap is the release inventory. This avoids silently testing a stale
// hand-maintained list when a public route is added or retired.
function loadPublicPaths() {
  const sitemap = readFileSync(resolve(ROOT, 'sitemap.xml'), 'utf8');
  let locations;
  try {
    const parsedLocations = execFileSync(
      'python3',
      [
        '-c',
        [
          'import json, sys',
          'import xml.etree.ElementTree as ET',
          'root = ET.fromstring(sys.stdin.buffer.read())',
          'locations = [',
          '    element.text.strip()',
          '    for element in root.iter()',
          "    if element.tag.rsplit('}', 1)[-1] == 'loc'",
          '    and element.text and element.text.strip()',
          ']',
          'print(json.dumps(locations))',
        ].join('\n'),
      ],
      { input: sitemap, encoding: 'utf8', timeout: 5000 }
    );
    locations = JSON.parse(parsedLocations);
  } catch (error) {
    const detail = error.stderr?.toString().trim().split('\n').at(-1);
    throw new Error(
      `sitemap.xml could not be parsed${detail ? `: ${detail}` : ''}`
    );
  }
  if (locations.length === 0) throw new Error('sitemap.xml has no public routes');
  const publicPaths = locations.map((location) => {
    const url = new URL(location);
    if (url.origin !== 'https://askjamie.bot' || url.search || url.hash) {
      throw new Error(`Invalid sitemap URL for responsive QA: ${location}`);
    }
    return url.pathname || '/';
  });
  const seenPaths = new Set();
  const duplicatePaths = new Set();
  for (const path of publicPaths) {
    if (seenPaths.has(path)) duplicatePaths.add(path);
    else seenPaths.add(path);
  }
  if (duplicatePaths.size) {
    throw new Error(
      `Duplicate sitemap route(s) for responsive QA: ${[...duplicatePaths].join(', ')}`
    );
  }

  const brandGuardRoot = '/lens-system/okhp3-brandguard/';
  const brandGuardDirectory = resolve(ROOT, brandGuardRoot.slice(1));
  const publishedCaseStudyPaths = existsSync(brandGuardDirectory)
    ? readdirSync(brandGuardDirectory, { withFileTypes: true })
      .filter(entry => entry.isDirectory() &&
        existsSync(resolve(brandGuardDirectory, entry.name, 'index.html')))
      .map(entry => `${brandGuardRoot}${entry.name}/`)
    : [];
  const sitemapCaseStudyPaths = publicPaths.filter(path =>
    path.startsWith(brandGuardRoot) && path !== brandGuardRoot
  );
  const publishedCaseStudyPathSet = new Set(publishedCaseStudyPaths);
  const sitemapCaseStudyPathSet = new Set(sitemapCaseStudyPaths);
  const missingFromSitemap = publishedCaseStudyPaths
    .filter(path => !sitemapCaseStudyPathSet.has(path))
    .sort();
  const missingLocalPage = sitemapCaseStudyPaths
    .filter(path => !publishedCaseStudyPathSet.has(path))
    .sort();

  if (missingFromSitemap.length || (!FORCE_STATIC && missingLocalPage.length)) {
    const details = [];
    if (missingFromSitemap.length) {
      details.push(`published pages missing from sitemap.xml: ${missingFromSitemap.join(', ')}`);
    }
    if (missingLocalPage.length) {
      details.push(`sitemap routes without a published page directory: ${missingLocalPage.join(', ')}`);
    }
    throw new Error(`BrandGuard case-study route mismatch: ${details.join('; ')}`);
  }

  const missingPublishedRoutes = publicPaths
    .filter(path => !sitemapCaseStudyPathSet.has(path))
    .flatMap(path => {
      let decodedPath;
      try {
        decodedPath = decodeURIComponent(path);
      } catch {
        throw new Error(`Invalid escaped path in sitemap URL for responsive QA: ${path}`);
      }
      const isDirectoryRoute =
        decodedPath.endsWith('/') || !/\.[^/]+$/.test(decodedPath);
      const isHtmlFileRoute = /\.html$/i.test(decodedPath);
      const pageFile = isDirectoryRoute
        ? resolve(ROOT, decodedPath.slice(1), 'index.html')
        : isHtmlFileRoute
          ? resolve(ROOT, decodedPath.slice(1))
          : null;
      if (!pageFile) {
        return [`${path} (not an HTML page route; use a published directory or .html page)`];
      }
      if (pageFile !== ROOT && !pageFile.startsWith(`${ROOT}/`) &&
          !pageFile.startsWith(`${ROOT}\\`)) {
        return [`${path} (resolves outside the published page tree)`];
      }
      if (!existsSync(pageFile) || !statSync(pageFile).isFile()) {
        if (FORCE_STATIC) return [];
        return [`${path} (expected ${relative(ROOT, pageFile)})`];
      }
      if (!isPagesArtifactPublicHtmlFile(pageFile)) {
        return [`${path} (local HTML file is not included in the published Pages artifact)`];
      }
      return [];
    });

  if (missingPublishedRoutes.length) {
    throw new Error(
      `Sitemap routes without a published local page: ${missingPublishedRoutes.join(', ')}. ` +
      'Remove stale sitemap routes or add the missing published pages.'
    );
  }

  const sitemapHtmlPathSet = new Set(publicPaths.map(path => {
    const decodedPath = decodeURIComponent(path);
    return decodedPath.endsWith('/') || !/\.[^/]+$/.test(decodedPath)
      ? `${decodedPath.slice(1)}index.html`
      : decodedPath.slice(1);
  }));
  const missingNonBrandGuardPages = collectPublishedNonBrandGuardHtmlPages()
    .filter(page => !sitemapHtmlPathSet.has(page.localPath));
  if (missingNonBrandGuardPages.length) {
    const omittedPages = missingNonBrandGuardPages
      .map(page => `${page.localPath} (${page.route})`)
      .join(', ');
    throw new Error(
      `Published non-BrandGuard HTML pages missing from sitemap.xml: ${omittedPages}. ` +
      'Add the published routes to sitemap.xml or remove the unpublished pages.'
    );
  }

  return publicPaths;
}
const PUBLIC_PATHS = loadPublicPaths();
function pageHasHostedWebFontStylesheet(path) {
  const decodedPath = decodeURIComponent(path);
  const pageFile = decodedPath.endsWith('/')
    ? resolve(ROOT, decodedPath.slice(1), 'index.html')
    : resolve(ROOT, decodedPath.slice(1));
  if (!existsSync(pageFile)) return false;
  const html = readFileSync(pageFile, 'utf8');
  return [...html.matchAll(/<link\b[^>]*>/gi)].some(([tag]) =>
    /\brel\s*=\s*["'][^"']*\bstylesheet\b[^"']*["']/i.test(tag) &&
    /\bhref\s*=\s*["']https:\/\/fonts\.googleapis\.com\//i.test(tag)
  );
}
const PUBLIC_FALLBACK_FONT_PATHS = new Set(
  PUBLIC_PATHS.filter(path =>
    !path.startsWith('/lens-system/okhp3-brandguard/') &&
    pageHasHostedWebFontStylesheet(path)
  )
);
function shouldCheckPublicFallbackFontReadability(path, viewportWidth) {
  return PUBLIC_FALLBACK_FONT_PATHS.has(path) &&
    PUBLIC_FALLBACK_FONT_VIEWPORT_WIDTHS.has(viewportWidth);
}
const QA_PATHS = PUBLIC_FALLBACK_FONT_ONLY
  ? PUBLIC_PATHS.filter(path => PUBLIC_FALLBACK_FONT_PATHS.has(path))
  : BRANDGUARD_EDGE_ONLY
  ? PUBLIC_PATHS.filter(path =>
      path.startsWith('/lens-system/okhp3-brandguard/')
    )
  : BRANDGUARD_FONT_RESERVATION_EDGE_ONLY
    ? BRANDGUARD_FONT_RESERVATION_EDGE_PATHS.filter(path =>
        PUBLIC_PATHS.includes(path)
      )
    : PUBLIC_PATHS;
if (PUBLIC_FALLBACK_FONT_ONLY && QA_PATHS.length === 0) {
  throw new Error(
    'No non-BrandGuard sitemap routes with hosted web-font stylesheets were found.'
  );
}

const RESULTS_DIR    = resolve(ROOT, 'assets/audit/responsive-qa');
const RESULTS_FILE   = resolve(
  RESULTS_DIR,
  PUBLIC_FALLBACK_FONT_ONLY
    ? 'public-fallback-font-results.json'
    : BRANDGUARD_FONT_RESERVATION_EDGE_ONLY
      ? 'brandguard-font-reservation-edge-results.json'
      : BRANDGUARD_EDGE_ONLY
        ? 'brandguard-edge-results.json'
        : 'results.json'
);
const INTERRUPTED_REPORTS_DIR = resolve(RESULTS_DIR, 'interrupted-runs');
const INTERRUPTED_REPORTS_LIMIT = 10;
const SCREENSHOTS_DIR = resolve(RESULTS_DIR, 'screenshots');
const COMPLETED_BROWSER_BASELINE_FILE = RESULTS_FILE.replace(
  /\.json$/,
  '.completed-browser-baseline.json'
);
const PAGE_MAP_SWEEP_HISTORY_FILE = RESULTS_FILE.replace(
  /\.json$/,
  '.page-map-sweep-history.json'
);

function writeResultsReport(report) {
  const temporaryFile = `${RESULTS_FILE}.${process.pid}.tmp`;
  writeFileSync(temporaryFile, JSON.stringify(report, null, 2));
  renameSync(temporaryFile, RESULTS_FILE);
}

function readPageMapSweepHistory() {
  if (!existsSync(PAGE_MAP_SWEEP_HISTORY_FILE)) {
    return { schema_version: 1, runs: [] };
  }
  const history = JSON.parse(
    readFileSync(PAGE_MAP_SWEEP_HISTORY_FILE, 'utf8')
  );
  if (!isValidPageMapSweepHistory(history)) {
    throw new Error(
      `Invalid page-map sweep history: ${relative(ROOT, PAGE_MAP_SWEEP_HISTORY_FILE)}`
    );
  }
  return history;
}

function writePageMapSweepHistory(history) {
  const temporaryFile = `${PAGE_MAP_SWEEP_HISTORY_FILE}.${process.pid}.tmp`;
  writeFileSync(temporaryFile, JSON.stringify(history, null, 2));
  renameSync(temporaryFile, PAGE_MAP_SWEEP_HISTORY_FILE);
}

function attachPageMapSweepHistoryAssessment(report, history, summaries) {
  const currentRun = {
    generated: report.generated,
    playwright_version: report.playwright_version,
    chromium_version: report.chromium_version,
    summaries,
  };
  const assessment = assessPageMapSweepHistory(history.runs, currentRun);
  const status = summaries.length === 0
    ? 'no_page_map_samples'
    : assessment.comparable_group_count === 0
      ? 'baseline_warming'
      : assessment.comparable_group_count === summaries.length
        ? 'compared'
        : 'partially_compared';
  report.page_map_sweep_performance = {
    status,
    history_file: relative(ROOT, PAGE_MAP_SWEEP_HISTORY_FILE),
    stored_sample_count: summaries.length,
    ...assessment,
  };

  for (const regression of assessment.regressions) {
    const row = report.results.find(result => {
      try {
        return new URL(result.url).pathname === regression.route &&
          result.viewport === regression.viewport;
      } catch {
        return false;
      }
    });
    if (!row) continue;
    const message =
      `UNIVERSE PAGE-MAP DESTINATION SWEEP SLOWED ACROSS RUNS: ` +
      `${regression.route}, ${regression.group_label} ` +
      `(group ${regression.group_index}), ` +
      `${regression.destination_count} destinations; median per-destination time ` +
      `increased from ${regression.baseline_median_destination_ms}ms across ` +
      `${regression.baseline_run_count} baseline runs ` +
      `${regression.comparison_window.baseline_from} to ` +
      `${regression.comparison_window.baseline_to} to ` +
      `${regression.current_median_destination_ms}ms ` +
      `(${regression.slowdown_ratio}x) across ` +
      `${regression.confirmation_run_count} consecutive runs ` +
      `${regression.comparison_window.confirmation_from} to ` +
      `${regression.comparison_window.confirmation_to}`;
    row.errors.push(message);
    row.pass = false;
  }
  report.passing_checks = report.results.filter(result => result.pass).length;
  report.failing_checks = report.results.filter(result => !result.pass).length;

  if (summaries.length > 0) {
    const updatedHistory = appendPageMapSweepHistory(history, currentRun);
    writePageMapSweepHistory(updatedHistory);
  }
}

function readPreviousCompletedBrowserWarningBaseline() {
  const candidates = [];

  if (existsSync(COMPLETED_BROWSER_BASELINE_FILE)) {
    const savedBaseline = JSON.parse(
      readFileSync(COMPLETED_BROWSER_BASELINE_FILE, 'utf8')
    );
    if (
      savedBaseline?.mode !== 'playwright' ||
      savedBaseline?.status !== 'complete' ||
      !Number.isSafeInteger(savedBaseline.warning_count) ||
      savedBaseline.warning_count < 0
    ) {
      throw new Error(
        `Invalid completed browser QA warning baseline: ${relative(ROOT, COMPLETED_BROWSER_BASELINE_FILE)}`
      );
    }
    candidates.push(savedBaseline);
  }

  if (existsSync(RESULTS_FILE)) {
    const currentReport = JSON.parse(readFileSync(RESULTS_FILE, 'utf8'));
    const currentBaseline = completedBrowserWarningBaseline(currentReport);
    if (currentBaseline) candidates.push(currentBaseline);
  }

  return candidates.reduce((newest, candidate) => {
    if (!newest) return candidate;
    const candidateTime = Date.parse(candidate.generated ?? '');
    const newestTime = Date.parse(newest.generated ?? '');
    if (
      Number.isFinite(candidateTime) &&
      (!Number.isFinite(newestTime) || candidateTime > newestTime)
    ) {
      return candidate;
    }
    return newest;
  }, null);
}

function saveBrowserWarningBaseline(baseline) {
  if (
    baseline?.mode !== 'playwright' ||
    baseline?.status !== 'complete' ||
    !Number.isSafeInteger(baseline.warning_count) ||
    baseline.warning_count < 0
  ) {
    throw new Error('Refusing to save a warning baseline from an incomplete browser QA report');
  }
  writeFileSync(
    COMPLETED_BROWSER_BASELINE_FILE,
    JSON.stringify(baseline, null, 2)
  );
}

function saveCompletedBrowserWarningBaseline(report) {
  saveBrowserWarningBaseline(completedBrowserWarningBaseline(report));
}

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function archivePreviousInterruptedReport() {
  if (!existsSync(RESULTS_FILE)) return null;

  const previous = JSON.parse(readFileSync(RESULTS_FILE, 'utf8'));
  if (!['early_stop', 'abandoned'].includes(previous.status)) return null;

  const generatedAt = typeof previous.generated === 'string'
    ? Date.parse(previous.generated)
    : Number.NaN;
  previous.generated = Number.isFinite(generatedAt)
    ? new Date(generatedAt).toISOString()
    : statSync(RESULTS_FILE).mtime.toISOString();

  mkdirSync(INTERRUPTED_REPORTS_DIR, { recursive: true });
  const timestamp = previous.generated.replace(/[:.]/g, '-');
  const status = previous.status;
  const baseName = `${timestamp}--${status}`;
  const serialized = JSON.stringify(previous, null, 2);
  let archivePath = resolve(INTERRUPTED_REPORTS_DIR, `${baseName}.json`);
  let suffix = 1;

  while (existsSync(archivePath)) {
    try {
      const archived = JSON.parse(readFileSync(archivePath, 'utf8'));
      if (canonicalJson(archived) === canonicalJson(previous)) {
        pruneInterruptedReports();
        return null;
      }
    } catch {
      // Keep malformed archive entries as collisions; do not overwrite evidence.
    }
    suffix += 1;
    archivePath = resolve(INTERRUPTED_REPORTS_DIR, `${baseName}-${suffix}.json`);
  }

  writeFileSync(archivePath, serialized);
  pruneInterruptedReports();
  return archivePath;
}

function pruneInterruptedReports() {
  const reportFiles = readdirSync(INTERRUPTED_REPORTS_DIR)
    .filter(name =>
      /^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z--(?:early_stop|abandoned)(?:-\d+)?\.json$/.test(name)
    )
    .sort();

  while (reportFiles.length > INTERRUPTED_REPORTS_LIMIT) {
    unlinkSync(resolve(INTERRUPTED_REPORTS_DIR, reportFiles.shift()));
  }
}

function listInterruptedReports() {
  console.log(`Latest report (current): ${relative(ROOT, RESULTS_FILE)}`);
  console.log(`Interrupted report archive: ${relative(ROOT, INTERRUPTED_REPORTS_DIR)}`);

  if (!existsSync(INTERRUPTED_REPORTS_DIR)) {
    console.log('No retained interrupted runs found.');
    return;
  }

  const reportFiles = readdirSync(INTERRUPTED_REPORTS_DIR)
    .filter(name =>
      /^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z--(?:early_stop|abandoned)(?:-\d+)?\.json$/.test(name)
    )
    .sort((a, b) => b.localeCompare(a));

  if (reportFiles.length === 0) {
    console.log('No retained interrupted runs found.');
    return;
  }

  console.log('Retained interrupted runs (newest first):');
  for (const name of reportFiles) {
    const reportPath = resolve(INTERRUPTED_REPORTS_DIR, name);
    try {
      const report = JSON.parse(readFileSync(reportPath, 'utf8'));
      const generated = typeof report.generated === 'string' && report.generated.trim()
        ? report.generated.replace(/\s+/g, ' ')
        : '(generation time not recorded)';
      const status = typeof report.status === 'string' && report.status.trim()
        ? report.status.replace(/\s+/g, ' ')
        : '(status not recorded)';
      const failureReason = typeof report.failure_reason === 'string' && report.failure_reason.trim()
        ? report.failure_reason.replace(/\s+/g, ' ')
        : '(no failure reason recorded)';
      const validCount = value =>
        Number.isSafeInteger(value) && value >= 0 ? value : null;
      const completedChecks = validCount(report.total_checks);
      const expectedPages = validCount(report.pages_expected);
      const checkedViewports = validCount(report.viewports_checked);
      const expectedChecks = expectedPages !== null && checkedViewports !== null
        ? expectedPages * checkedViewports
        : null;
      const progress = [];
      if (completedChecks !== null || expectedChecks !== null) {
        progress.push(
          `checks ${completedChecks ?? '?'}${expectedChecks !== null ? `/${expectedChecks}` : ''}`
        );
      }
      const passingChecks = validCount(report.passing_checks);
      const failingChecks = validCount(report.failing_checks);
      if (passingChecks !== null) progress.push(`${passingChecks} passed`);
      if (failingChecks !== null) progress.push(`${failingChecks} failed`);
      console.log(
        `  ${generated} | ${status} | ${failureReason}` +
        `${progress.length > 0 ? ` | ${progress.join(', ')}` : ''} | ${relative(ROOT, reportPath)}`
      );
    } catch (error) {
      console.error(`Unable to read interrupted report ${relative(ROOT, reportPath)}: ${error.message}`);
      process.exitCode = 1;
    }
  }
}

function reconcilePreviousBrowserReport() {
  if (!existsSync(RESULTS_FILE)) return null;

  const previous = JSON.parse(readFileSync(RESULTS_FILE, 'utf8'));
  if (previous.status !== 'in_progress') return null;

  const ownerPid = previous.runner_pid;
  let ownerIsRunning = false;
  if (Number.isSafeInteger(ownerPid) && ownerPid > 0) {
    try {
      process.kill(ownerPid, 0);
      ownerIsRunning = true;
    } catch (error) {
      ownerIsRunning = error?.code === 'EPERM';
    }
  }

  if (ownerIsRunning) {
    return `Responsive QA is already running (PID ${ownerPid}); refusing to replace its in-progress report.`;
  }

  previous.status = 'abandoned';
  previous.abandoned_at = new Date().toISOString();
  writeResultsReport(previous);
  console.warn(
    `Previous responsive-QA report was left in progress and is now marked abandoned; ` +
    `preserving ${previous.total_checks ?? 0} partial checks. Failure reason remains unknown.`
  );
  return null;
}

const BRANDGUARD_GEOMETRY_PATH = '/lens-system/okhp3-brandguard/';
const BRANDGUARD_THEME_GEOMETRY_PATHS = new Set(
  PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))
);
const BRANDGUARD_DEFERRED_THEME_GEOMETRY_PATHS = new Set(
  [...BRANDGUARD_THEME_GEOMETRY_PATHS].filter(path => {
    const pagePath = resolve(ROOT, path.slice(1), 'index.html');
    return existsSync(pagePath) &&
      readFileSync(pagePath, 'utf8').includes('data-deferred-styles');
  })
);
// Use the sitemap as the route inventory so each published BrandGuard
// case-study receives the same before/after branded-font measurements.
const BRANDGUARD_FONT_GEOMETRY_PATHS = new Set(
  PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))
);
const BRANDGUARD_FALLBACK_FONT_READABILITY_VIEWPORT_WIDTHS = new Set([400, 401]);
function shouldCheckBrandGuardFallbackFontReadability(path, viewportWidth) {
  return path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
    PUBLIC_PATHS.includes(path) &&
    BRANDGUARD_FALLBACK_FONT_READABILITY_VIEWPORT_WIDTHS.has(viewportWidth);
}
const BRANDGUARD_FORCED_COLORS_PATHS = new Set(
  PUBLIC_PATHS.filter(path =>
    path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
    path !== BRANDGUARD_GEOMETRY_PATH
  )
);
const BRANDGUARD_FORCED_COLORS_VIEWPORTS = new Set([
  'mobile-320',
  'desktop-1280',
]);
function shouldCheckBrandGuardForcedColorsReadability(path, viewportName) {
  return BRANDGUARD_FORCED_COLORS_PATHS.has(path) &&
    PUBLIC_PATHS.includes(path) &&
    BRANDGUARD_FORCED_COLORS_VIEWPORTS.has(viewportName);
}
// Retained as a representative route set for the separate #272 boundary review.
const BRANDGUARD_FONT_EDGE_GEOMETRY_PATHS = new Set([
  BRANDGUARD_GEOMETRY_PATH,
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
]);
const BRANDGUARD_FONT_RESERVATION_EDGE_GEOMETRY_PATHS = new Set([
  BRANDGUARD_GEOMETRY_PATH,
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
]);
const BRANDGUARD_FONT_ACTION_GEOMETRY_PATHS = new Set([
  BRANDGUARD_GEOMETRY_PATH,
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
]);
const BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS = new Set([
  BRANDGUARD_GEOMETRY_PATH,
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
]);
const BRANDGUARD_BREADCRUMB_KEYBOARD_FOCUS_VIEWPORTS = new Set([
  'mobile-350-edge',
  'mobile-370-edge',
]);
const BRANDGUARD_TEXT_SPACING_VIEWPORTS = new Set([
  'mobile-350-edge',
  'mobile-370-edge',
]);
function shouldCheckBrandGuardBreadcrumbKeyboardFocus(path, viewportName) {
  return path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
    PUBLIC_PATHS.includes(path) &&
    BRANDGUARD_BREADCRUMB_KEYBOARD_FOCUS_VIEWPORTS.has(viewportName);
}
function shouldCheckBrandGuardTextSpacing(path, viewportName) {
  return path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
    PUBLIC_PATHS.includes(path) &&
    BRANDGUARD_TEXT_SPACING_VIEWPORTS.has(viewportName);
}
function shouldCheckBrandGuardHeroKeyboardFocus(path, viewportName) {
  return BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS.has(path) &&
    viewportName === 'mobile-320';
}
const BRANDGUARD_HERO_FORCED_COLORS_KEYBOARD_FOCUS_PATHS =
  BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS;
function shouldCheckBrandGuardHeroForcedColorsKeyboardFocus(path, viewportName) {
  return BRANDGUARD_HERO_FORCED_COLORS_KEYBOARD_FOCUS_PATHS.has(path) &&
    PUBLIC_PATHS.includes(path) &&
    viewportName === 'mobile-320';
}
const BRANDGUARD_MOBILE_NAV_KEYBOARD_PATHS = new Set(
  PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))
);
const BRANDGUARD_MOBILE_NAV_KEYBOARD_BOUNDARY_VIEWPORTS = new Set([
  'mobile-355-boundary',
  'mobile-365-boundary',
]);
function shouldCheckBrandGuardMobileNavKeyboard(path, viewportName) {
  return BRANDGUARD_MOBILE_NAV_KEYBOARD_PATHS.has(path) &&
    (BRANDGUARD_MOBILE_NAV_KEYBOARD_BOUNDARY_VIEWPORTS.has(viewportName) ||
      (BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS.has(path) &&
        viewportName === 'mobile-320'));
}
const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-350-edge',
  'mobile-355-boundary',
  'mobile-356-gap',
  'mobile-360',
  'mobile-364-gap',
  'mobile-365-boundary',
  'mobile-370-edge',
  'mobile-390',
  'mobile-400-edge',
  'mobile-401-edge',
  'mobile-430',
  'tablet-768',
  'tablet-769-edge',
  'tablet-899',
  'tablet-900-edge',
]);
const BRANDGUARD_HEADER_THEME_GEOMETRY_VIEWPORTS = new Set([
  'mobile-355-boundary',
  'mobile-365-boundary',
]);
function shouldCheckBrandGuardHeaderThemeGeometry(path, viewportName) {
  return PUBLIC_PATHS.includes(path) &&
    BRANDGUARD_DEFERRED_THEME_GEOMETRY_PATHS.has(path) &&
    BRANDGUARD_HEADER_THEME_GEOMETRY_VIEWPORTS.has(viewportName);
}
const BRANDGUARD_MOBILE_MENU_DESTINATION_PATHS = new Set(
  PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))
);
function shouldCheckBrandGuardMobileMenuDestinations(path, viewportName) {
  return path === BRANDGUARD_GEOMETRY_PATH &&
    BRANDGUARD_MOBILE_MENU_DESTINATION_PATHS.has(path) &&
    viewportName === 'mobile-320';
}
const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-360',
  'mobile-390',
  'mobile-400-edge',
  'mobile-401-edge',
  'tablet-768',
  'tablet-769-edge',
  'tablet-899',
  'tablet-900-edge',
]);
const BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS = new Set([
  'mobile-350-edge',
  'mobile-370-edge',
]);
const BRANDGUARD_FONT_NAV_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
]);
const BRANDGUARD_FONT_NAV_BOUNDARY_GEOMETRY_VIEWPORTS = new Set([
  'mobile-355-boundary',
  'mobile-365-boundary',
]);
const BRANDGUARD_FONT_BOUNDARY_GEOMETRY_VIEWPORTS = new Set([
  'mobile-355-boundary',
  'mobile-365-boundary',
]);
const BRANDGUARD_FONT_GAP_GEOMETRY_PATHS = new Set([
  BRANDGUARD_GEOMETRY_PATH,
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
]);
const BRANDGUARD_FONT_GAP_GEOMETRY_VIEWPORTS = new Set([
  'mobile-356-gap',
  'mobile-357-font-gap',
  'mobile-358-font-gap',
  'mobile-359-font-gap',
  'mobile-360',
  'mobile-361-font-gap',
  'mobile-362-font-gap',
  'mobile-363-font-gap',
  'mobile-364-gap',
]);
const BRANDGUARD_NARROW_CONTENT_VIEWPORT = 'mobile-320';
const BRANDGUARD_FONT_CONTENT_VIEWPORT_WIDTHS = new Set([320, 350, 370]);
const BRANDGUARD_TOUCH_TARGET_VIEWPORT_WIDTHS = new Set([320, 350, 370, 390]);
const BRANDGUARD_TOUCH_TARGET_MINIMUM_PX = 44;
function shouldCheckBrandGuardHeroActions(path, viewportName) {
  return BRANDGUARD_FONT_ACTION_GEOMETRY_PATHS.has(path) &&
    viewportName === BRANDGUARD_NARROW_CONTENT_VIEWPORT;
}
function shouldCheckBrandGuardHeroActionAccessibleNames(path, viewportName) {
  return path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
    PUBLIC_PATHS.includes(path) &&
    viewportName === BRANDGUARD_NARROW_CONTENT_VIEWPORT;
}
const BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1;
const BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1;
const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000;
const BRANDGUARD_GEOMETRY_SELECTORS = [
  '.askjamie-main .askjamie-breadcrumb',
  '.askjamie-main .askjamie-hero-copy h1',
  '.askjamie-main .askjamie-hero-copy .hero-subtitle',
  '.askjamie-main .askjamie-hero-copy .hero-tagline',
];
const BRANDGUARD_TEXT_LINE_SELECTORS = [...BRANDGUARD_GEOMETRY_SELECTORS];
const BRANDGUARD_HERO_ACTION_SELECTOR =
  '.askjamie-main .askjamie-hero-copy .hero-actions > a[href]';
const BRANDGUARD_HERO_ACTION_CONTAINER_SELECTOR =
  '.askjamie-main .askjamie-hero-copy .hero-actions';

function compareBrandGuardFontGeometry(fallbackGeometry, loadedGeometry) {
  return BRANDGUARD_GEOMETRY_SELECTORS.map(selector => {
    const before = fallbackGeometry?.[selector] ?? null;
    const after = loadedGeometry?.[selector] ?? null;
    const delta = before && after
      ? Object.fromEntries(
          ['x', 'y', 'width', 'height'].map(property => [
            property,
            Math.round((after[property] - before[property]) * 100) / 100,
          ])
        )
      : null;
    const changed_properties = delta
      ? Object.keys(delta).filter(
          property => Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX
        )
      : [];
    return {
      selector,
      before,
      after,
      delta,
      changed_properties,
    };
  });
}

function compareBrandGuardTextLineReflow(beforeLines, afterLines) {
  const changes = [];
  const errors = [];
  for (const selector of BRANDGUARD_TEXT_LINE_SELECTORS) {
    const before = beforeLines?.[selector];
    const after = afterLines?.[selector];
    if (!before?.lines?.length || !after?.lines?.length) {
      errors.push(
        `BRANDGUARD HERO TEXT-LINE EVIDENCE UNAVAILABLE: ${selector}; ` +
        `before=${JSON.stringify(before ?? null)}; after=${JSON.stringify(after ?? null)}`
      );
      continue;
    }
    const beforeText = before.lines.map(line => line.text);
    const afterText = after.lines.map(line => line.text);
    if (JSON.stringify(beforeText) !== JSON.stringify(afterText)) {
      const change = {
        selector,
        before_lines: before.lines,
        after_lines: after.lines,
      };
      changes.push(change);
      errors.push(
        `BRANDGUARD HERO TEXT-LINE REFLOW: ${selector} rendered different text lines ` +
        `during deferred theme activation; before=${JSON.stringify(beforeText)}; ` +
        `after=${JSON.stringify(afterText)}`
      );
    }
  }
  return {
    errors,
    evidence: {
      comparison: 'deferred-theme-activation',
      before: beforeLines,
      after: afterLines,
      reflows: changes,
    },
  };
}

function captureBrandGuardTextLineFragments(selectors) {
  const textLines = {};
  const round = value => Math.round(value * 100) / 100;
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (!element) {
      textLines[selector] = null;
      continue;
    }
    const computed = getComputedStyle(element);
    if (
      !element.getClientRects().length ||
      computed.display === 'none' ||
      computed.visibility === 'hidden' ||
      Number(computed.opacity) === 0 ||
      (typeof element.checkVisibility === 'function' &&
        !element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }))
    ) {
      textLines[selector] = { lines: [] };
      continue;
    }

    const words = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let textNode;
    while ((textNode = walker.nextNode())) {
      const expression = /\S+/g;
      let match;
      while ((match = expression.exec(textNode.nodeValue ?? ''))) {
        const wordRange = document.createRange();
        wordRange.setStart(textNode, match.index);
        wordRange.setEnd(textNode, match.index + match[0].length);
        const wordRects = [...wordRange.getClientRects()]
          .filter(wordRect => wordRect.width > 0 && wordRect.height > 0);
        if (wordRects.length === 1) {
          words.push({ text: match[0], rect: wordRects[0] });
          continue;
        }

        // A long unbroken token can wrap internally. Resolve its characters
        // only in that case so each visible line keeps its actual fragment.
        let offset = match.index;
        for (const character of match[0]) {
          const characterRange = document.createRange();
          characterRange.setStart(textNode, offset);
          offset += character.length;
          characterRange.setEnd(textNode, offset);
          const characterRect = [...characterRange.getClientRects()]
            .find(characterRect => characterRect.width > 0 && characterRect.height > 0);
          if (characterRect) words.push({ text: character, rect: characterRect });
        }
      }
    }
    words.sort((first, second) =>
      first.rect.top - second.rect.top || first.rect.left - second.rect.left
    );
    const lines = [];
    for (const word of words) {
      let line = lines.find(candidate => Math.abs(candidate.top - word.rect.top) < 1);
      if (!line) {
        line = {
          top: round(word.rect.top),
          bottom: round(word.rect.bottom),
          fragments: [],
        };
        lines.push(line);
      }
      line.fragments.push({
        text: word.text,
        left: round(word.rect.left),
        right: round(word.rect.right),
      });
      line.bottom = Math.max(line.bottom, round(word.rect.bottom));
    }
    for (const line of lines) {
      line.fragments.sort((first, second) => first.left - second.left);
      line.text = line.fragments.map(fragment => fragment.text).join(' ');
    }
    textLines[selector] = { lines };
  }
  return textLines;
}

const BRANDGUARD_NARROW_CONTENT_SELECTORS = [
  '.askjamie-main .askjamie-breadcrumb',
  '.askjamie-main .askjamie-breadcrumb .breadcrumb-label',
  '.askjamie-main .askjamie-hero-copy h1',
  '.askjamie-main .askjamie-hero-copy .hero-subtitle',
  '.askjamie-main .askjamie-hero-copy .hero-tagline',
  '.askjamie-main .capability-transition',
];
const BRANDGUARD_FONT_HOSTS = new Set(['fonts.googleapis.com', 'fonts.gstatic.com']);
const BRANDGUARD_FONT_FAMILIES = ['Kalam', 'Baloo 2', 'Kalam', 'Open Sans'];
const BRANDGUARD_LOGO_SELECTOR =
  '.askjamie-main .askjamie-logo--crumb img';
const CRITICAL_HERO_STYLESHEET_PATH = '/assets/css/critical-hero.css';
const BRANDGUARD_CRITICAL_STYLE_VIEWPORT = 'mobile-390';
const UNIVERSE_DIAGRAM_GEOMETRY_PATH = '/universe/';
const UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-390',
  'tablet-768',
  'desktop-1280',
]);
const UNIVERSE_PAGE_MAP_FONT_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-390',
  'tablet-768',
  'desktop-1280',
]);
const UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX = 1;
const UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS = 15000;
const UNIVERSE_PAGE_MAP_ZOOM_200_PROBE = {
  reference_viewport: { width: 1280, height: 800 },
  layout_viewport: { width: 640, height: 400 },
  browser_zoom_percent: 200,
};
const UNIVERSE_PAGE_MAP_GROUP_SELECTOR = '.universe-map-generated .universe-map-group';
const UNIVERSE_PAGE_MAP_RESERVATIONS_PX = new Map([
  [4, 22 * 16],
  [5, 17 * 16],
]);
const DEFAULT_UNIVERSE_PAGE_MAP_RESERVATION_PX = 14 * 16;
const UNIVERSE_DIAGRAM_GEOMETRY_SELECTORS = [
  '.askjamie-hero--universe .mermaid-scroll-wrap',
  '.askjamie-hero--universe .askjamie-mermaid-shell',
];

async function checkCriticalHeroStyles(page, path, viewportWidth, stylesheetResponses) {
  if (path !== BRANDGUARD_GEOMETRY_PATH &&
      path !== UNIVERSE_DIAGRAM_GEOMETRY_PATH) {
    return null;
  }

  const evidence = await page.evaluate(({ path, stylesheetPath, viewportWidth }) => {
    const stylesheetLink = [...document.querySelectorAll('link[rel~="stylesheet"]')]
      .find(link => new URL(link.href).pathname === stylesheetPath);
    const rootFontSize = Number.parseFloat(
      getComputedStyle(document.documentElement).fontSize
    );
    const evidence = {
      stylesheet: {
        href: stylesheetLink?.href ?? null,
        same_origin: stylesheetLink
          ? new URL(stylesheetLink.href).origin === location.origin
          : false,
        attached: Boolean(stylesheetLink?.sheet),
      },
      root_font_size_px: rootFontSize,
      computed: {},
    };

    if (path === '/lens-system/okhp3-brandguard/') {
      const selector = '.askjamie-brandguard-page .askjamie-hero-copy h1';
      const heading = document.querySelector(selector);
      const style = heading ? getComputedStyle(heading) : null;
      evidence.computed.brandguard_heading = {
        selector,
        present: Boolean(heading),
        font_size: style?.fontSize ?? null,
        letter_spacing: style?.letterSpacing ?? null,
        expected_font_size: `${rootFontSize * 2}px`,
        expected_letter_spacing: `${rootFontSize * 2 * -0.03}px`,
      };
    } else {
      const selector = '.askjamie-hero--universe .mermaid-scroll-wrap';
      const wrapper = document.querySelector(selector);
      const style = wrapper ? getComputedStyle(wrapper) : null;
      const minHeightRem = viewportWidth <= 640 ? 18 : 22;
      evidence.computed.universe_diagram_wrapper = {
        selector,
        present: Boolean(wrapper),
        min_height: style?.minHeight ?? null,
        overflow: style?.overflow ?? null,
        expected_min_height: `${rootFontSize * minHeightRem}px`,
      };
    }
    return evidence;
  }, { path, stylesheetPath: CRITICAL_HERO_STYLESHEET_PATH, viewportWidth });

  const errors = [];
  const releaseOrigin = new URL(BASE_URL).origin;
  const stylesheetResponse = [...stylesheetResponses].reverse().find(response =>
    new URL(response.url).origin === releaseOrigin &&
    new URL(response.url).pathname === CRITICAL_HERO_STYLESHEET_PATH
  );
  if (!evidence.stylesheet.href) {
    errors.push(
      `CRITICAL HERO STYLESHEET LINK MISSING: route ${path} has no ` +
      `${CRITICAL_HERO_STYLESHEET_PATH} link`
    );
  }
  if (evidence.stylesheet.href && !evidence.stylesheet.same_origin) {
    errors.push(
      `CRITICAL HERO STYLESHEET NOT LOCAL: route ${path} ` +
      `${evidence.stylesheet.href} is not served by ${releaseOrigin}`
    );
  }
  if (!stylesheetResponse) {
    errors.push(
      `CRITICAL HERO STYLESHEET RESPONSE MISSING: route ${path} did not request ` +
      `${CRITICAL_HERO_STYLESHEET_PATH}`
    );
  } else if (stylesheetResponse.status >= 400) {
    errors.push(
      `CRITICAL HERO STYLESHEET HTTP ${stylesheetResponse.status}: route ${path} ` +
      `${stylesheetResponse.url}`
    );
  }
  if (!evidence.stylesheet.attached) {
    errors.push(
      `CRITICAL HERO STYLESHEET NOT APPLIED: route ${path} ` +
      `${CRITICAL_HERO_STYLESHEET_PATH} has no attached CSSStyleSheet`
    );
  }

  const closeEnough = (actual, expected) =>
    Number.isFinite(Number.parseFloat(actual)) &&
    Math.abs(Number.parseFloat(actual) - Number.parseFloat(expected)) < 0.05;
  if (path === BRANDGUARD_GEOMETRY_PATH) {
    const heading = evidence.computed.brandguard_heading;
    if (!heading.present ||
        !closeEnough(heading.font_size, heading.expected_font_size) ||
        !closeEnough(heading.letter_spacing, heading.expected_letter_spacing)) {
      errors.push(
        `CRITICAL HERO STYLE MISMATCH: route ${path} ${heading.selector} ` +
        `computed font-size=${JSON.stringify(heading.font_size)}, ` +
        `letter-spacing=${JSON.stringify(heading.letter_spacing)}; expected ` +
        `font-size=${heading.expected_font_size}, ` +
        `letter-spacing=${heading.expected_letter_spacing}`
      );
    }
  } else {
    const wrapper = evidence.computed.universe_diagram_wrapper;
    if (!wrapper.present ||
        !closeEnough(wrapper.min_height, wrapper.expected_min_height) ||
        wrapper.overflow !== 'hidden') {
      errors.push(
        `CRITICAL HERO STYLE MISMATCH: route ${path} ${wrapper.selector} ` +
        `computed min-height=${JSON.stringify(wrapper.min_height)}, ` +
        `overflow=${JSON.stringify(wrapper.overflow)}; expected ` +
        `min-height=${wrapper.expected_min_height}, overflow="hidden"`
      );
    }
  }

  return {
    errors,
    evidence: {
      ...evidence,
      stylesheet_response: stylesheetResponse ?? null,
    },
  };
}

function hasDarkUniverseTheme(state) {
  return state?.color_scheme === 'dark' && state?.prefers_dark === true;
}

async function captureUniversePageMapGeometry(
  page,
  groupIndex,
  expectedOpenGroupIndexes = [groupIndex]
) {
  return page.evaluate(({ index, expectedOpenGroupIndexes }) => {
    const round = value => Math.round(value * 100) / 100;
    const groups = [...document.querySelectorAll(
      '.universe-map-generated .universe-map-group'
    )];
    const group = groups[index];
    if (!group) return null;

    const box = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [
          property,
          round(rect[property] + (property === 'x' ? window.scrollX : property === 'y' ? window.scrollY : 0)),
        ])
      );
    };
    const diagram = group.querySelector('.mermaid');
    const scrollWrap = group.querySelector('.mermaid-scroll-wrap');
    const svg = diagram?.querySelector('svg');
    const summary = group.querySelector('summary');
    const previousSummary = groups[index - 1]?.querySelector('summary');
    const nextSummary = groups[index + 1]?.querySelector('summary');

    return {
      color_scheme: document.documentElement.getAttribute('data-color-scheme'),
      prefers_dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
      title: summary?.textContent.trim() ?? '',
      node_count: Number(group.dataset.mapNodeCount),
      open: group.open,
      other_groups_collapsed: groups.every((other, otherIndex) =>
        otherIndex === index || !other.open
      ),
      only_expected_groups_open: groups.every((other, otherIndex) =>
        expectedOpenGroupIndexes.includes(otherIndex) ? other.open : !other.open
      ),
      scroll_left: scrollWrap?.scrollLeft ?? null,
      render_started: diagram?.dataset.mermaidRendered === '1',
      svg_ready: Boolean(diagram?.querySelector('svg .node')),
      has_svg_node: Boolean(diagram?.querySelector('svg .node')),
      source_present: diagram?.textContent.includes('flowchart') ?? false,
      reserved_min_height_px: scrollWrap
        ? round(parseFloat(getComputedStyle(scrollWrap).minHeight))
        : null,
      svg_geometry: box(svg),
      geometry: {
        group: box(group),
        summary: box(summary),
        figure: box(group.querySelector('figure.askjamie-mermaid-shell')),
        reserved_shell: box(scrollWrap),
        previous_group_summary: box(previousSummary),
        next_group_summary: box(nextSummary),
      },
    };
  }, { index: groupIndex, expectedOpenGroupIndexes });
}

async function openUniversePageMapGroupForGeometryCheck(page, groupIndex, groupCount) {
  const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
  const target = groups.nth(groupIndex);
  const errors = [];
  const initiallyCollapsed = await groups.evaluateAll(elements =>
    elements.every(element => !element.open)
  );
  if (!initiallyCollapsed) {
    errors.push(
      `UNIVERSE PAGE MAP COLLAPSED STATE INVALID: expected every group except ` +
      `${groupIndex + 1} to be closed before opening it`
    );
  }

  await target.locator('summary').focus();
  await page.keyboard.press('Enter');
  const openedByKeyboard = await target.evaluate(group => group.open);
  if (!openedByKeyboard) {
    errors.push(
      `UNIVERSE PAGE MAP KEYBOARD OPEN FAILED: Enter did not open group ${groupIndex + 1}`
    );
  }

  const othersStayedCollapsed = await groups.evaluateAll((elements, index) =>
    elements.every((element, elementIndex) =>
      elementIndex === index || !element.open
    ),
  groupIndex);
  if (!othersStayedCollapsed) {
    errors.push(
      'UNIVERSE PAGE MAP COLLAPSED STATE INVALID: opening one group also opened a different group'
    );
  }

  // The page-init hook holds Mermaid's scheduled render callback until the
  // shell and its neighboring summaries have been measured.
  await target.locator('.mermaid').scrollIntoViewIfNeeded();
  const renderScheduled = await page.waitForFunction(
    () => window.__responsiveQaMermaidRenderQueue?.length > 0,
    undefined,
    { timeout: 10000 }
  ).then(() => true, () => false);
  if (!renderScheduled) {
    errors.push(
      `UNIVERSE PAGE MAP RENDER NOT SCHEDULED: group ${groupIndex + 1} did not reach the Mermaid observer`
    );
  }

  const before = await captureUniversePageMapGeometry(page, groupIndex);
  if (before?.svg_ready) {
    errors.push(
      `UNIVERSE PAGE MAP BASELINE UNAVAILABLE: group ${groupIndex + 1} rendered before its ` +
      `reserved-shell measurement; before=${JSON.stringify(before)}`
    );
  }
  if (!before?.source_present) {
    errors.push(
      `UNIVERSE PAGE MAP SOURCE FALLBACK CHANGED: expected Mermaid source before SVG readiness ` +
      `for group ${groupIndex + 1}; before=${JSON.stringify({
        source_present: before?.source_present,
        render_started: before?.render_started,
      })}`
    );
  }
  if (!before?.other_groups_collapsed) {
    errors.push(
      `UNIVERSE PAGE MAP COLLAPSED STATE INVALID: group ${groupIndex + 1} opened while another ` +
      `generated group was also open; before=${JSON.stringify(before)}`
    );
  }
  if (!hasDarkUniverseTheme(before)) {
    errors.push(
      `UNIVERSE PAGE MAP DARK THEME NOT ACTIVE: expected a pinned dark color scheme and dark ` +
      `browser preference before measuring group ${groupIndex + 1}; ` +
      `before=${JSON.stringify({ color_scheme: before?.color_scheme, prefers_dark: before?.prefers_dark })}`
    );
  }

  const expectedReservation = UNIVERSE_PAGE_MAP_RESERVATIONS_PX.get(before?.node_count) ??
    DEFAULT_UNIVERSE_PAGE_MAP_RESERVATION_PX;
  if (before?.reserved_min_height_px == null ||
      before.reserved_min_height_px + UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX <
        expectedReservation ||
      before.geometry.reserved_shell?.height + UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX <
        expectedReservation) {
    errors.push(
      `UNIVERSE PAGE MAP RESERVATION TOO SMALL: group ${groupIndex + 1} expected a ` +
      `${before?.title ? `"${before.title}" ` : ''}` +
      `${expectedReservation}px reserved shell before rendering; measured=${JSON.stringify({
        min_height_px: before?.reserved_min_height_px,
        shell: before?.geometry?.reserved_shell,
      })}`
    );
  }

  return {
    errors,
    groupIndex,
    groupCount,
    before,
    openedByKeyboard,
    renderScheduled,
    initiallyCollapsed,
    othersStayedCollapsed,
  };
}

async function releaseResponsiveQaMermaidRender(page) {
  return page.evaluate(() => {
    const entry = window.__responsiveQaMermaidRenderQueue?.shift();
    if (!entry) return false;
    entry.callback(...entry.args);
    return true;
  });
}

async function compareUniversePageMapGeometry(page, groupCheck, after) {
  const { groupIndex, groupCount, before } = groupCheck;
  const errors = [];
  const shifts = [];
  const groupLabel = before?.title ? `"${before.title}"` : `group ${groupIndex + 1}`;
  if (before && after) {
    if (!hasDarkUniverseTheme(after)) {
      errors.push(
        `UNIVERSE PAGE MAP DARK THEME NOT ACTIVE: expected a pinned dark color scheme and dark ` +
        `browser preference after rendering group ${groupIndex + 1}; ` +
        `after=${JSON.stringify({ color_scheme: after?.color_scheme, prefers_dark: after?.prefers_dark })}`
      );
    }
    for (const selector of [
      'group',
      'summary',
      'figure',
      'reserved_shell',
      'previous_group_summary',
      'next_group_summary',
    ]) {
      const beforeRect = before.geometry[selector];
      const afterRect = after.geometry[selector];
      if (!beforeRect || !afterRect) {
        // The first/last group has no neighbor summary on one side.
        if ((selector === 'previous_group_summary' && groupIndex === 0) ||
            (selector === 'next_group_summary' && groupIndex === groupCount - 1)) {
          continue;
        }
        errors.push(
          `UNIVERSE PAGE MAP GEOMETRY MISSING: group ${groupIndex + 1} ${groupLabel} ${selector}; ` +
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
        const shift = {
          selector,
          before: beforeRect,
          after: afterRect,
          delta,
          changed_properties: changedProperties,
        };
        shifts.push(shift);
        errors.push(
          `UNIVERSE PAGE MAP GEOMETRY SHIFT: group ${groupIndex + 1} ${groupLabel} ${selector} changed ` +
          `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
          `with ${UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
          `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
        );
      }
    }

    if (after.svg_geometry &&
        before.geometry.reserved_shell.height +
          UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX < after.svg_geometry.height) {
      errors.push(
        `UNIVERSE PAGE MAP CONTENT EXCEEDS RESERVED SHELL: group ${groupIndex + 1} ${groupLabel} ` +
        `reserved=${JSON.stringify(before.geometry.reserved_shell)}; ` +
        `rendered=${JSON.stringify(after.svg_geometry)}`
      );
    }
  }

  if (!after?.open || !after?.other_groups_collapsed || !after?.svg_ready) {
    errors.push(
      `UNIVERSE PAGE MAP READY STATE INVALID: expected group ${groupIndex + 1} ${groupLabel} ` +
      `to render while every other group remains collapsed; ` +
      `after=${JSON.stringify({
        open: after?.open,
        other_groups_collapsed: after?.other_groups_collapsed,
        svg_ready: after?.svg_ready,
      })}`
    );
  }

  let closedByKeyboard = false;
  try {
    const target = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(groupIndex);
    await target.locator('summary').focus();
    await page.keyboard.press('Enter');
    closedByKeyboard = !(await target.evaluate(group => group.open));
    if (!closedByKeyboard) {
      errors.push(
        `UNIVERSE PAGE MAP KEYBOARD CLOSE FAILED: Enter did not close group ${groupIndex + 1} ${groupLabel}`
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP KEYBOARD CLOSE CHECK ERROR: group ${groupIndex + 1} ` +
      `${error.message.split('\n')[0]}`
    );
  }

  return { errors, shifts, closedByKeyboard };
}

async function addUniversePageMapConnectorFixtures(page, groupIndex) {
  return page.locator('.universe-map-generated .universe-map-group')
    .nth(groupIndex).locator('.mermaid svg').evaluate(svg => {
      const fixtureDefinitions = [
        { name: 'dotted', dasharray: '1 3' },
        { name: 'dashed', dasharray: '7 4' },
        { name: 'opaque', dasharray: 'none' },
        { name: 'translucent', dasharray: 'none' },
        { name: 'paint-translucent', dasharray: 'none' },
        { name: 'reverse', dasharray: 'none', markerDirections: ['start'] },
        { name: 'two-way', dasharray: 'none', markerDirections: ['start', 'end'] },
      ];
      const errors = [];
      const sourceConnector = svg.querySelector(
        'path.flowchart-link[marker-end], path.flowchart-link[marker-start]'
      );
      const sourceDirection = ['marker-end', 'marker-start']
        .find(direction => sourceConnector?.hasAttribute(direction));
      const sourceMarkerId = sourceConnector?.getAttribute(sourceDirection)
        ?.match(/url\(["']?#([^"')]+)["']?\)/)?.[1];
      const sourceMarker = sourceMarkerId
        ? [...svg.querySelectorAll('marker')].find(marker => marker.id === sourceMarkerId)
        : null;
      if (!sourceConnector || !sourceMarker) {
        return {
          fixtures: [],
          errors: ['no flow connector with a rendered marker is available to clone'],
        };
      }

      const fixtures = fixtureDefinitions.map(({ name, dasharray, markerDirections }) => {
        const directions = markerDirections ?? ['end'];
        let connector = svg.querySelector(
          `path.flowchart-link[data-responsive-qa-connector-fixture="${name}"]`
        );
        if (!connector) {
          connector = sourceConnector.cloneNode(true);
          connector.dataset.responsiveQaConnectorFixture = name;
          connector.removeAttribute('marker-start');
          connector.removeAttribute('marker-end');
          sourceConnector.parentElement.append(connector);
        }
        const markers = directions.map(direction => {
          let marker = [...svg.querySelectorAll('marker')]
            .find(candidate =>
              candidate.dataset.responsiveQaConnectorFixture === name &&
              candidate.dataset.responsiveQaConnectorFixtureDirection === direction
            );
          if (!marker) {
            marker = sourceMarker.cloneNode(true);
            marker.id = `${sourceMarker.id}-responsive-qa-${name}-${direction}`;
            marker.dataset.responsiveQaConnectorFixture = name;
            marker.dataset.responsiveQaConnectorFixtureDirection = direction;
            marker.removeAttribute('aria-hidden');
            sourceMarker.parentElement.append(marker);
          }
          connector.setAttribute(`marker-${direction}`, `url(#${marker.id})`);
          return marker;
        });
        if (name === 'opaque' || name === 'translucent' ||
            name === 'paint-translucent') {
          const dark = document.documentElement.getAttribute('data-color-scheme') === 'dark';
          const paint = dark ? '#fff' : '#000';
          connector.style.stroke = paint;
          connector.style.strokeOpacity = name === 'paint-translucent' ? '0.25' : '1';
          connector.style.opacity = name === 'translucent' ? '0.25' : '1';
          for (const marker of markers) {
            for (const path of marker.querySelectorAll('path')) {
              path.style.fill = paint;
              path.style.fillOpacity = name === 'paint-translucent' ? '0.25' : '1';
              path.style.opacity = name === 'translucent' ? '0.25' : '1';
            }
          }
        } else {
          connector.style.strokeDasharray = dasharray;
        }
        return {
          name,
          dasharray: getComputedStyle(connector).strokeDasharray,
          marker_directions: directions.map(direction => `marker-${direction}`),
        };
      });
      return { fixtures, errors };
    });
}

async function captureUniversePageMapReadability(page, groupIndex, theme) {
  const connectorFixtures = await addUniversePageMapConnectorFixtures(
    page,
    groupIndex
  );
  const evidence = await page.evaluate(({ index, themeName }) => {
    const color = value => {
      const text = value?.trim().toLowerCase();
      const hex = text?.match(/^#([0-9a-f]{3,8})$/i)?.[1];
      if (hex) {
        const expanded = hex.length <= 4
          ? [...hex].map(channel => channel + channel).join('')
          : hex;
        if (![6, 8].includes(expanded.length)) return null;
        return [
          parseInt(expanded.slice(0, 2), 16),
          parseInt(expanded.slice(2, 4), 16),
          parseInt(expanded.slice(4, 6), 16),
          expanded.length === 8 ? parseInt(expanded.slice(6, 8), 16) / 255 : 1,
        ];
      }
      const match = text?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const channels = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return channels.length >= 3
        ? [channels[0], channels[1], channels[2], channels[3] ?? 1]
        : null;
    };
    const luminance = value => {
      const channels = color(value);
      if (!channels || channels[3] < 0.99) return null;
      const [red, green, blue] = channels.slice(0, 3).map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.04045
          ? normalized / 12.92
          : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    const contrast = (foreground, background) => {
      const first = luminance(foreground);
      const second = luminance(background);
      return first === null || second === null
        ? null
        : (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const contrastAfterOpacity = (foreground, background, opacity) => {
      const paint = color(foreground);
      const paper = color(background);
      if (!paint || !paper || paper[3] < 0.99 || !Number.isFinite(opacity)) return null;
      const alpha = Math.max(0, Math.min(1, paint[3] * opacity));
      const composite = paint.slice(0, 3).map((channel, index) =>
        channel * alpha + paper[index] * (1 - alpha)
      );
      const relativeLuminance = channels => {
        const [red, green, blue] = channels.map(channel => {
          const normalized = channel / 255;
          return normalized <= 0.04045
            ? normalized / 12.92
            : ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      };
      const first = relativeLuminance(composite);
      const second = relativeLuminance(paper.slice(0, 3));
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const opacityChain = (element, stopAt = svg) => {
      let combined = 1;
      const values = [];
      for (let current = element; current; current = current.parentElement) {
        const opacity = getComputedStyle(current).opacity;
        const parsed = Number(opacity);
        combined *= Number.isFinite(parsed) ? parsed : 1;
        values.push({
          element: `${current.tagName.toLowerCase()}${current.id ? `#${current.id}` : ''}`,
          opacity,
        });
        if (current === stopAt || current === svg) break;
      }
      return { combined, values };
    };
    const sameColor = (first, second) => {
      const a = color(first);
      const b = color(second);
      return Boolean(a && b && a[3] >= 0.99 && b[3] >= 0.99 &&
        a.slice(0, 3).every((channel, channelIndex) =>
          Math.abs(channel - b[channelIndex]) <= 1
        ));
    };
    const group = document.querySelectorAll(
      '.universe-map-generated .universe-map-group'
    )[index];
    const main = group?.closest('.askjamie-main');
    const svg = group?.querySelector('.mermaid svg');
    const forcedColors = themeName === 'forced-colors';
    const resolveSvgColor = (property, value) => {
      if (!svg) return '';
      const probe = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      probe.style[property] = value;
      svg.append(probe);
      const resolved = getComputedStyle(probe)[property];
      probe.remove();
      return resolved;
    };
    const expectedNodeFill = forcedColors
      ? resolveSvgColor('fill', 'Canvas')
      : getComputedStyle(main ?? document.documentElement)
        .getPropertyValue('--color-surface').trim();
    const nodes = [...(svg?.querySelectorAll('.node') ?? [])];
    const nodeLabels = nodes.map((node, nodeIndex) => {
      const label = node.querySelector('.nodeLabel p, .nodeLabel');
      const surface = node.querySelector('rect');
      const foreground = label ? getComputedStyle(label).color : '';
      const background = surface ? getComputedStyle(surface).fill : '';
      return {
        element: `svg .node:nth-of-type(${nodeIndex + 1}) .nodeLabel`,
        text: label?.textContent.trim() ?? '',
        foreground,
        background,
        ratio: contrast(foreground, background),
      };
    });
    const nodeFills = nodes.map((node, nodeIndex) => {
      const surface = node.querySelector('rect');
      const fill = surface ? getComputedStyle(surface).fill : '';
      const stroke = surface ? getComputedStyle(surface).stroke : '';
      return {
        element: `svg .node:nth-of-type(${nodeIndex + 1}) rect`,
        fill,
        stroke,
        expected_fill: expectedNodeFill,
        matches_theme: sameColor(fill, expectedNodeFill),
        opaque: Boolean(color(fill)?.[3] >= 0.99),
        stroke_contrast: contrast(stroke, fill),
      };
    });
    const paperBackground = forcedColors
      ? resolveSvgColor('fill', 'Canvas')
      : getComputedStyle(main ?? document.documentElement)
        .getPropertyValue('--color-bg').trim();
    const edgeLabels = [...(svg?.querySelectorAll('.edgeLabel') ?? [])]
      .flatMap((edgeGroup, labelIndex) => {
        const label = edgeGroup.querySelector('text, p, span');
        if (!label?.textContent.trim()) return [];
        const surface = edgeGroup?.querySelector('rect');
        const foreground = label.tagName.toLowerCase() === 'text'
          ? getComputedStyle(label).fill
          : getComputedStyle(label).color;
        const candidateBackground = surface
          ? getComputedStyle(surface).fill
          : edgeGroup
            ? getComputedStyle(edgeGroup).backgroundColor
            : '';
        const background = luminance(candidateBackground) === null
          ? paperBackground
          : candidateBackground;
        return [{
          element: `svg .edgeLabel:nth-of-type(${labelIndex + 1})`,
          text: label.textContent.trim(),
          foreground,
          background,
          ratio: contrast(foreground, background),
        }];
      });
    const connectorElements = [...(svg?.querySelectorAll('path.flowchart-link') ?? [])];
    const flowConnectors = connectorElements.map((connector, connectorIndex) => {
      const style = getComputedStyle(connector);
      const foreground = style.stroke;
      const chain = opacityChain(connector);
      const strokeOpacity = Number(style.strokeOpacity || 1);
      const effectiveOpacity = chain.combined * strokeOpacity;
      return {
        element: `svg .edgePaths path.flowchart-link:nth-of-type(${connectorIndex + 1})`,
        foreground,
        background: paperBackground,
        ratio: contrastAfterOpacity(foreground, paperBackground, effectiveOpacity),
        effective_opacity: effectiveOpacity,
        opacity_values: {
          element_chain: chain.values,
          stroke_opacity: style.strokeOpacity,
        },
        dasharray: style.strokeDasharray,
        fixture: connector.dataset.responsiveQaConnectorFixture ?? null,
      };
    });
    const markerReferences = connectorElements.flatMap((connector, connectorIndex) =>
      ['marker-start', 'marker-end'].flatMap(direction => {
        const markerId = connector.getAttribute(direction)
          ?.match(/url\(["']?#([^"')]+)["']?\)/)?.[1];
        return markerId ? [{ markerId, direction, connector, connectorIndex }] : [];
      })
    );
    const markerIds = [...new Set(markerReferences.map(reference => reference.markerId))];
    const arrowheads = markerReferences.flatMap(
      ({ markerId, direction, connector, connectorIndex }) => {
      const connectorElement =
        `svg .edgePaths path.flowchart-link:nth-of-type(${connectorIndex + 1})`;
      const marker = [...(svg?.querySelectorAll('marker') ?? [])]
        .find(candidate => candidate.id === markerId);
      if (!marker) {
        return [{
          element: `svg marker#${markerId} (${direction})`,
          direction,
          connector_element: connectorElement,
          foreground: null,
          background: paperBackground,
          ratio: null,
          fixture: connector.dataset.responsiveQaConnectorFixture ?? null,
        }];
      }
      return [...marker.querySelectorAll('path')].flatMap((path, pathIndex) => {
        const style = getComputedStyle(path);
        const markerChain = opacityChain(path, marker);
        const connectorChain = opacityChain(connector);
        return ['fill', 'stroke'].flatMap(property => {
          const foreground = style[property];
          if (foreground === 'none' || !color(foreground)) return [];
          const paintOpacity = Number(style.getPropertyValue(`${property}-opacity`) || 1);
          const effectiveOpacity = connectorChain.combined *
            markerChain.combined * paintOpacity;
          return [{
            element: `svg marker#${markerId} path:nth-of-type(${pathIndex + 1}) ` +
              `${property} (${direction})`,
            direction,
            connector_element: connectorElement,
            foreground,
            background: paperBackground,
            effective_opacity: effectiveOpacity,
            opacity_values: {
              connector_element_chain: connectorChain.values,
              marker_element_chain: markerChain.values,
              paint_opacity: style.getPropertyValue(`${property}-opacity`),
            },
            ratio: contrastAfterOpacity(foreground, paperBackground, effectiveOpacity),
            fixture: marker.dataset.responsiveQaConnectorFixture ?? null,
          }];
        });
      });
    });
    return {
      theme: themeName,
      svg_ready: Boolean(svg?.querySelector('.node')),
      node_labels: nodeLabels,
      node_fills: nodeFills,
      edge_labels: edgeLabels,
      flow_connectors: flowConnectors,
      arrowheads,
      marker_ids: markerIds,
    };
  }, { index: groupIndex, themeName: theme });

  const errors = [];
  const groupLabel = `group ${groupIndex + 1}`;
  if (!evidence?.svg_ready) {
    errors.push(
      `UNIVERSE PAGE MAP READABILITY UNAVAILABLE: theme=${theme} ${groupLabel} has no rendered SVG`
    );
    return { errors, evidence };
  }
  if (evidence.node_labels.length === 0) {
    errors.push(
      `UNIVERSE PAGE MAP READABILITY INVALID: theme=${theme} ${groupLabel} has no node-label SVG elements`
    );
  }
  for (const sample of evidence.node_labels) {
    if (sample.ratio === null || sample.ratio < 4.5) {
      errors.push(
        `UNIVERSE PAGE MAP READABILITY FAILURE: theme=${theme} ` +
        `element=${sample.element} node-label contrast=${sample.ratio}; ` +
        `${JSON.stringify(sample)}`
      );
    }
  }
  for (const sample of evidence.node_fills) {
    if (!sample.opaque || !sample.matches_theme) {
      errors.push(
        `UNIVERSE PAGE MAP READABILITY FAILURE: theme=${theme} ` +
        `element=${sample.element} node fill is missing, transparent, or not using the active theme surface; ` +
        `${JSON.stringify(sample)}`
      );
    }
    if (theme === 'forced-colors' &&
        (sample.stroke_contrast === null || sample.stroke_contrast < 3)) {
      errors.push(
        `UNIVERSE PAGE MAP READABILITY FAILURE: theme=${theme} ` +
        `element=${sample.element} node surface outline contrast=${sample.stroke_contrast}; ` +
        `${JSON.stringify(sample)}`
      );
    }
  }
  for (const sample of evidence.edge_labels) {
    if (sample.ratio === null || sample.ratio < 4.5) {
      errors.push(
        `UNIVERSE PAGE MAP READABILITY FAILURE: theme=${theme} ` +
        `element=${sample.element} edge-label contrast=${sample.ratio}; ` +
        `${JSON.stringify(sample)}`
      );
    }
  }
  if (evidence.flow_connectors.length === 0) {
    errors.push(
      `UNIVERSE PAGE MAP CONNECTOR READABILITY INVALID: theme=${theme} ${groupLabel} ` +
      `has no flow-connector SVG paths`
    );
  }
  for (const fixture of connectorFixtures.errors) {
    errors.push(
      `UNIVERSE PAGE MAP CONNECTOR FIXTURE INVALID: theme=${theme} ` +
      `group=${groupIndex + 1} ${fixture}`
    );
  }
  for (const fixtureName of ['dotted', 'dashed']) {
    const connector = evidence.flow_connectors.find(
      sample => sample.fixture === fixtureName
    );
    const expectedDasharray = connectorFixtures.fixtures.find(
      fixture => fixture.name === fixtureName
    )?.dasharray;
    if (!connector || !connector.dasharray || connector.dasharray === 'none' ||
        connector.dasharray !== expectedDasharray) {
      errors.push(
        `UNIVERSE PAGE MAP CONNECTOR FIXTURE INVALID: theme=${theme} ` +
        `group=${groupIndex + 1} fixture=${fixtureName} is not measured as a non-solid path; ` +
        `${JSON.stringify({ connector, expectedDasharray, connectorFixtures })}`
      );
    }
    if (!evidence.arrowheads.some(sample => sample.fixture === fixtureName)) {
      errors.push(
        `UNIVERSE PAGE MAP ARROWHEAD FIXTURE INVALID: theme=${theme} ` +
        `group=${groupIndex + 1} fixture=${fixtureName} direction=marker-end ` +
        `element=${connector?.element ?? 'missing'} marker is not measured; ` +
        `${JSON.stringify(evidence.arrowheads)}`
      );
    }
  }
  for (const [fixtureName, expectedDirections] of [
    ['reverse', ['marker-start']],
    ['two-way', ['marker-start', 'marker-end']],
  ]) {
    const connector = evidence.flow_connectors.find(
      sample => sample.fixture === fixtureName
    );
    const fixture = connectorFixtures.fixtures.find(
      sample => sample.name === fixtureName
    );
    if (!connector || !fixture ||
        JSON.stringify(fixture.marker_directions) !== JSON.stringify(expectedDirections)) {
      errors.push(
        `UNIVERSE PAGE MAP ARROWHEAD FIXTURE INVALID: theme=${theme} ` +
        `group=${groupIndex + 1} fixture=${fixtureName} expected_directions=` +
        `${JSON.stringify(expectedDirections)} element=${connector?.element ?? 'missing'}; ` +
        `${JSON.stringify({ connector, fixture, connectorFixtures })}`
      );
    }
    for (const direction of expectedDirections) {
      if (!evidence.arrowheads.some(sample =>
        sample.fixture === fixtureName && sample.direction === direction
      )) {
        errors.push(
          `UNIVERSE PAGE MAP ARROWHEAD FIXTURE INVALID: theme=${theme} ` +
          `group=${groupIndex + 1} fixture=${fixtureName} direction=${direction} ` +
          `element=${connector?.element ?? 'missing'} marker is not measured; ` +
          `${JSON.stringify(evidence.arrowheads.filter(
            sample => sample.fixture === fixtureName
          ))}`
        );
      }
    }
  }
  for (const sample of evidence.flow_connectors.filter(
    candidate => !['translucent', 'paint-translucent'].includes(candidate.fixture)
  )) {
    if (sample.ratio === null || sample.ratio < 3) {
      errors.push(
        `UNIVERSE PAGE MAP CONNECTOR CONTRAST FAILURE: theme=${theme} ` +
        `element=${sample.element} dasharray=${sample.dasharray} ` +
        `opacity_values=${JSON.stringify(sample.opacity_values)} ` +
        `flow-connector contrast=${sample.ratio}; ` +
        `${JSON.stringify(sample)}`
      );
    }
  }
  for (const fixtureName of ['opaque', 'translucent', 'paint-translucent']) {
    const sample = evidence.flow_connectors.find(
      candidate => candidate.fixture === fixtureName
    );
    const passes = fixtureName === 'opaque'
      ? sample?.ratio !== null && sample?.ratio >= 3
      : sample?.ratio !== null && sample?.ratio < 3;
    if (!passes) {
      errors.push(
        `UNIVERSE PAGE MAP CONNECTOR OPACITY FIXTURE INVALID: theme=${theme} ` +
        `element=${sample?.element ?? 'missing'} fixture=${fixtureName} ` +
        `opacity_values=${JSON.stringify(sample?.opacity_values)} ` +
        `contrast=${sample?.ratio}; ${JSON.stringify(sample)}`
      );
    }
  }
  if (evidence.marker_ids.length === 0 || evidence.arrowheads.length === 0) {
    errors.push(
      `UNIVERSE PAGE MAP ARROWHEAD READABILITY INVALID: theme=${theme} ${groupLabel} ` +
      `direction=marker-start|marker-end element=svg marker reference ` +
      `has no measurable arrowheads`
    );
  }
  for (const sample of evidence.arrowheads.filter(
    candidate => !['translucent', 'paint-translucent'].includes(candidate.fixture)
  )) {
    if (sample.ratio === null || sample.ratio < 3) {
      errors.push(
        `UNIVERSE PAGE MAP ARROWHEAD CONTRAST FAILURE: theme=${theme} ` +
        `direction=${sample.direction} element=${sample.element} ` +
        `connector_element=${sample.connector_element} ` +
        `opacity_values=${JSON.stringify(sample.opacity_values)} ` +
        `arrowhead contrast=${sample.ratio}; ` +
        `${JSON.stringify(sample)}`
      );
    }
  }
  for (const fixtureName of ['opaque', 'translucent', 'paint-translucent']) {
    const sample = evidence.arrowheads.find(
      candidate => candidate.fixture === fixtureName
    );
    const passes = fixtureName === 'opaque'
      ? sample?.ratio !== null && sample?.ratio >= 3
      : sample?.ratio !== null && sample?.ratio < 3;
    if (!passes) {
      errors.push(
        `UNIVERSE PAGE MAP ARROWHEAD OPACITY FIXTURE INVALID: theme=${theme} ` +
        `direction=${sample?.direction ?? 'missing'} ` +
        `element=${sample?.element ?? 'missing'} ` +
        `connector_element=${sample?.connector_element ?? 'missing'} fixture=${fixtureName} ` +
        `opacity_values=${JSON.stringify(sample?.opacity_values)} ` +
        `contrast=${sample?.ratio}; ${JSON.stringify(sample)}`
      );
    }
  }
  return { errors, evidence };
}

async function captureUniversePageMapControlReadability(page, groupIndex) {
  const errors = [];
  const transitionEvidence = {};
  const target = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(groupIndex);
  const summary = target.locator(':scope > summary');
  const firstLink = target.locator('a[data-universe-node]').first();
  const settleMediaChange = async () => page.evaluate(() =>
    new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    ))
  );
  const captureFocusedControl = selector => page.evaluate(({ index, selector }) => {
    const group = document.querySelectorAll(
      '.universe-map-generated .universe-map-group'
    )[index];
    const control = group.querySelector(selector);
    const controlStyle = getComputedStyle(control);
    const groupStyle = getComputedStyle(group);
    const rgba = value => {
      const match = value?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return values.length >= 3
        ? [values[0], values[1], values[2], values[3] ?? 1]
        : null;
    };
    const luminance = value => {
      const channels = rgba(value);
      if (!channels) return null;
      const linear = channels.slice(0, 3).map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.04045 ? normalized / 12.92 :
          ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    };
    const contrast = (foreground, background) => {
      const foregroundChannels = rgba(foreground);
      const backgroundChannels = rgba(background);
      if (!foregroundChannels || !backgroundChannels || backgroundChannels[3] < 0.99) {
        return null;
      }
      const renderedForeground = foregroundChannels[3] >= 0.99
        ? foreground
        : `rgb(${foregroundChannels.slice(0, 3).map((channel, channelIndex) =>
            Math.round(channel * foregroundChannels[3] +
              backgroundChannels[channelIndex] * (1 - foregroundChannels[3]))
          ).join(', ')})`;
      const first = luminance(renderedForeground);
      const second = luminance(background);
      return first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const probe = document.createElement('span');
    probe.style.color = 'Canvas';
    group.append(probe);
    const canvas = getComputedStyle(probe).color;
    probe.remove();
    const summarySelector = selector === ':scope > summary';
    return {
      selector: summarySelector
        ? '.universe-map-group > summary:focus-visible'
        : '.universe-map-group a[data-universe-node]:focus-visible',
      focused: control === document.activeElement,
      focus_visible: control.matches(':focus-visible'),
      visible: control.getClientRects().length > 0 &&
        controlStyle.visibility === 'visible' && controlStyle.display !== 'none',
      text_color: controlStyle.color,
      text_contrast: contrast(controlStyle.color, canvas),
      canvas,
      boundary: {
        selector: '.universe-map-group',
        color: groupStyle.borderTopColor,
        width: groupStyle.borderTopWidth,
        style: groupStyle.borderTopStyle,
        contrast: contrast(groupStyle.borderTopColor, canvas),
      },
      focus: {
        color: controlStyle.outlineColor,
        width: controlStyle.outlineWidth,
        style: controlStyle.outlineStyle,
        contrast: contrast(controlStyle.outlineColor, canvas),
      },
    };
  }, { index: groupIndex, selector });

  try {
    // Change the media feature while keyboard focus is already on each control.
    await page.emulateMedia({ forcedColors: 'none' });
    await settleMediaChange();
    await summary.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    const summaryBefore = await captureFocusedControl(':scope > summary');
    await page.emulateMedia({ forcedColors: 'active' });
    await settleMediaChange();
    const summaryAfter = await captureFocusedControl(':scope > summary');
    transitionEvidence.summary = { before: summaryBefore, after: summaryAfter };
    if (!summaryBefore.focused || !summaryBefore.focus_visible ||
        !summaryAfter.focused || !summaryAfter.focus_visible ||
        !summaryAfter.visible || summaryAfter.text_contrast === null ||
        summaryAfter.text_contrast < 4.5 ||
        Number.parseFloat(summaryAfter.boundary.width) < 1 ||
        summaryAfter.boundary.style === 'none' ||
        summaryAfter.boundary.contrast === null ||
        summaryAfter.boundary.contrast < 3 ||
        Number.parseFloat(summaryAfter.focus.width) < 2 ||
        summaryAfter.focus.style === 'none' ||
        summaryAfter.focus.contrast === null ||
        summaryAfter.focus.contrast < 3) {
      errors.push(
        `UNIVERSE PAGE MAP FORCED-COLORS SUMMARY TRANSITION FAILURE: ` +
        `element=${summaryAfter.selector}; computed_colors=${JSON.stringify({
          before: summaryBefore,
          after: summaryAfter,
        })}`
      );
    }

    await page.emulateMedia({ forcedColors: 'none' });
    await settleMediaChange();
    await summary.focus();
    let linkFocusedBefore = false;
    for (let tab = 0; tab < 100 && !linkFocusedBefore; tab++) {
      await page.keyboard.press('Tab');
      linkFocusedBefore = await firstLink.evaluate(element =>
        element === document.activeElement && element.matches(':focus-visible')
      );
    }
    const linkBefore = await captureFocusedControl('a[data-universe-node]');
    await page.emulateMedia({ forcedColors: 'active' });
    await settleMediaChange();
    const linkAfter = await captureFocusedControl('a[data-universe-node]');
    transitionEvidence.fallback_link = { before: linkBefore, after: linkAfter };
    if (!linkBefore.focused || !linkBefore.focus_visible ||
        !linkAfter.focused || !linkAfter.focus_visible ||
        !linkAfter.visible || linkAfter.text_contrast === null ||
        linkAfter.text_contrast < 4.5 ||
        Number.parseFloat(linkAfter.boundary.width) < 1 ||
        linkAfter.boundary.style === 'none' ||
        linkAfter.boundary.contrast === null ||
        linkAfter.boundary.contrast < 3 ||
        Number.parseFloat(linkAfter.focus.width) < 2 ||
        linkAfter.focus.style === 'none' ||
        linkAfter.focus.contrast === null ||
        linkAfter.focus.contrast < 3) {
      errors.push(
        `UNIVERSE PAGE MAP FORCED-COLORS FALLBACK-LINK TRANSITION FAILURE: ` +
        `element=${linkAfter.selector}; computed_colors=${JSON.stringify({
          before: linkBefore,
          after: linkAfter,
        })}`
      );
    }

    await summary.focus();
    let linkFocused = false;
    for (let tab = 0; tab < 100 && !linkFocused; tab++) {
      await page.keyboard.press('Tab');
      linkFocused = await firstLink.evaluate(element =>
        element === document.activeElement && element.matches(':focus-visible')
      );
    }
    const evidence = await page.evaluate(index => {
      const group = document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )[index];
      const channels = value => {
        const match = value?.match(/^rgba?\((.+)\)$/);
        if (!match) return null;
        const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
        return values.length >= 3
          ? [values[0], values[1], values[2], values[3] ?? 1]
          : null;
      };
      const luminance = value => {
        const rgba = channels(value);
        if (!rgba) return null;
        const linear = rgba.slice(0, 3).map(channel => {
          const normalized = channel / 255;
          return normalized <= 0.04045 ? normalized / 12.92 :
            ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      const contrast = (foreground, background) => {
        const foregroundChannels = channels(foreground);
        const backgroundChannels = channels(background);
        if (!foregroundChannels || !backgroundChannels ||
            backgroundChannels[3] < 0.99) return null;
        const renderedForeground = foregroundChannels[3] >= 0.99
          ? foreground
          : `rgb(${foregroundChannels.slice(0, 3).map((channel, index) =>
              Math.round(channel * foregroundChannels[3] +
                backgroundChannels[index] * (1 - foregroundChannels[3]))
            ).join(', ')})`;
        const first = luminance(renderedForeground);
        const second = luminance(background);
        return first === null || second === null ? null :
          (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
      };
      const systemColor = name => {
        const probe = document.createElement('span');
        probe.style.color = name;
        group.append(probe);
        const value = getComputedStyle(probe).color;
        probe.remove();
        return value;
      };
      const canvas = systemColor('Canvas');
      const summary = group.querySelector(':scope > summary');
      const summaryStyle = getComputedStyle(summary);
      const summaryBox = summary.getBoundingClientRect();
      const groupStyle = getComputedStyle(group);
      return {
        canvas,
        summary: {
          selector: '.universe-map-group > summary',
          visible: summaryBox.width > 0 && summaryBox.height > 0 &&
            summaryStyle.visibility === 'visible' &&
            summaryStyle.display !== 'none',
          color: summaryStyle.color,
          background: canvas,
          contrast: contrast(summaryStyle.color, canvas),
          focus: {
            color: summaryStyle.outlineColor,
            width: summaryStyle.outlineWidth,
            style: summaryStyle.outlineStyle,
          },
        },
        boundary: {
          selector: '.universe-map-group',
          color: groupStyle.borderTopColor,
          width: groupStyle.borderTopWidth,
          style: groupStyle.borderTopStyle,
          background: canvas,
          contrast: contrast(groupStyle.borderTopColor, canvas),
        },
        links: [...group.querySelectorAll('a[data-universe-node]')].map(
          (link, linkIndex) => {
            const style = getComputedStyle(link);
            const box = link.getBoundingClientRect();
            return {
              selector: `.universe-map-group a[data-universe-node]:nth-of-type(${linkIndex + 1})`,
              text: link.textContent.trim(),
              visible: box.width > 0 && box.height > 0 &&
                style.visibility === 'visible' && style.display !== 'none',
              color: style.color,
              background: canvas,
              contrast: contrast(style.color, canvas),
              underline: style.textDecorationLine.includes('underline'),
              focused: link === document.activeElement &&
                link.matches(':focus-visible'),
              focus: {
                color: style.outlineColor,
                width: style.outlineWidth,
                style: style.outlineStyle,
                contrast: contrast(style.outlineColor, canvas),
              },
            };
          }
        ),
      };
    }, groupIndex);

    if (!evidence.summary.visible || evidence.summary.contrast === null ||
        evidence.summary.contrast < 4.5) {
      errors.push(
        `UNIVERSE PAGE MAP CONTROL READABILITY FAILURE: theme=forced-colors ` +
        `element=${evidence.summary.selector} text contrast=${evidence.summary.contrast}; ` +
        `${JSON.stringify(evidence.summary)}`
      );
    }
    if (Number.parseFloat(evidence.boundary.width) < 1 ||
        evidence.boundary.style === 'none' ||
        evidence.boundary.contrast === null || evidence.boundary.contrast < 3) {
      errors.push(
        `UNIVERSE PAGE MAP CONTROL BOUNDARY FAILURE: theme=forced-colors ` +
        `element=${evidence.boundary.selector} contrast=${evidence.boundary.contrast}; ` +
        `${JSON.stringify(evidence.boundary)}`
      );
    }
    if (evidence.links.length === 0) {
      errors.push(
        `UNIVERSE PAGE MAP FALLBACK LINKS UNAVAILABLE: theme=forced-colors ` +
        `group=${groupIndex + 1} no accessible links were found`
      );
    }
    for (const link of evidence.links) {
      if (!link.visible || link.contrast === null || link.contrast < 4.5 ||
          !link.underline) {
        errors.push(
          `UNIVERSE PAGE MAP FALLBACK LINK READABILITY FAILURE: theme=forced-colors ` +
          `element=${link.selector} text contrast=${link.contrast}; ${JSON.stringify(link)}`
        );
      }
    }
    const focusedLink = evidence.links[0];
    if (!linkFocused || !focusedLink?.focused ||
        focusedLink.focus.style === 'none' ||
        Number.parseFloat(focusedLink.focus.width) < 2 ||
        focusedLink.focus.contrast === null || focusedLink.focus.contrast < 3) {
      errors.push(
        `UNIVERSE PAGE MAP FALLBACK LINK FOCUS FAILURE: theme=forced-colors ` +
        `element=a[data-universe-node]:focus-visible; ` +
        `${JSON.stringify(focusedLink?.focus ?? focusedLink)}`
      );
    }

    let summaryReached = false;
    for (let tab = 0; tab < 100 && !summaryReached; tab++) {
      await page.keyboard.press('Shift+Tab');
      summaryReached = await summary.evaluate(element =>
        element === document.activeElement && element.matches(':focus-visible')
      );
    }
    const summaryFocus = await summary.evaluate(element => {
      const style = getComputedStyle(element);
      const probe = document.createElement('span');
      probe.style.color = 'Canvas';
      element.parentElement.append(probe);
      const canvas = getComputedStyle(probe).color;
      probe.remove();
      const luminance = value => {
        const match = value.match(/^rgba?\((.+)\)$/);
        if (!match) return null;
        const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
        if (values.length < 3) return null;
        const linear = values.slice(0, 3).map(channel => {
          const normalized = channel / 255;
          return normalized <= 0.04045 ? normalized / 12.92 :
            ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      const rgba = value => {
        const match = value.match(/^rgba?\((.+)\)$/);
        return match
          ? match[1].split(/[,/ ]+/).filter(Boolean).map(Number)
          : null;
      };
      const outline = rgba(style.outlineColor);
      const canvasChannels = rgba(canvas);
      const renderedOutline = outline && canvasChannels
        ? `rgb(${outline.slice(0, 3).map((channel, index) =>
            Math.round(channel * (outline[3] ?? 1) +
              canvasChannels[index] * (1 - (outline[3] ?? 1)))
          ).join(', ')})`
        : '';
      const first = luminance(renderedOutline);
      const second = luminance(canvas);
      return {
        selector: '.universe-map-group > summary:focus-visible',
        focused: element === document.activeElement &&
          element.matches(':focus-visible'),
        color: style.outlineColor,
        width: style.outlineWidth,
        style: style.outlineStyle,
        contrast: first === null || second === null ? null :
          (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
      };
    });
    if (!summaryReached || !summaryFocus.focused || summaryFocus.style === 'none' ||
        Number.parseFloat(summaryFocus.width) < 2 ||
        summaryFocus.contrast === null || summaryFocus.contrast < 3) {
      errors.push(
        `UNIVERSE PAGE MAP SUMMARY FOCUS FAILURE: theme=forced-colors ` +
        `element=${summaryFocus.selector} contrast=${summaryFocus.contrast}; ` +
        `${JSON.stringify(summaryFocus)}`
      );
    }
    return {
      errors,
      evidence: {
        ...evidence,
        summary_focus: summaryFocus,
        focus_transitions: transitionEvidence,
      },
    };
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP CONTROL READABILITY CHECK ERROR: theme=forced-colors ` +
      `group=${groupIndex + 1} ${error.message.split('\n')[0]}`
    );
    return { errors, evidence: null };
  }
}

async function checkUniversePageMapThemeSwitch(page, groupCheck, renderedState, waitForTwoFrames) {
  const { groupIndex, groupCount, before } = groupCheck;
  const errors = [];
  const transitions = [];
  const target = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(groupIndex);
  const toggle = page.locator('.askjamie-main .glee-color-toggle');
  const baseline = renderedState;
  const label = before?.title ? `"${before.title}"` : `group ${groupIndex + 1}`;
  const originalSvgPreserved = async () => page.evaluate(index =>
    window.__responsiveQaThemeSwitchSvg === document.querySelectorAll(
      '.universe-map-generated .universe-map-group'
    )[index]?.querySelector('.mermaid svg'),
  groupIndex);
  const geometrySelectors = [
    'group',
    'summary',
    'figure',
    'reserved_shell',
    'previous_group_summary',
    'next_group_summary',
  ];

  const compareState = (state, expectedTheme) => {
    if (state?.color_scheme !== expectedTheme ||
        state?.prefers_dark !== true ||
        !state?.open ||
        !state?.other_groups_collapsed ||
        !state?.svg_ready) {
      errors.push(
        `UNIVERSE PAGE MAP THEME SWITCH STATE INVALID: expected ${label} to remain open with ` +
        `a ready SVG, all other generated groups collapsed, and ${expectedTheme} color scheme; ` +
        `state=${JSON.stringify({
          color_scheme: state?.color_scheme,
          prefers_dark: state?.prefers_dark,
          open: state?.open,
          other_groups_collapsed: state?.other_groups_collapsed,
          svg_ready: state?.svg_ready,
        })}`
      );
    }

    for (const selector of geometrySelectors) {
      const baselineRect = baseline?.geometry?.[selector];
      const stateRect = state?.geometry?.[selector];
      if (!baselineRect || !stateRect) {
        if ((selector === 'previous_group_summary' && groupIndex === 0) ||
            (selector === 'next_group_summary' && groupIndex === groupCount - 1)) {
          continue;
        }
        errors.push(
          `UNIVERSE PAGE MAP THEME SWITCH GEOMETRY MISSING: group ${groupIndex + 1} ` +
          `${selector}; baseline=${JSON.stringify(baselineRect)}; state=${JSON.stringify(stateRect)}`
        );
        continue;
      }

      const changedProperties = ['x', 'y', 'width', 'height'].flatMap(property => {
        const delta = Math.round((stateRect[property] - baselineRect[property]) * 100) / 100;
        return Math.abs(delta) > UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX
          ? [`${property}=${delta}px`]
          : [];
      });
      if (changedProperties.length > 0) {
        errors.push(
          `UNIVERSE PAGE MAP THEME SWITCH GEOMETRY SHIFT: group ${groupIndex + 1} ${label} ` +
          `${selector} changed ${changedProperties.join(', ')} with ` +
          `${UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
          `baseline=${JSON.stringify(baselineRect)}; state=${JSON.stringify(stateRect)}`
        );
      }
    }
  };

  if (!baseline?.svg_ready || !baseline?.open || !baseline?.other_groups_collapsed) {
    errors.push(
      `UNIVERSE PAGE MAP THEME SWITCH BASELINE INVALID: expected ready open SVG for ${label}; ` +
      `baseline=${JSON.stringify(baseline)}`
    );
  }
  const originalSvgCaptured = await target.locator('.mermaid svg').evaluate(svg => {
    window.__responsiveQaThemeSwitchSvg = svg;
    return true;
  }).catch(() => false);
  if (!originalSvgCaptured) {
    errors.push(
      `UNIVERSE PAGE MAP THEME SWITCH SVG MISSING: could not retain the rendered SVG for ${label}`
    );
  }
  const baselineReadability = await captureUniversePageMapReadability(
    page,
    groupIndex,
    baseline?.color_scheme ?? 'dark'
  );
  errors.push(...baselineReadability.errors);
  let forcedColorsReadability = null;
  try {
    await page.emulateMedia({ forcedColors: 'active' });
    await waitForTwoFrames();
    const forcedColorsActive = await page.evaluate(() =>
      matchMedia('(forced-colors: active)').matches
    );
    if (!forcedColorsActive) {
      errors.push(
        'UNIVERSE PAGE MAP FORCED-COLORS MODE UNAVAILABLE: browser did not activate ' +
        'the forced-colors media feature'
      );
    } else {
      await target.locator('.mermaid svg').evaluate(svg => {
        const namespace = 'http://www.w3.org/2000/svg';
        const label = document.createElementNS(namespace, 'g');
        label.classList.add('edgeLabel');
        label.dataset.responsiveQaForcedColorsFixture = 'true';
        const surface = document.createElementNS(namespace, 'rect');
        surface.setAttribute('width', '220');
        surface.setAttribute('height', '28');
        surface.style.fill = 'var(--color-surface)';
        const text = document.createElementNS(namespace, 'text');
        text.textContent = 'Forced-colors edge-label fixture';
        text.setAttribute('x', '8');
        text.setAttribute('y', '20');
        text.style.fill = 'var(--color-fg)';
        label.append(surface, text);
        svg.append(label);
      });
      forcedColorsReadability = await captureUniversePageMapReadability(
        page,
        groupIndex,
        'forced-colors'
      );
      errors.push(...forcedColorsReadability.errors);
      if (!forcedColorsReadability.evidence?.edge_labels?.some(sample =>
        sample.text === 'Forced-colors edge-label fixture'
      )) {
        errors.push(
          `UNIVERSE PAGE MAP FORCED-COLORS READABILITY INVALID: group ${groupIndex + 1} ` +
          'did not measure the representative SVG edge-label fixture'
        );
      }
      const controls = await captureUniversePageMapControlReadability(
        page,
        groupIndex
      );
      errors.push(...controls.errors);
      forcedColorsReadability.control_evidence = controls.evidence;
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP FORCED-COLORS CHECK ERROR: group ${groupIndex + 1} ` +
      `${error.message.split('\n')[0]}`
    );
  } finally {
    await page.emulateMedia({ forcedColors: 'none' }).catch(() => {});
    await waitForTwoFrames().catch(() => {});
  }

  const checkNarrowLightModePan = async () => {
    const evidence = {
      color_scheme: null,
      open: false,
      other_groups_collapsed: false,
      rendered_svg_ready: false,
      touch_points: null,
      max_scroll_left: null,
      scroll_left_before_swipe: null,
      scroll_left_after_pan: null,
      touch_close_reopen: null,
      vertical_page_scroll: null,
      reverse_vertical_page_scroll: null,
      landscape_vertical_page_scroll: null,
      ordinary_link: null,
      svg_preserved: false,
    };
    const scrollWrap = target.locator('.mermaid-scroll-wrap');
    const link = target.locator('.link-list a').first();
    try {
      const dimensions = await scrollWrap.evaluate(element => ({
        client_width: element.clientWidth,
        scroll_width: element.scrollWidth,
        max_scroll_left: Math.max(0, element.scrollWidth - element.clientWidth),
      }));
      evidence.max_scroll_left = dimensions.max_scroll_left;
      if (dimensions.max_scroll_left <= 0) {
        errors.push(
          `UNIVERSE PAGE MAP LIGHT-MODE PAN FAILED: ${label} has no horizontal overflow at 320px; ` +
          `dimensions=${JSON.stringify(dimensions)}`
        );
      } else {
        await scrollWrap.evaluate(element => { element.scrollLeft = 0; });
        evidence.touch_points = await page.evaluate(() => navigator.maxTouchPoints);
        if (evidence.touch_points < 1) {
          errors.push(
            `UNIVERSE PAGE MAP LIGHT-MODE SWIPE FAILED: ${label} viewport does not expose touch input; ` +
            `navigator.maxTouchPoints=${evidence.touch_points}`
          );
        }
        evidence.scroll_left_before_swipe = await scrollWrap.evaluate(element => element.scrollLeft);
        await scrollWrap.scrollIntoViewIfNeeded();
        const box = await scrollWrap.boundingBox();
        if (!box) throw new Error('diagram scroll viewport has no visible bounds');
        const session = await page.context().newCDPSession(page);
        const y = box.y + box.height / 2;
        const startX = box.x + box.width * 0.8;
        const endX = box.x + box.width * 0.2;
        try {
          await session.send('Input.dispatchTouchEvent', {
            type: 'touchStart',
            touchPoints: [{ id: 1, x: startX, y }],
          });
          for (const progress of [0.25, 0.5, 0.75, 1]) {
            await new Promise(resolve => setTimeout(resolve, 30));
            await session.send('Input.dispatchTouchEvent', {
              type: 'touchMove',
              touchPoints: [{
                id: 1,
                x: startX + (endX - startX) * progress,
                y,
              }],
            });
          }
          await session.send('Input.dispatchTouchEvent', {
            type: 'touchEnd',
            touchPoints: [],
          });
        } finally {
          await session.detach();
        }
        await page.waitForFunction(
          element => element.scrollLeft > 0,
          await scrollWrap.elementHandle(),
          { timeout: 3000 }
        );
        evidence.scroll_left_after_pan = await scrollWrap.evaluate(element => element.scrollLeft);

        const touchRetention = {
          scroll_left_before_close: evidence.scroll_left_after_pan,
          scroll_left_after_reopen: null,
          closed_by_touch: false,
          all_groups_collapsed_after_close: false,
          reopened_by_touch: false,
          other_groups_collapsed_after_reopen: false,
          preserved: false,
        };
        evidence.touch_close_reopen = touchRetention;
        const summary = target.locator('summary');
        await summary.scrollIntoViewIfNeeded();
        let summaryBox = await summary.boundingBox();
        if (!summaryBox) throw new Error('selected group summary has no tappable bounds');
        await page.touchscreen.tap(
          summaryBox.x + summaryBox.width / 2,
          summaryBox.y + summaryBox.height / 2
        );
        touchRetention.closed_by_touch = !(await target.evaluate(group => group.open));
        touchRetention.all_groups_collapsed_after_close =
          await page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).evaluateAll(
            groups => groups.every(group => !group.open)
          );
        await summary.scrollIntoViewIfNeeded();
        summaryBox = await summary.boundingBox();
        if (!summaryBox) throw new Error('closed group summary has no tappable bounds');
        await page.touchscreen.tap(
          summaryBox.x + summaryBox.width / 2,
          summaryBox.y + summaryBox.height / 2
        );
        touchRetention.reopened_by_touch = await target.evaluate(group => group.open);
        await target.locator('.mermaid svg .node').first().waitFor({ timeout: 20000 });
        await waitForTwoFrames();
        const stateAfterTouchReopen = await captureUniversePageMapGeometry(page, groupIndex);
        touchRetention.other_groups_collapsed_after_reopen =
          Boolean(stateAfterTouchReopen?.other_groups_collapsed);
        touchRetention.scroll_left_after_reopen =
          await scrollWrap.evaluate(element => element.scrollLeft);
        touchRetention.preserved =
          Math.abs(touchRetention.scroll_left_after_reopen -
            touchRetention.scroll_left_before_close) <= 1;
        if (!touchRetention.closed_by_touch ||
            !touchRetention.all_groups_collapsed_after_close ||
            !touchRetention.reopened_by_touch ||
            !touchRetention.other_groups_collapsed_after_reopen ||
            !touchRetention.preserved) {
          errors.push(
            `UNIVERSE PAGE MAP TOUCH CLOSE/REOPEN FAILED: ${label} must close and reopen by ` +
            `touch with only the selected group open and preserve its pan; ` +
            `evidence=${JSON.stringify(touchRetention)}`
          );
        }

        try {
          await scrollWrap.scrollIntoViewIfNeeded();
          const diagram = target.locator('.mermaid svg');
          const diagramBox = await diagram.boundingBox();
          if (!diagramBox) throw new Error('rendered diagram has no visible bounds');
          const visible = await page.evaluate(box => {
            const top = Math.max(0, box.y);
            const bottom = Math.min(window.innerHeight, box.y + box.height);
            return {
              top,
              height: bottom - top,
              viewport_height: window.innerHeight,
            };
          }, diagramBox);
          if (visible.height < 80) {
            throw new Error(
              `rendered diagram has no usable vertical swipe area; visible=${JSON.stringify(visible)}`
            );
          }
          const startX = diagramBox.x + diagramBox.width / 2;
          const startY = visible.top + visible.height * 0.75;
          const endY = visible.top + visible.height * 0.25;
          const scrollYBefore = await page.evaluate(() => window.scrollY);
          await page.evaluate(() => {
            window.__responsiveQaMapLinkActivationDuringVerticalSwipe = null;
            document.addEventListener('click', event => {
              const link = event.target instanceof Element
                ? event.target.closest('.mermaid svg a[href]')
                : null;
              if (!link) return;
              window.__responsiveQaMapLinkActivationDuringVerticalSwipe =
                link.getAttribute('href');
              event.preventDefault();
            }, true);
          });
          const verticalSession = await page.context().newCDPSession(page);
          try {
            await verticalSession.send('Input.dispatchTouchEvent', {
              type: 'touchStart',
              touchPoints: [{ id: 1, x: startX, y: startY }],
            });
            for (const progress of [0.25, 0.5, 0.75, 1]) {
              await new Promise(resolve => setTimeout(resolve, 30));
              await verticalSession.send('Input.dispatchTouchEvent', {
                type: 'touchMove',
                touchPoints: [{
                  id: 1,
                  x: startX,
                  y: startY + (endY - startY) * progress,
                }],
              });
            }
            await verticalSession.send('Input.dispatchTouchEvent', {
              type: 'touchEnd',
              touchPoints: [],
            });
          } finally {
            await verticalSession.detach();
          }
          await page.waitForFunction(
            before => window.scrollY > before,
            scrollYBefore,
            { timeout: 3000 }
          );
          const scrollYAfter = await page.evaluate(() => window.scrollY);
          const mapLinkActivation = await page.evaluate(
            () => window.__responsiveQaMapLinkActivationDuringVerticalSwipe
          );
          const verticalState = await captureUniversePageMapGeometry(page, groupIndex);
          evidence.vertical_page_scroll = {
            scroll_y_before: scrollYBefore,
            scroll_y_after: scrollYAfter,
            scrolled: scrollYAfter > scrollYBefore,
            map_link_activation: mapLinkActivation,
            selected_group_open: verticalState?.open ?? false,
            other_groups_collapsed: verticalState?.other_groups_collapsed ?? false,
          };
          if (scrollYAfter <= scrollYBefore) {
            errors.push(
              `UNIVERSE PAGE MAP VERTICAL SWIPE FAILED: ${label} page did not scroll when ` +
              `the gesture started over the rendered diagram; evidence=` +
              `${JSON.stringify(evidence.vertical_page_scroll)}`
            );
          }
          if (mapLinkActivation !== null) {
            errors.push(
              `UNIVERSE PAGE MAP VERTICAL SWIPE ACTIVATED A LINK: ${label}; ` +
              `evidence=${JSON.stringify(evidence.vertical_page_scroll)}`
            );
          }
          if (!verticalState?.open || !verticalState?.other_groups_collapsed) {
            errors.push(
              `UNIVERSE PAGE MAP VERTICAL SWIPE CHANGED GROUPS: ${label}; ` +
              `evidence=${JSON.stringify(evidence.vertical_page_scroll)}`
            );
          }

          const reverseDiagramBox = await diagram.boundingBox();
          if (!reverseDiagramBox) throw new Error('rendered diagram has no visible bounds for reverse swipe');
          const reverseVisible = await page.evaluate(box => {
            const top = Math.max(0, box.y);
            const bottom = Math.min(window.innerHeight, box.y + box.height);
            return { top, height: bottom - top };
          }, reverseDiagramBox);
          if (reverseVisible.height < 80) {
            throw new Error(
              `rendered diagram has no usable reverse vertical swipe area; ` +
              `visible=${JSON.stringify(reverseVisible)}`
            );
          }
          const reverseStartX = reverseDiagramBox.x + reverseDiagramBox.width / 2;
          const reverseStartY = reverseVisible.top + reverseVisible.height * 0.25;
          const reverseEndY = reverseVisible.top + reverseVisible.height * 0.75;
          const reverseScrollYBefore = await page.evaluate(() => window.scrollY);
          if (reverseScrollYBefore <= 0) {
            throw new Error(
              `reverse vertical swipe must start from a nonzero page scroll position; ` +
              `scrollY=${reverseScrollYBefore}`
            );
          }
          await page.evaluate(() => {
            window.__responsiveQaMapLinkActivationDuringVerticalSwipe = null;
          });
          const reverseSession = await page.context().newCDPSession(page);
          try {
            await reverseSession.send('Input.dispatchTouchEvent', {
              type: 'touchStart',
              touchPoints: [{ id: 1, x: reverseStartX, y: reverseStartY }],
            });
            for (const progress of [0.25, 0.5, 0.75, 1]) {
              await new Promise(resolve => setTimeout(resolve, 30));
              await reverseSession.send('Input.dispatchTouchEvent', {
                type: 'touchMove',
                touchPoints: [{
                  id: 1,
                  x: reverseStartX,
                  y: reverseStartY + (reverseEndY - reverseStartY) * progress,
                }],
              });
            }
            await reverseSession.send('Input.dispatchTouchEvent', {
              type: 'touchEnd',
              touchPoints: [],
            });
          } finally {
            await reverseSession.detach();
          }
          await page.waitForFunction(
            before => window.scrollY < before,
            reverseScrollYBefore,
            { timeout: 3000 }
          );
          const reverseScrollYAfter = await page.evaluate(() => window.scrollY);
          const reverseLinkActivation = await page.evaluate(
            () => window.__responsiveQaMapLinkActivationDuringVerticalSwipe
          );
          const reverseState = await captureUniversePageMapGeometry(page, groupIndex);
          evidence.reverse_vertical_page_scroll = {
            scroll_y_before: reverseScrollYBefore,
            scroll_y_after: reverseScrollYAfter,
            scrolled: reverseScrollYAfter < reverseScrollYBefore,
            map_link_activation: reverseLinkActivation,
            selected_group_open: reverseState?.open ?? false,
            other_groups_collapsed: reverseState?.other_groups_collapsed ?? false,
          };
          if (reverseScrollYAfter >= reverseScrollYBefore) {
            errors.push(
              `UNIVERSE PAGE MAP REVERSE VERTICAL SWIPE FAILED: ${label} page did not scroll up; ` +
              `evidence=${JSON.stringify(evidence.reverse_vertical_page_scroll)}`
            );
          }
          if (reverseLinkActivation !== null) {
            errors.push(
              `UNIVERSE PAGE MAP REVERSE VERTICAL SWIPE ACTIVATED A LINK: ${label}; ` +
              `evidence=${JSON.stringify(evidence.reverse_vertical_page_scroll)}`
            );
          }
          if (!reverseState?.open || !reverseState?.other_groups_collapsed) {
            errors.push(
              `UNIVERSE PAGE MAP REVERSE VERTICAL SWIPE CHANGED GROUPS: ${label}; ` +
              `evidence=${JSON.stringify(evidence.reverse_vertical_page_scroll)}`
            );
          }
        } catch (error) {
          errors.push(
            `UNIVERSE PAGE MAP VERTICAL SWIPE FAILED: ${label} could not confirm both page-scroll ` +
            `directions without map-link activation or group changes; ${error.message.split('\n')[0]}`
          );
        }

        const originalViewport = page.viewportSize();
        try {
          await page.setViewportSize({ width: 320, height: 400 });
          await waitForTwoFrames();
          await scrollWrap.scrollIntoViewIfNeeded();
          const landscapeDiagram = target.locator('.mermaid svg');
          const landscapeDiagramBox = await landscapeDiagram.boundingBox();
          if (!landscapeDiagramBox) throw new Error('short landscape diagram has no visible bounds');
          const landscapeVisible = await page.evaluate(box => {
            const top = Math.max(0, box.y);
            const bottom = Math.min(window.innerHeight, box.y + box.height);
            return { top, height: bottom - top, viewport_height: window.innerHeight };
          }, landscapeDiagramBox);
          if (landscapeVisible.height < 80) {
            throw new Error(
              `short landscape diagram has no usable vertical swipe area; ` +
              `visible=${JSON.stringify(landscapeVisible)}`
            );
          }
          const landscapeStartX = landscapeDiagramBox.x + landscapeDiagramBox.width / 2;
          const landscapeStartY = landscapeVisible.top + landscapeVisible.height * 0.75;
          const landscapeEndY = landscapeVisible.top + landscapeVisible.height * 0.25;
          const landscapeScrollYBefore = await page.evaluate(() => window.scrollY);
          await page.evaluate(() => {
            window.__responsiveQaMapLinkActivationDuringVerticalSwipe = null;
          });
          const landscapeSession = await page.context().newCDPSession(page);
          try {
            await landscapeSession.send('Input.dispatchTouchEvent', {
              type: 'touchStart',
              touchPoints: [{ id: 1, x: landscapeStartX, y: landscapeStartY }],
            });
            for (const progress of [0.25, 0.5, 0.75, 1]) {
              await new Promise(resolve => setTimeout(resolve, 30));
              await landscapeSession.send('Input.dispatchTouchEvent', {
                type: 'touchMove',
                touchPoints: [{
                  id: 1,
                  x: landscapeStartX,
                  y: landscapeStartY + (landscapeEndY - landscapeStartY) * progress,
                }],
              });
            }
            await landscapeSession.send('Input.dispatchTouchEvent', {
              type: 'touchEnd',
              touchPoints: [],
            });
          } finally {
            await landscapeSession.detach();
          }
          await page.waitForFunction(
            before => window.scrollY > before,
            landscapeScrollYBefore,
            { timeout: 3000 }
          );
          const landscapeScrollYAfter = await page.evaluate(() => window.scrollY);
          const landscapeLinkActivation = await page.evaluate(
            () => window.__responsiveQaMapLinkActivationDuringVerticalSwipe
          );
          const landscapeState = await captureUniversePageMapGeometry(page, groupIndex);
          evidence.landscape_vertical_page_scroll = {
            viewport: { width: 320, height: 400 },
            scroll_y_before: landscapeScrollYBefore,
            scroll_y_after: landscapeScrollYAfter,
            scrolled: landscapeScrollYAfter > landscapeScrollYBefore,
            map_link_activation: landscapeLinkActivation,
            selected_group_open: landscapeState?.open ?? false,
            other_groups_collapsed: landscapeState?.other_groups_collapsed ?? false,
          };
          if (landscapeScrollYAfter <= landscapeScrollYBefore) {
            errors.push(
              `UNIVERSE PAGE MAP LANDSCAPE VERTICAL SWIPE FAILED: ${label} page did not scroll ` +
              `at 320x400; evidence=${JSON.stringify(evidence.landscape_vertical_page_scroll)}`
            );
          }
          if (landscapeLinkActivation !== null) {
            errors.push(
              `UNIVERSE PAGE MAP LANDSCAPE VERTICAL SWIPE ACTIVATED A LINK: ${label}; ` +
              `evidence=${JSON.stringify(evidence.landscape_vertical_page_scroll)}`
            );
          }
          if (!landscapeState?.open || !landscapeState?.other_groups_collapsed) {
            errors.push(
              `UNIVERSE PAGE MAP LANDSCAPE VERTICAL SWIPE CHANGED GROUPS: ${label}; ` +
              `evidence=${JSON.stringify(evidence.landscape_vertical_page_scroll)}`
            );
          }
        } catch (error) {
          errors.push(
            `UNIVERSE PAGE MAP LANDSCAPE VERTICAL SWIPE FAILED: ${label} could not confirm ` +
            `page scrolling at 320x400 without map-link activation or group changes; ` +
            `${error.message.split('\n')[0]}`
          );
        } finally {
          if (originalViewport) await page.setViewportSize(originalViewport).catch(() => {});
          await waitForTwoFrames().catch(() => {});
        }
      }
    } catch (error) {
      errors.push(
        `UNIVERSE PAGE MAP LIGHT-MODE PAN FAILED: ${label} did not respond to horizontal input; ` +
        `${error.message.split('\n')[0]}`
      );
    }

    const state = await captureUniversePageMapGeometry(page, groupIndex).catch(() => null);
    evidence.color_scheme = state?.color_scheme ?? null;
    evidence.open = Boolean(state?.open);
    evidence.other_groups_collapsed = Boolean(state?.other_groups_collapsed);
    evidence.rendered_svg_ready = Boolean(state?.svg_ready);
    if (state?.color_scheme !== 'light' || !state?.open ||
        !state?.other_groups_collapsed || !state?.svg_ready) {
      errors.push(
        `UNIVERSE PAGE MAP LIGHT-MODE PAN STATE INVALID: ${label} must remain open with a ready ` +
        `SVG and all other groups collapsed; state=${JSON.stringify(state)}`
      );
    }

    try {
      const linkSetup = await link.evaluate(element => {
        window.__responsiveQaLightModePanLinkActivation = null;
        const href = element.getAttribute('href');
        element.addEventListener('click', event => {
          event.preventDefault();
          window.__responsiveQaLightModePanLinkActivation = {
            trusted: event.isTrusted,
            href: element.getAttribute('href'),
          };
        }, { once: true });
        return {
          href,
          visible: element.getClientRects().length > 0,
        };
      });
      await link.focus();
      const focused = await link.evaluate(element => element === document.activeElement);
      await link.click();
      const activation = await page.evaluate(
        () => window.__responsiveQaLightModePanLinkActivation ?? null
      );
      evidence.ordinary_link = {
        ...linkSetup,
        focused,
        activated: activation?.trusted === true && activation?.href === linkSetup.href,
        activation,
      };
      if (!linkSetup.visible || !focused || !evidence.ordinary_link.activated) {
        errors.push(
          `UNIVERSE PAGE MAP LIGHT-MODE PAN LINK FAILED: ${label} ordinary page link was not ` +
          `usable after panning; evidence=${JSON.stringify(evidence.ordinary_link)}`
        );
      }
    } catch (error) {
      errors.push(
        `UNIVERSE PAGE MAP LIGHT-MODE PAN LINK FAILED: ${label} ordinary page link could not ` +
        `be tested after panning; ${error.message.split('\n')[0]}`
      );
    }

    evidence.svg_preserved = await originalSvgPreserved();
    if (!evidence.svg_preserved) {
      errors.push(
        `UNIVERSE PAGE MAP LIGHT-MODE PAN RERENDERED SVG: ${label} did not retain its original SVG`
      );
    }
    return evidence;
  };

  let retainedPanScrollLeft = null;
  let narrowPanCompleted = false;
  const scrollWrap = target.locator('.mermaid-scroll-wrap');
  const initialToggleState = await toggle.getAttribute('data-state').catch(() => null);
  if (initialToggleState !== 'dark') {
    errors.push(
      `UNIVERSE PAGE MAP THEME SWITCH CONTROL INVALID: expected the color-scheme control to start ` +
      `pinned dark; state=${JSON.stringify(initialToggleState)}`
    );
  }

  if (initialToggleState === 'dark') {
    for (const transition of [
      { theme: 'light', clicks: 2 },
      { theme: 'dark', clicks: 1 },
      { theme: 'light', clicks: 2 },
    ]) {
      try {
        const scrollLeftBeforeThemeChange = retainedPanScrollLeft === null
          ? null
          : await scrollWrap.evaluate(element => element.scrollLeft);
        for (let clickIndex = 0; clickIndex < transition.clicks; clickIndex += 1) {
          await toggle.click();
        }
        await page.waitForFunction(
          theme => document.documentElement.getAttribute('data-color-scheme') === theme,
          transition.theme,
          { timeout: 5000 }
        );
        await waitForTwoFrames();
        const state = await captureUniversePageMapGeometry(page, groupIndex);
        compareState(state, transition.theme);
        const readability = await captureUniversePageMapReadability(
          page,
          groupIndex,
          transition.theme
        );
        errors.push(...readability.errors);
        const narrowPan = transition.theme === 'light' &&
          page.viewportSize()?.width === 320 &&
          !narrowPanCompleted
          ? await checkNarrowLightModePan()
          : null;
        if (narrowPan) {
          narrowPanCompleted = true;
          retainedPanScrollLeft = narrowPan.scroll_left_after_pan;
        }
        let horizontalScrollRetention = null;
        if (scrollLeftBeforeThemeChange !== null) {
          const scrollLeftAfterThemeChange = await scrollWrap.evaluate(
            element => element.scrollLeft
          );
          const preserved = Math.abs(
            scrollLeftAfterThemeChange - retainedPanScrollLeft
          ) <= 1;
          horizontalScrollRetention = {
            before_theme_change: scrollLeftBeforeThemeChange,
            expected_after_theme_change: retainedPanScrollLeft,
            after_theme_change: scrollLeftAfterThemeChange,
            preserved,
          };
          if (!preserved) {
            errors.push(
              `UNIVERSE PAGE MAP THEME SWITCH LOST HORIZONTAL POSITION: ${label} ` +
              `expected scrollLeft=${retainedPanScrollLeft}, got ` +
              `${scrollLeftAfterThemeChange} after switching to ${transition.theme}`
            );
          }
        }
        const svg_preserved = await originalSvgPreserved();
        if (!svg_preserved) {
          errors.push(
            `UNIVERSE PAGE MAP THEME SWITCH RERENDERED SVG: theme=${transition.theme} ` +
            `group ${groupIndex + 1} ${label} did not retain its original SVG element`
          );
        }
        transitions.push({
          theme: transition.theme,
          state,
          readability: readability.evidence,
          svg_preserved,
          ...(horizontalScrollRetention
            ? { horizontal_scroll_retention: horizontalScrollRetention }
            : {}),
          ...(narrowPan ? { narrow_light_mode_pan: narrowPan } : {}),
        });
      } catch (error) {
        errors.push(
          `UNIVERSE PAGE MAP THEME SWITCH FAILED: group ${groupIndex + 1} ${label} ` +
          `could not reach ${transition.theme}; ${error.message.split('\n')[0]}`
        );
      }
    }
  }
  const lightTransition = transitions.find(transition => transition.theme === 'light');
  const darkReturn = transitions.find((transition, index) =>
    transition.theme === 'dark' && index > 0
  );
  const baselineFills = baselineReadability.evidence?.node_fills?.map(sample => sample.fill) ?? [];
  const lightFills = lightTransition?.readability?.node_fills?.map(sample => sample.fill) ?? [];
  const returnedDarkFills = darkReturn?.readability?.node_fills?.map(sample => sample.fill) ?? [];
  if (baselineFills.length > 0 && lightFills.length > 0 &&
      JSON.stringify(baselineFills) === JSON.stringify(lightFills)) {
    errors.push(
      `UNIVERSE PAGE MAP READABILITY FAILURE: theme=light element=svg .node rect ` +
      `node fills did not adapt from dark to light`
    );
  }
  if (baselineFills.length > 0 && returnedDarkFills.length > 0 &&
      JSON.stringify(baselineFills) !== JSON.stringify(returnedDarkFills)) {
    errors.push(
      `UNIVERSE PAGE MAP READABILITY FAILURE: theme=dark element=svg .node rect ` +
      `node fills did not return to their baseline dark palette`
    );
  }

  let keyboardClosed = false;
  let keyboardReopened = false;
  let closeReopenPanRetention = null;
  let desktopZoomApplied = false;
  try {
    const viewportWidth = page.viewportSize()?.width;
    if (viewportWidth === 1280) {
      await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
      desktopZoomApplied = true;
      await waitForTwoFrames();
      const dimensions = await scrollWrap.evaluate(element => ({
        client_width: element.clientWidth,
        scroll_width: element.scrollWidth,
      }));
      if (dimensions.scroll_width <= dimensions.client_width) {
        throw new Error(
          `desktop page map has no horizontal overflow at 200% zoom; ` +
          `dimensions=${JSON.stringify(dimensions)}`
        );
      }
      await scrollWrap.evaluate(element => { element.scrollLeft = 0; });
      await scrollWrap.scrollIntoViewIfNeeded();
      await scrollWrap.hover();
      await page.mouse.wheel(200, 0);
      await page.waitForFunction(
        element => element.scrollLeft > 0,
        await scrollWrap.elementHandle(),
        { timeout: 3000 }
      );
    }
    const scrollLeftBeforeClose = await scrollWrap.evaluate(element => element.scrollLeft);
    if ((viewportWidth === 320 || viewportWidth === 1280) &&
        scrollLeftBeforeClose <= 0) {
      throw new Error(
        `expected a panned map at ${viewportWidth}px` +
        `${viewportWidth === 1280 ? ' with 200% zoom' : ''} before closing; ` +
        `scrollLeft=${scrollLeftBeforeClose}`
      );
    }
    const summary = target.locator('summary');
    await summary.focus();
    const summaryFocused = await summary.evaluate(element => element === document.activeElement);
    await page.keyboard.press('Enter');
    keyboardClosed = !(await target.evaluate(group => group.open));
    const closedGroups = await page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).evaluateAll(
      groups => groups.every(group => !group.open)
    );
    await page.keyboard.press('Enter');
    keyboardReopened = await target.evaluate(group => group.open);
    await waitForTwoFrames();
    const scrollLeftAfterReopen = await scrollWrap.evaluate(element => element.scrollLeft);
    closeReopenPanRetention = {
      viewport_width: viewportWidth,
      zoom: viewportWidth === 1280 ? 2 : 1,
      before_close: scrollLeftBeforeClose,
      after_reopen: scrollLeftAfterReopen,
      preserved: Math.abs(scrollLeftAfterReopen - scrollLeftBeforeClose) <= 1,
    };
    const openState = await captureUniversePageMapGeometry(page, groupIndex);
    if (!summaryFocused || !keyboardClosed || !closedGroups ||
        !keyboardReopened || !openState?.other_groups_collapsed || !openState?.svg_ready ||
        !closeReopenPanRetention.preserved) {
      errors.push(
        `UNIVERSE PAGE MAP CLOSE/REOPEN FAILED: expected ${label} to close and reopen ` +
        `with Enter while retaining its pan position; ${JSON.stringify({
          summary_focused: summaryFocused,
          closed_by_keyboard: keyboardClosed,
          all_groups_closed_after_toggle: closedGroups,
          reopened_by_keyboard: keyboardReopened,
          other_groups_collapsed: openState?.other_groups_collapsed,
          svg_ready: openState?.svg_ready,
          close_reopen_pan_retention: closeReopenPanRetention,
        })}`
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP CLOSE/REOPEN ERROR: group ${groupIndex + 1} ` +
      `${error.message.split('\n')[0]}`
    );
  } finally {
    if (desktopZoomApplied) {
      await page.evaluate(() => { document.documentElement.style.zoom = '1'; })
        .catch(() => {});
      await waitForTwoFrames().catch(() => {});
    }
  }

  let baselineRestoration = null;
  try {
    const stateBeforeRestore = await toggle.getAttribute('data-state');
    const clicksToDark = { auto: 2, light: 1, dark: 0 };
    if (!(stateBeforeRestore in clicksToDark)) {
      throw new Error(`unexpected color-scheme state ${JSON.stringify(stateBeforeRestore)}`);
    }
    const scrollLeftBeforeRestore = await scrollWrap.evaluate(element => element.scrollLeft);
    for (let clickIndex = 0; clickIndex < clicksToDark[stateBeforeRestore]; clickIndex += 1) {
      await toggle.click();
    }
    await waitForTwoFrames();
    const stateAfterRestore = await toggle.getAttribute('data-state');
    const scrollLeftAfterRestore = await scrollWrap.evaluate(element => element.scrollLeft);
    baselineRestoration = {
      state_before: stateBeforeRestore,
      state_after: stateAfterRestore,
      scroll_left_before: scrollLeftBeforeRestore,
      scroll_left_after: scrollLeftAfterRestore,
      restored_to_dark: stateAfterRestore === 'dark',
      pan_preserved: Math.abs(scrollLeftAfterRestore - scrollLeftBeforeRestore) <= 1,
    };
    if (!baselineRestoration.restored_to_dark || !baselineRestoration.pan_preserved) {
      errors.push(
        `UNIVERSE PAGE MAP THEME SWITCH BASELINE NOT RESTORED: group ${groupIndex + 1} ` +
        JSON.stringify(baselineRestoration)
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP THEME SWITCH BASELINE RESTORE FAILED: group ${groupIndex + 1} ` +
      `${error.message.split('\n')[0]}`
    );
  }

  return {
    errors,
    evidence: {
      initial_toggle_state: initialToggleState,
      baseline,
      baseline_readability: baselineReadability.evidence,
      forced_colors_readability: forcedColorsReadability?.evidence ?? null,
      transitions,
      keyboard_closed: keyboardClosed,
      keyboard_reopened: keyboardReopened,
      close_reopen_pan_retention: closeReopenPanRetention,
      baseline_restoration: baselineRestoration,
    },
  };
}

async function checkUniversePageMapNarrowUsability(
  page,
  groupIndex,
  checkLabel = '320PX',
  interactionLabel = 'TAP',
  checkTextClipping = false
) {
  const errors = [];
  const groupLabel = `group ${groupIndex + 1}`;
  const prefix = `UNIVERSE PAGE MAP ${checkLabel} ${groupLabel}`;
  let evidence = null;

  try {
    evidence = await page.evaluate(({ index, groupSelector, checkTextClipping }) => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      const group = groups[index];
      if (!group) return { group_exists: false };

      const visible = element => Boolean(
        element &&
        element.getClientRects().length > 0 &&
        getComputedStyle(element).display !== 'none' &&
        getComputedStyle(element).visibility !== 'hidden'
      );
      const rect = element => {
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return {
          left: Math.round(box.left * 100) / 100,
          right: Math.round(box.right * 100) / 100,
          width: Math.round(box.width * 100) / 100,
          height: Math.round(box.height * 100) / 100,
        };
      };
      const textClipping = element => {
        if (!element) return { clipped: true, text_rects: [], clipping_ancestors: [] };
        const range = document.createRange();
        range.selectNodeContents(element);
        const textRects = [...range.getClientRects()].map(box => ({
          left: Math.round(box.left * 100) / 100,
          right: Math.round(box.right * 100) / 100,
          top: Math.round(box.top * 100) / 100,
          bottom: Math.round(box.bottom * 100) / 100,
        }));
        const clippingAncestors = [];
        for (let ancestor = element; ancestor && ancestor !== document.body;
             ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor);
          const box = ancestor.getBoundingClientRect();
          const clipsX = ['hidden', 'clip'].includes(style.overflowX);
          const clipsY = ['hidden', 'clip'].includes(style.overflowY);
          const clippedTextRects = textRects.filter(textRect =>
            (clipsX && (textRect.left < box.left - 1 || textRect.right > box.right + 1)) ||
            (clipsY && (textRect.top < box.top - 1 || textRect.bottom > box.bottom + 1))
          );
          if (clippedTextRects.length > 0) {
            clippingAncestors.push({
              element: ancestor.tagName.toLowerCase() +
                (ancestor.id ? `#${ancestor.id}` : '') +
                (typeof ancestor.className === 'string' && ancestor.className.trim()
                  ? `.${ancestor.className.trim().split(/\s+/).join('.')}`
                  : ''),
              overflow_x: style.overflowX,
              overflow_y: style.overflowY,
              rect: {
                left: Math.round(box.left * 100) / 100,
                right: Math.round(box.right * 100) / 100,
                top: Math.round(box.top * 100) / 100,
                bottom: Math.round(box.bottom * 100) / 100,
              },
              clipped_text_rects: clippedTextRects,
            });
          }
        }
        return {
          clipped: clippingAncestors.length > 0,
          text_rects: textRects,
          clipping_ancestors: clippingAncestors,
        };
      };
      const summary = group.querySelector('summary');
      const scrollWrap = group.querySelector('.mermaid-scroll-wrap');
      const diagram = group.querySelector('.mermaid');
      const originalScrollLeft = scrollWrap?.scrollLeft ?? 0;
      const maxScrollLeft = scrollWrap
        ? Math.max(0, scrollWrap.scrollWidth - scrollWrap.clientWidth)
        : 0;
      let scrollLeftAfterPan = null;
      if (scrollWrap && maxScrollLeft > 0) {
        scrollWrap.scrollLeft = 0;
        scrollWrap.scrollLeft = maxScrollLeft;
        scrollLeftAfterPan = scrollWrap.scrollLeft;
        scrollWrap.scrollLeft = originalScrollLeft;
      }

      const links = [...(group.querySelectorAll('.link-list a') ?? [])];
      return {
        group_exists: true,
        title: summary?.textContent.trim() ?? '',
        open: group.open,
        other_groups_collapsed: groups.every((other, otherIndex) =>
          otherIndex === index || !other.open
        ),
        page_width: {
          viewport: window.innerWidth,
          document: document.documentElement.scrollWidth,
          body: document.body?.scrollWidth ?? null,
          ...(checkTextClipping
            ? {
                elements: [
                  {
                    selector: 'html',
                    client_width: document.documentElement.clientWidth,
                    scroll_width: document.documentElement.scrollWidth,
                  },
                  ...(document.body
                    ? [{
                        selector: 'body',
                        client_width: document.body.clientWidth,
                        scroll_width: document.body.scrollWidth,
                      }]
                    : []),
                ],
              }
            : {}),
        },
        summary: {
          ...(checkTextClipping
            ? { selector: `${groupSelector}:nth-of-type(${index + 1}) > summary` }
            : {}),
          visible: visible(summary),
          text: summary?.textContent.trim() ?? '',
          rect: rect(summary),
          client_width: summary?.clientWidth ?? null,
          scroll_width: summary?.scrollWidth ?? null,
          ...(checkTextClipping ? { text_layout: textClipping(summary) } : {}),
        },
        map: {
          ...(checkTextClipping
            ? {
                selector: `${groupSelector}:nth-of-type(${index + 1}) .mermaid-scroll-wrap`,
              }
            : {}),
          visible: visible(scrollWrap),
          overflow_x: scrollWrap ? getComputedStyle(scrollWrap).overflowX : null,
          client_width: scrollWrap?.clientWidth ?? null,
          scroll_width: scrollWrap?.scrollWidth ?? null,
          max_scroll_left: maxScrollLeft,
          scroll_left_after_pan: scrollLeftAfterPan,
          rendered_svg_ready: Boolean(
            diagram?.dataset.universeReady === '1' &&
            diagram.querySelector('svg .node')
          ),
        },
        links: links.map((link, linkIndex) => {
          const box = rect(link);
          return {
            ...(checkTextClipping
              ? {
                  selector:
                    `${groupSelector}:nth-of-type(${index + 1}) .link-list a:nth-of-type(${linkIndex + 1})`,
                }
              : {}),
            text: link.textContent.trim(),
            href: link.getAttribute('href'),
            visible: visible(link),
            rect: box,
            ...(checkTextClipping ? { text_layout: textClipping(link) } : {}),
            within_viewport: Boolean(
              box && box.left >= -1 && box.right <= window.innerWidth + 1
            ),
          };
        }),
      };
    }, {
      index: groupIndex,
      groupSelector: UNIVERSE_PAGE_MAP_GROUP_SELECTOR,
      checkTextClipping,
    });
  } catch (error) {
    errors.push(
      `${prefix} USABILITY CHECK ERROR: ${error.message.split('\n')[0]}`
    );
    return { errors, evidence };
  }

  const label = evidence?.title ? `"${evidence.title}"` : groupLabel;
  if (!evidence?.group_exists) {
    errors.push(`${prefix} MISSING: generated page-map group was not found`);
    return { errors, evidence };
  }
  if (!evidence.open || !evidence.other_groups_collapsed) {
    errors.push(
      `${prefix} COLLAPSED STATE INVALID: expected only ${label} to be open; ` +
      `${JSON.stringify({
        open: evidence.open,
        other_groups_collapsed: evidence.other_groups_collapsed,
      })}`
    );
  }
  if (evidence.page_width.document > evidence.page_width.viewport ||
      evidence.page_width.body > evidence.page_width.viewport) {
    errors.push(
      `${prefix} PAGE OVERFLOW: document or body is wider than the viewport; ` +
      `${JSON.stringify(evidence.page_width)}`
    );
  }
  if (!evidence.summary.visible || !evidence.summary.text ||
      !evidence.summary.rect || evidence.summary.rect.left < -1 ||
      evidence.summary.rect.right > evidence.page_width.viewport + 1 ||
      evidence.summary.client_width <= 0 ||
      evidence.summary.scroll_width > evidence.summary.client_width + 1 ||
      (checkTextClipping && evidence.summary.text_layout?.clipped)) {
    errors.push(
      `${prefix} SUMMARY UNUSABLE: ${label} is hidden, clipped, or horizontally overflowing; ` +
      (checkTextClipping
        ? `viewport=${JSON.stringify(evidence.page_width)}; element=${evidence.summary.selector}; `
        : '') +
      `${JSON.stringify(evidence.summary)}`
    );
  }
  if (!evidence.map.visible ||
      !['auto', 'scroll'].includes(evidence.map.overflow_x) ||
      evidence.map.max_scroll_left <= 1 ||
      evidence.map.scroll_left_after_pan <= 1 ||
      !evidence.map.rendered_svg_ready) {
    errors.push(
      `${prefix} DIAGRAM NOT HORIZONTALLY SCROLLABLE OR READY: ${label}; ` +
      (checkTextClipping
        ? `viewport=${JSON.stringify(evidence.page_width)}; element=${evidence.map.selector}; `
        : '') +
      `${JSON.stringify(evidence.map)}`
    );
  }
  if (evidence.links.length === 0 ||
      evidence.links.some(link =>
        !link.visible || !link.href || !link.within_viewport ||
        (checkTextClipping && link.text_layout?.clipped)
      )) {
    const affectedLinks = evidence.links.filter(link =>
      !link.visible || !link.href || !link.within_viewport ||
      (checkTextClipping && link.text_layout?.clipped)
    );
    errors.push(
      `${prefix} ORDINARY LINKS UNUSABLE: ${label} has missing, hidden, or clipped page links; ` +
      (checkTextClipping ? `viewport=${JSON.stringify(evidence.page_width)}; ` : '') +
      `elements=${JSON.stringify(affectedLinks.length ? affectedLinks : evidence.links)}`
    );
  }

  let linkClickWorks = false;
  try {
    const setup = await page.evaluate((index) => {
      const group = document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )[index];
      const link = group?.querySelector('.link-list a');
      if (!link) return null;

      link.setAttribute('data-responsive-qa-click-target', '');
      link.addEventListener('click', event => {
        event.preventDefault();
        window.__responsiveQaPageMapLinkClick = {
          trusted: event.isTrusted,
          href: link.getAttribute('href'),
        };
      }, { capture: true, once: true });
      return { href: link.getAttribute('href') };
    }, groupIndex);

    if (setup?.href) {
      const link = page.locator(
        `${UNIVERSE_PAGE_MAP_GROUP_SELECTOR}:nth-of-type(${groupIndex + 1}) ` +
        '.link-list a[data-responsive-qa-click-target]'
      );
      await link.scrollIntoViewIfNeeded();
      linkClickWorks = await link.click({ timeout: 5000 })
        .then(() => true, () => false);
      const clickEvidence = await page.evaluate(() =>
        window.__responsiveQaPageMapLinkClick ?? null
      );
      linkClickWorks = linkClickWorks &&
        clickEvidence?.trusted === true &&
        clickEvidence?.href === setup.href;
      evidence.ordinary_link_click = {
        attempted_href: setup.href,
        click: clickEvidence,
        works: linkClickWorks,
      };
    } else {
      evidence.ordinary_link_click = { works: false, reason: 'no ordinary link found' };
    }
  } catch (error) {
    evidence.ordinary_link_click = {
      works: false,
      error: error.message.split('\n')[0],
    };
  } finally {
    await page.evaluate((index) => {
      document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )[index]?.querySelector('[data-responsive-qa-click-target]')
        ?.removeAttribute('data-responsive-qa-click-target');
    }, groupIndex).catch(() => {});
  }
  if (!linkClickWorks) {
    errors.push(
      `${prefix} ORDINARY LINK DID NOT RESPOND TO A ${interactionLabel}: ` +
      `${JSON.stringify(evidence.ordinary_link_click)}`
    );
  }

  const pageWidthAfterInteraction = await page.evaluate(({ checkTextClipping }) => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body?.scrollWidth ?? null,
    ...(checkTextClipping
      ? {
          elements: [
            {
              selector: 'html',
              client_width: document.documentElement.clientWidth,
              scroll_width: document.documentElement.scrollWidth,
            },
            ...(document.body
              ? [{
                  selector: 'body',
                  client_width: document.body.clientWidth,
                  scroll_width: document.body.scrollWidth,
                }]
              : []),
          ],
        }
      : {}),
  }), { checkTextClipping }).catch(() => null);
  evidence.page_width_after_interaction = pageWidthAfterInteraction;
  if (pageWidthAfterInteraction &&
      (pageWidthAfterInteraction.document > pageWidthAfterInteraction.viewport ||
       pageWidthAfterInteraction.body > pageWidthAfterInteraction.viewport)) {
    errors.push(
      `${prefix} PAGE OVERFLOW AFTER INTERACTION: ${JSON.stringify(pageWidthAfterInteraction)}`
    );
  }

  return { errors, evidence };
}

async function checkUniversePageMapZoomUsability(page) {
  const errors = [];
  const originalViewport = page.viewportSize();
  const groupEvidence = [];
  const referenceViewport = UNIVERSE_PAGE_MAP_ZOOM_200_PROBE.reference_viewport;
  const layoutViewport = UNIVERSE_PAGE_MAP_ZOOM_200_PROBE.layout_viewport;
  const evidence = {
    browser_zoom_percent: UNIVERSE_PAGE_MAP_ZOOM_200_PROBE.browser_zoom_percent,
    reference_viewport: referenceViewport,
    layout_viewport: layoutViewport,
    viewport_matches_reference: originalViewport?.width === referenceViewport.width &&
      originalViewport?.height === referenceViewport.height,
    groups: groupEvidence,
  };

  if (!originalViewport || !evidence.viewport_matches_reference) {
    errors.push(
      `UNIVERSE PAGE MAP 200% ZOOM BASELINE INVALID: expected ` +
      `${referenceViewport.width}x${referenceViewport.height} reference viewport; ` +
      `actual=${JSON.stringify(originalViewport)}`
    );
    return { errors, evidence };
  }

  try {
    // Browser zoom halves the CSS layout viewport at 200%. Resizing to that
    // equivalent CSS viewport exercises the same responsive layout reflow.
    await page.setViewportSize(layoutViewport);
    await page.evaluate(() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))
    ));
    evidence.actual_layout_viewport = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      document_width: document.documentElement.scrollWidth,
      body_width: document.body?.scrollWidth ?? null,
      elements: [
        {
          selector: 'html',
          client_width: document.documentElement.clientWidth,
          scroll_width: document.documentElement.scrollWidth,
        },
        ...(document.body
          ? [{
              selector: 'body',
              client_width: document.body.clientWidth,
              scroll_width: document.body.scrollWidth,
            }]
          : []),
      ],
    }));
    if (evidence.actual_layout_viewport.width !== layoutViewport.width ||
        evidence.actual_layout_viewport.height !== layoutViewport.height) {
      errors.push(
        `UNIVERSE PAGE MAP 200% ZOOM VIEWPORT INVALID: expected ` +
        `${JSON.stringify(layoutViewport)} CSS layout viewport; ` +
        `actual=${JSON.stringify(evidence.actual_layout_viewport)}`
      );
    }

    const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
    const groupCount = await groups.count();
    evidence.group_count = groupCount;
    if (groupCount === 0) {
      errors.push(
        'UNIVERSE PAGE MAP 200% ZOOM GROUP MISSING: no generated details groups were found'
      );
      return { errors, evidence };
    }

    for (let groupIndex = 0; groupIndex < groupCount; groupIndex += 1) {
      const target = groups.nth(groupIndex);
      const summary = target.locator('summary');
      let openedByKeyboard = false;
      let keyboardLinkActivation = {
        focused: false,
        activated: false,
        href: null,
        selector:
          `${UNIVERSE_PAGE_MAP_GROUP_SELECTOR}:nth-of-type(${groupIndex + 1}) .link-list a:first-of-type`,
      };
      try {
        await summary.focus();
        await page.keyboard.press('Enter');
        openedByKeyboard = await target.evaluate(group => group.open);
        if (!openedByKeyboard) {
          errors.push(
            `UNIVERSE PAGE MAP 200% ZOOM SUMMARY CONTROL INACCESSIBLE: ` +
            `Enter did not open group ${groupIndex + 1}; ` +
            `element=${UNIVERSE_PAGE_MAP_GROUP_SELECTOR}:nth-of-type(${groupIndex + 1}) > summary; ` +
            `viewport=${JSON.stringify(evidence.actual_layout_viewport)}`
          );
        }

        const usability = openedByKeyboard
          ? await checkUniversePageMapNarrowUsability(
            page,
            groupIndex,
            '200% ZOOM',
            'CLICK',
            true
          )
          : { errors: [], evidence: null };
        errors.push(...usability.errors);

        const link = target.locator('.link-list a').first();
        const linkCount = await target.locator('.link-list a').count();
        if (linkCount > 0) {
          const setup = await link.evaluate(element => {
            element.addEventListener('click', event => {
              event.preventDefault();
              window.__responsiveQaZoomLinkActivation = {
                trusted: event.isTrusted,
                href: element.getAttribute('href'),
              };
            }, { capture: true, once: true });
            return { href: element.getAttribute('href') };
          });
          await link.focus();
          keyboardLinkActivation.focused = await link.evaluate(
            element => element === document.activeElement
          );
          keyboardLinkActivation.href = setup.href;
          if (keyboardLinkActivation.focused) {
            await page.keyboard.press('Enter');
            const activation = await page.evaluate(
              () => window.__responsiveQaZoomLinkActivation ?? null
            );
            keyboardLinkActivation.activated =
              activation?.trusted === true && activation?.href === setup.href;
            keyboardLinkActivation.event = activation;
          }
        }
        if (!keyboardLinkActivation.focused || !keyboardLinkActivation.activated) {
          errors.push(
            `UNIVERSE PAGE MAP 200% ZOOM LINK CONTROL INACCESSIBLE: ` +
            `group ${groupIndex + 1} ordinary link could not be focused and activated by keyboard; ` +
            `viewport=${JSON.stringify(evidence.actual_layout_viewport)}; ` +
            `link=${JSON.stringify(keyboardLinkActivation)}`
          );
        }

        await summary.focus();
        await page.keyboard.press('Enter');
        const closedByKeyboard = !(await target.evaluate(group => group.open));
        if (!closedByKeyboard) {
          errors.push(
            `UNIVERSE PAGE MAP 200% ZOOM SUMMARY CONTROL INACCESSIBLE: ` +
            `Enter did not close group ${groupIndex + 1}; ` +
            `element=${UNIVERSE_PAGE_MAP_GROUP_SELECTOR}:nth-of-type(${groupIndex + 1}) > summary; ` +
            `viewport=${JSON.stringify(evidence.actual_layout_viewport)}`
          );
        }
        groupEvidence.push({
          group_index: groupIndex + 1,
          title: await summary.textContent().then(text => text?.trim() ?? ''),
          opened_by_keyboard: openedByKeyboard,
          closed_by_keyboard: closedByKeyboard,
          keyboard_link_activation: keyboardLinkActivation,
          usability: usability.evidence,
        });
      } catch (error) {
        errors.push(
          `UNIVERSE PAGE MAP 200% ZOOM CHECK ERROR: group ${groupIndex + 1} ` +
          `${error.message.split('\n')[0]}; viewport=${JSON.stringify(
            evidence.actual_layout_viewport
          )}`
        );
      }
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP 200% ZOOM CHECK ERROR: ${error.message.split('\n')[0]}`
    );
  } finally {
    await page.setViewportSize(originalViewport).catch(() => {});
  }

  return { errors, evidence };
}

async function checkUniversePageMapGroupsGeometry(page, waitForTwoFrames) {
  const errors = [];
  const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
  const groupCount = await groups.count();
  if (groupCount === 0) {
    return {
      errors: ['UNIVERSE PAGE MAP GROUP MISSING: no generated details groups were found'],
      initiallyCollapsed: false,
      groupCount,
      groups: [],
    };
  }
  const themeStylesReady = await page.waitForFunction(
    () => [...document.querySelectorAll('link[data-deferred-styles]')].every(link => {
      try {
        return link.media === 'all' && Boolean(link.sheet?.cssRules.length);
      } catch {
        return false;
      }
    }),
    undefined,
    { timeout: 15000 }
  ).then(() => true, () => false);
  if (!themeStylesReady) {
    errors.push(
      'UNIVERSE PAGE MAP THEME STYLES UNAVAILABLE: could not confirm the full theme stylesheet ' +
      'was active before measuring rendered SVG readability'
    );
  } else {
    await waitForTwoFrames();
  }

  const initiallyCollapsed = await groups.evaluateAll(elements =>
    elements.every(element => !element.open)
  );
  if (!initiallyCollapsed) {
    errors.push(
      'UNIVERSE PAGE MAP COLLAPSED STATE INVALID: a generated group was open before the group checks'
    );
  }

  const groupEvidence = [];
  for (let groupIndex = 0; groupIndex < groupCount; groupIndex += 1) {
    const groupCheck = await openUniversePageMapGroupForGeometryCheck(
      page,
      groupIndex,
      groupCount
    );
    errors.push(...groupCheck.errors);

    let released = false;
    let ready = false;
    let after = null;
    let narrowUsability = null;
    let themeSwitch = null;
    try {
      if (groupCheck.renderScheduled) {
        released = await releaseResponsiveQaMermaidRender(page);
        if (!released) {
          errors.push(
            `UNIVERSE PAGE MAP RENDER NOT RELEASED: group ${groupIndex + 1} had no held Mermaid render`
          );
        } else {
          ready = await page.waitForFunction((index) => {
            const group = document.querySelectorAll(
              '.universe-map-generated .universe-map-group'
            )[index];
            return Boolean(group?.querySelector('.mermaid svg .node'));
          }, groupIndex, { timeout: 20000 }).then(() => true, () => false);
        }
      }
      await waitForTwoFrames();
      after = await captureUniversePageMapGeometry(page, groupIndex);
      if (!ready) {
        errors.push(
          `UNIVERSE PAGE MAP DID NOT REACH SVG READY STATE: group ${groupIndex + 1} ` +
          `${groupCheck.before?.title ? `"${groupCheck.before.title}"` : ''}; ` +
          `before=${JSON.stringify(groupCheck.before)}; after=${JSON.stringify(after)}`
        );
      }
      if (ready && after?.svg_ready) {
        themeSwitch = await checkUniversePageMapThemeSwitch(
          page,
          groupCheck,
          after,
          waitForTwoFrames
        );
        errors.push(...themeSwitch.errors);
      }
      if (page.viewportSize()?.width === 320) {
        narrowUsability = await checkUniversePageMapNarrowUsability(page, groupIndex);
        errors.push(...narrowUsability.errors);
      }
    } catch (error) {
      errors.push(
        `UNIVERSE PAGE MAP GEOMETRY CHECK ERROR: group ${groupIndex + 1} ` +
        `${error.message.split('\n')[0]}`
      );
      after = await captureUniversePageMapGeometry(page, groupIndex).catch(() => null);
    }

    const comparison = await compareUniversePageMapGeometry(page, groupCheck, after);
    errors.push(...comparison.errors);
    groupEvidence.push({
      group_index: groupIndex + 1,
      title: groupCheck.before?.title ?? '',
      node_count: groupCheck.before?.node_count ?? null,
      initially_collapsed: groupCheck.initiallyCollapsed,
      opened_by_keyboard: groupCheck.openedByKeyboard,
      other_groups_stayed_collapsed: groupCheck.othersStayedCollapsed,
      render_scheduled: groupCheck.renderScheduled,
      render_released: released,
      rendered_svg_ready: ready,
      closed_by_keyboard: comparison.closedByKeyboard,
      before: groupCheck.before,
      after,
      shifts: comparison.shifts,
      ...(themeSwitch ? { theme_switch: themeSwitch.evidence } : {}),
      ...(narrowUsability ? { narrow_usability: narrowUsability.evidence } : {}),
    });
  }

  let groupSwitchPanRetention = null;
  if ([320, 1280].includes(page.viewportSize()?.width)) {
    const switchCheck = await checkUniversePageMapGroupSwitchPanRetention(
      page,
      groupCount,
      waitForTwoFrames
    );
    errors.push(...switchCheck.errors);
    groupSwitchPanRetention = switchCheck.evidence;
  }

  return {
    errors,
    initiallyCollapsed,
    groupCount,
    groups: groupEvidence,
    group_switch_pan_retention: groupSwitchPanRetention,
  };
}

async function checkUniversePageMapGroupSwitchPanRetention(page, groupCount, waitForTwoFrames) {
  const errors = [];
  const evidence = {
    group_indexes: [1, 2],
    viewport_width: page.viewportSize()?.width ?? null,
    zoom: page.viewportSize()?.width === 1280 ? 2 : 1,
    maps: [],
    switches: [],
  };
  if (groupCount < 2) {
    errors.push(
      'UNIVERSE PAGE MAP GROUP SWITCH UNAVAILABLE: expected at least two generated map groups'
    );
    return { errors, evidence };
  }

  const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
  let zoomApplied = false;
  try {
    if (evidence.zoom === 2) {
      await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
      zoomApplied = true;
      await waitForTwoFrames();
    }

    const panOpenGroup = async (index, fraction) => {
      const group = groups.nth(index);
      for (let otherIndex = 0; otherIndex < groupCount; otherIndex += 1) {
        if (otherIndex !== index) {
          const otherGroup = groups.nth(otherIndex);
          if (await otherGroup.evaluate(element => element.open)) {
            await otherGroup.locator('summary').click();
          }
        }
      }
      if (!(await group.evaluate(element => element.open))) {
        await group.locator('summary').click();
      }
      await group.locator('.mermaid svg .node').first().waitFor({ timeout: 20000 });
      await waitForTwoFrames();
      const wrap = group.locator('.mermaid-scroll-wrap');
      const dimensions = await wrap.evaluate(element => ({
        client_width: element.clientWidth,
        scroll_width: element.scrollWidth,
      }));
      if (dimensions.scroll_width <= dimensions.client_width) {
        throw new Error(
          `group ${index + 1} has no horizontal overflow; dimensions=${JSON.stringify(dimensions)}`
        );
      }
      const position = await wrap.evaluate((element, ratio) => {
        const max_scroll_left = element.scrollWidth - element.clientWidth;
        element.scrollLeft = max_scroll_left * ratio;
        return {
          max_scroll_left,
          scroll_left: element.scrollLeft,
        };
      }, fraction);
      if (position.scroll_left <= 0 ||
          position.scroll_left >= position.max_scroll_left) {
        throw new Error(
          `group ${index + 1} did not reach an intermediate pan position; ` +
          `${JSON.stringify(position)}`
        );
      }
      return { group_index: index + 1, ...dimensions, ...position };
    };
    const captureSwitchState = async (index, expectedPosition, transition) => {
      const state = await page.evaluate(groupIndex => {
        const mapGroups = [...document.querySelectorAll(
          '.universe-map-generated .universe-map-group'
        )];
        return {
          open_indexes: mapGroups.flatMap((group, groupIndex) =>
            group.open ? [groupIndex + 1] : []
          ),
          svg_ready: Boolean(mapGroups[groupIndex]?.querySelector('.mermaid svg .node')),
          scroll_left: mapGroups[groupIndex]?.querySelector('.mermaid-scroll-wrap')?.scrollLeft ?? null,
        };
      }, index);
      const preserved = state.scroll_left !== null &&
        Math.abs(state.scroll_left - expectedPosition) <= 1;
      evidence.switches.push({
        transition,
        selected_group_index: index + 1,
        ...state,
        expected_scroll_left: expectedPosition,
        pan_preserved: preserved,
        only_selected_group_open: state.open_indexes.length === 1 &&
          state.open_indexes[0] === index + 1,
      });
      if (!state.svg_ready || !preserved ||
          state.open_indexes.length !== 1 || state.open_indexes[0] !== index + 1) {
        errors.push(
          `UNIVERSE PAGE MAP GROUP SWITCH LOST PAN OR OPEN STATE: ${transition}; ` +
          `evidence=${JSON.stringify(evidence.switches.at(-1))}`
        );
      }
    };

    const firstPosition = await panOpenGroup(0, 0.25);
    evidence.maps.push(firstPosition);
    await captureSwitchState(0, firstPosition.scroll_left, 'first map initially panned');
    const secondPosition = await panOpenGroup(1, 0.75);
    evidence.maps.push(secondPosition);
    await captureSwitchState(1, secondPosition.scroll_left, 'switch to second map');
    if (Math.abs(firstPosition.scroll_left - secondPosition.scroll_left) <= 1) {
      errors.push(
        'UNIVERSE PAGE MAP GROUP SWITCH POSITIONS NOT DISTINCT: the two maps were not ' +
        `panned to distinct horizontal positions; first=${firstPosition.scroll_left}, ` +
        `second=${secondPosition.scroll_left}`
      );
    }
    await groups.nth(1).locator('summary').click();
    const closedAfterSecond = await groups.evaluateAll(elements =>
      elements.every(group => !group.open)
    );
    evidence.switches.push({
      transition: 'close second group before returning to first',
      all_groups_closed: closedAfterSecond,
    });
    if (!closedAfterSecond) {
      errors.push(
        'UNIVERSE PAGE MAP GROUP SWITCH CLOSE FAILED: closing the second group must leave ' +
        'all generated groups closed'
      );
    }
    await groups.nth(0).locator('summary').click();
    await captureSwitchState(0, firstPosition.scroll_left, 'return to first map');
    await groups.nth(0).locator('summary').click();
    const closedAfterFirst = await groups.evaluateAll(elements =>
      elements.every(group => !group.open)
    );
    evidence.switches.push({
      transition: 'close first group before returning to second',
      all_groups_closed: closedAfterFirst,
    });
    if (!closedAfterFirst) {
      errors.push(
        'UNIVERSE PAGE MAP GROUP SWITCH CLOSE FAILED: closing the first group must leave ' +
        'all generated groups closed'
      );
    }
    await groups.nth(1).locator('summary').click();
    await captureSwitchState(1, secondPosition.scroll_left, 'return to second map');
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP GROUP SWITCH CHECK FAILED: ${error.message.split('\n')[0]}`
    );
  } finally {
    await groups.evaluateAll(elements => {
      elements.forEach(group => { group.open = false; });
    }).catch(() => {});
    evidence.all_groups_closed_after_check = await groups.evaluateAll(elements =>
      elements.every(group => !group.open)
    ).catch(() => false);
    if (!evidence.all_groups_closed_after_check) {
      errors.push(
        'UNIVERSE PAGE MAP GROUP SWITCH CLEANUP FAILED: generated groups must be collapsed ' +
        'before subsequent page-map checks'
      );
    }
    if (zoomApplied) {
      await page.evaluate(() => { document.documentElement.style.zoom = '1'; })
        .catch(() => {});
      await waitForTwoFrames().catch(() => {});
    }
  }
  return { errors, evidence };
}

async function checkUniversePageMapFontPendingRotation(page, groupIndexes, waitForTwoFrames) {
  const errors = [];
  const originalViewport = page.viewportSize();
  const evidence = {
    font_requests_held: true,
    original_viewport: originalViewport,
    bursts: [],
    viewport_restored: false,
  };
  let savedScrollPositions = [];
  let savedSvgCount = 0;

  const settleAndCapture = async (name, viewport) => {
    const reservationsSettled = await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(
        '.universe-map-group[open] li, .universe-map-group[open] figcaption'
      )].filter(element => element.getClientRects().length);
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS })
      .then(() => true, () => false);
    if (!reservationsSettled) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION RESERVATIONS NOT SETTLED: ` +
        `stage=${name}; expected_width=${viewport.width}`
      );
    }
    await waitForTwoFrames();
    const stage = await page.evaluate(({ name, viewport }) => {
      const items = [...document.querySelectorAll(
        '.universe-map-group[open] li, .universe-map-group[open] figcaption'
      )].filter(element => element.getClientRects().length);
      const mapGroups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )].filter(group => group.open);
      return {
        name,
        viewport_width: window.innerWidth,
        expected_viewport_width: viewport.width,
        document_scroll_width: document.documentElement.scrollWidth,
        document_overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        open_group_count: mapGroups.length,
        svg_count: mapGroups.filter(group => group.querySelector('svg .node')).length,
        reservation_count: items.length,
        reservation_widths: items.map(element =>
          Number(element.dataset.universeFontReservationWidth ?? NaN)
        ),
      };
    }, { name, viewport });
    evidence.bursts.push(stage);
    if (stage.viewport_width !== viewport.width ||
        stage.expected_viewport_width !== viewport.width) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION VIEWPORT INVALID: ` +
        `stage=${JSON.stringify(stage)}`
      );
    }
    if (stage.reservation_count === 0 ||
        stage.reservation_widths.some(width => width !== viewport.width)) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION STALE RESERVATION: settled reservations ` +
        `must all match ${viewport.width}px; stage=${JSON.stringify(stage)}`
      );
    }
    if (stage.document_overflow) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION PAGE OVERFLOW: ` +
        `scroll_width=${stage.document_scroll_width}px; viewport=${stage.viewport_width}px`
      );
    }
    if (stage.open_group_count !== groupIndexes.length ||
        stage.svg_count !== groupIndexes.length) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION MAP CHANGED: open maps and rendered SVGs ` +
        `must remain present; stage=${JSON.stringify(stage)}`
      );
    }
  };

  try {
    if (originalViewport?.width !== 320 || groupIndexes.length === 0) {
      errors.push(
        'UNIVERSE PAGE MAP FONT-PENDING ROTATION CHECK UNAVAILABLE: expected open maps at 320px'
      );
      return { errors, evidence };
    }
    const allOpen = await page.evaluate((indexes) => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      return indexes.every(index => Boolean(groups[index]?.open && groups[index]?.querySelector('svg .node')));
    }, groupIndexes);
    if (!allOpen) {
      errors.push(
        'UNIVERSE PAGE MAP FONT-PENDING ROTATION BASELINE INVALID: selected groups must be open and rendered'
      );
      return { errors, evidence };
    }
    savedScrollPositions = await Promise.all(groupIndexes.map(index =>
      page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(index)
        .locator('.mermaid-scroll-wrap').evaluate(element => element.scrollLeft)
    ));
    savedSvgCount = await page.evaluate((indexes) => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      window.__responsiveQaFontPendingRotationSvgs =
        indexes.map(index => groups[index]?.querySelector('svg') ?? null);
      return window.__responsiveQaFontPendingRotationSvgs.filter(Boolean).length;
    }, groupIndexes);
    if (savedSvgCount !== groupIndexes.length) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION BASELINE INVALID: ` +
        `expected ${groupIndexes.length} rendered SVGs; found=${savedSvgCount}`
      );
    }

    const rapidResizeBurst = async (name, widths) => {
      for (const width of widths) {
        const viewport = width === 740
          ? { width, height: 390 }
          : { width, height: width === 320 ? 740 : 844 };
        await page.setViewportSize(viewport);
      }
      const finalViewport = page.viewportSize();
      await settleAndCapture(name, finalViewport);
    };

    // Do not wait for reservation completion between orientation changes.
    await rapidResizeBurst('settled_portrait_after_burst', [740, 390, 740, 320]);
    await rapidResizeBurst('settled_phone_after_second_burst', [740, 320, 390]);

    await page.setViewportSize(originalViewport);
    await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(
        '.universe-map-group[open] li, .universe-map-group[open] figcaption'
      )].filter(element => element.getClientRects().length);
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS })
      .then(() => true, () => {
        errors.push(
          'UNIVERSE PAGE MAP FONT-PENDING ROTATION RESERVATIONS NOT SETTLED: ' +
          'original portrait viewport'
        );
      });
    await Promise.all(groupIndexes.map((index, position) =>
      page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(index)
        .locator('.mermaid-scroll-wrap')
        .evaluate((element, scrollLeft) => {
          element.scrollLeft = scrollLeft;
        }, savedScrollPositions[position])
    ));
    await waitForTwoFrames();
    await settleAndCapture('restored_portrait', originalViewport);
    evidence.svg_preserved = await page.evaluate((indexes) => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      return indexes.every((index, position) =>
        groups[index]?.querySelector('svg') ===
        window.__responsiveQaFontPendingRotationSvgs?.[position]
      );
    }, groupIndexes);
    if (!evidence.svg_preserved) {
      errors.push(
        'UNIVERSE PAGE MAP FONT-PENDING ROTATION MAP REPLACED: an open map SVG changed during rotation'
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP FONT-PENDING ROTATION CHECK ERROR: ${error.message.split('\n')[0]}`
    );
  } finally {
    if (originalViewport) {
      await page.setViewportSize(originalViewport).catch(() => {});
      await waitForTwoFrames().catch(() => {});
      evidence.viewport_restored =
        page.viewportSize()?.width === originalViewport.width &&
        page.viewportSize()?.height === originalViewport.height;
    }
    await page.evaluate(() => {
      delete window.__responsiveQaFontPendingRotationSvgs;
    }).catch(() => {});
    if (!evidence.viewport_restored) {
      errors.push(
        `UNIVERSE PAGE MAP FONT-PENDING ROTATION VIEWPORT NOT RESTORED: ` +
        `expected=${JSON.stringify(originalViewport)}; actual=${JSON.stringify(page.viewportSize())}`
      );
    }
  }
  return { errors, evidence };
}

async function checkUniversePageMapWebFontGeometry(
  page,
  releaseFontRequests,
  fontRequestSeen,
  fontFailureSeen,
  fontRequests,
  fontResponses,
  fontFailures,
  waitForTwoFrames
) {
  const errors = [];
  const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
  const groupCount = await groups.count();
  const viewportWidth = page.viewportSize()?.width ?? null;
  const midpointGroupIndex = Math.floor(groupCount / 2);
  const groupIndexes = groupCount > 0 && viewportWidth === 320
    ? [...new Set([0, groupCount - 1])]
    : groupCount > 0 && viewportWidth === 390
      ? [...new Set([0, midpointGroupIndex, groupCount - 1])]
    : groupCount > 0
      ? [midpointGroupIndex]
      : [];
  const expectedOpenGroupIndexes = groupIndexes;
  const evidence = {
    threshold_px: UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX,
    viewport_width: viewportWidth,
    measured_group_indexes: groupIndexes.map(index => index + 1),
    font_requests_held_before_baseline: fontRequests.length,
    stylesheet_ready_before_baseline: false,
    resource_failures: [],
    font_load_verified: false,
    fallback_font_state: null,
    loaded_font_state: null,
    mermaid_svg_ready_before_font_capture: false,
    mermaid_svg_ready_after_font_load: false,
    mermaid_group_readiness_before_font_capture: [],
    mermaid_group_readiness_after_font_load: [],
    pan_before_font_load: [],
    pan_after_font_load: [],
    group_index: groupIndexes.length === 1 ? groupIndexes[0] + 1 : null,
    before: null,
    after: null,
    groups_before: [],
    groups_after: [],
    shifts: [],
    collapsed_after_check: false,
  };
  const targets = groupIndexes.map(index => groups.nth(index));
  let opened = false;
  let baselineCaptured = false;

  try {
    if (groupCount === 0) {
      errors.push('UNIVERSE PAGE MAP WEB FONT GROUP MISSING: no generated group was available');
      return { errors, evidence };
    }

    const allCollapsed = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    );
    if (!allCollapsed) {
      errors.push(
        'UNIVERSE PAGE MAP WEB FONT BASELINE INVALID: generated groups were not collapsed before font measurement'
      );
    }

    const fontStylesheetReady = await Promise.race([
      page.waitForFunction(() => {
        const link = document.querySelector('link[data-deferred-fonts]');
        return Boolean(link?.sheet) && link.media === 'all';
      }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS })
        .then(() => true, () => false),
      fontFailureSeen.then(() => false),
    ]);
    evidence.stylesheet_ready_before_baseline = fontStylesheetReady;
    if (!fontStylesheetReady) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT STYLESHEET NOT READY: deferred Google Fonts stylesheet was not active before measurement; ` +
        `resource_failures=${JSON.stringify(fontFailures)}`
      );
    }

    await Promise.all(targets.map(target =>
      target.evaluate(group => { group.open = true; })
    ));
    opened = await Promise.all(targets.map(target =>
      target.evaluate(group => group.open)
    )).then(states => states.every(Boolean));
    await waitForTwoFrames();

    const reservationsReady = await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(
        '.universe-map-group[open] li, .universe-map-group[open] figcaption'
      )];
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS })
      .then(() => true, () => false);
    if (!reservationsReady) {
      errors.push(
        'UNIVERSE PAGE MAP WEB FONT BASELINE INVALID: open-group fallback reservations did not settle before measurement'
      );
    }

    const fontSamples = await page.evaluate(() => {
      const selectors = [
        '.universe-map-generated > h2',
        '.universe-map-generated > p',
        '.universe-map-group > summary',
        '.universe-map-group[open] li',
        '.universe-map-group[open] figcaption',
      ];
      const elements = selectors.flatMap(selector =>
        [...document.querySelectorAll(selector)]
          .filter(element => element.getClientRects().length)
          .map(element => ({ selector, element }))
      );
      const loadPromises = elements.map(({ selector, element }) => {
        const style = getComputedStyle(element);
        return document.fonts.load(style.font, element.textContent || 'Universe')
          .then(faces => ({
            selector,
            family: style.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''),
            font: style.font,
            faces: faces.map(face => ({
              family: face.family.replace(/^["']|["']$/g, ''),
              status: face.status,
            })),
          }))
          .catch(error => ({
            selector,
            family: style.fontFamily.split(',')[0].trim().replace(/^["']|["']$/g, ''),
            font: style.font,
            faces: [],
            load_error: error.message.split('\n')[0],
          }));
      });
      window.__responsiveQaUniversePageMapFontLoads = Promise.all(loadPromises);
      return elements.map(({ selector, element }) => ({
        selector,
        text: element.textContent.trim().replace(/\s+/g, ' ').slice(0, 80),
      }));
    });

    const fontAssetResponses = fontResponses.filter(response =>
      response.resource_type === 'font'
    );
    const fontRequestObserved = await Promise.race([
      fontRequestSeen,
      fontFailureSeen.then(() => false),
    ]);
    if (!fontRequestObserved || fontRequests.length === 0) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT BASELINE INVALID: no held web-font request was observed; ` +
        `requests=${JSON.stringify(fontRequests)}`
      );
    }
    if (fontAssetResponses.length > 0) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT BASELINE INVALID: a font asset responded before the fallback capture; ` +
        `responses=${JSON.stringify(fontAssetResponses)}`
      );
    }

    const panBeforeFontLoad = await Promise.all(targets.map((target, index) =>
      target.evaluate((group, groupIndex) => {
        const scrollWrap = group.querySelector('.mermaid-scroll-wrap');
        if (!scrollWrap) {
          return {
            group_index: groupIndex + 1,
            pan_supported: false,
            can_pan: false,
            scroll_left: null,
            max_scroll_left: null,
          };
        }
        const maxScrollLeft = scrollWrap.scrollWidth - scrollWrap.clientWidth;
        if (maxScrollLeft <= 1) {
          return {
            group_index: groupIndex + 1,
            pan_supported: false,
            can_pan: false,
            scroll_left: scrollWrap.scrollLeft,
            max_scroll_left: maxScrollLeft,
          };
        }
        scrollWrap.scrollLeft = Math.max(1, Math.round(maxScrollLeft / 2));
        return {
          group_index: groupIndex + 1,
          pan_supported: true,
          can_pan: scrollWrap.scrollLeft > 0,
          scroll_left: scrollWrap.scrollLeft,
          max_scroll_left: maxScrollLeft,
        };
      }, index)
    ));
    evidence.pan_before_font_load = panBeforeFontLoad;
    for (const pan of panBeforeFontLoad) {
      if (pan.pan_supported && !pan.can_pan) {
        errors.push(
          `UNIVERSE PAGE MAP WEB FONT PAN BASELINE UNAVAILABLE: group ${pan.group_index} ` +
          `could not be panned before font loading; ${JSON.stringify(pan)}`
        );
      }
    }
    await waitForTwoFrames();

    const beforeGroups = [];
    for (const groupIndex of groupIndexes) {
      beforeGroups.push(await captureUniversePageMapGeometry(
        page,
        groupIndex,
        expectedOpenGroupIndexes
      ));
    }
    const before = beforeGroups[0] ?? null;
    evidence.groups_before = beforeGroups.map((geometry, index) => ({
      group_index: groupIndexes[index] + 1,
      geometry,
    }));
    evidence.before = groupIndexes.length === 1
      ? before
      : { groups: evidence.groups_before };
    evidence.font_request_count_before_baseline = fontRequests.length;
    evidence.font_responses_before_baseline = [...fontResponses];
    evidence.fallback_font_state = await page.evaluate(() => ({
      status: document.fonts.status,
      reservation_widths: [...document.querySelectorAll(
        '[data-universe-font-reservation="true"]'
      )].map(element => Number(element.dataset.universeFontReservationWidth)),
    }));
    evidence.mermaid_group_readiness_before_font_capture = beforeGroups.map(
      (geometry, index) => ({
        group_index: groupIndexes[index] + 1,
        svg_ready: Boolean(geometry?.svg_ready),
      })
    );
    evidence.mermaid_svg_ready_before_font_capture =
      beforeGroups.length > 0 && beforeGroups.every(geometry => geometry?.svg_ready);
    baselineCaptured = opened && beforeGroups.length === groupIndexes.length &&
      beforeGroups.every(geometry =>
        geometry?.open && geometry?.only_expected_groups_open &&
        geometry?.svg_ready && geometry?.geometry?.reserved_shell
      );
    for (let index = 0; index < groupIndexes.length; index += 1) {
      const groupIndex = groupIndexes[index];
      const groupBefore = beforeGroups[index];
      const hasPrevious = groupIndex > 0;
      const hasNext = groupIndex < groupCount - 1;
      if (!groupBefore?.open || !groupBefore?.only_expected_groups_open ||
          !groupBefore?.svg_ready || !groupBefore?.geometry?.reserved_shell ||
          Boolean(groupBefore?.geometry?.previous_group_summary) !== hasPrevious ||
          Boolean(groupBefore?.geometry?.next_group_summary) !== hasNext) {
        baselineCaptured = false;
        errors.push(
          `UNIVERSE PAGE MAP WEB FONT BASELINE UNAVAILABLE: expected an open, SVG-ready group ` +
          `with a reserved shell and only existing adjacent summaries; group=${groupIndex + 1}; ` +
          `before=${JSON.stringify(groupBefore)}`
        );
      }
    }
    if (!opened) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT BASELINE UNAVAILABLE: could not open every selected group; ` +
        `groups=${JSON.stringify(groupIndexes.map(index => index + 1))}; ` +
        `before=${JSON.stringify(beforeGroups)}`
      );
    }
    if (fontSamples.length === 0) {
      errors.push(
        'UNIVERSE PAGE MAP WEB FONT BASELINE INVALID: no visible page-map text samples were available'
      );
    }

    if (viewportWidth === 320 && baselineCaptured && fontRequestObserved &&
        fontAssetResponses.length === 0) {
      const fontPendingRotation = await checkUniversePageMapFontPendingRotation(
        page,
        groupIndexes,
        waitForTwoFrames
      );
      evidence.font_pending_rotation = fontPendingRotation.evidence;
      errors.push(...fontPendingRotation.errors);
    }

    releaseFontRequests();
    let loadedFontState = null;
    try {
      loadedFontState = await page.evaluate(async timeoutMs => {
        const waitWithTimeout = (promise, label) => {
          let timeoutId;
          return Promise.race([
            promise,
            new Promise((_, reject) => {
              timeoutId = setTimeout(
                () => reject(new Error(`${label} exceeded ${timeoutMs}ms`)),
                timeoutMs
              );
            }),
          ]).finally(() => clearTimeout(timeoutId));
        };
        const loads = await waitWithTimeout(
          window.__responsiveQaUniversePageMapFontLoads,
          'font loading'
        );
        await waitWithTimeout(document.fonts.ready, 'document.fonts.ready');
        await new Promise(resolve => requestAnimationFrame(() =>
          requestAnimationFrame(resolve)
        ));
        const faceStates = [...document.fonts].map(face => ({
          family: face.family.replace(/^["']|["']$/g, ''),
          status: face.status,
        }));
        return { status: document.fonts.status, loads, face_states: faceStates };
      }, UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS);
      evidence.loaded_font_state = loadedFontState;
    } catch (error) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONTS NOT READY: ${error.message.split('\n')[0]}`
      );
    }

    const afterGroups = [];
    for (const groupIndex of groupIndexes) {
      afterGroups.push(await captureUniversePageMapGeometry(
        page,
        groupIndex,
        expectedOpenGroupIndexes
      ));
    }
    const after = afterGroups[0] ?? null;
    evidence.groups_after = afterGroups.map((geometry, index) => ({
      group_index: groupIndexes[index] + 1,
      geometry,
    }));
    evidence.pan_after_font_load = afterGroups.map((geometry, index) => ({
      group_index: groupIndexes[index] + 1,
      retention_checked: Boolean(panBeforeFontLoad[index]?.can_pan),
      scroll_left: geometry?.scroll_left ?? null,
    }));
    evidence.after = groupIndexes.length === 1
      ? after
      : { groups: evidence.groups_after };
    evidence.mermaid_group_readiness_after_font_load = afterGroups.map(
      (geometry, index) => ({
        group_index: groupIndexes[index] + 1,
        svg_ready: Boolean(geometry?.svg_ready),
      })
    );
    evidence.mermaid_svg_ready_after_font_load =
      afterGroups.length > 0 && afterGroups.every(geometry => geometry?.svg_ready);
    const stylesheetResponses = fontResponses.filter(response =>
      response.resource_type === 'stylesheet'
    );
    const hasStylesheet = stylesheetResponses.some(response => response.status < 400);
    const hasFontAsset = fontResponses.some(response =>
      response.resource_type === 'font' && response.status >= 200 && response.status < 300
    );
    const requestedFontFamilies = new Set(
      (loadedFontState?.loads ?? []).map(sample => sample.family.toLowerCase())
    );
    const failedSampleFaces = loadedFontState?.loads?.filter(sample =>
      sample.load_error ||
      sample.faces.length === 0 ||
      sample.faces.some(face => face.status !== 'loaded' ||
        face.family.toLowerCase() !== sample.family.toLowerCase())
    ) ?? [];
    const failedRegisteredFaces = (loadedFontState?.face_states ?? [])
      .filter(face => face.status === 'error' &&
        requestedFontFamilies.has(face.family.toLowerCase()))
      .map(face => ({
        family: face.family,
        selector: 'document.fonts',
        faces: [{ family: face.family, status: face.status }],
      }));
    const failedFontFaces = [...failedSampleFaces, ...failedRegisteredFaces];
    const reportedDecodeFailures = new Set();
    for (const sample of failedFontFaces) {
      const key = `${sample.selector}:${sample.family}`;
      if (reportedDecodeFailures.has(key)) continue;
      reportedDecodeFailures.add(key);
      const failedFace = sample.faces.find(face =>
        face.status !== 'loaded' ||
        face.family.toLowerCase() !== sample.family.toLowerCase()
      );
      fontFailures.push({
        host: 'fonts.gstatic.com',
        resource_type: 'font',
        failure_kind: 'decode',
        family: sample.family,
        selector: sample.selector,
        error: sample.load_error ??
          (failedFace
            ? `browser reported font face ${failedFace.family} as ${failedFace.status}`
            : 'browser returned no usable font faces'),
      });
    }
    const fontLoadVerified = hasStylesheet && hasFontAsset &&
      loadedFontState?.status === 'loaded' && failedFontFaces.length === 0 &&
      fontFailures.length === 0;
    evidence.resource_failures = [...fontFailures];
    evidence.font_load_verified = fontLoadVerified;

    if (fontLoadVerified && baselineCaptured &&
        afterGroups.length === groupIndexes.length &&
        afterGroups.every(group => group?.open && group?.only_expected_groups_open && group?.svg_ready)) {
      for (let index = 0; index < groupIndexes.length; index += 1) {
        const groupIndex = groupIndexes[index];
        const beforeGeometry = beforeGroups[index]?.geometry ?? {};
        const afterGeometry = afterGroups[index]?.geometry ?? {};
        const beforeScrollLeft = beforeGroups[index]?.scroll_left;
        const afterScrollLeft = afterGroups[index]?.scroll_left;
        if (panBeforeFontLoad[index]?.can_pan &&
            (beforeScrollLeft === null || afterScrollLeft === null ||
              Math.abs(afterScrollLeft - beforeScrollLeft) >
                UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX)) {
          errors.push(
            `UNIVERSE PAGE MAP WEB FONT PAN SHIFT: group ${groupIndex + 1} changed horizontal ` +
            `scroll position with ${UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
            `before=${JSON.stringify(beforeScrollLeft)}; after=${JSON.stringify(afterScrollLeft)}`
          );
        }
        const selectors = [
          'group',
          'summary',
          'figure',
          'reserved_shell',
          ...(groupIndex > 0 ? ['previous_group_summary'] : []),
          ...(groupIndex < groupCount - 1 ? ['next_group_summary'] : []),
        ];
        for (const selector of selectors) {
          const beforeRect = beforeGeometry[selector];
          const afterRect = afterGeometry[selector];
          if (!beforeRect || !afterRect) {
            errors.push(
              `UNIVERSE PAGE MAP WEB FONT GEOMETRY MISSING: group ${groupIndex + 1} ${selector}; ` +
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
          const changedProperties = Object.keys(delta).filter(property =>
            Math.abs(delta[property]) > UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX
          );
          if (changedProperties.length > 0) {
            const shift = {
              group_index: groupIndex + 1,
              selector,
              before: beforeRect,
              after: afterRect,
              delta,
              changed_properties: changedProperties,
            };
            evidence.shifts.push(shift);
            errors.push(
              `UNIVERSE PAGE MAP WEB FONT GEOMETRY SHIFT: group ${groupIndex + 1} ${selector} changed ` +
              `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
              `with ${UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
              `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
            );
          }
        }
      }
    } else {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT GEOMETRY COMPARISON UNAVAILABLE: verified font loading is required; ` +
        `before_svg_ready=${evidence.mermaid_svg_ready_before_font_capture}; ` +
        `after_svg_ready=${evidence.mermaid_svg_ready_after_font_load}; ` +
        `font_load_verified=${fontLoadVerified}; resource_failures=${JSON.stringify(fontFailures)}; ` +
        `before=${JSON.stringify(beforeGroups)}; after=${JSON.stringify(afterGroups)}`
      );
    }

    if (!hasStylesheet || !hasFontAsset || loadedFontState?.status !== 'loaded' ||
        failedFontFaces.length > 0 || fontFailures.length > 0) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONTS UNAVAILABLE: stylesheet_loaded=${hasStylesheet}; ` +
        `font_asset_loaded=${hasFontAsset}; status=${JSON.stringify(loadedFontState?.status ?? null)}; ` +
        `failed_faces=${JSON.stringify(failedFontFaces)}; ` +
        `resource_failures=${JSON.stringify(fontFailures)}; ` +
        `resources=${JSON.stringify(fontResponses)}`
      );
    }
    if (afterGroups.length !== groupIndexes.length ||
        afterGroups.some(group => !group?.only_expected_groups_open)) {
      errors.push(
        `UNIVERSE PAGE MAP WEB FONT COLLAPSED STATE INVALID: only selected groups should remain open during font measurement; ` +
        `after=${JSON.stringify(afterGroups)}`
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP WEB FONT GEOMETRY CHECK ERROR: ${error.message.split('\n')[0]}`
    );
  } finally {
    releaseFontRequests();
    evidence.resource_failures = [...fontFailures];
    if (targets.length > 0) {
      await Promise.all(targets.map(target =>
        target.evaluate(group => { group.open = false; }).catch(() => {})
      ));
      await waitForTwoFrames().catch(() => {});
    }
    evidence.collapsed_after_check = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    ).catch(() => false);
    if (!evidence.collapsed_after_check) {
      errors.push(
        'UNIVERSE PAGE MAP WEB FONT COLLAPSED STATE INVALID: generated groups were not restored to closed after font measurement'
      );
    }
  }

  return { errors, evidence };
}

async function checkUniversePageMapRotationReflow(page, waitForTwoFrames) {
  const errors = [];
  const originalViewport = page.viewportSize();
  const originalScroll = await page.evaluate(() => ({
    x: window.scrollX,
    y: window.scrollY,
  }));
  const portraitViewport = { width: 320, height: 740 };
  const landscapeViewport = { width: 740, height: 390 };
  const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
  const groupCount = await groups.count();
  const groupIndex = Math.floor(groupCount / 2);
  const target = groups.nth(groupIndex);
  const evidence = {
    tolerance_px: UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX,
    portrait_viewport: portraitViewport,
    landscape_viewport: landscapeViewport,
    group_index: groupCount > 0 ? groupIndex + 1 : null,
    svg_preserved: false,
    stages: [],
    collapsed_after_check: false,
    viewport_restored: false,
  };
  let opened = false;

  const captureStage = async (name, viewport) => {
    const stage = await page.evaluate(({ index, viewport }) => {
      const round = value => Math.round(value * 100) / 100;
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      const group = groups[index];
      if (!group) return { group_exists: false };
      const rect = element => {
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return {
          x: round(box.x),
          y: round(box.y),
          width: round(box.width),
          height: round(box.height),
          right: round(box.right),
          bottom: round(box.bottom),
        };
      };
      const visible = element => Boolean(
        element &&
        element.getClientRects().length > 0 &&
        getComputedStyle(element).display !== 'none' &&
        getComputedStyle(element).visibility !== 'hidden'
      );
      const textOverflow = element => {
        if (!element) return { horizontal_clipping: false, vertical_clipping: false };
        const style = getComputedStyle(element);
        return {
          horizontal_clipping:
            ['hidden', 'clip'].includes(style.overflowX) &&
            element.scrollWidth > element.clientWidth + 1,
          vertical_clipping:
            ['hidden', 'clip'].includes(style.overflowY) &&
            element.scrollHeight > element.clientHeight + 1,
        };
      };
      const shell = group.querySelector('.mermaid-scroll-wrap');
      const summary = group.querySelector('summary');
      const reservationElements = [...group.querySelectorAll('li, figcaption')]
        .filter(visible);
      return {
        group_exists: true,
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        expected_viewport_width: viewport.width,
        document_scroll_width: document.documentElement.scrollWidth,
        document_overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        open: group.open,
        svg_ready: Boolean(group.querySelector('svg .node')),
        svg_preserved: group.querySelector('svg') === window.__responsiveQaRotationSvg,
        shell: {
          rect: rect(shell),
          client_width: shell?.clientWidth ?? 0,
          scroll_width: shell?.scrollWidth ?? 0,
          overflow_x: shell ? getComputedStyle(shell).overflowX : null,
          min_height: shell ? getComputedStyle(shell).minHeight : null,
        },
        summary: {
          text: summary?.textContent.trim() ?? '',
          visible: visible(summary),
          rect: rect(summary),
          ...textOverflow(summary),
        },
        neighboring_summaries: [
          groups[index - 1]?.querySelector('summary'),
          groups[index + 1]?.querySelector('summary'),
        ].filter(Boolean).map(element => ({
          text: element.textContent.trim(),
          visible: visible(element),
          rect: rect(element),
          ...textOverflow(element),
        })),
        reservation_count: reservationElements.length,
        reservation_widths: reservationElements.map(element => ({
          selector: element.tagName.toLowerCase(),
          width: Number(element.dataset.universeFontReservationWidth ?? NaN),
          marked: element.dataset.universeFontReservation === 'true',
        })),
      };
    }, { index: groupIndex, viewport });
    evidence.stages.push({ name, ...stage });

    const prefix = `UNIVERSE PAGE MAP ROTATION ${name.toUpperCase()}`;
    if (!stage.group_exists || !stage.open || !stage.svg_ready) {
      errors.push(
        `${prefix} MAP UNAVAILABLE: expected the same open, rendered map; ` +
        `stage=${JSON.stringify(stage)}`
      );
      return;
    }
    if (stage.viewport_width !== viewport.width ||
        stage.expected_viewport_width !== viewport.width) {
      errors.push(
        `${prefix} VIEWPORT SIZE INVALID: expected ${viewport.width}px; ` +
        `stage=${JSON.stringify(stage)}`
      );
    }
    if (stage.document_overflow) {
      errors.push(
        `${prefix} PAGE OVERFLOW: document scroll width=${stage.document_scroll_width}px ` +
        `exceeds viewport=${stage.viewport_width}px`
      );
    }
    if (!stage.svg_preserved) {
      errors.push(`${prefix} MAP REPLACED: the open map's SVG changed during rotation`);
    }
    const shell = stage.shell;
    if (!shell?.rect || shell.rect.width <= 0 || shell.rect.x < -1 ||
        shell.rect.right > viewport.width + 1 || shell.rect.y < -1 ||
        shell.rect.bottom > viewport.height + 1 || shell.client_width <= 0 ||
        shell.scroll_width < shell.client_width ||
        !['auto', 'scroll'].includes(shell.overflow_x)) {
      errors.push(
        `${prefix} SHELL UNUSABLE: shell must remain visible within the viewport and locally scrollable; ` +
        `shell=${JSON.stringify(shell)}`
      );
    }
    const summaries = [stage.summary, ...stage.neighboring_summaries];
    if (summaries.some(item =>
      !item.visible || !item.text || !item.rect || item.rect.width <= 0 ||
      item.rect.x < -1 || item.rect.right > viewport.width + 1 ||
      item.horizontal_clipping || item.vertical_clipping
    ) || stage.summary.rect.y < -1 ||
        stage.summary.rect.bottom > viewport.height + 1) {
      errors.push(
        `${prefix} SUMMARY UNUSABLE: the open summary must remain visible in the viewport and adjacent summaries within its width; ` +
        `summaries=${JSON.stringify(summaries)}`
      );
    }
    if (stage.reservation_count === 0 ||
        stage.reservation_widths.some(item =>
          !item.marked || item.width !== viewport.width
        )) {
      errors.push(
        `${prefix} RESERVATION WIDTH NOT UPDATED: every visible open-map text reservation ` +
        `must match the current viewport width; reservations=${JSON.stringify(stage.reservation_widths)}`
      );
    }
  };

  try {
    if (originalViewport?.width !== portraitViewport.width || groupCount === 0) {
      return {
        errors: [
          'UNIVERSE PAGE MAP ROTATION CHECK UNAVAILABLE: expected a 320px portrait page with a generated map group',
        ],
        evidence,
      };
    }
    const allCollapsed = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    );
    if (!allCollapsed) {
      errors.push(
        'UNIVERSE PAGE MAP ROTATION BASELINE INVALID: expected all generated groups closed before opening the map'
      );
    }
    await target.locator('summary').focus();
    await page.keyboard.press('Enter');
    opened = await target.evaluate(group => group.open);
    if (!opened) {
      errors.push(
        `UNIVERSE PAGE MAP ROTATION OPEN FAILED: keyboard did not open group ${groupIndex + 1}`
      );
    }
    await target.locator('.mermaid-scroll-wrap').waitFor({ state: 'visible' });
    await page.waitForFunction(() => {
      const group = document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )[Math.floor(document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      ).length / 2)];
      return Boolean(group?.querySelector('svg .node'));
    }, undefined, { timeout: 15000 }).catch(() => {});
    await page.evaluate(() => {
      const index = Math.floor(document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      ).length / 2);
      window.__responsiveQaRotationSvg =
        document.querySelectorAll('.universe-map-generated .universe-map-group')[index]
          ?.querySelector('svg') ?? null;
    });
    await page.waitForFunction(() => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      const group = groups[Math.floor(groups.length / 2)];
      const items = [...(group?.querySelectorAll('li, figcaption') ?? [])]
        .filter(element => element.getClientRects().length);
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS });
    await waitForTwoFrames();
    await target.locator('.mermaid-scroll-wrap').scrollIntoViewIfNeeded();
    await captureStage('portrait_initial', portraitViewport);

    await page.setViewportSize(landscapeViewport);
    await page.waitForFunction(() => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      const group = groups[Math.floor(groups.length / 2)];
      const items = [...(group?.querySelectorAll('li, figcaption') ?? [])]
        .filter(element => element.getClientRects().length);
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS });
    await waitForTwoFrames();
    await target.locator('.mermaid-scroll-wrap').scrollIntoViewIfNeeded();
    await captureStage('landscape', landscapeViewport);

    await page.setViewportSize(portraitViewport);
    await page.waitForFunction(() => {
      const groups = [...document.querySelectorAll(
        '.universe-map-generated .universe-map-group'
      )];
      const group = groups[Math.floor(groups.length / 2)];
      const items = [...(group?.querySelectorAll('li, figcaption') ?? [])]
        .filter(element => element.getClientRects().length);
      return items.length > 0 && items.every(element =>
        element.dataset.universeFontReservation === 'true' &&
        Number(element.dataset.universeFontReservationWidth) === window.innerWidth
      );
    }, undefined, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS });
    await waitForTwoFrames();
    await target.locator('.mermaid-scroll-wrap').scrollIntoViewIfNeeded();
    await captureStage('portrait_return', portraitViewport);

    evidence.svg_preserved = evidence.stages.every(stage => stage.svg_preserved);
    const widths = evidence.stages.map(stage =>
      [...new Set(stage.reservation_widths?.map(item => item.width) ?? [])]
    );
    if (JSON.stringify(widths) !== JSON.stringify([[320], [740], [320]])) {
      errors.push(
        `UNIVERSE PAGE MAP ROTATION RESERVATION HISTORY INVALID: expected reservation widths ` +
        `320→740→320; observed=${JSON.stringify(widths)}`
      );
    }
  } catch (error) {
    errors.push(
      `UNIVERSE PAGE MAP ROTATION CHECK ERROR: ${error.message.split('\n')[0]}`
    );
  } finally {
    await target.evaluate(group => { group.open = false; }).catch(() => {});
    await waitForTwoFrames().catch(() => {});
    evidence.collapsed_after_check = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    ).catch(() => false);
    if (!evidence.collapsed_after_check) {
      errors.push(
        'UNIVERSE PAGE MAP ROTATION COLLAPSED STATE INVALID: generated groups were not closed after rotation checks'
      );
    }
    if (originalViewport) {
      await page.setViewportSize(originalViewport).catch(() => {});
      await waitForTwoFrames().catch(() => {});
      await page.evaluate(({ x, y }) => window.scrollTo(x, y), originalScroll).catch(() => {});
      await waitForTwoFrames().catch(() => {});
      evidence.viewport_restored =
        page.viewportSize()?.width === originalViewport.width &&
        page.viewportSize()?.height === originalViewport.height;
    }
    if (!evidence.viewport_restored) {
      errors.push(
        `UNIVERSE PAGE MAP ROTATION VIEWPORT NOT RESTORED: ` +
        `expected=${JSON.stringify(originalViewport)}; actual=${JSON.stringify(page.viewportSize())}`
      );
    }
    await page.evaluate(() => { delete window.__responsiveQaRotationSvg; }).catch(() => {});
  }

  return { errors, evidence };
}

async function checkBrandGuardHeroGeometry(page, releaseThemeStyles = () => {}) {
  const errors = [];
  const capture = async () => {
    const captured = await page.evaluate((selectors) => {
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
      const themeLink = document.querySelector('link[data-deferred-styles]') ??
        [...document.querySelectorAll('link[rel~="stylesheet"][href]')].find(link =>
          new URL(link.href).pathname === '/assets/css/theme.css'
        ) ?? null;
      return {
        themeMedia: themeLink?.media ?? null,
        themeSheetAttached: Boolean(themeLink?.sheet),
        themeHref: themeLink?.href ?? null,
        themeDeferred: themeLink?.hasAttribute('data-deferred-styles') ?? null,
        geometry,
      };
    }, BRANDGUARD_GEOMETRY_SELECTORS);
    captured.textLines = await page.evaluate(
      captureBrandGuardTextLineFragments,
      BRANDGUARD_TEXT_LINE_SELECTORS
    );
    return captured;
  };

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
  if (!before.themeHref) {
    releaseThemeStyles();
    errors.push(
      'BRANDGUARD HERO GEOMETRY BASELINE UNAVAILABLE: theme stylesheet link was not found; ' +
      `measured before=${JSON.stringify(before.geometry)}; after=not-measured`
    );
    return {
      errors,
      evidence: {
        threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
        deferred_theme: null,
        before: before.geometry,
        after: null,
        shifts: [],
        text_line_reflow: {
          comparison: 'unavailable-theme-stylesheet',
          before: before.textLines,
          after: null,
          reflows: [],
        },
      },
    };
  }
  if (before.themeDeferred === false) {
    const missingSelectors = BRANDGUARD_GEOMETRY_SELECTORS.filter(
      selector => !before.geometry[selector]
    );
    if (!before.themeSheetAttached || (before.themeMedia && before.themeMedia !== 'all')) {
      errors.push(
        `BRANDGUARD HERO GEOMETRY ACTIVE THEME UNAVAILABLE: ` +
        `media=${JSON.stringify(before.themeMedia)}, stylesheet_attached=${before.themeSheetAttached}`
      );
    }
    if (missingSelectors.length) {
      errors.push(
        `BRANDGUARD HERO GEOMETRY MISSING: active-theme selectors unavailable: ` +
        `${missingSelectors.join(', ')}`
      );
    }
    return {
      errors,
      evidence: {
        threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
        comparison: 'not-applicable-active-stylesheet',
        deferred_theme: {
          href: before.themeHref,
          deferred: false,
          media: before.themeMedia,
          stylesheet_attached: before.themeSheetAttached,
        },
        before: before.geometry,
        after: before.geometry,
        shifts: [],
        text_line_reflow: {
          comparison: 'not-applicable-active-stylesheet',
          before: before.textLines,
          after: before.textLines,
          reflows: [],
        },
      },
    };
  }
  if (before.themeMedia !== 'not all' && before.themeSheetAttached) {
    releaseThemeStyles();
    errors.push(
      `BRANDGUARD HERO GEOMETRY BASELINE UNAVAILABLE: link[data-deferred-styles] ` +
      `media=${JSON.stringify(before.themeMedia)}, stylesheet attached before capture; ` +
      `measured before=${JSON.stringify(before.geometry)}; after=not-measured`
    );
    return {
      errors,
      evidence: {
        threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
        deferred_theme: {
          href: before.themeHref,
          deferred: before.themeDeferred,
          media_before: before.themeMedia,
        },
        before: before.geometry,
        after: null,
        shifts: [],
        text_line_reflow: {
          comparison: 'unavailable-theme-baseline',
          before: before.textLines,
          after: null,
          reflows: [],
        },
      },
    };
  }

  // The request gate leaves a genuinely deferred theme unapplied until this
  // critical-only baseline exists.
  releaseThemeStyles();

  try {
    await page.waitForFunction(() => {
      const themeLink = document.querySelector('link[data-deferred-styles]') ??
        [...document.querySelectorAll('link[rel~="stylesheet"][href]')].find(link =>
          new URL(link.href).pathname === '/assets/css/theme.css'
        );
      if (!themeLink?.sheet) return false;
      return themeLink.hasAttribute('data-deferred-styles')
        ? themeLink.media === 'all'
        : !themeLink.media || themeLink.media === 'all';
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
          deferred: before.themeDeferred,
          media_before: before.themeMedia,
          media_after: after.themeMedia,
        },
        before: before.geometry,
        after: after.geometry,
        shifts: [],
        text_line_reflow: {
          comparison: 'theme-activation-failed',
          before: before.textLines,
          after: after.textLines,
          reflows: [],
        },
      },
    };
  }

  const after = await capture();
  const textLineReflow = compareBrandGuardTextLineReflow(
    before.textLines,
    after.textLines
  );
  errors.push(...textLineReflow.errors);
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
        deferred: before.themeDeferred,
        media_before: before.themeMedia,
        media_after: after.themeMedia,
      },
      before: before.geometry,
      after: after.geometry,
      shifts,
      text_line_reflow: textLineReflow.evidence,
    },
  };
}

async function checkBrandGuardHeaderThemeGeometry(
  page,
  baselineOnly = false,
  priorBaseline = null
) {
  const errors = [];
  const route = new URL(page.url()).pathname;
  const viewportWidth = page.viewportSize()?.width ?? null;
  const fail = (reason, before, after = null) => errors.push(
    `BRANDGUARD HEADER/NAV THEME GEOMETRY ${reason}: ` +
    `route=${route}; viewport_width_px=${viewportWidth}; ` +
    `before=${JSON.stringify(before)}; after=${JSON.stringify(after)}`
  );
  const capture = () => page.evaluate(() => {
    const round = value => Math.round(value * 100) / 100;
    const box = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [
          property,
          round(rect[property]),
        ])
      );
    };
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number.parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0;
    };
    const themeLink = document.querySelector('link[data-deferred-styles]');
    const header = document.querySelector('.askjamie-main .site-header');
    const nav = header?.querySelector('.primary-nav');
    const labels = [...(nav?.querySelectorAll('a[href]') ?? [])]
      .filter(visible)
      .map((link, index) => ({
        key: `${link.getAttribute('href') ?? ''}#${index}`,
        text: link.textContent.replace(/\s+/g, ' ').trim(),
        box: box(link),
      }));
    return {
      theme: themeLink
        ? {
            href: themeLink.href,
            deferred: themeLink.hasAttribute('data-deferred-styles'),
            media: themeLink.media,
            stylesheet_attached: Boolean(themeLink.sheet),
          }
        : null,
      menu_open: Boolean(
        header?.classList.contains('nav-open') &&
        header.querySelector('.nav-toggle')?.getAttribute('aria-expanded') === 'true' &&
        nav?.getAttribute('aria-hidden') !== 'true'
      ),
      header: box(header),
      logo: box(header?.querySelector('.logo')),
      toggle: box(header?.querySelector('.nav-toggle')),
      navigation: box(nav),
      navigation_scroll_width: nav?.scrollWidth ?? null,
      navigation_client_width: nav?.clientWidth ?? null,
      viewport_width: window.innerWidth,
      document_scroll_width: document.documentElement.scrollWidth,
      labels,
    };
  });
  const evidence = {
    route,
    viewport_width_px: viewportWidth,
    threshold_px: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
    measurement_scope: 'open-mobile-navigation-before-and-after-deferred-theme-activation',
    font_measurements_included: false,
    comparison_status: 'not-performed',
    theme_activation_status: priorBaseline
      ? 'released-by-hero-geometry'
      : 'not-started',
    before: priorBaseline?.evidence?.before ?? null,
    after: null,
    shifts: [],
  };
  const toggle = page.locator('.askjamie-main .site-header .nav-toggle');
  let menuWasOpened = priorBaseline?.menuWasOpened ?? false;
  try {
    const waitForSettledOpenMenu = () => page.waitForFunction(() => {
      const header = document.querySelector('.askjamie-main .site-header');
      const nav = header?.querySelector('.primary-nav');
      if (!header?.classList.contains('nav-open') ||
          header.querySelector('.nav-toggle')?.getAttribute('aria-expanded') !== 'true' ||
          nav?.getAttribute('aria-hidden') === 'true') {
        return false;
      }
      const firstVisibleLink = [...(nav?.querySelectorAll('a[href]') ?? [])]
        .find(link => {
          const rect = link.getBoundingClientRect();
          const style = getComputedStyle(link);
          return style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            rect.width > 0 &&
            rect.height > 0;
        });
      const transitionSettled = nav
        ? nav.getAnimations().every(animation => animation.playState !== 'running')
        : false;
      return Boolean(firstVisibleLink) && transitionSettled;
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    let before = priorBaseline?.evidence?.before ?? null;
    if (priorBaseline) {
      if (priorBaseline.evidence.comparison_status !== 'baseline-ready') {
        evidence.comparison_status = 'not-performed-baseline-unavailable';
        return { errors, evidence };
      }
      await waitForSettledOpenMenu();
    } else {
      const beforeTheme = await page.evaluate(() => {
        const link = document.querySelector('link[data-deferred-styles]');
        return link
          ? {
              href: link.href,
              deferred: link.hasAttribute('data-deferred-styles'),
              media: link.media,
              stylesheet_attached: Boolean(link.sheet),
            }
          : null;
      });
      if (
        !beforeTheme?.deferred ||
        beforeTheme.media === 'all' ||
        beforeTheme.stylesheet_attached
      ) {
        before = await capture();
        evidence.before = before;
        fail('BASELINE UNAVAILABLE', before);
        return { errors, evidence, menuWasOpened };
      }

      if (!await toggle.isVisible()) {
        before = await capture();
        evidence.before = before;
        fail('BASELINE UNAVAILABLE (mobile menu toggle is not visible)', before);
        return { errors, evidence, menuWasOpened };
      }
      await toggle.click();
      menuWasOpened = true;
      await waitForSettledOpenMenu();

      before = await capture();
      evidence.before = before;
      if (!before.menu_open || before.labels.length === 0) {
        fail('BASELINE UNAVAILABLE (open mobile navigation has no measurable links)', before);
        return { errors, evidence, menuWasOpened };
      }
      if (baselineOnly) {
        evidence.comparison_status = 'baseline-ready';
        return { errors, evidence, menuWasOpened };
      }
    }

    evidence.theme_activation_status = 'released';
    try {
      await page.waitForFunction(() => {
        const link = document.querySelector('link[data-deferred-styles]');
        return link?.media === 'all' && Boolean(link.sheet);
      }, undefined, { timeout: 15000 });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
      evidence.theme_activation_status = 'activated';
    } catch (error) {
      evidence.after = await capture();
      fail(
        `ACTIVATION FAILED (${error.message.split('\n')[0]})`,
        before,
        evidence.after
      );
      return { errors, evidence };
    }

    try {
      await waitForSettledOpenMenu();
    } catch (error) {
      evidence.after = await capture();
      evidence.comparison_status = 'not-performed-menu-unsettled';
      fail(
        `MENU DID NOT SETTLE AFTER THEME ACTIVATION (${error.message.split('\n')[0]})`,
        before,
        evidence.after
      );
      return { errors, evidence };
    }
    const after = await capture();
    evidence.after = after;
    if (!after.menu_open || after.labels.length === 0) {
      fail('MENU STATE LOST AFTER THEME ACTIVATION', before, after);
    }
    const fixedBoxes = ['header', 'logo', 'toggle', 'navigation'];
    const comparisons = fixedBoxes.map(name => ({
      name,
      before: before[name],
      after: after[name],
    }));
    const beforeLabels = new Map(before.labels.map(label => [label.key, label]));
    const afterLabels = new Map(after.labels.map(label => [label.key, label]));
    for (const [key, label] of beforeLabels) {
      const afterLabel = afterLabels.get(key);
      if (!afterLabel) {
        fail(`MENU LINK MISSING AFTER THEME ACTIVATION (${JSON.stringify(label.text)})`, before, after);
        continue;
      }
      comparisons.push({
        name: `navigation_link:${key}`,
        before: label.box,
        after: afterLabel.box,
      });
    }
    for (const [key, label] of afterLabels) {
      if (!beforeLabels.has(key)) {
        fail(`MENU LINK APPEARED ONLY AFTER THEME ACTIVATION (${JSON.stringify(label.text)})`, before, after);
      }
    }
    for (const comparison of comparisons) {
      if (!comparison.before || !comparison.after) {
        fail(
          `GEOMETRY MISSING (${comparison.name})`,
          comparison.before,
          comparison.after
        );
        continue;
      }
      const delta = Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [
          property,
          Math.round(
            (comparison.after[property] - comparison.before[property]) * 100
          ) / 100,
        ])
      );
      const changedProperties = Object.keys(delta).filter(property =>
        Math.abs(delta[property]) > BRANDGUARD_GEOMETRY_TOLERANCE_PX
      );
      if (changedProperties.length > 0) {
        const shift = {
          name: comparison.name,
          before: comparison.before,
          after: comparison.after,
          delta,
          changed_properties: changedProperties,
        };
        evidence.shifts.push(shift);
        fail(
          `SHIFT (${comparison.name}; ` +
          `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')})`,
          comparison.before,
          comparison.after
        );
      }
    }
    if (before.document_scroll_width !== after.document_scroll_width ||
        after.document_scroll_width > viewportWidth) {
      fail('DOCUMENT WIDTH CHANGED OR OVERFLOWS', before, after);
    }
    evidence.comparison_status = 'performed';
    return { errors, evidence };
  } catch (error) {
    evidence.after = await capture().catch(() => null);
    fail(
      `MEASUREMENT FAILED (${error.message.split('\n')[0]})`,
      evidence.before,
      evidence.after
    );
    return { errors, evidence, menuWasOpened };
  } finally {
    if (menuWasOpened && !baselineOnly) {
      try {
        if (await toggle.isVisible() &&
            await toggle.getAttribute('aria-expanded') === 'true') {
          await toggle.click();
          await page.waitForFunction(() =>
            document.querySelector(
              '.askjamie-main .site-header .nav-toggle'
            )?.getAttribute('aria-expanded') === 'false',
          undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
        }
      } catch (error) {
        errors.push(
          `BRANDGUARD HEADER/NAV THEME GEOMETRY MENU CLEANUP FAILED: ` +
          `route=${route}; viewport_width_px=${viewportWidth}; ` +
          `${error.message.split('\n')[0]}`
        );
      }
    }
  }
}

async function checkBrandGuardHeroKeyboardFocus(page, { forcedColors = false } = {}) {
  const errors = [];
  const errorPrefix = forcedColors
    ? 'BRANDGUARD 320PX HERO FORCED-COLORS KEYBOARD'
    : 'BRANDGUARD 320PX HERO KEYBOARD';
  const tabActionErrorPrefix = forcedColors
    ? `${errorPrefix} ACTION`
    : 'BRANDGUARD 320PX HERO ACTION';
  const evidence = {
    route: new URL(page.url()).pathname,
    viewport_width_px: page.viewportSize()?.width ?? null,
    selector: BRANDGUARD_HERO_ACTION_SELECTOR,
    navigation_method: 'keyboard Tab',
    color_mode: forcedColors ? 'forced-colors' : 'normal',
    forced_colors_active: await page.evaluate(
      () => matchMedia('(forced-colors: active)').matches
    ),
    expected_action_count: 0,
    tab_presses: 0,
    actions: [],
    missing_actions: [],
  };
  if (forcedColors && !evidence.forced_colors_active) {
    errors.push(
      `BRANDGUARD 320PX HERO FORCED-COLORS MODE UNAVAILABLE: ` +
      `browser did not activate the forced-colors media feature`
    );
  }
  const actions = await page.locator(BRANDGUARD_HERO_ACTION_SELECTOR).evaluateAll(
    elements => elements.map((element, index) => ({
      index,
      label: element.textContent.replace(/\s+/g, ' ').trim(),
      href: element.getAttribute('href') ?? '',
      visible: getComputedStyle(element).display !== 'none' &&
        getComputedStyle(element).visibility !== 'hidden' &&
        Number.parseFloat(getComputedStyle(element).opacity) > 0 &&
        element.getClientRects().length > 0,
    }))
  );
  evidence.expected_action_count = actions.length;
  if (actions.length === 0) {
    errors.push(
      `${errorPrefix} ACTIONS MISSING: ` +
      `selector=${BRANDGUARD_HERO_ACTION_SELECTOR}`
    );
    return { errors, evidence };
  }
  for (const action of actions) {
    if (!action.visible) {
      errors.push(
        `${errorPrefix} ACTION NOT VISIBLE: ` +
        `label=${JSON.stringify(action.label)}; href=${action.href}`
      );
    }
  }

  const focusedActionIndexes = new Set();
  const maximumTabPresses = Math.max(40, actions.length * 8);
  for (let press = 1;
    press <= maximumTabPresses && focusedActionIndexes.size < actions.length;
    press += 1) {
    await page.keyboard.press('Tab');
    evidence.tab_presses = press;
    const focused = await page.evaluate(({ selector, forcedColors }) => {
      const active = document.activeElement;
      if (!(active instanceof Element) || !active.matches(selector)) return null;

      const round = value => Math.round(value * 100) / 100;
      const rect = active.getBoundingClientRect();
      const style = getComputedStyle(active);
      const outlineWidth = Number.parseFloat(style.outlineWidth) || 0;
      const outlineOffset = Number.parseFloat(style.outlineOffset) || 0;
      const outlineVisible = style.outlineStyle !== 'none' &&
        outlineWidth > 0 &&
        style.outlineColor !== 'transparent' &&
        !/rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/.test(style.outlineColor);
      const ringExtent = Math.max(0, outlineWidth + outlineOffset);
      const ring = {
        left: rect.left - ringExtent,
        top: rect.top - ringExtent,
        right: rect.right + ringExtent,
        bottom: rect.bottom + ringExtent,
      };
      const clippingAncestors = [];
      const focusRingBackgroundLayers = [];
      let focusRingEffectiveOpacity = Number.parseFloat(style.opacity || '1');
      let canvasColor = null;
      for (let ancestor = active.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const ancestorStyle = getComputedStyle(ancestor);
        const ancestorRect = ancestor.getBoundingClientRect();
        if (forcedColors) {
          focusRingBackgroundLayers.push(ancestorStyle.backgroundColor);
          focusRingEffectiveOpacity *= Number.parseFloat(ancestorStyle.opacity || '1');
        }
        const clippedAxes = [];
        if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowX) &&
            (ring.left < ancestorRect.left - 0.5 ||
              ring.right > ancestorRect.right + 0.5)) {
          clippedAxes.push('horizontal');
        }
        if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowY) &&
            (ring.top < ancestorRect.top - 0.5 ||
              ring.bottom > ancestorRect.bottom + 0.5)) {
          clippedAxes.push('vertical');
        }
        if (ancestorStyle.clipPath !== 'none' ||
            ancestorStyle.contain.split(/\s+/).includes('paint')) {
          clippedAxes.push('clip-path-or-paint-containment');
        }
        if (clippedAxes.length > 0) {
          clippingAncestors.push({
            tag: ancestor.tagName.toLowerCase(),
            id: ancestor.id || null,
            class_name: typeof ancestor.className === 'string'
              ? ancestor.className
              : null,
            clipped_axes: [...new Set(clippedAxes)],
            bounds: {
              left: round(ancestorRect.left),
              top: round(ancestorRect.top),
              right: round(ancestorRect.right),
              bottom: round(ancestorRect.bottom),
            },
          });
        }
        if (ancestor === document.documentElement) break;
      }
      if (forcedColors) {
        const colorProbe = document.createElement('span');
        colorProbe.style.cssText =
          'position:fixed;left:-10000px;top:0;color:CanvasText;background-color:Canvas';
        document.documentElement.append(colorProbe);
        canvasColor = getComputedStyle(colorProbe).backgroundColor;
        colorProbe.remove();
      }
      const label = active.textContent.replace(/\s+/g, ' ').trim();
      const elementIndex = [...document.querySelectorAll(selector)].indexOf(active);
      return {
        index: elementIndex,
        label,
        href: active.getAttribute('href') ?? '',
        focused_with_keyboard: active.matches(':focus-visible'),
        outline_visible: outlineVisible,
        outline_style: style.outlineStyle,
        outline_width_px: round(outlineWidth),
        outline_offset_px: round(outlineOffset),
        outline_color: style.outlineColor,
        ...(forcedColors
          ? {
              focus_ring_background_layers: focusRingBackgroundLayers,
              focus_ring_effective_opacity: round(focusRingEffectiveOpacity),
              canvas_color: canvasColor,
            }
          : {}),
        target_bounds: {
          left: round(rect.left),
          top: round(rect.top),
          right: round(rect.right),
          bottom: round(rect.bottom),
        },
        focus_ring_bounds: Object.fromEntries(
          Object.entries(ring).map(([key, value]) => [key, round(value)])
        ),
        viewport_clipped: ring.left < -0.5 ||
          ring.top < -0.5 ||
          ring.right > window.innerWidth + 0.5 ||
          ring.bottom > window.innerHeight + 0.5,
        clipping_ancestors: clippingAncestors,
      };
    }, { selector: BRANDGUARD_HERO_ACTION_SELECTOR, forcedColors });

    if (!focused) continue;
    if (forcedColors) {
      focused.focus_indicator_contrast = calculateBrandGuardForcedColorsContrast({
        foregroundColor: focused.outline_color,
        backgroundLayers: focused.focus_ring_background_layers,
        canvasColor: focused.canvas_color,
        effectiveOpacity: focused.focus_ring_effective_opacity,
      });
      focused.focus_indicator_contrast.minimum_contrast_ratio = 3;
    }
    focusedActionIndexes.add(focused.index);
    if (evidence.actions.some(action => action.index === focused.index)) continue;
    evidence.actions.push(focused);
    if (!focused.focused_with_keyboard ||
        !focused.outline_visible ||
        focused.outline_width_px < 2) {
      errors.push(
        `${errorPrefix} FOCUS INDICATOR NOT VISIBLE: ` +
        `label=${JSON.stringify(focused.label)}; evidence=${JSON.stringify(focused)}`
      );
    }
    if (forcedColors &&
        (!focused.focus_indicator_contrast ||
          focused.focus_indicator_contrast.contrast_ratio === null ||
          focused.focus_indicator_contrast.contrast_ratio < 3)) {
      errors.push(
        `${errorPrefix} FOCUS INDICATOR LOW CONTRAST: ` +
        `label=${JSON.stringify(focused.label)}; evidence=${JSON.stringify(focused)}`
      );
    }
    if (focused.viewport_clipped || focused.clipping_ancestors.length > 0) {
      errors.push(
        `${errorPrefix} FOCUS INDICATOR CLIPPED: ` +
        `label=${JSON.stringify(focused.label)}; evidence=${JSON.stringify(focused)}`
      );
    }
  }

  evidence.missing_actions = actions
    .filter(action => !focusedActionIndexes.has(action.index))
    .map(({ index, label, href }) => ({ index, label, href }));
  for (const action of evidence.missing_actions) {
    errors.push(
      `${tabActionErrorPrefix} NOT REACHED BY KEYBOARD TAB: ` +
      `label=${JSON.stringify(action.label)}; href=${action.href}; ` +
      `tab_presses=${evidence.tab_presses}`
    );
  }
  return { errors, evidence };
}

async function dismissBrandGuardTransitionDialogWithKeyboard(page, route) {
  const errors = [];
  const evidence = {
    route,
    dismissal_method: 'Escape',
    dialog_open_before_dismissal: false,
    focus_returned_to_opener: false,
  };
  const dialog = page.locator('[data-transition-dialog]');
  const opener = page.locator('[data-transition-open]');
  try {
    evidence.dialog_open_before_dismissal = await dialog.evaluate(element => element.open);
    if (!evidence.dialog_open_before_dismissal) {
      errors.push(
        `BRANDGUARD TRANSITION DIALOG NOT OPEN FOR KEYBOARD DISMISSAL: route=${route}`
      );
      return { errors, evidence };
    }
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(
      () => document.activeElement === document.querySelector('[data-transition-open]')
    );
    evidence.focus_returned_to_opener =
      await opener.evaluate(element => document.activeElement === element);
    if (!evidence.focus_returned_to_opener) {
      errors.push(
        `BRANDGUARD TRANSITION DIALOG FOCUS NOT RESTORED: route=${route}; ` +
        'expected focus on [data-transition-open] after Escape'
      );
    }
  } catch (error) {
    errors.push(
      `BRANDGUARD TRANSITION DIALOG KEYBOARD DISMISSAL FAILED: route=${route}; ` +
      error.message.split('\n')[0]
    );
    evidence.failure = error.message.split('\n')[0];
  }
  return { errors, evidence };
}

async function checkBrandGuardHeroForcedColorsKeyboardFocus(
  browser,
  path,
  url,
  viewport,
  externalBlock
) {
  let context = null;
  const evidence = {
    route: path,
    viewport_width_px: viewport.width,
    color_mode: 'forced-colors',
    browser_context_isolated: true,
    navigation_method: 'keyboard Tab',
  };
  try {
    context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      colorScheme: 'light',
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    await page.emulateMedia({ forcedColors: 'active' });
    await page.route('**/*', route =>
      externalBlock.test(route.request().url())
        ? route.abort()
        : route.continue()
    );
    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
    });
    await page.waitForFunction(() => {
      const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => new URL(link.href).pathname === '/assets/css/theme.css');
      return Boolean(themeStylesheet?.sheet) &&
        (!themeStylesheet.hasAttribute('data-deferred-styles') ||
          themeStylesheet.media === 'all');
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    const hero = page.locator('.askjamie-main .askjamie-hero-inner');
    await hero.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => {
      const heroInner = document.querySelector(
        '.askjamie-main .askjamie-hero-inner'
      );
      return Boolean(heroInner) &&
        Number.parseFloat(getComputedStyle(heroInner).opacity) >= 0.999;
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ));
    });
    // Inspect the page behind its arrival note, just as the main viewport run
    // does; dedicated transition checks cover the open dialog independently.
    const transitionDismiss = page.locator(
      '[data-transition-dialog][open] [data-transition-dismiss]'
    );
    if (await transitionDismiss.isVisible()) await transitionDismiss.click();
    const result = await checkBrandGuardHeroKeyboardFocus(page, {
      forcedColors: true,
    });
    return {
      errors: result.errors,
      evidence: { ...evidence, ...result.evidence },
    };
  } catch (error) {
    return {
      errors: [
        `BRANDGUARD 320PX HERO FORCED-COLORS KEYBOARD FOCUS CHECK FAILED: ` +
        `route=${path}; ${error.message.split('\n')[0]}`,
      ],
      evidence: {
        ...evidence,
        check_failed: true,
        failure: error.message.split('\n')[0],
      },
    };
  } finally {
    await context?.close().catch(() => {});
  }
}

async function checkBrandGuardMobileNavigationKeyboard(page, route) {
  const keyboardPage = await page.context().newPage();
  try {
    await keyboardPage.goto(
      new URL(route, page.url()).toString(),
      {
        waitUntil: 'domcontentloaded',
        timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
      }
    );
    return await runBrandGuardMobileNavigationKeyboard(keyboardPage, route);
  } finally {
    await keyboardPage.close();
  }
}

async function runBrandGuardMobileNavigationKeyboard(page, route) {
  const errors = [];
  const evidence = {
    route,
    viewport_width_px: page.viewportSize()?.width ?? null,
    navigation_method: 'Enter, Tab, Space, and Escape',
    toggle_reached_with_tab: false,
    toggle_tab_presses: 0,
    preceding_header_control: null,
    menu_opened_with_enter: false,
    menu_closed_with_escape: false,
    menu_opened_with_space: false,
    menu_closed_with_space: false,
    visible_link_count: 0,
    links: [],
    tab_order_matches_dom_order: true,
    horizontal_overflow: [],
  };
  const viewportWidth = page.viewportSize()?.width ?? null;
  const prefix = `BRANDGUARD ${viewportWidth ?? 'UNKNOWN'}PX MOBILE NAV KEYBOARD`;
  const fail = message => errors.push(`${prefix}: route=${route}; ${message}`);
  await page.waitForFunction(() => {
    const dialog = document.querySelector('dialog[data-transition-dialog]');
    return !dialog || dialog.open || document.readyState === 'complete';
  });
  const transitionDismiss = page.locator(
    '[data-transition-dialog][open] [data-transition-dismiss]'
  );
  if (await transitionDismiss.isVisible()) await transitionDismiss.click();
  await page.waitForFunction(() =>
    !document.querySelector('[data-transition-dialog][open]')
  );
  try {
    await page.waitForFunction(() =>
      [...document.querySelectorAll('link[data-deferred-styles]')].every(link => {
        try {
          return link.media !== 'not all' && Boolean(link.sheet?.cssRules.length);
        } catch {
          return false;
        }
      }),
      undefined,
      { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS }
    );
  } catch (error) {
    fail(
      `deferred theme styles did not activate before keyboard checks; ` +
      `reason=${error.message.split('\n')[0]}`
    );
    return { errors, evidence };
  }
  const header = page.locator('.askjamie-main .site-header');
  const toggle = header.locator('.nav-toggle');
  const nav = header.locator('.primary-nav');

  const readMenuState = () => page.evaluate(() => {
    const headerElement = document.querySelector('.askjamie-main .site-header');
    const toggleElement = headerElement?.querySelector('.nav-toggle');
    const navElement = headerElement?.querySelector('.primary-nav');
    return {
      open: Boolean(
        headerElement?.classList.contains('nav-open') &&
        toggleElement?.getAttribute('aria-expanded') === 'true' &&
        navElement?.getAttribute('aria-hidden') !== 'true' &&
        !navElement?.hasAttribute('inert')
      ),
      closed: Boolean(
        !headerElement?.classList.contains('nav-open') &&
        toggleElement?.getAttribute('aria-expanded') === 'false' &&
        navElement?.getAttribute('aria-hidden') === 'true' &&
        navElement?.hasAttribute('inert')
      ),
      focus_is_toggle: document.activeElement === toggleElement,
      focus_is_first_link: document.activeElement === navElement?.querySelector('a[href]'),
    };
  });
  const waitForMenu = async (open, requireFirstLink = false) => {
    try {
      await page.waitForFunction(({ open: expectedOpen, requireFirstLink: requireFirst }) => {
        const headerElement = document.querySelector('.askjamie-main .site-header');
        const toggleElement = headerElement?.querySelector('.nav-toggle');
        const navElement = headerElement?.querySelector('.primary-nav');
        const isOpen = Boolean(
          headerElement?.classList.contains('nav-open') &&
          toggleElement?.getAttribute('aria-expanded') === 'true' &&
          navElement?.getAttribute('aria-hidden') !== 'true' &&
          !navElement?.hasAttribute('inert')
        );
        const isClosed = Boolean(
          !headerElement?.classList.contains('nav-open') &&
          toggleElement?.getAttribute('aria-expanded') === 'false' &&
          navElement?.getAttribute('aria-hidden') === 'true' &&
          navElement?.hasAttribute('inert')
        );
        const transform = navElement ? getComputedStyle(navElement).transform : null;
        const settled = transform === 'none' || transform === 'matrix(1, 0, 0, 1, 0, 0)';
        return expectedOpen
          ? isOpen && settled &&
              (!requireFirst || document.activeElement === navElement?.querySelector('a[href]'))
          : isClosed;
      }, { open, requireFirstLink }, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
      return true;
    } catch (error) {
      fail(
        `menu did not ${open ? 'open' : 'close'}; state=${JSON.stringify(await readMenuState())}; ` +
        `reason=${error.message.split('\n')[0]}`
      );
      return false;
    }
  };

  if (![320, 355, 365].includes(viewportWidth)) {
    fail(
      `unsupported viewport width; expected=320px, 355px, or 365px; ` +
      `observed=${viewportWidth ?? 'unknown'}`
    );
    return { errors, evidence };
  }
  if (!await toggle.isVisible()) {
    fail('mobile menu toggle is not visible');
    return { errors, evidence };
  }
  const headerFocusables = header.locator('a[href], button, [tabindex]');
  const precedingControl = await headerFocusables.evaluateAll(elements => {
    const toggleElement = document.querySelector('.askjamie-main .site-header .nav-toggle');
    const candidates = elements.map((element, index) => ({ element, index }))
      .filter(({ element }) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return element !== toggleElement &&
          !element.closest('.primary-nav') &&
          element.tabIndex >= 0 &&
          !element.disabled &&
          !element.closest('[inert]') &&
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          Number.parseFloat(style.opacity) > 0 &&
          rect.width > 0 &&
          rect.height > 0 &&
          Boolean(element.compareDocumentPosition(toggleElement) & Node.DOCUMENT_POSITION_FOLLOWING);
      });
    const previous = candidates.at(-1);
    if (!previous) return null;
    return {
      index: previous.index,
      label: previous.element.getAttribute('aria-label') ||
        previous.element.innerText?.replace(/\s+/g, ' ').trim() ||
        previous.element.getAttribute('href') ||
        previous.element.className,
    };
  });
  if (!precedingControl) {
    fail('no visible, keyboard-focusable header control precedes the mobile menu toggle');
    return { errors, evidence };
  }
  evidence.preceding_header_control = precedingControl.label;
  await headerFocusables.nth(precedingControl.index).focus();
  await page.keyboard.press('Tab');
  evidence.toggle_tab_presses = 1;
  evidence.toggle_reached_with_tab = await page.evaluate(() =>
    document.activeElement ===
      document.querySelector('.askjamie-main .site-header .nav-toggle')
  );
  if (!evidence.toggle_reached_with_tab) {
    fail(
      `mobile menu toggle was not reached from its preceding header control by Tab; ` +
      `preceding=${JSON.stringify(precedingControl)}; state=${JSON.stringify(await readMenuState())}`
    );
    return { errors, evidence };
  }
  const toggleFocus = await toggle.evaluate(element => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const outlineWidth = Number.parseFloat(style.outlineWidth) || 0;
    const outlineOffset = Number.parseFloat(style.outlineOffset) || 0;
    const ringExtent = Math.max(0, outlineWidth + outlineOffset);
    const ring = {
      left: rect.left - ringExtent,
      top: rect.top - ringExtent,
      right: rect.right + ringExtent,
      bottom: rect.bottom + ringExtent,
    };
    const clippingAncestors = [];
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const ancestorStyle = getComputedStyle(ancestor);
      const bounds = ancestor.getBoundingClientRect();
      const clippedAxes = [];
      if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowX) &&
          (ring.left < bounds.left - 0.5 || ring.right > bounds.right + 0.5)) {
        clippedAxes.push('horizontal');
      }
      if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowY) &&
          (ring.top < bounds.top - 0.5 || ring.bottom > bounds.bottom + 0.5)) {
        clippedAxes.push('vertical');
      }
      if (ancestorStyle.clipPath !== 'none' ||
          ancestorStyle.contain.split(/\s+/).includes('paint')) {
        clippedAxes.push('clip-path-or-paint-containment');
      }
      if (clippedAxes.length > 0) {
        clippingAncestors.push({
          tag: ancestor.tagName.toLowerCase(),
          id: ancestor.id || null,
          clipped_axes: [...new Set(clippedAxes)],
        });
      }
    }
    const outlineVisible = style.outlineStyle !== 'none' &&
      outlineWidth >= 2 &&
      style.outlineColor !== 'transparent' &&
      !/rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/.test(style.outlineColor);
    return {
      focused: document.activeElement === element,
      focused_with_keyboard: element.matches(':focus-visible'),
      outline_visible: outlineVisible,
      outline_width_px: outlineWidth,
      outline_color: style.outlineColor,
      viewport_clipped: ring.left < -0.5 ||
        ring.top < -0.5 ||
        ring.right > window.innerWidth + 0.5 ||
        ring.bottom > window.innerHeight + 0.5,
      clipping_ancestors: clippingAncestors,
      focus_ring_bounds: ring,
    };
  });
  evidence.toggle_focus = toggleFocus;
  if (!toggleFocus.focused || !toggleFocus.focused_with_keyboard ||
      !toggleFocus.outline_visible || toggleFocus.viewport_clipped ||
      toggleFocus.clipping_ancestors.length > 0) {
    fail(
      `menu toggle focus indicator is not visible after keyboard Tab; ` +
      `evidence=${JSON.stringify(toggleFocus)}`
    );
  }

  await page.keyboard.press('Enter');
  evidence.menu_opened_with_enter = await waitForMenu(true, true);
  const visibleLinks = await nav.locator('a[href]').evaluateAll(anchors =>
    anchors.filter(anchor => {
      const style = getComputedStyle(anchor);
      const rect = anchor.getBoundingClientRect();
      return style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number.parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0;
    }).map(anchor => ({
      label: anchor.textContent.replace(/\s+/g, ' ').trim(),
      href: anchor.getAttribute('href') ?? '',
    }))
  );
  evidence.visible_link_count = visibleLinks.length;
  if (visibleLinks.length === 0) {
    fail('no visible navigation links are available to test after opening the menu');
  }

  for (let index = 0; index < visibleLinks.length; index += 1) {
    if (index > 0) await page.keyboard.press('Tab');
    const focused = await nav.locator('a[href]').evaluateAll((anchors, expectedIndex) => {
      const visible = anchors.filter(anchor => {
        const style = getComputedStyle(anchor);
        const rect = anchor.getBoundingClientRect();
        return style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          Number.parseFloat(style.opacity) > 0 &&
          rect.width > 0 &&
          rect.height > 0;
      });
      const active = document.activeElement;
      const expected = visible[expectedIndex];
      const style = active instanceof Element ? getComputedStyle(active) : null;
      const rect = active?.getBoundingClientRect();
      const outlineWidth = Number.parseFloat(style?.outlineWidth ?? '0') || 0;
      const outlineOffset = Number.parseFloat(style?.outlineOffset ?? '0') || 0;
      const outlineColor = style?.outlineColor ?? 'transparent';
      const ringVisible = style?.outlineStyle !== 'none' &&
        outlineWidth >= 2 &&
        outlineColor !== 'transparent' &&
        !/rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/.test(outlineColor);
      const ringExtent = Math.max(0, outlineWidth + outlineOffset);
      const ring = rect ? {
        left: rect.left - ringExtent,
        top: rect.top - ringExtent,
        right: rect.right + ringExtent,
        bottom: rect.bottom + ringExtent,
      } : null;
      const clippingAncestors = [];
      if (active instanceof Element && ring) {
        for (let ancestor = active.parentElement; ancestor; ancestor = ancestor.parentElement) {
          const ancestorStyle = getComputedStyle(ancestor);
          const bounds = ancestor.getBoundingClientRect();
          const clippedAxes = [];
          if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowX) &&
              (ring.left < bounds.left - 0.5 || ring.right > bounds.right + 0.5)) {
            clippedAxes.push('horizontal');
          }
          if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowY) &&
              (ring.top < bounds.top - 0.5 || ring.bottom > bounds.bottom + 0.5)) {
            clippedAxes.push('vertical');
          }
          if (ancestorStyle.clipPath !== 'none' ||
              ancestorStyle.contain.split(/\s+/).includes('paint')) {
            clippedAxes.push('clip-path-or-paint-containment');
          }
          if (clippedAxes.length > 0) {
            clippingAncestors.push({
              tag: ancestor.tagName.toLowerCase(),
              id: ancestor.id || null,
              clipped_axes: [...new Set(clippedAxes)],
            });
          }
        }
      }
      const round = value => Math.round(value * 100) / 100;
      const navElement = active?.closest('.primary-nav');
      return {
        index: visible.indexOf(active),
        expected_index: expectedIndex,
        is_expected_link: active === expected,
        label: active?.textContent.replace(/\s+/g, ' ').trim() ?? '',
        href: active?.getAttribute('href') ?? '',
        focused_with_keyboard: active instanceof Element && active.matches(':focus-visible'),
        outline_visible: ringVisible,
        outline_width_px: round(outlineWidth),
        outline_color: outlineColor,
        focus_ring_bounds: ring
          ? Object.fromEntries(Object.entries(ring).map(([key, value]) => [key, round(value)]))
          : null,
        viewport_clipped: !ring ||
          ring.left < -0.5 ||
          ring.top < -0.5 ||
          ring.right > window.innerWidth + 0.5 ||
          ring.bottom > window.innerHeight + 0.5,
        clipping_ancestors: clippingAncestors,
        navigation_scroll_width: navElement?.scrollWidth ?? null,
        navigation_client_width: navElement?.clientWidth ?? null,
        document_scroll_width: document.documentElement.scrollWidth,
        viewport_width: window.innerWidth,
      };
    }, index);
    evidence.links.push(focused);
    if (!focused.is_expected_link) {
      evidence.tab_order_matches_dom_order = false;
      fail(
        `visible link was skipped or reached out of order; expected=${JSON.stringify(visibleLinks[index])}; ` +
        `focused=${JSON.stringify(focused)}`
      );
    }
    if (!focused.focused_with_keyboard || !focused.outline_visible) {
      fail(
        `focus indicator is not visible on ${JSON.stringify(focused.label)}; ` +
        `evidence=${JSON.stringify(focused)}`
      );
    }
    if (focused.viewport_clipped || focused.clipping_ancestors.length > 0) {
      fail(
        `focus indicator is clipped on ${JSON.stringify(focused.label)}; ` +
        `evidence=${JSON.stringify(focused)}`
      );
    }
    if (focused.navigation_scroll_width > focused.navigation_client_width + 1 ||
        focused.document_scroll_width > focused.viewport_width + 1) {
      evidence.horizontal_overflow.push(focused);
      fail(`horizontal overflow while focusing ${JSON.stringify(focused.label)}`);
    }
  }

  await page.keyboard.press('Escape');
  evidence.menu_closed_with_escape = await waitForMenu(false);
  const focusAfterEscape = await readMenuState();
  if (focusAfterEscape.closed && !focusAfterEscape.focus_is_toggle) {
    fail(`Escape closed the menu without returning focus to the toggle; state=${JSON.stringify(focusAfterEscape)}`);
  }

  await page.keyboard.press('Space');
  evidence.menu_opened_with_space = await waitForMenu(true, true);
  await toggle.focus();
  await page.keyboard.press('Space');
  evidence.menu_closed_with_space = await waitForMenu(false);
  const finalState = await readMenuState();
  if (finalState.closed && !finalState.focus_is_toggle) {
    fail(`Space closed the menu without leaving focus on the toggle; state=${JSON.stringify(finalState)}`);
  }
  return { errors, evidence };
}

async function checkBrandGuardMobileMenuDestinations(page) {
  const errors = [];
  const context = page.context();
  const expectedOrigin = new URL(page.url()).origin;
  const sourceRoutes = [...BRANDGUARD_MOBILE_MENU_DESTINATION_PATHS];
  const sourcePages = [];
  const destinations = new Map();
  const evidence = {
    viewport_width_px: page.viewportSize()?.width ?? null,
    distinct_destination_count: 0,
    source_routes: [],
    destinations: [],
  };
  const prefix = 'BRANDGUARD MOBILE MENU DESTINATION';
  const fail = message => errors.push(`${prefix}: ${message}`);

  try {
    if (evidence.viewport_width_px !== 320) {
      fail(`expected a 320px viewport; observed=${evidence.viewport_width_px}`);
      return { errors, evidence };
    }

    for (const sourceRoute of sourceRoutes) {
      const sourcePage = await context.newPage();
      sourcePages.push(sourcePage);
      const routeEvidence = {
        source_route: sourceRoute,
        menu_opened: false,
        links: [],
        error: null,
      };
      evidence.source_routes.push(routeEvidence);
      try {
        await sourcePage.goto(
          new URL(sourceRoute, page.url()).toString(),
          {
            waitUntil: 'domcontentloaded',
            timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
          }
        );
        await sourcePage.waitForFunction(() => {
          const dialog = document.querySelector('dialog[data-transition-dialog]');
          return !dialog || dialog.open || document.readyState === 'complete';
        });
        const transitionDismiss = sourcePage.locator(
          '[data-transition-dialog][open] [data-transition-dismiss]'
        );
        if (await transitionDismiss.isVisible()) await transitionDismiss.click();
        await sourcePage.waitForFunction(() =>
          !document.querySelector('[data-transition-dialog][open]')
        );

        const header = sourcePage.locator('.askjamie-main .site-header');
        const toggle = header.locator('.nav-toggle');
        const nav = header.locator('.primary-nav');
        if (!await toggle.isVisible()) {
          throw new Error('mobile menu toggle is not visible');
        }
        await toggle.click();
        await sourcePage.waitForFunction(() => {
          const headerElement = document.querySelector(
            '.askjamie-main .site-header'
          );
          const toggleElement = headerElement?.querySelector('.nav-toggle');
          const navElement = headerElement?.querySelector('.primary-nav');
          const transform = navElement
            ? getComputedStyle(navElement).transform
            : null;
          return Boolean(
            headerElement?.classList.contains('nav-open') &&
            toggleElement?.getAttribute('aria-expanded') === 'true' &&
            navElement?.getAttribute('aria-hidden') !== 'true' &&
            !navElement?.hasAttribute('inert') &&
            (transform === 'none' ||
              transform === 'matrix(1, 0, 0, 1, 0, 0)')
          );
        }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
        routeEvidence.menu_opened = true;

        const visibleLinks = await nav.locator('a[href]').evaluateAll(anchors =>
          anchors.map((anchor, index) => ({ anchor, index }))
            .filter(({ anchor }) => {
              const style = getComputedStyle(anchor);
              const rect = anchor.getBoundingClientRect();
              return style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                Number.parseFloat(style.opacity) > 0 &&
                rect.width > 0 &&
                rect.height > 0;
            }).map(({ anchor, index }) => ({
              label: anchor.textContent.replace(/\s+/g, ' ').trim(),
              href: anchor.href,
              raw_href: anchor.getAttribute('href') ?? '',
              dom_index: index,
            }))
        );
        if (visibleLinks.length === 0) {
          throw new Error('open mobile menu has no visible links');
        }
        for (let index = 0; index < visibleLinks.length; index += 1) {
          const link = visibleLinks[index];
          let destination;
          try {
            destination = new URL(link.href, sourcePage.url());
          } catch {
            routeEvidence.links.push({
              label: link.label,
              expected_destination: link.raw_href || null,
              result: 'failed',
              error: 'invalid destination URL',
            });
            fail(
              `source_route=${sourceRoute}; label=${JSON.stringify(link.label)}; ` +
              `expected_destination=${JSON.stringify(link.raw_href)}; ` +
              'invalid destination URL'
            );
            continue;
          }

          const expectedDestination =
            `${destination.pathname}${destination.search}${destination.hash}`;
          const linkEvidence = {
            label: link.label || '(untitled link)',
            expected_destination: expectedDestination,
            destination_url: destination.href,
          };
          routeEvidence.links.push(linkEvidence);

          if (destination.origin !== expectedOrigin) {
            linkEvidence.result = 'failed';
            linkEvidence.reached_destination = null;
            linkEvidence.error = `destination is not local to ${expectedOrigin}`;
            fail(
              `source_route=${sourceRoute}; label=${JSON.stringify(linkEvidence.label)}; ` +
              `expected_destination=${JSON.stringify(expectedDestination)}; ` +
              linkEvidence.error
            );
            continue;
          }

          if (!destinations.has(destination.href)) {
            destinations.set(destination.href, {
              source_route: sourceRoute,
              label: linkEvidence.label,
              expected_destination: expectedDestination,
              destination_url: destination.href,
              source_page: sourcePage,
              link_index: link.dom_index,
            });
          }
        }
      } catch (error) {
        routeEvidence.error = error.message.split('\n')[0];
        fail(
          `source_route=${sourceRoute}; could not inspect the open menu: ` +
          routeEvidence.error
        );
      }
    }

    for (const target of destinations.values()) {
      const result = {
        source_route: target.source_route,
        label: target.label,
        expected_destination: target.expected_destination,
        reached_destination: null,
        http_status: null,
        fragment_found: null,
        not_found_page: false,
        result: 'failed',
        error: null,
      };
      let destinationPage;
      let response = null;
      const expectedUrl = new URL(target.destination_url);
      const expectedRequestUrl = new URL(expectedUrl);
      expectedRequestUrl.hash = '';
      const onResponse = candidate => {
        const request = candidate.request();
        if (!request.isNavigationRequest()) return;
        let initialRequest = request;
        while (initialRequest.redirectedFrom()) {
          initialRequest = initialRequest.redirectedFrom();
        }
        try {
          const initialUrl = new URL(initialRequest.url());
          initialUrl.hash = '';
          if (initialUrl.href === expectedRequestUrl.href) response = candidate;
        } catch {
          // Ignore non-URL navigation response events.
        }
      };
      context.on('response', onResponse);
      try {
        const sourcePage = target.source_page;
        const link = sourcePage.locator(
          '.askjamie-main .site-header .primary-nav a[href]'
        )
          .nth(target.link_index);
        [destinationPage] = await Promise.all([
          context.waitForEvent('page', { timeout: 10000 }),
          link.click({ modifiers: ['ControlOrMeta'], timeout: 5000 }),
        ]);
        await destinationPage.waitForURL(
          reachedUrl =>
            reachedUrl.origin === expectedOrigin &&
            reachedUrl.pathname === expectedUrl.pathname &&
            reachedUrl.search === expectedUrl.search &&
            reachedUrl.hash === expectedUrl.hash,
          {
            waitUntil: 'domcontentloaded',
            timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
          }
        );
        await destinationPage.waitForLoadState('domcontentloaded', {
          timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
        });
        const reachedUrl = new URL(destinationPage.url());
        const reachedDocument = await destinationPage.evaluate(fragment => {
          const visible = element =>
            element.getClientRects().length > 0 &&
            getComputedStyle(element).visibility !== 'hidden' &&
            getComputedStyle(element).display !== 'none';
          let fragmentFound = true;
          if (fragment) {
            let id = fragment.slice(1);
            try {
              id = decodeURIComponent(id);
            } catch {
              // Retain the literal fragment when it is not valid percent encoding.
            }
            fragmentFound = Boolean(
              document.getElementById(id) ||
              [...document.getElementsByName(id)].some(visible)
            );
          }
          return {
            title: document.title,
            heading: document.querySelector('main h1, h1')
              ?.textContent.trim() ?? '',
            bodyMessages: [...document.querySelectorAll(
              'main p, main [role="alert"], main [aria-live]'
            )].filter(visible).map(element => element.textContent.trim()),
            fragmentFound,
          };
        }, expectedUrl.hash);
        result.reached_destination =
          `${reachedUrl.pathname}${reachedUrl.search}${reachedUrl.hash}`;
        result.http_status = response?.status() ?? null;
        result.fragment_found = reachedDocument.fragmentFound;
        result.not_found_page = isNotFoundDocument(
          reachedDocument.title,
          reachedDocument.heading,
          reachedDocument.bodyMessages
        );
        const reachedExpectedDestination =
          reachedUrl.origin === expectedOrigin &&
          reachedUrl.pathname === expectedUrl.pathname &&
          reachedUrl.search === expectedUrl.search &&
          reachedUrl.hash === expectedUrl.hash;
        const successfulResponse = Boolean(
          response && response.status() < 400
        );
        const validFragment = !expectedUrl.hash || result.fragment_found;
        if (
          reachedExpectedDestination &&
          successfulResponse &&
          validFragment &&
          !result.not_found_page
        ) {
          result.result = 'passed';
        } else if (!reachedExpectedDestination) {
          result.error = 'navigation did not reach the expected local destination';
        } else if (!successfulResponse) {
          result.error = 'destination response was unsuccessful or unavailable';
        } else if (!validFragment) {
          result.error = 'destination fragment was not found';
        } else if (result.not_found_page) {
          result.error = 'destination rendered a not-found page';
        }
      } catch (error) {
        try {
          const reachedUrl = new URL(destinationPage?.url() ?? '');
          result.reached_destination =
            `${reachedUrl.pathname}${reachedUrl.search}${reachedUrl.hash}`;
        } catch {
          result.reached_destination = null;
        }
        result.http_status = response?.status() ?? null;
        result.error = `navigation failed: ${error.message.split('\n')[0]}`;
      } finally {
        context.off('response', onResponse);
        await destinationPage?.close().catch(() => {});
      }
      evidence.destinations.push(result);
      if (result.result !== 'passed') {
        fail(
          `source_route=${result.source_route}; label=${JSON.stringify(result.label)}; ` +
          `expected_destination=${JSON.stringify(result.expected_destination)}; ` +
          `reached_destination=${JSON.stringify(result.reached_destination)}; ` +
          `result=${result.result}; ${result.error}`
        );
      }
    }
    evidence.distinct_destination_count = destinations.size;
    return { errors, evidence };
  } finally {
    for (const sourcePage of sourcePages) {
      await sourcePage.close().catch(() => {});
    }
  }
}

async function checkBrandGuardHeroActionAccessibleNames(
  page,
  route = new URL(page.url()).pathname
) {
  const errors = [];
  const actionContainer = page.locator(BRANDGUARD_HERO_ACTION_CONTAINER_SELECTOR);
  const actions = page.locator(BRANDGUARD_HERO_ACTION_SELECTOR);
  const actionCount = await actions.count();
  const links = [];

  if (actionCount === 0) {
    errors.push(
      `BRANDGUARD 320PX HERO ACTIONS MISSING ACCESSIBLE NAMES: ` +
      `route=${route}; label=null; destination=null; ` +
      `selector=${BRANDGUARD_HERO_ACTION_SELECTOR}`
    );
  }

  for (let index = 0; index < actionCount; index += 1) {
    const action = actions.nth(index);
    const visibleLabel = (await action.innerText()).replace(/\s+/g, ' ').trim();
    const href = await action.getAttribute('href');
    const matchingAccessibleNameCount = visibleLabel
      ? await actionContainer.getByRole('link', {
          name: visibleLabel,
          exact: true,
        }).count()
      : 0;
    const linkEvidence = {
      index,
      visible_label: visibleLabel || null,
      href,
      accessible_name_matches_visible_label: matchingAccessibleNameCount > 0,
      distinguishes_from_siblings: matchingAccessibleNameCount === 1,
      matching_accessible_name_count: matchingAccessibleNameCount,
    };
    links.push(linkEvidence);

    if (!visibleLabel || matchingAccessibleNameCount !== 1) {
      errors.push(
        `BRANDGUARD 320PX HERO ACTION ACCESSIBLE NAME ` +
        `${visibleLabel ? 'MISSING OR UNCLEAR' : 'HAS NO VISIBLE LABEL'}: ` +
        `route=${route}; link=${JSON.stringify({
          label: visibleLabel || null,
          href,
          destination: href,
        })}; expected one hero link whose accessible name exactly matches ` +
        `its visible label; matching_links=${matchingAccessibleNameCount}`
      );
    }
  }

  return {
    errors,
    evidence: {
      route,
      viewport_width_px: page.viewportSize()?.width ?? null,
      selector: BRANDGUARD_HERO_ACTION_SELECTOR,
      action_count: actionCount,
      links,
    },
  };
}

async function checkBrandGuardWebFontGeometry(
  page,
  releaseFontRequests,
  fontRequests,
  fontResponses,
  fontResourceFailures,
  includeHeaderNavigation = false,
  includeHeroActions = false
) {
  const errors = [];
  const viewportWidth = page.viewportSize()?.width ?? null;
  const checkTouchTargets =
    BRANDGUARD_TOUCH_TARGET_VIEWPORT_WIDTHS.has(viewportWidth);
  const route = new URL(page.url()).pathname;
  const isBrandGuardHub = route === BRANDGUARD_GEOMETRY_PATH;
  const reservationWaitStartedAt = performance.now();
  let reservationWaitMs = 0;
  let reservationPresentAtBaseline = false;
  let fontRequestGateReleasedAfterBaseline = false;
  let fontRequestsBeforeBaseline = [];
  let fontResponsesBeforeBaseline = [];
  const tabletBaseline = viewportWidth >= 768 && viewportWidth <= 899;
  const tabletFontEdge = viewportWidth === 769 || viewportWidth === 900;
  const tabletFontReportEvidence = (fallbackGeometry, loadedGeometry) =>
    tabletFontEdge
      ? {
          fallback_font_geometry: fallbackGeometry,
          loaded_font_geometry: loadedGeometry,
          font_geometry_deltas: compareBrandGuardFontGeometry(
            fallbackGeometry,
            loadedGeometry
          ),
          font_geometry_tolerance_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
        }
      : {};
  const reservationRequired = await page.evaluate(() =>
    (window.innerWidth === 320 || window.innerWidth >= 768) &&
    document.body?.classList.contains('askjamie-main') &&
    Boolean(document.querySelector('.askjamie-breadcrumb'))
  );
  if (reservationRequired) {
    try {
      await page.waitForFunction(() => {
        if (window.innerWidth === 320) {
          const notice = document.querySelector(
            '.askjamie-main .capability-transition'
          );
          const transitionControl = notice?.querySelector('[data-transition-open]');
          const breadcrumb = document.querySelector('.askjamie-breadcrumb');
          const heroChildren = [
            ...document.querySelectorAll('.askjamie-hero-copy > *'),
          ];
          const required = [
            notice,
            ...(notice ? [...notice.children] : []),
            breadcrumb,
            ...heroChildren,
          ];
          return Boolean(
            notice &&
            (!transitionControl || !transitionControl.hidden) &&
            required.length > 0 &&
            required.every(element =>
              element.getClientRects().length > 0 &&
              element.dataset.fontGeometryReserved === 'true'
            )
          );
        }
        return Boolean(document.querySelector('[data-font-geometry-reserved="true"]'));
      },
        undefined,
        { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS }
      );
      reservationWaitMs = Math.round(performance.now() - reservationWaitStartedAt);
      reservationPresentAtBaseline = true;
    } catch (error) {
      reservationWaitMs = Math.round(performance.now() - reservationWaitStartedAt);
      releaseFontRequests();
      errors.push(
        `BRANDGUARD ${tabletBaseline ? 'TABLET ' : ''}WEB FONT BASELINE UNAVAILABLE: ` +
        `${viewportWidth === 320
          ? 'responsive geometry was not reserved: the transition notice panel and children, breadcrumb, and hero were not all reserved'
          : 'responsive geometry was not reserved '} ` +
        `before font measurement (${error.message.split('\n')[0]}); font geometry was not compared`
      );
      return {
        errors,
        evidence: {
          baseline_status: 'unavailable',
          baseline_scope: tabletBaseline ? 'tablet' : 'responsive',
          comparison: 'not-performed-reservation-unavailable',
          threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
          timeout_ms: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
          viewport_width_px: viewportWidth,
          before: null,
          after: null,
          ...tabletFontReportEvidence(null, null),
          font_loading: null,
          font_resources: fontResponses,
          font_resource_failures: fontResourceFailures,
          shifts: [],
        },
      };
    }
  }
  const deferredThemeLink = await page.evaluate(() => {
    const link = document.querySelector('link[data-deferred-styles]');
    return link ? { href: link.href, media: link.media } : null;
  });
  if (deferredThemeLink) {
    try {
      await page.waitForFunction(() => {
        const link = document.querySelector('link[data-deferred-styles]');
        return link?.media === 'all' && Boolean(link.sheet);
      }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
    } catch (error) {
      releaseFontRequests();
      errors.push(
        `BRANDGUARD WEB FONT BASELINE UNAVAILABLE: deferred theme stylesheet did not settle ` +
        `before font measurement (${error.message.split('\n')[0]}); font geometry was not compared`
      );
      return {
        errors,
        evidence: {
          threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
          timeout_ms: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
          deferred_theme: deferredThemeLink,
          before: null,
          after: null,
          ...tabletFontReportEvidence(null, null),
          font_loading: null,
          font_resources: fontResponses,
          font_resource_failures: fontResourceFailures,
          shifts: [],
        },
      };
    }
  }
  const fontStylesheetMode = await page.evaluate(() =>
    document.querySelector('link[data-deferred-fonts]') ? 'deferred' : 'active'
  );
  if (fontStylesheetMode === 'active') {
    try {
      await page.waitForFunction(() => {
        const fontStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
          .find(link => {
            const url = new URL(link.href);
            return url.hostname === 'fonts.googleapis.com' &&
              url.pathname === '/css2' &&
              link.media !== 'not all';
          });
        return Boolean(fontStylesheet?.sheet);
      }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    } catch (error) {
      errors.push(
        `BRANDGUARD WEB FONT STYLESHEET NOT READY: ${error.message.split('\n')[0]}; ` +
        `measurements will continue`
      );
    }
  }

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

  const captureHeaderNavigation = () => page.evaluate(() => {
    const header = document.querySelector('.askjamie-main .site-header');
    const nav = header?.querySelector('.primary-nav');
    const round = value => Math.round(value * 100) / 100;
    const box = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [property, round(rect[property])])
      );
    };
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number.parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0;
    };
    const labels = [...(nav?.querySelectorAll('a') ?? [])]
      .filter(visible)
      .map((link, index) => {
        const range = document.createRange();
        range.selectNodeContents(link);
        const textRects = [...range.getClientRects()];
        const lineTops = textRects
          .map(rect => rect.top)
          .sort((left, right) => left - right)
          .filter((top, lineIndex, tops) =>
            lineIndex === 0 || top - tops[lineIndex - 1] > 4
          );
        const textBounds = textRects.length > 0
          ? {
              left: round(Math.min(...textRects.map(rect => rect.left))),
              right: round(Math.max(...textRects.map(rect => rect.right))),
              top: round(Math.min(...textRects.map(rect => rect.top))),
              bottom: round(Math.max(...textRects.map(rect => rect.bottom))),
            }
          : null;
        return {
          key: `${link.getAttribute('href') ?? ''}#${index}`,
          text: link.textContent.replace(/\s+/g, ' ').trim(),
          line_count: lineTops.length,
          text_bounds: textBounds,
          ...box(link),
        };
      });
    const navStyle = nav ? getComputedStyle(nav) : null;
    return {
      menu_open: Boolean(
        header?.classList.contains('nav-open') &&
        header.querySelector('.nav-toggle')?.getAttribute('aria-expanded') === 'true' &&
        nav?.getAttribute('aria-hidden') !== 'true'
      ),
      header: box(header),
      logo: box(header?.querySelector('.logo')),
      navigation: box(nav),
      navigation_scroll_width: nav?.scrollWidth ?? null,
      navigation_client_width: nav?.clientWidth ?? null,
      navigation_overflow_x: navStyle?.overflowX ?? null,
      toggle: box(header?.querySelector('.nav-toggle')),
      document_scroll_width: document.documentElement.scrollWidth,
      viewport_width: window.innerWidth,
      label_count: labels.length,
      anchor_count: nav?.querySelectorAll('a').length ?? 0,
      labels,
    };
  });

  const openMobileNavigation = async () => {
    const toggle = page.locator('.askjamie-main .site-header .nav-toggle');
    if (!await toggle.isVisible()) {
      throw new Error('mobile navigation toggle is not visible');
    }
    await toggle.click();
    await page.waitForFunction(() => {
      const header = document.querySelector('.askjamie-main .site-header');
      const nav = header?.querySelector('.primary-nav');
      if (!header?.classList.contains('nav-open') ||
          header.querySelector('.nav-toggle')?.getAttribute('aria-expanded') !== 'true' ||
          nav?.getAttribute('aria-hidden') === 'true') {
        return false;
      }
      const style = getComputedStyle(nav);
      const firstLabel = [...nav.querySelectorAll('a')].find(link => {
        const rect = link.getBoundingClientRect();
        return getComputedStyle(link).display !== 'none' && rect.width > 0 && rect.height > 0;
      });
      return Boolean(firstLabel) &&
        (style.transform === 'none' || style.transform === 'matrix(1, 0, 0, 1, 0, 0)');
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
  };

  const validateNarrowHeaderNavigation = (measurement, stateName) => {
    if (viewportWidth !== 320 || !measurement) return [];
    const failures = [];
    if (measurement.label_count !== measurement.anchor_count) {
      failures.push(
        `${stateName} mobile menu exposes ${measurement.label_count} of ` +
        `${measurement.anchor_count} navigation labels`
      );
    }
    if (measurement.navigation_scroll_width > measurement.navigation_client_width + 1) {
      failures.push(
        `${stateName} mobile navigation overflows horizontally ` +
        `(scrollWidth=${measurement.navigation_scroll_width}px, ` +
        `clientWidth=${measurement.navigation_client_width}px)`
      );
    }
    if (measurement.document_scroll_width > measurement.viewport_width + 1) {
      failures.push(
        `${stateName} page overflows horizontally while the mobile menu is open ` +
        `(scrollWidth=${measurement.document_scroll_width}px, ` +
        `viewport=${measurement.viewport_width}px)`
      );
    }
    const navBox = measurement.navigation;
    for (const label of measurement.labels) {
      if (label.line_count !== 1) {
        failures.push(
          `${stateName} mobile menu label wraps: ${label.text} (${label.line_count} lines)`
        );
      }
      const bounds = label.text_bounds;
      const clipped =
        !bounds ||
        !label.width ||
        !navBox ||
        bounds.left < Math.max(label.x, navBox.x, 0) - 1 ||
        bounds.right > Math.min(
          label.x + label.width,
          navBox.x + navBox.width,
          measurement.viewport_width
        ) + 1;
      if (clipped) {
        failures.push(
          `${stateName} mobile menu label is clipped: ${label.text}; ` +
          `text=${JSON.stringify(bounds)}; link=${JSON.stringify(label)}; ` +
          `navigation=${JSON.stringify(navBox)}`
        );
      }
      const verticallyClipped =
        !bounds ||
        bounds.top < Math.max(label.y, navBox?.y ?? 0, 0) - 1 ||
        bounds.bottom > Math.min(
          label.y + label.height,
          (navBox?.y ?? 0) + (navBox?.height ?? 0),
          page.viewportSize()?.height ?? Number.POSITIVE_INFINITY
        ) + 1;
      if (verticallyClipped) {
        failures.push(
          `${stateName} mobile menu label is vertically clipped: ${label.text}; ` +
          `text=${JSON.stringify(bounds)}; link=${JSON.stringify(label)}; ` +
          `navigation=${JSON.stringify(navBox)}`
        );
      }
    }
    return failures.map(failure =>
      `BRANDGUARD 320PX MOBILE NAV READABILITY FAILED: ${failure}`
    );
  };

  let headerNavigationBefore = null;
  if (includeHeaderNavigation || checkTouchTargets) {
    try {
      await openMobileNavigation();
      if (includeHeaderNavigation) {
        headerNavigationBefore = await captureHeaderNavigation();
        errors.push(...validateNarrowHeaderNavigation(
          headerNavigationBefore,
          'fallback-font state'
        ));
        if (!headerNavigationBefore.menu_open || headerNavigationBefore.labels.length === 0) {
          errors.push(
            `BRANDGUARD HEADER/NAV FONT GEOMETRY BASELINE UNAVAILABLE: ` +
            `open mobile navigation has no measurable labels; ` +
            `measured before=${JSON.stringify(headerNavigationBefore)}; after=not-measured`
          );
          headerNavigationBefore = null;
        }
      }
    } catch (error) {
      errors.push(
        `${checkTouchTargets
          ? `BRANDGUARD ${viewportWidth}PX MOBILE MENU TOUCH TARGET BASELINE UNAVAILABLE: route=${route}; `
          : 'BRANDGUARD HEADER/NAV FONT GEOMETRY BASELINE UNAVAILABLE: '}` +
        `${error.message.split('\n')[0]}; after will still be measured`
      );
    }
  }
  const beforeMenuTouchTargets = checkTouchTargets
    ? await captureBrandGuardTouchTargets(page, isBrandGuardHub, true)
    : null;
  if (beforeMenuTouchTargets) {
    errors.push(...validateBrandGuardTouchTargets(
      beforeMenuTouchTargets,
      'fallback-font state',
      viewportWidth,
      route,
      'mobile-menu'
    ));
  }

  const captureTextLayout = () => page.evaluate((selectors) => {
    if (window.innerWidth !== 320) return null;

    const layout = {};
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) {
        layout[selector] = null;
        continue;
      }

      const range = document.createRange();
      range.selectNodeContents(element);
      const fragments = [...range.getClientRects()];
      // At 320px, wrap changes are diagnostic rather than an independent
      // failure threshold. The 1px block-geometry gate remains authoritative,
      // and both font states must still pass the clipping and overlap checks.
      const lineTops = fragments
        .map(rect => rect.top)
        .sort((left, right) => left - right)
        .filter((top, index, tops) =>
          index === 0 || top - tops[index - 1] > 4
        );
      const bounds = range.getBoundingClientRect();
      const round = value => Math.round(value * 100) / 100;
      layout[selector] = {
        line_count: lineTops.length,
        text_bounds: {
          top: round(bounds.top),
          bottom: round(bounds.bottom),
          height: round(bounds.height),
        },
      };
    }
    return layout;
  }, BRANDGUARD_GEOMETRY_SELECTORS);

  const captureTransitionNotice = () => page.evaluate(() => {
    if (window.innerWidth !== 320) return null;

    const round = value => Math.round(value * 100) / 100;
    const box = element => {
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['x', 'y', 'width', 'height'].map(property => [property, round(rect[property])])
      );
    };
    const notice = document.querySelector(
      '.askjamie-main .capability-transition'
    );
    if (!notice) return null;

    const children = [...notice.children].map((element, index) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const textRects = [...range.getClientRects()];
      const lineTops = textRects
        .map(rect => rect.top)
        .sort((left, right) => left - right)
        .filter((top, lineIndex, tops) =>
          lineIndex === 0 || top - tops[lineIndex - 1] > 4
        );
      return {
        key: `${element.tagName.toLowerCase()}#${index}`,
        text: element.textContent.replace(/\s+/g, ' ').trim(),
        visible: getComputedStyle(element).display !== 'none' &&
          getComputedStyle(element).visibility !== 'hidden' &&
          element.getBoundingClientRect().width > 0 &&
          element.getBoundingClientRect().height > 0,
        reserved: element.dataset.fontGeometryReserved === 'true',
        line_count: lineTops.length,
        ...box(element),
      };
    });
    const transitionControl = notice.querySelector('[data-transition-open]');
    return {
      viewport_width: window.innerWidth,
      notice: box(notice),
      notice_reserved: notice.dataset.fontGeometryReserved === 'true',
      transition_control_present: Boolean(transitionControl),
      transition_control_visible: Boolean(
        transitionControl && !transitionControl.hidden &&
        getComputedStyle(transitionControl).display !== 'none' &&
        transitionControl.getBoundingClientRect().width > 0 &&
        transitionControl.getBoundingClientRect().height > 0
      ),
      transition_control_reserved:
        transitionControl?.dataset.fontGeometryReserved === 'true',
      children,
    };
  });

  const captureHeroActions = () => page.evaluate(() => {
    if (window.innerWidth !== 320) return null;

    const round = value => Math.round(value * 100) / 100;
    const box = element => {
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['x', 'y', 'width', 'height', 'top', 'right', 'bottom', 'left']
          .map(property => [property, round(rect[property])])
      );
    };
    const visible = element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        Number.parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0 &&
        element.getClientRects().length > 0;
    };
    const actionContainers = [
      ...document.querySelectorAll(
        '.askjamie-main .askjamie-hero-copy .hero-actions'
      ),
    ];
    const container = actionContainers[0];
    if (!container) {
      return { viewport_width: window.innerWidth, container_count: 0, container: null, buttons: [], overlaps: [] };
    }

    const buttons = [...container.querySelectorAll('a')]
      .map((button, index) => {
        const text = button.textContent.replace(/\s+/g, ' ').trim();
        const textRange = document.createRange();
        textRange.selectNodeContents(button);
        const textRects = [...textRange.getClientRects()];
        const textBounds = textRange.getBoundingClientRect();
        const rect = button.getBoundingClientRect();
        const style = getComputedStyle(button);
        const labelInsideButton =
          textBounds.width > 0 &&
          textBounds.height > 0 &&
          textBounds.left >= rect.left - 1 &&
          textBounds.right <= rect.right + 1 &&
          textBounds.top >= rect.top - 1 &&
          textBounds.bottom <= rect.bottom + 1;
        return {
          key: `${button.getAttribute('href') ?? ''}#${index}`,
          text,
          visible: visible(button),
          label_visible: Boolean(text) && textRects.length > 0 && labelInsideButton,
          label_line_count: [...new Set(textRects.map(textRect =>
            Math.round(textRect.top * 100) / 100
          ))].length,
          label_bounds: Object.fromEntries(
            ['left', 'right', 'top', 'bottom', 'width', 'height']
              .map(property => [property, round(textBounds[property])])
          ),
          target: box(button),
          target_width_px: round(rect.width),
          target_height_px: round(rect.height),
          horizontal_clipping: ['hidden', 'clip'].includes(style.overflowX) &&
            button.scrollWidth > button.clientWidth + 1,
          vertical_clipping: ['hidden', 'clip'].includes(style.overflowY) &&
            button.scrollHeight > button.clientHeight + 1,
          outside_viewport: rect.left < -1 || rect.right > window.innerWidth + 1,
        };
      });
    const overlaps = [];
    for (let first = 0; first < buttons.length; first += 1) {
      for (let second = first + 1; second < buttons.length; second += 1) {
        const left = buttons[first].target;
        const right = buttons[second].target;
        if (Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1 &&
            Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1) {
          overlaps.push({ first: buttons[first].key, second: buttons[second].key });
        }
      }
    }
    const containerRect = box(container);
    const containerStyle = getComputedStyle(container);
    return {
      viewport_width: window.innerWidth,
      container_count: actionContainers.length,
      container: {
        visible: visible(container),
        ...containerRect,
        horizontal_clipping: ['hidden', 'clip'].includes(containerStyle.overflowX) &&
          container.scrollWidth > container.clientWidth + 1,
        vertical_clipping: ['hidden', 'clip'].includes(containerStyle.overflowY) &&
          container.scrollHeight > container.clientHeight + 1,
      },
      buttons,
      overlaps,
    };
  });

  const before = await capture();
  fontRequestsBeforeBaseline = [...fontRequests];
  fontResponsesBeforeBaseline = [...fontResponses];
  if (fontResponsesBeforeBaseline.some(response => response.resource_type === 'font')) {
    errors.push(
      `BRANDGUARD WEB FONT BASELINE INVALID: a hosted font responded before fallback capture; ` +
      `requests=${JSON.stringify(fontRequestsBeforeBaseline)}; ` +
      `responses=${JSON.stringify(fontResponsesBeforeBaseline)}`
    );
  }
  const beforeTextLayout = await captureTextLayout();
  const beforeTransitionNotice = await captureTransitionNotice();
  if (beforeTransitionNotice) {
    if (!beforeTransitionNotice.notice_reserved) {
      errors.push(
        `BRANDGUARD 320PX ARRIVAL NOTICE NOT RESERVED BEFORE WEB FONT BASELINE: ` +
        `${JSON.stringify(beforeTransitionNotice)}`
      );
    }
    if (beforeTransitionNotice.transition_control_present &&
        (!beforeTransitionNotice.transition_control_visible ||
         !beforeTransitionNotice.transition_control_reserved)) {
      errors.push(
        `BRANDGUARD 320PX ARRIVAL NOTICE CONTROL NOT READY BEFORE WEB FONT BASELINE: ` +
        `${JSON.stringify(beforeTransitionNotice)}`
      );
    }
    const unreservedChildren = beforeTransitionNotice.children.filter(child =>
      !child.reserved
    );
    if (unreservedChildren.length > 0) {
      errors.push(
        `BRANDGUARD 320PX ARRIVAL NOTICE CHILDREN NOT RESERVED BEFORE WEB FONT BASELINE: ` +
        `${JSON.stringify(unreservedChildren)}`
      );
    }
  }
  const beforeHeroActions = includeHeroActions ? await captureHeroActions() : null;
  const checkFontContent = BRANDGUARD_FONT_CONTENT_VIEWPORT_WIDTHS.has(viewportWidth);
  const beforeTouchTargets = checkTouchTargets
    ? await captureBrandGuardTouchTargets(page, isBrandGuardHub)
    : null;
  const beforeNarrowContent = checkFontContent
    ? await checkBrandGuardNarrowContent(page, viewportWidth)
    : null;
  if (beforeNarrowContent) {
    errors.push(...beforeNarrowContent.errors.map(error =>
      `BRANDGUARD ${viewportWidth}PX FONT CONTENT CHECK FAILED (fallback-font state): ${error}`
    ));
  }
  releaseFontRequests();
  fontRequestGateReleasedAfterBaseline = true;

  let fontLoading = null;
  try {
    await page.waitForFunction(() => {
      const fontLink = document.querySelector('link[data-deferred-fonts]');
      if (fontLink) return fontLink.media === 'all' && Boolean(fontLink.sheet);

      const fontStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => {
          const url = new URL(link.href);
          return url.hostname === 'fonts.googleapis.com' &&
            url.pathname === '/css2' &&
            link.media !== 'not all';
        });
      return Boolean(fontStylesheet?.sheet);
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    const fontSelectors = includeHeaderNavigation || checkTouchTargets
      ? [...BRANDGUARD_GEOMETRY_SELECTORS, '.askjamie-main .site-header .primary-nav a']
      : BRANDGUARD_GEOMETRY_SELECTORS;
    const expectedFamilies = includeHeaderNavigation || checkTouchTargets
      ? [...BRANDGUARD_FONT_FAMILIES, 'Open Sans']
      : BRANDGUARD_FONT_FAMILIES;
    fontLoading = await page.evaluate(async ({ selectors, expectedFamilies, timeoutMs }) => {
      const fontLink = document.querySelector('link[data-deferred-fonts]');
      const activeFontStylesheet = fontLink ?? [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => {
          const url = new URL(link.href);
          return url.hostname === 'fonts.googleapis.com' &&
            url.pathname === '/css2' &&
            link.media !== 'not all';
        });
      if (!activeFontStylesheet?.sheet || (fontLink && fontLink.media !== 'all')) {
        throw new Error(
          `Google Fonts stylesheet was not active (mode=${fontLink ? 'deferred' : 'active'}, ` +
          `media=${JSON.stringify(activeFontStylesheet?.media ?? null)})`
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
          link_media: activeFontStylesheet.media,
          fonts,
        };
      } finally {
        clearTimeout(timeoutId);
      }
    }, {
      selectors: fontSelectors,
      expectedFamilies,
      timeoutMs: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
    });
  } catch (error) {
    errors.push(
      `BRANDGUARD WEB FONTS NOT READY: ${error.message.split('\n')[0]}; ` +
      `selectors=${JSON.stringify(BRANDGUARD_GEOMETRY_SELECTORS)}; ` +
      `before=${JSON.stringify(before)}; after will still be measured`
    );
  }

  const after = await capture();
  const afterTransitionNotice = await captureTransitionNotice();
  const afterHeroActions = includeHeroActions ? await captureHeroActions() : null;
  const afterTouchTargets = checkTouchTargets
    ? await captureBrandGuardTouchTargets(page, isBrandGuardHub)
    : null;
  const afterMenuTouchTargets = checkTouchTargets
    ? await captureBrandGuardTouchTargets(page, isBrandGuardHub, true)
    : null;
  const hasStylesheet = fontResponses.some(
    response => response.resource_type === 'stylesheet' && response.status < 400
  );
  const hasFontAsset = fontResponses.some(
    response => response.resource_type === 'font' && response.status < 400
  );
  const failedFontChecks = fontLoading?.fonts.filter(font =>
    !font.loaded && font.reason !== 'element missing'
  ) ?? [];
  const fontEvidenceAvailable =
    Boolean(fontLoading) &&
    hasStylesheet &&
    hasFontAsset &&
    failedFontChecks.length === 0 &&
    fontResourceFailures.length === 0;
  const afterFontStateName = fontEvidenceAvailable
    ? 'branded-font state'
    : 'after-font-attempt state';
  if (beforeTouchTargets) {
    errors.push(...validateBrandGuardTouchTargets(
      beforeTouchTargets,
      'fallback-font state',
      viewportWidth,
      route
    ));
    errors.push(...validateBrandGuardTouchTargets(
      afterTouchTargets,
      'branded-font state',
      viewportWidth,
      route
    ));
  }
  if (afterMenuTouchTargets) {
    errors.push(...validateBrandGuardTouchTargets(
      afterMenuTouchTargets,
      'branded-font state',
      viewportWidth,
      route,
      'mobile-menu'
    ));
  }
  const headerNavigationAfter = includeHeaderNavigation
    ? await captureHeaderNavigation()
    : null;
  if (includeHeaderNavigation) {
    errors.push(...validateNarrowHeaderNavigation(
      headerNavigationAfter,
      afterFontStateName
    ));
  }
  const headerNavigationShifts = fontEvidenceAvailable ? [] : null;
  const headerNavigationLineChanges = fontEvidenceAvailable ? [] : null;
  if (includeHeaderNavigation && fontEvidenceAvailable) {
    if (!headerNavigationBefore) {
      errors.push(
        `BRANDGUARD HEADER/NAV FONT GEOMETRY NOT COMPARED: fallback-font labels were not measured`
      );
    } else if (!headerNavigationAfter.menu_open || headerNavigationAfter.labels.length === 0) {
      errors.push(
        `BRANDGUARD HEADER/NAV FONT GEOMETRY NOT COMPARED: mobile navigation closed or labels missing ` +
        `after font load; after=${JSON.stringify(headerNavigationAfter)}`
      );
    } else {
      const compareBox = (name, beforeBox, afterBox) => {
        if (!beforeBox || !afterBox) {
          errors.push(
            `BRANDGUARD HEADER/NAV FONT GEOMETRY MISSING: ${name}; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
          return;
        }
        const delta = Object.fromEntries(
          ['x', 'y', 'width', 'height'].map(property => [
            property,
            Math.round((afterBox[property] - beforeBox[property]) * 100) / 100,
          ])
        );
        const changedProperties = Object.keys(delta).filter(property =>
          Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX
        );
        if (changedProperties.length > 0) {
          headerNavigationShifts.push({
            name,
            before: beforeBox,
            after: afterBox,
            delta,
            changed_properties: changedProperties,
          });
          errors.push(
            `BRANDGUARD HEADER/NAV WEB FONT GEOMETRY SHIFT: ${name} changed ` +
            `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
            `with ${BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
        }
      };
      for (const key of ['header', 'logo', 'navigation', 'toggle']) {
        compareBox(
          `.site-header ${key}`,
          headerNavigationBefore[key],
          headerNavigationAfter[key]
        );
      }
      const beforeLabels = new Map(
        headerNavigationBefore.labels.map(label => [label.key, label])
      );
      const afterLabels = new Map(
        headerNavigationAfter.labels.map(label => [label.key, label])
      );
      for (const [key, beforeLabel] of beforeLabels) {
        const afterLabel = afterLabels.get(key);
        if (!afterLabel || afterLabel.text !== beforeLabel.text) {
          errors.push(
            `BRANDGUARD HEADER/NAV WEB FONT LABEL MISSING: ${beforeLabel.text}; ` +
            `before=${JSON.stringify(beforeLabel)}; after=${JSON.stringify(afterLabel ?? null)}`
          );
          continue;
        }
        compareBox(
          `.primary-nav a[href="${key.slice(0, key.lastIndexOf('#'))}"] (${beforeLabel.text})`,
          beforeLabel,
          afterLabel
        );
        if (beforeLabel.line_count !== afterLabel.line_count) {
          const change = {
            text: beforeLabel.text,
            fallback_line_count: beforeLabel.line_count,
            branded_line_count: afterLabel.line_count,
          };
          headerNavigationLineChanges.push(change);
          errors.push(
            `BRANDGUARD HEADER/NAV WEB FONT LABEL WRAP: ${beforeLabel.text} changed from ` +
            `${beforeLabel.line_count} to ${afterLabel.line_count} lines`
          );
        }
      }
      for (const [key, afterLabel] of afterLabels) {
        if (!beforeLabels.has(key)) {
          errors.push(
            `BRANDGUARD HEADER/NAV WEB FONT LABEL APPEARED: ${afterLabel.text}; ` +
            `after=${JSON.stringify(afterLabel)}`
          );
        }
      }
    }
  }
  const afterTextLayout = await captureTextLayout();
  const afterNarrowContent = beforeNarrowContent
    ? await checkBrandGuardNarrowContent(page, viewportWidth)
    : null;
  if (afterNarrowContent) {
    errors.push(...afterNarrowContent.errors.map(error =>
      `BRANDGUARD ${viewportWidth}PX FONT CONTENT CHECK FAILED (${afterFontStateName}): ${error}`
    ));
  }
  const missingTabletGeometrySelectors = tabletFontEdge
    ? BRANDGUARD_GEOMETRY_SELECTORS.filter(selector => !before[selector] || !after[selector])
    : [];
  for (const selector of missingTabletGeometrySelectors) {
    errors.push(
      `BRANDGUARD TABLET WEB FONT GEOMETRY MISSING: ` +
      `selector=${selector}; route=${route}; ` +
      `fallback_font_geometry=${JSON.stringify(before[selector] ?? null)}; ` +
      `loaded_font_geometry=${JSON.stringify(after[selector] ?? null)}`
    );
  }
  if (!fontEvidenceAvailable) {
    errors.push(
      `BRANDGUARD WEB FONTS UNAVAILABLE: stylesheet_loaded=${hasStylesheet}; ` +
      `font_asset_loaded=${hasFontAsset}; failed_faces=${JSON.stringify(failedFontChecks)}; ` +
      `failed_resources=${JSON.stringify(fontResourceFailures)}; ` +
      `resources=${JSON.stringify(fontResponses)}; ` +
      `before=${JSON.stringify(before)}; after=${JSON.stringify(after)}`
    );
  }
  const loadedFontGeometry = fontEvidenceAvailable ? after : null;

  const shifts = [];
  const fontGeometryDeltas = compareBrandGuardFontGeometry(before, loadedFontGeometry);
  if (fontEvidenceAvailable) {
    for (const geometryDelta of fontGeometryDeltas) {
      const { selector, before: beforeRect, after: afterRect, delta, changed_properties } =
        geometryDelta;
      if (!beforeRect || !afterRect) {
        errors.push(
          `BRANDGUARD WEB FONT GEOMETRY MISSING: ${selector}; ` +
          `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
        );
        continue;
      }

      if (changed_properties.length > 0) {
        shifts.push(geometryDelta);
        errors.push(
          `BRANDGUARD WEB FONT GEOMETRY SHIFT: ${selector} changed ` +
          `${changed_properties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
          `with ${BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
          `before=${JSON.stringify(beforeRect)}; after=${JSON.stringify(afterRect)}`
        );
      }
    }
  }

  const transitionNoticeGeometryShifts = fontEvidenceAvailable ? [] : null;
  if (beforeTransitionNotice || afterTransitionNotice) {
    if (!beforeTransitionNotice || !afterTransitionNotice) {
      if (fontEvidenceAvailable) errors.push(
        `BRANDGUARD 320PX ARRIVAL NOTICE WEB FONT GEOMETRY NOT COMPARED: ` +
        `before=${JSON.stringify(beforeTransitionNotice)}; ` +
        `after=${JSON.stringify(afterTransitionNotice)}`
      );
    } else if (fontEvidenceAvailable) {
      const compareNoticeBox = (name, beforeBox, afterBox) => {
        if (!beforeBox || !afterBox) {
          errors.push(
            `BRANDGUARD 320PX ARRIVAL NOTICE WEB FONT GEOMETRY MISSING: ${name}; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
          return;
        }
        const delta = Object.fromEntries(
          ['x', 'y', 'width', 'height'].map(property => [
            property,
            Math.round((afterBox[property] - beforeBox[property]) * 100) / 100,
          ])
        );
        const changedProperties = Object.keys(delta).filter(property =>
          Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX
        );
        if (changedProperties.length > 0) {
          transitionNoticeGeometryShifts.push({
            name,
            before: beforeBox,
            after: afterBox,
            delta,
            changed_properties: changedProperties,
          });
          errors.push(
            `BRANDGUARD 320PX ARRIVAL NOTICE WEB FONT GEOMETRY SHIFT: ${name} changed ` +
            `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
            `with ${BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
        }
      };
      compareNoticeBox(
        '.capability-transition',
        beforeTransitionNotice.notice,
        afterTransitionNotice.notice
      );
      const beforeChildren = new Map(
        beforeTransitionNotice.children.map(child => [child.key, child])
      );
      const afterChildren = new Map(
        afterTransitionNotice.children.map(child => [child.key, child])
      );
      // Child text remains checked for presence/readability, but only the
      // reserved panel is a layout gate: inline controls can change intrinsic
      // width with the font without moving the notice or downstream content.
      for (const [key, beforeChild] of beforeChildren) {
        const afterChild = afterChildren.get(key);
        if (!afterChild || afterChild.text !== beforeChild.text) {
          errors.push(
            `BRANDGUARD 320PX ARRIVAL NOTICE CONTENT MISSING AFTER WEB FONT LOAD: ` +
            `${key}; before=${JSON.stringify(beforeChild)}; ` +
            `after=${JSON.stringify(afterChild ?? null)}`
          );
        }
      }
      for (const [key, afterChild] of afterChildren) {
        if (!beforeChildren.has(key)) {
          errors.push(
            `BRANDGUARD 320PX ARRIVAL NOTICE CONTENT APPEARED AFTER WEB FONT LOAD: ` +
            `${key}; after=${JSON.stringify(afterChild)}`
          );
        }
      }
    }
    for (const [stage, state] of [
      ['fallback-font state', beforeTransitionNotice],
      [afterFontStateName, afterTransitionNotice],
    ]) {
      if (!state) continue;
      const unreadable = state.children.filter(child =>
        !child.text || !child.visible || child.line_count === 0
      );
      if (unreadable.length > 0) {
        errors.push(
          `BRANDGUARD 320PX ARRIVAL NOTICE TEXT NOT READABLE (${stage}): ` +
          `children=${JSON.stringify(unreadable)}`
        );
      }
    }
  }

  const lineCountChanges = fontEvidenceAvailable ? [] : null;
  if (fontEvidenceAvailable && beforeTextLayout && afterTextLayout) {
    for (const selector of BRANDGUARD_GEOMETRY_SELECTORS) {
      const beforeLayout = beforeTextLayout[selector];
      const afterLayout = afterTextLayout[selector];
      if (!beforeLayout || !afterLayout) continue;
      if (beforeLayout.line_count !== afterLayout.line_count) {
        lineCountChanges.push({
          selector,
          fallback_line_count: beforeLayout.line_count,
          branded_line_count: afterLayout.line_count,
          delta: afterLayout.line_count - beforeLayout.line_count,
        });
      }
    }
  }

  const heroActionGeometryShifts = fontEvidenceAvailable ? [] : null;
  const validateHeroActionState = (stage, actionState) => {
    if (!actionState || actionState.viewport_width !== 320) {
      errors.push(
        `BRANDGUARD 320PX HERO ACTIONS NOT MEASURED (${stage}): ` +
        `evidence=${JSON.stringify(actionState)}`
      );
      return;
    }
    if (actionState.container_count !== 1 || !actionState.container?.visible) {
      errors.push(
        `BRANDGUARD 320PX HERO ACTION AREA NOT READABLE (${stage}): ` +
        `evidence=${JSON.stringify(actionState)}`
      );
    }
    if (actionState.container?.horizontal_clipping ||
        actionState.container?.vertical_clipping) {
      errors.push(
        `BRANDGUARD 320PX HERO ACTION AREA CLIPPED (${stage}): ` +
        `evidence=${JSON.stringify(actionState.container)}`
      );
    }
    if (actionState.buttons.length === 0) {
      errors.push(`BRANDGUARD 320PX HERO ACTION BUTTONS MISSING (${stage})`);
    }
    for (const button of actionState.buttons) {
      if (!button.text || !button.visible || !button.label_visible) {
        errors.push(
          `BRANDGUARD 320PX HERO BUTTON LABEL NOT READABLE (${stage}): ` +
          `evidence=${JSON.stringify(button)}`
        );
      }
      if (button.target_width_px < 44 || button.target_height_px < 44) {
        errors.push(
          `BRANDGUARD 320PX HERO BUTTON TARGET TOO SMALL (${stage}): ` +
          `minimum=44x44px; evidence=${JSON.stringify(button)}`
        );
      }
      if (button.horizontal_clipping ||
          button.vertical_clipping ||
          button.outside_viewport) {
        errors.push(
          `BRANDGUARD 320PX HERO BUTTON CLIPPED (${stage}): ` +
          `evidence=${JSON.stringify(button)}`
        );
      }
    }
    for (const overlap of actionState.overlaps) {
      errors.push(
        `BRANDGUARD 320PX HERO BUTTONS OVERLAP (${stage}): ` +
        `evidence=${JSON.stringify(overlap)}`
      );
    }
  };
  if (includeHeroActions) {
    validateHeroActionState('fallback-font state', beforeHeroActions);
    validateHeroActionState(afterFontStateName, afterHeroActions);
    if (fontEvidenceAvailable) {
      const beforeButtons = new Map(
        (beforeHeroActions?.buttons ?? []).map(button => [button.key, button])
      );
      const afterButtons = new Map(
        (afterHeroActions?.buttons ?? []).map(button => [button.key, button])
      );
      const compareActionBox = (name, beforeBox, afterBox) => {
        if (!beforeBox || !afterBox) {
          errors.push(
            `BRANDGUARD 320PX HERO ACTION GEOMETRY MISSING: ${name}; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
          return;
        }
        const delta = Object.fromEntries(
          ['x', 'y', 'width', 'height'].map(property => [
            property,
            Math.round((afterBox[property] - beforeBox[property]) * 100) / 100,
          ])
        );
        const changedProperties = Object.keys(delta).filter(property =>
          Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX
        );
        if (changedProperties.length > 0) {
          heroActionGeometryShifts.push({
            name,
            before: beforeBox,
            after: afterBox,
            delta,
            changed_properties: changedProperties,
          });
          errors.push(
            `BRANDGUARD 320PX HERO ACTION WEB FONT GEOMETRY SHIFT: ${name} changed ` +
            `${changedProperties.map(property => `${property}=${delta[property]}px`).join(', ')} ` +
            `with ${BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX}px tolerance; ` +
            `before=${JSON.stringify(beforeBox)}; after=${JSON.stringify(afterBox)}`
          );
        }
      };
      compareActionBox(
        '.hero-actions',
        beforeHeroActions?.container,
        afterHeroActions?.container
      );
      for (const [key, beforeButton] of beforeButtons) {
        const afterButton = afterButtons.get(key);
        if (!afterButton || beforeButton.text !== afterButton.text) {
          errors.push(
            `BRANDGUARD 320PX HERO BUTTON MISSING AFTER WEB FONT LOAD: ` +
            `${beforeButton.text}; before=${JSON.stringify(beforeButton)}; ` +
            `after=${JSON.stringify(afterButton ?? null)}`
          );
          continue;
        }
        compareActionBox(
          `.hero-actions a[href="${key.slice(0, key.lastIndexOf('#'))}"] (${beforeButton.text})`,
          beforeButton.target,
          afterButton.target
        );
      }
      for (const [key, afterButton] of afterButtons) {
        if (!beforeButtons.has(key)) {
          errors.push(
            `BRANDGUARD 320PX HERO BUTTON APPEARED AFTER WEB FONT LOAD: ` +
            `${afterButton.text}; after=${JSON.stringify(afterButton)}`
          );
        }
      }
    }
  }

  return {
    errors,
    evidence: {
      threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
      timeout_ms: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS,
      font_stylesheet_mode: fontStylesheetMode,
      deferred_theme: deferredThemeLink
        ? { ...deferredThemeLink, media_after_settle: 'all' }
        : null,
      reservation_present_at_baseline: reservationPresentAtBaseline,
      reservation_wait_ms: reservationWaitMs,
      comparison: fontEvidenceAvailable
        ? 'performed'
        : 'not-performed-fonts-unavailable',
      font_evidence_status: fontEvidenceAvailable ? 'verified' : 'unavailable',
      measurement_state: fontEvidenceAvailable
        ? 'fallback-and-loaded-fonts'
        : 'fallback-only-fonts-unavailable',
      font_request_gate: {
        held_requests_before_baseline: fontRequestsBeforeBaseline,
        font_responses_before_baseline: fontResponsesBeforeBaseline.filter(
          response => response.resource_type === 'font'
        ),
        released_after_baseline: fontRequestGateReleasedAfterBaseline,
      },
      before,
      after,
      ...tabletFontReportEvidence(before, loadedFontGeometry),
      font_loading: fontLoading,
      font_resources: fontResponses,
      font_resource_failures: fontResourceFailures,
      shifts,
      ...(includeHeaderNavigation
        ? {
            header_navigation: {
              route,
              viewport_width_px: viewportWidth,
              threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
              measurement_scope: fontEvidenceAvailable
                ? 'fallback-versus-branded-fonts-after-theme-settled'
                : 'fallback-readability-checked-font-swap-unavailable',
              font_evidence_status: fontEvidenceAvailable
                ? 'verified'
                : 'unavailable',
              comparison_status: !fontEvidenceAvailable
                ? 'not-performed-fonts-unavailable'
                : headerNavigationBefore &&
                headerNavigationAfter?.menu_open &&
                headerNavigationAfter.labels.length > 0
                  ? 'performed'
                  : 'not-performed',
              font_selector: '.askjamie-main .site-header .primary-nav a',
              menu_open_required: true,
              no_label_wrapping_required: viewportWidth === 320,
              horizontal_overflow_checked: viewportWidth === 320,
              all_navigation_labels_required: viewportWidth === 320,
              font_loading: fontLoading,
              font_resources: fontResponses,
              font_resource_failures: fontResourceFailures,
              font_request_gate: {
                held_requests_before_baseline: fontRequestsBeforeBaseline,
                font_responses_before_baseline: fontResponsesBeforeBaseline.filter(
                  response => response.resource_type === 'font'
                ),
                released_after_baseline: fontRequestGateReleasedAfterBaseline,
              },
              before: headerNavigationBefore,
              after: headerNavigationAfter,
              shifts: headerNavigationShifts,
              line_count_changes: headerNavigationLineChanges,
            },
          }
        : {}),
      ...(beforeTextLayout
        ? {
            font_text_layout: {
              block_geometry_threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
              line_counts_are_diagnostic: true,
              comparison_status: fontEvidenceAvailable
                ? 'performed'
                : 'not-performed-fonts-unavailable',
              before: beforeTextLayout,
              after: afterTextLayout,
              line_count_changes: lineCountChanges,
            },
          }
        : {}),
      ...(beforeNarrowContent
        ? {
            narrow_content_during_font_load: {
              viewport_width_px: viewportWidth,
              before: beforeNarrowContent?.evidence ?? null,
              after: afterNarrowContent?.evidence ?? null,
            },
          }
        : {}),
      ...(beforeTextLayout
        ? {
            arrival_notice_web_font_geometry: {
              measurement_scope: fontEvidenceAvailable
                ? 'fallback-versus-branded-fonts-after-theme-settled'
                : 'fallback-readability-checked-font-swap-unavailable',
              font_evidence_status: fontEvidenceAvailable ? 'verified' : 'unavailable',
              comparison_status: fontEvidenceAvailable
                ? 'performed'
                : 'not-performed-fonts-unavailable',
              deferred_theme_geometry_included: false,
              viewport_width_px: 320,
              threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
              before: beforeTransitionNotice,
              after: afterTransitionNotice,
              shifts: transitionNoticeGeometryShifts,
            },
          }
        : {}),
      ...(includeHeroActions
        ? {
            hero_actions_web_font_geometry: {
              measurement_scope: fontEvidenceAvailable
                ? 'fallback-versus-branded-fonts-after-theme-settled'
                : 'fallback-readability-checked-font-swap-unavailable',
              font_evidence_status: fontEvidenceAvailable ? 'verified' : 'unavailable',
              comparison_status: fontEvidenceAvailable
                ? 'performed'
                : 'not-performed-fonts-unavailable',
              deferred_theme_geometry_included: false,
              viewport_width_px: 320,
              threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX,
              minimum_target_px: { width: 44, height: 44 },
              before: beforeHeroActions,
              after: afterHeroActions,
              shifts: heroActionGeometryShifts,
            },
          }
        : {}),
      ...(beforeTouchTargets
        ? {
            touch_targets_during_font_load: {
              measurement_scope: 'hub-case-study-cards-or-detail-page-call-to-action-links-before-and-after-branded-font-load',
              viewport_width_px: viewportWidth,
              minimum_target_px: {
                width: BRANDGUARD_TOUCH_TARGET_MINIMUM_PX,
                height: BRANDGUARD_TOUCH_TARGET_MINIMUM_PX,
              },
              route: new URL(page.url()).pathname,
              fallback_font: beforeTouchTargets,
              branded_font: afterTouchTargets,
            },
          }
        : {}),
      ...(beforeMenuTouchTargets
        ? {
            mobile_menu_touch_targets_during_font_load: {
              measurement_scope: 'visible-mobile-navigation-links-and-menu-toggle-after-opening-navigation-before-and-after-branded-font-load',
              viewport_width_px: viewportWidth,
              minimum_target_px: {
                width: BRANDGUARD_TOUCH_TARGET_MINIMUM_PX,
                height: BRANDGUARD_TOUCH_TARGET_MINIMUM_PX,
              },
              route,
              fallback_font: beforeMenuTouchTargets,
              branded_font: afterMenuTouchTargets,
            },
          }
        : {}),
    },
  };
}

async function captureBrandGuardTouchTargets(page, isBrandGuardHub, menuOnly = false) {
  return page.evaluate(({ isHub, menuOnly }) => {
    const selectors = menuOnly
      ? '.askjamie-main .site-header .nav-toggle, .askjamie-main .site-header .primary-nav a[href]'
      : isHub
        ? 'main a.brandguard-case-card[href]'
        : 'main a.btn[href]';
    const round = value => Math.round(value * 100) / 100;
    const clipAreaHasNoVisiblePixels = clip => {
      const match = clip.match(/^rect\((.+)\)$/);
      if (!match) return false;
      const [top, right, bottom, left] = match[1]
        .split(',')
        .map(value => Number.parseFloat(value));
      return [top, right, bottom, left].every(Number.isFinite) &&
        (right <= left || bottom <= top);
    };
    const controls = [...document.querySelectorAll(selectors)];
    const visibleControls = controls
      .map((element, index) => {
        const rect = element.getBoundingClientRect();
        let ancestor = element;
        let visible = rect.width > 0 &&
          rect.height > 0 &&
          element.getClientRects().length > 0;
        while (ancestor && visible) {
          const ancestorStyle = getComputedStyle(ancestor);
          visible = ancestorStyle.display !== 'none' &&
            ancestorStyle.visibility !== 'hidden' &&
            ancestorStyle.visibility !== 'collapse' &&
            Number.parseFloat(ancestorStyle.opacity) > 0 &&
            !clipAreaHasNoVisiblePixels(ancestorStyle.clip) &&
            !ancestor.inert;
          ancestor = ancestor.parentElement;
        }
        if (element.disabled || element.getAttribute('aria-disabled') === 'true') {
          visible = false;
        }
        if (!visible) return null;

        const label = element.getAttribute('aria-label') ||
          element.innerText ||
          element.textContent ||
          '';
        return {
          key: `${element.tagName.toLowerCase()}#${index}`,
          tag: element.tagName.toLowerCase(),
          label: label.replace(/\s+/g, ' ').trim(),
          href: element.getAttribute('href'),
          destination: element.getAttribute('href'),
          target: Object.fromEntries(
            ['left', 'right', 'top', 'bottom', 'width', 'height']
              .map(property => [property, round(rect[property])])
          ),
          element,
        };
      })
      .filter(Boolean);
    const overlaps = [];
    for (let first = 0; first < visibleControls.length; first += 1) {
      for (let second = first + 1; second < visibleControls.length; second += 1) {
        const firstControl = visibleControls[first];
        const secondControl = visibleControls[second];
        if (firstControl.element.contains(secondControl.element) ||
            secondControl.element.contains(firstControl.element)) {
          continue;
        }
        const left = firstControl.target;
        const right = secondControl.target;
        const overlapWidth = Math.min(left.right, right.right) -
          Math.max(left.left, right.left);
        const overlapHeight = Math.min(left.bottom, right.bottom) -
          Math.max(left.top, right.top);
        if (overlapWidth > 1 && overlapHeight > 1) {
          overlaps.push({
            first: {
              key: firstControl.key,
              tag: firstControl.tag,
              label: firstControl.label,
              href: firstControl.href,
              destination: firstControl.href,
              target: firstControl.target,
            },
            second: {
              key: secondControl.key,
              tag: secondControl.tag,
              label: secondControl.label,
              href: secondControl.href,
              destination: secondControl.href,
              target: secondControl.target,
            },
            overlap_width_px: round(overlapWidth),
            overlap_height_px: round(overlapHeight),
          });
        }
      }
    }
    return {
      viewport_width_px: window.innerWidth,
      ...(menuOnly
        ? {
            menu_open: Boolean(
              document.querySelector('.askjamie-main .site-header')?.classList.contains('nav-open') &&
              document.querySelector('.askjamie-main .site-header .nav-toggle')
                ?.getAttribute('aria-expanded') === 'true' &&
              document.querySelector('.askjamie-main .site-header .primary-nav')
                ?.getAttribute('aria-hidden') !== 'true'
            ),
            visible_menu_link_count: visibleControls.filter(control =>
              control.tag === 'a'
            ).length,
          }
        : {}),
      visible_target_count: visibleControls.length,
      targets: visibleControls.map(({ element: _element, ...target }) => target),
      overlaps,
    };
  }, { isHub: isBrandGuardHub, menuOnly });
}

function validateBrandGuardTouchTargets(
  evidence,
  stage,
  viewportWidth,
  route,
  targetGroup = 'content'
) {
  const errors = [];
  const groupLabel = targetGroup === 'mobile-menu' ? ' MOBILE MENU' : '';
  const prefix = `BRANDGUARD ${viewportWidth}PX${groupLabel} TOUCH TARGET`;
  if (evidence.viewport_width_px !== viewportWidth) {
    errors.push(
      `${prefix} NOT MEASURED (${stage}): route=${route}; ` +
      `expected=${viewportWidth}px; actual=${evidence.viewport_width_px}px`
    );
  }
  if (evidence.menu_open === false) {
    errors.push(
      `${prefix} MOBILE MENU CLOSED (${stage}): route=${route}; ` +
      `evidence=${JSON.stringify(evidence)}`
    );
  }
  if (evidence.visible_menu_link_count === 0) {
    errors.push(
      `${prefix} MOBILE MENU LINKS MISSING (${stage}): route=${route}; ` +
      `evidence=${JSON.stringify(evidence)}`
    );
  }
  if (evidence.visible_target_count === 0) {
    errors.push(`${prefix}S MISSING (${stage}): route=${route}`);
  }
  for (const target of evidence.targets) {
    if (target.target.width < BRANDGUARD_TOUCH_TARGET_MINIMUM_PX ||
        target.target.height < BRANDGUARD_TOUCH_TARGET_MINIMUM_PX) {
      errors.push(
        `${prefix} TOO SMALL (${stage}): route=${route}; ` +
        `minimum=${BRANDGUARD_TOUCH_TARGET_MINIMUM_PX}x` +
        `${BRANDGUARD_TOUCH_TARGET_MINIMUM_PX}px; target=${JSON.stringify(target)}`
      );
    }
  }
  for (const overlap of evidence.overlaps) {
    errors.push(
      `${prefix}S OVERLAP: route=${route}; stage=${stage}; ` +
      `controls=${JSON.stringify(overlap)}`
    );
  }
  return errors;
}

async function checkBrandGuardNarrowContent(page, viewportWidth = 320) {
  const evidence = await page.evaluate(({ selectors, tolerancePx }) => {
    const elements = {};
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (!element) {
        elements[selector] = null;
        continue;
      }

      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const round = value => Math.round(value * 100) / 100;
      const isBreadcrumbLabel =
        selector === '.askjamie-main .askjamie-breadcrumb .breadcrumb-label';
      const textRange = isBreadcrumbLabel ? document.createRange() : null;
      if (textRange) textRange.selectNodeContents(element);
      const textRects = textRange ? [...textRange.getClientRects()] : [];
      elements[selector] = {
        text_present: Boolean(element.textContent.trim()),
        visible: style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          Number.parseFloat(style.opacity) > 0 &&
          rect.width > 0 &&
          rect.height > 0,
        rect: Object.fromEntries(
          ['left', 'right', 'top', 'bottom', 'width', 'height']
            .map(property => [property, round(rect[property])])
        ),
        client_width: element.clientWidth,
        scroll_width: element.scrollWidth,
        client_height: element.clientHeight,
        scroll_height: element.scrollHeight,
        horizontal_content_overflow:
          element.scrollWidth > element.clientWidth + tolerancePx,
        vertical_content_overflow:
          element.scrollHeight > element.clientHeight + tolerancePx,
        horizontal_content_clipping:
          ['hidden', 'clip'].includes(style.overflowX) &&
          element.scrollWidth > element.clientWidth + tolerancePx,
        vertical_content_clipping:
          ['hidden', 'clip'].includes(style.overflowY) &&
          element.scrollHeight > element.clientHeight + tolerancePx,
        outside_viewport:
          rect.left < -tolerancePx || rect.right > window.innerWidth + tolerancePx,
        rendered_text_outside_viewport: isBreadcrumbLabel &&
          textRects.some(textRect =>
            textRect.left < -tolerancePx ||
            textRect.right > window.innerWidth + tolerancePx
          ),
      };
    }

    const breadcrumb = document.querySelector(selectors[0]);
    const heading = document.querySelector(selectors[2]);
    const notice = document.querySelector(
      '.askjamie-main .capability-transition'
    );
    const breadcrumbRect = breadcrumb?.getBoundingClientRect();
    const headingRect = heading?.getBoundingClientRect();
    const noticeRect = notice?.getBoundingClientRect();
    const heroOverlaps = [];
    const heroElements = selectors.slice(2)
      .map(selector => ({ selector, element: document.querySelector(selector) }))
      .filter(({ element }) => element);
    for (let first = 0; first < heroElements.length; first += 1) {
      const firstRect = heroElements[first].element.getBoundingClientRect();
      for (let second = first + 1; second < heroElements.length; second += 1) {
        const secondRect = heroElements[second].element.getBoundingClientRect();
        const horizontalOverlap = Math.min(firstRect.right, secondRect.right) -
          Math.max(firstRect.left, secondRect.left);
        const verticalOverlap = Math.min(firstRect.bottom, secondRect.bottom) -
          Math.max(firstRect.top, secondRect.top);
        if (horizontalOverlap > tolerancePx && verticalOverlap > tolerancePx) {
          heroOverlaps.push({
            first: heroElements[first].selector,
            second: heroElements[second].selector,
            overlap_height: round(verticalOverlap),
          });
        }
      }
    }
    return {
      viewport_width: window.innerWidth,
      document_scroll_width: document.documentElement.scrollWidth,
      document_overflow: document.documentElement.scrollWidth > window.innerWidth,
      breadcrumb_overlaps_heading: Boolean(
        breadcrumbRect && headingRect &&
        breadcrumbRect.bottom > headingRect.top + tolerancePx
      ),
      notice_overlaps_breadcrumb: Boolean(
        noticeRect && breadcrumbRect &&
        Math.min(noticeRect.right, breadcrumbRect.right) -
          Math.max(noticeRect.left, breadcrumbRect.left) > tolerancePx &&
        Math.min(noticeRect.bottom, breadcrumbRect.bottom) -
          Math.max(noticeRect.top, breadcrumbRect.top) > tolerancePx
      ),
      hero_overlaps: heroOverlaps,
      elements,
    };
  }, {
    selectors: BRANDGUARD_NARROW_CONTENT_SELECTORS,
    tolerancePx: BRANDGUARD_GEOMETRY_TOLERANCE_PX,
  });

  const errors = [];
  const errorPrefix = viewportWidth === 320
    ? 'BRANDGUARD 320PX'
    : `BRANDGUARD ${viewportWidth}PX`;
  if (evidence.viewport_width !== viewportWidth) {
    errors.push(
      `${errorPrefix} CONTENT NOT MEASURED: expected viewport=${viewportWidth}px; ` +
      `actual=${evidence.viewport_width}px`
    );
  }
  for (const [selector, element] of Object.entries(evidence.elements)) {
    if (!element) {
      errors.push(`${errorPrefix} CONTENT MISSING: ${selector}`);
      continue;
    }
    if (!element.text_present || !element.visible) {
      errors.push(
        `${errorPrefix} CONTENT NOT READABLE: ${selector}; ` +
        `evidence=${JSON.stringify(element)}`
      );
    }
    if (element.horizontal_content_clipping ||
        element.vertical_content_clipping) {
      errors.push(
        `${errorPrefix} CONTENT CLIPPED: ${selector}; ` +
        `evidence=${JSON.stringify(element)}`
      );
    }
    if (element.outside_viewport) {
      errors.push(
        `${errorPrefix} VIEWPORT OVERFLOW: ${selector}; ` +
        `evidence=${JSON.stringify(element)}`
      );
    }
    if (element.rendered_text_outside_viewport) {
      errors.push(
        `${errorPrefix} RENDERED TEXT OUTSIDE VIEWPORT: ${selector}; ` +
        `evidence=${JSON.stringify(element)}`
      );
    }
  }
  if (evidence.document_overflow) {
    errors.push(
      `${errorPrefix} VIEWPORT OVERFLOW: document scroll width ` +
      `${evidence.document_scroll_width}px exceeds viewport ${evidence.viewport_width}px`
    );
  }
  if (evidence.breadcrumb_overlaps_heading) {
    errors.push(
      `${errorPrefix} BREADCRUMB OVERLAPS HERO: ` +
      `evidence=${JSON.stringify(evidence.elements)}`
    );
  }
  if (evidence.notice_overlaps_breadcrumb) {
    errors.push(
      `${errorPrefix} ARRIVAL NOTICE OVERLAPS BREADCRUMB: ` +
      `evidence=${JSON.stringify(evidence.elements)}`
    );
  }
  for (const overlap of evidence.hero_overlaps) {
    errors.push(
      `${errorPrefix} HERO CONTENT OVERLAPS: ` +
      `evidence=${JSON.stringify(overlap)}`
    );
  }

  return { errors, evidence };
}

async function checkBrandGuardTextSpacingReadability(page, route, viewportWidth) {
  const errors = [];
  const spacing = {
    letter_spacing: '0.12em',
    word_spacing: '0.16em',
    line_height: '1.5',
  };
  await page.evaluate(() => {
    document.documentElement.dataset.responsiveQaTextSpacing = 'true';
  });
  const evidence = await page.evaluate(() => {
    const breadcrumb = document.querySelector(
      '.askjamie-main .askjamie-breadcrumb'
    );
    const label = breadcrumb?.querySelector('.breadcrumb-label');
    const heading = document.querySelector(
      '.askjamie-main .askjamie-hero-copy h1'
    );
    const round = value => Math.round(value * 100) / 100;
    const box = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return Object.fromEntries(
        ['left', 'right', 'top', 'bottom', 'width', 'height']
          .map(property => [property, round(rect[property])])
      );
    };
    const breadcrumbRect = breadcrumb?.getBoundingClientRect();
    const labelRect = label?.getBoundingClientRect();
    const headingRect = heading?.getBoundingClientRect();
    const labelStyle = label ? getComputedStyle(label) : null;
    const textRange = breadcrumb ? document.createRange() : null;
    if (textRange) textRange.selectNodeContents(breadcrumb);
    const textRects = textRange
      ? [...textRange.getClientRects()]
        .filter(rect => rect.width > 0 && rect.height > 0)
        .map(rect => ({
          left: round(rect.left),
          right: round(rect.right),
          top: round(rect.top),
          bottom: round(rect.bottom),
        }))
      : [];
    const overlaps = (first, second) => Boolean(
      first && second &&
      Math.min(first.right, second.right) > Math.max(first.left, second.left) &&
      Math.min(first.bottom, second.bottom) > Math.max(first.top, second.top)
    );
    const labelStyleValues = labelStyle
      ? {
          font_size_px: Number.parseFloat(labelStyle.fontSize),
          letter_spacing: labelStyle.letterSpacing,
          word_spacing: labelStyle.wordSpacing,
          line_height: labelStyle.lineHeight,
          overflow_x: labelStyle.overflowX,
          overflow_y: labelStyle.overflowY,
          client_width: label.clientWidth,
          scroll_width: label.scrollWidth,
          client_height: label.clientHeight,
          scroll_height: label.scrollHeight,
        }
      : null;
    const fontSize = labelStyleValues?.font_size_px ?? 0;
    const pixelValue = value => Number.parseFloat(value);
    const spacingMatches = (actual, multiplier) =>
      Number.isFinite(pixelValue(actual)) &&
      Math.abs(pixelValue(actual) - fontSize * multiplier) <= 0.05;
    return {
      viewport_width: window.innerWidth,
      spacing_marker_applied:
        document.documentElement.dataset.responsiveQaTextSpacing === 'true',
      breadcrumb_text_present: Boolean(breadcrumb?.textContent.trim()),
      breadcrumb_visible: Boolean(
        breadcrumb && breadcrumb.getClientRects().length &&
        getComputedStyle(breadcrumb).visibility !== 'hidden' &&
        Number.parseFloat(getComputedStyle(breadcrumb).opacity) > 0
      ),
      label_text_present: Boolean(label?.textContent.trim()),
      label_visible: Boolean(
        label && label.getClientRects().length &&
        labelStyle?.visibility !== 'hidden' &&
        Number.parseFloat(labelStyle?.opacity ?? '0') > 0
      ),
      heading_present: Boolean(heading),
      breadcrumb_box: box(breadcrumb),
      label_box: box(label),
      hero_heading_box: box(heading),
      text_fragments: textRects,
      text_inside_viewport: textRects.length > 0 &&
        textRects.every(rect => rect.left >= 0 && rect.right <= window.innerWidth),
      text_inside_breadcrumb: textRects.length > 0 && Boolean(breadcrumbRect) &&
        textRects.every(rect =>
          rect.left >= breadcrumbRect.left &&
          rect.right <= breadcrumbRect.right &&
          rect.top >= breadcrumbRect.top &&
          rect.bottom <= breadcrumbRect.bottom
        ),
      text_overlaps_hero: Boolean(headingRect) &&
        textRects.some(rect => overlaps(rect, headingRect)),
      breadcrumb_overlaps_hero: overlaps(breadcrumbRect, headingRect),
      clipped: Boolean(labelStyleValues) && (
        (['hidden', 'clip'].includes(labelStyleValues.overflow_x) &&
          labelStyleValues.scroll_width > labelStyleValues.client_width) ||
        (['hidden', 'clip'].includes(labelStyleValues.overflow_y) &&
          labelStyleValues.scroll_height > labelStyleValues.client_height)
      ),
      spacing: labelStyleValues,
      required_spacing_applied: Boolean(labelStyleValues) &&
        spacingMatches(labelStyleValues.letter_spacing, 0.12) &&
        spacingMatches(labelStyleValues.word_spacing, 0.16) &&
        spacingMatches(labelStyleValues.line_height, 1.5),
    };
  });
  const prefix = `BRANDGUARD ${viewportWidth}PX TEXT SPACING`;
  if (evidence.viewport_width !== viewportWidth) {
    errors.push(
      `${prefix} NOT MEASURED: route=${route}; expected viewport=${viewportWidth}px; ` +
      `actual=${evidence.viewport_width}px`
    );
  }
  if (!evidence.breadcrumb_text_present || !evidence.breadcrumb_visible ||
      !evidence.label_text_present || !evidence.label_visible ||
      !evidence.heading_present || !evidence.text_fragments.length) {
    errors.push(
      `${prefix} BREADCRUMB NOT READABLE: route=${route}; ` +
      `evidence=${JSON.stringify(evidence)}`
    );
  }
  if (!evidence.required_spacing_applied) {
    errors.push(
      `${prefix} OVERRIDES NOT APPLIED: route=${route}; ` +
      `expected letter-spacing=0.12em, word-spacing=0.16em, line-height=1.5; ` +
      `evidence=${JSON.stringify(evidence.spacing)}`
    );
  }
  if (!evidence.text_inside_viewport || !evidence.text_inside_breadcrumb ||
      evidence.clipped) {
    errors.push(
      `${prefix} BREADCRUMB TEXT CLIPPED OR OUTSIDE ITS VISIBLE AREA: ` +
      `route=${route}; evidence=${JSON.stringify(evidence)}`
    );
  }
  if (evidence.text_overlaps_hero || evidence.breadcrumb_overlaps_hero) {
    errors.push(
      `${prefix} BREADCRUMB OVERLAPS HERO: route=${route}; ` +
      `evidence=${JSON.stringify(evidence)}`
    );
  }
  return {
    errors,
    evidence: {
      route,
      viewport_width_px: viewportWidth,
      spacing_override: spacing,
      tolerance_px: 0,
      ...evidence,
    },
  };
}

async function checkBrandGuardBreadcrumbKeyboardFocus(page, route) {
  const errors = [];
  const evidence = {
    route,
    viewport_width_px: page.viewportSize()?.width ?? null,
    navigation_method: 'Tab from the immediately preceding visible control',
    expected_link_count: 0,
    links: [],
    current_page: null,
  };
  const prefix =
    `BRANDGUARD ${evidence.viewport_width_px}PX BREADCRUMB KEYBOARD`;
  const initial = await page.evaluate(() => {
    const breadcrumb = document.querySelector(
      '.askjamie-main .askjamie-breadcrumb'
    );
    const currentPage = breadcrumb?.querySelector('.breadcrumb-label');
    const currentStyle = currentPage ? getComputedStyle(currentPage) : null;
    const currentRect = currentPage?.getBoundingClientRect();
    const links = breadcrumb
      ? [...breadcrumb.querySelectorAll('a[href]')].map((link, index) => ({
          index,
          href: link.getAttribute('href') ?? '',
          resolved_href: link.href,
          accessible_name: link.getAttribute('aria-label')?.trim() ||
            link.innerText.replace(/\s+/g, ' ').trim() ||
            link.querySelector('img[alt]')?.alt.trim() || '',
          visible: (() => {
            const style = getComputedStyle(link);
            const rect = link.getBoundingClientRect();
            return style.display !== 'none' &&
              style.visibility !== 'hidden' &&
              Number.parseFloat(style.opacity) > 0 &&
              rect.width > 0 && rect.height > 0;
          })(),
        }))
      : [];
    return {
      current_page: currentPage
        ? {
            text: currentPage.textContent.replace(/\s+/g, ' ').trim(),
            aria_current: currentPage.getAttribute('aria-current'),
            is_link: currentPage.matches('a[href]'),
            visible: currentStyle.display !== 'none' &&
              currentStyle.visibility !== 'hidden' &&
              Number.parseFloat(currentStyle.opacity) > 0 &&
              currentRect.width > 0 && currentRect.height > 0,
          }
        : null,
      links,
    };
  });
  evidence.expected_link_count = initial.links.length;
  evidence.current_page = initial.current_page;
  if (!initial.current_page?.text || !initial.current_page.visible ||
      initial.current_page.is_link ||
      initial.current_page.aria_current !== 'page') {
    errors.push(
      `${prefix} CURRENT PAGE ITEM INACCESSIBLE: ` +
      `route=${route}; evidence=${JSON.stringify(initial.current_page)}`
    );
  }
  if (initial.links.length === 0) {
    errors.push(
      `${prefix} LINKS MISSING: route=${route}; ` +
      'expected at least one published breadcrumb destination'
    );
    return { errors, evidence };
  }

  for (const link of initial.links) {
    const linkEvidence = {
      ...link,
      reached_by_tab: false,
      focus_visible: false,
      outline_visible: false,
      viewport_clipped: null,
      hero_overlap: null,
      clipping_ancestors: [],
    };
    if (!link.visible || !link.accessible_name || !link.href ||
        new URL(link.resolved_href).origin !== new URL(page.url()).origin) {
      errors.push(
        `${prefix} LINK INACCESSIBLE: route=${route}; ` +
        `link=${JSON.stringify(link)}`
      );
    }

    const tabStart = await page.evaluate(linkIndex => {
      const breadcrumb = document.querySelector(
        '.askjamie-main .askjamie-breadcrumb'
      );
      const target = breadcrumb?.querySelectorAll('a[href]')[linkIndex];
      if (!target) return null;
      const focusable = [...document.querySelectorAll(
        'a[href], area[href], button:not([disabled]), input:not([disabled]), ' +
        'select:not([disabled]), textarea:not([disabled]), ' +
        '[tabindex]:not([tabindex="-1"])'
      )].filter(element => {
        const style = getComputedStyle(element);
        return !element.matches(':disabled') &&
          !element.closest('[inert], [aria-hidden="true"]') &&
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          Number.parseFloat(style.opacity) > 0 &&
          element.getClientRects().length > 0;
      });
      const targetIndex = focusable.indexOf(target);
      const preceding = targetIndex > 0 ? focusable[targetIndex - 1] : null;
      if (!preceding) {
        return { found: false, target_is_focusable: targetIndex >= 0 };
      }
      preceding.focus();
      return {
        found: true,
        focused: document.activeElement === preceding,
        preceding_tag: preceding.tagName.toLowerCase(),
        preceding_name: preceding.getAttribute('aria-label') ||
          preceding.innerText?.replace(/\s+/g, ' ').trim() || '',
      };
    }, link.index);
    if (!tabStart?.found || !tabStart.focused) {
      errors.push(
        `${prefix} TAB START MISSING: route=${route}; ` +
        `link=${JSON.stringify(link)}; preceding=${JSON.stringify(tabStart)}`
      );
      evidence.links.push(linkEvidence);
      continue;
    }

    await page.keyboard.press('Tab');
    const focused = await page.evaluate(linkIndex => {
      const breadcrumb = document.querySelector(
        '.askjamie-main .askjamie-breadcrumb'
      );
      const target = breadcrumb?.querySelectorAll('a[href]')[linkIndex];
      const active = document.activeElement;
      if (!target || active !== target) return null;

      const round = value => Math.round(value * 100) / 100;
      const rect = target.getBoundingClientRect();
      const heading = document.querySelector(
        '.askjamie-main .askjamie-hero-copy h1'
      );
      const headingRect = heading?.getBoundingClientRect();
      const style = getComputedStyle(target);
      const outlineWidth = Number.parseFloat(style.outlineWidth) || 0;
      const outlineOffset = Number.parseFloat(style.outlineOffset) || 0;
      const extent = Math.max(0, outlineWidth + outlineOffset);
      const ring = {
        left: rect.left - extent,
        top: rect.top - extent,
        right: rect.right + extent,
        bottom: rect.bottom + extent,
      };
      const clippingAncestors = [];
      for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
        const ancestorStyle = getComputedStyle(ancestor);
        const ancestorRect = ancestor.getBoundingClientRect();
        const clippedAxes = [];
        if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowX) &&
            (ring.left < ancestorRect.left - 0.5 ||
             ring.right > ancestorRect.right + 0.5)) {
          clippedAxes.push('horizontal');
        }
        if (['hidden', 'clip', 'scroll', 'auto'].includes(ancestorStyle.overflowY) &&
            (ring.top < ancestorRect.top - 0.5 ||
             ring.bottom > ancestorRect.bottom + 0.5)) {
          clippedAxes.push('vertical');
        }
        if (ancestorStyle.clipPath !== 'none' ||
            ancestorStyle.contain.split(/\s+/).includes('paint')) {
          clippedAxes.push('clip-path-or-paint-containment');
        }
        if (clippedAxes.length > 0) {
          clippingAncestors.push({
            tag: ancestor.tagName.toLowerCase(),
            class_name: typeof ancestor.className === 'string'
              ? ancestor.className
              : null,
            clipped_axes: [...new Set(clippedAxes)],
          });
        }
        if (ancestor === document.documentElement) break;
      }
      const outlineVisible = style.outlineStyle !== 'none' &&
        outlineWidth >= 2 &&
        style.outlineColor !== 'transparent' &&
        !/rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/.test(style.outlineColor);
      return {
        reached_by_tab: true,
        focus_visible: target.matches(':focus-visible'),
        outline_visible: outlineVisible,
        outline_style: style.outlineStyle,
        outline_width_px: round(outlineWidth),
        outline_offset_px: round(outlineOffset),
        outline_color: style.outlineColor,
        target_bounds: Object.fromEntries(
          ['left', 'top', 'right', 'bottom']
            .map(property => [property, round(rect[property])])
        ),
        focus_ring_bounds: Object.fromEntries(
          Object.entries(ring).map(([key, value]) => [key, round(value)])
        ),
        viewport_clipped: ring.left < -0.5 ||
          ring.top < -0.5 ||
          ring.right > window.innerWidth + 0.5 ||
          ring.bottom > window.innerHeight + 0.5,
        hero_overlap: Boolean(
          headingRect &&
          Math.min(ring.right, headingRect.right) >
            Math.max(ring.left, headingRect.left) + 0.5 &&
          Math.min(ring.bottom, headingRect.bottom) >
            Math.max(ring.top, headingRect.top) + 0.5
        ),
        hero_heading_bounds: headingRect
          ? Object.fromEntries(
              ['left', 'top', 'right', 'bottom']
                .map(property => [property, round(headingRect[property])])
            )
          : null,
        clipping_ancestors: clippingAncestors,
      };
    }, link.index);
    if (focused) Object.assign(linkEvidence, focused);
    else linkEvidence.reached_by_tab = false;
    if (!focused?.focus_visible || !focused.outline_visible) {
      errors.push(
        `${prefix} FOCUS INDICATOR NOT VISIBLE: route=${route}; ` +
        `link=${JSON.stringify(linkEvidence)}`
      );
    }
    if (focused?.viewport_clipped || focused?.clipping_ancestors.length > 0) {
      errors.push(
        `${prefix} FOCUS INDICATOR CLIPPED: route=${route}; ` +
        `link=${JSON.stringify(linkEvidence)}`
      );
    }
    if (focused?.hero_overlap) {
      errors.push(
        `${prefix} FOCUS INDICATOR COLLIDES WITH HERO: route=${route}; ` +
        `link=${JSON.stringify(linkEvidence)}`
      );
    }
    evidence.links.push(linkEvidence);
  }

  return { errors, evidence };
}

function formatBrandGuardFallbackFontReadabilityResult(
  contentCheck,
  blockedFontRequests,
  path,
  viewportWidth
) {
  const errors = contentCheck.errors.map(error =>
    `BRANDGUARD FALLBACK-FONT READABILITY FAILED: route=${path}; ` +
    `viewport=${viewportWidth}px; ${error}`
  );
  if (!blockedFontRequests.some(request =>
    request.host === 'fonts.googleapis.com' &&
    request.resource_type === 'stylesheet'
  )) {
    errors.push(
      `BRANDGUARD FALLBACK-FONT READABILITY UNVERIFIED: route=${path}; ` +
      `viewport=${viewportWidth}px; web-font stylesheet request was not observed and blocked`
    );
  }
  return {
    errors,
    evidence: {
      route: path,
      viewport_width_px: viewportWidth,
      web_font_requests_blocked: blockedFontRequests.some(request =>
        request.host === 'fonts.googleapis.com' &&
        request.resource_type === 'stylesheet'
      ),
      blocked_requests: blockedFontRequests,
      readability: contentCheck.evidence,
    },
  };
}

async function checkBrandGuardFallbackFontReadability(
  browser,
  path,
  url,
  viewport,
  externalBlock
) {
  const blockedFontRequests = [];
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await page.route('**/*', route => {
      const request = route.request();
      const requestUrl = new URL(request.url());
      if (BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname)) {
        blockedFontRequests.push({
          host: requestUrl.hostname,
          path: requestUrl.pathname,
          resource_type: request.resourceType(),
        });
        return route.abort();
      }
      if (externalBlock.test(request.url())) return route.abort();
      return route.continue();
    });
    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
    });
    await page.waitForFunction(() => {
      const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => new URL(link.href).pathname === '/assets/css/theme.css');
      return Boolean(themeStylesheet?.sheet) &&
        (!themeStylesheet.hasAttribute('data-deferred-styles') ||
          themeStylesheet.media === 'all');
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ));
    });

    const transitionDismiss = page.locator(
      '[data-transition-dialog][open] [data-transition-dismiss]'
    );
    if (await transitionDismiss.isVisible()) await transitionDismiss.click();

    const contentCheck = await checkBrandGuardNarrowContent(page, viewport.width);
    return formatBrandGuardFallbackFontReadabilityResult(
      contentCheck,
      blockedFontRequests,
      path,
      viewport.width
    );
  } catch (error) {
    return {
      errors: [
        `BRANDGUARD FALLBACK-FONT READABILITY UNAVAILABLE: route=${path}; ` +
        `viewport=${viewport.width}px; ${error.message.split('\n')[0]}`,
      ],
      evidence: {
        route: path,
        viewport_width_px: viewport.width,
        web_font_requests_blocked: blockedFontRequests.some(request =>
          request.host === 'fonts.googleapis.com' &&
          request.resource_type === 'stylesheet'
        ),
        blocked_requests: blockedFontRequests,
        readability: null,
      },
    };
  } finally {
    await context.close();
  }
}

function formatPublicFallbackFontReadabilityResult(
  contentCheck,
  blockedFontRequests,
  path,
  viewportWidth
) {
  const errors = contentCheck.findings.map(finding =>
    `PUBLIC FALLBACK-FONT READABILITY FAILED: route=${path}; ` +
    `viewport=${viewportWidth}px; ${finding.type}: ` +
    `${JSON.stringify(finding.text)} at ${finding.selector}; ` +
    `evidence=${JSON.stringify(finding.evidence)}`
  );
  const stylesheetBlocked = blockedFontRequests.some(request =>
    request.host === 'fonts.googleapis.com' &&
    request.resource_type === 'stylesheet'
  );
  if (!stylesheetBlocked) {
    errors.push(
      `PUBLIC FALLBACK-FONT READABILITY UNVERIFIED: route=${path}; ` +
      `viewport=${viewportWidth}px; hosted web-font stylesheet request was not observed and blocked`
    );
  }
  return {
    errors,
    evidence: {
      route: path,
      viewport_width_px: viewportWidth,
      font_outage_policy: 'requests to fonts.googleapis.com and fonts.gstatic.com are aborted',
      web_font_stylesheet_request_blocked: stylesheetBlocked,
      web_font_asset_request_observed: blockedFontRequests.some(request =>
        request.host === 'fonts.gstatic.com' &&
        request.resource_type === 'font'
      ),
      blocked_requests: blockedFontRequests,
      readability: contentCheck,
    },
  };
}

async function capturePublicFallbackFontReadability(page) {
  return page.evaluate(() => {
    const findings = [];
    const blocks = new Map();
    const ignoredTags = new Set([
      'SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'CANVAS', 'TEMPLATE',
    ]);
    const round = value => Math.round(value * 100) / 100;
    const describeElement = element => {
      const parts = [];
      for (let current = element;
           current && current !== document.body;
           current = current.parentElement) {
        const tag = current.tagName.toLowerCase();
        const classes = typeof current.className === 'string'
          ? current.className.trim().split(/\s+/).filter(Boolean).slice(0, 3)
          : [];
        const classSuffix = classes.length ? `.${classes.join('.')}` : '';
        const siblings = current.parentElement
          ? [...current.parentElement.children].filter(
            sibling => sibling.tagName === current.tagName
          )
          : [];
        const position = siblings.length > 1
          ? `:nth-of-type(${siblings.indexOf(current) + 1})`
          : '';
        parts.unshift(`${tag}${classSuffix}${position}`);
      }
      return parts.join(' > ') || 'body';
    };
    const isVisible = element => {
      if (element.closest('.sr-only, [aria-hidden="true"]')) return false;
      const skipLink = element.closest('.skip-link, .okh-skip-link');
      if (skipLink && !skipLink.matches(':focus, :focus-visible')) return false;
      for (let current = element; current; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === 'none' ||
            style.visibility === 'hidden' ||
            style.visibility === 'collapse' ||
            Number.parseFloat(style.opacity) <= 0.01 ||
            current.inert ||
            ignoredTags.has(current.tagName)) {
          return false;
        }
      }
      if (typeof element.checkVisibility === 'function' &&
          !element.checkVisibility({
            checkOpacity: true,
            checkVisibilityCSS: true,
            contentVisibilityAuto: true,
          })) {
        return false;
      }
      return element.getClientRects().length > 0;
    };
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT
    );
    while (walker.nextNode()) {
      const textNode = walker.currentNode;
      const text = textNode.textContent.replace(/\s+/g, ' ').trim();
      const element = textNode.parentElement;
      if (!text || !element || !isVisible(element) ||
          element.closest('svg, script, style, noscript, canvas, template')) {
        continue;
      }
      const range = document.createRange();
      range.selectNode(textNode);
      const rects = [...range.getClientRects()]
        .filter(rect => rect.width > 0 && rect.height > 0)
        .map(rect => ({
          left: round(rect.left),
          right: round(rect.right),
          top: round(rect.top + window.scrollY),
          bottom: round(rect.bottom + window.scrollY),
        }));
      if (rects.length === 0) continue;

      let block = blocks.get(element);
      if (!block) {
        block = {
          element,
          selector: describeElement(element),
          texts: [],
          rects: [],
        };
        blocks.set(element, block);
      }
      block.texts.push(text);
      block.rects.push(...rects);

      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor);
        const box = ancestor.getBoundingClientRect();
        for (const rect of rects) {
          const clippedX = ['hidden', 'clip'].includes(style.overflowX) &&
            (rect.left < box.left - 1 || rect.right > box.right + 1);
          const clippedY = ['hidden', 'clip'].includes(style.overflowY) &&
            (rect.top < box.top + window.scrollY - 1 ||
              rect.bottom > box.bottom + window.scrollY + 1);
          if (clippedX || clippedY) {
            findings.push({
              type: 'text clipped',
              selector: block.selector,
              text,
              evidence: {
                clipping_ancestor: describeElement(ancestor),
                clipped_axes: [
                  ...(clippedX ? ['horizontal'] : []),
                  ...(clippedY ? ['vertical'] : []),
                ],
                text_rect: rect,
              },
            });
            break;
          }
        }
        if (ancestor === document.body) break;
      }

      const outsideViewport = rects.find(rect =>
        rect.left < -1 || rect.right > window.innerWidth + 1
      );
      if (outsideViewport) {
        findings.push({
          type: 'text overflows viewport',
          selector: block.selector,
          text,
          evidence: {
            viewport_width_px: window.innerWidth,
            text_rect: outsideViewport,
          },
        });
      }
      const style = getComputedStyle(element);
      if (['hidden', 'clip'].includes(style.overflowX) &&
          element.scrollWidth > element.clientWidth + 1) {
        findings.push({
          type: 'text block overflows its clipped container',
          selector: block.selector,
          text,
          evidence: {
            client_width_px: element.clientWidth,
            scroll_width_px: element.scrollWidth,
            overflow_x: style.overflowX,
          },
        });
      }
    }

    const blockList = [...blocks.values()].map(block => ({
      ...block,
      text: block.texts.join(' ').replace(/\s+/g, ' ').trim().slice(0, 180),
    }));
    const overlaps = [];
    const isAncestor = (ancestor, element) =>
      ancestor !== element && ancestor.contains(element);
    for (let firstIndex = 0; firstIndex < blockList.length; firstIndex += 1) {
      const first = blockList[firstIndex];
      for (let secondIndex = firstIndex + 1;
           secondIndex < blockList.length;
           secondIndex += 1) {
        const second = blockList[secondIndex];
        if (isAncestor(first.element, second.element) ||
            isAncestor(second.element, first.element)) {
          continue;
        }
        let overlap = null;
        for (const firstRect of first.rects) {
          for (const secondRect of second.rects) {
            const width = Math.min(firstRect.right, secondRect.right) -
              Math.max(firstRect.left, secondRect.left);
            const height = Math.min(firstRect.bottom, secondRect.bottom) -
              Math.max(firstRect.top, secondRect.top);
            if (width > 1 && height > 1) {
              overlap = {
                width_px: round(width),
                height_px: round(height),
                first_text_rect: firstRect,
                second_text_rect: secondRect,
              };
              break;
            }
          }
          if (overlap) break;
        }
        if (overlap) {
          overlaps.push({
            first: { selector: first.selector, text: first.text },
            second: { selector: second.selector, text: second.text },
            overlap,
          });
        }
      }
    }
    for (const overlap of overlaps) {
      findings.push({
        type: 'text blocks overlap',
        selector: `${overlap.first.selector} and ${overlap.second.selector}`,
        text: `${overlap.first.text} / ${overlap.second.text}`,
        evidence: overlap.overlap,
      });
    }
    return {
      viewport_width_px: window.innerWidth,
      visible_text_block_count: blockList.length,
      findings,
    };
  });
}

async function checkPublicFallbackFontReadability(
  browser,
  path,
  url,
  viewport,
  externalBlock
) {
  const blockedFontRequests = [];
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  try {
    await page.route('**/*', route => {
      const request = route.request();
      const requestUrl = new URL(request.url());
      if (BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname)) {
        blockedFontRequests.push({
          host: requestUrl.hostname,
          path: requestUrl.pathname,
          resource_type: request.resourceType(),
        });
        return route.abort();
      }
      if (externalBlock.test(request.url())) return route.abort();
      return route.continue();
    });
    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
    });
    await page.waitForFunction(() => {
      const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => new URL(link.href).pathname === '/assets/css/theme.css');
      return Boolean(themeStylesheet?.sheet) &&
        (!themeStylesheet.hasAttribute('data-deferred-styles') ||
          themeStylesheet.media === 'all');
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ));
    });

    const transitionDismiss = page.locator(
      '[data-transition-dialog][open] [data-transition-dismiss]'
    );
    if (await transitionDismiss.isVisible()) await transitionDismiss.click();
    await page.waitForFunction(() =>
      !document.querySelector('[data-transition-dialog][open]')
    );
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    )));
    await page.evaluate(() =>
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
    );
    await page.waitForFunction(() => window.scrollY === 0);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    )));

    const contentCheck = await capturePublicFallbackFontReadability(page);
    return formatPublicFallbackFontReadabilityResult(
      contentCheck,
      blockedFontRequests,
      path,
      viewport.width
    );
  } catch (error) {
    return {
      errors: [
        `PUBLIC FALLBACK-FONT READABILITY UNAVAILABLE: route=${path}; ` +
        `viewport=${viewport.width}px; ${error.message.split('\n')[0]}`,
      ],
      evidence: {
        route: path,
        viewport_width_px: viewport.width,
        font_outage_policy: 'requests to fonts.googleapis.com and fonts.gstatic.com are aborted',
        web_font_stylesheet_request_blocked: blockedFontRequests.some(request =>
          request.host === 'fonts.googleapis.com' &&
          request.resource_type === 'stylesheet'
        ),
        web_font_asset_request_observed: blockedFontRequests.some(request =>
          request.host === 'fonts.gstatic.com' &&
          request.resource_type === 'font'
        ),
        blocked_requests: blockedFontRequests,
        readability: null,
      },
    };
  } finally {
    await context.close();
  }
}

function parseBrandGuardForcedColorsColor(value) {
  const text = String(value ?? '').trim().toLowerCase();
  if (text === 'transparent') return [0, 0, 0, 0];
  const match = text.match(/^rgba?\((.*)\)$/);
  if (!match) return null;
  const [channelText, alphaText] = match[1].split('/');
  const channels = channelText.includes(',')
    ? channelText.split(',').map(part => part.trim())
    : channelText.trim().split(/\s+/);
  if (channels.length < 3 || channels.length > 4) return null;
  const parseChannel = channel => {
    const percentage = channel.endsWith('%');
    const number = Number.parseFloat(channel);
    return Number.isFinite(number)
      ? Math.min(255, Math.max(0, percentage ? number * 2.55 : number))
      : null;
  };
  const rgb = channels.slice(0, 3).map(parseChannel);
  const alphaChannel = alphaText?.trim() ?? channels[3] ?? '1';
  const alphaNumber = Number.parseFloat(alphaChannel);
  const alpha = Number.isFinite(alphaNumber)
    ? Math.min(1, Math.max(0, alphaChannel.endsWith('%')
      ? alphaNumber / 100
      : alphaNumber))
    : null;
  return rgb.every(channel => channel !== null) && alpha !== null
    ? [...rgb, alpha]
    : null;
}

function compositeBrandGuardForcedColorsColor(foreground, background, opacity = 1) {
  const source = Array.isArray(foreground)
    ? foreground
    : parseBrandGuardForcedColorsColor(foreground);
  const under = Array.isArray(background)
    ? background
    : parseBrandGuardForcedColorsColor(background);
  if (!source || !under) return null;
  const alpha = source[3] * Math.min(1, Math.max(0, opacity));
  const outAlpha = alpha + under[3] * (1 - alpha);
  if (outAlpha <= 0) return null;
  return [
    ...source.slice(0, 3).map((channel, index) =>
      (channel * alpha + under[index] * under[3] * (1 - alpha)) / outAlpha
    ),
    outAlpha,
  ];
}

function formatBrandGuardForcedColorsColor(channels) {
  if (!channels || channels[3] < 0.999) return null;
  return `rgb(${channels.slice(0, 3).map(channel => Math.round(channel)).join(', ')})`;
}

function calculateBrandGuardForcedColorsContrast({
  foregroundColor,
  backgroundLayers,
  canvasColor,
  effectiveOpacity,
}) {
  let background = parseBrandGuardForcedColorsColor(canvasColor);
  if (!background || background[3] < 0.999) {
    return {
      foreground_color: foregroundColor,
      background_color: backgroundLayers?.[0] ?? null,
      effective_foreground_color: null,
      effective_background_color: null,
      background_layers: backgroundLayers ?? [],
      canvas_color: canvasColor ?? null,
      effective_opacity: effectiveOpacity,
      contrast_ratio: null,
      minimum_contrast_ratio: 4.5,
    };
  }
  for (const layer of [...(backgroundLayers ?? [])].reverse()) {
    background = compositeBrandGuardForcedColorsColor(layer, background);
    if (!background || background[3] < 0.999) break;
  }
  const effectiveForeground = background
    ? compositeBrandGuardForcedColorsColor(
      foregroundColor,
      background,
      effectiveOpacity
    )
    : null;
  const luminance = channels => {
    if (!channels || channels[3] < 0.999) return null;
    const [red, green, blue] = channels.slice(0, 3).map(channel => {
      const normalized = channel / 255;
      return normalized <= 0.04045
        ? normalized / 12.92
        : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const foregroundLuminance = luminance(effectiveForeground);
  const backgroundLuminance = luminance(background);
  return {
    foreground_color: foregroundColor,
    background_color: backgroundLayers?.[0] ?? canvasColor ?? null,
    effective_foreground_color: formatBrandGuardForcedColorsColor(effectiveForeground),
    effective_background_color: formatBrandGuardForcedColorsColor(background),
    background_layers: backgroundLayers ?? [],
    canvas_color: canvasColor ?? null,
    effective_opacity: effectiveOpacity,
    contrast_ratio: foregroundLuminance === null || backgroundLuminance === null
      ? null
      : Math.round(
        ((Math.max(foregroundLuminance, backgroundLuminance) + 0.05) /
          (Math.min(foregroundLuminance, backgroundLuminance) + 0.05)) * 100
      ) / 100,
    minimum_contrast_ratio: 4.5,
  };
}

function formatBrandGuardForcedColorsReadabilityResult(
  evidence,
  path,
  viewportWidth
) {
  const errors = [];
  const prefix =
    `BRANDGUARD FORCED-COLORS READABILITY FAILED: ` +
    `route=${path}; viewport=${viewportWidth}px; `;
  if (!evidence.forced_colors_active) {
    errors.push(
      `BRANDGUARD FORCED-COLORS MODE UNAVAILABLE: route=${path}; ` +
      `viewport=${viewportWidth}px; browser did not activate the forced-colors media feature`
    );
  }

  for (const selector of BRANDGUARD_GEOMETRY_SELECTORS) {
    const element = evidence.elements?.[selector];
    if (!element) {
      errors.push(`${prefix}${selector} is missing or was not measured`);
      continue;
    }
    if (!element.text_present || !element.visible) {
      errors.push(
        `${prefix}${selector} is not visible; evidence=${JSON.stringify(element)}`
      );
    }
    if (element.clipped) {
      errors.push(
        `${prefix}${selector} is clipped; evidence=${JSON.stringify(element)}`
      );
    }
    if (element.outside_viewport) {
      errors.push(
        `${prefix}${selector} extends beyond the viewport; ` +
        `evidence=${JSON.stringify(element)}`
      );
    }
    const contrast = element.contrast;
    if (!contrast || contrast.contrast_ratio === null) {
      errors.push(
        `${prefix}text block=${selector} contrast could not be measured; ` +
        `foreground=${JSON.stringify(contrast?.foreground_color ?? null)}; ` +
        `effective background=${JSON.stringify(contrast?.effective_background_color ?? null)}`
      );
    } else if (contrast.contrast_ratio < contrast.minimum_contrast_ratio) {
      errors.push(
        `${prefix}text block=${selector} contrast=${contrast.contrast_ratio}:1 ` +
        `< ${contrast.minimum_contrast_ratio}:1; ` +
        `foreground=${contrast.effective_foreground_color}; ` +
        `background=${contrast.effective_background_color}; ` +
        `measured_colors=${JSON.stringify(contrast)}`
      );
    }
  }
  if (evidence.document_overflow) {
    errors.push(
      `${prefix}document scroll width ${evidence.document_scroll_width}px ` +
      `exceeds viewport ${evidence.viewport_width}px`
    );
  }
  for (const overlap of evidence.overlaps ?? []) {
    errors.push(
      `${prefix}text elements overlap; evidence=${JSON.stringify(overlap)}`
    );
  }

  return {
    errors,
    evidence: {
      route: path,
      viewport_width_px: viewportWidth,
      ...evidence,
    },
  };
}

async function checkBrandGuardForcedColorsReadability(
  browser,
  path,
  url,
  viewport,
  externalBlock
) {
  let context = null;
  try {
    context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      colorScheme: 'light',
      serviceWorkers: 'block',
    });
    const page = await context.newPage();
    await page.emulateMedia({ forcedColors: 'active' });
    await page.route('**/*', route =>
      externalBlock.test(route.request().url())
        ? route.abort()
        : route.continue()
    );
    await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
    });
    await page.waitForFunction(() => {
      const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
        .find(link => new URL(link.href).pathname === '/assets/css/theme.css');
      return Boolean(themeStylesheet?.sheet) &&
        (!themeStylesheet.hasAttribute('data-deferred-styles') ||
          themeStylesheet.media === 'all');
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    const hero = page.locator('.askjamie-main .askjamie-hero-inner');
    await hero.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => {
      const heroInner = document.querySelector(
        '.askjamie-main .askjamie-hero-inner'
      );
      return Boolean(heroInner) &&
        Number.parseFloat(getComputedStyle(heroInner).opacity) >= 0.999;
    }, undefined, { timeout: BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ));
    });

    const evidence = await page.evaluate((selectors) => {
      const round = value => Math.round(value * 100) / 100;
      const box = rect => Object.fromEntries(
        ['left', 'right', 'top', 'bottom', 'width', 'height']
          .map(property => [property, round(rect[property])])
      );
      const elements = {};
      for (const selector of selectors) {
        const element = document.querySelector(selector);
        if (!element) {
          elements[selector] = null;
          continue;
        }
        const rect = element.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(element);
        const textRects = [...range.getClientRects()];
        const textBounds = textRects.length
          ? box(textRects.reduce((bounds, textRect) => ({
            left: Math.min(bounds.left, textRect.left),
            right: Math.max(bounds.right, textRect.right),
            top: Math.min(bounds.top, textRect.top),
            bottom: Math.max(bounds.bottom, textRect.bottom),
            width: Math.max(bounds.right, textRect.right) -
              Math.min(bounds.left, textRect.left),
            height: Math.max(bounds.bottom, textRect.bottom) -
              Math.min(bounds.top, textRect.top),
          }), {
            left: Number.POSITIVE_INFINITY,
            right: Number.NEGATIVE_INFINITY,
            top: Number.POSITIVE_INFINITY,
            bottom: Number.NEGATIVE_INFINITY,
          }))
          : null;
        const style = getComputedStyle(element);
        let visible = Boolean(
          element.textContent.trim() &&
          element.getClientRects().length > 0 &&
          rect.width > 0 &&
          rect.height > 0 &&
          textBounds?.width > 0 &&
          textBounds?.height > 0
        );
        let clipped = false;
        let effectiveOpacity = 1;
        for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
          const ancestorStyle = getComputedStyle(ancestor);
          const ancestorRect = ancestor.getBoundingClientRect();
          effectiveOpacity *= Number.parseFloat(ancestorStyle.opacity || '1');
          if (ancestorStyle.display === 'none' ||
              ['hidden', 'collapse'].includes(ancestorStyle.visibility)) {
            visible = false;
          }
          if (textBounds &&
              ((['hidden', 'clip'].includes(ancestorStyle.overflowX) &&
                (textBounds.left < ancestorRect.left - 1 ||
                  textBounds.right > ancestorRect.right + 1)) ||
               (['hidden', 'clip'].includes(ancestorStyle.overflowY) &&
                (textBounds.top < ancestorRect.top - 1 ||
                  textBounds.bottom > ancestorRect.bottom + 1)))) {
            clipped = true;
          }
          if (ancestor === document.documentElement) break;
        }
        const color = style.color.trim().toLowerCase();
        if (effectiveOpacity <= 0 ||
            color === 'transparent' ||
            /rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/.test(color)) {
          visible = false;
        }
        elements[selector] = {
          text_present: Boolean(element.textContent.trim()),
          visible,
          clipped,
          outside_viewport: rect.left < -1 ||
            rect.right > window.innerWidth + 1,
          rect: box(rect),
          text_bounds: textBounds,
          color: style.color,
          effective_opacity: round(effectiveOpacity),
          overflow_x: style.overflowX,
          overflow_y: style.overflowY,
          contrast_input: {
            foreground_color: style.color,
            background_layers: (() => {
              const layers = [];
              for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
                layers.push(getComputedStyle(ancestor).backgroundColor);
                if (ancestor === document.documentElement) break;
              }
              return layers;
            })(),
            effective_opacity: round(effectiveOpacity),
          },
        };
      }
      const canvasProbe = document.createElement('span');
      canvasProbe.style.color = 'CanvasText';
      canvasProbe.style.backgroundColor = 'Canvas';
      canvasProbe.style.position = 'fixed';
      canvasProbe.style.visibility = 'hidden';
      document.documentElement.append(canvasProbe);
      const canvasColor = getComputedStyle(canvasProbe).backgroundColor;
      canvasProbe.remove();
      for (const element of Object.values(elements)) {
        if (element?.contrast_input) {
          element.contrast_input.canvas_color = canvasColor;
        }
      }
      const measured = selectors
        .map(selector => ({ selector, element: document.querySelector(selector) }))
        .filter(({ element }) => element);
      const overlaps = [];
      for (let first = 0; first < measured.length; first += 1) {
        for (let second = first + 1; second < measured.length; second += 1) {
          const firstRect = measured[first].element.getBoundingClientRect();
          const secondRect = measured[second].element.getBoundingClientRect();
          const overlapWidth = Math.min(firstRect.right, secondRect.right) -
            Math.max(firstRect.left, secondRect.left);
          const overlapHeight = Math.min(firstRect.bottom, secondRect.bottom) -
            Math.max(firstRect.top, secondRect.top);
          if (overlapWidth > 1 && overlapHeight > 1) {
            overlaps.push({
              first: measured[first].selector,
              second: measured[second].selector,
              overlap_width_px: round(overlapWidth),
              overlap_height_px: round(overlapHeight),
            });
          }
        }
      }
      return {
        forced_colors_active: matchMedia('(forced-colors: active)').matches,
        viewport_width: window.innerWidth,
        document_scroll_width: document.documentElement.scrollWidth,
        document_overflow:
          document.documentElement.scrollWidth > window.innerWidth + 1,
        elements,
        overlaps,
      };
    }, BRANDGUARD_GEOMETRY_SELECTORS);
    for (const element of Object.values(evidence.elements)) {
      if (!element?.contrast_input) continue;
      element.contrast = calculateBrandGuardForcedColorsContrast(
        {
          foregroundColor: element.contrast_input.foreground_color,
          backgroundLayers: element.contrast_input.background_layers,
          canvasColor: element.contrast_input.canvas_color,
          effectiveOpacity: element.contrast_input.effective_opacity,
        }
      );
      delete element.contrast_input;
    }
    return formatBrandGuardForcedColorsReadabilityResult(
      evidence,
      path,
      viewport.width
    );
  } catch (error) {
    return {
      errors: [
        `BRANDGUARD FORCED-COLORS READABILITY UNAVAILABLE: route=${path}; ` +
        `viewport=${viewport.width}px; ${error.message.split('\n')[0]}`,
      ],
      evidence: {
        route: path,
        viewport_width_px: viewport.width,
        forced_colors_active: false,
        elements: null,
        overlaps: null,
      },
    };
  } finally {
    await context?.close();
  }
}

async function checkUniverseDiagramGeometry(
  page,
  releaseMermaid,
  mermaidRequestSeen,
  universePageMapFontRequestSeen,
  universePageMapFontFailureSeen,
  releaseUniversePageMapFontRequests,
  universePageMapFontRequests,
  universePageMapFontResponses,
  universePageMapFontFailures
) {
  const errors = [];
  let before = null;
  let after = null;
  let pageMapCheck = null;
  let pageMapFontGeometry = null;
  let pageMapRotationReflow = null;
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
      color_scheme: document.documentElement.getAttribute('data-color-scheme'),
      prefers_dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
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

    themeActive = await page.waitForFunction(() => {
      const themeLink = document.querySelector('link[data-deferred-styles]');
      return themeLink?.media === 'all' && Boolean(themeLink.sheet);
    }, undefined, { timeout: 15000 }).then(() => true, () => false);

    if (themeActive) {
      await waitForTwoFrames();
      // The desktop hero has a finite scroll reveal that changes its transform.
      // Let that existing animation finish before isolating Mermaid's layout.
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
    if (!hasDarkUniverseTheme(before)) {
      errors.push(
        'UNIVERSE DIAGRAM DARK THEME NOT ACTIVE: expected a pinned dark color scheme and dark ' +
        `browser preference before rendering; before=${JSON.stringify({
          color_scheme: before.color_scheme,
          prefers_dark: before.prefers_dark,
        })}`
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

    const heroRenderScheduled = await page.waitForFunction(
      () => window.__responsiveQaMermaidRenderQueue?.length > 0,
      undefined,
      { timeout: 10000 }
    ).then(() => true, () => false);
    if (!heroRenderScheduled) {
      errors.push(
        'UNIVERSE DIAGRAM RENDER NOT SCHEDULED: hero did not reach the Mermaid observer'
      );
    } else if (!await releaseResponsiveQaMermaidRender(page)) {
      errors.push('UNIVERSE DIAGRAM RENDER NOT RELEASED: no held hero Mermaid render was available');
    }

    mermaidRequested = await mermaidRequestSeen;
    if (!mermaidRequested) {
      errors.push(
        'UNIVERSE DIAGRAM NOT INITIALIZED: Mermaid module request was not observed after bringing the hero into view'
      );
    }
    releaseMermaid();

    if (mermaidRequested) {
      rendered = await page.waitForFunction(() => {
        const diagram = document.querySelector('.askjamie-hero--universe .mermaid');
        return diagram?.dataset.universeReady === '1' && Boolean(diagram.querySelector('svg .node'));
      }, undefined, { timeout: 20000 }).then(() => true, () => false);
    }
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
    if (after && !hasDarkUniverseTheme(after)) {
      errors.push(
        'UNIVERSE DIAGRAM DARK THEME NOT ACTIVE: expected a pinned dark color scheme and dark ' +
        `browser preference after rendering; after=${JSON.stringify({
          color_scheme: after.color_scheme,
          prefers_dark: after.prefers_dark,
        })}`
      );
    }

    // Finish the hero render before opening any page map so Mermaid never has
    // multiple diagrams rendering at once during this geometry check.
    pageMapCheck = await checkUniversePageMapGroupsGeometry(page, waitForTwoFrames);
    errors.push(...pageMapCheck.errors);
    if (releaseUniversePageMapFontRequests) {
      pageMapFontGeometry = await checkUniversePageMapWebFontGeometry(
        page,
        releaseUniversePageMapFontRequests,
        universePageMapFontRequestSeen,
        universePageMapFontFailureSeen,
        universePageMapFontRequests,
        universePageMapFontResponses,
        universePageMapFontFailures,
        waitForTwoFrames
      );
      errors.push(...pageMapFontGeometry.errors);
      if (page.viewportSize()?.width === 320) {
        pageMapRotationReflow = await checkUniversePageMapRotationReflow(
          page,
          waitForTwoFrames
        );
        errors.push(...pageMapRotationReflow.errors);
      }
    }
  } catch (error) {
    errors.push(`UNIVERSE DIAGRAM GEOMETRY CHECK ERROR: ${error.message.split('\n')[0]}`);
  } finally {
    releaseMermaid();
    releaseUniversePageMapFontRequests?.();
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

  const pageMapEvidence = {
    group_count: pageMapCheck?.groupCount ?? 0,
    initially_collapsed: pageMapCheck?.initiallyCollapsed ?? false,
    all_groups_rendered_svg_ready:
      pageMapCheck?.groups.length > 0 &&
      pageMapCheck.groups.every(group => group.rendered_svg_ready),
    groups: pageMapCheck?.groups ?? [],
    group_switch_pan_retention: pageMapCheck?.group_switch_pan_retention ?? null,
  };

  return {
    errors,
    evidence: {
      threshold_px: UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX,
      theme_active_before_render: themeActive,
      dark_theme_active_before_render: hasDarkUniverseTheme(before),
      dark_theme_active_after_render: hasDarkUniverseTheme(after),
      mermaid_module_requested: mermaidRequested,
      rendered_svg_ready: rendered,
      expected_reservation_px: (page.viewportSize().width <= 640 ? 18 : 22) * 16,
      before,
      after,
      shifts,
      page_map: pageMapEvidence,
      ...(pageMapFontGeometry
        ? { page_map_web_font_geometry: pageMapFontGeometry.evidence }
        : {}),
      ...(pageMapRotationReflow
        ? { page_map_rotation_reflow: pageMapRotationReflow.evidence }
        : {}),
    },
  };
}

async function checkUniversePageMapThemeFailure(browser, viewport, url) {
  const errors = [];
  const stylesheetPath = '/assets/css/theme.css';
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
  });
  const page = await context.newPage();
  let stylesheetFailure = null;
  const failureEvent = page.waitForEvent('response', {
    predicate: response =>
      new URL(response.url()).pathname === stylesheetPath &&
      response.status() >= 500,
    timeout: 15000,
  }).then(response => ({
    status: response.status(),
    url: response.url(),
  }), () => null);

  try {
    await page.route('**/*', async route => {
      if (new URL(route.request().url()).pathname === stylesheetPath) {
        await route.fulfill({
          status: 503,
          contentType: 'text/css',
          body: '/* controlled theme stylesheet failure */',
        });
        return;
      }
      await route.continue();
    });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const groups = page.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
    await groups.first().waitFor({ state: 'visible', timeout: 20000 });
    stylesheetFailure = await failureEvent;
    if (!stylesheetFailure || stylesheetFailure.status !== 503) {
      errors.push(
        `UNIVERSE PAGE MAP THEME FAILURE NOT OBSERVED: expected HTTP 503 from ${stylesheetPath}`
      );
    }

    const stylesheetActivated = await page.waitForFunction(() => {
      const link = [...document.querySelectorAll('link[data-deferred-styles]')]
        .find(candidate => new URL(candidate.href).pathname === '/assets/css/theme.css');
      return link?.media === 'all';
    }, undefined, { timeout: 5000 }).then(() => true, () => false);
    if (!stylesheetActivated) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE CHECK INVALID: deferred local stylesheet was not activated'
      );
    }

    const themeNotice = page.locator('#universe-theme-status');
    const noticeVisible = await themeNotice.waitFor({ state: 'visible', timeout: 5000 })
      .then(() => true, () => false);
    const noticeDetails = await themeNotice.evaluate(element => ({
      text: element.textContent.trim(),
      role: element.getAttribute('role'),
      live: element.getAttribute('aria-live'),
      display: getComputedStyle(element).display,
    })).catch(() => null);
    if (!noticeVisible || noticeDetails?.role !== 'status' ||
        noticeDetails?.live !== 'polite' ||
        !/visual theme could not load/i.test(noticeDetails?.text ?? '') ||
        noticeDetails?.display === 'none') {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE NOTICE MISSING: expected a visible, polite status explaining the unavailable theme'
      );
    }

    const groupCount = await groups.count();
    const initialCollapsed = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    );
    if (!initialCollapsed) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE COLLAPSED STATE INVALID: groups were open before interaction'
      );
    }

    const firstGroup = groups.first();
    const summary = firstGroup.locator('summary');
    await summary.focus();
    await page.keyboard.press('Enter');
    const openedByKeyboard = await firstGroup.evaluate(group => group.open);
    const otherGroupsCollapsedAfterOpen = await groups.evaluateAll(elements =>
      elements.slice(1).every(element => !element.open)
    );
    if (!openedByKeyboard) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE KEYBOARD OPEN FAILED: Enter did not open the first group'
      );
    }
    if (!otherGroupsCollapsedAfterOpen) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE COLLAPSED STATE INVALID: opening one group opened another'
      );
    }

    const links = firstGroup.locator('a[data-universe-node]');
    const accessibleLinks = await links.evaluateAll(elements =>
      elements.map(element => ({
        href: element.getAttribute('href'),
        visible: element.getClientRects().length > 0 &&
          getComputedStyle(element).visibility !== 'hidden' &&
          getComputedStyle(element).display !== 'none',
      }))
    );
    const linksAvailable = accessibleLinks.length > 0 &&
      accessibleLinks.every(link => link.href && link.visible);
    if (!linksAvailable) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE LINKS UNAVAILABLE: expected visible page links in the open group'
      );
    }

    await summary.focus();
    await page.keyboard.press('Enter');
    const closedByKeyboard = !(await firstGroup.evaluate(group => group.open));
    const collapsedAfterCheck = await groups.evaluateAll(elements =>
      elements.every(element => !element.open)
    );
    if (!closedByKeyboard) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE KEYBOARD CLOSE FAILED: Enter did not close the first group'
      );
    }
    if (!collapsedAfterCheck) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE COLLAPSED STATE INVALID: groups remained open after the check'
      );
    }

    const themeReady = await page.evaluate(() => {
      const link = [...document.querySelectorAll('link[data-deferred-styles]')]
        .find(candidate => new URL(candidate.href).pathname === '/assets/css/theme.css');
      try {
        return Boolean(link?.sheet?.cssRules.length);
      } catch {
        return false;
      }
    });
    if (themeReady) {
      errors.push(
        'UNIVERSE PAGE MAP THEME FAILURE READINESS INVALID: failed stylesheet was reported ready'
      );
    }

    return {
      errors,
      evidence: {
        stylesheet_path: stylesheetPath,
        stylesheet_failure: stylesheetFailure,
        theme_failure_notice_visible: noticeVisible,
        theme_failure_notice: noticeDetails,
        theme_readiness: themeReady ? 'ready' : 'unavailable',
        geometry_comparison_available: false,
        geometry_status: 'unavailable_theme_stylesheet_failure',
        group_count: groupCount,
        initially_collapsed: initialCollapsed,
        accessible_page_links_available: linksAvailable,
        accessible_page_link_count: accessibleLinks.length,
        opened_by_keyboard: openedByKeyboard,
        closed_by_keyboard: closedByKeyboard,
        collapsed_after_check: collapsedAfterCheck,
      },
    };
  } catch (error) {
    return {
      errors: [
        ...errors,
        `UNIVERSE PAGE MAP THEME FAILURE CHECK ERROR: ${error.message.split('\n')[0]}`,
      ],
      evidence: {
        stylesheet_path: stylesheetPath,
        stylesheet_failure: stylesheetFailure,
        theme_readiness: 'unavailable',
        geometry_comparison_available: false,
      },
    };
  } finally {
    await context.close();
  }
}

async function checkUniverseMermaidFailure(browser, viewport, url) {
  const errors = [];
  const mermaidPath = '/assets/vendor/mermaid/mermaid.esm.min.mjs';
  const externalBlock =
    /fonts\.(gstatic|googleapis)\.com|google-analytics\.com|googletagmanager\.com|cdn\.jsdelivr\.net/;
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
  });
  const page = await context.newPage();
  let noJsContext;

  const isMermaidRequest = request =>
    new URL(request.url()).pathname === mermaidPath;
  const mermaidRequest = page.waitForRequest(isMermaidRequest, { timeout: 15000 })
    .then(request => request.url(), () => null);
  const mermaidFailure = page.waitForEvent('requestfailed', {
    predicate: isMermaidRequest,
    timeout: 15000,
  }).then(request => request.failure()?.errorText || 'failed', () => null);
  const renderWarning = page.waitForEvent('console', {
    predicate: message =>
      message.type() === 'warning' &&
      message.text().startsWith('[mermaid-init] render error:'),
    timeout: 15000,
  }).then(message => message.text(), () => null);

  const inspectFallback = targetPage => targetPage.evaluate(() => {
    const figure = document.querySelector(
      '.askjamie-hero--universe .askjamie-mermaid-shell'
    );
    const diagram = figure?.querySelector('.mermaid');
    const status = figure?.querySelector('[data-mermaid-failure-status]');
    const caption = figure?.querySelector('figcaption');
    const links = [...(figure?.querySelectorAll('.link-list a') ?? [])];
    const visible = element => Boolean(
      element &&
      element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== 'hidden' &&
      getComputedStyle(element).display !== 'none'
    );

    return {
      caption: caption?.textContent.trim() ?? '',
      caption_visible: visible(caption),
      failure_status_visible: visible(status),
      failure_status_text: status?.textContent.trim() ?? '',
      failure_status_exposed: Boolean(status && !status.hasAttribute('hidden') &&
        getComputedStyle(status).display !== 'none' &&
        getComputedStyle(status).visibility !== 'hidden'),
      failure_status_sr_only: status?.classList.contains('sr-only') ?? false,
      failure_status_role: status?.getAttribute('role') ?? null,
      failure_status_live: status?.getAttribute('aria-live') ?? null,
      links: links.map(link => ({
        text: link.textContent.trim(),
        href: link.getAttribute('href'),
        visible: visible(link),
      })),
      ready_state: diagram?.dataset.universeReady ?? null,
      has_svg_node: Boolean(diagram?.querySelector('svg .node')),
      noscript_visible: visible(figure?.querySelector('.mermaid-noscript')),
      noscript_text: figure?.querySelector('.mermaid-noscript')?.textContent.trim() ?? '',
    };
  });

  const clickPageMapLink = async targetPage => {
    const link = targetPage.locator(
      '.askjamie-hero--universe .link-list a[href="#indexed-map-title"]'
    );
    if (await link.count() !== 1 || !(await link.isVisible())) return false;
    await link.click({ timeout: 5000 });
    return targetPage.evaluate(() =>
      location.hash === '#indexed-map-title' &&
      Boolean(document.querySelector('#indexed-map-title'))
    );
  };

  const checkGeneratedPageMapFallback = async targetPage => {
    const groups = targetPage.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
    const groupCount = await groups.count();
    if (groupCount === 0) {
      return {
        errors: ['UNIVERSE MERMAID FAILURE PAGE MAP MISSING: no generated page-map group was found'],
        evidence: { group_count: 0, groups: [] },
      };
    }

    const errors = [];
    const groupEvidence = [];
    let destinationSweepDurationMs = 0;
    let destinationCount = 0;
    for (let groupIndex = 0; groupIndex < groupCount; groupIndex += 1) {
      const groupLabel = `group ${groupIndex + 1}`;
      const group = targetPage.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR).nth(groupIndex);
      const renderWarning = targetPage.waitForEvent('console', {
        predicate: message =>
          message.type() === 'warning' &&
          message.text().startsWith('[mermaid-init] render error:'),
        timeout: 10000,
      }).then(message => message.text(), () => null);

      await group.locator('summary').click({ timeout: 5000 });
      const opened = await group.evaluate(element => element.open);
      if (!opened) {
        errors.push(
          `UNIVERSE MERMAID FAILURE PAGE MAP DID NOT OPEN: ${groupLabel} stayed closed`
        );
      }

      await group.locator('.mermaid').scrollIntoViewIfNeeded();
      const renderStarted = await targetPage.waitForFunction(index => {
        const currentGroup = document.querySelectorAll(
          '.universe-map-generated .universe-map-group'
        )[index];
        return currentGroup?.querySelector('.mermaid')
          ?.dataset.mermaidRendered === '1';
      }, groupIndex, { timeout: 10000 }).then(() => true, () => false);
      if (!renderStarted) {
        errors.push(
          `UNIVERSE MERMAID FAILURE PAGE MAP NOT ATTEMPTED: ${groupLabel} did not start rendering`
        );
      }

      const renderFailureCaught = await renderWarning;
      if (!renderFailureCaught) {
        errors.push(
          `UNIVERSE MERMAID FAILURE PAGE MAP NOT CAUGHT: ${groupLabel} render failure was not observed`
        );
      }

      const state = await targetPage.evaluate(index => {
        const isVisible = element => Boolean(
          element &&
          element.getClientRects().length > 0 &&
          getComputedStyle(element).visibility !== 'hidden' &&
          getComputedStyle(element).display !== 'none'
        );
        const group = document.querySelectorAll(
          '.universe-map-generated .universe-map-group'
        )[index];
        const diagram = group?.querySelector('.mermaid');
        const status = group?.querySelector('[data-mermaid-failure-status]');
        const caption = group?.querySelector('figcaption');
        const links = [...(group?.querySelectorAll('.link-list a') ?? [])];
        return {
          open: Boolean(group?.open),
          summary: group?.querySelector('summary')?.textContent.trim() ?? '',
          summary_visible: isVisible(group?.querySelector('summary')),
          caption: caption?.textContent.trim() ?? '',
          caption_visible: isVisible(caption),
          failure_status_visible: isVisible(status),
          failure_status_text: status?.textContent.trim() ?? '',
          failure_status_role: status?.getAttribute('role') ?? null,
          failure_status_live: status?.getAttribute('aria-live') ?? null,
          links: links.map(link => ({
            text: link.textContent.trim(),
            href: link.getAttribute('href'),
            node_id: link.getAttribute('data-universe-node'),
            visible: isVisible(link),
          })),
          render_started: diagram?.dataset.mermaidRendered === '1',
          ready_state: diagram?.dataset.universeReady ?? null,
          has_svg_node: Boolean(diagram?.querySelector('svg .node')),
        };
      }, groupIndex);

      if (!state.open || !state.summary_visible || !state.summary) {
        errors.push(
          `UNIVERSE MERMAID FAILURE PAGE MAP SUMMARY UNAVAILABLE: ${groupLabel} ` +
          JSON.stringify(state)
        );
      }
      if (!state.caption_visible || !state.caption) {
        errors.push(
          `UNIVERSE MERMAID FAILURE HID PAGE MAP CAPTION: ${groupLabel} ` +
          JSON.stringify(state)
        );
      }
      if (!state.failure_status_visible ||
          !state.failure_status_text.includes('could not be displayed') ||
          state.failure_status_role !== 'status' ||
          state.failure_status_live !== 'polite') {
        errors.push(
          `UNIVERSE MERMAID FAILURE STATUS UNAVAILABLE OR INACCESSIBLE: ${groupLabel} ` +
          JSON.stringify(state)
        );
      }
      if (state.links.length < 1 || state.links.some(link => !link.visible || !link.href)) {
        errors.push(
          `UNIVERSE MERMAID FAILURE MADE GENERATED PAGE LINKS UNAVAILABLE: ${groupLabel} ` +
          JSON.stringify(state.links)
        );
      }
      if (state.ready_state === '1' && !state.has_svg_node) {
        errors.push(
          `UNIVERSE MERMAID FAILURE MARKED PAGE MAP READY WITHOUT A RENDERED SVG: ${groupLabel} ` +
          JSON.stringify({
            ready_state: state.ready_state,
            has_svg_node: state.has_svg_node,
          })
        );
      }

      const expectedOrigin = new URL(url).origin;
      const destinationEvidence = [];
      const destinationDurationsMs = [];
      const pageContext = targetPage.context();
      const baselineLivePageCount = pageContext.pages().length;
      const resourceCleanup = {
        baseline_live_page_count: baselineLivePageCount,
        max_live_page_count: baselineLivePageCount,
        all_destinations_closed: true,
        samples: [],
      };
      const recordResourceCleanup = (linkIndex, destinationResult) => {
        const livePageCount = pageContext.pages().length;
        const closed = livePageCount === baselineLivePageCount;
        resourceCleanup.max_live_page_count = Math.max(
          resourceCleanup.max_live_page_count,
          livePageCount
        );
        resourceCleanup.all_destinations_closed =
          resourceCleanup.all_destinations_closed && closed;
        resourceCleanup.samples.push({
          destination_index: linkIndex + 1,
          destination_count: state.links.length,
          live_page_count: livePageCount,
          expected_live_page_count: baselineLivePageCount,
          destination_page_closed: closed,
        });
        if (!closed) {
          errors.push(
            `UNIVERSE PAGE-MAP RESOURCE COUNT GREW: ${groupLabel}, after destination ` +
            `${linkIndex + 1} of ${state.links.length}; live pages ` +
            `${livePageCount}, expected ${baselineLivePageCount}` +
            `${destinationResult?.expected_path
              ? ` (${JSON.stringify(destinationResult.expected_path)})`
              : ''}`
          );
        }
      };
      const groupDestinationSweepStartedAt = performance.now();
      for (let linkIndex = 0; linkIndex < state.links.length; linkIndex += 1) {
        const destinationStartedAt = performance.now();
        const link = state.links[linkIndex];
        const label = link.text || '(untitled link)';
        destinationCount += 1;
        let destination;
        try {
          destination = new URL(link.href, targetPage.url());
        } catch {
          const result = {
            link_index: linkIndex + 1,
            text: label,
            href: link.href,
            expected_path: null,
            reached_path: null,
            http_status: null,
            passed: false,
            failed_stage: 'destination_validation',
            error: 'invalid destination URL',
          };
          result.destination_duration_ms = Math.round(
            (performance.now() - destinationStartedAt) * 10
          ) / 10;
          destinationEvidence.push(result);
          errors.push(
            `UNIVERSE MERMAID FAILURE BROKE GENERATED PAGE LINK: ${groupLabel}, ` +
            `link ${linkIndex + 1} "${label}" (${JSON.stringify(link.href)}): ` +
            'invalid destination URL'
          );
          destinationDurationsMs.push(performance.now() - destinationStartedAt);
          recordResourceCleanup(linkIndex, result);
          continue;
        }

        const result = {
          link_index: linkIndex + 1,
          text: label,
          href: link.href,
          expected_path: destination.pathname,
          reached_path: null,
          reached_title: null,
          reached_heading: null,
          reached_body_message: null,
          http_status: null,
          not_found_page: false,
          passed: false,
          failed_stage: null,
          error: null,
        };
        if (!link.node_id || !link.visible || !link.href) {
          result.failed_stage = 'destination_validation';
          result.error = 'ordinary page link is missing its node ID, visibility, or href';
        } else if (destination.origin !== expectedOrigin) {
          result.failed_stage = 'destination_validation';
          result.error = `destination is not local to ${expectedOrigin}`;
        } else {
          const ordinaryPageLink = group.locator('.link-list a').nth(linkIndex);
          let destinationPage;
          let currentStage = 'tab_open';
          let cancelNavigationWait = () => {};
          try {
            const context = targetPage.context();
            const expectedResponseUrl = new URL(destination.href);
            expectedResponseUrl.hash = '';
            const navigationPromise = new Promise(resolve => {
              let settled = false;
              let timeout;
              const finish = outcome => {
                if (settled) return;
                settled = true;
                clearTimeout(timeout);
                context.off('response', onResponse);
                context.off('requestfailed', onRequestFailed);
                resolve(outcome);
              };
              const matchesDestination = request => {
                if (!request.isNavigationRequest()) return false;
                let initialRequest = request;
                while (initialRequest.redirectedFrom()) {
                  initialRequest = initialRequest.redirectedFrom();
                }
                const initialRequestUrl = new URL(initialRequest.url());
                initialRequestUrl.hash = '';
                return initialRequestUrl.href === expectedResponseUrl.href;
              };
              const onResponse = response => {
                const request = response.request();
                if (!matchesDestination(request)) return;
                const isRedirect = response.status() >= 300 &&
                  response.status() < 400 &&
                  Boolean(response.headers().location);
                if (!isRedirect) {
                  finish({ response });
                }
              };
              const onRequestFailed = request => {
                if (matchesDestination(request)) finish({ request });
              };
              context.on('response', onResponse);
              context.on('requestfailed', onRequestFailed);
              timeout = setTimeout(
                () => finish({ error: new Error('navigation response timed out') }),
                10000
              );
              cancelNavigationWait = () =>
                finish({ error: new Error('navigation wait cancelled') });
            });
            const destinationPagePromise = context.waitForEvent('page', {
              timeout: 10000,
            }).then(page => {
              destinationPage = page;
              return page;
            });
            const [openedPage] = await Promise.all([
              destinationPagePromise,
              ordinaryPageLink.click({
                modifiers: ['ControlOrMeta'],
                timeout: 5000,
              }),
            ]);
            currentStage = 'navigation_response';
            const navigation = await navigationPromise;
            if (navigation.error) throw navigation.error;
            if (navigation.request) {
              const failure = navigation.request.failure()?.errorText ||
                'navigation request failed';
              throw new Error(`page.goto: ${failure}`);
            }
            const response = navigation.response;
            result.http_status = response?.status() ?? null;
            currentStage = 'expected_url';
            await openedPage.waitForURL(
              url => url.origin === expectedOrigin &&
                url.pathname === destination.pathname,
              {
                waitUntil: 'domcontentloaded',
                timeout: 10000,
              }
            );
            currentStage = 'document_read';
            await openedPage.waitForLoadState('domcontentloaded', {
              timeout: 10000,
            });
            const reachedUrl = new URL(openedPage.url());
            const reachedDocument = await openedPage.evaluate(() => ({
              title: document.title,
              heading: document.querySelector('main h1, h1')?.textContent.trim() ?? '',
              bodyMessages: [...document.querySelectorAll(
                'main p, main [role="alert"], main [aria-live]'
              )].filter(element =>
                element.getClientRects().length > 0 &&
                getComputedStyle(element).visibility !== 'hidden' &&
                getComputedStyle(element).display !== 'none'
              ).map(element => element.textContent.trim()),
            }));
            result.reached_path = reachedUrl.pathname;
            result.reached_title = reachedDocument.title;
            result.reached_heading = reachedDocument.heading;
            result.reached_body_message = findNotFoundBodyMessage(
              reachedDocument.bodyMessages
            );
            result.not_found_page = isNotFoundDocument(
              result.reached_title,
              result.reached_heading,
              reachedDocument.bodyMessages
            );
            const reachedExpectedPath =
              reachedUrl.origin === expectedOrigin &&
              reachedUrl.pathname === destination.pathname;
            const successfulResponse = Boolean(
              response && response.status() < 400
            );
            result.passed =
              reachedExpectedPath &&
              successfulResponse &&
              !result.not_found_page;
            if (!result.passed) {
              if (!reachedExpectedPath) {
                result.failed_stage = 'expected_url';
                result.error = 'navigation did not reach the expected local path';
              } else if (!successfulResponse) {
                result.failed_stage = 'http_response';
                result.error = 'destination response was unsuccessful';
              } else if (result.not_found_page) {
                result.failed_stage = 'document_validation';
                result.error = 'destination rendered a not-found page';
              }
            }
          } catch (error) {
            result.failed_stage = currentStage;
            result.reached_path = (() => {
              try {
                return new URL(destinationPage?.url() ?? targetPage.url()).pathname;
              } catch {
                return null;
              }
            })();
            if (destinationPage) {
              const reachedDocument = await destinationPage.evaluate(() => ({
                title: document.title,
                heading: document.querySelector('main h1, h1')?.textContent.trim() ?? '',
                bodyMessages: [...document.querySelectorAll(
                  'main p, main [role="alert"], main [aria-live]'
                )].filter(element =>
                  element.getClientRects().length > 0 &&
                  getComputedStyle(element).visibility !== 'hidden' &&
                  getComputedStyle(element).display !== 'none'
                ).map(element => element.textContent.trim()),
              })).catch(() => null);
              result.reached_title = reachedDocument?.title ?? null;
              result.reached_heading = reachedDocument?.heading ?? null;
              result.reached_body_message = findNotFoundBodyMessage(
                reachedDocument?.bodyMessages ?? []
              );
            }
            result.error = `navigation failed: ${error.message.split('\n')[0]}`;
          } finally {
            cancelNavigationWait();
            await destinationPage?.close().catch(() => {});
          }
        }

        recordResourceCleanup(linkIndex, result);
        destinationEvidence.push(result);
        result.destination_duration_ms = Math.round(
          (performance.now() - destinationStartedAt) * 10
        ) / 10;
        if (!result.passed) {
          const reachedPageIdentity = [
            result.reached_title
              ? `title ${JSON.stringify(result.reached_title)}`
              : 'title unavailable',
            result.reached_heading &&
              result.reached_heading !== result.reached_title
              ? `heading ${JSON.stringify(result.reached_heading)}`
              : null,
            result.reached_body_message
              ? `body message ${JSON.stringify(result.reached_body_message)}`
              : null,
          ].filter(Boolean).join(', ');
          errors.push(
            `UNIVERSE MERMAID FAILURE BROKE GENERATED PAGE LINK: ${groupLabel}, ` +
            `link ${linkIndex + 1} "${label}" (${JSON.stringify(link.href)}): ` +
            `${result.error} (failed stage ${result.failed_stage}); ` +
            `expected path ${JSON.stringify(result.expected_path)}, ` +
            `reached path ${JSON.stringify(result.reached_path)}, ` +
            `reached document ${reachedPageIdentity}, ` +
            `HTTP ${result.http_status ?? 'unavailable'}`
          );
        }
        destinationDurationsMs.push(performance.now() - destinationStartedAt);
      }
      const groupDestinationSweepDurationMs = Math.round(
        performance.now() - groupDestinationSweepStartedAt
      );
      const destinationSweepScaling =
        assessDestinationSweepScaling(destinationDurationsMs);
      if (destinationSweepScaling.slowed) {
        errors.push(
          `UNIVERSE PAGE-MAP DESTINATION SWEEP SLOWED: ${groupLabel}, ` +
          `${destinationSweepScaling.destination_count} destinations; ` +
          `median per-destination time increased from ` +
          `${destinationSweepScaling.early_median_ms}ms to ` +
          `${destinationSweepScaling.late_median_ms}ms ` +
          `(${destinationSweepScaling.slowdown_ratio}x)`
        );
      }
      destinationSweepDurationMs += groupDestinationSweepDurationMs;
      const ordinaryPageLinkWorks =
        destinationEvidence.length === state.links.length &&
        destinationEvidence.length > 0 &&
        destinationEvidence.every(destination => destination.passed);
      const ordinaryPageLinkEvidence = destinationEvidence[0] ?? null;

      groupEvidence.push({
        group_index: groupIndex + 1,
        render_started: renderStarted,
        render_failure_caught: Boolean(renderFailureCaught),
        state,
        ordinary_page_link_works: ordinaryPageLinkWorks,
        ordinary_page_link: ordinaryPageLinkEvidence,
        all_destinations_work: ordinaryPageLinkWorks,
        destination_count: destinationEvidence.length,
        destination_sweep_duration_ms: groupDestinationSweepDurationMs,
        destination_sweep_scaling: destinationSweepScaling,
        resource_cleanup: resourceCleanup,
        destinations: destinationEvidence,
      });

    }

    return {
      errors,
      evidence: {
        group_count: groupCount,
        all_groups_checked: groupEvidence.length === groupCount,
        all_groups_render_failures_caught:
          groupEvidence.length === groupCount &&
          groupEvidence.every(group => group.render_failure_caught),
        all_group_links_visible:
          groupEvidence.length === groupCount &&
          groupEvidence.every(group =>
            group.state.links.length > 0 &&
            group.state.links.every(link => link.visible && link.href)
          ),
        all_groups_navigated:
          groupEvidence.length === groupCount &&
          groupEvidence.every(group => group.all_destinations_work),
        all_group_destinations_navigated:
          groupEvidence.length === groupCount &&
          groupEvidence.every(group => group.all_destinations_work),
        destination_sweep: {
          duration_ms: destinationSweepDurationMs,
          destination_count: destinationCount,
        },
        resource_cleanup: {
          all_destinations_closed:
            groupEvidence.length === groupCount &&
            groupEvidence.every(group =>
              group.resource_cleanup.all_destinations_closed &&
              group.resource_cleanup.samples.length === group.destination_count
            ),
          max_live_page_count: Math.max(
            0,
            ...groupEvidence.map(group =>
              group.resource_cleanup.max_live_page_count
            )
          ),
          groups: groupEvidence.map(group => ({
            group_index: group.group_index,
            destination_count: group.destination_count,
            ...group.resource_cleanup,
          })),
        },
        groups: groupEvidence,
      },
    };
  };

  const checkGeneratedPageMapWithoutJavaScript = async targetPage => {
    const groups = targetPage.locator(UNIVERSE_PAGE_MAP_GROUP_SELECTOR);
    const groupCount = await groups.count();
    if (groupCount === 0) {
      return {
        errors: ['UNIVERSE NO-JAVASCRIPT PAGE MAP MISSING: no generated page-map group was found'],
        evidence: { group_count: 0, group: null, ordinary_page_link_works: false },
      };
    }

    const errors = [];
    const group = groups.first();
    try {
      await group.locator('summary').click({ timeout: 5000 });
    } catch (error) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT PAGE MAP DID NOT OPEN: ${error.message.split('\n')[0]}`
      );
    }

    const state = await group.evaluate(element => {
      const isVisible = node => Boolean(
        node &&
        node.getClientRects().length > 0 &&
        getComputedStyle(node).visibility !== 'hidden' &&
        getComputedStyle(node).display !== 'none'
      );
      const summary = element.querySelector('summary');
      const caption = element.querySelector('figcaption');
      const links = [...element.querySelectorAll('.link-list a')];
      return {
        open: element.open,
        summary: summary?.textContent.trim() ?? '',
        summary_visible: isVisible(summary),
        caption: caption?.textContent.trim() ?? '',
        caption_visible: isVisible(caption),
        links: links.map(link => ({
          text: link.textContent.trim(),
          href: link.getAttribute('href'),
          visible: isVisible(link),
        })),
      };
    });

    if (!state.open || !state.summary_visible || !state.summary) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT PAGE MAP SUMMARY UNAVAILABLE: ${JSON.stringify(state)}`
      );
    }
    if (!state.caption_visible || !state.caption) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT PAGE MAP CAPTION UNAVAILABLE: ${JSON.stringify(state)}`
      );
    }
    if (state.links.length < 1 || state.links.some(link => !link.visible || !link.href)) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT GENERATED PAGE LINKS UNAVAILABLE: ${JSON.stringify(state.links)}`
      );
    }

    const summary = group.locator(':scope > summary');
    await summary.focus();
    let linkFocus;
    const firstFallbackLink = group.locator('.link-list a').first();
    for (let tab = 0; tab < 100; tab += 1) {
      await targetPage.keyboard.press('Tab');
      linkFocus = await firstFallbackLink.evaluate(link => {
        const style = getComputedStyle(link);
        const channels = value => value.match(/^rgba?\((.+)\)$/)?.[1]
          .split(/[,/ ]+/).filter(Boolean).map(Number);
        const luminance = value => {
          const values = channels(value);
          if (!values) return null;
          const linear = values.slice(0, 3).map(channel => {
            const normalized = channel / 255;
            return normalized <= 0.04045 ? normalized / 12.92 :
              ((normalized + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
        };
        const probe = document.createElement('span');
        probe.style.color = 'Canvas';
        link.parentElement.append(probe);
        const canvas = getComputedStyle(probe).color;
        probe.remove();
        const outline = channels(style.outlineColor);
        const background = channels(canvas);
        const rendered = outline && background
          ? `rgb(${outline.slice(0, 3).map((channel, index) =>
              Math.round(channel * (outline[3] ?? 1) +
                background[index] * (1 - (outline[3] ?? 1)))
            ).join(', ')})`
          : '';
        const first = luminance(rendered);
        const second = luminance(canvas);
        return {
          selector: '.universe-map-group .link-list a:focus-visible',
          focused: link === document.activeElement && link.matches(':focus-visible'),
          color: style.color,
          outline_color: style.outlineColor,
          outline_width: style.outlineWidth,
          outline_style: style.outlineStyle,
          background: canvas,
          contrast: first === null || second === null ? null :
            (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
        };
      });
      if (linkFocus.focused) break;
    }
    const forcedColorsEvidence = await group.evaluate(element => {
      const channels = value => {
        const match = value?.match(/^rgba?\((.+)\)$/);
        if (!match) return null;
        const parsed = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
        return parsed.length >= 3
          ? [parsed[0], parsed[1], parsed[2], parsed[3] ?? 1]
          : null;
      };
      const luminance = value => {
        const values = channels(value);
        if (!values) return null;
        const linear = values.slice(0, 3).map(channel => {
          const normalized = channel / 255;
          return normalized <= 0.04045 ? normalized / 12.92 :
            ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      const contrast = (foreground, background) => {
        const fg = channels(foreground);
        const bg = channels(background);
        if (!fg || !bg || bg[3] < 0.99) return null;
        const rendered = fg[3] >= 0.99 ? foreground :
          `rgb(${fg.slice(0, 3).map((channel, index) =>
            Math.round(channel * fg[3] + bg[index] * (1 - fg[3]))
          ).join(', ')})`;
        const first = luminance(rendered);
        const second = luminance(background);
        return first === null || second === null ? null :
          (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
      };
      const probe = document.createElement('span');
      probe.style.color = 'Canvas';
      element.append(probe);
      const canvas = getComputedStyle(probe).color;
      probe.remove();
      const summaryElement = element.querySelector(':scope > summary');
      const summaryStyle = getComputedStyle(summaryElement);
      const summaryBox = summaryElement.getBoundingClientRect();
      const groupStyle = getComputedStyle(element);
      const links = [...element.querySelectorAll('.link-list a')];
      return {
        summary: {
          selector: '.universe-map-group > summary',
          visible: summaryBox.width > 0 && summaryBox.height > 0 &&
            summaryStyle.visibility === 'visible' && summaryStyle.display !== 'none',
          color: summaryStyle.color,
          background: canvas,
          contrast: contrast(summaryStyle.color, canvas),
        },
        boundary: {
          selector: '.universe-map-group',
          color: groupStyle.borderTopColor,
          width: groupStyle.borderTopWidth,
          style: groupStyle.borderTopStyle,
          background: canvas,
          contrast: contrast(groupStyle.borderTopColor, canvas),
        },
        links: links.map((link, index) => {
          const style = getComputedStyle(link);
          const box = link.getBoundingClientRect();
          return {
            selector: `.universe-map-group .link-list a:nth-of-type(${index + 1})`,
            text: link.textContent.trim(),
            visible: box.width > 0 && box.height > 0 &&
              style.visibility === 'visible' && style.display !== 'none',
            color: style.color,
            background: canvas,
            contrast: contrast(style.color, canvas),
            underline: style.textDecorationLine.includes('underline'),
          };
        }),
      };
    });
    let summaryFocus;
    for (let tab = 0; tab < 100; tab += 1) {
      await targetPage.keyboard.press('Shift+Tab');
      summaryFocus = await summary.evaluate(element => {
        const style = getComputedStyle(element);
        const channels = value => value.match(/^rgba?\((.+)\)$/)?.[1]
          .split(/[,/ ]+/).filter(Boolean).map(Number);
        const luminance = value => {
          const values = channels(value);
          if (!values) return null;
          const linear = values.slice(0, 3).map(channel => {
            const normalized = channel / 255;
            return normalized <= 0.04045 ? normalized / 12.92 :
              ((normalized + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
        };
        const probe = document.createElement('span');
        probe.style.color = 'Canvas';
        element.parentElement.append(probe);
        const canvas = getComputedStyle(probe).color;
        probe.remove();
        const outline = channels(style.outlineColor);
        const background = channels(canvas);
        const rendered = outline && background
          ? `rgb(${outline.slice(0, 3).map((channel, index) =>
              Math.round(channel * (outline[3] ?? 1) +
                background[index] * (1 - (outline[3] ?? 1)))
            ).join(', ')})`
          : '';
        const first = luminance(rendered);
        const second = luminance(canvas);
        return {
          selector: '.universe-map-group > summary:focus-visible',
          focused: element === document.activeElement && element.matches(':focus-visible'),
          color: style.color,
          outline_color: style.outlineColor,
          outline_width: style.outlineWidth,
          outline_style: style.outlineStyle,
          background: canvas,
          contrast: first === null || second === null ? null :
            (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
        };
      });
      if (summaryFocus.focused) break;
    }
    forcedColorsEvidence.link_focus = linkFocus;
    forcedColorsEvidence.summary_focus = summaryFocus;
    if (!forcedColorsEvidence.summary.visible ||
        forcedColorsEvidence.summary.contrast === null ||
        forcedColorsEvidence.summary.contrast < 4.5) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FORCED-COLORS SUMMARY UNREADABLE: ` +
        JSON.stringify(forcedColorsEvidence.summary)
      );
    }
    if (parseFloat(forcedColorsEvidence.boundary.width) < 1 ||
        forcedColorsEvidence.boundary.style === 'none' ||
        forcedColorsEvidence.boundary.contrast === null ||
        forcedColorsEvidence.boundary.contrast < 3) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FORCED-COLORS BOUNDARY INDISTINGUISHABLE: ` +
        JSON.stringify(forcedColorsEvidence.boundary)
      );
    }
    if (forcedColorsEvidence.links.length !== state.links.length ||
        forcedColorsEvidence.links.some(link =>
          !link.visible || link.contrast === null || link.contrast < 4.5 ||
          !link.underline
        )) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FORCED-COLORS DESTINATION LINKS UNREADABLE: ` +
        JSON.stringify(forcedColorsEvidence.links)
      );
    }
    if (!linkFocus.focused || linkFocus.outline_style === 'none' ||
        parseFloat(linkFocus.outline_width) < 2 ||
        linkFocus.contrast === null || linkFocus.contrast < 3) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FORCED-COLORS LINK FOCUS NOT VISIBLE: ` +
        JSON.stringify(linkFocus)
      );
    }
    if (!summaryFocus.focused || summaryFocus.outline_style === 'none' ||
        parseFloat(summaryFocus.outline_width) < 2 ||
        summaryFocus.contrast === null || summaryFocus.contrast < 3) {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FORCED-COLORS SUMMARY FOCUS NOT VISIBLE: ` +
        JSON.stringify(summaryFocus)
      );
    }

    const currentUrl = new URL(targetPage.url());
    const navigableLinkIndex = state.links.findIndex(link => {
      try {
        const destination = new URL(link.href, targetPage.url());
        return destination.origin === currentUrl.origin &&
          destination.pathname !== currentUrl.pathname;
      } catch {
        return false;
      }
    });
    let ordinaryPageLinkWorks = false;
    let ordinaryPageLinkEvidence = null;
    if (navigableLinkIndex >= 0) {
      const ordinaryPageLink = group.locator('.link-list a').nth(navigableLinkIndex);
      const targetPath = new URL(
        state.links[navigableLinkIndex].href,
        targetPage.url()
      ).pathname;
      try {
        const response = await Promise.all([
          targetPage.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 10000 }),
          ordinaryPageLink.click({ timeout: 5000 }),
        ]).then(([navigationResponse]) => navigationResponse);
        ordinaryPageLinkWorks =
          new URL(targetPage.url()).pathname === targetPath &&
          Boolean(response && response.status() < 400);
        ordinaryPageLinkEvidence = {
          href: state.links[navigableLinkIndex].href,
          target_path: targetPath,
          http_status: response?.status() ?? null,
        };
      } catch {
        ordinaryPageLinkWorks = false;
      }
    }
    if (!ordinaryPageLinkWorks) {
      errors.push(
        'UNIVERSE NO-JAVASCRIPT GENERATED PAGE LINK BROKE: ' +
        'ordinary link did not reach a successful local page'
      );
    }

    return {
      errors,
      evidence: {
        group_count: groupCount,
        state,
        forced_colors: forcedColorsEvidence,
        ordinary_page_link_works: ordinaryPageLinkWorks,
        ordinary_page_link: ordinaryPageLinkEvidence,
      },
    };
  };

  try {
    await page.route('**/*', route => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.pathname === mermaidPath) return route.abort();
      if (externalBlock.test(route.request().url())) return route.abort();
      return route.continue();
    });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const transitionDismiss = page.locator(
      '[data-transition-dialog][open] [data-transition-dismiss]'
    );
    if (await transitionDismiss.isVisible()) await transitionDismiss.click();
    await page.locator('.askjamie-hero--universe .mermaid').scrollIntoViewIfNeeded();

    const renderStarted = await page.waitForFunction(() => {
      const diagram = document.querySelector('.askjamie-hero--universe .mermaid');
      return diagram?.dataset.mermaidRendered === '1';
    }, undefined, { timeout: 10000 }).then(() => true, () => false);
    if (!renderStarted) {
      errors.push('UNIVERSE MERMAID FAILURE CHECK DID NOT START: hero initialization was not observed');
    }

    const [requestedUrl, failure, warning] = await Promise.all([
      mermaidRequest,
      mermaidFailure,
      renderWarning,
    ]);
    if (!requestedUrl) {
      errors.push('UNIVERSE MERMAID FAILURE CHECK DID NOT REQUEST: local Mermaid module request was not observed');
    }
    if (!failure) {
      errors.push('UNIVERSE MERMAID FAILURE CHECK DID NOT FAIL: blocked local module request did not fail');
    }
    if (!warning) {
      errors.push('UNIVERSE MERMAID FAILURE NOT CAUGHT: expected the renderer failure warning');
    }

    const failedState = await inspectFallback(page);
    if (!failedState.caption_visible || !failedState.caption) {
      errors.push(`UNIVERSE MERMAID FAILURE HID CAPTION: ${JSON.stringify(failedState)}`);
    }
    if (!failedState.failure_status_visible ||
        !failedState.failure_status_text.includes('could not be displayed') ||
        failedState.failure_status_role !== 'status' ||
        failedState.failure_status_live !== 'polite') {
      errors.push(
        `UNIVERSE MERMAID FAILURE STATUS UNAVAILABLE OR INACCESSIBLE: ${JSON.stringify(failedState)}`
      );
    }
    if (failedState.links.length < 3 || failedState.links.some(link => !link.visible || !link.href)) {
      errors.push(`UNIVERSE MERMAID FAILURE MADE PAGE LINKS UNAVAILABLE: ${JSON.stringify(failedState.links)}`);
    }
    if (failedState.ready_state === '1' || failedState.has_svg_node) {
      errors.push(
        'UNIVERSE MERMAID FAILURE REPORTED FALSE READY STATE: ' +
        JSON.stringify({
          ready_state: failedState.ready_state,
          has_svg_node: failedState.has_svg_node,
        })
      );
    }
    const failedPageMapLinkWorks = await clickPageMapLink(page).catch(() => false);
    if (!failedPageMapLinkWorks) {
      errors.push('UNIVERSE MERMAID FAILURE BROKE ORDINARY LINK: page-map link did not reach its in-page target');
    }

    const generatedPageMap = await checkGeneratedPageMapFallback(page);
    errors.push(...generatedPageMap.errors);

    noJsContext = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      javaScriptEnabled: false,
      forcedColors: 'active',
    });
    const noJsPage = await noJsContext.newPage();
    await noJsPage.route('**/*', route =>
      externalBlock.test(route.request().url()) ? route.abort() : route.continue()
    );
    await noJsPage.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (!(await noJsPage.evaluate(() => matchMedia('(forced-colors: active)').matches))) {
      errors.push('UNIVERSE NO-JAVASCRIPT FORCED-COLORS MODE WAS NOT ACTIVE BEFORE PAGE LOAD');
    }
    const noJsState = await inspectFallback(noJsPage);
    if (!noJsState.noscript_visible ||
        !noJsState.noscript_text.includes('The page links below work without JavaScript.')) {
      errors.push(`UNIVERSE NO-JAVASCRIPT FALLBACK MISSING: ${JSON.stringify(noJsState)}`);
    }
    if (noJsState.failure_status_text !== '' ||
        !noJsState.failure_status_exposed || !noJsState.failure_status_sr_only ||
        noJsState.failure_status_role !== 'status' ||
        noJsState.failure_status_live !== 'polite') {
      errors.push(
        `UNIVERSE NO-JAVASCRIPT FAILURE STATUS SHOULD BE EMPTY, EXPOSED AND VISUALLY HIDDEN: ${JSON.stringify(noJsState)}`
      );
    }
    if (!noJsState.caption_visible || noJsState.links.length < 3 ||
        noJsState.links.some(link => !link.visible || !link.href)) {
      errors.push(`UNIVERSE NO-JAVASCRIPT LINKS UNAVAILABLE: ${JSON.stringify(noJsState)}`);
    }
    const noJsPageMapLinkWorks = await clickPageMapLink(noJsPage).catch(() => false);
    if (!noJsPageMapLinkWorks) {
      errors.push('UNIVERSE NO-JAVASCRIPT LINK BROKE: page-map link did not reach its in-page target');
    }
    const noJsGeneratedPageMap = await checkGeneratedPageMapWithoutJavaScript(noJsPage);
    errors.push(...noJsGeneratedPageMap.errors);

    return {
      errors,
      evidence: {
        module_requested: Boolean(requestedUrl),
        module_failure: failure,
        render_failure_caught: Boolean(warning),
        failed_state: failedState,
        failed_page_map_link_works: failedPageMapLinkWorks,
        generated_page_map: generatedPageMap.evidence,
        no_javascript_state: noJsState,
        no_javascript_page_map_link_works: noJsPageMapLinkWorks,
        no_javascript_generated_page_map: noJsGeneratedPageMap.evidence,
      },
    };
  } catch (error) {
    errors.push(`UNIVERSE MERMAID FAILURE CHECK ERROR: ${error.message.split('\n')[0]}`);
    return { errors, evidence: null };
  } finally {
    await page.close();
    await context.close();
    await noJsContext?.close();
  }
}

// ── MODE A: Playwright ────────────────────────────────────────────────────────

async function runWithPlaywright(previousWarningBaseline) {
  const browserRunStartedAt = performance.now();
  let pw;
  let playwrightVersion;
  try {
    const require = createRequire(import.meta.url);
    pw = require('playwright');
    playwrightVersion = require('playwright/package.json').version;
  } catch {
    return { ok: false, reason: 'Playwright is not installed or its package version is unavailable' };
  }

  mkdirSync(RESULTS_DIR, { recursive: true });
  mkdirSync(SCREENSHOTS_DIR, { recursive: true });

  let browser;
  try {
    browser = await pw.chromium.launch({ headless: true });
  } catch {
    return { ok: false, reason: 'Chromium could not be launched' };
  }

  const chromiumVersion = browser.version();
  console.log(`Browser runtime: Playwright ${playwrightVersion}; Chromium ${chromiumVersion}`);

  // Reuse contexts for isolation and speed, but create a fresh page for every
  // route. External resources are blocked so browser QA measures local assets.
  const EXTERNAL_BLOCK = /fonts\.(gstatic|googleapis)\.com|google-analytics\.com|googletagmanager\.com|cdn\.jsdelivr\.net/;

  const workers = [];
  const allResults = [];
  let completedPages = 0;
  let earlyStopReason = null;
  const buildBrowserReport = (status = 'in_progress', failure_reason = null) => ({
    generated: new Date().toISOString(),
    mode: 'playwright',
    status,
    failure_reason,
    ...(status === 'in_progress' ? { runner_pid: process.pid } : {}),
    duration_ms: Math.round(performance.now() - browserRunStartedAt),
    coverage_profile: BRANDGUARD_EDGE_ONLY
      ? 'brandguard-edge-only'
      : PUBLIC_FALLBACK_FONT_ONLY
        ? 'public-fallback-font-mobile-only'
        : 'full-public-sitemap',
    coverage_limit: BRANDGUARD_EDGE_ONLY
      ? 'Intentional focused run: sitemap-listed BrandGuard routes at mobile-350-edge and mobile-370-edge only; this is not full release coverage.'
      : PUBLIC_FALLBACK_FONT_ONLY
        ? 'Intentional focused run: non-BrandGuard sitemap routes that declare hosted web-font stylesheets at 320px, 360px, and 390px only; this is not full release coverage.'
        : null,
    ...getStartProvenanceReport(),
    ...(status === 'in_progress' ? {} : getCompletionProvenance()),
    playwright_version: playwrightVersion,
    browser_engine: 'chromium',
    browser_version: chromiumVersion,
    chromium_version: chromiumVersion,
    base_url: BASE_URL,
    pages_checked: completedPages,
    pages_expected: QA_PATHS.length,
    viewports_checked: QA_VIEWPORTS.length,
    total_checks: allResults.length,
    passing_checks: allResults.filter(r => r.pass).length,
    failing_checks: allResults.filter(r => !r.pass).length,
    results: allResults,
  });
  const stopForSignal = signal => {
    if (earlyStopReason) return;
    earlyStopReason = `Received ${signal}`;
    writeResultsReport(buildBrowserReport('early_stop', earlyStopReason));
    void browser.close().catch(() => {}).finally(() => {
      process.exit(signal === 'SIGINT' ? 130 : 143);
    });
  };
  process.once('SIGINT', () => stopForSignal('SIGINT'));
  process.once('SIGTERM', () => stopForSignal('SIGTERM'));

  async function runViewport(worker, path, url) {
    const { vp, ctx } = worker;
    const page = await ctx.newPage();
    const checkBrandGuardTheme =
      BRANDGUARD_THEME_GEOMETRY_PATHS.has(path) &&
      BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name);
    const checkBrandGuardHeaderTheme =
      shouldCheckBrandGuardHeaderThemeGeometry(path, vp.name);
    const deferBrandGuardThemeForBaseline =
      checkBrandGuardTheme &&
      BRANDGUARD_DEFERRED_THEME_GEOMETRY_PATHS.has(path);
    const checkBrandGuardAllRouteFonts =
      BRANDGUARD_FONT_GEOMETRY_PATHS.has(path) &&
      (BRANDGUARD_FONT_GEOMETRY_VIEWPORTS.has(vp.name) ||
        BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS.has(vp.name));
    const checkBrandGuardBoundaryFonts =
      BRANDGUARD_FONT_GEOMETRY_PATHS.has(path) &&
      BRANDGUARD_FONT_BOUNDARY_GEOMETRY_VIEWPORTS.has(vp.name);
    const checkBrandGuardGapFonts =
      BRANDGUARD_FONT_GAP_GEOMETRY_PATHS.has(path) &&
      BRANDGUARD_FONT_GAP_GEOMETRY_VIEWPORTS.has(vp.name);
    const checkBrandGuardFontReservationEdges =
      BRANDGUARD_FONT_RESERVATION_EDGE_GEOMETRY_PATHS.has(path) &&
      (vp.name === 'mobile-349-reservation-edge' ||
        vp.name === 'mobile-371-reservation-edge');
    const checkBrandGuardFonts =
      checkBrandGuardAllRouteFonts ||
      checkBrandGuardBoundaryFonts ||
      checkBrandGuardGapFonts ||
      checkBrandGuardFontReservationEdges;
    const includeBrandGuardHeaderNavigation =
      (BRANDGUARD_FONT_GEOMETRY_PATHS.has(path) &&
        BRANDGUARD_FONT_NAV_GEOMETRY_VIEWPORTS.has(vp.name)) ||
      (BRANDGUARD_FONT_EDGE_GEOMETRY_PATHS.has(path) &&
        BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS.has(vp.name)) ||
      (checkBrandGuardBoundaryFonts &&
        BRANDGUARD_FONT_NAV_BOUNDARY_GEOMETRY_VIEWPORTS.has(vp.name));
    const checkUniverseDiagram =
      path === UNIVERSE_DIAGRAM_GEOMETRY_PATH &&
      UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS.has(vp.name);
    const checkBrandGuardHeroActions =
      shouldCheckBrandGuardHeroActions(path, vp.name);
    const checkBrandGuardHeroActionAccessibleNameLabels =
      shouldCheckBrandGuardHeroActionAccessibleNames(path, vp.name);
    const checkUniversePageMapFonts =
      path === UNIVERSE_DIAGRAM_GEOMETRY_PATH &&
      UNIVERSE_PAGE_MAP_FONT_GEOMETRY_VIEWPORTS.has(vp.name);
    const checkUniverseFailure =
      path === UNIVERSE_DIAGRAM_GEOMETRY_PATH && vp.name === 'mobile-390';
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
    let releaseBrandGuardThemeStyles = () => {};
    let brandGuardThemeGate = Promise.resolve();
    if (deferBrandGuardThemeForBaseline) {
      let release;
      brandGuardThemeGate = new Promise(resolve => { release = resolve; });
      let released = false;
      releaseBrandGuardThemeStyles = () => {
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
    let releaseUniversePageMapFontRequests = null;
    let universePageMapFontGate = Promise.resolve();
    let universePageMapFontRequestSeen = Promise.resolve(false);
    let resolveUniversePageMapFontFailure = () => {};
    const universePageMapFontFailureSeen = checkUniversePageMapFonts
      ? new Promise(resolve => { resolveUniversePageMapFontFailure = resolve; })
      : Promise.resolve(null);
    if (checkUniversePageMapFonts) {
      let releaseFontGate;
      universePageMapFontGate = new Promise(resolve => { releaseFontGate = resolve; });
      let released = false;
      releaseUniversePageMapFontRequests = () => {
        if (released) return;
        released = true;
        releaseFontGate();
      };
      universePageMapFontRequestSeen = page.waitForRequest(request => {
        const requestUrl = new URL(request.url());
        return requestUrl.hostname === 'fonts.gstatic.com' &&
          request.resourceType() === 'font';
      }, { timeout: UNIVERSE_PAGE_MAP_FONT_GEOMETRY_TIMEOUT_MS })
        .then(() => true, () => false);
    }
    const blockedExternal = new Set();
    const consoleErrors = [];
    const requestFailures = [];
    const failedResponses = [];
    const criticalHeroStylesheetResponses = [];
    const brandGuardFontRequests = [];
    const brandGuardFontResponses = [];
    const brandGuardFontFailures = [];
    const universePageMapFontRequests = [];
    const universePageMapFontResponses = [];
    const universePageMapFontFailures = [];
    const httpFailedFontRequests = new WeakSet();
    const requestInfo = new WeakMap();
    const requestedUrls = new Set();
    const requestedAtByUrl = new Map();
    const warnings = [];

    const recordUniversePageMapFontFailure = failure => {
      if (!checkUniversePageMapFonts) return;
      const requestUrl = new URL(failure.url);
      const requestType = failure.resource_type;
      if (!BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname) ||
          !['stylesheet', 'font'].includes(requestType)) return;
      const record = {
        host: requestUrl.hostname,
        path: requestUrl.pathname,
        resource_type: requestType,
        failure_kind: failure.status !== undefined ? 'http' : 'network',
        ...(failure.status !== undefined ? { status: failure.status } : {}),
        ...(failure.error ? { error: failure.error } : {}),
      };
      universePageMapFontFailures.push(record);
      resolveUniversePageMapFontFailure(record);
    };
    const recordBrandGuardFontFailure = failure => {
      if (!checkBrandGuardFonts) return;
      const requestUrl = new URL(failure.url);
      const requestType = failure.resource_type;
      if (!BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname) ||
          !['stylesheet', 'font'].includes(requestType)) return;
      brandGuardFontFailures.push({
        host: requestUrl.hostname,
        path: requestUrl.pathname,
        resource_type: requestType,
        failure_kind: failure.status !== undefined ? 'http' : 'network',
        ...(failure.status !== undefined ? { status: failure.status } : {}),
        ...(failure.error ? { error: failure.error } : {}),
      });
    };

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
      if (checkBrandGuardFonts &&
          req.resourceType() === 'font' &&
          new URL(req.url()).hostname === 'fonts.gstatic.com') {
        const requestUrl = new URL(req.url());
        brandGuardFontRequests.push({
          host: requestUrl.hostname,
          path: requestUrl.pathname,
          resource_type: req.resourceType(),
        });
      }
      if (checkUniversePageMapFonts && req.resourceType() === 'font') {
        const requestUrl = new URL(req.url());
        if (requestUrl.hostname === 'fonts.gstatic.com') {
          universePageMapFontRequests.push({
            host: requestUrl.hostname,
            path: requestUrl.pathname,
            resource_type: req.resourceType(),
          });
        }
      }
      requestInfo.set(req, {
        requestedUrl: req.url(),
        documentUrl: req.frame()?.url() || '',
        createdAt: Date.now(),
      });
    };
    const onRequestFailed = req => {
      const alreadyRecordedAsHttpFailure = httpFailedFontRequests.has(req);
      if (!alreadyRecordedAsHttpFailure) {
        recordBrandGuardFontFailure({
          url: req.url(),
          resource_type: req.resourceType(),
          error: req.failure()?.errorText || 'unknown request failure',
        });
        recordUniversePageMapFontFailure({
          url: req.url(),
          resource_type: req.resourceType(),
          error: req.failure()?.errorText || 'unknown request failure',
        });
      }
      if (blockedExternal.has(req.url()) || alreadyRecordedAsHttpFailure) return;
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
      if (responseUrl.pathname === CRITICAL_HERO_STYLESHEET_PATH &&
          resp.request().resourceType() === 'stylesheet') {
        criticalHeroStylesheetResponses.push({
          url: resp.url(),
          status: resp.status(),
          resourceType: resp.request().resourceType(),
        });
      }
      if (checkBrandGuardFonts && BRANDGUARD_FONT_HOSTS.has(responseUrl.hostname)) {
        brandGuardFontResponses.push({
          host: responseUrl.hostname,
          path: responseUrl.pathname,
          resource_type: resp.request().resourceType(),
          status: resp.status(),
        });
      }
      if (checkUniversePageMapFonts &&
          BRANDGUARD_FONT_HOSTS.has(responseUrl.hostname)) {
        universePageMapFontResponses.push({
          host: responseUrl.hostname,
          path: responseUrl.pathname,
          resource_type: resp.request().resourceType(),
          status: resp.status(),
        });
      }
      if (resp.status() >= 400) {
        if (BRANDGUARD_FONT_HOSTS.has(responseUrl.hostname) &&
            ['stylesheet', 'font'].includes(resp.request().resourceType())) {
          httpFailedFontRequests.add(resp.request());
        }
        recordBrandGuardFontFailure({
          url: resp.url(),
          resource_type: resp.request().resourceType(),
          status: resp.status(),
        });
        recordUniversePageMapFontFailure({
          url: resp.url(),
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
      if (deferBrandGuardThemeForBaseline &&
          requestUrl.pathname === '/assets/css/theme.css' &&
          route.request().resourceType() === 'stylesheet') {
        return brandGuardThemeGate.then(() => route.continue());
      }
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
      if (checkUniversePageMapFonts &&
          BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname)) {
        const requestType = route.request().resourceType();
        if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestType === 'stylesheet') {
          return route.continue();
        }
        if (requestUrl.hostname === 'fonts.gstatic.com' &&
            requestType === 'font') {
          return universePageMapFontGate.then(() => route.continue());
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
    if (checkUniverseDiagram) {
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.addInitScript(() => {
        localStorage.setItem('askjamie-color-scheme', 'dark');
        const queue = [];
        Object.defineProperty(window, '__responsiveQaMermaidRenderQueue', {
          configurable: false,
          value: queue,
        });
        const isMermaidRenderCallback = callback =>
          typeof callback === 'function' &&
          /renderOne\s*\(\s*node\s*\)/.test(Function.prototype.toString.call(callback));

        if (typeof window.requestIdleCallback === 'function') {
          const requestIdleCallback = window.requestIdleCallback.bind(window);
          window.requestIdleCallback = (callback, options) => {
            if (isMermaidRenderCallback(callback)) {
              queue.push({ callback, args: [] });
              return queue.length;
            }
            return requestIdleCallback(callback, options);
          };
        }

        const setTimeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) => {
          if (isMermaidRenderCallback(callback)) {
            queue.push({ callback, args });
            return queue.length;
          }
          return setTimeout(callback, delay, ...args);
        };
      });
    }
    page.on('console', onConsole);
    page.on('request', onRequest);
    page.on('requestfailed', onRequestFailed);
    page.on('response', onResponse);

    try {
      try {
        await page.goto(url, {
          waitUntil: 'commit',
          timeout: LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS,
        });
        const readyDeadline = performance.now() + LOCAL_DOCUMENT_READY_TIMEOUT_MS;
        // Keep the five-second window anchored to document commit, including
        // any time spent setting up Playwright's DOMContentLoaded wait.
        const remainingReadyWindow = Math.max(
          1,
          Math.ceil(readyDeadline - performance.now())
        );
        await page.waitForLoadState('domcontentloaded', {
          timeout: remainingReadyWindow,
        }).catch(() => {
          warnings.push(
            `DOMContentLoaded not observed within ${LOCAL_DOCUMENT_READY_TIMEOUT_MS / 1000}s ` +
            'after local document commit'
          );
        });
      } catch (err) {
        const navigationTimedOut =
          err.name === 'TimeoutError' ||
          /\bTimeout \d+ms exceeded\b/.test(err.message);
        const navigationLostConnection =
          /\bnet::ERR_(?:CONNECTION_[A-Z_]+|EMPTY_RESPONSE|NETWORK_CHANGED|INTERNET_DISCONNECTED|ADDRESS_UNREACHABLE|NAME_NOT_RESOLVED)\b/
            .test(err.message);
        const navigationFailureCategory = navigationTimedOut
          ? 'navigation timeout:'
          : navigationLostConnection
            ? 'navigation transport failure:'
            : 'navigation failed:';
        return { url, viewport: vp.name, width: vp.width, height: vp.height,
                 mode: 'playwright', pass: false,
                 errors: [
                   `${navigationFailureCategory} ${err.message.split('\n')[0]}`,
                 ],
                 warnings };
      }

      // Review the underlying layout after acknowledging the arrival note.
      // At the 320px keyboard-focus routes, verify the actual Escape close
      // restores focus before continuing the hero-action Tab sequence.
      const transitionDismiss = page.locator('[data-transition-dialog][open] [data-transition-dismiss]');
      const brandGuardTransitionFocusRestoration =
        shouldCheckBrandGuardHeroKeyboardFocus(path, vp.name) &&
        await transitionDismiss.isVisible()
          ? await dismissBrandGuardTransitionDialogWithKeyboard(page, path)
          : null;
      if (!brandGuardTransitionFocusRestoration &&
          await transitionDismiss.isVisible()) {
        await transitionDismiss.click();
      }

      const criticalHeroStyles =
        (path === BRANDGUARD_GEOMETRY_PATH &&
          vp.name === BRANDGUARD_CRITICAL_STYLE_VIEWPORT) ||
        (path === UNIVERSE_DIAGRAM_GEOMETRY_PATH &&
          UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS.has(vp.name))
          ? await checkCriticalHeroStyles(
            page,
            path,
            vp.width,
            criticalHeroStylesheetResponses
          )
          : null;

      const brandGuardHeaderThemeBaseline =
        checkBrandGuardHeaderTheme
          ? await checkBrandGuardHeaderThemeGeometry(page, true)
          : null;

      // Verify hero geometry on every supported BrandGuard route. Compare before
      // and after only where the page actually defers its theme stylesheet.
      const heroThemeGeometry =
        checkBrandGuardTheme
          ? await checkBrandGuardHeroGeometry(page, releaseBrandGuardThemeStyles)
          : null;
      const brandGuardHeaderThemeGeometry =
        brandGuardHeaderThemeBaseline
          ? await checkBrandGuardHeaderThemeGeometry(
            page,
            false,
            brandGuardHeaderThemeBaseline
          )
          : null;
      const heroFontGeometry = checkBrandGuardFonts
        ? await checkBrandGuardWebFontGeometry(
          page,
          releaseBrandGuardFontRequests,
          brandGuardFontRequests,
          brandGuardFontResponses,
          brandGuardFontFailures,
          includeBrandGuardHeaderNavigation,
          checkBrandGuardHeroActions
        )
        : null;
      const brandGuardHeroActionAccessibleNames =
        checkBrandGuardHeroActionAccessibleNameLabels
        ? await checkBrandGuardHeroActionAccessibleNames(page, path)
        : null;
      const brandGuardNarrowContent =
        path.startsWith(BRANDGUARD_GEOMETRY_PATH) &&
        vp.name === BRANDGUARD_NARROW_CONTENT_VIEWPORT
          ? await checkBrandGuardNarrowContent(page)
          : null;
      const brandGuardBreadcrumbKeyboardFocus =
        shouldCheckBrandGuardBreadcrumbKeyboardFocus(path, vp.name)
          ? await checkBrandGuardBreadcrumbKeyboardFocus(page, path)
          : null;
      const brandGuardFallbackFontReadability =
        shouldCheckBrandGuardFallbackFontReadability(path, vp.width)
          ? await checkBrandGuardFallbackFontReadability(
            browser,
            path,
            url,
            vp,
            EXTERNAL_BLOCK
          )
          : null;
      const publicFallbackFontReadability =
        shouldCheckPublicFallbackFontReadability(path, vp.width)
          ? await checkPublicFallbackFontReadability(
            browser,
            path,
            url,
            vp,
            EXTERNAL_BLOCK
          )
          : null;
      const brandGuardForcedColorsReadability =
        shouldCheckBrandGuardForcedColorsReadability(path, vp.name)
          ? await checkBrandGuardForcedColorsReadability(
            browser,
            path,
            url,
            vp,
            EXTERNAL_BLOCK
          )
          : null;
      const universeDiagramGeometry = checkUniverseDiagram
        ? await checkUniverseDiagramGeometry(
          page,
          releaseUniverseMermaid,
          universeMermaidRequestSeen,
          universePageMapFontRequestSeen,
          universePageMapFontFailureSeen,
          releaseUniversePageMapFontRequests,
          universePageMapFontRequests,
          universePageMapFontResponses,
          universePageMapFontFailures
        )
        : null;
      const universePageMapZoom = checkUniverseDiagram &&
        vp.name === 'desktop-1280'
        ? await checkUniversePageMapZoomUsability(page)
        : null;
      const universeMermaidFailure = checkUniverseFailure
        ? await checkUniverseMermaidFailure(browser, vp, url)
        : null;
      const universePageMapThemeFailure = checkUniverseFailure
        ? await checkUniversePageMapThemeFailure(browser, vp, url)
        : null;
      const brandGuardHeroKeyboardFocus =
        shouldCheckBrandGuardHeroKeyboardFocus(path, vp.name)
          ? await checkBrandGuardHeroKeyboardFocus(page)
          : null;
      const brandGuardHeroForcedColorsKeyboardFocus =
        shouldCheckBrandGuardHeroForcedColorsKeyboardFocus(path, vp.name)
          ? await checkBrandGuardHeroForcedColorsKeyboardFocus(
            browser,
            path,
            url,
            vp,
            EXTERNAL_BLOCK
          )
          : null;
      const brandGuardMobileNavigationKeyboard =
        shouldCheckBrandGuardMobileNavKeyboard(path, vp.name)
          ? await checkBrandGuardMobileNavigationKeyboard(page, path)
          : null;
      const brandGuardMobileMenuDestinations =
        shouldCheckBrandGuardMobileMenuDestinations(path, vp.name)
          ? await checkBrandGuardMobileMenuDestinations(page)
          : null;
      const brandGuardTextSpacingReadability =
        shouldCheckBrandGuardTextSpacing(path, vp.name)
          ? await checkBrandGuardTextSpacingReadability(page, path, vp.width)
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
        ...(brandGuardTransitionFocusRestoration?.errors ?? []),
        ...(criticalHeroStyles?.errors ?? []),
        ...(brandGuardHeaderThemeBaseline?.errors ?? []),
        ...(brandGuardHeaderThemeGeometry?.errors ?? []),
        ...(heroThemeGeometry?.errors ?? []),
        ...(heroFontGeometry?.errors ?? []),
        ...(brandGuardNarrowContent?.errors ?? []),
        ...(brandGuardBreadcrumbKeyboardFocus?.errors ?? []),
        ...(brandGuardFallbackFontReadability?.errors ?? []),
        ...(publicFallbackFontReadability?.errors ?? []),
        ...(brandGuardForcedColorsReadability?.errors ?? []),
        ...(universeDiagramGeometry?.errors ?? []),
        ...(universePageMapZoom?.errors ?? []),
        ...(universeMermaidFailure?.errors ?? []),
        ...(universePageMapThemeFailure?.errors ?? []),
        ...(brandGuardHeroKeyboardFocus?.errors ?? []),
        ...(brandGuardHeroForcedColorsKeyboardFocus?.errors ?? []),
        ...(brandGuardMobileNavigationKeyboard?.errors ?? []),
        ...(brandGuardMobileMenuDestinations?.errors ?? []),
        ...(brandGuardTextSpacingReadability?.errors ?? []),
        ...(brandGuardHeroActionAccessibleNames?.errors ?? []),
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

      const brandGuardHeaderNavigationFontGeometry =
        heroFontGeometry?.evidence?.header_navigation ?? null;
      const heroFontGeometryEvidence = heroFontGeometry
        ? Object.fromEntries(Object.entries(heroFontGeometry.evidence)
          .filter(([key]) => key !== 'header_navigation'))
        : null;
      const row = { url, viewport: vp.name, width: vp.width, height: vp.height,
                    mode: 'playwright', pass: errors.length === 0, errors, warnings,
                    ...(brandGuardHeaderThemeGeometry
                      ? {
                          brandguard_header_navigation_theme_geometry:
                            brandGuardHeaderThemeGeometry.evidence,
                        }
                      : {}),
                    ...(heroThemeGeometry
                      ? { hero_theme_geometry: heroThemeGeometry.evidence }
                      : {}),
                     ...(criticalHeroStyles
                       ? { critical_hero_styles: criticalHeroStyles.evidence }
                       : {}),
                     ...(heroFontGeometry
                      ? { hero_font_geometry: heroFontGeometryEvidence }
                      : {}),
                     ...(brandGuardHeaderNavigationFontGeometry
                      ? {
                          brandguard_header_navigation_font_geometry:
                            brandGuardHeaderNavigationFontGeometry,
                        }
                      : {}),
                     ...(brandGuardNarrowContent
                       ? { brandguard_320_content: brandGuardNarrowContent.evidence }
                       : {}),
                     ...(brandGuardBreadcrumbKeyboardFocus
                       ? {
                           brandguard_breadcrumb_keyboard_focus:
                             brandGuardBreadcrumbKeyboardFocus.evidence,
                         }
                       : {}),
                    ...(brandGuardTextSpacingReadability
                      ? {
                          brandguard_breadcrumb_text_spacing:
                            brandGuardTextSpacingReadability.evidence,
                        }
                      : {}),
                    ...(brandGuardFallbackFontReadability
                      ? {
                          brandguard_fallback_font_readability:
                            brandGuardFallbackFontReadability.evidence,
                        }
                      : {}),
                     ...(publicFallbackFontReadability
                       ? {
                           public_fallback_font_readability:
                             publicFallbackFontReadability.evidence,
                         }
                       : {}),
                     ...(brandGuardForcedColorsReadability
                       ? {
                           brandguard_forced_colors_readability:
                             brandGuardForcedColorsReadability.evidence,
                         }
                       : {}),
                    ...(brandGuardHeroKeyboardFocus
                      ? {
                          brandguard_hero_keyboard_focus:
                            brandGuardHeroKeyboardFocus.evidence,
                        }
                      : {}),
                    ...(brandGuardTransitionFocusRestoration
                      ? {
                          brandguard_transition_focus_restoration:
                            brandGuardTransitionFocusRestoration.evidence,
                        }
                      : {}),
                     ...(brandGuardHeroForcedColorsKeyboardFocus
                       ? {
                           brandguard_hero_forced_colors_keyboard_focus:
                             brandGuardHeroForcedColorsKeyboardFocus.evidence,
                         }
                       : {}),
                     ...(brandGuardMobileNavigationKeyboard
                       ? {
                           brandguard_mobile_navigation_keyboard:
                             brandGuardMobileNavigationKeyboard.evidence,
                         }
                       : {}),
                      ...(brandGuardMobileMenuDestinations
                        ? {
                            brandguard_mobile_menu_destinations:
                              brandGuardMobileMenuDestinations.evidence,
                          }
                        : {}),
                     ...(brandGuardHeroActionAccessibleNames
                       ? {
                           brandguard_hero_action_accessible_names:
                             brandGuardHeroActionAccessibleNames.evidence,
                         }
                       : {}),
                    ...(universeDiagramGeometry
                      ? { universe_diagram_geometry: universeDiagramGeometry.evidence }
                      : {}),
                    ...(universeDiagramGeometry?.evidence?.page_map_web_font_geometry
                      ? {
                          universe_page_map_font_geometry:
                            universeDiagramGeometry.evidence.page_map_web_font_geometry,
                        }
                      : {}),
                     ...(universePageMapZoom
                       ? { universe_page_map_zoom_200: universePageMapZoom.evidence }
                       : {}),
                    ...(universeMermaidFailure
                      ? { universe_mermaid_failure: universeMermaidFailure.evidence }
                       : {}),
                     ...(universePageMapThemeFailure
                       ? {
                           universe_page_map_theme_failure:
                             universePageMapThemeFailure.evidence,
                         }
                       : {}) };
      if (!row.pass) {
        const ssFile = `${path.replace(/\//g, '_')}_${vp.name}.png`;
        await page.screenshot({ path: resolve(SCREENSHOTS_DIR, ssFile) });
      }
      return row;
    } finally {
      releaseBrandGuardFontRequests();
      releaseUniversePageMapFontRequests?.();
      page.removeListener('console', onConsole);
      page.removeListener('request', onRequest);
      page.removeListener('requestfailed', onRequestFailed);
      page.removeListener('response', onResponse);
      await page.close();
    }
  }

  try {
    workers.push(...await Promise.all(QA_VIEWPORTS.map(async vp => {
      const ctx = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        hasTouch: vp.width === 320,
      });
      return { vp, ctx };
    })));

    for (const path of QA_PATHS) {
      const url = BASE_URL + path;

      const vpResults = await mapConcurrent(
        workers,
        VIEWPORT_CONCURRENCY,
        async worker => {
          const result = await runViewport(worker, path, url);
          if (!earlyStopReason) {
            allResults.push(result);
            writeResultsReport(buildBrowserReport('in_progress'));
          }
          return result;
        }
      );
      if (earlyStopReason) return { ok: true, stopped: true };

      const fails = vpResults.filter(r => !r.pass);
      if (fails.length > 0) {
        fails.forEach(r => {
          console.log(`  FAIL  ${r.viewport.padEnd(14)} ${path}`);
          r.errors.forEach(e => console.log(`         → ${e}`));
        });
      }
      const warningViewports = new Map();
      for (const row of vpResults) {
        for (const warning of row.warnings ?? []) {
          const viewports = warningViewports.get(warning) ?? [];
          if (!viewports.includes(row.viewport)) viewports.push(row.viewport);
          warningViewports.set(warning, viewports);
        }
      }
      for (const [warning, viewports] of warningViewports) {
        console.log(`  WARN  ${path} (${viewports.join(', ')})`);
        console.log(`         → ${warning}`);
      }
      process.stdout.write(`  done  ${path}\n`);

      completedPages += 1;
      writeResultsReport(buildBrowserReport('in_progress'));
    }
    for (const { ctx } of workers) await ctx.close();
    await browser.close();
  } catch (error) {
    if (earlyStopReason) return { ok: true, stopped: true };
    earlyStopReason = error?.message || String(error);
    writeResultsReport(buildBrowserReport('early_stop', earlyStopReason));
    await browser.close().catch(() => {});
    throw error;
  }

  // A signal can arrive while cleanup is pending, after the last route.
  // Keep its early-stop receipt even when every viewport row is present.
  if (earlyStopReason) return { ok: true, stopped: true };

  const report = buildBrowserReport('complete');
  if (report.coverage_profile === 'full-public-sitemap') {
    const pageMapSummaries = summarizePageMapSweepRun(report);
    if (pageMapSummaries.length > 0) {
      const pageMapHistory = readPageMapSweepHistory();
      attachPageMapSweepHistoryAssessment(
        report,
        pageMapHistory,
        pageMapSummaries
      );
    }
  }
  writeResultsReport(report);

  const warningCount = countBrowserWarnings(report);
  saveCompletedBrowserWarningBaseline(report);
  console.log(
    `\nTotal: ${report.total_checks} checks — ${report.passing_checks} passed, ${report.failing_checks} failures, ${warningCount} warnings`
  );
  console.log(formatBrowserWarningTrend(warningCount, previousWarningBaseline));
  const failureSummary = summarizeBrowserFailures(report);
  if (failureSummary.length > 0) {
    console.log('Failures by message:');
    for (const { message, count } of failureSummary) {
      console.log(`  ${count} occurrences — ${message}`);
    }
  }
  if (warningCount > 0) {
    const warningCounts = new Map();
    for (const row of report.results) {
      for (const warning of row.warnings ?? []) {
        warningCounts.set(warning, (warningCounts.get(warning) ?? 0) + 1);
      }
    }
    console.log('Warnings by message:');
    for (const [message, count] of [...warningCounts].sort(
      ([messageA, countA], [messageB, countB]) =>
        countB - countA ||
        (messageA < messageB ? -1 : messageA > messageB ? 1 : 0)
    )) {
      console.log(`  ${count} occurrences — ${message}`);
    }
  }
  console.log(`Browser QA duration: ${(report.duration_ms / 1000).toFixed(1)}s`);
  console.log(`Results: ${RESULTS_FILE}`);
  if (report.failing_checks > 0) process.exit(1);
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
  const skippedRoutes = [];
  let totalFails  = 0;
  let pagesFound  = 0;

  for (const path of PUBLIC_PATHS) {
    const fsPath = path.endsWith('.html')
      ? resolve(ROOT, path.replace(/^\//, ''))
      : resolve(ROOT, path.replace(/^\//, ''), 'index.html');
    if (!existsSync(fsPath)) {
      console.log(`  SKIP  ${path} — file not found`);
      skippedRoutes.push({
        url: `${BASE_URL}${path}`,
        path,
        reason: 'local HTML file not found',
        checked: false,
      });
      continue;
    }

    const html   = readFileSync(fsPath, 'utf-8');
    const errors = staticLintPage(path, html);
    pagesFound++;

    for (const vp of VIEWPORTS) {
      const rowErrors = [...errors];
      const pass = rowErrors.length === 0;
      if (!pass) totalFails++;
      results.push({
        url:      BASE_URL + path,
        viewport: vp.name,
        width:    vp.width,
        height:   vp.height,
        mode:     'static-lint',
        pass,
        errors: rowErrors,
      });
    }

    if (errors.length > 0) {
      console.log(`  FAIL  ${path}`);
      errors.forEach(e => console.log(`         → ${e}`));
    } else {
      console.log(`  pass  ${path}`);
    }
  }

  const report = {
    generated: new Date().toISOString(),
    mode: 'static-lint',
    ...getStartProvenanceReport(),
    ...getCompletionProvenance(),
    note: [
      'Static-lint mode: 10 structural checks per page, applied uniformly to all configured viewport rows.',
      'Sitemap routes listed in skipped_routes were not checked because their local HTML files were missing.',
      'Viewport-specific checks (overflow, console errors, broken images) require Playwright.',
      'BrandGuard breadcrumb, heading, subtitle, and tagline box geometry and visible text-line reflow are checked on every sitemap-listed BrandGuard route in Playwright mode at mobile-320, mobile-350-edge, mobile-355-boundary, mobile-356-gap, mobile-360, mobile-364-gap, mobile-365-boundary, mobile-370-edge, mobile-390, mobile-400-edge (400px), mobile-401-edge (401px), mobile-430, tablet-768, tablet-769-edge, tablet-899, and tablet-900-edge; pages with deferred theme styles compare both independently before and after activation, while active-theme pages verify rendered geometry.',
      'BrandGuard hero geometry after web fonts load is checked on every sitemap-listed BrandGuard route at mobile-320, mobile-350-edge, mobile-360, mobile-370-edge, mobile-390, mobile-400-edge (400px), mobile-401-edge (401px), tablet-768, tablet-769-edge, tablet-899, and tablet-900-edge; the hub, LVMH, and BFS routes also compare the same four hero elements before and after expected fonts load at 349px and 371px with font-resource evidence and an independent 1px tolerance.',
      'At mobile-320, every sitemap-listed BrandGuard route compares shared-header and open mobile-navigation labels before and after required web fonts load, including Open Sans for menu labels, with a separate 1px tolerance; at 320px every label must remain single-line and unclipped with no horizontal overflow. The hub, LVMH, and BFS routes also receive the 350px and 370px edge checks.',
      'Shared-header and open mobile-navigation geometry, labels, and font-resource evidence are measured separately from hero geometry before and after branded fonts load on every sitemap-listed BrandGuard route at mobile-355-boundary and mobile-365-boundary, with a 1px tolerance.',
      'On sitemap-listed BrandGuard routes whose local pages defer theme styles, the shared header and open mobile menu are measured before and after theme activation at 355px and 365px; route-specific geometry shifts and activation failures are reported independently from font and hero evidence.',
      'Hero web-font boundary measurements cover every sitemap-listed BrandGuard route at mobile-355-boundary and mobile-365-boundary.',
      'At every CSS viewport width from 356px through 364px, the BrandGuard hub, LVMH, and BFS routes compare the same four hero elements before and after expected web-font faces load, preserve font-resource evidence, and use an independent 1px tolerance.',
      'BrandGuard breadcrumb and hero readability, clipping, viewport overflow, and overlap are checked in Playwright mode on every sitemap-listed BrandGuard route at mobile-320; fallback-font and branded-font content checks also run at mobile-350-edge and mobile-370-edge.',
      'Every sitemap-listed BrandGuard breadcrumb is Tab-tested at mobile-350-edge and mobile-370-edge for visible in-viewport focus rings, hero-heading collision and clipping, accessible current-page labels, and valid same-site link destinations.',
      'BrandGuard fallback-font readability is independently checked with Google Fonts requests blocked on every sitemap-listed route at 400px and 401px, after the full local theme is active.',
      'Every sitemap-listed BrandGuard case study is checked in isolated browser-emulated forced-colors mode at 320px and 1280px for visible breadcrumb, heading, subtitle, and tagline text, clipping, viewport overflow, and overlap; failures include route and viewport.',
       'At mobile-320, the BrandGuard hub, LVMH, and BFS hero actions are Tab-tested in an isolated forced-colors browser context for reachability, a visible focus outline with at least 3:1 contrast, viewport bounds, and ancestor clipping; this keyboard-focus evidence is reported separately from forced-colors text readability.',
      'BrandGuard hub case-study cards and detail-page styled call-to-action links are measured before and after branded-font loading at mobile-320, mobile-350-edge, mobile-370-edge, and mobile-390; undersized targets and overlaps include route, label, destination, stage, and target geometry in Playwright result rows.',
       'Playwright checks hero action accessible names against visible labels and sibling uniqueness on every sitemap-listed BrandGuard route at mobile-320; failures include route, label, and destination.',
      'The BrandGuard hub, LVMH, and BFS hero action area and links are compared before and after branded-font loading at mobile-320 in Playwright mode, after deferred theme styles settle; labels, overlap, clipping, 44px touch targets, and 1px geometry tolerance are checked separately from theme activation.',
      'The BrandGuard hub, LVMH, and BFS mobile menus are checked at 320px for Tab-reachable toggles, keyboard opening with Enter and Space, Escape and Space closure, ordered access to every visible link, visible unclipped focus rings, and horizontal overflow.',
      'At mobile-320, responsive QA opens the menu on every sitemap-listed BrandGuard route, follows each distinct local destination, checks the reached path and fragment target, rejects not-found pages, and records source route, label, expected destination, reached destination, HTTP status, and result.',
      'Universe hero geometry and page-map shell geometry during sequential Mermaid rendering are checked in dark mode in Playwright mode at mobile-320, mobile-390, tablet-768, and desktop-1280; each ready page-map group is also measured through light and dark color-scheme switches.',
      'Every rendered page-map group is checked for theme-matched node fills, 4.5:1 node/edge-label contrast, and 3:1 flow-connector/arrowhead contrast against the active theme background before, between, and after dark/light switches without replacing its SVG.',
      'Every generated page-map group is also checked in browser-emulated forced-colors mode for Canvas node surfaces, CanvasText outlines and labels, readable summaries and fallback links, contrasting card boundaries, and visible keyboard focus; failures include the affected selector and computed colors.',
      'For each generated page-map group, keyboard focus is placed on the summary and then a fallback link before forced-colors is enabled; each transition verifies focus retention, text and boundary contrast, and a visible focus ring with selector-specific computed colors in failures.',
      'Universe page-map shell and neighboring-summary geometry is measured before and after web fonts become ready at mobile-320, mobile-390, tablet-768, and desktop-1280; selected maps are panned before fonts load and must retain horizontal scroll position afterward, with only selected groups open; first and last open groups are measured together at mobile-320 and mobile-390, while the midpoint group remains measured at mobile-390; fallback-font state, loaded-font state, and Mermaid SVG readiness are reported separately. At mobile-320, rapid phone-rotation bursts are also exercised while font assets remain held, and settled reservation widths and page overflow are checked.',
      'At mobile-320, one already-open page map is resized through 320px portrait, 740px landscape, and 320px portrait again; reservation widths, page overflow, shell and summary usability, SVG identity, and restored closed state are reported at every stage.',
      'Every generated Universe page-map group is checked for page overflow, diagram scrolling, usable summaries, and working ordinary links at mobile-320 in Playwright mode.',
      'At mobile-320 and desktop-1280 (at 200% zoom), two different page maps are panned to distinct positions; closing one group and opening the other must leave only the selected group open and preserve each map position when revisited.',
      'At mobile-320, the same ready page-map SVG is swiped horizontally and vertically in both finger directions with touch input in light mode; horizontal position is retained through dark and light theme switches and through tapping the selected group closed and open again, with the selected/open and collapsed-group states checked. Both vertical gestures scroll the page in the expected direction without activating a map link or opening another group; SVG identity and ordinary link activation are also checked. At mobile-320 and desktop-1280 (at 200% zoom), the selected map is also panned before keyboard close/reopen and its horizontal position and group state are verified afterward.',
      'Every generated Universe page-map group is checked for clipped summaries and links, keyboard access, local diagram scrolling, and page overflow at a 200% zoom-equivalent layout viewport (1280x800 reference, 640x400 CSS viewport) in Playwright mode. The no-JavaScript fallback is also loaded with forced-colors active before navigation; its first disclosure summary, boundary, every ordinary destination link, and keyboard focus are checked with selector-specific computed-color evidence.',
      'To run full browser QA: npm install -D playwright && npx playwright install chromium && node scripts/responsive-qa.mjs',
    ].join(' '),
    base_url: BASE_URL,
    pages_checked: pagesFound,
    skipped_routes: skippedRoutes,
    coverage_complete: skippedRoutes.length === 0,
    viewports_checked: VIEWPORTS.length,
    total_checks: results.length,
    passing_checks: results.filter(r => r.pass).length,
    failing_checks: totalFails,
    results,
  };

  writeResultsReport(report);

  console.log(`\nStatic-lint: ${pagesFound} pages × ${VIEWPORTS.length} viewports = ${results.length} checks`);
  if (skippedRoutes.length > 0) {
    console.log(`Skipped: ${skippedRoutes.length} sitemap route(s) — not checked because local HTML files were missing.`);
    console.log('STATIC LINT INCOMPLETE: skipped sitemap routes were not checked.');
  }
  console.log(`Passing: ${report.passing_checks} | Failing: ${totalFails}`);
  if (totalFails === 0 && skippedRoutes.length === 0) {
    console.log('ALL CHECKS PASS.');
  }
  console.log(`Results: ${RESULTS_FILE}`);
  if (totalFails > 0 || skippedRoutes.length > 0) process.exit(1);
  return report;
}

// ── Entry point ───────────────────────────────────────────────────────────────

(async () => {
  if (process.argv.includes('--list-interrupted')) {
    listInterruptedReports();
    return;
  }

  console.log('AskJamie™ Responsive QA\n' + '='.repeat(40));
  console.log(`Base URL: ${BASE_URL}`);
  if ((BRANDGUARD_EDGE_ONLY || BRANDGUARD_FONT_RESERVATION_EDGE_ONLY) && FORCE_STATIC) {
    console.error('BrandGuard edge-only modes require Playwright mode; remove --static.');
    process.exit(2);
  }
  if (BRANDGUARD_EDGE_ONLY && BRANDGUARD_FONT_RESERVATION_EDGE_ONLY) {
    console.error('Choose only one BrandGuard edge-only mode.');
    process.exit(2);
  }
  if (BRANDGUARD_FONT_RESERVATION_EDGE_ONLY &&
      QA_PATHS.length !== BRANDGUARD_FONT_RESERVATION_EDGE_PATHS.length) {
    console.error(
      'The BrandGuard font reservation-edge run requires all three configured routes in sitemap.xml.'
    );
    process.exit(1);
  }
  console.log(`Pages: ${QA_PATHS.length} | Viewports: ${QA_VIEWPORTS.length}\n`);

  const previousReportError = reconcilePreviousBrowserReport();
  if (previousReportError) {
    console.error(previousReportError);
    process.exit(1);
  }

  const archivedReport = archivePreviousInterruptedReport();
  if (archivedReport) console.log(`Archived previous interrupted report: ${archivedReport}`);

  if (REQUIRE_RELEASE_HERO_ROUTES) {
    const requiredRoutes = [BRANDGUARD_GEOMETRY_PATH, UNIVERSE_DIAGRAM_GEOMETRY_PATH];
    const missingRoutes = requiredRoutes.filter(path => !PUBLIC_PATHS.includes(path));
    if (missingRoutes.length > 0) {
      console.error(
        `Required release hero routes are missing from sitemap.xml: ${missingRoutes.join(', ')}`
      );
      process.exit(1);
    }
  }

  let previousWarningBaseline = null;
  try {
    previousWarningBaseline = readPreviousCompletedBrowserWarningBaseline();
    if (previousWarningBaseline) {
      // Preserve the last complete browser run before either mode can replace
      // results.json with an in-progress, interrupted, or static-lint report.
      saveBrowserWarningBaseline(previousWarningBaseline);
    }
  } catch (error) {
    if (!FORCE_STATIC) {
      console.error(`Unable to read previous browser QA warning baseline: ${error.message}`);
      process.exit(1);
    }
    console.warn(`Unable to preserve previous browser QA warning baseline: ${error.message}`);
  }

  const pwResult = FORCE_STATIC
    ? null
    : await runWithPlaywright(previousWarningBaseline);
  if (pwResult?.ok === false) {
    console.error(`Required browser QA could not start: ${pwResult.reason}`);
    console.error('Run with --static only if you explicitly want the structural lint mode.');
    process.exit(1);
  }
  if (pwResult?.stopped) return;
  if (!pwResult) {
    await staticAnalysis();
  }
})();

function isMermaidInlineStyleWarning(msg) {
  return msg.type() === 'error' &&
    msg.text().startsWith('Applying inline style violates the following Content Security Policy directive') &&
    /\/assets\/vendor\/mermaid\//.test(msg.location().url || '');
}

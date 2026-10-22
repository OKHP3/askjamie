const MIN_DESTINATIONS_FOR_SCALING_CHECK = 9;
const MIN_SLOWDOWN_RATIO = 2.5;
const MIN_SLOWDOWN_DELTA_MS = 100;
const PAGE_MAP_HISTORY_SCHEMA_VERSION = 1;
const PAGE_MAP_HISTORY_RUN_LIMIT = 12;
const PAGE_MAP_BASELINE_SAMPLE_LIMIT = 5;
const PAGE_MAP_MIN_BASELINE_SAMPLES = 3;
const PAGE_MAP_CONFIRMATION_SAMPLE_COUNT = 2;
const PAGE_MAP_HISTORY_SLOWDOWN_RATIO = 1.75;
const PAGE_MAP_HISTORY_SLOWDOWN_DELTA_MS = 100;

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function roundMilliseconds(value) {
  return Math.round(value * 10) / 10;
}

function summaryKey(summary) {
  return JSON.stringify([
    summary.route,
    summary.viewport,
    summary.group_index,
    summary.destination_count,
    summary.destination_signature,
  ]);
}

/**
 * Extract one normalized destination-time sample for each generated group.
 * Only complete, full-sitemap browser runs with successful navigations are
 * useful comparisons; partial and focused runs must not contaminate history.
 */
export function summarizePageMapSweepRun(report) {
  if (
    report?.mode !== 'playwright' ||
    report?.status !== 'complete' ||
    report?.coverage_profile !== 'full-public-sitemap' ||
    !Array.isArray(report.results)
  ) {
    return [];
  }

  const summaries = [];
  for (const row of report.results) {
    const pageMap = row?.universe_mermaid_failure?.generated_page_map;
    if (!pageMap || !Array.isArray(pageMap.groups)) continue;

    let route;
    try {
      route = new URL(row.url).pathname;
    } catch {
      continue;
    }

    for (const group of pageMap.groups) {
      const destinations = group?.destinations;
      const destinationCount = group?.destination_count;
      if (
        typeof row.viewport !== 'string' ||
        !Number.isSafeInteger(group?.group_index) ||
        group.group_index < 1 ||
        !Number.isSafeInteger(destinationCount) ||
        destinationCount < 1 ||
        !Array.isArray(destinations) ||
        destinations.length !== destinationCount ||
        destinations.some(destination =>
          destination?.passed !== true ||
          typeof destination?.expected_path !== 'string' ||
          !Number.isFinite(destination?.destination_duration_ms) ||
          destination.destination_duration_ms < 0
        )
      ) {
        continue;
      }

      const durations = destinations.map(
        destination => destination.destination_duration_ms
      );
      summaries.push({
        route,
        viewport: row.viewport,
        group_index: group.group_index,
        group_label: typeof group.state?.title === 'string'
          ? group.state.title
          : `group ${group.group_index}`,
        destination_count: destinationCount,
        destination_signature: JSON.stringify(
          destinations.map(destination => destination.expected_path).sort()
        ),
        median_destination_ms: roundMilliseconds(median(durations)),
      });
    }
  }
  return summaries;
}

/**
 * Compare a completed run to recent runs from the same browser runtime.
 * A robust median baseline needs at least three samples, and both the prior
 * run and current run must exceed it to avoid alerting on one noisy run.
 */
export function assessPageMapSweepHistory(historyRuns, currentRun) {
  const regressions = [];
  let comparableGroupCount = 0;
  const compatibleHistory = [];
  for (let index = historyRuns.length - 1; index >= 0; index -= 1) {
    const run = historyRuns[index];
    if (
      run?.playwright_version !== currentRun.playwright_version ||
      run?.chromium_version !== currentRun.chromium_version ||
      !Array.isArray(run.summaries)
    ) {
      break;
    }
    compatibleHistory.unshift(run);
  }

  for (const current of currentRun.summaries) {
    const key = summaryKey(current);
    const preceding = [];
    for (let index = compatibleHistory.length - 1; index >= 0; index -= 1) {
      const matching = compatibleHistory[index].summaries.find(
        summary => summaryKey(summary) === key
      );
      if (!matching) break;
      preceding.push({
        generated: compatibleHistory[index].generated,
        median_destination_ms: matching.median_destination_ms,
      });
    }
    preceding.reverse();

    const comparisonSamples = [
      ...preceding,
      {
        generated: currentRun.generated,
        median_destination_ms: current.median_destination_ms,
      },
    ];
    const confirmation = comparisonSamples.slice(
      -PAGE_MAP_CONFIRMATION_SAMPLE_COUNT
    );
    const baseline = comparisonSamples.slice(
      0,
      -PAGE_MAP_CONFIRMATION_SAMPLE_COUNT
    ).slice(-PAGE_MAP_BASELINE_SAMPLE_LIMIT);
    if (baseline.length < PAGE_MAP_MIN_BASELINE_SAMPLES) continue;
    comparableGroupCount += 1;

    const baselineMedianMs = median(
      baseline.map(sample => sample.median_destination_ms)
    );
    const sustainedSlowdown = confirmation.every(sample => {
      const ratio = baselineMedianMs > 0
        ? sample.median_destination_ms / baselineMedianMs
        : null;
      return ratio !== null &&
        ratio >= PAGE_MAP_HISTORY_SLOWDOWN_RATIO &&
        sample.median_destination_ms - baselineMedianMs >=
          PAGE_MAP_HISTORY_SLOWDOWN_DELTA_MS;
    });
    if (!sustainedSlowdown) continue;

    const latestMedianMs =
      comparisonSamples.at(-1).median_destination_ms;
    regressions.push({
      ...current,
      baseline_median_destination_ms: roundMilliseconds(baselineMedianMs),
      current_median_destination_ms: roundMilliseconds(latestMedianMs),
      slowdown_ratio: roundMilliseconds(latestMedianMs / baselineMedianMs),
      baseline_run_count: baseline.length,
      confirmation_run_count: confirmation.length,
      comparison_window: {
        baseline_from: baseline[0].generated,
        baseline_to: baseline.at(-1).generated,
        confirmation_from: confirmation[0].generated,
        confirmation_to: confirmation.at(-1).generated,
      },
    });
  }

  return {
    regressions,
    comparable_group_count: comparableGroupCount,
    compatible_history_run_count: compatibleHistory.length,
    history_run_limit: PAGE_MAP_HISTORY_RUN_LIMIT,
    minimum_baseline_run_count: PAGE_MAP_MIN_BASELINE_SAMPLES,
    confirmation_run_count: PAGE_MAP_CONFIRMATION_SAMPLE_COUNT,
  };
}

export function appendPageMapSweepHistory(history, run) {
  return {
    schema_version: PAGE_MAP_HISTORY_SCHEMA_VERSION,
    runs: [...history.runs, run].slice(-PAGE_MAP_HISTORY_RUN_LIMIT),
  };
}

export function isValidPageMapSweepHistory(history) {
  return history?.schema_version === PAGE_MAP_HISTORY_SCHEMA_VERSION &&
    Array.isArray(history.runs) &&
    history.runs.every(run =>
      typeof run?.generated === 'string' &&
      typeof run?.playwright_version === 'string' &&
      typeof run?.chromium_version === 'string' &&
      Array.isArray(run?.summaries) &&
      run.summaries.every(summary =>
        typeof summary?.route === 'string' &&
        typeof summary?.viewport === 'string' &&
        Number.isSafeInteger(summary?.group_index) &&
        Number.isSafeInteger(summary?.destination_count) &&
        summary.destination_count > 0 &&
        typeof summary?.destination_signature === 'string' &&
        Number.isFinite(summary?.median_destination_ms) &&
        summary.median_destination_ms >= 0
      )
    );
}

/**
 * Compare the median time per destination near the start and end of a link
 * sweep. Medians make a single unusually slow destination insufficient to
 * trigger the guard; the relative and absolute margins require a sustained,
 * material increase rather than a noisy total-runtime cutoff.
 */
export function assessDestinationSweepScaling(destinationDurationsMs) {
  const samples = destinationDurationsMs.filter(
    duration => Number.isFinite(duration) && duration >= 0
  );
  const destinationCount = samples.length;

  if (destinationCount < MIN_DESTINATIONS_FOR_SCALING_CHECK) {
    return {
      status: 'insufficient_destinations',
      destination_count: destinationCount,
      checked: false,
      slowed: false,
      early_median_ms: null,
      late_median_ms: null,
      slowdown_ratio: null,
    };
  }

  const windowSize = Math.max(3, Math.floor(destinationCount / 3));
  const earlyMedianMs = median(samples.slice(0, windowSize));
  const lateMedianMs = median(samples.slice(-windowSize));
  const slowdownRatio = earlyMedianMs > 0
    ? lateMedianMs / earlyMedianMs
    : null;
  const slowed =
    slowdownRatio !== null &&
    slowdownRatio >= MIN_SLOWDOWN_RATIO &&
    lateMedianMs - earlyMedianMs >= MIN_SLOWDOWN_DELTA_MS;

  return {
    status: 'checked',
    destination_count: destinationCount,
    checked: true,
    slowed,
    early_median_ms: roundMilliseconds(earlyMedianMs),
    late_median_ms: roundMilliseconds(lateMedianMs),
    slowdown_ratio: slowdownRatio === null
      ? null
      : roundMilliseconds(slowdownRatio),
  };
}

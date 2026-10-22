export function countBrowserWarnings(report) {
  if (!Array.isArray(report?.results)) {
    throw new TypeError('Browser QA report must contain a results array');
  }

  return report.results.reduce(
    (count, row) => count + (Array.isArray(row?.warnings) ? row.warnings.length : 0),
    0
  );
}

export function completedBrowserWarningBaseline(report) {
  if (report?.mode !== 'playwright' || report?.status !== 'complete') {
    return null;
  }

  return {
    mode: 'playwright',
    status: 'complete',
    generated: typeof report.generated === 'string' ? report.generated : null,
    warning_count: countBrowserWarnings(report),
  };
}

export function formatBrowserWarningTrend(currentCount, previousBaseline) {
  if (!Number.isSafeInteger(currentCount) || currentCount < 0) {
    throw new TypeError('Current browser warning total must be a non-negative integer');
  }

  if (previousBaseline == null) {
    return `Browser QA warning trend: current ${currentCount}; no previous completed browser report.`;
  }

  const previousCount = previousBaseline.warning_count;
  if (!Number.isSafeInteger(previousCount) || previousCount < 0) {
    throw new TypeError('Previous browser warning total must be a non-negative integer');
  }

  const change = currentCount - previousCount;
  if (change === 0) {
    return `Browser QA warning trend: current ${currentCount}; previous completed browser report: ${previousCount} warnings (unchanged).`;
  }

  const direction = change > 0 ? 'increased' : 'decreased';
  return (
    `Browser QA warning trend: current ${currentCount}; previous completed browser report: ` +
    `${previousCount} warnings (${direction} by ${Math.abs(change)}).`
  );
}

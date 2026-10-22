export function summarizeBrowserFailures(report) {
  if (!Array.isArray(report?.results)) {
    throw new TypeError('Browser QA report must contain a results array');
  }

  const counts = new Map();
  for (const row of report.results) {
    if (row?.pass !== false || !Array.isArray(row.errors)) continue;
    for (const message of row.errors) {
      counts.set(message, (counts.get(message) ?? 0) + 1);
    }
  }

  return [...counts]
    .map(([message, count]) => ({ message, count }))
    .sort(
      (a, b) =>
        b.count - a.count ||
        (a.message < b.message ? -1 : a.message > b.message ? 1 : 0)
    );
}

// Deferred application scripts must finish before font geometry is measured.
export async function waitForFontBaselineReadiness(page, timeoutMs) {
  await page.waitForLoadState('domcontentloaded', { timeout: timeoutMs });
  return page.evaluate(() => ({ document_ready_state: document.readyState }));
}

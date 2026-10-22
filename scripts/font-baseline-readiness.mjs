// Font comparisons must not include controls revealed by application startup.
export async function waitForFontBaselineReadiness(page, timeoutMs) {
  await page.waitForLoadState('domcontentloaded', { timeout: timeoutMs });
  await page.waitForFunction(() => {
    const control = document.querySelector(
      '.capability-transition [data-transition-open]'
    );
    return !control || !control.hidden;
  }, undefined, { timeout: timeoutMs });
  return page.evaluate(() => {
    const control = document.querySelector(
      '.capability-transition [data-transition-open]'
    );
    return {
      document_ready_state: document.readyState,
      transition_control_present: Boolean(control),
      transition_control_revealed: control ? !control.hidden : null,
    };
  });
}

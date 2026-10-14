import { chromium } from "playwright";

const baseUrl = process.env.BASE_URL || "http://127.0.0.1:5000";
const failures = [];

function check(condition, message) {
  if (!condition) failures.push(message);
}

async function waitFor(locator, options, path, description) {
  try {
    await locator.waitFor(options);
  } catch (error) {
    throw new Error(`${path}: ${description}: ${error.message}`);
  }
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const brandRequests = [];
page.on("request", (request) => {
  if (request.url().includes("/assets/js/askjamie-analytics.js")) brandRequests.push(request.url());
});
page.on("pageerror", (error) => failures.push(`uncaught browser error: ${error.message}`));
page.on("requestfailed", (request) => {
  const failure = request.failure();
  if (failure?.errorText === "net::ERR_ABORTED") return;
  failures.push(
    `request failed: ${request.url()}${failure ? ` (${failure.errorText})` : ""}`
  );
});
page.on("console", (message) => {
  if (message.type() === "error") {
    failures.push(`browser console error: ${message.text()}`);
  }
});

try {
  const universePath = "/universe/";
  try {
    await page.goto(`${baseUrl}${universePath}`, { waitUntil: "domcontentloaded" });
  } catch (error) {
    throw new Error(`${universePath}: ${error.message}`);
  }
  await waitFor(page.locator(".askjamie-mermaid-shell svg").first(), {
    state: "attached",
    timeout: 15000,
  }, universePath, "Mermaid SVG did not load");
  check(
    (await page.locator(".askjamie-mermaid-shell svg").count()) > 0,
    "Universe Mermaid diagram did not render an SVG"
  );
  check(
    await page.locator('.askjamie-hero--universe [data-mermaid-failure-status]').textContent() === "" &&
      (await page.locator('.askjamie-hero--universe [data-mermaid-failure-status]').getAttribute("class"))
        .split(/\s+/).includes("sr-only"),
    "Universe failure status should remain empty and visually hidden after a successful Mermaid render"
  );

  const noJsContext = await browser.newContext({ javaScriptEnabled: false });
  const noJsPage = await noJsContext.newPage();
  await noJsPage.goto(`${baseUrl}${universePath}`, { waitUntil: "domcontentloaded" });
  check(
    await noJsPage.locator(".mermaid-noscript").isVisible(),
    "Universe static fallback is not visible without JavaScript"
  );
  const noJsStatus = noJsPage.locator(
    '.askjamie-hero--universe [data-mermaid-failure-status]'
  );
  check(
    await noJsStatus.textContent() === "" &&
      await noJsStatus.getAttribute("role") === "status" &&
      await noJsStatus.getAttribute("aria-live") === "polite" &&
      !(await noJsStatus.evaluate((node) => node.hasAttribute("hidden"))) &&
      (await noJsStatus.getAttribute("class")).split(/\s+/).includes("sr-only") &&
      await noJsStatus.evaluate((node) => getComputedStyle(node).display !== "none"),
    "Universe failure status should be an initially empty, exposed polite live region"
  );
  check(
    (await noJsPage.locator('.mermaid-noscript a[href="/contact/"]').count()) === 1,
    "Universe static fallback is missing its contact link"
  );
  await noJsContext.close();

  const renderFailurePage = await context.newPage();
  await renderFailurePage.route("**/*", (route) => {
    const requestUrl = new URL(route.request().url());
    if (requestUrl.pathname === "/assets/vendor/mermaid/mermaid.esm.min.mjs") {
      return route.fulfill({
        status: 200,
        contentType: "text/javascript",
        body: 'export default { initialize() {}, run() { return new Promise((resolve, reject) => { window.__triggerMermaidFailure = () => reject(new Error("simulated render failure")); }); } };',
      });
    }
    if (/fonts\.(gstatic|googleapis)\.com|google-analytics\.com|googletagmanager\.com/.test(requestUrl.hostname)) {
      return route.abort();
    }
    return route.continue();
  });
  await renderFailurePage.goto(`${baseUrl}${universePath}`, { waitUntil: "domcontentloaded" });
  const renderFailureStatus = renderFailurePage.locator(
    '.askjamie-hero--universe [data-mermaid-failure-status]'
  );
  const universeNavLinks = renderFailurePage.locator(
    ".askjamie-hero--universe .link-list a"
  );
  await renderFailurePage.waitForFunction(
    () => typeof window.__triggerMermaidFailure === "function"
  );
  await universeNavLinks.nth(0).focus();
  check(
    await universeNavLinks.nth(0).evaluate((node) => document.activeElement === node),
    "Universe navigation link could not receive keyboard focus before render failure"
  );
  await renderFailurePage.evaluate(() => window.__triggerMermaidFailure());
  await renderFailurePage.waitForFunction(() => {
    const status = document.querySelector(
      '.askjamie-hero--universe [data-mermaid-failure-status]'
    );
    return status?.textContent.includes("could not be displayed") &&
      !status.classList.contains("sr-only");
  }, { timeout: 15000 });
  check(
    (await renderFailureStatus.textContent()).includes("could not be displayed") &&
      await renderFailureStatus.getAttribute("role") === "status" &&
      await renderFailureStatus.getAttribute("aria-live") === "polite" &&
      !(await renderFailureStatus.getAttribute("class")).split(/\s+/).includes("sr-only"),
    "Universe render failure did not show an accessible unavailable status"
  );
  check(
    await universeNavLinks.count() === 3 &&
      await universeNavLinks.evaluateAll((links) => links.every((link) => {
        const style = getComputedStyle(link);
        return link instanceof HTMLAnchorElement &&
          link.href &&
          style.display !== "none" &&
          style.visibility !== "hidden";
      })),
    "Universe navigation links should remain present and visible after render failure"
  );
  check(
    await universeNavLinks.nth(0).evaluate((node) => document.activeElement === node),
    "Universe render failure moved focus away from the focused navigation link"
  );
  await renderFailurePage.keyboard.press("Tab");
  check(
    await universeNavLinks.nth(1).evaluate((node) => document.activeElement === node),
    "Universe navigation links are not reachable in keyboard order after render failure"
  );
  await renderFailurePage.keyboard.press("Tab");
  check(
    await universeNavLinks.nth(2).evaluate((node) => document.activeElement === node),
    "Universe navigation links are not reachable in keyboard order after render failure"
  );
  check(
    await renderFailurePage.locator(
      ".askjamie-hero--universe .mermaid[data-universe-ready='1']"
    ).count() === 0,
    "Universe render failure incorrectly reported the diagram as ready"
  );
  await renderFailurePage.close();

  try {
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  } catch (error) {
    throw new Error("/: " + error.message);
  }
  const searchTrigger = page.locator(".okh-search-trigger");
  await waitFor(searchTrigger, { state: "visible", timeout: 5000 }, "/", "Search trigger unavailable");
  await page.locator('[data-transition-dialog][open] [data-transition-dismiss]').click();
  await searchTrigger.click();
  const overlay = page.locator(".okh-search-overlay");
  await waitFor(overlay, { state: "visible", timeout: 5000 }, "/", "Search overlay unavailable");
  const searchInput = overlay.locator(".okh-search-input");
  await searchInput.fill("BrandGuard");
  check(
    (await searchInput.inputValue()) === "BrandGuard",
    "Search overlay input did not accept keyboard text"
  );
  check(
    (await overlay.getAttribute("data-open")) === "true",
    "Search overlay did not open"
  );
  await page.keyboard.press("Escape");

  // Exercise a formerly blocking app page and prove the brand module runs
  // once, after the app, through its fingerprinted dynamic import.
  brandRequests.length = 0;
  await page.goto(`${baseUrl}/lens-system/resume-representative/`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof window.askJamieTrack === "function");
  check(brandRequests.length === 1 && /\?v=[a-f0-9]{8}$/.test(brandRequests[0]),
    "Brand analytics must load once through its content fingerprint");
  check(await page.locator('script[src*="/assets/js/app.js"]').evaluate((script) => script.defer),
    "Shared app must use defer");
  const trackedEvents = await page.evaluate(() => {
    const events = [];
    window.gtag = (...args) => events.push(args);
    const link = document.querySelector('a[href*="chatgpt.com/g/"]');
    link.addEventListener("click", (event) => event.preventDefault(), { once: true });
    link.click();
    return events;
  });
  check(trackedEvents.filter((args) => args[0] === "event" && args[1] === "gpt_click").length === 1,
    "A GPT click must produce exactly one existing analytics event");

  const aboutPath = "/about/";
  for (const colorScheme of ["light", "dark"]) {
    const schemeContext = await browser.newContext({ colorScheme });
    await schemeContext.addInitScript(() => {
      if (localStorage.getItem("askjamie-color-scheme") === null) {
        localStorage.setItem("askjamie-color-scheme", "light");
      }
    });
    const schemePage = await schemeContext.newPage();
    await schemePage.goto(`${baseUrl}${aboutPath}`, { waitUntil: "domcontentloaded" });
    const schemeToggle = schemePage.locator(".glee-color-toggle");
    await waitFor(schemeToggle, { state: "visible", timeout: 5000 }, aboutPath, "Color-scheme toggle unavailable");
    check(await schemePage.locator("html").getAttribute("data-theme") === "light", "AskJamie must keep data-theme light");
    check(await schemePage.locator("html").getAttribute("data-color-scheme") === "light", "Stored light scheme was not applied before paint");
    const lightBody = await schemePage.locator("body").evaluate((el) => getComputedStyle(el).backgroundColor);
    const lightFooter = await schemePage.locator(".site-footer").evaluate((el) => getComputedStyle(el).backgroundColor);
    await schemeToggle.click();
    check(await schemePage.locator("html").getAttribute("data-color-scheme") === "dark", "Toggle did not apply dark scheme");
    const darkBody = await schemePage.locator("body").evaluate((el) => getComputedStyle(el).backgroundColor);
    const darkFooter = await schemePage.locator(".site-footer").evaluate((el) => getComputedStyle(el).backgroundColor);
    check(lightBody !== darkBody || lightFooter !== darkFooter, `Visible scheme did not change under ${colorScheme} OS preference`);
    await schemePage.reload({ waitUntil: "domcontentloaded" });
    check(await schemePage.locator("html").getAttribute("data-color-scheme") === "dark", "Dark preference did not persist across reload");
    await schemePage.locator(".glee-color-toggle").click();
    check(await schemePage.locator("html").getAttribute("data-color-scheme") === null, "Auto scheme did not remove the explicit attribute");
    await schemeContext.close();
  }
} catch (error) {
  failures.push(error.message);
} finally {
  await browser.close();
}

if (failures.length) {
  console.error("JavaScript smoke tests failed:");
  failures.forEach((failure) => console.error(`  - ${failure}`));
  process.exit(1);
}

console.log("JavaScript smoke tests passed: Mermaid, search overlay, dark mode, deferred app, and fingerprinted analytics without duplicate events.");

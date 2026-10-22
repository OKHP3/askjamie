import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.BASE_URL || "http://127.0.0.1:5000";
const browser = await chromium.launch({ headless: true });
const fontHosts = new Set(["fonts.googleapis.com", "fonts.gstatic.com"]);

async function runFailureScenario(failureTarget, failureMode = "network") {
  const isCorruptHttp200 = failureMode === "corrupt-http-200";
  const httpStatus = isCorruptHttp200
    ? 200
    : failureTarget === "stylesheet" ? 403 : 503;
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    colorScheme: "dark",
  });
  const fontResponses = [];
  const fontFailures = [];
  const httpFailedFontRequests = new WeakSet();
  let failingFontUrl = null;
  let releaseFont;
  let gateOpen = false;
  const fontGate = new Promise(resolve => { releaseFont = resolve; });
  const isFailureRequest = request => {
    const url = new URL(request.url());
    return failureTarget === "stylesheet"
      ? url.hostname === "fonts.googleapis.com" &&
          request.resourceType() === "stylesheet"
      : url.hostname === "fonts.gstatic.com" &&
          request.resourceType() === "font";
  };
  const failureEvent = failureMode === "http" || isCorruptHttp200
    ? page.waitForEvent("response", {
      predicate: response =>
        isFailureRequest(response.request()) && response.status() === httpStatus,
      timeout: 15000,
    })
    : page.waitForEvent("requestfailed", {
      predicate: isFailureRequest,
      timeout: 15000,
    });

  page.on("response", response => {
    const url = new URL(response.url());
    if (fontHosts.has(url.hostname) &&
        ["stylesheet", "font"].includes(response.request().resourceType())) {
      fontResponses.push({
        host: url.hostname,
        resourceType: response.request().resourceType(),
        status: response.status(),
      });
      if (response.status() >= 400) {
        httpFailedFontRequests.add(response.request());
        fontFailures.push({
          url: response.url(),
          host: url.hostname,
          resourceType: response.request().resourceType(),
          status: response.status(),
        });
      }
    }
  });
  page.on("requestfailed", request => {
    const url = new URL(request.url());
    if (fontHosts.has(url.hostname) &&
        ["stylesheet", "font"].includes(request.resourceType()) &&
        !httpFailedFontRequests.has(request)) {
      fontFailures.push({
        url: request.url(),
        host: url.hostname,
        resourceType: request.resourceType(),
        error: request.failure()?.errorText || "unknown",
      });
    }
  });

  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const resourceType = request.resourceType();
    if (url.hostname === "fonts.googleapis.com" &&
        resourceType === "stylesheet" && failureTarget === "stylesheet" &&
        failureMode === "network") {
      await route.abort();
      return;
    }
    if (url.hostname === "fonts.googleapis.com" &&
        resourceType === "stylesheet" && failureTarget === "stylesheet" &&
        failureMode === "http") {
      await route.fulfill({
        status: httpStatus,
        contentType: "text/css",
        body: "/* stylesheet unavailable */",
      });
      return;
    }
    if (url.hostname === "fonts.gstatic.com" &&
        resourceType === "font" && failureTarget === "font-file") {
      failingFontUrl ??= request.url();
      if (!gateOpen) await fontGate;
      if (request.url() !== failingFontUrl) {
        await route.continue();
        return;
      }
      if (failureMode === "http" || isCorruptHttp200) {
        await route.fulfill({
          status: httpStatus,
          contentType: "font/woff2",
          body: isCorruptHttp200 ? "not a valid WOFF2 font" : "font unavailable",
        });
      } else {
        await route.abort();
      }
      return;
    }
    await route.continue();
  });

  try {
    await page.goto(new URL("/universe/", base).href, { waitUntil: "domcontentloaded" });
    const groups = page.locator(".universe-map-group");
    await groups.first().waitFor({ state: "visible", timeout: 20000 });
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".universe-map-generated .universe-map-group")]
        .every(group => !group.open),
    null, { timeout: 10000 });

    if (failureTarget === "stylesheet") {
      await page.waitForFunction(() =>
        [...document.querySelectorAll("link[data-deferred-fonts]")]
          .some(link => link.media === "all"),
      null, { timeout: 10000 });
      const failedResponse = await failureEvent;
      failingFontUrl = failedResponse.url();
    } else {
      const fontRequest = page.waitForRequest(request =>
        new URL(request.url()).hostname === "fonts.gstatic.com" &&
        request.resourceType() === "font",
      { timeout: 15000 });
      const request = await fontRequest;
      assert.equal(gateOpen, false, "Font response must remain held before release");

      gateOpen = true;
      releaseFont();
      const failedEvent = await failureEvent;
      failingFontUrl = failedEvent.url();
      assert.equal(failingFontUrl, request.url(),
        "The failed response must match the held hosted font request");
      await page.evaluate(() => document.fonts.ready);
    }

    const browserFontState = isCorruptHttp200
      ? await page.evaluate(async () => {
        const summary = document.querySelector(".universe-map-group summary");
        const style = getComputedStyle(summary);
        let loadError = null;
        let loadedFaces = [];
        try {
          loadedFaces = await document.fonts.load(
            style.font,
            summary.textContent || "Universe",
          );
        } catch (error) {
          loadError = error.message;
        }
        await document.fonts.ready;
        const family = style.fontFamily.split(",")[0].trim()
          .replace(/^["']|["']$/g, "");
        const faces = [...document.fonts]
          .filter(face => face.family.replace(/^["']|["']$/g, "").toLowerCase() ===
            family.toLowerCase())
          .map(face => ({ family: face.family, status: face.status }));
        return {
          family,
          status: document.fonts.status,
          loadError,
          loadedFaces: loadedFaces.map(face => ({
            family: face.family,
            status: face.status,
          })),
          faces,
        };
      })
      : null;
    const unusableFace = isCorruptHttp200 && (
      Boolean(browserFontState.loadError) ||
      browserFontState.loadedFaces.length === 0 ||
      browserFontState.loadedFaces.some(face => face.status !== "loaded") ||
      browserFontState.faces.some(face => face.status !== "loaded")
    );
    if (unusableFace) {
      fontFailures.push({
        url: failingFontUrl,
        host: "fonts.gstatic.com",
        resourceType: "font",
        status: 200,
        failureKind: "decode",
        error: browserFontState.loadError ||
          `Browser did not load a usable ${browserFontState.family} face`,
      });
    }

    const failedRequest = fontFailures.some(failure =>
      failureTarget === "stylesheet"
        ? failure.host === "fonts.googleapis.com" && failure.resourceType === "stylesheet"
        : failure.host === "fonts.gstatic.com" && failure.resourceType === "font"
    );
    assert.equal(failedRequest, true, `${failureTarget}: failed request must remain visible`);
    assert.equal(
      fontFailures.filter(failure => failure.url === failingFontUrl).length,
      1,
      `${failureTarget}: one failed hosted resource must appear once in the QA evidence`,
    );
    if (failureMode === "http" || isCorruptHttp200) {
      assert.equal(
        fontFailures.some(failure =>
          failure.url === failingFontUrl && failure.status === httpStatus
        ),
        true,
        `${failureTarget}: failed response status must remain visible`,
      );
      assert.equal(
        fontResponses.some(response => response.status === httpStatus),
        true,
        `${failureTarget}: HTTP response must be recorded`,
      );
      if (isCorruptHttp200) {
        assert.equal(httpStatus, 200, "Corrupt font fixture must return HTTP 200");
        assert.equal(
          fontResponses.some(response =>
            response.resourceType === "font" && response.status === 200
          ),
          true,
          "Corrupt font response must be recorded as HTTP 200",
        );
        assert.equal(unusableFace, true,
          "Browser must report the HTTP-200 font face as unusable or unloaded");
        assert.equal(
          fontFailures.some(failure =>
            failure.url === failingFontUrl && failure.failureKind === "decode"
          ),
          true,
          "Browser font decode failure must remain visible in evidence",
        );
      }
    } else {
      assert.equal(
        fontFailures.some(failure =>
          failure.url === failingFontUrl &&
          typeof failure.error === "string" &&
          failure.error.length > 0
        ),
        true,
        `${failureTarget}: network failure reason must remain visible`,
      );
    }

    const stylesheetLoaded = fontResponses.some(response =>
      response.resourceType === "stylesheet" && response.status < 400
    );
    const fontAssetLoaded = fontResponses.some(response =>
      response.resourceType === "font" && response.status >= 200 && response.status < 300
    );
    const fontLoadVerified = stylesheetLoaded && fontAssetLoaded && fontFailures.length === 0;
    const geometryComparisonAvailable = fontLoadVerified;
    assert.equal(fontLoadVerified, false, `${failureTarget}: font readiness must be unavailable`);
    assert.equal(geometryComparisonAvailable, false,
      `${failureTarget}: comparison must not pass without a verified font load`);
    if (failureTarget === "font-file" && failureMode === "http") {
      assert.equal(stylesheetLoaded, true,
        "The stylesheet may load successfully even though the font asset fails");
      assert.equal(fontAssetLoaded, true,
        "Other hosted font assets may load successfully alongside the HTTP error");
    }

    const summary = groups.first().locator("summary");
    await summary.focus();
    await page.keyboard.press("Enter");
    assert.equal(await groups.first().evaluate(group => group.open), true,
      "Keyboard must open a page-map group after hosted-font failure");
    await page.keyboard.press("Enter");
    assert.equal(await groups.first().evaluate(group => group.open), false,
      "Keyboard must close a page-map group after hosted-font failure");
    assert.equal(await groups.evaluateAll(elements => elements.every(group => !group.open)), true,
      `${failureTarget}: page-map groups must remain collapsed after the check`);

    return {
      failureTarget,
      failureMode,
      expectedHttpStatus: failureMode === "http" || isCorruptHttp200 ? httpStatus : null,
      fontResponses,
      fontFailures,
      stylesheetLoaded,
      fontAssetLoaded,
      browserFontState,
      unusableFace,
      fontLoadVerified,
      geometryComparisonAvailable,
      collapsedAfterCheck: true,
    };
  } finally {
    gateOpen = true;
    releaseFont();
    await page.close();
  }
}

try {
  const results = [];
  results.push(await runFailureScenario("stylesheet"));
  results.push(await runFailureScenario("stylesheet", "http"));
  results.push(await runFailureScenario("font-file"));
  results.push(await runFailureScenario("font-file", "http"));
  results.push(await runFailureScenario("font-file", "corrupt-http-200"));
  console.log(JSON.stringify({
    result: "PASS",
    scope: "Network, HTTP, and HTTP-200 decode failures for hosted fonts; readiness and geometry evidence; page-map keyboard and collapsed states",
    results,
  }));
} finally {
  await browser.close();
}

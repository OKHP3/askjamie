import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const base = process.env.BASE_URL || "http://127.0.0.1:5000";
const browser = await chromium.launch({ headless: true });
const captureGroupLayout = async (page, index) => page.evaluate(groupIndex => {
  const round = value => Math.round(value * 100) / 100;
  const groups = [...document.querySelectorAll(".universe-map-generated .universe-map-group")];
  const group = groups[groupIndex];
  if (!group) return null;
  const box = element => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return Object.fromEntries(["x", "y", "width", "height"].map(property => [
      property,
      round(rect[property] + (property === "x" ? scrollX : property === "y" ? scrollY : 0)),
    ]));
  };
  const diagram = group.querySelector(".mermaid");
  return {
    colorScheme: document.documentElement.getAttribute("data-color-scheme"),
    open: group.open,
    otherGroupsCollapsed: groups.every((other, otherIndex) =>
      otherIndex === groupIndex || !other.open
    ),
    svgReady: Boolean(diagram?.querySelector("svg .node")),
    geometry: {
      group: box(group),
      summary: box(group.querySelector("summary")),
      shell: box(group.querySelector(".mermaid-scroll-wrap")),
      previousSummary: box(groups[groupIndex - 1]?.querySelector("summary")),
      nextSummary: box(groups[groupIndex + 1]?.querySelector("summary")),
    },
  };
}, index);

const assertGroupLayoutStable = (before, after, index, count, expectedTheme) => {
  assert.equal(after.colorScheme, expectedTheme, "Color-scheme control must apply the selected theme");
  assert.equal(after.open, true, "Theme changes must leave the selected group open");
  assert.equal(after.otherGroupsCollapsed, true, "Theme changes must leave other generated groups collapsed");
  assert.equal(after.svgReady, true, "Theme changes must preserve the rendered SVG");
  for (const [name, beforeRect] of Object.entries(before.geometry)) {
    const afterRect = after.geometry[name];
    if ((name === "previousSummary" && index === 0) ||
        (name === "nextSummary" && index === count - 1)) {
      assert.equal(afterRect, null, `The ${name} is absent only at the edge of the group list`);
      continue;
    }
    assert.ok(beforeRect && afterRect, `Both ${name} boxes must be measured`);
    for (const property of ["x", "y", "width", "height"]) {
      assert.ok(
        Math.abs(afterRect[property] - beforeRect[property]) <= 1,
        `${name} ${property} shifted when switching to ${expectedTheme}: ` +
          `${beforeRect[property]} -> ${afterRect[property]}`
      );
    }
  }
};

const contrastObservations = [];
const assertSvgLabelContrast = async (group, theme) => {
  const evidence = await group.evaluate(root => {
    const rgb = value => {
      const values = value.match(/[\d.]+/g)?.map(Number) ?? [];
      return values.length >= 3 ? values.slice(0, 3) : null;
    };
    const luminance = value => {
      const channels = rgb(value);
      if (!channels) return null;
      const [r, g, b] = channels.map(channel => {
        const normalized = channel / 255;
        return normalized <= 0.04045
          ? normalized / 12.92
          : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (foreground, background) => {
      const a = luminance(foreground), b = luminance(background);
      return a === null || b === null ? null : (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const nodeLabels = [...root.querySelectorAll("svg .node")].map(node => {
      const label = node.querySelector(".nodeLabel p, .nodeLabel");
      const surface = node.querySelector("rect");
      const foreground = label && getComputedStyle(label).color;
      const background = surface && getComputedStyle(surface).fill;
      return { text: label?.textContent.trim() ?? "", foreground, background, ratio: ratio(foreground, background) };
    });
    const edgeLabels = [...root.querySelectorAll("svg .edgeLabel text, svg .edgeLabel p, svg .edgeLabel span")]
      .filter(label => label.textContent.trim())
      .map(label => {
        const foreground = label.tagName.toLowerCase() === "text"
          ? getComputedStyle(label).fill
          : getComputedStyle(label).color;
        const surface = label.closest(".edgeLabel")?.querySelector("rect");
        const background = surface
          ? getComputedStyle(surface).fill
          : getComputedStyle(label.closest(".edgeLabel")).backgroundColor;
        return { text: label.textContent.trim(), foreground, background, ratio: ratio(foreground, background) };
      });
    return { nodeLabels, edgeLabels };
  });
  assert.ok(evidence.nodeLabels.length, `${theme}: rendered SVG has no node labels`);
  for (const sample of [...evidence.nodeLabels, ...evidence.edgeLabels]) {
    assert.ok(sample.ratio !== null && sample.ratio >= 4.5,
      `${theme}: low-contrast SVG label ${JSON.stringify(sample)}`);
  }
  contrastObservations.push({
    theme,
    nodeLabels: evidence.nodeLabels.length,
    edgeLabels: evidence.edgeLabels.length,
    minimumRatio: Math.min(...[...evidence.nodeLabels, ...evidence.edgeLabels].map(sample => sample.ratio)),
  });
  return evidence;
};

try {
  const report = await (await fetch(`${base}/assets/data/universe-map.json`)).json();
  for (const width of [390, 1280]) {
    for (const theme of ["light", "dark"]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: theme });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => { if (/render error/i.test(message.text())) errors.push(message.text()); });
      await page.addInitScript(value => localStorage.setItem("askjamie-color-scheme", value), theme);
      await page.goto(`${base}/universe/`);
      const groups = page.locator(".universe-map-group");
      assert.equal(await groups.count(), report.diagrams.length);
      for (let i = 0; i < await groups.count(); i++) {
        const group = groups.nth(i);
        await group.locator("summary").click();
        await group.locator(".mermaid").scrollIntoViewIfNeeded();
        await group.locator("svg .node").first().waitFor({ timeout: 20000 });
        await page.waitForFunction(element => element.querySelectorAll("svg a[href]").length === element.querySelectorAll("a[data-universe-node]").length, await group.elementHandle());
        assert.equal(await group.locator("svg .node").count(), report.diagrams[i].nodes.length);
        await assertSvgLabelContrast(group, theme);
        const labels = await group.locator(".nodeLabel").allTextContents();
        const expectedLabels = report.diagrams[i].nodes.map(id => {
          const node = report.nodes.find(item => item.id === id);
          return `${node.title} (${node.status})`.replace(/\s+/g, " ").trim();
        });
        assert.deepEqual(labels.map(value => value.replace(/\s+/g, " ").trim()).sort(), expectedLabels.sort(), "Mermaid must decode titles and symbols exactly");
        const colors = await group.locator(".nodeLabel p").first().evaluate(e => [getComputedStyle(e).color, getComputedStyle(e.closest(".node").querySelector("rect")).fill]);
        assert.notEqual(colors[0], "rgb(0, 0, 0)", "Node text must use the brand foreground");
        assert.notEqual(colors[0], colors[1], "Node text must differ from its surface");
        assert.equal(await group.locator("svg a[href^='/']").count(), report.diagrams[i].nodes.length);
        assert.ok(await group.locator(".mermaid").evaluate(element => element.inert), "Decorative hidden diagrams must be inert");
        const scrollWrap = group.locator(".mermaid-scroll-wrap");
        assert.ok(await scrollWrap.evaluate(element => !element.inert), "Scroll container must remain interactive");
        if (width === 390 && await scrollWrap.evaluate(element => element.scrollWidth > element.clientWidth)) {
          await scrollWrap.hover();
          await page.mouse.wheel(200, 0);
          await page.waitForFunction(element => element.scrollLeft > 0, await scrollWrap.elementHandle());
        }
        await group.locator("summary").focus();
        await page.keyboard.press("Tab");
        assert.equal(await page.evaluate(() => Boolean(document.activeElement.closest('[aria-hidden="true"]'))), false, "Tab must skip hidden diagram anchors");
        await group.locator("a[data-universe-node]").first().focus();
        assert.ok(await group.locator("a[data-universe-node]").first().evaluate(element => element === document.activeElement), "Equivalent visible page links remain focusable");
        if (i === 0 && process.env.OUTPUT_DIR) {
          await fs.mkdir(process.env.OUTPUT_DIR, { recursive: true });
          await group.screenshot({ path: `${process.env.OUTPUT_DIR}/universe-detail-${width}-${theme}.png` });
        }
        await group.locator("summary").click();
      }

      const switchIndex = Math.min(1, (await groups.count()) - 1);
      const switchPage = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: theme });
      await switchPage.addInitScript(value => localStorage.setItem("askjamie-color-scheme", value), theme);
      await switchPage.goto(`${base}/universe/`);
      const switchGroups = switchPage.locator(".universe-map-group");
      const switchGroup = switchGroups.nth(switchIndex);
      await switchGroup.locator("summary").click();
      await switchGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
      const layoutBeforeSwitch = await captureGroupLayout(switchPage, switchIndex);
      const groupCount = await switchGroups.count();
      const originalSvg = await switchGroup.locator("svg").evaluate(svg => {
        window.__universeQaOriginalSvg = svg;
        return true;
      });
      const beforeReadability = await assertSvgLabelContrast(switchGroup, theme);
      const toggle = switchPage.locator(".askjamie-main .glee-color-toggle");
      assert.equal(await toggle.getAttribute("data-state"), theme);
      const nextTheme = theme === "dark" ? "light" : "dark";
      const clicksToNextTheme = theme === "dark" ? 2 : 1;
      for (let click = 0; click < clicksToNextTheme; click += 1) {
        await toggle.click();
      }
      await switchPage.waitForFunction(expected =>
        document.documentElement.getAttribute("data-color-scheme") === expected, nextTheme);
      await switchPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
      const layoutAfterSwitch = await captureGroupLayout(switchPage, switchIndex);
      assertGroupLayoutStable(layoutBeforeSwitch, layoutAfterSwitch, switchIndex, groupCount, nextTheme);
      const afterReadability = await assertSvgLabelContrast(switchGroup, nextTheme);
      assert.equal(await switchPage.evaluate(() =>
        window.__universeQaOriginalSvg === document.querySelector(
          ".universe-map-generated .universe-map-group:nth-of-type(2) svg"
        )
      ), true, "Theme changes must not rerender the SVG");

      const clicksToReturn = nextTheme === "dark" ? 2 : 1;
      for (let click = 0; click < clicksToReturn; click += 1) {
        await toggle.click();
      }
      await switchPage.waitForFunction(expected =>
        document.documentElement.getAttribute("data-color-scheme") === expected, theme);
      await switchPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
      const layoutAfterReturn = await captureGroupLayout(switchPage, switchIndex);
      assertGroupLayoutStable(layoutBeforeSwitch, layoutAfterReturn, switchIndex, groupCount, theme);
      const returnedReadability = await assertSvgLabelContrast(switchGroup, theme);
      assert.equal(beforeReadability.nodeLabels.length, returnedReadability.nodeLabels.length);
      assert.equal(afterReadability.nodeLabels.length, returnedReadability.nodeLabels.length);
      assert.ok(originalSvg, "Rendered SVG must exist before theme switches");

      await switchGroup.locator("summary").focus();
      await switchPage.keyboard.press("Enter");
      assert.equal((await captureGroupLayout(switchPage, switchIndex)).open, false, "Enter must close the selected group after theme changes");
      await switchPage.keyboard.press("Enter");
      const keyboardReopened = await captureGroupLayout(switchPage, switchIndex);
      assert.equal(keyboardReopened.open, true, "Enter must reopen the selected group after theme changes");
      assert.equal(keyboardReopened.otherGroupsCollapsed, true, "Keyboard interaction must not open another group");
      assert.equal(keyboardReopened.svgReady, true, "Keyboard interaction must preserve the rendered SVG");
      await switchPage.close();

      const urls = await page.locator("a[data-universe-node]").evaluateAll(links => [...new Set(links.map(link => link.href.replace(location.origin, "https://askjamie.bot")))].sort());
      assert.deepEqual(urls, report.nodes.filter(node => node.indexed).map(node => node.url).sort());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Page overflows viewport");
      assert.deepEqual(errors, []);
      await page.close();
    }
  }
  const noJs = await browser.newPage({ javaScriptEnabled: false });
  await noJs.goto(`${base}/universe/`);
  await noJs.locator(".universe-map-group summary").first().click();
  assert.ok(await noJs.locator(".universe-map-group[open] a[data-universe-node]").first().isVisible());
  assert.equal(await noJs.locator(".universe-map-group[open] .mermaid").first().evaluate(e => getComputedStyle(e).visibility), "hidden", "Raw diagram syntax must stay hidden without JavaScript");
  await noJs.locator(".universe-map-group[open] .mermaid").first().evaluate(e => e.setAttribute("data-processed", "true"));
  assert.equal(await noJs.locator(".universe-map-group[open] .mermaid").first().evaluate(e => getComputedStyle(e).visibility), "hidden", "Mermaid's early processed flag must not expose source before SVG insertion");
  const noMermaid = await browser.newPage();
  await noMermaid.route("**/assets/vendor/mermaid/mermaid.esm.min.mjs", route => route.abort());
  await noMermaid.goto(`${base}/universe/`);
  const fallbackGroups = noMermaid.locator(".universe-map-group");
  for (let i = 0; i < report.diagrams.length; i++) {
    const group = fallbackGroups.nth(i);
    await group.locator("summary").click();
    const links = group.locator("a[data-universe-node]");
    assert.equal(await links.count(), report.diagrams[i].nodes.length, `Fallback links missing in group ${i + 1}`);
    for (let j = 0; j < report.diagrams[i].nodes.length; j++) {
      const expectedNode = report.nodes.find(node => node.id === report.diagrams[i].nodes[j]);
      assert.ok(expectedNode, `Manifest node missing for group ${i + 1}, link ${j + 1}`);
      const href = await links.nth(j).getAttribute("href");
      const destination = new URL(href, noMermaid.url());
      const expectedPath = new URL(expectedNode.url).pathname;
      assert.equal(destination.origin, new URL(base).origin, `Group ${i + 1} link ${j + 1} is not local`);
      assert.equal(destination.pathname, expectedPath, `Group ${i + 1} link ${j + 1} has the wrong path`);
      const response = await noMermaid.context().request.get(destination.href);
      assert.ok(response.ok() && new URL(response.url()).pathname === expectedPath,
        `Group ${i + 1} link ${j + 1}: HTTP ${response.status()} ${response.url()}`);
    }
  }
  const narrow = await browser.newPage({ viewport: { width: 320, height: 740 }, colorScheme: "dark" });
  await narrow.addInitScript(() => localStorage.setItem("askjamie-color-scheme", "dark"));
  await narrow.goto(`${base}/universe/`);
  const narrowGroups = narrow.locator(".universe-map-group");
  const narrowGroup = narrowGroups.first();
  await narrowGroup.locator("summary").click();
  await narrowGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
  await narrowGroup.locator("svg").evaluate(svg => { window.__universeQaNarrowSvg = svg; });
  const narrowToggle = narrow.locator(".askjamie-main .glee-color-toggle");
  await narrowToggle.click();
  await narrowToggle.click();
  await narrow.waitForFunction(() => document.documentElement.getAttribute("data-color-scheme") === "light");
  assert.equal(await narrowGroup.evaluate(group => group.open), true);
  assert.equal(await narrowGroups.evaluateAll(groups => groups.filter(group => group.open).length), 1);
  assert.equal(await narrow.evaluate(() =>
    window.__universeQaNarrowSvg === document.querySelector(".universe-map-group svg")
  ), true, "Light-mode switch must preserve the rendered SVG");
  const narrowWrap = narrowGroup.locator(".mermaid-scroll-wrap");
  assert.ok(await narrowWrap.evaluate(element => element.scrollWidth > element.clientWidth));
  await narrowWrap.hover();
  await narrow.mouse.wheel(220, 0);
  await narrow.waitForFunction(element => element.scrollLeft > 0, await narrowWrap.elementHandle(), { timeout: 3000 });
  const usableLink = narrowGroup.locator("a[data-universe-node]").first();
  assert.ok(await usableLink.isVisible());
  await usableLink.focus();
  assert.ok(await usableLink.evaluate(element => element === document.activeElement));
  await assertSvgLabelContrast(narrowGroup, "light at 320px");
  const keyboard = await browser.newPage();
  await keyboard.goto(`${base}/universe/`);
  await keyboard.locator(".universe-map-group summary").first().focus();
  await keyboard.keyboard.press("Enter");
  assert.equal(await keyboard.locator(".universe-map-group[open]").count(), 1);
  console.log(`Universe browser checks passed: ${report.nodes.length} indexed pages, ${report.diagrams.length} diagrams, both themes; contrast observations=${JSON.stringify(contrastObservations)}; in-place theme switching, 320px light-mode panning, all fallback links, keyboard and no-JavaScript links.`);
} finally {
  await browser.close();
}

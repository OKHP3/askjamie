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
      const switchGroup = groups.nth(switchIndex);
      await switchGroup.locator("summary").click();
      await switchGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
      const layoutBeforeSwitch = await captureGroupLayout(page, switchIndex);
      const groupCount = await groups.count();
      const toggle = page.locator(".askjamie-main .glee-color-toggle");
      assert.equal(await toggle.getAttribute("data-state"), theme);
      const nextTheme = theme === "dark" ? "light" : "dark";
      const clicksToNextTheme = theme === "dark" ? 2 : 1;
      for (let click = 0; click < clicksToNextTheme; click += 1) {
        await toggle.click();
      }
      await page.waitForFunction(expected =>
        document.documentElement.getAttribute("data-color-scheme") === expected, nextTheme);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
      const layoutAfterSwitch = await captureGroupLayout(page, switchIndex);
      assertGroupLayoutStable(layoutBeforeSwitch, layoutAfterSwitch, switchIndex, groupCount, nextTheme);

      const clicksToReturn = nextTheme === "dark" ? 2 : 1;
      for (let click = 0; click < clicksToReturn; click += 1) {
        await toggle.click();
      }
      await page.waitForFunction(expected =>
        document.documentElement.getAttribute("data-color-scheme") === expected, theme);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      )));
      const layoutAfterReturn = await captureGroupLayout(page, switchIndex);
      assertGroupLayoutStable(layoutBeforeSwitch, layoutAfterReturn, switchIndex, groupCount, theme);

      await switchGroup.locator("summary").focus();
      await page.keyboard.press("Enter");
      assert.equal((await captureGroupLayout(page, switchIndex)).open, false, "Enter must close the selected group after theme changes");
      await page.keyboard.press("Enter");
      const keyboardReopened = await captureGroupLayout(page, switchIndex);
      assert.equal(keyboardReopened.open, true, "Enter must reopen the selected group after theme changes");
      assert.equal(keyboardReopened.otherGroupsCollapsed, true, "Keyboard interaction must not open another group");
      assert.equal(keyboardReopened.svgReady, true, "Keyboard interaction must preserve the rendered SVG");

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
  const keyboard = await browser.newPage();
  await keyboard.goto(`${base}/universe/`);
  await keyboard.locator(".universe-map-group summary").first().focus();
  await keyboard.keyboard.press("Enter");
  assert.equal(await keyboard.locator(".universe-map-group[open]").count(), 1);
  console.log(`Universe browser checks passed: ${report.nodes.length} indexed pages, ${report.diagrams.length} diagrams, two widths, both themes, in-place theme switching, keyboard and no-JavaScript links.`);
} finally {
  await browser.close();
}

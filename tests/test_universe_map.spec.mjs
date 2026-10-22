import { chromium } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { isNotFoundDocument } from "../scripts/universe-not-found.mjs";

const base = process.env.BASE_URL || "http://127.0.0.1:5000";
const browser = await chromium.launch({ headless: true });
const soft404Fixtures = [
  {
    path: "/vercel/",
    title: "404: NOT_FOUND",
    heading: "Deployment unavailable",
    expected: true,
  },
  {
    path: "/generic/",
    title: "Page Not Found | Example Hosting",
    heading: "We couldn't find that page",
    expected: true,
  },
  {
    path: "/minimal/",
    title: "Example Hosting",
    heading: "The requested page does not exist",
    expected: true,
  },
  {
    path: "/not-found-heading/",
    title: "Example Hosting",
    heading: "The page could not be found",
    expected: true,
  },
  {
    path: "/unavailable/",
    title: "Example Hosting",
    heading: "This page is not available",
    expected: true,
  },
  {
    path: "/body-only-could-not-find/",
    title: "Example Hosting",
    heading: "Deployment unavailable",
    bodyMessage: "The page you are looking for could not be found.",
    expected: true,
  },
  {
    path: "/body-only-request-missing/",
    title: "Example Hosting",
    heading: "Welcome",
    bodyMessage: "The requested page does not exist.",
    expected: true,
  },
  {
    path: "/branded-title-first/",
    title: "Example Hosting | 404 page not found",
    heading: "Deployment unavailable",
    expected: true,
  },
  {
    path: "/branded-heading-first/",
    title: "Example Hosting",
    heading: "Example Hosting — Page not found",
    expected: true,
  },
  {
    path: "/valid/",
    title: "Why a 404 Not Found message appears in search results",
    heading: "How to interpret a page not found result",
    expected: false,
  },
  {
    path: "/valid-branded-title/",
    title: "Example Hosting | Why a 404 Not Found message appears in search results",
    heading: "Understanding search results",
    expected: false,
  },
  {
    path: "/valid-branded-404-reference/",
    title: "Example Hosting | 404 error codes explained",
    heading: "Understanding search results",
    expected: false,
  },
  {
    path: "/valid-body-reference/",
    title: "Example Hosting",
    heading: "Understanding error pages",
    bodyMessage: "This article explains why a requested page may not exist.",
    expected: false,
  },
  {
    path: "/valid-body-404-reference/",
    title: "Example Hosting",
    heading: "Understanding error codes",
    bodyMessage: "404 error codes explained with examples.",
    expected: false,
  },
  {
    path: "/body-only-branded-404/",
    title: "Example Hosting",
    heading: "Deployment unavailable",
    bodyMessage: "404: NOT_FOUND",
    expected: true,
  },
  {
    path: "/hidden-body-error/",
    title: "Example Hosting",
    heading: "Welcome",
    bodyMessage: "The requested page does not exist.",
    hideBodyMessage: true,
    expected: false,
  },
];

const soft404Page = await browser.newPage();
await soft404Page.route("https://soft-404-fixture.test/**", async route => {
  const fixture = soft404Fixtures.find(
    candidate => candidate.path === new URL(route.request().url()).pathname
  );
  if (!fixture) {
    await route.fulfill({ status: 404, body: "Unknown controlled fixture" });
    return;
  }
  await route.fulfill({
    status: 200,
    contentType: "text/html; charset=utf-8",
    body: `<!doctype html><html><head><title>${fixture.title}</title></head>` +
      `<body><main><h1>${fixture.heading}</h1>` +
      `${fixture.bodyMessage
        ? `<p${fixture.hideBodyMessage ? ' hidden' : ''}>${fixture.bodyMessage}</p>`
        : ""}</main></body></html>`,
  });
});
for (const fixture of soft404Fixtures) {
  const response = await soft404Page.goto(`https://soft-404-fixture.test${fixture.path}`);
  assert.equal(response?.status(), 200, `${fixture.path} must be served as HTTP 200`);
  const document = await soft404Page.evaluate(() => ({
    title: document.title,
    heading: document.querySelector("main h1, h1")?.textContent.trim() ?? "",
    bodyMessages: [...document.querySelectorAll(
      "main p, main [role='alert'], main [aria-live]"
    )].filter(element =>
      element.getClientRects().length > 0 &&
      getComputedStyle(element).visibility !== "hidden" &&
      getComputedStyle(element).display !== "none"
    ).map(element => element.textContent.trim()),
  }));
  assert.equal(
    isNotFoundDocument(document.title, document.heading, document.bodyMessages),
    fixture.expected,
    `${fixture.path}: title, primary heading, and body messages must classify correctly`
  );
}
await soft404Page.close();
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
    scrollLeft: diagram?.closest(".mermaid-scroll-wrap")?.scrollLeft ?? null,
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
  assert.ok(before.scrollLeft !== null && after.scrollLeft !== null,
    "The page map must expose its horizontal scroll position");
  assert.ok(Math.abs(after.scrollLeft - before.scrollLeft) <= 1,
    `Theme changes must retain horizontal scroll position: ${before.scrollLeft} -> ${after.scrollLeft}`);
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

const swipeDiagramHorizontally = async (page, scrollWrap) => {
  const box = await scrollWrap.boundingBox();
  assert.ok(box, "Rendered diagram must have a touchable viewport");
  const session = await page.context().newCDPSession(page);
  const y = box.y + box.height / 2;
  const startX = box.x + box.width * 0.8;
  const endX = box.x + box.width * 0.2;
  try {
    await session.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ id: 1, x: startX, y }],
    });
    for (const progress of [0.25, 0.5, 0.75, 1]) {
      await new Promise(resolve => setTimeout(resolve, 30));
      await session.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ id: 1, x: startX + (endX - startX) * progress, y }],
      });
    }
    await session.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
  } finally {
    await session.detach();
  }
};

const swipeDiagramVertically = async (page, diagram, direction = "up") => {
  const box = await diagram.boundingBox();
  assert.ok(box, "Rendered diagram must have a touchable viewport");
  const viewportHeight = await page.evaluate(() => window.innerHeight);
  const visibleTop = Math.max(0, box.y);
  const visibleBottom = Math.min(viewportHeight, box.y + box.height);
  const visibleHeight = visibleBottom - visibleTop;
  assert.ok(visibleHeight >= 80, "Rendered diagram must have a visible vertical swipe area");
  const startX = box.x + box.width / 2;
  const startY = visibleTop + visibleHeight * (direction === "down" ? 0.25 : 0.75);
  const endY = visibleTop + visibleHeight * (direction === "down" ? 0.75 : 0.25);
  const session = await page.context().newCDPSession(page);
  try {
    await session.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ id: 1, x: startX, y: startY }],
    });
    for (const progress of [0.25, 0.5, 0.75, 1]) {
      await new Promise(resolve => setTimeout(resolve, 30));
      await session.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{
          id: 1,
          x: startX,
          y: startY + (endY - startY) * progress,
        }],
      });
    }
    await session.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
  } finally {
    await session.detach();
  }
};

const addEdgeLabelReadabilityFixture = async group => group.locator("svg").evaluate(svg => {
  const namespace = "http://www.w3.org/2000/svg";
  const label = document.createElementNS(namespace, "g");
  label.classList.add("edgeLabel");
  const surface = document.createElementNS(namespace, "rect");
  surface.setAttribute("width", "220");
  surface.setAttribute("height", "28");
  surface.style.fill = "var(--color-surface)";
  const text = document.createElementNS(namespace, "text");
  text.textContent = "Theme readability edge-label fixture";
  text.setAttribute("x", "8");
  text.setAttribute("y", "20");
  text.style.fill = "var(--color-fg)";
  label.append(surface, text);
  svg.append(label);
  return true;
});

const addConnectorReadabilityFixtures = async group => group.locator("svg").evaluate(svg => {
  const fixtureDefinitions = [
    { name: "dotted", dasharray: "1 3" },
    { name: "dashed", dasharray: "7 4" },
    { name: "opaque", dasharray: "none" },
    { name: "translucent", dasharray: "none" },
    { name: "paint-translucent", dasharray: "none" },
    { name: "reverse", dasharray: "none", markerDirections: ["start"] },
    { name: "two-way", dasharray: "none", markerDirections: ["start", "end"] },
  ];
  const sourceConnector = svg.querySelector(
    "path.flowchart-link[marker-end], path.flowchart-link[marker-start]"
  );
  const sourceDirection = ["marker-end", "marker-start"]
    .find(direction => sourceConnector?.hasAttribute(direction));
  const sourceMarkerId = sourceConnector?.getAttribute(sourceDirection)
    ?.match(/url\(["']?#([^"')]+)["']?\)/)?.[1];
  const sourceMarker = sourceMarkerId
    ? [...svg.querySelectorAll("marker")].find(marker => marker.id === sourceMarkerId)
    : null;
  if (!sourceConnector || !sourceMarker) {
    throw new Error(
      "Controlled connector fixtures require a rendered flow path and marker"
    );
  }

  return fixtureDefinitions.map(({ name, dasharray, markerDirections }) => {
    const directions = markerDirections ?? ["end"];
    let connector = svg.querySelector(
      `path.flowchart-link[data-test-connector-fixture="${name}"]`
    );
    if (!connector) {
      connector = sourceConnector.cloneNode(true);
      connector.dataset.testConnectorFixture = name;
      connector.removeAttribute("marker-start");
      connector.removeAttribute("marker-end");
      sourceConnector.parentElement.append(connector);
    }
    const markers = directions.map(direction => {
      let marker = [...svg.querySelectorAll("marker")]
        .find(candidate =>
          candidate.dataset.testConnectorFixture === name &&
          candidate.dataset.testConnectorFixtureDirection === direction
        );
      if (!marker) {
        marker = sourceMarker.cloneNode(true);
        marker.id = `${sourceMarker.id}-test-${name}-${direction}`;
        marker.dataset.testConnectorFixture = name;
        marker.dataset.testConnectorFixtureDirection = direction;
        sourceMarker.parentElement.append(marker);
      }
      connector.setAttribute(`marker-${direction}`, `url(#${marker.id})`);
      return marker;
    });
    if (name === "opaque" || name === "translucent" ||
        name === "paint-translucent") {
      const dark = document.documentElement.getAttribute("data-color-scheme") === "dark";
      const paint = dark ? "#fff" : "#000";
      connector.style.stroke = paint;
      connector.style.strokeOpacity = name === "paint-translucent" ? "0.25" : "1";
      connector.style.opacity = name === "translucent" ? "0.25" : "1";
      for (const marker of markers) {
        for (const path of marker.querySelectorAll("path")) {
          path.style.fill = paint;
          path.style.fillOpacity = name === "paint-translucent" ? "0.25" : "1";
          path.style.opacity = name === "translucent" ? "0.25" : "1";
        }
      }
    } else {
      connector.style.strokeDasharray = dasharray;
    }
    return {
      name,
      dasharray: getComputedStyle(connector).strokeDasharray,
      markerDirections: directions.map(direction => `marker-${direction}`),
    };
  });
});

const contrastObservations = [];
const assertSvgLabelContrast = async (group, theme) => {
  const connectorFixtures = await addConnectorReadabilityFixtures(group);
  const evidence = await group.evaluate(root => {
    const rgb = value => {
      const color = value?.trim().toLowerCase();
      const hex = color?.match(/^#([0-9a-f]{3,8})$/i)?.[1];
      if (hex) {
        const expanded = hex.length <= 4
          ? [...hex].map(channel => channel + channel).join("")
          : hex;
        if (![6, 8].includes(expanded.length)) return null;
        return [
          parseInt(expanded.slice(0, 2), 16),
          parseInt(expanded.slice(2, 4), 16),
          parseInt(expanded.slice(4, 6), 16),
          expanded.length === 8 ? parseInt(expanded.slice(6, 8), 16) / 255 : 1,
        ];
      }
      const match = color?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return values.length >= 3
        ? [values[0], values[1], values[2], values[3] ?? 1]
        : null;
    };
    const luminance = value => {
      const channels = rgb(value);
      if (!channels || channels[3] < 0.99) return null;
      const [r, g, b] = channels.slice(0, 3).map(channel => {
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
    const contrastAfterOpacity = (foreground, background, opacity) => {
      const paint = rgb(foreground);
      const paper = rgb(background);
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
          element: `${current.tagName.toLowerCase()}${current.id ? `#${current.id}` : ""}`,
          opacity,
        });
        if (current === stopAt || current === svg) break;
      }
      return { combined, values };
    };
    const themeScope = root.closest(".askjamie-main");
    const resolveSvgColor = (svg, property, value) => {
      const probe = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      probe.style[property] = value;
      svg.append(probe);
      const resolved = getComputedStyle(probe)[property];
      probe.remove();
      return resolved;
    };
    const svg = root.querySelector("svg");
    const forcedColors = matchMedia("(forced-colors: active)").matches;
    const expectedNodeFill = forcedColors
      ? resolveSvgColor(svg, "fill", "Canvas")
      : getComputedStyle(themeScope).getPropertyValue("--color-surface").trim();
    const paperBackground = forcedColors
      ? resolveSvgColor(svg, "fill", "Canvas")
      : getComputedStyle(themeScope).getPropertyValue("--color-bg").trim();
    const colorsEqual = (first, second) => {
      const a = rgb(first), b = rgb(second);
      return Boolean(a && b && a[3] >= 0.99 && b[3] >= 0.99 &&
        a.slice(0, 3).every((channel, index) => Math.abs(channel - b[index]) <= 1));
    };
    const nodes = [...root.querySelectorAll("svg .node")];
    const nodeLabels = nodes.map((node, index) => {
      const label = node.querySelector(".nodeLabel p, .nodeLabel");
      const surface = node.querySelector("rect");
      const foreground = label && getComputedStyle(label).color;
      const background = surface && getComputedStyle(surface).fill;
      return {
        element: `svg .node:nth-of-type(${index + 1}) .nodeLabel`,
        text: label?.textContent.trim() ?? "",
        foreground,
        background,
        ratio: ratio(foreground, background),
      };
    });
    const nodeFills = nodes.map((node, index) => {
      const surface = node.querySelector("rect");
      const fill = surface && getComputedStyle(surface).fill;
      const stroke = surface && getComputedStyle(surface).stroke;
      return {
        element: `svg .node:nth-of-type(${index + 1}) rect`,
        fill,
        stroke,
        expected: expectedNodeFill,
        matchesTheme: colorsEqual(fill, expectedNodeFill),
        opaque: Boolean(rgb(fill)?.[3] >= 0.99),
        strokeContrast: ratio(stroke, fill),
      };
    });
    const edgeLabels = [...root.querySelectorAll("svg .edgeLabel")]
      .flatMap((edgeGroup, index) => {
        const label = edgeGroup.querySelector("text, p, span");
        if (!label?.textContent.trim()) return [];
        const foreground = label.tagName.toLowerCase() === "text"
          ? getComputedStyle(label).fill
          : getComputedStyle(label).color;
        const surface = edgeGroup?.querySelector("rect");
        const explicitBackground = surface
          ? getComputedStyle(surface).fill
          : getComputedStyle(edgeGroup).backgroundColor;
        const background = luminance(explicitBackground) !== null
          ? explicitBackground
          : paperBackground;
        return [{
          element: `svg .edgeLabel:nth-of-type(${index + 1})`,
          text: label.textContent.trim(),
          foreground,
          background,
          ratio: ratio(foreground, background),
        }];
      });
    const connectorElements = [...root.querySelectorAll("svg path.flowchart-link")];
    const flowConnectors = connectorElements.map((connector, index) => {
      const style = getComputedStyle(connector);
      const foreground = style.stroke;
      const chain = opacityChain(connector);
      const strokeOpacity = Number(style.strokeOpacity || 1);
      const effectiveOpacity = chain.combined * strokeOpacity;
      return {
        element: `svg .edgePaths path.flowchart-link:nth-of-type(${index + 1})`,
        foreground,
        background: paperBackground,
        ratio: contrastAfterOpacity(foreground, paperBackground, effectiveOpacity),
        effectiveOpacity,
        opacityValues: {
          elementChain: chain.values,
          strokeOpacity: style.strokeOpacity,
        },
        dasharray: style.strokeDasharray,
        fixture: connector.dataset.testConnectorFixture ?? null,
      };
    });
    const markerReferences = connectorElements.flatMap((connector, connectorIndex) =>
      ["marker-start", "marker-end"].flatMap(direction => {
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
      const marker = [...root.querySelectorAll("svg marker")]
        .find(candidate => candidate.id === markerId);
      if (!marker) {
        return [{
          element: `svg marker#${markerId} (${direction})`,
          direction,
          connectorElement,
          foreground: null,
          background: paperBackground,
          ratio: null,
          fixture: connector.dataset.testConnectorFixture ?? null,
        }];
      }
      return [...marker.querySelectorAll("path")].flatMap((path, pathIndex) => {
        const style = getComputedStyle(path);
        const markerChain = opacityChain(path, marker);
        const connectorChain = opacityChain(connector);
        return ["fill", "stroke"].flatMap(property => {
          const foreground = style[property];
          if (foreground === "none" || !rgb(foreground)) return [];
          const paintOpacity = Number(style.getPropertyValue(`${property}-opacity`) || 1);
          const effectiveOpacity = connectorChain.combined *
            markerChain.combined * paintOpacity;
          return [{
            element: `svg marker#${markerId} path:nth-of-type(${pathIndex + 1}) ` +
              `${property} (${direction})`,
            direction,
            connectorElement,
            foreground,
            background: paperBackground,
            effectiveOpacity,
            opacityValues: {
              connectorElementChain: connectorChain.values,
              markerElementChain: markerChain.values,
              paintOpacity: style.getPropertyValue(`${property}-opacity`),
            },
            ratio: contrastAfterOpacity(foreground, paperBackground, effectiveOpacity),
            fixture: marker.dataset.testConnectorFixture ?? null,
          }];
        });
      });
    });
    return { nodeLabels, nodeFills, edgeLabels, flowConnectors, arrowheads, markerIds };
  });
  assert.ok(evidence.nodeLabels.length, `${theme}: rendered SVG has no node labels`);
  for (const sample of evidence.nodeLabels) {
    assert.ok(sample.ratio !== null && sample.ratio >= 4.5,
      `${theme}: low-contrast SVG element ${sample.element}: ${JSON.stringify(sample)}`);
  }
  for (const surface of evidence.nodeFills) {
    assert.ok(surface.opaque && surface.matchesTheme,
      `${theme}: unreadable or non-adaptive SVG element ${surface.element}: ${JSON.stringify(surface)}`);
    if (theme === "forced-colors") {
      assert.ok(surface.strokeContrast !== null && surface.strokeContrast >= 3,
        `${theme}: indistinguishable node surface ${surface.element}: ${JSON.stringify(surface)}`);
    }
  }
  for (const sample of evidence.edgeLabels) {
    assert.ok(sample.ratio !== null && sample.ratio >= 4.5,
      `${theme}: low-contrast SVG element ${sample.element}: ${JSON.stringify(sample)}`);
  }
  assert.ok(evidence.flowConnectors.length,
    `${theme}: rendered SVG has no flow connector paths`);
  for (const fixtureName of ["dotted", "dashed"]) {
    const connector = evidence.flowConnectors.find(
      sample => sample.fixture === fixtureName
    );
    const expectedDasharray = connectorFixtures.find(
      fixture => fixture.name === fixtureName
    )?.dasharray;
    assert.ok(connector && connector.dasharray && connector.dasharray !== "none" &&
      connector.dasharray === expectedDasharray,
      `${theme}: ${fixtureName} SVG connector fixture was not measured as non-solid: ` +
        `${JSON.stringify({ connector, expectedDasharray, connectorFixtures })}`);
    assert.ok(evidence.arrowheads.some(sample => sample.fixture === fixtureName),
      `${theme}: ${fixtureName} marker direction=marker-end ` +
        `element=${connector?.element ?? "missing"} was not measured: ` +
        `${JSON.stringify(evidence.arrowheads)}`);
  }
  for (const [fixtureName, expectedDirections] of [
    ["reverse", ["marker-start"]],
    ["two-way", ["marker-start", "marker-end"]],
  ]) {
    const connector = evidence.flowConnectors.find(
      sample => sample.fixture === fixtureName
    );
    const fixture = connectorFixtures.find(sample => sample.name === fixtureName);
    assert.ok(connector && fixture,
      `${theme}: ${fixtureName} connector fixture is missing: ` +
        `${JSON.stringify({ connector, fixture })}`);
    assert.deepEqual(fixture.markerDirections, expectedDirections,
      `${theme}: ${fixtureName} element=${connector.element} fixture must reference ` +
        `directions=${JSON.stringify(expectedDirections)}`);
    for (const direction of expectedDirections) {
      assert.ok(evidence.arrowheads.some(sample =>
        sample.fixture === fixtureName && sample.direction === direction
      ), `${theme}: ${fixtureName} direction=${direction} ` +
        `element=${connector.element} marker was not measured: ` +
        `${JSON.stringify(evidence.arrowheads.filter(sample => sample.fixture === fixtureName))}`);
    }
  }
  for (const fixtureName of ["opaque", "translucent", "paint-translucent"]) {
    const connector = evidence.flowConnectors.find(sample => sample.fixture === fixtureName);
    const marker = evidence.arrowheads.find(sample => sample.fixture === fixtureName);
    assert.ok(connector && marker,
      `${theme}: ${fixtureName} opacity fixtures must be measured for the connector and arrowhead`);
    assert.ok(
      fixtureName === "opaque"
        ? connector.ratio !== null && connector.ratio >= 3
        : connector.ratio !== null && connector.ratio < 3,
      `${theme}: connector opacity fixture must produce the expected contrast; ` +
        `opacity_values=${JSON.stringify(connector.opacityValues)} ` +
        `contrast=${connector.ratio}; ${JSON.stringify(connector)}`
    );
    assert.ok(
      fixtureName === "opaque"
        ? marker.ratio !== null && marker.ratio >= 3
        : marker.ratio !== null && marker.ratio < 3,
      `${theme}: arrowhead opacity fixture must produce the expected contrast; ` +
        `direction=${marker.direction} element=${marker.element} ` +
        `opacity_values=${JSON.stringify(marker.opacityValues)} ` +
        `contrast=${marker.ratio}; ${JSON.stringify(marker)}`
    );
  }
  for (const sample of evidence.flowConnectors.filter(
    candidate => !["translucent", "paint-translucent"].includes(candidate.fixture)
  )) {
    assert.ok(sample.ratio !== null && sample.ratio >= 3,
      `${theme}: low-contrast SVG element ${sample.element} ` +
        `(dasharray=${sample.dasharray}, opacity_values=${JSON.stringify(sample.opacityValues)}): ` +
        `${JSON.stringify(sample)}`);
  }
  assert.ok(evidence.markerIds.length && evidence.arrowheads.length,
    `${theme}: direction=marker-start|marker-end element=svg marker reference ` +
      `has no measurable arrowheads`);
  for (const sample of evidence.arrowheads.filter(
    candidate => !["translucent", "paint-translucent"].includes(candidate.fixture)
  )) {
    assert.ok(sample.ratio !== null && sample.ratio >= 3,
      `${theme}: low-contrast SVG element ${sample.element} ` +
        `direction=${sample.direction} connector=${sample.connectorElement} ` +
        `opacity_values=${JSON.stringify(sample.opacityValues)}: ${JSON.stringify(sample)}`);
  }
  contrastObservations.push({
    theme,
    nodeLabels: evidence.nodeLabels.length,
    nodeFills: evidence.nodeFills.length,
    edgeLabels: evidence.edgeLabels.length,
    flowConnectors: evidence.flowConnectors.length,
    arrowheads: evidence.arrowheads.length,
    minimumRatio: Math.min(...[
      ...evidence.nodeLabels,
      ...evidence.edgeLabels,
      ...evidence.flowConnectors,
      ...evidence.arrowheads,
    ]
      .map(sample => sample.ratio)),
  });
  return evidence;
};

const assertForcedColorsPageMapControls = async (group, mapIndex) => {
  const summary = group.locator(":scope > summary");
  const firstLink = group.locator("a[data-universe-node]").first();
  const page = group.page();
  const settleMediaChange = () => page.evaluate(() =>
    new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    ))
  );
  const captureFocusedControl = (selector) => group.evaluate((element, targetSelector) => {
    const target = element.querySelector(targetSelector);
    const groupStyle = getComputedStyle(element);
    const targetStyle = getComputedStyle(target);
    const rgba = value => {
      const match = value?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return values.length >= 3 ? [values[0], values[1], values[2], values[3] ?? 1] : null;
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
        : `rgb(${foregroundChannels.slice(0, 3).map((channel, index) =>
            Math.round(channel * foregroundChannels[3] +
              backgroundChannels[index] * (1 - foregroundChannels[3]))
          ).join(", ")})`;
      const first = luminance(renderedForeground);
      const second = luminance(background);
      return first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const systemColor = name => {
      const probe = document.createElement("span");
      probe.style.color = name;
      element.append(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    };
    const canvas = systemColor("Canvas");
    const outlineColor = targetStyle.outlineColor;
    const focusSelector = targetSelector === ":scope > summary"
      ? ".universe-map-group > summary:focus-visible"
      : ".universe-map-group a[data-universe-node]:focus-visible";
    return {
      selector: focusSelector,
      focused: target === document.activeElement,
      focusVisible: target.matches(":focus-visible"),
      visible: target.getClientRects().length > 0 &&
        targetStyle.visibility === "visible" && targetStyle.display !== "none",
      textColor: targetStyle.color,
      textContrast: contrast(targetStyle.color, canvas),
      canvas,
      boundary: {
        selector: ".universe-map-group",
        color: groupStyle.borderTopColor,
        width: groupStyle.borderTopWidth,
        style: groupStyle.borderTopStyle,
        contrast: contrast(groupStyle.borderTopColor, canvas),
      },
      focus: {
        color: outlineColor,
        width: targetStyle.outlineWidth,
        style: targetStyle.outlineStyle,
        contrast: contrast(outlineColor, canvas),
      },
    };
  }, selector);
  const assertVisibleTransition = (controlName, before, after) => {
    assert.ok(before.focused && before.focusVisible &&
      after.focused && after.focusVisible && after.visible &&
      after.textContrast !== null && after.textContrast >= 4.5 &&
      parseFloat(after.boundary.width) >= 1 &&
      after.boundary.style !== "none" &&
      after.boundary.contrast !== null && after.boundary.contrast >= 3 &&
      parseFloat(after.focus.width) >= 2 &&
      after.focus.style !== "none" &&
      after.focus.contrast !== null && after.focus.contrast >= 3,
    `forced-colors transition map ${mapIndex + 1}: ${controlName} lost distinguishable ` +
      `text, boundary, or focus after emulation changed; ` +
      JSON.stringify({ before, after }));
  };

  // Exercise a media-mode change while each control already has keyboard focus.
  await page.emulateMedia({ forcedColors: "none" });
  await summary.focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  const summaryBefore = await captureFocusedControl(":scope > summary");
  await page.emulateMedia({ forcedColors: "active" });
  await settleMediaChange();
  const summaryAfter = await captureFocusedControl(":scope > summary");
  assertVisibleTransition("disclosure summary", summaryBefore, summaryAfter);

  await page.emulateMedia({ forcedColors: "none" });
  await settleMediaChange();
  await summary.focus();
  let linkFocusedBefore = false;
  for (let tab = 0; tab < 100 && !linkFocusedBefore; tab++) {
    await page.keyboard.press("Tab");
    linkFocusedBefore = await firstLink.evaluate(element =>
      element === document.activeElement && element.matches(":focus-visible")
    );
  }
  const linkBefore = await captureFocusedControl("a[data-universe-node]");
  await page.emulateMedia({ forcedColors: "active" });
  await settleMediaChange();
  const linkAfter = await captureFocusedControl("a[data-universe-node]");
  assertVisibleTransition("fallback link", linkBefore, linkAfter);

  await summary.focus();
  let linkFocused = false;
  for (let tab = 0; tab < 100 && !linkFocused; tab++) {
    await page.keyboard.press("Tab");
    linkFocused = await firstLink.evaluate(element =>
      element === document.activeElement && element.matches(":focus-visible")
    );
  }
  const linksEvidence = await group.evaluate(element => {
    const rgba = value => {
      const match = value?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const values = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return values.length >= 3 ? [values[0], values[1], values[2], values[3] ?? 1] : null;
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
      if (!foregroundChannels || !backgroundChannels ||
          backgroundChannels[3] < 0.99) return null;
      const renderedForeground = foregroundChannels[3] >= 0.99
        ? foreground
        : `rgb(${foregroundChannels.slice(0, 3).map((channel, index) =>
            Math.round(channel * foregroundChannels[3] +
              backgroundChannels[index] * (1 - foregroundChannels[3]))
          ).join(", ")})`;
      const first = luminance(renderedForeground);
      const second = luminance(background);
      return first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const getSystemColor = name => {
      const probe = document.createElement("span");
      probe.style.color = name;
      element.append(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    };
    const canvas = getSystemColor("Canvas");
    const summaryElement = element.querySelector(":scope > summary");
    const summaryStyle = getComputedStyle(summaryElement);
    const summaryBox = summaryElement.getBoundingClientRect();
    const groupStyle = getComputedStyle(element);
    return {
      summary: {
        selector: ".universe-map-group > summary",
        visible: summaryBox.width > 0 && summaryBox.height > 0 &&
          summaryStyle.visibility === "visible" && summaryStyle.display !== "none",
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
        selector: ".universe-map-group",
        color: groupStyle.borderTopColor,
        width: groupStyle.borderTopWidth,
        style: groupStyle.borderTopStyle,
        background: canvas,
        contrast: contrast(groupStyle.borderTopColor, canvas),
      },
      links: [...element.querySelectorAll("a[data-universe-node]")].map((link, index) => {
        const style = getComputedStyle(link);
        const box = link.getBoundingClientRect();
        return {
          selector: `.universe-map-group a[data-universe-node]:nth-of-type(${index + 1})`,
          text: link.textContent.trim(),
          visible: box.width > 0 && box.height > 0 &&
            style.visibility === "visible" && style.display !== "none",
          color: style.color,
          background: canvas,
          contrast: contrast(style.color, canvas),
          underline: style.textDecorationLine.includes("underline"),
          focused: link === document.activeElement && link.matches(":focus-visible"),
          focus: {
            color: style.outlineColor,
            width: style.outlineWidth,
            style: style.outlineStyle,
            contrast: contrast(style.outlineColor, canvas),
          },
        };
      }),
    };
  });
  assert.ok(linksEvidence.summary.visible &&
    linksEvidence.summary.contrast !== null && linksEvidence.summary.contrast >= 4.5,
  `forced-colors map ${mapIndex + 1}: disclosure summary text is not readable: ` +
    JSON.stringify(linksEvidence.summary));
  assert.ok(parseFloat(linksEvidence.boundary.width) >= 1 &&
    linksEvidence.boundary.style !== "none" &&
    linksEvidence.boundary.contrast !== null &&
    linksEvidence.boundary.contrast >= 3,
  `forced-colors map ${mapIndex + 1}: disclosure boundary is not distinguishable: ` +
    JSON.stringify(linksEvidence.boundary));
  assert.ok(linksEvidence.links.length > 0,
    `forced-colors map ${mapIndex + 1}: accessible fallback links are missing`);
  for (const link of linksEvidence.links) {
    assert.ok(link.visible && link.contrast !== null &&
      link.contrast >= 4.5 && link.underline,
    `forced-colors map ${mapIndex + 1}: fallback link is not readable/distinguishable: ` +
      JSON.stringify(link));
  }
  assert.ok(linkFocused && linksEvidence.links[0]?.focused &&
    linksEvidence.links[0].focus.style !== "none" &&
    parseFloat(linksEvidence.links[0].focus.width) >= 2 &&
    linksEvidence.links[0].focus.contrast !== null &&
    linksEvidence.links[0].focus.contrast >= 3,
  `forced-colors map ${mapIndex + 1}: fallback-link keyboard focus is not visible: ` +
    JSON.stringify(linksEvidence.links[0]));

  let summaryReached = false;
  for (let tab = 0; tab < 100 && !summaryReached; tab++) {
    await group.page().keyboard.press("Shift+Tab");
    summaryReached = await summary.evaluate(element =>
      element === document.activeElement && element.matches(":focus-visible")
    );
  }
  const summaryFocus = await summary.evaluate(element => {
    const style = getComputedStyle(element);
    const probe = document.createElement("span");
    probe.style.color = "Canvas";
    element.parentElement.append(probe);
    const canvas = getComputedStyle(probe).color;
    probe.remove();
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
    const outline = channels(style.outlineColor);
    const canvasChannels = channels(canvas);
    const renderedOutline = outline && canvasChannels
      ? `rgb(${outline.slice(0, 3).map((channel, index) =>
          Math.round(channel * (outline[3] ?? 1) +
            canvasChannels[index] * (1 - (outline[3] ?? 1)))
        ).join(", ")})`
      : "";
    const first = luminance(renderedOutline);
    const second = luminance(canvas);
    return {
      selector: ".universe-map-group > summary:focus-visible",
      focused: element === document.activeElement && element.matches(":focus-visible"),
      color: style.outlineColor,
      width: style.outlineWidth,
      style: style.outlineStyle,
      contrast: first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
    };
  });
  assert.ok(summaryReached && summaryFocus.focused && summaryFocus.style !== "none" &&
    parseFloat(summaryFocus.width) >= 2 &&
    summaryFocus.contrast !== null && summaryFocus.contrast >= 3,
  `forced-colors map ${mapIndex + 1}: disclosure-summary keyboard focus is not visible: ` +
    JSON.stringify(summaryFocus));
  return linksEvidence;
};

try {
  const report = await (await fetch(`${base}/assets/data/universe-map.json`)).json();
  const fontPanPage = await browser.newPage({
    viewport: { width: 320, height: 900 },
    colorScheme: "dark",
  });
  let releaseHeldFontRequests;
  const heldFontRequests = new Promise(resolve => {
    releaseHeldFontRequests = resolve;
  });
  const fontRequestSeen = fontPanPage.waitForRequest(
    request => request.resourceType() === "font",
    { timeout: 15000 }
  );
  const fontResponses = [];
  fontPanPage.on("response", response => {
    if (response.request().resourceType() === "font") fontResponses.push(response);
  });
  let fontRequestsReleased = false;
  try {
    await fontPanPage.addInitScript(() =>
      localStorage.setItem("askjamie-color-scheme", "dark")
    );
    await fontPanPage.route("**/*", async route => {
      if (route.request().resourceType() === "font") {
        await heldFontRequests;
      }
      await route.continue();
    });
    await fontPanPage.goto(`${base}/universe/`, { waitUntil: "domcontentloaded" });
    await fontPanPage.waitForFunction(() =>
      [...document.querySelectorAll("link[data-deferred-styles]")].every(link =>
        link.media === "all" && Boolean(link.sheet?.cssRules.length)
      ),
    null, { timeout: 15000 });
    const fontPanIndex = Math.min(1, report.diagrams.length - 1);
    const fontPanGroups = fontPanPage.locator(".universe-map-group");
    const fontPanGroup = fontPanGroups.nth(fontPanIndex);
    await fontPanGroup.locator("summary").click();
    await fontPanGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
    await fontPanPage.waitForFunction(() => document.fonts.status === "loading", null, {
      timeout: 15000,
    });
    const heldFontRequest = await fontRequestSeen;
    assert.ok(heldFontRequest.url(), "A real web-font request must be held before panning");
    const fontPanScrollWrap = fontPanGroup.locator(".mermaid-scroll-wrap");
    const panBeforeFonts = await fontPanScrollWrap.evaluate(element => {
      const maxScrollLeft = element.scrollWidth - element.clientWidth;
      element.scrollLeft = maxScrollLeft > 1 ? Math.max(1, Math.round(maxScrollLeft / 2)) : 0;
      return {
        scrollLeft: element.scrollLeft,
        maxScrollLeft,
        fontsStatus: document.fonts.status,
      };
    });
    assert.ok(panBeforeFonts.maxScrollLeft > 1,
      "The selected page map must overflow horizontally before fonts load");
    assert.ok(panBeforeFonts.scrollLeft > 0,
      "The selected page map must be panned before fonts load");
    assert.equal(panBeforeFonts.fontsStatus, "loading",
      "The page map must be panned while web fonts are still loading");
    const layoutBeforeFonts = await captureGroupLayout(fontPanPage, fontPanIndex);
    assert.equal(layoutBeforeFonts.open, true, "The selected group must be open before fonts load");
    assert.equal(layoutBeforeFonts.otherGroupsCollapsed, true,
      "Other groups must remain collapsed before fonts load");

    releaseHeldFontRequests();
    fontRequestsReleased = true;
    await fontPanPage.evaluate(() => document.fonts.ready);
    await fontPanPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)
    )));
    assert.ok(fontResponses.some(response =>
      response.status() >= 200 && response.status() < 400
    ), `At least one held web font must load successfully: ${
      fontResponses.map(response => response.status()).join(", ") || "no responses"
    }`);
    const layoutAfterFonts = await captureGroupLayout(fontPanPage, fontPanIndex);
    assert.equal(layoutAfterFonts.open, true, "Font loading must leave the selected group open");
    assert.equal(layoutAfterFonts.otherGroupsCollapsed, true,
      "Font loading must leave other groups collapsed");
    assert.ok(Math.abs(layoutAfterFonts.scrollLeft - layoutBeforeFonts.scrollLeft) <= 1,
      `Font loading must retain horizontal position: ${layoutBeforeFonts.scrollLeft} -> ` +
        `${layoutAfterFonts.scrollLeft}`);
  } finally {
    if (!fontRequestsReleased) releaseHeldFontRequests();
    await fontPanPage.close();
  }

  for (const width of [390, 1280]) {
    for (const theme of ["light", "dark"]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: theme });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => { if (/render error/i.test(message.text())) errors.push(message.text()); });
      await page.addInitScript(value => localStorage.setItem("askjamie-color-scheme", value), theme);
      await page.goto(`${base}/universe/`);
      await page.waitForFunction(() =>
        [...document.querySelectorAll("link[data-deferred-styles]")].every(link =>
          link.media === "all" && Boolean(link.sheet?.cssRules.length)),
      null, { timeout: 15000 });
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
      await switchPage.waitForFunction(() =>
        [...document.querySelectorAll("link[data-deferred-styles]")].every(link =>
          link.media === "all" && Boolean(link.sheet?.cssRules.length)),
      null, { timeout: 15000 });
      const switchGroups = switchPage.locator(".universe-map-group");
      const switchGroup = switchGroups.nth(switchIndex);
      await switchGroup.locator("summary").click();
      await switchGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
      const switchScrollWrap = switchGroup.locator(".mermaid-scroll-wrap");
      if (width === 1280) {
        await switchPage.evaluate(() => { document.documentElement.style.zoom = "2"; });
        await switchPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
          requestAnimationFrame(resolve)
        )));
      }
      const panDimensions = await switchScrollWrap.evaluate(element => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }));
      assert.ok(panDimensions.scrollWidth > panDimensions.clientWidth,
        `The ${width}px page map must overflow horizontally before checking pan retention`);
      await switchScrollWrap.evaluate(element => { element.scrollLeft = 0; });
      await switchScrollWrap.hover();
      await switchPage.mouse.wheel(200, 0);
      await switchPage.waitForFunction(
        element => element.scrollLeft > 0,
        await switchScrollWrap.elementHandle()
      );
      const layoutBeforeSwitch = await captureGroupLayout(switchPage, switchIndex);
      assert.ok(layoutBeforeSwitch.scrollLeft > 0,
        `The selected page map must be panned at ${width}px before checking theme retention`);
      const groupCount = await switchGroups.count();
      const originalSvg = await switchGroup.locator("svg").evaluate(svg => {
        window.__universeQaOriginalSvg = svg;
        return true;
      });
      await addEdgeLabelReadabilityFixture(switchGroup);
      const beforeReadability = await assertSvgLabelContrast(switchGroup, theme);
      assert.ok(beforeReadability.edgeLabels.some(sample =>
        sample.text === "Theme readability edge-label fixture"),
      "The rendered-SVG contrast check must exercise an edge label");
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
      assert.ok(afterReadability.edgeLabels.some(sample =>
        sample.text === "Theme readability edge-label fixture"),
      `${nextTheme}: edge labels must still be checked after switching themes`);
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
      assert.ok(returnedReadability.edgeLabels.some(sample =>
        sample.text === "Theme readability edge-label fixture"),
      `${theme}: edge labels must still be checked after switching back`);
      assert.equal(beforeReadability.nodeLabels.length, returnedReadability.nodeLabels.length);
      assert.equal(afterReadability.nodeLabels.length, returnedReadability.nodeLabels.length);
      assert.deepEqual(
        returnedReadability.nodeFills.map(surface => surface.fill),
        beforeReadability.nodeFills.map(surface => surface.fill),
        `${theme}: node fills must return to their original colors after switching back`
      );
      assert.notDeepEqual(
        afterReadability.nodeFills.map(surface => surface.fill),
        beforeReadability.nodeFills.map(surface => surface.fill),
        `${nextTheme}: node fills must adapt after switching from ${theme}`
      );
      assert.ok(originalSvg, "Rendered SVG must exist before theme switches");
      assert.equal(await switchPage.evaluate(() =>
        window.__universeQaOriginalSvg === document.querySelector(
          ".universe-map-generated .universe-map-group:nth-of-type(2) svg"
        )
      ), true, "Switching back must preserve the original rendered SVG");

      await switchGroup.locator("summary").focus();
      const layoutBeforeClose = await captureGroupLayout(switchPage, switchIndex);
      assert.ok(layoutBeforeClose.scrollLeft > 0,
        `The selected page map must remain panned at ${width}px before closing`);
      await switchPage.keyboard.press("Enter");
      const layoutClosed = await captureGroupLayout(switchPage, switchIndex);
      assert.equal(layoutClosed.open, false, "Enter must close the selected group after theme changes");
      assert.equal(await switchGroups.evaluateAll(groups => groups.every(group => !group.open)),
        true, "Closing the selected group must leave every group collapsed");
      await switchPage.keyboard.press("Enter");
      const keyboardReopened = await captureGroupLayout(switchPage, switchIndex);
      assert.equal(keyboardReopened.open, true, "Enter must reopen the selected group after theme changes");
      assert.equal(keyboardReopened.otherGroupsCollapsed, true, "Keyboard interaction must not open another group");
      assert.equal(keyboardReopened.svgReady, true, "Keyboard interaction must preserve the rendered SVG");
      assert.ok(Math.abs(keyboardReopened.scrollLeft - layoutBeforeClose.scrollLeft) <= 1,
        `Closing and reopening the ${width}px page map must retain its horizontal position: ` +
          `${layoutBeforeClose.scrollLeft} -> ${keyboardReopened.scrollLeft}`);
      assert.equal(await switchPage.evaluate(() =>
        window.__universeQaOriginalSvg === document.querySelector(
          ".universe-map-generated .universe-map-group:nth-of-type(2) svg"
        )
      ), true, "Closing and reopening the selected group must preserve its rendered SVG");
      if (width === 1280) {
        await switchPage.evaluate(() => { document.documentElement.style.zoom = "1"; });
      }

      assert.ok(await switchGroups.count() >= 2,
        "Switching-map retention requires at least two page-map groups");
      if (width === 1280) {
        await switchPage.evaluate(() => { document.documentElement.style.zoom = "2"; });
        await switchPage.evaluate(() => new Promise(resolve => requestAnimationFrame(() =>
          requestAnimationFrame(resolve)
        )));
      }
      const firstGroup = switchGroups.nth(0);
      const secondGroup = switchGroups.nth(1);
      const panGroup = async (group, groupIndex, fraction, label) => {
        for (let index = 0; index < await switchGroups.count(); index += 1) {
          const otherGroup = switchGroups.nth(index);
          if (index !== groupIndex &&
              await otherGroup.evaluate(element => element.open)) {
            await otherGroup.locator("summary").click();
          }
        }
        if (!(await group.evaluate(element => element.open))) {
          await group.locator("summary").click();
        }
        await group.locator("svg .node").first().waitFor({ timeout: 20000 });
        const wrap = group.locator(".mermaid-scroll-wrap");
        const dimensions = await wrap.evaluate(element => ({
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        }));
        assert.ok(dimensions.scrollWidth > dimensions.clientWidth,
          `${label} must overflow horizontally before group-switch retention is checked`);
        const position = await wrap.evaluate((element, ratio) => {
          const maxScrollLeft = element.scrollWidth - element.clientWidth;
          element.scrollLeft = maxScrollLeft * ratio;
          return {
            maxScrollLeft,
            scrollLeft: element.scrollLeft,
          };
        }, fraction);
        assert.ok(position.scrollLeft > 0,
          `${label} must be panned away from its starting position`);
        assert.ok(position.scrollLeft < position.maxScrollLeft,
          `${label} must be panned to an intermediate position`);
        return position.scrollLeft;
      };
      const assertOnlyGroupOpen = async (selectedGroup, label) => {
        assert.equal(await selectedGroup.evaluate(element => element.open), true,
          `${label} must be the selected open group`);
        assert.equal(await switchGroups.evaluateAll(groups =>
          groups.filter(group => group.open).length), 1,
        `${label}: only the selected group may remain open`);
      };

      const firstPosition = await panGroup(firstGroup, 0, 0.25, "First group map");
      await assertOnlyGroupOpen(firstGroup, "First group");
      const secondPosition = await panGroup(secondGroup, 1, 0.75, "Second group map");
      await assertOnlyGroupOpen(secondGroup, "Second group");
      assert.ok(Math.abs(firstPosition - secondPosition) > 1,
        `The two maps must be panned to distinct positions: ${firstPosition} vs ${secondPosition}`);

      await secondGroup.locator("summary").click();
      assert.equal(await switchGroups.evaluateAll(groups => groups.every(group => !group.open)),
        true, "Closing the second group must leave all page-map groups closed");
      await firstGroup.locator("summary").click();
      await assertOnlyGroupOpen(firstGroup, "First group after returning from the second");
      const firstReturnedPosition = await firstGroup.locator(".mermaid-scroll-wrap")
        .evaluate(element => element.scrollLeft);
      assert.ok(Math.abs(firstReturnedPosition - firstPosition) <= 1,
        `Switching to the second group and back must retain the first map position: ` +
          `${firstPosition} -> ${firstReturnedPosition}`);

      await firstGroup.locator("summary").click();
      assert.equal(await switchGroups.evaluateAll(groups => groups.every(group => !group.open)),
        true, "Closing the first group must leave all page-map groups closed");
      await secondGroup.locator("summary").click();
      await assertOnlyGroupOpen(secondGroup, "Second group after returning from the first");
      const secondReturnedPosition = await secondGroup.locator(".mermaid-scroll-wrap")
        .evaluate(element => element.scrollLeft);
      assert.ok(Math.abs(secondReturnedPosition - secondPosition) <= 1,
        `Switching back to the second group must retain its own map position: ` +
          `${secondPosition} -> ${secondReturnedPosition}`);
      if (width === 1280) {
        await switchPage.evaluate(() => { document.documentElement.style.zoom = "1"; });
      }
      await switchPage.close();

      const urls = await page.locator("a[data-universe-node]").evaluateAll(links => [...new Set(links.map(link => link.href.replace(location.origin, "https://askjamie.bot")))].sort());
      assert.deepEqual(urls, report.nodes.filter(node => node.indexed).map(node => node.url).sort());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Page overflows viewport");
      assert.deepEqual(errors, []);
      await page.close();
    }
  }
  for (const width of [390, 1280]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: "light" });
    await page.emulateMedia({ forcedColors: "active" });
    await page.addInitScript(() => localStorage.setItem("askjamie-color-scheme", "light"));
    await page.goto(`${base}/universe/`);
    await page.waitForFunction(() =>
      [...document.querySelectorAll("link[data-deferred-styles]")].every(link =>
        link.media === "all" && Boolean(link.sheet?.cssRules.length)),
    null, { timeout: 15000 });
    assert.equal(await page.evaluate(() => matchMedia("(forced-colors: active)").matches), true,
      "Browser must emulate forced-colors mode");
    const groups = page.locator(".universe-map-group");
    assert.ok(await groups.count(), "Generated page maps must be present in forced-colors mode");
    for (let i = 0; i < await groups.count(); i++) {
      const group = groups.nth(i);
      await group.locator("summary").click();
      await group.locator(".mermaid").scrollIntoViewIfNeeded();
      await group.locator("svg .node").first().waitFor({ timeout: 20000 });
      await addEdgeLabelReadabilityFixture(group);
      const evidence = await assertSvgLabelContrast(group, "forced-colors");
      assert.ok(evidence.edgeLabels.some(sample =>
        sample.text === "Theme readability edge-label fixture"),
      `forced-colors: map ${i + 1} must measure the representative SVG edge-label fixture`);
      await assertForcedColorsPageMapControls(group, i);
      await group.locator("summary").click();
    }
    await page.close();
  }
  const themeFailurePage = await browser.newPage({
    viewport: { width: 390, height: 844 },
    colorScheme: "dark",
  });
  await themeFailurePage.addInitScript(() =>
    localStorage.setItem("askjamie-color-scheme", "dark")
  );
  await themeFailurePage.route("**/*", async route => {
    if (new URL(route.request().url()).pathname === "/assets/css/theme.css") {
      await route.fulfill({
        status: 503,
        contentType: "text/css",
        body: "/* controlled theme stylesheet failure */",
      });
      return;
    }
    await route.continue();
  });
  const localThemeFailure = themeFailurePage.waitForResponse(response =>
    new URL(response.url()).pathname === "/assets/css/theme.css" &&
    response.status() === 503,
  {
    timeout: 15000,
  });
  await themeFailurePage.goto(`${base}/universe/`, { waitUntil: "domcontentloaded" });
  const failedThemeRequest = await localThemeFailure;
  assert.equal(new URL(failedThemeRequest.url()).pathname, "/assets/css/theme.css");
  assert.equal(failedThemeRequest.status(), 503,
    "Theme stylesheet failure fixture must return an HTTP 5xx response");
  await themeFailurePage.waitForFunction(() =>
    [...document.querySelectorAll("link[data-deferred-styles]")].some(link =>
      new URL(link.href).pathname === "/assets/css/theme.css" && link.media === "all"),
  null, { timeout: 10000 });
  const themeFailureNotice = themeFailurePage.locator("#universe-theme-status");
  await themeFailureNotice.waitFor({ state: "visible", timeout: 5000 });
  const themeFailureNoticeState = await themeFailureNotice.evaluate(element => ({
    text: element.textContent.trim(),
    role: element.getAttribute("role"),
    live: element.getAttribute("aria-live"),
  }));
  assert.match(themeFailureNoticeState.text, /visual theme could not load/i);
  assert.equal(themeFailureNoticeState.role, "status");
  assert.equal(themeFailureNoticeState.live, "polite");
  const themeLinkState = await themeFailurePage.evaluate(() => {
    const link = [...document.querySelectorAll("link[data-deferred-styles]")]
      .find(candidate => new URL(candidate.href).pathname === "/assets/css/theme.css");
    let ready = false;
    try {
      ready = Boolean(link?.sheet?.cssRules.length);
    } catch {
      ready = false;
    }
    return {
      activated: link?.media === "all",
      ready,
    };
  });
  assert.equal(themeLinkState.activated, true, "Failed local theme stylesheet must have been activated");
  assert.equal(themeLinkState.ready, false, "HTTP 5xx theme stylesheet must not report ready");
  const failedThemeGroups = themeFailurePage.locator(".universe-map-group");
  assert.equal(await failedThemeGroups.count(), report.diagrams.length);
  assert.equal(await failedThemeGroups.evaluateAll(groups => groups.every(group => !group.open)), true,
    "Groups must start collapsed when the local theme stylesheet returns HTTP 5xx");
  const firstFailedThemeGroup = failedThemeGroups.first();
  const firstFailedThemeSummary = firstFailedThemeGroup.locator("summary");
  await firstFailedThemeSummary.focus();
  await themeFailurePage.keyboard.press("Enter");
  assert.equal(await firstFailedThemeGroup.evaluate(group => group.open), true,
    "Keyboard must open a page-map group when the local theme stylesheet returns HTTP 5xx");
  assert.equal(await failedThemeGroups.evaluateAll(groups =>
    groups.slice(1).every(group => !group.open)), true,
  "Opening a page-map group must leave the other groups collapsed after the HTTP 5xx stylesheet failure");
  const failedThemeLinks = firstFailedThemeGroup.locator("a[data-universe-node]");
  assert.equal(await failedThemeLinks.count(), report.diagrams[0].nodes.length);
  assert.equal(await failedThemeLinks.evaluateAll(links =>
    links.every(link =>
      link.getClientRects().length > 0 &&
      getComputedStyle(link).visibility !== "hidden" &&
      getComputedStyle(link).display !== "none" &&
      Boolean(link.getAttribute("href"))
    )), true, "Accessible page links must remain available after the HTTP 5xx stylesheet failure");
  await themeFailurePage.keyboard.press("Enter");
  assert.equal(await firstFailedThemeGroup.evaluate(group => group.open), false,
    "Keyboard must close a page-map group when the local theme stylesheet returns HTTP 5xx");
  assert.equal(await failedThemeGroups.evaluateAll(groups => groups.every(group => !group.open)), true,
    "Page-map groups must remain collapsed after failed-theme keyboard checks");
  const failedThemeEvidence = {
    theme_readiness: themeLinkState.ready ? "ready" : "unavailable",
    geometry_comparison_available: false,
  };
  assert.deepEqual(failedThemeEvidence, {
    theme_readiness: "unavailable",
    geometry_comparison_available: false,
  }, "HTTP 5xx theme layout must not be treated as a geometry baseline");
  await themeFailurePage.close();

  const noJs = await browser.newPage({ javaScriptEnabled: false });
  await noJs.emulateMedia({ forcedColors: "active" });
  assert.equal(await noJs.evaluate(() => matchMedia("(forced-colors: active)").matches), true,
    "No-JavaScript page must enter browser-emulated forced-colors mode before navigation");
  await noJs.goto(`${base}/universe/`);
  const noJsGroup = noJs.locator(".universe-map-group").first();
  const noJsSummary = noJsGroup.locator(":scope > summary");
  await noJsSummary.click();
  assert.equal(await noJsGroup.evaluate(group => group.open), true,
    "No-JavaScript page-map disclosure must open in forced-colors mode");
  const noJsLinks = noJsGroup.locator("a[data-universe-node]");
  assert.ok(await noJsLinks.count(), "No-JavaScript page-map destinations must be present");
  await noJsSummary.focus();
  let noJsFirstLinkFocus;
  for (let tab = 0; tab < 100; tab++) {
    await noJs.keyboard.press("Tab");
    noJsFirstLinkFocus = await noJsLinks.first().evaluate(link => ({
      focused: link === document.activeElement && link.matches(":focus-visible"),
      color: getComputedStyle(link).color,
      outlineColor: getComputedStyle(link).outlineColor,
      outlineWidth: getComputedStyle(link).outlineWidth,
      outlineStyle: getComputedStyle(link).outlineStyle,
    }));
    if (noJsFirstLinkFocus.focused) break;
  }
  const noJsForcedColorsEvidence = await noJsGroup.evaluate(group => {
    const rgba = value => {
      const match = value?.match(/^rgba?\((.+)\)$/);
      if (!match) return null;
      const channels = match[1].split(/[,/ ]+/).filter(Boolean).map(Number);
      return channels.length >= 3 ? [...channels.slice(0, 3), channels[3] ?? 1] : null;
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
      const fg = rgba(foreground);
      const bg = rgba(background);
      if (!fg || !bg || bg[3] < 0.99) return null;
      const rendered = fg[3] >= 0.99 ? foreground :
        `rgb(${fg.slice(0, 3).map((channel, index) =>
          Math.round(channel * fg[3] + bg[index] * (1 - fg[3]))
        ).join(", ")})`;
      const first = luminance(rendered);
      const second = luminance(background);
      return first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    const probe = document.createElement("span");
    probe.style.color = "Canvas";
    group.append(probe);
    const canvas = getComputedStyle(probe).color;
    probe.remove();
    const summary = group.querySelector(":scope > summary");
    const summaryStyle = getComputedStyle(summary);
    const summaryBox = summary.getBoundingClientRect();
    const groupStyle = getComputedStyle(group);
    return {
      summary: {
        selector: ".universe-map-group > summary",
        visible: summaryBox.width > 0 && summaryBox.height > 0 &&
          summaryStyle.visibility === "visible" && summaryStyle.display !== "none",
        color: summaryStyle.color,
        background: canvas,
        contrast: contrast(summaryStyle.color, canvas),
      },
      boundary: {
        selector: ".universe-map-group",
        color: groupStyle.borderTopColor,
        width: groupStyle.borderTopWidth,
        style: groupStyle.borderTopStyle,
        background: canvas,
        contrast: contrast(groupStyle.borderTopColor, canvas),
      },
      links: [...group.querySelectorAll("a[data-universe-node]")].map((link, index) => {
        const style = getComputedStyle(link);
        const box = link.getBoundingClientRect();
        return {
          selector: `.universe-map-group a[data-universe-node]:nth-of-type(${index + 1})`,
          text: link.textContent.trim(),
          visible: box.width > 0 && box.height > 0 &&
            style.visibility === "visible" && style.display !== "none",
          color: style.color,
          background: canvas,
          contrast: contrast(style.color, canvas),
          underline: style.textDecorationLine.includes("underline"),
        };
      }),
    };
  });
  const noJsFocusRingContrast = await noJsLinks.first().evaluate(link => {
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
    const style = getComputedStyle(link);
    const probe = document.createElement("span");
    probe.style.color = "Canvas";
    link.parentElement.append(probe);
    const canvas = getComputedStyle(probe).color;
    probe.remove();
    const outline = channels(style.outlineColor);
    const background = channels(canvas);
    const rendered = outline && background
      ? `rgb(${outline.slice(0, 3).map((channel, index) =>
          Math.round(channel * (outline[3] ?? 1) +
            background[index] * (1 - (outline[3] ?? 1)))
        ).join(", ")})`
      : "";
    const first = luminance(rendered);
    const second = luminance(canvas);
    return {
      selector: ".universe-map-group a[data-universe-node]:focus-visible",
      focused: link === document.activeElement && link.matches(":focus-visible"),
      color: style.outlineColor,
      width: style.outlineWidth,
      style: style.outlineStyle,
      background: canvas,
      contrast: first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
    };
  });
  assert.ok(noJsForcedColorsEvidence.summary.visible &&
    noJsForcedColorsEvidence.summary.contrast !== null &&
    noJsForcedColorsEvidence.summary.contrast >= 4.5,
  `no-JavaScript forced-colors: disclosure summary is not readable: ` +
    JSON.stringify(noJsForcedColorsEvidence.summary));
  assert.ok(parseFloat(noJsForcedColorsEvidence.boundary.width) >= 1 &&
    noJsForcedColorsEvidence.boundary.style !== "none" &&
    noJsForcedColorsEvidence.boundary.contrast !== null &&
    noJsForcedColorsEvidence.boundary.contrast >= 3,
  `no-JavaScript forced-colors: disclosure boundary is not distinguishable: ` +
    JSON.stringify(noJsForcedColorsEvidence.boundary));
  assert.equal(noJsForcedColorsEvidence.links.length, report.diagrams[0].nodes.length,
    `no-JavaScript forced-colors: all ordinary destinations must be present; ` +
      JSON.stringify(noJsForcedColorsEvidence.links));
  for (const link of noJsForcedColorsEvidence.links) {
    assert.ok(link.visible && link.contrast !== null && link.contrast >= 4.5 &&
      link.underline,
    `no-JavaScript forced-colors: fallback link is not readable/distinguishable: ` +
      JSON.stringify(link));
  }
  assert.ok(noJsFirstLinkFocus.focused &&
    noJsFirstLinkFocus.outlineStyle !== "none" &&
    parseFloat(noJsFirstLinkFocus.outlineWidth) >= 2 &&
    noJsFocusRingContrast.contrast !== null &&
    noJsFocusRingContrast.contrast >= 3,
  `no-JavaScript forced-colors: destination keyboard focus is not visible: ` +
    JSON.stringify({ ...noJsFirstLinkFocus, ...noJsFocusRingContrast }));
  let noJsSummaryFocus;
  for (let tab = 0; tab < 100; tab++) {
    await noJs.keyboard.press("Shift+Tab");
    noJsSummaryFocus = await noJsSummary.evaluate(summary => {
    const style = getComputedStyle(summary);
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
    const probe = document.createElement("span");
    probe.style.color = "Canvas";
    summary.parentElement.append(probe);
    const canvas = getComputedStyle(probe).color;
    probe.remove();
    const outline = channels(style.outlineColor);
    const background = channels(canvas);
    const rendered = outline && background
      ? `rgb(${outline.slice(0, 3).map((channel, index) =>
          Math.round(channel * (outline[3] ?? 1) +
            background[index] * (1 - (outline[3] ?? 1)))
        ).join(", ")})`
      : "";
    const first = luminance(rendered);
    const second = luminance(canvas);
    return {
      selector: ".universe-map-group > summary:focus-visible",
      focused: summary === document.activeElement && summary.matches(":focus-visible"),
      color: style.color,
      outlineColor: style.outlineColor,
      outlineWidth: style.outlineWidth,
      outlineStyle: style.outlineStyle,
      background: canvas,
      contrast: first === null || second === null ? null :
        (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05),
    };
    });
    if (noJsSummaryFocus.focused) break;
  }
  assert.ok(noJsSummaryFocus.focused &&
    noJsSummaryFocus.outlineStyle !== "none" &&
    parseFloat(noJsSummaryFocus.outlineWidth) >= 2 &&
    noJsSummaryFocus.contrast !== null && noJsSummaryFocus.contrast >= 3,
  `no-JavaScript forced-colors: disclosure-summary keyboard focus is not visible: ` +
    JSON.stringify(noJsSummaryFocus));
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
  const narrowContext = await browser.newContext({
    viewport: { width: 320, height: 740 },
    colorScheme: "dark",
    hasTouch: true,
  });
  const narrow = await narrowContext.newPage();
  await narrow.addInitScript(() => localStorage.setItem("askjamie-color-scheme", "dark"));
  await narrow.goto(`${base}/universe/`);
  // Panning is a full-theme behavior. Do not race the mobile activation delay.
  await narrow.waitForFunction(() =>
    [...document.querySelectorAll("link[data-deferred-styles]")].every(link =>
      link.media === "all" && Boolean(link.sheet?.cssRules.length)),
  null, { timeout: 15000 });
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
  assert.ok(await narrow.evaluate(() => navigator.maxTouchPoints > 0),
    "Narrow page-map viewport must support touch input");
  await narrowWrap.evaluate(element => { element.scrollLeft = 0; });
  await narrowWrap.scrollIntoViewIfNeeded();
  await swipeDiagramHorizontally(narrow, narrowWrap);
  await narrow.waitForFunction(element => element.scrollLeft > 0, await narrowWrap.elementHandle(), { timeout: 3000 });
  assert.equal(await narrowGroups.evaluateAll(groups => groups.filter(group => group.open).length), 1,
    "Touch panning must not open another page-map group");
  const touchPanBeforeClose = await narrowWrap.evaluate(element => element.scrollLeft);
  const narrowSummary = narrowGroup.locator("summary");
  await narrowSummary.scrollIntoViewIfNeeded();
  const narrowSummaryBox = await narrowSummary.boundingBox();
  assert.ok(narrowSummaryBox, "Selected page-map summary must be tappable");
  await narrow.touchscreen.tap(
    narrowSummaryBox.x + narrowSummaryBox.width / 2,
    narrowSummaryBox.y + narrowSummaryBox.height / 2
  );
  assert.equal(await narrowGroup.evaluate(group => group.open), false,
    "Touch-tapping the selected summary must close its page-map group");
  assert.equal(await narrowGroups.evaluateAll(groups => groups.every(group => !group.open)), true,
    "Closing the selected group by touch must leave every page-map group collapsed");
  await narrowSummary.scrollIntoViewIfNeeded();
  const closedSummaryBox = await narrowSummary.boundingBox();
  assert.ok(closedSummaryBox, "Closed page-map summary must remain tappable");
  await narrow.touchscreen.tap(
    closedSummaryBox.x + closedSummaryBox.width / 2,
    closedSummaryBox.y + closedSummaryBox.height / 2
  );
  await narrowGroup.locator("svg .node").first().waitFor({ timeout: 20000 });
  const touchPanAfterReopen = await narrowWrap.evaluate(element => element.scrollLeft);
  assert.equal(await narrowGroup.evaluate(group => group.open), true,
    "Touch-tapping the selected summary again must reopen its page-map group");
  assert.equal(await narrowGroups.evaluateAll(groups =>
    groups.every((group, index) => index === 0 || !group.open)
  ), true, "Touch-reopening the selected group must leave every other group collapsed");
  assert.ok(Math.abs(touchPanAfterReopen - touchPanBeforeClose) <= 1,
    `Touch close/reopen must retain horizontal pan: ${touchPanBeforeClose} -> ${touchPanAfterReopen}`);
  assert.ok(await narrowGroup.locator(".mermaid svg a[href]").count() > 0,
    "Rendered diagram must expose map links for the vertical-swipe activation check");
  const pageScrollBeforeVerticalSwipe = await narrow.evaluate(() => window.scrollY);
  await narrow.evaluate(() => {
    window.__universeQaMapLinkActivationDuringVerticalSwipe = null;
    document.addEventListener("click", event => {
      const link = event.target instanceof Element
        ? event.target.closest(".mermaid svg a[href]")
        : null;
      if (!link) return;
      window.__universeQaMapLinkActivationDuringVerticalSwipe = link.getAttribute("href");
      event.preventDefault();
    }, true);
  });
  await swipeDiagramVertically(narrow, narrowGroup.locator(".mermaid svg"));
  await narrow.waitForFunction(
    scrollY => window.scrollY > scrollY,
    pageScrollBeforeVerticalSwipe,
    { timeout: 3000 }
  );
  assert.ok(
    await narrow.evaluate(() => window.scrollY) > pageScrollBeforeVerticalSwipe,
    "A vertical touch swipe starting over the rendered diagram must scroll the page"
  );
  assert.equal(
    await narrow.evaluate(() => window.__universeQaMapLinkActivationDuringVerticalSwipe),
    null,
    "Vertical page scrolling must not activate a page-map link"
  );
  assert.equal(await narrowGroup.evaluate(group => group.open), true,
    "Vertical page scrolling must leave the selected page-map group open");
  assert.equal(await narrowGroups.evaluateAll(groups => groups.filter(group => group.open).length), 1,
    "Vertical page scrolling must not open another page-map group");
  const pageScrollBeforeReverseVerticalSwipe = await narrow.evaluate(() => window.scrollY);
  assert.ok(pageScrollBeforeReverseVerticalSwipe > 0,
    "The reverse vertical swipe must start from a nonzero page scroll position");
  await narrow.evaluate(() => {
    window.__universeQaMapLinkActivationDuringVerticalSwipe = null;
  });
  await swipeDiagramVertically(narrow, narrowGroup.locator(".mermaid svg"), "down");
  await narrow.waitForFunction(
    scrollY => window.scrollY < scrollY,
    pageScrollBeforeReverseVerticalSwipe,
    { timeout: 3000 }
  );
  assert.ok(
    await narrow.evaluate(() => window.scrollY) < pageScrollBeforeReverseVerticalSwipe,
    "A downward finger swipe over the rendered diagram must scroll the page back up"
  );
  assert.equal(
    await narrow.evaluate(() => window.__universeQaMapLinkActivationDuringVerticalSwipe),
    null,
    "A downward vertical page swipe must not activate a page-map link"
  );
  assert.equal(await narrowGroup.evaluate(group => group.open), true,
    "A downward vertical page swipe must leave the selected page-map group open");
  assert.equal(await narrowGroups.evaluateAll(groups => groups.filter(group => group.open).length), 1,
    "A downward vertical page swipe must not open another page-map group");
  await narrow.setViewportSize({ width: 320, height: 400 });
  await narrow.waitForTimeout(50);
  await narrowGroup.locator(".mermaid").scrollIntoViewIfNeeded();
  const landscapePageScrollBeforeSwipe = await narrow.evaluate(() => window.scrollY);
  await narrow.evaluate(() => {
    window.__universeQaMapLinkActivationDuringVerticalSwipe = null;
  });
  await swipeDiagramVertically(narrow, narrowGroup.locator(".mermaid svg"));
  await narrow.waitForFunction(
    scrollY => window.scrollY > scrollY,
    landscapePageScrollBeforeSwipe,
    { timeout: 3000 }
  );
  assert.ok(
    await narrow.evaluate(() => window.scrollY) > landscapePageScrollBeforeSwipe,
    "A vertical touch swipe over the map must scroll the page at 320x400"
  );
  assert.equal(
    await narrow.evaluate(() => window.__universeQaMapLinkActivationDuringVerticalSwipe),
    null,
    "A vertical swipe at 320x400 must not activate a page-map link"
  );
  assert.equal(await narrowGroup.evaluate(group => group.open), true,
    "A vertical swipe at 320x400 must leave the selected page-map group open");
  assert.equal(await narrowGroups.evaluateAll(groups => groups.filter(group => group.open).length), 1,
    "A vertical swipe at 320x400 must not open another page-map group");
  await narrow.setViewportSize({ width: 320, height: 740 });
  const usableLink = narrowGroup.locator("a[data-universe-node]").first();
  assert.ok(await usableLink.isVisible());
  await usableLink.focus();
  assert.ok(await usableLink.evaluate(element => element === document.activeElement));
  const ordinaryLinkHref = await usableLink.evaluate(link => {
    window.__universeQaNarrowLinkActivation = null;
    link.addEventListener("click", event => {
      event.preventDefault();
      window.__universeQaNarrowLinkActivation = {
        trusted: event.isTrusted,
        href: link.getAttribute("href"),
      };
    }, { once: true });
    return link.getAttribute("href");
  });
  await usableLink.click();
  assert.deepEqual(
    await narrow.evaluate(() => window.__universeQaNarrowLinkActivation),
    { trusted: true, href: ordinaryLinkHref },
    "Ordinary page links must still activate after panning the diagram in light mode"
  );
  const narrowAfterPan = await captureGroupLayout(narrow, 0);
  assert.equal(narrowAfterPan.colorScheme, "light");
  assert.equal(narrowAfterPan.open, true, "Panning and link activation must leave the selected group open");
  assert.equal(narrowAfterPan.otherGroupsCollapsed, true, "Panning must not open another group");
  assert.equal(narrowAfterPan.svgReady, true, "Panning must preserve the ready diagram");
  assert.equal(await narrow.evaluate(() =>
    window.__universeQaNarrowSvg === document.querySelector(".universe-map-group svg")
  ), true, "Light-mode panning and link activation must preserve the same SVG");
  await assertSvgLabelContrast(narrowGroup, "light at 320px");
  const keyboard = await browser.newPage();
  await keyboard.goto(`${base}/universe/`);
  await keyboard.locator(".universe-map-group summary").first().focus();
  await keyboard.keyboard.press("Enter");
  assert.equal(await keyboard.locator(".universe-map-group[open]").count(), 1);
  console.log(`Universe browser checks passed: ${report.nodes.length} indexed pages, ${report.diagrams.length} diagrams, light/dark/forced-colors modes; contrast observations=${JSON.stringify(contrastObservations)}; in-place theme switching, focused summary and fallback-link transitions into forced-colors, 320px touch-enabled horizontal panning, upward and downward vertical page scrolling over the diagram without map-link activation or group changes, plus upward scrolling in 320x400 landscape, ordinary link activation, all fallback links, keyboard and no-JavaScript links, and no-JavaScript forced-colors summary, boundary, destination readability, and keyboard focus; failed local-theme readiness unavailable with no geometry baseline.`);
} finally {
  await browser.close();
}

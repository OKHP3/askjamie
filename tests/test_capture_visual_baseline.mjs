import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  assessCaptureReadiness,
  estimateImageScaleRatio,
  validateCaptureDestination,
} from "../scripts/capture-visual-baseline-core.mjs";

test("capture destination safety rejects repository roots and committed baseline replacement", () => {
  assert.throws(
    () =>
      validateCaptureDestination({
        repoRoot: "/repo/askjamie",
        outputDir: "/repo/askjamie",
      }),
    /overwrite the repository root/
  );

  assert.throws(
    () =>
      validateCaptureDestination({
        repoRoot: "/repo/askjamie",
        outputDir: "/repo",
      }),
    /overwrite the repository root or one of its ancestors/
  );

  assert.throws(
    () =>
      validateCaptureDestination({
        repoRoot: "/repo/askjamie",
        outputDir: "/repo/askjamie/assets/audit/visual-baseline",
        outputEntries: ["homepage-1280.png"],
      }),
    /already contains committed visual baselines/
  );

  const safe = validateCaptureDestination({
    repoRoot: "/repo/askjamie",
    outputDir: "/tmp/askjamie-captures",
    reportFile: "/tmp/askjamie-captures/capture-readiness.json",
  });
  assert.equal(safe.outputDir, path.resolve("/tmp/askjamie-captures"));
  assert.equal(safe.reportFile, path.resolve("/tmp/askjamie-captures/capture-readiness.json"));
});

test("capture readiness flags incomplete images, failed requests, and timeout conditions", () => {
  assert.deepEqual(
    assessCaptureReadiness({
      imageCount: 4,
      loadedImages: 4,
      responseErrors: [],
      readinessWaitMs: 75,
      animationSettleMs: 42,
    }),
    { status: "pass", reasons: [] }
  );

  const incomplete = assessCaptureReadiness({
    imageCount: 4,
    loadedImages: 3,
    responseErrors: [],
    readinessWaitMs: 75,
    animationSettleMs: 42,
  });
  assert.equal(incomplete.status, "fail");
  assert.match(incomplete.reasons.join(" | "), /incomplete images: 3\/4/);

  const failed = assessCaptureReadiness({
    imageCount: 4,
    loadedImages: 4,
    responseErrors: [{ url: "/broken.png", status: 404 }],
    readinessWaitMs: 75,
    animationSettleMs: 42,
  });
  assert.equal(failed.status, "fail");
  assert.match(failed.reasons.join(" | "), /failed requests: 1/);

  const timedOut = assessCaptureReadiness({
    imageCount: 4,
    loadedImages: 4,
    responseErrors: [],
    readinessWaitMs: 16001,
    animationSettleMs: 42,
    readinessTimeoutMs: 15000,
  });
  assert.equal(timedOut.status, "fail");
  assert.match(timedOut.reasons.join(" | "), /readiness timeout exceeded/);
});

test("image scale ratio highlights a large source used in a small rendered box", () => {
  assert.equal(estimateImageScaleRatio({ naturalWidth: 1024, renderedWidth: 40 }), 25.6);
  assert.equal(estimateImageScaleRatio({ naturalWidth: 0, renderedWidth: 40 }), null);
});

test("public logo blocks use small density-aware avatar assets", () => {
  const repoRoot = fileURLToPath(new URL("..", import.meta.url));
  const files = execFileSync(
    "git",
    ["ls-files", "*.html"],
    { cwd: repoRoot, encoding: "utf8" }
  )
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((file) => file.trim().replaceAll("\\", "/"))
    .filter((file) => !file.startsWith("assets/templates/") && !file.startsWith("dist-pages/"));

  assert.ok(files.length > 0);
  for (const relative of files) {
    const file = path.join(repoRoot, relative);
    const text = fs.readFileSync(file, "utf8");
    const match = text.match(/<div class="logo">[\s\S]*?<\/div>/);
    assert.ok(match, `missing public header logo in ${relative}`);
    assert.doesNotMatch(
      match[0],
      /askjamie-avatar-tall-left-square-1024\.png/,
      `nav logo still points at the large avatar in ${relative}`
    );
    assert.match(
      match[0],
      /src="\/assets\/img\/askjamie-header-avatar-40\.png"/,
      `nav logo does not use the small fallback in ${relative}`
    );
    for (const [size, density] of [[40, 1], [80, 2], [120, 3]]) {
      assert.match(match[0], new RegExp(`askjamie-header-avatar-${size}\\.png ${density}x`),
        `missing ${density}x source in ${relative}`);
      const image = fs.readFileSync(path.join(repoRoot, `assets/img/askjamie-header-avatar-${size}.png`));
      assert.equal(image.readUInt32BE(16), size, `wrong PNG width for ${density}x source`);
      assert.equal(image.readUInt32BE(20), size, `wrong PNG height for ${density}x source`);
    }
  }
});

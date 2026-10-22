import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.env.BASE_URL || 'http://127.0.0.1:5000';
const screenshotDir = process.env.TRANSITION_SCREENSHOT_DIR;
const browser = await chromium.launch({ headless: true });
const errors = [];
const routes = fs.readFileSync('sitemap.xml', 'utf8').match(/<loc>[^<]+<\/loc>/g)
  .map(item => new URL(item.slice(5, -6)).pathname);
const brandguardRoutes = routes.filter(route =>
  route === '/lens-system/okhp3-brandguard/' ||
  route.startsWith('/lens-system/okhp3-brandguard/')
);
const details = routes.filter(route => route.startsWith('/lens-system/') &&
  !['/lens-system/', '/lens-system/okhp3-brandguard/'].includes(route));
async function pageFor(options = {}) {
  const context = await browser.newContext({ colorScheme: 'light', reducedMotion: 'reduce', ...options });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  return { context, page };
}
async function capture(page, name) {
  if (!screenshotDir) return;
  fs.mkdirSync(screenshotDir, { recursive: true });
  await page.screenshot({ path: path.join(screenshotDir, name + '.png'), fullPage: false });
}
try {
  const { context, page } = await pageFor({ viewport: { width: 1280, height: 900 } });
  await page.goto(base);
  const isMac = await page.evaluate(() => /Mac|iPod|iPhone|iPad/.test(navigator.platform));
  const searchShortcut = isMac ? 'Meta+k' : 'Control+k';
  const searchLabel = isMac ? 'Open search (⌘K)' : 'Open search (Ctrl+K)';
  const dialog = page.locator('[data-transition-dialog]');
  await dialog.waitFor({ state: 'visible' });
  assert.equal(await page.locator('[data-transition-dismiss]').evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Tab');
  assert.equal(await page.getByRole('link', { name: 'What is changing', exact: true }).last().evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.locator('[data-transition-dismiss]').evaluate(el => document.activeElement === el), true);
  await page.keyboard.press(searchShortcut);
  assert.notEqual(await page.locator('.okh-search-overlay').getAttribute('data-open'), 'true');
  await capture(page, 'homepage-dialog-desktop');
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  // Native dialog close events are queued after its open attribute disappears.
  await page.waitForFunction(() => document.activeElement === document.querySelector('[data-transition-open]'));
  assert.equal(await page.locator('[data-transition-open]').evaluate(el => document.activeElement === el), true);
  await page.reload();
  assert.equal(await dialog.isVisible(), false);
  await page.locator('[data-transition-open]').click();
  await dialog.waitFor({ state: 'visible' });
  await page.locator('[data-transition-dismiss]').click();
  await page.getByRole('button', { name: searchLabel, exact: true }).click();
  await page.getByRole('searchbox', { name: 'Search', exact: true }).fill('retirement');
  await page.locator('.okh-search-result[href="/whats-next/"]').waitFor();
  await page.getByRole('button', { name: 'Close search', exact: true }).click();
  await page.goto(base + details[0]);
  assert.equal(await page.locator('[data-transition-dialog]').isVisible(), false);
  assert.equal(await page.locator('.capability-transition').isVisible(), true);
  await capture(page, 'gpt-page-notice-desktop');
  await page.goto(base + '/whats-next/');
  await page.getByRole('heading', { name: 'The ideas carry forward.', exact: true }).waitFor();
  await capture(page, 'transition-page-desktop');
  await context.close();

  // BrandGuard case studies must restore focus from the arrival dialog to its
  // opener so keyboard users can continue through the page into the hero.
  const brandguardKeyboardRoutes = [
    '/lens-system/okhp3-brandguard/',
    '/lens-system/okhp3-brandguard/lvmh/',
    '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
  ];
  for (const route of brandguardKeyboardRoutes) {
    const { context, page } = await pageFor({ viewport: { width: 320, height: 844 } });
    await page.goto(base + route);
    const dialog = page.locator('[data-transition-dialog]');
    const opener = page.locator('[data-transition-open]');
    const dismiss = page.locator('[data-transition-dismiss]');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(
      await dismiss.evaluate(element => document.activeElement === element),
      true,
      `${route}: arrival dialog did not focus its dismiss button`
    );

    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(
      () => document.activeElement === document.querySelector('[data-transition-open]')
    );
    assert.equal(
      await opener.evaluate(element => document.activeElement === element),
      true,
      `${route}: Escape did not return focus to the transition-note opener`
    );

    await page.keyboard.press('Enter');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(
      await dismiss.evaluate(element => document.activeElement === element),
      true,
      `${route}: keyboard reopening did not focus the dialog dismiss button`
    );
    await page.keyboard.press('Tab');
    assert.equal(
      await dialog.getByRole('link', { name: 'What is changing', exact: true })
        .evaluate(element => document.activeElement === element),
      true,
      `${route}: Tab did not move from the dismiss button to the dialog link`
    );
    await page.keyboard.press('Shift+Tab');
    assert.equal(
      await dismiss.evaluate(element => document.activeElement === element),
      true,
      `${route}: Shift+Tab did not wrap to the dialog dismiss button`
    );
    await page.keyboard.press('Enter');
    await dialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(
      () => document.activeElement === document.querySelector('[data-transition-open]')
    );
    assert.equal(
      await opener.evaluate(element => document.activeElement === element),
      true,
      `${route}: keyboard dismissal did not return focus to the transition-note opener`
    );

    const heroActions = page.locator('.askjamie-hero .hero-actions > a[href]');
    const heroActionCount = await heroActions.count();
    assert.ok(heroActionCount > 0, `${route}: hero actions are missing`);
    const reachedHeroActions = new Set();
    for (let tab = 0; tab < 60 && reachedHeroActions.size < heroActionCount; tab += 1) {
      await page.keyboard.press('Tab');
      const focusedAction = await page.evaluate(() =>
        [...document.querySelectorAll('.askjamie-hero .hero-actions > a[href]')]
          .indexOf(document.activeElement)
      );
      if (focusedAction >= 0) reachedHeroActions.add(focusedAction);
    }
    assert.equal(
      reachedHeroActions.size,
      heroActionCount,
      `${route}: Tab sequence did not reach every hero action after closing the dialog`
    );
    await context.close();
  }

  // Fresh direct arrivals must see the note on every dedicated GPT page.
  assert.equal(details.length, 16);
  for (const route of details) {
    const { context, page } = await pageFor({ viewport: { width: 390, height: 844 } });
    await page.goto(base + route);
    await page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, route);
    await page.locator('[data-transition-dismiss]').click();
    assert.equal(await page.locator('.capability-transition').isVisible(), true, route);
    if (route === details[0]) await capture(page, 'gpt-page-notice-mobile');
    await context.close();
  }
  for (const width of [320, 390]) {
    const { context, page } = await pageFor({ viewport: { width, height: 844 } });
    await page.goto(base);
    await page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await capture(page, 'homepage-dialog-mobile-' + width);
    await page.locator('[data-transition-dismiss]').click();
    await page.goto(base + '/whats-next/');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await context.close();
  }
  // Storage denial must not block dismissal or explicit reopening.
  const denied = await pageFor();
  await denied.context.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {
    get() { throw new DOMException('Storage denied', 'SecurityError'); }
  }));
  await denied.page.goto(base);
  await denied.page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
  await denied.page.locator('[data-transition-dismiss]').click();
  await denied.page.locator('[data-transition-open]').click();
  await denied.page.locator('[data-transition-dialog]').waitFor({ state: 'visible' });
  await denied.context.close();
  const noJs = await pageFor({ javaScriptEnabled: false });
  await noJs.page.goto(base + details[0]);
  assert.equal(await noJs.page.locator('.capability-transition').isVisible(), true);
  assert.equal(await noJs.page.locator('[data-transition-open]').isVisible(), false);
  assert.equal(await noJs.page.locator('[data-transition-dialog]').isVisible(), false);
  await noJs.page.getByRole('link', { name: 'Read the transition update', exact: true }).click();
  assert.equal(new URL(noJs.page.url()).pathname, '/whats-next/');
  await noJs.context.close();
  assert.equal(brandguardRoutes.length, 14, 'Expected the sitemap-listed BrandGuard hub and 13 case studies');
  for (const route of brandguardRoutes) {
    const { context, page } = await pageFor({
      viewport: { width: 320, height: 844 },
      javaScriptEnabled: false
    });
    await page.goto(base + route);
    const notice = page.locator('.capability-transition');
    const transitionLink = notice.getByRole('link', {
      name: 'Read the transition update',
      exact: true
    });
    assert.equal(await notice.isVisible(), true, `${route}: transition notice is visible without JavaScript`);
    assert.equal(await transitionLink.isVisible(), true, `${route}: transition link is visible without JavaScript`);
    assert.equal(await page.locator('[data-transition-open]').isVisible(), false, `${route}: JavaScript-only control stays hidden`);
    assert.equal(await page.locator('[data-transition-dialog]').isVisible(), false, `${route}: dialog stays closed without JavaScript`);
    const noticeLayout = await notice.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const textBlocks = [
        element.querySelector('h2'),
        ...element.querySelectorAll('p')
      ].filter(Boolean);
      const linkRects = [...element.querySelectorAll('a[href]')].flatMap(link =>
        [...link.getClientRects()].map(linkRect => ({
          label: link.textContent.trim(),
          left: linkRect.left,
          right: linkRect.right,
          top: linkRect.top,
          bottom: linkRect.bottom,
          width: linkRect.width,
          height: linkRect.height
        }))
      );
      return {
        notice: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
        textBlocks: textBlocks.map(block => {
          const blockRect = block.getBoundingClientRect();
          const style = getComputedStyle(block);
          return {
            tag: block.tagName,
            text: block.textContent.trim(),
            left: blockRect.left,
            right: blockRect.right,
            fontSize: parseFloat(style.fontSize),
            lineHeight: parseFloat(style.lineHeight),
            fits: block.scrollWidth <= block.clientWidth
          };
        }),
        linkRects,
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
        viewportHeight: innerHeight
      };
    });
    assert.equal(
      noticeLayout.documentWidth <= noticeLayout.viewportWidth,
      true,
      `${route}: page has horizontal overflow: ${JSON.stringify(noticeLayout)}`
    );
    assert.equal(
      noticeLayout.notice.left >= 0 && noticeLayout.notice.right <= noticeLayout.viewportWidth &&
        noticeLayout.notice.top >= 0 && noticeLayout.notice.bottom <= noticeLayout.viewportHeight,
      true,
      `${route}: notice is outside the initial viewport: ${JSON.stringify(noticeLayout)}`
    );
    assert.equal(
      noticeLayout.textBlocks.every(block =>
        block.left >= 0 && block.right <= noticeLayout.viewportWidth &&
        block.fontSize >= 14 && block.lineHeight >= block.fontSize && block.fits
      ),
      true,
      `${route}: notice text is clipped or too small to read: ${JSON.stringify(noticeLayout.textBlocks)}`
    );
    assert.equal(
      noticeLayout.linkRects.length > 0 && noticeLayout.linkRects.every(rect =>
        rect.left >= 0 && rect.right <= noticeLayout.viewportWidth &&
        rect.top >= 0 && rect.bottom <= noticeLayout.viewportHeight &&
        rect.width > 0 && rect.height > 0
      ),
      true,
      `${route}: transition link is outside the viewport or has no usable target: ${JSON.stringify(noticeLayout.linkRects)}`
    );
    const noticeLinks = notice.getByRole('link');
    const expectedLinkNames = ['Read the transition update', 'Talk with Jamie'];
    assert.equal(await noticeLinks.count(), expectedLinkNames.length, `${route}: expected both transition links`);
    for (let index = 0; index < expectedLinkNames.length; index += 1) {
      assert.equal(
        await noticeLinks.nth(index).isVisible(),
        true,
        `${route}: ${expectedLinkNames[index]} is not visible without JavaScript`
      );
    }
    const focusedLinkNames = [];
    for (let tab = 0; tab < 40 && focusedLinkNames.length < expectedLinkNames.length; tab += 1) {
      await page.keyboard.press('Tab');
      const focusedLink = await page.evaluate(() => {
        const element = document.activeElement;
        if (!element.matches('.capability-transition a[href]')) return null;
        const style = getComputedStyle(element);
        return {
          name: element.textContent.trim(),
          focusVisible: element.matches(':focus-visible'),
          outlineStyle: style.outlineStyle,
          outlineWidth: parseFloat(style.outlineWidth)
        };
      });
      if (!focusedLink) continue;
      focusedLinkNames.push(focusedLink.name);
      assert.equal(
        focusedLink.name,
        expectedLinkNames[focusedLinkNames.length - 1],
        `${route}: transition links are not reached in the expected order`
      );
      assert.equal(focusedLink.focusVisible, true, `${route}: ${focusedLink.name} lacks keyboard-visible focus`);
      assert.notEqual(focusedLink.outlineStyle, 'none', `${route}: ${focusedLink.name} has no visible focus outline`);
      assert.ok(focusedLink.outlineWidth > 0, `${route}: ${focusedLink.name} has no focus outline width`);
    }
    assert.deepEqual(focusedLinkNames, expectedLinkNames, `${route}: Tab did not reach both transition links`);
    await page.keyboard.press('Shift+Tab');
    assert.equal(
      await noticeLinks.first().evaluate(element => document.activeElement === element),
      true,
      `${route}: Shift+Tab did not return to the first transition link`
    );
    await Promise.all([
      page.waitForURL('**/whats-next/', { waitUntil: 'domcontentloaded' }),
      page.keyboard.press('Enter')
    ]);
    assert.equal(new URL(page.url()).pathname, '/whats-next/', `${route}: Enter did not follow the transition link without JavaScript`);
    await context.close();
  }
  for (const route of brandguardRoutes) {
    const { context, page } = await pageFor({
      viewport: { width: 320, height: 844 },
      javaScriptEnabled: false
    });
    const blockedStylesheets = [];
    const failedStylesheets = [];
    page.on('requestfailed', request => {
      if (request.resourceType() === 'stylesheet') failedStylesheets.push(request.url());
    });
    await context.route('**/*', async intercepted => {
      if (intercepted.request().resourceType() === 'stylesheet') {
        blockedStylesheets.push(intercepted.request().url());
        await intercepted.abort('blockedbyclient');
      } else {
        await intercepted.continue();
      }
    });

    await page.goto(base + route);
    assert.ok(blockedStylesheets.length > 0, `${route}: no stylesheet request was intercepted`);
    for (const stylesheet of blockedStylesheets) {
      assert.ok(
        failedStylesheets.includes(stylesheet),
        `${route}: blocked stylesheet failure was not observed: ${stylesheet}`
      );
    }

    const notice = page.locator('.capability-transition');
    const noticeHeading = notice.getByRole('heading', {
      name: 'A new chapter for AskJamie',
      exact: true
    });
    const transitionLink = notice.getByRole('link', {
      name: 'Read the transition update',
      exact: true
    });
    assert.equal(await notice.isVisible(), true, `${route}: notice is hidden when stylesheets fail`);
    assert.equal(await noticeHeading.isVisible(), true, `${route}: notice heading is hidden when stylesheets fail`);
    const explanatoryCopy = notice.locator('p').nth(1);
    assert.ok(
      (await explanatoryCopy.innerText()).trim().length > 40,
      `${route}: transition notice explanatory text is missing when stylesheets fail`
    );
    assert.equal(
      await explanatoryCopy.isVisible(),
      true,
      `${route}: transition notice explanatory text is hidden when stylesheets fail`
    );
    assert.equal(await transitionLink.isVisible(), true, `${route}: transition link is hidden when stylesheets fail`);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
      `${route}: horizontal overflow when stylesheets fail`
    );

    await Promise.all([
      page.waitForURL('**/whats-next/', { waitUntil: 'domcontentloaded' }),
      transitionLink.click()
    ]);
    assert.equal(
      new URL(page.url()).pathname,
      '/whats-next/',
      `${route}: ordinary transition link did not work without stylesheets or JavaScript`
    );
    await context.close();
  }
  assert.deepEqual(errors, []);
  console.log('Transition QA passed: 16 direct-entry GPT pages, 1280/390/320px, focus, Escape, reopen, session persistence, search, denied storage, and 320px no-JavaScript checks across 14 BrandGuard routes with and without stylesheets.');
} finally {
  await browser.close();
}

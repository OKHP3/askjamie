// Enable optional stylesheets after the document is parsed.
// Critical route CSS paints first with system fallbacks; the full theme and
// branded type settle without waiting for third-party analytics to finish.
const deferredFonts = document.querySelectorAll("link[data-deferred-fonts]");
const deferredStyles = document.querySelectorAll("link[data-deferred-styles]");
const isUniversePage = Boolean(document.querySelector(".universe-map-generated"));

// Preserve the critical-font geometry of BrandGuard tablet pages before the
// theme and web-font styles activate. This reserves each page's own rendered
// line count instead of imposing fixed heights across different case studies.
const brandGuardReservationWidth = window.innerWidth;
const needsBrandGuardFontReservation =
  (window.innerWidth === 320 || (window.innerWidth >= 768 && window.innerWidth <= 899)) &&
  document.body?.classList.contains("askjamie-main") &&
  document.querySelector(".askjamie-breadcrumb");

const reserveBrandGuardTabletGeometry = () => {
  // A delayed stylesheet callback must not stamp measurements for an old width.
  if (window.innerWidth !== brandGuardReservationWidth) return;
  const selectors = [
    ".site-header .container",
    ".site-specials",
    ".capability-transition > *",
    ".askjamie-breadcrumb",
    ".askjamie-hero-copy > *",
  ];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((element) => {
      const height = element.getBoundingClientRect().height;
      if (height > 0) {
        element.style.minHeight = `${Math.ceil(height * 100) / 100}px`;
        element.dataset.fontGeometryReserved = "true";
      }
    });
  });
};

if (needsBrandGuardFontReservation) {
  let appDomReady = document.readyState === "complete";
  document.addEventListener("DOMContentLoaded", () => {
    appDomReady = true;
  }, { once: true });
  const deferredThemeLink = document.querySelector("link[data-deferred-styles]");
  if (deferredThemeLink) {
    reserveBrandGuardTabletGeometry();
  } else {
    const tabletFontStylesheet = document.querySelector("link[data-brandguard-tablet-fonts]");
    const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
      .find((link) => new URL(link.href).pathname === "/assets/css/theme.css");
    const reserveAndActivateTabletFonts = () => {
      const reserveAfterAppInitialization = () => {
        // The transition notice reveals its access button from app.js at
        // DOMContentLoaded. Do not measure an incomplete notice or activate
        // fonts until that synchronous initializer has finished.
        window.setTimeout(() => {
          requestAnimationFrame(() => requestAnimationFrame(() => {
            reserveBrandGuardTabletGeometry();
            if (tabletFontStylesheet) tabletFontStylesheet.media = "all";
          }));
        }, 0);
      };
      if (!appDomReady) {
        document.addEventListener(
          "DOMContentLoaded",
          reserveAfterAppInitialization,
          { once: true }
        );
      } else {
        reserveAfterAppInitialization();
      }
    };
    const reserveAfterThemeStyles = () => {
      reserveAndActivateTabletFonts();
    };
    if (!themeStylesheet || themeStylesheet.sheet?.cssRules.length) {
      reserveAfterThemeStyles();
    } else {
      themeStylesheet.addEventListener("load", reserveAfterThemeStyles, { once: true });
    }
  }
}

// Universe theme and web fonts must be activated in separate phases: measure
// visible text blocks with the complete theme and fallback fonts, reserve those
// natural block sizes, then release the font stylesheet.
if (isUniversePage && (deferredFonts.length || deferredStyles.length)) {
  const selectors = [
    ".site-header",
    ".site-header .container",
    ".site-specials",
    ".site-specials > *",
    ".site-header .primary-nav a",
    ".askjamie-hero-copy .eyebrow",
    ".askjamie-hero-copy h1",
    ".askjamie-hero-copy .hero-subtitle",
    ".askjamie-hero-copy .askjamie-hero-tagline",
    ".askjamie-hero-copy .hero-actions .btn",
    ".askjamie-hero-visual",
    ".askjamie-hero-visual figcaption",
    ".askjamie-hero-visual .mermaid-referral-note",
    ".askjamie-hero-visual .link-list li",
    ".universe-map-generated > h2",
    ".universe-map-generated > p",
    ".universe-map-group > summary",
    ".universe-map-group[open] li",
  ];
  let generation = 0;
  let reservationWidth = window.innerWidth;
  let fontStylesReleased = false;
  let measuring = false;
  let measurementPending = false;

  const clearReservations = () => {
    document.querySelectorAll('[data-universe-font-reservation="true"]').forEach((element) => {
      element.style.removeProperty("min-block-size");
      element.style.removeProperty("min-inline-size");
      delete element.dataset.universeFontReservation;
      delete element.dataset.universeFontReservationWidth;
    });
  };

  const reserve = (expectedGeneration, expectedWidth) => {
    if (expectedGeneration !== generation || expectedWidth !== window.innerWidth ||
        expectedWidth !== reservationWidth) return false;
    selectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((element) => {
        if (!element.getClientRects().length) return;
        const height = element.getBoundingClientRect().height;
        if (height <= 0) return;
        if (element.dataset.universeFontReservation === "true" &&
            Number(element.dataset.universeFontReservationWidth) === expectedWidth) return;
        element.style.minBlockSize = `${Math.ceil(height * 100) / 100}px`;
        // Keep inline minima only on text boxes with separately measured
        // >1px geometry changes; primary navigation widths remain natural.
        if (element.matches(
          ".site-specials-label, .site-specials-link, .askjamie-hero-copy .hero-actions .btn"
        )) {
          const width = element.getBoundingClientRect().width;
          if (width > 0) element.style.minInlineSize = `${Math.ceil(width * 100) / 100}px`;
        }
        element.dataset.universeFontReservation = "true";
        element.dataset.universeFontReservationWidth = String(expectedWidth);
      });
    });
    return expectedGeneration === generation && expectedWidth === window.innerWidth &&
      expectedWidth === reservationWidth;
  };

  const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));
  const measureCurrentWidth = async () => {
    if (measuring) {
      measurementPending = true;
      return;
    }
    measuring = true;
    // Bound retries so a continuously resizing viewport cannot defer fonts
    // indefinitely. Stale passes never apply their measurements.
    let attempt = 0;
    do {
      measurementPending = false;
      const expectedGeneration = generation;
      const expectedWidth = window.innerWidth;
      await nextFrame();
      await nextFrame();
      reserve(expectedGeneration, expectedWidth);
      attempt += 1;
    } while (measurementPending && attempt < 4);
    measuring = false;
  };

  const activateTheme = async () => {
    const waits = [...deferredStyles].map((link) => {
      try {
        if (link.sheet?.cssRules.length) return Promise.resolve();
      } catch {
        // A cross-origin stylesheet cannot be inspected; its load event below
        // remains the readiness signal.
      }
      return new Promise((resolve) => {
        link.addEventListener("load", resolve, { once: true });
        link.addEventListener("error", resolve, { once: true });
      });
    });
    deferredStyles.forEach((link) => { link.media = "all"; });
    await Promise.all(waits);
  };

  const reserveThenReleaseFonts = async () => {
    await activateTheme();
    await measureCurrentWidth();
    deferredFonts.forEach((link) => { link.media = "all"; });
    fontStylesReleased = true;
  };

  const refreshVisibleUniverseGeometry = () => {
    if (!fontStylesReleased || document.fonts.status === "loading") {
      generation += 1;
      void measureCurrentWidth();
    }
  };

  window.addEventListener("resize", () => {
    if (window.innerWidth === reservationWidth) return;
    generation += 1;
    reservationWidth = window.innerWidth;
    clearReservations();
    if (!fontStylesReleased || document.fonts.status === "loading") {
      void measureCurrentWidth();
    }
  }, { passive: true });

  document.querySelectorAll(".universe-map-group").forEach((group) => {
    group.addEventListener("toggle", () => {
      if (group.open) refreshVisibleUniverseGeometry();
    });
  });

  const startUniverseActivation = () => {
    if (window.matchMedia("(max-width: 899px)").matches) {
      window.setTimeout(reserveThenReleaseFonts, 2000);
    } else {
      void reserveThenReleaseFonts();
    }
  };
  if (document.readyState === "complete") {
    startUniverseActivation();
  } else {
    window.addEventListener("load", startUniverseActivation, { once: true });
  }
}

// Each reservation belongs to its measured width, including widths inside the
// tablet range. Height-only viewport changes do not invalidate that measurement.
window.addEventListener("resize", () => {
  if (window.innerWidth === brandGuardReservationWidth) return;

  document.querySelectorAll('[data-font-geometry-reserved="true"]').forEach((element) => {
    element.style.removeProperty("min-height");
    delete element.dataset.fontGeometryReserved;
  });
}, { passive: true });

if (!isUniversePage && (deferredFonts.length || deferredStyles.length)) {
  const enableDeferredStyles = () => {
    deferredFonts.forEach((link) => {
      link.media = "all";
    });
    deferredStyles.forEach((link) => {
      link.media = "all";
    });
  };

  const enableAfterLoad = () => {
    // On mobile, let the critical hero remain stable through the LCP window.
    // Desktop keeps the full theme's existing immediate post-load behavior.
    if (window.matchMedia("(max-width: 899px)").matches) {
      window.setTimeout(enableDeferredStyles, 2000);
    } else {
      enableDeferredStyles();
    }
  };

  if (document.readyState === "complete") {
    enableAfterLoad();
  } else {
    window.addEventListener("load", enableAfterLoad, { once: true });
  }
}

// Enable optional stylesheets after the document is parsed.
// Critical route CSS paints first with system fallbacks; the full theme and
// branded type settle without waiting for third-party analytics to finish.
const deferredFonts = document.querySelectorAll("link[data-deferred-fonts]");
const deferredStyles = document.querySelectorAll("link[data-deferred-styles]");

// Preserve the critical-font geometry of BrandGuard tablet pages before the
// theme and web-font styles activate. This reserves each page's own rendered
// line count instead of imposing fixed heights across different case studies.
const needsBrandGuardFontReservation =
  (window.innerWidth === 320 || (window.innerWidth >= 768 && window.innerWidth <= 899)) &&
  document.body?.classList.contains("askjamie-main") &&
  document.querySelector(".askjamie-breadcrumb");

const reserveBrandGuardTabletGeometry = () => {
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
  const deferredThemeLink = document.querySelector("link[data-deferred-styles]");
  if (deferredThemeLink) {
    reserveBrandGuardTabletGeometry();
  } else {
    const tabletFontStylesheet = document.querySelector("link[data-brandguard-tablet-fonts]");
    const themeStylesheet = [...document.querySelectorAll('link[rel~="stylesheet"]')]
      .find((link) => new URL(link.href).pathname === "/assets/css/theme.css");
    let themeReady = !themeStylesheet || Boolean(themeStylesheet.sheet?.cssRules.length);
    let fontStylesReady = !tabletFontStylesheet;
    let reservationScheduled = false;
    const scheduleReservation = () => {
      if (!themeReady || !fontStylesReady || reservationScheduled) return;
      reservationScheduled = true;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        reserveBrandGuardTabletGeometry();
      }));
    };
    if (themeStylesheet && !themeReady) {
      themeStylesheet.addEventListener("load", () => {
        themeReady = true;
        scheduleReservation();
      }, { once: true });
    }
    if (tabletFontStylesheet) {
      tabletFontStylesheet.addEventListener("load", () => {
        fontStylesReady = true;
        scheduleReservation();
      }, { once: true });
      tabletFontStylesheet.media = "all";
    }
    scheduleReservation();
  }
}

if (deferredFonts.length || deferredStyles.length) {
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
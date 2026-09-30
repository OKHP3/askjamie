// French interaction drafts and source provenance: i18n/pilot/fr/interactions/.
// Display text only. Locale routes, search keys, and keyboard behavior stay canonical.
function okhLocaleText(english, french) {
  return /^fr(?:-|$)/i.test(document.documentElement.lang || "") ? french : english;
}

// ════════════════════════════════════════════════════════════════════════════
//  app.js — Shared client-side script (OverKill Hill P³)
//
//  Sections (in load order):
//   1. GLOBAL   · Reading-progress bar (article pages)
//   2. GLOBAL   · DOMContentLoaded: nav, year stamps, theme controls,
//                 scroll reveal, smooth anchors
//   3. GLOBAL   · Under-construction overlay gate (when present)
//   4. GLOBAL   · Sticky TOC scroll-follow + scrollspy (article pages, ≥1024px)
//   5. GLOBAL   · Search — overlay + dedicated /search/ page
//                 (search.js consolidated here 2026-05-03)
// ════════════════════════════════════════════════════════════════════════════

// ── 1. Reading progress bar ─────────────────────────────────────────────────
(function () {
  const bar = document.getElementById("reading-progress");
  if (!bar) return;

  window.addEventListener(
    "scroll",
    function () {
      const scrollTop =
        window.scrollY || document.documentElement.scrollTop;
      const docHeight =
        document.documentElement.scrollHeight -
        document.documentElement.clientHeight;
      const pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
      bar.style.width = Math.min(pct, 100) + "%";
    },
    { passive: true }
  );
})();

// ── 1b. Mermaid text alternatives ───────────────────────────────────────────
// This runs before deferred Mermaid modules on pages that use inline Mermaid
// setup, and also covers pages using the shared mermaid-init module.
(function () {
  const diagrams = document.querySelectorAll(".mermaid");
  diagrams.forEach((node, index) => {
    if (node.getAttribute("aria-label") || node.getAttribute("aria-describedby")) return;
    const source = node.textContent.replace(/\s+/g, " ").trim();
    const labels = [...source.matchAll(/["']([^"']{2,120})["']/g)]
      .map((match) => match[1].replace(/\\n/g, " "))
      .filter((label, position, all) => all.indexOf(label) === position)
      .slice(0, 24);
    node.setAttribute(
      "aria-label",
      node.dataset.diagramLabel
        || `Diagram ${index + 1}: ${
          labels.join("; ") || "Diagram source is available in the page markup."
        }`,
    );
    node.setAttribute("role", "img");
  });
})();

// ── 2. Page interactions: nav, year, theme toggle, scroll reveal ───────────
document.addEventListener("DOMContentLoaded", () => {
  const header = document.querySelector(".site-header");
  const navToggle = document.querySelector(".nav-toggle");
  const yearSpans = document.querySelectorAll(
    "#current-year, #current-year-about, #current-year-manifesto, #current-year-projects, #current-year-glee, #current-year-askjamie"
  );
  const body = document.body;

  document.addEventListener("click", (event) => {
    const trigger = event.target instanceof Element ? event.target.closest("[data-gtag-event]") : null;
    if (!trigger) return;
    const eventName = trigger.dataset.gtagEvent;
    if (!eventName || typeof window.gtag !== "function") return;
    const payload = {};
    if (trigger.dataset.gtagCategory) payload.event_category = trigger.dataset.gtagCategory;
    if (trigger.dataset.gtagLabel) payload.event_label = trigger.dataset.gtagLabel;
    window.gtag("event", eventName, payload);
  });

  // Mobile nav
  if (navToggle && header) {
    const mobileNavQuery = window.matchMedia("(max-width: 768px)");
    const primaryNav = document.getElementById(navToggle.getAttribute("aria-controls"));
    let navWasOpen = false;

    function setNavAccessibility(open) {
      if (!primaryNav) return;
      const hidden = mobileNavQuery.matches && !open;
      primaryNav.setAttribute("aria-hidden", String(hidden));
      primaryNav.toggleAttribute("inert", hidden);
    }

    function setNavOpen(open, returnFocus) {
      navWasOpen = open;
      header.classList.toggle("nav-open", open);
      navToggle.setAttribute("aria-expanded", String(open));
      setNavAccessibility(open);
      if (!open && returnFocus) navToggle.focus();
      if (open && primaryNav) {
        const firstLink = primaryNav.querySelector("a[href]");
        if (firstLink) setTimeout(() => firstLink.focus(), 0);
      }
    }

    setNavAccessibility(false);
    navToggle.addEventListener("click", () => {
      setNavOpen(!navWasOpen, true);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && navWasOpen && mobileNavQuery.matches) {
        event.preventDefault();
        setNavOpen(false, true);
      }
    });

    const syncNavOnViewportChange = () => {
      if (!mobileNavQuery.matches) {
        navWasOpen = false;
        header.classList.remove("nav-open");
        navToggle.setAttribute("aria-expanded", "false");
      }
      setNavAccessibility(navWasOpen);
    };
    if (typeof mobileNavQuery.addEventListener === "function") {
      mobileNavQuery.addEventListener("change", syncNavOnViewportChange);
    } else {
      mobileNavQuery.addListener(syncNavOnViewportChange);
    }
  }

  // Header shadow
  if (header) {
    window.addEventListener("scroll", () => {
      if (window.scrollY > 50) {
        header.classList.add("scrolled");
      } else {
        header.classList.remove("scrolled");
      }
    });
  }

  // Year stamps
  const year = new Date().getFullYear();
  yearSpans.forEach((el) => {
    if (el) el.textContent = year;
  });

  // ── Header controls wrapper (holds search + theme toggle) ───────────────────
  // Created on all pages so injectTrigger() always has a consistent target.
  let headerControls = null;
  if (header) {
    const container = header.querySelector(".container");
    if (container) {
      headerControls = document.createElement("div");
      headerControls.className = "header-controls";
      const navTogglePre = container.querySelector(".nav-toggle");
      if (navTogglePre) {
        container.insertBefore(headerControls, navTogglePre);
      } else {
        container.appendChild(headerControls);
      }
    }
  }

  // Brand sites keep data-theme="light" for shared rules while their
  // auto/light/dark preference is expressed through data-color-scheme.
  const brandLocked =
    body.classList.contains("glee-main") ||
    body.classList.contains("askjamie-main");

  const readStorage = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  };
  const writeStorage = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (_) {
      // Private browsing and disabled storage must not break the page.
    }
  };
  const removeStorage = (key) => {
    try {
      localStorage.removeItem(key);
    } catch (_) {
      // Private browsing and disabled storage must not break the page.
    }
  };

  if (!brandLocked) {
    const STATES      = ["system", "light", "dark"];
    const STATE_ICONS = {
      system: '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
      light:  '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
      dark:   '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    };
    const STATE_ARIA  = {
      system: okhLocaleText("Switch to light mode", "Passer au mode clair"),
      light:  okhLocaleText("Switch to dark mode", "Passer au mode sombre"),
      dark:   okhLocaleText("Switch to system mode", "Utiliser le thème du système"),
    };

    const savedTheme = readStorage("okh-theme");
    let currentState = STATES.includes(savedTheme) ? savedTheme : "system";

    function applyThemeState(state) {
      if (state === "system") {
        const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
      } else {
        document.documentElement.setAttribute("data-theme", state);
      }
    }

    applyThemeState(currentState);

    const themeToggle = document.createElement("button");
    themeToggle.classList.add("theme-toggle");
    themeToggle.dataset.state = currentState;
    themeToggle.setAttribute("aria-label", STATE_ARIA[currentState]);
    themeToggle.innerHTML = STATE_ICONS[currentState];

    if (headerControls) {
      headerControls.appendChild(themeToggle);
    } else if (header && header.querySelector(".container")) {
      header.querySelector(".container").appendChild(themeToggle);
    }

    themeToggle.addEventListener("click", () => {
      const idx    = STATES.indexOf(currentState);
      currentState = STATES[(idx + 1) % STATES.length];
      themeToggle.dataset.state = currentState;
      themeToggle.setAttribute("aria-label", STATE_ARIA[currentState]);
      themeToggle.innerHTML = STATE_ICONS[currentState];
      applyThemeState(currentState);
      writeStorage("okh-theme", currentState);
    });

    const systemThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const syncSystemTheme = () => {
      if (currentState === "system") applyThemeState("system");
    };
    if (typeof systemThemeQuery.addEventListener === "function") {
      systemThemeQuery.addEventListener("change", syncSystemTheme);
    } else {
      systemThemeQuery.addListener(syncSystemTheme);
    }
  } else {
    // Glee-fully and AskJamie keep data-theme="light" for shared light rules,
    // while their optional dark scheme is controlled independently.
    document.documentElement.setAttribute("data-theme", "light");

    const isGlee = body.classList.contains("glee-main");
    const schemeKey = isGlee ? "glee-color-scheme" : "askjamie-color-scheme";
    const schemeStates = ["auto", "light", "dark"];
    const schemeColors = isGlee
      ? { light: "#d35b2d", dark: "#1e1b19" }
      : { light: "#f5efe1", dark: "#2c5e6f" };
    const schemeIcons = {
      auto: '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',
      light: '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
      dark: '<svg class="tt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>',
    };
    const schemeLabels = {
      auto: "Color scheme: following your device — click to pin light",
      light: "Color scheme: pinned light — click to switch to dark",
      dark: "Color scheme: pinned dark — click to follow device",
    };
    const savedScheme = readStorage(schemeKey);
    let schemeState = schemeStates.includes(savedScheme) ? savedScheme : "auto";

    function applySchemeState(state) {
      if (state === "auto") {
        document.documentElement.removeAttribute("data-color-scheme");
      } else {
        document.documentElement.setAttribute("data-color-scheme", state);
      }

      document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
        if (state !== "auto") {
          meta.setAttribute("content", schemeColors[state]);
          return;
        }
        const media = meta.getAttribute("media") || "";
        meta.setAttribute(
          "content",
          media.includes("prefers-color-scheme: dark")
            ? schemeColors.dark
            : schemeColors.light
        );
      });
    }

    applySchemeState(schemeState);
    const schemeToggle = document.createElement("button");
    schemeToggle.type = "button";
    schemeToggle.className = "glee-color-toggle";
    schemeToggle.dataset.state = schemeState;
    schemeToggle.setAttribute("aria-label", schemeLabels[schemeState]);
    schemeToggle.innerHTML = schemeIcons[schemeState];
    if (headerControls) {
      headerControls.appendChild(schemeToggle);
    } else if (header && header.querySelector(".container")) {
      header.querySelector(".container").appendChild(schemeToggle);
    }

    schemeToggle.addEventListener("click", () => {
      const currentIndex = schemeStates.indexOf(schemeState);
      schemeState = schemeStates[(currentIndex + 1) % schemeStates.length];
      schemeToggle.dataset.state = schemeState;
      schemeToggle.setAttribute("aria-label", schemeLabels[schemeState]);
      schemeToggle.innerHTML = schemeIcons[schemeState];
      applySchemeState(schemeState);
      if (schemeState === "auto") {
        removeStorage(schemeKey);
      } else {
        writeStorage(schemeKey, schemeState);
      }
    });
  }

  // Language switcher dropdown (i18n pilot pages only -- the markup only
  // exists on the four pilot routes and their /fr/, /de/, /es/
  // equivalents, so this is a no-op everywhere else). Same disclosure
  // shape as the theme toggle above: a small button shows the current
  // state (here, the active page's language flag) and a click reveals
  // the other options. Unlike the theme toggle this doesn't hold its own
  // state -- each option is a real link to a different page.
  document.querySelectorAll(".lang-switch").forEach((wrap) => {
    const toggle = wrap.querySelector(".lang-switch-toggle");
    const menu = wrap.querySelector(".lang-switch-menu");
    if (!toggle || !menu) return;

    const closeMenu = () => {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    };
    const openMenu = () => {
      menu.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    };

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (menu.hidden) {
        openMenu();
      } else {
        closeMenu();
      }
    });

    wrap.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hidden) {
        closeMenu();
        toggle.focus();
      }
    });

    document.addEventListener("click", (event) => {
      if (!menu.hidden && !wrap.contains(event.target)) closeMenu();
    });

    document.addEventListener("focusin", (event) => {
      if (!menu.hidden && !wrap.contains(event.target)) closeMenu();
    });
  });

  // Optional scroll animation. Visibility never depends on this controller.
  let revealObserver;
  try {
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
        typeof window.IntersectionObserver === "function") {
      const revealEls = document.querySelectorAll(".reveal-on-scroll");
      revealObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              revealObserver.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.15 }
      );

      revealEls.forEach((el) => {
        el.classList.add("is-reveal-ready");
        revealObserver.observe(el);
      });
    } else {
      document.querySelectorAll(".reveal-on-scroll").forEach((el) => el.classList.add("is-visible"));
    }
  } catch (error) {
    // An unavailable enhancement must not interrupt anchors or other controls.
    revealObserver?.disconnect();
    document.querySelectorAll(".reveal-on-scroll").forEach((el) => el.classList.add("is-visible"));
  }

  // Smooth scroll for internal anchors
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (e) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const href = link.getAttribute("href");
      if (!href || href === "#") return;
      let target;
      try { target = document.getElementById(decodeURIComponent(href.slice(1))); }
      catch (error) { return; }
      if (!target) return;
      e.preventDefault();
      // Move keyboard navigation with the viewport, including non-focusable main.
      if (!target.hasAttribute("tabindex") && target.tabIndex < 0) {
        target.setAttribute("tabindex", "-1");
        target.addEventListener("blur", () => target.removeAttribute("tabindex"), { once: true });
      }
      target.focus({ preventScroll: true });
      if (location.hash !== href) history.pushState(history.state, "", href);
      target.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start"
      });
    });
  });

  // ── 3. GLEE · Under-construction overlay gate ────────────────────────────
  // Used on glee-fully.tools toolbox pages that are live-but-not-finished.
  // No-op on pages without `.construction-overlay`.
  const constructionOverlay = document.querySelector(".construction-overlay");

  if (constructionOverlay) {
    constructionOverlay.setAttribute("role", "dialog");
    constructionOverlay.setAttribute("aria-modal", "true");
    constructionOverlay.setAttribute("aria-label", "Work-in-progress page notice");
    const opener = document.activeElement &&
      document.activeElement !== document.body &&
      document.activeElement !== document.documentElement
      ? document.activeElement
      : null;
    const wipKey =
      constructionOverlay.getAttribute("data-wip-key") ||
      window.location.pathname;

    const storageKey = `glee-wip-dismissed:${wipKey}`;

    // If user already dismissed this specific WIP page, hide overlay
    if (readStorage(storageKey) === "true") {
      body.classList.add("construction-dismissed");
      constructionOverlay.setAttribute("hidden", "");
    } else {
      const dismissOverlay = () => {
        body.classList.add("construction-dismissed");
        constructionOverlay.setAttribute("aria-hidden", "true");
        constructionOverlay.setAttribute("hidden", "");
        writeStorage(storageKey, "true");

        if (opener && opener.isConnected && !constructionOverlay.contains(opener)) {
          opener.focus();
          return;
        }
        const mainTarget = document.querySelector("#main h1, #main");
        if (mainTarget) {
          if (!mainTarget.hasAttribute("tabindex")) {
            mainTarget.setAttribute("tabindex", "-1");
          }
          mainTarget.focus({ preventScroll: true });
        }
      };

      const dismissButtons = constructionOverlay.querySelectorAll(
        "[data-wip-dismiss]"
      );

      dismissButtons.forEach((btn) => {
        btn.addEventListener("click", dismissOverlay);
      });

      constructionOverlay.addEventListener("click", (event) => {
        if (event.target === constructionOverlay) {
          const primaryDismiss = constructionOverlay.querySelector(
            "[data-wip-dismiss]"
          );
          if (primaryDismiss) primaryDismiss.click();
        }
      });

      const overlayFocusable = Array.from(
        constructionOverlay.querySelectorAll(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      );
      if (overlayFocusable.length) {
        requestAnimationFrame(() => overlayFocusable[0].focus());
      }
      constructionOverlay.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          dismissOverlay();
          return;
        }
        if (event.key !== "Tab" || !overlayFocusable.length) return;
        const first = overlayFocusable[0];
        const last = overlayFocusable[overlayFocusable.length - 1];
        if (event.shiftKey) {
          if (document.activeElement === first) {
            event.preventDefault();
            last.focus();
          }
        } else if (document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
    }
  }

});

// 4. Centered TOC scroll-follow. Shared by every #toc-widget sidebar.
(function () {
  const toc = document.getElementById("toc-widget");
  const footer = document.querySelector(".site-footer");
  if (!toc || !footer) return;

  const wide = window.matchMedia("(min-width: 1024px)");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const rail = toc.closest(".sidebar-rail, .manifesto-sidebar");
  const header = document.querySelector(".site-header");
  let position = 0;
  let frame = 0;
  let previousTime = 0;

  function schedule() {
    if (!frame) frame = requestAnimationFrame(tick);
  }

  function tick(time) {
    frame = 0;
    if (!wide.matches || !toc.getClientRects().length) {
      position = 0;
      previousTime = 0;
      toc.style.removeProperty("transform");
      toc.style.removeProperty("max-height");
      toc.classList.remove("toc-follow-active");
      if (rail) rail.classList.remove("toc-follow-rail");
      return;
    }

    // A sticky ancestor and a translated child otherwise fight over position.
    if (rail) rail.classList.add("toc-follow-rail");
    toc.classList.add("toc-follow-active");
    const topGap = Math.max(112, (header ? header.getBoundingClientRect().height : 0) + 16);
    toc.style.maxHeight = `${Math.max(80, window.innerHeight - topGap - 32)}px`;
    const box = toc.getBoundingClientRect();
    const naturalTop = box.top + window.scrollY - position;
    const centered = Math.max(topGap, (window.innerHeight - box.height) / 2);
    const footerTop = footer.getBoundingClientRect().top + window.scrollY;
    const maximum = Math.max(0, footerTop - 32 - naturalTop - box.height);
    const target = Math.min(Math.max(0, window.scrollY + centered - naturalTop), maximum);
    // Preserve the Mac Studio 8%-per-frame feel at 60 Hz on faster displays too.
    const elapsed = previousTime ? Math.min(64, time - previousTime) : 1000 / 60;
    const blend = reduced.matches ? 1 : 1 - Math.pow(0.92, elapsed / (1000 / 60));
    position += (target - position) * blend;
    // Clamp immediately at the footer even during a fast fling toward the bottom.
    position = Math.min(position, maximum);
    if (Math.abs(target - position) < 0.1) position = target;
    toc.style.transform = `translateY(${position}px)`;
    previousTime = time;
    if (position !== target) schedule();
    else previousTime = 0;
  }

  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  [wide, reduced].forEach((query) => {
    if (typeof query.addEventListener === "function") query.addEventListener("change", schedule);
    else if (typeof query.addListener === "function") query.addListener(schedule);
  });
  // Fonts, images and diagrams can change the sidebar's natural position.
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(schedule);
    observer.observe(document.body);
    observer.observe(toc);
  }
  if (document.fonts) document.fonts.ready.then(schedule);
  window.addEventListener("load", schedule);
  schedule();
}());

// ── 4b. TOC scrollspy — active-link tracking for #toc-widget ───────────
// Works on any page that has id="toc-widget" with .toc-list anchor links.
// Pairs with Section 4's lerp scroll-follow. No-op when widget is absent.
(function () {
  var links = Array.from(document.querySelectorAll('#toc-widget .toc-list a[href^="#"]'));
  if (!links.length) return;

  var targets = links
    .map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); })
    .filter(Boolean);
  if (!targets.length) return;

  function setActive() {
    var triggerY = window.scrollY + window.innerHeight * 0.20;
    var activeId = null;
    targets.forEach(function (el) {
      if (el.getBoundingClientRect().top + window.scrollY <= triggerY) activeId = el.id;
    });
    links.forEach(function (a) {
      a.classList.toggle('toc-active', a.getAttribute('href').slice(1) === activeId);
    });
  }

  window.addEventListener('scroll', setActive, { passive: true });
  setTimeout(setActive, 100);
}());

// ── 5. OKH Search — overlay + dedicated /search/ page ──────────────────────
// Consolidated from search.js (2026-05-03). All 26 production pages load this.
// Index: /assets/data/search-index.json  Styles: inlined into theme.css (2026-05-04)
// Keyboard: Ctrl/Cmd+K or "/" to open · Esc to close · ↑/↓ navigate · ↵ follow
(function () {
  "use strict";

  // French is the only reviewed, indexable locale. German and Spanish remain
  // noindex drafts with intentionally empty indexes, so they search the
  // English catalog until their publication gate explicitly promotes them.
  const isAskJamie = document.body.classList.contains("askjamie-main");
  // A sibling's published locales do not establish AskJamie catalog coverage.
  const SEARCH_INDEXES = isAskJamie ? {} : { fr: "/assets/data/search-index.fr.json" };
  const pageLocale = (document.documentElement.lang || "en").toLowerCase().split("-", 1)[0];
  const INDEX_URL = SEARCH_INDEXES[pageLocale] || "/assets/data/search-index.json";
  const usesEnglishFallback = isAskJamie ? pageLocale !== "en" : pageLocale === "de" || pageLocale === "es";
  const scopeNotice = usesEnglishFallback ? " Search English content." : "";
  const isGlee = () => document.body.classList.contains("glee-main");
  const searchCopy = () => isGlee() ? {
    label: "Search Glee‑fully Tools",
    placeholder: "Search tools, branches, and everyday tasks…",
    introduction: "Find Tool-ettes, branch guides, and pages across the Glee‑fully Toolbox.",
    suggestions: ["resume", "budget", "scheduling", "travel", "journal"],
  } : document.body.classList.contains("askjamie-main") ? {
    label: "Search AskJamie",
    placeholder: "Search AskJamie: lenses, BrandGuard cases, and how it works…",
    introduction: "Search AskJamie pages, the Lens System, and public BrandGuard demonstrations.",
    suggestions: ["BrandGuard", "BFS", "résumé", "portfolio", "enterprise", "Coca-Cola"],
  } : {
    label: okhLocaleText("Search OverKill Hill", "Rechercher sur OverKill Hill"),
    placeholder: okhLocaleText("Search the Forge: articles, projects, ideas…", "Rechercher dans la forge : articles, projets, idées…"),
    introduction: okhLocaleText("Search across writings, projects, manifesto, and the Council archives.", "Recherchez parmi les pages disponibles en français. La recherche complète donne accès au contenu en anglais."),
    suggestions: /^fr(?:-|$)/i.test(document.documentElement.lang || "")
      ? ["projets", "protocoles", "contact", "IA"]
      : ["mermaid", "ROY", "council", "manifesto", "diagram", "visual edition"],
    suggestionLabels: /^fr(?:-|$)/i.test(document.documentElement.lang || "")
      ? null : ["Mermaid", "ROY", "Council", "Manifesto", "diagram", "v0.3 Visual Edition"],
  };

  // ----- index loader (cached promise) -----
  let _indexPromise = null;
  function loadIndex(forceRetry) {
    if (!_indexPromise || forceRetry) {
      _indexPromise = fetch(INDEX_URL, { credentials: "same-origin" })
        .then((r) => {
          if (!r.ok) throw new Error("Index fetch failed: " + r.status);
          return r.json();
        })
        .then((d) => {
          const entries = Array.isArray(d.entries) ? d.entries : d.pages;
          if (!Array.isArray(entries)) throw new Error("Invalid search index schema");
          return entries.map((entry) => ({ ...entry, category: entry.category || entry.section || "Page" }));
        })
        .catch((err) => {
          console.warn("[okh-search] index load failed:", err);
          throw err;
        });
    }
    return _indexPromise;
  }

  // ----- scoring -----
  function normalizeSearchText(value) {
    return String(value || "")
      .normalize("NFKD")
      .replace(/\p{M}/gu, "")
      .toLowerCase();
  }
  function tokenize(q) {
    return normalizeSearchText(q)
      .split(/[^\p{L}\p{N}'-]+/gu)
      .filter((t) => t.length >= 2);
  }
  function scoreEntry(entry, tokens) {
    if (!tokens.length) return 0;
    const title    = normalizeSearchText(entry.title);
    const desc     = normalizeSearchText(entry.description);
    const headings = normalizeSearchText((entry.headings || []).join(" "));
    const body     = normalizeSearchText(entry.body);
    const url      = normalizeSearchText(entry.url);

    let score = 0;
    let allHit = true;
    for (const t of tokens) {
      let tokenHit = 0;
      if (title.includes(t))    tokenHit += 8;
      if (headings.includes(t)) tokenHit += 5;
      if (desc.includes(t))     tokenHit += 4;
      if (body.includes(t))     tokenHit += 2;
      if (url.includes(t))      tokenHit += 1;
      if (tokenHit === 0) allHit = false;
      score += tokenHit;
    }
    // Bonus: full-phrase match
    const phrase = tokens.join(" ");
    if (phrase.length > 2) {
      if (title.includes(phrase)) score += 10;
      if (desc.includes(phrase))  score += 6;
      if (body.includes(phrase))  score += 4;
    }
    // Slight penalty for article-section duplicates so the parent ranks above
    if (entry.category === "Article Section") score -= 0.5;
    return allHit ? score : score * 0.4;
  }
  function search(entries, q, options) {
    const tokens = tokenize(q);
    if (!tokens.length) return [];
    const normalized = typeof options === "number" ? { limit: options } : (options || {});
    const category = normalized.category || "all";
    const scored = [];
    for (const e of entries) {
      if (category !== "all" && (e.category || "Page") !== category) continue;
      const s = scoreEntry(e, tokens);
      if (s > 0) scored.push([s, e]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    return scored.slice(0, normalized.limit || 30).map(([s, e]) => ({ score: s, entry: e }));
  }

  // ----- snippet + highlight -----
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function snippetFor(entry, tokens, length) {
    const body = entry.body || entry.description || "";
    if (!body || !tokens.length) return "";
    const bestIdx = normalizedMatchRanges(body, tokens, true)[0]?.[0] ?? -1;
    let start = 0;
    if (bestIdx > 80) start = Math.max(0, bestIdx - 60);
    let snip = body.slice(start, start + (length || 220));
    if (start > 0) snip = "…" + snip;
    if (start + (length || 220) < body.length) snip += "…";
    return snip;
  }
  function normalizedMatchRanges(text, tokens, firstOnly = false) {
    if (!tokens.length) return [];
    const original = String(text ?? "");
    const normalized = normalizeSearchText(original);
    if (firstOnly) {
      let first = -1;
      for (const token of tokens) {
        const at = token ? normalized.indexOf(token) : -1;
        if (at !== -1 && (first === -1 || at < first)) first = at;
      }
      if (first === -1) return [];
      let offset = 0;
      // Snippets need one source offset, not every occurrence in a long article.
      for (const match of original.matchAll(/\P{M}\p{M}*|\p{M}+/gu)) {
        offset += normalizeSearchText(match[0]).length;
        if (offset > first) return [[match.index, match.index + match[0].length]];
      }
      return [];
    }
    const starts = [];
    const ends = [];
    // Preserve original offsets through accents and compatibility expansions
    // (P³, decomposed accents, ligatures). Include combining marks in the span.
    for (const match of original.matchAll(/\P{M}\p{M}*|\p{M}+/gu)) {
      const folded = normalizeSearchText(match[0]);
      for (let i = 0; i < folded.length; i++) {
        starts.push(match.index);
        ends.push(match.index + match[0].length);
      }
    }
    const ranges = [];
    for (const token of tokens) {
      if (!token) continue;
      for (let at = normalized.indexOf(token); at !== -1; at = normalized.indexOf(token, at + 1)) {
        ranges.push([starts[at], ends[at + token.length - 1]]);
      }
    }
    ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [];
    for (const range of ranges) {
      const previous = merged[merged.length - 1];
      if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
      else merged.push(range);
    }
    return merged;
  }
  function highlight(text, tokens) {
    const original = String(text ?? "");
    let html = "";
    let offset = 0;
    for (const [start, end] of normalizedMatchRanges(original, tokens)) {
      html += escapeHtml(original.slice(offset, start)) + "<mark>" + escapeHtml(original.slice(start, end)) + "</mark>";
      offset = end;
    }
    return html + escapeHtml(original.slice(offset));
  }

  function categoryLabel(category) {
    const labels = {
      Home: okhLocaleText("Home", "Accueil"),
      Brand: okhLocaleText("Brand", "Marque"),
      Project: okhLocaleText("Project", "Projet"),
      About: okhLocaleText("About", "À propos"),
      Projects: okhLocaleText("Projects", "Projets"),
      Contact: okhLocaleText("Contact", "Contact"),
      Page: okhLocaleText("Page", "Page"),
    };
    return labels[category] || category;
  }

  // ----- result rendering -----
  function renderResultHtml(result, tokens) {
    const e = result.entry;
    const snip = snippetFor(e, tokens, 220);
    return (
      '<div class="okh-search-result-meta">' +
        '<span class="okh-search-result-cat">'  + escapeHtml(categoryLabel(e.category || "Page")) + "</span>" +
        (e.branch_label ? '<span>' + escapeHtml(e.branch_label) + '</span>' : "") +
        (e.publication_state ? '<span class="okh-search-result-state">' + escapeHtml({live: okhLocaleText("Live catalog entry", "Entrée du catalogue en ligne"), beta: okhLocaleText("Beta", "Bêta"), unavailable: okhLocaleText("Unavailable", "Indisponible")}[e.publication_state] || e.publication_state) + '</span>' : "") +
        '<span class="okh-search-result-url">'  + escapeHtml(e.url) + "</span>" +
      "</div>" +
      '<h3 class="okh-search-result-title">' + highlight(e.title || e.url, tokens) + "</h3>" +
      (snip ? '<p class="okh-search-result-snippet">' + highlight(snip, tokens) + "</p>" : "")
    );
  }

  // ── Overlay (every page) ────────────────────────────────────────────────
  function buildOverlay() {
    if (document.querySelector(".okh-search-overlay")) return null;
    const wrap = document.createElement("div");
    wrap.className = "okh-search-overlay";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", searchCopy().label);
    wrap.innerHTML = (
      '<div class="okh-search-panel" role="document">' +
        '<div class="okh-search-input-row">' +
          '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
            '<circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />' +
          "</svg>" +
          '<input type="search" class="okh-search-input" autocomplete="off" spellcheck="false" ' +
            'placeholder="' + escapeHtml(searchCopy().placeholder) + '" aria-label="' + okhLocaleText("Search", "Rechercher") + '" />' +
          '<button type="button" class="okh-search-close" aria-label="' + okhLocaleText("Close search", "Fermer la recherche") + '">' + okhLocaleText("Esc", "Échap") + '</button>' +
        "</div>" +
        '<div class="okh-search-results" role="list" aria-label="' + okhLocaleText("Search results", "Résultats de recherche") + '"></div>' +
        '<div class="okh-search-status sr-only" role="status" aria-live="polite" aria-atomic="true"></div>' +
        '<div class="okh-search-footer">' +
          '<div class="okh-search-keys">' +
            "<span><kbd>↑</kbd><kbd>↓</kbd> " + okhLocaleText("navigate", "parcourir") + "</span>" +
            "<span><kbd>↵</kbd> " + okhLocaleText("open", "ouvrir") + "</span>" +
            "<span><kbd>" + okhLocaleText("Esc", "Échap") + "</kbd> " + okhLocaleText("close", "fermer") + "</span>" +
          "</div>" +
          '<a href="/search/">' + okhLocaleText("Open full search →", "Ouvrir la recherche complète (contenu en anglais) →") + '</a>' +
        "</div>" +
      "</div>"
    );
    document.body.appendChild(wrap);
    return wrap;
  }

  function emptyStateHtml() {
    return (
      '<div class="okh-search-empty">' +
        "<p>" + escapeHtml(searchCopy().introduction) + "</p>" +
        '<ul class="okh-search-hint-list">' +
          searchCopy().suggestions.map((q, i) => '<li><button type="button" data-q="' + escapeHtml(q) + '">' + escapeHtml(searchCopy().suggestionLabels?.[i] || q) + '</button></li>').join("") +
        "</ul>" +
      "</div>"
    );
  }

  // Results remain ordinary links. Keyboard selection moves real focus so
  // assistive technology announces the destination and native Enter/Tab work.
  function initResultNavigation(input, list) {
    const links = () => Array.from(list.querySelectorAll(".okh-search-result"));
    function setActive(index) {
      const results = links();
      if (!results.length) return;
      const activeIndex = Math.max(0, Math.min(index, results.length - 1));
      results.forEach((link, position) => {
        if (position === activeIndex) link.setAttribute("data-active", "true");
        else link.removeAttribute("data-active");
      });
    }
    function focusResult(index) {
      const results = links();
      if (!results.length) return;
      if (index < 0) { input.focus(); return; }
      setActive(index);
      results[Math.min(index, results.length - 1)].focus();
    }
    input.addEventListener("keydown", event => {
      if (event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const current = links().findIndex(link => link.getAttribute("data-active") === "true");
        if (document.activeElement === input) {
          if (event.key === "ArrowDown") {
            focusResult(0);
            if (links().length > 1) setActive(1);
          } else {
            focusResult(current > 0 ? current - 1 : links().length - 1);
          }
        } else {
          focusResult(current + (event.key === "ArrowDown" ? 1 : -1));
        }
      } else if (event.key === "Enter" && links().length) {
        event.preventDefault();
        (links().find(link => link.getAttribute("data-active") === "true") || links()[0]).click();
      }
    });
    list.addEventListener("focusin", event => {
      links().forEach(link => {
        if (link === event.target) link.setAttribute("data-active", "true");
        else link.removeAttribute("data-active");
      });
    });
    list.addEventListener("keydown", event => {
      const index = links().indexOf(event.target);
      if (index < 0 || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
      event.preventDefault();
      focusResult(index + (event.key === "ArrowDown" ? 1 : -1));
    });
  }

  function markFirstResult(list) {
    const first = list.querySelector(".okh-search-result");
    if (first && !list.querySelector('[data-active="true"]')) {
      first.setAttribute("data-active", "true");
    }
  }

  function initOverlay() {
    const overlay = buildOverlay();
    if (!overlay) return;
    const input    = overlay.querySelector(".okh-search-input");
    const list     = overlay.querySelector(".okh-search-results");
    const status   = overlay.querySelector(".okh-search-status");
    const closeBtn = overlay.querySelector(".okh-search-close");

    let entries        = [];
    let currentResults = [];
    let lastTokens     = [];
    let lastFocus      = null;
    let focusTimer     = null;
    let indexState     = "loading";

    function showLoading() {
      indexState = "loading";
      list.innerHTML = '<p class="okh-search-loading">' + okhLocaleText("Loading search index…", "Chargement de l’index de recherche…") + '</p>';
      status.textContent = okhLocaleText("Loading search index…", "Chargement de l’index de recherche…");
    }

    function setLoadError(error) {
      indexState = "error";
      list.innerHTML =
        '<div class="okh-search-noresults okh-search-noresults--error">' +
          "<p>" + okhLocaleText("Search could not load the index.", "La recherche n’a pas pu charger l’index.") + "</p>" +
          '<button type="button" class="okh-search-retry">' + okhLocaleText("Retry search index", "Réessayer de charger l’index") + '</button>' +
        "</div>";
      status.textContent = okhLocaleText("Search index failed to load.", "Impossible de charger l’index de recherche.");
      console.warn("[okh-search] overlay index load failed:", error);
      list.querySelector(".okh-search-retry").addEventListener("click", () => {
        showLoading();
        loadIndex(true).then((d) => {
          entries = d;
          indexState = "ready";
          render();
        }).catch(setLoadError);
      });
    }

    function focusableInPanel() {
      return Array.from(overlay.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), [tabindex]:not([tabindex="-1"])'
      )).filter((el) => el.offsetParent !== null || el === input);
    }

    function open(opener) {
      if (overlay.dataset.open === "true") return;
      if (document.querySelector('dialog[data-transition-dialog][open]')) return;
      // Pointer activation does not focus buttons in every browser.
      lastFocus = opener || document.activeElement;
      overlay.dataset.open = "true";
      document.documentElement.style.overflow = "hidden";
      showLoading();
      loadIndex().then((d) => {
        entries = d;
        indexState = "ready";
        render();
      }).catch(setLoadError);
      focusTimer = setTimeout(() => {
        if (overlay.dataset.open === "true") input.focus();
      }, 30);
    }
    function close() {
      clearTimeout(focusTimer);
      overlay.dataset.open = "false";
      document.documentElement.style.overflow = "";
      if (lastFocus && typeof lastFocus.focus === "function") {
        try { lastFocus.focus(); } catch (e) { /* ignore */ }
      }
      lastFocus = null;
    }
    function renderEmpty() {
      overlay.querySelector(".okh-search-footer a").href = "/search/";
      list.innerHTML = emptyStateHtml();
      status.textContent = okhLocaleText("Search ready. Enter a term or choose a suggested search.", "La recherche est prête. Saisissez un terme ou choisissez une suggestion.");
      list.querySelectorAll("button[data-q]").forEach((btn) => {
        btn.addEventListener("click", () => {
          input.value = btn.getAttribute("data-q") || "";
          render();
          input.focus();
        });
      });
    }
    function render() {
      const q = input.value.trim();
      overlay.querySelector(".okh-search-footer a").href = "/search/" + (q ? "?q=" + encodeURIComponent(q) : "");
      if (indexState !== "ready") return;
      if (!entries.length) {
        list.innerHTML = '<p class="okh-search-empty">' + okhLocaleText("No indexed pages are available.", "Aucune page indexée n’est disponible.") + '</p>';
        status.textContent = okhLocaleText("No indexed pages are available.", "Aucune page indexée n’est disponible.");
        return;
      }
      if (!q) { renderEmpty(); currentResults = []; lastTokens = []; return; }
      lastTokens     = tokenize(q);
      currentResults = search(entries, q, 12);
      if (!currentResults.length) {
        list.innerHTML =
          '<div class="okh-search-noresults"><p>' + okhLocaleText("No matches for ", "Aucun résultat pour ") + '<strong>' +
          escapeHtml(q) + "</strong>.</p><p>" + okhLocaleText("Try ", "Essayez ") + searchCopy().suggestions.map(escapeHtml).join(", ") + ".</p></div>";
        status.textContent = okhLocaleText("No search results for ", "Aucun résultat de recherche pour ") + q + ".";
        return;
      }
      list.innerHTML = currentResults.map((r) => (
        '<div role="listitem"><a class="okh-search-result" href="' +
          escapeHtml(r.entry.url) + '">' + renderResultHtml(r, lastTokens) +
        "</a></div>"
      )).join("");
      markFirstResult(list);
      status.textContent = currentResults.length +
        (currentResults.length === 1
          ? okhLocaleText(" result found for ", " résultat trouvé pour ")
          : okhLocaleText(" results found for ", " résultats trouvés pour ")) + q + ".";
    }
    input.addEventListener("input", render);
    initResultNavigation(input, list);
    closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", (ev) => { if (ev.target === overlay) close(); });

    // Focus trap — keep Tab inside the panel while it's open
    overlay.addEventListener("keydown", (ev) => {
      if (ev.key !== "Tab" || overlay.dataset.open !== "true") return;
      const focusables = focusableInPanel();
      if (!focusables.length) { ev.preventDefault(); input.focus(); return; }
      const first  = focusables[0];
      const last   = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (ev.shiftKey) {
        if (active === first || !overlay.contains(active)) { ev.preventDefault(); last.focus(); }
      } else {
        if (active === last) { ev.preventDefault(); first.focus(); }
      }
    });

    document.addEventListener("keydown", (ev) => {
      if (document.querySelector('dialog[data-transition-dialog][open]')) return;
      if (overlay.dataset.open === "true" && ev.key === "Escape") { ev.preventDefault(); close(); return; }
      const isMac    = /Mac|iPod|iPhone|iPad/.test(navigator.platform);
      const trigger  = (isMac && ev.metaKey && ev.key.toLowerCase() === "k") ||
                       (!isMac && ev.ctrlKey && ev.key.toLowerCase() === "k");
      if (trigger) { ev.preventDefault(); open(); return; }
      if (ev.key === "/" && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
        const tag     = (document.activeElement && document.activeElement.tagName || "").toLowerCase();
        const isField = tag === "input" || tag === "textarea" || tag === "select" ||
                        (document.activeElement && document.activeElement.isContentEditable);
        if (!isField) { ev.preventDefault(); open(); }
      }
    });

    injectTrigger(open);
  }

  function injectTrigger(openFn) {
    if (document.querySelector(".okh-search-trigger")) return;
    const isMac    = /Mac|iPod|iPhone|iPad/.test(navigator.platform);
    const shortcut = isMac ? "⌘K" : "Ctrl+K";
    const btn      = document.createElement("button");
    btn.type       = "button";
    btn.className  = "okh-search-trigger";
    btn.setAttribute("aria-label", okhLocaleText("Open search (", "Ouvrir la recherche (") + shortcut + ")");
    btn.innerHTML = (
      '<svg class="okh-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
        'stroke="currentColor" stroke-width="2" aria-hidden="true">' +
        '<circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />' +
      "</svg>" +
      '<span class="okh-search-label">' + okhLocaleText("Search", "Rechercher") + '</span>' +
      '<kbd>' + shortcut + '</kbd>'
    );
    btn.addEventListener("click", (e) => { e.preventDefault(); openFn(btn); });

    // Primary: prepend into .header-controls so search sits left of theme toggle
    const controls = document.querySelector(".header-controls");
    if (controls) {
      controls.insertBefore(btn, controls.firstChild);
      return;
    }
    // Fallbacks for pages without .header-controls
    const toggle = document.querySelector(".nav-toggle");
    if (toggle && toggle.parentNode) { toggle.parentNode.insertBefore(btn, toggle); return; }
    const hdr = document.querySelector(".site-header .container, .site-header");
    if (hdr) { hdr.appendChild(btn); return; }
    document.body.appendChild(btn);
  }

  // ── Dedicated /search/ page ─────────────────────────────────────────────
  function initSearchPage() {
    const input = document.getElementById("search-page-input") || document.querySelector("[data-glee-search-inline-input]");
    const list  = document.getElementById("search-results") || document.querySelector("[data-glee-search-inline-results]");
    const stats = document.getElementById("search-stats") || document.querySelector("[data-glee-search-inline-status]");
    const cats  = document.getElementById("search-categories") || document.querySelector("[data-glee-search-inline-categories]");
    if (!input || !list) return;

    let entries        = [];
    let activeCategory = "all";
    let indexLoadError = null;
    let indexLoaded = false;
    let editingQuery = false;
    const listMarkup = (html) => list.tagName === "UL" ? "<li>" + html + "</li>" : html;

    function setIndexLoadError(error) {
      indexLoadError = error || null;
      if (error) {
        list.innerHTML = listMarkup(
          '<div class="okh-search-noresults okh-search-noresults--error">' +
            "<p>" + okhLocaleText("Search could not load the index.", "La recherche n’a pas pu charger l’index.") + "</p>" +
            "<p>" + okhLocaleText("Check your connection, then try again.", "Vérifiez votre connexion, puis réessayez.") + "</p>" +
            '<button type="button" class="okh-search-retry">' + okhLocaleText("Retry search index", "Réessayer de charger l’index") + '</button>' +
          "</div>");
        list.querySelector(".okh-search-retry").addEventListener("click", () => initialize(true));
        if (stats) stats.textContent = okhLocaleText("Search index failed to load.", "Impossible de charger l’index de recherche.");
        return true;
      }
      return false;
    }

    function readQueryFromURL() {
      const params = new URL(window.location.href).searchParams;
      return {
        q: params.get("q") || "",
        category: params.get("cat") || "all",
      };
    }
    function writeQueryToURL(q, category, replace) {
      const url = new URL(window.location.href);
      if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
      if (category && category !== "all") url.searchParams.set("cat", category);
      else url.searchParams.delete("cat");
      const method = replace ? "replaceState" : "pushState";
      if (url.toString() !== window.location.href) window.history[method]({}, "", url.toString());
    }

    function normalizeCategory(category) {
      if (!cats || category === "all") return "all";
      return Array.from(cats.querySelectorAll("button")).some((button) =>
        button.getAttribute("data-cat") === category
      ) ? category : "all";
    }

    function syncCategoryButtons() {
      if (!cats) return;
      cats.querySelectorAll("button").forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          (button.getAttribute("data-cat") || "all") === activeCategory ? "true" : "false"
        )
      );
    }

    function render(options) {
      const historyMode = options && options.historyMode === "push" ? "push" : "replace";
      const q = input.value.trim();
      writeQueryToURL(q, activeCategory, historyMode === "replace");
      if (indexLoadError) {
        setIndexLoadError(indexLoadError);
        return;
      }
      if (!indexLoaded) {
        list.innerHTML = "";
        if (stats) stats.textContent = okhLocaleText("Loading index…", "Chargement de l’index…");
        return;
      }
      if (!entries.length) {
        list.innerHTML = listMarkup('<p class="okh-search-empty">' + okhLocaleText("No indexed pages are available.", "Aucune page indexée n’est disponible.") + '</p>');
        if (stats) stats.textContent = okhLocaleText("No indexed pages are available.", "Aucune page indexée n’est disponible.");
        return;
      }
      if (!q && !isGlee()) {
        list.innerHTML = "";
        if (stats) stats.textContent = entries.length
          ? "Type to search " + entries.length + " indexed entries." + scopeNotice
          : okhLocaleText("Loading index…", "Chargement de l’index…") + scopeNotice;
        return;
      }
      const tokens = tokenize(q);
      const results = q ? search(entries, q, { limit: 60, category: activeCategory })
        : entries.filter((entry) => activeCategory === "all" || entry.category === activeCategory)
          .slice(0, 60).map((entry) => ({ entry }));
      if (!results.length) {
        list.innerHTML = listMarkup(
          '<div class="search-empty-state"><p>' + okhLocaleText("No matches for ", "Aucun résultat pour ") + '<strong>' +
          escapeHtml(q) + "</strong>" +
          (activeCategory !== "all" ? ' in <em>' + escapeHtml(activeCategory) + "</em>" : "") +
          ".</p></div>");
        if (stats) stats.textContent = okhLocaleText("0 results", "0 résultat");
        return;
      }
      if (stats) stats.textContent =
        results.length + " result" + (results.length === 1 ? "" : "s") +
        (q ? " for \u201c" + q + "\u201d" : ". Type to search the catalog.") + scopeNotice;
      list.innerHTML = results.map((r) => (
        listMarkup('<a class="okh-search-result" href="' + escapeHtml(r.entry.url) + '">' +
          renderResultHtml(r, tokens) +
        "</a>")
      )).join("");
      markFirstResult(list);
    }

    function buildCategoryChips() {
      if (!cats) return;
      const counts = {};
      for (const e of entries) {
        const c = e.category || "Page";
        counts[c] = (counts[c] || 0) + 1;
      }
      const ordered = ["all"].concat(Object.keys(counts).sort());
      cats.innerHTML = ordered.map((c) => {
        const label   = c === "all" ? "All (" + entries.length + ")" : c + " (" + counts[c] + ")";
        const pressed = c === activeCategory ? "true" : "false";
        return '<button type="button" data-cat="' + escapeHtml(c) +
               '" aria-pressed="' + pressed + '">' + escapeHtml(label) + "</button>";
      }).join("");
      cats.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          editingQuery = false;
          activeCategory = b.getAttribute("data-cat") || "all";
          cats.querySelectorAll("button").forEach((x) =>
            x.setAttribute("aria-pressed", x === b ? "true" : "false")
          );
          render({ historyMode: "push" });
        });
      });
    }

    function syncFromURL(skipFocus) {
      editingQuery = false;
      const urlState = readQueryFromURL();
      input.value = urlState.q;
      activeCategory = normalizeCategory(urlState.category || "all");
      syncCategoryButtons();
      if (!skipFocus) input.focus();
      render({ historyMode: "replace" });
    }

    window.addEventListener("popstate", () => {
      syncFromURL(true);
    });

    function initialize(retry) {
      indexLoadError = null;
      indexLoaded = false;
      list.innerHTML = "";
      if (stats) stats.textContent = okhLocaleText("Loading index…", "Chargement de l’index…");
      loadIndex(retry).then((d) => {
        indexLoadError = null;
        indexLoaded = true;
        entries = d;
        const initial = readQueryFromURL();
        input.value = initial.q;
        activeCategory = initial.category || "all";
        buildCategoryChips();
        activeCategory = normalizeCategory(activeCategory);
        syncCategoryButtons();
        input.focus();
        render();
      }).catch(setIndexLoadError);
    }
    input.value = readQueryFromURL().q;
    initialize(false);

    input.addEventListener("input", () => {
      render({ historyMode: editingQuery ? "replace" : "push" });
      editingQuery = true;
    });
    input.addEventListener("change", () => { editingQuery = false; });
    initResultNavigation(input, list);
    const form = input.closest("form");
    if (form) form.addEventListener("submit", (event) => { event.preventDefault(); render(); });
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────
  function loadBrandModule() {
    const body = document.body;
    const moduleUrl = body.classList.contains("glee-main")
      ? "/assets/js/glee-site-enhancements.js?v=ebfa263e"
      : body.classList.contains("askjamie-main")
        ? "/assets/js/askjamie-analytics.js"
        : null;
    if (moduleUrl) {
      import(moduleUrl).catch((error) => {
        console.warn("[shared-runtime] optional brand module failed to load:", error);
      });
    }
  }

  function start() {
    loadBrandModule();
    if ((document.getElementById("search-page-input") && document.getElementById("search-results")) || document.querySelector("[data-glee-search-inline-input]")) {
      initSearchPage();
      initOverlay(); // search button still works on the search page itself
    } else {
      initOverlay();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
}());

// AskJamie transition note: one automatic arrival notice per tab session.
// The HTML notices and update link remain usable without JavaScript/storage.
(function () {
  function startTransitionNote() {
    if (!document.body.classList.contains("askjamie-main")) return;
    const dialog = document.querySelector("dialog[data-transition-dialog]");
    if (!dialog || typeof dialog.showModal !== "function") return;
    const key = "askjamie-gpt-transition-2026-09";
    const triggers = document.querySelectorAll("[data-transition-open]");
    let returnFocus = null;

    function remember() {
      try { sessionStorage.setItem(key, "acknowledged"); }
      catch (_) { /* Storage is optional; the current dialog still closes. */ }
    }
    function open(trigger) {
      if (dialog.open || document.querySelector('dialog[open], .okh-search-overlay[data-open="true"]')) return;
      returnFocus = trigger || triggers[0] || document.activeElement;
      dialog.showModal();
      document.body.classList.add("capability-transition-open");
    }
    triggers.forEach((trigger) => {
      trigger.hidden = false;
      trigger.addEventListener("click", () => open(trigger));
    });
    dialog.querySelector("[data-transition-dismiss]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const controls = Array.from(dialog.querySelectorAll('a[href], button:not([disabled])'));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
    dialog.querySelectorAll("a[href]").forEach((link) => {
      link.addEventListener("click", () => { remember(); dialog.close(); });
    });
    dialog.addEventListener("close", () => {
      remember();
      document.body.classList.remove("capability-transition-open");
      if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
    });
    let acknowledged = false;
    try { acknowledged = sessionStorage.getItem(key) === "acknowledged"; }
    catch (_) { /* Browsing must work when storage is unavailable. */ }
    if (!acknowledged) open();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startTransitionNote);
  } else {
    startTransitionNote();
  }
}());

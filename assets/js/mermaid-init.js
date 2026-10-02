// Mermaid initialization — shared module across overkillhill.com,
// glee-fully.tools, and askjamie.bot.
// Relies on YAML front-matter in each diagram for theme/look (theme: neutral, look: neo).
// initialize() intentionally omits themeVariables to avoid overriding the YAML config.
//
// Performance: on pages with many diagrams (e.g. the v0.3 article) we use
// IntersectionObserver to defer rendering until each diagram approaches the
// viewport. Falls back to immediate render where the API is unavailable.

// Most diagrams are informational and do not need Mermaid's click handling.
// The two v2 heat pages opt in with data-mermaid-security="loose" because
// their diagrams contain curated outbound click directives.
const usesClickableLinks =
  document.body?.dataset.mermaidSecurity === "loose";

const ALLOWED_CLICK_TARGETS = [
  { origin: "https://mermaidchart.cello.so", pathname: "/UhVlNtC2MlS" },
  { origin: "https://replit.com", pathname: "/refer/overkillhillp3" },
  { origin: "https://overkillhill.com", pathname: "/writings/first-diagram-is-a-liar/" },
  { origin: "https://overkillhill.com", pathname: "/" },
  { origin: "https://www.linkedin.com", pathname: "/company/overkillhillp3" },
  { origin: "https://ko-fi.com", pathname: "/T6T71HCY6A" },
];

function isAllowedClickTarget(value) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      ALLOWED_CLICK_TARGETS.some(
        ({ origin, pathname }) =>
          url.origin === origin &&
          url.pathname === pathname
      )
    );
  } catch {
    return false;
  }
}

function sanitizeClickableLinks(source) {
  // Mermaid's click syntax is line-oriented: click NODE "URL" "tooltip".
  // Remove an entire directive when its URL is not an exact allowlist match.
  return source.replace(
    /^(\s*click\s+\S+\s+)"([^"]+)"(?:\s+"[^"]*")?\s*$/gm,
    (line, prefix, target) =>
      isAllowedClickTarget(target) ? line : "%% Removed disallowed click target"
  );
}

// Diagram authors often bring a light, editor-specific palette in YAML
// front-matter or classDef rules. Resolve those literals against the active
// site's semantic tokens before Mermaid parses the source so diagrams follow
// the light/dark theme without changing the source examples shown to readers.
function resolveThemeColors(source) {
  const styles = getComputedStyle(document.documentElement);
  const token = (name, fallback) =>
    styles.getPropertyValue(name).trim() || fallback;
  const colors = {
    "#111827": token("--mermaid-primary-color", "#111827"),
    "#14213D": token("--mermaid-primary-text-color", "#e5e7eb"),
    "#102A43": token("--mermaid-primary-text-color", "#e5e7eb"),
    "#325DCC": token("--mermaid-primary-border-color", "#c46a2c"),
    "#4A5CCC": token("--mermaid-primary-border-color", "#c46a2c"),
    "#64748B": token("--mermaid-line-color", "#c46a2c"),
    "#6B7280": token("--color-muted", "#9ca3af"),
    "#94A3B8": token("--color-muted", "#9ca3af"),
    "#CBD5E0": token("--mermaid-primary-border-color", "#c46a2c"),
    "#181f26": token("--mermaid-secondary-color", "#181f26"),
    "#FFF7E8": token("--mermaid-secondary-color", "#181f26"),
    "#EEFDF3": token("--mermaid-tertiary-color", "#1c3a34"),
    "#FFFFFF": token("--mermaid-edge-label-bg", "#181f26"),
  };
  const pagePalette = {
    // v1 heat-a diagrams: preserve each semantic role while switching the
    // editor palettes to the active site theme.
    "#FFF4CC": "--mermaid-secondary-color",
    "#D6A700": "--mermaid-line-color",
    "#4A3B00": "--mermaid-primary-text-color",
    "#EAF3FF": "--mermaid-primary-color",
    "#4A78A8": "--mermaid-primary-border-color",
    "#142033": "--mermaid-primary-text-color",
    "#F6F0FF": "--mermaid-secondary-color",
    "#7E57C2": "--mermaid-primary-border-color",
    "#221533": "--mermaid-primary-text-color",
    "#FFF1F2": "--mermaid-secondary-color",
    "#C05666": "--mermaid-line-color",
    "#5A1822": "--mermaid-primary-text-color",
    "#EEF9F0": "--mermaid-tertiary-color",
    "#3E8E5A": "--mermaid-line-color",
    "#12331D": "--mermaid-primary-text-color",
    "#EAF7F1": "--mermaid-tertiary-color",
    "#2F855A": "--mermaid-line-color",
    "#F3F4F6": "--mermaid-primary-color",
    "#334155": "--mermaid-primary-text-color",
    "#EAF2FF": "--mermaid-primary-color",
    "#F3E8FF": "--mermaid-secondary-color",
    "#7C3AED": "--mermaid-primary-border-color",
    "#3B0764": "--mermaid-primary-text-color",
    "#DC2626": "--mermaid-line-color",
    "#7F1D1D": "--mermaid-primary-text-color",
    "#ECFDF5": "--mermaid-tertiary-color",
    "#059669": "--mermaid-line-color",
    "#064E3B": "--mermaid-primary-text-color",
    "#F8FAFC": "--mermaid-primary-color",
    "#4B5563": "--color-muted",
    "#FFF7ED": "--mermaid-secondary-color",
    "#DD6B20": "--mermaid-line-color",
    "#5B3415": "--mermaid-primary-text-color",
    "#4A90D9": "--mermaid-primary-border-color",
    "#1A5FA8": "--mermaid-primary-border-color",
    "#F0F4F8": "--mermaid-primary-color",
    "#9EB3C2": "--mermaid-primary-border-color",
    "#2D3748": "--mermaid-primary-text-color",
    "#FFF8E7": "--mermaid-secondary-color",
    "#D4A017": "--mermaid-line-color",
    "#4A3300": "--mermaid-primary-text-color",
    "#FDE8E8": "--mermaid-secondary-color",
    "#C0392B": "--mermaid-line-color",
    "#7B1A1A": "--mermaid-primary-text-color",
    "#E8F5E9": "--mermaid-tertiary-color",
    "#2E7D32": "--mermaid-line-color",
    "#1A3D1E": "--mermaid-primary-text-color",
    "#6C5CE7": "--mermaid-primary-border-color",
    "#4A3AB5": "--mermaid-line-color",
    "#00B894": "--mermaid-tertiary-color",
    "#007A61": "--mermaid-line-color",
    "#EBF4FF": "--mermaid-primary-color",
    "#1A3D5C": "--mermaid-primary-text-color",
    "#FFFBF0": "--mermaid-secondary-color",
    "#F5F5F5": "--mermaid-primary-color",
    "#F0FFF4": "--mermaid-tertiary-color",
    "#F3F0FF": "--mermaid-secondary-color",
    "#2D1B6E": "--mermaid-primary-text-color",
    "#2B6CB0": "--mermaid-primary-border-color",
    "#0B2A4A": "--mermaid-primary-text-color",
    "#EFE9FF": "--mermaid-secondary-color",
    "#6B46C1": "--mermaid-primary-border-color",
    "#2A124D": "--mermaid-primary-text-color",
    "#5A1010": "--mermaid-primary-text-color",
    "#E9FBEF": "--mermaid-tertiary-color",
    "#0F3D25": "--mermaid-primary-text-color",
    "#E3F2FD": "--mermaid-primary-color",
    "#1565C0": "--mermaid-primary-border-color",
    "#1B5E20": "--mermaid-primary-text-color",
    "#FFF3E0": "--mermaid-secondary-color",
    "#EF6C00": "--mermaid-line-color",
    "#E65100": "--mermaid-primary-text-color",
    "#FFEBEE": "--mermaid-secondary-color",
    "#7B1111": "--mermaid-primary-text-color",
    "#EDE7F6": "--mermaid-secondary-color",
    "#5E35B1": "--mermaid-primary-border-color",
    "#311B92": "--mermaid-primary-text-color",
    "#C46A2C": "--mermaid-primary-border-color",
    "#E6A03C": "--mermaid-line-color",
    "#0F172A": "--mermaid-primary-text-color",
    "#1C3A34": "--mermaid-tertiary-color",
    "#5B3A27": "--mermaid-secondary-color",
    "#676A2C": "--mermaid-secondary-color",
    "#2A2320": "--mermaid-secondary-color",
    // v2 heat-a editor palettes.
    "#13213C": "--mermaid-primary-text-color",
    "#132238": "--mermaid-primary-text-color",
    "#14532D": "--mermaid-primary-text-color",
    "#16A34A": "--mermaid-line-color",
    "#1E3A8A": "--mermaid-primary-text-color",
    "#2563EB": "--mermaid-primary-border-color",
    "#31135E": "--mermaid-primary-text-color",
    "#312E81": "--mermaid-primary-text-color",
    "#3563D9": "--mermaid-primary-border-color",
    "#374151": "--color-muted",
    "#4A1020": "--mermaid-primary-text-color",
    "#4C3510": "--mermaid-primary-text-color",
    "#5B6676": "--color-muted",
    "#6D28D9": "--mermaid-primary-border-color",
    "#78350F": "--mermaid-primary-text-color",
    "#C53030": "--mermaid-line-color",
    "#CBD5E1": "--color-muted",
    "#D7A322": "--mermaid-line-color",
    "#D97706": "--mermaid-line-color",
    "#E8F3FF": "--mermaid-primary-color",
    "#EFF6FF": "--mermaid-primary-color",
    "#F3FFF6": "--mermaid-tertiary-color",
    "#F4ECFF": "--mermaid-secondary-color",
    "#F5F3FF": "--mermaid-secondary-color",
    "#F8FBFF": "--mermaid-primary-color",
    "#F9FFF9": "--mermaid-tertiary-color",
    "#FAFAFA": "--mermaid-primary-color",
    "#FAFBFD": "--mermaid-primary-color",
    "#FFF8E8": "--mermaid-secondary-color",
    "#FFF8FB": "--mermaid-secondary-color",
    "#FFFCF2": "--mermaid-secondary-color",
    // v2 heat-b editor palettes.
    "#0A1628": "--mermaid-primary-text-color",
    "#1A2060": "--mermaid-primary-text-color",
    "#1E3A5F": "--mermaid-primary-text-color",
    "#3E6EA8": "--mermaid-primary-border-color",
    "#627184": "--mermaid-line-color",
    "#A06E28": "--mermaid-line-color",
    "#A8C1DD": "--mermaid-primary-border-color",
    "#C9D2DE": "--mermaid-primary-border-color",
    "#D1FAE5": "--mermaid-tertiary-color",
    "#D9C7A5": "--mermaid-line-color",
    "#E5E7EB": "--mermaid-primary-text-color",
    "#F0F4FF": "--mermaid-primary-color",
    "#F0FDF4": "--mermaid-tertiary-color",
    "#F1F5F9": "--mermaid-primary-color",
    "#F9FAFB": "--mermaid-primary-color",
    "#FAFBFC": "--mermaid-primary-color",
    "#FFFCF7": "--mermaid-secondary-color",
  };
  return source.replace(/#[0-9A-Fa-f]{6}/g, (value) => {
    const normalized = value.toUpperCase();
    const direct = colors[normalized] || colors[value];
    if (direct) return direct;
    const tokenName = pagePalette[normalized];
    return tokenName ? token(tokenName, value) : value;
  });
}

const diagrams = Array.from(document.querySelectorAll(".mermaid"));
let mermaidPromise = null;

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("/assets/vendor/mermaid/mermaid.esm.min.mjs").then(
      ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: usesClickableLinks ? "loose" : "strict",
          flowchart: { curve: "basis", nodeSpacing: 55, rankSpacing: 65, htmlLabels: true },
        });
        return mermaid;
      }
    );
  }
  return mermaidPromise;
}

function addAccessibleAlternative(node, index) {
  // Mermaid replaces the source element with an SVG. Preserve a concise,
  // screen-reader-readable alternative before that happens. Authors can
  // provide a better label with data-diagram-label; otherwise the node labels
  // and source type are useful text for users who cannot see the rendering.
  if (node.getAttribute("aria-label") || node.getAttribute("aria-describedby")) {
    return;
  }
  const source = node.textContent.replace(/\s+/g, " ").trim();
  const labels = [...source.matchAll(/["']([^"']{2,120})["']/g)]
    .map((match) => match[1].replace(/\\n/g, " "))
    .filter((label, position, all) => all.indexOf(label) === position)
    .slice(0, 24);
  const fallback = labels.length > 0
    ? labels.join("; ")
    : "Diagram source is available in the page markup.";
  const label = node.dataset.diagramLabel
    || `Diagram ${index + 1}: ${fallback}`;
  node.setAttribute("role", "img");
  node.setAttribute("aria-label", label);
}

function enhanceMermaidLinks(node) {
  node.querySelectorAll("svg a").forEach((link) => {
    const href = link.getAttribute("href") || link.getAttribute("xlink:href") ||
      link.getAttributeNS("http://www.w3.org/1999/xlink", "href");
    if (href) link.setAttribute("href", href);
    if (link.getAttribute("target") === "_blank") link.setAttribute("rel", "noopener noreferrer");
    const label = link.getAttribute("aria-label") || link.querySelector("[title]")?.getAttribute("title") ||
      link.querySelector("title")?.textContent?.trim();
    if (label) link.setAttribute("aria-label", label);
    if (href) {
      // Interactive SVG links must remain reachable in the keyboard order.
      node.setAttribute("role", "group");
      node.querySelector("svg")?.setAttribute("role", "group");
      link.setAttribute("role", "link");
      link.setAttribute("tabindex", "0");
    } else link.setAttribute("tabindex", "-1");
  });
}

function showUniverseRenderFailure(node) {
  const status = node
    .closest(".askjamie-mermaid-shell")
    ?.querySelector("[data-mermaid-failure-status]");
  if (!status) return;
  status.hidden = false;
  status.textContent =
    "The diagram could not be displayed. Use the links below to browse these pages.";
}

function renderOne(node) {
  if (node.dataset.mermaidRendered === "1") return;
  node.dataset.mermaidRendered = "1";
  addAccessibleAlternative(node, diagrams.indexOf(node));
  if (usesClickableLinks) {
    node.textContent = sanitizeClickableLinks(node.textContent);
  }
  node.textContent = resolveThemeColors(node.textContent);
  loadMermaid()
    .then((mermaid) => mermaid.run({ nodes: [node] }))
    .then(() => enhanceMermaidLinks(node))
    .catch((err) => {
      showUniverseRenderFailure(node);
      console.warn("[mermaid-init] render error:", err);
    });
}

function scheduleRender(node) {
  if (typeof requestIdleCallback === "function") {
    requestIdleCallback(() => renderOne(node), { timeout: 2500 });
  } else {
    setTimeout(() => renderOne(node), 1500);
  }
}

// If only a few diagrams, or no IntersectionObserver, render immediately.
if (diagrams.length <= 2 || typeof IntersectionObserver === "undefined") {
  diagrams.forEach(scheduleRender);
} else {
  const isSmallScreen = typeof matchMedia === "function" && matchMedia("(max-width: 768px)").matches;
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          scheduleRender(entry.target);
          io.unobserve(entry.target);
        }
      });
    },
    { rootMargin: isSmallScreen ? "0px" : "400px 0px", threshold: 0.01 }
  );
  diagrams.forEach((node) => io.observe(node));
}

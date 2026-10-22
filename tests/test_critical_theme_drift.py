import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-critical-theme-drift.py"
spec = importlib.util.spec_from_file_location("critical_theme_drift", SCRIPT)
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)


def test_current_critical_theme_contract_is_clean():
    assert drift.check_contracts(ROOT) == []


def test_shared_property_drift_is_reported(tmp_path):
    (tmp_path / "assets/css").mkdir(parents=True)
    critical = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    theme = theme.replace(
        ".askjamie-main .btn-quiet {\n  border-color: #d7d7d7;\n  color: #4b5563;",
        ".askjamie-main .btn-quiet {\n  border-color: #d7d7d7;\n  color: #7b5563;",
        1,
    )
    (tmp_path / "assets/css/critical-hero.css").write_text(critical, encoding="utf-8")
    (tmp_path / "assets/css/theme.css").write_text(theme, encoding="utf-8")

    findings = drift.check_contracts(tmp_path)

    assert any("quiet action: color drifted" in finding for finding in findings)


def test_missing_shared_selector_is_reported(tmp_path):
    (tmp_path / "assets/css").mkdir(parents=True)
    critical = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    theme = theme.replace(".askjamie-main .btn-quiet", ".askjamie-main .btn-quiet-renamed")
    (tmp_path / "assets/css/critical-hero.css").write_text(critical, encoding="utf-8")
    (tmp_path / "assets/css/theme.css").write_text(theme, encoding="utf-8")

    findings = drift.check_contracts(tmp_path)

    assert any("quiet action: shared selector missing" in finding for finding in findings)


def test_brandguard_mobile_breadcrumb_matches_critical_geometry_fallback():
    selector = ".askjamie-brandguard-page .askjamie-logo--crumb img"
    crumb_selector = ".askjamie-brandguard-page .askjamie-logo--crumb"
    critical = drift.parse_rules(
        (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    )
    theme = drift.parse_rules(
        (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    )

    assert drift._last_value(critical, selector, "max-width") == "7.5rem"
    assert drift._last_value(theme, selector, "max-width") == "7.5rem"
    assert drift._last_value(critical, crumb_selector, "margin-bottom") == "0"
    assert drift._last_value(theme, crumb_selector, "margin-bottom") == "0"
    label_selector = ".askjamie-brandguard-page .askjamie-breadcrumb .breadcrumb-label"
    assert drift._last_value(critical, label_selector, "min-width") == "15.25rem"
    assert drift._last_value(theme, label_selector, "min-width") == "15.25rem"


def test_brandguard_breadcrumb_typography_and_vertical_rhythm_match():
    selector = ".askjamie-main .askjamie-breadcrumb"
    properties = {
        "font-size": "0.95rem",
        "line-height": "1.6",
        "letter-spacing": "0.01em",
        "margin-bottom": "0.75rem",
    }
    critical = drift.parse_rules(
        (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    )
    theme = drift.parse_rules(
        (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    )

    for property_name, expected in properties.items():
        assert drift._last_value(critical, selector, property_name) == expected
        assert drift._last_value(theme, selector, property_name) == expected


def test_brandguard_mobile_case_study_controls_reserve_44px_targets():
    theme_source = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    theme = drift.parse_rules(theme_source)
    selectors = (
        ".askjamie-main[class*=\"brandguard\"] main a.btn[href]",
        ".askjamie-main.askjamie-brandguard-page main a.brandguard-case-card[href]",
    )

    assert "@media (max-width: 767px)" in theme_source
    for selector in selectors:
        assert drift._last_value(theme, selector, "min-inline-size") == "44px"
        assert drift._last_value(theme, selector, "min-block-size") == "44px"


def test_brandguard_tablet_critical_shell_matches_theme_layout_contracts():
    import re

    critical_source = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    critical = drift.parse_rules(
        critical_source
    )
    theme = drift.parse_rules(
        (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    )
    tagline_selector = ".askjamie-brandguard-page .askjamie-hero-copy .hero-tagline"
    submenu_selector = ".askjamie-main .primary-nav .has-submenu > .submenu"
    submenu_item_selector = ".askjamie-main .primary-nav .has-submenu"
    submenu_open_selector = (
        ".askjamie-main .primary-nav .has-submenu:hover > .submenu"
    )
    theme_submenu_selector = ".primary-nav .has-submenu > .submenu"
    theme_submenu_open_selector = ".primary-nav .has-submenu:hover > .submenu"
    toggle_selector = ".askjamie-main .glee-color-toggle"
    toggle_properties = {
        "width": "2.25rem",
        "height": "2.25rem",
        "min-width": "44px",
        "min-height": "44px",
        "padding": "0",
        "flex-shrink": "0",
    }

    assert drift._last_value(critical, tagline_selector, "max-width") == "32rem"
    assert drift._last_value(theme, ".hero-tagline", "max-width") == "32rem"
    assert drift._last_value(critical, submenu_item_selector, "position") == "relative"
    assert any(
        declarations.get("position") == "relative"
        for declarations in theme.get(".primary-nav .has-submenu", [])
    )
    assert drift._last_value(critical, submenu_selector, "position") == "absolute"
    assert drift._last_value(critical, submenu_selector, "display") == "none"
    assert any(
        declarations.get("position") == "absolute"
        and declarations.get("display") == "none"
        for declarations in theme.get(theme_submenu_selector, [])
    )
    assert drift._last_value(critical, submenu_open_selector, "display") == "block"
    assert any(
        declarations.get("display") == "block"
        for declarations in theme.get(theme_submenu_open_selector, [])
    )
    for property_name, expected in toggle_properties.items():
        assert drift._last_value(critical, toggle_selector, property_name) == expected
        assert drift._last_value(theme, ".glee-color-toggle", property_name) == expected

    desktop_nav = re.search(
        r"@media \(min-width: 769px\)\s*\{(.*?)(?=\n@media|\Z)",
        critical_source,
        re.DOTALL,
    )
    assert desktop_nav
    assert re.search(
        r"\.askjamie-main \.primary-nav ul\s*\{[^}]*flex-wrap:\s*wrap;",
        desktop_nav.group(1),
        re.DOTALL,
    )


def test_brandguard_text_line_comparison_detects_reflow_with_unchanged_boxes():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("function compareBrandGuardTextLineReflow(")
    end = source.index("\nconst BRANDGUARD_NARROW_CONTENT_SELECTORS", start)
    helper = source[start:end]
    node_test = f"""
const vm = require('node:vm');
const selectors = [
  '.askjamie-main .askjamie-breadcrumb',
  '.askjamie-main .askjamie-hero-copy h1',
  '.askjamie-main .askjamie-hero-copy .hero-subtitle',
  '.askjamie-main .askjamie-hero-copy .hero-tagline'
];
const context = vm.createContext({{ BRANDGUARD_TEXT_LINE_SELECTORS: selectors }});
vm.runInContext({json.dumps(helper)}, context);
const before = Object.fromEntries(selectors.map(selector => [
  selector, {{ lines: [{{ text: 'unchanged copy' }}] }}
]));
const after = Object.fromEntries(selectors.map(selector => [
  selector, {{ lines: [{{ text: 'unchanged copy' }}] }}
]));
const heading = selectors[1];
before[heading] = {{ lines: [{{ text: 'A reserved heading' }},
                              {{ text: 'that wrapped here' }}] }};
after[heading] = {{ lines: [{{ text: 'A reserved' }},
                             {{ text: 'heading that wrapped here' }}] }};
const result = vm.runInContext(
  'compareBrandGuardTextLineReflow(' + JSON.stringify(before) + ', ' +
  JSON.stringify(after) + ')',
  context
);
console.log(JSON.stringify({{
  boxGeometryBefore: {{ height: 96, width: 288 }},
  boxGeometryAfter: {{ height: 96, width: 288 }},
  result
}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    output = json.loads(result.stdout)

    assert output["boxGeometryBefore"] == output["boxGeometryAfter"]
    assert len(output["result"]["evidence"]["reflows"]) == 1
    assert output["result"]["evidence"]["reflows"][0]["selector"] == (
        ".askjamie-main .askjamie-hero-copy h1"
    )
    assert len(output["result"]["errors"]) == 1
    assert "BRANDGUARD HERO TEXT-LINE REFLOW" in output["result"]["errors"][0]
    assert set(output["result"]["evidence"]) == {
        "comparison",
        "before",
        "after",
        "reflows",
    }


def test_brandguard_390px_font_tracking_keeps_fallback_line_counts():
    critical = drift.parse_rules(
        (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    )
    theme = drift.parse_rules(
        (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    )
    tracked_blocks = {
        ".askjamie-main.askjamie-brandguard-page .capability-transition > p:nth-of-type(2)": "-0.005em",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1": "-0.03em",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle": "-0.04em",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-tagline": "-0.04em",
    }

    for selector, expected in tracked_blocks.items():
        assert drift._last_value(critical, selector, "letter-spacing") == expected
        assert drift._last_value(theme, selector, "letter-spacing") == expected


def test_brandguard_320px_rules_wrap_breadcrumbs_within_the_viewport():
    import re

    css_sources = (
        (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8"),
        (ROOT / "assets/css/theme.css").read_text(encoding="utf-8"),
    )
    media_query = "@media (max-width: 340px)"

    for source in css_sources:
        match = re.search(
            rf"{re.escape(media_query)}\s*\{{(.*?)(?=\n@media|\Z)",
            source,
            re.DOTALL,
        )
        assert match
        narrow_rules = match.group(1)
        assert "flex: 1 1 100%;" in narrow_rules
        assert "min-width: 0;" in narrow_rules
        assert "white-space: normal;" in narrow_rules
        assert "overflow-wrap: anywhere;" in narrow_rules
        assert ".askjamie-breadcrumb .breadcrumb-separator" in narrow_rules
        assert ".askjamie-main.askjamie-brandguard-page .askjamie-breadcrumb" in narrow_rules
        assert ".askjamie-main:has(.brandguard-demo-notice)" in narrow_rules
        assert ".askjamie-hero-copy .hero-actions > a" in narrow_rules
        assert "align-content: flex-start;" in narrow_rules
        assert "flex: 0 0 100%;" in narrow_rules
        assert "height: 6.5rem;" in narrow_rules
        assert "min-height: 6.5rem;" in narrow_rules
        assert "line-height: 1.4;" in narrow_rules


def test_brandguard_360px_font_tracking_keeps_fallback_line_counts():
    import re

    critical = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    media_query = "@media (min-width: 350px) and (max-width: 370px)"
    next_media_query = "@media (min-width: 381px) and (max-width: 400px)"
    shared_360_rules = {
        ".askjamie-main.askjamie-brandguard-page .capability-transition h2": {
            "letter-spacing": "-0.05em",
        },
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1": {
            "letter-spacing": "-0.08em",
        },
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle": {
            "font-size": "1rem",
            "letter-spacing": "-1.25px",
        },
    }

    for source in (critical, theme):
        start = source.index(media_query)
        end = source.index(next_media_query, start)
        mobile_360_rules = source[start:end]
        for selector, declarations in shared_360_rules.items():
            match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", mobile_360_rules)
            assert match
            for property_name, value in declarations.items():
                assert re.search(rf"{property_name}:\s*{re.escape(value)};", match.group(1))

    start = theme.index(media_query)
    end = theme.index(next_media_query, start)
    mobile_360_theme_rules = theme[start:end]
    case_study_rules = {
        ".askjamie-main:has(.brandguard-demo-notice) .capability-transition h2": {
            "letter-spacing": "-0.05em",
        },
        ".askjamie-main:has(.brandguard-demo-notice) .askjamie-hero-copy h1": {
            "font-size": "1.75rem",
            "letter-spacing": "0.5px",
        },
        ".askjamie-main:has(.brandguard-demo-notice) .askjamie-hero-copy .hero-subtitle": {
            "font-size": "0.97rem",
            "letter-spacing": "-1.1px",
        },
    }
    for selector, declarations in case_study_rules.items():
        match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", mobile_360_theme_rules)
        assert match
        for property_name, value in declarations.items():
            assert re.search(rf"{property_name}:\s*{re.escape(value)};", match.group(1))


def test_brandguard_case_study_390px_font_tracking_is_scoped_to_case_studies():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")

    assert "@media (min-width: 381px) and (max-width: 400px)" in theme
    assert (
        ".askjamie-main:has(.brandguard-demo-notice) "
        ".askjamie-hero-copy h1 {\n"
        "    font-size: 2.125rem;\n"
        "    letter-spacing: -0.04em;"
    ) in theme
    assert (
        ".askjamie-main:has(.brandguard-demo-notice) "
        ".askjamie-hero-copy .hero-subtitle {\n"
        "    font-size: 1rem;\n"
        "    letter-spacing: -0.04em;"
    ) in theme
    assert (
        ".askjamie-main:has(.brandguard-demo-notice) "
        ".askjamie-hero-copy .hero-tagline {\n"
        "    letter-spacing: -0.04em;"
    ) in theme


def test_responsive_qa_covers_new_brandguard_routes_at_primary_and_edge_widths():
    import json
    import subprocess

    script = r"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = { ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve };
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_NARROW_CONTENT_VIEWPORT ='));
const predicates = source.slice(source.indexOf('    const checkBrandGuardAllRouteFonts ='),
  source.indexOf('    const checkUniverseDiagram ='));
const routes = ['/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
  '/lens-system/okhp3-brandguard/new-case/', '/about/'];
console.log(JSON.stringify(Object.fromEntries(['mobile-390', 'mobile-360', 'mobile-350-edge', 'mobile-370-edge', 'tablet-768'].map(name =>
  [name, routes.map(path => vm.runInNewContext(config + '\n' + predicates + '\ncheckBrandGuardFonts',
    {...runtime, PUBLIC_PATHS: routes, path, vp: {name}}))]))));
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                            capture_output=True, check=True)
    assert json.loads(result.stdout) == {
        'mobile-390': [True, True, True, True, False],
        'mobile-360': [True, True, True, True, False],
        'mobile-350-edge': [True, True, True, True, False],
        'mobile-370-edge': [True, True, True, True, False],
        'tablet-768': [True, True, True, True, False],
    }


def test_responsive_qa_rejects_duplicate_sitemap_routes_and_preserves_unique_routes():
    import json
    import subprocess

    routes = ["/about/", "/contact/"]

    def sitemap_xml(locations):
        return (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
            f"<url><loc>https://askjamie.bot{path}</loc></url>"
            for path in locations
            )
            + "</urlset>"
        )

    node_test = r"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const loader = source.slice(source.indexOf('function loadPublicPaths()'),
  source.indexOf('const PUBLIC_PATHS = loadPublicPaths();'));
const root = '/responsive-qa-fixture';
const sitemapCases = JSON.parse(process.argv[1]);
const routeFiles = new Set(['/about/index.html', '/contact/index.html']
  .map(file => pathApi.resolve(root, file.slice(1))));
let routeCheckCount = 0;
function load(sitemap) {
  const runtime = {
    ROOT: root,
    FORCE_STATIC: true,
    URL,
    execFileSync,
    readFileSync: () => sitemap,
    resolve: pathApi.resolve,
    existsSync: path => {
      routeCheckCount += 1;
      return routeFiles.has(path);
    },
    readdirSync: fs.readdirSync,
    statSync: () => ({ isFile: () => true }),
    relative: pathApi.relative,
    isPagesArtifactPublicHtmlFile: () => true,
    collectPublishedNonBrandGuardHtmlPages: () => []
  };
  return vm.runInNewContext(loader + '\nloadPublicPaths()', runtime);
}
let malformedError = '';
try {
  load(sitemapCases.malformed);
} catch (error) {
  malformedError = error.message;
}
const routeChecksAfterMalformed = routeCheckCount;
let duplicateError = '';
try {
  load(sitemapCases.duplicate);
} catch (error) {
  duplicateError = error.message;
}
console.log(JSON.stringify({
  malformedError,
  routeChecksAfterMalformed,
  duplicateError,
  uniquePaths: load(sitemapCases.unique)
}));
"""
    sitemap_cases = {
        "malformed": (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://askjamie.bot/about/</loc></url>"
        ),
        "duplicate": sitemap_xml(
            routes + ["/about/", "/contact/", "/about/"]
        ),
        "unique": sitemap_xml(routes),
    }
    result = subprocess.run(
        ["node", "-e", node_test, json.dumps(sitemap_cases)],
        cwd=ROOT, text=True, capture_output=True
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["malformedError"].startswith(
        "sitemap.xml could not be parsed:"
    )
    assert output["routeChecksAfterMalformed"] == 0
    assert output["duplicateError"] == (
        "Duplicate sitemap route(s) for responsive QA: /about/, /contact/"
    )
    assert output["uniquePaths"] == routes


def test_responsive_qa_checks_hero_action_font_geometry_on_320px_representatives():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    script = r"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_GEOMETRY_TOLERANCE_PX ='));
const runtime = { ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve,
  PUBLIC_PATHS: ['/lens-system/okhp3-brandguard/',
    '/lens-system/okhp3-brandguard/lvmh/',
    '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
    '/lens-system/okhp3-brandguard/new-case/', '/about/'] };
const routes = runtime.PUBLIC_PATHS;
console.log(JSON.stringify(Object.fromEntries(
  ['mobile-320', 'mobile-360'].map(viewport => [viewport,
    routes.map(path => vm.runInNewContext(config +
      '\nshouldCheckBrandGuardHeroActions(path, viewport)', {
        ...runtime, path, viewport
      }))
  ])
)));
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True
    )
    assert json.loads(result.stdout) == {
        "mobile-320": [True, True, True, False, False],
        "mobile-360": [False, False, False, False, False],
    }
    assert "includeHeroActions" in source
    assert "BRANDGUARD 320PX HERO ACTION WEB FONT GEOMETRY SHIFT" in source
    assert "BRANDGUARD 320PX HERO BUTTON LABEL NOT READABLE" in source
    assert "BRANDGUARD 320PX HERO BUTTONS OVERLAP" in source
    assert "minimum_target_px: { width: 44, height: 44 }" in source
    assert "deferred_theme_geometry_included: false" in source
    assert "hero_actions_web_font_geometry" in source
    resize_test = (ROOT / "tests" / "test_deferred_fonts_resize.spec.mjs").read_text(
        encoding="utf-8"
    )
    assert "'.capability-transition', '.askjamie-breadcrumb'" in resize_test
    assert ".capability-transition, .capability-transition > *," in resize_test
    assert "checkBrandGuardTransitionInitializationRace(path, 320)" in resize_test
    assert "for (const path of sitemapPaths)" in resize_test
    assert "brandguard_sitemap_routes: sitemapPaths" in resize_test
    assert "transition child position" in resize_test
    assert "must be reserved before fonts release" in resize_test


def test_brandguard_320_hero_action_accessible_names_match_visible_labels_and_are_unique():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    assert "getByRole('link'" in source
    assert "shouldCheckBrandGuardHeroActions(path, vp.name)" in source
    assert "brandguard_hero_action_accessible_names" in source
    assert "brandGuardHeroActionAccessibleNames?.errors" in source
    assert "BRANDGUARD 320PX HERO ACTION ACCESSIBLE NAME" in source
    assert "route=${route}; link=${JSON.stringify" in source

    node_test = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const start = source.indexOf('async function checkBrandGuardHeroActionAccessibleNames');
const end = source.indexOf('async function checkBrandGuardWebFontGeometry', start);
const helper = source.slice(start, end);
const selectors = `
const BRANDGUARD_HERO_ACTION_SELECTOR =
  '.askjamie-main .askjamie-hero-copy .hero-actions > a[href]';
const BRANDGUARD_HERO_ACTION_CONTAINER_SELECTOR =
  '.askjamie-main .askjamie-hero-copy .hero-actions';`;
function makePage(links) {
  const actionSelector =
    '.askjamie-main .askjamie-hero-copy .hero-actions > a[href]';
  const container = {
    getByRole: (_role, options) => ({
      count: async () => links.filter(link =>
        link.accessibleName === options.name
      ).length
    })
  };
  const actions = {
    count: async () => links.length,
    nth: index => ({
      innerText: async () => links[index].visibleLabel,
      getAttribute: async name => name === 'href' ? links[index].href : null,
    })
  };
  return {
    locator: selector => selector === actionSelector
      ? actions : container,
    viewportSize: () => ({ width: 320 }),
    url: () => 'https://askjamie.bot/lens-system/okhp3-brandguard/',
  };
}
const check = vm.runInNewContext(
  selectors + '\n' + helper + '\ncheckBrandGuardHeroActionAccessibleNames',
  { URL }
);
(async () => {
  const good = await check(makePage([
    { visibleLabel: 'Explore cases', accessibleName: 'Explore cases', href: '/cases/' },
    { visibleLabel: 'Contact Jamie', accessibleName: 'Contact Jamie', href: '/contact/' },
  ]), '/lens-system/okhp3-brandguard/');
  const missing = await check(makePage([
    { visibleLabel: 'Open the case', accessibleName: '', href: '/case/' },
  ]), '/lens-system/okhp3-brandguard/lvmh/');
  const blankLabel = await check(makePage([
    { visibleLabel: '   ', accessibleName: 'Accessible fallback label', href: '/blank/' },
  ]), '/lens-system/okhp3-brandguard/lvmh/');
  const duplicate = await check(makePage([
    { visibleLabel: 'Read more', accessibleName: 'Read more', href: '/one/' },
    { visibleLabel: 'Read more', accessibleName: 'Read more', href: '/two/' },
  ]), '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/');
  const noActions = await check(
    makePage([]), '/lens-system/okhp3-brandguard/new-case/'
  );
  console.log(JSON.stringify({
    good, missing, blankLabel, duplicate, noActions
  }));
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout)
    assert outcomes["good"]["errors"] == []
    assert [link["distinguishes_from_siblings"] for link in
            outcomes["good"]["evidence"]["links"]] == [True, True]
    assert len(outcomes["missing"]["errors"]) == 1
    assert "/lens-system/okhp3-brandguard/lvmh/" in outcomes["missing"]["errors"][0]
    assert '\"label\":\"Open the case\"' in outcomes["missing"]["errors"][0]
    assert '\"href\":\"/case/\"' in outcomes["missing"]["errors"][0]
    assert '\"destination\":\"/case/\"' in outcomes["missing"]["errors"][0]
    assert len(outcomes["blankLabel"]["errors"]) == 1
    assert "HAS NO VISIBLE LABEL" in outcomes["blankLabel"]["errors"][0]
    assert '\"href\":\"/blank/\"' in outcomes["blankLabel"]["errors"][0]
    assert len(outcomes["duplicate"]["errors"]) == 2
    assert all(
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/" in error
        and "matching_links=2" in error
        for error in outcomes["duplicate"]["errors"]
    )
    assert len(outcomes["noActions"]["errors"]) == 1
    assert "/lens-system/okhp3-brandguard/new-case/" in outcomes["noActions"]["errors"][0]
    assert "label=null; destination=null" in outcomes["noActions"]["errors"][0]


def test_brandguard_320_hero_action_accessible_names_cover_every_sitemap_route():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sitemap_routes = sorted({
        location.text.strip().removeprefix("https://askjamie.bot")
        for location in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if location.text and
        location.text.strip().startswith(
            "https://askjamie.bot/lens-system/okhp3-brandguard/"
        )
    })
    assert sitemap_routes
    assert "shouldCheckBrandGuardHeroActionAccessibleNames(path, vp.name)" in source
    assert "checkBrandGuardHeroActionAccessibleNameLabels" in source

    node_test = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const start = source.indexOf(
  'function shouldCheckBrandGuardHeroActionAccessibleNames'
);
const end = source.indexOf(
  '\nconst BRANDGUARD_GEOMETRY_TOLERANCE_PX',
  start
);
const predicate = source.slice(start, end);
const routes = JSON.parse(process.argv[1]);
const shouldCheck = vm.runInNewContext(
  predicate + '\nshouldCheckBrandGuardHeroActionAccessibleNames',
  {
    BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/',
    BRANDGUARD_NARROW_CONTENT_VIEWPORT: 'mobile-320',
    PUBLIC_PATHS: routes,
  }
);
console.log(JSON.stringify({
  mobile320: routes.map(path => shouldCheck(path, 'mobile-320')),
  mobile360: routes.map(path => shouldCheck(path, 'mobile-360')),
  unsitemapped: shouldCheck(
    '/lens-system/okhp3-brandguard/not-in-sitemap/', 'mobile-320'
  ),
  unrelated: shouldCheck('/about/', 'mobile-320'),
}));
"""
    result = subprocess.run(
        ["node", "-e", node_test, json.dumps(sitemap_routes)],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout)
    assert matrix == {
        "mobile320": [True] * len(sitemap_routes),
        "mobile360": [False] * len(sitemap_routes),
        "unsitemapped": False,
        "unrelated": False,
    }


def test_brandguard_320_hero_keyboard_focus_is_separate_and_checks_clipping():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    predicate = source[
        source.index("const BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS"):
        source.index("const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS")
    ]
    predicate_test = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const config = source.slice(source.indexOf('const BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS'),
  source.indexOf('const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS'));
const routes = [
  '/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
  '/lens-system/okhp3-brandguard/lego/',
];
console.log(JSON.stringify(Object.fromEntries(
  ['mobile-320', 'mobile-360'].map(viewportName => [viewportName,
    routes.map(path => vm.runInNewContext(config +
      '\nshouldCheckBrandGuardHeroKeyboardFocus(path, viewportName)', {
        path, viewportName,
        PUBLIC_PATHS: routes,
        BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/'
      }))
  ])
)));
"""
    result = subprocess.run(
        ["node", "-e", predicate_test], cwd=ROOT, text=True,
        capture_output=True, check=True
    )
    assert json.loads(result.stdout) == {
        "mobile-320": [True, True, True, False],
        "mobile-360": [False, False, False, False],
    }

    focus_check = source[
        source.index("async function checkBrandGuardHeroKeyboardFocus"):
        source.index("async function checkBrandGuardWebFontGeometry")
    ]
    assert "page.keyboard.press('Tab')" in focus_check
    assert "active.matches(':focus-visible')" in focus_check
    assert "outline_visible" in focus_check
    assert "clipping_ancestors" in focus_check
    assert "viewport_clipped" in focus_check
    assert "NOT REACHED BY KEYBOARD TAB" in focus_check
    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function captureBrandGuardTouchTargets")
    ]
    assert "checkBrandGuardHeroKeyboardFocus(" not in font_check
    assert "brandguard_hero_keyboard_focus:" in source

    focus_selector = (
        ".askjamie-main:is(.askjamie-brandguard-page, "
        ".brandguard-lvmh-page, .brandguard-bfs-page)"
    )
    for stylesheet in ("assets/css/critical-hero.css", "assets/css/theme.css"):
        css = (ROOT / stylesheet).read_text(encoding="utf-8")
        assert "@media (max-width: 340px)" in css
        focus_rule = css[css.index(focus_selector):]
        assert ".askjamie-hero-copy .hero-actions > a:focus-visible" in focus_rule
        assert "outline: 3px solid var(--color-accent, #2d6f7e)" in focus_rule
        assert "outline-offset: 3px" in focus_rule
        assert "scroll-margin-block: 8px" in focus_rule


def test_brandguard_320_forced_colors_hero_keyboard_focus_is_separate_and_clipped():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    predicate = source[
        source.index("const BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS"):
        source.index("const BRANDGUARD_MOBILE_NAV_KEYBOARD_PATHS")
    ]
    predicate_test = r"""
const vm = require('node:vm');
const routes = [
  '/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
  '/lens-system/okhp3-brandguard/lego/',
];
const context = vm.createContext({
  PUBLIC_PATHS: routes,
  BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/'
});
vm.runInContext(PREDICATE, context);
const shouldCheck = vm.runInContext(
  'shouldCheckBrandGuardHeroForcedColorsKeyboardFocus',
  context
);
console.log(JSON.stringify({
  mobile320: routes.map(path => shouldCheck(path, 'mobile-320')),
  mobile360: routes.map(path => shouldCheck(path, 'mobile-360')),
  unpublished: shouldCheck('/lens-system/okhp3-brandguard/not-published/', 'mobile-320')
}));
""".replace("PREDICATE", json.dumps(predicate))
    result = subprocess.run(
        ["node", "-e", predicate_test], cwd=ROOT, text=True,
        capture_output=True, check=True
    )
    assert json.loads(result.stdout) == {
        "mobile320": [True, True, True, False],
        "mobile360": [False, False, False, False],
        "unpublished": False,
    }

    keyboard_check = source[
        source.index("async function checkBrandGuardHeroKeyboardFocus"):
        source.index("async function checkBrandGuardMobileNavigationKeyboard")
    ]
    assert "await page.keyboard.press('Tab')" in keyboard_check
    assert "active.matches(':focus-visible')" in keyboard_check
    assert "outline_visible" in keyboard_check
    assert "focus_indicator_contrast" in keyboard_check
    assert "minimum_contrast_ratio = 3" in keyboard_check
    assert "viewport_clipped" in keyboard_check
    assert "clipping_ancestors" in keyboard_check
    assert "await page.emulateMedia({ forcedColors: 'active' })" in keyboard_check
    assert "checkBrandGuardHeroKeyboardFocus(page, {" in keyboard_check
    assert "forcedColors: true" in keyboard_check
    assert "serviceWorkers: 'block'" in keyboard_check
    assert "[data-transition-dialog][open] [data-transition-dismiss]" in keyboard_check
    assert "await transitionDismiss.click()" in keyboard_check
    assert "brandguard_hero_forced_colors_keyboard_focus:" in source
    assert "brandguard_forced_colors_readability:" in source
    assert (
        "this keyboard-focus evidence is reported separately from "
        "forced-colors text readability"
    ) in source
    navigation_check = source[
        source.index("async function checkBrandGuardMobileNavigationKeyboard"):
        source.index("async function checkBrandGuardHeroActionAccessibleNames")
    ]
    assert "const keyboardPage = await page.context().newPage()" in navigation_check
    assert "await keyboardPage.goto(" in navigation_check
    assert "await keyboardPage.close()" in navigation_check
    assert "dialog.open || document.readyState === 'complete'" in navigation_check
    assert "[data-transition-dialog][open] [data-transition-dismiss]" in navigation_check
    assert "await transitionDismiss.click()" in navigation_check
    assert "await page.waitForFunction(() =>" in navigation_check
    assert "!document.querySelector('[data-transition-dialog][open]')" in navigation_check

    focus_selector = (
        ".askjamie-main:is(.askjamie-brandguard-page, "
        ".brandguard-lvmh-page, .brandguard-bfs-page)"
    )
    for stylesheet in ("assets/css/critical-hero.css", "assets/css/theme.css"):
        css = (ROOT / stylesheet).read_text(encoding="utf-8")
        assert "@media (forced-colors: active) and (max-width: 340px)" in css
        forced_rule = css[css.index(
            "@media (forced-colors: active) and (max-width: 340px)"
        ):]
        assert focus_selector in forced_rule
        assert ".askjamie-hero-copy .hero-actions > a:focus-visible" in forced_rule
        assert "outline: 3px solid CanvasText" in forced_rule
        assert "outline-offset: 3px" in forced_rule
        assert "forced-color-adjust: auto" in forced_rule


def test_brandguard_mobile_360_reserves_only_measured_font_reflow_routes():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    route_classes = {
        "brandguard-case-lego": "lego",
        "brandguard-starbucks": "starbucks",
        "coke-brandguard": "coca-cola",
        "brandguard-case-brooks-running": "brooks-running",
        "brandguard-case-costco": "costco",
        "brandguard-case-discount-tire": "discount-tire",
        "brandguard-case-hershey": "hershey",
        "brandguard-case-mathews-archery": "mathews-archery",
        "brandguard-case-scheels": "scheels",
    }

    for body_class, route in route_classes.items():
        page = (
            ROOT
            / "lens-system/okhp3-brandguard"
            / route
            / "index.html"
        ).read_text(encoding="utf-8")
        assert f'<body class="askjamie-main {body_class}"' in page
        assert f".askjamie-main.{body_class}" in theme

    assert "min-height: 3lh;" in theme
    assert "min-height: 2lh;" in theme
    assert "min-height: 6lh;" in theme
    assert "min-height: 7lh;" in theme
    assert "min-height: 8lh;" in theme
    assert ".askjamie-main.coke-brandguard .manifesto-blockquote" in theme


def test_missing_350px_hub_heading_reservation_is_reported(tmp_path):
    (tmp_path / "assets/css").mkdir(parents=True)
    critical = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    reservation = (
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1 {\n"
        "    min-height: 4lh;\n"
        "  }"
    )
    assert reservation in critical
    critical = critical.replace(reservation, "", 1)
    (tmp_path / "assets/css/critical-hero.css").write_text(critical, encoding="utf-8")
    (tmp_path / "assets/css/theme.css").write_text(theme, encoding="utf-8")

    assert any(
        "BrandGuard mobile heading fallback wrapping: min-height drifted" in finding
        for finding in drift.check_contracts(tmp_path)
    )


def test_capability_transition_geometry_drift_is_reported(tmp_path):
    (tmp_path / "assets/css").mkdir(parents=True)
    critical = (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    theme = theme.replace(
        ".askjamie-main .capability-transition {\n"
        "  margin-block: 1.5rem;\n"
        "  padding: clamp(1rem, 3vw, 1.75rem);",
        ".askjamie-main .capability-transition {\n"
        "  margin-block: 1.5rem;\n"
        "  padding: 1.25rem;",
        1,
    )
    (tmp_path / "assets/css/critical-hero.css").write_text(critical, encoding="utf-8")
    (tmp_path / "assets/css/theme.css").write_text(theme, encoding="utf-8")

    findings = drift.check_contracts(tmp_path)

    assert any("capability transition panel: padding drifted" in finding for finding in findings)


def test_responsive_qa_measures_brandguard_theme_geometry_at_mobile_and_tablet_widths():
    import re

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    theme_viewports = re.search(
        r"const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert theme_viewports
    assert set(re.findall(r"'([^']+)'", theme_viewports.group(1))) == {
        "mobile-320",
        "mobile-350-edge",
        "mobile-355-boundary",
        "mobile-356-gap",
        "mobile-360",
        "mobile-364-gap",
        "mobile-365-boundary",
        "mobile-370-edge",
        "mobile-390",
        "mobile-400-edge",
        "mobile-401-edge",
        "mobile-430",
        "tablet-768",
        "tablet-769-edge",
        "tablet-899",
        "tablet-900-edge",
    }
    assert "'mobile-320'" in source
    assert "'mobile-360'" in source
    assert "'mobile-390'" in source
    assert "'mobile-430'" in source
    assert "'tablet-768'" in source
    assert "'tablet-769-edge'" in source
    assert "'tablet-899'" in source
    assert "'tablet-900-edge'" in source
    assert "{ name: 'tablet-768',   width: 768,  height: 1024 }" in source
    assert "{ name: 'tablet-769-edge', width: 769, height: 1024 }" in source
    assert "{ name: 'tablet-899',   width: 899,  height: 1024 }" in source
    assert "{ name: 'tablet-900-edge', width: 900, height: 1024 }" in source
    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "{ name: 'mobile-320',   width: 320,  height: 740  }" in source
    font_viewports = re.search(
        r"const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert font_viewports
    assert set(re.findall(r"'([^']+)'", font_viewports.group(1))) == {
        "mobile-320",
        "mobile-360",
        "mobile-390",
        "mobile-400-edge",
        "mobile-401-edge",
        "tablet-768",
        "tablet-769-edge",
        "tablet-899",
        "tablet-900-edge",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    font_gap_viewports = re.search(
        r"const BRANDGUARD_FONT_GAP_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert font_gap_viewports
    assert set(re.findall(r"'([^']+)'", font_gap_viewports.group(1))) == {
        "mobile-356-gap",
        "mobile-357-font-gap",
        "mobile-358-font-gap",
        "mobile-359-font-gap",
        "mobile-360",
        "mobile-361-font-gap",
        "mobile-362-font-gap",
        "mobile-363-font-gap",
        "mobile-364-gap",
    }
    assert "link[data-deferred-styles]" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "brandGuardThemeGate.then(() => route.continue())" in source
    assert "stylesheet attached before capture" in source
    assert "BRANDGUARD WEB FONT BASELINE UNAVAILABLE" in source
    assert "deferred theme stylesheet did not settle " in source
    assert "'.askjamie-main .askjamie-breadcrumb'" in source
    assert "'.askjamie-main .askjamie-hero-copy h1'" in source
    assert "'.askjamie-main .askjamie-hero-copy .hero-subtitle'" in source
    assert "'.askjamie-main .askjamie-hero-copy .hero-tagline'" in source
    assert "BRANDGUARD HERO GEOMETRY SHIFT" in source
    assert "before=${JSON.stringify(beforeRect)}" in source
    assert "after=${JSON.stringify(afterRect)}" in source
    assert "hero_theme_geometry" in source


def test_responsive_qa_checks_brandguard_font_gap_at_every_width_on_three_routes():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve }};
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_NARROW_CONTENT_VIEWPORT ='));
const predicate = source.slice(source.indexOf('    const checkBrandGuardTheme ='),
  source.indexOf('    const checkBrandGuardFonts ='));
const routes = [
  '/lens-system/okhp3-brandguard/',
  '/lens-system/okhp3-brandguard/lvmh/',
  '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
  '/lens-system/okhp3-brandguard/lego/',
  '/about/'
];
const viewports = ['mobile-356-gap', 'mobile-357-font-gap', 'mobile-358-font-gap',
  'mobile-359-font-gap', 'mobile-360', 'mobile-361-font-gap',
  'mobile-362-font-gap', 'mobile-363-font-gap', 'mobile-364-gap'];
console.log(JSON.stringify(Object.fromEntries(viewports.map(name => [name,
  routes.map(path => vm.runInNewContext(config + '\\n' + predicate +
    '\\ncheckBrandGuardGapFonts', {{ ...runtime, PUBLIC_PATHS: routes.slice(0, 3),
      path, vp: {{ name }} }}))
]))));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout)
    for viewport, width in zip(
        (
            "mobile-356-gap",
            "mobile-357-font-gap",
            "mobile-358-font-gap",
            "mobile-359-font-gap",
            "mobile-360",
            "mobile-361-font-gap",
            "mobile-362-font-gap",
            "mobile-363-font-gap",
            "mobile-364-gap",
        ),
        range(356, 365),
        strict=True,
    ):
        assert matrix[viewport] == [True, True, True, False, False]
        assert f"width: {width}," in source

    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    geometry_selectors = source[
        source.index("const BRANDGUARD_GEOMETRY_SELECTORS ="):
        source.index("const BRANDGUARD_HERO_ACTION_SELECTOR")
    ]
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert all(
        selector in geometry_selectors
        for selector in (
            "'.askjamie-main .askjamie-breadcrumb'",
            "'.askjamie-main .askjamie-hero-copy h1'",
            "'.askjamie-main .askjamie-hero-copy .hero-subtitle'",
            "'.askjamie-main .askjamie-hero-copy .hero-tagline'",
        )
    )
    assert "}, BRANDGUARD_GEOMETRY_SELECTORS);" in font_check
    assert "font_resource_failures: fontResourceFailures" in font_check
    assert "font_resources: fontResponses" in font_check


def test_responsive_qa_measures_deferred_theme_geometry_on_every_sitemap_brandguard_route():
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sitemap_routes = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert sitemap_routes

    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve }};
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_NARROW_CONTENT_VIEWPORT ='));
const predicate = source.slice(source.indexOf('    const checkBrandGuardTheme ='),
  source.indexOf('    const checkBrandGuardAllRouteFonts ='));
const routes = {json.dumps(sitemap_routes + ['/about/'])};
const viewports = ['mobile-320', 'mobile-350-edge', 'mobile-355-boundary',
  'mobile-356-gap', 'mobile-360', 'mobile-364-gap', 'mobile-365-boundary',
  'mobile-370-edge', 'mobile-390', 'mobile-400-edge', 'mobile-401-edge',
  'mobile-430', 'tablet-768',
  'tablet-769-edge', 'tablet-899', 'tablet-900-edge', 'desktop-1280'];
console.log(JSON.stringify(Object.fromEntries(viewports.map(name => [name,
  routes.map(path => vm.runInNewContext(config + '\\n' + predicate +
    '\\ncheckBrandGuardTheme', {{ ...runtime, PUBLIC_PATHS: routes, path, vp: {{ name }} }}))
]))));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    matrix = json.loads(result.stdout)
    for viewport in (
        "mobile-320", "mobile-350-edge", "mobile-355-boundary", "mobile-356-gap",
        "mobile-360", "mobile-364-gap", "mobile-365-boundary", "mobile-370-edge",
        "mobile-390", "mobile-400-edge", "mobile-401-edge", "mobile-430",
        "tablet-768", "tablet-769-edge", "tablet-899", "tablet-900-edge",
    ):
        assert matrix[viewport] == [True] * len(sitemap_routes) + [False]
    assert matrix["desktop-1280"] == [False] * (len(sitemap_routes) + 1)

    header_theme_viewports = re.search(
        r"const BRANDGUARD_HEADER_THEME_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert header_theme_viewports
    assert set(re.findall(r"'([^']+)'", header_theme_viewports.group(1))) == {
        "mobile-355-boundary",
        "mobile-365-boundary",
    }
    header_node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve }};
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_NARROW_CONTENT_VIEWPORT ='));
const predicate = source.slice(source.indexOf('    const checkBrandGuardTheme ='),
  source.indexOf('    const checkBrandGuardFonts ='));
const routes = {json.dumps(sitemap_routes + ['/about/'])};
const viewports = ['mobile-320', 'mobile-355-boundary', 'mobile-356-gap',
  'mobile-365-boundary', 'mobile-370-edge'];
console.log(JSON.stringify(Object.fromEntries(viewports.map(name => [name,
  routes.map(path => vm.runInNewContext(config + '\\n' + predicate +
    '\\ncheckBrandGuardHeaderTheme', {{ ...runtime, PUBLIC_PATHS: routes,
      path, vp: {{ name }} }}))
]))));
"""
    header_result = subprocess.run(
        ["node", "-e", header_node_test],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert header_result.returncode == 0, header_result.stderr
    header_matrix = json.loads(header_result.stdout)
    deferred_routes = [
        route for route in sitemap_routes
        if "data-deferred-styles" in (
            ROOT / route.lstrip("/") / "index.html"
        ).read_text(encoding="utf-8")
    ]
    for viewport in ("mobile-355-boundary", "mobile-365-boundary"):
        assert header_matrix[viewport] == [
            route in deferred_routes for route in sitemap_routes + ["/about/"]
        ]
    for viewport in ("mobile-320", "mobile-356-gap", "mobile-370-edge"):
        assert header_matrix[viewport] == [False] * (len(sitemap_routes) + 1)

    header_check = source[
        source.index("async function checkBrandGuardHeaderThemeGeometry"):
        source.index("async function checkBrandGuardHeroKeyboardFocus")
    ]
    assert "brandguard_header_navigation_theme_geometry" in source
    assert "brandGuardHeaderThemeGeometry?.errors" in source
    assert "measurement_scope: 'open-mobile-navigation-before-and-after-deferred-theme-activation'" in header_check
    assert "font_measurements_included: false" in header_check
    assert "BASELINE UNAVAILABLE" in header_check
    assert "ACTIVATION FAILED" in header_check
    assert "MEASUREMENT FAILED" in header_check
    assert "evidence.comparison_status = 'baseline-ready'" in header_check
    assert "releaseThemeStyles" not in header_check
    assert "before=${JSON.stringify(before)}" in header_check
    assert "after=${JSON.stringify(after)}" in header_check
    assert "brandguard_header_navigation_theme_geometry:" in source
    assert "...(brandGuardHeaderThemeGeometry?.errors ?? [])" in source
    baseline_capture = source.index("const brandGuardHeaderThemeBaseline =")
    hero_theme_check = source.index(
        "const heroThemeGeometry =", baseline_capture
    )
    post_theme_capture = source.index(
        "const brandGuardHeaderThemeGeometry =", hero_theme_check
    )
    assert baseline_capture < hero_theme_check < post_theme_capture

    assert "hero_theme_geometry: heroThemeGeometry.evidence" in source
    assert "text_line_reflow: textLineReflow.evidence" in source
    assert (
        "const textLineReflow = compareBrandGuardTextLineReflow("
        in source
    )
    assert "PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))" in source
    assert "{ name: 'mobile-400-edge', width: 400, height: 780 }" in source
    assert "{ name: 'mobile-401-edge', width: 401, height: 780 }" in source


def test_responsive_qa_viewport_scheduler_keeps_four_workers_busy_and_returns_every_viewport():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("async function mapConcurrent(")
    end = source.index("\n}\n", start) + 2
    helper = source[start:end]
    assert helper.startswith("async function mapConcurrent(")

    node_test = f"""
const vm = require('node:vm');
const helper = {json.dumps(helper)};
const script = helper + `
(async () => {{
  let active = 0;
  let maxActive = 0;
  const counts = Array(20).fill(0);
  const results = await mapConcurrent(
    Array.from({{ length: 20 }}, (_, index) => index),
    4,
    async value => {{
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise(resolve => setTimeout(resolve, (5 - value % 5) * 2));
      active -= 1;
      counts[value] += 1;
      return value;
    }}
  );
  return {{ results, maxActive, counts }};
}})()
`;
vm.runInNewContext(script, {{ setTimeout }}).then(
  result => console.log(JSON.stringify(result)),
  error => {{ console.error(error); process.exitCode = 1; }}
);
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    evidence = json.loads(result.stdout)
    assert evidence["results"] == list(range(20))
    assert evidence["maxActive"] == 4
    assert evidence["counts"] == [1] * 20


def test_browser_report_records_runtime_and_intentional_coverage_limits():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "duration_ms: Math.round(performance.now() - browserRunStartedAt)" in source
    assert "coverage_profile: BRANDGUARD_EDGE_ONLY" in source
    assert "'full-public-sitemap'" in source
    assert "'brandguard-edge-only'" in source
    assert (
        "Intentional focused run: sitemap-listed BrandGuard routes at "
        "mobile-350-edge and mobile-370-edge only; this is not full release coverage."
    ) in source
    assert "Browser QA duration:" in source


def test_deferred_font_script_reserves_brandguard_tablet_geometry_before_activation():
    source = (ROOT / "assets/js/deferred-fonts.js").read_text(encoding="utf-8")

    assert "(window.innerWidth === 320 || window.innerWidth >= 768)" in source
    assert 'classList.contains("askjamie-main")' in source
    assert 'document.querySelector(".askjamie-breadcrumb")' in source
    assert '".site-header .container"' in source
    assert '".site-specials"' in source
    assert '".capability-transition"' in source
    assert '".capability-transition > *"' in source
    assert '".askjamie-hero-copy > *"' in source
    assert 'element.style.minHeight = `${Math.ceil(height * 100) / 100}px`' in source
    assert 'element.dataset.fontGeometryReserved = "true"' in source
    assert 'themeStylesheet.addEventListener("load", reserveAfterThemeStyles' in source
    assert 'document.querySelector("link[data-brandguard-tablet-fonts]")' in source
    assert 'tabletFontStylesheet.media = "all"' in source
    qa_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    assert "responsive geometry was not reserved " in qa_source
    assert source.index("fontGeometryReserved") < source.index("const enableAfterLoad")
    case_pages = sorted((ROOT / "lens-system/okhp3-brandguard").glob("*/index.html"))
    assert len(case_pages) == 13
    assert all(
        'media="(max-width: 319px), (min-width: 321px) and (max-width: 767px)" data-brandguard-tablet-fonts'
        in page.read_text(encoding="utf-8")
        for page in case_pages
    )


def test_responsive_qa_checks_brandguard_content_at_320px_from_sitemap_routes():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "const PUBLIC_PATHS = loadPublicPaths();" in source
    assert "const BRANDGUARD_NARROW_CONTENT_VIEWPORT = 'mobile-320'" in source
    assert "const BRANDGUARD_FONT_CONTENT_VIEWPORT_WIDTHS = new Set([320, 350, 370])" in source
    assert "path.startsWith(BRANDGUARD_GEOMETRY_PATH)" in source
    assert "vp.name === BRANDGUARD_NARROW_CONTENT_VIEWPORT" in source
    assert "BRANDGUARD_NARROW_CONTENT_SELECTORS" in source
    assert "horizontal_content_clipping" in source
    assert "vertical_content_clipping" in source
    assert "outside_viewport" in source
    assert "evidence.document_overflow" in source
    assert "breadcrumb_overlaps_heading" in source
    assert "${errorPrefix} CONTENT NOT READABLE" in source
    assert "${errorPrefix} CONTENT CLIPPED" in source
    assert "${errorPrefix} VIEWPORT OVERFLOW" in source
    assert "${errorPrefix} BREADCRUMB OVERLAPS HERO" in source
    assert "brandguard_320_content" in source


def test_brandguard_text_spacing_check_covers_sitemap_routes_at_350_and_370():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    routes = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert routes

    node_test = f"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const predicate = source.slice(source.indexOf('const BRANDGUARD_TEXT_SPACING_VIEWPORTS ='),
  source.indexOf('function shouldCheckBrandGuardHeroKeyboardFocus'));
const routes = {json.dumps(routes)};
const context = vm.createContext({{
  BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/',
  PUBLIC_PATHS: routes,
}});
const shouldCheck = vm.runInContext(predicate +
  '\\nshouldCheckBrandGuardTextSpacing', context);
const viewports = ['mobile-350-edge', 'mobile-370-edge', 'mobile-360'];
const matrix = Object.fromEntries(viewports.map(viewport => [viewport,
  routes.map(route => shouldCheck(route, viewport))]));
console.log(JSON.stringify({{
  matrix,
  future: shouldCheck('/lens-system/okhp3-brandguard/future/', 'mobile-350-edge'),
  unrelated: shouldCheck('/about/', 'mobile-370-edge'),
}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    coverage = json.loads(result.stdout)
    assert coverage["matrix"]["mobile-350-edge"] == [True] * len(routes)
    assert coverage["matrix"]["mobile-370-edge"] == [True] * len(routes)
    assert coverage["matrix"]["mobile-360"] == [False] * len(routes)
    assert coverage["future"] is False
    assert coverage["unrelated"] is False

    assert "checkBrandGuardTextSpacingReadability(page, path, vp.width)" in source
    assert "brandguard_breadcrumb_text_spacing:" in source
    assert "tolerance_px: 0" in source
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source


def test_brandguard_text_spacing_readability_reports_bounds_and_hero_overlap_separately():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("async function checkBrandGuardTextSpacingReadability(")
    end = source.index("\nasync function checkBrandGuardBreadcrumbKeyboardFocus", start)
    checker = source[start:end]
    node_test = f"""
const vm = require('node:vm');
const checker = {json.dumps(checker)};
const context = vm.createContext({{
  window: {{ innerWidth: 350 }},
  getComputedStyle: element => element.style,
}});
const check = vm.runInContext(checker + '\\ncheckBrandGuardTextSpacingReadability', context);
const expectedTextStyle = {{
  display: 'block',
  visibility: 'visible',
  opacity: '1',
  fontSize: '15px',
  letterSpacing: '1.8px',
  wordSpacing: '2.4px',
  lineHeight: '22.5px',
  overflowX: 'visible',
  overflowY: 'visible',
}};

async function runCase(kind) {{
  const textRect = kind === 'overlap'
    ? {{ left: 20, right: 200, top: 55, bottom: 75, width: 180, height: 20 }}
    : {{ left: 20, right: kind === 'outside' ? 351 : 200,
         top: 25, bottom: 45, width: 180, height: 20 }};
  const breadcrumbRect = {{ left: 10, right: 340, top: 10, bottom: 90,
    width: 330, height: 80 }};
  const labelRect = {{ left: 10, right: 340, top: 20, bottom: 50,
    width: 330, height: 30 }};
  const headingRect = kind === 'overlap'
    ? {{ left: 10, right: 340, top: 65, bottom: 105, width: 330, height: 40 }}
    : {{ left: 10, right: 340, top: 100, bottom: 140, width: 330, height: 40 }};
  const label = {{
    textContent: 'A long breadcrumb label',
    style: expectedTextStyle,
    clientWidth: 330,
    scrollWidth: 330,
    clientHeight: 30,
    scrollHeight: 30,
    getClientRects: () => [labelRect],
    getBoundingClientRect: () => labelRect,
  }};
  const breadcrumb = {{
    textContent: 'Home A long breadcrumb label',
    style: expectedTextStyle,
    getClientRects: () => [breadcrumbRect],
    getBoundingClientRect: () => breadcrumbRect,
    querySelector: selector => selector === '.breadcrumb-label' ? label : null,
  }};
  const heading = {{
    style: expectedTextStyle,
    getBoundingClientRect: () => headingRect,
  }};
  context.document = {{
    documentElement: {{ dataset: {{}} }},
    querySelector: selector => selector.endsWith('.askjamie-breadcrumb')
      ? breadcrumb
      : selector.endsWith('.breadcrumb-label')
        ? label
        : heading,
    createRange: () => ({{
      selectNodeContents() {{}},
      getClientRects: () => [textRect],
    }}),
  }};
  const page = {{
    evaluate: async measure => measure(),
  }};
  const result = await check(
    page, '/lens-system/okhp3-brandguard/lvmh/', 350
  );
  return {{
    result,
    spacing_marker_applied:
      context.document.documentElement.dataset.responsiveQaTextSpacing === 'true',
  }};
}}

(async () => {{
  const success = await runCase('success');
  const outside = await runCase('outside');
  const overlap = await runCase('overlap');
  console.log(JSON.stringify({{ success, outside, overlap }}));
}})().catch(error => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    checks = json.loads(result.stdout)

    assert checks["success"]["result"]["errors"] == []
    evidence = checks["success"]["result"]["evidence"]
    assert evidence["tolerance_px"] == 0
    assert evidence["required_spacing_applied"] is True
    assert checks["success"]["spacing_marker_applied"] is True
    assert all(
        spacing in (ROOT / "assets" / "css" / "theme.css").read_text(encoding="utf-8")
        for spacing in (
            'html[data-responsive-qa-text-spacing="true"]',
            "letter-spacing: 0.12em",
            "word-spacing: 0.16em",
            "line-height: 1.5",
        )
    )
    assert len(checks["outside"]["result"]["errors"]) == 1
    assert "BREADCRUMB TEXT CLIPPED OR OUTSIDE ITS VISIBLE AREA" in (
        checks["outside"]["result"]["errors"][0]
    )
    assert len(checks["overlap"]["result"]["errors"]) == 1
    assert "BREADCRUMB OVERLAPS HERO" in checks["overlap"]["result"]["errors"][0]
    assert all(
        "FONT GEOMETRY" not in error
        for case in ("outside", "overlap")
        for error in checks[case]["result"]["errors"]
    )


def test_brandguard_content_failures_are_reported_as_separate_dom_categories():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    content_check = source[
        source.index("async function checkBrandGuardNarrowContent"):
        source.index("function formatBrandGuardFallbackFontReadabilityResult")
    ]
    node_test = f"""
const vm = require('node:vm');
const contentCheck = {json.dumps(content_check)};
const selectors = [
  '.askjamie-main .askjamie-breadcrumb',
  '.askjamie-main .askjamie-breadcrumb .breadcrumb-label',
  '.askjamie-main .askjamie-hero-copy h1',
  '.askjamie-main .askjamie-hero-copy .hero-subtitle',
  '.askjamie-main .askjamie-hero-copy .hero-tagline',
  '.askjamie-main .capability-transition'
];
const context = vm.createContext({{
  BRANDGUARD_GEOMETRY_TOLERANCE_PX: 1,
  BRANDGUARD_NARROW_CONTENT_SELECTORS: selectors,
  window: {{ innerWidth: 320 }},
  getComputedStyle: element => element.style
}});
vm.runInContext(contentCheck, context);

function makeElement(top, bottom) {{
  const style = {{
    display: 'block',
    visibility: 'visible',
    opacity: '1',
    overflowX: 'visible',
    overflowY: 'visible'
  }};
  return {{
    textContent: 'Visible fixture text',
    style,
    clientWidth: 320,
    scrollWidth: 320,
    clientHeight: bottom - top,
    scrollHeight: bottom - top,
    getBoundingClientRect: () => ({{
      left: 0, right: 320, top, bottom, width: 320, height: bottom - top
    }})
  }};
}}

async function runCase(kind) {{
  const elements = new Map([
    [selectors[0], makeElement(10, 30)],
    [selectors[1], makeElement(10, 25)],
    [selectors[2], makeElement(50, 80)],
    [selectors[3], makeElement(90, 110)],
    [selectors[4], makeElement(120, 140)],
    [selectors[5], makeElement(150, 170)]
  ]);
  let documentScrollWidth = 320;

  if (kind === 'clipping') {{
    const heading = elements.get(selectors[2]);
    heading.style.overflowX = 'hidden';
    heading.clientWidth = 200;
    heading.scrollWidth = 202;
  }} else if (kind === 'vertical-clipping') {{
    const heading = elements.get(selectors[2]);
    heading.style.overflowY = 'hidden';
    heading.clientHeight = 20;
    heading.scrollHeight = 22;
  }} else if (kind === 'overflow') {{
    documentScrollWidth = 321;
  }} else if (kind === 'element-outside-viewport') {{
    const heading = elements.get(selectors[2]);
    heading.getBoundingClientRect = () => ({{
      left: 0, right: 322, top: 50, bottom: 80, width: 322, height: 30
    }});
  }} else if (kind === 'overlap') {{
    elements.set(selectors[0], makeElement(10, 61));
    elements.set(selectors[2], makeElement(59, 89));
  }} else {{
    throw new Error(`Unknown fixture: ${{kind}}`);
  }}

  context.document = {{
    querySelector: selector => elements.get(selector) ?? null,
    createRange: () => ({{
      selectNodeContents() {{}},
      getClientRects: () => []
    }}),
    documentElement: {{ scrollWidth: documentScrollWidth }}
  }};
  return await vm.runInContext(
    '(async () => checkBrandGuardNarrowContent(' +
      '{{ evaluate: (measure, args) => measure(args) }}, 320))()',
    context
  );
}}

(async () => {{
  const results = {{}};
  for (const kind of [
    'clipping', 'vertical-clipping', 'overflow',
    'element-outside-viewport', 'overlap'
  ]) {{
    results[kind] = await runCase(kind);
  }}
  console.log(JSON.stringify(results));
}})().catch(error => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)

    expected_categories = {
        "clipping": "CONTENT CLIPPED",
        "vertical-clipping": "CONTENT CLIPPED",
        "overflow": "VIEWPORT OVERFLOW",
        "element-outside-viewport": "VIEWPORT OVERFLOW",
        "overlap": "BREADCRUMB OVERLAPS HERO",
    }
    for case, category in expected_categories.items():
        errors = output[case]["errors"]
        assert len(errors) == 1
        assert category in errors[0]
        assert not any(
            other_category in errors[0]
            for other_category in set(expected_categories.values()) - {category}
        )
        assert "FONT GEOMETRY" not in errors[0]

    assert output["clipping"]["evidence"]["elements"][
        ".askjamie-main .askjamie-hero-copy h1"
    ]["horizontal_content_clipping"] is True
    vertical_clipping = output["vertical-clipping"]["evidence"]["elements"][
        ".askjamie-main .askjamie-hero-copy h1"
    ]
    assert vertical_clipping["vertical_content_clipping"] is True
    assert vertical_clipping["vertical_content_overflow"] is True
    assert output["vertical-clipping"]["evidence"]["document_overflow"] is False
    assert output["vertical-clipping"]["evidence"]["breadcrumb_overlaps_heading"] is False
    assert output["clipping"]["evidence"]["document_overflow"] is False
    assert output["overflow"]["evidence"]["document_overflow"] is True
    assert output["overflow"]["evidence"]["breadcrumb_overlaps_heading"] is False
    outside_viewport = output["element-outside-viewport"]
    assert len(outside_viewport["errors"]) == 1
    assert "VIEWPORT OVERFLOW" in outside_viewport["errors"][0]
    assert ".askjamie-main .askjamie-hero-copy h1" in outside_viewport["errors"][0]
    assert "document scroll width" not in outside_viewport["errors"][0]
    assert "CONTENT CLIPPED" not in outside_viewport["errors"][0]
    assert "BREADCRUMB OVERLAPS HERO" not in outside_viewport["errors"][0]
    assert "FONT GEOMETRY" not in outside_viewport["errors"][0]
    assert outside_viewport["evidence"]["document_scroll_width"] == 320
    assert outside_viewport["evidence"]["viewport_width"] == 320
    assert outside_viewport["evidence"]["document_overflow"] is False
    assert outside_viewport["evidence"]["breadcrumb_overlaps_heading"] is False
    heading_evidence = outside_viewport["evidence"]["elements"][
        ".askjamie-main .askjamie-hero-copy h1"
    ]
    assert heading_evidence["outside_viewport"] is True
    assert heading_evidence["horizontal_content_clipping"] is False
    assert heading_evidence["vertical_content_clipping"] is False
    assert output["overlap"]["evidence"]["breadcrumb_overlaps_heading"] is True
    assert output["overlap"]["evidence"]["document_overflow"] is False

    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    assert "const BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1;" in source
    assert "element.scrollHeight > element.clientHeight + tolerancePx" in source
    assert "const BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1;" in source
    assert "Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in font_check


def test_fallback_font_readability_covers_every_brandguard_sitemap_route_at_400_and_401():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    routes = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert routes
    coverage_source = source[
        source.index("const BRANDGUARD_FALLBACK_FONT_READABILITY_VIEWPORT_WIDTHS"):
        source.index("// Retained as a representative route set")
    ]
    node_test = f"""
const vm = require('node:vm');
const routes = {json.dumps(routes)};
const context = vm.createContext({{
  BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/',
  PUBLIC_PATHS: routes,
}});
const coverage = {json.dumps(coverage_source)};
    vm.runInContext(coverage, context);
    const shouldCheck = vm.runInContext(
      'shouldCheckBrandGuardFallbackFontReadability',
      context
    );
    console.log(JSON.stringify(routes.map(path => ({{
      path,
      at400: shouldCheck(path, 400),
      at401: shouldCheck(path, 401),
      at390: shouldCheck(path, 390),
    }}))));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    coverage = json.loads(result.stdout)
    assert [row["path"] for row in coverage] == routes
    assert all(row["at400"] and row["at401"] for row in coverage)
    assert all(not row["at390"] for row in coverage)
    assert "serviceWorkers: 'block'" in source
    assert "if (BRANDGUARD_FONT_HOSTS.has(requestUrl.hostname))" in source
    assert "return route.abort();" in source
    assert "themeStylesheet.media === 'all'" in source
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in source
    for selector in (
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ):
        assert selector in source


def test_forced_colors_readability_covers_every_sitemap_case_study_in_isolated_viewports():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    routes = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    hub = "/lens-system/okhp3-brandguard/"
    expected = [path for path in routes if path.startswith(hub) and path != hub]
    assert expected
    assert {
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
        "/lens-system/okhp3-brandguard/lego/",
        "/lens-system/okhp3-brandguard/lvmh/",
    }.issubset(expected)
    assert all(path in routes for path in expected)

    coverage_source = source[
        source.index("const BRANDGUARD_FORCED_COLORS_PATHS"):
        source.index("// Retained as a representative route set")
    ]
    node_test = f"""
const vm = require('node:vm');
const routes = {json.dumps(routes + ['/about/'])};
const context = vm.createContext({{
  PUBLIC_PATHS: routes,
  BRANDGUARD_GEOMETRY_PATH: {json.dumps(hub)},
}});
vm.runInContext({json.dumps(coverage_source)}, context);
const shouldCheck = vm.runInContext(
  'shouldCheckBrandGuardForcedColorsReadability',
  context
);
console.log(JSON.stringify(routes.map(path => ({{
  path,
  mobile: shouldCheck(path, 'mobile-320'),
  desktop: shouldCheck(path, 'desktop-1280'),
  tablet: shouldCheck(path, 'tablet-768'),
}}))));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    coverage = {row["path"]: row for row in json.loads(result.stdout)}
    assert all(
        coverage[path]["mobile"] and coverage[path]["desktop"] and
        not coverage[path]["tablet"]
        for path in expected
    )
    assert not coverage[hub]["mobile"] and not coverage[hub]["desktop"]
    assert all(
        not coverage[path]["mobile"] and not coverage[path]["desktop"]
        for path in routes
        if path not in expected and path != hub
    )

    forced_colors_check = source[
        source.index("async function checkBrandGuardForcedColorsReadability"):
        source.index("async function checkUniverseDiagramGeometry")
    ]
    assert "await page.emulateMedia({ forcedColors: 'active' })" in forced_colors_check
    assert "colorScheme: 'light'" in forced_colors_check
    assert "serviceWorkers: 'block'" in forced_colors_check
    assert "matchMedia('(forced-colors: active)').matches" in forced_colors_check
    assert "style.color = 'CanvasText'" in forced_colors_check
    assert "style.backgroundColor = 'Canvas'" in forced_colors_check
    assert "background_layers" in forced_colors_check
    assert "calculateBrandGuardForcedColorsContrast" in source
    assert "foregroundColor: element.contrast_input.foreground_color" in forced_colors_check
    assert "backgroundLayers: element.contrast_input.background_layers" in forced_colors_check
    assert "canvasColor: element.contrast_input.canvas_color" in forced_colors_check
    assert "effectiveOpacity: element.contrast_input.effective_opacity" in forced_colors_check
    assert "minimum_contrast_ratio: 4.5" in source
    assert "await hero.scrollIntoViewIfNeeded()" in forced_colors_check
    assert "getComputedStyle(heroInner).opacity" in forced_colors_check
    assert "brandguard_forced_colors_readability" in source
    assert "brandGuardForcedColorsReadability?.errors" in source
    for selector in (
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ):
        assert selector in source


def test_fallback_font_failures_identify_route_viewport_and_text_block():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    formatter = source[
        source.index("function formatBrandGuardFallbackFontReadabilityResult"):
        source.index("async function checkBrandGuardFallbackFontReadability")
    ]
    content_check = {
        "errors": [
            "BRANDGUARD 400PX CONTENT CLIPPED: .askjamie-main .askjamie-hero-copy h1"
        ],
        "evidence": {"elements": {}},
    }
    blocked_requests = [{
        "host": "fonts.googleapis.com",
        "path": "/css2",
        "resource_type": "stylesheet",
    }]
    node_test = f"""
const vm = require('node:vm');
const context = vm.createContext({{}});
vm.runInContext({json.dumps(formatter)}, context);
const format = vm.runInContext('formatBrandGuardFallbackFontReadabilityResult', context);
const result = format(
  {json.dumps(content_check)},
  {json.dumps(blocked_requests)},
  '/lens-system/okhp3-brandguard/lvmh/',
  400
);
const unverified = format({json.dumps(content_check)}, [], '/lens-system/okhp3-brandguard/lvmh/', 401);
console.log(JSON.stringify({{result, unverified}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    output = json.loads(result.stdout)
    failure = output["result"]["errors"][0]
    assert "/lens-system/okhp3-brandguard/lvmh/" in failure
    assert "viewport=400px" in failure
    assert ".askjamie-main .askjamie-hero-copy h1" in failure
    assert output["result"]["evidence"]["web_font_requests_blocked"] is True
    assert output["result"]["evidence"]["blocked_requests"] == blocked_requests
    assert any(
        "route=/lens-system/okhp3-brandguard/lvmh/" in error and
        "viewport=401px" in error and
        "stylesheet request was not observed and blocked" in error
        for error in output["unverified"]["errors"]
    )


def test_brandguard_font_content_at_350_and_370_is_checked_in_both_font_states():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    content_check = source[
        source.index("async function checkBrandGuardNarrowContent"):
        source.index("async function checkUniverseDiagramGeometry")
    ]

    assert "BRANDGUARD_FONT_CONTENT_VIEWPORT_WIDTHS.has(viewportWidth)" in font_check
    assert "await checkBrandGuardNarrowContent(page, viewportWidth)" in font_check
    assert font_check.count("await checkBrandGuardNarrowContent(page, viewportWidth)") == 2
    assert "BRANDGUARD ${viewportWidth}PX FONT CONTENT CHECK FAILED (fallback-font state)" in font_check
    assert "const afterFontStateName = fontEvidenceAvailable" in font_check
    assert "? 'branded-font state'" in font_check
    assert "FONT CONTENT CHECK FAILED (${afterFontStateName})" in font_check
    assert "narrow_content_during_font_load" in font_check
    assert "viewport_width_px: viewportWidth" in font_check
    assert "document_overflow:" in content_check
    assert "CONTENT CLIPPED:" in content_check
    assert "VIEWPORT OVERFLOW:" in content_check
    assert "BREADCRUMB OVERLAPS HERO:" in content_check
    assert "with ${BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX}px tolerance" in font_check
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    for stylesheet in ("assets/css/theme.css", "assets/css/critical-hero.css"):
        css = (ROOT / stylesheet).read_text(encoding="utf-8")
        rule_start = css.index("@media (min-width: 341px) and (max-width: 370px)")
        rule_end = css.index("\n}", rule_start) + 2
        narrow_breadcrumb = css[rule_start:rule_end]
        assert ".askjamie-breadcrumb .breadcrumb-label" in narrow_breadcrumb
        assert "white-space: normal" in narrow_breadcrumb
        assert "min-width: 0" in narrow_breadcrumb
        assert "min-height: 14.6rem" in narrow_breadcrumb


def test_brandguard_font_reservation_edges_cover_349_and_371_on_three_routes():
    import re

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    viewport_block = re.search(r"const VIEWPORTS = \[(.*?)\n\];", source, re.DOTALL)
    assert viewport_block
    assert re.search(
        r"\{ name: 'mobile-349-reservation-edge',\s+width: 349,",
        viewport_block.group(1),
    )
    assert re.search(
        r"\{ name: 'mobile-371-reservation-edge',\s+width: 371,",
        viewport_block.group(1),
    )

    reservation_paths = re.search(
        r"const BRANDGUARD_FONT_RESERVATION_EDGE_PATHS = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    assert reservation_paths
    assert set(re.findall(r"'([^']+)'", reservation_paths.group(1))) == {
        "/lens-system/okhp3-brandguard/",
        "/lens-system/okhp3-brandguard/lvmh/",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
    }
    reservation_viewports = re.search(
        r"const BRANDGUARD_FONT_RESERVATION_EDGE_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert reservation_viewports
    assert set(re.findall(r"'([^']+)'", reservation_viewports.group(1))) == {
        "mobile-349-reservation-edge",
        "mobile-371-reservation-edge",
    }

    assert "BRANDGUARD_FONT_RESERVATION_EDGE_GEOMETRY_PATHS.has(path)" in source
    assert "BRANDGUARD_FONT_RESERVATION_EDGE_VIEWPORTS.has(vp.name)" in source
    assert re.search(
        r"const checkBrandGuardFonts =[\s\S]*?checkBrandGuardFontReservationEdges;",
        source,
    )
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    for selector in (
        "'.askjamie-main .askjamie-breadcrumb'",
        "'.askjamie-main .askjamie-hero-copy h1'",
        "'.askjamie-main .askjamie-hero-copy .hero-subtitle'",
        "'.askjamie-main .askjamie-hero-copy .hero-tagline'",
    ):
        assert selector in source
    assert "font_resource_failures: fontResourceFailures" in source
    assert "font_resources: fontResponses" in source
    assert "--brandguard-font-reservation-edge-only" in source

    theme = (ROOT / "assets" / "css" / "theme.css").read_text(encoding="utf-8")
    for width in (349, 371):
        media = f"@media (min-width: {width}px) and (max-width: {width}px)"
        assert media in theme
    assert ".askjamie-main.askjamie-brandguard-page .capability-transition > h2" in theme
    assert ".askjamie-main.brandguard-lvmh-page .askjamie-hero-copy h1" in theme
    assert ".askjamie-main.brandguard-bfs-page .askjamie-hero-copy .hero-subtitle" in theme


def test_brandguard_touch_targets_are_checked_at_phone_widths_and_font_states():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function captureBrandGuardTouchTargets")
    ]
    capture = source[
        source.index("async function captureBrandGuardTouchTargets"):
        source.index("function validateBrandGuardTouchTargets")
    ]
    evidence = source[
        source.index("touch_targets_during_font_load"):
        source.index("async function captureBrandGuardTouchTargets")
    ]

    assert (
        "const BRANDGUARD_TOUCH_TARGET_VIEWPORT_WIDTHS = "
        "new Set([320, 350, 370, 390])"
    ) in source
    assert "const BRANDGUARD_TOUCH_TARGET_MINIMUM_PX = 44" in source
    assert "BRANDGUARD_TOUCH_TARGET_VIEWPORT_WIDTHS.has(viewportWidth)" in font_check
    assert "const beforeTouchTargets = checkTouchTargets" in font_check
    assert "const afterTouchTargets = checkTouchTargets" in font_check
    assert "validateBrandGuardTouchTargets(" in font_check
    assert font_check.count("validateBrandGuardTouchTargets(") == 4
    assert "fallback_font: beforeTouchTargets" in evidence
    assert "branded_font: afterTouchTargets" in evidence
    assert "route: new URL(page.url()).pathname" in evidence
    assert "? 'main a.brandguard-case-card[href]'" in capture
    assert ": 'main a.btn[href]'" in capture
    assert "const openMobileNavigation = async" in font_check
    assert "if (includeHeaderNavigation || checkTouchTargets)" in font_check
    assert "captureBrandGuardTouchTargets(page, isBrandGuardHub, true)" in font_check
    assert "mobile_menu_touch_targets_during_font_load" in evidence
    assert "fallback_font: beforeMenuTouchTargets" in evidence
    assert "branded_font: afterMenuTouchTargets" in evidence
    assert "'mobile-menu'" in font_check
    assert "primary-nav a[href]" in capture
    assert "destination: element.getAttribute('href')" in capture
    assert "visible_menu_link_count" in capture
    assert "visible_target_count: visibleControls.length" in capture
    assert "clipAreaHasNoVisiblePixels(ancestorStyle.clip)" in capture
    assert "firstControl.element.contains(secondControl.element)" in capture


def test_brandguard_open_menu_targets_cover_every_sitemap_route_at_phone_widths():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sitemap_paths = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert sitemap_paths
    config = source[
        source.index("const BRANDGUARD_GEOMETRY_PATH ="):
        source.index("const BRANDGUARD_NARROW_CONTENT_VIEWPORT =")
    ]
    route_predicate = source[
        source.index("    const checkBrandGuardAllRouteFonts ="):
        source.index("    const checkUniverseDiagram =")
    ]
    routes = sitemap_paths + ["/lens-system/okhp3-brandguard/unlisted-case/"]
    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve,
  PUBLIC_PATHS: {json.dumps(sitemap_paths)} }};
const config = {json.dumps(config)};
const predicate = {json.dumps(route_predicate)};
const paths = {json.dumps(routes)};
const matrix = Object.fromEntries([
  'mobile-320', 'mobile-350-edge', 'mobile-370-edge', 'mobile-390'
].map(name => [
  name,
  paths.map(path => vm.runInNewContext(config + '\\n' + predicate +
    '\\ncheckBrandGuardFonts', {{ ...runtime, path, vp: {{ name }} }}))
]));
console.log(JSON.stringify(matrix));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    matrix = json.loads(result.stdout)
    for viewport in (
        "mobile-320",
        "mobile-350-edge",
        "mobile-370-edge",
        "mobile-390",
    ):
        assert matrix[viewport] == [True] * len(sitemap_paths) + [False]

    capture = source[
        source.index("async function captureBrandGuardTouchTargets"):
        source.index("function validateBrandGuardTouchTargets")
    ]
    assert ".primary-nav a[href]" in capture
    assert "menu_open: Boolean(" in capture
    assert "visible_menu_link_count" in capture
    assert "destination: element.getAttribute('href')" in capture
    assert "target: Object.fromEntries(" in capture

    validator = source[
        source.index("function validateBrandGuardTouchTargets"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    closed_menu = {
        "viewport_width_px": 350,
        "menu_open": False,
        "visible_menu_link_count": 0,
        "visible_target_count": 1,
        "targets": [],
        "overlaps": [],
    }
    node_test = f"""
const vm = require('node:vm');
const context = vm.createContext({{}});
vm.runInContext('const BRANDGUARD_TOUCH_TARGET_MINIMUM_PX = 44;\\n' +
  {json.dumps(validator)}, context);
const validate = vm.runInContext('validateBrandGuardTouchTargets', context);
console.log(JSON.stringify(validate({json.dumps(closed_menu)},
  'fallback-font state', 350, '/lens-system/okhp3-brandguard/lvmh/', 'mobile-menu')));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    failures = json.loads(result.stdout)
    assert len(failures) == 2
    assert "MOBILE MENU CLOSED" in failures[0]
    assert "MOBILE MENU LINKS MISSING" in failures[1]
    assert "350PX MOBILE MENU TOUCH TARGET" in failures[0]
    assert all("fallback-font state" in failure for failure in failures)
    assert all("/lens-system/okhp3-brandguard/lvmh/" in failure for failure in failures)


def test_brandguard_mobile_menu_distinct_destinations_are_followed_and_reported():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    predicate = source[
        source.index("const BRANDGUARD_MOBILE_MENU_DESTINATION_PATHS"):
        source.index("const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS")
    ]
    source_routes = sorted({
        location.text.strip().removeprefix("https://askjamie.bot")
        for location in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if location.text and
        location.text.strip().startswith(
            "https://askjamie.bot/lens-system/okhp3-brandguard/"
        )
    })
    assert len(source_routes) > 3
    node_test = f"""
const vm = require('node:vm');
const {{ shouldCheck, menuRouteCount }} = vm.runInNewContext(
  {json.dumps(predicate)} +
    '\\n({{ shouldCheck: shouldCheckBrandGuardMobileMenuDestinations, ' +
    'menuRouteCount: BRANDGUARD_MOBILE_MENU_DESTINATION_PATHS.size }})',
  {{
    BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/',
    PUBLIC_PATHS: {json.dumps(source_routes)},
  }}
);
console.log(JSON.stringify({{
  menuRouteCount,
  hub320: shouldCheck('/lens-system/okhp3-brandguard/', 'mobile-320'),
  lvmh320: shouldCheck('/lens-system/okhp3-brandguard/lvmh/', 'mobile-320'),
  bfs320: shouldCheck(
    '/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/',
    'mobile-320'
  ),
  hub390: shouldCheck('/lens-system/okhp3-brandguard/', 'mobile-390'),
  other320: shouldCheck('/lens-system/okhp3-brandguard/lego/', 'mobile-320'),
}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    assert json.loads(result.stdout) == {
        "menuRouteCount": len(source_routes),
        "hub320": True,
        "lvmh320": False,
        "bfs320": False,
        "hub390": False,
        "other320": False,
    }

    check = source[
        source.index("async function checkBrandGuardMobileMenuDestinations"):
        source.index("async function checkBrandGuardHeroActionAccessibleNames")
    ]
    for required in (
        "const destinations = new Map()",
        "source_route: sourceRoute",
        "label: linkEvidence.label",
        "expected_destination: expectedDestination",
        "context.waitForEvent('page'",
        "modifiers: ['ControlOrMeta']",
        "destinationPage.waitForURL(",
        "fragmentFound",
        "isNotFoundDocument(",
        "reached_destination:",
        "result: 'failed'",
        "result.result = 'passed'",
        "http_status:",
    ):
        assert required in check

    assert "checkBrandGuardMobileMenuDestinations(page)" in source
    assert "brandguard_mobile_menu_destinations:" in source
    assert "brandGuardMobileMenuDestinations?.errors" in source
    assert "follows each distinct local destination" in source


def test_brandguard_touch_target_failures_name_route_stage_and_controls():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    validator = source[
        source.index("function validateBrandGuardTouchTargets"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    failing_evidence = {
        "viewport_width_px": 350,
        "visible_target_count": 2,
        "targets": [
            {
                "key": "a#1",
                "tag": "a",
                "label": "Case study",
                "href": "/case-study/",
                "destination": "/case-study/",
                "target": {
                    "left": 10,
                    "right": 50,
                    "top": 20,
                    "bottom": 64,
                    "width": 40,
                    "height": 44,
                },
            },
            {
                "key": "button#2",
                "tag": "button",
                "label": "Open menu",
                "href": None,
                "destination": None,
                "target": {
                    "left": 10,
                    "right": 54,
                    "top": 30,
                    "bottom": 74,
                    "width": 44,
                    "height": 44,
                },
            },
        ],
        "overlaps": [
            {
                "first": {
                    "key": "a#1",
                    "label": "Case study",
                    "destination": "/case-study/",
                    "target": {
                        "left": 10,
                        "right": 50,
                        "top": 20,
                        "bottom": 64,
                        "width": 40,
                        "height": 44,
                    },
                },
                "second": {
                    "key": "button#2",
                    "label": "Open menu",
                    "destination": None,
                    "target": {
                        "left": 10,
                        "right": 54,
                        "top": 30,
                        "bottom": 74,
                        "width": 44,
                        "height": 44,
                    },
                },
                "overlap_width_px": 40,
                "overlap_height_px": 34,
            },
        ],
    }
    node_test = f"""
const vm = require('node:vm');
const validator = {json.dumps(validator)};
const context = vm.createContext({{}});
vm.runInContext('const BRANDGUARD_TOUCH_TARGET_MINIMUM_PX = 44;\\n' + validator, context);
const check = vm.runInContext(
  'validateBrandGuardTouchTargets',
  context
);
const failures = check({json.dumps(failing_evidence)}, 'fallback-font state', 350,
  '/lens-system/okhp3-brandguard/lvmh/');
console.log(JSON.stringify(failures));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    errors = json.loads(result.stdout)
    assert len(errors) == 2
    assert all("/lens-system/okhp3-brandguard/lvmh/" in error for error in errors)
    assert all("fallback-font state" in error for error in errors)
    assert "Case study" in errors[0]
    assert "TOO SMALL" in errors[0]
    assert "/case-study/" in errors[0]
    assert '"destination":"/case-study/"' in errors[0]
    assert '"width":40' in errors[0]
    assert "TARGETS OVERLAP" in errors[1]
    assert '"destination":"/case-study/"' in errors[1]
    assert '"width":40' in errors[1]
    assert "stage=fallback-font state" in errors[1]


def test_brandguard_header_navigation_font_geometry_covers_every_boundary_route():
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    for name, width in (
        ("mobile-355-boundary", 355),
        ("mobile-365-boundary", 365),
    ):
        assert f"{{ name: '{name}', width: {width}, height: 780 }}" in source

    nav_viewports = re.search(
        r"const BRANDGUARD_FONT_NAV_BOUNDARY_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert nav_viewports
    assert set(re.findall(r"'([^']+)'", nav_viewports.group(1))) == {
        "mobile-355-boundary",
        "mobile-365-boundary",
    }

    sitemap_paths = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert sitemap_paths
    test_routes = sitemap_paths + [
        "/lens-system/okhp3-brandguard/unlisted-case/",
        "/about/",
    ]
    config = source[
        source.index("const BRANDGUARD_GEOMETRY_PATH ="):
        source.index("const BRANDGUARD_NARROW_CONTENT_VIEWPORT =")
    ]
    route_predicate = source[
        source.index("    const checkBrandGuardAllRouteFonts ="):
        source.index("    const checkUniverseDiagram =")
    ]
    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve,
  PUBLIC_PATHS: {json.dumps(sitemap_paths)} }};
const config = {json.dumps(config)};
const predicate = {json.dumps(route_predicate)};
const routes = {json.dumps(test_routes)};
const viewports = ['mobile-320', 'mobile-355-boundary', 'mobile-365-boundary',
  'mobile-350-edge', 'mobile-356-gap', 'mobile-357-font-gap',
  'mobile-358-font-gap', 'mobile-359-font-gap', 'mobile-360',
  'mobile-361-font-gap', 'mobile-362-font-gap', 'mobile-363-font-gap',
  'mobile-364-gap', 'mobile-370-edge'];
const matrix = Object.fromEntries(viewports.map(name => [name, routes.map(path =>
  vm.runInNewContext(config + '\\n' + predicate +
    '\\n({{ checkBrandGuardFonts, includeBrandGuardHeaderNavigation }})',
    {{ ...runtime, path, vp: {{ name }} }}))]));
console.log(JSON.stringify(matrix));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    matrix = json.loads(result.stdout)
    expected = [True] * len(sitemap_paths) + [False, False]
    assert [item["checkBrandGuardFonts"] for item in matrix["mobile-320"]] == expected
    assert [item["includeBrandGuardHeaderNavigation"] for item in matrix["mobile-320"]] == expected
    for viewport in ("mobile-355-boundary", "mobile-365-boundary"):
        assert [item["includeBrandGuardHeaderNavigation"] for item in matrix[viewport]] == expected
    for viewport in (
        "mobile-350-edge",
        "mobile-370-edge",
    ):
        assert [item["includeBrandGuardHeaderNavigation"] for item in matrix[viewport]] == [
            route in {
                "/lens-system/okhp3-brandguard/",
                "/lens-system/okhp3-brandguard/lvmh/",
                "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
            }
            for route in test_routes
        ]
    for viewport in (
        "mobile-356-gap",
        "mobile-357-font-gap",
        "mobile-358-font-gap",
        "mobile-359-font-gap",
        "mobile-360",
        "mobile-361-font-gap",
        "mobile-362-font-gap",
        "mobile-363-font-gap",
        "mobile-364-gap",
    ):
        assert [item["includeBrandGuardHeaderNavigation"] for item in matrix[viewport]] == [
            False
        ] * len(test_routes)

    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS.has(path)" in source
    assert "checkBrandGuardBoundaryFonts &&" in source
    assert "await openMobileNavigation()" in source
    assert "before: headerNavigationBefore" in source
    assert "after: headerNavigationAfter" in source
    assert "font_evidence_status:" in source
    assert "font_resources: fontResponses" in source
    assert "font_resource_failures: fontResourceFailures" in source
    assert "[...BRANDGUARD_FONT_FAMILIES, 'Open Sans']" in source
    assert "matchingFaces.every(face => face.status === 'loaded')" in source
    assert "all_navigation_labels_required: viewportWidth === 320" in source
    assert "no_label_wrapping_required: viewportWidth === 320" in source
    assert "horizontal_overflow_checked: viewportWidth === 320" in source
    assert "brandguard_header_navigation_font_geometry:" in source
    assert "key !== 'header_navigation'" in source


def test_responsive_qa_checks_brandguard_font_geometry_at_350_and_370_edges():
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    assert "{ name: 'mobile-350-edge', width: 350, height: 780 }" in source
    assert "{ name: 'mobile-370-edge', width: 370, height: 780 }" in source
    assert "{ name: 'mobile-356-gap', width: 356, height: 780 }" in source
    assert "{ name: 'mobile-364-gap', width: 364, height: 780 }" in source
    assert "BRANDGUARD_FONT_EDGE_GEOMETRY_PATHS = new Set([" in source
    assert "'/lens-system/okhp3-brandguard/'" in source
    assert "'/lens-system/okhp3-brandguard/lvmh/'" in source
    assert "'/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/'" in source
    edge_viewports = re.search(
        r"const BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert edge_viewports
    assert set(re.findall(r"'([^']+)'", edge_viewports.group(1))) == {
        "mobile-350-edge",
        "mobile-370-edge",
    }
    representative_routes = re.search(
        r"const BRANDGUARD_FONT_EDGE_GEOMETRY_PATHS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert representative_routes
    assert set(re.findall(r"'([^']+)'", representative_routes.group(1))) == {
        "/lens-system/okhp3-brandguard/lvmh/",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
    }
    assert "  BRANDGUARD_GEOMETRY_PATH," in representative_routes.group(1)
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS.has(path)" in source
    assert "BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source

    sitemap_paths = sorted({
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    })
    assert sitemap_paths
    routes = sitemap_paths + [
        "/lens-system/okhp3-brandguard/future-sitemap-case/",
        "/about/",
        "/lens-system/okhp3-brandguard/unlisted-case/",
    ]
    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const runtime = {{ ROOT: process.cwd(), existsSync: fs.existsSync,
  readFileSync: fs.readFileSync, resolve: pathApi.resolve }};
const config = source.slice(source.indexOf('const BRANDGUARD_GEOMETRY_PATH ='),
  source.indexOf('const BRANDGUARD_NARROW_CONTENT_VIEWPORT ='));
const predicates = source.slice(source.indexOf('    const checkBrandGuardAllRouteFonts ='),
  source.indexOf('    const checkUniverseDiagram ='));
const routes = {json.dumps(routes)};
const publicPaths = routes.slice(0, -1);
const viewports = ['mobile-320', 'mobile-350-edge', 'mobile-355-boundary',
  'mobile-356-gap', 'mobile-360', 'mobile-364-gap', 'mobile-365-boundary',
  'mobile-370-edge', 'mobile-390',
  'mobile-400-edge', 'mobile-401-edge',
  'tablet-768', 'tablet-899', 'mobile-430', 'desktop-1280'];
const matrix = Object.fromEntries(viewports.map(name => [name, routes.map(path =>
  vm.runInNewContext(config + '\\n' + predicates +
            '\\n({{ allRoutes: checkBrandGuardAllRouteFonts, boundary: checkBrandGuardBoundaryFonts, gap: checkBrandGuardGapFonts, measured: checkBrandGuardFonts }})',
    {{ ...runtime, PUBLIC_PATHS: publicPaths, path, vp: {{ name }} }}))]));
const unlistedRepresentative = vm.runInNewContext(config + '\\n' + predicates + '\\ncheckBrandGuardFonts',
  {{ ...runtime, PUBLIC_PATHS: publicPaths.filter(path => path !== '/lens-system/okhp3-brandguard/lvmh/'),
     path: '/lens-system/okhp3-brandguard/lvmh/', vp: {{ name: 'mobile-355-boundary' }} }});
console.log(JSON.stringify({{matrix, unlistedRepresentative}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    evaluated = json.loads(result.stdout)
    matrix = evaluated["matrix"]
    assert evaluated["unlistedRepresentative"] is False
    expected = [True] * len(sitemap_paths) + [True, False, False]
    expected_boundary = [
        route.startswith("/lens-system/okhp3-brandguard/") and route != routes[-1]
        for route in routes
    ]
    for rows in matrix.values():
        assert all(
            row["measured"] == (row["allRoutes"] or row["boundary"] or row["gap"])
            for row in rows
        )
    for viewport in ("mobile-350-edge", "mobile-370-edge"):
        assert [row["allRoutes"] for row in matrix[viewport]] == expected
        assert [row["boundary"] for row in matrix[viewport]] == [False] * len(routes)
    for viewport in ("mobile-355-boundary", "mobile-365-boundary"):
        assert [row["allRoutes"] for row in matrix[viewport]] == [False] * len(routes)
        assert [row["boundary"] for row in matrix[viewport]] == expected_boundary
        assert [row["gap"] for row in matrix[viewport]] == [False] * len(routes)
        assert [row["measured"] for row in matrix[viewport]] == expected_boundary
    expected_gap = [
        route in {
            "/lens-system/okhp3-brandguard/",
            "/lens-system/okhp3-brandguard/lvmh/",
            "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
        }
        for route in routes
    ]
    for viewport in ("mobile-356-gap", "mobile-364-gap"):
        assert [row["allRoutes"] for row in matrix[viewport]] == [False] * len(routes)
        assert [row["boundary"] for row in matrix[viewport]] == [False] * len(routes)
        assert [row["gap"] for row in matrix[viewport]] == expected_gap
        assert [row["measured"] for row in matrix[viewport]] == expected_gap
    for viewport in (
        "mobile-320",
        "mobile-360",
        "mobile-390",
        "mobile-400-edge",
        "mobile-401-edge",
        "tablet-768",
        "tablet-899",
    ):
        assert [row["allRoutes"] for row in matrix[viewport]] == expected
        assert [row["boundary"] for row in matrix[viewport]] == [False] * len(routes)
    for viewport in ("mobile-430", "desktop-1280"):
        assert [row["allRoutes"] for row in matrix[viewport]] == [False] * len(routes)
        assert [row["boundary"] for row in matrix[viewport]] == [False] * len(routes)

    assert "{ name: 'mobile-355-boundary', width: 355, height: 780 }" in source
    assert "{ name: 'mobile-365-boundary', width: 365, height: 780 }" in source
    gap_viewports = re.search(
        r"const BRANDGUARD_FONT_GAP_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert gap_viewports
    assert set(re.findall(r"'([^']+)'", gap_viewports.group(1))) == {
        "mobile-356-gap",
        "mobile-357-font-gap",
        "mobile-358-font-gap",
        "mobile-359-font-gap",
        "mobile-360",
        "mobile-361-font-gap",
        "mobile-362-font-gap",
        "mobile-363-font-gap",
        "mobile-364-gap",
    }
    gap_routes = re.search(
        r"const BRANDGUARD_FONT_GAP_GEOMETRY_PATHS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert gap_routes
    assert set(re.findall(r"'([^']+)'", gap_routes.group(1))) == {
        "/lens-system/okhp3-brandguard/lvmh/",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
    }
    assert "BRANDGUARD_GEOMETRY_PATH," in gap_routes.group(1)
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS" in source
    assert "font_loading: fontLoading" in source
    boundary_viewports = re.search(
        r"const BRANDGUARD_FONT_BOUNDARY_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert boundary_viewports
    assert set(re.findall(r"'([^']+)'", boundary_viewports.group(1))) == {
        "mobile-355-boundary",
        "mobile-365-boundary",
    }


def test_brandguard_font_reservations_are_scoped_to_measured_edge_widths():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")

    assert "@media (min-width: 350px) and (max-width: 355px)" in theme
    assert "@media (min-width: 365px) and (max-width: 370px)" in theme
    assert (
        "@media (min-width: 356px) and (max-width: 356px) {\n"
        "  .askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 6lh;"
    ) in theme
    assert (
        ".askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1 {\n"
        "    min-height: 4lh;"
    ) in theme
    assert (
        "@media (min-width: 364px) and (max-width: 364px) {\n"
        "  .askjamie-main.brandguard-bfs-page .capability-transition > p:nth-of-type(2),\n"
        "  .askjamie-main.brandguard-lvmh-page .capability-transition > p:nth-of-type(2) {\n"
        "    min-height: 7lh;"
    ) in theme
    assert (
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1,\n"
        "  .askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1,\n"
        "  .askjamie-main.brandguard-case-lego .askjamie-hero-copy h1 {\n"
        "    min-height: 4lh;"
    ) in theme
    assert ".askjamie-main.brandguard-lvmh-page .askjamie-hero-copy h1" in theme
    assert ".askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1" in theme
    assert ".askjamie-main.brandguard-bfs-page .capability-transition > p:nth-of-type(2)" in theme
    assert ".askjamie-main.brandguard-lvmh-page .capability-transition > p:nth-of-type(2)" in theme


def test_brandguard_font_reservations_cover_400_and_401_pixel_edges():
    import re

    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    measured_edge_rules = re.search(
        r"@media \(min-width: 401px\) and \(max-width: 401px\) \{(.*?)\n\}",
        theme,
        re.DOTALL,
    )

    assert measured_edge_rules
    assert "@media (min-width: 381px) and (max-width: 400px)" in theme
    for reservation in (
        ".askjamie-main.brandguard-case-costco .askjamie-hero-copy .hero-tagline {\n"
        "    min-height: 6lh;",
        ".askjamie-main.brandguard-case-dollar-general .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 2lh;",
        ".askjamie-main.brandguard-case-dollar-general .askjamie-hero-copy .hero-tagline {\n"
        "    min-height: 6lh;",
    ):
        assert reservation in theme

    for reservation in (
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 6lh;",
        ".askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1 {\n"
        "    min-height: 4lh;",
        ".askjamie-main.brandguard-case-discount-tire .askjamie-hero-copy h1 {\n"
        "    min-height: 3lh;",
        ".askjamie-main.brandguard-case-hershey .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 8lh;",
        ".askjamie-main.brandguard-case-mathews-archery .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 7lh;",
        ".askjamie-main.brandguard-case-scheels .askjamie-hero-copy .hero-subtitle {\n"
        "    min-height: 7lh;",
        ".askjamie-main.brandguard-lvmh-page .askjamie-hero-copy .hero-tagline {\n"
        "    min-height: 3lh;",
    ):
        assert reservation in measured_edge_rules.group(1)

def test_brandguard_breadcrumbs_wrap_at_400_and_401_with_fallback_fonts():
    rule = (
        "@media (min-width: 400px) and (max-width: 401px) {\n"
        "  .askjamie-main[class*=\"brandguard\"] .askjamie-breadcrumb {\n"
        "    min-height: 14.54rem;\n"
        "  }\n\n"
        "  .askjamie-main[class*=\"brandguard\"] .askjamie-breadcrumb .breadcrumb-label {\n"
        "    flex: 1 1 100%;\n"
        "    min-width: 0;\n"
        "    white-space: normal;\n"
        "    overflow-wrap: anywhere;\n"
        "  }\n\n"
        "  .askjamie-main[class*=\"brandguard\"] .askjamie-breadcrumb .breadcrumb-separator {\n"
        "    display: none;\n"
        "  }\n"
        "}"
    )
    for stylesheet in ("assets/css/theme.css", "assets/css/critical-hero.css"):
        css = (ROOT / stylesheet).read_text(encoding="utf-8")
        assert rule in css

def test_brandguard_breadcrumb_text_stays_inside_350_and_370_pixel_edges():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    responsive_qa = (ROOT / "scripts" / "responsive-qa.mjs").read_text(
        encoding="utf-8"
    )
    narrow_rule = theme[
        theme.index("@media (min-width: 341px) and (max-width: 370px)"):
        theme.index("/* Keep longer BrandGuard breadcrumb labels")
    ]

    assert ".askjamie-main.askjamie-brandguard-page .askjamie-breadcrumb" in narrow_rule
    assert ".askjamie-main:has(.brandguard-demo-notice) .askjamie-breadcrumb" in narrow_rule
    assert "flex: 1 1 100%;" in narrow_rule
    assert "min-width: 0;" in narrow_rule
    assert "max-width: 100%;" in narrow_rule
    assert "overflow-wrap: anywhere;" in narrow_rule
    assert "rendered_text_outside_viewport" in responsive_qa
    assert "BRANDGUARD_FONT_CONTENT_VIEWPORT_WIDTHS = new Set([320, 350, 370])" in responsive_qa
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in responsive_qa
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in responsive_qa
    assert "const BRANDGUARD_EDGE_ONLY = process.argv.includes('--brandguard-edge-only')" in responsive_qa
    assert "vp.name === 'mobile-350-edge' || vp.name === 'mobile-370-edge'" in responsive_qa
    assert "brandguard-edge-results.json" in responsive_qa


def test_brandguard_breadcrumb_keyboard_focus_covers_published_phone_edges():
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    focus_rule = theme[
        theme.index("@media (min-width: 341px) and (max-width: 370px)"):
        theme.index("/* Keep longer BrandGuard breadcrumb labels")
    ]
    assert ".askjamie-main[class*=\"brandguard\"] .askjamie-breadcrumb a:focus-visible" in focus_rule
    assert ".askjamie-main:has(.brandguard-demo-notice) .askjamie-breadcrumb a:focus-visible" in focus_rule
    assert "outline: 3px solid var(--color-accent, #2d6f7e);" in focus_rule
    assert "outline-offset: 3px;" in focus_rule
    assert "scroll-margin-block: 8px;" in focus_rule

    check = source[
        source.index("async function checkBrandGuardBreadcrumbKeyboardFocus"):
        source.index("function formatBrandGuardFallbackFontReadabilityResult")
    ]
    for evidence in (
        "await page.keyboard.press('Tab')",
        "document.activeElement === preceding",
        "target.matches(':focus-visible')",
        "viewport_clipped",
        "clipping_ancestors",
        "hero_overlap",
        "accessible_name",
        "aria_current !== 'page'",
        "resolved_href",
    ):
        assert evidence in check
    assert "shouldCheckBrandGuardBreadcrumbKeyboardFocus(path, vp.name)" in source
    assert "brandguard_breadcrumb_keyboard_focus:" in source

    sitemap = ET.parse(ROOT / "sitemap.xml").getroot()
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    routes = [
        re.sub(r"^https://askjamie\.bot", "", node.text or "")
        for node in sitemap.findall(".//s:loc", namespace)
        if (node.text or "").startswith("https://askjamie.bot/lens-system/okhp3-brandguard/")
    ]
    assert routes
    predicate = source[
        source.index("const BRANDGUARD_BREADCRUMB_KEYBOARD_FOCUS_VIEWPORTS"):
        source.index("function shouldCheckBrandGuardHeroKeyboardFocus")
    ]
    node_test = r"""
const vm = require('node:vm');
const routes = JSON.parse(process.argv[1]);
const context = vm.createContext({
  PUBLIC_PATHS: routes,
  BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/'
});
vm.runInContext(PREDICATE, context);
const check = vm.runInContext('shouldCheckBrandGuardBreadcrumbKeyboardFocus', context);
console.log(JSON.stringify({
  at350: routes.map(path => check(path, 'mobile-350-edge')),
  at370: routes.map(path => check(path, 'mobile-370-edge')),
  otherWidth: routes.map(path => check(path, 'mobile-360')),
  unpublished: check('/lens-system/okhp3-brandguard/unpublished/', 'mobile-350-edge')
}));
""".replace("PREDICATE", json.dumps(predicate))
    result = subprocess.run(
        ["node", "-e", node_test, json.dumps(routes)],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    matrix = json.loads(result.stdout)
    assert matrix == {
        "at350": [True] * len(routes),
        "at370": [True] * len(routes),
        "otherWidth": [False] * len(routes),
        "unpublished": False,
    }

    published_html = [
        ROOT / "lens-system/okhp3-brandguard/index.html",
        *sorted((ROOT / "lens-system/okhp3-brandguard").glob("*/index.html")),
    ]
    for page in published_html:
        html = page.read_text(encoding="utf-8")
        assert re.search(
            r'<span\b(?=[^>]*\bclass=["\'][^"\']*\bbreadcrumb-label\b)' +
            r'(?=[^>]*\baria-current=["\']page["\'])[^>]*>',
            html,
        ), f"{page.relative_to(ROOT)} has no accessible current-page breadcrumb"
    template = (ROOT / "assets/templates/template--case-study.html").read_text(
        encoding="utf-8"
    )
    assert 'class="breadcrumb-label" aria-current="page"' in template
def test_responsive_qa_measures_brandguard_geometry_after_branded_fonts_load():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000" in source
    assert "fontStylesheetMode === 'active'" in source
    assert "const activeFontStylesheet = fontLink ??" in source
    assert "after will still be measured" in source
    assert "fonts.googleapis.com', 'fonts.gstatic.com" in source
    assert "requestType === 'font'" in source
    assert "brandGuardFontGate.then(() => route.continue())" in source
    assert "BRANDGUARD WEB FONT GEOMETRY SHIFT" in source
    assert "before=${JSON.stringify(beforeRect)}" in source
    assert "after=${JSON.stringify(afterRect)}" in source
    assert "font_asset_loaded=${hasFontAsset}" in source
    assert "hero_font_geometry" in source


def test_brandguard_320_font_swap_records_wraps_and_checks_content_in_both_states():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("async function checkBrandGuardNarrowContent")
    ]
    narrow_check = source[
        source.index("async function checkBrandGuardNarrowContent"):
        source.index("async function checkUniverseDiagramGeometry")
    ]

    assert "'mobile-320'" in source
    assert "const captureTextLayout" in font_check
    assert "line_count_changes" in font_check
    assert "font_text_layout" in font_check
    assert "block_geometry_threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in font_check
    assert "line_counts_are_diagnostic: true" in font_check
    assert "narrow_content_during_font_load" in font_check
    assert "captureTransitionNotice" in font_check
    assert "arrival_notice_web_font_geometry" in font_check
    assert "BRANDGUARD 320PX ARRIVAL NOTICE WEB FONT GEOMETRY SHIFT" in font_check
    assert "the transition notice panel and children, breadcrumb, and hero were not all reserved" in font_check
    assert "ARRIVAL NOTICE CONTROL NOT READY BEFORE WEB FONT BASELINE" in font_check
    assert "transition_control_present: Boolean(transitionControl)" in font_check
    assert "beforeTransitionNotice.transition_control_present &&" in font_check
    assert "transition_control_reserved" in font_check
    assert "${errorPrefix} ARRIVAL NOTICE OVERLAPS BREADCRUMB" in narrow_check
    assert "notice_overlaps_breadcrumb" in narrow_check
    assert "deferred_theme_geometry_included: false" in font_check
    assert font_check.count("await checkBrandGuardNarrowContent(page, viewportWidth)") == 2
    assert "fallback-font state" in font_check
    assert "branded-font state" in font_check
    before_capture = font_check.index("const before = await capture();")
    assert before_capture < font_check.index("releaseFontRequests();", before_capture)
    assert "hero_overlaps: heroOverlaps" in narrow_check
    assert "${errorPrefix} HERO CONTENT OVERLAPS" in narrow_check
    assert "vertical_content_clipping" in narrow_check
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source


def test_responsive_qa_covers_every_sitemap_brandguard_route_for_font_geometry():
    import html
    import re
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    geometry_selectors = re.search(
        r"const BRANDGUARD_GEOMETRY_SELECTORS = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    font_viewports = re.search(
        r"const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    sample_routes = {
        "/lens-system/okhp3-brandguard/lvmh/":
            ROOT / "lens-system/okhp3-brandguard/lvmh/index.html",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/":
            ROOT / "lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/index.html",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS = new Set(" in source
    assert "PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))" in source
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS.has(path)" in source
    assert font_viewports
    assert {"tablet-769-edge", "tablet-900-edge"} <= set(
        re.findall(r"'([^']+)'", font_viewports.group(1))
    )
    assert "{ name: 'mobile-400-edge', width: 400, height: 780 }" in source
    assert "{ name: 'mobile-401-edge', width: 401, height: 780 }" in source
    assert "mobile-360" in re.findall(r"'([^']+)'", font_viewports.group(1))
    assert geometry_selectors
    assert re.findall(r"'([^']+)'", geometry_selectors.group(1)) == [
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ]
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "const before = await capture();" in source
    assert "const after = await capture();" in source
    assert "Math.abs(delta[property]) > BRANDGUARD_GEOMETRY_TOLERANCE_PX" in source
    sitemap = ET.parse(ROOT / "sitemap.xml")
    sitemap_paths = {
        url.text.removeprefix("https://askjamie.bot")
        for url in sitemap.findall(".//{*}loc")
        if url.text and "/lens-system/okhp3-brandguard/" in url.text
    }
    assert sitemap_paths
    assert "path.startsWith(BRANDGUARD_GEOMETRY_PATH)" in source
    assert all(route in sitemap_paths for route in sample_routes)
    assert "'.askjamie-main .askjamie-hero-copy h1'" in source

    headline_lengths = {}
    for route, page in sample_routes.items():
        content = page.read_text(encoding="utf-8")
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", content, re.DOTALL | re.IGNORECASE)
        assert match, f"{route} has no hero headline"
        headline = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        headline_lengths[route] = len(" ".join(headline.split()))

    case_study_pages = sorted(
        (ROOT / "lens-system" / "okhp3-brandguard").glob("*/index.html")
    )
    all_headline_lengths = {}
    for page in case_study_pages:
        content = page.read_text(encoding="utf-8")
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", content, re.DOTALL | re.IGNORECASE)
        assert match, f"{page} has no hero headline"
        headline = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
        all_headline_lengths[f"/{page.parent.relative_to(ROOT).as_posix()}/"] = len(
            " ".join(headline.split())
        )

    assert headline_lengths["/lens-system/okhp3-brandguard/lvmh/"] == min(
        all_headline_lengths.values()
    )
    assert headline_lengths[
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/"
    ] == max(all_headline_lengths.values())


def test_published_brandguard_case_studies_match_sitemap_routes():
    import xml.etree.ElementTree as ET

    brandguard_root = "/lens-system/okhp3-brandguard/"
    published_routes = {
        f"{brandguard_root}{page.parent.name}/"
        for page in (ROOT / "lens-system" / "okhp3-brandguard").glob("*/index.html")
    }
    sitemap_routes = {
        url.text.removeprefix("https://askjamie.bot")
        for url in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if url.text
        and url.text.removeprefix("https://askjamie.bot").startswith(brandguard_root)
        and url.text.removeprefix("https://askjamie.bot") != brandguard_root
    }

    missing_from_sitemap = sorted(published_routes - sitemap_routes)
    unexpected_in_sitemap = sorted(sitemap_routes - published_routes)
    assert published_routes, "No published BrandGuard case-study pages were found"
    assert not missing_from_sitemap, (
        "Published BrandGuard case-study pages are missing from sitemap.xml: "
        f"{missing_from_sitemap}"
    )
    assert not unexpected_in_sitemap, (
        "BrandGuard sitemap routes have no published case-study page directory: "
        f"{unexpected_in_sitemap}"
    )

    responsive_qa = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    assert "BrandGuard case-study route mismatch:" in responsive_qa
    assert "published pages missing from sitemap.xml:" in responsive_qa
    assert "sitemap routes without a published page directory:" in responsive_qa


def test_responsive_qa_fails_on_missing_or_unexpected_brandguard_routes(tmp_path):
    import shutil
    import subprocess

    script_source = ROOT / "scripts" / "responsive-qa.mjs"
    route = "/lens-system/okhp3-brandguard/future-case-study/"
    cases = (
        ("missing", route, None),
        ("unexpected", None, route),
    )
    for name, local_route, sitemap_route in cases:
        fixture_root = tmp_path / name
        scripts_dir = fixture_root / "scripts"
        scripts_dir.mkdir(parents=True)
        shutil.copy2(script_source, scripts_dir / "responsive-qa.mjs")
        shutil.copy2(
            ROOT / "scripts" / "universe-not-found.mjs",
            scripts_dir / "universe-not-found.mjs",
        )
        shutil.copy2(
            ROOT / "scripts" / "page-map-sweep-performance.mjs",
            scripts_dir / "page-map-sweep-performance.mjs",
        )
        shutil.copy2(
            ROOT / "scripts" / "responsive-qa-warning-trend.mjs",
            scripts_dir / "responsive-qa-warning-trend.mjs",
        )
        shutil.copy2(
            ROOT / "scripts" / "responsive-qa-failure-summary.mjs",
            scripts_dir / "responsive-qa-failure-summary.mjs",
        )
        sitemap_entries = '<url><loc>https://askjamie.bot/about/</loc></url>'
        if sitemap_route:
            sitemap_entries += f'<url><loc>https://askjamie.bot{sitemap_route}</loc></url>'
        (fixture_root / "sitemap.xml").write_text(
            f"<urlset>{sitemap_entries}</urlset>",
            encoding="utf-8",
        )
        if local_route:
            page = fixture_root / local_route.lstrip("/") / "index.html"
            page.parent.mkdir(parents=True)
            page.write_text("<!doctype html><title>Fixture</title>", encoding="utf-8")
        about_page = fixture_root / "about/index.html"
        about_page.parent.mkdir(parents=True)
        about_page.write_text("<!doctype html><title>About</title>", encoding="utf-8")

        result = subprocess.run(
            ["node", "scripts/responsive-qa.mjs", "--static"],
            cwd=fixture_root,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        if name == "missing":
            assert "published pages missing from sitemap.xml:" in result.stderr
            assert route in result.stderr
        else:
            assert f"SKIP  {route} — file not found" in result.stdout
            assert "STATIC LINT INCOMPLETE: skipped sitemap routes were not checked." in result.stdout


def test_responsive_qa_requires_complete_brandguard_390px_geometry_and_font_evidence():
    import re

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    selectors = re.search(
        r"const BRANDGUARD_GEOMETRY_SELECTORS = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    expected_families = re.search(
        r"const BRANDGUARD_FONT_FAMILIES = \[(.*?)\];",
        source,
        re.DOTALL,
    )

    assert selectors
    assert re.findall(r"'([^']+)'", selectors.group(1)) == [
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ]
    assert expected_families
    assert re.findall(r"'([^']+)'", expected_families.group(1)) == [
        "Kalam",
        "Baloo 2",
        "Kalam",
        "Open Sans",
    ]

    # A missing selector or a failed web-font face must fail the browser row;
    # neither condition may be silently omitted from 390px measurements.
    assert "BRANDGUARD WEB FONT GEOMETRY MISSING" in source
    assert "const failedFontChecks = fontLoading?.fonts.filter(font =>" in source
    assert "!font.loaded && font.reason !== 'element missing'" in source
    assert "failedFontChecks.length === 0" in source
    assert "fontResourceFailures.length === 0" in source
    assert "BRANDGUARD WEB FONTS UNAVAILABLE" in source


def test_responsive_qa_measures_universe_page_map_geometry_with_dark_theme_and_320px_usability():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert """UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-390',
  'tablet-768',
  'desktop-1280',
]);""" in source
    assert "{ name: 'tablet-768',   width: 768,  height: 1024 }" in source
    assert "page.emulateMedia({ colorScheme: 'dark' })" in source
    assert "localStorage.setItem('askjamie-color-scheme', 'dark')" in source
    assert "UNIVERSE PAGE MAP DARK THEME NOT ACTIVE" in source
    assert "UNIVERSE PAGE MAP GEOMETRY SHIFT" in source
    assert "UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX" in source
    assert "UNIVERSE PAGE MAP KEYBOARD OPEN FAILED" in source
    assert "UNIVERSE PAGE MAP KEYBOARD CLOSE FAILED" in source
    assert "UNIVERSE PAGE MAP COLLAPSED STATE INVALID" in source
    theme_failure_check = source[
        source.index("async function checkUniversePageMapThemeFailure"):
        source.index("async function checkUniverseMermaidFailure")
    ]
    assert "stylesheetPath = '/assets/css/theme.css'" in theme_failure_check
    assert "await route.fulfill({" in theme_failure_check
    assert "status: 503" in theme_failure_check
    assert "body: '/* controlled theme stylesheet failure */'" in theme_failure_check
    assert "const themeNotice" in theme_failure_check
    assert "noticeDetails?.role !== 'status'" in theme_failure_check
    assert "noticeDetails?.live !== 'polite'" in theme_failure_check
    assert "UNIVERSE PAGE MAP THEME FAILURE KEYBOARD OPEN FAILED" in theme_failure_check
    assert "UNIVERSE PAGE MAP THEME FAILURE KEYBOARD CLOSE FAILED" in theme_failure_check
    assert "accessible_page_links_available: linksAvailable" in theme_failure_check
    assert "theme_readiness: themeReady ? 'ready' : 'unavailable'" in theme_failure_check
    assert "geometry_comparison_available: false" in theme_failure_check
    assert "checkUniversePageMapThemeFailure(browser, vp, url)" in source
    assert "universe_page_map_theme_failure" in source
    assert "async function checkUniversePageMapThemeSwitch" in source
    assert "await checkUniversePageMapThemeSwitch(" in source
    assert "const clicksToDark = { auto: 2, light: 1, dark: 0 };" in source
    assert "UNIVERSE PAGE MAP THEME SWITCH BASELINE NOT RESTORED" in source
    assert "UNIVERSE PAGE MAP THEME SWITCH GEOMETRY SHIFT" in source
    assert "UNIVERSE PAGE MAP CLOSE/REOPEN FAILED" in source
    assert "dark_theme_active_before_render" in source
    assert "dark_theme_active_after_render" in source
    assert (
        "checked in dark mode in Playwright mode at mobile-320, mobile-390, "
        "tablet-768, and desktop-1280; each ready page-map group is also measured "
        "through light and dark color-scheme switches."
    ) in source
    assert (
        "Every rendered page-map group is checked for theme-matched node fills, "
        "4.5:1 node/edge-label contrast, and 3:1 flow-connector/arrowhead contrast "
        "against the active theme background before, between, and after dark/light "
        "switches without replacing its SVG."
    ) in source
    assert "await page.emulateMedia({ forcedColors: 'active' })" in source
    assert "forced_colors_readability" in source
    assert "node surface outline contrast=${sample.stroke_contrast}" in source
    assert "Forced-colors edge-label fixture" in source
    assert "UNIVERSE PAGE MAP FORCED-COLORS MODE UNAVAILABLE" in source
    assert "Every generated page-map group is also checked in browser-emulated forced-colors mode" in source
    assert "@media (forced-colors: active)" in (
        ROOT / "assets" / "css" / "theme.css"
    ).read_text(encoding="utf-8")
    assert "async function captureUniversePageMapReadability" in source
    assert "UNIVERSE PAGE MAP READABILITY FAILURE: theme=${theme}" in source
    assert "async function checkUniversePageMapNarrowUsability" in source
    assert "checkNarrowLightModePan" in source
    assert "Input.dispatchTouchEvent" in source
    assert "navigator.maxTouchPoints" in source
    assert "UNIVERSE PAGE MAP LIGHT-MODE PAN FAILED" in source
    assert "UNIVERSE PAGE MAP LIGHT-MODE PAN LINK FAILED" in source
    assert "narrow_light_mode_pan" in source
    assert "checkLabel = '320PX'" in source
    assert "interactionLabel = 'TAP'" in source
    assert "ORDINARY LINK DID NOT RESPOND TO A ${interactionLabel}" in source
    assert """UNIVERSE_PAGE_MAP_ZOOM_200_PROBE = {
  reference_viewport: { width: 1280, height: 800 },
  layout_viewport: { width: 640, height: 400 },
  browser_zoom_percent: 200,
};""" in source
    assert "async function checkUniversePageMapZoomUsability(page)" in source
    assert "UNIVERSE PAGE MAP 200% ZOOM" in source
    assert "universe_page_map_zoom_200" in source
    assert "LINK CONTROL INACCESSIBLE" in source
    assert "viewport=${JSON.stringify(evidence.actual_layout_viewport)}" in source
    assert "selector: 'html'" in source
    assert "selector: 'body'" in source
    assert "element=${UNIVERSE_PAGE_MAP_GROUP_SELECTOR}:nth-of-type(${groupIndex + 1}) > summary" in source
    assert "await page.setViewportSize(layoutViewport)" in source
    assert "await page.setViewportSize(originalViewport)" in source
    assert "checkTextClipping = false" in source
    assert "checkTextClipping && evidence.summary.text_layout?.clipped" in source
    assert "checkTextClipping && link.text_layout?.clipped" in source
    assert "clipping_ancestors" in source
    assert "clipped_text_rects" in source
    assert "vp.name === 'desktop-1280'" in source


def test_responsive_qa_measures_universe_page_map_geometry_after_web_fonts_load():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    font_check = source[
        source.index("async function checkUniversePageMapWebFontGeometry"):
        source.index("async function checkBrandGuardHeroGeometry")
    ]

    assert """UNIVERSE_PAGE_MAP_FONT_GEOMETRY_VIEWPORTS = new Set([
  'mobile-320',
  'mobile-390',
  'tablet-768',
  'desktop-1280',
]);""" in source
    assert "checkUniversePageMapWebFontGeometry(" in source
    assert "document.fonts.load(style.font" in font_check
    assert "'.universe-map-group[open] figcaption'" in font_check
    assert "document.fonts.ready" in font_check
    assert "font_responses_before_baseline" in font_check
    assert "stylesheet_ready_before_baseline" in font_check
    assert "UNIVERSE PAGE MAP WEB FONT GEOMETRY SHIFT" in font_check
    assert "UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX" in font_check
    assert "previous_group_summary" in font_check
    assert "next_group_summary" in font_check
    assert "reserved_shell" in font_check
    assert "mermaid_svg_ready_before_font_capture" in font_check
    assert "mermaid_svg_ready_after_font_load" in font_check
    assert "fallback_font_state" in font_check
    assert "loaded_font_state" in font_check
    assert "collapsed_after_check" in font_check
    assert "opened_by_keyboard" in source
    assert "closed_by_keyboard" in source
    assert "universe_page_map_font_geometry" in source
    assert (
        "page-map shell and neighboring-summary geometry is measured before and after "
        "web fonts become ready at mobile-320, mobile-390, tablet-768, and desktop-1280"
    ) in source
    assert (
        "first and last open groups are measured together at mobile-320 and mobile-390, "
        "while the midpoint group remains measured at mobile-390"
    ) in source
    assert (
        "fallback-font state, loaded-font state, and Mermaid SVG readiness are reported separately"
    ) in source
    assert "fontFailureSeen.then(() => false)" in font_check
    assert "resource_failures: []" in font_check
    assert "fontLoadVerified = hasStylesheet && hasFontAsset" in font_check
    assert "fontFailures.length === 0" in font_check
    assert "viewportWidth === 320" in font_check
    assert "viewportWidth === 390" in font_check
    assert "new Set([0, midpointGroupIndex, groupCount - 1])" in font_check
    assert "[0, groupCount - 1]" in font_check
    assert "measured_group_indexes: groupIndexes.map(index => index + 1)" in font_check
    assert "expectedOpenGroupIndexes" in font_check
    assert "only_expected_groups_open" in font_check
    assert "mermaid_group_readiness_before_font_capture" in font_check
    assert "mermaid_group_readiness_after_font_load" in font_check
    assert "pan_before_font_load" in font_check
    assert "pan_after_font_load" in font_check
    assert "pan_supported: false" in font_check
    assert "retention_checked: Boolean(panBeforeFontLoad[index]?.can_pan)" in font_check
    assert "UNIVERSE PAGE MAP WEB FONT PAN BASELINE UNAVAILABLE" in font_check
    assert "UNIVERSE PAGE MAP WEB FONT PAN SHIFT" in font_check
    assert "scroll_left: scrollWrap?.scrollLeft ?? null" in source
    assert "afterGroups.every(group => group?.open && group?.only_expected_groups_open && group?.svg_ready)" in font_check
    assert "...(groupIndex > 0 ? ['previous_group_summary'] : [])" in font_check
    assert "...(groupIndex < groupCount - 1 ? ['next_group_summary'] : [])" in font_check
    assert "UNIVERSE PAGE MAP WEB FONT GEOMETRY COMPARISON UNAVAILABLE" in font_check
    assert "UNIVERSE PAGE MAP WEB FONTS UNAVAILABLE" in font_check
    assert "recordUniversePageMapFontFailure" in source
    assert "universePageMapFontFailureSeen" in source
    assert "if (resp.status() >= 400)" in source
    assert "status: resp.status()" in source
    assert "const httpFailedFontRequests = new WeakSet();" in source
    assert "httpFailedFontRequests.add(resp.request());" in source
    assert "const alreadyRecordedAsHttpFailure = httpFailedFontRequests.has(req);" in source
    assert "if (blockedExternal.has(req.url()) || alreadyRecordedAsHttpFailure) return;" in source
    assert "fontFailures.length === 0" in font_check
    assert "if (fontLoadVerified && baselineCaptured" in font_check
    assert "releaseFontRequests();" in font_check
    diagram_check = source[
        source.index("async function checkUniverseDiagramGeometry"):
        source.index("async function checkUniverseMermaidFailure")
    ]
    diagram_call = source[
        source.index("const universeDiagramGeometry ="):
        source.index("const universePageMapZoom =")
    ]
    assert "universePageMapFontFailureSeen," in diagram_check
    assert "universePageMapFontFailures" in diagram_check
    assert "universePageMapFontFailureSeen," in diagram_call
    assert "universePageMapFontFailures" in diagram_call

    failure_test = (ROOT / "tests" / "test_universe_font_failures.spec.mjs").read_text(
        encoding="utf-8"
    )
    assert 'runFailureScenario("stylesheet")' in failure_test
    assert 'runFailureScenario("stylesheet", "http")' in failure_test
    assert 'runFailureScenario("font-file")' in failure_test
    assert 'runFailureScenario("font-file", "http")' in failure_test
    assert 'runFailureScenario("font-file", "corrupt-http-200")' in failure_test
    assert "status: httpStatus" in failure_test
    assert "failure.status === httpStatus" in failure_test
    assert "response.status === httpStatus" in failure_test
    assert "one failed hosted resource must appear once in the QA evidence" in failure_test
    assert "network failure reason must remain visible" in failure_test
    assert "Font response must remain held before release" in failure_test
    assert "Keyboard must open a page-map group" in failure_test
    assert "Keyboard must close a page-map group" in failure_test
    assert "page-map groups must remain collapsed after the check" in failure_test
    assert "geometryComparisonAvailable, false" in failure_test
    assert "Browser must report the HTTP-200 font face as unusable or unloaded" in failure_test
    assert "Corrupt font response must be recorded as HTTP 200" in failure_test
    assert "failureKind: \"decode\"" in failure_test
    assert "sample.load_error" in font_check
    assert "failure_kind: 'decode'" in font_check
    assert "browser returned no usable font faces" in font_check
    assert "face_states: faceStates" in font_check
    assert "face.status === 'error'" in font_check


def test_responsive_qa_checks_open_page_map_reflow_during_phone_rotation():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    rotation_check = source[
        source.index("async function checkUniversePageMapRotationReflow"):
        source.index("async function checkBrandGuardHeroGeometry")
    ]
    deferred_fonts = (ROOT / "assets" / "js" / "deferred-fonts.js").read_text(
        encoding="utf-8"
    )
    resize_handler = deferred_fonts[
        deferred_fonts.index('window.addEventListener("resize", () => {'):
        deferred_fonts.index('document.querySelectorAll(".universe-map-group")')
    ]

    assert "const portraitViewport = { width: 320, height: 740 };" in rotation_check
    assert "const landscapeViewport = { width: 740, height: 390 };" in rotation_check
    assert "await page.setViewportSize(landscapeViewport);" in rotation_check
    assert "await page.setViewportSize(portraitViewport);" in rotation_check
    assert "group_exists: true" in rotation_check
    assert "await target.locator('.mermaid-scroll-wrap').scrollIntoViewIfNeeded();" in rotation_check
    assert "RESERVATION WIDTH NOT UPDATED" in rotation_check
    assert "PAGE OVERFLOW: document scroll width" in rotation_check
    assert "SHELL UNUSABLE: shell must remain visible" in rotation_check
    assert "SUMMARY UNUSABLE: the open summary must remain visible" in rotation_check
    assert "horizontal_clipping" in rotation_check
    assert "vertical_clipping" in rotation_check
    assert "svg_preserved" in rotation_check
    assert "evidence.viewport_restored" in rotation_check
    assert "page_map_rotation_reflow" in source
    assert "await checkUniversePageMapRotationReflow(" in source
    assert "void measureCurrentWidth();" in resize_handler
    assert "if (!fontStylesReleased || document.fonts.status === \"loading\")" not in resize_handler
    assert "if (measurementPending) {" in deferred_fonts
    assert "reserve(generation, window.innerWidth);" in deferred_fonts


def test_responsive_qa_catches_stale_page_map_reservations_during_held_font_rotation():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    rotation_check = source[
        source.index("async function checkUniversePageMapFontPendingRotation"):
        source.index("async function checkUniversePageMapWebFontGeometry")
    ]
    font_check = source[
        source.index("async function checkUniversePageMapWebFontGeometry"):
        source.index("async function checkBrandGuardHeroGeometry")
    ]

    assert "rapidResizeBurst('settled_portrait_after_burst', [740, 390, 740, 320]);" in rotation_check
    assert "rapidResizeBurst('settled_phone_after_second_burst', [740, 320, 390]);" in rotation_check
    assert "await page.setViewportSize(viewport);" in rotation_check
    assert "Number(element.dataset.universeFontReservationWidth) === window.innerWidth" in rotation_check
    assert "document_overflow" in rotation_check
    assert "reservation_widths" in rotation_check
    assert "svg_preserved" in rotation_check
    assert "fontRequestObserved &&" in font_check
    assert "fontAssetResponses.length === 0" in font_check
    assert "font_pending_rotation" in font_check
    assert font_check.index("font_pending_rotation") < font_check.index("releaseFontRequests();")
    assert "font_responses_before_baseline" in font_check
    assert "UNIVERSE PAGE MAP WEB FONT GEOMETRY SHIFT" in font_check
    assert "UNIVERSE PAGE MAP WEB FONTS UNAVAILABLE" in font_check


def test_public_fallback_font_readability_uses_non_brandguard_font_sitemap_routes():
    import json
    import re
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sitemap_paths = [
        loc.text.removeprefix("https://askjamie.bot")
        for loc in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
    ]
    expected_paths = []
    for path in sitemap_paths:
        if path.startswith("/lens-system/okhp3-brandguard/"):
            continue
        page_file = ROOT / path.lstrip("/") / "index.html" if path.endswith("/") else ROOT / path.lstrip("/")
        html = page_file.read_text(encoding="utf-8")
        for tag in re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
            has_stylesheet = re.search(
                r"""\brel\s*=\s*["'][^"']*\bstylesheet\b[^"']*["']""",
                tag,
                flags=re.IGNORECASE,
            )
            has_google_fonts = re.search(
                r"""\bhref\s*=\s*["']https://fonts\.googleapis\.com/""",
                tag,
                flags=re.IGNORECASE,
            )
            if has_stylesheet and has_google_fonts:
                expected_paths.append(path)
                break

    node_test = f"""
const fs = require('node:fs');
const pathApi = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const config = source.slice(source.indexOf('function pageHasHostedWebFontStylesheet'),
  source.indexOf('const QA_PATHS ='));
const context = vm.createContext({{
  PUBLIC_PATHS: {json.dumps(sitemap_paths)},
  PUBLIC_FALLBACK_FONT_VIEWPORT_WIDTHS: new Set([320, 360, 390]),
  ROOT: process.cwd(),
  existsSync: fs.existsSync,
  readFileSync: fs.readFileSync,
  resolve: pathApi.resolve,
}});
vm.runInContext(config, context);
console.log(vm.runInContext(`JSON.stringify({{
  routes: [...PUBLIC_FALLBACK_FONT_PATHS],
  checks: [
    shouldCheckPublicFallbackFontReadability({json.dumps(expected_paths[0])}, 320),
    shouldCheckPublicFallbackFontReadability({json.dumps(expected_paths[0])}, 390),
    shouldCheckPublicFallbackFontReadability({json.dumps(expected_paths[0])}, 400),
    shouldCheckPublicFallbackFontReadability('/lens-system/okhp3-brandguard/', 320),
  ],
}})`, context));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["routes"] == expected_paths
    assert output["checks"] == [True, True, False, False]
    assert "PUBLIC_FALLBACK_FONT_ONLY" in source
    assert "PUBLIC_FALLBACK_FONT_PATHS.has(path)" in source
    assert "new Set([320, 360, 390])" in source
    assert "public_fallback_font_readability" in source
    assert "--public-fallback-font-only" in source


def test_public_fallback_font_failures_name_route_viewport_and_text_block():
    import json
    import subprocess

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    formatter = source[
        source.index("function formatPublicFallbackFontReadabilityResult"):
        source.index("async function capturePublicFallbackFontReadability")
    ]
    capture = source[
        source.index("async function capturePublicFallbackFontReadability"):
        source.index("async function checkPublicFallbackFontReadability")
    ]
    fallback_check = source[
        source.index("async function checkPublicFallbackFontReadability"):
        source.index("function parseBrandGuardForcedColorsColor")
    ]
    assert "window.scrollTo({ top: 0, left: 0, behavior: 'instant' })" in fallback_check
    assert (
        fallback_check.index("!document.querySelector('[data-transition-dialog][open]')") <
        fallback_check.index("window.scrollTo(")
    )
    assert fallback_check.index("window.scrollTo(") < fallback_check.index(
        "capturePublicFallbackFontReadability(page)"
    )
    content_check = {
        "findings": [{
            "type": "text clipped",
            "selector": "main > section.hero > h1",
            "text": "A long public-page heading",
            "evidence": {"clipped_axes": ["horizontal"]},
        }],
        "visible_text_block_count": 8,
        "viewport_width_px": 320,
    }
    blocked_requests = [
        {
            "host": "fonts.googleapis.com",
            "path": "/css2",
            "resource_type": "stylesheet",
        },
        {
            "host": "fonts.gstatic.com",
            "path": "/font.woff2",
            "resource_type": "font",
        },
    ]
    node_test = f"""
const vm = require('node:vm');
const context = vm.createContext({{}});
vm.runInContext({json.dumps(formatter)}, context);
const format = vm.runInContext('formatPublicFallbackFontReadabilityResult', context);
const result = format(
  {json.dumps(content_check)},
  {json.dumps(blocked_requests)},
  '/about/',
  320
);
const unverified = format({json.dumps(content_check)}, [], '/about/', 390);
console.log(JSON.stringify({{result, unverified}}));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    failure = output["result"]["errors"][0]
    assert "route=/about/" in failure
    assert "viewport=320px" in failure
    assert "text clipped" in failure
    assert "main > section.hero > h1" in failure
    assert "A long public-page heading" in failure
    assert output["result"]["evidence"]["web_font_stylesheet_request_blocked"] is True
    assert output["result"]["evidence"]["web_font_asset_request_observed"] is True
    assert output["result"]["evidence"]["blocked_requests"] == blocked_requests
    assert any(
        "route=/about/" in error and
        "viewport=390px" in error and
        "stylesheet request was not observed and blocked" in error
        for error in output["unverified"]["errors"]
    )
    assert "document.createTreeWalker" in capture
    assert "text clipped" in capture
    assert "text overflows viewport" in capture
    assert "text blocks overlap" in capture
    assert "getClientRects()" in capture


def test_brandguard_mobile_navigation_keyboard_covers_all_published_boundary_routes():
    import json
    import subprocess
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    sitemap_routes = sorted({
        location.text.strip().removeprefix("https://askjamie.bot")
        for location in ET.parse(ROOT / "sitemap.xml").findall(".//{*}loc")
        if location.text and
        location.text.strip().startswith(
            "https://askjamie.bot/lens-system/okhp3-brandguard/"
        )
    })
    assert sitemap_routes

    predicate = source[
        source.index("const BRANDGUARD_MOBILE_NAV_KEYBOARD_PATHS"):
        source.index("const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS")
    ]
    representative_320_routes = [
        "/lens-system/okhp3-brandguard/",
        "/lens-system/okhp3-brandguard/lvmh/",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/",
    ]
    test_routes = sitemap_routes + [
        "/lens-system/okhp3-brandguard/unlisted/",
        "/about/",
    ]
    node_test = f"""
const vm = require('node:vm');
const routes = {json.dumps(test_routes)};
const context = vm.createContext({{
  PUBLIC_PATHS: {json.dumps(sitemap_routes)},
  BRANDGUARD_GEOMETRY_PATH: '/lens-system/okhp3-brandguard/',
  BRANDGUARD_HERO_KEYBOARD_FOCUS_PATHS: new Set({json.dumps(representative_320_routes)})
}});
vm.runInContext({json.dumps(predicate)}, context);
const check = vm.runInContext('shouldCheckBrandGuardMobileNavKeyboard', context);
console.log(JSON.stringify(Object.fromEntries(
  ['mobile-320', 'mobile-355-boundary', 'mobile-365-boundary', 'mobile-360']
    .map(viewport => [viewport, routes.map(path => check(path, viewport))])
)));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    matrix = json.loads(result.stdout)
    for viewport in ("mobile-355-boundary", "mobile-365-boundary"):
        assert matrix[viewport] == [True] * len(sitemap_routes) + [False, False]
    assert matrix["mobile-320"] == [
        route in representative_320_routes for route in test_routes
    ]
    assert matrix["mobile-360"] == [False] * len(test_routes)

    keyboard_check = source[
        source.index("async function runBrandGuardMobileNavigationKeyboard"):
        source.index("async function checkBrandGuardMobileMenuDestinations")
    ]
    for evidence in (
        "BRANDGUARD ${viewportWidth ?? 'UNKNOWN'}PX MOBILE NAV KEYBOARD",
        "const viewportWidth = page.viewportSize()?.width ?? null;",
        "unsupported viewport width",
        "deferred theme styles did not activate before keyboard checks",
        "const fail = message => errors.push(`${prefix}: route=${route}; ${message}`)",
        "evidence.toggle_focus = toggleFocus",
        "toggleFocus.clipping_ancestors.length > 0",
        "element.matches(':focus-visible')",
        "menu toggle focus indicator is not visible",
        "await page.keyboard.press('Enter')",
        "await page.keyboard.press('Tab')",
        "await page.keyboard.press('Escape')",
        "await page.keyboard.press('Space')",
        "menu_closed_with_escape",
        "menu_closed_with_space",
        "focusAfterEscape.closed && !focusAfterEscape.focus_is_toggle",
        "focused_with_keyboard",
        "outline_visible",
        "clipping_ancestors",
    ):
        assert evidence in keyboard_check
    assert "shouldCheckBrandGuardMobileNavKeyboard(path, vp.name)" in source
    assert "brandguard_mobile_navigation_keyboard:" in source

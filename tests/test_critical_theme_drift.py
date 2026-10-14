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


def test_brandguard_mobile_390_reserves_font_geometry_for_affected_routes():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")
    route_classes = {
        "brandguard-case-lego": "lego",
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

    for selector, line_height in [
        (".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle", "6lh"),
        (".askjamie-main.coke-brandguard .site-header", "11.37rem"),
        (".askjamie-main.brandguard-case-costco .site-header", "11.37rem"),
        (".askjamie-main.brandguard-case-lego .askjamie-hero-copy h1", "3lh"),
        (".askjamie-main.brandguard-case-hershey .askjamie-hero-copy .hero-subtitle", "8lh"),
        (".askjamie-main.brandguard-case-hershey .askjamie-hero-copy .hero-tagline", "5lh"),
        (".askjamie-main.brandguard-case-mathews-archery .askjamie-hero-copy .hero-subtitle", "7lh"),
        (".askjamie-main.brandguard-case-scheels .askjamie-hero-copy .hero-subtitle", "7lh"),
        (".askjamie-main.coke-brandguard .manifesto-blockquote", "3lh"),
    ]:
        assert selector in theme
        assert line_height in theme


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

    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set([" in source
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
        "tablet-768",
        "tablet-899",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_VIEWPORTS.has(vp.name)" in source
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


def test_deferred_font_script_reserves_brandguard_tablet_geometry_before_activation():
    source = (ROOT / "assets/js/deferred-fonts.js").read_text(encoding="utf-8")

    assert "window.innerWidth >= 768" in source
    assert "window.innerWidth <= 899" in source
    assert 'classList.contains("askjamie-main")' in source
    assert 'document.querySelector(".askjamie-breadcrumb")' in source
    assert '".site-header .container"' in source
    assert '".site-specials"' in source
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
        'media="(max-width: 319px), (min-width: 321px) and (max-width: 767px), (min-width: 900px)" data-brandguard-tablet-fonts'
        in page.read_text(encoding="utf-8")
        for page in case_pages
    )


def test_responsive_qa_checks_brandguard_content_at_320px_from_sitemap_routes():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "const PUBLIC_PATHS = loadPublicPaths();" in source
    assert "const BRANDGUARD_NARROW_CONTENT_VIEWPORT = 'mobile-320'" in source
    assert "path.startsWith(BRANDGUARD_GEOMETRY_PATH)" in source
    assert "vp.name === BRANDGUARD_NARROW_CONTENT_VIEWPORT" in source
    assert "BRANDGUARD_NARROW_CONTENT_SELECTORS" in source
    assert "horizontal_content_clipping" in source
    assert "vertical_content_clipping" in source
    assert "outside_viewport" in source
    assert "breadcrumb_overlaps_heading" in source
    assert "BRANDGUARD 320PX CONTENT NOT READABLE" in source
    assert "BRANDGUARD 320PX CONTENT CLIPPED" in source
    assert "BRANDGUARD 320PX BREADCRUMB OVERLAPS HERO" in source
    assert "brandguard_320_content" in source


def test_responsive_qa_checks_brandguard_font_geometry_at_350_and_370_edges():
    import re

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    assert "{ name: 'mobile-350-edge', width: 350, height: 780 }" in source
    assert "{ name: 'mobile-370-edge', width: 370, height: 780 }" in source
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
    assert "BRANDGUARD_FONT_EDGE_GEOMETRY_PATHS.has(path)" in source
    assert "BRANDGUARD_FONT_EDGE_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source


def test_brandguard_font_reservations_are_scoped_to_measured_edge_widths():
    theme = (ROOT / "assets/css/theme.css").read_text(encoding="utf-8")

    assert "@media (min-width: 350px) and (max-width: 355px)" in theme
    assert "@media (min-width: 365px) and (max-width: 370px)" in theme
    assert (
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1,\n"
        "  .askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1 {\n"
        "    min-height: 4lh;"
    ) in theme
    assert ".askjamie-main.brandguard-lvmh-page .askjamie-hero-copy h1" in theme
    assert ".askjamie-main.brandguard-bfs-page .askjamie-hero-copy h1" in theme
    assert ".askjamie-main.brandguard-bfs-page .capability-transition > p:nth-of-type(2)" in theme
    assert ".askjamie-main.brandguard-lvmh-page .capability-transition > p:nth-of-type(2)" in theme


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


def test_responsive_qa_covers_every_sitemap_brandguard_route_for_font_geometry():
    import html
    import re
    import xml.etree.ElementTree as ET

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sample_routes = {
        "/lens-system/okhp3-brandguard/lvmh/":
            ROOT / "lens-system/okhp3-brandguard/lvmh/index.html",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/":
            ROOT / "lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/index.html",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS = new Set(" in source
    assert "PUBLIC_PATHS.filter(path => path.startsWith(BRANDGUARD_GEOMETRY_PATH))" in source
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS.has(path)" in source
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
    assert "async function checkUniversePageMapThemeSwitch" in source
    assert "await checkUniversePageMapThemeSwitch(" in source
    assert "UNIVERSE PAGE MAP THEME SWITCH GEOMETRY SHIFT" in source
    assert "UNIVERSE PAGE MAP THEME SWITCH KEYBOARD FAILED" in source
    assert "dark_theme_active_before_render" in source
    assert "dark_theme_active_after_render" in source
    assert (
        "checked in dark mode in Playwright mode at mobile-320, mobile-390, "
        "tablet-768, and desktop-1280; each ready page-map group is also measured "
        "through light and dark color-scheme switches."
    ) in source
    assert "async function checkUniversePageMapNarrowUsability" in source
    assert "UNIVERSE PAGE MAP 320PX" in source
    assert "ORDINARY LINK DID NOT RESPOND TO A TAP" in source

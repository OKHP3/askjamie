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
    critical = drift.parse_rules(
        (ROOT / "assets/css/critical-hero.css").read_text(encoding="utf-8")
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
    assert "'tablet-899'" in source
    assert "{ name: 'tablet-768',   width: 768,  height: 1024 }" in source
    assert "{ name: 'tablet-899',   width: 899,  height: 1024 }" in source
    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "{ name: 'mobile-320',   width: 320,  height: 740  }" in source
    font_viewports = re.search(
        r"const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert font_viewports
    assert set(re.findall(r"'([^']+)'", font_viewports.group(1))) == {
        "mobile-360",
        "mobile-390",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "link[data-deferred-styles]" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "'.askjamie-main .askjamie-breadcrumb'" in source
    assert "'.askjamie-main .askjamie-hero-copy h1'" in source
    assert "'.askjamie-main .askjamie-hero-copy .hero-subtitle'" in source
    assert "'.askjamie-main .askjamie-hero-copy .hero-tagline'" in source
    assert "BRANDGUARD HERO GEOMETRY SHIFT" in source
    assert "before=${JSON.stringify(beforeRect)}" in source
    assert "after=${JSON.stringify(afterRect)}" in source
    assert "hero_theme_geometry" in source


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


def test_responsive_qa_samples_short_and_long_brandguard_case_studies():
    import html
    import re

    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")
    sample_routes = {
        "/lens-system/okhp3-brandguard/lvmh/":
            ROOT / "lens-system/okhp3-brandguard/lvmh/index.html",
        "/lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/":
            ROOT / "lens-system/okhp3-brandguard/bfs-framing-intelligent-futures/index.html",
    }
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS = new Set([" in source
    assert "BRANDGUARD_FONT_GEOMETRY_PATHS.has(path)" in source
    assert all(f"'{route}'" in source for route in sample_routes)
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


def test_responsive_qa_measures_universe_page_map_geometry_with_dark_theme():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "UNIVERSE_DIAGRAM_GEOMETRY_VIEWPORTS = new Set(['mobile-390', 'desktop-1280'])" in source
    assert "page.emulateMedia({ colorScheme: 'dark' })" in source
    assert "localStorage.setItem('askjamie-color-scheme', 'dark')" in source
    assert "UNIVERSE PAGE MAP DARK THEME NOT ACTIVE" in source
    assert "UNIVERSE PAGE MAP GEOMETRY SHIFT" in source
    assert "UNIVERSE_DIAGRAM_GEOMETRY_TOLERANCE_PX" in source
    assert "UNIVERSE PAGE MAP KEYBOARD OPEN FAILED" in source
    assert "UNIVERSE PAGE MAP KEYBOARD CLOSE FAILED" in source
    assert "UNIVERSE PAGE MAP COLLAPSED STATE INVALID" in source
    assert "dark_theme_active_before_render" in source
    assert "dark_theme_active_after_render" in source

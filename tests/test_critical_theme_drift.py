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


def test_responsive_qa_measures_brandguard_mobile_theme_geometry():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set([" in source
    assert "'mobile-360'" in source
    assert "'mobile-390'" in source
    assert "'mobile-430'" in source
    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    assert "BRANDGUARD_FONT_GEOMETRY_VIEWPORT = 'mobile-390'" in source
    assert "link[data-deferred-styles]" in source
    assert "BRANDGUARD HERO GEOMETRY SHIFT" in source
    assert "before=${JSON.stringify(beforeRect)}" in source
    assert "after=${JSON.stringify(afterRect)}" in source
    assert "hero_theme_geometry" in source


def test_responsive_qa_measures_brandguard_geometry_after_branded_fonts_load():
    source = (ROOT / "scripts" / "responsive-qa.mjs").read_text(encoding="utf-8")

    assert "BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000" in source
    assert "fonts.googleapis.com', 'fonts.gstatic.com" in source
    assert "requestType === 'font'" in source
    assert "brandGuardFontGate.then(() => route.continue())" in source
    assert "BRANDGUARD WEB FONT GEOMETRY SHIFT" in source
    assert "before=${JSON.stringify(beforeRect)}" in source
    assert "after=${JSON.stringify(afterRect)}" in source
    assert "font_asset_loaded=${hasFontAsset}" in source
    assert "hero_font_geometry" in source

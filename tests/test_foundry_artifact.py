"""Focused Found-Ry release artifact regression."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


cache_bust = _load_module("foundry_cache_bust", "scripts/cache-bust.py")
_load_module("foundry_csp", "scripts/csp.py")
generate_csp = _load_module("foundry_generate_csp", "scripts/generate-csp.py")
csp = sys.modules["csp"]
prepare_pages_artifact = _load_module(
    "foundry_prepare_pages_artifact", "scripts/prepare-pages-artifact.py"
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class FoundryArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="askjamie-foundry-")
        self.addCleanup(self.temporary.cleanup)
        self.site_root = Path(self.temporary.name).resolve() / "site"
        self.site_root.mkdir()

        self.pages = {
            "index.html": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta http-equiv=\"Content-Security-Policy\" content=\"placeholder\" />
    <link rel=\"stylesheet\" href=\"/assets/css/theme.css\">
    <script>window.askjamieShell = true;</script>
  </head>
  <body>
    <a href=\"/found-ry/\">Open Found-Ry</a>
    <script src=\"/assets/js/app.js\"></script>
    <script src=\"/assets/js/deferred-fonts.js\"></script>
  </body>
</html>
""",
            "found-ry/index.html": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta http-equiv=\"Content-Security-Policy\" content=\"placeholder\" />
    <link rel=\"stylesheet\" href=\"/assets/css/theme.css\">
    <script>window.foundryShell = true;</script>
  </head>
  <body>
    <h1>AskJamie Found-Ry</h1>
    <script src=\"/assets/js/app.js\"></script>
    <script src=\"/assets/js/deferred-fonts.js\"></script>
  </body>
</html>
""",
            "lens-system/okhp3-brandguard/index.html": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta http-equiv=\"Content-Security-Policy\" content=\"placeholder\" />
    <link rel=\"stylesheet\" href=\"/assets/css/critical-hero.css\">
    <link rel=\"stylesheet\" href=\"/assets/css/theme.css\">
  </head>
  <body><h1>BrandGuard</h1></body>
</html>
""",
            "universe/index.html": """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta http-equiv=\"Content-Security-Policy\" content=\"placeholder\" />
    <link rel=\"stylesheet\" href=\"/assets/css/critical-hero.css\">
    <link rel=\"stylesheet\" href=\"/assets/css/theme.css\">
  </head>
  <body><h1>Universe</h1></body>
</html>
""",
        }

        self.assets = {
            "assets/css/theme.css": "body { color: #2e2b29; }\n",
            "assets/css/critical-hero.css": (
                ROOT / "assets/css/critical-hero.css"
            ).read_text(encoding="utf-8"),
            "assets/js/app.js": "console.log('app');\n",
            "assets/js/mermaid-init.js": "console.log('mermaid');\n",
            "assets/js/universe-map.js": "console.log('universe');\n",
            "assets/js/deferred-fonts.js": "console.log('fonts');\n",
            "assets/js/analytics-loader.js": "console.log('loader');\n",
            "assets/js/askjamie-analytics.js": "console.log('analytics');\n",
        }

        for relative, content in self.pages.items():
            _write(self.site_root / relative, content)
        for relative, content in self.assets.items():
            _write(self.site_root / relative, content)

        self.pages_list = [
            self.site_root / "index.html",
            self.site_root / "found-ry/index.html",
            self.site_root / "lens-system/okhp3-brandguard/index.html",
            self.site_root / "universe/index.html",
        ]

    def test_foundry_fixture_covers_shared_asset_inventory(self):
        self.assertEqual(
            set(self.assets),
            set(cache_bust.ASSETS),
            "update the Found-Ry fixture whenever scripts/cache-bust.py gains an asset",
        )

    def test_foundry_release_artifact_keeps_route_assets_fingerprints_and_csp(self):
        with patch.object(cache_bust, "ROOT", self.site_root), patch.object(
            cache_bust, "iter_html_files", lambda root: iter(self.pages_list)
        ):
            self.assertEqual(cache_bust.main([]), 0)

        with patch.object(csp, "ROOT", self.site_root), patch.object(
            csp, "all_pages", lambda: self.pages_list
        ), patch.object(generate_csp, "ROOT", self.site_root), patch.object(
            generate_csp, "POLICY_FILE", self.site_root / "config/csp-policies.json"
        ), patch.object(generate_csp, "all_pages", lambda: self.pages_list):
            self.assertEqual(generate_csp.main([]), 0)

        output = self.site_root.parent / "release"
        with patch.object(prepare_pages_artifact, "ROOT", self.site_root):
            manifest = prepare_pages_artifact.prepare(output)

        foundry_page = output / "found-ry/index.html"
        self.assertTrue(foundry_page.is_file())
        self.assertTrue((output / "assets/js/app.js").is_file())
        self.assertTrue((output / "assets/js/deferred-fonts.js").is_file())
        self.assertTrue((output / "assets/js/askjamie-analytics.js").is_file())
        self.assertTrue((output / "assets/js/analytics-loader.js").is_file())
        critical_hero = self.site_root / "assets/css/critical-hero.css"
        artifact_critical_hero = output / "assets/css/critical-hero.css"
        critical_hero_fingerprint = cache_bust.file_hash(critical_hero)
        self.assertTrue(artifact_critical_hero.is_file())
        self.assertEqual(artifact_critical_hero.read_bytes(), critical_hero.read_bytes())

        foundry_html = foundry_page.read_text(encoding="utf-8")
        self.assertIn(
            f"/assets/js/app.js?v={cache_bust.file_hash(self.site_root / 'assets/js/app.js')}",
            foundry_html,
        )
        self.assertIn(
            (
                f"/assets/js/deferred-fonts.js?v="
                f"{cache_bust.file_hash(self.site_root / 'assets/js/deferred-fonts.js')}"
            ),
            foundry_html,
        )
        self.assertIn(
            (
                '"/assets/js/askjamie-analytics.js":"/assets/js/askjamie-analytics.js?v='
                f"{cache_bust.file_hash(self.site_root / 'assets/js/askjamie-analytics.js')}"
            ),
            foundry_html,
        )
        self.assertEqual(
            csp.meta_policy(foundry_page),
            csp.meta_policy(self.site_root / "found-ry/index.html"),
        )
        for route in (
            "lens-system/okhp3-brandguard/index.html",
            "universe/index.html",
        ):
            route_page = output / route
            self.assertTrue(route_page.is_file(), route)
            route_html = route_page.read_text(encoding="utf-8")
            self.assertIn(
                f"/assets/css/critical-hero.css?v={critical_hero_fingerprint}",
                route_html,
                route,
            )
        self.assertGreaterEqual(manifest["files"], 5)
        self.assertEqual(
            manifest["root_files"], sorted(prepare_pages_artifact.PUBLIC_ROOT_FILES)
        )
        self.assertEqual(
            manifest["asset_directories"], sorted(prepare_pages_artifact.PUBLIC_ASSET_DIRS)
        )


if __name__ == "__main__":
    unittest.main()

"""Pages packaging safety regressions, runnable with unittest or pytest."""

import importlib.util
import contextlib
import io
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-pages-artifact.py"
SPEC = importlib.util.spec_from_file_location("pages_artifact_safety", SCRIPT)
pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pages)


class PagesArtifactSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="askjamie-pages-safety-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        self.patch_root = patch.object(pages, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.write("index.html", "<h1>Public</h1>")
        self.output = self.base / "release"

    def write(self, relative, content="public"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_repository_ancestors_and_source_outputs_are_rejected_before_deletion(self):
        for output in (self.root, self.base, self.root / "assets", self.root / "about/new", self.root / ".scratch"):
            with self.subTest(output=output), patch.object(pages.shutil, "rmtree") as delete:
                with self.assertRaises(ValueError):
                    pages.prepare(output)
                delete.assert_not_called()
        self.assertEqual((self.root / "index.html").read_text(), "<h1>Public</h1>")

    def test_existing_unowned_directory_and_file_are_preserved(self):
        self.output.mkdir()
        note = self.output / "forgotten.txt"
        note.write_text("preserve me")
        for output in (self.output, note):
            with self.subTest(output=output), patch.object(pages.shutil, "rmtree") as delete:
                with self.assertRaises(ValueError):
                    pages.prepare(output)
                delete.assert_not_called()
        self.assertEqual(note.read_text(), "preserve me")

    def test_exact_generated_artifact_can_be_rebuilt_idempotently(self):
        first = pages.prepare(self.output)
        second = pages.prepare(self.output)
        self.assertEqual(first, second)
        self.assertTrue((self.base / "release.manifest.json").is_file())
        self.assertEqual(list(self.output.iterdir()), [self.output / "index.html"])

    def test_changed_generated_artifact_is_preserved(self):
        pages.prepare(self.output)
        (self.output / "index.html").write_text("uncommitted edit")
        with patch.object(pages.shutil, "rmtree") as delete:
            with self.assertRaisesRegex(ValueError, "differs from its manifest"):
                pages.prepare(self.output)
            delete.assert_not_called()
        self.assertEqual((self.output / "index.html").read_text(), "uncommitted edit")

    def test_added_file_in_generated_artifact_is_preserved(self):
        pages.prepare(self.output)
        (self.output / "notes.txt").write_text("keep")
        with self.assertRaises(ValueError):
            pages.prepare(self.output)
        self.assertEqual((self.output / "notes.txt").read_text(), "keep")

    def test_scratch_and_external_disposable_outputs_are_supported(self):
        pages.prepare(self.root / ".scratch" / "release")
        pages.prepare(self.output)
        self.assertTrue((self.output / "index.html").is_file())

    def test_hidden_source_artwork_and_symlinks_are_excluded(self):
        for name in ("about/.env", "assets/img/.DS_Store", "assets/img/.gitkeep",
                     "assets/img/design.pdn", "assets/img/DESIGN.PDN",
                     "assets/data/.hidden/data.json", "assets/.well-known/private.txt"):
            self.write(name)
        for name in (".well-known/security.txt", ".well-known/discord", "_headers", "CNAME",
                     "about/index.html", "assets/img/public.png", "assets/js/app.js"):
            self.write(name)
        private = self.base / "private.txt"
        private.write_text("private fixture")
        (self.root / "assets/img/linked.txt").symlink_to(private)
        (self.root / "assets/img/linked-dir").symlink_to(self.base, target_is_directory=True)
        pages.prepare(self.output)
        actual = {path.relative_to(self.output).as_posix()
                  for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(actual, {"index.html", ".well-known/security.txt", ".well-known/discord",
                                  "_headers", "CNAME", "about/index.html",
                                  "assets/img/public.png", "assets/js/app.js"})
        self.assertTrue((self.root / "assets/img/design.pdn").is_file())
        self.assertEqual(private.read_text(), "private fixture")

    def test_symlink_output_or_manifest_is_rejected(self):
        self.output.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            pages.prepare(self.output)
        self.output.unlink()
        target = self.base / "important.txt"
        target.write_text("preserve")
        (self.base / "release.manifest.json").symlink_to(target)
        with self.assertRaises(ValueError):
            pages.prepare(self.output)
        self.assertEqual(target.read_text(), "preserve")

    def test_existing_unrelated_manifest_is_preserved(self):
        sidecar = self.base / "release.manifest.json"
        sidecar.write_text("unrelated")
        with self.assertRaises(ValueError):
            pages.prepare(self.output)
        self.assertEqual(sidecar.read_text(), "unrelated")

    def test_symlink_output_ancestor_is_rejected_before_creating_target(self):
        target = self.base / "elsewhere"
        target.mkdir()
        alias = self.base / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "ancestors"):
            pages.prepare(alias / "new-release")
        self.assertEqual(list(target.iterdir()), [])

    def test_scratch_html_is_excluded_from_source_collectors(self):
        self.write(".scratch/generated/deep/index.html", "<h1>Generated duplicate</h1>")
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        for filename, root_name, collector in (
            ("validate-site.py", "ROOT", "find_html_files"),
            ("audit-site.py", "ROOT", "iter_public_files"),
            ("build-search-index.py", "REPO_ROOT", "collect_pages"),
        ):
            with self.subTest(filename=filename):
                spec = importlib.util.spec_from_file_location(filename, SCRIPT.parent / filename)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with patch.object(module, root_name, self.root):
                    paths = list(getattr(module, collector)())
                if filename == "build-search-index.py":
                    self.assertEqual([record["url"] for record in paths], ["/"])
                else:
                    self.assertEqual([Path(path) for path in paths], [self.root / "index.html"])

    def test_link_checker_does_not_scan_generated_scratch_links(self):
        self.write(".scratch/generated/deep/index.html", '<a href="/missing.html">Broken fixture</a>')
        self.write("sitemap.xml", '<urlset><url><loc>https://askjamie.bot/</loc></url></urlset>')
        (self.root / "assets").mkdir()
        spec = importlib.util.spec_from_file_location("scratch_links", SCRIPT.parent / "check-links.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 0)

    def test_privacy_and_font_regressions_ignore_generated_scratch_pages(self):
        root = SCRIPT.parent.parent
        candidates = [root / "index.html", root / ".scratch/generated/deep/index.html"]
        for filename in ("test_privacy_consent.py", "test_external_font_origins.py"):
            with self.subTest(filename=filename):
                spec = importlib.util.spec_from_file_location(filename, root / "tests" / filename)
                module = importlib.util.module_from_spec(spec)
                with patch.object(Path, "rglob", return_value=iter(candidates)):
                    spec.loader.exec_module(module)
                self.assertEqual(module.PUBLIC_HTML, [root / "index.html"])

    def test_csp_excludes_even_tracked_scratch_html(self):
        sys.path.insert(0, str(SCRIPT.parent))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPT.parent)))
        import csp
        result = subprocess.CompletedProcess([], 0, stdout="index.html\n.scratch/generated/index.html\n")
        with patch.object(csp, "ROOT", self.root), patch.object(csp.subprocess, "run", return_value=result):
            self.assertEqual(csp.all_pages(), [self.root / "index.html"])

    def test_ci_transports_only_filtered_artifact_including_well_known(self):
        workflow = (SCRIPT.parent.parent / ".github/workflows/validate.yml").read_text()
        upload = workflow.split("- name: Preserve validated Pages artifact", 1)[1].split("  deploy:", 1)[0]
        self.assertIn("--output .scratch/pages-release", workflow)
        prepare = workflow.index("- name: Prepare public Pages artifact for browser QA")
        browser_qa = workflow.index("- name: Run full browser responsive QA and JavaScript smoke tests")
        self.assertLess(prepare, browser_qa)
        browser_step = workflow[browser_qa:].split("\n      - name:", 1)[0]
        self.assertIn("--directory .scratch/pages-release", browser_step)
        self.assertIn("--require-release-hero-routes", browser_step)
        self.assertIn("path: .scratch/pages-release", upload)
        self.assertIn("include-hidden-files: true", upload)
        pages_upload = workflow.split("- name: Upload site artifact", 1)[1].split(
            "- name: Deploy to GitHub Pages", 1
        )[0]
        self.assertIn("path: 'dist-pages'", pages_upload)
        self.assertEqual(upload.count("include-hidden-files: true"), 1)
        self.assertEqual(pages_upload.count("include-hidden-files: true"), 1)
        self.assertEqual(workflow.count("include-hidden-files: true"), 2)


if __name__ == "__main__":
    unittest.main()

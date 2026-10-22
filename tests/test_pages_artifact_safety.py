"""Pages packaging safety regressions, runnable with unittest or pytest."""

import importlib.util
import contextlib
import io
import os
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


def workflow_run_script(workflow, step_name):
    step = workflow.split(f"- name: {step_name}", 1)[1].split("\n      - name:", 1)[0]
    lines = step.splitlines()
    run_line = next(index for index, line in enumerate(lines) if line == "        run: |")
    body = []
    for line in lines[run_line + 1:]:
        if line.startswith("          "):
            body.append(line[10:])
        elif not line.strip():
            body.append("")
        else:
            break
    return "\n".join(body).rstrip() + "\n"


def run_workflow_script(script, cwd, env):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


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

    def test_deployed_probe_uses_exact_artifact_after_publication_and_retains_failure_evidence(self):
        workflow = (SCRIPT.parent.parent / ".github/workflows/validate.yml").read_text()
        deploy = workflow.split("  deploy:", 1)[1]
        checkout = deploy.split("- name: Checkout exact revision probe tooling", 1)[1].split(
            "- name: Set up probe Python", 1
        )[0]
        self.assertIn("ref: ${{ github.sha }}", checkout)
        self.assertIn("path: probe-tools", checkout)
        self.assertLess(deploy.index("Checkout exact revision"), deploy.index("Download validated"))
        self.assertLess(deploy.index("uses: actions/deploy-pages@"), deploy.index("Verify served sample"))
        after_deploy = deploy.split("uses: actions/deploy-pages@", 1)[1]
        self.assertIn("python3 probe-tools/scripts/check-deployed-pages.py", after_deploy)
        self.assertIn("--artifact-root dist-pages", after_deploy)
        self.assertIn('--expected-artifact-name "pages-site-$GITHUB_SHA"', after_deploy)
        self.assertNotIn("prepare-pages-artifact.py", deploy)
        self.assertNotIn("upload-pages-artifact", after_deploy)
        self.assertNotIn("--output", deploy)
        self.assertIn("timeout-minutes: 15", deploy)
        for name in ("Summarize served sample", "Retain served sample evidence"):
            step = deploy.split("- name: " + name, 1)[1].split("- name:", 1)[0]
            self.assertIn("if: always()", step)
        self.assertIn("path: artifacts/deployed-pages", after_deploy)
        self.assertIn("retention-days: 90", after_deploy)

    def test_post_deployment_hero_check_downloads_release_css_for_the_deployment_revision(self):
        workflow = (SCRIPT.parent.parent /
                    ".github/workflows/post-deployment-hero-styles.yml").read_text()
        self.assertIn("actions: read", workflow)
        self.assertIn("name: pages-site-${{ github.event.workflow_run.head_sha || github.sha }}", workflow)
        self.assertIn("run-id: ${{ steps.validated-release.outputs.run_id }}", workflow)
        self.assertIn("github-token: ${{ secrets.GITHUB_TOKEN }}", workflow)
        self.assertIn("--expected-css=.validated-pages/assets/css/critical-hero.css", workflow)
        self.assertIn("github.event.workflow_run.id", workflow)
        self.assertIn("gh run list", workflow)

    def test_post_deployment_hero_check_reports_and_retains_missing_validated_artifact(self):
        workflow = (SCRIPT.parent.parent /
                    ".github/workflows/post-deployment-hero-styles.yml").read_text()
        diagnostic = workflow.split(
            "- name: Record validated artifact retrieval failure", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("always()", diagnostic)
        self.assertIn("steps.validated-release.outcome == 'failure'", diagnostic)
        self.assertIn("steps.download-validated-release.outcome == 'failure'", diagnostic)
        self.assertIn("SOURCE_REVISION: ${{ github.event.workflow_run.head_sha || github.sha }}", diagnostic)
        self.assertIn("The validated Pages artifact for source revision", diagnostic)
        self.assertIn(
            r'The matching validation run was [\`$VALIDATION_RUN_ID\`]($GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$VALIDATION_RUN_ID)',
            diagnostic,
        )
        no_run_message = diagnostic.split("else", 1)[1].split("fi", 1)[0]
        self.assertIn("No successful validation run for this revision could be located.", no_run_message)
        self.assertNotIn("](", no_run_message)
        self.assertIn("may have expired or otherwise become unavailable", diagnostic)
        self.assertIn("No artifact from another revision was substituted", diagnostic)
        self.assertIn("artifacts/deployed-hero-style-check/report.md", diagnostic)

        summary = workflow.split("- name: Add route-specific receipt to the run summary", 1)[1].split(
            "- name:", 1
        )[0]
        retained = workflow.split("- name: Retain deployed hero-style evidence", 1)[1]
        self.assertIn("if: always()", summary)
        self.assertIn("report.md", summary)
        self.assertIn("if: always()", retained)
        self.assertIn("path: artifacts/deployed-hero-style-check/", retained)
        self.assertIn("name: pages-site-${{ github.event.workflow_run.head_sha || github.sha }}", workflow)
        self.assertIn("run-id: ${{ steps.validated-release.outputs.run_id }}", workflow)

    def test_missing_validation_run_reports_revision_and_retains_failure_summary(self):
        workflow = (SCRIPT.parent.parent /
                    ".github/workflows/post-deployment-hero-styles.yml").read_text()
        source_revision = "revision-without-validation"
        workdir = self.base / "missing-validation-run"
        bin_dir = workdir / "bin"
        bin_dir.mkdir(parents=True)
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$GH_CALLS\"\n")
        gh.chmod(0o755)
        output_file = workdir / "github-output"
        summary_file = workdir / "github-summary"
        gh_calls = workdir / "gh-calls"

        env = os.environ.copy()
        env.update({
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "SOURCE_REVISION": source_revision,
            "VALIDATION_RUN_ID": "",
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_STEP_SUMMARY": str(summary_file),
            "GITHUB_SERVER_URL": "https://github.example",
            "GITHUB_REPOSITORY": "owner/repository",
            "GH_CALLS": str(gh_calls),
        })

        lookup = run_workflow_script(
            workflow_run_script(workflow, "Find the successful validation run for this revision"),
            workdir,
            env,
        )
        self.assertNotEqual(lookup.returncode, 0)
        self.assertIn("--commit " + source_revision, gh_calls.read_text())
        self.assertEqual(output_file.read_text() if output_file.exists() else "", "")

        report = run_workflow_script(
            workflow_run_script(workflow, "Record validated artifact retrieval failure"),
            workdir,
            env,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        report_path = workdir / "artifacts/deployed-hero-style-check/report.md"
        report_content = report_path.read_text()
        self.assertIn(
            f"The validated Pages artifact for source revision `{source_revision}` is unavailable.",
            report_content,
        )
        self.assertIn("No successful validation run for this revision could be located.", report_content)
        self.assertIn("No artifact from another revision was substituted.", report_content)
        self.assertNotIn("](", report_content)

        summary = run_workflow_script(
            workflow_run_script(workflow, "Add route-specific receipt to the run summary"),
            workdir,
            env,
        )
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(summary_file.read_text(), report_content)
        retained = workflow.split("- name: Retain deployed hero-style evidence", 1)[1]
        self.assertIn("if: always()", retained)
        self.assertIn("path: artifacts/deployed-hero-style-check/", retained)
        self.assertIn("deployed-hero-style-check-${{ github.event.workflow_run.head_sha || github.sha }}",
                      retained)

    def test_failed_exact_artifact_download_reports_run_and_never_uses_other_revision(self):
        workflow = (SCRIPT.parent.parent /
                    ".github/workflows/post-deployment-hero-styles.yml").read_text()
        source_revision = "deployment-revision"
        other_revision = "different-revision"
        validation_run_id = "run-1842"
        workdir = self.base / "missing-exact-artifact"
        workdir.mkdir()
        output_file = workdir / "github-output"
        summary_file = workdir / "github-summary"
        env = os.environ.copy()
        env.update({
            "SOURCE_REVISION": source_revision,
            "VALIDATION_RUN_ID": validation_run_id,
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_STEP_SUMMARY": str(summary_file),
            "GITHUB_SERVER_URL": "https://github.example",
            "GITHUB_REPOSITORY": "owner/repository",
        })

        lookup = run_workflow_script(
            workflow_run_script(workflow, "Find the successful validation run for this revision"),
            workdir,
            env,
        )
        self.assertEqual(lookup.returncode, 0, lookup.stderr)
        self.assertEqual(output_file.read_text(), f"run_id={validation_run_id}\n")

        download_step = workflow.split(
            "- name: Download the exact validated Pages artifact", 1
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("uses: actions/download-artifact@v8", download_step)
        self.assertIn(
            "name: pages-site-${{ github.event.workflow_run.head_sha || github.sha }}",
            download_step,
        )
        self.assertIn("run-id: ${{ steps.validated-release.outputs.run_id }}", download_step)

        # The exact artifact is absent; only a different revision's artifact exists.
        available_artifacts = {
            (validation_run_id, f"pages-site-{other_revision}"): "wrong revision contents",
        }
        exact_artifact = (validation_run_id, f"pages-site-{source_revision}")
        self.assertNotIn(exact_artifact, available_artifacts)

        report_env = env | {"VALIDATION_RUN_ID": validation_run_id}
        report = run_workflow_script(
            workflow_run_script(workflow, "Record validated artifact retrieval failure"),
            workdir,
            report_env,
        )
        self.assertEqual(report.returncode, 0, report.stderr)
        report_path = workdir / "artifacts/deployed-hero-style-check/report.md"
        report_content = report_path.read_text()
        self.assertIn(
            f"The validated Pages artifact for source revision `{source_revision}` is unavailable.",
            report_content,
        )
        self.assertIn(
            f"The matching validation run was [`{validation_run_id}`]"
            f"(https://github.example/owner/repository/actions/runs/{validation_run_id}), "
            "but its exact artifact could not be retrieved.",
            report_content,
        )
        self.assertIn("No artifact from another revision was substituted.", report_content)
        self.assertNotIn("wrong revision contents", report_content)

        summary = run_workflow_script(
            workflow_run_script(workflow, "Add route-specific receipt to the run summary"),
            workdir,
            env,
        )
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(summary_file.read_text(), report_content)
        retained = workflow.split("- name: Retain deployed hero-style evidence", 1)[1]
        self.assertIn("if: always()", retained)
        self.assertIn("path: artifacts/deployed-hero-style-check/", retained)


if __name__ == "__main__":
    unittest.main()

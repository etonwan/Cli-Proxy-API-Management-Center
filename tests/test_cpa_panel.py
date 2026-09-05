import contextlib
import fcntl
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import cpa_panel as panel


OLD = b"<!doctype html><html>Old panel</html>"
NEW = b"<!doctype html><html>New panel</html>"


class PanelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(panel, "DATA_ROOT", self.root))
        self.verify = self.enterContext(patch.object(panel, "verify_served"))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        for environment in panel.PORTS:
            panel.panel_path(environment).parent.mkdir(parents=True)
            panel.panel_path(environment).write_bytes(OLD)
        self.build = self.root / "build.html"
        self.build.write_bytes(NEW)

    def test_install_dev_does_not_touch_prod(self):
        panel.main(["install-dev", str(self.build)])
        self.assertEqual(panel.panel_path("dev").read_bytes(), NEW)
        self.assertEqual(panel.previous_path("dev").read_bytes(), OLD)
        self.assertEqual(panel.panel_path("prod").read_bytes(), OLD)
        self.verify.assert_called_once_with("dev", NEW)

    def test_noop_preserves_previous(self):
        panel.install("dev", NEW)
        panel.install("dev", NEW)
        self.assertEqual(panel.previous_path("dev").read_bytes(), OLD)

    def test_deploy_requires_confirmation_and_does_not_depend_on_dev(self):
        with patch.object(panel, "build_panel", return_value=NEW):
            with patch("builtins.input", return_value="no"):
                panel.main(["deploy"])
            self.assertEqual(panel.panel_path("prod").read_bytes(), OLD)
            self.assertFalse(panel.previous_path("prod").exists())
            with patch("builtins.input", return_value="prod"):
                panel.main(["deploy"])
        self.assertEqual(panel.panel_path("prod").read_bytes(), NEW)
        self.assertEqual(panel.previous_path("prod").read_bytes(), OLD)
        self.assertEqual(panel.panel_path("dev").read_bytes(), OLD)
        self.verify.assert_called_once_with("prod", NEW)

    def test_failed_build_never_changes_production(self):
        with patch.object(panel, "build_panel", side_effect=subprocess.CalledProcessError(1, "docker")):
            with self.assertRaises(subprocess.CalledProcessError):
                panel.main(["deploy"])
        self.assertEqual(panel.panel_path("prod").read_bytes(), OLD)
        self.assertFalse(panel.previous_path("prod").exists())

    def test_rollback_swaps_and_prod_requires_confirmation(self):
        panel.install("prod", NEW)
        with patch("builtins.input", return_value="no"):
            panel.main(["rollback", "prod"])
        self.assertEqual(panel.panel_path("prod").read_bytes(), NEW)
        with patch("builtins.input", return_value="prod"):
            panel.main(["rollback", "prod"])
        self.assertEqual(panel.panel_path("prod").read_bytes(), OLD)
        self.assertEqual(panel.previous_path("prod").read_bytes(), NEW)

    def test_rejects_invalid_or_missing_files(self):
        for content in (b"", b'{"error": "not found"}', b"<!doctype html><html>truncated"):
            with self.assertRaises(ValueError):
                panel.install("dev", content)
        with self.assertRaises(FileNotFoundError):
            panel.main(["rollback", "dev"])
        self.assertEqual(panel.panel_path("dev").read_bytes(), OLD)

    def test_backup_failure_keeps_current(self):
        with patch.object(panel, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                panel.install("dev", NEW)
        self.assertEqual(panel.panel_path("dev").read_bytes(), OLD)

    def test_http_failure_keeps_recovery_copy(self):
        self.verify.side_effect = OSError("connection refused")
        with self.assertRaises(OSError):
            panel.install("dev", NEW)
        self.assertEqual(panel.panel_path("dev").read_bytes(), NEW)
        self.assertEqual(panel.previous_path("dev").read_bytes(), OLD)

    def test_concurrent_operation_is_rejected(self):
        with (self.root / ".cpa-panel.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                panel.main(["install-dev", str(self.build)])
        self.assertEqual(panel.panel_path("dev").read_bytes(), OLD)


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(panel, "ROOT", self.root))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        (self.root / "dist").mkdir()
        (self.root / "dist/index.html").write_bytes(NEW)
        self.state = {
            ("branch", "--show-current"): "main",
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): "a" * 40,
            ("rev-parse", "origin/main"): "a" * 40,
        }
        self.enterContext(patch.object(
            panel.subprocess, "check_output",
            side_effect=lambda args, **kwargs: self.state[tuple(args[3:])],
        ))
        self.run = self.enterContext(patch.object(panel.subprocess, "run"))

    def test_checks_and_builds_synced_main_with_commit_version(self):
        self.assertEqual(panel.build_panel(), NEW)
        calls = self.run.call_args_list
        self.assertEqual(calls[0].args[0], ["git", "fetch", "origin", "main"])
        self.assertIn("unittest", calls[1].args[0])
        self.assertEqual(calls[2].args[0][:3], ["docker", "run", "--rm"])
        self.assertIn("VERSION=main-aaaaaaaaaaaa", calls[2].args[0])
        self.assertIn("bun run verify", calls[2].args[0][-1])
        self.assertTrue(all(call.kwargs["check"] for call in calls))

    def test_rejects_wrong_branch_and_uncommitted_work(self):
        for key, value in ((("branch", "--show-current"), "dev"),
                           (("status", "--porcelain"), " M src/App.tsx")):
            previous = self.state[key]
            self.state[key] = value
            with self.assertRaises(ValueError):
                panel.build_panel()
            self.state[key] = previous
        self.run.assert_not_called()

    def test_rejects_unsynced_main(self):
        self.state[("rev-parse", "origin/main")] = "b" * 40
        with self.assertRaises(ValueError):
            panel.build_panel()
        self.assertEqual(self.run.call_count, 1)

    def test_fetch_test_or_build_failure_stops(self):
        for stage in range(3):
            self.run.side_effect = [None] * stage + [subprocess.CalledProcessError(1, "check")]
            with self.assertRaises(subprocess.CalledProcessError):
                panel.build_panel()

    def test_source_change_during_build_is_rejected(self):
        for key, value in ((("status", "--porcelain"), " M src/App.tsx"),
                           (("rev-parse", "HEAD"), "b" * 40)):
            def change_source(args, **kwargs):
                if args[0] == "docker":
                    self.state[key] = value
            self.run.side_effect = change_source
            previous = self.state[key]
            with self.assertRaises(ValueError):
                panel.build_panel()
            self.state[key] = previous


if __name__ == "__main__":
    unittest.main()

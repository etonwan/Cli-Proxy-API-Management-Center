import contextlib
import fcntl
import io
from pathlib import Path
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

    def test_promote_requires_accepted_hash_and_confirmation(self):
        panel.install("dev", NEW)
        with self.assertRaises(ValueError):
            panel.main(["promote", panel.digest(OLD)])
        with patch("builtins.input", return_value="no"):
            panel.main(["promote", panel.digest(NEW)])
        self.assertEqual(panel.panel_path("prod").read_bytes(), OLD)
        self.assertFalse(panel.previous_path("prod").exists())
        with patch("builtins.input", return_value="prod"):
            panel.main(["promote", panel.digest(NEW)])
        self.assertEqual(panel.panel_path("prod").read_bytes(), NEW)
        self.assertEqual(panel.previous_path("prod").read_bytes(), OLD)

    def test_promote_requires_dev_to_serve_the_same_bytes(self):
        self.verify.side_effect = ValueError("wrong served content")
        with self.assertRaises(ValueError):
            panel.main(["promote", panel.digest(OLD)])
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


if __name__ == "__main__":
    unittest.main()

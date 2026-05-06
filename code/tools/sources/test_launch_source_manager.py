import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


def load_launcher_module():
    path = pathlib.Path("code/tools/sources/launch_source_manager.py").resolve()
    spec = importlib.util.spec_from_file_location("launch_source_manager", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LaunchSourceManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_launcher_module()

    def test_parse_source_manager_ready_line(self):
        payload = self.mod.parse_ready_line(
            'SOURCE_MANAGER_READY {"status":"ready","url":"http://127.0.0.1:8765","selected_port":8765}'
        )
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["url"], "http://127.0.0.1:8765")
        self.assertEqual(payload["selected_port"], 8765)
        self.assertIsNone(self.mod.parse_ready_line("URL: http://127.0.0.1:8765"))
        self.assertIsNone(self.mod.parse_ready_line("SOURCE_MANAGER_READY {not-json"))

    def test_exit_zero_before_readiness_is_exited_before_ready(self):
        self.assertEqual(self.mod.classify_exit_before_ready(0, False), "exited_before_ready")
        self.assertEqual(self.mod.classify_exit_before_ready(1, False), "failed_before_ready")
        self.assertEqual(self.mod.classify_exit_before_ready(0, True), "stopped_after_ready")

    def test_health_and_browser_failure_classifications(self):
        self.assertEqual(self.mod.classify_health_result(False), "health_check_failed")
        self.assertEqual(self.mod.classify_browser_result(False), "browser_open_failed")
        with mock.patch.object(self.mod, "urlopen", side_effect=OSError("connection refused")):
            ok, payload, error = self.mod.probe_health("http://127.0.0.1:8765", timeout_seconds=0.01)
        self.assertFalse(ok)
        self.assertEqual(payload, {})
        self.assertIn("connection refused", error)

    def test_invalid_port_falls_back_to_default(self):
        port, warning = self.mod.coerce_port("not-a-port", default=8765)
        self.assertEqual(port, 8765)
        self.assertIn("Invalid requested port", warning)
        port, warning = self.mod.coerce_port("70000", default=8765)
        self.assertEqual(port, 8765)
        self.assertIn("Invalid requested port", warning)

    def test_diagnostics_html_includes_actionable_status_and_log_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            log_path = root / "source-manager.log"
            diag_path = root / "diagnostics.html"
            self.mod.write_diagnostics_html(
                diag_path,
                {
                    "classification": "health_check_failed",
                    "message": "Health endpoint did not respond.",
                    "url": "http://127.0.0.1:8766",
                    "log_path": str(log_path),
                    "status_file": str(root / "status.json"),
                    "ready_file": str(root / "ready.json"),
                    "log_tail": ["first line", "last line"],
                },
            )
            html = diag_path.read_text(encoding="utf-8")
        self.assertIn("health_check_failed", html)
        self.assertIn("Take a screenshot", html)
        self.assertIn(str(log_path), html)
        self.assertIn("Health endpoint did not respond.", html)
        self.assertIn("last line", html)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import pathlib
import sys
import unittest


def load_ui_local_module():
    path = pathlib.Path("code/tools/sources/ui_local.py").resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("ui_local", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class UiLocalHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = load_ui_local_module().HTML

    def test_adam_ssm_branding_is_present(self):
        self.assertIn("ADAM SSM - Sleepless Source Manager", self.html)
        self.assertIn("<title>ADAM SSM - Sleepless Source Manager</title>", self.html)

    def test_editorial_theme_tokens_are_defined(self):
        self.assertIn("--bg-page:", self.html)
        self.assertIn("--bg-panel:", self.html)
        self.assertIn("--accent-ink:", self.html)
        self.assertIn("--border-soft:", self.html)
        self.assertIn(".app-shell", self.html)
        self.assertIn(".app-subtitle", self.html)

    def test_shared_component_classes_exist(self):
        for marker in [
            ".branch-tabs",
            ".branch-tab.active",
            ".panel",
            ".section-heading",
            ".help",
            "details",
            "summary",
            "button.secondary",
            "button.warn",
        ]:
            self.assertIn(marker, self.html)

    def test_status_search_and_responsive_hooks_exist(self):
        for marker in [
            ".search-panel",
            ".search-results",
            "#status",
            "#wealth_status",
            ".status-ok",
            ".status-fail",
            ".status-warn",
            "@media (max-width:",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_action_and_panel_hooks_exist(self):
        for marker in [
            "Review ref_link proposals",
            "ref_link_review_modal",
            "ref_link_review_panel",
            "Apply selected",
            "Refresh scan",
            "Dismiss selected",
            "/api/ref_link_review_scan",
            "/api/ref_link_review_apply",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_apply_message_keeps_escaped_newlines(self):
        self.assertIn("fileList.join('\\n- ')", self.html)
        self.assertNotIn("fileList.join('\n- ')", self.html)

    def test_ref_link_review_simplified_workspace_hooks_exist(self):
        for marker in [
            "ref_link_review_modal",
            "ref_link_review_close",
            "ref_link_review_benchmark_url",
            "ref_link_review_status_filters",
            "ref_link_review_confidence_filters",
            "ref_link_review_reason_filters",
            "Clear filters",
            "Select visible",
            "Unselect visible",
            "Bulk actions apply only to the rows currently visible",
            "Restore selected",
            "setRefLinkReviewMultiSelectValues",
            "filteredRefLinkReviewRows",
            "renderRefLinkReviewUrl",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_repeated_bucket_actions_are_removed(self):
        self.assertNotIn("Select filtered", self.html)
        self.assertNotIn("Unselect filtered", self.html)

    def test_ref_link_review_progress_details_override_and_resize_hooks_exist(self):
        for marker in [
            "ref_link_review_scan_status",
            "ref_link_review_scan_progress",
            "ref_link_review_scan_progress_label",
            "ref_link_review_scan_progress_fill",
            "toggleRefLinkReviewDetails",
            "updateRefLinkReviewOverride",
            "ref_link_review_override_input",
            "beginRefLinkReviewColumnResize",
            "ref_link_review_resize_handle",
            "/api/ref_link_review_scan_status",
        ]:
            self.assertIn(marker, self.html)


if __name__ == "__main__":
    unittest.main()

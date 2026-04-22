import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

SOURCE_DIR = pathlib.Path("code/tools/sources").resolve()
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from source_paths import (
    DEFAULT_ALIASES_PATH,
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_DICTIONARY_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_WEALTH_CHANGE_LOG_PATH,
)


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
        cls.mod = load_ui_local_module()
        cls.html = cls.mod.HTML

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
        self.assertIn(DEFAULT_REGISTRY_PATH, self.html)
        self.assertIn(DEFAULT_CHANGE_LOG_PATH, self.html)

    def test_ref_link_review_simplified_workspace_hooks_exist(self):
        for marker in [
            "ref_link_review_modal",
            "ref_link_review_close",
            "ref_link_review_benchmark_url",
            "ref_link_review_filter_button_status",
            "ref_link_review_filter_button_confidence",
            "ref_link_review_filter_button_reason",
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

    def test_ref_link_review_compact_benchmark_and_toolbar_hooks_exist(self):
        for marker in [
            "ref-link-review-toolbar-note",
            "ref-link-review-benchmark-meta",
            "ref-link-review-benchmark-actions",
            "ref-link-review-toolbar-summary",
            "Benchmark:",
            "Last scan used:",
        ]:
            self.assertIn(marker, self.html)
        self.assertNotIn("Configured default:", self.html)

    def test_ref_link_review_tray_shell_hooks_exist(self):
        for marker in [
            "ref_link_review_topbar",
            "ref_link_review_topbar_summary",
            "ref_link_review_tray",
            "ref_link_review_tray_resize_handle",
            "ref_link_review_tray_sections",
            "ref_link_review_tray_section_filters",
            "ref_link_review_tray_section_benchmark",
            "ref_link_review_tray_section_actions",
            "ref_link_review_tray_section_help",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_tray_interaction_hooks_exist(self):
        for marker in [
            "toggleRefLinkReviewTraySection",
            "ref_link_review_tray_section_header_filters",
            "ref_link_review_tray_section_header_benchmark",
            "ref_link_review_tray_section_header_actions",
            "ref_link_review_tray_section_header_help",
            "toggleRefLinkReviewFilterPopover",
            "ref_link_review_filter_popover_status",
            "ref_link_review_filter_popover_confidence",
            "ref_link_review_filter_popover_reason",
            "ref-link-review-filter-popover",
            "refLinkReviewReasonLabel",
            "ref-link-review-topbar-status-line",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_tray_resize_and_responsive_hooks_exist(self):
        for marker in [
            "beginRefLinkReviewTrayResize",
            "applyRefLinkReviewTrayWidth",
            "refLinkReviewClampTrayWidth",
            "toggleRefLinkReviewTrayOpen",
            "ref_link_review_tray_toggle_button",
            "beginRefLinkReviewModalResize",
            "applyRefLinkReviewModalSize",
            "ref_link_review_modal_resize_handle",
        ]:
            self.assertIn(marker, self.html)

    def test_history_tab_restore_guidance_and_cleanup_hooks_exist(self):
        for marker in [
            "branch_history_tab",
            "Show Changes Up To",
            "Restore Guidance",
            "Remove History Record",
            "Filter to this time",
            "Removes history records only. Source records and generated files are not changed.",
            "/api/history",
            "/api/history/delete_entry",
            "history_summary_strip",
            "history_cleanup_reason",
            "history_cleanup_reason_suggestions",
            "historyApplyCleanupReason('testing noise')",
            "Remove History Records For This Source",
            "cleanup_scope",
            "Relaunch App",
            "/api/relaunch",
            "relaunchApp()",
        ]:
            self.assertIn(marker, self.html)

    def test_ref_link_review_compact_topbar_scan_hooks_exist(self):
        for marker in [
            "ref-link-review-scan-status-compact",
            "ref-link-review-title-line",
            "height = refLinkReviewClampModalHeight",
            "repeat(auto-fit, minmax(132px, 1fr))",
            "min-width: max(100%, 240px)",
        ]:
            self.assertIn(marker, self.html)

    def test_build_file_change_summary_matches_new_metadata_paths(self):
        summary = self.mod.build_file_change_summary(
            [
                f"/tmp/{DEFAULT_REGISTRY_PATH}",
                f"/tmp/{DEFAULT_CHANGE_LOG_PATH}",
                f"/tmp/{DEFAULT_ALIASES_PATH}",
                "handmade_tables/dictionary.xlsx",
            ],
            "edit",
            "src-example",
            ["source", "bib.title"],
            key_renamed=True,
        )
        summaries = {entry["file"]: entry["summary"] for entry in summary}
        self.assertIn("Updated record src-example.", summaries[f"/tmp/{DEFAULT_REGISTRY_PATH}"])
        self.assertIn("Added edit history record", summaries[f"/tmp/{DEFAULT_CHANGE_LOG_PATH}"])
        self.assertEqual(
            summaries[f"/tmp/{DEFAULT_ALIASES_PATH}"],
            "Added Source/Citekey alias mappings for key rename.",
        )
        self.assertEqual(
            summaries["handmade_tables/dictionary.xlsx"],
            "Regenerated Sources sheet from canonical registry.",
        )

    def test_build_file_change_summary_matches_new_wealth_log_path(self):
        summary = self.mod.build_file_change_summary(
            [f"/tmp/{DEFAULT_WEALTH_CHANGE_LOG_PATH}"],
            "delete",
            "wealth-key",
            [],
        )
        self.assertEqual(
            summary[0]["summary"],
            "Added delete Wealth Research history record for wealth-key.",
        )

    def test_build_ref_link_review_summary_matches_new_metadata_paths(self):
        summary = self.mod.build_ref_link_review_file_change_summary(
            [f"/tmp/{DEFAULT_REGISTRY_PATH}", f"/tmp/{DEFAULT_CHANGE_LOG_PATH}"],
            ["src-a", "src-b"],
        )
        summaries = {entry["file"]: entry["summary"] for entry in summary}
        self.assertEqual(
            summaries[f"/tmp/{DEFAULT_REGISTRY_PATH}"],
            "Updated ref_link for 2 record(s).",
        )
        self.assertEqual(
            summaries[f"/tmp/{DEFAULT_CHANGE_LOG_PATH}"],
            "Added 2 ref_link review history records.",
        )

    def test_history_file_descriptors_cover_data_registry_and_cleanup_paths(self):
        files = self.mod._history_file_descriptors(
            "data_sources",
            "edit",
            "Edited via local UI",
            pathlib.Path(DEFAULT_REGISTRY_PATH),
            pathlib.Path(DEFAULT_CHANGE_LOG_PATH),
            pathlib.Path(DEFAULT_ALIASES_PATH),
            {},
        )
        indexed = {entry["path"]: entry for entry in files}
        ordered_paths = [entry["path"] for entry in files]
        self.assertIn(DEFAULT_REGISTRY_PATH, indexed)
        self.assertIn(DEFAULT_CHANGE_LOG_PATH, indexed)
        self.assertIn(DEFAULT_ALIASES_PATH, indexed)
        self.assertTrue(indexed[DEFAULT_ALIASES_PATH]["optional"])
        self.assertEqual(ordered_paths[0], self.mod.DEFAULT_DICTIONARY_PATH)
        self.assertTrue(ordered_paths[1].endswith('.bib'))
        self.assertTrue(ordered_paths[2].endswith('.bib'))

    def test_delete_history_entry_removes_selected_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = pathlib.Path(tmpdir) / "change_log.json"
            log_path.write_text(
                json.dumps(
                    {
                        "changes": [
                            {"operation": "add", "record_id": "src-a", "reason": "test", "updated_at": "2026-03-24T10:00:00Z"},
                            {"operation": "edit", "record_id": "src-b", "reason": "test", "updated_at": "2026-03-24T11:00:00Z"},
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            removed = self.mod.delete_history_entry(log_path, 0)
            remaining = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(removed["record_id"], "src-a")
            self.assertEqual(len(remaining["changes"]), 1)
            self.assertEqual(remaining["changes"][0]["record_id"], "src-b")


    def test_delete_history_entries_for_record_only_updates_history_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            log_path = root / "change_log.json"
            source_path = root / DEFAULT_REGISTRY_PATH
            dictionary_path = root / DEFAULT_DICTIONARY_PATH
            bib_path = root / "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"
            for path, content in [
                (
                    log_path,
                    json.dumps(
                        {
                            "changes": [
                                {"operation": "add", "record_id": "src-a", "reason": "test", "updated_at": "2026-03-24T10:00:00Z"},
                                {"operation": "edit", "record_id": "src-a", "reason": "test", "updated_at": "2026-03-24T11:00:00Z"},
                                {"operation": "delete", "record_id": "src-b", "reason": "test", "updated_at": "2026-03-24T12:00:00Z"},
                            ]
                        },
                        indent=2,
                    )
                    + "\n",
                ),
                (source_path, '{"records": [{"id": "src-a"}]}\n'),
                (dictionary_path, "dictionary bytes"),
                (bib_path, "@article{a}\n"),
            ]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            before = {
                source_path: source_path.read_text(encoding="utf-8"),
                dictionary_path: dictionary_path.read_text(encoding="utf-8"),
                bib_path: bib_path.read_text(encoding="utf-8"),
            }
            removed = self.mod.delete_history_entries_for_record(log_path, "src-a")

            remaining = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(len(removed), 2)
            self.assertEqual(len(remaining["changes"]), 1)
            self.assertEqual(remaining["changes"][0]["record_id"], "src-b")
            for path, content in before.items():
                self.assertEqual(path.read_text(encoding="utf-8"), content)


    def test_delete_history_entries_for_record_removes_all_matching_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = pathlib.Path(tmpdir) / "change_log.json"
            log_path.write_text(
                json.dumps(
                    {
                        "changes": [
                            {"operation": "add", "record_id": "src-a", "reason": "test", "updated_at": "2026-03-24T10:00:00Z"},
                            {"operation": "edit", "record_id": "src-a", "reason": "test", "updated_at": "2026-03-24T11:00:00Z"},
                            {"operation": "delete", "record_id": "src-b", "reason": "test", "updated_at": "2026-03-24T12:00:00Z"},
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            removed = self.mod.delete_history_entries_for_record(log_path, "src-a")
            remaining = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(len(removed), 2)
            self.assertEqual([entry["record_id"] for entry in removed], ["src-a", "src-a"])
            self.assertEqual(len(remaining["changes"]), 1)
            self.assertEqual(remaining["changes"][0]["record_id"], "src-b")


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import pathlib
import sys
import unittest


def load_ref_link_review_module():
    path = pathlib.Path("code/tools/sources/ref_link_review.py").resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("ref_link_review", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_show_payload(entry_key, bibbaseid):
    entry = {
        "key": entry_key,
        "bibbaseid": bibbaseid,
        "bibtex": f"@article{{{entry_key},\n  title = {{Example Title}},\n  year = {{2024}}\n}}\n",
        "title": "Example Title",
        "year": "2024",
        "author": [{"lastnames": ["Example"], "firstnames": ["Eve"], "suffixes": [], "propositions": []}],
    }
    inner = f'var bibbase = {{data: {json.dumps([entry])}, groups: []}};'
    outer = {"data": inner}
    return f"var bibbase_data = {json.dumps(outer)}; document.write(bibbase_data.data);"


class RefLinkReviewScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_ref_link_review_module()

    def test_blank_exact_citekey_match_is_ready_to_apply(self):
        registry = {
            "config": {"bib_output": "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"},
            "records": [
                {
                    "id": "src-example",
                    "source": "Example2024",
                    "citekey": "Example2024",
                    "ref_link": "",
                    "link": "https://example.org/source",
                    "bib": {"title": "Example Title", "author": "Example, Eve", "year": "2024"},
                }
            ],
        }
        scan = self.mod.scan_registry_ref_links(
            registry,
            show_payload_text=make_show_payload("Example2024", "example-exampletitle-2024"),
            hosted_bib_text="@article{Example2024,}\n",
            local_bib_text="@article{Example2024,}\n",
        )
        self.assertEqual(scan["summary"]["ready_to_apply"], 1)
        proposal = scan["ready_to_apply"][0]
        self.assertEqual(proposal["record_id"], "src-example")
        self.assertTrue(proposal["selected"])
        self.assertEqual(
            proposal["proposed_ref_link"],
            "https://bibbase.org/network/publication/example-exampletitle-2024",
        )
        self.assertIn("blank ref_link, exact citekey match", proposal["reason_flags"])


if __name__ == "__main__":
    unittest.main()

import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SOURCE_DIR = pathlib.Path("code/tools/sources").resolve()
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import common


class DictionaryWorkbookWriteTests(unittest.TestCase):
    def test_invalid_temp_workbook_does_not_replace_existing_output(self):
        source_workbook = pathlib.Path("handmade_tables/dictionary.xlsx")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = pathlib.Path(tmpdir)
            template = tmpdir_path / "template.xlsx"
            output = tmpdir_path / "dictionary.xlsx"
            shutil.copy2(source_workbook, template)
            shutil.copy2(source_workbook, output)
            before = output.read_bytes()

            with mock.patch.object(common, "build_sources_sheet_xml", return_value=b"<worksheet><sheetData>"):
                with self.assertRaises(RuntimeError):
                    common.write_sources_sheet(template, output, [])

            self.assertEqual(output.read_bytes(), before)
            self.assertFalse(output.with_suffix(output.suffix + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()

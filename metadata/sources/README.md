# Canonical Source Registry

This folder is the canonical home for source metadata.

- `sources.yaml`: canonical source records (JSON-compatible YAML)
- `schema.json`: schema contract
- `aliases.yaml`: old->new key mappings for breaking renames
- `change_log.yaml`: intake audit entries
- `documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib`: generated bibliography artifact
- `reconciliation_report.md`: migration reconciliation report

## Policy

- Do not edit `handmade_tables/dictionary.xlsx` manually.
- `handmade_tables/dictionary.xlsx` (`Sources` sheet) and `.bib` are generated artifacts.
- Add/edit operations should go through the local Source Registry UI.
- Comment-only dictionary columns are intentionally omitted from the UI and kept blank in generated outputs.

## Commands

```bash
python3 code/tools/sources/build_sources_artifacts.py --registry metadata/sources/sources.yaml
python3 code/tools/sources/validate_sources.py --registry metadata/sources/sources.yaml --schema metadata/sources/schema.json --aliases metadata/sources/aliases.yaml --change-log metadata/sources/change_log.yaml --check-generated --dictionary handmade_tables/dictionary.xlsx --bib "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"
python3 code/tools/sources/ui_local.py
```

## Running The UI (Beginner-Friendly)

### Option 1 (Mac, double-click)

1. In Finder, open this repository folder.
2. Double-click `Launch_Source_Registry_UI.command`.
3. Your browser should open automatically at `http://127.0.0.1:8765`.
4. Keep the Terminal window open while using the UI.
5. Close the UI tab/window when finished; the local server stops automatically.

If macOS blocks the file the first time:
1. Right-click `Launch_Source_Registry_UI.command`.
2. Click `Open`.
3. Confirm `Open` in the dialog.

### Option 2 (Windows, double-click)

1. Open this repository folder in File Explorer.
2. Double-click `Launch_Source_Registry_UI.bat`.
3. Your browser opens at `http://127.0.0.1:8765`.
4. Keep the command window open while using the UI.
5. Close the UI tab/window when finished; the local server stops automatically.

### Option 3 (Terminal)

1. Open Terminal.
2. Go to the repository folder:
   - `cd /path/to/warehouse`
3. Run:
   - `python3 code/tools/sources/ui_local.py`
4. Open `http://127.0.0.1:8765` in your browser.

### If Python is missing

Run:
- `python3 --version`

If that fails, install Python 3 from:
- https://www.python.org/downloads/

## UI Behavior Notes

- Fields with `*` are mandatory.
- `keywords` is optional (recommended).
- Legend suggestion in add mode uses citation style:
  - `Lastname (year)` for one author
  - `Lastname et al. (year)` for multiple authors
- `Edit target` and `Change reason` only appear in `edit` mode.
- In edit mode, **Load existing entry** and **Delete entry** are grouped under **Edit tools**.
- Key rename confirmation is now prompted only when you actually change `Source / Citekey` during edit.
- `Source / Citekey` is entered once in the UI and saved to both:
  - `Source` (dictionary context)
  - `citekey` (BibTeX context)
- `Link` and bib `url` are kept as separate fields. In current data they are not always identical, so fusing them would lose information in some records.
- For **new entries**, the UI collects a single `URL / Link` field and writes that value to both `link` and bib `url`.
- For **existing entries (edit mode)**, `bib.url` can still be reviewed/adjusted separately when needed.
- `id` is internal and not shown in the form.
- `Your name` is required and saved in change logs; add/edit records also keep `created_by` / `updated_by`.
- BibTeX paste behavior:
  - Clicking **Parse BibTeX and fill fields** overwrites the mapped fields below.
  - Editing the paste box alone does nothing until Parse is clicked again.
  - After parsing, manual edits in fields below are the final values that will be saved.
- Button sequence is intentional:
  1. **Check entry (validation only, no save)**
  2. **Save entry + regenerate dictionary.xlsx and .bib**
  3. **Publish web BibTeX (copy canonical .bib to web path)**
- The status panel now shows an explicit checklist with `PASS`/`FAIL` for each validation category.
- Validation now also runs duplicate checks against generated artifacts:
  - `handmade_tables/dictionary.xlsx` (`Sources` sheet)
  - `documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib`
- Save/delete responses now list exactly which files were modified and include a per-file change summary.
- `Change reason` is required in edit mode during check and before save.
- Edit-only helper:
  - **Load existing entry into form**
- On validation/save errors, the UI shows a popup window and detailed error text in the status panel.
- In edit mode, you can delete an entry. The UI always asks for confirmation before deleting.

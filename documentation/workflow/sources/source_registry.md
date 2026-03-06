# Source Registry Workflow (Add/Edit)

## Canonical Data

Canonical records are stored in `metadata/sources/sources.yaml`.
Generated artifacts:
- `handmade_tables/dictionary.xlsx` (`Sources` sheet)
- `documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib`

## Contributor Flow

1. Launch local UI:
   - Mac double-click: `code/tools/source_manager_mac.command`
   - Windows double-click: `code/tools/source_manager_win.bat`
   - Or Terminal: `python3 code/tools/sources/ui_local.py`
2. Open `http://127.0.0.1:8765`.
3. Choose `mode` = `add` or `edit`.
4. Fill `Your name` (required).
5. For edit mode, pick an existing target from the suggested list and load it.
6. Optionally paste a full BibTeX entry and parse it into fields.
7. Validate in the UI (errors must be fixed before save).
8. Save and regenerate artifacts locally.
9. Close the UI tab/window to stop the local server.

## Duplicate Rules

- Exact duplicates are blocked in intake for:
  - `source`
  - `citekey`
  - normalized URL
  - normalized `(title, year)`
- Fuzzy similarities are surfaced as warnings for review.
- Existing values are suggested in the UI for:
  - `section`
  - `aggsource`
  - `data_type`
- Existing source keys are suggested for edit targets.
- Comment columns are not exposed in the UI.

## Field Semantics

- `Source / Citekey` is collected once and stored as both `source` and `citekey`.
- For new entries, one `URL / Link` input populates both `link` and bib `url`.
- For existing entries, `link` and bib `url` can still be reviewed separately if needed.
- `id` is internal and not user-editable.
- Fields marked with `*` are mandatory.
- `keywords` is optional (recommended).
- Legend suggestion for add mode is citation-style:
  - `Lastname (year)` for single author
  - `Lastname et al. (year)` for multi-author entries
- `Edit target` and `Change reason` are edit-only controls.
- Source/Citekey renames trigger an explicit save-time confirmation, then aliases are tracked.
- UI tracks who made the change via `Your name`, stored in logs and record audit fields.

## BibTeX Paste Semantics

- Pasting BibTeX and clicking parse overwrites mapped fields below.
- Changing the paste textarea without parsing again does nothing.
- Manual edits in fields below always win and are what gets saved.

## Action Sequence

1. Check entry (validation only, no save).
2. Save entry and regenerate artifacts.
Edit-only helper: Load existing entry.
If validation/save fails, the UI shows an error popup and full details in the status panel.
In edit mode, delete is available with a required confirmation prompt.

## Key Rename Tracking

When Source/Citekey is renamed and confirmed in edit mode:
- alias entries are recorded in `metadata/sources/aliases.yaml`
- change record is appended to `metadata/sources/change_log.yaml`

## Local Tooling

- Bootstrap registry from legacy files:
  - `python3 code/tools/sources/bootstrap_registry.py --dictionary handmade_tables/dictionary.xlsx --bib /path/to/file.bib --out metadata/sources/sources.yaml`
- Build artifacts:
  - `python3 code/tools/sources/build_sources_artifacts.py --registry metadata/sources/sources.yaml`
- Validate:
  - `python3 code/tools/sources/validate_sources.py --registry metadata/sources/sources.yaml --schema metadata/sources/schema.json --aliases metadata/sources/aliases.yaml --change-log metadata/sources/change_log.yaml --check-generated --dictionary handmade_tables/dictionary.xlsx --bib "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"`
- Local UI:
  - `python3 code/tools/sources/ui_local.py`
- Migration reconciliation report:
  - `python3 code/tools/sources/reconcile_report.py --registry metadata/sources/sources.yaml --bib "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib" --out metadata/sources/reconciliation_report.md`

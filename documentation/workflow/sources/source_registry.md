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
   - Linux double-click: `code/tools/source_manager_linux.desktop` (mark as executable/trusted once)
   - Linux Terminal: `bash code/tools/source_manager_linux.sh`
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
3. Compare local `.bib` with online reference.
4. Publish local `.bib` from the UI:
   - Click `Run setup check` in Step 4 (this now runs a push-auth dry-run check).
   - If Linux shows a GCM credential-store error, click `Configure GCM store` (sets a working `cache` store automatically).
   - Click `Connect GitHub` and complete browser sign-in when prompted.
   - If setup issues appear, follow the setup commands shown in the UI Status panel.
   - Click `Publish local .bib to GitHub`.
   - If another collaborator uses the same computer, click `Switch GitHub account` before they publish.
   - The publish step is restricted to `documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib`.
   - If setup shows `ahead` with only the canonical `.bib` file, either publish directly (it will push pending `.bib` commit(s)) or click `Clean pending publish commit` to uncommit and retry.
Edit-only helper: Load existing entry.
If validation/save fails, the UI shows an error popup and full details in the status panel.
In edit mode, delete is available with a required confirmation prompt.

## One-Time Machine Setup (for one-click publish)

Open a terminal and run these commands explicitly.

1. Configure git identity once:
   - `git config --global user.name "Your Name"`
   - `git config --global user.email "you@example.com"`
2. Install Git Credential Manager (GCM):
   - Windows: install or update Git for Windows (includes GCM): https://git-scm.com/download/win
   - macOS (Homebrew): `brew install --cask git-credential-manager`
   - Linux docs: https://github.com/git-ecosystem/git-credential-manager/blob/release/docs/install.md
   - Linux quick script: https://aka.ms/gcm/linux-install-source.sh
3. Configure GCM:
   - `git-credential-manager configure`
   - `git config --global credential.helper manager-core`
4. Verify setup:
   - `git config --global --get user.name`
   - `git config --global --get user.email`
   - `git config --global --get credential.helper`
5. Back in the UI, click `Run setup check` in Step 4.
6. Click `Connect GitHub` in Step 4 and finish browser sign-in.

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

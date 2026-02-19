#!/usr/bin/env python3
"""Local web UI for adding/editing source records with guardrails.

Run:
  python3 code/tools/sources/ui_local.py
Then open http://127.0.0.1:8765
"""

import argparse
import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

from common import load_json_yaml, load_registry, normalize_text, normalize_url, normalize_whitespace, parse_bib_entries, read_sources_sheet, save_registry

URL_RE = re.compile(r"^https?://", re.IGNORECASE)
DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
YEAR_RE = re.compile(r"^\d{4}$")


def now_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def append_change(changelog_path: Path, op: str, rec_id: str, reason: str, actor: str) -> None:
    data = load_json_yaml(changelog_path) or {"changes": []}
    data.setdefault("changes", []).append(
        {
            "operation": op,
            "record_id": rec_id,
            "reason": reason,
            "actor": actor,
            "issue_number": "local-ui",
            "updated_at": now_utc(),
        }
    )
    changelog_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_alias(aliases_path: Path, alias_type: str, old: str, new: str, reason: str) -> None:
    if not old or not new or old == new:
        return
    data = load_json_yaml(aliases_path) or {"aliases": []}
    data.setdefault("aliases", []).append(
        {
            "type": alias_type,
            "old": old,
            "new": new,
            "reason": reason,
            "updated_at": now_utc(),
        }
    )
    aliases_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def suggested_options(records: List[dict]) -> Dict[str, List[str]]:
    def uniq(key: str) -> List[str]:
        return sorted({normalize_whitespace(str(r.get(key, ""))) for r in records if normalize_whitespace(str(r.get(key, "")))})

    targets = sorted({normalize_whitespace(str(r.get("source", ""))) for r in records if normalize_whitespace(str(r.get("source", "")))})
    return {
        "section": uniq("section"),
        "aggsource": uniq("aggsource"),
        "data_type": uniq("data_type"),
        "inclusion_in_warehouse": uniq("inclusion_in_warehouse"),
        "targets": targets,
    }


def find_target(records: List[dict], target: str) -> List[dict]:
    t = normalize_whitespace(target)
    hits = []
    for r in records:
        if r.get("id", "") == t or r.get("source", "") == t or r.get("citekey", "") == t:
            hits.append(r)
    return hits


def validate_candidate(records: List[dict], candidate: dict, mode: str, target_id: str = "") -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    checks: List[dict] = []

    required = ["editor_name", "section", "aggsource", "legend", "source", "citekey", "link"]
    missing_required = [k for k in required if not normalize_whitespace(str(candidate.get(k, "")))]
    if missing_required:
        for k in missing_required:
            errors.append(f"Missing required field: {k}")
    checks.append({
        "name": "Required core fields",
        "passed": len(missing_required) == 0,
        "detail": "All required fields present" if not missing_required else f"Missing: {', '.join(missing_required)}",
    })

    if candidate.get("source", "") != candidate.get("citekey", ""):
        errors.append("source and citekey must be the same value")
    checks.append({
        "name": "Source/Citekey consistency",
        "passed": candidate.get("source", "") == candidate.get("citekey", ""),
        "detail": "source equals citekey",
    })

    bib = candidate.get("bib", {}) or {}
    missing_bib_required = [k for k in ["entry_type", "title", "author", "year"] if not normalize_whitespace(str(bib.get(k, "")))]
    if missing_bib_required:
        for k in missing_bib_required:
            errors.append(f"Missing required bib field: {k}")
    checks.append({
        "name": "Required BibTeX fields",
        "passed": len(missing_bib_required) == 0,
        "detail": "Bib required fields present" if not missing_bib_required else f"Missing: {', '.join(missing_bib_required)}",
    })

    link = normalize_whitespace(candidate.get("link", ""))
    ref_link = normalize_whitespace(candidate.get("ref_link", ""))
    bib_url = normalize_whitespace(str(bib.get("url", "")))
    doi = normalize_whitespace(str(bib.get("doi", "")))
    year = normalize_whitespace(str(bib.get("year", "")))

    bad_urls: List[str] = []
    if link and not URL_RE.match(link):
        bad_urls.append(f"link={link}")
        errors.append(f"Invalid URL in link: {link}")
    if ref_link and not URL_RE.match(ref_link):
        bad_urls.append(f"ref_link={ref_link}")
        errors.append(f"Invalid URL in ref_link: {ref_link}")
    if bib_url and not URL_RE.match(bib_url):
        bad_urls.append(f"bib.url={bib_url}")
        errors.append(f"Invalid URL in bib.url: {bib_url}")
    if not link and not bib_url:
        errors.append("Missing URL: provide at least Link (or bib.url)")
    checks.append({
        "name": "URL checks",
        "passed": (not bad_urls) and bool(link or bib_url),
        "detail": "URL fields valid" if (not bad_urls) and bool(link or bib_url) else ("; ".join(bad_urls) if bad_urls else "Missing link/bib.url"),
    })
    if doi and not DOI_RE.match(doi):
        warnings.append(f"DOI format looks unusual: {doi}")
    checks.append({
        "name": "DOI format",
        "passed": not doi or DOI_RE.match(doi) is not None,
        "detail": "DOI is empty or valid pattern" if (not doi or DOI_RE.match(doi) is not None) else "Unusual DOI format (warning)",
    })
    if year and not YEAR_RE.match(year):
        errors.append(f"Year must be YYYY: {year}")
    checks.append({
        "name": "Year format",
        "passed": bool(year) and YEAR_RE.match(year) is not None,
        "detail": "Year uses YYYY" if bool(year) and YEAR_RE.match(year) is not None else "Year must be YYYY",
    })

    c_source = normalize_whitespace(candidate.get("source", ""))
    c_citekey = normalize_whitespace(candidate.get("citekey", ""))
    c_url = normalize_url(bib_url or link)
    c_title = normalize_text(str(bib.get("title", "")))
    c_year = normalize_whitespace(str(bib.get("year", "")))

    duplicate_errors: List[str] = []
    for rec in records:
        rid = rec.get("id", "")
        if mode == "edit" and target_id and rid == target_id:
            continue

        r_source = normalize_whitespace(rec.get("source", ""))
        r_citekey = normalize_whitespace(rec.get("citekey", ""))
        r_url = normalize_url(str((rec.get("bib", {}) or {}).get("url", "") or rec.get("link", "")))
        r_title = normalize_text(str((rec.get("bib", {}) or {}).get("title", "")))
        r_year = normalize_whitespace(str((rec.get("bib", {}) or {}).get("year", "")))

        if c_source and c_source == r_source:
            msg = f"Exact duplicate source found: {c_source} (record {rid})"
            duplicate_errors.append(msg)
            errors.append(msg)
        if c_citekey and c_citekey == r_citekey:
            msg = f"Exact duplicate citekey found: {c_citekey} (record {rid})"
            duplicate_errors.append(msg)
            errors.append(msg)
        if c_url and c_url == r_url:
            msg = f"Exact duplicate URL found: {c_url} (record {rid})"
            duplicate_errors.append(msg)
            errors.append(msg)
        if c_title and c_year and c_title == r_title and c_year == r_year:
            msg = f"Exact duplicate (title, year) found: ({c_title}, {c_year}) (record {rid})"
            duplicate_errors.append(msg)
            errors.append(msg)
    checks.append({
        "name": "Exact duplicate checks",
        "passed": len(duplicate_errors) == 0,
        "detail": "No exact duplicates found" if not duplicate_errors else "; ".join(sorted(set(duplicate_errors))),
    })

    return {"errors": sorted(set(errors)), "warnings": sorted(set(warnings)), "checks": checks}


def validate_candidate_against_artifacts(registry: dict, candidate: dict, mode: str, target: str = "") -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    checks: List[dict] = []
    cfg = registry.get("config", {}) or {}
    dictionary_path = Path(cfg.get("dictionary_output", "handmade_tables/dictionary.xlsx"))
    bib_path = Path(cfg.get("bib_output", "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"))
    target_norm = normalize_whitespace(target)

    c_source = normalize_whitespace(candidate.get("source", ""))
    c_citekey = normalize_whitespace(candidate.get("citekey", ""))
    cbib = candidate.get("bib", {}) or {}
    c_url = normalize_url(str(cbib.get("url", "") or candidate.get("link", "")))
    c_title = normalize_text(str(cbib.get("title", "")))
    c_year = normalize_whitespace(str(cbib.get("year", "")))

    dict_checked = False
    dict_errors: List[str] = []
    if dictionary_path.exists():
        try:
            rows = read_sources_sheet(dictionary_path)
            dict_checked = True
            for row in rows:
                r_source = normalize_whitespace(str(row.get("Source", "")))
                r_citekey = normalize_whitespace(str(row.get("Citekey", "")))
                r_link = normalize_url(str(row.get("Link", "")))
                if mode == "edit" and target_norm and (r_source == target_norm or r_citekey == target_norm):
                    continue
                if c_source and c_source == r_source:
                    dict_errors.append(f"Dictionary duplicate Source: {c_source}")
                if c_citekey and c_citekey == r_citekey:
                    dict_errors.append(f"Dictionary duplicate Citekey: {c_citekey}")
                if c_url and c_url == r_link:
                    dict_errors.append(f"Dictionary duplicate Link URL: {c_url}")
            errors.extend(dict_errors)
        except Exception as exc:  # pylint: disable=broad-except
            warnings.append(f"Dictionary check could not run: {exc}")
    else:
        warnings.append(f"Dictionary artifact not found: {dictionary_path}")
    checks.append({
        "name": "Dictionary artifact duplicate checks",
        "passed": dict_checked and len(dict_errors) == 0,
        "detail": "No exact duplicates in dictionary.xlsx Sources sheet"
        if dict_checked and not dict_errors
        else ("; ".join(sorted(set(dict_errors))) if dict_errors else "Dictionary check skipped"),
    })

    bib_checked = False
    bib_errors: List[str] = []
    if bib_path.exists():
        try:
            entries = parse_bib_entries(bib_path.read_text(encoding="utf-8"))
            bib_checked = True
            for key, entry in entries.items():
                k_norm = normalize_whitespace(key)
                if mode == "edit" and target_norm and k_norm == target_norm:
                    continue
                if c_citekey and c_citekey == k_norm:
                    bib_errors.append(f"Bib duplicate key: {c_citekey}")
                fields = entry.get("fields", {}) or {}
                e_url = normalize_url(str(fields.get("url", "")))
                e_title = normalize_text(str(fields.get("title", "")))
                e_year = normalize_whitespace(str(fields.get("year", "")))
                if c_url and c_url == e_url:
                    bib_errors.append(f"Bib duplicate URL: {c_url} (key {key})")
                if c_title and c_year and c_title == e_title and c_year == e_year:
                    bib_errors.append(f"Bib duplicate (title, year): ({c_title}, {c_year}) (key {key})")
            errors.extend(bib_errors)
        except Exception as exc:  # pylint: disable=broad-except
            warnings.append(f"Bib artifact check could not run: {exc}")
    else:
        warnings.append(f"Bib artifact not found: {bib_path}")
    checks.append({
        "name": "Bib artifact duplicate checks",
        "passed": bib_checked and len(bib_errors) == 0,
        "detail": "No exact duplicates in generated .bib"
        if bib_checked and not bib_errors
        else ("; ".join(sorted(set(bib_errors))) if bib_errors else "Bib check skipped"),
    })

    return {"errors": sorted(set(errors)), "warnings": sorted(set(warnings)), "checks": checks}


def parse_bib_paste(text: str) -> dict:
    entries = parse_bib_entries(text)
    if not entries:
        raise ValueError("No BibTeX entry found")
    key = next(iter(entries.keys()))
    entry = entries[key]
    fields = entry.get("fields", {})
    return {
        "source_key": key,
        "bib": {
            "entry_type": entry.get("entry_type", "misc"),
            "title": fields.get("title", ""),
            "author": fields.get("author", ""),
            "year": fields.get("year", ""),
            "month": fields.get("month", ""),
            "journal": fields.get("journal", ""),
            "booktitle": fields.get("booktitle", ""),
            "volume": fields.get("volume", ""),
            "number": fields.get("number", ""),
            "pages": fields.get("pages", ""),
            "institution": fields.get("institution", ""),
            "publisher": fields.get("publisher", ""),
            "doi": fields.get("doi", ""),
            "url": fields.get("url", ""),
            "urldate": fields.get("urldate", ""),
            "abstract": fields.get("abstract", ""),
            "keywords": fields.get("keywords", ""),
            "note": fields.get("note", ""),
            "extra_fields": {k: v for k, v in fields.items() if k not in {
                "title", "author", "year", "month", "journal", "booktitle", "volume", "number", "pages",
                "institution", "publisher", "doi", "url", "urldate", "abstract", "keywords", "note"
            }},
        },
    }


def make_candidate(payload: dict) -> dict:
    record = payload.get("record", {})
    mode = normalize_whitespace(payload.get("mode", "add")).lower()
    source_key = normalize_whitespace(record.get("source_key", ""))
    bib = record.get("bib", {}) or {}
    link_value = normalize_whitespace(record.get("link", ""))
    bib_url_value = normalize_whitespace(bib.get("url", ""))
    if mode == "add":
        # For new entries, collect one URL field and mirror it to both link and bib.url.
        bib_url_value = link_value or bib_url_value
    editor_name = normalize_whitespace(payload.get("editor_name", "")) or normalize_whitespace(record.get("editor_name", ""))
    return {
        "section": normalize_whitespace(record.get("section", "")),
        "aggsource": normalize_whitespace(record.get("aggsource", "")),
        "legend": normalize_whitespace(record.get("legend", "")),
        "source": source_key,
        "citekey": source_key,
        "data_type": normalize_whitespace(record.get("data_type", "")),
        "link": link_value,
        "ref_link": normalize_whitespace(record.get("ref_link", "")),
        "inclusion_in_warehouse": normalize_whitespace(record.get("inclusion_in_warehouse", "")),
        "multigeo_reference": normalize_whitespace(record.get("multigeo_reference", "")),
        "metadata": normalize_whitespace(record.get("metadata", "")),
        "metadatalink": normalize_whitespace(record.get("metadatalink", "")),
        "editor_name": editor_name,
        "bib": {
            "entry_type": normalize_whitespace(bib.get("entry_type", "misc")) or "misc",
            "title": normalize_whitespace(bib.get("title", "")),
            "author": normalize_whitespace(bib.get("author", "")),
            "year": normalize_whitespace(bib.get("year", "")),
            "month": normalize_whitespace(bib.get("month", "")),
            "journal": normalize_whitespace(bib.get("journal", "")),
            "booktitle": normalize_whitespace(bib.get("booktitle", "")),
            "volume": normalize_whitespace(bib.get("volume", "")),
            "number": normalize_whitespace(bib.get("number", "")),
            "pages": normalize_whitespace(bib.get("pages", "")),
            "institution": normalize_whitespace(bib.get("institution", "")),
            "publisher": normalize_whitespace(bib.get("publisher", "")),
            "doi": normalize_whitespace(bib.get("doi", "")),
            "url": bib_url_value,
            "urldate": normalize_whitespace(bib.get("urldate", "")),
            "abstract": normalize_whitespace(bib.get("abstract", "")),
            "keywords": normalize_whitespace(bib.get("keywords", "")),
            "note": normalize_whitespace(bib.get("note", "")),
            "extra_fields": bib.get("extra_fields", {}) or {},
        },
    }


SUMMARY_TOP_FIELDS = [
    "section", "aggsource", "legend", "source", "citekey", "data_type", "link", "ref_link",
    "inclusion_in_warehouse", "multigeo_reference", "metadata", "metadatalink",
]
SUMMARY_BIB_FIELDS = [
    "entry_type", "title", "author", "year", "month", "journal", "booktitle",
    "volume", "number", "pages", "institution", "publisher", "doi", "url",
    "urldate", "abstract", "keywords", "note",
]


def summarize_record_all_fields(rec: dict) -> List[str]:
    out: List[str] = []
    for key in SUMMARY_TOP_FIELDS:
        if normalize_whitespace(str(rec.get(key, ""))):
            out.append(key)
    bib = rec.get("bib", {}) or {}
    for key in SUMMARY_BIB_FIELDS:
        if normalize_whitespace(str(bib.get(key, ""))):
            out.append(f"bib.{key}")
    return out


def summarize_record_diff(before: dict, after: dict) -> List[str]:
    changed: List[str] = []
    for key in SUMMARY_TOP_FIELDS:
        if normalize_whitespace(str(before.get(key, ""))) != normalize_whitespace(str(after.get(key, ""))):
            changed.append(key)
    bbib = before.get("bib", {}) or {}
    abib = after.get("bib", {}) or {}
    for key in SUMMARY_BIB_FIELDS:
        if normalize_whitespace(str(bbib.get(key, ""))) != normalize_whitespace(str(abib.get(key, ""))):
            changed.append(f"bib.{key}")
    return changed


def build_file_change_summary(modified_files: List[str], operation: str, record_id: str, changed_fields: List[str], key_renamed: bool = False) -> List[dict]:
    summary: List[dict] = []
    for fp in modified_files:
        p = str(fp)
        if p.endswith("metadata/sources/sources.yaml"):
            if operation == "edit":
                text = f"Updated record {record_id}. Fields changed: {', '.join(changed_fields) if changed_fields else '(none detected)'}."
            elif operation == "add":
                text = f"Added record {record_id}. Fields populated: {', '.join(changed_fields) if changed_fields else '(none detected)'}."
            else:
                text = f"Deleted record {record_id}. Removed fields: {', '.join(changed_fields) if changed_fields else '(none detected)'}."
            summary.append({"file": p, "summary": text})
            continue
        if p.endswith("metadata/sources/change_log.yaml"):
            summary.append({"file": p, "summary": f"Appended {operation} audit entry for {record_id}."})
            continue
        if p.endswith("metadata/sources/aliases.yaml"):
            if key_renamed:
                summary.append({"file": p, "summary": "Added Source/Citekey alias mappings for key rename."})
            else:
                summary.append({"file": p, "summary": "Aliases file touched."})
            continue
        if p.endswith("handmade_tables/dictionary.xlsx"):
            summary.append({"file": p, "summary": "Regenerated Sources sheet from canonical registry."})
            continue
        if p.endswith(".bib"):
            summary.append({"file": p, "summary": "Regenerated or published BibTeX artifact."})
            continue
        summary.append({"file": p, "summary": "File updated."})
    return summary


def apply_payload(registry: dict, payload: dict, aliases_path: Path, changelog_path: Path) -> dict:
    records = registry.get("records", [])
    mode = normalize_whitespace(payload.get("mode", "add")).lower()
    target = normalize_whitespace(payload.get("target", ""))
    reason = normalize_whitespace(payload.get("change_reason", ""))
    breaking = bool(payload.get("key_rename_confirmed", False))
    candidate = make_candidate(payload)
    editor_name = normalize_whitespace(candidate.get("editor_name", ""))

    if not editor_name:
        raise ValueError("editor_name is required")
    if mode not in {"add", "edit"}:
        raise ValueError("mode must be add or edit")

    if mode == "edit":
        if not reason:
            raise ValueError("change_reason is required for edit mode")
        hits = find_target(records, target)
        if len(hits) != 1:
            raise ValueError(f"Edit target must match exactly one record; got {len(hits)}")

        rec = hits[0]
        before_rec = json.loads(json.dumps(rec))
        before_source = rec.get("source", "")
        before_citekey = rec.get("citekey", "")

        updated = json.loads(json.dumps(rec))
        for k in [
            "section", "aggsource", "legend", "source", "citekey", "data_type", "link", "ref_link",
            "inclusion_in_warehouse", "multigeo_reference", "metadata", "metadatalink"
        ]:
            val = candidate.get(k, "")
            if val != "":
                updated[k] = val

        ubib = updated.get("bib", {})
        for k, v in candidate.get("bib", {}).items():
            if k == "extra_fields":
                continue
            if v != "":
                ubib[k] = v
        updated["bib"] = ubib
        updated["updated_at"] = now_utc()
        updated["updated_by"] = editor_name

        changed_key = before_source != updated.get("source", "") or before_citekey != updated.get("citekey", "")
        if changed_key and not breaking:
            raise ValueError("You changed Source/Citekey. Confirm key rename in the save confirmation prompt.")

        updated_for_validation = json.loads(json.dumps(updated))
        updated_for_validation["editor_name"] = editor_name
        check = validate_candidate(records, updated_for_validation, mode="edit", target_id=rec.get("id", ""))
        if check["errors"]:
            raise ValueError("\n".join(check["errors"]))

        rec.update(updated)

        if changed_key and breaking:
            append_alias(aliases_path, "source", before_source, rec.get("source", ""), reason)
            append_alias(aliases_path, "citekey", before_citekey, rec.get("citekey", ""), reason)

        append_change(changelog_path, "edit", rec.get("id", ""), reason, editor_name)
        return {
            "status": "ok",
            "operation": "edit",
            "warnings": check["warnings"],
            "checks": check["checks"],
            "record_id": rec.get("id", ""),
            "changed_fields": summarize_record_diff(before_rec, rec),
            "key_renamed": changed_key,
        }

    if not reason:
        reason = "Added via local UI"

    newrec = {
        "id": f"src-{re.sub(r'[^A-Za-z0-9]+', '-', candidate.get('source', '').strip()).strip('-').lower()}",
        "section": candidate.get("section", ""),
        "aggsource": candidate.get("aggsource", ""),
        "legend": candidate.get("legend", ""),
        "source": candidate.get("source", ""),
        "data_type": candidate.get("data_type", ""),
        "link": candidate.get("link", ""),
        "ref_link": candidate.get("ref_link", ""),
        "citekey": candidate.get("citekey", ""),
        "inclusion_in_warehouse": candidate.get("inclusion_in_warehouse", ""),
        "multigeo_reference": candidate.get("multigeo_reference", ""),
        "metadata": candidate.get("metadata", ""),
        "metadatalink": candidate.get("metadatalink", ""),
        "qcommentsforta": "",
        "tareply": "",
        "tacomments": "",
        "arjcomments": "",
        "arjreplies": "",
        "seeaggsourcelisthere": "",
        "bib": candidate.get("bib", {}),
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "created_by": editor_name,
        "updated_by": editor_name,
    }

    check = validate_candidate(records, candidate, mode="add")
    if check["errors"]:
        raise ValueError("\n".join(check["errors"]))

    records.append(newrec)
    append_change(changelog_path, "add", newrec.get("id", ""), reason, editor_name)
    return {
        "status": "ok",
        "operation": "add",
        "warnings": check["warnings"],
        "checks": check["checks"],
        "record_id": newrec.get("id", ""),
        "changed_fields": summarize_record_all_fields(newrec),
        "key_renamed": False,
    }


HTML = """<!doctype html>
<html>
<head>
<meta charset='utf-8' />
<title>Source Registry UI</title>
<style>
body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; background: #f6f7f9; }
.wrap { max-width: 1180px; margin: 0 auto; background: #fff; border: 1px solid #d9dde3; border-radius: 10px; padding: 20px; }
.grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
label { font-size: 12px; color: #4d5663; font-weight: 600; display: block; margin-bottom: 6px; }
input, textarea, select { width: 100%; padding: 10px; border: 1px solid #cfd6e0; border-radius: 6px; font-size: 13px; }
textarea { min-height: 86px; }
.row { margin-bottom: 16px; }
button { background: #0b57d0; color: white; border: 0; border-radius: 6px; padding: 10px 12px; font-size: 13px; cursor: pointer; }
button.secondary { background: #4d5968; }
button.warn { background: #a03d02; }
small { color: #5f6977; }
pre { background: #0e1116; color: #dce4ef; padding: 12px; border-radius: 8px; overflow: auto; }
.help { background: #f0f4fa; border: 1px solid #d9e3f0; padding: 10px; border-radius: 8px; color: #334455; font-size: 12px; }
.req { color: #ad2b2b; }
.step { font-weight: 700; color: #123a66; margin-bottom: 6px; }
.hidden { display: none; }
</style>
</head>
<body>
<div class='wrap'>
  <h2>Source Registry Manager (Local)</h2>
  <p><small>Canonical file: <code>metadata/sources/sources.yaml</code>. This UI validates and writes locally.</small></p>

  <div class='grid3 row'>
    <div>
      <label>Mode <span class='req'>*</span></label>
      <select id='mode' onchange='onModeChange()'>
        <option value='add'>add (new source)</option>
        <option value='edit'>edit (existing source)</option>
      </select>
    </div>
    <div>
      <label>Your name <span class='req'>*</span></label>
      <input id='editor_name' placeholder='Your full name'>
    </div>
    <div id='editTargetWrap' class='hidden'>
      <label>Edit target (existing Source/Citekey) <span class='req'>*</span></label>
      <input id='target' list='target_opts' placeholder='Start typing an existing source'>
    </div>
  </div>

  <div id='editReasonWrap' class='row hidden'>
    <label>Change reason <span class='req'>*</span></label>
    <input id='change_reason' list='change_reason_opts' placeholder='Select or write a reason'>
  </div>

  <div id='editToolsWrap' class='row hidden'>
    <div class='step'>Edit tools</div>
    <div style='display:flex; gap:10px; flex-wrap:wrap;'>
      <button id='loadBtn' onclick='loadTarget()'>Load existing entry into form</button>
      <button class='warn' onclick='deleteEntry()'>Delete entry (requires confirmation)</button>
    </div>
  </div>

  <details open>
    <summary><b>BibTeX Paste</b></summary>
    <div class='help' style='margin-top:8px;'>
      Paste a full BibTeX entry here (for example, from Google Scholar), then click <b>Parse BibTeX and fill fields</b>.
      Parsing copies data into the fields below and <b>overwrites those target fields</b>.
      Editing the paste box alone does nothing until Parse is clicked again.
      After parsing, any manual edits in the fields below are what will be saved.
    </div>
    <div class='row' style='margin-top:8px;'>
      <textarea id='bib_paste' placeholder='@article{Key, ...}'></textarea>
      <button class='secondary' onclick='parseBib()'>Parse BibTeX and fill fields</button>
    </div>
  </details>

  <h3>Core Source Fields</h3>
  <div class='grid3'>
    <div class='row'><label>Section <span class='req'>*</span></label><input id='section' list='section_opts'></div>
    <div class='row'><label>Aggsource <span class='req'>*</span></label><input id='aggsource' list='aggsource_opts'></div>
    <div class='row'><label>Legend <span class='req'>*</span></label><input id='legend'></div>
    <div class='row'><label>Source / Citekey <span class='req'>*</span></label><input id='source_key' placeholder='Same value used as Source and citekey'></div>
    <div class='row'><label>URL / Link <span class='req'>*</span></label><input id='link'></div>
  </div>

  <h3>Bib Fields</h3>
  <div class='grid3'>
    <div class='row'>
      <label>entry_type <span class='req'>*</span></label>
      <select id='bib_entry_type' onchange='onEntryTypeChange()'>
        <option value='article'>article</option>
        <option value='book'>book</option>
        <option value='incollection'>incollection</option>
        <option value='inproceedings'>inproceedings</option>
        <option value='techreport'>techreport</option>
        <option value='misc'>misc</option>
        <option value='unpublished'>unpublished</option>
      </select>
    </div>
    <div class='row'><label>author <span class='req'>*</span></label><input id='bib_author' oninput='trySuggestLegend()'></div>
    <div class='row'><label>year <span class='req'>*</span></label><input id='bib_year' oninput='trySuggestLegend()'></div>
    <div class='row hidden' id='row_bib_url'><label>bib.url (edit only)</label><input id='bib_url'></div>
  </div>
  <div class='row'><label>title <span class='req'>*</span></label><input id='bib_title'></div>

  <details>
    <summary><b>More fields</b> (optional)</summary>
    <div class='grid3' style='margin-top:10px;'>
      <div class='row'><label>Data_type</label><input id='data_type' list='data_type_opts'></div>
      <div class='row'><label>Ref_link</label><input id='ref_link'></div>
      <div class='row'><label>Inclusion_in_Warehouse</label><input id='inclusion_in_warehouse' list='inclusion_in_warehouse_opts'></div>
      <div class='row'><label>Multigeo_Reference</label><input id='multigeo_reference'></div>
      <div class='row'><label>Metadatalink</label><input id='metadatalink'></div>
      <div class='row'><label>month</label><input id='bib_month'></div>
      <div class='row' id='row_bib_journal'><label>journal</label><input id='bib_journal'></div>
      <div class='row' id='row_bib_booktitle'><label>booktitle</label><input id='bib_booktitle'></div>
      <div class='row'><label>volume</label><input id='bib_volume'></div>
      <div class='row'><label>number</label><input id='bib_number'></div>
      <div class='row'><label>pages</label><input id='bib_pages'></div>
      <div class='row' id='row_bib_institution'><label>institution</label><input id='bib_institution'></div>
      <div class='row' id='row_bib_publisher'><label>publisher</label><input id='bib_publisher'></div>
      <div class='row'><label>doi</label><input id='bib_doi'></div>
      <div class='row'><label>urldate</label><input id='bib_urldate'></div>
      <div class='row'><label>keywords</label><input id='bib_keywords'></div>
      <div class='row'><label>note</label><input id='bib_note'></div>
    </div>
    <div class='row'><label>Metadata</label><textarea id='metadata'></textarea></div>
    <div class='row'><label>abstract</label><textarea id='bib_abstract'></textarea></div>
  </details>

  <h3>Actions (in order)</h3>
  <div class='row'>
    <div class='step'>Step 1: Check entry</div>
    <button class='secondary' onclick='validateOnly()'>Check entry (validation only, no save)</button>
  </div>
  <div class='row'>
    <div class='step'>Step 2: Save and regenerate files</div>
    <button class='warn' onclick='applyAndBuild()'>Save entry + regenerate dictionary.xlsx and .bib</button>
  </div>
  <datalist id='section_opts'></datalist>
  <datalist id='aggsource_opts'></datalist>
  <datalist id='data_type_opts'></datalist>
  <datalist id='target_opts'></datalist>
  <datalist id='inclusion_in_warehouse_opts'></datalist>
  <datalist id='change_reason_opts'>
    <option value='Correct typo/formatting only'></option>
    <option value='Update metadata fields (section/aggsource/data_type)'></option>
    <option value='Update bibliographic details (title/author/year/journal)'></option>
    <option value='Update URL or DOI'></option>
    <option value='Align Source/Citekey naming convention'></option>
    <option value='Merge duplicate entry information'></option>
    <option value='Delete obsolete or duplicate entry'></option>
  </datalist>

  <h3>Status</h3>
  <pre id='status'></pre>
</div>

<script>
function v(id){ return document.getElementById(id).value || ''; }
function setStatus(obj){ document.getElementById('status').textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2); }
function showErrorWindow(msg){
  alert(`There are validation errors:\\n\\n${msg}`);
}
let dirty = false;
let loadedSourceKey = '';
function markDirty(){ dirty = true; }
function clearDirty(){ dirty = false; }

function onModeChange(){
  const mode = v('mode');
  const isEdit = mode === 'edit';
  for (const id of ['editTargetWrap','editReasonWrap','editToolsWrap']) {
    document.getElementById(id).classList.toggle('hidden', !isEdit);
  }
  document.getElementById('loadBtn').disabled = !isEdit;
  document.getElementById('row_bib_url').classList.toggle('hidden', !isEdit);
  if (!isEdit) {
    loadedSourceKey = '';
    document.getElementById('target').value = '';
    document.getElementById('change_reason').value = '';
    // In add mode we keep a single URL field and mirror it to bib.url on save.
    document.getElementById('bib_url').value = '';
  }
}

function firstAuthorLastName(authorText){
  const s = (authorText || '').trim();
  if(!s) return '';
  const first = s.split(/\s+and\s+/i)[0].trim();
  if(first.includes(',')) return first.split(',')[0].trim();
  const parts = first.split(/\s+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

function trySuggestLegend(){
  if(v('mode') !== 'add') return;
  const legendEl = document.getElementById('legend');
  if(legendEl.dataset.userEdited === '1') return;
  if((legendEl.value || '').trim() !== '') return;
  const authorText = (v('bib_author') || '').trim();
  const ln = firstAuthorLastName(authorText);
  if(!ln) return;
  const yr = (v('bib_year') || '').trim() || 'n.d.';
  const multi = /\sand\s/i.test(authorText);
  legendEl.value = multi ? `${ln} et al. (${yr})` : `${ln} (${yr})`;
}

function onEntryTypeChange(){
  const t = v('bib_entry_type');
  const show = (id, flag) => document.getElementById(id).classList.toggle('hidden', !flag);
  const isEdit = v('mode') === 'edit';
  show('row_bib_url', isEdit);
  show('row_bib_journal', t === 'article');
  show('row_bib_booktitle', t === 'incollection' || t === 'inproceedings');
  show('row_bib_publisher', t === 'book' || t === 'incollection' || t === 'inproceedings');
  show('row_bib_institution', t === 'techreport');
}

function getPayload(){
  return {
    mode: v('mode'),
    editor_name: v('editor_name'),
    target: v('target'),
    change_reason: v('change_reason'),
    key_rename_confirmed: false,
    record: {
      section: v('section'), aggsource: v('aggsource'), legend: v('legend'), source_key: v('source_key'),
      data_type: v('data_type'), link: v('link'), ref_link: v('ref_link'), inclusion_in_warehouse: v('inclusion_in_warehouse'),
      multigeo_reference: v('multigeo_reference'), metadata: v('metadata'), metadatalink: v('metadatalink'),
      bib: {
        entry_type: v('bib_entry_type'), title: v('bib_title'), author: v('bib_author'), year: v('bib_year'), month: v('bib_month'),
        journal: v('bib_journal'), booktitle: v('bib_booktitle'), volume: v('bib_volume'), number: v('bib_number'), pages: v('bib_pages'),
        institution: v('bib_institution'), publisher: v('bib_publisher'), doi: v('bib_doi'), url: v('bib_url'), urldate: v('bib_urldate'),
        keywords: v('bib_keywords'), note: v('bib_note'), abstract: v('bib_abstract')
      }
    }
  }
}

function formatChecks(out){
  const checks = out.checks || [];
  if (!checks.length) return '';
  const lines = ['Checks performed:'];
  checks.forEach(ch => {
    const tag = ch.passed ? 'PASS' : 'FAIL';
    const detail = ch.detail ? ` - ${ch.detail}` : '';
    lines.push(`- [${tag}] ${ch.name}${detail}`);
  });
  return lines.join('\\n');
}

function buildDuplicateGuidance(out){
  const errors = out.errors || [];
  const dup = errors.filter(e =>
    e.includes('Exact duplicate') ||
    e.includes('Dictionary duplicate') ||
    e.includes('Bib duplicate')
  );
  if (!dup.length) return '';
  const fieldHints = [];
  if (dup.some(e => e.toLowerCase().includes('source'))) fieldHints.push('Source / Citekey');
  if (dup.some(e => e.toLowerCase().includes('citekey') || e.toLowerCase().includes('key'))) fieldHints.push('Source / Citekey');
  if (dup.some(e => e.toLowerCase().includes('url') || e.toLowerCase().includes('link'))) fieldHints.push('URL / Link');
  if (dup.some(e => e.toLowerCase().includes('title'))) fieldHints.push('bib.title');
  if (dup.some(e => e.toLowerCase().includes('year'))) fieldHints.push('bib.year');
  const uniqueHints = [...new Set(fieldHints)];
  const lines = [
    'A record with similar information already exists.',
    'Please switch to edit mode if you want to modify an existing entry, or choose different values for:',
  ];
  if (uniqueHints.length) {
    lines.push(`- ${uniqueHints.join('\\n- ')}`);
  } else {
    lines.push('- Source / Citekey');
    lines.push('- URL / Link');
    lines.push('- bib.title / bib.year');
  }
  return lines.join('\\n');
}

function setStatusWithChecks(out, heading){
  const lines = [];
  if (heading) lines.push(heading);
  const summary = (out.ok || out.status === 'ok') ? 'Overall result: PASSED' : 'Overall result: FAILED';
  lines.push(summary);
  const checkText = formatChecks(out);
  if (checkText) lines.push(checkText);
  if (v('mode') === 'add') {
    const dupGuidance = buildDuplicateGuidance(out);
    if (dupGuidance) lines.push(dupGuidance);
  }
  if (out.warnings && out.warnings.length) lines.push(`Warnings:\\n- ${out.warnings.join('\\n- ')}`);
  if (out.errors && out.errors.length) lines.push(`Errors:\\n- ${out.errors.join('\\n- ')}`);
  if (out.file_change_summary && out.file_change_summary.length) {
    const details = out.file_change_summary.map(x => `- ${x.file}: ${x.summary}`);
    lines.push(`Modification summary by file:\\n${details.join('\\n')}`);
  }
  setStatus(lines.join('\\n\\n'));
}

async function req(path, payload){
  let r;
  try {
    r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload || {})});
  } catch (err) {
    throw new Error(`Could not reach local server while calling ${path}. The UI server may have stopped.`);
  }
  let j = {};
  try {
    j = await r.json();
  } catch (err) {
    throw new Error(`Server returned an unreadable response for ${path} (HTTP ${r.status}).`);
  }
  if(!r.ok){ throw new Error(j.error || ('HTTP '+r.status)); }
  return j;
}

async function loadOptions(){
  let r;
  try {
    r = await fetch('/api/options');
  } catch (err) {
    throw new Error('Could not reach local server for options refresh. The save may have succeeded; reload the page and check status.');
  }
  const j = await r.json();
  for (const k of ['section','aggsource','data_type','target','inclusion_in_warehouse']) {
    const dl = document.getElementById((k === 'target' ? 'target' : k) + '_opts');
    const vals = (k === 'target') ? (j.targets || []) : (j[k] || []);
    dl.innerHTML = '';
    vals.forEach(val => { const opt = document.createElement('option'); opt.value = val; dl.appendChild(opt); });
  }
}

async function parseBib(){
  try{
    const j = await req('/api/parse_bib', {text: document.getElementById('bib_paste').value});
    const b = j.bib || {};
    document.getElementById('bib_entry_type').value = b.entry_type || '';
    document.getElementById('bib_title').value = b.title || '';
    document.getElementById('bib_author').value = b.author || '';
    document.getElementById('bib_year').value = b.year || '';
    document.getElementById('bib_month').value = b.month || '';
    document.getElementById('bib_journal').value = b.journal || '';
    document.getElementById('bib_booktitle').value = b.booktitle || '';
    document.getElementById('bib_volume').value = b.volume || '';
    document.getElementById('bib_number').value = b.number || '';
    document.getElementById('bib_pages').value = b.pages || '';
    document.getElementById('bib_institution').value = b.institution || '';
    document.getElementById('bib_publisher').value = b.publisher || '';
    document.getElementById('bib_doi').value = b.doi || '';
    document.getElementById('bib_url').value = b.url || '';
    if (!v('link') && b.url) document.getElementById('link').value = b.url;
    document.getElementById('bib_urldate').value = b.urldate || '';
    document.getElementById('bib_keywords').value = b.keywords || '';
    document.getElementById('bib_note').value = b.note || '';
    document.getElementById('bib_abstract').value = b.abstract || '';
    if (!v('source_key') && j.source_key) document.getElementById('source_key').value = j.source_key;
    onEntryTypeChange();
    trySuggestLegend();
    markDirty();
    setStatus({ok:true, message:'BibTeX parsed. Fields were overwritten with parsed values.'});
  }catch(err){ setStatus({ok:false, error:String(err)}); }
}

async function loadTarget(){
  try{
    if (!v('target').trim()) {
      throw new Error('Please select an existing entry in Edit target first.');
    }
    let r;
    try {
      r = await fetch('/api/record?target=' + encodeURIComponent(v('target')));
    } catch (err) {
      throw new Error('Could not reach local server while loading the target entry.');
    }
    const j = await r.json();
    if(!r.ok) throw new Error(j.error || `Load failed (HTTP ${r.status}).`);
    const rec = j.record || {};
    document.getElementById('section').value = rec.section || '';
    document.getElementById('aggsource').value = rec.aggsource || '';
    document.getElementById('legend').value = rec.legend || '';
    document.getElementById('source_key').value = rec.source || rec.citekey || '';
    loadedSourceKey = rec.source || rec.citekey || '';
    document.getElementById('data_type').value = rec.data_type || '';
    document.getElementById('link').value = rec.link || '';
    document.getElementById('ref_link').value = rec.ref_link || '';
    document.getElementById('inclusion_in_warehouse').value = rec.inclusion_in_warehouse || '';
    document.getElementById('multigeo_reference').value = rec.multigeo_reference || '';
    document.getElementById('metadata').value = rec.metadata || '';
    document.getElementById('metadatalink').value = rec.metadatalink || '';
    const b = rec.bib || {};
    document.getElementById('bib_entry_type').value = b.entry_type || '';
    document.getElementById('bib_title').value = b.title || '';
    document.getElementById('bib_author').value = b.author || '';
    document.getElementById('bib_year').value = b.year || '';
    document.getElementById('bib_month').value = b.month || '';
    document.getElementById('bib_journal').value = b.journal || '';
    document.getElementById('bib_booktitle').value = b.booktitle || '';
    document.getElementById('bib_volume').value = b.volume || '';
    document.getElementById('bib_number').value = b.number || '';
    document.getElementById('bib_pages').value = b.pages || '';
    document.getElementById('bib_institution').value = b.institution || '';
    document.getElementById('bib_publisher').value = b.publisher || '';
    document.getElementById('bib_doi').value = b.doi || '';
    document.getElementById('bib_url').value = b.url || '';
    document.getElementById('bib_urldate').value = b.urldate || '';
    document.getElementById('bib_keywords').value = b.keywords || '';
    document.getElementById('bib_note').value = b.note || '';
    document.getElementById('bib_abstract').value = b.abstract || '';
    onEntryTypeChange();
    clearDirty();
    setStatus({ok:true, loaded: rec.id || v('target')});
  }catch(err){
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

async function validateOnly(){
  try{
    if (v('mode') === 'edit' && !v('change_reason').trim()) {
      throw new Error('Change reason is required in edit mode.');
    }
    const out = await req('/api/validate_entry', getPayload());
    setStatusWithChecks(out, 'Validation run complete.');
    if (!out.ok || (out.errors && out.errors.length)) {
      showErrorWindow((out.errors || []).join('\\n') || 'Validation failed.');
    }
  }
  catch(err){
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

async function applyAndBuild(){
  try{
    if (!v('editor_name').trim()) throw new Error('Your name is required before saving.');
    if (v('mode') === 'edit' && !v('change_reason').trim()) throw new Error('Change reason is required in edit mode before saving.');
    const payload = getPayload();
    if (payload.mode === 'edit') {
      const before = (loadedSourceKey || '').trim();
      const after = (v('source_key') || '').trim();
      if (before && after && before !== after) {
        const ok = confirm(
          `You are renaming Source/Citekey from '${before}' to '${after}'.\\n\\n` +
          `This is a compatibility change and will add an alias mapping. Continue?`
        );
        if (!ok) {
          throw new Error('Save cancelled. Key rename was not confirmed.');
        }
        payload.key_rename_confirmed = true;
      }
    }
    const out = await req('/api/apply_and_build', payload);
    setStatusWithChecks(out, 'Save complete.');
    try {
      await loadOptions();
    } catch (optErr) {
      const current = document.getElementById('status').textContent || '';
      setStatus(`${current}\\n\\nWarning: ${String(optErr)}`);
    }
    clearDirty();
  }
  catch(err){
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

async function deleteEntry(){
  try{
    if (v('mode') !== 'edit') throw new Error('Delete is available only in edit mode.');
    if (!v('target').trim()) throw new Error('Select an entry in Edit target before deleting.');
    if (!v('editor_name').trim()) throw new Error('Your name is required before deleting.');
    const msg = `Delete entry '${v('target')}'?\\n\\nThis action cannot be undone.`;
    if (!confirm(msg)) return;

    const out = await req('/api/delete_entry', {
      target: v('target'),
      editor_name: v('editor_name'),
      change_reason: v('change_reason') || 'Deleted via local UI'
    });
    setStatusWithChecks(out, 'Delete complete.');
    await loadOptions();
    clearDirty();
  }catch(err){
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

loadOptions();
onModeChange();
onEntryTypeChange();
document.getElementById('legend').addEventListener('input', () => { document.getElementById('legend').dataset.userEdited = '1'; });
document.querySelectorAll('input, textarea, select').forEach(el => {
  el.addEventListener('input', markDirty);
  el.addEventListener('change', markDirty);
});
window.addEventListener('beforeunload', (e) => {
  if (!dirty) return;
  e.preventDefault();
  e.returnValue = '';
});
window.addEventListener('unload', () => {
  if (navigator.sendBeacon) {
    navigator.sendBeacon('/api/shutdown', new Blob([JSON.stringify({})], {type: 'application/json'}));
  }
});
setInterval(() => { fetch('/api/ping').catch(() => {}); }, 2000);
</script>
</body></html>"""


class App:
    def __init__(self, registry: Path, aliases: Path, changelog: Path):
        self.registry_path = registry
        self.aliases_path = aliases
        self.changelog_path = changelog
        self.last_ping = time.time()
        self.idle_timeout_seconds = 300

    @property
    def registry(self):
        return load_registry(self.registry_path)

    def save(self, reg: dict):
        save_registry(self.registry_path, reg)

    def artifact_paths(self, reg: dict) -> List[Path]:
        cfg = reg.get("config", {}) or {}
        dictionary_output = Path(cfg.get("dictionary_output", "handmade_tables/dictionary.xlsx"))
        bib_output = Path(cfg.get("bib_output", "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"))
        return [self.registry_path, self.changelog_path, self.aliases_path, dictionary_output, bib_output]


def file_mtimes(paths: List[Path]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in paths:
        try:
            out[str(p)] = p.stat().st_mtime_ns
        except FileNotFoundError:
            out[str(p)] = -1
    return out


def modified_paths(before: Dict[str, int], after: Dict[str, int]) -> List[str]:
    return sorted([p for p in after.keys() if before.get(p, -1) != after.get(p, -1)])


class Handler(BaseHTTPRequestHandler):
    app: App = None

    def _send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        self.app.last_ping = time.time()
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML)
            return

        if parsed.path == "/api/options":
            reg = self.app.registry
            self._send_json(suggested_options(reg.get("records", [])))
            return

        if parsed.path == "/api/ping":
            self.app.last_ping = time.time()
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/record":
            reg = self.app.registry
            qs = parse_qs(parsed.query)
            target = (qs.get("target") or [""])[0]
            hits = find_target(reg.get("records", []), target)
            if len(hits) != 1:
                self._send_json({"error": f"target must match exactly one record; got {len(hits)}"}, 400)
                return
            self._send_json({"record": hits[0]})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        self.app.last_ping = time.time()
        try:
            if self.path == "/api/parse_bib":
                data = self._read_json()
                out = parse_bib_paste(data.get("text", ""))
                self._send_json(out)
                return

            if self.path == "/api/validate_entry":
                data = self._read_json()
                reg = self.app.registry
                mode = normalize_whitespace(data.get("mode", "add")).lower()
                target = normalize_whitespace(data.get("target", ""))
                reason = normalize_whitespace(data.get("change_reason", ""))
                records = reg.get("records", [])

                candidate = make_candidate(data)
                target_id = ""
                target_check = {"name": "Edit target resolution", "passed": True, "detail": "Not applicable for add mode"}
                if mode == "edit":
                    hits = find_target(records, target)
                    if len(hits) != 1:
                        self._send_json({
                            "ok": False,
                            "errors": [f"Edit target must match exactly one record; got {len(hits)}"],
                            "warnings": [],
                            "checks": [{"name": "Edit target resolution", "passed": False, "detail": f"Matches found: {len(hits)}"}],
                        })
                        return
                    if not reason:
                        self._send_json({
                            "ok": False,
                            "errors": ["change_reason is required for edit mode"],
                            "warnings": [],
                            "checks": [{"name": "Change reason", "passed": False, "detail": "Missing change_reason"}],
                        })
                        return
                    target_id = hits[0].get("id", "")
                    target_check = {"name": "Edit target resolution", "passed": True, "detail": f"Resolved to {target_id}"}
                out = validate_candidate(records, candidate, mode, target_id)
                out.setdefault("checks", []).insert(0, target_check)
                if mode == "edit":
                    out.setdefault("checks", []).insert(1, {"name": "Change reason", "passed": True, "detail": "Change reason provided"})
                artifact_out = validate_candidate_against_artifacts(reg, candidate, mode, target)
                out["errors"] = sorted(set(out.get("errors", []) + artifact_out.get("errors", [])))
                out["warnings"] = sorted(set(out.get("warnings", []) + artifact_out.get("warnings", [])))
                out.setdefault("checks", []).extend(artifact_out.get("checks", []))
                self._send_json({"ok": len(out["errors"]) == 0, **out})
                return

            if self.path == "/api/apply_and_build":
                data = self._read_json()
                reg = self.app.registry
                tracked_paths = self.app.artifact_paths(reg)
                before = file_mtimes(tracked_paths)
                candidate = make_candidate(data)
                mode = normalize_whitespace(data.get("mode", "add")).lower()
                target = normalize_whitespace(data.get("target", ""))
                artifact_out = validate_candidate_against_artifacts(reg, candidate, mode, target)
                if artifact_out.get("errors"):
                    raise ValueError("\n".join(artifact_out["errors"]))
                out = apply_payload(reg, data, self.app.aliases_path, self.app.changelog_path)
                self.app.save(reg)

                from build_sources_artifacts import main as build_main  # pylint: disable=import-outside-toplevel
                import sys  # pylint: disable=import-outside-toplevel

                argv_orig = sys.argv[:]
                try:
                    sys.argv = ["build_sources_artifacts.py", "--registry", str(self.app.registry_path)]
                    build_main()
                finally:
                    sys.argv = argv_orig

                after = file_mtimes(tracked_paths)
                self._send_json({
                    "ok": True,
                    **out,
                    "checks": (out.get("checks", []) + artifact_out.get("checks", [])),
                    "modified_files": modified_paths(before, after),
                    "file_change_summary": build_file_change_summary(
                        modified_paths(before, after),
                        out.get("operation", mode),
                        out.get("record_id", ""),
                        out.get("changed_fields", []),
                        out.get("key_renamed", False),
                    ),
                    "message": "Record saved and artifacts regenerated",
                })
                return

            if self.path == "/api/delete_entry":
                data = self._read_json()
                target = normalize_whitespace(data.get("target", ""))
                editor = normalize_whitespace(data.get("editor_name", ""))
                reason = normalize_whitespace(data.get("change_reason", "")) or "Deleted via local UI"
                if not target:
                    raise ValueError("target is required")
                if not editor:
                    raise ValueError("editor_name is required")

                reg = self.app.registry
                records = reg.get("records", [])
                tracked_paths = self.app.artifact_paths(reg)
                before = file_mtimes(tracked_paths)
                hits = find_target(records, target)
                if len(hits) != 1:
                    raise ValueError(f"Delete target must match exactly one record; got {len(hits)}")
                rec = hits[0]
                rec_id = rec.get("id", "")
                deleted_fields = summarize_record_all_fields(rec)
                records.remove(rec)
                append_change(self.app.changelog_path, "delete", rec_id, reason, editor)
                self.app.save(reg)

                from build_sources_artifacts import main as build_main  # pylint: disable=import-outside-toplevel
                import sys  # pylint: disable=import-outside-toplevel

                argv_orig = sys.argv[:]
                try:
                    sys.argv = ["build_sources_artifacts.py", "--registry", str(self.app.registry_path)]
                    build_main()
                finally:
                    sys.argv = argv_orig

                after = file_mtimes(tracked_paths)
                self._send_json({
                    "ok": True,
                    "record_id": rec_id,
                    "operation": "delete",
                    "changed_fields": deleted_fields,
                    "checks": [{"name": "Delete target resolution", "passed": True, "detail": f"Deleted {rec_id}"}],
                    "modified_files": modified_paths(before, after),
                    "file_change_summary": build_file_change_summary(
                        modified_paths(before, after),
                        "delete",
                        rec_id,
                        deleted_fields,
                        False,
                    ),
                    "message": "Entry deleted and artifacts regenerated",
                })
                return

            if self.path == "/api/shutdown":
                self._send_json({"ok": True, "message": "Shutting down"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            self._send_json({"error": "Not found"}, 404)
        except Exception as exc:  # pylint: disable=broad-except
            self._send_json({"error": str(exc)}, 400)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--registry", default="metadata/sources/sources.yaml")
    parser.add_argument("--aliases", default="metadata/sources/aliases.yaml")
    parser.add_argument("--change-log", default="metadata/sources/change_log.yaml")
    args = parser.parse_args()

    app = App(Path(args.registry), Path(args.aliases), Path(args.change_log))
    Handler.app = app

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)

    def idle_guard():
        while True:
            time.sleep(1)
            if time.time() - app.last_ping > app.idle_timeout_seconds:
                try:
                    httpd.shutdown()
                except Exception:
                    pass
                return

    threading.Thread(target=idle_guard, daemon=True).start()

    print(f"Source Registry UI running at http://{args.host}:{args.port}")
    print("The server stops automatically when the UI window/tab closes.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

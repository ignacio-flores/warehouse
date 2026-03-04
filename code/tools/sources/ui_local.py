#!/usr/bin/env python3
"""Local web UI for adding/editing source records with guardrails.

Run:
  python3 code/tools/sources/ui_local.py
Then open http://127.0.0.1:8765
"""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import threading
import time
from difflib import unified_diff
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from common import load_json_yaml, load_registry, normalize_text, normalize_url, normalize_whitespace, parse_bib_entries, read_sources_sheet, save_registry

URL_RE = re.compile(r"^https?://", re.IGNORECASE)
DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
YEAR_RE = re.compile(r"^\d{4}$")
ONLINE_COMPARE_MAX_DIFF_LINES = 400
ONLINE_COMPARE_MAX_DIFF_CHARS = 60000
CANONICAL_BIB_PATH = "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"
REPO_ROOT = Path(__file__).resolve().parents[3]


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
    bib_path = Path(cfg.get("bib_output", CANONICAL_BIB_PATH))
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
#status { white-space: pre-wrap; line-height: 1.35; }
#status .status-ok { color: #8fd28f; font-weight: 600; }
#status .status-fail { color: #ff9da4; font-weight: 600; }
#status .status-warn { color: #f0c674; font-weight: 600; }
#status .git-file { color: #8ab4f8; }
#status .git-hunk { color: #c792ea; }
#status .git-add { color: #8fd28f; }
#status .git-del { color: #ff9da4; }
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
  <div class='row'>
    <div class='step'>Step 3: Compare with online reference (optional)</div>
    <button class='secondary' onclick='compareOnlineBib()'>Compare local .bib with online reference</button>
    <small style='display:block; margin-top:6px;'>Compares local .bib against the configured online reference URL.</small>
  </div>
  <div class='row'>
    <div class='step'>Step 4: Publish local .bib with one click</div>
    <div style='display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;'>
      <button class='secondary' onclick='runGitSetupCheck()'>Run setup check</button>
      <button class='secondary' onclick='connectGitHubAccount()'>Connect GitHub</button>
      <button class='secondary' onclick='configureGcmStore()'>Configure GCM store</button>
      <button class='secondary' onclick='switchGitHubAccount()'>Switch GitHub account</button>
      <button class='secondary' onclick='cleanupPendingPublish()'>Clean pending publish commit</button>
      <button class='warn' id='publishBtn' onclick='publishOnlineBib()' disabled title='Run setup check first.'>Publish local .bib to GitHub</button>
    </div>
    <small style='display:block; margin-top:6px;'>If setup is missing, the Status panel will show exact terminal commands to run. If publish gets stuck with "ahead", use "Clean pending publish commit". On Linux, if GCM complains about credential store, click "Configure GCM store".</small>
    <small id='gitSetupLine' style='display:block; margin-top:6px;'></small>
    <small id='publishResultLine' style='display:block; margin-top:6px;'></small>
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
function escapeHtml(s){
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function statusClassForLine(line){
  if (line.startsWith('Overall result: PASSED') || line.startsWith('- Status: up_to_date') || line.startsWith('- Status: published') || line.startsWith('- Status: cleaned')) return 'status-ok';
  if (line.startsWith('Overall result: FAILED') || line.startsWith('- Status: unavailable') || line.startsWith('- Status: not_configured')) return 'status-fail';
  if (line.startsWith('- Status: different')) return 'status-warn';
  return '';
}
function diffClassForLine(line){
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) return 'git-file';
  if (line.startsWith('@@')) return 'git-hunk';
  if (line.startsWith('+') && !line.startsWith('+++')) return 'git-add';
  if (line.startsWith('-') && !line.startsWith('---')) return 'git-del';
  return '';
}
function setStatusColored(text){
  const el = document.getElementById('status');
  const lines = String(text || '').split('\\n');
  let inUnifiedDiff = false;
  el.innerHTML = lines.map((line) => {
    if (line.startsWith('Unified diff (online -> local):')) {
      inUnifiedDiff = true;
    }
    const cls = inUnifiedDiff ? diffClassForLine(line) : statusClassForLine(line);
    const body = escapeHtml(line);
    return cls ? `<span class=\"${cls}\">${body}</span>` : body;
  }).join('\\n');
}
function showErrorWindow(msg){
  alert(`There are validation errors:\\n\\n${msg}`);
}
let dirty = false;
let loadedSourceKey = '';
let gitSetupState = null;
let publishInFlight = false;
let authInFlight = false;
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

function formatOnlineCompare(onlineCompare){
  if (!onlineCompare) return '';
  const lines = ['Online .bib comparison:'];
  lines.push(`- Status: ${onlineCompare.status || 'unknown'}`);
  if (onlineCompare.message) lines.push(`- Message: ${onlineCompare.message}`);
  if (onlineCompare.reference_url) lines.push(`- Online reference: ${onlineCompare.reference_url}`);
  if (onlineCompare.local_bib_path) lines.push(`- Local bib: ${onlineCompare.local_bib_path}`);
  if (onlineCompare.diff_preview) lines.push(`Unified diff (online -> local):\\n${onlineCompare.diff_preview}`);
  return lines.join('\\n');
}

function formatGitPublish(gitPublish){
  if (!gitPublish) return '';
  const lines = ['Git publish:'];
  lines.push(`- Status: ${gitPublish.status || 'unknown'}`);
  if (gitPublish.message) lines.push(`- Message: ${gitPublish.message}`);
  if (gitPublish.target_repo) lines.push(`- Repo: ${gitPublish.target_repo}`);
  if (gitPublish.target_branch) lines.push(`- Branch: ${gitPublish.target_branch}`);
  if (gitPublish.target_path) lines.push(`- Path: ${gitPublish.target_path}`);
  if (gitPublish.commit_sha) lines.push(`- Commit SHA: ${gitPublish.commit_sha}`);
  if (gitPublish.commit_url) lines.push(`- Commit URL: ${gitPublish.commit_url}`);
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
  const onlineText = formatOnlineCompare(out.online_compare);
  if (onlineText) lines.push(onlineText);
  const gitText = formatGitPublish(out.git_publish);
  if (gitText) lines.push(gitText);
  setStatusColored(lines.join('\\n\\n'));
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

async function compareOnlineBib(){
  try{
    const out = await req('/api/compare_online_bib', {});
    setStatusWithChecks(out, 'Online comparison complete.');
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

function renderGitSetupState(state){
  gitSetupState = state;
  const publishBtn = document.getElementById('publishBtn');
  const line = document.getElementById('gitSetupLine');
  if (!state) {
    publishBtn.disabled = true;
    publishBtn.title = 'Run setup check first.';
    line.textContent = 'Setup status unavailable. Click "Run setup check".';
    return;
  }
  const issues = state.issues || [];
  const warnings = state.warnings || [];
  const target = state.target_repo ? `${state.target_repo}/${state.target_path} @ ${state.target_branch}` : '(target unavailable)';
  if (state.ready) {
    publishBtn.disabled = false;
    publishBtn.title = 'Publish local .bib to GitHub';
    line.textContent = warnings.length
      ? `Setup ready with warning: ${warnings[0]} Target: ${target}`
      : `Setup ready. Target: ${target}`;
    return;
  }
  publishBtn.disabled = true;
  publishBtn.title = 'Fix setup issues before publishing.';
  line.textContent = `Setup required: ${issues[0] || 'Run setup check'}. Target: ${target}`;
}

async function runGitSetupCheck(showStatus = true){
  try{
    if (showStatus) {
      const line = document.getElementById('publishResultLine');
      line.textContent = 'Running setup check (including push-auth precheck)...';
    }
    const url = showStatus ? '/api/git_publish_status?check_push=1' : '/api/git_publish_status';
    const r = await fetch(url);
    const j = await r.json();
    renderGitSetupState(j);
    if (showStatus) {
      const lines = ['Git setup check'];
      lines.push(j.ready ? 'Overall result: PASSED' : 'Overall result: FAILED');
      if (j.git_version) lines.push(`- Git: ${j.git_version}`);
      if (j.user_name || j.user_email) lines.push(`- Git identity: ${j.user_name || '(missing name)'} <${j.user_email || 'missing-email'}>`);
      lines.push(`- Credential helper: ${j.credential_helper || '(not configured)'}`);
      if (j.target_repo) lines.push(`- Target: ${j.target_repo}/${j.target_path} @ ${j.target_branch}`);
      if ((j.ahead_count || 0) || (j.behind_count || 0)) lines.push(`- Branch divergence: ahead ${j.ahead_count || 0}, behind ${j.behind_count || 0}`);
      if (j.ahead_changed_paths && j.ahead_changed_paths.length) lines.push(`- Unpushed files: ${j.ahead_changed_paths.join(', ')}`);
      if (j.ahead_only_canonical_bib) lines.push('- Recovery: you can publish now, or click "Clean pending publish commit" to uncommit and retry.');
      if (j.gcm_detected) lines.push(`- GCM credential store: ${j.gcm_credential_store || '(not configured)'}`);
      if (j.push_check_requested) {
        lines.push(`- Push auth check: ${j.push_check_status || 'unknown'}`);
        if (j.push_check_message) lines.push(`- Push auth detail: ${j.push_check_message}`);
      }
      if (j.warnings && j.warnings.length) lines.push(`Warnings:\\n- ${j.warnings.join('\\n- ')}`);
      if (j.issues && j.issues.length) lines.push(`Issues:\\n- ${j.issues.join('\\n- ')}`);
      if (!j.ready) {
        lines.push(
          'How to fix setup:',
          '1) Open a terminal (PowerShell on Windows, Terminal on macOS/Linux).',
          '2) Configure Git identity once (type exactly):',
          '   git config --global user.name "Your Name"',
          '   git config --global user.email "you@example.com"',
          '3) Install Git Credential Manager (GCM):',
          '   - Windows: install/update Git for Windows (includes GCM): https://git-scm.com/download/win',
          '   - macOS (Homebrew): brew install --cask git-credential-manager',
          '   - Linux: docs: https://github.com/git-ecosystem/git-credential-manager/blob/release/docs/install.md',
          '            quick script: https://aka.ms/gcm/linux-install-source.sh',
          '4) Configure GCM (type exactly):',
          '   git-credential-manager configure',
          '   git config --global credential.helper manager-core',
          '5) Verify setup (type exactly):',
          '   git config --global --get user.name',
          '   git config --global --get user.email',
          '   git config --global --get credential.helper',
          '6) In this UI, click "Connect GitHub" and complete browser sign-in.',
          '7) If Linux shows "No credential store has been selected", click "Configure GCM store" in this UI.',
          '8) Click "Run setup check" again in this UI.',
          '9) Shared computer: click "Switch GitHub account" before the next collaborator publishes.'
        );
      }
      setStatusColored(lines.join('\\n'));
      const line = document.getElementById('publishResultLine');
      const stamp = new Date().toLocaleTimeString();
      line.textContent = `[${stamp}] Setup check complete: ${j.ready ? 'ready' : 'action required'}.`;
    }
  } catch (err) {
    renderGitSetupState(null);
    if (showStatus) {
      setStatus({ok:false, error:String(err)});
      showErrorWindow(String(err));
      const line = document.getElementById('publishResultLine');
      const stamp = new Date().toLocaleTimeString();
      line.textContent = `[${stamp}] Setup check failed: ${String(err)}`;
    }
  }
}

async function switchGitHubAccount(){
  try{
    const out = await req('/api/git_switch_account', {});
    setStatusWithChecks(out, 'GitHub account switch complete.');
    await runGitSetupCheck(false);
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
  }
}

async function connectGitHubAccount(){
  try{
    if (authInFlight || publishInFlight) return;
    authInFlight = true;
    const line = document.getElementById('publishResultLine');
    line.textContent = 'Connecting GitHub account. Complete the browser sign-in flow if prompted.';
    const out = await req('/api/git_connect_account', {});
    setStatusWithChecks(out, 'GitHub connection complete.');
    const gp = out.git_publish || {};
    const stamp = new Date().toLocaleTimeString();
    line.textContent = `[${stamp}] ${gp.message || 'GitHub connection flow finished.'}`;
    await runGitSetupCheck(true);
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
    const line = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    line.textContent = `[${stamp}] Connect GitHub failed: ${String(err)}`;
    await runGitSetupCheck(false);
  } finally {
    authInFlight = false;
  }
}

async function configureGcmStore(){
  try{
    if (authInFlight || publishInFlight) return;
    const out = await req('/api/git_configure_credential_store', {});
    setStatusWithChecks(out, 'GCM credential store configured.');
    const gp = out.git_publish || {};
    const line = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    line.textContent = `[${stamp}] ${gp.message || 'GCM credential store configured.'}`;
    await runGitSetupCheck(true);
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
    const line = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    line.textContent = `[${stamp}] Configure GCM store failed: ${String(err)}`;
    await runGitSetupCheck(false);
  }
}

async function cleanupPendingPublish(){
  try{
    if (publishInFlight) return;
    const ok = confirm(
      'This will uncommit pending local publish commit(s) if they only touch the canonical .bib file.\\n' +
      'Your .bib file changes will be kept locally. Continue?'
    );
    if (!ok) return;
    const out = await req('/api/git_cleanup_pending_publish', {});
    setStatusWithChecks(out, 'Pending publish cleanup complete.');
    const gp = out.git_publish || {};
    const line = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    if (gp.status === 'cleaned') {
      line.textContent = `[${stamp}] Cleanup complete: pending .bib commit was removed and local .bib changes were kept.`;
    } else if (gp.status === 'nothing_to_clean') {
      line.textContent = `[${stamp}] Nothing to clean: no unpushed commits found.`;
    } else {
      line.textContent = `[${stamp}] Cleanup finished with status: ${gp.status || 'unknown'}.`;
    }
    await runGitSetupCheck(false);
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
    const line = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    line.textContent = `[${stamp}] Cleanup failed: ${String(err)}`;
    await runGitSetupCheck(false);
  }
}

async function publishOnlineBib(){
  try{
    if (publishInFlight) return;
    if (!v('editor_name').trim()) throw new Error('Your name is required before publishing.');
    if (!(gitSetupState && gitSetupState.ready)) {
      throw new Error('Setup is not ready. Click "Run setup check" and resolve issues first.');
    }
    publishInFlight = true;
    const publishBtn = document.getElementById('publishBtn');
    const publishLine = document.getElementById('publishResultLine');
    publishBtn.disabled = true;
    publishBtn.textContent = 'Publishing...';
    publishLine.textContent = 'Publishing local .bib to GitHub. If prompted, finish browser sign-in.';
    const out = await req('/api/publish_online_bib', {
      editor_name: v('editor_name'),
      change_reason: v('change_reason')
    });
    setStatusWithChecks(out, 'Git publish complete.');
    const gp = out.git_publish || {};
    const stamp = new Date().toLocaleTimeString();
    if (gp.status === 'published') {
      const shortSha = gp.commit_sha ? gp.commit_sha.slice(0, 7) : '(no sha)';
      publishLine.textContent = `[${stamp}] Publish succeeded: commit ${shortSha}.`;
    } else if (gp.status === 'up_to_date') {
      publishLine.textContent = `[${stamp}] No publish needed: local .bib already matches GitHub.`;
    } else {
      publishLine.textContent = `[${stamp}] Publish finished with status: ${gp.status || 'unknown'}.`;
    }
    await runGitSetupCheck(false);
  } catch (err) {
    setStatus({ok:false, error:String(err)});
    showErrorWindow(String(err));
    const publishLine = document.getElementById('publishResultLine');
    const stamp = new Date().toLocaleTimeString();
    publishLine.textContent = `[${stamp}] Publish failed: ${String(err)}`;
    await runGitSetupCheck(false);
  } finally {
    publishInFlight = false;
    const publishBtn = document.getElementById('publishBtn');
    publishBtn.textContent = 'Publish local .bib to GitHub';
    if (gitSetupState && gitSetupState.ready) {
      publishBtn.disabled = false;
    }
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
runGitSetupCheck(false);
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
        bib_output = Path(cfg.get("bib_output", CANONICAL_BIB_PATH))
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


def _coerce_timeout_seconds(value, default: int = 20) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(120, timeout))


def _trim_diff_preview(lines: List[str], max_lines: int = ONLINE_COMPARE_MAX_DIFF_LINES, max_chars: int = ONLINE_COMPARE_MAX_DIFF_CHARS) -> Dict[str, object]:
    shown: List[str] = []
    total_chars = 0
    truncated = False
    for idx, line in enumerate(lines):
        if idx >= max_lines:
            truncated = True
            break
        next_chars = total_chars + len(line) + 1
        if next_chars > max_chars:
            truncated = True
            break
        shown.append(line)
        total_chars = next_chars
    if truncated:
        shown.append(f"... [diff truncated: showing {len(shown)} of {len(lines)} lines]")
    return {"preview": "\n".join(shown), "truncated": truncated}


def _normalize_github_repo_url(url: str) -> str:
    raw = normalize_whitespace(url)
    if not raw:
        return ""
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw[len("git@github.com:") :]
    if raw.startswith("ssh://git@github.com/"):
        raw = "https://github.com/" + raw[len("ssh://git@github.com/") :]
    raw = raw.rstrip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    return raw.lower()


def _parse_github_raw_reference(reference_url: str) -> Dict[str, str]:
    parsed = urlparse(reference_url)
    if parsed.netloc.lower() != "raw.githubusercontent.com":
        return {}
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4:
        return {}
    owner = parts[0]
    repo = parts[1]
    branch = parts[2]
    path = unquote("/".join(parts[3:]))
    if not owner or not repo or not branch or not path:
        return {}
    return {"owner": owner, "repo": repo, "branch": branch, "path": path}


def _run_git(args: List[str], timeout_seconds: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        cwd=str(REPO_ROOT),
    )


def _remote_candidates(owner: str, repo: str, timeout_seconds: int) -> List[str]:
    target = f"https://github.com/{owner}/{repo}".lower()
    names: List[str] = []
    try:
        proc = _run_git(["remote", "-v"], timeout_seconds)
    except Exception:
        return []
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "(fetch)":
            continue
        name, remote_url = parts[0], parts[1]
        if _normalize_github_repo_url(remote_url) == target:
            names.append(name)
    # Prefer upstream first for canonical repo, then origin.
    ordered = sorted(set(names), key=lambda x: (0 if x == "upstream" else 1 if x == "origin" else 2, x))
    return ordered


def _fetch_github_raw_via_git(reference_url: str, timeout_seconds: int) -> Dict[str, str]:
    ref = _parse_github_raw_reference(reference_url)
    if not ref:
        raise RuntimeError("URL is not a supported raw.githubusercontent.com path.")
    owner = ref["owner"]
    repo = ref["repo"]
    branch = ref["branch"]
    path = ref["path"]

    remotes = _remote_candidates(owner, repo, timeout_seconds)
    if not remotes:
        raise RuntimeError(f"No git remote matched github.com/{owner}/{repo}.")

    errors: List[str] = []
    for remote in remotes:
        spec = f"{remote}/{branch}:{path}"
        try:
            proc = _run_git(["show", spec], timeout_seconds)
            return {"text": proc.stdout.decode("utf-8", errors="replace"), "method": f"git-remote:{remote}/{branch}"}
        except Exception as show_exc:  # pylint: disable=broad-except
            errors.append(f"{remote} show failed: {show_exc}")

        # Try to refresh the remote branch, then retry show.
        try:
            _run_git(["fetch", "--quiet", remote, branch], timeout_seconds)
            proc = _run_git(["show", spec], timeout_seconds)
            return {"text": proc.stdout.decode("utf-8", errors="replace"), "method": f"git-fetch:{remote}/{branch}"}
        except Exception as fetch_exc:  # pylint: disable=broad-except
            errors.append(f"{remote} fetch/show failed: {fetch_exc}")

    raise RuntimeError("; ".join(errors) if errors else "git fallback failed")


def _fetch_url_text(reference_url: str, timeout_seconds: int) -> Dict[str, str]:
    req = Request(reference_url, headers={"User-Agent": "SourceRegistryUI/1.0"})
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310 (URL is user-configured reference)
            remote_raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
        return {"text": remote_raw.decode(charset, errors="replace"), "method": "python-urllib"}
    except Exception as exc:  # pylint: disable=broad-except
        msg = str(exc)
        curl_error = "curl not available"
        curl_bin = shutil.which("curl")
        if curl_bin:
            try:
                proc = subprocess.run(
                    [
                        curl_bin,
                        "--fail",
                        "--location",
                        "--silent",
                        "--show-error",
                        "--max-time",
                        str(timeout_seconds),
                        reference_url,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return {"text": proc.stdout.decode("utf-8", errors="replace"), "method": "curl-system-trust"}
            except Exception as curl_exc:  # pylint: disable=broad-except
                curl_error = str(curl_exc)

        try:
            git_fetched = _fetch_github_raw_via_git(reference_url, timeout_seconds)
            return git_fetched
        except Exception as git_exc:  # pylint: disable=broad-except
            raise RuntimeError(
                f"{msg}. curl fallback failed: {curl_error}. git fallback also failed: {git_exc}"
            ) from git_exc


def compare_local_bib_with_online(registry: dict) -> dict:
    cfg = registry.get("config", {}) or {}
    bib_path = Path(cfg.get("bib_output", CANONICAL_BIB_PATH))
    reference_url = normalize_whitespace(str(cfg.get("online_bib_reference_url", "")))
    timeout_seconds = _coerce_timeout_seconds(cfg.get("online_bib_timeout_seconds", 20), default=20)
    base = {
        "reference_url": reference_url,
        "local_bib_path": str(bib_path),
        "timeout_seconds": timeout_seconds,
        "local_hash": "",
        "remote_hash": "",
        "diff_preview": "",
        "diff_line_count": 0,
        "truncated": False,
        "fetch_method": "",
        "compared_at": now_utc(),
    }

    if not reference_url:
        return {
            **base,
            "ok": False,
            "status": "not_configured",
            "message": "online_bib_reference_url is not configured in metadata/sources/sources.yaml.",
        }
    if not bib_path.exists():
        return {
            **base,
            "ok": False,
            "status": "unavailable",
            "message": f"Local bib artifact is missing: {bib_path}",
        }

    try:
        local_text = bib_path.read_text(encoding="utf-8")
    except Exception as exc:  # pylint: disable=broad-except
        return {
            **base,
            "ok": False,
            "status": "unavailable",
            "message": f"Could not read local bib artifact: {exc}",
        }

    local_hash = hashlib.sha256(local_text.encode("utf-8")).hexdigest()
    base["local_hash"] = local_hash

    try:
        fetched = _fetch_url_text(reference_url, timeout_seconds)
        remote_text = fetched.get("text", "")
        base["fetch_method"] = fetched.get("method", "")
    except Exception as exc:  # pylint: disable=broad-except
        return {
            **base,
            "ok": False,
            "status": "unavailable",
            "message": f"Could not fetch online reference: {exc}",
        }

    remote_hash = hashlib.sha256(remote_text.encode("utf-8")).hexdigest()
    base["remote_hash"] = remote_hash

    if local_text == remote_text:
        return {
            **base,
            "ok": True,
            "status": "up_to_date",
            "message": "Local .bib matches the online reference.",
        }

    diff_lines = list(
        unified_diff(
            remote_text.splitlines(),
            local_text.splitlines(),
            fromfile=f"online:{reference_url}",
            tofile=f"local:{bib_path}",
            lineterm="",
        )
    )
    trimmed = _trim_diff_preview(diff_lines)
    preview = str(trimmed.get("preview", ""))
    truncated = bool(trimmed.get("truncated", False))
    message = "Local .bib differs from the online reference."
    if truncated:
        message = message + " Diff preview was truncated."

    return {
        **base,
        "ok": True,
        "status": "different",
        "message": message,
        "diff_preview": preview,
        "diff_line_count": len(diff_lines),
        "truncated": truncated,
    }


def _git_publish_target(registry: dict) -> Dict[str, str]:
    cfg = registry.get("config", {}) or {}
    reference_url = normalize_whitespace(str(cfg.get("online_bib_reference_url", "")))
    if not reference_url:
        return {"ok": False, "message": "online_bib_reference_url is not configured in metadata/sources/sources.yaml."}
    ref = _parse_github_raw_reference(reference_url)
    if not ref:
        return {"ok": False, "message": "online_bib_reference_url must be a raw.githubusercontent.com URL."}
    if ref.get("path") != CANONICAL_BIB_PATH:
        return {
            "ok": False,
            "message": (
                "online_bib_reference_url must target "
                f"{CANONICAL_BIB_PATH} (got {ref.get('path', '')})."
            ),
        }
    return {
        "ok": True,
        "owner": ref["owner"],
        "repo": ref["repo"],
        "branch": ref["branch"],
        "path": ref["path"],
        "reference_url": reference_url,
    }


def _run_git_any(args: List[str], timeout_seconds: int = 20, stdin_text: str = "") -> subprocess.CompletedProcess:
    cmd = ["git"] + args
    return _run_cmd_any(cmd, timeout_seconds=timeout_seconds, stdin_text=stdin_text)


def _run_cmd_any(cmd: List[str], timeout_seconds: int = 20, stdin_text: str = "") -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            cwd=str(REPO_ROOT),
            input=stdin_text.encode("utf-8") if stdin_text else None,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=b"",
            stderr=f"git {' '.join(args)} timed out after {timeout_seconds}s".encode("utf-8", errors="replace"),
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout=b"",
            stderr=str(exc).encode("utf-8", errors="replace"),
        )


def _git_out(args: List[str], timeout_seconds: int = 20) -> str:
    proc = _run_git_any(args, timeout_seconds)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"git {' '.join(args)} failed")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _git_try_out(args: List[str], timeout_seconds: int = 20) -> str:
    proc = _run_git_any(args, timeout_seconds)
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _remote_for_target(owner: str, repo: str, timeout_seconds: int = 20) -> str:
    candidates = _remote_candidates(owner, repo, timeout_seconds)
    return candidates[0] if candidates else ""


def _int_out(value: str, default: int = 0) -> int:
    try:
        return int((value or "").strip())
    except Exception:  # pylint: disable=broad-except
        return default


def _repo_relative_posix(path: Path) -> str:
    if path.is_absolute():
        try:
            return path.relative_to(REPO_ROOT).as_posix()
        except Exception:  # pylint: disable=broad-except
            return path.as_posix()
    return path.as_posix()


def _ahead_changed_paths(remote: str, branch: str) -> List[str]:
    revs_raw = _git_try_out(["rev-list", f"{remote}/{branch}..HEAD"], timeout_seconds=20)
    revs = [x.strip() for x in revs_raw.splitlines() if x.strip()]
    if not revs:
        return []
    paths = set()
    for sha in revs:
        changed = _git_try_out(["show", "--pretty=format:", "--name-only", sha], timeout_seconds=30)
        for line in changed.splitlines():
            p = line.strip()
            if p:
                paths.add(p)
    return sorted(paths)


def _push_head(remote: str, branch: str, timeout_seconds: int = 180) -> None:
    push_proc = _run_git_any(["push", remote, f"HEAD:{branch}"], timeout_seconds=timeout_seconds)
    if push_proc.returncode == 0:
        return
    err = push_proc.stderr.decode("utf-8", errors="replace").strip()
    low = err.lower()
    if push_proc.returncode == 124 or "timed out" in low:
        raise RuntimeError(
            "Push timed out while waiting for GitHub auth. Complete browser sign-in and retry. "
            "If it keeps happening, click 'Switch GitHub account' and retry publish."
        )
    if "permission" in low or "403" in low or "denied" in low:
        raise PermissionError("GitHub rejected push: this account does not have collaborator publish access.")
    if "non-fast-forward" in low or "fetch first" in low:
        raise RuntimeError("Push rejected because branch is not up to date. Please sync local repository and retry.")
    raise RuntimeError(err or "git push failed")


def git_publish_status(registry: dict, check_push_auth: bool = False) -> dict:
    cfg = registry.get("config", {}) or {}
    bib_path = Path(cfg.get("bib_output", CANONICAL_BIB_PATH))
    target = _git_publish_target(registry)
    issues: List[str] = []
    warnings: List[str] = []
    info = {
        "git_available": False,
        "git_version": "",
        "user_name": "",
        "user_email": "",
        "credential_helper": "",
        "credential_helper_configured": False,
        "gcm_detected": False,
        "gcm_credential_store": "",
        "gcm_store_required": False,
        "target_configured": bool(target.get("ok")),
        "target_repo": "",
        "target_branch": "",
        "target_path": "",
        "target_reference_url": target.get("reference_url", ""),
        "target_message": target.get("message", ""),
        "target_remote": "",
        "local_bib_path": str(bib_path),
        "local_bib_exists": bib_path.exists(),
        "ahead_count": 0,
        "behind_count": 0,
        "ahead_only_canonical_bib": False,
        "ahead_changed_paths": [],
        "push_check_requested": bool(check_push_auth),
        "push_check_status": "skipped",
        "push_check_message": "",
    }

    if target.get("ok"):
        info["target_repo"] = f"{target['owner']}/{target['repo']}"
        info["target_branch"] = target["branch"]
        info["target_path"] = target["path"]
    else:
        issues.append(str(target.get("message", "Invalid publish target configuration.")))

    git_check = _run_git_any(["--version"])
    if git_check.returncode != 0:
        issues.append("Git is not installed or not available in PATH.")
    else:
        info["git_available"] = True
        info["git_version"] = git_check.stdout.decode("utf-8", errors="replace").strip()

    if info["git_available"]:
        info["user_name"] = _git_try_out(["config", "--get", "user.name"])
        info["user_email"] = _git_try_out(["config", "--get", "user.email"])
        helper = _git_try_out(["config", "--get", "credential.helper"])
        info["credential_helper"] = helper
        info["credential_helper_configured"] = bool(helper)
        helper_lc = helper.lower()
        gcm_detected = ("manager" in helper_lc) or bool(_resolve_gcm_binary(helper))
        info["gcm_detected"] = gcm_detected
        if gcm_detected:
            store = _git_try_out(["config", "--global", "--get", "credential.credentialStore"])
            info["gcm_credential_store"] = store
            if not store and platform.system().lower() == "linux":
                info["gcm_store_required"] = True
                issues.append("GCM credential store is not configured on Linux. Click 'Configure GCM store' in Step 4.")

        if not info["user_name"]:
            issues.append("Git user.name is not configured.")
        if not info["user_email"]:
            issues.append("Git user.email is not configured.")
        if not info["credential_helper_configured"]:
            issues.append("Git credential.helper is not configured.")

        if target.get("ok"):
            remote = _remote_for_target(target["owner"], target["repo"])
            info["target_remote"] = remote
            if not remote:
                issues.append(f"No git remote points to github.com/{target['owner']}/{target['repo']}.")
            else:
                fetch_proc = _run_git_any(["fetch", "--quiet", remote, target["branch"]], timeout_seconds=30)
                if fetch_proc.returncode != 0:
                    fetch_err = fetch_proc.stderr.decode("utf-8", errors="replace").strip()
                    issues.append(f"Could not fetch {remote}/{target['branch']}: {fetch_err or 'unknown fetch error'}")
                else:
                    ahead = _int_out(_git_try_out(["rev-list", "--count", f"{remote}/{target['branch']}..HEAD"]))
                    behind = _int_out(_git_try_out(["rev-list", "--count", f"HEAD..{remote}/{target['branch']}"]))
                    info["ahead_count"] = ahead
                    info["behind_count"] = behind
                    if ahead > 0:
                        allowed_path = _repo_relative_posix(bib_path)
                        changed_paths = _ahead_changed_paths(remote, target["branch"])
                        info["ahead_changed_paths"] = changed_paths
                        if not changed_paths:
                            issues.append("Could not inspect unpushed commits. Please sync and retry.")
                        elif all(p == allowed_path for p in changed_paths):
                            info["ahead_only_canonical_bib"] = True
                            warnings.append(
                                f"Local branch has {ahead} unpushed commit(s) touching only canonical .bib. "
                                "Publish will push them."
                            )
                        else:
                            issues.append(
                                f"Local branch has {ahead} unpushed commit(s) including non-canonical files: "
                                + ", ".join(changed_paths)
                            )
                    if behind > 0:
                        issues.append(f"Local branch is behind by {behind} commit(s). Please sync before one-click publish.")

                if check_push_auth and not issues:
                    push_timeout = _coerce_timeout_seconds(cfg.get("git_push_check_timeout_seconds", 120), default=120)
                    dry = _run_git_any(["push", "--dry-run", remote, f"HEAD:{target['branch']}"], timeout_seconds=push_timeout)
                    out = dry.stdout.decode("utf-8", errors="replace").strip()
                    err = dry.stderr.decode("utf-8", errors="replace").strip()
                    low = f"{out}\n{err}".lower()
                    if dry.returncode == 0:
                        info["push_check_status"] = "ok"
                        info["push_check_message"] = "Push auth check passed."
                    elif dry.returncode == 124 or "timed out" in low:
                        info["push_check_status"] = "auth_timeout"
                        info["push_check_message"] = "Push auth check timed out."
                        issues.append(
                            "Push auth check timed out. Complete browser sign-in and run setup check again."
                        )
                    elif "permission" in low or "403" in low or "denied" in low:
                        info["push_check_status"] = "denied"
                        info["push_check_message"] = "Connected account does not have collaborator publish access."
                        issues.append(
                            "Connected account does not have collaborator publish access for this repository."
                        )
                    else:
                        info["push_check_status"] = "failed"
                        info["push_check_message"] = err or out or "Push auth check failed."
                        issues.append(f"Push auth check failed: {err or out or 'unknown error'}")

    if bib_path.as_posix() != CANONICAL_BIB_PATH:
        issues.append(f"bib_output must be set to {CANONICAL_BIB_PATH} for one-click publish.")
    if not info["local_bib_exists"]:
        issues.append(f"Local .bib artifact is missing: {bib_path}")

    info["issues"] = issues
    info["warnings"] = warnings
    info["ready"] = len(issues) == 0
    return info


def git_switch_account(registry: dict) -> dict:
    target = _git_publish_target(registry)
    if not target.get("ok"):
        raise ValueError(target.get("message", "Invalid publish target configuration."))
    helper = _git_try_out(["config", "--get", "credential.helper"])
    if not helper:
        return {
            "ok": True,
            "status": "no_helper",
            "message": "No credential helper is configured, so there is no cached GitHub account to clear.",
        }
    proc = _run_git_any(["credential", "reject"], stdin_text="protocol=https\nhost=github.com\n\n")
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "Could not clear cached GitHub credentials.")
    return {
        "ok": True,
        "status": "cleared",
        "message": "Cached GitHub credentials were cleared. Next publish will prompt browser login.",
    }


def _resolve_gcm_binary(helper_value: str = "") -> str:
    helper = normalize_whitespace(helper_value)
    if helper:
        try:
            if Path(helper).name == "git-credential-manager" and Path(helper).exists():
                return helper
        except Exception:  # pylint: disable=broad-except
            pass
    found = shutil.which("git-credential-manager")
    return found or ""


def _gcm_list_accounts(gcm_bin: str) -> str:
    if not gcm_bin:
        return ""
    listed = _run_cmd_any([gcm_bin, "github", "list"], timeout_seconds=20)
    if listed.returncode != 0:
        return ""
    return listed.stdout.decode("utf-8", errors="replace").strip()


def git_configure_credential_store(registry: dict) -> dict:
    status = git_publish_status(registry, check_push_auth=False)
    helper = str(status.get("credential_helper", ""))
    if not helper:
        raise ValueError("Git credential.helper is not configured. Run setup check and configure helper first.")
    gcm_bin = _resolve_gcm_binary(helper)
    if not gcm_bin:
        raise ValueError("Git Credential Manager was not found. Install GCM first.")

    current_store = _git_try_out(["config", "--global", "--get", "credential.credentialStore"])
    if current_store:
        return {
            "ok": True,
            "status": "already_configured",
            "message": f"GCM credential store is already configured as '{current_store}'.",
            "credential_store": current_store,
        }

    preferred = "cache"
    set_proc = _run_git_any(["config", "--global", "credential.credentialStore", preferred], timeout_seconds=20)
    if set_proc.returncode != 0:
        err = set_proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "Could not configure GCM credential store.")

    # Keep this enabled for consistency with setup guidance.
    _run_git_any(["config", "--global", "credential.helper", "manager-core"], timeout_seconds=20)
    final_store = _git_try_out(["config", "--global", "--get", "credential.credentialStore"]) or preferred
    return {
        "ok": True,
        "status": "configured",
        "message": f"Configured GCM credential store as '{final_store}'. You can now click Connect GitHub.",
        "credential_store": final_store,
    }


def git_connect_account(registry: dict) -> dict:
    status = git_publish_status(registry, check_push_auth=False)
    target = _git_publish_target(registry)
    if not target.get("ok"):
        raise ValueError(target.get("message", "Invalid publish target configuration."))

    gcm_bin = _resolve_gcm_binary(str(status.get("credential_helper", "")))
    if not gcm_bin:
        raise ValueError(
            "Git Credential Manager was not found. Install it first, then run setup check again."
        )

    login = _run_cmd_any(
        [gcm_bin, "github", "login", "--url", "https://github.com", "--browser", "--force"],
        timeout_seconds=240,
    )
    out = login.stdout.decode("utf-8", errors="replace").strip()
    err = login.stderr.decode("utf-8", errors="replace").strip()
    msg = err or out
    if login.returncode != 0:
        low = msg.lower()
        if login.returncode == 124 or "timed out" in low:
            raise RuntimeError(
                "GitHub sign-in timed out. Click 'Connect GitHub' again and finish browser login promptly."
            )
        if "no credential store has been selected" in low:
            raise RuntimeError(
                "GCM credential store is missing. Click 'Configure GCM store' in Step 4, then click 'Connect GitHub' again."
            )
        raise RuntimeError(msg or "GitHub sign-in failed.")

    accounts = _gcm_list_accounts(gcm_bin)
    return {
        "ok": True,
        "status": "connected",
        "message": "GitHub sign-in completed. Run setup check to confirm push access.",
        "connected_accounts": accounts,
    }


def git_cleanup_pending_publish(registry: dict) -> dict:
    status = git_publish_status(registry)
    ahead = int(status.get("ahead_count", 0))
    behind = int(status.get("behind_count", 0))
    if ahead <= 0:
        return {
            "ok": True,
            "status": "nothing_to_clean",
            "message": "No unpushed commits were found.",
            "target_repo": status.get("target_repo", ""),
            "target_branch": status.get("target_branch", ""),
            "target_path": status.get("target_path", ""),
        }
    if behind > 0:
        raise ValueError("Branch is behind remote. Sync branch first; cleanup is blocked for safety.")
    if not bool(status.get("ahead_only_canonical_bib", False)):
        paths = status.get("ahead_changed_paths", []) or []
        raise ValueError(
            "Cleanup is only allowed when unpushed commits touch canonical .bib only. "
            f"Found: {', '.join(paths) if paths else 'unknown files'}."
        )
    remote = str(status.get("target_remote", ""))
    branch = str(status.get("target_branch", ""))
    if not remote or not branch:
        raise ValueError("Could not resolve target remote/branch for cleanup.")
    reset_proc = _run_git_any(["reset", "--mixed", f"{remote}/{branch}"], timeout_seconds=20)
    if reset_proc.returncode != 0:
        err = reset_proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "Could not clean pending publish commits.")

    bib_rel = _repo_relative_posix(Path(registry.get("config", {}).get("bib_output", CANONICAL_BIB_PATH)))
    bib_dirty = bool(_git_try_out(["status", "--porcelain", "--", bib_rel], timeout_seconds=20))
    return {
        "ok": True,
        "status": "cleaned",
        "message": "Pending local .bib commit(s) were uncommitted. Your .bib changes were kept locally.",
        "target_repo": status.get("target_repo", ""),
        "target_branch": branch,
        "target_path": status.get("target_path", ""),
        "local_bib_has_uncommitted_changes": bib_dirty,
    }


def publish_local_bib_with_git(registry: dict, editor_name: str, change_reason: str) -> dict:
    status = git_publish_status(registry)
    if not status.get("ready"):
        raise ValueError("Publish setup is not ready:\n- " + "\n- ".join(status.get("issues", [])))

    target = _git_publish_target(registry)
    cfg = registry.get("config", {}) or {}
    bib_path = Path(cfg.get("bib_output", CANONICAL_BIB_PATH))
    local_text = bib_path.read_text(encoding="utf-8")
    local_hash = hashlib.sha256(local_text.encode("utf-8")).hexdigest()
    remote = str(status.get("target_remote", ""))
    branch = str(target["branch"])
    remote_spec = f"{remote}/{branch}:{target['path']}"
    pushed_pending = False

    if int(status.get("ahead_count", 0)) > 0:
        if not bool(status.get("ahead_only_canonical_bib", False)):
            raise ValueError("Local branch has unpushed commits outside canonical .bib. Please sync before publishing.")
        _push_head(remote, branch, timeout_seconds=60)
        pushed_pending = True

    _run_git_any(["fetch", "--quiet", remote, branch], timeout_seconds=30)
    remote_show = _run_git_any(["show", remote_spec], timeout_seconds=30)
    remote_text = remote_show.stdout.decode("utf-8", errors="replace") if remote_show.returncode == 0 else ""
    remote_hash = hashlib.sha256(remote_text.encode("utf-8")).hexdigest() if remote_text else ""

    if remote_text == local_text:
        if pushed_pending:
            commit_sha = _git_out(["rev-parse", "HEAD"])
            repo = status.get("target_repo", "")
            commit_url = f"https://github.com/{repo}/commit/{commit_sha}" if repo and commit_sha else ""
            return {
                "ok": True,
                "status": "published",
                "message": "Pushed pending local .bib commit(s) to GitHub.",
                "target_repo": status.get("target_repo", ""),
                "target_branch": branch,
                "target_path": target["path"],
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "commit_sha": commit_sha,
                "commit_url": commit_url,
            }
        return {
            "ok": True,
            "status": "up_to_date",
            "message": "Local .bib already matches remote. Nothing was pushed. If you expected changes, run Step 2 (save + regenerate) first.",
            "target_repo": status.get("target_repo", ""),
            "target_branch": branch,
            "target_path": target["path"],
            "local_hash": local_hash,
            "remote_hash": remote_hash,
            "commit_sha": "",
            "commit_url": "",
        }

    _git_out(["add", "--", str(bib_path)])
    staged = _git_out(["diff", "--cached", "--name-only", "--", str(bib_path)])
    if not staged:
        return {
            "ok": True,
            "status": "up_to_date",
            "message": "No staged .bib changes were detected. Nothing was pushed. If you edited entries, run Step 2 (save + regenerate) first.",
            "target_repo": status.get("target_repo", ""),
            "target_branch": branch,
            "target_path": target["path"],
            "local_hash": local_hash,
            "remote_hash": remote_hash,
            "commit_sha": "",
            "commit_url": "",
        }

    actor = normalize_whitespace(editor_name) or normalize_whitespace(status.get("user_name", "")) or "unknown"
    reason = normalize_whitespace(change_reason) or "Published via Source Registry UI"
    commit_message = (
        "sources: publish canonical .bib from Source Registry UI\n\n"
        f"Editor: {actor}\n"
        f"Reason: {reason}\n"
        f"Published-at: {now_utc()}\n"
        f"Local-SHA256: {local_hash}"
    )
    commit_proc = _run_git_any(["commit", "-m", commit_message, "--", str(bib_path)], timeout_seconds=30)
    if commit_proc.returncode != 0:
        err = commit_proc.stderr.decode("utf-8", errors="replace").strip()
        out = commit_proc.stdout.decode("utf-8", errors="replace").strip()
        msg = err or out
        if "nothing to commit" in msg.lower():
            return {
                "ok": True,
                "status": "up_to_date",
                "message": "No .bib changes to commit. If you expected changes, run Step 2 (save + regenerate) first.",
                "target_repo": status.get("target_repo", ""),
                "target_branch": branch,
                "target_path": target["path"],
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "commit_sha": "",
                "commit_url": "",
            }
        raise RuntimeError(msg or "git commit failed")

    _push_head(remote, branch, timeout_seconds=60)

    commit_sha = _git_out(["rev-parse", "HEAD"])
    repo = status.get("target_repo", "")
    commit_url = f"https://github.com/{repo}/commit/{commit_sha}" if repo and commit_sha else ""
    return {
        "ok": True,
        "status": "published",
        "message": "Published local .bib to GitHub via git push.",
        "target_repo": repo,
        "target_branch": branch,
        "target_path": target["path"],
        "local_hash": local_hash,
        "remote_hash": remote_hash,
        "commit_sha": commit_sha,
        "commit_url": commit_url,
    }


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

        if parsed.path == "/api/git_publish_status":
            reg = self.app.registry
            qs = parse_qs(parsed.query)
            check_push = (qs.get("check_push") or ["0"])[0].strip().lower() in {"1", "true", "yes", "on"}
            self._send_json(git_publish_status(reg, check_push_auth=check_push))
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

            if self.path == "/api/compare_online_bib":
                reg = self.app.registry
                online_compare = compare_local_bib_with_online(reg)
                self._send_json({
                    "ok": True,
                    "operation": "compare_online_bib",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "online_compare": online_compare,
                    "message": "Online comparison complete",
                })
                return

            if self.path == "/api/git_switch_account":
                reg = self.app.registry
                switched = git_switch_account(reg)
                self._send_json({
                    "ok": True,
                    "operation": "git_switch_account",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "git_publish": switched,
                    "git_status": git_publish_status(reg),
                    "message": switched.get("message", "Git account switch complete."),
                })
                return

            if self.path == "/api/git_connect_account":
                reg = self.app.registry
                connected = git_connect_account(reg)
                self._send_json({
                    "ok": True,
                    "operation": "git_connect_account",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "git_publish": connected,
                    "git_status": git_publish_status(reg),
                    "message": connected.get("message", "GitHub connection complete."),
                })
                return

            if self.path == "/api/git_configure_credential_store":
                reg = self.app.registry
                configured = git_configure_credential_store(reg)
                self._send_json({
                    "ok": True,
                    "operation": "git_configure_credential_store",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "git_publish": configured,
                    "git_status": git_publish_status(reg),
                    "message": configured.get("message", "GCM credential store configured."),
                })
                return

            if self.path == "/api/git_cleanup_pending_publish":
                reg = self.app.registry
                cleaned = git_cleanup_pending_publish(reg)
                self._send_json({
                    "ok": True,
                    "operation": "git_cleanup_pending_publish",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "git_publish": cleaned,
                    "git_status": git_publish_status(reg),
                    "message": cleaned.get("message", "Pending publish cleanup complete."),
                })
                return

            if self.path == "/api/publish_online_bib":
                data = self._read_json()
                reg = self.app.registry
                editor_name = normalize_whitespace(data.get("editor_name", ""))
                change_reason = normalize_whitespace(data.get("change_reason", ""))
                published = publish_local_bib_with_git(reg, editor_name, change_reason)
                online_compare = compare_local_bib_with_online(reg)
                self._send_json({
                    "ok": True,
                    "operation": "publish_online_bib",
                    "checks": [],
                    "warnings": [],
                    "errors": [],
                    "git_publish": published,
                    "git_status": git_publish_status(reg),
                    "online_compare": online_compare,
                    "message": published.get("message", "Publish complete."),
                })
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
                online_compare = compare_local_bib_with_online(reg)
                self._send_json({
                    "ok": True,
                    **out,
                    "checks": (out.get("checks", []) + artifact_out.get("checks", [])),
                    "modified_files": modified_paths(before, after),
                    "online_compare": online_compare,
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
                online_compare = compare_local_bib_with_online(reg)
                self._send_json({
                    "ok": True,
                    "record_id": rec_id,
                    "operation": "delete",
                    "changed_fields": deleted_fields,
                    "checks": [{"name": "Delete target resolution", "passed": True, "detail": f"Deleted {rec_id}"}],
                    "modified_files": modified_paths(before, after),
                    "online_compare": online_compare,
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
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, 403)
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

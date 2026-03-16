import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlencode
from urllib.request import urlopen

from common import normalize_url, normalize_whitespace, now_utc


OUTER_PAYLOAD_RE = re.compile(r"var bibbase_data = (.*); document\.write\(bibbase_data\.data\);?\s*$", re.S)
RECOVER_CITEKEY_RE = re.compile(r"^@\w+\{([^,]+),")


def parse_bibbase_show_payload(show_payload_text: str) -> List[dict]:
    match = OUTER_PAYLOAD_RE.search(show_payload_text)
    if not match:
        raise ValueError("Could not parse BibBase show payload.")
    outer = json.loads(match.group(1))
    html = outer["data"]
    start = html.index("data:") + len("data:")
    while html[start].isspace():
        start += 1
    depth = 0
    in_string = False
    escaped = False
    end = None
    for idx in range(start, len(html)):
        ch = html[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
    if end is None:
        raise ValueError("Could not find BibBase entry array.")
    return json.loads(html[start:end])


def _recover_citekey(entry: dict) -> str:
    bibtex = normalize_whitespace((entry.get("bibtex") or "").replace("\n", " "))
    match = RECOVER_CITEKEY_RE.match(bibtex)
    if match:
        return normalize_whitespace(match.group(1))
    return normalize_whitespace(entry.get("key", ""))


def _proposal_id(record_id: str, proposed_ref_link: str) -> str:
    return hashlib.sha256(f"{record_id}|{proposed_ref_link}".encode("utf-8")).hexdigest()


def build_bibbase_show_url(profile_source_url: str) -> str:
    return "https://bibbase.org/show?" + urlencode(
        {
            "bib": profile_source_url,
            "sort": "author_short",
            "theme": "default",
            "showSearch": "true",
            "urlLabel": "link",
            "titleLinks": "true",
            "jsonp": "1",
        }
    )


def _fetch_text(reference_url: str, timeout_seconds: int) -> dict:
    with urlopen(reference_url, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8", errors="replace")
    return {"text": text, "method": "urllib"}


def fetch_and_scan_registry_ref_links(registry: dict, fetch_text=None) -> dict:
    cfg = registry.get("config", {}) or {}
    profile_source_url = normalize_whitespace(cfg.get("bibbase_profile_source_url", ""))
    timeout_seconds = int(cfg.get("bibbase_timeout_seconds", 20) or 20)
    if not profile_source_url:
        return {
            "ok": False,
            "status": "not_configured",
            "message": "bibbase_profile_source_url is not configured in metadata/sources/sources.yaml.",
        }
    local_bib_path = Path(cfg.get("bib_output", "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"))
    local_bib_text = local_bib_path.read_text(encoding="utf-8")
    fetch_impl = fetch_text or _fetch_text
    hosted_bib = fetch_impl(profile_source_url, timeout_seconds)
    show_payload = fetch_impl(build_bibbase_show_url(profile_source_url), timeout_seconds)
    scan = scan_registry_ref_links(registry, show_payload["text"], hosted_bib["text"], local_bib_text)
    scan["scan_metadata"]["profile_source_url"] = profile_source_url
    scan["scan_metadata"]["fetch_method"] = hosted_bib.get("method", "")
    return {"ok": True, "status": "ok", **scan}


def apply_selected_ref_links(registry: dict, proposals: List[dict], selected_ids) -> dict:
    proposal_map = {proposal["proposal_id"]: proposal for proposal in proposals if proposal.get("proposal_id")}
    records_by_id = {record.get("id", ""): record for record in registry.get("records", [])}
    applied_ids = []
    skipped_ids = []
    stale_ids = []
    missing_proposal_ids = []
    for proposal_id in selected_ids:
        proposal = proposal_map.get(proposal_id)
        if not proposal:
            missing_proposal_ids.append(proposal_id)
            continue
        record = records_by_id.get(proposal.get("record_id", ""))
        if not record:
            continue
        current_ref_link = normalize_url(record.get("ref_link", ""))
        expected_current = normalize_url(proposal.get("current_ref_link", ""))
        if current_ref_link != expected_current:
            stale_ids.append(record["id"])
            continue
        if current_ref_link:
            skipped_ids.append(record["id"])
            continue
        record["ref_link"] = proposal["proposed_ref_link"]
        applied_ids.append(record["id"])
    return {
        "applied_ids": sorted(applied_ids),
        "skipped_ids": sorted(skipped_ids),
        "stale_ids": sorted(stale_ids),
        "missing_proposal_ids": sorted(missing_proposal_ids),
    }


def scan_registry_ref_links(registry: dict, show_payload_text: str, hosted_bib_text: str, local_bib_text: str) -> dict:
    hosted_bib_is_stale = hosted_bib_text != local_bib_text
    entries = parse_bibbase_show_payload(show_payload_text)
    by_citekey: Dict[str, List[str]] = {}
    for entry in entries:
        citekey = _recover_citekey(entry)
        candidate = normalize_url(f"https://bibbase.org/network/publication/{entry['bibbaseid']}")
        by_citekey.setdefault(citekey, [])
        if candidate not in by_citekey[citekey]:
            by_citekey[citekey].append(candidate)

    ready_to_apply = []
    needs_review = []
    for record in registry.get("records", []):
        citekey = normalize_whitespace(record.get("citekey", ""))
        current = normalize_url(record.get("ref_link", ""))
        candidates = by_citekey.get(citekey, [])
        if not candidates:
            continue
        if current and current != candidates[0]:
            reason_flags = ["stored ref_link differs from live BibBase"]
            if hosted_bib_is_stale:
                reason_flags.append("hosted BibBase may be stale")
            needs_review.append(
                {
                    "proposal_id": _proposal_id(record["id"], candidates[0]),
                    "record_id": record["id"],
                    "citekey": citekey,
                    "current_ref_link": current,
                    "proposed_ref_link": candidates[0],
                    "selected": False,
                    "confidence": "medium",
                    "reason_flags": reason_flags,
                }
            )
            continue
        if not current and len(candidates) == 1:
            reason_flags = ["blank ref_link, exact citekey match"]
            bucket = ready_to_apply
            selected = True
            confidence = "high"
            if hosted_bib_is_stale:
                reason_flags.append("hosted BibBase may be stale")
                bucket = needs_review
                selected = False
                confidence = "medium"
            bucket.append(
                {
                    "proposal_id": _proposal_id(record["id"], candidates[0]),
                    "record_id": record["id"],
                    "citekey": citekey,
                    "current_ref_link": current,
                    "proposed_ref_link": candidates[0],
                    "selected": selected,
                    "confidence": confidence,
                    "reason_flags": reason_flags,
                }
            )

    return {
        "summary": {
            "ready_to_apply": len(ready_to_apply),
            "needs_review": len(needs_review),
            "dismissed": 0,
        },
        "ready_to_apply": ready_to_apply,
        "needs_review": needs_review,
        "scan_metadata": {
            "compared_at": now_utc(),
            "hosted_bib_matches_local": not hosted_bib_is_stale,
            "hosted_bib_is_stale": hosted_bib_is_stale,
        },
    }

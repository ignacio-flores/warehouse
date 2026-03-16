import hashlib
import json
import re
from typing import Dict, List

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


def scan_registry_ref_links(registry: dict, show_payload_text: str, hosted_bib_text: str, local_bib_text: str) -> dict:
    del hosted_bib_text
    del local_bib_text

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
        if not current and len(candidates) == 1:
            ready_to_apply.append(
                {
                    "proposal_id": _proposal_id(record["id"], candidates[0]),
                    "record_id": record["id"],
                    "citekey": citekey,
                    "current_ref_link": current,
                    "proposed_ref_link": candidates[0],
                    "selected": True,
                    "confidence": "high",
                    "reason_flags": ["blank ref_link, exact citekey match"],
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
            "hosted_bib_matches_local": True,
        },
    }

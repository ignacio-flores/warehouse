#!/usr/bin/env python3
"""Generate reconciliation report for source registry migration."""

import argparse
from collections import Counter
from pathlib import Path

from common import load_registry, normalize_whitespace, parse_bib_entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="metadata/sources/sources.yaml")
    parser.add_argument("--bib", default="documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib")
    parser.add_argument("--out", default="metadata/sources/reconciliation_report.md")
    args = parser.parse_args()

    reg = load_registry(Path(args.registry))
    records = reg.get("records", [])

    missing_citekey = [r for r in records if not normalize_whitespace(r.get("citekey", ""))]
    mismatched = [
        r
        for r in records
        if normalize_whitespace(r.get("source", ""))
        and normalize_whitespace(r.get("citekey", ""))
        and normalize_whitespace(r.get("source", "")) != normalize_whitespace(r.get("citekey", ""))
    ]

    bib_entries = {}
    bib_path = Path(args.bib)
    if bib_path.exists():
        bib_entries = parse_bib_entries(bib_path.read_text(encoding="utf-8", errors="ignore"))

    citekeys = {normalize_whitespace(r.get("citekey", "")) for r in records if normalize_whitespace(r.get("citekey", ""))}
    orphan_bib = sorted([k for k in bib_entries.keys() if k not in citekeys])

    dup_source = Counter(normalize_whitespace(r.get("source", "")) for r in records)
    dup_source = [k for k, v in dup_source.items() if k and v > 1]

    lines = [
        "# Source Registry Reconciliation Report",
        "",
        f"- Total canonical records: {len(records)}",
        f"- Records missing citekey: {len(missing_citekey)}",
        f"- Records where source != citekey: {len(mismatched)}",
        f"- Duplicate source keys: {len(dup_source)}",
        f"- Orphan bib entries (not referenced by citekey): {len(orphan_bib)}",
        "",
        "## Sample Mismatches (source != citekey)",
    ]

    for r in mismatched[:30]:
        lines.append(f"- `{r.get('source','')}` -> `{r.get('citekey','')}`")

    lines += ["", "## Sample Orphan Bib Entries"]
    for key in orphan_bib[:30]:
        lines.append(f"- `{key}`")

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote reconciliation report to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

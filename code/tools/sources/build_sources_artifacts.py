#!/usr/bin/env python3
"""Generate dictionary Sources sheet and canonical .bib from metadata/sources/sources.yaml."""

import argparse
from pathlib import Path

from common import (
    load_registry,
    normalize_whitespace,
    record_to_sources_sheet_row,
    records_sorted,
    render_bib_entry,
    write_sources_sheet,
)


def write_bib(path: Path, records: list) -> None:
    entries = []
    for rec in records:
        key = normalize_whitespace(rec.get("citekey", "")) or normalize_whitespace(rec.get("source", ""))
        if not key:
            continue
        entries.append(render_bib_entry(key, rec))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(entries).strip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="metadata/sources/sources.yaml", help="Path to canonical registry")
    parser.add_argument("--dictionary-template", default=None, help="Template dictionary.xlsx path")
    parser.add_argument("--dictionary-output", default=None, help="Output dictionary.xlsx path")
    parser.add_argument("--bib-output", default=None, help="Output .bib path")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    reg = load_registry(registry_path)
    cfg = reg.get("config", {})

    dictionary_template = Path(args.dictionary_template or cfg.get("dictionary_template", "handmade_tables/dictionary.xlsx"))
    dictionary_output = Path(args.dictionary_output or cfg.get("dictionary_output", "handmade_tables/dictionary.xlsx"))
    bib_output = Path(args.bib_output or cfg.get("bib_output", "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"))

    records = records_sorted(reg.get("records", []))

    rows = [record_to_sources_sheet_row(r) for r in records]
    write_sources_sheet(dictionary_template, dictionary_output, rows)
    write_bib(bib_output, records)

    print(f"Generated dictionary Sources sheet: {dictionary_output}")
    print(f"Generated bib: {bib_output}")
    print(f"Records: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

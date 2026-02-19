#!/usr/bin/env python3
"""Common utilities for source registry tooling."""

import json
import re
import zipfile
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

SOURCES_HEADERS = [
    "Section",
    "AggSource",
    "Legend",
    "Source",
    "Data_Type",
    "Link",
    "Ref_link",
    "Citekey",
    "Inclusion_in_Warehouse",
    "Multigeo_Reference",
    "Metadata",
    "Metadatalink",
    "QcommentsforTA",
    "TAreply",
    "TAcomments",
    "ARJcomments",
    "ARJreplies",
    "SeeAggSourcelisthere",
]

CANONICAL_KEYS = [
    "id",
    "section",
    "aggsource",
    "legend",
    "source",
    "data_type",
    "link",
    "ref_link",
    "citekey",
    "inclusion_in_warehouse",
    "multigeo_reference",
    "metadata",
    "metadatalink",
    "qcommentsforta",
    "tareply",
    "tacomments",
    "arjcomments",
    "arjreplies",
    "seeaggsourcelisthere",
    "bib",
    "created_at",
    "updated_at",
]

DEFAULT_REGISTRY = OrderedDict(
    [
        ("version", 1),
        (
            "config",
            OrderedDict(
                [
                    ("bib_output", "documentation/BibTeX files/GCWealthProject_DataSourcesLibrary.bib"),
                    ("dictionary_template", "handmade_tables/dictionary.xlsx"),
                    ("dictionary_output", "handmade_tables/dictionary.xlsx"),
                ]
            ),
        ),
        ("records", []),
    ]
)


def now_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_text(value: str) -> str:
    return normalize_whitespace(value).lower()


def normalize_url(value: str) -> str:
    val = normalize_whitespace(value)
    if not val:
        return ""
    parsed = urlparse(val)
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    netloc = parsed.netloc.lower()
    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    rebuilt = f"{scheme}://{netloc}{path}"
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    return rebuilt


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def dump_json_yaml(path: Path, payload: dict) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def load_registry(path: Path) -> dict:
    data = load_json_yaml(path)
    if not data:
        return deepcopy(DEFAULT_REGISTRY)
    return data


def save_registry(path: Path, data: dict) -> None:
    dump_json_yaml(path, data)


def column_name(idx: int) -> str:
    name = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def parse_bib_entries(text: str) -> Dict[str, dict]:
    entries: Dict[str, dict] = {}
    i = 0
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        brace = text.find("{", at)
        if brace < 0:
            break
        entry_type = text[at + 1 : brace].strip().lower()
        j = brace + 1
        depth = 1
        while j < len(text):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        raw = text[brace + 1 : j]
        if "," not in raw:
            i = j + 1
            continue
        key, fields_blob = raw.split(",", 1)
        key = key.strip()
        fields = parse_bib_fields(fields_blob)
        entries[key] = {"entry_type": entry_type, "fields": fields}
        i = j + 1
    return entries


def parse_bib_fields(blob: str) -> OrderedDict:
    fields = OrderedDict()
    p = 0
    n = len(blob)
    while p < n:
        while p < n and blob[p] in " \n\r\t,":
            p += 1
        if p >= n:
            break
        eq = blob.find("=", p)
        if eq < 0:
            break
        name = blob[p:eq].strip().lower()
        p = eq + 1
        while p < n and blob[p] in " \n\r\t":
            p += 1
        if p >= n:
            break

        if blob[p] == "{":
            depth = 1
            p += 1
            start = p
            while p < n and depth > 0:
                if blob[p] == "{":
                    depth += 1
                elif blob[p] == "}":
                    depth -= 1
                p += 1
            value = blob[start : p - 1]
        elif blob[p] == '"':
            p += 1
            start = p
            while p < n and blob[p] != '"':
                if blob[p] == "\\":
                    p += 1
                p += 1
            value = blob[start:p]
            p += 1
        else:
            start = p
            while p < n and blob[p] not in ",\n\r":
                p += 1
            value = blob[start:p].strip()

        fields[name] = value.strip()
        comma = blob.find(",", p)
        if comma < 0:
            break
        p = comma + 1
    return fields


def format_bib_value(value: str) -> str:
    return "{" + value.replace("\n", " ").strip() + "}"


def render_bib_entry(key: str, record: dict) -> str:
    bib = record.get("bib", {})
    entry_type = bib.get("entry_type", "misc").strip().lower() or "misc"
    ordered_fields = [
        "title",
        "author",
        "year",
        "month",
        "journal",
        "booktitle",
        "volume",
        "number",
        "pages",
        "institution",
        "publisher",
        "doi",
        "url",
        "urldate",
        "abstract",
        "keywords",
        "note",
    ]

    fields = OrderedDict()
    for f in ordered_fields:
        val = bib.get(f, "")
        if normalize_whitespace(str(val)):
            fields[f] = str(val)

    extras = bib.get("extra_fields", {}) or {}
    for k in sorted(extras.keys()):
        val = normalize_whitespace(str(extras[k]))
        if val:
            fields[k] = val

    lines = [f"@{entry_type}{{{key},"]
    last_index = len(fields) - 1
    for idx, (name, value) in enumerate(fields.items()):
        tail = "," if idx != last_index else ""
        lines.append(f"  {name} = {format_bib_value(value)}{tail}")
    lines.append("}")
    return "\n".join(lines)


def records_sorted(records: List[dict]) -> List[dict]:
    return sorted(records, key=lambda r: (normalize_text(r.get("source", "")), normalize_text(r.get("citekey", ""))))


def get_cell_value(cell: ET.Element, shared: List[str], ns: Dict[str, str]) -> str:
    ctype = cell.attrib.get("t")
    if ctype == "inlineStr":
        node = cell.find("a:is/a:t", ns)
        return node.text if node is not None and node.text else ""
    v = cell.find("a:v", ns)
    if v is None:
        return ""
    raw = v.text or ""
    if ctype == "s" and raw.isdigit():
        idx = int(raw)
        return shared[idx] if idx < len(shared) else ""
    return raw


def locate_sources_sheet(zf: zipfile.ZipFile) -> Tuple[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels.findall(f"{{{NS_PKG_REL}}}Relationship")}
    ns = {"a": NS_MAIN, "r": NS_REL}
    for sheet in workbook.findall("a:sheets/a:sheet", ns):
        if sheet.attrib.get("name") == "Sources":
            rid = sheet.attrib.get(f"{{{NS_REL}}}id")
            if rid:
                target = rid_map[rid]
                if not target.startswith("xl/"):
                    target = "xl/" + target
                return target, sheet.attrib.get("sheetId", "")
    raise RuntimeError("Sources sheet not found in workbook")


def read_sources_sheet(xlsx_path: Path) -> List[dict]:
    with zipfile.ZipFile(xlsx_path, "r") as zf:
        sheet_path, _ = locate_sources_sheet(zf)
        shared = []
        ns = {"a": NS_MAIN}
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                text = "".join((t.text or "") for t in si.findall(".//a:t", ns))
                shared.append(text)

        ws = ET.fromstring(zf.read(sheet_path))
        sheet_data = ws.find("a:sheetData", ns)
        if sheet_data is None:
            return []

        headers = {}
        out = []
        for row in sheet_data.findall("a:row", ns):
            ridx = int(row.attrib.get("r", "0"))
            vals = {}
            for cell in row.findall("a:c", ns):
                ref = cell.attrib.get("r", "")
                col = re.match(r"[A-Z]+", ref)
                if not col:
                    continue
                vals[col.group(0)] = get_cell_value(cell, shared, ns)
            if ridx == 1:
                headers = vals
                continue

            if not headers:
                continue

            record = {}
            any_value = False
            for idx in range(1, len(SOURCES_HEADERS) + 1):
                col = column_name(idx)
                header = headers.get(col, SOURCES_HEADERS[idx - 1])
                val = vals.get(col, "")
                record[header] = val
                if normalize_whitespace(val):
                    any_value = True
            if any_value:
                out.append(record)
    return out


def xml_cell(col_idx: int, row_idx: int, value: str) -> str:
    col = column_name(col_idx)
    ref = f"{col}{row_idx}"
    if value is None:
        value = ""
    escaped = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escaped}</t></is></c>'


def build_sources_sheet_xml(rows: List[dict]) -> bytes:
    header = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    body = [
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    max_row = len(rows) + 1
    body.append(f'<dimension ref="A1:R{max_row}"/>')
    body.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    body.append('<sheetFormatPr defaultRowHeight="15"/>')
    body.append('<sheetData>')

    all_rows = [OrderedDict((h, h) for h in SOURCES_HEADERS)]
    for row in rows:
        ordered = OrderedDict()
        for h in SOURCES_HEADERS:
            ordered[h] = row.get(h, "")
        all_rows.append(ordered)

    for r_idx, row in enumerate(all_rows, start=1):
        body.append(f'<row r="{r_idx}">')
        for c_idx, h in enumerate(SOURCES_HEADERS, start=1):
            body.append(xml_cell(c_idx, r_idx, row.get(h, "")))
        body.append('</row>')

    body.append('</sheetData>')
    body.append(f'<autoFilter ref="A1:R{max_row}"/>')
    body.append('</worksheet>')
    return (header + "".join(body)).encode("utf-8")


def update_filter_database_range(workbook_xml: bytes, max_row: int) -> bytes:
    ns = {"a": NS_MAIN}
    root = ET.fromstring(workbook_xml)
    for dn in root.findall("a:definedNames/a:definedName", ns):
        name = dn.attrib.get("name", "")
        if name == "_xlnm._FilterDatabase" and (dn.text or "").startswith("Sources!"):
            dn.text = f"Sources!$A$1:$R${max_row}"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_sources_sheet(template_xlsx: Path, output_xlsx: Path, rows: List[dict]) -> None:
    sheet_xml = build_sources_sheet_xml(rows)
    ensure_parent(output_xlsx)
    tmp_output = output_xlsx.with_suffix(output_xlsx.suffix + ".tmp")
    with zipfile.ZipFile(template_xlsx, "r") as zin:
        sheet_path, _ = locate_sources_sheet(zin)
        workbook_xml = update_filter_database_range(zin.read("xl/workbook.xml"), len(rows) + 1)

        with zipfile.ZipFile(tmp_output, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for name in zin.namelist():
                if name == sheet_path:
                    zout.writestr(name, sheet_xml)
                elif name == "xl/workbook.xml":
                    zout.writestr(name, workbook_xml)
                else:
                    zout.writestr(name, zin.read(name))
    tmp_output.replace(output_xlsx)


def normalize_record(raw: dict) -> dict:
    row = {k: raw.get(k, "") for k in CANONICAL_KEYS if k != "bib"}
    row["bib"] = raw.get("bib", {})
    row["source"] = normalize_whitespace(row.get("source", ""))
    row["citekey"] = normalize_whitespace(row.get("citekey", ""))
    row["section"] = normalize_whitespace(row.get("section", ""))
    row["aggsource"] = normalize_whitespace(row.get("aggsource", ""))
    row["legend"] = normalize_whitespace(row.get("legend", ""))
    row["link"] = normalize_whitespace(row.get("link", ""))
    row["ref_link"] = normalize_whitespace(row.get("ref_link", ""))
    return row


def record_to_sources_sheet_row(record: dict) -> dict:
    return {
        "Section": record.get("section", ""),
        "AggSource": record.get("aggsource", ""),
        "Legend": record.get("legend", ""),
        "Source": record.get("source", ""),
        "Data_Type": record.get("data_type", ""),
        "Link": record.get("link", ""),
        "Ref_link": record.get("ref_link", ""),
        "Citekey": record.get("citekey", ""),
        "Inclusion_in_Warehouse": record.get("inclusion_in_warehouse", ""),
        "Multigeo_Reference": record.get("multigeo_reference", ""),
        "Metadata": record.get("metadata", ""),
        "Metadatalink": record.get("metadatalink", ""),
        "QcommentsforTA": record.get("qcommentsforta", ""),
        "TAreply": record.get("tareply", ""),
        "TAcomments": record.get("tacomments", ""),
        "ARJcomments": record.get("arjcomments", ""),
        "ARJreplies": record.get("arjreplies", ""),
        "SeeAggSourcelisthere": record.get("seeaggsourcelisthere", ""),
    }

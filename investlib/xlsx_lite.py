"""Minimal stdlib-only .xlsx reader (no openpyxl/pandas dependency).

An .xlsx is a zip of XML parts; we only need shared strings + one sheet's
cell grid, so a full library is overkill for parsing broker report exports.
"""

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_to_num(col: str) -> int:
    n = 0
    for c in col:
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n


def _shared_strings(z: zipfile.ZipFile) -> list:
    try:
        data = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    return ["".join(t.text or "" for t in si.findall(".//m:t", _NS)) for si in root.findall("m:si", _NS)]


_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_DOC_REL_NS = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _sheet_name_to_path(z: zipfile.ZipFile) -> dict:
    """Map sheet name -> worksheet part path, via workbook.xml + its .rels
    (don't assume sheetN.xml order matches tab order)."""
    wb_root = ET.fromstring(z.read("xl/workbook.xml"))
    rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {rel.get("Id"): rel.get("Target") for rel in rels_root.findall("r:Relationship", _REL_NS)}
    mapping = {}
    for sheet in wb_root.findall("m:sheets/m:sheet", _NS):
        rid = sheet.get(f"{{{_DOC_REL_NS['r']}}}id")
        target = rid_to_target.get(rid)
        if target:
            mapping[sheet.get("name")] = "xl/" + target.lstrip("/")
    return mapping


def _cell_value(cell, shared: list) -> str:
    t = cell.get("t")
    v = cell.find("m:v", _NS)
    if t == "s":
        return shared[int(v.text)] if v is not None and v.text != "" else ""
    if t == "inlineStr":
        istr = cell.find("m:is", _NS)
        return "".join(tt.text or "" for tt in istr.findall(".//m:t", _NS)) if istr is not None else ""
    return v.text if v is not None else ""


def _rows_from_sheet_xml(data: bytes, shared: list) -> list:
    root = ET.fromstring(data)
    rows = []
    for row in root.findall(".//m:sheetData/m:row", _NS):
        cells = {}
        max_col = 0
        for c in row.findall("m:c", _NS):
            col_letters = "".join(ch for ch in c.get("r") if ch.isalpha())
            col_num = _col_to_num(col_letters)
            max_col = max(max_col, col_num)
            cells[col_num] = _cell_value(c, shared)
        rows.append([cells.get(i, "") for i in range(1, max_col + 1)])
    return rows


def sheet_names(path: Path) -> list:
    """List the worksheet names in a workbook (tab order), so callers can tell
    a single-report export from a multi-sheet 'master' workbook."""
    with zipfile.ZipFile(path) as z:
        return list(_sheet_name_to_path(z).keys())


def read_sheet_by_name(path: Path, sheet_name: str) -> list:
    """Return a sheet's rows as a list of lists of strings. Empty trailing
    cells within a row are '', but empty rows are still returned (some
    reports use blank rows as section separators)."""
    with zipfile.ZipFile(path) as z:
        shared = _shared_strings(z)
        name_to_path = _sheet_name_to_path(z)
        if sheet_name not in name_to_path:
            raise ValueError(f"{path}: no sheet named {sheet_name!r} (have {list(name_to_path)})")
        return _rows_from_sheet_xml(z.read(name_to_path[sheet_name]), shared)

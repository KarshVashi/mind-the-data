"""Read one worksheet of an .xlsx workbook with the standard library.

TfL publishes NUMBAT and the annual counts only as .xlsx. The project needs
a handful of plain cell values from them once a year, which does not justify
a spreadsheet dependency. Formulas are read as their cached values; styles,
dates and merged cells are ignored (the sheets used here hold text and
numbers only).
"""

import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_MAIN = "{" + NS["m"] + "}"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")

Cell = str | float | None


def column_index(letters: str) -> int:
    """Zero-based index of a column name: A is 0, Z 25, AA 26."""
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as book:
        root = ElementTree.fromstring(book.read("xl/workbook.xml"))
    return [sheet.attrib["name"] for sheet in root.iterfind("m:sheets/m:sheet", NS)]


def _sheet_part(book: zipfile.ZipFile, name: str) -> str:
    workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    rel_id = None
    for sheet in workbook.iterfind("m:sheets/m:sheet", NS):
        if sheet.attrib["name"] == name:
            rel_id = sheet.attrib["{" + NS["r"] + "}id"]
    if rel_id is None:
        raise KeyError(f"no sheet named {name!r}")
    rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iterfind("rel:Relationship", NS):
        if rel.attrib["Id"] == rel_id:
            target = rel.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise KeyError(f"sheet {name!r} has no part")


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
    # A rich-text string is split into runs; its value is all of them joined.
    return ["".join(t.text or "" for t in item.iter(_MAIN + "t")) for item in root]


def _value(cell: ElementTree.Element, shared: list[str]) -> Cell:
    kind = cell.attrib.get("t", "n")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(_MAIN + "t"))
    raw = cell.findtext("m:v", default=None, namespaces=NS)
    if raw is None:
        return None
    if kind == "s":
        return shared[int(raw)]
    if kind in ("str", "e"):
        return raw
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return float(raw)


def read_sheet(path: Path, name: str) -> Iterator[list[Cell]]:
    """Yield each row of the sheet as a list, empty cells as None.

    Rows the file leaves out (wholly empty rows) are yielded as empty lists,
    so row positions match the spreadsheet's.
    """
    with zipfile.ZipFile(path) as book:
        shared = _shared_strings(book)
        part = _sheet_part(book, name)
        with book.open(part) as handle:
            expected_row = 1
            for _, element in ElementTree.iterparse(handle):
                if element.tag != _MAIN + "row":
                    continue
                row_number = int(element.attrib.get("r", expected_row))
                while expected_row < row_number:
                    yield []
                    expected_row += 1
                values: list[Cell] = []
                for cell in element.iterfind("m:c", NS):
                    match = _CELL_REF.fullmatch(cell.attrib.get("r", ""))
                    position = column_index(match.group(1)) if match else len(values)
                    values.extend([None] * (position - len(values)))
                    values.append(_value(cell, shared))
                yield values
                expected_row = row_number + 1
                element.clear()

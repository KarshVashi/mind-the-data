import zipfile
from pathlib import Path

import pytest

from mind_the_data.demand.xlsx import column_index, read_sheet, sheet_names

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_book(path: Path, sheet_xml: str) -> Path:
    with zipfile.ZipFile(path, "w") as book:
        book.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
            '<sheet name="_Cover" sheetId="1" r:id="rId1"/>'
            '<sheet name="Link_Loads" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{PKG}">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/>'
            '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml" Type="x"/>'
            "</Relationships>",
        )
        book.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{MAIN}"><si><t>Link</t></si>'
            "<si><r><t>Bake</t></r><r><t>rloo</t></r></si></sst>",
        )
        book.writestr(
            "xl/worksheets/sheet1.xml", f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>'
        )
        book.writestr(
            "xl/worksheets/sheet2.xml",
            f'<worksheet xmlns="{MAIN}"><sheetData>{sheet_xml}</sheetData></worksheet>',
        )
    return path


@pytest.mark.parametrize(("letters", "index"), [("A", 0), ("Z", 25), ("AA", 26), ("DI", 112)])
def test_column_index(letters: str, index: int) -> None:
    assert column_index(letters) == index


def test_reads_shared_inline_and_numeric_cells_with_gaps(tmp_path: Path) -> None:
    book = write_book(
        tmp_path / "book.xlsx",
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="3"><c r="B3" t="inlineStr"><is><t>Lambeth North</t></is></c>'
        '<c r="D3"><v>9616.25</v></c><c r="E3" t="str"><v>=x</v></c></row>',
    )
    assert sheet_names(book) == ["_Cover", "Link_Loads"]
    rows = list(read_sheet(book, "Link_Loads"))
    assert rows == [
        ["Link", None, "Bakerloo"],
        [],
        [None, "Lambeth North", None, 9616.25, "=x"],
    ]


def test_unknown_sheet_is_an_error(tmp_path: Path) -> None:
    book = write_book(tmp_path / "book.xlsx", "")
    with pytest.raises(KeyError):
        list(read_sheet(book, "Station_Flows"))

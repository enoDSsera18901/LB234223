from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from lastbarrel.xlsx import (
    InvalidXlsxWorkbook,
    ObservationCellSpec,
    UnsafeFormulaCell,
    UnknownCell,
    UnknownWorksheet,
    list_worksheets,
    parse_observation_cells,
    read_cell,
    read_worksheet_cells,
)


WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Table 3a" sheetId="1" r:id="rId1"/>
    <sheet name="Notes" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>
"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>
"""

SHARED_STRINGS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
  <si><t>World crude production</t></si>
  <si><r><t>Million </t></r><r><t>barrels/day</t></r></si>
</sst>
"""

SHEET1_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="s"><v>0</v></c>
      <c r="B1" t="s"><v>1</v></c>
      <c r="C1" t="inlineStr"><is><t>2026-08</t></is></c>
    </row>
    <row r="5">
      <c r="C5"><v>104.25</v></c>
      <c r="D5"><f>C5+1</f><v>105.25</v></c>
      <c r="E5" t="b"><v>1</v></c>
    </row>
  </sheetData>
</worksheet>
"""

SHEET2_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Synthetic fixture only</t></is></c></row></sheetData>
</worksheet>
"""


def write_fixture(path: Path, *, unsafe_relationship: bool = False) -> None:
    rels = RELS_XML
    if unsafe_relationship:
        rels = rels.replace('Target="worksheets/sheet1.xml"', 'Target="../evil.xml"')
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", WORKBOOK_XML)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", SHARED_STRINGS_XML)
        archive.writestr("xl/worksheets/sheet1.xml", SHEET1_XML)
        archive.writestr("xl/worksheets/sheet2.xml", SHEET2_XML)


class WorkbookStructureTests(unittest.TestCase):
    def test_lists_worksheets_in_source_order(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            sheets = list_worksheets(path)
            self.assertEqual([sheet.name for sheet in sheets], ["Table 3a", "Notes"])
            self.assertEqual(sheets[0].path, "xl/worksheets/sheet1.xml")

    def test_decodes_shared_inline_numeric_boolean_and_formula_cells(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            cells = {cell.coordinate: cell for cell in read_worksheet_cells(path, "Table 3a")}

            self.assertEqual(cells["A1"].value, "World crude production")
            self.assertEqual(cells["B1"].value, "Million barrels/day")
            self.assertEqual(cells["C1"].value, "2026-08")
            self.assertEqual(cells["C5"].value, Decimal("104.25"))
            self.assertEqual(cells["D5"].value, Decimal("105.25"))
            self.assertEqual(cells["D5"].formula, "C5+1")
            self.assertEqual(cells["E5"].value, True)
            self.assertEqual(cells["D5"].lineage, "Table 3a!D5")

    def test_unknown_sheet_and_cell_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            with self.assertRaises(UnknownWorksheet):
                read_worksheet_cells(path, "Made Up")
            with self.assertRaises(UnknownCell):
                read_cell(path, "Table 3a", "Z99")

    def test_unsafe_relationship_target_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path, unsafe_relationship=True)
            with self.assertRaisesRegex(InvalidXlsxWorkbook, "unsafe workbook relationship"):
                list_worksheets(path)


class ObservationMappingTests(unittest.TestCase):
    def test_numeric_cell_becomes_observation_only_with_explicit_semantics(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            parsed = parse_observation_cells(
                path,
                [
                    ObservationCellSpec(
                        sheet_name="Table 3a",
                        value_cell="C5",
                        series_id="SYNTHETIC.WORLD.CRUDE",
                        period=date(2026, 8, 1),
                        unit="million barrels/day",
                    )
                ],
            )

            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].observation.value, Decimal("104.25"))
            self.assertEqual(parsed[0].observation.period, date(2026, 8, 1))
            self.assertEqual(parsed[0].source_lineage, "Table 3a!C5")

    def test_formula_backed_observation_requires_explicit_opt_in(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            spec = ObservationCellSpec(
                sheet_name="Table 3a",
                value_cell="D5",
                series_id="SYNTHETIC.FORMULA",
                period=date(2026, 8, 1),
                unit="synthetic units",
            )
            with self.assertRaises(UnsafeFormulaCell):
                parse_observation_cells(path, [spec])

            parsed = parse_observation_cells(
                path,
                [
                    ObservationCellSpec(
                        sheet_name=spec.sheet_name,
                        value_cell=spec.value_cell,
                        series_id=spec.series_id,
                        period=spec.period,
                        unit=spec.unit,
                        allow_formula_cached_value=True,
                    )
                ],
            )
            self.assertEqual(parsed[0].observation.value, Decimal("105.25"))
            self.assertEqual(parsed[0].formula, "C5+1")

    def test_text_cell_cannot_be_silently_used_as_numeric_observation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            with self.assertRaisesRegex(ValueError, "not numeric"):
                parse_observation_cells(
                    path,
                    [
                        ObservationCellSpec(
                            sheet_name="Table 3a",
                            value_cell="A1",
                            series_id="SYNTHETIC.TEXT",
                            period=date(2026, 8, 1),
                            unit="synthetic units",
                        )
                    ],
                )

    def test_duplicate_series_period_mapping_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            write_fixture(path)
            specs = [
                ObservationCellSpec(
                    "Table 3a", "C5", "SYNTHETIC.SAME", date(2026, 8, 1), "u"
                ),
                ObservationCellSpec(
                    "Table 3a", "D5", "SYNTHETIC.SAME", date(2026, 8, 1), "u",
                    allow_formula_cached_value=True,
                ),
            ]
            with self.assertRaisesRegex(ValueError, "duplicate observation mapping"):
                parse_observation_cells(path, specs)


if __name__ == "__main__":
    unittest.main()

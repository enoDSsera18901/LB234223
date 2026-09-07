"""Dependency-free, conservative XLSX structure reader for STEO source workbooks.

This module deliberately separates physical workbook decoding from EIA-specific
semantic mapping. It can enumerate worksheets, decode individual cells with
sheet/cell lineage, and turn explicitly mapped numeric cells into ``Observation``
objects. It does not infer series identifiers, periods, units, or table meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .steo import Observation


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200


class InvalidXlsxWorkbook(ValueError):
    """Workbook structure is missing, malformed, or exceeds safety limits."""


class UnknownWorksheet(KeyError):
    """Requested worksheet name does not exist in the workbook."""


class UnknownCell(KeyError):
    """Requested worksheet cell does not exist in the source workbook."""


class UnsafeFormulaCell(ValueError):
    """Observation mapping attempted to use a formula cell without opt-in."""


@dataclass(frozen=True, slots=True)
class WorksheetRef:
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class XlsxCell:
    sheet_name: str
    coordinate: str
    value: str | Decimal | bool | None
    cell_type: str
    formula: str | None = None

    @property
    def lineage(self) -> str:
        return f"{self.sheet_name}!{self.coordinate}"


@dataclass(frozen=True, slots=True)
class ObservationCellSpec:
    """Explicit semantic mapping from one source cell to one STEO observation.

    The caller must provide the series ID, period, and unit. LastBarrel only
    reads the numeric value from ``value_cell`` and records where it came from.
    """

    sheet_name: str
    value_cell: str
    series_id: str
    period: date
    unit: str
    allow_formula_cached_value: bool = False


@dataclass(frozen=True, slots=True)
class ParsedObservation:
    observation: Observation
    source_sheet: str
    source_cell: str
    formula: str | None

    @property
    def source_lineage(self) -> str:
        return f"{self.source_sheet}!{self.source_cell}"


def _tag(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _safe_member_bytes(archive: ZipFile, member: str) -> bytes:
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise InvalidXlsxWorkbook(f"missing XLSX member: {member}") from exc

    if info.file_size > _MAX_MEMBER_BYTES:
        raise InvalidXlsxWorkbook(f"XLSX member too large: {member}")
    if info.compress_size > 0 and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
        raise InvalidXlsxWorkbook(f"XLSX member compression ratio is unsafe: {member}")
    return archive.read(info)


def _validate_archive_limits(archive: ZipFile) -> None:
    total = 0
    for info in archive.infolist():
        total += info.file_size
        if total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise InvalidXlsxWorkbook("XLSX uncompressed size exceeds safety limit")
        if info.file_size > _MAX_MEMBER_BYTES:
            raise InvalidXlsxWorkbook(f"XLSX member too large: {info.filename}")
        if info.compress_size > 0 and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
            raise InvalidXlsxWorkbook(
                f"XLSX member compression ratio is unsafe: {info.filename}"
            )


def _open_archive(path: str | Path) -> ZipFile:
    try:
        archive = ZipFile(Path(path), "r")
    except (BadZipFile, OSError) as exc:
        raise InvalidXlsxWorkbook("source is not a readable XLSX ZIP archive") from exc
    try:
        _validate_archive_limits(archive)
    except Exception:
        archive.close()
        raise
    return archive


def _resolve_relationship_target(target: str) -> str:
    clean = target.replace("\\", "/")
    path = PurePosixPath(clean)
    if path.is_absolute() or ".." in path.parts:
        raise InvalidXlsxWorkbook(f"unsafe workbook relationship target: {target}")
    if path.parts and path.parts[0] == "xl":
        return str(path)
    return str(PurePosixPath("xl") / path)


def list_worksheets(path: str | Path) -> tuple[WorksheetRef, ...]:
    """Return workbook worksheet names and internal OOXML paths in source order."""

    with _open_archive(path) as archive:
        workbook = ET.fromstring(_safe_member_bytes(archive, "xl/workbook.xml"))
        relationships = ET.fromstring(
            _safe_member_bytes(archive, "xl/_rels/workbook.xml.rels")
        )

        targets: dict[str, str] = {}
        for relationship in relationships.findall(_tag(_PKG_REL_NS, "Relationship")):
            rel_id = relationship.attrib.get("Id")
            target = relationship.attrib.get("Target")
            target_mode = relationship.attrib.get("TargetMode", "Internal")
            if rel_id and target and target_mode != "External":
                targets[rel_id] = _resolve_relationship_target(target)

        result: list[WorksheetRef] = []
        sheets = workbook.find(_tag(_MAIN_NS, "sheets"))
        if sheets is None:
            raise InvalidXlsxWorkbook("workbook contains no sheets collection")
        for sheet in sheets.findall(_tag(_MAIN_NS, "sheet")):
            name = sheet.attrib.get("name")
            rel_id = sheet.attrib.get(_tag(_DOC_REL_NS, "id"))
            if not name or not rel_id or rel_id not in targets:
                raise InvalidXlsxWorkbook("worksheet relationship is incomplete")
            result.append(WorksheetRef(name=name, path=targets[rel_id]))
        if not result:
            raise InvalidXlsxWorkbook("workbook contains no worksheets")
        return tuple(result)


def _shared_strings(archive: ZipFile) -> tuple[str, ...]:
    try:
        payload = _safe_member_bytes(archive, "xl/sharedStrings.xml")
    except InvalidXlsxWorkbook as exc:
        if "missing XLSX member" in str(exc):
            return ()
        raise

    root = ET.fromstring(payload)
    values: list[str] = []
    for item in root.findall(_tag(_MAIN_NS, "si")):
        text_parts = [node.text or "" for node in item.iter(_tag(_MAIN_NS, "t"))]
        values.append("".join(text_parts))
    return tuple(values)


def _decode_cell(
    cell: ET.Element,
    *,
    sheet_name: str,
    shared_strings: tuple[str, ...],
) -> XlsxCell:
    coordinate = cell.attrib.get("r")
    if not coordinate:
        raise InvalidXlsxWorkbook("worksheet cell is missing coordinate")
    cell_type = cell.attrib.get("t", "n")
    formula_node = cell.find(_tag(_MAIN_NS, "f"))
    formula = formula_node.text if formula_node is not None else None

    if cell_type == "inlineStr":
        inline = cell.find(_tag(_MAIN_NS, "is"))
        value = "" if inline is None else "".join(
            node.text or "" for node in inline.iter(_tag(_MAIN_NS, "t"))
        )
        return XlsxCell(sheet_name, coordinate, value, cell_type, formula)

    value_node = cell.find(_tag(_MAIN_NS, "v"))
    raw = value_node.text if value_node is not None else None
    if raw is None:
        return XlsxCell(sheet_name, coordinate, None, cell_type, formula)

    if cell_type == "s":
        try:
            index = int(raw)
            value = shared_strings[index]
        except (ValueError, IndexError) as exc:
            raise InvalidXlsxWorkbook(
                f"invalid shared-string index at {sheet_name}!{coordinate}"
            ) from exc
    elif cell_type == "b":
        if raw not in {"0", "1"}:
            raise InvalidXlsxWorkbook(f"invalid boolean at {sheet_name}!{coordinate}")
        value = raw == "1"
    elif cell_type in {"str", "e"}:
        value = raw
    else:
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise InvalidXlsxWorkbook(
                f"invalid numeric value at {sheet_name}!{coordinate}: {raw!r}"
            ) from exc
    return XlsxCell(sheet_name, coordinate, value, cell_type, formula)


def read_worksheet_cells(path: str | Path, sheet_name: str) -> tuple[XlsxCell, ...]:
    """Decode non-empty cells from one worksheet with exact sheet/cell lineage."""

    worksheets = list_worksheets(path)
    by_name = {item.name: item for item in worksheets}
    if sheet_name not in by_name:
        raise UnknownWorksheet(sheet_name)

    with _open_archive(path) as archive:
        shared = _shared_strings(archive)
        root = ET.fromstring(_safe_member_bytes(archive, by_name[sheet_name].path))
        result: list[XlsxCell] = []
        for cell in root.iter(_tag(_MAIN_NS, "c")):
            decoded = _decode_cell(cell, sheet_name=sheet_name, shared_strings=shared)
            if decoded.value is not None or decoded.formula is not None:
                result.append(decoded)
        return tuple(result)


def read_cell(path: str | Path, sheet_name: str, coordinate: str) -> XlsxCell:
    wanted = coordinate.strip().upper()
    for cell in read_worksheet_cells(path, sheet_name):
        if cell.coordinate.upper() == wanted:
            return cell
    raise UnknownCell(f"{sheet_name}!{wanted}")


def parse_observation_cells(
    path: str | Path,
    specs: Iterable[ObservationCellSpec],
) -> tuple[ParsedObservation, ...]:
    """Create observations only from caller-explicit cell/series/date/unit mappings."""

    grouped: dict[str, dict[str, XlsxCell]] = {}
    result: list[ParsedObservation] = []
    seen: set[tuple[str, date]] = set()

    for spec in specs:
        if not spec.series_id.strip() or not spec.unit.strip():
            raise ValueError("series_id and unit must not be blank")
        key = (spec.series_id, spec.period)
        if key in seen:
            raise ValueError(
                f"duplicate observation mapping for {spec.series_id} at {spec.period.isoformat()}"
            )
        seen.add(key)

        if spec.sheet_name not in grouped:
            grouped[spec.sheet_name] = {
                cell.coordinate.upper(): cell
                for cell in read_worksheet_cells(path, spec.sheet_name)
            }
        cell = grouped[spec.sheet_name].get(spec.value_cell.strip().upper())
        if cell is None:
            raise UnknownCell(f"{spec.sheet_name}!{spec.value_cell.strip().upper()}")
        if cell.formula is not None and not spec.allow_formula_cached_value:
            raise UnsafeFormulaCell(
                f"formula-backed observation requires explicit opt-in: {cell.lineage}"
            )
        if not isinstance(cell.value, Decimal):
            raise ValueError(f"observation source cell is not numeric: {cell.lineage}")

        observation = Observation(
            series_id=spec.series_id,
            period=spec.period,
            value=cell.value,
            unit=spec.unit,
        )
        result.append(
            ParsedObservation(
                observation=observation,
                source_sheet=cell.sheet_name,
                source_cell=cell.coordinate,
                formula=cell.formula,
            )
        )
    return tuple(result)

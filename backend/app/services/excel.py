"""Query results as an Excel workbook, with the fields' formats (currency, percent, dates)."""

import io
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill

MAX_ROWS = 1_048_000  # Excel's limit is 1,048,576


def _number_format(col: Any) -> str | None:
    fmt = getattr(col, "format", None) or {}
    kind = fmt.get("type")
    col_type = getattr(col, "type", "")
    default = 1 if kind == "percent" else 0 if col_type == "integer" else 2
    decimals = int(fmt["decimals"]) if fmt.get("decimals") is not None else default
    places = ("." + "0" * decimals) if decimals else ""
    if kind == "percent":
        return f"0{places}%"
    if kind == "currency":
        return f'"{fmt.get("currency", "AED")}" #,##0{places}'
    if col_type in {"integer", "number"} or kind == "number":
        return f"#,##0{places}"
    if getattr(col, "type", "") == "date":
        return "yyyy-mm-dd"
    if getattr(col, "type", "") == "datetime":
        return "yyyy-mm-dd hh:mm"
    return None


def _value(v: Any, col: Any) -> Any:
    if v is None:
        return None
    kind = getattr(col, "type", "")
    if isinstance(v, str) and kind in {"date", "datetime"}:
        try:
            return (
                date.fromisoformat(v[:10])
                if kind == "date"
                else datetime.fromisoformat(v).replace(tzinfo=None)
            )
        except ValueError:
            return v
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v  # never let text become a formula
    return v


def workbook(columns: list[Any], batches: Iterable[list[list[Any]]], sheet: str = "Data") -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=(sheet[:31] or "Data").replace("/", "-"))
    ws.freeze_panes = "A2"
    header_font, header_fill = Font(bold=True), PatternFill("solid", fgColor="F1F5F9")
    header = []
    for c in columns:
        cell = WriteOnlyCell(ws, value=getattr(c, "label", str(c)))
        cell.font, cell.fill, cell.alignment = header_font, header_fill, Alignment(vertical="center")
        header.append(cell)
    from openpyxl.utils import get_column_letter

    for i, c in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(40, len(getattr(c, "label", "")) + 4))
    ws.append(header)
    formats = [_number_format(c) for c in columns]
    written = 0
    for batch in batches:
        for row in batch:
            if written >= MAX_ROWS:
                break
            out = []
            for v, c, f in zip(row, columns, formats, strict=False):
                cell = WriteOnlyCell(ws, value=_value(v, c))
                if f and cell.value is not None and not isinstance(cell.value, str):
                    cell.number_format = f
                out.append(cell)
            ws.append(out)
            written += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

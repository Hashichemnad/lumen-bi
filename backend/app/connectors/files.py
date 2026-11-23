"""Uploaded files: CSV/TSV, Excel (.xlsx, every sheet), Parquet."""

from collections.abc import Iterator
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from app.connectors.base import Connector, ConnectorError, ObjectInfo, unique_column_names

FORMATS = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".parquet": "parquet",
}
SINGLE_OBJECT = "data"


class FileConnector(Connector):
    """config: {"filename": original name, "path": stored path, "format": csv|xlsx|parquet}"""

    type = "file"
    label = "Uploaded file"

    @property
    def path(self) -> Path:
        path = Path(self.config.get("path", ""))
        if not path.is_file():
            raise ConnectorError("The uploaded file is missing. Upload it again.")
        return path

    def test_connection(self) -> None:
        self.list_objects()

    def list_objects(self) -> list[ObjectInfo]:
        if self.config.get("format") == "xlsx":
            from openpyxl import load_workbook

            try:
                wb = load_workbook(self.path, read_only=True)
            except Exception as exc:
                raise ConnectorError("This Excel file could not be opened.") from exc
            names = list(wb.sheetnames)
            wb.close()
            return [ObjectInfo(name=n, kind="sheet") for n in names]
        return [ObjectInfo(name=SINGLE_OBJECT, kind="file")]

    def extract(
        self,
        source: dict[str, Any],
        since: Any = None,
        incremental_column: str | None = None,
        batch_size: int = 50_000,
        inclusive: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        fmt = self.config.get("format")
        if fmt == "csv":
            yield from self._csv(batch_size)
        elif fmt == "parquet":
            yield from self._parquet(batch_size)
        elif fmt == "xlsx":
            yield from self._excel(source.get("object") or "", batch_size)
        else:
            raise ConnectorError("Unsupported file type.")

    def _csv(self, batch_size: int) -> Iterator[pa.RecordBatch]:
        con = duckdb.connect()
        try:
            rel = con.read_csv(str(self.path), header=True, sample_size=-1)
            # to_arrow_reader in newer DuckDB, fetch_record_batch in older releases
            to_reader = getattr(rel, "to_arrow_reader", None) or rel.fetch_record_batch
            reader = to_reader(batch_size)
            names = unique_column_names(reader.schema.names)
            yielded = False
            for batch in reader:
                yield pa.RecordBatch.from_arrays(batch.columns, names=names)
                yielded = True
            if not yielded:
                yield pa.RecordBatch.from_arrays([pa.array([], f.type) for f in reader.schema], names=names)
        except duckdb.Error as exc:
            raise ConnectorError(f"This CSV file could not be read: {str(exc).splitlines()[0]}") from exc
        finally:
            con.close()

    def _parquet(self, batch_size: int) -> Iterator[pa.RecordBatch]:
        try:
            pf = pq.ParquetFile(self.path)
        except Exception as exc:
            raise ConnectorError("This Parquet file could not be read.") from exc
        names = unique_column_names(pf.schema_arrow.names)
        yielded = False
        for batch in pf.iter_batches(batch_size=batch_size):
            yield pa.RecordBatch.from_arrays(batch.columns, names=names)
            yielded = True
        if not yielded:
            yield pa.RecordBatch.from_arrays([pa.array([], f.type) for f in pf.schema_arrow], names=names)

    def _excel(self, sheet: str, batch_size: int) -> Iterator[pa.RecordBatch]:
        from openpyxl import load_workbook

        try:
            wb = load_workbook(self.path, read_only=True, data_only=True)
        except Exception as exc:
            raise ConnectorError("This Excel file could not be opened.") from exc
        try:
            if sheet not in wb.sheetnames:
                raise ConnectorError(f"Sheet {sheet!r} was not found in the file.")
            rows = wb[sheet].iter_rows(values_only=True)
            header = next(rows, None)
            if header is None:
                raise ConnectorError(f"Sheet {sheet!r} is empty.")
            # Drop trailing empty header cells
            width = len(header)
            while width and (header[width - 1] is None or str(header[width - 1]).strip() == ""):
                width -= 1
            names = unique_column_names(list(header[:width]))
            buffer: list[tuple] = []
            yielded = False
            for row in rows:
                row = tuple(row[:width]) + (None,) * max(0, width - len(row))
                if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
                    continue
                buffer.append(row)
                if len(buffer) >= batch_size:
                    yield _rows_to_batch(buffer, names)
                    buffer, yielded = [], True
            if buffer or not yielded:
                yield _rows_to_batch(buffer, names)
        finally:
            wb.close()


def _rows_to_batch(rows: list[tuple], names: list[str]) -> pa.RecordBatch:
    arrays = []
    for i in range(len(names)):
        values = [r[i] for r in rows]
        kinds = {type(v) for v in values if v is not None}
        if kinds - {int, float, bool, str, datetime, date, time}:
            values = [None if v is None else str(v) for v in values]
        elif len(kinds) > 1 and not kinds <= {int, float}:
            # Mixed types in one Excel column (e.g. numbers and text): keep as text
            values = [None if v is None else str(v) for v in values]
        try:
            arrays.append(pa.array(values) if rows else pa.array([], pa.string()))
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays.append(pa.array([None if v is None else str(v) for v in values]))
    return pa.RecordBatch.from_arrays(arrays, names=names)

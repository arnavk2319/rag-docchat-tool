"""Safe structured table queries (csv/xlsx) for the query_table agent tool.

The model supplies a declarative op validated by Pydantic; it is translated to pandas boolean
masks / groupby calls. No eval, exec, DataFrame.query or expression strings: columns must exist
in the file, operators and aggregate functions come from closed enums, and values are scalars.
Row, column, byte and cell-size caps bound the work. Table contents are untrusted data.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ROWS_OUT = 50
MAX_CELL_CHARS = 80
MAX_BYTES = 5_000_000
MAX_ROWS_READ = 100_000
MAX_COLS = 200
MAX_UNZIPPED = 50_000_000
MAX_ZIP_MEMBERS = 200
TABLE_SUFFIXES = (".csv", ".xlsx")
COL_PATTERN = r"^[\w .\-/()%#]{1,64}$"  # no quotes, brackets, backticks, operators, newlines

Scalar = str | int | float | bool


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_M):
    column: str = Field(pattern=COL_PATTERN)
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "in"]
    value: Scalar | list[Scalar]

    @field_validator("value")
    @classmethod
    def _finite(cls, v: Scalar | list[Scalar]) -> Scalar | list[Scalar]:
        for x in v if isinstance(v, list) else [v]:
            if isinstance(x, float) and not math.isfinite(x):
                raise ValueError("non-finite value")
        return v


class Agg(_M):
    column: str = Field(pattern=COL_PATTERN)
    fn: Literal["sum", "mean", "min", "max", "count", "nunique"]


class Sort(_M):
    column: str = Field(pattern=COL_PATTERN)
    desc: bool = False


class TableOp(_M):
    sheet: str | None = Field(default=None, max_length=64)
    select: list[str] | None = Field(default=None, max_length=20)
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    aggs: list[Agg] = Field(default_factory=list, max_length=5)
    sort: list[Sort] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=20, ge=1, le=MAX_ROWS_OUT)


class TableError(Exception):
    """User-facing, content-free error message."""


def _check_cols(op: TableOp) -> None:
    names = [*(op.select or []), *op.group_by]
    for n in names:
        if not re.fullmatch(COL_PATTERN, n):
            raise ValueError("invalid column name")


def validate_op(op: TableOp) -> TableOp:
    _check_cols(op)
    if op.aggs and not op.group_by and op.select:
        raise ValueError("select cannot be combined with aggs without group_by")
    return op


def _check_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except Exception as exc:
        raise TableError("could not read table") from exc
    if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise TableError("could not read table")


def load_table(data: bytes, filename: str, sheet: str | None) -> pd.DataFrame:
    if len(data) > MAX_BYTES:
        raise TableError("table too large")
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data), nrows=MAX_ROWS_READ)
        else:
            _check_zip(data)
            df = pd.read_excel(
                io.BytesIO(data), sheet_name=sheet if sheet else 0, nrows=MAX_ROWS_READ,
                engine="openpyxl",
            )
    except Exception as exc:
        raise TableError("could not read table") from exc
    if len(df.columns) > MAX_COLS:
        raise TableError("too many columns")
    df.columns = [str(c) for c in df.columns]
    return df


def _need(df: pd.DataFrame, col: str) -> None:
    if col not in df.columns:
        raise TableError(f"unknown column; available: {', '.join(list(df.columns)[:30])}")


def _mask(df: pd.DataFrame, f: Filter) -> pd.Series:
    _need(df, f.column)
    s = df[f.column]
    v = f.value
    try:
        if f.op == "in":
            return s.isin(v if isinstance(v, list) else [v])
        if isinstance(v, list):
            raise TableError("list value only valid with op=in")
        if f.op == "contains":
            return s.astype(str).str.contains(str(v), case=False, regex=False, na=False)
        if f.op in ("gt", "ge", "lt", "le") and pd.api.types.is_numeric_dtype(s):
            v = float(v)
        ops = {"eq": s.eq, "ne": s.ne, "gt": s.gt, "ge": s.ge, "lt": s.lt, "le": s.le}
        return ops[f.op](v)
    except TableError:
        raise
    except Exception as exc:
        raise TableError("filter value incompatible with column") from exc


def _cell(v: object) -> str:
    s = "" if pd.isna(v) else str(v)
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "..."


def execute(df: pd.DataFrame, op: TableOp) -> str:
    for f in op.filters:
        df = df[_mask(df, f)]
    if op.aggs or op.group_by:
        for c in op.group_by:
            _need(df, c)
        for a in op.aggs:
            _need(df, a.column)
        named = {f"{a.fn}_{a.column}": (a.column, a.fn) for a in op.aggs}
        if op.group_by:
            if not named:
                named = {"count": (op.group_by[0], "size")}
            out = df.groupby(op.group_by, dropna=False).agg(**named).reset_index()
        else:
            try:
                out = pd.DataFrame([{k: df[c].agg(fn) for k, (c, fn) in named.items()}])
            except Exception as exc:
                raise TableError("aggregate incompatible with column") from exc
    else:
        out = df
        if op.select:
            for c in op.select:
                _need(df, c)
            out = df[op.select]
    for s in op.sort:
        _need(out, s.column)
    if op.sort:
        out = out.sort_values(
            [s.column for s in op.sort], ascending=[not s.desc for s in op.sort], kind="stable"
        )
    total = len(out)
    out = out.head(op.limit)
    lines = [" | ".join(_cell(c) for c in out.columns)]
    lines += [" | ".join(_cell(v) for v in row) for row in out.itertuples(index=False)]
    if total > len(out):
        lines.append(f"[{total - len(out)} more rows not shown]")
    if total == 0:
        lines.append("[no rows]")
    return "\n".join(lines)

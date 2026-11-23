"""The query spec: the one format used by charts, dashboards, AI, exports and subscriptions.

{"dataset": 12,
 "dimensions": [{"field": "Order Date", "grain": "month"}, "Region"],
 "measures": [{"field": "Amount", "agg": "sum", "calc": {"type": "pct_change"}}, {"field": "Margin"}],
 "filters": [{"field": "Status", "op": "in", "values": ["Won"]},
             {"field": "Order Date", "op": "relative", "unit": "month", "n": 12},
             {"field": "Region", "op": "top", "n": 5, "by": {"field": "Amount", "agg": "sum"}},
             {"field": "Amount", "agg": "sum", "op": "gt", "value": 1000}],
 "sort": [{"key": "sum:Amount", "dir": "desc"}], "limit": 5000, "params": {"FX Rate": 3.67},
 "totals": true}
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Grain = Literal["year", "quarter", "month", "week", "day", "hour", "minute"]
Part = Literal["year", "quarter", "month", "week", "weekday", "day", "hour"]
Agg = Literal[
    "sum", "avg", "count", "count_distinct", "min", "max", "median", "percentile", "stdev", "var", "none"
]
CalcType = Literal["running_total", "pct_total", "difference", "pct_change", "rank", "moving_avg"]
FilterOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is_null",
    "not_null",
    "relative",
    "top",
    "bottom",
]


class Dimension(BaseModel):
    field: str
    grain: Grain | None = None  # truncate a date: month -> first day of the month
    part: Part | None = None  # a date part as a number: month -> 1..12
    key: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if self.grain and self.part:
            raise ValueError("Use grain or part, not both")
        return self


class TableCalc(BaseModel):
    type: CalcType
    along: str | None = None  # dimension key to compute along (default: first date dimension, else first)
    partition_by: list[str] | None = None  # dimension keys to restart at (default: all others)
    window: int = Field(3, ge=1, le=1000)  # moving average size


class Measure(BaseModel):
    field: str  # "*" with agg count = number of rows
    agg: Agg | None = None  # default: the field's default aggregation
    p: float | None = Field(None, gt=0, lt=1)  # for percentile
    calc: TableCalc | None = None
    key: str | None = None


class Filter(BaseModel):
    field: str
    op: FilterOp
    value: Any = None
    values: list[Any] | None = None
    grain: Grain | None = None
    part: Part | None = None
    agg: Agg | None = None  # set to filter on an aggregated value (applied after grouping)
    # relative dates: last n units, including the current one
    unit: Literal["day", "week", "month", "quarter", "year"] | None = None
    n: int | None = Field(None, ge=1, le=100_000)
    by: Measure | None = None  # top/bottom: rank by this measure


class Sort(BaseModel):
    key: str
    dir: Literal["asc", "desc"] = "asc"


def _dims(v):
    return [{"field": x} if isinstance(x, str) else x for x in v or []]


class QuerySpec(BaseModel):
    dataset: int
    mode: Literal["aggregate", "rows"] = "aggregate"
    dimensions: list[Dimension] = []
    measures: list[Measure] = []
    fields: list[str] = []  # rows mode: which fields to return (default: all visible)
    filters: list[Filter] = []
    sort: list[Sort] = []
    limit: int | None = Field(None, ge=1)
    params: dict[str, Any] = {}
    totals: bool = False
    table_calc: TableCalc | None = None  # applies to every measure that has no calc of its own

    _norm_dims = field_validator("dimensions", mode="before")(_dims)
    _norm_measures = field_validator("measures", mode="before")(_dims)

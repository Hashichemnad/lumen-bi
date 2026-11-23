from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class DatasetField(Base):
    """A field of a dataset as people see it: a source column or a calculated field.

    kind: column | calculated | group | bin
    role: dimension (things you group by) | measure (things you add up)
    format: {"type": "number"|"currency"|"percent"|"date"|"datetime"|"text", "decimals": 2,
             "currency": "AED"}
    config (group): {"source": "Region", "groups": [{"name": "GCC", "values": ["KSA", "QAT"]}],
                     "other": "Other"}
    config (bin):   {"source": "Age", "size": 10}
    """

    __tablename__ = "dataset_fields"
    __table_args__ = (UniqueConstraint("dataset_id", "name", name="uq_dataset_field_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))  # column name, or the calculated field's name
    kind: Mapped[str] = mapped_column(String(20), default="column")
    label: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(20), default="dimension")
    data_type: Mapped[str] = mapped_column(String(20), default="string")
    format: Mapped[dict] = mapped_column(JSONType, default=dict)
    default_agg: Mapped[str] = mapped_column(String(20), default="sum")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_aggregate: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # set when a formula no longer compiles
    missing: Mapped[bool] = mapped_column(Boolean, default=False)  # column no longer in the source
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        for key, value in {
            "kind": "column",
            "label": "",
            "description": "",
            "role": "dimension",
            "data_type": "string",
            "format": {},
            "default_agg": "sum",
            "hidden": False,
            "config": {},
            "is_aggregate": False,
            "missing": False,
            "position": 0,
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)


class Hierarchy(Base):
    """A drill path, e.g. Country → Emirate → Branch.

    Date fields get year → quarter → month → day automatically, without a hierarchy.
    """

    __tablename__ = "hierarchies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    levels: Mapped[list] = mapped_column(JSONType, default=list)  # field names, top to bottom


class Parameter(Base):
    """A value people can change on a dashboard and that formulas use as @Name."""

    __tablename__ = "parameters"
    __table_args__ = (UniqueConstraint("dataset_id", "name", name="uq_parameter_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(20), default="number")  # number integer string date boolean
    default_value: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"value": ...}
    allowed_values: Mapped[list] = mapped_column(JSONType, default=list)  # empty = any value

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class RlsRule(Base):
    """A row-level security rule on a dataset. All enabled rules of a dataset apply together (AND).

    kind: attribute | entitlement
    config (attribute):   {"field": "Region", "attribute": "region", "ignore_case": true}
        rows where [Region] is one of the user's "region" values (a value or a list). Built-in attributes:
        email, user_id, name, groups (group names).
    config (entitlement): {"field": "Branch", "dataset": 7, "user_column": "email",
                           "value_column": "branch", "match": "email", "all_value": "*",
                           "ignore_case": true}
        rows where [Branch] is one of the values listed for the user in another dataset (an access table
        kept in Salesforce, Oracle or a file). "match" is the user attribute compared with user_column;
        a row with all_value gives access to everything.
    exempt_groups: group ids whose members this rule does not apply to (e.g. Management sees all rows).
    """

    __tablename__ = "rls_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="attribute")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    exempt_groups: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        for key, value in {
            "name": "",
            "kind": "attribute",
            "config": {},
            "exempt_groups": [],
            "enabled": True,
            "description": "",
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)


class DatasetShare(Base):
    """Who may query a dataset besides Creators and Admins (who can query every dataset).

    principal: everyone | user | group. Row-level security still applies to everyone but Admins.
    """

    __tablename__ = "dataset_shares"
    __table_args__ = (
        UniqueConstraint("dataset_id", "principal", "principal_id", name="uq_dataset_share_principal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    principal: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[int] = mapped_column(Integer, default=0)  # 0 for everyone
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

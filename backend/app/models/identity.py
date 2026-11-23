from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class IdentityProvider(Base):
    """A configured single sign-on provider.

    type: entra | uaepass | oidc
    config (non-secret settings), by type:
      entra:   {"tenant_id": "..."}
      uaepass: {"environment": "staging" | "production", "min_user_type": "SOP1" | "SOP2" | "SOP3",
                "acr_values": "...", "ui_locales": "en" | "ar"}
      oidc:    {"discovery_url": "https://.../.well-known/openid-configuration",
                "scopes": "openid email profile"}
    attribute_map: {"lumen attribute": "claim name"}, e.g. {"department": "department"}
    group_map: {"claim value": "Lumen group name"} (Entra group object IDs -> groups)
    """

    __tablename__ = "identity_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))  # shown on the button: "Microsoft", "UAE PASS"
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[str] = mapped_column(String(255), default="")
    client_secret_enc: Mapped[str] = mapped_column(String(1000), default="")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    attribute_map: Mapped[dict] = mapped_column(JSONType, default=dict)
    group_map: Mapped[dict] = mapped_column(JSONType, default=dict)
    # invite-only (False) or create accounts on first sign-in (True)
    auto_create: Mapped[bool] = mapped_column(Boolean, default=False)
    default_role: Mapped[str] = mapped_column(String(20), default="viewer")
    allowed_domains: Mapped[list] = mapped_column(JSONType, default=list)  # empty = any
    # Link to an existing account with the same email on first sign-in
    link_by_email: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        for key, value in {
            "enabled": False,
            "client_id": "",
            "client_secret_enc": "",
            "config": {},
            "attribute_map": {},
            "group_map": {},
            "auto_create": False,
            "default_role": "viewer",
            "allowed_domains": [],
            "link_by_email": True,
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)


class UserIdentity(Base):
    """Links a user to an external identity (one user can have Microsoft and UAE PASS both)."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider_id", "subject", name="uq_identity_provider_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("identity_providers.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(255))  # stable id from the provider (sub / oid / uuid)
    email: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    provider: Mapped[IdentityProvider] = relationship(lazy="joined")

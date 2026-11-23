"""Milestone 13: AI providers, the AI request log, and OAuth for the MCP server."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import JSONType, UTCDateTime, utcnow


class AiProvider(Base):
    """A model Lumen can call.

    kind: anthropic | openai | openai_compatible (Ollama, vLLM, LM Studio, Azure OpenAI and the like)
    local: runs on the organisation's own servers. Only local providers may see sensitive datasets.
    features: which features may use it; empty = all (ask, explain, formula, dashboard, digest).
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(200))
    api_key_enc: Mapped[str] = mapped_column(String(2000), default="")
    local: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    features: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs):
        for key, value in {
            "base_url": "",
            "api_key_enc": "",
            "local": False,
            "enabled": True,
            "is_default": False,
            "features": [],
        }.items():
            kwargs.setdefault(key, value)
        super().__init__(**kwargs)


class AiRequest(Base):
    """One call to a model: who, which feature, which provider, how long, and whether it worked.
    The prompt and answer are not stored (they can contain data)."""

    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )
    provider_name: Mapped[str] = mapped_column(String(100), default="")
    feature: Mapped[str] = mapped_column(String(20))
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok | failed
    error: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class OAuthClient(Base):
    """An app registered to use the MCP server (usually by dynamic client registration, e.g. Claude)."""

    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    redirect_uris: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OAuthCode(Base):
    """A one-time authorization code (PKCE S256), valid for a few minutes. Stored as a hash."""

    __tablename__ = "oauth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    redirect_uri: Mapped[str] = mapped_column(String(1000))
    code_challenge: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(200), default="")
    resource: Mapped[str] = mapped_column(String(500), default="")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OAuthToken(Base):
    """An access token (short-lived) and its refresh token for one person and one app. Both stored as
    hashes. Refresh tokens rotate: using one replaces the pair."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(200), default="")
    access_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    refresh_expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

"""Organisation settings kept in the database: branding (name, logo, colour, sign-in message) and email
(SMTP). Email set here replaces the SMTP_* environment settings; its password is encrypted."""

import base64
import re
import time

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models import AppSetting, User

LOGO_MAX_BYTES = 200_000
LOGO_RE = re.compile(r"^data:image/(png|jpeg|webp|svg\+xml);base64,([A-Za-z0-9+/=]+)$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_with_white(hex_colour: str) -> float:
    return 1.05 / (_luminance(hex_colour) + 0.05)


class Branding(BaseModel):
    name: str = Field("Lumen", min_length=1, max_length=60)
    accent: str | None = None  # buttons and highlights; None = the theme's own blue
    logo: str | None = None  # data:image/...;base64,...
    login_message: str = Field("", max_length=300)

    @field_validator("accent")
    @classmethod
    def _accent(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not HEX_RE.match(v):
            raise ValueError("Use a colour like #1f6fb5")
        if contrast_with_white(v) < 4.5:
            raise ValueError("Choose a darker colour: white text on buttons must stay readable")
        return v.lower()

    @field_validator("logo")
    @classmethod
    def _logo(cls, v: str | None) -> str | None:
        if not v:
            return None
        m = LOGO_RE.match(v)
        if not m:
            raise ValueError("The logo must be a PNG, JPEG, WebP or SVG picture")
        data = base64.b64decode(m.group(2))
        if len(data) > LOGO_MAX_BYTES:
            raise ValueError("The logo must be smaller than 200 KB")
        if m.group(1) == "svg+xml" and re.search(rb"<script|on\w+\s*=|javascript:", data, re.I):
            # Shown only as an <img>, where scripts never run, but there is no reason to keep them
            raise ValueError("The SVG logo contains scripts")
        return v


def branding(db: Session) -> Branding:
    row = db.get(AppSetting, "branding")
    try:
        return Branding(**(row.value if row else {}))
    except ValueError:
        return Branding()


def save_branding(db: Session, value: Branding, user: User) -> Branding:
    row = db.get(AppSetting, "branding")
    if row is None:
        row = AppSetting(key="branding", value={})
        db.add(row)
    row.value = value.model_dump()
    row.updated_by_id = user.id
    return value


# ------------------------------------------------------------------ email
class EmailSettings(BaseModel):
    host: str = Field("", max_length=255)
    port: int = Field(587, ge=1, le=65535)
    user: str = Field("", max_length=255)
    password: str | None = Field(None, max_length=500)  # None = keep the saved one
    sender: str = Field("", max_length=320)  # "Lumen BI <no-reply@company.ae>"
    tls: bool = True


_cache: tuple[float, dict] | None = None


def smtp(db: Session | None = None) -> dict:
    """The SMTP settings in use: the ones saved in Admin → Settings, else the environment's."""
    global _cache
    if db is None and _cache and _cache[0] > time.time():
        return _cache[1]
    from app.db import SessionLocal

    s = get_settings()
    env = {"host": s.smtp_host, "port": s.smtp_port, "user": s.smtp_user, "password": s.smtp_password,
           "sender": s.smtp_from, "tls": s.smtp_tls, "source": "environment"}  # fmt: skip
    try:
        if db is not None:
            row = db.get(AppSetting, "email")
            value = dict(row.value) if row else {}
        else:
            with SessionLocal() as own:
                row = own.get(AppSetting, "email")
                value = dict(row.value) if row else {}
    except Exception:  # noqa: BLE001 - email must still work if settings cannot be read
        value = {}
    out = env
    if value.get("host"):
        try:
            password = decrypt(value.get("password_enc"))
        except ValueError:
            password = ""
        out = {"host": value["host"], "port": value.get("port", 587), "user": value.get("user", ""),
               "password": password, "sender": value.get("sender") or s.smtp_from,
               "tls": value.get("tls", True), "source": "settings"}  # fmt: skip
    _cache = (time.time() + 30, out)
    return out


def save_email(db: Session, body: EmailSettings, user: User) -> None:
    global _cache
    row = db.get(AppSetting, "email")
    if row is None:
        row = AppSetting(key="email", value={})
        db.add(row)
    old = dict(row.value or {})
    value = body.model_dump(exclude={"password"})
    if body.password is None:
        value["password_enc"] = old.get("password_enc", "")
    else:
        value["password_enc"] = encrypt(body.password) if body.password else ""
    row.value = value
    row.updated_by_id = user.id
    _cache = None


def email_view(db: Session) -> dict:
    row = db.get(AppSetting, "email")
    value = dict(row.value) if row else {}
    used = smtp(db)
    return {
        "host": value.get("host", ""),
        "port": value.get("port", 587),
        "user": value.get("user", ""),
        "has_password": bool(value.get("password_enc")),
        "sender": value.get("sender", ""),
        "tls": value.get("tls", True),
        "in_use": used["source"] if used["host"] else "none",
        "environment_host": get_settings().smtp_host,
    }

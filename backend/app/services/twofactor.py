"""Two-factor sign-in with an authenticator app (TOTP, RFC 6238: SHA-1, 6 digits, 30 seconds) and
one-time recovery codes.

- The secret is encrypted at rest; recovery codes are stored as SHA-256 hashes and each works once.
- A code is accepted for the current 30-second step and one step either side (clock drift), and never for a
  step at or before the last one used (no replay).
- Admins can require it for admins only or for everyone (password sign-in; single sign-on providers handle
  their own second factor).
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.models import AppSetting, Role, User

DIGITS = 6
PERIOD = 30
RECOVERY_CODES = 10
POLICIES = ("off", "admins", "everyone")


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, when: float | None = None) -> str:
    return _code(secret, int((when if when is not None else time.time()) // PERIOD))


def matching_step(secret: str, code: str, after: int = 0, when: float | None = None) -> int | None:
    """The time step the code belongs to (current ±1), if it is later than `after`."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((when if when is not None else time.time()) // PERIOD)
    for step in (now - 1, now, now + 1):
        if step > after and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def uri(user: User, secret: str) -> str:
    issuer = get_settings().app_name
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(user.email)}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


def qr_svg(text: str) -> str:
    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode()


# ------------------------------------------------------------------ recovery codes
def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    user.recovery_codes = [_hash(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _hash(code)
    left = list(user.recovery_codes or [])
    for stored in left:
        if hmac.compare_digest(stored, h):
            left.remove(stored)
            user.recovery_codes = left
            return True
    return False


# ------------------------------------------------------------------ enrolment and checking
def start(user: User) -> dict:
    secret = new_secret()
    user.totp_pending_enc = encrypt(secret)
    link = uri(user, secret)
    return {"secret": secret, "uri": link, "qr_svg": qr_svg(link)}


def confirm(user: User, code: str) -> list[str] | None:
    """Turn it on if the code matches the pending secret; returns the new recovery codes."""
    secret = decrypt(user.totp_pending_enc) if user.totp_pending_enc else ""
    if not secret:
        return None
    step = matching_step(secret, code)
    if step is None:
        return None
    user.totp_secret_enc = user.totp_pending_enc
    user.totp_pending_enc = ""
    user.totp_enabled = True
    user.totp_last_step = step
    return new_recovery_codes(user)


def verify(user: User, code: str) -> str | None:
    """'totp' or 'recovery' when the code is right (and records it as used), else None."""
    if not user.totp_enabled:
        return None
    secret = decrypt(user.totp_secret_enc)
    step = matching_step(secret, code, after=user.totp_last_step or 0)
    if step is not None:
        user.totp_last_step = step
        return "totp"
    if use_recovery_code(user, code):
        return "recovery"
    return None


def turn_off(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret_enc = ""
    user.totp_pending_enc = ""
    user.totp_last_step = 0
    user.recovery_codes = []


# ------------------------------------------------------------------ policy
def policy(db: Session) -> str:
    row = db.get(AppSetting, "security")
    value = (row.value or {}).get("require_two_factor", "off") if row else "off"
    return value if value in POLICIES else "off"


def required_for(db: Session, user: User) -> bool:
    p = policy(db)
    return p == "everyone" or (p == "admins" and user.has_role(Role.admin))


def save_policy(db: Session, value: str, admin: User) -> None:
    row = db.get(AppSetting, "security")
    if row is None:
        row = AppSetting(key="security", value={})
        db.add(row)
    row.value = {**(row.value or {}), "require_two_factor": value}
    row.updated_by_id = admin.id

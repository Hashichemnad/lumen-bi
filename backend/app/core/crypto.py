"""Encryption for secrets stored in the database (SSO client secrets, connection passwords).

Uses Fernet (AES-128-CBC + HMAC-SHA256). The key is derived from ENCRYPTION_KEY, so any long random
string works. Changing ENCRYPTION_KEY makes existing secrets unreadable: they must be entered again.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    material = s.encryption_key or s.secret_key  # fall back so development works without extra setup
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted; was ENCRYPTION_KEY changed?") from exc

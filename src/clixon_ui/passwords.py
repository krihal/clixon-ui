"""Password hashing (stdlib scrypt, no extra dependency) and temporary password generation. Pure."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**15, 8, 1
MIN_LENGTH = 8


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _N, _R, _P)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def temporary_password() -> str:
    """Readable one-time password: 12 characters without look-alikes (0/O, 1/l/I)."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def check_new_password(password: str, username: str = "") -> str | None:
    """Reason the password is not acceptable, or None."""
    if len(password) < MIN_LENGTH:
        return f"The password must be at least {MIN_LENGTH} characters."
    if password.lower() in ("admin", "password", username.lower()):
        return "The password is too easy to guess."
    return None

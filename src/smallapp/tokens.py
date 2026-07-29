"""Secret generation, owner-token hashing, and signed session cookies.

Pure: no I/O, no env, no logging. Every secret comparison goes through
`hmac.compare_digest`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time

COOKIE_NAME = "__Host-smallapp"
COOKIE_VERSION = "v1"
DEFAULT_TTL = 30 * 24 * 3600

SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32
SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_secret() -> str:
    """A fresh HMAC key for cookie signing."""
    return secrets.token_urlsafe(32)


def generate_token() -> str:
    """A fresh owner login token. Printed once, then only its hash is stored."""
    return secrets.token_urlsafe(24)


def hash_token(token: str, salt: bytes | None = None) -> str:
    """Salted scrypt hash of `token`, self-describing so verification needs no config."""
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        token.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_token(token: str, encoded: str) -> bool:
    """Constant-time check of `token` against a `hash_token` output. False on any garble."""
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = (int(part) for part in parts[1:4])
        salt = _unb64(parts[4])
        expected = _unb64(parts[5])
    except (ValueError, binascii.Error):
        return False
    if not 1 < n <= 1 << 20 or not 0 < r <= 64 or not 0 < p <= 16 or n & (n - 1):
        return False
    if len(expected) != SCRYPT_LEN:
        return False
    try:
        digest = hashlib.scrypt(token.encode(), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_LEN)
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def _mac(secret: str, sub: str, exp: int) -> bytes:
    message = f"{COOKIE_VERSION}.{sub}.{exp}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).digest()


def sign_cookie(
    secret: str, sub: str = "owner", ttl: int = DEFAULT_TTL, now: int | None = None
) -> str:
    """Return `v1.<b64 sub>.<exp>.<b64 mac>` valid for `ttl` seconds."""
    if not secret:
        raise ValueError("cookie secret is empty")
    issued = int(time.time()) if now is None else now
    exp = issued + ttl
    encoded_sub = _b64(sub.encode())
    return f"{COOKIE_VERSION}.{encoded_sub}.{exp}.{_b64(_mac(secret, encoded_sub, exp))}"


def verify_cookie(secret: str, value: str, now: int | None = None) -> str | None:
    """Return the subject if `value` is an intact, unexpired cookie, else None."""
    parts = value.split(".")
    if len(parts) != 4 or parts[0] != COOKIE_VERSION:
        return None
    _, encoded_sub, raw_exp, raw_mac = parts
    try:
        exp = int(raw_exp)
        mac = _unb64(raw_mac)
        sub = _unb64(encoded_sub).decode()
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if not hmac.compare_digest(_mac(secret, encoded_sub, exp), mac):
        return None
    if exp <= (int(time.time()) if now is None else now):
        return None
    return sub

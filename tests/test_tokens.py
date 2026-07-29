"""Token hashing and signed cookies, including every way verification must fail."""

from __future__ import annotations

import inspect
import re

from smallapp import tokens
from smallapp.tokens import (
    generate_secret,
    generate_token,
    hash_token,
    sign_cookie,
    verify_cookie,
    verify_token,
)

SECRET = "test-secret"
NOW = 1_700_000_000


def test_generated_values_are_unique_and_long() -> None:
    assert generate_secret() != generate_secret()
    assert generate_token() != generate_token()
    assert len(generate_token()) >= 24


def test_hash_token_is_salted_scrypt() -> None:
    encoded = hash_token("swordfish")
    assert encoded.startswith("scrypt$")
    assert verify_token("swordfish", encoded)
    assert not verify_token("swordfisH", encoded)


def test_same_token_hashes_differently_under_different_salts() -> None:
    a = hash_token("swordfish", salt=b"a" * 16)
    b = hash_token("swordfish", salt=b"b" * 16)
    assert a != b
    assert verify_token("swordfish", a)
    assert verify_token("swordfish", b)


def test_verify_token_rejects_garbled_encodings() -> None:
    good = hash_token("swordfish", salt=b"a" * 16)
    assert not verify_token("swordfish", "")
    assert not verify_token("swordfish", "bcrypt$1$2$3$aa$bb")
    assert not verify_token("swordfish", good.replace("scrypt$16384", "scrypt$3"))
    assert not verify_token("swordfish", good[:-4])
    assert not verify_token("swordfish", good.rsplit("$", 1)[0] + "$!!!!")


def test_cookie_round_trips() -> None:
    cookie = sign_cookie(SECRET, sub="owner", ttl=60, now=NOW)
    assert cookie.startswith("v1.")
    assert verify_cookie(SECRET, cookie, now=NOW) == "owner"


def test_cookie_rejects_tampered_payload() -> None:
    cookie = sign_cookie(SECRET, sub="owner", ttl=60, now=NOW)
    version, sub, exp, mac = cookie.split(".")
    forged = ".".join([version, sign_cookie(SECRET, sub="root", now=NOW).split(".")[1], exp, mac])
    assert verify_cookie(SECRET, forged, now=NOW) is None


def test_cookie_rejects_tampered_signature() -> None:
    cookie = sign_cookie(SECRET, ttl=60, now=NOW)
    flipped = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
    assert verify_cookie(SECRET, flipped, now=NOW) is None


def test_cookie_rejects_expired() -> None:
    cookie = sign_cookie(SECRET, ttl=60, now=NOW)
    assert verify_cookie(SECRET, cookie, now=NOW + 61) is None


def test_cookie_rejects_wrong_version_prefix() -> None:
    cookie = sign_cookie(SECRET, ttl=60, now=NOW)
    assert verify_cookie(SECRET, "v2" + cookie[2:], now=NOW) is None


def test_cookie_rejects_wrong_secret() -> None:
    cookie = sign_cookie(SECRET, ttl=60, now=NOW)
    assert verify_cookie("other-secret", cookie, now=NOW) is None


def test_cookie_rejects_truncated_value() -> None:
    cookie = sign_cookie(SECRET, ttl=60, now=NOW)
    assert verify_cookie(SECRET, cookie[: len(cookie) // 2], now=NOW) is None
    assert verify_cookie(SECRET, "", now=NOW) is None


def test_cookie_default_now_uses_the_clock() -> None:
    assert verify_cookie(SECRET, sign_cookie(SECRET, ttl=60)) == "owner"


def test_secrets_are_only_compared_with_compare_digest() -> None:
    source = inspect.getsource(tokens)
    body = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("#", '"'))
    )
    for match in re.finditer(r"^\s*(?:if |return |assert ).*==.*$", body, re.MULTILINE):
        assert "parts[0]" in match.group(0), f"non-constant-time compare: {match.group(0)!r}"
    assert "hmac.compare_digest" in source

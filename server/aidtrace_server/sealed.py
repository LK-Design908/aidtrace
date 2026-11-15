"""Sealed-box receipt protocol (server side, plus a reference device implementation).

Device -> server: POST /api/recipient/submit with body = libsodium sealed box
(crypto_box_seal, X25519 + XSalsa20-Poly1305) of a padded plaintext:

    u32 big-endian length L | L bytes UTF-8 JSON | zero padding to the bucket size

Bucket sizes come from server.yaml (receipt.buckets). The PIN field is padded to a fixed width
before serialisation, so the PIN never affects the size. Normal and duress submissions of the
same receipt are therefore byte-for-byte the same length, and the device never learns which PIN
was entered. The server responds identically to every submission.

JSON fields (v1):
    v, kind ("receipt" | "problem"), account, device_token, nonce (32 hex chars), ts (ISO-8601 UTC),
    receipt: qr, pin, items [{code, qty_received, damaged, expired}], cold_chain {vvm_stage,
             indicator_triggered} | null, notes, photo (base64 JPEG, EXIF stripped) | null
    problem: category, notes (<= 280 chars), qr | null
"""
from __future__ import annotations

import base64
import json
import secrets
import struct
from datetime import datetime, timezone

from nacl.public import PublicKey, SealedBox

from .settings import static_config


def buckets() -> list[int]:
    return static_config()["receipt"]["buckets"]


def pad_pin(pin: str) -> str:
    n = static_config()["receipt"]["pin_pad_length"]
    if len(pin) > n:
        raise ValueError("PIN too long")
    return pin + " " * (n - len(pin))


def pad(plaintext: bytes) -> bytes:
    need = len(plaintext) + 4
    for b in buckets():
        if need <= b:
            return struct.pack(">I", len(plaintext)) + plaintext + b"\x00" * (b - need)
    raise ValueError("payload too large for largest bucket")


def unpad(buf: bytes) -> bytes:
    if len(buf) not in buckets():
        raise ValueError("unexpected plaintext size")
    (n,) = struct.unpack(">I", buf[:4])
    if n > len(buf) - 4:
        raise ValueError("bad length prefix")
    return buf[4:4 + n]


def parse(plain_padded: bytes) -> dict:
    d = json.loads(unpad(plain_padded).decode("utf-8"))
    if d.get("v") != 1 or d.get("kind") not in ("receipt", "problem"):
        raise ValueError("unsupported payload")
    return d


# ------------------------------------------------------------------ reference device implementation

def build_receipt(account: str, device_token: str, qr: str, pin: str, items: list[dict],
                  cold_chain: dict | None = None, notes: str = "", photo_jpeg: bytes | None = None,
                  ts: datetime | None = None, nonce: str | None = None) -> dict:
    return {
        "v": 1, "kind": "receipt", "account": account, "device_token": device_token,
        "nonce": nonce or secrets.token_hex(16),
        "ts": (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "receipt": {"qr": qr, "pin": pad_pin(pin), "items": items, "cold_chain": cold_chain, "notes": notes[:500],
                    "photo": base64.b64encode(photo_jpeg).decode() if photo_jpeg else None},
    }


def build_problem(account: str, device_token: str, category: str, notes: str = "", qr: str | None = None,
                  ts: datetime | None = None) -> dict:
    return {"v": 1, "kind": "problem", "account": account, "device_token": device_token,
            "nonce": secrets.token_hex(16), "ts": (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "problem": {"category": category, "notes": notes[:280], "qr": qr}}


def seal(payload: dict, server_box_public_hex: str) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return SealedBox(PublicKey(bytes.fromhex(server_box_public_hex))).encrypt(pad(data))

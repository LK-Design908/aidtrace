"""SMS fallback receipt codes (brief §6.3.3).

A 24-byte binary code, Base45-encoded as 36 characters (all in the GSM 7-bit basic set):

    header (6 bytes, cleartext)
      u8   version (=1)
      u16  recipient SMS id
      u24  minutes since 2026-01-01 UTC (device clock), also the replay/uniqueness counter
    body (13 bytes, encrypted: XOR with HMAC-SHA256(k, "ks" || header)[:13])
      u24  shipment id
      u24  scan point id
      u16  total quantity received (sum over manifest lines, capped at 65535)
      u8   flags: bit0 any damaged/expired, bit1 cold-chain problem
      4B   PIN as packed BCD, padded with 0xF to 8 digits (fixed width)
    tag (5 bytes): HMAC-SHA256(k, "tag" || header || ciphertext)[:5]

k = per-recipient 16-byte key, HMAC(server master, recipient id), given to the device at enrolment.
Normal and duress codes are the same length and computationally indistinguishable without k,
and the PIN is never visible in the SMS. The photo follows later over data, as a normal sealed
receipt for the same scan point.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .qr import b45decode, b45encode

EPOCH = datetime(2026, 1, 1)
CODE_BYTES = 24


@dataclass
class SmsReceipt:
    sms_id: int
    minutes: int
    shipment_id: int
    scan_point_id: int
    total_qty: int
    flags: int
    pin: str

    @property
    def device_ts(self) -> datetime:
        return EPOCH + timedelta(minutes=self.minutes)


def _hm(k: bytes, *parts: bytes) -> bytes:
    return hmac.new(k, b"".join(parts), hashlib.sha256).digest()


def pack_pin(pin: str) -> bytes:
    if not (pin.isdigit() and 1 <= len(pin) <= 8):
        raise ValueError("PIN must be 1-8 digits")
    nibbles = [int(c) for c in pin] + [0xF] * (8 - len(pin))
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, 8, 2))


def unpack_pin(b: bytes) -> str:
    out = ""
    for byte in b:
        for n in (byte >> 4, byte & 0xF):
            if n == 0xF:
                return out
            if n > 9:
                raise ValueError("bad BCD")
            out += str(n)
    return out


def encode(k: bytes, r: SmsReceipt) -> str:
    """Reference encoder (the PWA implements the same in src/sms.ts)."""
    header = struct.pack(">BH", 1, r.sms_id) + r.minutes.to_bytes(3, "big")
    body = (r.shipment_id.to_bytes(3, "big") + r.scan_point_id.to_bytes(3, "big") +
            struct.pack(">HB", min(r.total_qty, 0xFFFF), r.flags) + pack_pin(r.pin))
    ks = _hm(k, b"ks", header)[:len(body)]
    ct = bytes(a ^ b for a, b in zip(body, ks))
    tag = _hm(k, b"tag", header, ct)[:5]
    return b45encode(header + ct + tag)


def peek_sms_id(code: str) -> int | None:
    try:
        raw = b45decode(code.strip().upper())
    except ValueError:
        return None
    if len(raw) != CODE_BYTES or raw[0] != 1:
        return None
    return struct.unpack(">H", raw[1:3])[0]


def decode(k: bytes, code: str) -> SmsReceipt | None:
    try:
        raw = b45decode(code.strip().upper())
    except ValueError:
        return None
    if len(raw) != CODE_BYTES or raw[0] != 1:
        return None
    header, ct, tag = raw[:6], raw[6:19], raw[19:]
    if not hmac.compare_digest(tag, _hm(k, b"tag", header, ct)[:5]):
        return None
    body = bytes(a ^ b for a, b in zip(ct, _hm(k, b"ks", header)[:13]))
    try:
        pin = unpack_pin(body[9:13])
    except ValueError:
        return None
    qty, flags = struct.unpack(">HB", body[6:9])
    return SmsReceipt(struct.unpack(">H", header[1:3])[0], int.from_bytes(header[3:6], "big"),
                      int.from_bytes(body[0:3], "big"), int.from_bytes(body[3:6], "big"), qty, flags, pin)


def minutes_now() -> int:
    return int((datetime.now(timezone.utc).replace(tzinfo=None) - EPOCH).total_seconds() // 60)

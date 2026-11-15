"""Signed QR payloads and printable labels.

Binary layout (v1), all integers big-endian:
    u8  version (=1)
    u8  key id (Ed25519 signing key)
    u32 shipment id
    u32 scan point id
    u8  scan point kind (0 dispatch, 1 handover, 2 final distribution)
    u16 expiry, in days since 2020-01-01 (UTC)
    u8  destination code length, then ASCII destination admin-2 p-code
    u8  number of manifest lines, then per line: u8 item code, varint quantity
    64  Ed25519 signature over all preceding bytes

The bytes are encoded in Base45 (RFC 9285) so the QR uses alphanumeric mode. Typical payloads
are about 100 bytes, or roughly 150 Base45 characters, which fits QR version 7 or lower at ECC
level M (target: version 10 or lower).
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nacl.exceptions import BadSignatureError

from .keys import KeyRing

EPOCH = datetime(2020, 1, 1)
KINDS = ["dispatch", "handover", "final_distribution"]
B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def b45encode(data: bytes) -> str:
    out = []
    for i in range(0, len(data) - 1, 2):
        n = data[i] * 256 + data[i + 1]
        c, n = divmod(n, 45 * 45)
        e, d = divmod(n, 45)
        out += [B45[d], B45[e], B45[c]]
    if len(data) % 2:
        e, d = divmod(data[-1], 45)
        out += [B45[d], B45[e]]
    return "".join(out)


def b45decode(s: str) -> bytes:
    vals = [B45.index(ch) for ch in s]
    out = bytearray()
    for i in range(0, len(vals), 3):
        chunk = vals[i:i + 3]
        if len(chunk) == 3:
            n = chunk[0] + chunk[1] * 45 + chunk[2] * 45 * 45
            if n > 0xFFFF:
                raise ValueError("invalid base45")
            out += bytes([n >> 8, n & 0xFF])
        elif len(chunk) == 2:
            n = chunk[0] + chunk[1] * 45
            if n > 0xFF:
                raise ValueError("invalid base45")
            out.append(n)
        else:
            raise ValueError("invalid base45 length")
    return bytes(out)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


@dataclass
class QRPayload:
    shipment_id: int
    scan_point_id: int
    kind: str
    expiry: datetime              # date (UTC) after which the code is rejected
    dest: str
    manifest: list[tuple[int, int]]   # (item code, quantity)
    key_id: int = 0


def encode(p: QRPayload, ring: KeyRing) -> str:
    dest = p.dest.encode("ascii")
    days = (p.expiry.replace(tzinfo=None) - EPOCH).days
    body = struct.pack(">BBIIBH", 1, ring.active_signing, p.shipment_id, p.scan_point_id, KINDS.index(p.kind), days)
    body += bytes([len(dest)]) + dest + bytes([len(p.manifest)])
    for code, qty in p.manifest:
        body += bytes([code]) + _varint(qty)
    _, sig = ring.sign(body)
    return b45encode(body + sig)


def decode_unverified(s: str) -> tuple[QRPayload, bytes, bytes]:
    raw = b45decode(s)
    body, sig = raw[:-64], raw[-64:]
    ver, kid, sid, spid, kind, days = struct.unpack(">BBIIBH", body[:13])
    if ver != 1:
        raise ValueError("unsupported version")
    i = 13
    n = body[i]
    dest = body[i + 1:i + 1 + n].decode("ascii")
    i += 1 + n
    lines = body[i]
    i += 1
    manifest = []
    for _ in range(lines):
        code = body[i]
        qty, i = _read_varint(body, i + 1)
        manifest.append((code, qty))
    if i != len(body):
        raise ValueError("trailing bytes")
    return QRPayload(sid, spid, KINDS[kind], EPOCH + timedelta(days=days), dest, manifest, kid), body, sig


def verify(s: str, ring: KeyRing, at: datetime | None = None) -> QRPayload | None:
    """Return the payload if the signature is valid and the code has not expired, else None."""
    try:
        p, body, sig = decode_unverified(s)
        vk = ring.verify_key(p.key_id)
        if vk is None:
            return None
        vk.verify(body, sig)
    except (ValueError, IndexError, struct.error, BadSignatureError):
        return None
    at = (at or datetime.now(timezone.utc)).replace(tzinfo=None)
    if at.date() > p.expiry.date():
        return None
    return p


def qr_version(s: str) -> int:
    import segno
    return segno.make(s, error="m", micro=False).version


def label_pdf(entries: list[dict]) -> bytes:
    """One A6 label per scan point: QR, shipment code, scan point, destination, manifest.

    entries: [{qr, shipment_code, scan_kind, seq, dest, site, manifest: [(label, qty)], window}]
    """
    import segno
    from fpdf import FPDF

    pdf = FPDF(format=(105, 148), unit="mm")     # A6
    pdf.set_auto_page_break(False)
    for e in entries:
        pdf.add_page()
        img = io.BytesIO()
        segno.make(e["qr"], error="m", micro=False).save(img, kind="png", scale=8, border=2)
        img.seek(0)
        pdf.image(img, x=14, y=6, w=77)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_xy(6, 86)
        pdf.cell(93, 6, f"{e['shipment_code']}  |  Scan {e['seq']}: {e['scan_kind'].replace('_', ' ')}", align="C")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_xy(6, 93)
        pdf.multi_cell(93, 4, f"Destination: {e['site']} ({e['dest']})\nDelivery window: {e['window']}\n" +
                       "; ".join(f"{lab} x{q}" for lab, q in e["manifest"]))
        # Fallback for a broken camera: the same signed code as text, typed into the recipient app.
        pdf.set_font("Courier", "", 6.5)
        pdf.set_xy(6, pdf.get_y() + 2)
        pdf.multi_cell(93, 2.8, "If scanning fails, type this code:\n" + e["qr"])
    return bytes(pdf.output())

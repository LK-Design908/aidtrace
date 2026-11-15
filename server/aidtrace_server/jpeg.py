"""Server-side JPEG metadata stripping (defence in depth: the device strips EXIF first).

Removes APPn segments (EXIF, XMP, ICC, JFIF thumbnails, etc.) and COM comments, keeping only the
segments needed to decode the image. Pure Python, no image library.
"""
from __future__ import annotations

KEEP = {0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF, 0xDB, 0xDD}


def strip_metadata(data: bytes) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError("corrupt JPEG marker")
        marker = data[i + 1]
        if marker == 0xD9:                       # EOI
            out += data[i:i + 2]
            return bytes(out)
        if marker == 0xDA:                       # SOS: rest is entropy-coded data up to EOI
            out += data[i:]
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + seg_len]
        if marker in KEEP:
            out += seg
        i += 2 + seg_len
    raise ValueError("JPEG without SOS")


def has_metadata(data: bytes) -> bool:
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        m = data[i + 1]
        if m == 0xDA:
            return False
        if 0xE0 <= m <= 0xEF or m == 0xFE:
            return True
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return False

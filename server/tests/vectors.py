"""Cross-language test vectors (Python reference -> recipient/src/test-vectors.json).

    python -I -m tests.vectors        (from server/) regenerates the file
The Python test (test_vectors.py) recomputes everything and fails if the file has drifted; the
JS test (recipient/src/protocol.test.ts) checks the PWA produces/accepts exactly these values.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from nacl.public import PrivateKey
from nacl.signing import SigningKey

from aidtrace_server import qr as qrmod
from aidtrace_server import sms as smsmod

OUT = Path(__file__).resolve().parents[2] / "recipient" / "src" / "test-vectors.json"


class _Ring:
    def __init__(self, sk: SigningKey):
        self.sk, self.active_signing = sk, 1

    def sign(self, msg: bytes):
        return 1, self.sk.sign(msg).signature


def build() -> dict:
    sk = SigningKey(bytes(range(1, 33)))
    payload = qrmod.QRPayload(4321, 98765, "final_distribution", datetime(2027, 3, 1), "SO2201",
                              [(2, 10), (3, 400), (5, 70000)])
    qr = qrmod.encode(payload, _Ring(sk))
    box = PrivateKey(bytes(range(33, 65)))
    sms_key = bytes(range(100, 116))
    sms_in = dict(sms_id=513, shipment_id=4321, scan_point_id=98765, total_qty=410, flags=1, pin="2468",
                  minutes=int((datetime(2026, 11, 15, 10, 0) - smsmod.EPOCH).total_seconds() // 60))
    r = smsmod.SmsReceipt(sms_in["sms_id"], sms_in["minutes"], sms_in["shipment_id"], sms_in["scan_point_id"],
                          sms_in["total_qty"], sms_in["flags"], sms_in["pin"])
    r_duress = smsmod.SmsReceipt(**{**r.__dict__, "pin": "13579864"})
    return {
        "base45": {"bytes_hex": b"AidTrace!".hex(), "text": qrmod.b45encode(b"AidTrace!")},
        "qr": {"signing_keys": {"1": sk.verify_key.encode().hex()}, "code": qr, "shipment_id": 4321,
               "scan_point_id": 98765, "kind": "final_distribution", "expiry": "2027-03-01", "dest": "SO2201",
               "manifest": [[2, 10], [3, 400], [5, 70000]]},
        "box": {"private_hex": box.encode().hex(), "public_hex": box.public_key.encode().hex()},
        "sms": {"key_hex": sms_key.hex(), "input": {**sms_in, "at": "2026-11-15T10:00:00Z"},
                "code": smsmod.encode(sms_key, r), "code_duress": smsmod.encode(sms_key, r_duress)},
        "buckets": [2048, 8192, 32768, 163840], "pin_pad": 12,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")

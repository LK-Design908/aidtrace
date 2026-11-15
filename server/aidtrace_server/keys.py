"""Server key management: Ed25519 signing keys (QR codes) and X25519 box keys (sealed receipts).

Keys live outside the repo in AIDTRACE_KEY_DIR:
    signing_<kid>.key   32-byte Ed25519 seed (hex)
    box_<kid>.key       32-byte X25519 private key (hex)
    keyring.json        {"active_signing": kid, "active_box": kid, "retired": [...]}

Rotation: `aidtrace-server rotate-keys` creates new keys and makes them active. Old signing keys
still verify QR codes already printed, and old box keys still decrypt receipts queued offline,
until they are retired. Enrolled devices fetch the new public keys at next sync (/api/recipient/keys).
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey


@dataclass
class KeyRing:
    dir: Path
    signing: dict[int, SigningKey]
    box: dict[int, PrivateKey]
    active_signing: int
    active_box: int
    sms_master: bytes = b""

    def sms_key(self, recipient_id: int) -> bytes:
        """Per-recipient 16-byte SMS key = HMAC-SHA256(master, "sms" || id). Not stored per device."""
        import hashlib
        import hmac
        return hmac.new(self.sms_master, b"sms" + recipient_id.to_bytes(4, "big"), hashlib.sha256).digest()[:16]

    def sign(self, msg: bytes) -> tuple[int, bytes]:
        return self.active_signing, self.signing[self.active_signing].sign(msg).signature

    def verify_key(self, kid: int) -> VerifyKey | None:
        k = self.signing.get(kid)
        return k.verify_key if k else None

    def open_sealed(self, blob: bytes) -> bytes | None:
        """Try the active box key first, then older ones (receipts queued before rotation)."""
        for kid in [self.active_box] + [k for k in self.box if k != self.active_box]:
            try:
                return SealedBox(self.box[kid]).decrypt(blob)
            except Exception:
                continue
        return None

    def public_info(self) -> dict:
        return {
            "signing_keys": {str(k): v.verify_key.encode().hex() for k, v in self.signing.items()},
            "active_signing": self.active_signing,
            "box_public_key": self.box[self.active_box].public_key.encode().hex(),
            "active_box": self.active_box,
        }

    def box_public(self) -> PublicKey:
        return self.box[self.active_box].public_key


def _write_secret(p: Path, data: str) -> None:
    p.write_text(data, encoding="ascii")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def generate(key_dir: Path, rotate: bool = False) -> KeyRing:
    key_dir.mkdir(parents=True, exist_ok=True)
    ring_file = key_dir / "keyring.json"
    ring = json.loads(ring_file.read_text()) if ring_file.exists() else {"active_signing": 0, "active_box": 0, "retired": []}
    if ring_file.exists() and not rotate:
        return load(key_dir)
    kid = (max(ring["active_signing"], ring["active_box"]) + 1) if ring_file.exists() else 1
    if kid > 255:
        raise RuntimeError("key id space exhausted; retire old keys")
    _write_secret(key_dir / f"signing_{kid}.key", SigningKey.generate().encode().hex())
    _write_secret(key_dir / f"box_{kid}.key", PrivateKey.generate().encode().hex())
    ring.update(active_signing=kid, active_box=kid)
    ring_file.write_text(json.dumps(ring, indent=2))
    return load(key_dir)


def load(key_dir: Path) -> KeyRing:
    ring_file = key_dir / "keyring.json"
    if not ring_file.exists():
        raise FileNotFoundError(f"No keys in {key_dir}. Run `aidtrace-server setup-keys` first.")
    ring = json.loads(ring_file.read_text())
    retired = set(ring.get("retired", []))
    signing = {int(p.stem.split("_")[1]): SigningKey(bytes.fromhex(p.read_text().strip()))
               for p in key_dir.glob("signing_*.key") if int(p.stem.split("_")[1]) not in retired}
    box = {int(p.stem.split("_")[1]): PrivateKey(bytes.fromhex(p.read_text().strip()))
           for p in key_dir.glob("box_*.key") if int(p.stem.split("_")[1]) not in retired}
    sms_file = key_dir / "sms_master.key"
    if not sms_file.exists():
        _write_secret(sms_file, secrets.token_hex(32))
    return KeyRing(key_dir, signing, box, ring["active_signing"], ring["active_box"],
                   bytes.fromhex(sms_file.read_text().strip()))


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)

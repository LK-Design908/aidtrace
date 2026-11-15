from __future__ import annotations

import base64
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from nacl.public import PublicKey, SealedBox

from aidtrace_server.app import create_app
from aidtrace_server.models import User
from aidtrace_server.security import hash_secret
from aidtrace_server.settings import from_env

PW = "correct horse battery"


@pytest.fixture()
def app(tmp_path):
    st = from_env(mode="live", data_dir=tmp_path / "live", key_dir=tmp_path / "keys", submit_min_ms=0)
    a = create_app(st, generate_keys=True)
    s = a.state.ctx.sessions()
    for name, role in [("admin", "hq_admin"), ("sfp", "security_focal_point"), ("field", "field_staff"),
                       ("field2", "field_staff")]:
        s.add(User(username=name, password_hash=hash_secret(PW), role=role))
    s.commit()
    s.close()
    return a


@pytest.fixture()
def client(app):
    return TestClient(app)


def login(client, name: str) -> dict:
    r = client.post("/api/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def hdr(client):
    return {n: login(client, n) for n in ("admin", "sfp", "field", "field2")}


def risk_units() -> tuple[str, str]:
    """(adm2 p-code of a low-band unit, adm2 p-code of a high/severe-band unit) from pipeline output."""
    from aidtrace_server.settings import _repo_root
    root = _repo_root() / "data" / "processed"
    r = pd.read_parquet(root / "risk" / "som_risk.parquet")
    r = r[r["horizon"] == 1]
    cw = pd.read_csv(root / "spine" / "som_crosswalk.csv")
    low = r[r["band"] == "low"]["unit_id"].iloc[0]
    high = r[r["band"].isin(["high", "severe"])]["unit_id"].iloc[0]
    pc = lambda u: cw.loc[cw["unit_id"] == u, "adm2_pcode"].iloc[0]  # noqa: E731
    return pc(low), pc(high)


class Device:
    """Reference recipient device (the PWA implements the same protocol in Milestone 6)."""

    def __init__(self, client, account: str, code: str, normal: str, duress: str):
        keys = client.get("/api/recipient/keys").json()
        sealed = SealedBox(PublicKey(bytes.fromhex(keys["box_public_key"]))).encrypt(
            json.dumps({"normal": normal, "duress": duress}).encode())
        r = client.post("/api/recipient/enrol", json={"account": account, "enrolment_code": code,
                                                       "sealed_pins": base64.b64encode(sealed).decode()})
        assert r.status_code == 200, r.text
        self.state = r.json()          # everything the device stores
        self.client = client

    def seal_receipt(self, qr: str, pin: str, items: list[dict], **kw) -> bytes:
        from aidtrace_server.sealed import build_receipt, seal
        p = build_receipt(self.state["account"], self.state["device_token"], qr, pin, items, **kw)
        return seal(p, self.state["box_public_key"])

    def submit(self, blob: bytes):
        return self.client.post("/api/recipient/submit", content=blob,
                                headers={"Content-Type": "application/octet-stream"})


def make_recipient(client, hdr, pcode: str, normal="2468", duress="1357") -> Device:
    r = client.post("/api/recipients", json={"site_name": f"Clinic {pcode}", "adm2_pcode": pcode}, headers=hdr["admin"])
    assert r.status_code == 201, r.text
    return Device(client, r.json()["account"], r.json()["enrolment_code"], normal, duress)


def jpeg_with_exif() -> bytes:
    exif = b"Exif\x00\x00" + b"GPS-LAT-LON-SECRET" * 4
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    dqt = b"\xff\xdb" + (67).to_bytes(2, "big") + bytes(65)
    sos = b"\xff\xda" + (8).to_bytes(2, "big") + bytes(6) + b"\x12\x34\x56" + b"\xff\xd9"
    return b"\xff\xd8" + app1 + dqt + sos


BETA_COUNTRIES = ["DJI", "SOM", "SDN", "SSD", "KEN"]


def served_units() -> int:
    """Number of modelling units across the countries the beta serves."""
    from aidtrace_server.settings import _repo_root
    root = _repo_root() / "data" / "processed" / "spine"
    return sum(len(pd.read_csv(root / f"{c.lower()}_crosswalk.csv")["unit_id"].unique()) for c in BETA_COUNTRIES)

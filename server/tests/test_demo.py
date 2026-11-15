"""Milestone 7: demo mode end to end (brief §8) and demo isolation (brief §2.4).

create shipment -> scan -> submit normal -> confirmed; submit duress -> appears confirmed to field
staff, alert visible to focal point only; submit wrong PIN -> flagged. Plus: demo routes do not
exist in live mode, ACLED in demo only as aggregates, and the timing padding is applied.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from nacl.public import PublicKey, SealedBox

from aidtrace_server.app import create_app
from aidtrace_server.risk import acled_level_for
from aidtrace_server.sealed import build_receipt, seal
from aidtrace_server.seed import seed
from aidtrace_server.settings import from_env

from .test_server import ITEMS, strip_ids


@pytest.fixture()
def demo(tmp_path):
    st = from_env(mode="demo", data_dir=tmp_path / "demo", key_dir=tmp_path / "keys", submit_min_ms=0)
    seed(st, password="synthetic-demo-pass")
    app = create_app(st, generate_keys=True)
    return app, TestClient(app)


def role(c, r):
    res = c.post("/api/demo/login", json={"role": r})
    assert res.status_code == 200, res.text
    return {"Authorization": "Bearer " + res.json()["token"]}


def demo_phone(c):
    """What the PWA does: 'Set up a demo phone', then normal enrolment with the suggested PINs."""
    d = c.post("/api/demo/device", json={}).json()
    keys = c.get("/api/recipient/keys").json()
    pins = d["suggested_pins"]
    sealed = SealedBox(PublicKey(bytes.fromhex(keys["box_public_key"]))).encrypt(
        json.dumps({"normal": pins["normal"], "duress": pins["safety"]}).encode())
    e = c.post("/api/recipient/enrol", json={"account": d["account"], "enrolment_code": d["enrolment_code"],
                                             "sealed_pins": base64.b64encode(sealed).decode()}).json()
    return d, e, pins


def test_demo_end_to_end(demo):
    app, c = demo
    field, sec = role(c, "field_staff"), role(c, "security_focal_point")
    d, dev, pins = demo_phone(c)
    seen = []
    for pin in (pins["normal"], pins["safety"], "0000"):
        sh = c.post("/api/shipments", headers=field, json={
            "origin": "Demo warehouse", "dest_adm2_pcode": d["adm2_pcode"], "dest_site": "Demo Health Centre (synthetic)",
            "window_start": str(date.today()), "window_end": str(date.today() + timedelta(days=3)),
            "items": [{"item_code": 2, "quantity": 10}, {"item_code": 3, "quantity": 40}],
            "recipients": [d["account"]], "scan_points": ["final_distribution"]}).json()
        qr = c.get(f"/api/shipments/{sh['id']}/qr", headers=field).json()[-1]["qr"]
        assert c.get(f"/api/shipments/{sh['id']}/qr/1.svg", headers=field).text.startswith("<?xml")
        r = c.post("/api/recipient/submit", content=seal(build_receipt(dev["account"], dev["device_token"], qr, pin, ITEMS),
                                                         dev["box_public_key"]))
        seen.append((sh["id"], r.status_code, r.text))
    assert len({(code, text) for _, code, text in seen}) == 1, "phone sees the same response every time"
    (normal_id, *_), (duress_id, *_), (wrong_id, *_) = seen

    vn = c.get(f"/api/shipments/{normal_id}", headers=field).json()
    vd = c.get(f"/api/shipments/{duress_id}", headers=field).json()
    vw = c.get(f"/api/shipments/{wrong_id}", headers=field).json()
    assert vn["status"] == vd["status"] == "delivered"
    assert strip_ids(vn) == strip_ids(vd), "duress identical to normal for field staff"
    assert vw["scan_points"][-1]["status"] == "unverified_pin_mismatch" and "pin_failures" in vw["flags"]

    assert c.get(f"/api/shipments/{duress_id}", headers=sec).json()["duress"] is True
    alerts = c.get("/api/alerts", headers=sec).json()
    assert any(a["shipment_id"] == duress_id for a in alerts)
    assert c.get("/api/alerts", headers=field).status_code == 403


def test_demo_seed_is_synthetic_and_isolated(demo, tmp_path):
    app, c = demo
    st = app.state.ctx.settings
    assert st.mode == "demo" and st.data_dir.name == "demo"
    hq = role(c, "hq_admin")
    assert all("(synthetic)" in r["site_name"] for r in c.get("/api/recipients", headers=hq).json())
    assert all("synthetic" in (r.get("free_text") or "synthetic") for r in c.get("/api/reports", headers=hq).json())
    # ACLED in demo (owner decision 2026-11-15): aggregated district-month counts for signed-in staff only,
    # never event-level, never to recipients or unauthenticated callers, never in risk scores.
    st.acled_display = "event_level"
    assert acled_level_for(st, "security_focal_point") == "aggregated"
    assert acled_level_for(st, "field_staff") == "aggregated"
    assert acled_level_for(st, "recipient") == "none" and acled_level_for(st, None) == "none"
    r = c.get("/api/acled", headers=hq)
    assert r.status_code == 200 and r.json()["level"] == "aggregated" and "ACLED" in r.json()["attribution"]
    assert all("latitude" not in row and "notes" not in row for row in r.json()["rows"])
    assert c.get("/api/acled").status_code == 401
    assert "acled" not in c.get("/api/risk", headers=hq).text.lower()
    st.demo_acled_display = "none"                      # switchable off with AIDTRACE_DEMO_ACLED=none
    assert c.get("/api/acled", headers=hq).status_code == 403


def test_demo_routes_absent_in_live(tmp_path):
    live = TestClient(create_app(from_env(mode="live", data_dir=tmp_path / "live", key_dir=tmp_path / "k"), generate_keys=True))
    assert live.post("/api/demo/login", json={"role": "hq_admin"}).status_code in (404, 405)
    assert live.post("/api/demo/device", json={}).status_code in (404, 405)
    assert live.get("/api/health").json()["mode"] == "live"


def test_seed_refuses_live(tmp_path):
    with pytest.raises(SystemExit):
        seed(from_env(mode="live", data_dir=tmp_path / "live", key_dir=tmp_path / "k"))


def test_submission_timing_is_padded(tmp_path):
    st = from_env(mode="demo", data_dir=tmp_path / "demo", key_dir=tmp_path / "keys", submit_min_ms=600)
    seed(st, password="synthetic-demo-pass")
    app = create_app(st, generate_keys=True)
    c = TestClient(app)
    field = role(c, "field_staff")
    d, dev, pins = demo_phone(c)
    times = []
    for pin in (pins["normal"], pins["safety"], "0000"):
        sh = c.post("/api/shipments", headers=field, json={
            "origin": "x", "dest_adm2_pcode": d["adm2_pcode"], "dest_site": "y", "window_start": str(date.today()),
            "window_end": str(date.today()), "items": [{"item_code": 1, "quantity": 1}], "recipients": [d["account"]],
            "scan_points": ["final_distribution"]}).json()
        qr = c.get(f"/api/shipments/{sh['id']}/qr", headers=field).json()[-1]["qr"]
        blob = seal(build_receipt(dev["account"], dev["device_token"], qr, pin, ITEMS), dev["box_public_key"])
        t0 = time.monotonic()
        c.post("/api/recipient/submit", content=blob)
        times.append(time.monotonic() - t0)
    t0 = time.monotonic()
    c.post("/api/recipient/submit", content=b"garbage")              # rejected early: still padded
    times.append(time.monotonic() - t0)
    assert min(times) >= 0.6, times
    assert max(times) - min(times) < 0.25, f"timing spread too large: {times}"

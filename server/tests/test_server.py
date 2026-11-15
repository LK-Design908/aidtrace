"""Milestone 4 server tests (brief §8): auth, QR, sealed receipts and duress design, two-person
confirmation, field reports, ACLED gating, risk API, retention."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from aidtrace_server import qr as qrmod
from aidtrace_server.jpeg import has_metadata, strip_metadata
from aidtrace_server.models import AuditLog, DuressAlert, Receipt, SecurityEvent, Session_, User
from aidtrace_server.retention import purge
from aidtrace_server.risk import acled_layer, acled_level_for
from aidtrace_server.settings import from_env

from .conftest import PW, jpeg_with_exif, login, make_recipient, risk_units, served_units

TODAY = date.today()


def shipment(client, hdr, pcode, recips, scans=("dispatch", "final_distribution"), **kw):
    body = {"origin": "Mogadishu warehouse", "dest_adm2_pcode": pcode, "dest_site": "Health centre",
            "window_start": str(TODAY - timedelta(days=1)), "window_end": str(TODAY + timedelta(days=7)),
            "items": [{"item_code": 2, "quantity": 10, "batch": "B1", "expiry": str(TODAY + timedelta(days=400))},
                      {"item_code": 3, "quantity": 40}],
            "recipients": [d.state["account"] for d in recips], "scan_points": list(scans), **kw}
    r = client.post("/api/shipments", json=body, headers=hdr["field"])
    assert r.status_code == 201, r.text
    sh = r.json()
    qrs = client.get(f"/api/shipments/{sh['id']}/qr", headers=hdr["field"]).json()
    return sh, qrs


ITEMS = [{"code": 2, "qty_received": 10, "damaged": 0, "expired": 0},
         {"code": 3, "qty_received": 40, "damaged": 0, "expired": 0}]


def strip_ids(v):
    """Shipment view without identifiers/timestamps, to compare normal vs duress views."""
    drop = {"id", "code", "receipt_id", "received_at", "device_ts", "created_at", "first_receipt_at",
            "scan_point_id", "recipient", "recipients", "window_start", "window_end"}
    if isinstance(v, dict):
        return {k: strip_ids(x) for k, x in v.items() if k not in drop}
    if isinstance(v, list):
        return [strip_ids(x) for x in v]
    return v


# ------------------------------------------------------------------ auth

def test_login_lockout_and_rbac(client, hdr):
    assert client.get("/api/risk").status_code == 401
    assert client.get("/api/shipments").status_code == 401
    assert client.get("/api/acled").status_code == 401
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "field2", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "field2", "password": PW}).status_code == 423
    assert client.get("/api/alerts", headers=hdr["field"]).status_code == 403
    assert client.get("/api/alerts", headers=hdr["sfp"]).status_code == 200
    assert client.post("/api/users", json={"username": "x" * 5, "password": "y" * 12, "role": "field_staff"},
                       headers=hdr["field"]).status_code == 403


def test_session_idle_timeout(app, client, hdr):
    s = app.state.ctx.sessions()
    for sess in s.scalars(select(Session_)):
        sess.last_seen = sess.last_seen - timedelta(minutes=31)
    s.commit()
    assert client.get("/api/auth/me", headers=hdr["admin"]).status_code == 401


# ------------------------------------------------------------------ QR

def test_qr_sign_verify_size_tamper_expiry(app):
    ring = app.state.ctx.keys
    p = qrmod.QRPayload(123456, 987654, "handover", datetime(2027, 6, 1), "SO2201",
                        [(1, 5), (2, 120), (3, 4000), (4, 70000), (5, 12), (6, 300)])
    s = qrmod.encode(p, ring)
    assert qrmod.qr_version(s) <= 10, f"QR version {qrmod.qr_version(s)}"
    got = qrmod.verify(s, ring, at=datetime(2027, 1, 1))
    assert got and got.manifest == p.manifest and got.dest == "SO2201" and got.kind == "handover"
    tampered = s[:20] + ("0" if s[20] != "0" else "1") + s[21:]
    assert qrmod.verify(tampered, ring, at=datetime(2027, 1, 1)) is None
    assert qrmod.verify(s, ring, at=datetime(2027, 6, 2)) is None          # expired
    assert qrmod.verify("NOT A CODE", ring) is None


def test_labels_pdf(client, hdr):
    low, _ = risk_units()
    d = make_recipient(client, hdr, low)
    sh, _ = shipment(client, hdr, low, [d])
    r = client.get(f"/api/shipments/{sh['id']}/labels.pdf", headers=hdr["field"])
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


# ------------------------------------------------------------------ receipts and duress

def test_end_to_end_normal_duress_mismatch(app, client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low, normal="2468", duress="1357")
    sh1, q1 = shipment(client, hdr, low, [dev])
    sh2, q2 = shipment(client, hdr, low, [dev])
    sh3, q3 = shipment(client, hdr, low, [dev])
    final = lambda q: q[-1]["qr"]  # noqa: E731

    responses = [dev.submit(dev.seal_receipt(final(q1), "2468", ITEMS)),
                 dev.submit(dev.seal_receipt(final(q2), "1357", ITEMS)),
                 dev.submit(dev.seal_receipt(final(q3), "9999", ITEMS))]
    assert all(r.status_code == 202 for r in responses)
    assert len({r.text for r in responses}) == 1, "responses must be identical"

    v1 = client.get(f"/api/shipments/{sh1['id']}", headers=hdr["field"]).json()
    v2 = client.get(f"/api/shipments/{sh2['id']}", headers=hdr["field"]).json()
    v3 = client.get(f"/api/shipments/{sh3['id']}", headers=hdr["field"]).json()
    assert v1["scan_points"][-1]["status"] == "verified"
    assert strip_ids(v1) == strip_ids(v2), "duress must look identical to normal for field staff"
    assert "duress" not in json.dumps(v2) and "pin_class" not in json.dumps(v2)
    assert v3["scan_points"][-1]["status"] == "unverified_pin_mismatch" and "pin_failures" in v3["flags"]

    s2 = client.get(f"/api/shipments/{sh2['id']}", headers=hdr["sfp"]).json()
    assert s2["duress"] is True and s2["scan_points"][-1]["receipts"][0]["pin_class"] == "duress"
    alerts = client.get("/api/alerts", headers=hdr["sfp"]).json()
    assert len(alerts) == 1 and alerts[0]["shipment_id"] == sh2["id"]
    assert client.get("/api/alerts", headers=hdr["field"]).status_code == 403
    s = app.state.ctx.sessions()
    assert s.scalar(select(AuditLog).where(AuditLog.action == "view_duress_alerts")) is not None
    a = s.scalars(select(DuressAlert)).one()
    assert a.notified.get("outbox") is True                      # background notification ran
    assert (app.state.ctx.settings.outbox_dir / f"duress_{a.id}.json").exists()


def test_payload_sizes_identical_and_no_pin_material(app, client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low, normal="2468", duress="13579864")   # different PIN lengths
    sh, q = shipment(client, hdr, low, [dev])
    photo = jpeg_with_exif()
    sizes = {len(dev.seal_receipt(q[-1]["qr"], pin, ITEMS, photo_jpeg=photo, notes="ok")) for pin in
             ("2468", "13579864", "0000")}
    assert len(sizes) == 1, f"sealed sizes differ: {sizes}"
    # The device's stored state holds no PIN in any form.
    blob = json.dumps(dev.state)
    for pin in ("2468", "13579864"):
        assert pin not in blob
    assert not any("pin" in k.lower() for k in dev.state)
    # Server stores only argon2 hashes.
    db_bytes = (app.state.ctx.settings.data_dir / "aidtrace_live.db").read_bytes()
    assert b"13579864" not in db_bytes and b"$argon2id$" in db_bytes


def test_replay_rejected(app, client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    blob = dev.seal_receipt(q[-1]["qr"], "2468", ITEMS)
    r1, r2 = dev.submit(blob), dev.submit(blob)
    assert r1.text == r2.text
    s = app.state.ctx.sessions()
    assert len(s.scalars(select(Receipt)).all()) == 1
    assert s.scalar(select(SecurityEvent).where(SecurityEvent.kind == "replay")) is not None


def test_forged_qr_and_unassigned_recipient_rejected(app, client, hdr):
    low, _ = risk_units()
    dev, other = make_recipient(client, hdr, low), make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    forged = q[-1]["qr"][:-6] + "AAAAAA"
    dev.submit(dev.seal_receipt(forged, "2468", ITEMS))
    other.submit(other.seal_receipt(q[-1]["qr"], "2468", ITEMS))
    s = app.state.ctx.sessions()
    assert len(s.scalars(select(Receipt)).all()) == 0
    kinds = {e.kind for e in s.scalars(select(SecurityEvent))}
    assert {"bad_qr", "not_assigned"} <= kinds


def test_two_person_confirmation_high_risk(client, hdr):
    _, high = risk_units()
    a, b = make_recipient(client, hdr, high), make_recipient(client, hdr, high, normal="1111", duress="2222")
    r = client.post("/api/shipments", headers=hdr["field"], json={
        "origin": "x", "dest_adm2_pcode": high, "dest_site": "y", "window_start": str(TODAY), "window_end": str(TODAY),
        "items": [{"item_code": 1, "quantity": 1}], "recipients": [a.state["account"], b.state["account"]],
        "scan_points": ["final_distribution"]})
    assert r.status_code == 422, "high-risk destination needs at least two scan points"
    sh, q = shipment(client, hdr, high, [a, b])
    assert sh["two_person_required"] is True
    a.submit(a.seal_receipt(q[-1]["qr"], "2468", ITEMS))
    v = client.get(f"/api/shipments/{sh['id']}", headers=hdr["field"]).json()
    assert v["scan_points"][-1]["status"] == "partially_verified"
    a.submit(a.seal_receipt(q[-1]["qr"], "2468", ITEMS))           # same person twice does not count
    assert client.get(f"/api/shipments/{sh['id']}", headers=hdr["field"]).json()["scan_points"][-1]["status"] == "partially_verified"
    b.submit(b.seal_receipt(q[-1]["qr"], "2222", ITEMS))           # second person, under duress
    v = client.get(f"/api/shipments/{sh['id']}", headers=hdr["field"]).json()
    assert v["scan_points"][-1]["status"] == "verified" and v["status"] == "delivered"


def test_photo_exif_stripped_server_side(app, client, hdr):
    assert has_metadata(jpeg_with_exif()) and not has_metadata(strip_metadata(jpeg_with_exif()))
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS, photo_jpeg=jpeg_with_exif()))
    photos = list(app.state.ctx.settings.photo_dir.glob("*.jpg"))
    assert len(photos) == 1 and b"GPS-LAT-LON-SECRET" not in photos[0].read_bytes()


def test_problem_report_from_recipient(client, hdr):
    from aidtrace_server.sealed import build_problem, seal
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    dev.submit(seal(build_problem(dev.state["account"], dev.state["device_token"], "payment_demand", "asked for fee"),
                    dev.state["box_public_key"]))
    reps = client.get("/api/reports", headers=hdr["sfp"]).json()
    assert reps and reps[0]["type"] == "extortion_at_handover" and reps[0]["reporter_role"] == "recipient"
    assert dev.state["pseah_contact"] is not None


# ------------------------------------------------------------------ field reports

def test_field_report_text_visibility_and_audit(app, client, hdr):
    low, _ = risk_units()
    r = client.post("/api/reports", headers=hdr["field"], json={
        "type": "extortion_at_handover", "incident_date": str(TODAY), "adm2_pcode": low,
        "free_text": "Checkpoint demanded 10%", "severity": 4})
    assert r.status_code == 201 and r.json()["free_text"] == "Checkpoint demanded 10%"   # own report
    rid = r.json()["id"]
    other = client.get(f"/api/reports/{rid}", headers=hdr["field2"]).json()
    assert "free_text" not in other and other["has_text"] is True
    sfp = client.get(f"/api/reports/{rid}", headers=hdr["sfp"]).json()
    assert sfp["free_text"] == "Checkpoint demanded 10%"
    s = app.state.ctx.sessions()
    assert s.scalar(select(AuditLog).where(AuditLog.action == "view_field_report_detail")) is not None
    assert client.patch(f"/api/reports/{rid}/review", json={"review_status": "verified"}, headers=hdr["field"]).status_code == 403
    assert client.patch(f"/api/reports/{rid}/review", json={"review_status": "verified"}, headers=hdr["sfp"]).json()["review_status"] == "verified"


# ------------------------------------------------------------------ ACLED gating

@pytest.mark.parametrize("level,role,expected", [
    ("none", "hq_admin", "none"), ("none", "field_staff", "none"),
    ("aggregated", "field_staff", "aggregated"), ("aggregated", "security_focal_point", "aggregated"),
    ("aggregated", "recipient", "none"), ("aggregated", None, "none"),
    ("event_level", "security_focal_point", "event_level"), ("event_level", "field_staff", "aggregated"),
    ("event_level", "recipient", "none"),
])
def test_acled_levels(tmp_path, level, role, expected):
    st = from_env(mode="live", data_dir=tmp_path / "live", key_dir=tmp_path / "k", acled_display=level)
    assert acled_level_for(st, role) == expected
    out = acled_layer(st, role)
    if expected == "none":
        assert out is None
    else:
        assert out["level"] == expected and "attribution" in out
        assert all(set(r) <= {"unit_id", "month", "acled_events", "acled_fatalities", "acled_events_battles",
                              "acled_events_vac", "acled_events_remote", "acled_events_riots", "acled_events_protests",
                              "acled_events_strategic"} for r in out["rows"]), "no coordinates or notes"


def test_acled_endpoint_disabled_by_default(client, hdr):
    for role in ("admin", "sfp", "field"):
        assert client.get("/api/acled", headers=hdr[role]).status_code == 403


# ------------------------------------------------------------------ risk API

def test_risk_api_dates_disclaimer_no_acled(client, hdr):
    r = client.get("/api/risk?horizon=1", headers=hdr["field"])
    assert r.status_code == 200
    d = r.json()
    assert "estimates" in d["disclaimer"] and d["resolution"] == "admin-2"
    u = [x for x in d["units"] if "prob" in x]
    assert len(u) == served_units() and all("data_last_month" in x and "stale_months" in x for x in u)
    assert all("withhold" not in x["recommendation"].lower() for x in d["units"])
    assert "acled" not in r.text.lower()
    assert {x["country"] for x in d["units"]} == {"DJI", "SOM", "SDN", "SSD", "KEN"}, "beta serves five countries"
    assert "ETH" not in {x["country"] for x in d["units"]}
    one = client.get(f"/api/risk/{u[0]['unit_id']}", headers=hdr["field"]).json()
    assert "forecast_3m" in one and "recommendation" in one


# ------------------------------------------------------------------ retention, modes

def test_retention_purges_content_keeps_status(app, client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS, photo_jpeg=jpeg_with_exif(), notes="box torn"))
    s = app.state.ctx.sessions()
    rc = s.scalars(select(Receipt)).one()
    rc.server_received_at = rc.server_received_at - timedelta(days=181)
    s.commit()
    out = purge(app.state.ctx.sessions, app.state.ctx.settings)
    assert out["receipts_purged"] == 1
    assert not list(app.state.ctx.settings.photo_dir.glob("*.jpg"))
    v = client.get(f"/api/shipments/{sh['id']}", headers=hdr["field"]).json()
    assert v["scan_points"][-1]["status"] == "verified"


def test_demo_cannot_use_live_dir(tmp_path):
    st = from_env(mode="demo", data_dir=tmp_path / "live", key_dir=tmp_path / "k")
    with pytest.raises(RuntimeError):
        st.ensure_dirs()

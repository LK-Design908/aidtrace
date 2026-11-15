"""Cross-language vectors stay in sync; SMS codes: equal length for normal/duress, tamper-proof,
end-to-end through the mock gateway (normal, duress, wrong PIN, replay)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from aidtrace_server import sms as smsmod
from aidtrace_server.models import DuressAlert, Receipt, SecurityEvent

from .conftest import make_recipient, risk_units
from .test_server import ITEMS, shipment
from .vectors import OUT, build


def test_vectors_file_matches_reference():
    assert json.loads(OUT.read_text(encoding="utf-8")) == build(), "run `python -I -m tests.vectors` from server/"


def test_sms_codes_equal_length_and_roundtrip():
    v = build()["sms"]
    k = bytes.fromhex(v["key_hex"])
    assert len(v["code"]) == len(v["code_duress"]) == 36
    r = smsmod.decode(k, v["code"])
    assert r and r.pin == "2468" and r.total_qty == 410 and r.shipment_id == 4321
    assert smsmod.decode(k, v["code_duress"]).pin == "13579864"
    tampered = v["code"][:10] + ("0" if v["code"][10] != "0" else "1") + v["code"][11:]
    assert smsmod.decode(k, tampered) is None
    assert smsmod.decode(bytes(16), v["code"]) is None


def _code(app, dev, sh, q, pin, minutes=None):
    p = app.state.ctx.keys
    from aidtrace_server.qr import decode_unverified
    payload, _, _ = decode_unverified(q[-1]["qr"])
    k = p.sms_key(dev.state["sms_id"])
    assert k.hex() == __import__("base64").b64decode(dev.state["sms_key"]).hex()
    return smsmod.encode(k, smsmod.SmsReceipt(dev.state["sms_id"], minutes or smsmod.minutes_now(), payload.shipment_id,
                                              payload.scan_point_id, 50, 0, pin))


def test_sms_gateway_end_to_end(app, client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low, normal="2468", duress="1357")
    sh1, q1 = shipment(client, hdr, low, [dev])
    sh2, q2 = shipment(client, hdr, low, [dev])
    sh3, q3 = shipment(client, hdr, low, [dev])
    m = smsmod.minutes_now()
    replies = [client.post("/api/sms/inbound", json={"sender": "+252000", "text": t}) for t in
               (_code(app, dev, sh1, q1, "2468", m), _code(app, dev, sh2, q2, "1357", m), _code(app, dev, sh3, q3, "9999", m))]
    # live mode without a configured gateway secret: refused
    assert all(r.status_code == 403 for r in replies)


def test_sms_gateway_demo_mode(tmp_path):
    from fastapi.testclient import TestClient
    from aidtrace_server.app import create_app
    from aidtrace_server.models import User
    from aidtrace_server.security import hash_secret
    from aidtrace_server.settings import from_env
    from .conftest import PW, login
    a = create_app(from_env(mode="demo", data_dir=tmp_path / "demo", key_dir=tmp_path / "k", submit_min_ms=0), generate_keys=True)
    s = a.state.ctx.sessions()
    for n, r in [("admin", "hq_admin"), ("sfp", "security_focal_point"), ("field", "field_staff")]:
        s.add(User(username=n, password_hash=hash_secret(PW), role=r))
    s.commit()
    c = TestClient(a)
    hdr = {n: login(c, n) for n in ("admin", "sfp", "field")}
    low, _ = risk_units()
    dev = make_recipient(c, hdr, low, normal="2468", duress="1357")
    ships = [shipment(c, hdr, low, [dev]) for _ in range(3)]
    m = smsmod.minutes_now()
    codes = [_code(a, dev, sh, q, pin, m) for (sh, q), pin in zip(ships, ("2468", "1357", "9999"))]
    replies = [c.post("/api/sms/inbound", json={"sender": "+252000", "text": t}).json() for t in codes]
    assert len({json.dumps(r) for r in replies}) == 1, "identical reply for every SMS"
    statuses = [c.get(f"/api/shipments/{sh['id']}", headers=hdr["field"]).json()["scan_points"][-1]["status"] for sh, _ in ships]
    assert statuses == ["verified", "verified", "unverified_pin_mismatch"]
    assert c.get(f"/api/shipments/{ships[1][0]['id']}", headers=hdr["sfp"]).json()["duress"] is True
    # replay
    c.post("/api/sms/inbound", json={"sender": "+252000", "text": codes[0]})
    db = a.state.ctx.sessions()
    assert len(db.scalars(select(Receipt).where(Receipt.channel == "sms")).all()) == 3
    assert db.scalar(select(SecurityEvent).where(SecurityEvent.kind == "replay")) is not None
    # photo follows by data for the duress handover: no second alert
    sh2, q2 = ships[1]
    dev.submit(dev.seal_receipt(q2[-1]["qr"], "1357", ITEMS))
    assert len(db.scalars(select(DuressAlert)).all()) == 1

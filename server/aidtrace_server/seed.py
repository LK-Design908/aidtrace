"""Seed SYNTHETIC demo data through the real API (demo mode only; refuses to touch a live database).

    AIDTRACE_MODE=demo python -I -m aidtrace_server.seed

Creates demo users (passwords printed once), recipient accounts with enrolled reference devices,
shipments in a mix of states (normal, duress, PIN mismatch, two-person partial), and field reports.
All names, sites and accounts are fictional.
"""
from __future__ import annotations

import base64
import json
import random
import secrets
from datetime import date, timedelta

from fastapi.testclient import TestClient
from nacl.public import PublicKey, SealedBox

from .app import create_app
from .models import User
from .security import hash_secret
from .sealed import build_problem, build_receipt, seal
from .settings import from_env

DEMO_USERS = [("demo_hq", "hq_admin"), ("demo_security", "security_focal_point"), ("demo_field", "field_staff")]
SITES = ["Health Centre A", "Stabilisation Centre B", "Mobile Team C", "Health Post D", "MCH Clinic E", "Hospital F"]


def seed(settings=None, password: str | None = None, rng_seed: int = 7) -> dict:
    st = settings or from_env(mode="demo")
    if st.mode != "demo":
        raise SystemExit("seed only runs in demo mode")
    from dataclasses import replace
    st = replace(st, submit_min_ms=0)          # seeding goes through the API; skip timing padding
    app = create_app(st, generate_keys=True)
    c = TestClient(app)
    s = app.state.ctx.sessions()
    pw = password or secrets.token_urlsafe(9)
    for name, role in DEMO_USERS:
        if not s.query(User).filter_by(username=name).first():
            s.add(User(username=name, password_hash=hash_secret(pw), role=role))
    s.commit()
    s.close()
    hdr = {n: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": n, "password": pw}).json()["token"]}
           for n, _ in DEMO_USERS}
    rnd = random.Random(rng_seed)
    risk = c.get("/api/risk?horizon=1", headers=hdr["demo_field"]).json()["units"]
    adm2 = c.get("/api/adm2", headers=hdr["demo_field"]).json()
    by_unit = {}
    for a in adm2:
        by_unit.setdefault(a["unit_id"], a["adm2_pcode"])
    low = [u["unit_id"] for u in risk if u.get("band") == "low"]
    high = [u["unit_id"] for u in risk if u.get("band") in ("high", "severe")]
    units = rnd.sample(low, 4) + rnd.sample(high, 2)
    keys = c.get("/api/recipient/keys").json()

    devices: dict[str, list[dict]] = {}
    for i, unit in enumerate(units):
        for k in range(2 if unit in high else 1):
            r = c.post("/api/recipients", headers=hdr["demo_hq"], json={"site_name": f"{SITES[i]} (synthetic)",
                                                                      "adm2_pcode": by_unit[unit]}).json()
            normal, duress = f"{rnd.randint(1000, 9999)}", f"{rnd.randint(1000, 9999)}"
            while duress == normal:
                duress = f"{rnd.randint(1000, 9999)}"
            sealed = SealedBox(PublicKey(bytes.fromhex(keys["box_public_key"]))).encrypt(
                json.dumps({"normal": normal, "duress": duress}).encode())
            dev = c.post("/api/recipient/enrol", json={"account": r["account"], "enrolment_code": r["enrolment_code"],
                                                       "sealed_pins": base64.b64encode(sealed).decode()}).json()
            devices.setdefault(unit, []).append({**dev, "_normal": normal, "_duress": duress})

    today = date.today()
    summary = []
    plans = [("normal", 0), ("normal", 1), ("duress", 2), ("mismatch", 3), ("pending", 0), ("partial", 4), ("normal_two", 5)]
    for kind, ui in plans:
        unit = units[ui]
        devs = devices[unit]
        scans = ["dispatch", "final_distribution"] if unit in high or kind in ("partial", "normal_two") else ["final_distribution"]
        body = {"origin": "Central warehouse (synthetic)", "dest_adm2_pcode": by_unit[unit], "dest_site": devs[0]["site_name"],
                "window_start": str(today - timedelta(days=rnd.randint(3, 20))), "window_end": str(today + timedelta(days=rnd.randint(-2, 10))),
                "items": [{"item_code": 3, "quantity": 120, "batch": f"SAM-{rnd.randint(100, 999)}"},
                          {"item_code": 4, "quantity": 30, "batch": f"EM-{rnd.randint(100, 999)}",
                           "expiry": str(today + timedelta(days=rnd.choice([60, 400])))}],
                "recipients": [d["account"] for d in devs], "scan_points": scans,
                "low_profile": rnd.sample(["unbranded_packaging", "split_consignment", "varied_day_route"], 1)}
        sh = c.post("/api/shipments", headers=hdr["demo_field"], json=body).json()
        qrs = c.get(f"/api/shipments/{sh['id']}/qr", headers=hdr["demo_field"]).json()
        items = [{"code": 3, "qty_received": 120 if kind != "mismatch" else 100, "damaged": 0, "expired": 0},
                 {"code": 4, "qty_received": 30, "damaged": 0, "expired": 0}]

        def submit(dev, pin, qr):
            blob = seal(build_receipt(dev["account"], dev["device_token"], qr, pin, items), dev["box_public_key"])
            c.post("/api/recipient/submit", content=blob)
        if kind == "pending":
            pass
        elif kind == "partial":
            submit(devs[0], devs[0]["_normal"], qrs[-1]["qr"])
        elif kind == "normal_two":
            for d in devs:
                submit(d, d["_normal"], qrs[-1]["qr"])
        else:
            pin = {"normal": devs[0]["_normal"], "duress": devs[0]["_duress"], "mismatch": "0000"}[kind]
            for q in qrs:
                submit(devs[0], pin, q["qr"])
        summary.append({"shipment": sh["code"], "scenario": kind, "district": by_unit[unit]})

    for unit, typ, sev, txt in [(units[4], "access_denied", 3, "Road closed at checkpoint (synthetic)."),
                                (units[4], "extortion_at_handover", 4, "Fee demanded at handover (synthetic)."),
                                (units[5], "theft_in_transit", 3, "Two cartons missing on arrival (synthetic)."),
                                (units[1], "violence_near_site", 2, "Clashes reported nearby (synthetic).")]:
        c.post("/api/reports", headers=hdr["demo_field"], json={"type": typ, "incident_date": str(today - timedelta(days=rnd.randint(1, 40))),
                                                                "adm2_pcode": by_unit[unit], "free_text": txt, "severity": sev})
    d0 = devices[units[0]][0]
    c.post("/api/recipient/submit", content=seal(build_problem(d0["account"], d0["device_token"], "shortage", "SAM stock low (synthetic)"),
                                                 d0["box_public_key"]))
    demo_devices = [{"account": d["account"], "site": d["site_name"], "normal_pin": d["_normal"], "duress_pin": d["_duress"]}
                    for ds in devices.values() for d in ds]
    out = {"password": pw, "users": [n for n, _ in DEMO_USERS], "shipments": summary, "demo_devices": demo_devices}
    (st.data_dir / "demo_seed.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = seed()
    print(json.dumps({k: v for k, v in o.items() if k != "demo_devices"}, indent=2))
    print(f"Synthetic device PINs written to demo_seed.json in the demo data directory.")

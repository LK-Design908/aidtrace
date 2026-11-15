"""Map layers, indicators, Health Cluster export, verification queue, reconciliation."""
from __future__ import annotations

from datetime import date, timedelta

from aidtrace_server import settings as settings_mod

from .conftest import make_recipient, risk_units, served_units
from .test_server import ITEMS, shipment

TODAY = date.today()


def test_layers_timeseries(client, hdr):
    d = client.get("/api/layers", headers=hdr["field"]).json()
    assert d["months"][0] == "2017-01"
    n = served_units()
    for lid in ("need_index", "ipc_phase", "price_anom", "displacement", "ucdp_fatalities", "pop_density", "bldg_density"):
        L = d["layers"][lid]
        assert len(L["values"]) == n and all(len(v) == len(d["months"]) for v in L["values"].values())
    assert "2026-11" not in d["layers"]["ucdp_fatalities"]["observed_months"]   # UCDP (GED + Candidate) ends before the panel end
    assert set(d["forecast"]) == {"h1", "h3"}


def test_health_cluster_export_is_clean(client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    dev.submit(dev.seal_receipt(q[-1]["qr"], "1357", ITEMS))         # duress
    client.post("/api/reports", headers=hdr["field"], json={"type": "access_denied", "incident_date": str(TODAY),
                                                            "adm2_pcode": low, "free_text": "SECRET-TEXT", "severity": 3})
    assert client.get("/api/export/health-cluster.csv", headers=hdr["field"]).status_code == 403
    csv = client.get("/api/export/health-cluster.csv", headers=hdr["admin"]).text
    header = csv.splitlines()[0]
    assert header == "adm2_pcode,month,item,shipments_with_item,shipments_delivered_verified,quantity_dispatched,quantity_confirmed"
    low_csv = csv.lower()
    for bad in ("duress", "secret-text", dev.state["account"].lower(), "acled", "r-"):
        assert bad not in low_csv
    ind = client.get("/api/indicators", headers=hdr["field"]).json()
    ids = {i["id"]: i for i in ind["indicators"]}
    assert ids["shortfall_rate"]["srp"] and ids["access_constraint_reports"]["value"] == 1


def test_verification_draw_certain_at_rate_1(client, hdr, monkeypatch):
    cfg = settings_mod.static_config()
    monkeypatch.setitem(cfg["verification"], "base_rate", 1.0)
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS))
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS))         # second receipt: no second draw
    vq = client.get("/api/verification", headers=hdr["field"]).json()
    assert len(vq) == 1 and vq[0]["shipment_id"] == sh["id"]
    assert client.post(f"/api/verification/{vq[0]['id']}", json={"outcome": "confirmed"}, headers=hdr["field"]).json()["ok"]


def test_verification_never_at_rate_0(client, hdr, monkeypatch):
    cfg = settings_mod.static_config()
    monkeypatch.setitem(cfg["verification"], "base_rate", 0.0)
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS))
    assert client.get("/api/verification", headers=hdr["field"]).json() == []


def test_reconciliation_flags_excess(client, hdr):
    low, _ = risk_units()
    dev = make_recipient(client, hdr, low)
    sh, q = shipment(client, hdr, low, [dev])                         # 40 SAM units delivered
    dev.submit(dev.seal_receipt(q[-1]["qr"], "2468", ITEMS))
    month = f"{TODAY:%Y-%m}"
    client.post("/api/service-data", headers=hdr["admin"],
                json={"account": dev.state["account"], "month": month, "metric": "sam_admissions", "value": 10})
    client.post("/api/service-data", headers=hdr["admin"],
                json={"account": dev.state["account"], "month": month, "metric": "stockout_days_3", "value": 4})
    rows = client.get("/api/reconciliation", headers=hdr["field"]).json()["rows"]
    flags = {r["flag"] for r in rows}
    assert "deliveries_exceed_plausible_use" in flags and "stockout_despite_recent_delivery" in flags

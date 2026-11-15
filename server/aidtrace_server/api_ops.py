"""Map time series, SRP indicators, Health Cluster export, verification queue, reconciliation."""
from __future__ import annotations

import csv
import io
import statistics
from datetime import timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (FieldReport, Recipient, ServiceData, Shipment, ShipmentRecipient, User, VerificationTask,
                     utcnow)
from .receipts import CONFIRMING, shipment_view
from .security import audit, ctx, current_user, db, require_roles
from .settings import static_config

CFG = static_config()
router = APIRouter(prefix="/api")

LAYER_SOURCES = {
    # layer id: (file, column, label, observed-data source)
    "need_index": ("need", "need_index", "Need index (0-1)", "AidTrace need index"),
    "ipc_phase": ("panel", "ipc_cs_max", "IPC phase (current)", "FEWS NET"),
    "price_anom": ("panel", "price_staple_anom", "Staple price anomaly (log vs 12-mo mean)", "WFP"),
    "displacement": ("panel", "dtm_arrivals", "New arrivals (IOM DTM)", "IOM DTM"),
    "ucdp_fatalities": ("panel", "ucdp_deaths_best", "Conflict fatalities (UCDP, best)", "UCDP GED"),
    "pop_density": ("static", "pop_density_km2", "Population density (people/km², 2020)", "WorldPop"),
    "bldg_density": ("static", "bldg_density_km2", "Building density (buildings/km²)", "WorldPop buildings"),
}


@router.get("/adm2")
def adm2_list(request: Request, user: User = Depends(current_user)):
    """Admin-2 districts (COD-AB) with their modelling unit, for forms."""
    cw = ctx(request).data.crosswalk()
    if cw is None:
        return []
    return cw.sort_values(["country_name", "adm1_name", "adm2_name"]).fillna("").to_dict("records")


def _round(v, nd=3):
    return None if v is None or v != v else round(float(v), nd)


@router.get("/layers")
def layers(request: Request, user: User = Depends(current_user)):
    """Unit x month matrices for the map's time slider. District resolution only."""
    return _layers_cached(ctx(request))


_LAYER_CACHE: dict = {}


def _layers_cached(c) -> dict:
    """Built once per process (panels are rebuilt monthly; restart the server after a pipeline run)."""
    key = tuple(c.settings.countries)
    if key not in _LAYER_CACHE:
        _LAYER_CACHE[key] = _build_layers(c)
    return _LAYER_CACHE[key]


def _build_layers(c) -> dict:
    root = c.settings.repo_root / "data" / "processed"
    frames = []
    for iso in (x.lower() for x in c.settings.countries):
        pf = root / f"panel_{iso}.parquet"
        if not pf.exists():
            continue
        cols = ["unit_id", "month"] + [col for _, (f, col, _, _) in LAYER_SOURCES.items() if f in ("panel", "static")]
        panel = pd.read_parquet(pf)
        panel = panel[[col for col in cols if col in panel.columns]]
        nf = root / "need" / f"{iso}_need.parquet"
        if nf.exists():
            panel = panel.merge(pd.read_parquet(nf, columns=["unit_id", "month", "need_index"]), on=["unit_id", "month"], how="left")
        frames.append(panel)
    df = pd.concat(frames, ignore_index=True)
    months = sorted(df["month"].unique())
    out = {"months": [f"{pd.Timestamp(m):%Y-%m}" for m in months], "layers": {}}
    for lid, (_, col, label, src) in LAYER_SOURCES.items():
        if col not in df.columns:
            continue
        wide = df.pivot(index="unit_id", columns="month", values=col).reindex(columns=months)
        obs = wide.notna().any(axis=0)
        observed = [f"{pd.Timestamp(m):%Y-%m}" for m, o in obs.items() if o]
        out["layers"][lid] = {"label": label, "source": src, "observed_months": observed,
                              "values": {u: [_round(v) for v in row] for u, row in wide.iterrows()}}
    risk = c.data.risk()
    if risk is not None:
        fc = {}
        for h, d in risk.groupby("horizon"):
            fc[f"h{h}"] = {"target_month": f"{d['target_month'].iloc[0]:%Y-%m}",
                           "origin": f"{d['origin'].iloc[0]:%Y-%m}",
                           "values": {r.unit_id: {"prob": _round(r.prob), "lo": _round(r.prob_lo), "hi": _round(r.prob_hi),
                                                  "band": r.band} for r in d.itertuples()}}
        out["forecast"] = fc
    return out


# ================================================================== indicators

def _confirmed_receipts(sh: Shipment, final_only=True):
    sps = sh.scan_points[-1:] if final_only else sh.scan_points
    return [r for sp in sps for r in sp.receipts if r.pin_class in CONFIRMING]


def compute_indicators(s: Session, days: int = 90) -> dict:
    since = utcnow() - timedelta(days=days)
    shipments = s.scalars(select(Shipment).where(Shipment.created_at >= since)).all()
    views = {sh.id: shipment_view(sh, can_see_duress=False) for sh in shipments}
    due = [sh for sh in shipments if sh.window_end <= utcnow() or views[sh.id]["status"] == "delivered"]
    on_time = [sh for sh in due if views[sh.id]["status"] == "delivered" and
               min(r.device_ts for r in _confirmed_receipts(sh)) <= sh.window_end]
    times = []
    for sh in shipments:
        fin = _confirmed_receipts(sh)
        disp = [r for r in sh.scan_points[0].receipts if r.pin_class in CONFIRMING] if len(sh.scan_points) > 1 else []
        if fin:
            start = min(r.device_ts for r in disp) if disp else sh.created_at
            times.append((min(r.device_ts for r in fin) - start).total_seconds() / 86400)
    delivered = [sh for sh in shipments if views[sh.id]["status"] == "delivered"]
    shortfall = [sh for sh in delivered if "quantity_shortfall" in views[sh.id]["flags"]]
    stock: dict[tuple, int] = {}
    cat = {c["code"]: c["label"] for c in CFG["catalogue"]}
    for sh in delivered:
        for line in views[sh.id]["received_final"]:
            if line["confirmed"]:
                k = (sh.dest_unit_id, cat.get(line["item_code"], "?"))
                stock[k] = stock.get(k, 0) + line["confirmed"]
    access = s.scalars(select(FieldReport).where(FieldReport.type == "access_denied", FieldReport.incident_date >= since,
                                                 FieldReport.review_status != "rejected")).all()
    acc_by_unit: dict[str, int] = {}
    for r in access:
        acc_by_unit[r.unit_id] = acc_by_unit.get(r.unit_id, 0) + 1
    enrolled = s.scalars(select(Recipient).where(Recipient.enrolled_at.is_not(None), Recipient.active.is_(True))).all()
    reached = {r.recipient_id for sh in delivered for r in _confirmed_receipts(sh)}
    pct = lambda a, b: None if not b else round(100 * a / b, 1)  # noqa: E731
    return {
        "window_days": days,
        "indicators": [
            {"id": "verified_on_time", "label": "Shipments verified on time", "value": pct(len(on_time), len(due)),
             "unit": "%", "n": len(due), "srp": "Timeliness of operational reporting"},
            {"id": "median_dispatch_to_receipt", "label": "Median dispatch-to-receipt time",
             "value": None if not times else round(statistics.median(times), 1), "unit": "days", "n": len(times),
             "srp": "Availability of essential medicines, vaccines and nutrition commodities"},
            {"id": "shortfall_rate", "label": "Quantity shortfall rate", "value": pct(len(shortfall), len(delivered)),
             "unit": "%", "n": len(delivered), "srp": "Availability of essential medicines, vaccines and nutrition commodities"},
            {"id": "access_constraint_reports", "label": "Access-constraint reports", "value": len(access), "unit": "reports",
             "n": len(access), "srp": "Humanitarian access constraints affecting delivery"},
            {"id": "facility_coverage", "label": "Facilities/teams receiving supplies", "value": pct(len(reached), len(enrolled)),
             "unit": "%", "n": len(enrolled), "srp": "Coverage of mobile services"},
        ],
        "stock_by_district_item": [{"unit_id": u, "item": i, "confirmed_quantity": q} for (u, i), q in sorted(stock.items())],
        "access_reports_by_district": [{"unit_id": u, "reports": n} for u, n in sorted(acc_by_unit.items())],
    }


@router.get("/indicators")
def indicators(request: Request, days: int = 90, s: Session = Depends(db), user: User = Depends(current_user)):
    out = compute_indicators(s, days)
    t = ctx(request).data.triggers()
    if t is not None and len(t):
        recent = t[t["month"] >= t["month"].max() - pd.DateOffset(months=3)]
        out["trigger_alerts_by_district"] = (recent.groupby("unit_id")["trigger"].nunique().rename("active_triggers")
                                             .reset_index().to_dict("records"))
        out["trigger_alerts_note"] = "SRP §7 triggers in the last 3 months of pipeline data (FEWS NET, IOM DTM)"
    return out


def _csv(rows: list[dict], name: str) -> Response:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/indicators.csv")
def indicators_csv(days: int = 90, s: Session = Depends(db), user: User = Depends(current_user)):
    ind = compute_indicators(s, days)
    return _csv([{k: v for k, v in i.items()} for i in ind["indicators"]], "aidtrace_indicators.csv")


@router.get("/export/health-cluster.csv")
def health_cluster_export(s: Session = Depends(db), user: User = Depends(require_roles(["hq_admin", "security_focal_point"]))):
    """Aggregated delivery data for the Health Cluster / national IMS. Contains no field-report text,
    no duress information, no recipient identities and no ACLED-derived content. One row per
    district x month x item."""
    cat = {c["code"]: c["label"] for c in CFG["catalogue"]}
    agg: dict[tuple, dict] = {}
    for sh in s.scalars(select(Shipment)).all():
        v = shipment_view(sh, can_see_duress=False)       # duress == normal here by construction
        month = f"{sh.window_end:%Y-%m}"
        for line in v["received_final"]:
            k = (sh.dest_adm2_pcode, month, cat.get(line["item_code"], "?"))
            a = agg.setdefault(k, {"adm2_pcode": k[0], "month": k[1], "item": k[2], "shipments_with_item": 0,
                                   "shipments_delivered_verified": 0, "quantity_dispatched": 0, "quantity_confirmed": 0})
            a["shipments_with_item"] += 1
            a["quantity_dispatched"] += line["dispatched"]
            if v["status"] == "delivered":
                a["shipments_delivered_verified"] += 1
                a["quantity_confirmed"] += line["confirmed"] or 0
    rows = sorted(agg.values(), key=lambda r: (r["month"], r["adm2_pcode"], r["item"]))
    audit(s, user, "export_health_cluster")
    return _csv(rows, "aidtrace_health_cluster.csv")


# ================================================================== verification queue

@router.get("/verification")
def verification_queue(s: Session = Depends(db), user: User = Depends(current_user)):
    out = []
    for t in s.scalars(select(VerificationTask).order_by(VerificationTask.id.desc())):
        sh = s.get(Shipment, t.shipment_id)
        out.append({"id": t.id, "shipment_id": sh.id, "shipment": sh.code, "site": sh.dest_site,
                    "district": sh.dest_adm2_pcode, "selected_at": t.selected_at, "selection_rate": t.selection_rate,
                    "outcome": t.outcome, "outcome_notes": t.outcome_notes, "recorded_at": t.recorded_at})
    return out


class OutcomeIn(BaseModel):
    outcome: str
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("outcome")
    @classmethod
    def _o(cls, v):
        if v not in ("confirmed", "discrepancy", "not_reachable"):
            raise ValueError("bad outcome")
        return v


@router.post("/verification/{tid}")
def record_verification(tid: int, body: OutcomeIn, s: Session = Depends(db),
                        user: User = Depends(require_roles(["hq_admin", "security_focal_point", "field_staff"]))):
    t = s.get(VerificationTask, tid)
    if t is None:
        raise HTTPException(404)
    t.outcome, t.outcome_notes, t.recorded_by, t.recorded_at = body.outcome, body.notes, user.id, utcnow()
    s.commit()
    audit(s, user, f"verification_{body.outcome}", "verification_task", tid)
    return {"ok": True}


# ================================================================== reconciliation

class ServiceIn(BaseModel):
    account: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    metric: str = Field(max_length=48)
    value: float = Field(ge=0)


@router.post("/service-data", status_code=201)
def add_service_data(body: ServiceIn, s: Session = Depends(db), user: User = Depends(require_roles(["hq_admin"]))):
    rec = s.scalar(select(Recipient).where(Recipient.account == body.account))
    if rec is None:
        raise HTTPException(422, "Unknown recipient account")
    row = s.scalar(select(ServiceData).where(ServiceData.recipient_id == rec.id, ServiceData.month == body.month,
                                             ServiceData.metric == body.metric))
    if row:
        row.value = body.value
    else:
        s.add(ServiceData(recipient_id=rec.id, month=body.month, metric=body.metric, value=body.value, entered_by=user.id))
    s.commit()
    return {"ok": True}


def reconcile(s: Session) -> list[dict]:
    rc = CFG["reconciliation"]
    months = pd.period_range(end=pd.Timestamp(utcnow()).to_period("M"), periods=rc["window_months"], freq="M")
    mstr = [str(m) for m in months]
    since = months[0].to_timestamp()
    out = []
    for rec in s.scalars(select(Recipient).where(Recipient.enrolled_at.is_not(None))):
        sd = s.scalars(select(ServiceData).where(ServiceData.recipient_id == rec.id, ServiceData.month.in_(mstr))).all()
        ships = [s.get(Shipment, x.shipment_id) for x in
                 s.scalars(select(ShipmentRecipient).where(ShipmentRecipient.recipient_id == rec.id))]
        delivered: dict[int, int] = {}
        last_delivery = None
        for sh in ships:
            v = shipment_view(sh, can_see_duress=False)
            if v["status"] != "delivered":
                continue
            fin = _confirmed_receipts(sh)
            when = min(r.device_ts for r in fin)
            last_delivery = max(last_delivery or when, when)
            if when >= since:
                for line in v["received_final"]:
                    delivered[line["item_code"]] = delivered.get(line["item_code"], 0) + (line["confirmed"] or 0)
        metrics: dict[str, float] = {}
        for x in sd:
            metrics[x.metric] = metrics.get(x.metric, 0) + x.value
        for r in rc["ratios"]:
            if r["metric"] not in metrics:
                continue
            expected = metrics[r["metric"]] * r["per_unit"]
            got = delivered.get(r["item_code"], 0)
            flag = expected >= rc["min_expected"] and got > rc["excess_multiple"] * expected
            out.append({"account": rec.account, "site": rec.site_name, "unit_id": rec.unit_id, "item_code": r["item_code"],
                        "window": f"{mstr[0]}..{mstr[-1]}", "delivered_confirmed": got, "expected_use": round(expected, 1),
                        "ratio_note": r["note"], "flag": "deliveries_exceed_plausible_use" if flag else None})
        for metric, val in metrics.items():
            if metric.startswith("stockout_days_") and val > 0 and last_delivery and \
                    last_delivery >= utcnow() - timedelta(days=30):
                out.append({"account": rec.account, "site": rec.site_name, "unit_id": rec.unit_id,
                            "item_code": int(metric.rsplit("_", 1)[1]), "window": f"{mstr[0]}..{mstr[-1]}",
                            "delivered_confirmed": None, "expected_use": None, "ratio_note": f"{val:g} stock-out days reported",
                            "flag": "stockout_despite_recent_delivery"})
    return out


@router.get("/reconciliation")
def reconciliation(s: Session = Depends(db), user: User = Depends(current_user)):
    return {"ratios": CFG["reconciliation"]["ratios"], "rows": reconcile(s),
            "note": "Ratios are provisional; see docs/RECONCILIATION.md. Requires facility service data."}

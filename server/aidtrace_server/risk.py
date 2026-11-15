"""Read-only access to pipeline outputs: risk scores, need index, triggers, unit geometries, ACLED.

All outputs are admin-2 (district) resolution, never finer (brief §2.1). ACLED-derived content is
served only through `acled_layer`, gated by acled_display and role, and is never included in
risk scores (the scoring model runs without ACLED).
"""
from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FieldReport, utcnow
from .settings import Settings, static_config


COUNTRY_NAMES = {"DJI": "Djibouti", "ETH": "Ethiopia", "KEN": "Kenya", "SOM": "Somalia", "SSD": "South Sudan",
                 "SDN": "Sudan"}


class PipelineData:
    """Pipeline outputs for every country this deployment serves (settings.countries), combined.
    Unit ids are globally unique (p-codes carry the country prefix; Somalia's merged Banadir is SO22)."""

    def __init__(self, settings: Settings):
        self.root = settings.repo_root / "data" / "processed"
        self.isos = [c.lower() for c in settings.countries]
        self.settings = settings

    def _read_all(self, pattern: str) -> pd.DataFrame | None:
        frames = []
        for iso in self.isos:
            p = self.root / pattern.format(iso=iso)
            if p.exists():
                frames.append(pd.read_parquet(p).assign(country=iso.upper()))
        return pd.concat(frames, ignore_index=True) if frames else None

    @lru_cache
    def units_geojson(self) -> dict:
        import geopandas as gpd  # optional heavy import, only when serving geometry
        frames = []
        for iso in self.isos:
            p = self.root / "spine" / f"{iso}_units.parquet"
            if p.exists():
                g = gpd.read_parquet(p)
                g["country"] = iso.upper()
                g["country_name"] = COUNTRY_NAMES.get(iso.upper(), iso.upper())
                frames.append(g)
        g = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
        g["geometry"] = g.geometry.simplify(0.002, preserve_topology=True)
        return json.loads(g[["unit_id", "unit_name", "adm1_name", "country", "country_name", "geometry"]].to_json())

    @lru_cache
    def risk(self) -> pd.DataFrame | None:
        return self._read_all("risk/{iso}_risk.parquet")

    @lru_cache
    def need(self) -> pd.DataFrame | None:
        return self._read_all("need/{iso}_need.parquet")

    @lru_cache
    def triggers(self) -> pd.DataFrame | None:
        return self._read_all("need/{iso}_triggers.parquet")

    @lru_cache
    def static(self) -> pd.DataFrame | None:
        return self._read_all("static/{iso}_static.parquet")

    @lru_cache
    def need_meta(self) -> dict:
        meta: dict = {"trigger_status": {}}
        for iso in self.isos:
            p = self.root / "need" / f"{iso}_need_meta.json"
            if p.exists():
                m = json.loads(p.read_text(encoding="utf-8"))
                meta.setdefault("weights", m.get("weights"))
                meta.setdefault("sources", m.get("sources"))
                meta["trigger_status"][iso.upper()] = m.get("trigger_status")
        return meta

    @lru_cache
    def crosswalk(self) -> pd.DataFrame | None:
        frames = []
        for iso in self.isos:
            p = self.root / "spine" / f"{iso}_crosswalk.csv"
            if p.exists():
                frames.append(pd.read_csv(p).assign(country=iso.upper(),
                                                    country_name=COUNTRY_NAMES.get(iso.upper(), iso.upper())))
        return pd.concat(frames, ignore_index=True) if frames else None

    def unit_for_pcode(self, adm2_pcode: str) -> str | None:
        cw = self.crosswalk()
        if cw is None:
            return None
        m = cw.loc[cw["adm2_pcode"] == adm2_pcode, "unit_id"]
        return None if m.empty else str(m.iloc[0])

    def country_for_pcode(self, adm2_pcode: str) -> str | None:
        cw = self.crosswalk()
        if cw is None:
            return None
        m = cw.loc[cw["adm2_pcode"] == adm2_pcode, "country"]
        return None if m.empty else str(m.iloc[0])

    def band_for(self, unit_id: str, horizon: int = 1) -> str | None:
        r = self.risk()
        if r is None:
            return None
        m = r[(r["unit_id"] == unit_id) & (r["horizon"] == horizon)]
        return None if m.empty else str(m["band"].iloc[0])

    def latest_need(self) -> pd.DataFrame | None:
        """Each country's most recent need month (countries' panels end in different months)."""
        n = self.need()
        if n is None:
            return None
        last = n.groupby("country")["month"].transform("max")
        return n[n["month"] == last]


def field_report_counts(s: Session, days: int = 90) -> dict[str, dict]:
    since = utcnow() - timedelta(days=days)
    rows = s.execute(select(FieldReport.unit_id, FieldReport.type, func.count())
                     .where(FieldReport.incident_date >= since, FieldReport.review_status != "rejected")
                     .group_by(FieldReport.unit_id, FieldReport.type)).all()
    out: dict[str, dict] = {}
    for unit, typ, n in rows:
        out.setdefault(unit, {})[typ] = n
    return out


def access_trigger(counts: dict) -> bool:
    return counts.get("access_denied", 0) >= static_config()["access_reports_90d"]


def quadrant(high_need, band: str | None) -> str:
    higher = band in static_config()["higher_risk_bands"]
    if pd.isna(high_need):
        return "need_unknown_higher_risk" if higher else "need_unknown"
    if int(high_need) == 1:
        return "high_need_higher_risk" if higher else "high_need_lower_risk"
    return "lower_need_higher_risk" if higher else "lower_need_lower_risk"


QUADRANT_ACTION = {
    "high_need_lower_risk": "Standard delivery.",
    "high_need_higher_risk": "Deliver with enhanced verification and protection.",
    "lower_need_higher_risk": "Monitor.",
    "lower_need_lower_risk": "Routine.",
    "need_unknown_higher_risk": "Need data missing; treat as possible high need and deliver with enhanced verification.",
    "need_unknown": "Need data missing; routine delivery and seek need assessment.",
}


def recommendation(unit: dict) -> str:
    """Plain-text recommendation from the priority view, triggers and recent field reports.
    It never recommends withholding aid: risk changes how aid is delivered."""
    parts = []
    if unit.get("ipc_phase") is not None:
        parts.append(f"IPC Phase {int(unit['ipc_phase'])}")
    for t in unit.get("active_triggers", []):
        parts.append(t["label"])
    if unit.get("band"):
        pr = "<1%" if unit["prob"] < 0.01 else f"{unit['prob']:.0%}"
        parts.append(f"{unit['band']} violence risk ({pr}, data to {unit['data_last_month']})")
    fr = unit.get("field_reports_90d", {})
    for typ, n in sorted(fr.items(), key=lambda kv: -kv[1]):
        parts.append(f"{n} field report{'s' if n > 1 else ''} of {typ.replace('_', ' ')} in the last 90 days")
    q = unit.get("quadrant", "need_unknown")
    rec = []
    if q in ("high_need_higher_risk", "need_unknown_higher_risk"):
        rec += ["two-point verification (two-person confirmation)", "split consignment",
                "independent post-distribution check"]
        if fr.get("extortion_at_handover") or fr.get("diversion"):
            rec += ["varied delivery days and routes", "unbranded packaging"]
    elif q == "lower_need_higher_risk":
        rec += ["monitor; keep deliveries low-profile", "independent spot checks"]
    else:
        rec += ["standard verification"]
    if fr.get("access_denied"):
        rec.append("pre-position ahead of further access loss")
    text = "; ".join(parts) if parts else "Limited data"
    return f"{text}. {QUADRANT_ACTION.get(q, '')} Recommend: {', '.join(rec)}."


TRIGGER_LABELS = {
    "ipc_deterioration": "IPC deterioration",
    "ipc_phase45_current": "Phase 4-5 area (current)",
    "ipc_phase45_projected": "Phase 4-5 projected",
    "displacement_large": "large displacement inflow",
    "access_deterioration": "access deterioration (field reports)",
}


def unit_summaries(data: PipelineData, s: Session, horizon: int = 1) -> list[dict]:
    r = data.risk()
    n = data.latest_need()
    t = data.triggers()
    fr = field_report_counts(s)
    gj = data.units_geojson()
    st = data.static()
    st = st.set_index("unit_id") if st is not None else None
    out = []
    for f in gj["features"]:
        uid = f["properties"]["unit_id"]
        u = {"unit_id": uid, "unit_name": f["properties"]["unit_name"], "adm1_name": f["properties"]["adm1_name"],
             "country": f["properties"].get("country"), "country_name": f["properties"].get("country_name")}
        if st is not None and uid in st.index:
            x = st.loc[uid]
            num = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
            u["exposure"] = {"population": num(x.get("pop_total")), "under5": num(x.get("pop_u5")),
                             "buildings": num(x.get("bldg_count")), "urban_share": num(x.get("bldg_urban_share")),
                             "weight_source": x.get("weight_source")}
        if r is not None:
            m = r[(r["unit_id"] == uid) & (r["horizon"] == horizon)]
            if not m.empty:
                x = m.iloc[0]
                u.update(prob=float(x.prob), prob_lo=float(x.prob_lo), prob_hi=float(x.prob_hi), band=x.band,
                         target_month=f"{x.target_month:%Y-%m}", data_last_month=f"{x.data_last_month:%Y-%m}",
                         stale_months=int(x.stale_months), horizon=horizon)
        if n is not None:
            m = n[n["unit_id"] == uid]
            if not m.empty:
                x = m.iloc[0]
                comps = {k.replace("need_", ""): (None if pd.isna(x[k]) else float(x[k]))
                         for k in n.columns if k.startswith("need_") and k not in
                         ("need_index", "need_weight_available", "need_n_components", "need_missing")}
                u.update(ipc_p3plus_basis=None if pd.isna(x.get("ipc_p3plus_basis")) else str(x.get("ipc_p3plus_basis")),
                         need_index=None if pd.isna(x.need_index) else float(x.need_index),
                         need_weight_available=float(x.need_weight_available), need_components=comps,
                         need_missing=[c for c in str(x.need_missing).split(",") if c],
                         need_month=f"{x.month:%Y-%m}",
                         ipc_phase=None if pd.isna(x.ipc_phase_value) else int(x.ipc_phase_value),
                         high_need=None if pd.isna(x.high_need) else int(x.high_need))
        active = []
        if t is not None and len(t):
            tt = t[(t["unit_id"] == uid) & (t["month"] >= t["month"].max() - pd.DateOffset(months=3))]
            for _, a in tt.drop_duplicates("trigger", keep="last").iterrows():
                active.append({"trigger": a.trigger, "label": TRIGGER_LABELS.get(a.trigger, a.trigger),
                               "month": f"{a.month:%Y-%m}", "evidence": a.evidence, "source": a.source})
        counts = fr.get(uid, {})
        if access_trigger(counts):
            active.append({"trigger": "access_deterioration", "label": TRIGGER_LABELS["access_deterioration"],
                           "month": f"{utcnow():%Y-%m}", "evidence": f"{counts['access_denied']} access-denied reports in 90 days",
                           "source": "AidTrace field reports"})
        u["active_triggers"] = active
        u["field_reports_90d"] = counts
        u["quadrant"] = quadrant(u.get("high_need"), u.get("band"))
        u["recommendation"] = recommendation(u)
        out.append(u)
    return out


# ------------------------------------------------------------------ ACLED (gated)

def acled_level_for(settings: Settings, role: str | None) -> str:
    """Effective ACLED level for a caller: 'none' unless config and role both allow it."""
    level = settings.acled_display
    if settings.mode == "demo":         # owner decision 2026-11-15: demo shows aggregated ACLED (AIDTRACE_DEMO_ACLED)
        level = settings.demo_acled_display
    if level == "none" or role is None or role == "recipient":
        return "none"
    roles = static_config()["acled_roles"]
    if level == "event_level" and role in roles["event_level"]:
        return "event_level"
    if level in ("aggregated", "event_level") and role in roles["aggregated"]:
        return "aggregated"
    return "none"


def acled_layer(settings: Settings, role: str | None, unit_id: str | None = None) -> dict | None:
    level = acled_level_for(settings, role)
    if level == "none":
        return None
    base = settings.repo_root / "data" / "processed" / "restricted" / "acled"
    files = [base / f"{c.lower()}.parquet" for c in settings.countries if (base / f"{c.lower()}.parquet").exists()]
    if not files:
        return {"level": level, "rows": [], "attribution": static_config()["acled_attribution"]}
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if unit_id:
        df = df[df["unit_id"] == unit_id]
    cols = ["unit_id", "month", "acled_events", "acled_fatalities", "acled_events_battles", "acled_events_vac",
            "acled_events_remote", "acled_events_riots", "acled_events_protests", "acled_events_strategic"]
    df = df[[c for c in cols if c in df.columns]].copy()
    df["month"] = df["month"].dt.strftime("%Y-%m")
    out = {"level": level, "resolution": "admin-2 district-month", "rows": df.to_dict("records"),
           "attribution": static_config()["acled_attribution"]}
    if level == "event_level":
        out["note"] = ("Event-level display is permitted by configuration, but this beta stores only aggregated "
                       "ACLED outputs; points would be shown at ACLED's recorded geo_precision only.")
    return out

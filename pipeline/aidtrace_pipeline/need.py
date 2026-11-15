"""Need index and SRP §7 trigger engine.

    python -I -m aidtrace_pipeline.need --country SOM

Need index: a transparent weighted mean of component scores in [0, 1], renormalised over the
components available for each unit-month. Components without data are listed in
`need_missing` and never imputed. Observations are carried forward at most
`need.carry_forward_months` months, and each component's age is kept.

Triggers: explicit rules evaluated per unit and month, each producing a dated alert with
evidence. Rules whose inputs do not exist yet are reported as "not evaluable", never as
"no alert".
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from .common import PROCESSED, cfg, log, path

COMPONENT_SOURCES = {
    "ipc_phase": "FEWS NET IPC current (CS) max phase",
    "ipc_p3plus": "FEWS NET IPC current share of people (WorldPop population or building count) in Phase 3+; land area where neither exists",
    "malnutrition": "IPC AMN / SMART GAM-SAM",
    "outbreaks": "WHO AFRO/EMRO bulletins, EWARS/IDSR",
    "facility_functionality": "WHO HeRAMS",
    "displacement": "IOM DTM new arrivals (per 1,000 residents where WorldPop population exists)",
    "immunisation": "DHS/MICS measles coverage",
    "wash": "WASH cluster severity / JMP",
    "rainfall_anomaly": "CHIRPS v3 3-month rainfall z-score vs 1991-2020 (deficit or excess)",
    "flood_hazard": "GloFAS / JRC GSW",
    "price_anomaly": "WFP staple price anomaly",
}


def _panel(iso3: str) -> pd.DataFrame:
    return pd.read_parquet(path(PROCESSED) / f"panel_{iso3.lower()}.parquet").sort_values(["unit_id", "month"])


def _carry(p: pd.DataFrame, col: str, limit: int) -> tuple[pd.Series, pd.Series]:
    g = p.groupby("unit_id")
    val = g[col].ffill(limit=limit)
    obs = p["month"].where(p[col].notna())
    age = (p["month"] - obs.groupby(p["unit_id"]).ffill()).dt.days / 30.44
    return val, age.where(val.notna()).round()


def component_scores(p: pd.DataFrame) -> pd.DataFrame:
    c = cfg()["need"]
    lim = c["carry_forward_months"]
    s = pd.DataFrame(index=p.index)
    ph, s["ipc_phase_age"] = _carry(p, "ipc_cs_max", lim)
    s["ipc_phase_value"] = ph
    s["ipc_phase"] = (ph - 1) / 4
    # Share of PEOPLE in Phase 3+ where a weight grid exists (population or buildings), else of land area.
    sh_area, _ = _carry(p, "ipc_cs_p3plus", lim)
    sh_pop, _ = _carry(p, "ipc_cs_p3plus_pop", lim) if "ipc_cs_p3plus_pop" in p else (sh_area * np.nan, None)
    s["ipc_p3plus"] = sh_pop.where(sh_pop.notna(), sh_area)
    s["ipc_p3plus_basis"] = np.where(sh_pop.notna(), p.get("weight_source", "area"), np.where(sh_area.notna(), "area", None))
    pa, s["price_anomaly_age"] = _carry(p, "price_staple_anom", 3)
    s["price_anomaly"] = (pa / c["scaling"]["price_anom_full"]).clip(0, 1)
    arr = p["dtm_arrivals"]
    # Arrivals per 1,000 residents where population is known; otherwise absolute arrivals (log scale).
    per1k = arr / p["pop_total"] * 1000 if "pop_total" in p else arr * np.nan
    s["displacement"] = np.where(per1k.notna(), (per1k / c["scaling"]["displacement_per_1000_full"]).clip(0, 1),
                                 (np.log1p(arr) / np.log1p(c["scaling"]["displacement_full"])).clip(0, 1))
    s.loc[arr.isna(), "displacement"] = np.nan
    if "rain3_z" in p:
        # |z| <= 1 is normal variability (0); |z| >= the climate-shock threshold scores 1.
        zt = cfg()["triggers"]["climate_shock"]["rain_anom_z"]
        z, s["rainfall_anomaly_age"] = _carry(p, "rain3_z", 2)
        s["rainfall_anomaly"] = ((z.abs() - 1) / (zt - 1)).clip(0, 1)
    for k in COMPONENT_SOURCES:
        if k not in s:
            s[k] = np.nan
    return s


def need_index(iso3: str) -> pd.DataFrame:
    c = cfg()["need"]
    p = _panel(iso3).reset_index(drop=True)
    s = component_scores(p)
    w = pd.Series(c["weights"], dtype=float)
    comps = list(w.index)
    vals = s[comps]
    avail = vals.notna()
    wsum = avail.mul(w, axis=1).sum(axis=1)
    idx = vals.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, np.nan)
    out = p[["unit_id", "month"]].copy()
    out["need_index"] = idx.round(4)
    out["need_weight_available"] = wsum.round(3)          # share of total weight backed by data
    out["need_n_components"] = avail.sum(axis=1)
    out["need_missing"] = avail.apply(lambda r: ",".join(k for k in comps if not r[k]), axis=1)
    for k in comps:
        out[f"need_{k}"] = vals[k].round(4)
    out["ipc_phase_value"] = s["ipc_phase_value"]
    out["ipc_p3plus_basis"] = s["ipc_p3plus_basis"]
    out["pop_total"] = p["pop_total"] if "pop_total" in p else np.nan
    out["ipc_phase_age"] = s["ipc_phase_age"]
    hn = c["high_need"]
    out["ipc_eligible"] = (s["ipc_phase_value"] >= hn["ipc_phase_min"]).astype("Int8").where(s["ipc_phase_value"].notna())
    out["high_need"] = ((s["ipc_phase_value"] >= hn["ipc_phase_min"]) | (idx >= hn["index_min"])).astype("Int8")
    out.loc[idx.isna() & s["ipc_phase_value"].isna(), "high_need"] = pd.NA
    return out


# ------------------------------------------------------------------ triggers

def evaluate_triggers(iso3: str) -> tuple[pd.DataFrame, dict]:
    t = cfg()["triggers"]
    p = _panel(iso3).reset_index(drop=True)
    alerts = []

    def add(mask, trigger, evidence, value, threshold, source):
        for i in p.index[mask.fillna(False)]:
            alerts.append({"unit_id": p.at[i, "unit_id"], "month": p.at[i, "month"], "trigger": trigger,
                           "value": float(value[i]) if pd.notna(value[i]) else None, "threshold": threshold,
                           "evidence": evidence(i), "source": source})

    # IPC deterioration: current max phase above the previous current classification (within lookback).
    lb = t["ipc_deterioration"]["lookback_months"]
    cs = p[["unit_id", "month", "ipc_cs_max"]].dropna()
    cs = cs.assign(prev=cs.groupby("unit_id")["ipc_cs_max"].shift(), prev_month=cs.groupby("unit_id")["month"].shift())
    cs = cs[(cs["month"] - cs["prev_month"]).dt.days <= lb * 31]
    det = cs[cs["ipc_cs_max"] > cs["prev"]]
    for _, r in det.iterrows():
        alerts.append({"unit_id": r.unit_id, "month": r.month, "trigger": "ipc_deterioration", "value": r.ipc_cs_max,
                       "threshold": f"> previous ({int(r.prev)} in {r.prev_month:%Y-%m})",
                       "evidence": f"IPC current phase {int(r.prev)} ({r.prev_month:%Y-%m}) -> {int(r.ipc_cs_max)} ({r.month:%Y-%m})",
                       "source": "FEWS NET"})
    # New or projected Phase 4-5 area.
    mp = t["ipc_phase45"]["min_phase"]
    add(p["ipc_cs_max"] >= mp, "ipc_phase45_current",
        lambda i: f"IPC current max phase {int(p.at[i, 'ipc_cs_max'])}, {p.at[i, 'ipc_cs_p3plus']:.0%} of area Phase 3+",
        p["ipc_cs_max"], f">= {mp}", "FEWS NET")
    add(p["ipc_proj_max"] >= mp, "ipc_phase45_projected",
        lambda i: f"{p.at[i, 'ipc_proj_scenario']} projection (issued {pd.Timestamp(p.at[i, 'ipc_proj_issued']):%Y-%m-%d}) phase {int(p.at[i, 'ipc_proj_max'])}",
        p["ipc_proj_max"], f">= {mp}", "FEWS NET")
    # Large displacement inflow.
    dl = t["displacement_large"]
    add(p["dtm_arrivals"] >= dl["arrivals_month"], "displacement_large",
        lambda i: f"{int(p.at[i, 'dtm_arrivals'])} new arrivals (conflict {int(p.at[i, 'dtm_arrivals_conflict'])}, hazard {int(p.at[i, 'dtm_arrivals_hazard'])})",
        p["dtm_arrivals"], f">= {dl['arrivals_month']}/month", "IOM DTM")
    add((p["dtm_arrivals_conflict"] >= dl["conflict_arrivals_month"]) & (p["dtm_arrivals"] < dl["arrivals_month"]),
        "displacement_large", lambda i: f"{int(p.at[i, 'dtm_arrivals_conflict'])} conflict-driven arrivals",
        p["dtm_arrivals_conflict"], f">= {dl['conflict_arrivals_month']} conflict arrivals/month", "IOM DTM")

    # Climate shock (rainfall): seasonal deficit (3-month z) or monthly excess (flood-prone rain).
    if "rain3_z" in p:
        zt = t["climate_shock"]["rain_anom_z"]
        add(p["rain3_z"] <= -zt, "climate_shock",
            lambda i: f"3-month rainfall z = {p.at[i, 'rain3_z']:.1f} (deficit vs 1991-2020)",
            p["rain3_z"], f"3-month z <= -{zt}", "CHIRPS v3")
        add(p["rain_z"] >= zt, "climate_shock",
            lambda i: f"monthly rainfall {p.at[i, 'rain_mm']:.0f} mm, z = {p.at[i, 'rain_z']:.1f}"
                      + (f" ({p.at[i, 'rain_pct_normal']:.0%} of normal)" if pd.notna(p.at[i, 'rain_pct_normal']) else ""),
            p["rain_z"], f"monthly z >= {zt}", "CHIRPS v3")

    status = {
        "ipc_deterioration": "evaluated", "ipc_phase45_current": "evaluated", "ipc_phase45_projected": "evaluated",
        "displacement_large": "evaluated (DTM ETT coverage only: 2026-06 onwards, assessed districts)",
        "access_deterioration": "evaluated by server from field reports",
        "gam_emergency": "not evaluable: no IPC AMN / SMART data",
        "outbreak": "not evaluable: no outbreak bulletins",
        "mortality": "not evaluable: no CMR/U5MR data",
        "facility_functionality": "not evaluable: no HeRAMS data",
        "climate_shock": ("evaluated (CHIRPS rainfall: 3-month deficit or 1-month excess; flood hazard not yet available)"
                          if "rain3_z" in p and p["rain3_z"].notna().any() else "not evaluable: no CHIRPS / flood data"),
    }
    return pd.DataFrame(alerts), status


def run(iso3: str) -> dict:
    iso3 = iso3.upper()
    d = path(PROCESSED) / "need"
    d.mkdir(parents=True, exist_ok=True)
    n = need_index(iso3)
    n.to_parquet(d / f"{iso3.lower()}_need.parquet", index=False)
    a, status = evaluate_triggers(iso3)
    a.to_parquet(d / f"{iso3.lower()}_triggers.parquet", index=False)
    meta = {"weights": cfg()["need"]["weights"], "sources": COMPONENT_SOURCES, "trigger_status": status,
            "trigger_thresholds": cfg()["triggers"]}
    (d / f"{iso3.lower()}_need_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"need_rows": len(n), "alerts": len(a),
            "alerts_by_trigger": a["trigger"].value_counts().to_dict() if len(a) else {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="SOM")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(a.country), indent=2, default=str))


if __name__ == "__main__":
    main()

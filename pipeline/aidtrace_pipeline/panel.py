"""Build the unit x month panel for one country.

    python -I -m aidtrace_pipeline.panel --country SOM [--force]

Outputs
  data/processed/panel_<iso3>.parquet                 open sources only (no ACLED content)
  data/processed/panel.parquet                         copy for the pilot country (brief §4.2)
  data/processed/restricted/panel_with_acled_<iso3>.parquet   adds ACLED aggregates (if USE_ACLED)
  data/processed/spine/<iso3>_units.parquet            modelling-unit geometries (GeoParquet)
  data/processed/spine/<iso3>_crosswalk.csv            adm2 p-code -> unit_id
  data/processed/build_report_<iso3>.json              coverage, join rates, notes per source
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import date

import pandas as pd

from .common import PROCESSED, RESTRICTED_OUT, SourceResult, cfg, log, path
from .loaders import acled, chirps, dtm, fewsnet, prmn, static, ucdp, views, wfp
from .spine import crosswalk, units

# Sources whose last month bounds the panel end ("observed", not forecasts or projections).
OBSERVED_FOR_END = ("ucdp", "wfp", "dtm")

# Per-column missing indicators (beyond the per-source flag) for need-index components.
KEY_COLS = ["ipc_cs_max", "ipc_cs_p3plus", "ipc_proj_max", "price_staple_anom", "dtm_arrivals",
            "dtm_arrivals_conflict", "dtm_arrivals_hazard", "views_pred_fatalities"]


def month_range(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.period_range(start.to_period("M"), end.to_period("M"), freq="M").to_timestamp()


def resolve_end(results: dict[str, SourceResult], today: date | None = None) -> pd.Timestamp:
    e = cfg()["panel"]["end_month"]
    if e != "auto":
        return pd.Timestamp(e + "-01")
    today = today or date.today()
    last_complete = (pd.Timestamp(today).to_period("M") - 1).to_timestamp()
    observed = [r.last for k, r in results.items() if k in OBSERVED_FOR_END and r.last is not None]
    return min(last_complete, max(observed)) if observed else last_complete


def attach(panel: pd.DataFrame, name: str, res: SourceResult) -> pd.DataFrame:
    df = res.df.copy()
    df["month"] = pd.to_datetime(df["month"])
    cols = [c for c in res.value_cols if c in df.columns]
    clash = [c for c in cols if c in panel.columns]
    if clash:
        raise ValueError(f"{name}: columns already in panel: {clash}")
    p = panel.merge(df[["unit_id", "month"] + cols], on=["unit_id", "month"], how="left")
    in_cov = pd.Series(False, index=p.index)
    if res.first is not None:
        in_cov = (p["month"] >= res.first) & (p["month"] <= res.last)
    numeric = [c for c in cols if pd.api.types.is_numeric_dtype(p[c])]
    if res.zero_when_absent:
        p.loc[in_cov, numeric] = p.loc[in_cov, numeric].fillna(0)
        p[f"{name}_missing"] = (~in_cov).astype("int8")
    else:
        p[f"{name}_missing"] = p[cols].isna().all(axis=1).astype("int8")
    p.loc[~in_cov, cols] = None     # outside source coverage: NaN / NaT, never zero
    # Sources with no data for this country (e.g. DTM in Kenya) arrive as empty object columns:
    # make them float NaN so downstream maths treats them as missing numbers.
    for c in cols:
        if c in NON_NUMERIC:
            if c.endswith("_issued"):
                p[c] = pd.to_datetime(p[c], errors="coerce")
        elif p[c].dtype == object:
            p[c] = pd.to_numeric(p[c], errors="coerce").astype(float)
    return p


NON_NUMERIC = {"ipc_proj_scenario", "ipc_proj_issued", "views_issued"}


def build_panel(iso3: str, force: bool = False, today: date | None = None) -> dict:
    iso3 = iso3.upper()
    st = static.refresh(iso3, force)           # first: FEWS NET population weighting needs its weight grid
    results = {"ucdp": ucdp.refresh(iso3, force), "fewsnet": fewsnet.refresh(iso3, force),
               "wfp": wfp.refresh(iso3, force), "dtm": dtm.refresh(iso3, force),
               "views": views.refresh(iso3, force), "chirps": chirps.refresh(iso3, force),
               "prmn": prmn.refresh(iso3, force)}
    start = pd.Timestamp(cfg()["panel"]["start_month"] + "-01")
    end = resolve_end(results, today)

    u = units(iso3)
    spine = pd.MultiIndex.from_product([u["unit_id"], month_range(start, end)], names=["unit_id", "month"])
    panel = pd.DataFrame(index=spine).reset_index()
    panel = panel.merge(u[["unit_id", "unit_name", "adm1_pcode", "adm1_name", "country", "area_km2"]], on="unit_id")
    for name, res in results.items():
        panel = attach(panel, name, res)
    # Static district attributes (constant over time; NaN where a source does not cover the country)
    panel = panel.merge(st.drop(columns=["area_km2"]), on="unit_id", how="left")
    panel["pop_missing"] = panel["pop_total"].isna().astype("int8")
    # COW NMC country-year (calendar year of the month; NaN after the last NMC year, 2022)
    nmc = static.nmc()
    nmc = nmc[nmc["iso3"] == iso3].drop(columns="iso3")
    panel["year"] = panel["month"].dt.year
    panel = panel.merge(nmc, on="year", how="left").drop(columns="year")
    for c in KEY_COLS:
        if c in panel.columns:
            panel[f"{c}_missing"] = panel[c].isna().astype("int8")
    panel = panel.sort_values(["unit_id", "month"]).reset_index(drop=True)

    out = path(PROCESSED) / f"panel_{iso3.lower()}.parquet"
    panel.to_parquet(out, index=False)
    if iso3 == "SOM":   # pilot country: brief names data/processed/panel.parquet
        shutil.copyfile(out, path(PROCESSED) / "panel.parquet")

    spine_dir = path(PROCESSED) / "spine"
    spine_dir.mkdir(parents=True, exist_ok=True)
    u.to_parquet(spine_dir / f"{iso3.lower()}_units.parquet")
    crosswalk(iso3).to_csv(spine_dir / f"{iso3.lower()}_crosswalk.csv", index=False, encoding="utf-8")

    report = {
        "country": iso3, "built": str(date.today()), "start": f"{start:%Y-%m}", "end": f"{end:%Y-%m}",
        "units": int(len(u)), "rows": int(len(panel)), "columns": int(panel.shape[1]),
        "sources": {k: {"first": None if r.first is None else f"{r.first:%Y-%m}",
                        "last": None if r.last is None else f"{r.last:%Y-%m}",
                        "zero_when_absent": r.zero_when_absent, "note": r.note,
                        "share_unit_months_missing": float(panel[f"{k}_missing"].mean())}
                    for k, r in results.items()},
    }

    if cfg()["use_acled"]:
        a = acled.refresh(iso3, force)
        pa = attach(panel, "acled", a)
        rdir = path(RESTRICTED_OUT)
        rdir.mkdir(parents=True, exist_ok=True)
        pa.to_parquet(rdir / f"panel_with_acled_{iso3.lower()}.parquet", index=False)
        report["acled"] = {"first": f"{a.first:%Y-%m}", "last": f"{a.last:%Y-%m}", "note": a.note,
                           "output": "data/processed/restricted (gitignored)"}
    (path(PROCESSED) / f"build_report_{iso3.lower()}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="SOM")
    ap.add_argument("--force", action="store_true", help="rebuild every source regardless of timestamps")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rep = build_panel(a.country, a.force)
    log.info("panel %s: %s units x %s..%s = %s rows, %s cols", rep["country"], rep["units"], rep["start"],
             rep["end"], rep["rows"], rep["columns"])
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()

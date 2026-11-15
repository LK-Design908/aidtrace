"""IOM DTM Somalia Emergency Trend Tracking -> district-month displacement inflow.

- Joined on OCHA district p-codes (never names).
- Conflict-driven and natural-hazard-driven arrivals are kept separate (brief §4.1).
- A district-month counts as observed only if at least one settlement in it was assessed.
  Unassessed districts are NaN, not 0, because ETT coverage is urban-focused and partial.
- Figures are weekly key-informant estimates (see the workbook's Methodology sheet).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..common import SourceResult, country_cfg, excel_or_date, path, refresh_source, to_month
from ..spine import map_pcodes

SCHEMAS = {
    # Somalia Emergency Trend Tracking: weekly settlement-level KII rows.
    "som_ett": {"sheet": 0, "cols": {
        "Date of Assessment": "date", "OCHA District Pcode": "pcode", "Settlement ID": "settlement_id",
        "Main Cause of Displacement": "cause", "Total new arrivals since last week": "arrivals",
        "Total number of departures since last week": "departures"}},
    # South Sudan Event Tracking: one row per displacement/return event (> 50 households). IDPs and
    # returnees are both counted as arrivals at the location; there is no departures field.
    "ssd_et": {"sheet": "DTM_Event Tracking_Dataset", "cols": {
        "Assessment Date": "date", "Event Location:  County PCODE": "pcode", "Event Location SSID": "settlement_id",
        "Movement Trigger": "cause", "Total number of individuals": "arrivals"}},
}


def has_dtm(iso3: str) -> bool:
    return "dtm_file" in country_cfg(iso3)


def file(iso3: str) -> Path:
    return path(country_cfg(iso3)["dtm_file"])


@lru_cache
def read_rows(iso3: str) -> pd.DataFrame:
    sch = SCHEMAS[country_cfg(iso3).get("dtm_schema", "som_ett")]
    df = pd.read_excel(file(iso3), sheet_name=sch["sheet"], usecols=list(sch["cols"]))
    df = df.rename(columns=sch["cols"])
    if "departures" not in df:
        df["departures"] = pd.NA
    df["date"] = excel_or_date(df["date"])
    df["month"] = to_month(df["date"])
    for c in ("arrivals", "departures"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["cause"] = df["cause"].astype("string").str.strip()
    return df


def _cause_group(c: pd.Series) -> pd.Series:
    lc = c.str.lower().fillna("")
    return pd.Series(pd.Categorical(
        ["conflict" if "conflict" in x else
         "hazard" if any(k in x for k in ("hazard", "drought", "flood", "disaster")) else "other" for x in lc]),
        index=c.index)


VALUE_COLS = ["dtm_assessments", "dtm_settlements", "dtm_arrivals", "dtm_departures",
              "dtm_arrivals_conflict", "dtm_arrivals_hazard", "dtm_arrivals_other"]


def empty(iso3: str) -> SourceResult:
    """No DTM data for this country: every unit-month is missing (never zero)."""
    return SourceResult(pd.DataFrame(columns=["unit_id", "month"] + VALUE_COLS), None, None, False, VALUE_COLS,
                        f"no IOM DTM data with p-codes for {iso3}")


def build(iso3: str) -> SourceResult:
    df = read_rows(iso3).copy()
    df["unit_id"] = map_pcodes(df["pcode"], iso3)
    n, n_joined = len(df), int(df["unit_id"].notna().sum())
    df = df[df["unit_id"].notna()]
    df["cause_group"] = _cause_group(df["cause"])
    g = df.groupby(["unit_id", "month"])
    out = pd.DataFrame({
        "dtm_assessments": g.size(),
        "dtm_settlements": g["settlement_id"].nunique(),
        "dtm_arrivals": g["arrivals"].sum(min_count=1),
        "dtm_departures": g["departures"].sum(min_count=1),
    })
    for grp in ("conflict", "hazard", "other"):
        sub = df[(df["cause_group"] == grp) & (df["arrivals"] > 0)]
        out[f"dtm_arrivals_{grp}"] = sub.groupby(["unit_id", "month"])["arrivals"].sum()
    arr_cols = [f"dtm_arrivals_{x}" for x in ("conflict", "hazard", "other")]
    out[arr_cols] = out[arr_cols].fillna(0)   # assessed district-month: absent cause = 0 arrivals
    out = out.reset_index()
    note = f"{file(iso3).name}: {n} rows, {n_joined} joined by p-code; causes: {sorted(df['cause'].dropna().unique())}"
    res = SourceResult(out, df["month"].min(), df["month"].max(), False,
                       [c for c in out.columns if c not in ("unit_id", "month")], note)
    res.df.attrs.update(n_rows=n, n_rows_joined=n_joined)
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    if not has_dtm(iso3):
        return empty(iso3)
    return refresh_source("dtm", iso3, [file(iso3), path(country_cfg(iso3)["cod_ab_zip"])], build, force)

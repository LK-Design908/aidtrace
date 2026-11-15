"""Loader and panel tests (brief §8): row counts, date coverage, join rates (fail below 95%),
missing-data handling, ACLED isolation and leakage guards.

Run:  python -I -m pytest pipeline/tests -q
"""
from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import box

from aidtrace_pipeline import common
from aidtrace_pipeline.common import path
from aidtrace_pipeline.loaders import acled, dtm, fewsnet, ucdp, views, wfp
from aidtrace_pipeline.panel import build_panel
from aidtrace_pipeline.spine import admin2, crosswalk, units

ISO = "SOM"
MIN_JOIN = 0.95


@pytest.fixture(scope="session")
def built():
    return build_panel(ISO)


@pytest.fixture(scope="session")
def panel(built):
    return pd.read_parquet(path("data/processed/panel.parquet"))


# ------------------------------------------------------------------ spine

def test_spine_counts_and_pcodes():
    a = admin2(ISO)
    assert len(a) == 91
    assert a["adm2_pcode"].is_unique
    assert "SO22XX" in set(a["adm2_pcode"]), "synthetic code for Banadir 'Unspecified' missing"
    assert a.crs.to_epsg() == 4326
    cw = crosswalk(ISO)
    assert cw["unit_id"].notna().all()
    assert (cw.loc[cw["adm1_pcode"] == "SO22", "unit_id"] == "SO22").all(), "Banadir not merged"
    u = units(ISO)
    assert len(u) == 91 - 18 + 1
    assert u["unit_id"].is_unique and u.is_valid.all()


# ------------------------------------------------------------------ UCDP

def test_ucdp_join_rate_and_coverage():
    r = ucdp.build(ISO)
    n, j = r.df.attrs["n_precise"], r.df.attrs["n_precise_joined"]
    assert n > 4000                       # precise own-country events since 2012 (join-rate denominator)
    assert j / n >= MIN_JOIN, f"UCDP join rate {j / n:.1%} below 95%"
    assert r.first == pd.Timestamp("1989-01-01")
    assert r.last >= pd.Timestamp("2025-12-01")
    assert r.zero_when_absent


def test_ucdp_counts_reconcile_with_raw():
    r = ucdp.build(ISO)
    ev = ucdp.read_events(ISO)
    precise = ev[ev["where_prec"] <= common.cfg()["ucdp"]["max_where_prec_for_district"]]
    single = precise["date_start"].dt.to_period("M") == precise["date_end"].dt.to_period("M")
    assert int(r.df["ucdp_events"].sum()) == int(single.sum())
    assert int(r.df["ucdp_events_multimonth"].sum()) == int((~single).sum())


# ------------------------------------------------------------------ ACLED (restricted)

def test_acled_join_rate_and_isolation(built, panel):
    r = acled.build(ISO)
    n, j = r.df.attrs["n_precise"], r.df.attrs["n_precise_joined"]
    assert j / n >= MIN_JOIN, f"ACLED join rate {j / n:.1%} below 95%"
    # Only aggregated count columns: nothing that could reconstruct events.
    forbidden = {"notes", "actor1", "actor2", "latitude", "longitude", "event_id_cnty", "location", "source"}
    assert not forbidden & set(r.df.columns)
    assert not any(c.startswith("acled") for c in panel.columns), "ACLED content leaked into open panel"
    restricted = path("data/processed/restricted/panel_with_acled_som.parquet")
    assert restricted.exists()
    assert "data/processed/" in (path(".gitignore").read_text())


def test_acled_partial_months_excluded():
    r = acled.build(ISO)
    # Export window 2024-11-09..2025-11-07: both edge months are partial.
    assert r.first == pd.Timestamp("2024-12-01") and r.last == pd.Timestamp("2025-10-01")


def test_use_acled_env_override(monkeypatch):
    common.cfg.cache_clear()
    monkeypatch.setenv("USE_ACLED", "false")
    try:
        assert common.cfg()["use_acled"] is False
    finally:
        monkeypatch.delenv("USE_ACLED")
        common.cfg.cache_clear()


# ------------------------------------------------------------------ FEWS NET

def test_fewsnet_kml_attributes_parsed():
    g = fewsnet.read_kml(path("data/raw/fewsnet/SO_202610_CS.kml"))
    assert len(g) == 5
    assert g["value"].notna().all() and set(g["scenario"]) == {"CS"}


def test_fewsnet_empty_geojson_tolerated():
    assert len(fewsnet.read_geojson(path("data/raw/fewsnet/SO_202610.geojson"))) == 0


def test_fewsnet_coverage_and_separation():
    r = fewsnet.build(ISO)
    oct26 = r.df[r.df["month"] == "2026-10-01"]
    assert oct26["ipc_cs_coverage"].mean() >= MIN_JOIN, "CS Oct 2026 does not cover Somalia units"
    assert oct26["ipc_cs_max"].between(1, 5).all()
    proj = r.df.dropna(subset=["ipc_proj_max"])
    assert (pd.to_datetime(proj["ipc_proj_issued"]) <= proj["month"]).all(), "projection issued after target month"
    assert not r.zero_when_absent


# ------------------------------------------------------------------ WFP

def test_wfp_market_join_and_baseline():
    r = wfp.build(ISO)
    assert r.df.attrs["n_markets_joined"] / r.df.attrs["n_markets"] >= MIN_JOIN
    first_anom = r.df.dropna(subset=["price_staple_anom"])["month"].min()
    min_obs = common.cfg()["wfp"]["baseline_min_obs"]
    assert first_anom >= r.first + pd.DateOffset(months=min_obs), "anomaly computed without a baseline"


# ------------------------------------------------------------------ DTM

def test_dtm_pcode_join():
    r = dtm.build(ISO)
    assert r.df.attrs["n_rows_joined"] / r.df.attrs["n_rows"] >= MIN_JOIN
    assert r.first == pd.Timestamp("2026-06-01")
    d = r.df
    parts = d[["dtm_arrivals_conflict", "dtm_arrivals_hazard", "dtm_arrivals_other"]].sum(axis=1)
    assert (parts.round() == d["dtm_arrivals"].fillna(0).round()).all()


# ------------------------------------------------------------------ VIEWS

def test_views_grid_and_months():
    g = views.pg_geom(pd.Series([1, 720, 721]))
    assert g.iloc[0].equals(box(-180, -90, -179.5, -89.5))
    assert g.iloc[1].equals(box(179.5, -90, 180, -89.5))
    assert g.iloc[2].equals(box(-180, -89.5, -179.5, -89))
    assert views.month_id_to_ts(pd.Series([1, 562])).tolist() == [pd.Timestamp("1980-01-01"), pd.Timestamp("2026-10-01")]


# ------------------------------------------------------------------ panel

def test_panel_shape_and_keys(built, panel):
    months = pd.period_range(built["start"], built["end"], freq="M")
    assert len(panel) == built["units"] * len(months)
    assert not panel.duplicated(["unit_id", "month"]).any()
    assert panel["month"].min() == pd.Timestamp("2017-01-01")


def test_panel_missing_not_zero(panel):
    after = panel["month"] > ucdp.refresh("SOM").last
    assert panel.loc[after, "ucdp_events"].isna().all(), "UCDP must be NaN after its coverage ends"
    assert (panel.loc[after, "ucdp_missing"] == 1).all()
    assert panel.loc[~after, "ucdp_events"].notna().all()
    # Snapshot sources are never zero-filled
    for col, flag in [("ipc_cs_max", "ipc_cs_max_missing"), ("price_staple_anom", "price_staple_anom_missing"),
                      ("dtm_arrivals", "dtm_arrivals_missing")]:
        assert (panel[col].isna() == panel[flag].astype(bool)).all()
    # FEWS NET history (fetched 2017- packages): IPC observed long before the owner-supplied 2024+ files
    hist = panel.loc[panel["month"] < "2024-01-01"]
    assert hist["ipc_cs_max"].notna().any() and hist.groupby("month")["ipc_cs_max"].count().gt(0).sum() >= 15
    assert panel.loc[panel["month"] < "2026-06-01", "dtm_arrivals"].isna().all()

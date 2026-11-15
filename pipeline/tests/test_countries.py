"""Milestone 8: every SRP country's spine, loaders and panel (join rates fail below 95%)."""
from __future__ import annotations

import pandas as pd
import pytest

from aidtrace_pipeline.common import path
from aidtrace_pipeline.loaders import dtm, fewsnet, ucdp, wfp
from aidtrace_pipeline.panel import build_panel
from aidtrace_pipeline.spine import admin2, units

COUNTRIES = ["DJI", "ETH", "KEN", "SSD", "SDN"]
EXPECTED_ADM2 = {"DJI": 20, "ETH": 107, "KEN": 290, "SSD": 79, "SDN": 189}
MIN_JOIN = 0.95


@pytest.fixture(scope="module", params=COUNTRIES)
def iso(request):
    build_panel(request.param)            # uses cached source tables when inputs are unchanged
    return request.param


def test_spine(iso):
    a = admin2(iso)
    assert len(a) == EXPECTED_ADM2[iso] and a["adm2_pcode"].is_unique
    assert a.crs.to_epsg() == 4326 and a.is_valid.all()
    u = units(iso)
    assert len(u) == len(a) and u["unit_id"].is_unique


def test_ucdp_coverage_is_dataset_wide(iso):
    # Regression: coverage must end with the GED release, not with this country's last event.
    r = ucdp.refresh(iso)
    assert r.last == ucdp.to_month(pd.Series([ucdp._srp_events()["date_start"].max()])).iloc[0]
    assert r.last >= pd.Timestamp("2025-12-01")


def test_ucdp_join_rate(iso):
    r = ucdp.refresh(iso)
    n, j = r.df.attrs["n_precise"], r.df.attrs["n_precise_joined"]
    if n == 0:
        pytest.skip(f"{iso}: no precise UCDP events since 2012")
    assert j / n >= MIN_JOIN, f"{iso} UCDP join rate {j / n:.1%}"


def test_wfp_market_join(iso):
    r = wfp.refresh(iso)
    assert r.df.attrs["n_markets"] > 0
    assert r.df.attrs["n_markets_joined"] / r.df.attrs["n_markets"] >= MIN_JOIN


def test_dtm_where_available(iso):
    r = dtm.refresh(iso)
    if not dtm.has_dtm(iso):
        assert r.first is None and r.df.empty
        return
    assert r.df.attrs["n_rows_joined"] / r.df.attrs["n_rows"] >= MIN_JOIN


def test_fewsnet_consistent_columns(iso):
    r = fewsnet.refresh(iso)
    assert set(fewsnet.FEWS_COLS) <= set(r.df.columns)
    if r.first is None:
        assert iso == "DJI"


def test_panel_missing_not_zero(iso):
    p = pd.read_parquet(path(f"data/processed/panel_{iso.lower()}.parquet"))
    assert not p.duplicated(["unit_id", "month"]).any()
    assert p["month"].min() == pd.Timestamp("2017-01-01")
    last = ucdp.refresh(iso).last
    after = p["month"] > last
    assert p.loc[after, "ucdp_events"].isna().all()
    assert p.loc[~after, "ucdp_events"].notna().all()
    for col, flag in [("ipc_cs_max", "ipc_cs_max_missing"), ("price_staple_anom", "price_staple_anom_missing"),
                      ("dtm_arrivals", "dtm_arrivals_missing")]:
        assert (p[col].isna() == p[flag].astype(bool)).all()
    if not dtm.has_dtm(iso):
        assert p["dtm_missing"].eq(1).all()

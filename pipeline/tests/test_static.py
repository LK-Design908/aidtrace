"""New data: WorldPop population/buildings/urban extents, COW NMC and MIDLOC, population-weighted IPC."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aidtrace_pipeline.common import path
from aidtrace_pipeline.loaders import static

ALL = ["SOM", "ETH", "KEN", "SSD", "SDN", "DJI"]


def st(iso: str) -> pd.DataFrame:
    return pd.read_parquet(path(f"data/processed/static/{iso.lower()}_static.parquet"))


@pytest.mark.parametrize("iso,lo,hi", [("SSD", 9e6, 14e6), ("KEN", 40e6, 60e6)])
def test_population_totals_plausible(iso, lo, hi):
    s = st(iso)
    assert lo < s["pop_total"].sum() < hi, s["pop_total"].sum()
    assert (s["pop_u5"] <= s["pop_total"]).all() and (s["pop_u5"] / s["pop_total"]).between(0.05, 0.30).all()
    assert (s["weight_source"] == "population").all()
    assert path(f"data/processed/static/{iso.lower()}_weight.tif").exists()


@pytest.mark.parametrize("iso,lo,hi", [("SOM", 10e6, 20e6), ("SDN", 30e6, 55e6), ("DJI", 0.6e6, 1.5e6)])
def test_fetched_worldpop_totals(iso, lo, hi):
    """WorldPop 2020 totals fetched from worldpop.org (no age bands in that product)."""
    s = st(iso)
    assert lo < s["pop_total"].sum() < hi, s["pop_total"].sum()
    assert s["pop_u5"].isna().all()
    assert (s["weight_source"] == "population").all()


def test_somalia_buildings_and_urban():
    s = st("SOM")
    assert s["bldg_count"].sum() > 1e6
    assert s["bldg_urban_share"].between(0, 1).all() and s["urban_extent_share"].between(0, 1).all()
    assert s["bldg_imagery_year"].dropna().between(2009, 2021).all()
    assert s["urban_points"].sum() > 0


def test_no_exposure_data_is_nan_not_zero():
    s = st("ETH")
    assert s["pop_total"].isna().all() and s["bldg_count"].isna().all()
    assert (s["weight_source"] == "area").all()


@pytest.mark.parametrize("iso", ALL)
def test_midloc_static_counts(iso):
    s = st(iso)
    assert (s["mid_incidents_1992_2010"] >= s["mid_disputes_1992_2010"]).all()
    assert s["mid_incidents_1992_2010"].notna().all()


def test_nmc_country_years():
    n = static.nmc()
    assert set(n["iso3"]) == set(ALL)
    assert n.groupby("iso3")["year"].max().eq(2022).all()
    assert n.loc[n["iso3"] == "SSD", "year"].min() == 2011


@pytest.mark.parametrize("iso", ["SSD", "KEN", "SOM"])
def test_people_weighted_ipc(iso):
    p = pd.read_parquet(path(f"data/processed/panel_{iso.lower()}.parquet"))
    obs = p.dropna(subset=["ipc_cs_p3plus_pop"])
    assert len(obs) > 0, f"{iso}: no people-weighted IPC"
    assert obs["ipc_cs_p3plus_pop"].between(0, 1).all()


def test_area_only_countries_have_no_people_weighting():
    p = pd.read_parquet(path("data/processed/panel_eth.parquet"))
    assert p["ipc_cs_p3plus_pop"].isna().all() and p["ipc_cs_p3plus"].notna().any()


@pytest.mark.parametrize("iso", ["KEN", "SOM"])
def test_panel_static_and_nmc(iso):
    p = pd.read_parquet(path(f"data/processed/panel_{iso.lower()}.parquet"))
    assert p.groupby("unit_id")["mid_incidents_1992_2010"].nunique().le(1).all()     # constant over time
    assert p.loc[p["month"].dt.year > 2022, "nmc_cinc"].isna().all()                  # no NMC after 2022
    assert p.loc[p["month"].dt.year <= 2022, "nmc_cinc"].notna().all()


def test_nmc_feature_is_lagged_one_year():
    from aidtrace_pipeline.features import build_features
    f = build_features("SOM")
    n = static.nmc()
    som = n[n["iso3"] == "SOM"].set_index("year")["nmc_cinc"]
    row = f[f["origin"] == pd.Timestamp("2020-06-01")].iloc[0]
    assert np.isclose(row["nmc_cinc"], som[2019]), "origin 2020 must use NMC 2019"
    assert f.loc[f["origin"] >= pd.Timestamp("2024-01-01"), "nmc_cinc"].isna().all()   # 2023 not in NMC v7

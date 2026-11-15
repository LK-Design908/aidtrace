"""Model tests: no future leakage in features, temporal fold integrity, scoring outputs, ACLED isolation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aidtrace_pipeline import features as F
from aidtrace_pipeline import model as M
from aidtrace_pipeline.common import path

ISO = "SOM"


@pytest.fixture(scope="module")
def ds1():
    return M.dataset(ISO, 1, use_acled=False)


def test_features_do_not_use_future(monkeypatch):
    """Scrambling all UCDP data after an origin must not change features at that origin."""
    origin = pd.Timestamp("2022-06-01")
    base = F.build_features(ISO)
    real = F.conflict_history

    def scrambled(iso3):
        h = real(iso3).copy()
        fut = h["month"] > origin
        rng = np.random.default_rng(1)
        for c in ("ucdp_fatal_any", "ucdp_events", "ucdp_deaths_best", "ucdp_events_statebased",
                  "ucdp_imprecise_events_adm1", "ucdp_imprecise_events_country"):
            h.loc[fut, c] = rng.integers(0, 50, fut.sum())
        return h

    monkeypatch.setattr(F, "conflict_history", scrambled)
    alt = F.build_features(ISO)
    cols = [c for c in base.columns if c not in ("unit_id", "origin")]
    a = base[base["origin"] == origin].set_index("unit_id")[cols]
    b = alt[alt["origin"] == origin].set_index("unit_id")[cols]
    pd.testing.assert_frame_equal(a, b)


def test_target_is_future_and_projection_not_leaked(ds1):
    f = ds1.frame
    assert (f["target_month"] == f["origin"] + pd.DateOffset(months=1)).all()
    from aidtrace_pipeline.loaders import ucdp
    assert f.loc[f["target_month"] > ucdp.refresh("SOM").last, "y"].isna().all(), "target beyond UCDP coverage must be NaN"
    p = pd.read_parquet(path("data/processed/panel_som.parquet"), columns=["unit_id", "month", "ipc_proj_issued"])
    m = f[f["ipc_proj_max_target"].notna()].merge(p, left_on=["unit_id", "target_month"], right_on=["unit_id", "month"])
    assert (pd.to_datetime(m["ipc_proj_issued"]) <= m["origin"]).all()


def test_expanding_cv_never_trains_on_test_period(ds1, monkeypatch):
    seen = []

    def spy(model, train, test, feats, seed=0):
        seen.append((train["target_month"].max(), test["target_month"].min()))
        return np.full(len(test), 0.5)

    monkeypatch.setattr(M, "fit_predict", spy)
    M.expanding_cv(ds1, ["logit"])
    assert seen and all(tr < te for tr, te in seen)


def test_bands_and_scores():
    assert M.band(0.05) == "low" and M.band(0.2) == "elevated" and M.band(0.45) == "high" and M.band(0.9) == "severe"
    r = pd.read_parquet(path("data/processed/risk/som_risk.parquet"))
    assert set(r["horizon"]) == {1, 3}
    assert r.groupby("horizon")["unit_id"].nunique().eq(74).all()
    assert ((r["prob_lo"] <= r["prob"]) & (r["prob"] <= r["prob_hi"])).all()
    assert r["prob"].between(0, 1).all()
    assert not any("acled" in c for c in r.columns) and (r["model"] == "lightgbm_bagged_pooled_no_acled").all()


@pytest.mark.parametrize("iso,n_units", [("SOM", 74), ("ETH", 107), ("KEN", 290), ("SSD", 79), ("SDN", 189), ("DJI", 20)])
def test_pooled_scores_every_country(iso, n_units):
    """scoring: pooled -> one model scores every SRP country from the same origin."""
    from aidtrace_pipeline.common import cfg
    assert cfg()["model"]["scoring"] == "pooled"
    r = pd.read_parquet(path(f"data/processed/risk/{iso.lower()}_risk.parquet"))
    assert (r["model"] == "lightgbm_bagged_pooled_no_acled").all()
    assert r.groupby("horizon")["unit_id"].nunique().eq(n_units).all()
    assert r["unit_id"].str[:2].eq(cfg()["countries"][iso]["iso2"]).all() or iso == "SOM"
    assert r["origin"].nunique() == 1
    if iso == "DJI":
        assert r["note"].str.contains("Not evaluable").all()


def test_acled_model_outputs_only_restricted():
    open_dir = path("data/processed/model")
    assert not any("acled" in p.name for p in open_dir.glob("*"))
    assert any(path("data/processed/restricted/model").glob("cv_predictions_acled_*"))

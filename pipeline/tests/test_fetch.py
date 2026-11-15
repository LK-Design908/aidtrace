"""Allowlisted fetcher: refusals, redirect rule, no-overwrite promotion, held items, PRMN gating, CHIRPS."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from aidtrace_pipeline.common import path
from aidtrace_pipeline.fetch import core
from aidtrace_pipeline.fetch.core import NotAllowed, check_allowed, check_redirect


@pytest.mark.parametrize("source,url", [
    ("not_listed", "https://example.org/x.csv"),                                   # not on allowlist
    ("ucdp", "https://www.kaggle.com/datasets/ucdp/ged.csv"),                      # mirror host
    ("ucdp", "https://raw.githubusercontent.com/someone/ucdp/main/ged.csv"),       # mirror host
    ("ucdp", "http://ucdp.uu.se/downloads/ged.csv"),                               # not https
    ("ucdp", "https://ucdp.uu.se/downloads/run.exe"),                              # executable
    ("herams", "https://herams.org/data"),                                         # restricted / manual
])
def test_refusals(source, url):
    with pytest.raises(NotAllowed):
        check_allowed(source, url)


def test_acled_refused_without_permission(monkeypatch):
    monkeypatch.delenv("ACLED_API_KEY", raising=False)
    with pytest.raises(NotAllowed):
        check_allowed("acled", "https://acleddata.com/api/acled/read")


def test_allowed_official_hosts():
    check_allowed("ucdp", "https://ucdp.uu.se/downloads/index.html")
    check_allowed("wfp_prices", "https://data.humdata.org/api/3/action/package_show")


def test_hdx_redirect_rule_is_narrow():
    hdx = "https://data.humdata.org/dataset/x/resource/y/download/f.csv"
    assert check_redirect(hdx, "https://s3.us-east-1.amazonaws.com/hdx-production-filestore/resources/y/f.csv")
    assert not check_redirect(hdx, "https://s3.us-east-1.amazonaws.com/other-bucket/f.csv")
    assert not check_redirect("https://evil.example/x", "https://s3.us-east-1.amazonaws.com/hdx-production-filestore/f")
    assert not check_redirect(hdx, "http://s3.us-east-1.amazonaws.com/hdx-production-filestore/f.csv")


def test_unsafe_filenames_refused():
    for bad in ("../x.csv", "a/b.csv", "x.ps1"):
        with pytest.raises(NotAllowed):
            core.fetch_file("ucdp", "vtest", "https://ucdp.uu.se/downloads/x", bad)


def test_promote_never_overwrites_and_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "raw_dir", lambda s, v: tmp_path / "raw" / s / v)
    logged = []
    monkeypatch.setattr(core, "log_event", lambda e, d, **k: logged.append(e))
    f = tmp_path / "f.csv"
    f.write_text("a\n1\n")
    d = core.Download("ucdp", "v1", "https://ucdp.uu.se/x", f, "sha", 4, "now", "lic")
    assert core.promote(d, core.CheckResult(False, ["jump"], {})) is None and logged == ["held"]
    dest = core.promote(d, core.CheckResult(True, [], {}))
    assert dest.read_text() == "a\n1\n"
    f.write_text("CHANGED")
    assert core.promote(d, core.CheckResult(True, [], {})).read_text() == "a\n1\n"     # never overwritten


def test_provenance_records_licence_and_version():
    recs = [r for r in core.provenance() if r["event"] == "promoted"]
    assert recs, "no promoted downloads recorded"
    for r in recs:
        for k in ("url", "accessed", "version", "licence", "sha256"):
            assert r.get(k), (k, r)
        assert "?" not in r["url"]                      # no signed query strings logged


def test_licences_auto_block_present():
    t = path("data/LICENCES.md").read_text(encoding="utf-8")
    assert "AUTO:FETCHED START" in t and "AUTO:FETCHED END" in t


def test_prmn_is_gated_until_reviewed():
    from aidtrace_pipeline.loaders import prmn
    cw = pd.read_csv(path(prmn.CROSSWALK), dtype=str).fillna("")
    if (cw["reviewed"].str.lower() == "yes").any():
        pytest.skip("crosswalk has been reviewed by a human")
    r = prmn.build("SOM")
    assert r.df.empty and r.first is None and "not yet reviewed" in r.note


def test_chirps_panel_values():
    p = pd.read_parquet(path("data/processed/panel_som.parquet"))
    if p["rain_mm"].notna().sum() == 0:
        pytest.skip("CHIRPS not promoted yet")
    v = p["rain_mm"].dropna()
    assert (v >= 0).all() and v.max() < 1500
    z = p["rain3_z"].dropna()
    assert len(z) > 0 and abs(z.mean()) < 0.5 and 0.5 < z.std() < 2.0     # anomalies are centred
    assert p.loc[p["month"] < pd.Timestamp("2026-01-01"), "chirps_missing"].lt(1).any()


def test_prmn_uses_only_reviewed_rows():
    from aidtrace_pipeline.loaders import prmn
    cw = pd.read_csv(path(prmn.CROSSWALK), dtype=str).fillna("")
    yes = cw[cw["reviewed"].str.lower() == "yes"]
    if yes.empty:
        pytest.skip("crosswalk not reviewed")
    d = prmn.read()
    d = d[d["month"] >= pd.Timestamp("2017-01-01")]
    keys = set(zip(yes["prmn_region"], yes["prmn_district"]))
    expected = d[[k in keys for k in zip(d["region"], d["district"])]]["people"].sum()
    p = pd.read_parquet(path("data/processed/panel_som.parquet"))
    assert np.isclose(p["prmn_arrivals"].sum(), expected)
    assert p.loc[p["month"] > prmn.build("SOM").last, "prmn_arrivals"].isna().all()   # never zero-filled

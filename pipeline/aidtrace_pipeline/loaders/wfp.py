"""WFP market prices -> district-month staple price anomalies.

- Markets are placed by coordinates (WFP admin names are never used for joining).
- A series is (market_id, commodity_id, unit, pricetype, currency). Each series stays in its own
  local currency, which avoids mixing SOS and SLS in Somalia. Anomalies are computed per series
  and only then aggregated.
- Anomaly = log(price_t) - log(mean price over the previous `baseline_months`, needing at least
  `baseline_min_obs` observations), in a trailing window that excludes month t. This is a rolling
  baseline, not a seasonal one: with data only from 2025, a seasonal baseline is impossible.
  Add the WFP history to enable one.
- District value = median anomaly across staple series in the unit (with the series count).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from ..common import SourceResult, assign_points, cfg, country_cfg, path, refresh_source, to_month
from ..spine import units


def fetched_files(iso3: str | None = None) -> list[Path]:
    """Latest promoted HDX full-history file per country (data/raw/fetched_wfp_prices/<ISO>_<date>/...)."""
    base = path("data/raw/fetched_wfp_prices")
    if not base.exists():
        return []
    out = []
    for iso in ([iso3.upper()] if iso3 else sorted({p.name.split("_")[0] for p in base.iterdir() if p.is_dir()})):
        vs = sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith(iso + "_"))
        if vs:
            out += sorted(vs[-1].glob("*.csv"))
    return out


def files() -> list[Path]:
    return [path(f) for f in cfg()["wfp"]["files"]] + fetched_files()


@lru_cache
def read_prices(iso3: str) -> pd.DataFrame:
    frames = []
    for f in [path(x) for x in cfg()["wfp"]["files"]]:
        for ch in pd.read_csv(f, chunksize=250_000, low_memory=False, encoding="utf-8"):
            frames.append(ch[ch["countryiso3"] == iso3.upper()])
    from ..fetch.connectors import read_wfp_hdx
    for f in fetched_files(iso3):                           # full HDX history for this country
        h = read_wfp_hdx(f)
        h["countryiso3"] = iso3.upper()
        frames.append(h)
    df = pd.concat(frames, ignore_index=True)
    for c in ("market_id", "commodity_id"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    key = ["countryiso3", "date", "market_id", "commodity_id", "unit", "pricetype", "currency"]
    df = df.drop_duplicates(subset=key, keep="last")         # overlap between global and HDX files
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = to_month(df["date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["usdprice"] = pd.to_numeric(df["usdprice"], errors="coerce")
    return df[df["price"] > 0]


def build(iso3: str) -> SourceResult:
    c = cfg()["wfp"]
    df = read_prices(iso3).copy()
    for col in ("latitude", "longitude"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mk = df.drop_duplicates("market_id")[["market_id", "latitude", "longitude"]].dropna()
    pts = gpd.GeoDataFrame(mk, geometry=gpd.points_from_xy(mk.longitude, mk.latitude), crs="EPSG:4326")
    mk["unit_id"] = assign_points(pts, units(iso3)).values
    df = df.merge(mk[["market_id", "unit_id"]], on="market_id", how="left")
    n_markets, n_joined = len(mk), int(mk["unit_id"].notna().sum())
    df = df[df["unit_id"].notna()]

    staples = df[df["category"].isin(c["staple_categories"]) & (df["pricetype"] == "Retail")]
    key = ["market_id", "commodity_id", "unit", "pricetype", "currency"]
    s = staples.groupby(key + ["unit_id", "month"], as_index=False)["price"].median()
    s = s.sort_values(key + ["month"])
    s["logp"] = np.log(s["price"])

    def trailing(g: pd.DataFrame) -> pd.Series:
        ser = g.set_index("month")["logp"].asfreq("MS")
        base = np.log(np.exp(ser).shift(1).rolling(c["baseline_months"], min_periods=c["baseline_min_obs"]).mean())
        return (ser - base).reindex(g["month"]).set_axis(g.index)

    s["anom"] = s.groupby(key, group_keys=False)[["month", "logp"]].apply(trailing)
    g = s.dropna(subset=["anom"]).groupby(["unit_id", "month"])
    out = pd.DataFrame({"price_staple_anom": g["anom"].median(), "price_staple_n_series": g.size()})

    # Level indicator: median USD price per kg of local cereals (maize/sorghum), for display.
    cer = df[df["commodity"].str.contains(r"^(?:Maize|Sorghum)", regex=True) & (df["unit"] == "KG")
             & (df["pricetype"] == "Retail")]
    out = out.join(cer.groupby(["unit_id", "month"])["usdprice"].median().rename("price_cereal_usd_kg"), how="outer")
    out = out.join(df.groupby(["unit_id", "month"])["market_id"].nunique().rename("price_n_markets"), how="outer")
    out = out.reset_index()
    note = (f"WFP {', '.join(f.name for f in files())}; {n_markets} markets, {n_joined} placed in units; "
            f"currencies {sorted(df['currency'].unique())}")
    res = SourceResult(out, df["month"].min(), df["month"].max(), False,
                       [c_ for c_ in out.columns if c_ not in ("unit_id", "month")], note)
    res.df.attrs.update(n_markets=n_markets, n_markets_joined=n_joined)
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    inputs = [path(f) for f in cfg()["wfp"]["files"]] + fetched_files(iso3) + [path(country_cfg(iso3)["cod_ab_zip"])]
    return refresh_source("wfp", iso3, inputs, build, force)

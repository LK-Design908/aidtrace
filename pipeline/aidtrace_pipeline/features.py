"""Model features at a forecast origin (strictly no data after the origin month).

One row per (unit_id, origin). The target for horizon h is ucdp_fatal_any at origin + h.
Notation: `*_lagK` is the value K-1 months before the origin, so lag1 = the origin month itself,
the latest month known at forecast time.

Leakage guards
- Conflict history comes from the UCDP table, rolled per unit using only past rows.
- IPC current status is carried forward at most 12 months, with its age as a feature.
- IPC projections are used only if issued on or before the origin.
- Prices are carried forward at most 3 months.
- ACLED features (optional) are NaN outside ACLED coverage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import PROCESSED, RESTRICTED_OUT, cfg, load_result, path
from .spine import units

CONFLICT_HISTORY_START = pd.Timestamp("2010-01-01")


# ------------------------------------------------------------------ neighbours

def neighbour_matrix(iso3: str) -> pd.DataFrame:
    """Row-normalised queen-contiguity weights (units touching within 100 m)."""
    u = units(iso3).to_crs(cfg()["equal_area_crs"]).reset_index(drop=True)
    buf = u.buffer(100)
    ids = u["unit_id"].tolist()
    W = pd.DataFrame(0.0, index=ids, columns=ids)
    for i, gi in enumerate(buf):
        hits = u.index[buf.intersects(gi)]
        for j in hits:
            if j != i:
                W.iat[i, j] = 1.0
    rs = W.sum(axis=1).replace(0, np.nan)
    return W.div(rs, axis=0).fillna(0.0)


def spatial_lag(df: pd.DataFrame, col: str, W: pd.DataFrame) -> pd.Series:
    wide = df.pivot(index="month", columns="unit_id", values=col)[W.index]
    lagged = wide.fillna(0).to_numpy() @ W.to_numpy().T
    out = pd.DataFrame(lagged, index=wide.index, columns=W.index)
    out[wide.isna().all(axis=1).values] = np.nan
    return out.stack(future_stack=True).rename(f"{col}_splag").reindex(pd.MultiIndex.from_frame(df[["month", "unit_id"]])).values


# ------------------------------------------------------------------ builders

def conflict_history(iso3: str) -> pd.DataFrame:
    """Zero-filled UCDP grid (units x months) over GED coverage, from 2010."""
    r = load_result(path(PROCESSED) / "ucdp" / f"{iso3.lower()}.parquet")
    months = pd.period_range(CONFLICT_HISTORY_START.to_period("M"), r.last.to_period("M"), freq="M").to_timestamp()
    grid = pd.MultiIndex.from_product([units(iso3)["unit_id"], months], names=["unit_id", "month"]).to_frame(index=False)
    df = grid.merge(r.df, on=["unit_id", "month"], how="left")
    vc = [c for c in r.value_cols if c in df.columns]
    df[vc] = df[vc].fillna(0)
    return df.sort_values(["unit_id", "month"]).reset_index(drop=True)


def _months_since_event(s: pd.Series) -> pd.Series:
    out, last = [], None
    for i, v in enumerate(s.to_numpy()):
        if v > 0:
            last = i
        out.append(np.nan if last is None else i - last)
    return pd.Series(out, index=s.index).fillna(120).clip(upper=120)


def build_features(iso3: str, use_acled: bool = False) -> pd.DataFrame:
    h = conflict_history(iso3)
    g = h.groupby("unit_id", group_keys=False)
    f = h[["unit_id", "month"]].copy()
    fat, ev = h["ucdp_fatal_any"], h["ucdp_events"]
    ldeaths = np.log1p(h["ucdp_deaths_best"])

    for k in (1, 3, 6, 12):
        f[f"fatal_lag{k}"] = g["ucdp_fatal_any"].shift(k - 1)
        f[f"events_lag{k}"] = g["ucdp_events"].shift(k - 1)
    f["ldeaths_lag1"] = ldeaths
    for w in (3, 6, 12):
        f[f"fatal_mean{w}"] = g["ucdp_fatal_any"].transform(lambda s: s.rolling(w, min_periods=w).mean())
        f[f"ldeaths_sum{w}"] = np.log1p(g["ucdp_deaths_best"].transform(lambda s: s.rolling(w, min_periods=w).sum()))
    f["fatal_mean36"] = g["ucdp_fatal_any"].transform(lambda s: s.rolling(36, min_periods=36).mean())
    for t in ("statebased", "nonstate", "onesided"):
        f[f"events_{t}_sum12"] = g[f"ucdp_events_{t}"].transform(lambda s: s.rolling(12, min_periods=12).sum())
    f["months_since_fatal"] = g["ucdp_fatal_any"].transform(_months_since_event)
    f["imprecise_adm1_sum3"] = g["ucdp_imprecise_events_adm1"].transform(lambda s: s.rolling(3, min_periods=3).sum())
    f["imprecise_country_sum3"] = g["ucdp_imprecise_events_country"].transform(lambda s: s.rolling(3, min_periods=3).sum())

    W = neighbour_matrix(iso3)
    tmp = f[["unit_id", "month", "fatal_lag1", "fatal_mean12", "ldeaths_sum3"]]
    f["fatal_lag1_splag"] = spatial_lag(tmp, "fatal_lag1", W)
    f["fatal_mean12_splag"] = spatial_lag(tmp, "fatal_mean12", W)
    f["ldeaths_sum3_splag"] = spatial_lag(tmp, "ldeaths_sum3", W)

    u = units(iso3)[["unit_id", "area_km2"]]
    f = f.merge(u, on="unit_id", how="left")
    f["log_area"] = np.log(f.pop("area_km2"))

    f = f.merge(need_features(iso3), on=["unit_id", "month"], how="left")
    f = f.merge(static_features(iso3), on="unit_id", how="left")
    # COW NMC: use the year BEFORE the origin's year (annual data are published after the year ends).
    nmc = nmc_features(iso3)
    f["_y"] = f["month"].dt.year - 1
    f = f.merge(nmc, left_on="_y", right_on="year", how="left").drop(columns=["_y", "year"])
    if use_acled:
        f = f.merge(acled_features(iso3), on=["unit_id", "month"], how="left")
    return f.rename(columns={"month": "origin"})


def static_features(iso3: str) -> pd.DataFrame:
    """Time-invariant exposure/settlement/history features (NaN where a source does not cover the country)."""
    s = pd.read_parquet(path(PROCESSED) / "static" / f"{iso3.lower()}_static.parquet")
    out = pd.DataFrame({"unit_id": s["unit_id"]})
    out["log_pop"] = np.log1p(s["pop_total"])
    out["log_pop_density"] = np.log1p(s["pop_density_km2"])
    out["u5_share"] = s["pop_u5"] / s["pop_total"]
    out["log_buildings"] = np.log1p(s["bldg_count"])
    out["urban_share"] = s["bldg_urban_share"].fillna(s["urban_extent_share"])
    out["mid_incidents_hist"] = s["mid_incidents_1992_2010"]
    return out


def nmc_features(iso3: str) -> pd.DataFrame:
    n = pd.read_parquet(path(PROCESSED) / "static" / "nmc.parquet")
    n = n[n["iso3"] == iso3].copy()
    out = pd.DataFrame({"year": n["year"],
                        "nmc_log_milex": np.log1p(n["nmc_milex"]), "nmc_log_milper": np.log1p(n["nmc_milper"]),
                        "nmc_cinc": n["nmc_cinc"]})
    return out


def need_features(iso3: str) -> pd.DataFrame:
    """IPC current status, prices and rainfall anomalies as known at each origin (from the panel)."""
    fp = path(PROCESSED) / f"panel_{iso3.lower()}.parquet"
    import pyarrow.parquet as pq
    rain = [c for c in ("rain_z", "rain3_z") if c in pq.read_schema(fp).names]
    p = pd.read_parquet(fp, columns=["unit_id", "month", "ipc_cs_max", "ipc_cs_p3plus", "price_staple_anom"] + rain)
    p = p.sort_values(["unit_id", "month"])
    g = p.groupby("unit_id", group_keys=False)
    obs_month = p["month"].where(p["ipc_cs_max"].notna())
    p["ipc_cs_age"] = (p["month"] - obs_month.groupby(p["unit_id"]).ffill()).dt.days / 30.44
    for c in ("ipc_cs_max", "ipc_cs_p3plus"):
        p[f"{c}_known"] = g[c].ffill(limit=12)
    p.loc[p["ipc_cs_max_known"].isna(), "ipc_cs_age"] = np.nan
    p["price_anom_known"] = g["price_staple_anom"].ffill(limit=3)
    # CHIRPS rainfall anomalies (published within weeks of month end, so known at the origin month)
    for c in ("rain_z", "rain3_z"):
        p[c] = p[c] if c in rain else np.nan
    return p[["unit_id", "month", "ipc_cs_max_known", "ipc_cs_p3plus_known", "ipc_cs_age", "price_anom_known",
              "rain_z", "rain3_z"]]


def projection_feature(iso3: str, frame: pd.DataFrame, horizon: int) -> pd.Series:
    """IPC projected max phase for the target month, only if issued on or before the origin."""
    p = pd.read_parquet(path(PROCESSED) / f"panel_{iso3.lower()}.parquet",
                        columns=["unit_id", "month", "ipc_proj_max", "ipc_proj_issued"])
    tgt = frame[["unit_id", "origin"]].assign(month=frame["origin"] + pd.DateOffset(months=horizon))
    m = tgt.merge(p, on=["unit_id", "month"], how="left")
    ok = pd.to_datetime(m["ipc_proj_issued"]) <= m["origin"]
    return m["ipc_proj_max"].where(ok).set_axis(frame.index)


def acled_features(iso3: str) -> pd.DataFrame:
    r = load_result(path(RESTRICTED_OUT) / "acled" / f"{iso3.lower()}.parquet")
    months = pd.period_range(r.first.to_period("M"), r.last.to_period("M"), freq="M").to_timestamp()
    grid = pd.MultiIndex.from_product([units(iso3)["unit_id"], months], names=["unit_id", "month"]).to_frame(index=False)
    a = grid.merge(r.df, on=["unit_id", "month"], how="left").fillna(0).sort_values(["unit_id", "month"])
    g = a.groupby("unit_id", group_keys=False)
    out = a[["unit_id", "month"]].copy()
    out["acled_events_lag1"] = a["acled_events"]
    out["acled_any_lag1"] = a["acled_any"]
    out["acled_events_sum3"] = g["acled_events"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    out["acled_vac_lag1"] = a["acled_events_vac"]
    out["acled_battles_lag1"] = a["acled_events_battles"]
    out["acled_remote_lag1"] = a["acled_events_remote"]
    out["acled_fatalities_l1"] = np.log1p(a["acled_fatalities"])
    return out


def add_target(f: pd.DataFrame, iso3: str, horizon: int) -> pd.DataFrame:
    h = conflict_history(iso3)[["unit_id", "month", "ucdp_fatal_any"]]
    t = f[["unit_id", "origin"]].assign(month=f["origin"] + pd.DateOffset(months=horizon))
    t = t.merge(h, on=["unit_id", "month"], how="left")
    out = f.copy()
    out["target_month"] = t["month"].values
    out["y"] = t["ucdp_fatal_any"].values      # NaN where target is beyond UCDP coverage
    out["ipc_proj_max_target"] = projection_feature(iso3, out, horizon)
    out["target_moy_sin"] = np.sin(2 * np.pi * out["target_month"].dt.month / 12)
    out["target_moy_cos"] = np.cos(2 * np.pi * out["target_month"].dt.month / 12)
    return out


ID_COLS = ["unit_id", "origin", "target_month", "y"]
LOGIT_FEATURES = ["fatal_lag1", "fatal_mean3", "fatal_mean12", "ldeaths_sum12", "months_since_fatal"]


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ID_COLS]

"""CHIRPS v3 monthly rainfall per district, with anomalies against the 1991-2020 baseline.

Input: data/raw/fetched_chirps/<YYYYMM>/chirps-v3.0.YYYY.MM.tif (promoted by the allowlisted fetcher).
Per unit-month:
  rain_mm          area-weighted mean monthly rainfall (district rasterised at 5x CHIRPS resolution, so
                   districts smaller than one 0.05 deg cell still get their own value)
  rain_pct_normal  rain_mm / 1991-2020 mean for that calendar month
  rain_z           (rain_mm - mean) / sd for that calendar month
  rain3_z          same for the 3-month total ending in that month (seasonal deficit / excess)
z is left missing where the baseline has < 25 years or the normal is near zero (dry-season months), so
a few mm in a normally dry month never reads as an extreme anomaly.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import SourceResult, path, refresh_source
from ..spine import units

VALUE_COLS = ["rain_mm", "rain_pct_normal", "rain_z", "rain3_z"]
BASELINE = (1991, 2020)
MIN_YEARS = 25
MIN_NORMAL_MM = {"rain_z": 10.0, "rain3_z": 30.0}
UPSAMPLE = 5
NODATA = -9999


def files() -> list[Path]:
    base = path("data/raw/fetched_chirps")
    return sorted(base.glob("*/chirps-v3.0.*.tif")) if base.exists() else []


def _month(fp: Path) -> pd.Timestamp:
    y, m = re.search(r"(\d{4})\.(\d{2})\.tif$", fp.name).groups()
    return pd.Timestamp(int(y), int(m), 1)


def _unit_weights(iso3: str, transform, shape) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Fine grid (UPSAMPLE x CHIRPS) of unit indices -> per (coarse cell, unit) area weights."""
    from affine import Affine
    from rasterio.features import rasterize
    u = units(iso3).reset_index(drop=True)
    ft = transform * Affine.scale(1 / UPSAMPLE, 1 / UPSAMPLE)
    fshape = (shape[0] * UPSAMPLE, shape[1] * UPSAMPLE)
    grid = rasterize(((g, i + 1) for i, g in enumerate(u.geometry)), out_shape=fshape, transform=ft, fill=0, dtype="int32")
    rr, cc = np.nonzero(grid)
    cell = (rr // UPSAMPLE) * shape[1] + (cc // UPSAMPLE)
    uid = grid[rr, cc] - 1
    pairs = pd.DataFrame({"cell": cell, "u": uid}).value_counts().reset_index(name="w")
    # units still without a cell (tiny islands etc.): use the cell containing the centroid
    missing = sorted(set(range(len(u))) - set(pairs["u"]))
    extra = []
    for i in missing:
        c = u.geometry.iloc[i].representative_point()
        col, row = ~transform * (c.x, c.y)
        if 0 <= int(row) < shape[0] and 0 <= int(col) < shape[1]:
            extra.append((int(row) * shape[1] + int(col), i, 1))
    if extra:
        pairs = pd.concat([pairs, pd.DataFrame(extra, columns=["cell", "u", "w"])], ignore_index=True)
    return pairs["cell"].to_numpy(), pairs[["u", "w"]].to_numpy(), u["unit_id"].tolist()


def build(iso3: str) -> SourceResult:
    import rasterio
    from rasterio.windows import from_bounds
    fs = files()
    if not fs:
        return SourceResult(pd.DataFrame(columns=["unit_id", "month"] + VALUE_COLS), None, None, False, VALUE_COLS,
                            "no CHIRPS files promoted yet")
    b = units(iso3).total_bounds
    with rasterio.open(fs[0]) as r:
        win = from_bounds(b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1, r.transform).round_offsets().round_lengths()
        t = r.window_transform(win)
        shape = (int(win.height), int(win.width))
    cell, uw, ids = _unit_weights(iso3, t, shape)
    n = len(ids)
    rows = []
    for fp in fs:
        with rasterio.open(fp) as r:
            a = r.read(1, window=win, boundless=True, fill_value=NODATA).astype("float64")
        v = a.ravel()[cell]
        ok = (v != NODATA) & np.isfinite(v) & (v >= 0)
        num = np.bincount(uw[ok, 0], weights=v[ok] * uw[ok, 1], minlength=n)
        den = np.bincount(uw[ok, 0], weights=uw[ok, 1], minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            rows.append(pd.DataFrame({"unit_id": ids, "month": _month(fp), "rain_mm": num / den}))
    df = pd.concat(rows, ignore_index=True).sort_values(["unit_id", "month"])
    # 3-month total only where the three months are consecutive and all present
    g = df.groupby("unit_id")
    df["rain3_mm"] = g["rain_mm"].transform(lambda s: s.rolling(3).sum())
    gap = g["month"].diff(2).dt.days > 62
    df.loc[gap, "rain3_mm"] = np.nan
    df["cal"] = df["month"].dt.month
    base = df[df["month"].dt.year.between(*BASELINE)]
    out = df
    for col, src in (("rain_z", "rain_mm"), ("rain3_z", "rain3_mm")):
        clim = base.groupby(["unit_id", "cal"])[src].agg(["mean", "std", "count"]).reset_index()
        clim.columns = ["unit_id", "cal", f"{src}_mean", f"{src}_sd", f"{src}_n"]
        out = out.merge(clim, on=["unit_id", "cal"], how="left")
        ok = (out[f"{src}_n"] >= MIN_YEARS) & (out[f"{src}_mean"] >= MIN_NORMAL_MM[col]) & (out[f"{src}_sd"] > 0)
        out[col] = ((out[src] - out[f"{src}_mean"]) / out[f"{src}_sd"]).where(ok)
    out["rain_pct_normal"] = (out["rain_mm"] / out["rain_mm_mean"]).where(
        (out["rain_mm_n"] >= MIN_YEARS) & (out["rain_mm_mean"] >= MIN_NORMAL_MM["rain_z"]))
    out = out[["unit_id", "month"] + VALUE_COLS]
    years = base["month"].dt.year.nunique()
    note = (f"CHIRPS v3.0 monthly, {len(fs)} months {_month(fs[0]):%Y-%m}..{_month(fs[-1]):%Y-%m}; "
            f"baseline {BASELINE[0]}-{BASELINE[1]} ({years} years present)")
    res = SourceResult(out, _month(fs[0]), _month(fs[-1]), False, VALUE_COLS, note)
    res.df.attrs.update(baseline_years=int(years), months=len(fs))
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    return refresh_source("chirps", iso3, files(), build, force)

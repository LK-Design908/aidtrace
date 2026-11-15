"""Static district attributes and weight grids.

Sources (where supplied for a country; everything else is NaN, never zero):
- WorldPop age/sex population 2020 (KEN, SSD): total = sum of the 36 age/sex bands; under-1,
  under-5, under-15 and women 15-49 from the dedicated layers. Rasters (3 arc-sec) are summed to
  ~500 m (GDAL 'sum' resampling, so totals are preserved) before zonal aggregation.
- WorldPop building patterns v1.1 (SOM): building count, footprint area, share of buildings in
  'urban' cells, count-weighted mean imagery year (full-resolution block pass).
- WorldPop urban extents (SOM): share of district area inside an urban extent; named urban points.
- COW MIDLOC 2.1 (all countries): militarized interstate dispute incidents 1992-2010 located in the
  district (historical context only; outside the 2017+ panel period).
- COW NMC v7 (all countries): national material capabilities, country-year (separate table).

Weight grid: population where available, else building count, saved as
data/processed/static/<iso>_weight.tif. FEWS NET uses it for population-weighted IPC shares.
"""
from __future__ import annotations

import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import Window

from ..common import PROCESSED, assign_points, cfg, log, path
from ..spine import units

AGES = [0, 1] + list(range(5, 85, 5))
STATIC_COLS = ["pop_total", "pop_u1", "pop_u5", "pop_u15", "pop_f15_49", "pop_density_km2",
               "bldg_count", "bldg_area_m2", "bldg_urban_share", "bldg_imagery_year", "bldg_density_km2",
               "urban_extent_share", "urban_points", "mid_incidents_1992_2010", "mid_disputes_1992_2010",
               "weight_source"]


def _out_dir() -> Path:
    d = path(PROCESSED) / "static"
    d.mkdir(parents=True, exist_ok=True)
    return d


def inputs(iso3: str) -> list[Path]:
    s = cfg()["static"]
    out = [path(s["midloc_incidents"]), path(s["nmc_zip"])]
    if iso3 in s["worldpop_agesex"]:
        out += sorted(path(s["worldpop_agesex"][iso3]["dir"]).glob("*.tif"))
    elif fetched_worldpop(iso3) is not None:
        out.append(fetched_worldpop(iso3))
    if iso3 in s["buildings"]:
        out += sorted(path(s["buildings"][iso3]["dir"]).glob("*.tif"))
    if iso3 in s["urban_extents"]:
        out.append(path(s["urban_extents"][iso3]))
    return out


# ------------------------------------------------------------------ raster helpers

def _unit_grid(iso3: str, transform: Affine, shape: tuple[int, int]) -> tuple[np.ndarray, list[str]]:
    u = units(iso3).reset_index(drop=True)
    shapes = ((geom, i + 1) for i, geom in enumerate(u.geometry))
    grid = rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="int32")
    return grid, u["unit_id"].tolist()


def _read_decimated(fp: Path, factor: int) -> tuple[np.ndarray, Affine]:
    """Exact block sum: each output cell = sum of a factor x factor block of source cells
    (nodata/negative -> 0). Edge rows/columns that do not fill a whole block are dropped
    (at most factor-1 cells, ~400 m, along the raster's own outer edge)."""
    with rasterio.open(fp) as r:
        h, w = r.height // factor, r.width // factor
        out = np.zeros((h, w), dtype="float64")
        rows_per = factor * 256
        for r0 in range(0, h * factor, rows_per):
            nrows = min(rows_per, h * factor - r0)
            blk = r.read(1, window=Window(0, r0, w * factor, nrows), masked=True).filled(0).astype("float64")
            blk[~np.isfinite(blk) | (blk < 0)] = 0
            out[r0 // factor:(r0 + nrows) // factor] = blk.reshape(nrows // factor, factor, w, factor).sum(axis=(1, 3))
        t = r.transform * Affine.scale(factor, factor)
    return out, t


def _zonal(a: np.ndarray, grid: np.ndarray, n: int) -> np.ndarray:
    return np.bincount(grid.ravel(), weights=a.ravel(), minlength=n + 1)[1:]


def _write_weight(iso3: str, a: np.ndarray, t: Affine, source: str) -> None:
    fp = _out_dir() / f"{iso3.lower()}_weight.tif"
    with rasterio.open(fp, "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=t, compress="deflate") as dst:
        dst.write(a.astype("float32"), 1)
        dst.update_tags(source=source)


# ------------------------------------------------------------------ sources

def fetched_worldpop(iso3: str) -> Path | None:
    """WorldPop 2020 total-population raster fetched from worldpop.org (fetch connector), if promoted."""
    base = path("data/raw/fetched_worldpop")
    vs = sorted(base.glob(f"{iso3.upper()}_wpgp_*")) if base.exists() else []
    return next(vs[-1].glob("*.tif"), None) if vs else None


def population(iso3: str) -> pd.DataFrame | None:
    spec = cfg()["static"]["worldpop_agesex"].get(iso3)
    if not spec:
        fp = fetched_worldpop(iso3)                   # total only (no age bands in this product)
        if fp is None:
            return None
        total, t = _read_decimated(fp, cfg()["static"]["decimate"])
        grid, ids = _unit_grid(iso3, t, total.shape)
        _write_weight(iso3, total, t, f"worldpop_wpgp_{fp.stem}")
        return pd.DataFrame({"unit_id": ids, "pop_total": _zonal(total, grid, len(ids))})
    d, pre, f = path(spec["dir"]), spec["prefix"], cfg()["static"]["decimate"]
    total, t = None, None
    for sex in ("f", "m"):
        for a in AGES:
            arr, t = _read_decimated(d / f"{pre}_{sex}{a}.tif", f)
            total = arr if total is None else total + arr
    grid, ids = _unit_grid(iso3, t, total.shape)
    out = pd.DataFrame({"unit_id": ids, "pop_total": _zonal(total, grid, len(ids))})
    for col, suffix in [("pop_u1", "under1"), ("pop_u5", "under5"), ("pop_u15", "under15"), ("pop_f15_49", "f15_49")]:
        fp = d / f"{pre}_{suffix}.tif"
        if fp.exists():
            arr, _ = _read_decimated(fp, f)
            out[col] = _zonal(arr, grid, len(ids))
    _write_weight(iso3, total, t, "worldpop_agesex_2020_total")
    out.attrs["national_total"] = float(total.sum())
    return out


def buildings(iso3: str) -> pd.DataFrame | None:
    spec = cfg()["static"]["buildings"].get(iso3)
    if not spec:
        return None
    d, pre = path(spec["dir"]), spec["prefix"]
    u = units(iso3).reset_index(drop=True)
    n = len(u)
    acc = {k: np.zeros(n) for k in ("count", "area", "urban", "yearw")}
    with rasterio.open(d / f"{pre}_count.tif") as rc, rasterio.open(d / f"{pre}_total_area.tif") as ra, \
            rasterio.open(d / f"{pre}_urban.tif") as ru, rasterio.open(d / f"{pre}_imagery_year.tif") as ry:
        step = 2048
        for row in range(0, rc.height, step):
            win = Window(0, row, rc.width, min(step, rc.height - row))
            t = rc.window_transform(win)
            grid = rasterize(((g, i + 1) for i, g in enumerate(u.geometry)), out_shape=(int(win.height), int(win.width)),
                             transform=t, fill=0, dtype="int32")
            cnt = rc.read(1, window=win, masked=True).filled(0).astype("float64")
            area = ra.read(1, window=win, masked=True).filled(0).astype("float64")
            urb = (ru.read(1, window=win, masked=True).filled(0) == 1)
            yr = ry.read(1, window=win, masked=True).filled(0).astype("float64")
            yr[~np.isfinite(yr) | (yr < 1990)] = 0
            acc["count"] += _zonal(cnt, grid, n)
            acc["area"] += _zonal(area, grid, n)
            acc["urban"] += _zonal(cnt * urb, grid, n)
            acc["yearw"] += _zonal(cnt * yr, grid, n)
    wgrid, t = _read_decimated(d / f"{pre}_count.tif", cfg()["static"]["decimate"])
    _write_weight(iso3, wgrid, t, "worldpop_buildings_v1_1_count")
    c = acc["count"]
    with np.errstate(invalid="ignore", divide="ignore"):
        return pd.DataFrame({"unit_id": u["unit_id"], "bldg_count": c, "bldg_area_m2": acc["area"],
                             "bldg_urban_share": np.where(c > 0, acc["urban"] / c, np.nan),
                             "bldg_imagery_year": np.where(c > 0, acc["yearw"] / c, np.nan)})


def urban(iso3: str) -> pd.DataFrame | None:
    s = cfg()["static"]
    if iso3 not in s["urban_extents"]:
        return None
    u = units(iso3)
    ea = cfg()["equal_area_crs"]
    ext = gpd.read_file(path(s["urban_extents"][iso3])).to_crs(ea)
    uu = u[["unit_id", "geometry"]].to_crs(ea)
    uu["a"] = uu.area
    inter = gpd.overlay(uu, ext[["geometry"]], how="intersection", keep_geom_type=True)
    share = (inter.area.groupby(inter["unit_id"]).sum() / uu.set_index("unit_id")["a"]).fillna(0)
    out = pd.DataFrame({"unit_id": uu["unit_id"], "urban_extent_share": uu["unit_id"].map(share).fillna(0).values})
    if iso3 in s["urban_points"]:
        pts = gpd.read_file(path(s["urban_points"][iso3]))
        pts["unit_id"] = assign_points(pts, u).values
        out["urban_points"] = out["unit_id"].map(pts.groupby("unit_id").size()).fillna(0).values
    return out


@lru_cache
def midloc_points() -> gpd.GeoDataFrame:
    df = pd.read_csv(path(cfg()["static"]["midloc_incidents"]), na_values=["NA", ""], encoding="latin-1")
    df = df.dropna(subset=["midloc2_xlongitude", "midloc2_ylatitude"])
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["midloc2_xlongitude"], df["midloc2_ylatitude"]),
                            crs="EPSG:4326")


def midloc(iso3: str) -> pd.DataFrame:
    p = midloc_points()
    u = units(iso3)
    minx, miny, maxx, maxy = u.total_bounds
    p = p.cx[minx - 0.1:maxx + 0.1, miny - 0.1:maxy + 0.1].copy()
    out = pd.DataFrame({"unit_id": u["unit_id"]})
    if p.empty:
        out["mid_incidents_1992_2010"] = 0.0
        out["mid_disputes_1992_2010"] = 0.0
        return out
    p["unit_id"] = assign_points(p, u, snap_m=0).values      # no snapping across borders
    p = p[p["unit_id"].notna()]
    out["mid_incidents_1992_2010"] = out["unit_id"].map(p.groupby("unit_id").size()).fillna(0).values
    out["mid_disputes_1992_2010"] = out["unit_id"].map(p.groupby("unit_id")["dispnum"].nunique()).fillna(0).values
    return out


@lru_cache
def nmc() -> pd.DataFrame:
    """COW National Material Capabilities v7: country-year, mapped to our ISO3 codes (-9 = missing)."""
    with zipfile.ZipFile(path(cfg()["static"]["nmc_zip"])) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        df = pd.read_csv(io.BytesIO(z.read(name)))
    inv = {v: k for k, v in cfg()["static"]["nmc_abb"].items()}
    df = df[df["stateabb"].isin(inv)].copy()
    df["iso3"] = df["stateabb"].map(inv)
    df = df.replace(-9, np.nan)
    keep = {"milex": "nmc_milex", "milper": "nmc_milper", "tpop": "nmc_tpop", "upop": "nmc_upop",
            "cinc": "nmc_cinc", "pec": "nmc_pec", "irst": "nmc_irst"}
    out = df[["iso3", "year"] + list(keep)].rename(columns=keep)
    out.to_parquet(_out_dir() / "nmc.parquet", index=False)
    return out


# ------------------------------------------------------------------ build / refresh

def build(iso3: str) -> pd.DataFrame:
    u = units(iso3)[["unit_id", "area_km2"]]
    out = u.copy()
    weight_source = "area"
    for part in (population(iso3), buildings(iso3), urban(iso3), midloc(iso3)):
        if part is not None:
            out = out.merge(part, on="unit_id", how="left")
    if "pop_total" in out:
        out["pop_density_km2"] = out["pop_total"] / out["area_km2"]
        weight_source = "population"
    elif "bldg_count" in out:
        out["bldg_density_km2"] = out["bldg_count"] / out["area_km2"]
        weight_source = "buildings"
    for c in STATIC_COLS:
        if c not in out:
            out[c] = np.nan
    out["weight_source"] = weight_source
    out = out[["unit_id", "area_km2"] + STATIC_COLS]
    out.to_parquet(_out_dir() / f"{iso3.lower()}_static.parquet", index=False)
    meta = {"weight_source": weight_source, "inputs": [str(p.relative_to(path("."))) for p in inputs(iso3)]}
    (_out_dir() / f"{iso3.lower()}_static.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    nmc()
    log.info("static %s: weight=%s", iso3, weight_source)
    return out


def refresh(iso3: str, force: bool = False) -> pd.DataFrame:
    fp = _out_dir() / f"{iso3.lower()}_static.parquet"
    pkg = Path(__file__).resolve().parents[1]
    code_t = max(p.stat().st_mtime for p in (Path(__file__), pkg / "common.py", pkg / "spine.py", pkg / "pipeline.yaml"))
    newest = max([p.stat().st_mtime for p in inputs(iso3) if p.exists()] + [code_t])
    if force or not fp.exists() or fp.stat().st_mtime < newest:
        return build(iso3)
    return pd.read_parquet(fp)


# ------------------------------------------------------------------ weighted shares (used by FEWS NET)

@lru_cache
def _weight_and_grid(iso3: str):
    fp = _out_dir() / f"{iso3.lower()}_weight.tif"
    if not fp.exists():
        return None
    with rasterio.open(fp) as r:
        w = r.read(1).astype("float64")
        t = r.transform
        src = r.tags().get("source", "")
    grid, ids = _unit_grid(iso3, t, w.shape)
    return w, t, grid, ids, src


def weight_source(iso3: str) -> str | None:
    wg = _weight_and_grid(iso3)
    return None if wg is None else wg[4]


def weighted_shares(polys: gpd.GeoDataFrame, iso3: str) -> pd.DataFrame | None:
    """Share of each unit's weight (population or buildings) in each phase polygon value.
    Returns unit_id, value, wshare, or None when no weight grid exists for the country."""
    wg = _weight_and_grid(iso3)
    if wg is None or polys.empty:
        return None
    w, t, grid, ids, _ = wg
    burn = rasterize(((g, int(v)) for g, v in zip(polys.geometry, polys["value"]) if g is not None and not g.is_empty),
                     out_shape=w.shape, transform=t, fill=0, dtype="int16")
    n = len(ids)
    tot = _zonal(w, grid, n)
    rows = []
    for v in range(1, 6):
        m = burn == v
        if not m.any():
            continue
        s = _zonal(np.where(m, w, 0), grid, n)
        for uid, sv, tv in zip(ids, s, tot):
            if sv > 0 and tv > 0:
                rows.append((uid, v, sv / tv))
    return pd.DataFrame(rows, columns=["unit_id", "value", "wshare"])

"""VIEWS fatalities003 PRIO-GRID-month forecasts -> area-weighted district-month forecasts.

PRIO-GRID cell geometry: pg_id = (row - 1) * 720 + col, with row 1 at -90 deg lat and col 1 at
-180 deg lon, 0.5 deg cells. A cell's predicted fatalities (main_mean) are shared among units
in proportion to overlap area. `views_main_dich` is the area-weighted mean of the published
`main_dich` (its exact definition in this run is UNVERIFIED and should be confirmed against
the VIEWS documentation before display).

These are forecasts issued in `views.issued`. They are written to their own table and are
never treated as observed outcomes.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from ..common import SourceResult, cfg, country_cfg, path, refresh_source
from ..spine import units


def file() -> Path:
    return path(cfg()["views"]["pgm_file"])


def pg_geom(pg_id: pd.Series) -> gpd.GeoSeries:
    row = (pg_id - 1) // 720 + 1
    col = (pg_id - 1) % 720 + 1
    lat0 = -90 + (row - 1) * 0.5
    lon0 = -180 + (col - 1) * 0.5
    return gpd.GeoSeries([box(x, y, x + 0.5, y + 0.5) for x, y in zip(lon0, lat0)], index=pg_id.index, crs="EPSG:4326")


def month_id_to_ts(mid: pd.Series) -> pd.Series:
    """VIEWS month_id 1 = 1980-01."""
    y = 1980 + (mid - 1) // 12
    m = (mid - 1) % 12 + 1
    return pd.to_datetime(dict(year=y, month=m, day=1))


def build(iso3: str) -> SourceResult:
    u = units(iso3)
    df = pd.read_csv(file())
    cells = pd.DataFrame({"pg_id": df["pg_id"].unique()})
    cells = gpd.GeoDataFrame(cells, geometry=pg_geom(cells["pg_id"]), crs="EPSG:4326")
    minx, miny, maxx, maxy = u.total_bounds
    cells = cells.cx[minx - 0.5:maxx + 0.5, miny - 0.5:maxy + 0.5]
    ea = cfg()["equal_area_crs"]
    c_ea = cells.to_crs(ea)
    c_ea["cell_area"] = c_ea.area
    inter = gpd.overlay(c_ea, u[["unit_id", "geometry"]].to_crs(ea), how="intersection", keep_geom_type=True)
    inter["w_cell"] = inter.area / inter["cell_area"]          # share of the cell inside the unit
    w = pd.DataFrame(inter[["pg_id", "unit_id", "w_cell"]])
    inter["a"] = inter.area
    w["w_unit"] = inter["a"] / inter.groupby("unit_id")["a"].transform("sum")   # share of unit area
    d = df[df["pg_id"].isin(w["pg_id"])].merge(w, on="pg_id")
    d["fat"] = d["main_mean"] * d["w_cell"]
    d["dich"] = d["main_dich"] * d["w_unit"]
    out = d.groupby(["unit_id", "month_id"], as_index=False).agg(views_pred_fatalities=("fat", "sum"),
                                                                 views_main_dich=("dich", "sum"))
    out["month"] = month_id_to_ts(out["month_id"])
    out = out.drop(columns="month_id")
    out["views_issued"] = pd.Timestamp(cfg()["views"]["issued"] + "-01")
    note = f"VIEWS {file().name}: {len(cells)} PRIO-GRID cells near {iso3}; forecast months {out['month'].min():%Y-%m}..{out['month'].max():%Y-%m}"
    return SourceResult(out, out["month"].min(), out["month"].max(), False,
                        ["views_pred_fatalities", "views_main_dich", "views_issued"], note)


def refresh(iso3: str, force: bool = False) -> SourceResult:
    return refresh_source("views", iso3, [file(), path(country_cfg(iso3)["cod_ab_zip"])], build, force)

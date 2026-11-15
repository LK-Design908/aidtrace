"""Shared configuration, paths, refresh logic and spatial helpers."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import geopandas as gpd
import pandas as pd
import yaml

log = logging.getLogger("aidtrace")


@lru_cache
def root() -> Path:
    env = os.environ.get("AIDTRACE_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


@lru_cache
def cfg() -> dict:
    with open(Path(__file__).with_name("pipeline.yaml"), encoding="utf-8") as f:
        c = yaml.safe_load(f)
    if "USE_ACLED" in os.environ:
        c["use_acled"] = os.environ["USE_ACLED"].strip().lower() in ("1", "true", "yes")
    return c


def path(rel: str | Path) -> Path:
    return root() / rel


def country_cfg(iso3: str) -> dict:
    return cfg()["countries"][iso3.upper()]


PROCESSED = "data/processed"
RESTRICTED_OUT = "data/processed/restricted"   # licence-restricted derived outputs (gitignored)


def out_path(source: str, iso3: str, restricted: bool = False) -> Path:
    base = RESTRICTED_OUT if restricted else PROCESSED
    p = path(base) / source / f"{iso3.lower()}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- months

def to_month(s: pd.Series) -> pd.Series:
    """Datetime-like -> first day of month (datetime64[ns])."""
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp()


def excel_or_date(s: pd.Series) -> pd.Series:
    """Excel serial numbers or real dates -> datetime."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    num = pd.to_numeric(s, errors="coerce")
    out = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    txt = pd.to_datetime(s.where(num.isna()), errors="coerce")
    return out.fillna(txt)


# ---------------------------------------------------------------- outputs with coverage metadata

@dataclass
class SourceResult:
    """A loader's district-month table plus the months the source actually covers.

    Months inside [first, last] with no row for a unit mean "observed, nothing recorded"
    only if `zero_when_absent` is True (event counts). Outside coverage, values are NaN.
    """
    df: pd.DataFrame                 # columns: unit_id, month, <value cols>
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    zero_when_absent: bool
    value_cols: list[str]
    note: str = ""


def save_result(res: SourceResult, p: Path) -> None:
    res.df.to_parquet(p, index=False)
    meta = {
        "first": None if res.first is None else res.first.strftime("%Y-%m"),
        "last": None if res.last is None else res.last.strftime("%Y-%m"),
        "zero_when_absent": res.zero_when_absent,
        "value_cols": res.value_cols,
        "note": res.note,
        "stats": {k: v for k, v in res.df.attrs.items() if isinstance(v, (int, float, str))},
    }
    p.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_result(p: Path) -> SourceResult:
    meta = json.loads(p.with_suffix(".meta.json").read_text(encoding="utf-8"))
    ts = lambda v: None if v is None else pd.Timestamp(v + "-01")
    df = pd.read_parquet(p)
    df.attrs.update(meta.get("stats", {}))
    return SourceResult(df, ts(meta["first"]), ts(meta["last"]),
                        meta["zero_when_absent"], meta["value_cols"], meta.get("note", ""))


def needs_rebuild(output: Path, inputs: Iterable[Path]) -> bool:
    if not output.exists() or not output.with_suffix(".meta.json").exists():
        return True
    out_t = output.stat().st_mtime
    # Only code that shapes source tables triggers a rebuild (not model/need/panel code).
    pkg = Path(__file__).parent
    code_files = [pkg / "common.py", pkg / "spine.py", pkg / "pipeline.yaml", *(pkg / "loaders").glob("*.py")]
    code_t = max(p.stat().st_mtime for p in code_files if p.exists())
    in_t = max([p.stat().st_mtime for p in inputs if p.exists()] + [code_t])
    return in_t > out_t


def refresh_source(name: str, iso3: str, inputs: list[Path], build: Callable[[str], SourceResult],
                   force: bool = False, restricted: bool = False) -> SourceResult:
    """Rebuild a source's processed table only if an input (or pipeline code) is newer."""
    p = out_path(name, iso3, restricted)
    if force or needs_rebuild(p, inputs):
        log.info("rebuilding %s/%s", name, iso3)
        res = build(iso3)
        save_result(res, p)
        return res
    log.info("%s/%s up to date", name, iso3)
    return load_result(p)


# ---------------------------------------------------------------- spatial

def assign_points(points: gpd.GeoDataFrame, units: gpd.GeoDataFrame, snap_m: float | None = None) -> pd.Series:
    """Return unit_id for each point (NaN if outside all units beyond snap distance)."""
    if snap_m is None:
        snap_m = cfg()["snap_distance_m"]
    pts = points[["geometry"]].copy()
    j = gpd.sjoin(pts, units[["unit_id", "geometry"]], how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    result = j["unit_id"].reindex(points.index)
    miss = result.isna()
    if miss.any() and snap_m > 0:
        ea = cfg()["equal_area_crs"]
        near = gpd.sjoin_nearest(pts[miss].to_crs(ea), units[["unit_id", "geometry"]].to_crs(ea),
                                 how="left", max_distance=snap_m)
        near = near[~near.index.duplicated(keep="first")]
        result.loc[miss] = near["unit_id"].reindex(result[miss].index)
    return result


def area_weights(polys: gpd.GeoDataFrame, units: gpd.GeoDataFrame, keep: list[str]) -> pd.DataFrame:
    """Intersect polygons with units; return unit_id, keep cols, area share of the unit."""
    ea = cfg()["equal_area_crs"]
    u = units[["unit_id", "geometry"]].to_crs(ea)
    u["unit_area"] = u.area
    # Simplify to 250 m before overlaying: area shares are unaffected at district scale, and very
    # detailed boundaries (e.g. Ethiopia's 20 MB COD-AB) otherwise make the overlay very slow.
    u["geometry"] = u.geometry.simplify(250, preserve_topology=True).make_valid()
    p = polys[keep + ["geometry"]].to_crs(ea)
    p["geometry"] = p.geometry.make_valid().simplify(250, preserve_topology=True).make_valid()
    inter = gpd.overlay(u, p, how="intersection", keep_geom_type=True)
    inter["share"] = inter.area / inter["unit_area"]
    return pd.DataFrame(inter.drop(columns=["geometry", "unit_area"]))

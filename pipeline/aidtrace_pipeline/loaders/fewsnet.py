"""FEWS NET IPC-compatible classifications (CS / ML1 / ML2) -> district-month need indicators.

FEWS NET geometries are dissolved by phase, so each unit gets an area-weighted overlay:
  *_max       highest phase covering >= min_area_share_for_max of the unit
  *_mean      area-weighted mean phase over the classified part of the unit
  *_p3plus    share of unit area in Phase 3+
  *_coverage  share of unit area that is classified
  *_idp_max   highest phase of IDP-camp / urban point features inside the unit
Area weighting over-weights sparsely populated areas. Switch to population weighting when
WorldPop is added.

Current (CS) and projected (ML1/ML2) are kept in separate columns. A projection is attached to
each target month with `ipc_proj_issued` (its reporting date). Model features must only use
projections whose issued date is on or before the forecast origin (no leakage).

GDAL's KML driver drops FEWS NET ExtendedData, so KML is parsed directly.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union

from ..common import SourceResult, area_weights, assign_points, cfg, country_cfg, path, refresh_source
from ..spine import units

NS = {"k": "http://www.opengis.net/kml/2.2"}
ATTRS = ["projection_start", "projection_end", "reporting_date", "classification_scale", "scenario",
         "value", "is_allowing_for_assistance", "unit_type", "country_code"]


def files() -> list[Path]:
    """Supplied KML/GeoJSON exports plus IPC packages fetched from the FEWS NET Data Warehouse
    (data/raw/fetched_fewsnet/<CC>_<YYYYMM>/*.zip, promoted by aidtrace_pipeline.fetch)."""
    d = path(cfg()["fewsnet"]["dir"])
    fetched = path("data/raw/fetched_fewsnet")
    pk = sorted(fetched.glob("*/*.zip")) if fetched.exists() else []
    return sorted(list(d.glob("*.kml")) + list(d.glob("*.geojson"))) + pk


def read_package(p: Path) -> gpd.GeoDataFrame:
    """FEWS NET IPC package: <CC>_<YYYYMM>_{CS,ML1,ML2}[_IDP].shp with cov_start/cov_end/report_mon (MM-YYYY)."""
    import re
    import zipfile
    frames = []
    for n in zipfile.ZipFile(p).namelist():
        m = re.fullmatch(r"([A-Z]{2})_\d{6}_(CS|ML1|ML2)(_IDP)?\.shp", n)
        if not m:
            continue
        scen = m.group(2)
        g = gpd.read_file(f"zip://{p}!{n}").to_crs("EPSG:4326")
        if scen not in g.columns or g.empty:
            continue
        mon = lambda s: pd.to_datetime("01-" + s.astype(str), format="%d-%m-%Y", errors="coerce")  # noqa: E731
        out = gpd.GeoDataFrame({
            "projection_start": mon(g["cov_start"]),
            "projection_end": (mon(g["cov_end"]) + pd.offsets.MonthEnd(0)),
            "reporting_date": mon(g["report_mon"]),
            "classification_scale": "IPC 3.x (FEWS NET package)",
            "scenario": scen,
            "value": pd.to_numeric(g[scen], errors="coerce"),
            "is_allowing_for_assistance": g.get(f"HA{dict(CS=0, ML1=1, ML2=2)[scen]}"),
            "unit_type": g["unit_type"] if "unit_type" in g else ("idp_camp" if m.group(3) else "fsc_admin_lhz"),
            "country_code": g["country"] if "country" in g else m.group(1),
        }, geometry=g.geometry, crs="EPSG:4326")
        frames.append(out)
    if not frames:
        return gpd.GeoDataFrame(columns=ATTRS + ["geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")


def _coords(text: str) -> list[tuple[float, float]]:
    pts = []
    for tok in text.split():
        parts = tok.split(",")
        pts.append((float(parts[0]), float(parts[1])))
    return pts


def _kml_geom(pm: ET.Element):
    polys, points = [], []
    for poly in pm.iter("{%s}Polygon" % NS["k"]):
        outer = poly.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", NS)
        inners = poly.findall("k:innerBoundaryIs/k:LinearRing/k:coordinates", NS)
        if outer is not None and outer.text:
            polys.append(Polygon(_coords(outer.text), [_coords(i.text) for i in inners if i.text]))
    for pt in pm.iter("{%s}Point" % NS["k"]):
        c = pt.find("k:coordinates", NS)
        if c is not None and c.text:
            points.append(Point(_coords(c.text)[0]))
    if polys:
        return MultiPolygon(polys) if len(polys) > 1 else polys[0]
    if points:
        return MultiPoint(points) if len(points) > 1 else points[0]
    return None


def read_kml(p: Path) -> gpd.GeoDataFrame:
    root = ET.parse(p).getroot()
    rows = []
    for pm in root.iter("{%s}Placemark" % NS["k"]):
        rec = {sd.get("name"): (sd.text or "") for sd in pm.iter("{%s}SimpleData" % NS["k"])}
        rec = {k: rec.get(k) for k in ATTRS}
        rec["geometry"] = _kml_geom(pm)
        rows.append(rec)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def read_geojson(p: Path) -> gpd.GeoDataFrame:
    data = json.loads(p.read_text(encoding="utf-8"))
    feats = data.get("features", [])
    if not feats:
        return gpd.GeoDataFrame(columns=ATTRS + ["geometry"], geometry="geometry", crs="EPSG:4326")
    rows = [{**{k: f["properties"].get(k) for k in ATTRS}, "geometry": shape(f["geometry"])} for f in feats]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def read_all(iso2: str) -> gpd.GeoDataFrame:
    """All classifications for a country, de-duplicated across KML/GeoJSON exports."""
    frames = []
    for p in files():
        if p.suffix.lower() == ".zip":
            if not p.parent.name.startswith(iso2 + "_"):
                continue
            g = read_package(p)
        else:
            g = read_kml(p) if p.suffix.lower() == ".kml" else read_geojson(p)
        if len(g):
            g["source_file"] = p.name
            frames.append(g)
    allg = pd.concat(frames, ignore_index=True)
    allg = allg[allg["country_code"] == iso2].copy()
    if allg.empty:
        return gpd.GeoDataFrame(allg, geometry="geometry", crs="EPSG:4326")
    for c in ("projection_start", "projection_end", "reporting_date"):
        allg[c] = pd.to_datetime(allg[c].astype(str).str.replace("/", "-"), format="mixed", errors="coerce")
    allg["value"] = pd.to_numeric(allg["value"], errors="coerce")
    allg = allg[allg["value"].between(1, 5)]
    # The same classification (scenario + period + reporting date) can arrive in both a KML and a
    # GeoJSON export. Keep exactly one source file per classification (GeoJSON preferred, then the
    # file with more features), so phase areas are never double-counted.
    key = ["scenario", "projection_start", "projection_end", "reporting_date"]
    rank = (allg.groupby(key + ["source_file"]).size().rename("n").reset_index()
            .assign(pref=lambda d: d["source_file"].str.endswith(".geojson").astype(int))
            .sort_values(key + ["pref", "n"], ascending=[True] * len(key) + [False, False])
            .drop_duplicates(key))
    allg = allg.merge(rank[key + ["source_file"]], on=key + ["source_file"])
    return gpd.GeoDataFrame(allg, geometry="geometry", crs="EPSG:4326")


FEWS_COLS = [f"ipc_{p}_{s}" for p in ("cs", "proj")
             for s in ("coverage", "p3plus", "mean", "max", "idp_max", "p3plus_pop", "max_pop")] + \
    ["ipc_proj_scenario", "ipc_proj_issued", "ipc_cs_observed"]


def _summarise(cls: gpd.GeoDataFrame, u: gpd.GeoDataFrame, prefix: str, iso3: str | None = None) -> pd.DataFrame:
    """One classification (one scenario + period) -> per-unit indicators.
    *_p3plus_pop / *_max_pop use the country's weight grid (population, else building count; see
    loaders/static.py) instead of land area; NaN where no weight grid exists."""
    min_share = cfg()["fewsnet"]["min_area_share_for_max"]
    polys = cls[cls.geometry.geom_type.isin(["Polygon", "MultiPolygon"]) & (cls["unit_type"] != "idp_camp")]
    w = area_weights(polys, u, ["value"]) if len(polys) else pd.DataFrame(columns=["unit_id", "value", "share"])
    w = w.groupby(["unit_id", "value"], as_index=False)["share"].sum()
    out = pd.DataFrame(index=pd.Index(u["unit_id"], name="unit_id"))
    out[f"{prefix}_coverage"] = w.groupby("unit_id")["share"].sum().clip(upper=1)
    out[f"{prefix}_p3plus"] = w[w["value"] >= 3].groupby("unit_id")["share"].sum().clip(upper=1)
    out[f"{prefix}_mean"] = (w["value"] * w["share"]).groupby(w["unit_id"]).sum() / w.groupby("unit_id")["share"].sum()
    out[f"{prefix}_max"] = w[w["share"] >= min_share].groupby("unit_id")["value"].max()
    covered = out[f"{prefix}_coverage"].fillna(0) > 0
    out.loc[covered, f"{prefix}_p3plus"] = out.loc[covered, f"{prefix}_p3plus"].fillna(0)
    out.loc[~covered, [f"{prefix}_coverage"]] = 0
    from .static import weighted_shares
    ws = weighted_shares(polys, iso3) if iso3 else None
    if ws is not None and len(ws):
        out[f"{prefix}_p3plus_pop"] = ws[ws["value"] >= 3].groupby("unit_id")["wshare"].sum().clip(upper=1)
        out[f"{prefix}_max_pop"] = ws[ws["wshare"] >= min_share].groupby("unit_id")["value"].max()
        out.loc[covered, f"{prefix}_p3plus_pop"] = out.loc[covered, f"{prefix}_p3plus_pop"].fillna(0)
    else:
        out[f"{prefix}_p3plus_pop"] = float("nan")
        out[f"{prefix}_max_pop"] = float("nan")
    # IDP camps / point settlements
    pts = cls[cls["unit_type"] == "idp_camp"].explode(index_parts=False)
    pts = pts.assign(geometry=pts.geometry.representative_point())
    if len(pts):
        pts = pts.reset_index(drop=True)
        pts["unit_id"] = assign_points(pts, u).values
        out[f"{prefix}_idp_max"] = pts.groupby("unit_id")["value"].max()
    else:
        out[f"{prefix}_idp_max"] = pd.NA
    return out.reset_index()


def _months(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.period_range(start.to_period("M"), end.to_period("M"), freq="M").to_timestamp()


def build(iso3: str) -> SourceResult:
    iso2 = country_cfg(iso3)["iso2"]
    u = units(iso3)
    allg = read_all(iso2)
    if allg.empty:
        return SourceResult(pd.DataFrame(columns=["unit_id", "month"] + FEWS_COLS), None, None, False, FEWS_COLS,
                            f"no FEWS NET classifications for {iso2}")
    groups = allg.groupby(["scenario", "projection_start", "projection_end", "reporting_date"])
    cs_rows, proj_rows = [], []
    for (scen, ps, pe, rd), cls in groups:
        if scen == "CS":
            s = _summarise(cls, u, "ipc_cs", iso3)
            for m in _months(ps, pe):
                cs_rows.append(s.assign(month=m))
        else:
            s = _summarise(cls, u, "ipc_proj", iso3)
            for m in _months(ps, pe):
                proj_rows.append(s.assign(month=m, ipc_proj_scenario=scen, ipc_proj_issued=rd))
    cs = pd.concat(cs_rows, ignore_index=True) if cs_rows else pd.DataFrame(columns=["unit_id", "month"])
    proj = pd.concat(proj_rows, ignore_index=True) if proj_rows else pd.DataFrame(columns=["unit_id", "month"])
    if len(proj):
        # Latest projection issued for each target month (ML1 beats ML2 if issued the same day).
        proj["_rank"] = proj["ipc_proj_scenario"].map({"ML1": 0, "ML2": 1})
        proj = (proj.sort_values(["ipc_proj_issued", "_rank"], ascending=[False, True])
                    .drop_duplicates(["unit_id", "month"]).drop(columns="_rank"))
    out = cs.merge(proj, on=["unit_id", "month"], how="outer")
    for c in FEWS_COLS:                       # same columns for every country (e.g. Sudan has no CS)
        if c not in out:
            out[c] = pd.NA if c != "ipc_proj_issued" else pd.NaT
    out["ipc_cs_observed"] = out["ipc_cs_coverage"].notna().astype(int)
    for c in [c for c in FEWS_COLS if c not in ("ipc_proj_scenario", "ipc_proj_issued")]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    months = sorted(out["month"].unique())
    value_cols = FEWS_COLS
    note = ("FEWS NET classifications: " +
            "; ".join(f"{s} {ps:%Y-%m}..{pe:%Y-%m} (issued {rd:%Y-%m-%d})" for (s, ps, pe, rd) in groups.groups))
    # Snapshots: only months that have a classification are observed (no zero-fill).
    return SourceResult(out, pd.Timestamp(months[0]), pd.Timestamp(months[-1]), False, value_cols, note)


def refresh(iso3: str, force: bool = False) -> SourceResult:
    weight = path("data/processed/static") / f"{iso3.lower()}_weight.tif"     # population/buildings grid
    return refresh_source("fewsnet", iso3, files() + [path(country_cfg(iso3)["cod_ab_zip"]), weight], build, force)

"""Admin-2 spine: COD-AB districts -> modelling units, plus the p-code crosswalk."""
from __future__ import annotations

from functools import lru_cache

import geopandas as gpd
import pandas as pd

from .common import cfg, country_cfg, path


@lru_cache
def admin2(iso3: str) -> gpd.GeoDataFrame:
    """COD-AB admin-2 polygons in EPSG:4326 with synthetic p-codes applied (delivery-record level)."""
    c = country_cfg(iso3)
    zp = path(c["cod_ab_zip"])
    gdf = gpd.read_file(f"zip://{zp}!{c['cod_ab_layer']}")
    if gdf.crs is None:
        raise ValueError(f"{zp}: no CRS (.prj missing?) - refusing to assume a projection")
    gdf = gdf.to_crs("EPSG:4326")
    # Normalise column names across COD-AB vintages (some use ADM2_PCODE / adm2_en).
    gdf.columns = [col.lower() if col != "geometry" else col for col in gdf.columns]
    for lvl in (1, 2):
        if f"adm{lvl}_name" not in gdf and f"adm{lvl}_en" in gdf:
            gdf[f"adm{lvl}_name"] = gdf[f"adm{lvl}_en"]
    missing = [k for k in ("adm1_pcode", "adm1_name", "adm2_pcode", "adm2_name") if k not in gdf]
    if missing:
        raise ValueError(f"{zp}: missing columns {missing}")
    for s in c.get("synthetic_pcodes", []):
        m = (gdf["adm1_pcode"] == s["adm1_pcode"]) & (gdf["adm2_name"] == s["adm2_name"])
        gdf.loc[m, "adm2_pcode"] = s["pcode"]
    if not gdf["adm2_pcode"].is_unique:
        raise ValueError(f"{iso3}: duplicate admin-2 p-codes after synthetic codes")
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf


@lru_cache
def crosswalk(iso3: str) -> pd.DataFrame:
    """adm2_pcode -> unit_id (modelling unit)."""
    c = country_cfg(iso3)
    a = admin2(iso3)
    merge = set(c.get("merge_adm1_to_unit", []))
    unit = a["adm2_pcode"].where(~a["adm1_pcode"].isin(merge), a["adm1_pcode"])
    return pd.DataFrame({"adm2_pcode": a["adm2_pcode"], "unit_id": unit,
                         "adm2_name": a["adm2_name"], "adm1_pcode": a["adm1_pcode"],
                         "adm1_name": a["adm1_name"]})


@lru_cache
def units(iso3: str) -> gpd.GeoDataFrame:
    """Modelling units (merged where configured), EPSG:4326."""
    a = admin2(iso3).merge(crosswalk(iso3)[["adm2_pcode", "unit_id"]], on="adm2_pcode")
    g = a.dissolve(by="unit_id", aggfunc={"adm2_name": lambda s: s.iloc[0] if len(s) == 1 else None,
                                           "adm1_pcode": "first", "adm1_name": "first",
                                           "adm2_pcode": "count"}).reset_index()
    g = g.rename(columns={"adm2_pcode": "n_adm2", "adm2_name": "unit_name"})
    g["unit_name"] = g["unit_name"].fillna(g["adm1_name"])
    g["country"] = iso3.upper()
    g["area_km2"] = g.to_crs(cfg()["equal_area_crs"]).area / 1e6
    return g[["unit_id", "unit_name", "adm1_pcode", "adm1_name", "n_adm2", "country", "area_km2", "geometry"]]


def map_pcodes(pcodes: pd.Series, iso3: str) -> pd.Series:
    """Source admin-2 p-codes -> unit_id (NaN if unknown). Never matches on names."""
    cw = crosswalk(iso3).set_index("adm2_pcode")["unit_id"]
    return pcodes.astype("string").str.strip().map(cw)

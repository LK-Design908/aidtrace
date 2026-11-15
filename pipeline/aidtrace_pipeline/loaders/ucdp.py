"""UCDP GED: open-licence conflict events -> district-month counts and fatalities.

Rules
- Events with where_prec <= 3 are placed by coordinates (point-in-polygon, with coastal snap).
- Coarser events (admin-1, linear feature, country) are NOT forced into a district. They are
  counted per adm1 in `ucdp_imprecise_events_adm1` / `_deaths_adm1` on every unit in that adm1,
  and country-level ones in `ucdp_imprecise_events_country`, so the model can see them.
- Month = month of date_start when date_start and date_end fall in the same month; events
  spanning months are counted in `ucdp_events_multimonth` (month of date_start) and kept out of
  the core counts.
- Within GED coverage, a district-month with no events is 0 (recorded zero, which is not the
  same as no violence: under-reporting in remote areas).
"""
from __future__ import annotations

import zipfile
from functools import lru_cache

import geopandas as gpd
import pandas as pd

from ..common import SourceResult, assign_points, cfg, country_cfg, path, refresh_source, to_month
from ..spine import admin2, crosswalk, units

USECOLS = ["id", "type_of_violence", "country", "where_prec", "date_prec", "date_start", "date_end",
           "latitude", "longitude", "adm_1", "best", "low", "high", "deaths_civilians"]


def _zip() -> "path":
    return path(cfg()["ucdp"]["ged_zip"])


def candidate_file():
    """Latest promoted UCDP Candidate (monthly) release fetched from ucdp.uu.se, or None."""
    from ..fetch.core import latest_promoted
    d = latest_promoted("ucdp")
    return next(d.glob("*.csv"), None) if d else None


@lru_cache
def _srp_events() -> pd.DataFrame:
    """GED rows for all SRP countries, read once from the zip and cached as parquet (rebuilt if the zip changes)."""
    cache = path("data/processed/ucdp/_srp_events.parquet")
    cand = candidate_file()
    newest_in = max(_zip().stat().st_mtime, cand.stat().st_mtime if cand else 0)
    if cache.exists() and cache.stat().st_mtime > newest_in:
        return pd.read_parquet(cache)
    names = {n for c in cfg()["countries"].values() for n in c["ucdp_names"] + c.get("ucdp_extra_names", [])}
    with zipfile.ZipFile(_zip()) as z:
        member = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        with z.open(member) as f:
            chunks = [ch[ch["country"].isin(names)] for ch in
                      pd.read_csv(f, usecols=USECOLS, chunksize=200_000, low_memory=False)]
    ev = pd.concat(chunks, ignore_index=True)
    ev["date_start"] = pd.to_datetime(ev["date_start"])
    ev["date_end"] = pd.to_datetime(ev["date_end"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    ev["ucdp_release"] = "GED"
    # Candidate (monthly) events extend coverage past the final GED release. Only months after the GED
    # release's last month are taken, so nothing is double-counted when the next GED supersedes them.
    if cand is not None:
        c = pd.read_csv(cand, low_memory=False, encoding_errors="replace")
        c = c[c["country"].isin(names)][USECOLS].copy()
        c["date_start"] = pd.to_datetime(c["date_start"])
        c["date_end"] = pd.to_datetime(c["date_end"])
        ged_last = ev["date_start"].max().to_period("M")
        c = c[c["date_start"].dt.to_period("M") > ged_last]
        c["ucdp_release"] = f"Candidate {cand.parent.name}"
        ev = pd.concat([ev, c], ignore_index=True)
    ev.to_parquet(cache, index=False)
    return ev


@lru_cache
def read_events(iso3: str) -> pd.DataFrame:
    """GED rows for the country. `primary` = coded under this country's own name; rows coded under
    `ucdp_extra_names` (e.g. pre-2011 'Sudan' for South Sudan) are kept for spatial assignment only."""
    c = country_cfg(iso3)
    ev = _srp_events()
    ev = ev[ev["country"].isin(c["ucdp_names"] + c.get("ucdp_extra_names", []))].copy()
    ev["primary"] = ev["country"].isin(c["ucdp_names"])
    return ev.reset_index(drop=True)


JOIN_RATE_FROM = pd.Timestamp("2012-01-01")   # after South Sudan's independence (boundary change)


def build(iso3: str) -> SourceResult:
    ev = read_events(iso3).copy()
    maxp = cfg()["ucdp"]["max_where_prec_for_district"]
    ev["month"] = to_month(ev["date_start"])
    ev["single_month"] = ev["date_start"].dt.to_period("M") == ev["date_end"].dt.to_period("M")

    precise = ev[ev["where_prec"] <= maxp].copy()
    pts = gpd.GeoDataFrame(precise, geometry=gpd.points_from_xy(precise.longitude, precise.latitude), crs="EPSG:4326")
    precise["unit_id"] = assign_points(pts, units(iso3)).values
    # Join rate: own-country events since 2012 (current boundaries). Extra-name events are expected
    # to fall mostly outside this country and are simply dropped if they do.
    jr = precise[precise["primary"] & (precise["date_start"] >= JOIN_RATE_FROM)]
    join_stats = {"n_precise": len(jr), "n_precise_joined": int(jr["unit_id"].notna().sum())}
    ev = ev[ev["primary"]]                     # imprecise counts: own-country events only

    core = precise[precise["unit_id"].notna() & precise["single_month"]]
    g = core.groupby(["unit_id", "month"])
    out = pd.DataFrame({
        "ucdp_events": g.size(),
        "ucdp_deaths_best": g["best"].sum(),
        "ucdp_deaths_low": g["low"].sum(),
        "ucdp_deaths_high": g["high"].sum(),
        "ucdp_deaths_civilians": g["deaths_civilians"].sum(),
        "ucdp_events_fatal": g["best"].apply(lambda s: int((s > 0).sum())),
    })
    for t, lab in [(1, "statebased"), (2, "nonstate"), (3, "onesided")]:
        out[f"ucdp_events_{lab}"] = core[core["type_of_violence"] == t].groupby(["unit_id", "month"]).size()
        out[f"ucdp_deaths_{lab}"] = core[core["type_of_violence"] == t].groupby(["unit_id", "month"])["best"].sum()
    multi = precise[precise["unit_id"].notna() & ~precise["single_month"]].groupby(["unit_id", "month"]).size()
    out = out.join(multi.rename("ucdp_events_multimonth"), how="outer")

    # Imprecise events: spread the adm1 count onto every unit in that adm1 (as context, not location).
    imp = ev[ev["where_prec"] > maxp].copy()
    a1 = _adm1_lookup(iso3, imp)
    imp["adm1_pcode"] = a1
    adm1_counts = imp[imp["adm1_pcode"].notna()].groupby(["adm1_pcode", "month"]).agg(
        ucdp_imprecise_events_adm1=("id", "size"), ucdp_imprecise_deaths_adm1=("best", "sum")).reset_index()
    cw = crosswalk(iso3)[["unit_id", "adm1_pcode"]].drop_duplicates()
    adm1_units = adm1_counts.merge(cw, on="adm1_pcode").drop(columns="adm1_pcode").set_index(["unit_id", "month"])
    out = out.join(adm1_units, how="outer")
    ctry = imp[imp["adm1_pcode"].isna()].groupby("month").size().rename("ucdp_imprecise_events_country").reset_index()
    ctry = ctry.merge(pd.DataFrame({"unit_id": units(iso3)["unit_id"]}), how="cross").set_index(["unit_id", "month"])
    out = out.join(ctry, how="outer").reset_index()

    out = out.fillna(0)
    out["ucdp_fatal_any"] = (out["ucdp_events_fatal"] > 0).astype(int)
    first = to_month(pd.Series([pd.Timestamp("1989-01-01")])).iloc[0]
    # Coverage ends where the GED release ends (all SRP countries), not at this country's last
    # event: Djibouti's last event is 2021-08, but 2021-09..2025-12 are recorded zeros, not missing.
    last = to_month(pd.Series([_srp_events()["date_start"].max()])).iloc[0]
    value_cols = [c for c in out.columns if c not in ("unit_id", "month")]
    extra = int((~precise["primary"] & precise["unit_id"].notna()).sum())
    cand = candidate_file()
    note = (f"GED {_zip().name}" + (f" + Candidate {cand.name} (months after the GED release)" if cand else "")
            + f"; {len(ev)} own-country events; {join_stats['n_precise_joined']}/{join_stats['n_precise']} "
            f"precise (where_prec<={maxp}) events since 2012 joined to units"
            + (f"; {extra} events coded under {country_cfg(iso3).get('ucdp_extra_names')} placed inside the country" if extra else ""))
    res = SourceResult(out, first, last, True, value_cols, note)
    res.df.attrs.update(join_stats)
    return res


def _adm1_lookup(iso3: str, imp: pd.DataFrame) -> pd.Series:
    """adm1 p-code for imprecise events: point-in-adm1 when coordinates are an adm1 centroid
    (where_prec 4); otherwise NaN (country-level / linear features)."""
    a = admin2(iso3).dissolve(by="adm1_pcode").reset_index()[["adm1_pcode", "geometry"]]
    a = a.rename(columns={"adm1_pcode": "unit_id"})
    sub = imp[imp["where_prec"] == 4]
    pts = gpd.GeoDataFrame(sub, geometry=gpd.points_from_xy(sub.longitude, sub.latitude), crs="EPSG:4326")
    res = pd.Series(pd.NA, index=imp.index, dtype="object")
    if len(sub):
        res.loc[sub.index] = assign_points(pts, a).values
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    inputs = [_zip(), path(country_cfg(iso3)["cod_ab_zip"])] + ([candidate_file()] if candidate_file() else [])
    return refresh_source("ucdp", iso3, inputs, build, force)

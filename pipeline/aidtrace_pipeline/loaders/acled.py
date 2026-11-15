"""ACLED (licence-restricted): events -> aggregated district-month counts.

Output goes to data/processed/restricted/ only and is never merged into panel.parquet, the
Health Cluster export, demo mode or any unauthenticated route (brief §2.2). Only aggregated
counts are produced (no event rows, notes, actors or coordinates), so events cannot be
reconstructed from the output.

- geo_precision <= 2 placed by coordinates; 3 (larger region) counted as imprecise.
- time_precision 3 (month or coarser) kept in the month of event_date.
- Partial first/last months of the export window are excluded from coverage.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ..common import SourceResult, assign_points, cfg, country_cfg, path, refresh_source, to_month
from ..spine import units

USECOLS = ["event_id_cnty", "event_date", "time_precision", "event_type", "country",
           "latitude", "longitude", "geo_precision", "fatalities", "civilian_targeting"]

EVENT_TYPES = {
    "Battles": "battles",
    "Violence against civilians": "vac",
    "Explosions/Remote violence": "remote",
    "Riots": "riots",
    "Protests": "protests",
    "Strategic developments": "strategic",
}


def files() -> list[Path]:
    return sorted(path(cfg()["acled"]["dir"]).glob("*.csv"))


@lru_cache
def read_events(iso3: str) -> pd.DataFrame:
    names = country_cfg(iso3)["acled_names"]
    frames = [pd.read_csv(f, usecols=USECOLS, encoding="utf-8") for f in files()]
    ev = pd.concat(frames, ignore_index=True).drop_duplicates("event_id_cnty")
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    ev.attrs["window"] = (min(f["event_date"].min() for f in frames), max(f["event_date"].max() for f in frames))
    return ev[ev["country"].isin(names)].copy()


def _window(iso3: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Complete months covered by the export window(s)."""
    ev = read_events(iso3)
    lo, hi = (pd.to_datetime(x) for x in ev.attrs["window"])
    first = lo.to_period("M").to_timestamp()
    if lo.day != 1:
        first = (lo.to_period("M") + 1).to_timestamp()
    last = hi.to_period("M").to_timestamp()
    if hi != (hi.to_period("M") + 1).to_timestamp() - pd.Timedelta(days=1):
        last = (hi.to_period("M") - 1).to_timestamp()
    return first, last


def build(iso3: str) -> SourceResult:
    ev = read_events(iso3).copy()
    first, last = _window(iso3)
    ev["month"] = to_month(ev["event_date"])
    ev = ev[(ev["month"] >= first) & (ev["month"] <= last)]
    maxp = cfg()["acled"]["max_geo_precision_for_district"]
    precise = ev[ev["geo_precision"] <= maxp].copy()
    pts = gpd.GeoDataFrame(precise, geometry=gpd.points_from_xy(precise.longitude, precise.latitude), crs="EPSG:4326")
    precise["unit_id"] = assign_points(pts, units(iso3)).values
    joined = precise[precise["unit_id"].notna()]

    g = joined.groupby(["unit_id", "month"])
    out = pd.DataFrame({"acled_events": g.size(), "acled_fatalities": g["fatalities"].sum(),
                        "acled_events_civtarget": g["civilian_targeting"].apply(lambda s: int(s.notna().sum()))})
    for et, lab in EVENT_TYPES.items():
        out[f"acled_events_{lab}"] = joined[joined["event_type"] == et].groupby(["unit_id", "month"]).size()
    imp = ev[ev["geo_precision"] > maxp].groupby("month").size().rename("acled_imprecise_events_country").reset_index()
    imp = imp.merge(pd.DataFrame({"unit_id": units(iso3)["unit_id"]}), how="cross").set_index(["unit_id", "month"])
    out = out.join(imp, how="outer").fillna(0).reset_index()
    out["acled_any"] = (out["acled_events"] > 0).astype(int)
    note = (f"ACLED {len(files())} file(s); {len(ev)} events in complete months {first:%Y-%m}..{last:%Y-%m}; "
            f"{len(precise)} with geo_precision<={maxp}; {len(joined)} joined")
    res = SourceResult(out, first, last, True, [c for c in out.columns if c not in ("unit_id", "month")], note)
    res.df.attrs.update(n_precise=len(precise), n_precise_joined=len(joined))
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    return refresh_source("acled", iso3, files() + [path(country_cfg(iso3)["cod_ab_zip"])], build, force,
                          restricted=True)

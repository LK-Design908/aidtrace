"""Connectors: one function per allowlisted source. Each downloads into holding, runs its checks against the
previous promoted release, and promotes only if the checks pass (otherwise the file is HELD for review).
All parsing here treats downloads as untrusted data (no eval, no execution, no instructions followed)."""
from __future__ import annotations

import io
import re
import zipfile

import geopandas as gpd
import pandas as pd

from ..common import assign_points, cfg, log
from ..spine import units
from .core import CheckResult, Download, fetch_file, get, latest_promoted, promote

SRP5 = ["DJI", "SOM", "SDN", "SSD", "KEN"]
SRP6 = SRP5 + ["ETH"]
COUNTRY_NAMES = {"DJI": "Djibouti", "SOM": "Somalia", "SDN": "Sudan", "SSD": "South Sudan", "KEN": "Kenya", "ETH": "Ethiopia"}
MIN_JOIN = 0.95


def _join_rate(df: pd.DataFrame, lon: str, lat: str, isos=SRP6) -> float:
    """Share of points that fall inside one of the SRP countries' admin-2 units (5 km coastal snap)."""
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs="EPSG:4326")
    hit = pd.Series(False, index=pts.index)
    for iso in isos:
        u = units(iso)
        a = assign_points(pts[~hit], u)
        hit.loc[a.index] = hit.loc[a.index] | a.notna()
    return float(hit.mean()) if len(hit) else 1.0


# ================================================================== UCDP Candidate (monthly)

UCDP_COLS = ["id", "type_of_violence", "country", "where_prec", "date_prec", "date_start", "date_end",
             "latitude", "longitude", "adm_1", "best", "low", "high", "deaths_civilians"]


def ucdp_candidate() -> list[dict]:
    page = get("ucdp", "https://ucdp.uu.se/downloads/index.html").text
    files = sorted(set(re.findall(r"candidateged/(GEDEvent_v(\d\d)_01_\d\d_(\d\d)\.csv)", page)))
    if not files:
        return [{"source": "ucdp", "status": "nothing found on downloads page"}]
    fname, yy, mm = max(files, key=lambda f: (f[1], f[2]))          # latest cumulative file of the latest year
    version = f"candidate_v{yy}_01_{yy}_{mm}"
    d = fetch_file("ucdp", version, f"https://ucdp.uu.se/downloads/candidateged/{fname}", fname)
    return [_finish(d, _check_ucdp(d))]


def _check_ucdp(d: Download) -> CheckResult:
    reasons, stats = [], {}
    try:
        df = pd.read_csv(d.file, low_memory=False, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(d.file, low_memory=False, encoding="latin-1")
    missing = [c for c in UCDP_COLS if c not in df.columns]
    if missing:
        return CheckResult(False, [f"missing columns {missing}"], {})
    df["date_start"] = pd.to_datetime(df["date_start"], errors="coerce")
    horn = df[df["country"].isin(list(COUNTRY_NAMES.values()))]
    stats.update(rows=len(df), horn_rows=len(horn), date_min=str(df["date_start"].min())[:10], date_max=str(df["date_start"].max())[:10],
                 horn_by_country=horn["country"].value_counts().to_dict())
    if df["date_start"].isna().mean() > 0.01:
        reasons.append("unparseable dates > 1%")
    if not df["best"].between(0, 100_000).all():
        reasons.append("fatality estimates out of range")
    precise = horn[horn["where_prec"] <= cfg()["ucdp"]["max_where_prec_for_district"]]
    jr = _join_rate(precise, "longitude", "latitude")
    stats["precise_join_rate"] = round(jr, 4)
    if jr < MIN_JOIN:
        reasons.append(f"spatial join rate {jr:.1%} < 95%")
    prev = latest_promoted("ucdp")
    if prev is not None and prev.name != d.version:
        pf = next(prev.glob("*.csv"), None)
        if pf is not None:
            p = pd.read_csv(pf, usecols=["date_start", "country"], low_memory=False)
            p = p[p["country"].isin(list(COUNTRY_NAMES.values()))]
            stats["previous_horn_rows"] = len(p)
            if len(horn) < 0.8 * len(p):
                reasons.append(f"Horn rows dropped from {len(p)} to {len(horn)} (>20%)")
            if str(pd.to_datetime(p["date_start"]).max())[:7] > stats["date_max"][:7]:
                reasons.append("coverage ends earlier than previous release")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== FEWS NET IPC packages (history)

FEWS_ISO2 = {"SOM": "SO", "KEN": "KE", "SDN": "SD", "SSD": "SS", "DJI": "DJ"}


def fewsnet_history(start_year: int = 2017, months=(2, 6, 10)) -> list[dict]:
    """IPC-compatible classification packages (CS/ML1/ML2 shapefiles) from the FEWS NET Data Warehouse,
    one per country and outlook cycle. Countries/dates without a package return an empty body (skipped)."""
    import datetime as dt
    out = []
    try:
        get("fewsnet", "https://fdw.fews.net/api/ipcphase/", params={"format": "json", "page_size": 1})
    except Exception as e:  # noqa: BLE001  (allowlist note: check service is live before use)
        return [{"source": "fewsnet", "status": f"service not live: {e}"}]
    today = dt.date.today()
    for iso, cc in FEWS_ISO2.items():
        for y in range(start_year, today.year + 1):
            for m in months:
                if (y, m) > (today.year, today.month):
                    continue
                version = f"{cc}_{y}{m:02d}"
                try:
                    d = fetch_file("fewsnet", version, "https://fdw.fews.net/api/ipcpackage/", f"{version}.zip",
                                   params={"country_code": cc, "collection_date": f"{y}-{m:02d}-01"})
                except Exception as e:  # noqa: BLE001
                    out.append({"source": "fewsnet", "version": version, "status": f"error {e}"})
                    continue
                if d.size < 1000:                       # no package for this country/date
                    d.file.unlink(missing_ok=True)
                    continue
                out.append(_finish(d, _check_fews(d, cc)))
    return out


def _check_fews(d: Download, cc: str) -> CheckResult:
    reasons, stats = [], {}
    try:
        names = zipfile.ZipFile(d.file).namelist()
    except zipfile.BadZipFile:
        return CheckResult(False, ["not a zip"], {})
    main = [n for n in names if re.fullmatch(rf"{cc}_\d{{6}}_(CS|ML1|ML2)\.shp", n)]
    stats["layers"] = main
    if not main:
        return CheckResult(False, ["no CS/ML1/ML2 shapefiles"], stats)
    for n in main:
        stem = n[:-4]
        if any(stem + ext not in names for ext in (".shx", ".dbf", ".prj")):
            reasons.append(f"{n}: missing sidecar (.shx/.dbf/.prj)")
            continue
        g = gpd.read_file(f"zip://{d.file}!{n}")
        scen = stem.split("_")[-1]
        for col in ("cov_start", "cov_end", "report_mon", scen):
            if col not in g.columns:
                reasons.append(f"{n}: missing field {col}")
        if scen in g.columns:
            v = pd.to_numeric(g[scen], errors="coerce")
            bad = ~(v.between(1, 5) | v.isin([0, 66, 77, 88, 99]) | v.isna())
            if bad.any():
                reasons.append(f"{n}: {int(bad.sum())} unexpected phase values")
            stats[scen] = {"features": len(g), "phases": v[v.between(1, 5)].value_counts().sort_index().to_dict()}
        if g.crs is None:
            reasons.append(f"{n}: no CRS")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== WFP food prices (HDX, per country)

WFP_HDX = {"SOM": "somalia", "SDN": "sudan", "KEN": "kenya", "SSD": "south-sudan", "DJI": "djibouti"}


def wfp_prices() -> list[dict]:
    out = []
    for iso, slug in WFP_HDX.items():
        meta = get("wfp_prices", "https://data.humdata.org/api/3/action/package_show",
                   params={"id": f"wfp-food-prices-for-{slug}"}).json()["result"]
        res = next(r for r in meta["resources"] if r["name"].endswith("Food Prices"))
        version = f"{iso}_{meta['metadata_modified'][:10]}"
        d = fetch_file("wfp_prices", version, res["url"], f"wfp_food_prices_{iso.lower()}.csv",
                       hdx_dataset=meta["name"], hdx_modified=meta["metadata_modified"], licence_hdx=meta["license_id"])
        out.append(_finish(d, _check_wfp(d, iso)))
    return out


def read_wfp_hdx(fp) -> pd.DataFrame:
    """HDX WFP files carry an HXL tag row (#date, #adm1...) under the header: drop it."""
    df = pd.read_csv(fp, low_memory=False, encoding="utf-8", encoding_errors="replace")
    return df[~df["date"].astype(str).str.startswith("#")].copy()


def _check_wfp(d: Download, iso: str) -> CheckResult:
    reasons, stats = [], {}
    df = read_wfp_hdx(d.file)
    need = ["date", "market_id", "latitude", "longitude", "category", "commodity", "commodity_id", "unit",
            "pricetype", "currency", "price", "usdprice"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        return CheckResult(False, [f"missing columns {miss}"], {})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    stats.update(rows=len(df), date_min=str(df["date"].min())[:10], date_max=str(df["date"].max())[:10],
                 markets=int(df["market_id"].nunique()))
    if (df["price"] <= 0).mean() > 0.01:
        reasons.append("non-positive prices > 1%")
    mk = df.drop_duplicates("market_id").dropna(subset=["latitude", "longitude"])
    for c in ("latitude", "longitude"):
        mk[c] = pd.to_numeric(mk[c], errors="coerce")
    jr = _join_rate(mk.dropna(subset=["latitude", "longitude"]), "longitude", "latitude", isos=[iso])
    stats["market_join_rate"] = round(jr, 4)
    if jr < MIN_JOIN:
        reasons.append(f"market join rate {jr:.1%} < 95%")
    prev = _previous_file("wfp_prices", d, prefix=f"{iso}_")
    if prev is not None:
        p = read_wfp_hdx(prev)
        stats["previous_rows"] = len(p)
        if len(df) < 0.8 * len(p):
            reasons.append(f"rows dropped from {len(p)} to {len(df)} (>20%)")
        if str(pd.to_datetime(p["date"]).max())[:7] > stats["date_max"][:7]:
            reasons.append("coverage ends earlier than previous release")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== UNHCR PRMN (Somalia, via HDX)

def unhcr_prmn() -> list[dict]:
    meta = get("unhcr_prmn", "https://data.humdata.org/api/3/action/package_show",
               params={"id": "somalia-internally-displaced-persons-idps"}).json()["result"]
    if meta["organization"]["name"] not in ("unhcr", "unhcr-somalia") and "UNHCR" not in meta["organization"]["title"]:
        return [{"source": "unhcr_prmn", "status": "publisher is not UNHCR: not fetched"}]
    res = max((r for r in meta["resources"] if r["format"].upper() in ("XLSX", "CSV") and (r.get("size") or 0) > 100_000),
              key=lambda r: r.get("size") or 0)
    version = meta["metadata_modified"][:10]
    ext = ".xlsx" if res["format"].upper() == "XLSX" else ".csv"
    d = fetch_file("unhcr_prmn", version, res["url"], f"prmn_somalia{ext}", hdx_resource=res["name"])
    return [_finish(d, _check_prmn(d))]


def read_prmn(fp) -> pd.DataFrame:
    if str(fp).endswith(".xlsx"):
        x = pd.ExcelFile(fp)
        sheet = max(x.sheet_names, key=lambda s: x.parse(s, nrows=5).shape[1])
        df = x.parse(sheet)
    else:
        df = pd.read_csv(fp, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _check_prmn(d: Download) -> CheckResult:
    df = read_prmn(d.file)
    cols = {c.lower(): c for c in df.columns}
    stats = {"rows": len(df), "columns": list(df.columns)[:20]}
    reasons = []
    if not any("arrival" in c or "current" in c and "district" in c for c in cols):
        reasons.append("no arrival/current district column recognised (schema changed?)")
    if not any("reason" in c for c in cols):
        reasons.append("no displacement reason column")
    if not any("people" in c or "number" in c or "persons" in c for c in cols):
        reasons.append("no people count column")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== WorldPop population 2020 (constrained to countries lacking it)

def worldpop_population(isos=("SOM", "SDN", "DJI"), year: str = "2020") -> list[dict]:
    out = []
    for iso in isos:
        data = get("worldpop", "https://www.worldpop.org/rest/data/pop/wpgp", params={"iso3": iso}).json()["data"]
        rec = next((r for r in data if r.get("popyear") == year), None)
        if rec is None:
            out.append({"source": "worldpop", "status": f"no {year} record for {iso}"})
            continue
        url = rec["files"][0]
        fname = url.rsplit("/", 1)[-1]
        d = fetch_file("worldpop", f"{iso}_wpgp_{year}", url, fname, title=rec.get("title"), doi=rec.get("doi"),
                       licence_url=rec.get("license"))
        out.append(_finish(d, _check_worldpop(d, iso)))
    return out


def _check_worldpop(d: Download, iso: str) -> CheckResult:
    import numpy as np
    import rasterio
    reasons, stats = [], {}
    with rasterio.open(d.file) as r:
        stats.update(crs=str(r.crs), res=r.res, width=r.width, height=r.height)
        if r.crs is None or r.crs.to_epsg() != 4326:
            reasons.append(f"unexpected CRS {r.crs}")
        total = 0.0
        for _, w in r.block_windows(1):
            a = r.read(1, window=w, masked=True).filled(0)
            a = a[np.isfinite(a) & (a > 0)]
            total += float(a.sum())
    stats["national_total"] = round(total)
    plaus = {"SOM": (8e6, 25e6), "SDN": (30e6, 60e6), "DJI": (0.5e6, 2e6)}.get(iso)
    if plaus and not (plaus[0] <= total <= plaus[1]):
        reasons.append(f"national total {total:,.0f} outside plausible range {plaus}")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== CHIRPS v3 monthly rainfall (Africa)

CHIRPS_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/africa/tifs/"


def chirps_monthly(start_year: int = 1991) -> list[dict]:
    """Monthly CHIRPS v3 Africa rasters from 1991 (1991-2020 baseline + recent months). Each month is its
    own immutable version; already-promoted months are skipped."""
    listing = get("chirps", CHIRPS_BASE).text
    files = sorted(set(re.findall(r'href="(chirps-v3\.0\.(\d{4})\.(\d{2})\.tif)"', listing)))
    out = []
    for fname, y, m in files:
        if int(y) < start_year:
            continue
        version = f"{y}{m}"
        if (latest_promoted("chirps") is not None) and (latest_promoted("chirps").parent / version / fname).exists():
            continue
        d = fetch_file("chirps", version, CHIRPS_BASE + fname, fname)
        out.append(_finish(d, _check_chirps(d)))
    return out


def _check_chirps(d: Download) -> CheckResult:
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(d.file) as r:
        if r.crs is None or r.crs.to_epsg() != 4326:
            return CheckResult(False, [f"unexpected CRS {r.crs}"], {})
        w = from_bounds(21.5, -5.5, 52.0, 23.5, r.transform)     # Horn bounding box
        a = r.read(1, window=w, masked=True)
    a = np.ma.masked_equal(a, -9999)            # CHIRPS ocean/no-data sentinel (not declared in file metadata)
    v = a.compressed()
    stats = {"horn_mean_mm": round(float(v.mean()), 2) if v.size else None, "valid_share": round(float(v.size / a.size), 3)}
    reasons = []
    if v.size == 0 or stats["valid_share"] < 0.5:
        reasons.append("Horn window mostly nodata")
    elif (v < 0).any() or v.max() > 3000:
        reasons.append("monthly rainfall out of range (<0 or >3000 mm)")
    return CheckResult(not reasons, reasons, stats)


# ================================================================== helpers

def _previous_file(source: str, d: Download, prefix: str = ""):
    from ..common import path
    base = path("data/raw") / f"fetched_{source}"
    if not base.exists():
        return None
    vs = sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith(prefix) and p.name != d.version)
    for v in reversed(vs):
        f = v / d.file.name
        if f.exists():
            return f
    return None

def _finish(d: Download, check: CheckResult) -> dict:
    dest = promote(d, check)
    out = {"source": d.source, "version": d.version, "file": d.file.name,
           "status": "promoted" if dest else "HELD for review", "reasons": check.reasons, "stats": check.stats}
    log.info("%s %s %s -> %s", d.source, d.version, d.file.name, out["status"])
    return out

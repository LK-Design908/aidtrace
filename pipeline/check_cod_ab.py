"""Verify an OCHA COD-AB shapefile zip: sidecars, CRS, feature counts, p-code format,
and join rate against p-codes used by other sources. Read-only on data/raw.

Usage: python -I pipeline/check_cod_ab.py <shp.zip> <iso3> [--dtm <dtm.xlsx>]
"""
import argparse
import re
import sys
import zipfile

import geopandas as gpd
import pandas as pd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path")
    ap.add_argument("iso3")
    ap.add_argument("--dtm", help="IOM DTM SOM xlsx to test district p-code join")
    a = ap.parse_args()
    iso = a.iso3.lower()
    ok = True

    names = zipfile.ZipFile(a.zip_path).namelist()
    for shp in [n for n in names if n.endswith(".shp")]:
        stem = shp[:-4]
        missing = [ext for ext in (".shx", ".dbf", ".prj") if stem + ext not in names]
        if missing:
            ok = False
            print(f"MISSING sidecars for {shp}: {missing}")
    print(f"sidecar check: {'all present' if ok else 'FAILED'} ({sum(n.endswith('.shp') for n in names)} layers)")

    for lvl in (0, 1, 2):
        gdf = gpd.read_file(f"zip://{a.zip_path}!{iso}_admin{lvl}.shp")
        print(f"\nadmin{lvl}: {len(gdf)} features, CRS={gdf.crs.to_string() if gdf.crs else None}")
        print("  columns:", list(gdf.columns))
        print("  bounds:", [round(b, 3) for b in gdf.total_bounds])
        print("  invalid geometries:", int((~gdf.is_valid).sum()))
        pc = f"adm{lvl}_pcode"
        if pc in gdf.columns:
            print(f"  {pc}: unique={gdf[pc].is_unique}, sample={gdf[pc].head(3).tolist()}")
        if lvl == 2:
            adm2 = gdf
            bad = adm2[~adm2["adm2_pcode"].str.match(rf"^{iso[:2].upper()}\d{{4}}$")]
            print(f"  p-codes not matching {iso[:2].upper()}#### : {len(bad)}")
            for col in [c for c in adm2.columns if re.match(r"adm2_name", c)]:
                nonascii = adm2[col].dropna().map(lambda s: any(ord(ch) > 127 for ch in s)).sum()
                print(f"  {col}: non-ASCII names={nonascii}")
            area = adm2.to_crs("ESRI:102022")  # Africa Albers Equal Area
            km2 = area.area / 1e6
            print(f"  district area km2: min={km2.min():.0f} median={km2.median():.0f} max={km2.max():.0f}")

    if a.dtm:
        d = pd.read_excel(a.dtm, sheet_name=0, usecols=["OCHA District Pcode"])
        codes = d["OCHA District Pcode"].dropna().astype(str).str.strip()
        rate = codes.isin(set(adm2["adm2_pcode"])).mean()
        unmatched = sorted(set(codes) - set(adm2["adm2_pcode"]))
        print(f"\nDTM district p-code join: {rate:.1%} of {len(codes)} rows; unmatched codes: {unmatched[:20]}")
        if rate < 0.95:
            ok = False
            print("  FAIL: join rate below 95%")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""UNHCR PRMN (Somalia): monthly displacement arrivals by district and reason.

PRMN identifies districts by NAME only. The brief forbids name-only joins, so a crosswalk to COD-AB p-codes is
generated for HUMAN REVIEW (data/crosswalks/prmn_som_districts.csv). The loader uses only rows whose
`reviewed` column is "yes". Until the owner reviews it, PRMN contributes nothing to the panel (every
unit-month stays missing, never zero).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from ..common import SourceResult, path, refresh_source, to_month
from ..spine import crosswalk

CROSSWALK = "data/crosswalks/prmn_som_districts.csv"
# Known spelling variants (PRMN name -> COD-AB name). Used only to SUGGEST a match; a human still reviews.
# Banadir: PRMN reports Mogadishu as one area; the panel already merges the 17 COD-AB Banadir districts into
# one unit (SO22), so the region-level code SO22XX maps to exactly that unit.
SPELLING = {"baidoa": "Baydhaba", "kismayo": "Kismaayo", "lasqoray": "Laasqoray", "banadir": "Unspecified"}
NOTES = {
         "badhan": "district not in COD-AB (formed after the boundary set): leave unreviewed or pick parent by hand",
         "dhahar": "district not in COD-AB (formed after the boundary set): leave unreviewed or pick parent by hand",
         "0": "invalid name in source: exclude"}
VALUE_COLS = ["prmn_arrivals", "prmn_arrivals_conflict", "prmn_arrivals_drought", "prmn_arrivals_flood", "prmn_arrivals_other"]


def latest_file() -> Path | None:
    base = path("data/raw/fetched_unhcr_prmn")
    vs = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    return next(iter(sorted(vs[-1].glob("prmn_somalia.*"))), None) if vs else None


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def read() -> pd.DataFrame:
    from ..fetch.connectors import read_prmn
    df = read_prmn(latest_file())
    ren = {"Month End": "month_end", "Current (Arrival) Region": "region", "Current (Arrival) District": "district",
           "Reason": "reason", "Number of Individuals": "people"}
    df = df.rename(columns=ren)[list(ren.values())]
    df["people"] = pd.to_numeric(df["people"], errors="coerce")
    df["month"] = to_month(pd.to_datetime(df["month_end"], errors="coerce"))
    return df.dropna(subset=["month", "district"])


def build_crosswalk() -> pd.DataFrame:
    """Proposed name->p-code matches (exact normalised name within the same region). Preserves any
    `reviewed`/`adm2_pcode` edits already made by a human in the existing file."""
    df = read()
    names = df[["region", "district"]].drop_duplicates()
    cw = crosswalk("SOM")
    cw = cw.assign(_d=cw["adm2_name"].map(_norm), _r=cw["adm1_name"].map(_norm))
    rows = []
    for _, r in names.iterrows():
        cand = cw[(cw["_d"] == _norm(r["district"])) & (cw["_r"] == _norm(r["region"]))]
        if len(cand) == 1:
            m = cand.iloc[0]
            rows.append((r["region"], r["district"], m["adm2_pcode"], m["adm2_name"], m["adm1_name"], "exact name within region", ""))
        else:
            cand2 = cw[cw["_d"] == _norm(r["district"])]
            if len(cand2) == 1:
                m = cand2.iloc[0]
                rows.append((r["region"], r["district"], m["adm2_pcode"], m["adm2_name"], m["adm1_name"],
                             "exact name, REGION DIFFERS: check", ""))
            elif _norm(r["district"]) in SPELLING and len(m2 := cw[(cw["adm2_name"] == SPELLING[_norm(r["district"])])
                                                                    & (cw["_r"] == _norm(r["region"]))]) == 1:
                m = m2.iloc[0]
                rows.append((r["region"], r["district"], m["adm2_pcode"], m["adm2_name"], m["adm1_name"],
                             "SPELLING VARIANT suggested: check", ""))
            else:
                key = _norm(r["district"]) or str(r["district"]).strip()
                rows.append((r["region"], r["district"], "", "", "", NOTES.get(key, "no match: fill adm2_pcode by hand"), ""))
    out = pd.DataFrame(rows, columns=["prmn_region", "prmn_district", "adm2_pcode", "adm2_name", "adm1_name", "match", "reviewed"])
    fp = path(CROSSWALK)
    if fp.exists():                                           # keep human decisions
        old = pd.read_csv(fp, dtype=str).fillna("")
        keep = old[old["reviewed"].str.lower().isin(["yes", "no"])][["prmn_region", "prmn_district", "adm2_pcode", "reviewed"]]
        out = out.drop(columns=[]).merge(keep, on=["prmn_region", "prmn_district"], how="left", suffixes=("", "_human"))
        out["adm2_pcode"] = out["adm2_pcode_human"].where(out["adm2_pcode_human"].notna() & (out["adm2_pcode_human"] != ""), out["adm2_pcode"])
        out["reviewed"] = out["reviewed_human"].fillna(out["reviewed"])
        out = out.drop(columns=["adm2_pcode_human", "reviewed_human"])
    fp.parent.mkdir(parents=True, exist_ok=True)
    out.sort_values(["prmn_region", "prmn_district"]).to_csv(fp, index=False, encoding="utf-8")
    return out


def _reason_group(r: pd.Series) -> pd.Series:
    s = r.astype(str).str.lower()
    return pd.Series(["conflict" if ("conflict" in x or "insecurity" in x) else "drought" if "drought" in x
                      else "flood" if "flood" in x else "other" for x in s], index=r.index)


def empty(note: str) -> SourceResult:
    return SourceResult(pd.DataFrame(columns=["unit_id", "month"] + VALUE_COLS), None, None, False, VALUE_COLS, note)


def build(iso3: str) -> SourceResult:
    if iso3 != "SOM" or latest_file() is None:
        return empty(f"no PRMN data for {iso3}")
    fp = path(CROSSWALK)
    if not fp.exists():
        build_crosswalk()
    cw = pd.read_csv(fp, dtype=str).fillna("")
    ok = cw[cw["reviewed"].str.lower() == "yes"]
    if ok.empty:
        return empty("PRMN crosswalk not yet reviewed: data/crosswalks/prmn_som_districts.csv (set reviewed=yes)")
    df = read().merge(ok[["prmn_region", "prmn_district", "adm2_pcode"]], left_on=["region", "district"],
                      right_on=["prmn_region", "prmn_district"], how="inner")
    units_map = crosswalk("SOM").set_index("adm2_pcode")["unit_id"]
    df["unit_id"] = df["adm2_pcode"].map(units_map)
    df = df.dropna(subset=["unit_id"])
    df["grp"] = _reason_group(df["reason"])
    g = df.groupby(["unit_id", "month"])
    out = pd.DataFrame({"prmn_arrivals": g["people"].sum()})
    for grp in ("conflict", "drought", "flood", "other"):
        out[f"prmn_arrivals_{grp}"] = df[df["grp"] == grp].groupby(["unit_id", "month"])["people"].sum()
    out[VALUE_COLS[1:]] = out[VALUE_COLS[1:]].fillna(0)
    out = out.reset_index()
    res = SourceResult(out, df["month"].min(), df["month"].max(), False, VALUE_COLS,
                       f"UNHCR PRMN {latest_file().name}: {len(df)} rows via {len(ok)} reviewed district names")
    res.df.attrs.update(reviewed_names=len(ok))
    return res


def refresh(iso3: str, force: bool = False) -> SourceResult:
    f = latest_file()
    if iso3 != "SOM" or f is None:
        return empty(f"no PRMN data for {iso3}")
    return refresh_source("prmn", iso3, [f, path(CROSSWALK)], build, force)

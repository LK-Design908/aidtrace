"""Incremental update: (optionally) fetch from the allowlist, refresh only changed sources, rebuild need and
triggers, rescore with the pooled model, and record exactly which data versions the run used.

    python -I -m aidtrace_pipeline.update            # use what is already promoted
    python -I -m aidtrace_pipeline.update --fetch    # first run the allowlisted fetcher (all auto sources)

Nothing held for review is used: loaders read only promoted files under data/raw/. Each run writes
data/processed/model/run_manifest_<UTC timestamp>.json (never overwritten), rule 6 of the data policy.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from .common import PROCESSED, cfg, log, path


def fetched_versions() -> dict:
    """Every promoted fetched file currently on disk, with the SHA-256 recorded at promotion."""
    from .fetch.core import provenance
    sha = {r["file"]: r for r in provenance() if r["event"] == "promoted"}
    out: dict[str, list] = defaultdict(list)
    raw = path("data/raw")
    for d in sorted(raw.glob("fetched_*/*/*")):
        rel = str(d.relative_to(path("."))).replace("\\", "/")
        rec = sha.get(rel, {})
        out[d.parent.parent.name.removeprefix("fetched_")].append(
            {"version": d.parent.name, "file": d.name, "sha256": rec.get("sha256"), "url": rec.get("url"),
             "accessed": rec.get("accessed")})
    return dict(out)


def _summarise_fetched(fv: dict) -> dict:
    out = {}
    for k, v in fv.items():
        vs = sorted({x["version"] for x in v})
        out[k] = {"files": len(v), "versions": len(vs), "first": vs[0], "last": vs[-1]}
    return out


def run(fetch: bool = False, countries: list[str] | None = None) -> dict:
    from . import need, panel, pooled
    started = datetime.now(timezone.utc)
    fetch_summary = None
    if fetch:
        from .fetch.__main__ import run_sources
        from .fetch.licences import write_licences
        fetch_summary = run_sources("all")
        write_licences()
    countries = countries or pooled.COUNTRIES          # pooled scoring trains on all six panels
    reports, needs = {}, {}
    for iso in countries:
        reports[iso] = panel.build_panel(iso)
        needs[iso] = need.run(iso)
    summary = pooled.score() if cfg()["model"].get("scoring") == "pooled" else None

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    mdir = path(PROCESSED) / "model"
    mdir.mkdir(parents=True, exist_ok=True)
    mp = mdir / f"run_manifest_{stamp}.json"
    n = 1
    while mp.exists():                                   # never overwrite
        mp = mdir / f"run_manifest_{stamp}_{n}.json"
        n += 1
    fv = fetched_versions()
    manifest = {
        "run_started": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_finished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": {"scoring": cfg()["model"].get("scoring"), "name": pooled.POOLED_MODEL,
                  "horizons": cfg()["model"]["horizons"], "use_acled": cfg()["use_acled"]},
        "fetch": fetch_summary,
        "sources_per_country": {iso: {k: {kk: r[kk] for kk in ("first", "last", "note")} for k, r in rep["sources"].items()}
                                for iso, rep in reports.items()},
        "panel_end": {iso: rep["end"] for iso, rep in reports.items()},
        "need": needs,
        "risk": summary,
        "fetched_files": fv,
        "fetched_summary": _summarise_fetched(fv),
    }
    mp.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    log.info("run manifest: %s", mp)
    return {"manifest": str(mp.relative_to(path("."))), "panel_end": manifest["panel_end"], "risk": summary,
            "fetched": manifest["fetched_summary"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="run the allowlisted fetcher first")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(fetch=a.fetch), indent=2, default=str))


if __name__ == "__main__":
    main()

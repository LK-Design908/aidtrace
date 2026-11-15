"""Allowlisted data fetching.

    python -I -m aidtrace_pipeline.fetch run [--source ucdp|fewsnet|wfp_prices|unhcr_prmn|worldpop|chirps|all]
    python -I -m aidtrace_pipeline.fetch review                       # list HELD downloads and why
    python -I -m aidtrace_pipeline.fetch approve SOURCE VERSION FILE --note "who reviewed and why"
    python -I -m aidtrace_pipeline.fetch sources                      # allowlist with status and key needs
    python -I -m aidtrace_pipeline.fetch licences                     # regenerate the auto section of LICENCES.md

After a run, rebuild affected tables only: `python -I -m aidtrace_pipeline.update` (incremental).
"""
from __future__ import annotations

import argparse
import json
import logging
import os

from . import connectors as C
from .core import allowlist, approve, held_items
from .licences import write_licences

RUNNERS = {"ucdp": C.ucdp_candidate, "fewsnet": C.fewsnet_history, "wfp_prices": C.wfp_prices,
           "unhcr_prmn": C.unhcr_prmn, "worldpop": C.worldpop_population, "chirps": C.chirps_monthly}


def run_sources(source: str = "all") -> dict:
    results = {}
    for name in (RUNNERS if source == "all" else [source]):
        try:
            results[name] = RUNNERS[name]()
        except Exception as e:  # noqa: BLE001  one source failing must not stop the others
            results[name] = [{"source": name, "status": f"FAILED: {type(e).__name__}: {e}"}]
    return {k: {"promoted": sum(1 for x in v if x.get("status") == "promoted"),
                "held": [f"{x.get('version')}/{x.get('file')}: {x.get('reasons')}" for x in v if x.get("status", "").startswith("HELD")],
                "other": [x["status"] for x in v if x.get("status") not in ("promoted",) and not x.get("status", "").startswith("HELD")][:5]}
            for k, v in results.items()}


def main() -> None:
    ap = argparse.ArgumentParser(prog="aidtrace_pipeline.fetch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", default="all", choices=["all", *RUNNERS])
    sub.add_parser("review")
    a = sub.add_parser("approve")
    a.add_argument("source"), a.add_argument("version"), a.add_argument("file")
    a.add_argument("--note", required=True)
    sub.add_parser("sources")
    sub.add_parser("licences")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "run":
        summary = run_sources(args.source)
        write_licences()
        print(json.dumps(summary, indent=2))
    elif args.cmd == "review":
        print(json.dumps(held_items(), indent=2, ensure_ascii=False))
    elif args.cmd == "approve":
        print(approve(args.source, args.version, args.file, args.note))
        write_licences()
    elif args.cmd == "sources":
        for k, s in allowlist().items():
            need = s.get("key_env")
            have = bool(need and os.environ.get(need))
            print(f"{k:16s} {s['status']:10s} {'key ' + need + (' (set)' if have else ' (missing)') if need else '':38s} {s['name']}")
    elif args.cmd == "licences":
        print(write_licences())


if __name__ == "__main__":
    main()

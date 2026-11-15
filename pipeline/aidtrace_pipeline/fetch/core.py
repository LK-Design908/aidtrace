"""Allowlisted fetcher with a holding area, provenance log, release checks and non-destructive promotion.

Rules implemented (owner allowlist, 2026-11-15):
 1. Only hosts in allowlist.yaml, via official endpoints. No search, no crawling beyond a listed index page.
 2. Every download is logged (URL, access time, version, licence, size, SHA-256) in data/provenance.jsonl, and
    data/LICENCES.md gets an auto-generated section from the allowlist and the log.
 3. Downloads land in data/holding/<source>/<version>/. A connector's checks compare them with the previous
    promoted release (columns, ranges, p-code match, coverage, jumps); anything suspicious stays HELD for
    human review (`python -m aidtrace_pipeline.fetch review` / `approve`).
 4. Downloads are untrusted data: parsed only, never executed. Executable/script file types are refused,
    zip members are read in place and never extracted with their own paths, and document text is never
    treated as instructions.
 5. Keys come from environment variables; per-host rate limiting and retry with backoff; a generic
    User-Agent (no personal details).
 6. Promotion copies into data/raw/<source>/<version>/ and never overwrites an existing version. Model runs
    record the data versions they used (data/processed/model/run_manifest_*.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ..common import log, path

USER_AGENT = "aidtrace-data-fetch/0.1 (humanitarian research; automated, allowlisted)"
REFUSED_SUFFIXES = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".py", ".js", ".vbs", ".msi", ".jar", ".scr", ".com", ".hta"}
MIN_INTERVAL_S = 1.0           # per-host politeness delay
HOLDING = "data/holding"
PROVENANCE = "data/provenance.jsonl"


@lru_cache
def allowlist() -> dict:
    with open(Path(__file__).with_name("allowlist.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


class NotAllowed(Exception):
    pass


# Official download routes that redirect to the provider's own file store. A redirect hop is accepted only
# from the listed provider host into the listed bucket path; direct requests to these hosts are refused.
REDIRECT_TARGETS = {
    "data.humdata.org": [("s3.us-east-1.amazonaws.com", "/hdx-production-filestore/")],   # HDX resource downloads
}


def check_redirect(prev_url: str, url: str) -> bool:
    prev_host = (urlparse(prev_url).hostname or "").lower()
    u = urlparse(url)
    return u.scheme == "https" and any((u.hostname or "").lower() == h and u.path.startswith(prefix)
                                       for h, prefix in REDIRECT_TARGETS.get(prev_host, []))


def check_allowed(source: str, url: str) -> None:
    src = allowlist().get(source)
    if src is None:
        raise NotAllowed(f"source '{source}' is not on the allowlist (queue for human approval)")
    if src["status"] in ("manual",):
        raise NotAllowed(f"source '{source}' is manual-only (documents/restricted): no automatic fetch")
    if src["status"] == "restricted" and not os.environ.get(src.get("key_env", "")):
        raise NotAllowed(f"source '{source}' is restricted and has no credentials/permission configured")
    u = urlparse(url)
    if u.scheme != "https":
        raise NotAllowed(f"only https is allowed: {url}")
    host = (u.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in src["hosts"]):
        raise NotAllowed(f"host '{host}' is not allowlisted for '{source}' ({src['hosts']})")
    if Path(u.path).suffix.lower() in REFUSED_SUFFIXES:
        raise NotAllowed(f"refusing executable/script file type: {u.path}")


def _client():
    import httpx
    import truststore
    truststore.inject_into_ssl()            # this machine inspects HTTPS: use the Windows store, never disable TLS
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=False)


_last_hit: dict[str, float] = {}


def _polite(host: str) -> None:
    dt = time.monotonic() - _last_hit.get(host, 0)
    if dt < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - dt)
    _last_hit[host] = time.monotonic()


def get(source: str, url: str, params: dict | None = None, headers: dict | None = None, retries: int = 4,
        stream_to: Path | None = None):
    """GET with an allowlist check on every hop (redirects are re-checked), politeness and backoff.
    With stream_to, the body is streamed to that file (large rasters) and the response is returned."""
    with _client() as c:
        via_redirect_from = None
        for attempt in range(retries + 5):
            if via_redirect_from and check_redirect(via_redirect_from, url):
                pass                      # provider's own file store, reached from its official download URL
            else:
                check_allowed(source, url)
            _polite(urlparse(url).hostname or "")
            try:
                with c.stream("GET", url, params=params, headers=headers or {}) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        loc = r.headers["location"]
                        via_redirect_from = url
                        url = loc if loc.startswith("http") else f"{urlparse(url).scheme}://{urlparse(url).netloc}{loc}"
                        params = None
                        continue
                    if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                        time.sleep(int(r.headers.get("retry-after", 2 ** attempt * 5)))
                        continue
                    r.raise_for_status()
                    if stream_to is not None:
                        with open(stream_to, "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                f.write(chunk)
                    else:
                        r.read()
                    r.final_url = str(r.url).split("?")[0]           # signed query strings are not logged
                    return r
            except NotAllowed:
                raise
            except Exception:  # noqa: BLE001
                if attempt >= retries - 1:
                    raise
                time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"too many redirects/retries for {url}")


# ------------------------------------------------------------------ holding area and provenance

@dataclass
class Download:
    source: str
    version: str                     # release identifier (e.g. "v26_01_26_09", HDX metadata date)
    url: str
    file: Path                       # in holding
    sha256: str
    size: int
    accessed: str
    licence: str
    meta: dict = field(default_factory=dict)


def holding_dir(source: str, version: str) -> Path:
    d = path(HOLDING) / source / version
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_dir(source: str, version: str) -> Path:
    return path("data/raw") / f"fetched_{source}" / version


def fetch_file(source: str, version: str, url: str, filename: str, params: dict | None = None, **meta) -> Download:
    """Download into holding (skip if this exact version is already promoted or held)."""
    if Path(filename).suffix.lower() in REFUSED_SUFFIXES or "/" in filename or "\\" in filename or ".." in filename:
        raise NotAllowed(f"unsafe filename {filename!r}")
    promoted = raw_dir(source, version) / filename
    held = holding_dir(source, version) / filename
    for existing in (promoted, held):
        if existing.exists():
            return Download(source, version, url, existing, _sha(existing), existing.stat().st_size,
                            "cached", allowlist()[source].get("licence", "UNVERIFIED"), meta)
    tmp = held.with_suffix(held.suffix + ".part")
    r = get(source, url, params=params, stream_to=tmp)
    tmp.replace(held)
    d = Download(source, version, url if r.final_url.startswith(url.split("?")[0]) else f"{url} -> {r.final_url}", held, _sha(held), held.stat().st_size,
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), allowlist()[source].get("licence", "UNVERIFIED"), meta)
    log_event("downloaded", d)
    return d


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log_event(event: str, d: Download, **extra) -> None:
    rec = {"event": event, "source": d.source, "version": d.version, "url": d.url,
           "file": str(d.file.relative_to(path("."))).replace("\\", "/"), "sha256": d.sha256, "size": d.size,
           "accessed": d.accessed, "licence": d.licence, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           **({"meta": d.meta} if d.meta else {}), **extra}
    p = path(PROVENANCE)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def provenance() -> list[dict]:
    p = path(PROVENANCE)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ checks and promotion

@dataclass
class CheckResult:
    ok: bool
    reasons: list[str]
    stats: dict


def promote(d: Download, check: CheckResult) -> Path | None:
    """Promote a held file to data/raw/fetched_<source>/<version>/ if its checks passed. Never overwrites."""
    dest = raw_dir(d.source, d.version) / d.file.name
    if dest.exists():
        return dest
    if not check.ok:
        log_event("held", d, reasons=check.reasons, stats=check.stats)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(d.file, dest)
    log_event("promoted", Download(d.source, d.version, d.url, dest, d.sha256, d.size, d.accessed, d.licence, d.meta),
              stats=check.stats)
    return dest


def approve(source: str, version: str, filename: str, reviewer_note: str) -> Path:
    """Human approval of a HELD file (after review): promote it despite failed checks."""
    held = holding_dir(source, version) / filename
    if not held.exists():
        raise FileNotFoundError(held)
    d = Download(source, version, "(held)", held, _sha(held), held.stat().st_size, "-", allowlist()[source].get("licence", ""))
    res = promote(d, CheckResult(True, [], {"approved_by_human": reviewer_note}))
    return res


def latest_promoted(source: str) -> Path | None:
    base = path("data/raw") / f"fetched_{source}"
    if not base.exists():
        return None
    versions = sorted(p for p in base.iterdir() if p.is_dir())
    return versions[-1] if versions else None


def held_items() -> list[dict]:
    """Held = last event for that file is 'held' and it has not been promoted since."""
    last: dict[tuple, dict] = {}
    for r in provenance():
        if r["event"] in ("held", "promoted"):
            last[(r["source"], r["version"], Path(r["file"]).name)] = r
    return [r for r in last.values() if r["event"] == "held"]

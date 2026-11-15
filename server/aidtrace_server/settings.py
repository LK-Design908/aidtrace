"""Runtime settings. Secrets and locations come from environment variables; nothing sensitive in git.

AIDTRACE_MODE            live | demo                       (default live)
AIDTRACE_DATA_DIR        DB, photos, outbox                (default ~/.aidtrace/<mode>)
AIDTRACE_KEY_DIR         signing and box keys              (default ~/.aidtrace/keys/<mode>)
AIDTRACE_REPO_ROOT       repo root, for pipeline outputs   (default: inferred)
AIDTRACE_COUNTRIES       countries served together         (default DJI,SOM,SDN,SSD,KEN)
AIDTRACE_SESSION_IDLE_MIN / AIDTRACE_SESSION_MAX_HOURS
AIDTRACE_RETENTION_DAYS  photos and raw report content     (default 180)
AIDTRACE_DURESS_WEBHOOK  optional URL owned by the organisation (default: none)
AIDTRACE_SMTP_HOST/PORT/USER/PASSWORD/FROM, AIDTRACE_DURESS_EMAIL   optional email alerts
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache
def static_config() -> dict:
    with open(Path(__file__).with_name("server.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _repo_root() -> Path:
    env = os.environ.get("AIDTRACE_REPO_ROOT")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    mode: str = "live"
    data_dir: Path = field(default_factory=Path)
    key_dir: Path = field(default_factory=Path)
    repo_root: Path = field(default_factory=_repo_root)
    country: str = "SOM"           # primary country (single-country tools, e.g. scripts)
    countries: list = field(default_factory=lambda: ["DJI", "SOM", "SDN", "SSD", "KEN"])   # served together
    session_idle_min: int = 30
    session_max_hours: int = 12
    retention_days: int = 180
    login_max_failures: int = 5
    lockout_minutes: int = 15
    rate_login_per_min: int = 10
    rate_reports_per_min: int = 30
    duress_webhook: str | None = None
    duress_email: str | None = None
    smtp: dict = field(default_factory=dict)
    acled_display: str = "none"
    demo_acled_display: str = "aggregated"   # owner decision 2026-11-15: ACLED district-month counts shown in the demo
    submit_min_ms: int = 800       # every recipient submission takes at least this long (timing-oracle padding)

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / f'aidtrace_{self.mode}.db').as_posix()}"

    @property
    def photo_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.photo_dir, self.outbox_dir, self.key_dir):
            d.mkdir(parents=True, exist_ok=True)
        if self.mode == "demo" and "live" in self.data_dir.name:
            raise RuntimeError("demo mode must never use the live data directory")
        if self.mode == "live" and self.data_dir.name == "demo":
            raise RuntimeError("live mode must never use the demo data directory")


def _pipeline_acled_display(root: Path) -> str:
    p = root / "pipeline" / "aidtrace_pipeline" / "pipeline.yaml"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")).get("acled_display", "none")
    except OSError:
        return "none"


def from_env(**overrides) -> Settings:
    mode = overrides.pop("mode", os.environ.get("AIDTRACE_MODE", "live"))
    if mode not in ("live", "demo"):
        raise ValueError("AIDTRACE_MODE must be live or demo")
    home = Path.home() / ".aidtrace"
    root = _repo_root()
    s = Settings(
        mode=mode,
        data_dir=Path(os.environ.get("AIDTRACE_DATA_DIR", home / mode)),
        key_dir=Path(os.environ.get("AIDTRACE_KEY_DIR", home / "keys" / mode)),
        repo_root=root,
        country=os.environ.get("AIDTRACE_COUNTRY", "SOM").upper(),
        countries=[c.strip().upper() for c in os.environ.get("AIDTRACE_COUNTRIES", "DJI,SOM,SDN,SSD,KEN").split(",") if c.strip()],
        session_idle_min=int(os.environ.get("AIDTRACE_SESSION_IDLE_MIN", 30)),
        session_max_hours=int(os.environ.get("AIDTRACE_SESSION_MAX_HOURS", 12)),
        retention_days=int(os.environ.get("AIDTRACE_RETENTION_DAYS", 180)),
        duress_webhook=os.environ.get("AIDTRACE_DURESS_WEBHOOK") or None,
        duress_email=os.environ.get("AIDTRACE_DURESS_EMAIL") or None,
        smtp={k: os.environ[f"AIDTRACE_SMTP_{k.upper()}"] for k in ("host", "port", "user", "password", "from")
              if f"AIDTRACE_SMTP_{k.upper()}" in os.environ},
        acled_display=_pipeline_acled_display(root),
        demo_acled_display=os.environ.get("AIDTRACE_DEMO_ACLED", "aggregated"),
        submit_min_ms=int(os.environ.get("AIDTRACE_SUBMIT_MIN_MS", 800)),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    if s.acled_display not in ("none", "aggregated", "event_level"):
        raise ValueError("acled_display must be none | aggregated | event_level")
    if s.demo_acled_display not in ("none", "aggregated"):
        raise ValueError("AIDTRACE_DEMO_ACLED must be none | aggregated (the demo never shows event-level data)")
    return s

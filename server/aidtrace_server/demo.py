"""Demo-only routes (mounted ONLY when AIDTRACE_MODE=demo; they do not exist in live mode).

The demo runs as a separate process with its own database, keys and data directory, seeded with
synthetic data only, so real field reports and recipient accounts are unreachable from it (brief §2.4).
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Recipient, User, utcnow
from .security import create_session, ctx, db, sha256_hex

router = APIRouter(prefix="/api/demo")
DEMO_ROLES = {"hq_admin": "demo_hq", "security_focal_point": "demo_security", "field_staff": "demo_field"}
DEMO_PINS = {"normal": "2468", "safety": "1357"}


def _guard(request: Request) -> None:
    if ctx(request).settings.mode != "demo":       # belt and braces: router is not mounted in live mode
        raise HTTPException(404)


class RoleIn(BaseModel):
    role: str


@router.post("/login")
def demo_login(body: RoleIn, request: Request, s: Session = Depends(db)):
    """One-click sign-in as a synthetic demo user (role switcher on the landing page)."""
    _guard(request)
    name = DEMO_ROLES.get(body.role)
    u = s.scalar(select(User).where(User.username == name)) if name else None
    if u is None:
        raise HTTPException(404, "Unknown demo role")
    return {"token": create_session(s, u), "role": u.role, "username": u.username,
            "idle_timeout_min": ctx(request).settings.session_idle_min}


class DeviceIn(BaseModel):
    adm2_pcode: str | None = None


@router.post("/device")
def demo_device(body: DeviceIn, request: Request, s: Session = Depends(db)):
    """Create a fresh synthetic recipient account for a demo phone and return its one-time
    enrolment code and suggested demo PINs. The phone then enrols through the normal flow."""
    _guard(request)
    c = ctx(request)
    pcode = body.adm2_pcode
    if not pcode:
        risk = c.data.risk()
        cw = c.data.crosswalk()
        low = risk[(risk["horizon"] == 1) & (risk["band"] == "low")]["unit_id"].iloc[0]
        pcode = str(cw.loc[cw["unit_id"] == low, "adm2_pcode"].iloc[0])
    unit = c.data.unit_for_pcode(pcode)
    if unit is None:
        raise HTTPException(422, "Unknown p-code")
    code = secrets.token_hex(5).upper()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    acct = "R-" + "".join(secrets.choice(alphabet) for _ in range(6))
    r = Recipient(account=acct, site_name="Demo Health Centre (synthetic)", adm2_pcode=pcode, unit_id=unit,
                  enrolment_code_hash=sha256_hex(code), enrolment_expires=utcnow() + timedelta(hours=72),
                  country=c.data.country_for_pcode(pcode) or c.settings.country)
    s.add(r)
    s.commit()
    return {"account": acct, "enrolment_code": code, "adm2_pcode": pcode, "suggested_pins": DEMO_PINS,
            "note": "Synthetic demo account. In real use, the recipient chooses both PINs privately."}


@router.get("/info")
def demo_info(request: Request):
    _guard(request)
    return {"mode": "demo", "roles": list(DEMO_ROLES), "pins": DEMO_PINS,
            "notice": "All data in this demo is synthetic. No real shipments, recipients or reports."}

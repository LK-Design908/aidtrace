"""HTTP API. All distributor routes require a bearer session; recipient routes use the sealed protocol."""
from __future__ import annotations

import asyncio
import base64
import secrets
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import qr as qrmod
from .jpeg import strip_metadata
from .models import (DuressAlert, FieldReport, Recipient, ScanPoint, Shipment, ShipmentItem, ShipmentRecipient,
                     User, utcnow)
from .notify import notify_duress
from .receipts import process_submission, shipment_view
from .risk import acled_layer, unit_summaries
from .security import (audit, client_ip, create_session, ctx, current_user, db, hash_secret, require_roles,
                       sha256_hex, verify_secret)
from .settings import static_config

CFG = static_config()
SEE_DURESS = CFG["roles_see_duress"]
SEE_TEXT = CFG["roles_see_report_text"]
router = APIRouter(prefix="/api")


# ================================================================== auth

class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, s: Session = Depends(db)):
    c = ctx(request)
    c.limiter.check(f"login:{client_ip(request)}", c.settings.rate_login_per_min)
    u = s.scalar(select(User).where(User.username == body.username))
    now = utcnow()
    if u is not None and u.locked_until and u.locked_until > now:
        verify_secret(None, body.password)          # keep timing uniform
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked")
    ok = verify_secret(u.password_hash if u else None, body.password)
    if not ok or u is None or not u.active:
        if u is not None:
            u.failed_logins += 1
            if u.failed_logins >= c.settings.login_max_failures:
                u.locked_until = now + timedelta(minutes=c.settings.lockout_minutes)
                u.failed_logins = 0
                audit(s, u, "account_locked", "user", u.id)
            s.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    u.failed_logins = 0
    u.locked_until = None
    s.commit()
    token = create_session(s, u)
    audit(s, u, "login", "user", u.id)
    return {"token": token, "role": u.role, "username": u.username,
            "idle_timeout_min": c.settings.session_idle_min}


@router.post("/auth/logout")
def logout(request: Request, user: User = Depends(current_user), s: Session = Depends(db)):
    from .models import Session_
    sess = s.get(Session_, request.state.session_token_hash)
    if sess:
        s.delete(sess)
        s.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "role": user.role}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: str

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        if v not in CFG["roles"]:
            raise ValueError("unknown role")
        return v


@router.post("/users", status_code=201)
def create_user(body: UserIn, s: Session = Depends(db), admin: User = Depends(require_roles(["hq_admin"]))):
    if s.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "Username exists")
    u = User(username=body.username, password_hash=hash_secret(body.password), role=body.role)
    s.add(u)
    s.commit()
    audit(s, admin, "create_user", "user", u.id)
    return {"id": u.id, "username": u.username, "role": u.role}


# ================================================================== catalogue / config

@router.get("/config")
def config(user: User = Depends(current_user)):
    return {"catalogue": CFG["catalogue"], "low_profile_flags": CFG["low_profile_flags"],
            "scan_point_kinds": CFG["scan_point_kinds"], "field_report_types": CFG["field_report_types"],
            "disclaimer": CFG["disclaimer"], "role": user.role}


# ================================================================== recipients (admin side)

class RecipientIn(BaseModel):
    site_name: str = Field(max_length=200)
    adm2_pcode: str = Field(max_length=16)
    facility_id: str | None = Field(default=None, max_length=64)


def _account_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "R-" + "".join(secrets.choice(alphabet) for _ in range(6))


@router.post("/recipients", status_code=201)
def create_recipient(body: RecipientIn, request: Request, s: Session = Depends(db),
                     admin: User = Depends(require_roles(["hq_admin"]))):
    unit = ctx(request).data.unit_for_pcode(body.adm2_pcode)
    if unit is None:
        raise HTTPException(422, "Unknown admin-2 p-code")
    code = secrets.token_hex(5).upper()       # one-time enrolment code, shown once
    r = Recipient(account=_account_code(), site_name=body.site_name, adm2_pcode=body.adm2_pcode, unit_id=unit,
                  facility_id=body.facility_id, enrolment_code_hash=sha256_hex(code),
                  enrolment_expires=utcnow() + timedelta(hours=72), created_by=admin.id,
                  country=ctx(request).data.country_for_pcode(body.adm2_pcode) or ctx(request).settings.country)
    s.add(r)
    s.commit()
    audit(s, admin, "create_recipient", "recipient", r.id)
    return {"account": r.account, "enrolment_code": code, "expires": r.enrolment_expires}


@router.get("/recipients")
def list_recipients(s: Session = Depends(db), user: User = Depends(current_user)):
    rows = s.scalars(select(Recipient).order_by(Recipient.id)).all()
    return [{"account": r.account, "site_name": r.site_name, "adm2_pcode": r.adm2_pcode, "unit_id": r.unit_id,
             "enrolled": r.enrolled_at is not None, "active": r.active} for r in rows]


# ================================================================== recipient device side

@router.get("/recipient/keys")
def recipient_keys(request: Request):
    """Public keys only: organisation signing keys (verify QR offline) and the server box key."""
    return ctx(request).keys.public_info()


class EnrolIn(BaseModel):
    account: str = Field(max_length=16)
    enrolment_code: str = Field(max_length=32)
    sealed_pins: str = Field(max_length=4096)    # base64 sealed box of {"normal": "...", "duress": "..."}


@router.post("/recipient/enrol")
def enrol(body: EnrolIn, request: Request, s: Session = Depends(db)):
    """One-time online enrolment during vetting. PINs are sealed to the server key and stored only
    as argon2 hashes. The device keeps no PIN material."""
    import json as _json
    c = ctx(request)
    c.limiter.check(f"enrol:{client_ip(request)}", 10)
    r = s.scalar(select(Recipient).where(Recipient.account == body.account))
    if (r is None or r.enrolment_code_hash is None or r.enrolment_expires is None or r.enrolment_expires < utcnow()
            or r.enrolment_code_hash != sha256_hex(body.enrolment_code.strip().upper())):
        raise HTTPException(403, "Enrolment not possible")
    plain = c.keys.open_sealed(base64.b64decode(body.sealed_pins))
    try:
        pins = _json.loads(plain.decode()) if plain else None
        normal, duress = str(pins["normal"]), str(pins["duress"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid PIN payload")
    for p in (normal, duress):
        if not (p.isdigit() and 4 <= len(p) <= 8):
            raise HTTPException(422, "PINs must be 4-8 digits")
    if normal == duress:
        raise HTTPException(422, "Normal and duress PINs must differ")
    token = secrets.token_urlsafe(32)
    r.normal_pin_hash, r.duress_pin_hash = hash_secret(normal), hash_secret(duress)
    r.device_token_hash = sha256_hex(token)
    r.enrolment_code_hash = None
    r.enrolled_at = utcnow()
    s.commit()
    pk = c.keys.public_info()
    if r.id > 0xFFFF:
        raise HTTPException(500, "SMS id space exhausted")
    return {"account": r.account, "device_token": token, "site_name": r.site_name, **pk,
            "sms_id": r.id, "sms_key": base64.b64encode(c.keys.sms_key(r.id)).decode(), "sms_number": CFG["sms"]["number"],
            "pseah_contact": CFG["pseah_contacts"].get(r.country), "problem_types": CFG["recipient_problem_types"],
            "catalogue": [{"code": i["code"], "label": i["label"], "cold_chain": i.get("cold_chain", False)}
                          for i in CFG["catalogue"]]}


RECEIVED = {"status": "received", "message": "Receipt recorded"}


@router.post("/recipient/submit", status_code=202)
async def submit(request: Request, background: BackgroundTasks, s: Session = Depends(db)):
    """Accepts a sealed submission. The response is identical whatever the content (normal, duress,
    wrong PIN, replay, malformed), so neither the device nor an observer learns the outcome."""
    c = ctx(request)
    t0 = time.monotonic()
    c.limiter.check(f"submit:{client_ip(request)}", c.settings.rate_reports_per_min)
    blob = await request.body()
    if 0 < len(blob) <= 200_000:
        out = process_submission(c, s, blob)
        if out.alert_id:
            background.add_task(notify_duress, c, out.alert_id)
    await _pad_time(c, t0)
    return RECEIVED


async def _pad_time(c, t0: float) -> None:
    """Pad every recipient submission to a fixed minimum duration, so response time does not reveal
    whether the PIN was normal, duress or wrong (duress adds a DB write; failures return early)."""
    target = c.settings.submit_min_ms / 1000
    # Loop: on Windows asyncio.sleep can wake ~15 ms early (timer granularity), so one sleep can undershoot.
    while (remaining := target - (time.monotonic() - t0)) > 0:
        await asyncio.sleep(max(remaining, 0.001))


class SmsIn(BaseModel):
    sender: str = Field(max_length=32)
    text: str = Field(max_length=160)


@router.post("/sms/inbound")
async def sms_inbound(body: SmsIn, request: Request, background: BackgroundTasks, s: Session = Depends(db)):
    """Inbound SMS webhook (mock gateway in the beta). A real gateway must send the shared secret in
    X-Gateway-Secret (AIDTRACE_SMS_GATEWAY_SECRET). Without a configured secret this route only works
    in demo mode. The reply is identical for every message."""
    import hmac as _hmac
    import os as _os
    c = ctx(request)
    t0 = time.monotonic()
    secret = _os.environ.get("AIDTRACE_SMS_GATEWAY_SECRET")
    if secret:
        if not _hmac.compare_digest(request.headers.get("x-gateway-secret", ""), secret):
            raise HTTPException(403, "Forbidden")
    elif c.settings.mode != "demo":
        raise HTTPException(403, "SMS gateway not configured")
    c.limiter.check(f"sms:{body.sender}", c.settings.rate_reports_per_min)
    from .receipts import process_sms
    out = process_sms(c, s, body.text)
    if out.alert_id:
        background.add_task(notify_duress, c, out.alert_id)
    await _pad_time(c, t0)
    return {"status": "ok", "reply": "Receipt recorded"}


# ================================================================== shipments

class ItemIn(BaseModel):
    item_code: int
    quantity: int = Field(gt=0, lt=10_000_000)
    description: str | None = Field(default=None, max_length=200)
    batch: str | None = Field(default=None, max_length=64)
    expiry: date | None = None


class ShipmentIn(BaseModel):
    origin: str = Field(max_length=200)
    dest_adm2_pcode: str = Field(max_length=16)
    dest_site: str = Field(max_length=200)
    window_start: date
    window_end: date
    cold_chain: bool = False
    items: list[ItemIn] = Field(min_length=1, max_length=40)
    recipients: list[str] = Field(min_length=1, max_length=10)
    scan_points: list[str] = Field(min_length=1, max_length=3)
    low_profile: list[str] = []
    two_person_override: bool | None = None

    @field_validator("scan_points")
    @classmethod
    def _sp(cls, v):
        bad = [k for k in v if k not in CFG["scan_point_kinds"]]
        if bad:
            raise ValueError(f"unknown scan point kinds {bad}")
        return v

    @field_validator("low_profile")
    @classmethod
    def _lp(cls, v):
        bad = [k for k in v if k not in CFG["low_profile_flags"]]
        if bad:
            raise ValueError(f"unknown low-profile flags {bad}")
        return v


def _can_see_duress(u: User) -> bool:
    return u.role in SEE_DURESS


@router.post("/shipments", status_code=201)
def create_shipment(body: ShipmentIn, request: Request, s: Session = Depends(db),
                    user: User = Depends(require_roles(["hq_admin", "field_staff"]))):
    c = ctx(request)
    codes = {i["code"] for i in CFG["catalogue"]}
    if any(i.item_code not in codes for i in body.items):
        raise HTTPException(422, "Unknown item code")
    if body.window_end < body.window_start:
        raise HTTPException(422, "Window end before start")
    unit = c.data.unit_for_pcode(body.dest_adm2_pcode)
    if unit is None:
        raise HTTPException(422, "Unknown destination p-code")
    band = c.data.band_for(unit)
    high_risk = band in CFG["two_person_bands"]
    two_person = high_risk if body.two_person_override is None else (body.two_person_override or high_risk)
    if high_risk and len(body.scan_points) < CFG["min_scan_points_high_risk"]:
        raise HTTPException(422, f"High-risk destination ({band}): at least {CFG['min_scan_points_high_risk']} scan points required")
    recs = s.scalars(select(Recipient).where(Recipient.account.in_(body.recipients))).all()
    if len(recs) != len(set(body.recipients)):
        raise HTTPException(422, "Unknown recipient account")
    if two_person and len(recs) < 2:
        raise HTTPException(422, "Two-person confirmation requires at least two recipient accounts")
    sh = Shipment(code="SH-" + secrets.token_hex(4).upper(), origin=body.origin, dest_adm2_pcode=body.dest_adm2_pcode,
                  dest_unit_id=unit, dest_site=body.dest_site,
                  window_start=datetime.combine(body.window_start, datetime.min.time()),
                  window_end=datetime.combine(body.window_end, datetime.max.time()).replace(microsecond=0),
                  cold_chain=body.cold_chain or any(i.item_code == 5 for i in body.items), low_profile=body.low_profile,
                  two_person_required=two_person, risk_band_at_creation=band, created_by=user.id)
    for i in body.items:
        sh.items.append(ShipmentItem(item_code=i.item_code, quantity=i.quantity, description=i.description, batch=i.batch,
                                     expiry=datetime.combine(i.expiry, datetime.min.time()) if i.expiry else None))
    exp = sh.window_end + timedelta(days=CFG["receipt"]["qr_valid_days"])
    for k, kind in enumerate(body.scan_points, start=1):
        sh.scan_points.append(ScanPoint(seq=k, kind=kind, qr_expires=exp))
    for r in recs:
        sh.recipients.append(ShipmentRecipient(recipient_id=r.id))
    s.add(sh)
    s.commit()
    audit(s, user, "create_shipment", "shipment", sh.id)
    return shipment_view(sh, _can_see_duress(user))


@router.get("/shipments")
def list_shipments(s: Session = Depends(db), user: User = Depends(current_user)):
    rows = s.scalars(select(Shipment).order_by(Shipment.id.desc())).all()
    return [shipment_view(sh, _can_see_duress(user)) for sh in rows]


@router.get("/shipments/{sid}")
def get_shipment(sid: int, s: Session = Depends(db), user: User = Depends(current_user)):
    sh = s.get(Shipment, sid)
    if sh is None:
        raise HTTPException(404)
    if _can_see_duress(user):
        audit(s, user, "view_shipment_with_duress_status", "shipment", sid)
    return shipment_view(sh, _can_see_duress(user))


def _manifest(sh: Shipment) -> list[tuple[int, int]]:
    agg: dict[int, int] = {}
    for it in sh.items:
        agg[it.item_code] = agg.get(it.item_code, 0) + it.quantity
    return sorted(agg.items())


@router.get("/shipments/{sid}/qr")
def shipment_qr(sid: int, request: Request, s: Session = Depends(db), user: User = Depends(current_user)):
    sh = s.get(Shipment, sid)
    if sh is None:
        raise HTTPException(404)
    keys = ctx(request).keys
    return [{"scan_point_id": sp.id, "seq": sp.seq, "kind": sp.kind,
             "qr": (q := qrmod.encode(qrmod.QRPayload(sh.id, sp.id, sp.kind, sp.qr_expires, sh.dest_adm2_pcode,
                                                      _manifest(sh)), keys)),
             "qr_version": qrmod.qr_version(q)} for sp in sh.scan_points]


@router.get("/shipments/{sid}/qr/{seq}.svg")
def shipment_qr_svg(sid: int, seq: int, request: Request, s: Session = Depends(db), user: User = Depends(current_user)):
    import io as _io
    import segno
    sh = s.get(Shipment, sid)
    sp = next((p for p in sh.scan_points if p.seq == seq), None) if sh else None
    if sp is None:
        raise HTTPException(404)
    code = qrmod.encode(qrmod.QRPayload(sh.id, sp.id, sp.kind, sp.qr_expires, sh.dest_adm2_pcode, _manifest(sh)),
                        ctx(request).keys)
    buf = _io.BytesIO()
    segno.make(code, error="m", micro=False).save(buf, kind="svg", scale=6, border=2, dark="#0b0b0b", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml")


@router.get("/shipments/{sid}/labels.pdf")
def shipment_labels(sid: int, request: Request, s: Session = Depends(db), user: User = Depends(current_user)):
    sh = s.get(Shipment, sid)
    if sh is None:
        raise HTTPException(404)
    keys = ctx(request).keys
    cat = {c["code"]: c["label"] for c in CFG["catalogue"]}
    entries = [{"qr": qrmod.encode(qrmod.QRPayload(sh.id, sp.id, sp.kind, sp.qr_expires, sh.dest_adm2_pcode,
                                                    _manifest(sh)), keys),
                "shipment_code": sh.code, "scan_kind": sp.kind, "seq": sp.seq, "dest": sh.dest_adm2_pcode,
                "site": sh.dest_site, "window": f"{sh.window_start:%Y-%m-%d} to {sh.window_end:%Y-%m-%d}",
                "manifest": [(cat.get(c, "?"), q) for c, q in _manifest(sh)]} for sp in sh.scan_points]
    audit(s, user, "print_labels", "shipment", sid)
    return Response(qrmod.label_pdf(entries), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{sh.code}_labels.pdf"'})


# ================================================================== duress alerts

@router.get("/alerts")
def list_alerts(s: Session = Depends(db), user: User = Depends(require_roles(SEE_DURESS))):
    audit(s, user, "view_duress_alerts")
    rows = s.scalars(select(DuressAlert).order_by(DuressAlert.id.desc())).all()
    out = []
    for a in rows:
        sp = s.get(ScanPoint, a.receipt.scan_point_id)
        sh = s.get(Shipment, sp.shipment_id)
        out.append({"id": a.id, "created_at": a.created_at, "shipment": sh.code, "shipment_id": sh.id,
                    "scan_point": sp.kind, "site": sh.dest_site, "district": sh.dest_adm2_pcode,
                    "recipient": a.receipt.recipient.account, "acknowledged_at": a.acknowledged_at,
                    "notified": a.notified})
    return out


@router.post("/alerts/{aid}/ack")
def ack_alert(aid: int, s: Session = Depends(db), user: User = Depends(require_roles(SEE_DURESS))):
    a = s.get(DuressAlert, aid)
    if a is None:
        raise HTTPException(404)
    a.acknowledged_by, a.acknowledged_at = user.id, utcnow()
    s.commit()
    audit(s, user, "ack_duress_alert", "duress_alert", aid)
    return {"ok": True}


# ================================================================== field reports

class ReportIn(BaseModel):
    type: str
    incident_date: date
    adm2_pcode: str = Field(max_length=16)
    free_text: str | None = Field(default=None, max_length=4000)
    severity: int = Field(ge=1, le=5)
    shipment_id: int | None = None
    attachment_b64: str | None = Field(default=None, max_length=2_000_000)

    @field_validator("type")
    @classmethod
    def _t(cls, v):
        if v not in CFG["field_report_types"]:
            raise ValueError("unknown report type")
        return v


def _report_view(r: FieldReport, user: User) -> dict:
    v = {"id": r.id, "type": r.type, "incident_date": r.incident_date, "adm2_pcode": r.adm2_pcode,
         "unit_id": r.unit_id, "severity": r.severity, "reporter_role": r.reporter_role,
         "review_status": r.review_status, "shipment_id": r.shipment_id, "created_at": r.created_at,
         "has_text": bool(r.free_text), "has_attachment": bool(r.attachment_path)}
    if user.role in SEE_TEXT or r.reporter_user_id == user.id:
        v["free_text"] = r.free_text
    return v


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, request: Request, s: Session = Depends(db), user: User = Depends(current_user)):
    c = ctx(request)
    c.limiter.check(f"report:{user.id}", c.settings.rate_reports_per_min)
    unit = c.data.unit_for_pcode(body.adm2_pcode)
    if unit is None:
        raise HTTPException(422, "Unknown admin-2 p-code")
    att = None
    if body.attachment_b64:
        raw = base64.b64decode(body.attachment_b64)
        if raw[:2] == b"\xff\xd8":
            raw = strip_metadata(raw)
        att_path = c.settings.photo_dir / f"report_{secrets.token_hex(8)}.bin"
        att_path.write_bytes(raw)
        att = att_path.name
    r = FieldReport(type=body.type, incident_date=datetime.combine(body.incident_date, datetime.min.time()),
                    adm2_pcode=body.adm2_pcode, unit_id=unit, free_text=body.free_text, severity=body.severity,
                    reporter_user_id=user.id, reporter_role=user.role, attachment_path=att, shipment_id=body.shipment_id)
    s.add(r)
    s.commit()
    return _report_view(r, user)


@router.get("/reports")
def list_reports(unit_id: str | None = None, s: Session = Depends(db), user: User = Depends(current_user)):
    q = select(FieldReport).order_by(FieldReport.incident_date.desc())
    if unit_id:
        q = q.where(FieldReport.unit_id == unit_id)
    rows = s.scalars(q).all()
    if user.role in SEE_TEXT and any(r.free_text for r in rows):
        audit(s, user, "view_field_report_text", "field_report_list", unit_id or "all")
    return [_report_view(r, user) for r in rows]


@router.get("/reports/{rid}")
def get_report(rid: int, s: Session = Depends(db), user: User = Depends(current_user)):
    r = s.get(FieldReport, rid)
    if r is None:
        raise HTTPException(404)
    v = _report_view(r, user)
    if "free_text" in v:
        audit(s, user, "view_field_report_detail", "field_report", rid)
    return v


class ReviewIn(BaseModel):
    review_status: str

    @field_validator("review_status")
    @classmethod
    def _s(cls, v):
        if v not in ("unverified", "verified", "rejected"):
            raise ValueError("bad status")
        return v


@router.patch("/reports/{rid}/review")
def review_report(rid: int, body: ReviewIn, s: Session = Depends(db), user: User = Depends(require_roles(SEE_TEXT))):
    r = s.get(FieldReport, rid)
    if r is None:
        raise HTTPException(404)
    r.review_status = body.review_status
    s.commit()
    audit(s, user, f"review_report_{body.review_status}", "field_report", rid)
    return _report_view(r, user)


# ================================================================== risk / map

@router.get("/units.geojson")
def units(request: Request, user: User = Depends(current_user)):
    return ctx(request).data.units_geojson()


@router.get("/risk")
def risk(request: Request, horizon: int = 1, s: Session = Depends(db), user: User = Depends(current_user)):
    if horizon not in (1, 3):
        raise HTTPException(422, "horizon must be 1 or 3")
    d = ctx(request).data
    return {"horizon": horizon, "disclaimer": CFG["disclaimer"], "resolution": "admin-2",
            "need_weights": d.need_meta().get("weights"), "trigger_status": d.need_meta().get("trigger_status"),
            "units": unit_summaries(d, s, horizon)}


@router.get("/risk/{unit_id}")
def risk_unit(unit_id: str, request: Request, s: Session = Depends(db), user: User = Depends(current_user)):
    d = ctx(request).data
    units_ = {u["unit_id"]: u for u in unit_summaries(d, s, 1)}
    if unit_id not in units_:
        raise HTTPException(404)
    u = units_[unit_id]
    u3 = {x["unit_id"]: x for x in unit_summaries(d, s, 3)}[unit_id]
    u["forecast_3m"] = {k: u3.get(k) for k in ("prob", "prob_lo", "prob_hi", "band", "target_month")}
    n = d.need()
    if n is not None:
        t = n[n["unit_id"] == unit_id].sort_values("month").tail(24)
        u["need_trend"] = [{"month": f"{m:%Y-%m}", "need_index": None if v != v else float(v)}
                           for m, v in zip(t["month"], t["need_index"])]
    u["recent_reports"] = [_report_view(r, user) for r in s.scalars(
        select(FieldReport).where(FieldReport.unit_id == unit_id).order_by(FieldReport.incident_date.desc()).limit(10))]
    u["recent_deliveries"] = [{k: v for k, v in shipment_view(sh, _can_see_duress(user)).items()
                               if k in ("code", "status", "flags", "window_end", "dest_site")}
                              for sh in s.scalars(select(Shipment).where(Shipment.dest_unit_id == unit_id)
                                                  .order_by(Shipment.id.desc()).limit(10))]
    u["disclaimer"] = CFG["disclaimer"]
    return u


@router.get("/acled")
def acled(request: Request, unit_id: str | None = None, user: User = Depends(current_user)):
    out = acled_layer(ctx(request).settings, user.role, unit_id)
    if out is None:
        raise HTTPException(403, "ACLED display is not enabled for this role")
    return out

"""Receipt processing and status derivation.

The only place that reads Receipt.pin_class. `scan_status(..., can_see_duress)` returns the
general view when can_see_duress is False, and there a duress receipt is indistinguishable from a
normal one.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import qr as qrmod
from .jpeg import strip_metadata
from .models import (DuressAlert, FieldReport, Receipt, Recipient, ScanPoint, SecurityEvent, Shipment,
                     ShipmentRecipient, UsedNonce, utcnow)
from .sealed import parse
from .security import sha256_hex, tokens_equal, verify_secret
from .settings import static_config

log = logging.getLogger("aidtrace.receipts")
CONFIRMING = ("normal", "duress")


@dataclass
class Outcome:
    kind: str                 # receipt | problem | rejected
    receipt_id: int | None = None
    alert_id: int | None = None
    reason: str = ""


def _reject(s: Session, kind: str, detail: str) -> Outcome:
    s.add(SecurityEvent(kind=kind, detail=detail[:300]))
    s.commit()
    return Outcome("rejected", reason=kind)


def classify_pin(rec: Recipient, pin: str) -> str:
    """Always runs both argon2 verifications so timing does not depend on which PIN matched."""
    pin = pin.strip()
    ok_normal = verify_secret(rec.normal_pin_hash, pin)
    ok_duress = verify_secret(rec.duress_pin_hash, pin)
    if ok_duress:
        return "duress"
    if ok_normal:
        return "normal"
    return "mismatch"


def process_submission(ctx, s: Session, blob: bytes) -> Outcome:
    plain = ctx.keys.open_sealed(blob)
    if plain is None:
        return _reject(s, "undecryptable", f"{len(blob)} bytes")
    try:
        d = parse(plain)
    except (ValueError, UnicodeDecodeError) as e:
        return _reject(s, "malformed", str(e))

    nonce = str(d.get("nonce", ""))
    if len(nonce) != 32 or s.get(UsedNonce, nonce):
        return _reject(s, "replay", f"nonce {nonce[:8]}...")
    s.add(UsedNonce(nonce=nonce))
    s.commit()

    rec = s.scalar(select(Recipient).where(Recipient.account == d.get("account")))
    if rec is None or not rec.active or rec.device_token_hash is None or \
            not tokens_equal(rec.device_token_hash, sha256_hex(str(d.get("device_token", "")))):
        return _reject(s, "bad_device", f"account {str(d.get('account'))[:16]}")

    try:
        ts = datetime.strptime(d["ts"], "%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, ValueError):
        return _reject(s, "malformed", "timestamp")
    max_age = static_config()["receipt"]["max_age_days"]
    now = utcnow()
    if ts > now + timedelta(hours=1) or now - ts > timedelta(days=max_age):
        return _reject(s, "stale_timestamp", d["ts"])

    if d["kind"] == "problem":
        return _problem(ctx, s, rec, d, ts)
    return _receipt(ctx, s, rec, d, ts)


def process_sms(ctx, s: Session, text: str) -> Outcome:
    """Decode an SMS receipt code. Same classification and same (silent) outcome as data receipts."""
    from . import sms as smsmod
    sid = smsmod.peek_sms_id(text)
    rec = s.get(Recipient, sid) if sid else None
    if rec is None or not rec.active or rec.device_token_hash is None:
        return _reject(s, "sms_bad_code", "unknown sender id")
    r = smsmod.decode(ctx.keys.sms_key(rec.id), text)
    if r is None:
        return _reject(s, "sms_bad_code", f"tag/format failure for {rec.account}")
    nonce = hashlib_sha(text.strip().upper())
    if s.get(UsedNonce, nonce):
        return _reject(s, "replay", f"sms {rec.account}")
    s.add(UsedNonce(nonce=nonce))
    s.commit()
    now = utcnow()
    if r.device_ts > now + timedelta(hours=1) or now - r.device_ts > timedelta(days=static_config()["receipt"]["max_age_days"]):
        return _reject(s, "stale_timestamp", f"sms {rec.account}")
    sp = s.get(ScanPoint, r.scan_point_id)
    if sp is None or sp.shipment_id != r.shipment_id or sp.qr_expires < r.device_ts:
        return _reject(s, "bad_qr", f"sms {rec.account}")
    if s.scalar(select(ShipmentRecipient).where(ShipmentRecipient.shipment_id == sp.shipment_id,
                                                ShipmentRecipient.recipient_id == rec.id)) is None:
        return _reject(s, "not_assigned", f"sms {rec.account}")
    pin_class = classify_pin(rec, r.pin)
    rc = Receipt(scan_point_id=sp.id, recipient_id=rec.id, device_ts=r.device_ts, channel="sms", pin_class=pin_class,
                 quantities=[{"code": 0, "qty_received": r.total_qty, "damaged": r.flags & 1, "expired": 0, "summary": True}],
                 cold_chain={"indicator_triggered": True} if r.flags & 2 else None)
    s.add(rc)
    s.commit()
    alert_id = _duress_alert(s, rc) if pin_class == "duress" else None
    maybe_draw_verification(s, s.get(Shipment, sp.shipment_id))
    return Outcome("receipt", receipt_id=rc.id, alert_id=alert_id)


def hashlib_sha(t: str) -> str:
    import hashlib
    return "sms:" + hashlib.sha256(t.encode()).hexdigest()[:56]


def _receipt(ctx, s: Session, rec: Recipient, d: dict, ts: datetime) -> Outcome:
    r = d.get("receipt") or {}
    payload = qrmod.verify(str(r.get("qr", "")), ctx.keys, at=ts)
    if payload is None:
        return _reject(s, "bad_qr", f"account {rec.account}")
    sp = s.get(ScanPoint, payload.scan_point_id)
    if sp is None or sp.shipment_id != payload.shipment_id:
        return _reject(s, "bad_qr", "unknown scan point")
    assigned = s.scalar(select(ShipmentRecipient).where(ShipmentRecipient.shipment_id == sp.shipment_id,
                                                        ShipmentRecipient.recipient_id == rec.id))
    if assigned is None:
        return _reject(s, "not_assigned", f"{rec.account} -> shipment {sp.shipment_id}")

    pin_class = classify_pin(rec, str(r.get("pin", "")))
    photo_path = None
    if r.get("photo"):
        try:
            jpg = strip_metadata(base64.b64decode(r["photo"], validate=True))
            p = ctx.settings.photo_dir / f"r{sp.id}_{rec.id}_{int(now_ts())}.jpg"
            p.write_bytes(jpg)
            photo_path = p.name
        except (ValueError, base64.binascii.Error):
            log.warning("discarded invalid photo")
    items = [{"code": int(i.get("code", 0)), "qty_received": max(0, int(i.get("qty_received", 0))),
              "damaged": max(0, int(i.get("damaged", 0))), "expired": max(0, int(i.get("expired", 0)))}
             for i in (r.get("items") or [])][:50]
    rc = Receipt(scan_point_id=sp.id, recipient_id=rec.id, device_ts=ts, pin_class=pin_class, quantities=items,
                 cold_chain=r.get("cold_chain"), notes=(r.get("notes") or "")[:500] or None, photo_path=photo_path)
    s.add(rc)
    s.commit()
    alert_id = _duress_alert(s, rc) if pin_class == "duress" else None
    maybe_draw_verification(s, s.get(Shipment, sp.shipment_id))
    return Outcome("receipt", receipt_id=rc.id, alert_id=alert_id)


def _duress_alert(s: Session, rc: Receipt) -> int | None:
    """One alert per recipient and scan point: an SMS code followed by the data receipt (photo)
    for the same handover must not raise two alerts."""
    prior = s.scalar(select(DuressAlert).join(Receipt, DuressAlert.receipt_id == Receipt.id).where(
        Receipt.scan_point_id == rc.scan_point_id, Receipt.recipient_id == rc.recipient_id, Receipt.id != rc.id))
    if prior is not None:
        return None
    a = DuressAlert(receipt_id=rc.id)
    s.add(a)
    s.commit()
    return a.id


def maybe_draw_verification(s: Session, sh: Shipment) -> None:
    """When a shipment's final scan point first becomes verified, draw once (CSPRNG) whether it
    goes to the independent verification queue. Sites cannot predict which deliveries are checked."""
    import secrets as _secrets
    from .models import VerificationDraw, VerificationTask
    s.refresh(sh)
    if not sh.scan_points or s.get(VerificationDraw, sh.id):
        return
    if scan_status(sh, sh.scan_points[-1], can_see_duress=False)["status"] != "verified":
        return
    cfg = static_config()
    rate = cfg["verification"]["high_risk_rate"] if sh.risk_band_at_creation in cfg["two_person_bands"] \
        else cfg["verification"]["base_rate"]
    s.add(VerificationDraw(shipment_id=sh.id))
    if _secrets.randbelow(1_000_000) < int(rate * 1_000_000):
        s.add(VerificationTask(shipment_id=sh.id, selection_rate=rate))
    s.commit()


def _problem(ctx, s: Session, rec: Recipient, d: dict, ts: datetime) -> Outcome:
    p = d.get("problem") or {}
    cat = p.get("category")
    if cat not in static_config()["recipient_problem_types"]:
        return _reject(s, "malformed", "problem category")
    type_map = {"shortage": "other", "theft": "theft_in_transit", "payment_demand": "extortion_at_handover",
                "access_blocked": "access_denied", "other": "other"}
    shipment_id = None
    if p.get("qr"):
        q = qrmod.verify(str(p["qr"]), ctx.keys, at=ts)
        shipment_id = q.shipment_id if q else None
    fr = FieldReport(type=type_map[cat], incident_date=ts, adm2_pcode=rec.adm2_pcode, unit_id=rec.unit_id,
                     free_text=f"[recipient: {cat}] {(p.get('notes') or '')[:280]}".strip(), severity=3,
                     reporter_recipient_id=rec.id, reporter_role="recipient", shipment_id=shipment_id)
    s.add(fr)
    s.commit()
    return Outcome("problem")


def now_ts() -> float:
    return datetime.now().timestamp()


# ------------------------------------------------------------------ status derivation

def scan_status(sh: Shipment, sp: ScanPoint, can_see_duress: bool) -> dict:
    required = 2 if sh.two_person_required else 1
    confirming = [r for r in sp.receipts if r.pin_class in CONFIRMING]
    confirmers = {r.recipient_id for r in confirming}
    mismatches = [r for r in sp.receipts if r.pin_class == "mismatch"]
    if len(confirmers) >= required:
        status = "verified"
    elif confirmers:
        status = "partially_verified"
    elif mismatches:
        status = "unverified_pin_mismatch"
    else:
        status = "pending"
    out = {
        "scan_point_id": sp.id, "seq": sp.seq, "kind": sp.kind, "status": status,
        "required_confirmations": required, "confirmations": len(confirmers),
        "pin_mismatches": len(mismatches),
        "first_receipt_at": min((r.server_received_at for r in confirming), default=None),
        "receipts": [_receipt_view(r, can_see_duress) for r in sp.receipts],
    }
    if can_see_duress:
        out["duress"] = any(r.pin_class == "duress" for r in sp.receipts)
    return out


def _receipt_view(r: Receipt, can_see_duress: bool) -> dict:
    v = {"receipt_id": r.id, "recipient": r.recipient.account, "device_ts": r.device_ts,
         "received_at": r.server_received_at, "channel": r.channel,
         "result": "confirmed" if r.pin_class in CONFIRMING else "unverified_pin_mismatch",
         "quantities": r.quantities, "cold_chain": r.cold_chain, "notes": r.notes,
         "photo": r.photo_path is not None, "content_purged": r.content_purged}
    if can_see_duress:
        v["pin_class"] = r.pin_class
    return v


def shipment_view(sh: Shipment, can_see_duress: bool) -> dict:
    cfg = static_config()
    cat = {c["code"]: c for c in cfg["catalogue"]}
    scans = [scan_status(sh, sp, can_see_duress) for sp in sh.scan_points]
    dispatched = {}
    for it in sh.items:
        dispatched[it.item_code] = dispatched.get(it.item_code, 0) + it.quantity
    flags = []
    now = utcnow()
    last = scans[-1] if scans else None
    if last and last["status"] != "verified" and now > sh.window_end:
        flags.append("overdue")
    if any(s["pin_mismatches"] for s in scans):
        flags.append("pin_failures")
    ow = timedelta(days=cfg["flags"]["outside_window_days"])
    received = {}
    for sp in sh.scan_points:
        for r in sp.receipts:
            if r.pin_class not in CONFIRMING:
                continue
            if r.device_ts < sh.window_start - ow or r.device_ts > sh.window_end + ow:
                flags.append("receipt_outside_window")
            cc = r.cold_chain or {}
            if cc.get("indicator_triggered") or (cc.get("vvm_stage") or 0) >= cfg["flags"]["vvm_fail_stage"]:
                flags.append("cold_chain_failure")
            if sp.id == sh.scan_points[-1].id:
                for q in r.quantities:
                    if q.get("summary"):        # SMS summary line: total only, no per-item check
                        continue
                    received[q["code"]] = max(received.get(q["code"], 0), q["qty_received"] - q.get("damaged", 0) - q.get("expired", 0))
    if received and any(received.get(c, 0) < q for c, q in dispatched.items()):
        flags.append("quantity_shortfall")
    ne = timedelta(days=cfg["flags"]["near_expiry_days"])
    if any(it.expiry and it.expiry < sh.window_end + ne for it in sh.items):
        flags.append("near_expiry")
    overall = last["status"] if last else "pending"
    if last and last["status"] == "verified":
        overall = "delivered"
    elif any(s["status"] != "pending" for s in scans):
        overall = "in_transit" if overall == "pending" else overall
    v = {
        "id": sh.id, "code": sh.code, "status": overall, "origin": sh.origin, "dest_adm2_pcode": sh.dest_adm2_pcode,
        "dest_unit_id": sh.dest_unit_id, "dest_site": sh.dest_site, "window_start": sh.window_start,
        "window_end": sh.window_end, "cold_chain": sh.cold_chain, "low_profile": sh.low_profile,
        "two_person_required": sh.two_person_required, "risk_band_at_creation": sh.risk_band_at_creation,
        "items": [{"item_code": it.item_code, "item": cat.get(it.item_code, {}).get("label", "?"),
                   "description": it.description, "quantity": it.quantity, "batch": it.batch, "expiry": it.expiry}
                  for it in sh.items],
        "received_final": [{"item_code": c, "dispatched": q, "confirmed": received.get(c)} for c, q in dispatched.items()],
        "recipients": [sr.recipient.account for sr in sh.recipients],
        "scan_points": scans, "flags": sorted(set(flags)), "created_at": sh.created_at,
    }
    if can_see_duress:
        v["duress"] = any(sc.get("duress") for sc in scans)
    return v

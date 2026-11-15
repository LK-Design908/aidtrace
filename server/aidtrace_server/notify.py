"""Duress notifications to the organisation's security focal point.

Channels: always in-app (DuressAlert row) and an outbox file. Optionally a webhook and/or email
owned by the organisation. Never sent to local authorities. Runs after the HTTP response has been
sent (background task), so it cannot affect what the device sees.
Messages carry shipment code, site, district and time only: no PINs and no photo.
"""
from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage

from .models import DuressAlert, ScanPoint, Shipment, utcnow

log = logging.getLogger("aidtrace.notify")


def notify_duress(ctx, alert_id: int) -> None:
    s = ctx.sessions()
    try:
        a = s.get(DuressAlert, alert_id)
        r = a.receipt
        scan = s.get(ScanPoint, r.scan_point_id)
        sh = s.get(Shipment, scan.shipment_id)
        msg = {"type": "duress_alert", "alert_id": a.id, "shipment": sh.code, "scan_point": scan.kind,
               "site": sh.dest_site, "district": sh.dest_adm2_pcode, "received_at": r.server_received_at.isoformat() + "Z",
               "action": "Contact the security focal point protocol. Do not contact the recipient through ordinary channels."}
        st = ctx.settings
        done = {"in_app": True}
        (st.outbox_dir / f"duress_{a.id}.json").write_text(json.dumps(msg, indent=2), encoding="utf-8")
        done["outbox"] = True
        if st.duress_webhook:
            try:
                import httpx
                httpx.post(st.duress_webhook, json=msg, timeout=10).raise_for_status()
                done["webhook"] = True
            except Exception as e:  # noqa: BLE001
                log.error("duress webhook failed: %s", e)
                done["webhook"] = False
        if st.duress_email and st.smtp.get("host"):
            try:
                em = EmailMessage()
                em["Subject"] = f"[AidTrace] Duress alert {a.id}"
                em["From"] = st.smtp.get("from", "aidtrace@localhost")
                em["To"] = st.duress_email
                em.set_content(json.dumps(msg, indent=2))
                with smtplib.SMTP(st.smtp["host"], int(st.smtp.get("port", 587)), timeout=10) as smtp:
                    smtp.starttls()
                    if st.smtp.get("user"):
                        smtp.login(st.smtp["user"], st.smtp.get("password", ""))
                    smtp.send_message(em)
                done["email"] = True
            except Exception as e:  # noqa: BLE001
                log.error("duress email failed: %s", e)
                done["email"] = False
        a.notified = {**done, "at": utcnow().isoformat()}
        s.commit()
    finally:
        s.close()

"""Data retention: after `retention_days`, delete photos, attachments and free-text report content,
keeping only derived receipt status (brief §7). Also prunes nonces older than the maximum
accepted receipt age plus a margin, since older replays are rejected by timestamp anyway."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select

from .models import FieldReport, Receipt, UsedNonce, utcnow
from .settings import Settings, static_config


def purge(sessions, settings: Settings) -> dict:
    cutoff = utcnow() - timedelta(days=settings.retention_days)
    s = sessions()
    n_r = n_f = 0
    try:
        for r in s.scalars(select(Receipt).where(Receipt.server_received_at < cutoff, Receipt.content_purged.is_(False))):
            if r.photo_path:
                (settings.photo_dir / r.photo_path).unlink(missing_ok=True)
            r.photo_path, r.notes, r.cold_chain, r.content_purged = None, None, None, True
            n_r += 1
        for f in s.scalars(select(FieldReport).where(FieldReport.created_at < cutoff)):
            if f.attachment_path:
                (settings.photo_dir / f.attachment_path).unlink(missing_ok=True)
            if f.free_text or f.attachment_path:
                f.free_text, f.attachment_path = None, None
                n_f += 1
        nonce_cut = utcnow() - timedelta(days=static_config()["receipt"]["max_age_days"] + 7)
        n_n = s.execute(delete(UsedNonce).where(UsedNonce.seen_at < nonce_cut)).rowcount
        s.commit()
    finally:
        s.close()
    return {"receipts_purged": n_r, "reports_purged": n_f, "nonces_pruned": n_n}

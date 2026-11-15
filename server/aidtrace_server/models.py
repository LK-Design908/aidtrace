"""Database models (SQLite for the beta; portable to PostgreSQL/PostGIS).

Sensitive columns:
- Receipt.pin_class (normal | duress | mismatch) is only ever serialised for roles in
  `roles_see_duress`. Every other view derives a display status in which duress == normal.
- PINs are stored only as argon2 hashes. Device tokens are stored as SHA-256 hashes
  (high-entropy random values).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session_(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship()


class Recipient(Base):
    __tablename__ = "recipients"
    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(16), unique=True)        # e.g. R-7K2M9Q
    site_name: Mapped[str] = mapped_column(String(200))
    adm2_pcode: Mapped[str] = mapped_column(String(16))
    unit_id: Mapped[str] = mapped_column(String(16))
    facility_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str] = mapped_column(String(3), default="SOM")
    enrolment_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enrolment_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normal_pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duress_pin_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Shipment(Base):
    __tablename__ = "shipments"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    origin: Mapped[str] = mapped_column(String(200))
    dest_adm2_pcode: Mapped[str] = mapped_column(String(16))
    dest_unit_id: Mapped[str] = mapped_column(String(16))
    dest_site: Mapped[str] = mapped_column(String(200))
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    cold_chain: Mapped[bool] = mapped_column(Boolean, default=False)
    low_profile: Mapped[list] = mapped_column(JSON, default=list)
    two_person_required: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_band_at_creation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    items: Mapped[list["ShipmentItem"]] = relationship(cascade="all, delete-orphan")
    scan_points: Mapped[list["ScanPoint"]] = relationship(cascade="all, delete-orphan", order_by="ScanPoint.seq")
    recipients: Mapped[list["ShipmentRecipient"]] = relationship(cascade="all, delete-orphan")


class ShipmentItem(Base):
    __tablename__ = "shipment_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    item_code: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    batch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ShipmentRecipient(Base):
    __tablename__ = "shipment_recipients"
    __table_args__ = (UniqueConstraint("shipment_id", "recipient_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipients.id"))
    recipient: Mapped[Recipient] = relationship()


class ScanPoint(Base):
    __tablename__ = "scan_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    qr_expires: Mapped[datetime] = mapped_column(DateTime)
    receipts: Mapped[list["Receipt"]] = relationship(order_by="Receipt.server_received_at")


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    scan_point_id: Mapped[int] = mapped_column(ForeignKey("scan_points.id"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipients.id"))
    device_ts: Mapped[datetime] = mapped_column(DateTime)
    server_received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    channel: Mapped[str] = mapped_column(String(8), default="data")       # data | sms
    pin_class: Mapped[str] = mapped_column(String(16))                    # SENSITIVE: normal | duress | mismatch
    quantities: Mapped[list] = mapped_column(JSON, default=list)          # [{code, qty_received, damaged, expired}]
    cold_chain: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content_purged: Mapped[bool] = mapped_column(Boolean, default=False)
    recipient: Mapped[Recipient] = relationship()


class DuressAlert(Base):
    __tablename__ = "duress_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified: Mapped[dict] = mapped_column(JSON, default=dict)
    receipt: Mapped[Receipt] = relationship()


class UsedNonce(Base):
    __tablename__ = "used_nonces"
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldReport(Base):
    __tablename__ = "field_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    incident_date: Mapped[datetime] = mapped_column(DateTime)
    adm2_pcode: Mapped[str] = mapped_column(String(16))
    unit_id: Mapped[str] = mapped_column(String(16))
    free_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[int] = mapped_column(Integer)
    reporter_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reporter_recipient_id: Mapped[int | None] = mapped_column(ForeignKey("recipients.id"), nullable=True)
    reporter_role: Mapped[str] = mapped_column(String(32))
    attachment_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="unverified")
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerificationTask(Base):
    """Independent third-party follow-up of a confirmed delivery, drawn at random (CSPRNG)."""
    __tablename__ = "verification_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), unique=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    selection_rate: Mapped[float] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(24), default="pending")   # pending|confirmed|discrepancy|not_reachable
    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VerificationDraw(Base):
    """One draw per shipment, made when its final scan point first becomes verified."""
    __tablename__ = "verification_draws"
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), primary_key=True)
    drawn_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ServiceData(Base):
    """Facility service figures for consumption reconciliation (e.g. SAM admissions per month)."""
    __tablename__ = "service_data"
    __table_args__ = (UniqueConstraint("recipient_id", "month", "metric"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("recipients.id"))
    month: Mapped[str] = mapped_column(String(7))       # YYYY-MM
    metric: Mapped[str] = mapped_column(String(48))     # sam_admissions | cholera_cases_treated | vaccinations_given | stockout_days_<item_code>
    value: Mapped[float] = mapped_column()
    entered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SecurityEvent(Base):
    """Rejected submissions (replay, bad token, bad signature). No payload content stored."""
    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


def make_session_factory(db_url: str):
    engine = create_engine(db_url, connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)

"""Password/PIN hashing, distributor sessions, role checks, rate limiting, audit logging."""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .keys import random_token
from .models import AuditLog, Session_, User, utcnow

_ph = PasswordHasher()   # argon2id, library defaults (time_cost=3, memory_cost=64 MiB, parallelism=4)
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def hash_secret(secret: str) -> str:
    return _ph.hash(secret)


def verify_secret(hash_: str | None, secret: str) -> bool:
    """Constant-work verification: always runs one argon2 verify, even without a stored hash."""
    try:
        return _ph.verify(hash_ or _DUMMY_HASH, secret) and hash_ is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ------------------------------------------------------------------ rate limiting

class RateLimiter:
    """In-memory sliding window per key (beta; use Redis or a gateway in production)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")
            q.append(now)


# ------------------------------------------------------------------ context helpers

def ctx(request: Request):
    return request.app.state.ctx


def db(request: Request):
    s = ctx(request).sessions()
    try:
        yield s
    finally:
        s.close()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(s: Session, user: User | None, action: str, object_type: str | None = None, object_id=None) -> None:
    s.add(AuditLog(user_id=user.id if user else None, action=action, object_type=object_type,
                   object_id=None if object_id is None else str(object_id)))
    s.commit()


# ------------------------------------------------------------------ sessions

def create_session(s: Session, user: User) -> str:
    token = random_token(32)
    s.add(Session_(token_hash=sha256_hex(token), user_id=user.id))
    s.commit()
    return token


def current_user(request: Request, s: Session = Depends(db)) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token = auth[7:].strip()
    sess = s.get(Session_, sha256_hex(token))
    st = ctx(request).settings
    now = utcnow()
    if (sess is None or not sess.user.active
            or now - sess.last_seen > timedelta(minutes=st.session_idle_min)
            or now - sess.created_at > timedelta(hours=st.session_max_hours)):
        if sess is not None:
            s.delete(sess)
            s.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    sess.last_seen = now
    s.commit()
    request.state.session_token_hash = sess.token_hash
    return sess.user


def require_roles(roles: Iterable[str]):
    allowed = set(roles)

    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted for this role")
        return user
    return dep

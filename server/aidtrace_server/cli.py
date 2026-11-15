"""Admin CLI.

    aidtrace-server setup-keys            generate signing + box keys in AIDTRACE_KEY_DIR (idempotent)
    aidtrace-server rotate-keys           new active keys; old ones kept for verification/decryption
    aidtrace-server create-user NAME ROLE prompt for a password (or AIDTRACE_NEW_PASSWORD)
    aidtrace-server retention             purge content older than AIDTRACE_RETENTION_DAYS
    aidtrace-server serve [--port 8000]   run the API (put TLS in front in production)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os

from . import keys as keymod
from .settings import from_env, static_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="aidtrace-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-keys")
    sub.add_parser("rotate-keys")
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("role", choices=static_config()["roles"])
    sub.add_parser("retention")
    sub.add_parser("demo-reset")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    st = from_env()
    st.ensure_dirs()

    if a.cmd in ("setup-keys", "rotate-keys"):
        ring = keymod.generate(st.key_dir, rotate=a.cmd == "rotate-keys")
        print(f"keys in {st.key_dir}: active signing {ring.active_signing}, box {ring.active_box}")
    elif a.cmd == "create-user":
        from sqlalchemy import select
        from .models import User, make_session_factory
        from .security import hash_secret
        pw = os.environ.get("AIDTRACE_NEW_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
        if len(pw) < 12:
            raise SystemExit("password too short")
        s = make_session_factory(st.db_url)()
        if s.scalar(select(User).where(User.username == a.username)):
            raise SystemExit("user exists")
        s.add(User(username=a.username, password_hash=hash_secret(pw), role=a.role))
        s.commit()
        print(f"created {a.username} ({a.role}) in {st.mode} database")
    elif a.cmd == "retention":
        from .models import make_session_factory
        from .retention import purge
        print(json.dumps(purge(make_session_factory(st.db_url), st)))
    elif a.cmd == "demo-reset":
        if st.mode != "demo":
            raise SystemExit("demo-reset only runs with AIDTRACE_MODE=demo")
        db_file = st.data_dir / "aidtrace_demo.db"
        db_file.unlink(missing_ok=True)
        for p in st.photo_dir.glob("*"):
            p.unlink()
        from .seed import seed
        out = seed(st)
        print(f"demo reset: {len(out['shipments'])} synthetic shipments; demo users {', '.join(out['users'])}")
    elif a.cmd == "serve":
        import uvicorn
        from .app import create_app
        if st.mode == "demo" and not (st.data_dir / "aidtrace_demo.db").exists():
            from .seed import seed
            seed(st)
            print("seeded synthetic demo data")
        uvicorn.run(create_app(st, generate_keys=st.mode == "demo"), host=a.host, port=a.port)


if __name__ == "__main__":
    main()

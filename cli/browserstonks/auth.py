"""Connection token authentication for internet-facing BrowserStonks servers.

Issue tokens (`browserstonks tokens issue <label>`), store them in a
database (default SQLite at <config root>/tokens.db; config
`auth.database` may point at a Postgres/Mongo DSN — driver must be
installed), and require them on every websocket connection when
`auth.required: true`.

Wire protocol: the client sends `{"type": "auth", "token": "…"}` as its
FIRST message; the server replies `{"type": "auth.ok"}` or closes the
connection with an error. Tokens are stored hashed (sha256) — the
plaintext is shown once at issue time.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from .logging_util import log as _log

_auth_log = _log("auth")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenStore:
    """Token storage. SQLite default; Postgres/Mongo via `database` DSN
    when the driver is available (falls back to SQLite with a warning)."""

    def __init__(self, db_uri: str | None = None, root: Path | None = None) -> None:
        self.backend = "sqlite"
        self.path: Path | None = None
        if db_uri and db_uri.startswith("postgres"):
            try:
                import psycopg  # noqa: F401
                self.backend = "postgres"
                self.pg_uri = db_uri
            except ImportError:
                _auth_log.warning("postgres driver not installed — "
                                  "falling back to SQLite")
                db_uri = None
        elif db_uri and db_uri.startswith("mongodb"):
            try:
                import pymongo  # noqa: F401
                self.backend = "mongo"
                self.mongo_uri = db_uri
            except ImportError:
                _auth_log.warning("pymongo not installed — falling back "
                                  "to SQLite")
                db_uri = None
        if self.backend == "sqlite":
            self.path = Path(db_uri[len("sqlite://"):] if db_uri
                             and db_uri.startswith("sqlite://")
                             else (root or Path.cwd()) / "tokens.db")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------ init
    def _connect(self):
        if self.backend == "sqlite":
            return sqlite3.connect(self.path)
        if self.backend == "postgres":
            import psycopg
            return psycopg.connect(self.pg_uri)
        if self.backend == "mongo":
            import pymongo
            return pymongo.MongoClient(self.mongo_uri)

    def _init_schema(self) -> None:
        if self.backend == "sqlite":
            with self._connect() as c:
                c.execute("""CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    label TEXT,
                    issued_at REAL,
                    revoked_at REAL
                )""")
        elif self.backend == "postgres":
            with self._connect() as c:
                with c.cursor() as cur:
                    cur.execute("""CREATE TABLE IF NOT EXISTS tokens (
                        token_hash TEXT PRIMARY KEY,
                        label TEXT,
                        issued_at REAL,
                        revoked_at REAL
                    )""")
                c.commit()
        else:  # mongo
            with self._connect() as cli:
                cli.get_default_database()["tokens"]  # implicit creation

    # ------------------------------------------------------------------- api
    def issue(self, label: str = "") -> str:
        """Create a new token; returns the plaintext (shown once)."""
        token = "bs_" + secrets.token_urlsafe(32)
        now = time.time()
        if self.backend in ("sqlite", "postgres"):
            with self._connect() as c:
                c.execute("INSERT INTO tokens (token_hash, label, issued_at) "
                          "VALUES (?, ?, ?)"
                          if self.backend == "sqlite" else
                          "INSERT INTO tokens (token_hash, label, issued_at) "
                          "VALUES (%s, %s, %s)",
                          (_hash(token), label, now))
                if self.backend == "postgres":
                    c.commit()
        else:
            with self._connect() as cli:
                cli.get_default_database()["tokens"].insert_one(
                    {"_id": _hash(token), "label": label, "issued_at": now,
                     "revoked_at": None})
        _auth_log.info("issued token for '%s'", label or "unlabeled")
        return token

    def verify(self, token: str) -> bool:
        """True when the token exists and isn't revoked."""
        h = _hash(token)
        if self.backend in ("sqlite", "postgres"):
            q = ("SELECT revoked_at FROM tokens WHERE token_hash = ?"
                 if self.backend == "sqlite" else
                 "SELECT revoked_at FROM tokens WHERE token_hash = %s")
            with self._connect() as c:
                row = c.execute(q, (h,)).fetchone()
            return bool(row) and row[0] is None
        else:
            with self._connect() as cli:
                doc = cli.get_default_database()["tokens"].find_one({"_id": h})
            return bool(doc) and not doc.get("revoked_at")

    def revoke(self, token_or_label: str) -> int:
        """Revoke by plaintext token or label; returns count revoked."""
        h = _hash(token_or_label)
        now = time.time()
        if self.backend in ("sqlite", "postgres"):
            ph = "?" if self.backend == "sqlite" else "%s"
            with self._connect() as c:
                cur = c.execute(
                    f"UPDATE tokens SET revoked_at = {ph} "
                    f"WHERE token_hash = {ph} OR label = {ph}",
                    (now, h, token_or_label))
                n = cur.rowcount
                if self.backend == "postgres":
                    c.commit()
            return n
        else:
            with self._connect() as cli:
                res = cli.get_default_database()["tokens"].update_many(
                    {"$or": [{"_id": h}, {"label": token_or_label}]},
                    {"$set": {"revoked_at": now}})
                return res.modified_count

    def list(self) -> list[dict[str, Any]]:
        if self.backend in ("sqlite", "postgres"):
            ph = "?" if self.backend == "sqlite" else "%s"
            with self._connect() as c:
                rows = c.execute(
                    "SELECT label, issued_at, revoked_at FROM tokens"
                ).fetchall()
            return [{"label": r[0], "issued_at": r[1], "revoked": bool(r[2])}
                    for r in rows]
        else:
            with self._connect() as cli:
                docs = cli.get_default_database()["tokens"].find()
                return [{"label": d.get("label"),
                         "issued_at": d.get("issued_at"),
                         "revoked": bool(d.get("revoked_at"))} for d in docs]

"""Persistencia en SQLite: contactos, lista blanca de tools y auditoría."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from .policy import normalize_root

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MAX_AUDIT_ARGS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    token_hash  TEXT NOT NULL UNIQUE,
    path_root   TEXT,
    created_at  TEXT NOT NULL,
    revoked_at  TEXT
);
CREATE TABLE IF NOT EXISTS permissions (
    contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    PRIMARY KEY (contact_id, tool)
);
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    contact     TEXT NOT NULL,
    tool        TEXT NOT NULL,
    args        TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    outcome     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
"""


class StoreError(Exception):
    pass


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    path_root: str | None
    created_at: str
    revoked_at: str | None
    tools: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    # Los tokens son aleatorios de 256 bits: basta un hash rápido, sin sal.
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def init(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with self._db() as con:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(SCHEMA)

    # --- contactos -------------------------------------------------------

    def invite(self, name: str, tools: list[str], path_root: str | None = None) -> str:
        """Crea (o reactiva, si estaba revocado) un contacto. Devuelve su token,
        que no se guarda en claro y no se puede volver a consultar."""
        if not _NAME.match(name):
            raise StoreError("Nombre no válido: usa minúsculas, números, '-' y '_' (máx. 32).")
        tools = sorted({t.strip() for t in tools if t.strip()})
        root = normalize_root(path_root) if path_root else None
        token = "beira_" + secrets.token_urlsafe(32)
        with self._db() as con:
            row = con.execute("SELECT id, revoked_at FROM contacts WHERE name = ?", (name,)).fetchone()
            if row and row["revoked_at"] is None:
                raise StoreError(f"El contacto '{name}' ya existe. Revócalo antes de volver a invitarlo.")
            if row:
                cid = row["id"]
                con.execute(
                    "UPDATE contacts SET token_hash = ?, path_root = ?, created_at = ?, revoked_at = NULL WHERE id = ?",
                    (hash_token(token), root, _now(), cid),
                )
                con.execute("DELETE FROM permissions WHERE contact_id = ?", (cid,))
            else:
                cid = con.execute(
                    "INSERT INTO contacts (name, token_hash, path_root, created_at) VALUES (?, ?, ?, ?)",
                    (name, hash_token(token), root, _now()),
                ).lastrowid
            con.executemany(
                "INSERT INTO permissions (contact_id, tool) VALUES (?, ?)", [(cid, t) for t in tools]
            )
        return token

    def revoke(self, name: str) -> bool:
        with self._db() as con:
            cur = con.execute(
                "UPDATE contacts SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL", (_now(), name)
            )
            return cur.rowcount > 0

    def _contact(self, con: sqlite3.Connection, row: sqlite3.Row) -> Contact:
        tools = tuple(
            r["tool"]
            for r in con.execute(
                "SELECT tool FROM permissions WHERE contact_id = ? ORDER BY tool", (row["id"],)
            )
        )
        return Contact(row["id"], row["name"], row["path_root"], row["created_at"], row["revoked_at"], tools)

    def authenticate(self, token: str) -> Contact | None:
        """Contacto activo dueño de `token`, o None."""
        if not token:
            return None
        with self._db() as con:
            row = con.execute(
                "SELECT * FROM contacts WHERE token_hash = ? AND revoked_at IS NULL", (hash_token(token),)
            ).fetchone()
            return self._contact(con, row) if row else None

    def contacts(self) -> list[Contact]:
        with self._db() as con:
            rows = con.execute("SELECT * FROM contacts ORDER BY name").fetchall()
            return [self._contact(con, r) for r in rows]

    # --- auditoría -------------------------------------------------------

    def audit(
        self,
        contact: str,
        tool: str,
        arguments: dict[str, Any] | None,
        duration_ms: int,
        outcome: str,
        detail: str = "",
    ) -> None:
        args = json.dumps(arguments or {}, ensure_ascii=False, default=str)
        if len(args) > _MAX_AUDIT_ARGS:
            args = args[:_MAX_AUDIT_ARGS] + "…"
        with self._db() as con:
            con.execute(
                "INSERT INTO audit (ts, contact, tool, args, duration_ms, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), contact, tool[:200], args, duration_ms, outcome, detail[:500]),
            )

    def audit_log(self, limit: int = 20, contact: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM audit"
        params: list[Any] = []
        if contact:
            q += " WHERE contact = ?"
            params.append(contact)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._db() as con:
            return [dict(r) for r in con.execute(q, params)]

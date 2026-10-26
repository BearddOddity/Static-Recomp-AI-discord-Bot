"""SQLite storage for moderation cases, warnings and the cached #rules text.

Writes are tiny and infrequent, so the standard library's sqlite3 is used
directly from the event loop.
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    label TEXT NOT NULL,             -- scam, piracy, rule_violation, manual
    confidence REAL NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',
    content TEXT NOT NULL DEFAULT '',
    urls TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,            -- warned, review, closed
    actions TEXT NOT NULL DEFAULT '[]',
    timed_out INTEGER NOT NULL DEFAULT 0,
    message_deleted INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,              -- moderator id for manual warnings, NULL for the bot
    created_at REAL NOT NULL,
    decision TEXT,
    decided_by INTEGER,
    decided_at REAL,
    log_channel_id INTEGER,
    log_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    case_id INTEGER REFERENCES cases(id),
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS warnings_user ON warnings (guild_id, user_id);
CREATE TABLE IF NOT EXISTS rules (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    text TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

DAY = 86400


@dataclass
class Case:
    id: int
    guild_id: int
    user_id: int
    channel_id: int | None
    message_id: int | None
    label: str
    confidence: float
    reasons: list[str]
    content: str
    urls: list[str]
    status: str
    actions: list[str] = field(default_factory=list)
    timed_out: bool = False
    message_deleted: bool = False
    created_by: int | None = None
    created_at: float = 0.0
    decision: str | None = None
    decided_by: int | None = None
    decided_at: float | None = None
    log_channel_id: int | None = None
    log_message_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status != "closed"


@dataclass(frozen=True)
class Warning:
    id: int
    case_id: int | None
    reason: str
    created_at: float


class CaseStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- cases -------------------------------------------------------------

    def create_case(
        self,
        guild_id: int,
        user_id: int,
        label: str,
        reasons: list[str],
        *,
        channel_id: int | None = None,
        message_id: int | None = None,
        confidence: float = 0.0,
        content: str = "",
        urls: list[str] = (),
        created_by: int | None = None,
    ) -> Case:
        cursor = self._db.execute(
            "INSERT INTO cases (guild_id, user_id, channel_id, message_id, label, confidence, reasons,"
            " content, urls, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'warned', ?, ?)",
            (guild_id, user_id, channel_id, message_id, label, confidence, json.dumps(list(reasons)),
             content, json.dumps(list(urls)), created_by, time.time()),
        )
        self._db.commit()
        return self.get(cursor.lastrowid)

    def get(self, case_id: int) -> Case | None:
        row = self._db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return _case(row) if row else None

    def save(self, case: Case) -> None:
        self._db.execute(
            "UPDATE cases SET status = ?, actions = ?, timed_out = ?, message_deleted = ?, decision = ?,"
            " decided_by = ?, decided_at = ?, log_channel_id = ?, log_message_id = ? WHERE id = ?",
            (case.status, json.dumps(case.actions), int(case.timed_out), int(case.message_deleted),
             case.decision, case.decided_by, case.decided_at, case.log_channel_id, case.log_message_id, case.id),
        )
        self._db.commit()

    def cases_in_review(self) -> list[Case]:
        rows = self._db.execute("SELECT * FROM cases WHERE status = 'review' AND timed_out = 1").fetchall()
        return [_case(r) for r in rows]

    def cases_for_user(self, guild_id: int, user_id: int, limit: int = 10) -> list[Case]:
        rows = self._db.execute(
            "SELECT * FROM cases WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, user_id, limit),
        ).fetchall()
        return [_case(r) for r in rows]

    # --- warnings ----------------------------------------------------------

    def add_warning(self, guild_id: int, user_id: int, reason: str, case_id: int | None) -> None:
        self._db.execute(
            "INSERT INTO warnings (guild_id, user_id, case_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, case_id, reason[:500], time.time()),
        )
        self._db.commit()

    def active_warnings(self, guild_id: int, user_id: int, expiry_days: float) -> list[Warning]:
        """Warnings that still count: not revoked and newer than expiry_days (0 = never expire)."""
        since = time.time() - expiry_days * DAY if expiry_days > 0 else 0
        rows = self._db.execute(
            "SELECT id, case_id, reason, created_at FROM warnings"
            " WHERE guild_id = ? AND user_id = ? AND revoked = 0 AND created_at >= ? ORDER BY id",
            (guild_id, user_id, since),
        ).fetchall()
        return [Warning(r["id"], r["case_id"], r["reason"], r["created_at"]) for r in rows]

    def revoke_warnings_for_case(self, case_id: int) -> None:
        self._db.execute("UPDATE warnings SET revoked = 1 WHERE case_id = ?", (case_id,))
        self._db.commit()

    # --- rules -------------------------------------------------------------

    def save_rules(self, guild_id: int, channel_id: int, text: str) -> None:
        self._db.execute(
            "INSERT INTO rules (guild_id, channel_id, text, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id,"
            " text = excluded.text, updated_at = excluded.updated_at",
            (guild_id, channel_id, text, time.time()),
        )
        self._db.commit()

    def load_rules(self) -> dict[int, str]:
        return {r["guild_id"]: r["text"] for r in self._db.execute("SELECT guild_id, text FROM rules")}


def _case(row: sqlite3.Row) -> Case:
    data = dict(row)
    for key in ("reasons", "urls", "actions"):
        data[key] = json.loads(data[key])
    data["timed_out"] = bool(data["timed_out"])
    data["message_deleted"] = bool(data["message_deleted"])
    return Case(**data)

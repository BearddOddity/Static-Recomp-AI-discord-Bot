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
    label TEXT NOT NULL,             -- scam, piracy, hate_speech, rule_violation, manual
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
CREATE TABLE IF NOT EXISTS appeals (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,            -- open, accepted, rejected
    created_at REAL NOT NULL,
    decided_by INTEGER,
    decided_at REAL,
    log_channel_id INTEGER,
    log_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS answers (
    message_id INTEGER PRIMARY KEY,  -- the bot's last message of the answer, which holds the buttons
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    vote INTEGER NOT NULL,           -- 1 up, -1 down
    created_at REAL NOT NULL,
    PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS tickets (
    thread_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL,            -- open, human (a moderator took over), closed
    created_at REAL NOT NULL,
    closed_at REAL
);
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


@dataclass
class Appeal:
    case_id: int
    user_id: int
    text: str
    status: str = "open"
    created_at: float = 0.0
    decided_by: int | None = None
    decided_at: float | None = None
    log_channel_id: int | None = None
    log_message_id: int | None = None


@dataclass
class Ticket:
    thread_id: int
    guild_id: int
    user_id: int
    topic: str
    status: str = "open"
    created_at: float = 0.0
    closed_at: float | None = None


@dataclass(frozen=True)
class Answer:
    message_id: int
    guild_id: int | None
    channel_id: int | None
    user_id: int
    question: str
    answer: str
    created_at: float
    up: int = 0
    down: int = 0


@dataclass(frozen=True)
class Warning:
    id: int
    case_id: int | None
    reason: str
    created_at: float


# The database keeps no member text: message content, links, appeal text, the
# question behind an answer and a help topic are written only to Discord (the case
# report, the appeal post, the help thread). Older rows are cleared on startup.
PURGE = """
UPDATE cases SET content = '', urls = '[]' WHERE content != '' OR urls != '[]';
UPDATE appeals SET text = '' WHERE text != '';
UPDATE answers SET question = '' WHERE question != '';
UPDATE tickets SET topic = '' WHERE topic != '';
"""


class CaseStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.executescript(PURGE)
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
        # content and urls are not stored; the returned case carries them for this run only.
        cursor = self._db.execute(
            "INSERT INTO cases (guild_id, user_id, channel_id, message_id, label, confidence, reasons,"
            " status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'warned', ?, ?)",
            (guild_id, user_id, channel_id, message_id, label, confidence, json.dumps(list(reasons)),
             created_by, time.time()),
        )
        self._db.commit()
        case = self.get(cursor.lastrowid)
        assert case is not None  # the row was just inserted
        case.content, case.urls = content, list(urls)
        return case

    def get(self, case_id: int) -> Case | None:
        row = self._db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return _case(row) if row else None

    def save(self, case: Case) -> None:
        self._db.execute(
            "UPDATE cases SET label = ?, reasons = ?, status = ?, actions = ?, timed_out = ?, message_deleted = ?,"
            " decision = ?, decided_by = ?, decided_at = ?, log_channel_id = ?, log_message_id = ? WHERE id = ?",
            (case.label, json.dumps(case.reasons), case.status, json.dumps(case.actions), int(case.timed_out),
             int(case.message_deleted), case.decision, case.decided_by, case.decided_at,
             case.log_channel_id, case.log_message_id, case.id),
        )
        self._db.commit()

    def cases_in_review(self) -> list[Case]:
        rows = self._db.execute("SELECT * FROM cases WHERE status = 'review' AND timed_out = 1").fetchall()
        return [_case(r) for r in rows]

    def recent_open_case(self, guild_id: int, user_id: int, within_seconds: float) -> Case | None:
        """The member's case to add a new violation to: any case still waiting for
        review, or a warning case opened in the last within_seconds."""
        row = self._db.execute(
            "SELECT * FROM cases WHERE guild_id = ? AND user_id = ?"
            " AND (status = 'review' OR (status = 'warned' AND created_at >= ?)) ORDER BY id DESC LIMIT 1",
            (guild_id, user_id, time.time() - within_seconds),
        ).fetchone()
        return _case(row) if row else None

    def count_in_review(self, guild_id: int) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM cases WHERE guild_id = ? AND status = 'review'", (guild_id,)
        ).fetchone()[0]

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

    # --- appeals -----------------------------------------------------------

    def create_appeal(self, case_id: int, user_id: int, text: str) -> Appeal | None:
        """None when this case already has an appeal: one per case."""
        try:
            self._db.execute(
                "INSERT INTO appeals (case_id, user_id, text, status, created_at) VALUES (?, ?, '', 'open', ?)",
                (case_id, user_id, time.time()),
            )
        except sqlite3.IntegrityError:
            return None
        self._db.commit()
        appeal = self.get_appeal(case_id)
        assert appeal is not None  # the row was just inserted
        appeal.text = text  # kept in memory for the post; the database stores no appeal text
        return appeal

    def get_appeal(self, case_id: int) -> Appeal | None:
        row = self._db.execute("SELECT * FROM appeals WHERE case_id = ?", (case_id,)).fetchone()
        return Appeal(**dict(row)) if row else None

    def save_appeal(self, appeal: Appeal) -> None:
        self._db.execute(
            "UPDATE appeals SET status = ?, decided_by = ?, decided_at = ?, log_channel_id = ?, log_message_id = ?"
            " WHERE case_id = ?",
            (appeal.status, appeal.decided_by, appeal.decided_at, appeal.log_channel_id, appeal.log_message_id,
             appeal.case_id),
        )
        self._db.commit()

    # --- answer feedback ---------------------------------------------------

    def record_answer(self, message_id: int, guild_id: int | None, channel_id: int | None, user_id: int,
                      question: str, answer: str) -> None:
        # The member's question is not stored, only the answer the bot gave.
        self._db.execute(
            "INSERT OR REPLACE INTO answers (message_id, guild_id, channel_id, user_id, question, answer, created_at)"
            " VALUES (?, ?, ?, ?, '', ?, ?)",
            (message_id, guild_id, channel_id, user_id, answer[:3000], time.time()),
        )
        self._db.commit()

    def has_answer(self, message_id: int) -> bool:
        return self._db.execute("SELECT 1 FROM answers WHERE message_id = ?", (message_id,)).fetchone() is not None

    def vote(self, message_id: int, user_id: int, vote: int) -> int:
        """Record a vote (1 up, -1 down). Voting the same way again takes it back.
        Returns the member's vote now: 1, -1 or 0."""
        row = self._db.execute(
            "SELECT vote FROM votes WHERE message_id = ? AND user_id = ?", (message_id, user_id)
        ).fetchone()
        if row and row["vote"] == vote:
            self._db.execute("DELETE FROM votes WHERE message_id = ? AND user_id = ?", (message_id, user_id))
            now = 0
        else:
            self._db.execute(
                "INSERT OR REPLACE INTO votes (message_id, user_id, vote, created_at) VALUES (?, ?, ?, ?)",
                (message_id, user_id, vote, time.time()),
            )
            now = vote
        self._db.commit()
        return now

    def vote_counts(self, message_id: int) -> tuple[int, int]:
        row = self._db.execute(
            "SELECT COALESCE(SUM(vote = 1), 0) AS up, COALESCE(SUM(vote = -1), 0) AS down FROM votes"
            " WHERE message_id = ?", (message_id,),
        ).fetchone()
        return int(row["up"]), int(row["down"])

    def downvoted_answers(self, guild_id: int, limit: int = 10) -> list[Answer]:
        """Answers with more 👎 than 👍, the most recently downvoted first."""
        rows = self._db.execute(
            "SELECT a.*, SUM(v.vote = 1) AS up, SUM(v.vote = -1) AS down, MAX(v.created_at) AS last_vote"
            " FROM answers a JOIN votes v ON v.message_id = a.message_id WHERE a.guild_id = ?"
            " GROUP BY a.message_id HAVING down > up ORDER BY last_vote DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()
        return [
            Answer(r["message_id"], r["guild_id"], r["channel_id"], r["user_id"], r["question"], r["answer"],
                   r["created_at"], int(r["up"]), int(r["down"]))
            for r in rows
        ]

    # --- help tickets ------------------------------------------------------

    def create_ticket(self, thread_id: int, guild_id: int, user_id: int, topic: str) -> Ticket:
        self._db.execute(
            "INSERT INTO tickets (thread_id, guild_id, user_id, topic, status, created_at) VALUES (?, ?, ?, '', 'open', ?)",
            (thread_id, guild_id, user_id, time.time()),
        )
        self._db.commit()
        ticket = self.get_ticket(thread_id)
        assert ticket is not None  # the row was just inserted
        ticket.topic = topic[:500]  # in memory only, for the notice posted right away
        return ticket

    def get_ticket(self, thread_id: int) -> Ticket | None:
        row = self._db.execute("SELECT * FROM tickets WHERE thread_id = ?", (thread_id,)).fetchone()
        return Ticket(**dict(row)) if row else None

    def open_ticket_for(self, guild_id: int, user_id: int) -> Ticket | None:
        row = self._db.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status != 'closed' ORDER BY created_at DESC LIMIT 1",
            (guild_id, user_id),
        ).fetchone()
        return Ticket(**dict(row)) if row else None

    def set_ticket_status(self, thread_id: int, status: str) -> None:
        self._db.execute(
            "UPDATE tickets SET status = ?, closed_at = ? WHERE thread_id = ?",
            (status, time.time() if status == "closed" else None, thread_id),
        )
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

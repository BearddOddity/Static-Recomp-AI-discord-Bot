"""The safety brake: stops the bot punishing people by itself when something looks wrong.

Two ways it comes on:

- **By hand:** a moderator runs `/brake on` (an emergency stop). Until `/brake off`,
  nothing automatic happens: no deletes, warnings, DMs or timeouts, and the AI stops
  answering. Violations still get a case report, so moderators decide.
- **By itself:** if the AI judges more than MOD_BRAKE_LIMIT different members as
  violations within 10 minutes, a broken model, a bad prompt or a trick is far more
  likely than a real wave, so automatic actions on the AI's say-so pause for an hour.
  Certain rule hits (a known scam domain, a game file) and Discord AutoMod still act.

The state is saved, so a restart doesn't release it.
"""

import json
import logging
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("bot.moderation")

WINDOW_SECONDS = 600
AUTO_PAUSE_SECONDS = 3600


class SafetyBrake:
    def __init__(self, path: str | Path | None = None, limit: int = 8):
        self.path = Path(path) if path else None
        self.limit = limit  # 0 = never comes on by itself
        self.manual = False  # /brake on: everything automatic stops, until /brake off
        self.auto_until = 0.0  # wall-clock time the automatic pause ends
        self.reason = ""
        self._recent: deque[tuple[float, int]] = deque()  # (time, member) of AI-judged actions
        self._load()

    # --- state ---------------------------------------------------------------

    @property
    def auto_engaged(self) -> bool:
        return time.time() < self.auto_until

    @property
    def engaged(self) -> bool:
        return self.manual or self.auto_engaged

    def holds(self, certain: bool) -> bool:
        """Whether automatic action on a violation must wait for a moderator. certain:
        the rules or Discord AutoMod decided, not the AI."""
        return self.manual or (self.auto_engaged and not certain)

    def record(self, member_id: int, now: float | None = None) -> bool:
        """Count an automatic action the AI decided. True if it just switched the brake
        on, in which case this action is held too."""
        if self.limit <= 0:
            return False
        now = time.monotonic() if now is None else now
        recent = self._recent
        while recent and now - recent[0][0] > WINDOW_SECONDS:
            recent.popleft()
        recent.append((now, member_id))
        members = {m for _, m in recent}
        if len(members) <= self.limit:
            return False
        self.auto_until = time.time() + AUTO_PAUSE_SECONDS
        self.reason = f"the AI flagged {len(members)} members in {WINDOW_SECONDS // 60} minutes"
        recent.clear()
        log.warning("Safety brake on: %s", self.reason)
        self._save()
        return True

    def engage(self, by: str) -> None:
        self.manual = True
        self.reason = f"turned on by {by}"
        self._save()

    def release(self) -> None:
        self.manual, self.auto_until, self.reason = False, 0.0, ""
        self._recent.clear()
        self._save()

    def describe(self) -> str:
        if self.manual:
            return f"ON ({self.reason}): no automatic punishments and no AI answers until a moderator runs /brake off"
        if self.auto_engaged:
            minutes = max(1, round((self.auto_until - time.time()) / 60))
            return (f"ON for {minutes} more minutes ({self.reason}): the AI's verdicts only open case reports; "
                    "certain rule hits and AutoMod still act")
        return "off"

    # --- saving ----------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text())
            self.manual = bool(data.get("manual"))
            self.auto_until = float(data.get("auto_until", 0))
            self.reason = str(data.get("reason", ""))[:200]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"manual": self.manual, "auto_until": self.auto_until, "reason": self.reason}))
        except OSError:
            log.exception("Couldn't save the safety brake state")

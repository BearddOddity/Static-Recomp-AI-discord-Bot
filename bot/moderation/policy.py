"""The warning ladder. Pure functions, so the rules are easy to read and test.

    violation -> warning (DM'd to the user, case report for moderators)
    warning number MOD_WARN_LIMIT, a near-certain scam, or hate speech
        -> timeout until a human reviews
    ban -> only ever by a moderator pressing Ban on the case report

Opinions, rudeness and profanity are free speech and never reach this ladder.

The bot never bans or kicks anyone by itself.
"""

from dataclasses import dataclass
from datetime import timedelta

from ..config import Settings
from .engine import HARD_SCORE, Verdict

# Discord's longest timeout is 28 days. Review timeouts are renewed before they run
# out, so the member stays timed out until a moderator decides.
REVIEW_TIMEOUT = timedelta(days=28) - timedelta(minutes=5)
RENEW_WHEN_LEFT = timedelta(days=3)

LABEL_NAMES = {
    "scam": "Scam",
    "piracy": "Piracy",
    "hate_speech": "Hate speech",
    "rule_violation": "Rule violation",
    "manual": "Warning from a moderator",
}


@dataclass(frozen=True)
class Plan:
    delete_message: bool
    review_timeout: bool
    warning_number: int


def is_severe(verdict: Verdict, settings: Settings) -> bool:
    """Hate speech, near-certain scams (usually hacked accounts), and certain piracy
    (a game file or piracy-site link the rules recognised, no AI guess involved)."""
    if verdict.label == "hate_speech":
        return True
    if verdict.label == "piracy":
        return verdict.score >= HARD_SCORE
    if verdict.label != "scam":
        return False
    return verdict.score >= HARD_SCORE or verdict.confidence >= settings.mod_severe_confidence


def plan(label: str, severe: bool, active_warnings: int, settings: Settings) -> Plan:
    delete = {
        "scam": settings.mod_action,
        "piracy": settings.mod_piracy_action,
        "hate_speech": settings.mod_hate_action,
        "rule_violation": settings.mod_rule_action,
    }.get(label) == "delete"
    # Shared game files and piracy links always come down: leaving them up is distribution.
    if label == "piracy" and severe:
        delete = True
    number = active_warnings + 1
    # Hate speech always waits for a human; severe scams do unless turned off.
    # Piracy follows the normal warning ladder: it's often a newcomer who didn't know.
    review = (
        label == "hate_speech"
        or (severe and label == "scam" and settings.mod_severe_scam_review)
        or number >= settings.mod_warn_limit
    )
    return Plan(delete_message=delete, review_timeout=review, warning_number=number)

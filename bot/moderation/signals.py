from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """One reason a post looks suspicious. Weights add up to a risk score (0-100+)."""

    reason: str
    weight: int
    category: str = "scam"  # "scam" or "piracy": which kind of violation it points to


def total(signals: list[Signal]) -> int:
    return sum(s.weight for s in signals)


def leading_category(signals: list[Signal]) -> str:
    """The kind of violation the signals mostly point to."""
    piracy = sum(s.weight for s in signals if s.category == "piracy")
    return "piracy" if piracy * 2 >= total(signals) and piracy > 0 else "scam"

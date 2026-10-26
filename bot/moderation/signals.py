from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """One reason a post looks suspicious. Weights add up to a risk score (0-100+)."""

    reason: str
    weight: int


def total(signals: list[Signal]) -> int:
    return sum(s.weight for s in signals)

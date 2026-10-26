import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    keywords: tuple[str, ...]
    repo: str  # owner/repo
    pattern: re.Pattern


class DocsRouter:
    """Sends a question to specific repos' docs when it names a keyword.

    Rules come from DOCS_ROUTES, written as
        "xbox 360,x360,360=rexglue/rexglue-sdk; xbox=sp00nznet/xboxrecomp"
    Rules are checked in order and each match is removed from the question
    before the next rule runs, so "Xbox 360" goes to the first repo above and
    does not also count as plain "Xbox". Keywords match whole words, ignoring case.
    """

    def __init__(self, routes: list[Route]):
        self.routes = routes

    @classmethod
    def parse(cls, value: str) -> "DocsRouter":
        routes = []
        for rule in value.split(";"):
            if not rule.strip():
                continue
            words, sep, repo = rule.partition("=")
            keywords = tuple(w.strip().lower() for w in words.split(",") if w.strip())
            repo = repo.strip()
            if not sep or not keywords or repo.count("/") != 1:
                raise ValueError(f"Bad DOCS_ROUTES rule {rule.strip()!r}, expected words=owner/repo")
            # Longest first, so "xbox 360" wins over "360" inside the same rule.
            alternatives = "|".join(
                r"\s+".join(map(re.escape, k.split())) for k in sorted(keywords, key=len, reverse=True)
            )
            pattern = re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])", re.IGNORECASE)
            routes.append(Route(keywords, repo, pattern))
        return cls(routes)

    @property
    def repos(self) -> set[str]:
        return {route.repo.lower() for route in self.routes}

    def route(self, question: str) -> tuple[list[str], str]:
        """Return the repos the question is about (in rule order, empty means all)
        and the question with those keywords removed, for searching inside them."""
        matched = []
        for route in self.routes:
            question, hits = route.pattern.subn(" ", question)
            if hits and route.repo not in matched:
                matched.append(route.repo)
        return matched, question

    def match(self, question: str) -> list[str]:
        return self.route(question)[0]

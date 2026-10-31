"""Tells people when the AI needs attention, and proposes the fix on GitHub.

- A model that disappeared: the bot already switched to one that works. It posts
  a notice in the mod log and opens a pull request that makes the switch
  permanent in the server profile. Merge it and the bot picks it up on its next
  self-update.
- A key that stopped working: a key is a secret and never goes in git, so the bot
  opens a GitHub issue (and posts in the mod log) asking for a new one.
- Nothing works at all: an issue too.
"""

import base64
import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx

from .ai import Problem

log = logging.getLogger("bot.health")

API = "https://api.github.com"
LABEL = "ai-health"


def set_env_value(text: str, name: str, value: str) -> str:
    """Set NAME=value in a .env-style file: replace the first NAME= line, or add one."""
    line = f"{name}={value}"
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda _: line, text, count=1)
    return text.rstrip("\n") + f"\n{line}\n"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


class GitHubReporter:
    """Opens pull requests and issues on the bot's own repository. Needs a
    fine-grained token for that repo with Contents, Pull requests and Issues set to
    read and write."""

    def __init__(self, repo: str, token: str, client: httpx.AsyncClient | None = None):
        self.repo = repo
        self._client = client or httpx.AsyncClient(timeout=30)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        self._writable: bool | None = None

    async def can_write(self) -> bool:
        """Whether the token can push to the repo. Anyone can open issues on a public repo,
        so a copy of the bot run by someone else (with HEALTH_GITHUB_REPO still naming the
        original) must not file its problems there. Only the repo's own maintainers report."""
        if self._writable is None:
            info = await self._json("GET", "")
            self._writable = bool((info.get("permissions") or {}).get("push"))
            if not self._writable:
                log.warning("HEALTH_GITHUB_TOKEN can't push to %s, so AI problems are only posted in the mod log. "
                            "Point HEALTH_GITHUB_REPO at your own copy of the bot.", self.repo)
        return self._writable

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self._client.request(method, f"{API}/repos/{self.repo}{path}", headers=self._headers, **kwargs)
        return response

    async def _json(self, method: str, path: str, **kwargs):
        response = await self._call(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def open_pr(self, file_path: str, setting: str, value: str, title: str, body: str) -> str:
        """Open (or find) a pull request that sets setting=value in file_path. Returns its URL."""
        owner = self.repo.split("/")[0]
        branch = f"bot/{_slug(setting)}-{_slug(value.split(',')[0])}"
        existing = await self._json("GET", "/pulls", params={"state": "open", "head": f"{owner}:{branch}"})
        if existing:
            return existing[0]["html_url"]

        base = (await self._json("GET", ""))["default_branch"]
        sha = (await self._json("GET", f"/git/ref/heads/{quote(base)}"))["object"]["sha"]
        created = await self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
        if created.status_code not in (201, 422):  # 422: the branch is already there
            created.raise_for_status()

        current = await self._json("GET", f"/contents/{quote(file_path)}", params={"ref": branch})
        text = base64.b64decode(current["content"]).decode("utf-8")
        updated = set_env_value(text, setting, value)
        if updated != text:
            await self._json("PUT", f"/contents/{quote(file_path)}", json={
                "message": title,
                "content": base64.b64encode(updated.encode()).decode(),
                "sha": current["sha"],
                "branch": branch,
            })
        pr = await self._json("POST", "/pulls", json={"title": title, "head": branch, "base": base, "body": body})
        return pr["html_url"]

    async def open_issue(self, title: str, body: str) -> str:
        """Open an issue, unless an open one with the same title exists. Returns its URL."""
        for issue in await self._json("GET", "/issues", params={"state": "open", "labels": LABEL}):
            if issue.get("title") == title:
                return issue["html_url"]
        issue = await self._json("POST", "/issues", json={"title": title, "body": body, "labels": [LABEL]})
        return issue["html_url"]

    async def aclose(self) -> None:
        await self._client.aclose()


FOOTER = "\n\n---\n_Opened automatically by the bot's AI health check._"


def describe(problem: Problem, pr_file: str) -> tuple[str, str]:
    """Title and body for the pull request or issue."""
    if problem.kind == "model" and not pr_file:
        title = f"Switch {problem.setting} to {problem.replacement}"
        body = (
            f"The AI model `{problem.model}` stopped working: {problem.detail}\n\n"
            f"The bot has switched to `{problem.replacement}` for now. To keep it after a restart, set "
            f"`{problem.setting}={problem.replacement}` in the bot's `.env`. (With a server profile in "
            "`SERVER_PROFILE`, the bot would open a pull request for this instead.)"
        )
    elif problem.kind == "model":
        title = f"Switch {problem.setting} to {problem.replacement}"
        body = (
            f"The AI model `{problem.model}` stopped working: {problem.detail}\n\n"
            f"The bot has already switched to `{problem.replacement}`, which works, but only until it "
            f"restarts. Merge this to make the switch permanent: it sets `{problem.setting}` in "
            f"`{pr_file}`. The bot picks it up on its next self-update.\n\n"
            f"If `{problem.setting}` is also set in the bot's `.env`, remove it there, because `.env` wins "
            "over this file."
        )
    elif problem.kind == "key":
        title = "The AI key needs replacing"
        body = (
            f"The AI provider refused the bot's key while using `{problem.model}`:\n\n> {problem.detail}\n\n"
            "The key may be wrong, revoked, or out of credit. Create a new one (for OpenRouter: "
            "https://openrouter.ai/keys), put it in the bot's `.env` as `OPENAI_COMPAT_API_KEY` (or "
            "`ANTHROPIC_API_KEY`), and restart the bot.\n\n"
            "This is an issue rather than a pull request because a key is a secret and must never be "
            "committed to the repository."
        )
    else:
        title = f"No AI model is working for {problem.setting}"
        body = (
            f"Every model the bot tried for `{problem.setting}` failed. The last error:\n\n> {problem.detail}\n\n"
            "Pick a model that's available today (free ones on OpenRouter end in `:free`: "
            "https://openrouter.ai/models?max_price=0) and set it in the server profile or `.env`."
        )
    return title, body + FOOTER


class HealthMonitor:
    """Receives AI problems (FallbackProvider.on_problem), tells the moderators once,
    and opens one pull request or issue per problem."""

    def __init__(
        self,
        notify: Callable[[str], Awaitable[None]],
        reporter: GitHubReporter | None = None,
        pr_file: str = "",
    ):
        self._notify = notify
        self._reporter = reporter
        self._pr_file = pr_file
        self._seen: set[tuple[str, str, str]] = set()
        self.problems: list[Problem] = []

    async def report(self, problem: Problem) -> None:
        key = (problem.kind, problem.setting, problem.model)
        if key in self._seen:
            return
        self._seen.add(key)
        self.problems.append(problem)
        log.warning("AI problem: %s", problem)

        title, body = describe(problem, self._pr_file)
        link = ""
        if self._reporter is not None:
            try:
                if not await self._reporter.can_write():
                    pass  # someone else's repo: the mod log notice below is enough
                elif problem.kind == "model" and self._pr_file:
                    link = await self._reporter.open_pr(
                        self._pr_file, problem.setting, problem.replacement, title, body
                    )
                else:
                    link = await self._reporter.open_issue(title, body)
            except httpx.HTTPError:
                log.exception("Couldn't open a GitHub pull request or issue for %s", problem.kind)

        if problem.kind == "model":
            text = (f"⚠️ **AI model replaced.** `{problem.model}` stopped working, so I switched "
                    f"`{problem.setting}` to `{problem.replacement}`.")
        elif problem.kind == "key":
            text = "🔑 **The AI key needs replacing.** The provider refused it. Put a new key in `.env` and restart me."
        else:
            text = f"⛔ **No AI model is working** for `{problem.setting}`. Set a model that's available today."
        if link:
            text += f"\n{'Pull request' if '/pull/' in link else 'Details'}: <{link}>"
        try:
            await self._notify(text)
        except Exception:
            log.exception("Couldn't post the AI health notice")

    async def aclose(self) -> None:
        if self._reporter is not None:
            await self._reporter.aclose()

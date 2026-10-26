"""Self-updating: pulls new commits from GitHub, then restarts the bot.

Works when the bot runs from a git clone. The bot checks every
UPDATE_CHECK_MINUTES, fast-forwards to the latest commit on its branch, and exits
with RESTART_EXIT_CODE. start.sh, start.bat, the Linux service and Docker all
start it again, installing any new requirements first.

It never overwrites local changes: if files were edited by hand, or the history
has diverged, it logs a warning and leaves the checkout alone.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bot.updater")

RESTART_EXIT_CODE = 3
GIT_TIMEOUT = 120


@dataclass(frozen=True)
class Update:
    old: str
    new: str
    summary: str  # "abc1234 Fix the thing" for the newest commit


class Updater:
    def __init__(self, folder: str | Path = ".", branch: str = ""):
        self.folder = Path(folder)
        self.branch = branch

    @property
    def available(self) -> bool:
        return (self.folder / ".git").exists()

    async def _git(self, *args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=self.folder,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=GIT_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return 1, "timed out"
        return process.returncode, output.decode(errors="replace").strip()

    async def check(self) -> Update | None:
        """Pull new commits if there are any. Returns what changed, or None."""
        if not self.available:
            return None
        branch = self.branch
        if not branch:
            code, branch = await self._git("rev-parse", "--abbrev-ref", "HEAD")
            if code or branch == "HEAD":
                log.warning("Auto-update: not on a branch, skipping")
                return None
        code, output = await self._git("fetch", "--quiet", "origin", branch)
        if code:
            log.warning("Auto-update: git fetch failed: %s", output[:300])
            return None
        _, old = await self._git("rev-parse", "HEAD")
        _, new = await self._git("rev-parse", f"origin/{branch}")
        if old == new:
            return None
        code, _ = await self._git("merge-base", "--is-ancestor", "HEAD", f"origin/{branch}")
        if code:
            log.warning("Auto-update: this copy has commits GitHub doesn't, so it won't update by itself")
            return None
        _, changed = await self._git("status", "--porcelain", "--untracked-files=no")
        if changed:
            log.warning("Auto-update: files were edited here, so it won't overwrite them:\n%s", changed[:500])
            return None
        code, output = await self._git("merge", "--ff-only", "--quiet", f"origin/{branch}")
        if code:
            log.warning("Auto-update: git merge failed: %s", output[:300])
            return None
        _, summary = await self._git("log", "-1", "--format=%h %s")
        return Update(old[:7], new[:7], summary)

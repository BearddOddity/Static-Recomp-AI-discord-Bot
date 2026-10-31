"""The /status report: a checklist that says what works, and what to fix and how.

Server owners aren't necessarily programmers, so every problem comes with the exact
step that fixes it, problems are listed first, and when nothing needs fixing the
report says so in its title.
"""

from dataclasses import dataclass

import discord

OK, FIX, OFF = "ok", "fix", "off"
ICONS = {OK: "✅", FIX: "❌", OFF: "➖"}
GREEN, RED = 0x2ECC71, 0xE74C3C
DESCRIPTION_LIMIT = 4000  # Discord allows 4096 in an embed description


@dataclass(frozen=True)
class Check:
    state: str  # OK, FIX (needs fixing) or OFF (switched off on purpose, optional)
    text: str
    fix: str = ""  # for FIX: the exact step that fixes it


def ok(text: str) -> Check:
    return Check(OK, text)


def fix(text: str, how: str) -> Check:
    return Check(FIX, text, how)


def off(text: str) -> Check:
    return Check(OFF, text)


def _line(check: Check) -> str:
    line = f"{ICONS[check.state]} {check.text}"
    if check.fix:
        line += f"\n> **Fix:** {check.fix}"
    return line


def render(checks: list[Check], name: str, version: str) -> discord.Embed:
    """Problems first, then what works, then what's switched off. The title alone says
    whether anything needs doing."""
    problems = [c for c in checks if c.state == FIX]
    if problems:
        title = f"❌ {len(problems)} thing{'s' if len(problems) != 1 else ''} to fix"
    else:
        title = "✅ All good: nothing needs fixing"
    sections = [
        ("**Needs fixing**", problems, "\n\n"),
        ("**Working**", [c for c in checks if c.state == OK], "\n"),
        ("**Switched off (optional)**", [c for c in checks if c.state == OFF], "\n"),
    ]
    text = ""
    for heading, items, gap in sections:
        if not items:
            continue
        block = heading + "\n" + gap.join(_line(c) for c in items)
        if len(text) + len(block) + 2 > DESCRIPTION_LIMIT:
            # Problems come first, so they are never the part that gets cut.
            block = block[: max(0, DESCRIPTION_LIMIT - len(text) - 3)] + "…"
        text += ("\n\n" if text else "") + block
        if len(text) >= DESCRIPTION_LIMIT:
            break
    embed = discord.Embed(title=title, description=text, colour=RED if problems else GREEN)
    embed.set_footer(text=f"{name} v{version} · run /status again after fixing")
    return embed

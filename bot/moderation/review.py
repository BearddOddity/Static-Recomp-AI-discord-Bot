"""Case reports for moderators: warnings, review timeouts and the decision buttons.

Every button's custom_id is "case:<id>:<action>", so buttons keep working after
the bot restarts (cases live in SQLite, nothing is held in memory).
"""

import io
import logging
import time

import discord

from ..config import Settings
from .cases import Case, CaseStore
from .engine import Post, Verdict
from .policy import LABEL_NAMES, RENEW_WHEN_LEFT, REVIEW_TIMEOUT, is_severe, plan

log = logging.getLogger("bot.moderation")

PREFIX = "case"

# action -> (button label, style, permission the clicking moderator needs)
ACTIONS = {
    "ban": ("Ban", discord.ButtonStyle.danger, "ban_members"),
    "timeout": ("Timeout until reviewed", discord.ButtonStyle.secondary, "moderate_members"),
    "lift": ("Lift timeout, keep warning", discord.ButtonStyle.success, "moderate_members"),
    "dismiss": ("Not a violation", discord.ButtonStyle.secondary, "moderate_members"),
    "delete": ("Delete message", discord.ButtonStyle.secondary, "manage_messages"),
}


def custom_id(case_id: int, action: str) -> str:
    return f"{PREFIX}:{case_id}:{action}"


def parse_custom_id(value: str) -> tuple[int, str] | None:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1].isdigit():
        return None
    return int(parts[1]), parts[2]


def available_actions(case: Case) -> list[str]:
    if not case.is_open:
        return []
    actions = ["ban"]
    actions.append("lift" if case.timed_out else "timeout")
    actions.append("dismiss")
    if case.message_id and not case.message_deleted:
        actions.append("delete")
    return actions


def build_view(case: Case) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in available_actions(case):
        label, style, _ = ACTIONS[action]
        view.add_item(discord.ui.Button(label=label, style=style, custom_id=custom_id(case.id, action)))
    return view


def build_embed(case: Case, warning_count: int, settings: Settings) -> discord.Embed:
    status = {
        "warned": f"Warned ({warning_count}/{settings.mod_warn_limit})",
        "review": "⏸ In timeout until a moderator reviews",
        "closed": f"Closed: {case.decision}",
    }[case.status]
    color = {"warned": discord.Color.orange(), "review": discord.Color.red(), "closed": discord.Color.dark_grey()}[case.status]

    embed = discord.Embed(
        title=f"Case #{case.id} · {LABEL_NAMES.get(case.label, case.label)}"
        + (f" ({case.confidence:.0%} confident)" if case.confidence else ""),
        description=(case.content[:1500] or "*(no text)*"),
        color=color,
    )
    embed.add_field(name="Member", value=f"<@{case.user_id}> ({case.user_id})", inline=True)
    if case.channel_id:
        where = f"<#{case.channel_id}>"
        if case.message_id and not case.message_deleted:
            where += f" · [jump](https://discord.com/channels/{case.guild_id}/{case.channel_id}/{case.message_id})"
        embed.add_field(name="Where", value=where, inline=True)
    embed.add_field(name="Status", value=status, inline=False)
    embed.add_field(name="Why", value="\n".join(f"• {r}" for r in case.reasons[:8])[:1024] or "-", inline=False)
    if case.urls:
        embed.add_field(name="Links", value="\n".join(f"`{u}`" for u in case.urls[:5])[:1024], inline=False)
    if case.actions:
        embed.add_field(name="Actions taken", value="\n".join(f"• {a}" for a in case.actions[-8:])[:1024], inline=False)
    if case.status == "review":
        embed.add_field(
            name="Needs a decision",
            value="Bans are only made by moderators. The member stays timed out until someone decides.",
            inline=False,
        )
    who = f"moderator <@{case.created_by}>" if case.created_by else "the bot"
    embed.set_footer(text=f"Opened by {who}")
    embed.timestamp = discord.utils.utcnow()
    return embed


class CaseManager:
    def __init__(self, client: discord.Client, store: CaseStore, settings: Settings):
        self.client = client
        self.store = store
        self.settings = settings

    def warning_count(self, guild_id: int, user_id: int) -> int:
        return len(self.store.active_warnings(guild_id, user_id, self.settings.mod_warn_expiry_days))

    # --- opening cases -----------------------------------------------------

    async def open_from_message(self, message: discord.Message, post: Post, verdict: Verdict) -> Case:
        case = self.store.create_case(
            message.guild.id, message.author.id, verdict.label, list(verdict.reasons),
            channel_id=message.channel.id, message_id=message.id, confidence=verdict.confidence,
            content=message.content, urls=list(verdict.urls),
        )
        await self._apply(case, message.author, is_severe(verdict, self.settings), message, post.images)
        return case

    async def open_manual(self, guild: discord.Guild, member: discord.Member, moderator: discord.abc.User, reason: str) -> Case:
        case = self.store.create_case(guild.id, member.id, "manual", [reason], created_by=moderator.id, content=reason)
        await self._apply(case, member, False, None, [])
        return case

    async def _apply(self, case: Case, member: discord.abc.User, severe: bool, message: discord.Message | None, images) -> None:
        steps = plan(case.label, severe, self.warning_count(case.guild_id, case.user_id), self.settings)
        reason = case.reasons[0] if case.reasons else LABEL_NAMES.get(case.label, case.label)
        self.store.add_warning(case.guild_id, case.user_id, reason, case.id)
        case.actions.append(f"warning {steps.warning_number} of {self.settings.mod_warn_limit} given")

        if steps.delete_message and message is not None:
            await self._delete_message(case, message)
        if steps.review_timeout and isinstance(member, discord.Member):
            await self._review_timeout(case, member)
        if self.settings.mod_dm_warnings:
            await self._dm(member, case, steps.warning_number)

        self.store.save(case)
        await self._post_report(case, images)

    # --- actions -----------------------------------------------------------

    async def _delete_message(self, case: Case, message: discord.Message | None = None) -> None:
        try:
            if message is None:
                channel = self.client.get_channel(case.channel_id) or await self.client.fetch_channel(case.channel_id)
                message = await channel.fetch_message(case.message_id)
            await message.delete()
            case.message_deleted = True
            case.actions.append("message deleted")
        except discord.NotFound:
            case.message_deleted = True
            case.actions.append("message was already gone")
        except discord.HTTPException as error:
            case.actions.append(f"could not delete message ({error.status})")

    async def _review_timeout(self, case: Case, member: discord.Member) -> None:
        try:
            await member.timeout(REVIEW_TIMEOUT, reason=f"Case #{case.id}: waiting for moderator review")
            case.timed_out = True
            case.status = "review"
            case.actions.append("timed out until a moderator reviews")
        except discord.HTTPException as error:
            # Usually the member's role is above the bot's. The case still needs review.
            case.status = "review"
            case.actions.append(f"could not time out ({error.status}); review needed")

    async def _dm(self, member: discord.abc.User, case: Case, number: int) -> None:
        guild = self.client.get_guild(case.guild_id)
        server = guild.name if guild else "the server"
        lines = [
            f"**You received a warning in {server}** (case #{case.id}, warning {number} of {self.settings.mod_warn_limit}).",
            f"Reason: {case.reasons[0] if case.reasons else LABEL_NAMES.get(case.label, case.label)}",
        ]
        if case.status == "review":
            lines.append("You are timed out until a moderator reviews this case.")
        elif number + 1 >= self.settings.mod_warn_limit:
            lines.append("One more warning will time you out until a moderator reviews your case.")
        lines.append("If you think this was a mistake, or your account was hacked, a moderator will review it.")
        try:
            await member.send("\n".join(lines))
        except discord.HTTPException:
            pass  # DMs closed; the case report still has everything

    async def _post_report(self, case: Case, images=()) -> None:
        channel = self._log_channel()
        if channel is None:
            return
        embed = build_embed(case, self.warning_count(case.guild_id, case.user_id), self.settings)
        files = [
            discord.File(io.BytesIO(i.data), filename=f"case{case.id}-{n}.{i.media_type.split('/')[1]}", spoiler=True)
            for n, i in enumerate(images, start=1)
        ]
        try:
            sent = await channel.send(embed=embed, view=build_view(case), files=files)
        except discord.HTTPException:
            log.exception("Could not post case #%s to the mod log", case.id)
            return
        case.log_channel_id, case.log_message_id = channel.id, sent.id
        self.store.save(case)

    def _log_channel(self) -> discord.abc.Messageable | None:
        if not self.settings.mod_log_channel_id:
            return None
        channel = self.client.get_channel(self.settings.mod_log_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("MOD_LOG_CHANNEL_ID %s is not a text channel the bot can see", self.settings.mod_log_channel_id)
            return None
        return channel

    # --- moderator decisions -------------------------------------------------

    async def handle_button(self, interaction: discord.Interaction) -> None:
        parsed = parse_custom_id((interaction.data or {}).get("custom_id", ""))
        if not parsed:
            return
        case_id, action = parsed
        base_action = action.removesuffix("_confirm")
        case = self.store.get(case_id)
        if case is None or base_action not in ACTIONS:
            await interaction.response.send_message("That case no longer exists.", ephemeral=True)
            return

        permission = ACTIONS[base_action][2]
        if not isinstance(interaction.user, discord.Member) or not getattr(interaction.user.guild_permissions, permission):
            await interaction.response.send_message(f"You need the **{permission.replace('_', ' ')}** permission.", ephemeral=True)
            return
        if not case.is_open:
            await interaction.response.send_message(f"Case #{case.id} is already closed: {case.decision}.", ephemeral=True)
            return

        if action == "ban":
            # Bans can't be undone by the bot, so ask once more.
            confirm = discord.ui.View(timeout=60)
            confirm.add_item(discord.ui.Button(label="Yes, ban this member", style=discord.ButtonStyle.danger,
                                               custom_id=custom_id(case.id, "ban_confirm")))
            await interaction.response.send_message(
                f"Ban <@{case.user_id}> for case #{case.id}? Their messages from the last 24 hours will be removed.",
                view=confirm, ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        moderator = interaction.user
        guild = interaction.guild
        note = await self._decide(case, action, guild, moderator)
        self.store.save(case)
        await self._refresh_report(case)
        await interaction.followup.send(note, ephemeral=True)

    async def _decide(self, case: Case, action: str, guild: discord.Guild, moderator: discord.Member) -> str:
        member = guild.get_member(case.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(case.user_id)
            except discord.HTTPException:
                member = None

        if action == "delete":
            await self._delete_message(case)
            case.actions[-1] += f" (by {moderator})"
            return f"Case #{case.id}: {case.actions[-1]}."

        if action == "timeout":
            if member is None:
                return "That member is no longer in the server."
            await self._review_timeout(case, member)
            case.actions[-1] += f" (by {moderator})"
            return f"Case #{case.id}: {case.actions[-1]}."

        if action == "ban_confirm":
            try:
                await guild.ban(
                    discord.Object(id=case.user_id),
                    reason=f"Case #{case.id} ({case.label}), banned by {moderator}",
                    delete_message_seconds=86400,
                )
            except discord.HTTPException as error:
                return f"Could not ban ({error.status}). Check the bot's role is above theirs and it has Ban Members."
            self._close(case, f"banned by {moderator}", moderator)
            return f"Case #{case.id}: member banned."

        if action == "lift":
            await self._untimeout(case, member, moderator)
            self._close(case, f"timeout lifted by {moderator}, warning kept", moderator)
            return f"Case #{case.id}: timeout lifted, warning kept."

        if action == "dismiss":
            self.store.revoke_warnings_for_case(case.id)
            await self._untimeout(case, member, moderator)
            self._close(case, f"not a violation (dismissed by {moderator}), warning removed", moderator)
            return f"Case #{case.id}: dismissed and warning removed."

        return "Unknown action."

    async def _untimeout(self, case: Case, member: discord.Member | None, moderator) -> None:
        if not case.timed_out or member is None:
            return
        try:
            await member.timeout(None, reason=f"Case #{case.id} reviewed by {moderator}")
            case.timed_out = False
            case.actions.append(f"timeout lifted by {moderator}")
        except discord.HTTPException as error:
            case.actions.append(f"could not lift timeout ({error.status})")

    @staticmethod
    def _close(case: Case, decision: str, moderator: discord.abc.User) -> None:
        case.status = "closed"
        case.decision = decision
        case.decided_by = moderator.id
        case.decided_at = time.time()

    async def _refresh_report(self, case: Case) -> None:
        if not case.log_channel_id or not case.log_message_id:
            return
        channel = self.client.get_channel(case.log_channel_id)
        if channel is None:
            return
        try:
            message = channel.get_partial_message(case.log_message_id)
            embed = build_embed(case, self.warning_count(case.guild_id, case.user_id), self.settings)
            await message.edit(embed=embed, view=build_view(case))
        except discord.HTTPException:
            log.exception("Could not update the report for case #%s", case.id)

    # --- keeping review timeouts in place ------------------------------------

    async def renew_review_timeouts(self) -> int:
        """Re-apply timeouts that are close to Discord's 28-day limit, so nobody is
        released before a moderator decides. Returns how many were renewed."""
        renewed = 0
        now = discord.utils.utcnow()
        for case in self.store.cases_in_review():
            guild = self.client.get_guild(case.guild_id)
            if guild is None:
                continue
            member = guild.get_member(case.user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(case.user_id)
                except discord.HTTPException:
                    continue  # left the server; the case stays open
            until = member.timed_out_until
            if until is None or until <= now:
                if until is None:
                    # A moderator removed it by hand in Discord; respect that.
                    case.timed_out = False
                    case.actions.append("timeout was removed outside the bot")
                    self.store.save(case)
                    await self._refresh_report(case)
                    continue
            elif until - now > RENEW_WHEN_LEFT:
                continue
            try:
                await member.timeout(REVIEW_TIMEOUT, reason=f"Case #{case.id}: still waiting for review")
                renewed += 1
            except discord.HTTPException:
                log.warning("Could not renew the timeout for case #%s", case.id)
        return renewed

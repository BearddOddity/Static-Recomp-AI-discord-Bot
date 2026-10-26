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

# A new violation joins the member's case if it's still waiting for review, or was
# opened in the last few minutes, so a burst of cross-posts makes one report.
MERGE_WINDOW_SECONDS = 600
# Which label a merged case shows when it holds several kinds of violation.
LABEL_RANK = {"manual": 0, "rule_violation": 1, "piracy": 2, "scam": 3, "hate_speech": 4}
AUTOMOD_DEDUPE_SECONDS = 30
AUTOMOD_RULE_CACHE_SECONDS = 3600

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
        self._automod_seen: dict[tuple, float] = {}
        self._automod_rules: dict[int, tuple[float, bool]] = {}

    def warning_count(self, guild_id: int, user_id: int) -> int:
        return len(self.store.active_warnings(guild_id, user_id, self.settings.mod_warn_expiry_days))

    # --- opening cases -----------------------------------------------------

    async def open_from_message(self, message: discord.Message, post: Post, verdict: Verdict) -> Case:
        severe = is_severe(verdict, self.settings)
        existing = self.store.recent_open_case(message.guild.id, message.author.id, MERGE_WINDOW_SECONDS)
        if existing:
            await self._add_violation(existing, message.author, verdict.label, list(verdict.reasons), severe, message)
            return existing
        case = self.store.create_case(
            message.guild.id, message.author.id, verdict.label, list(verdict.reasons),
            channel_id=message.channel.id, message_id=message.id, confidence=verdict.confidence,
            content=message.content, urls=list(verdict.urls),
        )
        await self._apply(case, message.author, severe, message, post.images)
        return case

    async def open_from_automod(self, execution: discord.AutoModAction) -> Case | None:
        """Discord's AutoMod caught something. Its "Slurs" filter (and any rules listed in
        MOD_HATE_AUTOMOD_RULE_IDS) count as hate speech: timeout and a case for review."""
        if not await self._is_hate_rule(execution):
            return None
        # AutoMod sends one event per action it took (block, alert, ...); handle once.
        now = time.monotonic()
        key = (execution.guild_id, execution.user_id, execution.rule_id, execution.content)
        self._automod_seen = {k: t for k, t in self._automod_seen.items() if now - t < AUTOMOD_DEDUPE_SECONDS}
        if key in self._automod_seen:
            return None
        self._automod_seen[key] = now

        guild = execution.guild
        member = execution.member
        if member is None:
            try:
                member = await guild.fetch_member(execution.user_id)
            except discord.HTTPException:
                return None
        if member.bot or member.guild_permissions.manage_messages:
            return None

        matched = execution.matched_keyword or execution.matched_content or "a filtered word"
        reasons = ["hate speech caught by Discord's AutoMod slur filter", f"matched: ||{matched}||"]
        existing = self.store.recent_open_case(guild.id, member.id, MERGE_WINDOW_SECONDS)
        if existing:
            await self._add_violation(existing, member, "hate_speech", reasons, True, None, execution.channel_id)
            return existing

        case = self.store.create_case(
            guild.id, member.id, "hate_speech", reasons, channel_id=execution.channel_id,
            message_id=execution.message_id, confidence=1.0, content=execution.content,
        )
        if execution.message_id is None:
            case.message_deleted = True
            case.actions.append("AutoMod blocked the message")
        await self._apply(case, member, True, None, [])
        return case

    async def _is_hate_rule(self, execution: discord.AutoModAction) -> bool:
        if execution.rule_id in self.settings.mod_hate_automod_rule_ids:
            return True
        if execution.rule_trigger_type != discord.AutoModRuleTriggerType.keyword_preset:
            return False
        cached = self._automod_rules.get(execution.rule_id)
        if cached and time.monotonic() - cached[0] < AUTOMOD_RULE_CACHE_SECONDS:
            return cached[1]
        try:
            rule = await execution.fetch_rule()
        except discord.HTTPException:
            log.warning("Can't read AutoMod rule %s; give the bot the Manage Server permission", execution.rule_id)
            return False
        slurs = bool(rule.trigger.presets and rule.trigger.presets.slurs)
        self._automod_rules[execution.rule_id] = (time.monotonic(), slurs)
        return slurs

    async def _add_violation(
        self, case: Case, member: discord.abc.User, label: str, reasons: list[str], severe: bool,
        message: discord.Message | None, channel_id: int | None = None,
    ) -> None:
        """Fold another violation into an open case instead of opening a new report."""
        steps = plan(label, severe, self.warning_count(case.guild_id, case.user_id), self.settings)
        name = LABEL_NAMES.get(label, label)
        self.store.add_warning(case.guild_id, case.user_id, reasons[0] if reasons else name, case.id)
        channel_id = message.channel.id if message is not None else channel_id
        where = f" in <#{channel_id}>" if channel_id else ""
        case.actions.append(f"another {name.lower()} message{where}: warning {steps.warning_number}")
        case.reasons += [r for r in reasons if r not in case.reasons]
        if LABEL_RANK.get(label, 0) > LABEL_RANK.get(case.label, 0):
            case.label = label

        if steps.delete_message and message is not None:
            try:
                await message.delete()
                case.actions.append("that message was deleted")
            except discord.HTTPException as error:
                case.actions.append(f"could not delete that message ({error.status})")
        if steps.review_timeout and not case.timed_out and isinstance(member, discord.Member):
            await self._review_timeout(case, member)
            if self.settings.mod_dm_warnings:
                await self._dm(member, case, steps.warning_number)

        self.store.save(case)
        await self._refresh_report(case)

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

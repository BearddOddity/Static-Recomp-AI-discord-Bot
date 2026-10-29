"""Appeals: a warned member presses Appeal in the warning DM, explains, and moderators
accept or reject it. Accepting works like "Not a violation": the warning is removed and
any timeout lifted. Every button's custom_id is "appeal:<case id>:<action>", so buttons
keep working after a restart (appeals live in SQLite).
"""

import logging
import time

import discord

from .cases import Appeal, Case, CaseStore
from .review import APPEAL_PREFIX, CaseManager

log = logging.getLogger("bot.moderation")

PREFIX = APPEAL_PREFIX
MAX_APPEAL_CHARS = 1000


def custom_id(case_id: int, action: str) -> str:
    return f"{PREFIX}:{case_id}:{action}"


def parse_custom_id(value: str) -> tuple[int, str] | None:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1].isdigit() or parts[2] not in ("open", "accept", "reject"):
        return None
    return int(parts[1]), parts[2]


def appealable(case: Case) -> bool:
    """A case can be appealed while it is open, or after it closed with the warning kept.
    Bans and "not a violation" decisions are final here."""
    if case.is_open:
        return True
    return bool(case.decision) and "warning kept" in case.decision


def decision_view(case_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Accept appeal", emoji="✅", style=discord.ButtonStyle.success,
                                    custom_id=custom_id(case_id, "accept")))
    view.add_item(discord.ui.Button(label="Reject appeal", emoji="❌", style=discord.ButtonStyle.secondary,
                                    custom_id=custom_id(case_id, "reject")))
    return view


def appeal_embed(appeal: Appeal, case: Case, text: str | None = None) -> discord.Embed:
    """text is the appeal's words, passed in when the database no longer has them."""
    colors = {"open": discord.Color.gold(), "accepted": discord.Color.green(), "rejected": discord.Color.dark_grey()}
    body = appeal.text if text is None else text
    embed = discord.Embed(
        title=f"✉️ Appeal for case #{case.id}",
        description="> " + "\n> ".join(body.replace("@", "@​").splitlines())[:1800],
        color=colors.get(appeal.status, discord.Color.gold()),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"<@{case.user_id}>", inline=True)
    embed.add_field(name="Original reason", value=(case.reasons[0] if case.reasons else case.label)[:500], inline=True)
    if case.log_channel_id and case.log_message_id:
        embed.add_field(
            name="Case report",
            value=f"[Jump to case #{case.id}](https://discord.com/channels/{case.guild_id}/{case.log_channel_id}/{case.log_message_id})",
            inline=False,
        )
    if appeal.status == "open":
        embed.set_footer(text="Accepting removes the warning and lifts any timeout. Rejecting keeps the case as it is.")
    else:
        embed.set_footer(text=f"{appeal.status.title()} by a moderator")
    return embed


class AppealModal(discord.ui.Modal, title="Appeal this warning"):
    def __init__(self, manager: "AppealManager", case_id: int):
        super().__init__()
        self.manager, self.case_id = manager, case_id
        self.box = discord.ui.TextInput(
            label="Why was this a mistake?",
            style=discord.TextStyle.paragraph,
            placeholder="Explain what happened. A moderator will read this.",
            min_length=10,
            max_length=MAX_APPEAL_CHARS,
        )
        self.add_item(self.box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.manager.submit(interaction, self.case_id, self.box.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Appeal form failed for case %s", self.case_id)
        if not interaction.response.is_done():
            await interaction.response.send_message("That didn't work. Please try again later.", ephemeral=True)


class AppealManager:
    def __init__(self, client: discord.Client, store: CaseStore, cases: CaseManager):
        self.client, self.store, self.cases = client, store, cases

    async def handle_button(self, interaction: discord.Interaction) -> None:
        parsed = parse_custom_id((interaction.data or {}).get("custom_id", ""))
        if not parsed:
            return
        case_id, action = parsed
        if action == "open":
            await self._open(interaction, case_id)
        else:
            await self._decide(interaction, case_id, accept=action == "accept")

    # --- the member's side -------------------------------------------------

    async def _open(self, interaction: discord.Interaction, case_id: int) -> None:
        case = self.store.get(case_id)
        if case is None or interaction.user.id != case.user_id:
            await interaction.response.send_message("That warning isn't yours to appeal.", ephemeral=True)
            return
        if not self.cases.settings.appeals_enabled:
            await interaction.response.send_message(
                "Appeals are turned off in this server. Please message a moderator directly.", ephemeral=True
            )
            return
        existing = self.store.get_appeal(case_id)
        if existing:
            status = {"open": "is waiting for a moderator", "accepted": "was accepted", "rejected": "was rejected"}
            await interaction.response.send_message(f"Your appeal for case #{case_id} {status[existing.status]}.", ephemeral=True)
            return
        if not appealable(case):
            await interaction.response.send_message("This case is already closed, so it can't be appealed.", ephemeral=True)
            return
        await interaction.response.send_modal(AppealModal(self, case_id))

    async def submit(self, interaction: discord.Interaction, case_id: int, text: str) -> None:
        case = self.store.get(case_id)
        if case is None or interaction.user.id != case.user_id or not appealable(case):
            await interaction.response.send_message("That warning can't be appealed.", ephemeral=True)
            return
        appeal = self.store.create_appeal(case_id, interaction.user.id, text.strip()[:MAX_APPEAL_CHARS])
        if appeal is None:
            await interaction.response.send_message("You already appealed this warning.", ephemeral=True)
            return
        channel = self.cases.log_channel()
        if channel is None:
            await interaction.response.send_message(
                "Your appeal is saved, but this server has no mod log channel yet, so no moderator was told. "
                "Please message a moderator directly.", ephemeral=True,
            )
            return
        try:
            sent = await channel.send(embed=appeal_embed(appeal, case), view=decision_view(case_id))
        except discord.HTTPException:
            log.exception("Could not post the appeal for case #%s", case_id)
            await interaction.response.send_message("I couldn't reach the moderators. Please message one directly.", ephemeral=True)
            return
        appeal.log_channel_id, appeal.log_message_id = channel.id, sent.id
        self.store.save_appeal(appeal)
        case.actions.append("appeal sent by the member")
        self.store.save(case)
        await self.cases.refresh_report(case)
        await interaction.response.send_message(
            f"✅ Your appeal for case #{case_id} was sent to the moderators. You'll get a message here when they decide.",
            ephemeral=True,
        )

    # --- the moderators' side ----------------------------------------------

    async def _decide(self, interaction: discord.Interaction, case_id: int, accept: bool) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.moderate_members:
            await interaction.response.send_message("You need the **moderate members** permission.", ephemeral=True)
            return
        appeal, case = self.store.get_appeal(case_id), self.store.get(case_id)
        if appeal is None or case is None:
            await interaction.response.send_message("That appeal no longer exists.", ephemeral=True)
            return
        if appeal.status != "open":
            await interaction.response.send_message(f"This appeal was already {appeal.status}.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        appeal.status = "accepted" if accept else "rejected"
        appeal.decided_by, appeal.decided_at = member.id, time.time()
        note = ""
        if accept:
            if case.is_open or "warning kept" in (case.decision or ""):
                note = await self.cases.dismiss(case, interaction.guild, member)
            else:
                note = f"Case #{case.id} was already closed."
        else:
            case.actions.append(f"appeal rejected by {member}")
            self.store.save(case)
            await self.cases.refresh_report(case)
        self.store.save_appeal(appeal)

        # The appeal's words are only in the post, so read them back from it.
        embeds = interaction.message.embeds
        posted = (embeds[0].description or "") if embeds else ""
        words = "\n".join(line[2:] if line.startswith("> ") else line for line in posted.splitlines())
        try:
            await interaction.message.edit(embed=appeal_embed(appeal, case, text=words.replace("​", "")), view=None)
        except discord.HTTPException:
            log.warning("Could not update the appeal message for case #%s", case.id)
        await self._tell_member(case, appeal, accept)
        await interaction.followup.send(note or f"Appeal for case #{case.id} {appeal.status}.", ephemeral=True)

    async def _tell_member(self, case: Case, appeal: Appeal, accept: bool) -> None:
        user = self.client.get_user(case.user_id)
        if user is None:
            try:
                user = await self.client.fetch_user(case.user_id)
            except discord.HTTPException:
                return
        guild = self.client.get_guild(case.guild_id)
        server = guild.name if guild else "the server"
        text = (
            f"✅ A moderator accepted your appeal in **{server}**. The warning was removed."
            if accept else
            f"❌ A moderator reviewed your appeal in **{server}** and the warning stays. "
            "If you still disagree, message a moderator directly."
        )
        try:
            await user.send(text)
        except discord.HTTPException:
            pass  # DMs closed

"""Member-facing extras: 👍/👎 feedback on answers, private help threads, and the welcome
message. Button custom_ids are "fb:up", "fb:down" and "tk:<thread id>:close|human", so
they keep working after a restart (everything is stored in SQLite).
"""

import logging

import discord

from .moderation.cases import CaseStore, Ticket

log = logging.getLogger("bot.community")

FEEDBACK_UP, FEEDBACK_DOWN = "fb:up", "fb:down"
TICKET_PREFIX = "tk"
MAX_THREAD_NAME = 90


# --- 👍/👎 feedback ---------------------------------------------------------

def feedback_view(up: int = 0, down: int = 0) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=str(up) if up else None, emoji="👍", style=discord.ButtonStyle.secondary,
                                    custom_id=FEEDBACK_UP))
    view.add_item(discord.ui.Button(label=str(down) if down else None, emoji="👎", style=discord.ButtonStyle.secondary,
                                    custom_id=FEEDBACK_DOWN))
    return view


async def handle_feedback(store: CaseStore, interaction: discord.Interaction) -> None:
    message = interaction.message
    if message is None or not store.has_answer(message.id):
        await interaction.response.send_message("That answer is too old to rate.", ephemeral=True)
        return
    vote = 1 if (interaction.data or {}).get("custom_id") == FEEDBACK_UP else -1
    store.vote(message.id, interaction.user.id, vote)
    up, down = store.vote_counts(message.id)
    await interaction.response.edit_message(view=feedback_view(up, down))


def downvoted_embed(store: CaseStore, guild_id: int) -> discord.Embed:
    answers = store.downvoted_answers(guild_id)
    embed = discord.Embed(title="👎 Answers members marked as unhelpful", color=discord.Color.orange())
    if not answers:
        embed.description = "None yet. When members press 👎 on an answer, it shows up here with the question."
        return embed
    for answer in answers:
        link = f"https://discord.com/channels/{answer.guild_id}/{answer.channel_id}/{answer.message_id}"
        embed.add_field(
            name=f"👎 {answer.down} · 👍 {answer.up}"[:256],
            value=(f"**Q:** {answer.question[:200]}\n**A:** {answer.answer[:300]}\n[Jump to answer]({link})")[:1024],
            inline=False,
        )
    embed.set_footer(text="Questions the docs don't cover well are good candidates for new documentation.")
    return embed


# --- help threads ------------------------------------------------------------

def ticket_view(thread_id: int, *, human: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Close this thread", emoji="🔒", style=discord.ButtonStyle.secondary,
                                    custom_id=f"{TICKET_PREFIX}:{thread_id}:close"))
    if not human:
        view.add_item(discord.ui.Button(label="Get a human", emoji="🙋", style=discord.ButtonStyle.primary,
                                        custom_id=f"{TICKET_PREFIX}:{thread_id}:human"))
    return view


def parse_ticket_id(value: str) -> tuple[int, str] | None:
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != TICKET_PREFIX or not parts[1].isdigit() or parts[2] not in ("close", "human"):
        return None
    return int(parts[1]), parts[2]


def thread_name(display_name: str, topic: str) -> str:
    name = f"help-{display_name}-{topic}".lower()
    name = "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip().replace(" ", "-")
    while "--" in name:
        name = name.replace("--", "-")
    return (name or "help")[:MAX_THREAD_NAME]


def bot_answers_in(ticket: Ticket | None, author_id: int) -> bool:
    """The bot keeps answering the member who opened a thread until a moderator takes over."""
    return ticket is not None and ticket.status == "open" and ticket.user_id == author_id


async def open_help_thread(bot, interaction: discord.Interaction, question: str) -> None:
    """The /help command: a private thread with the member, answered by the bot."""
    settings, store = bot.settings, bot.store
    if not settings.help_enabled:
        await interaction.response.send_message("Help threads are turned off in this server.", ephemeral=True)
        return
    existing = store.open_ticket_for(interaction.guild_id, interaction.user.id)
    if existing:
        thread = interaction.guild.get_thread(existing.thread_id)
        if thread is not None and not thread.archived:
            await interaction.response.send_message(f"You already have an open help thread: {thread.mention}", ephemeral=True)
            return
        store.set_ticket_status(existing.thread_id, "closed")  # archived or deleted: start fresh

    parent = interaction.guild.get_channel(settings.help_channel_id) if settings.help_channel_id else interaction.channel
    if not isinstance(parent, discord.TextChannel):
        await interaction.response.send_message(
            "Use /help in a normal text channel, or ask a moderator to set a Help channel in /settings.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        thread = await parent.create_thread(
            name=thread_name(interaction.user.display_name, question),
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=1440,
            reason=f"Help thread for {interaction.user}",
        )
        await thread.add_user(interaction.user)
    except discord.Forbidden:
        await interaction.followup.send(
            "I can't create private threads here. A moderator needs to give me **Create Private Threads** "
            "(and **Manage Threads**) in that channel.", ephemeral=True,
        )
        return
    except discord.HTTPException:
        log.exception("Could not open a help thread")
        await interaction.followup.send("I couldn't open a thread. Please try again later.", ephemeral=True)
        return

    store.create_ticket(thread.id, interaction.guild_id, interaction.user.id, question)
    await interaction.followup.send(f"Your private help thread is ready: {thread.mention}", ephemeral=True)
    await thread.send(
        f"{interaction.user.mention} I'll answer here, and only you and the moderators can see this thread. "
        "Press **Get a human** at any time to bring in a moderator.",
        view=ticket_view(thread.id),
        allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
    )
    await bot.answer_in_thread(thread, interaction.user, question)


async def handle_ticket_button(bot, interaction: discord.Interaction) -> None:
    parsed = parse_ticket_id((interaction.data or {}).get("custom_id", ""))
    if not parsed:
        return
    thread_id, action = parsed
    store = bot.store
    ticket = store.get_ticket(thread_id)
    user = interaction.user
    if ticket is None or ticket.status == "closed":
        await interaction.response.send_message("This help thread is already closed.", ephemeral=True)
        return
    is_staff = isinstance(user, discord.Member) and (
        user.guild_permissions.manage_threads or user.guild_permissions.moderate_members
    )
    if user.id != ticket.user_id and not is_staff:
        await interaction.response.send_message("Only the member who opened this thread, or a moderator, can do that.", ephemeral=True)
        return

    thread = interaction.channel
    if action == "close":
        store.set_ticket_status(thread_id, "closed")
        await interaction.response.edit_message(view=None)
        await thread.send(f"🔒 Closed by {user.mention}. Use `/help` to open a new thread any time.",
                          allowed_mentions=discord.AllowedMentions.none())
        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass  # it still archives itself after a day
        return

    if ticket.status == "human":
        await interaction.response.send_message("A moderator was already asked to join.", ephemeral=True)
        return
    store.set_ticket_status(thread_id, "human")
    await interaction.response.edit_message(view=ticket_view(thread_id, human=True))
    role_id = bot.settings.help_staff_role_id
    ping = f"<@&{role_id}> " if role_id else ""
    await thread.send(
        f"🙋 {ping}{user.mention} would like a human to look at this. I'll stop answering here so you can talk.",
        allowed_mentions=discord.AllowedMentions(roles=[discord.Object(id=role_id)] if role_id else [], users=[user]),
    )
    await bot.notify_mods(f"🙋 Help requested by <@{ticket.user_id}> in {thread.mention}: {ticket.topic[:200]}")


# --- welcome message ---------------------------------------------------------

def render_welcome(template: str, member_mention: str, server: str, rules: str) -> str:
    """Fills {user}, {server} and {rules}. Plain replacement, so stray braces are harmless."""
    return (
        template.replace("{user}", member_mention)
        .replace("{server}", server)
        .replace("{rules}", rules)
        .replace("{help}", "`/help`")
    )[:1900]

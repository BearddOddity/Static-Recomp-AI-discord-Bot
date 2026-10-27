"""The /settings page: private (only the person who opened it sees it), with menus and
pop-up forms. What can be changed is listed in runtime_settings.FIELDS."""

import asyncio
import logging

import discord

from . import runtime_settings as rs
from .updater import RESTART_EXIT_CODE

log = logging.getLogger("bot.settings")

NONE_CHOICE = "__none__"
MODAL_PLACEHOLDER_MAX = 100


def can_use(interaction: discord.Interaction, field: rs.Field | None = None) -> bool:
    """Manage Server opens the page; keys, providers and the personality also need
    Administrator (or being the server owner). Checked on every click, not just on open,
    because a server can change who sees a command."""
    member, guild = interaction.user, interaction.guild
    if guild is None or not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    if field is not None and field.admin:
        return perms.administrator or member.id == guild.owner_id
    return perms.manage_guild or perms.administrator or member.id == guild.owner_id


def _value(bot, field: rs.Field) -> str:
    if field.live:
        return f"{bot.min_confidence:.2f}" if field.key == "MOD_MIN_CONFIDENCE" else str(bot.chat_context)
    return rs.current(field.key)


def _line(bot, field: rs.Field) -> str:
    lock = " 🔒" if field.admin else ""
    pending = " ⏳" if field.key in bot.settings_pending else ""
    return f"**{field.label}**{lock}: {rs.display(field, _value(bot, field))}{pending}"


def page(bot, group: str | None, notice: str = "") -> tuple[discord.Embed, discord.ui.View]:
    embed = discord.Embed(title="⚙️ Settings" + (f" · {group}" if group else ""), color=0x5865F2)
    lines = [notice] if notice else []
    if group is None:
        lines += [f"**{g}**: {rs.GROUP_HELP[g]}" for g in rs.GROUPS]
        lines.append("\nPick a group below. Only you can see this page.")
    else:
        lines += [_line(bot, f) for f in rs.fields_in(group)]
        lines.append("\n🔒 needs Administrator · ⏳ saved, applies after a restart")
    if bot.settings_pending:
        lines.append("\n⏳ **Restart needed** to apply your changes.")
    embed.description = "\n".join(lines)[:4000]
    view = HomeView(bot) if group is None else GroupView(bot, group)
    return embed, view


class _Base(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.bot = bot
        self.owner_id: int | None = None  # the person who opened the page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is None:
            self.owner_id = interaction.user.id
        if interaction.user.id != self.owner_id or not can_use(interaction):
            await interaction.response.send_message("This settings page isn't yours to use.", ephemeral=True)
            return False
        return True

    def add_restart_buttons(self) -> None:
        if self.bot.settings_pending:
            self.add_item(RestartButton())


class GroupSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Choose a group…",
            options=[discord.SelectOption(label=g, value=g, description=rs.GROUP_HELP[g][:100]) for g in rs.GROUPS],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        embed, view = page(self.view.bot, self.values[0])
        view.owner_id = self.view.owner_id
        await interaction.response.edit_message(embed=embed, view=view)


class HomeView(_Base):
    def __init__(self, bot):
        super().__init__(bot)
        self.add_item(GroupSelect())
        self.add_restart_buttons()
        self.add_item(ResetAllButton())


class FieldSelect(discord.ui.Select):
    def __init__(self, bot, group: str):
        options = []
        for field in rs.fields_in(group):
            shown = rs.display(field, _value(bot, field)).replace("<#", "#").replace(">", "")
            options.append(
                discord.SelectOption(
                    label=(("🔒 " if field.admin else "") + field.label)[:100],
                    value=field.key,
                    description=f"{shown}"[:100] if not field.secret else field.help[:100],
                )
            )
        super().__init__(placeholder="Choose a setting to change…", options=options[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        field = rs.FIELDS[self.values[0]]
        if not can_use(interaction, field):
            await interaction.response.send_message(
                "Changing that needs the Administrator permission (or being the server owner).", ephemeral=True
            )
            return
        if field.kind in ("choice", "bool"):
            view = ChoiceView(self.view.bot, field)
            view.owner_id = self.view.owner_id
            await interaction.response.edit_message(
                embed=discord.Embed(title=f"⚙️ {field.label}", description=field.help, color=0x5865F2), view=view
            )
            return
        await interaction.response.send_modal(FieldModal(self.view.bot, field, self.view.owner_id))


class GroupView(_Base):
    def __init__(self, bot, group: str):
        super().__init__(bot)
        self.group = group
        self.add_item(FieldSelect(bot, group))
        self.add_item(BackButton())
        self.add_restart_buttons()


class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        embed, view = page(self.view.bot, None)
        view.owner_id = self.view.owner_id
        await interaction.response.edit_message(embed=embed, view=view)


class ChoiceSelect(discord.ui.Select):
    def __init__(self, field: rs.Field):
        if field.kind == "bool":
            options = [discord.SelectOption(label="On", value="true"), discord.SelectOption(label="Off", value="false")]
        else:
            options = [
                discord.SelectOption(label=c or "Off (none)", value=c or NONE_CHOICE) for c in field.choices
            ]
        super().__init__(placeholder="Choose…", options=options)
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0]
        await save_and_show(self.view.bot, interaction, self.field, "" if raw == NONE_CHOICE else raw, self.view.owner_id)


class ChoiceView(_Base):
    def __init__(self, bot, field: rs.Field):
        super().__init__(bot)
        self.field = field
        self.add_item(ChoiceSelect(field))
        self.add_item(CancelButton(field.group))


class CancelButton(discord.ui.Button):
    def __init__(self, group: str):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)
        self.group = group

    async def callback(self, interaction: discord.Interaction) -> None:
        embed, view = page(self.view.bot, self.group)
        view.owner_id = self.view.owner_id
        await interaction.response.edit_message(embed=embed, view=view)


class FieldModal(discord.ui.Modal):
    def __init__(self, bot, field: rs.Field, owner_id: int | None):
        super().__init__(title=field.label[:45])
        self.bot, self.field, self.owner_id = bot, field, owner_id
        current = "" if field.secret else _value(bot, field)
        hint = "Paste the new key. Leave empty to cancel." if field.secret else field.help
        self.box = discord.ui.TextInput(
            label=field.label[:45],
            style=discord.TextStyle.paragraph if field.kind == "paragraph" else discord.TextStyle.short,
            default=current[: min(field.max_length, 4000)] or None,
            placeholder=hint[:MODAL_PLACEHOLDER_MAX] or None,
            required=False,
            max_length=min(field.max_length, 4000) if not field.secret else 400,
        )
        self.add_item(self.box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not can_use(interaction, self.field):
            await interaction.response.send_message("You can't change that setting.", ephemeral=True)
            return
        raw = self.box.value or ""
        if self.field.secret and not raw.strip():
            embed, view = page(self.bot, self.field.group, "Nothing changed.")
            view.owner_id = self.owner_id
            await interaction.response.edit_message(embed=embed, view=view)
            return
        await save_and_show(self.bot, interaction, self.field, raw, self.owner_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Settings form failed for %s", self.field.key)  # never logs the value
        if not interaction.response.is_done():
            await interaction.response.send_message("That didn't work. Check the bot logs.", ephemeral=True)


async def save_and_show(bot, interaction: discord.Interaction, field: rs.Field, raw: str, owner_id: int | None) -> None:
    def show(notice: str) -> tuple[discord.Embed, discord.ui.View]:
        embed, view = page(bot, field.group, notice)
        view.owner_id = owner_id
        return embed, view

    try:
        value = rs.validate(field, raw)
        if field.key in ("MOD_LOG_CHANNEL_ID", "RULES_CHANNEL_ID") and value:
            channel = interaction.guild.get_channel(int(value))
            if channel is None:
                raise ValueError("I can't see a channel with that ID in this server.")
    except ValueError as error:
        embed, view = show(f"⚠️ {error}")
        await interaction.response.edit_message(embed=embed, view=view)
        return

    before = rs.display(field, _value(bot, field))
    if field.live:
        if not value:
            embed, view = show("⚠️ That setting can't be empty.")
            await interaction.response.edit_message(embed=embed, view=view)
            return
        if field.key == "MOD_MIN_CONFIDENCE":
            bot.set_tuning(confidence=float(value))
        else:
            bot.set_tuning(context=int(value))
        note = "✅ Applied now."
    else:
        saved = rs.load_overrides()
        saved[field.key] = value
        rs.save_overrides(saved)
        bot.settings_pending.add(field.key)
        note = "✅ Saved. Press **Restart now** to apply it."

    log.info("Setting %s changed by %s", field.key, interaction.user.id)  # never the value
    after = rs.display(field, _value(bot, field))
    change = "" if field.secret else f": {before} → {after}"
    await bot.notify_mods(f"⚙️ {interaction.user.mention} changed **{field.label}**{change}"
                          + (" (value hidden)" if field.secret else "")
                          + ("" if field.live else " (restart needed)"))
    embed, view = show(note)
    await interaction.response.edit_message(embed=embed, view=view)


class RestartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Restart now", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = self.view.bot
        await interaction.response.edit_message(
            content="🔄 Restarting to apply your settings. Back in about 20 seconds.", embed=None, view=None
        )
        await bot.notify_mods(f"🔄 {interaction.user.mention} restarted the bot to apply new settings.")
        bot.exit_code = RESTART_EXIT_CODE  # the start script and the service start it again
        asyncio.create_task(bot.close())


class ResetAllButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Reset all Discord settings", style=discord.ButtonStyle.secondary)
        self.armed = False

    async def callback(self, interaction: discord.Interaction) -> None:
        if not can_use(interaction, rs.FIELDS["OPENAI_COMPAT_API_KEY"]):
            await interaction.response.send_message("Only an administrator can reset all settings.", ephemeral=True)
            return
        if not self.armed:
            self.armed, self.label = True, "Click again to confirm"
            self.style = discord.ButtonStyle.danger
            await interaction.response.edit_message(view=self.view)
            return
        rs.save_overrides({})
        bot = self.view.bot
        bot.settings_pending.add("(reset)")
        await bot.notify_mods(f"⚙️ {interaction.user.mention} reset all Discord settings to the .env values (restart needed).")
        embed, view = page(bot, None, "✅ Reset. Your .env values apply after a restart.")
        view.owner_id = self.view.owner_id
        await interaction.response.edit_message(embed=embed, view=view)

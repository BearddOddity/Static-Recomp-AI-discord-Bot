"""Keeps a copy of each server's #rules channel, so moderation and /ask follow it."""

import logging

import discord

from .cases import CaseStore

log = logging.getLogger("bot.moderation")

MAX_RULES_CHARS = 6000
MAX_RULES_MESSAGES = 50


def message_text(message: discord.Message) -> str:
    """Rules are often posted as embeds, so read those as well as plain text."""
    parts = [message.content]
    for embed in message.embeds:
        parts += [embed.title or "", embed.description or ""]
        parts += [f"{f.name}\n{f.value}" for f in embed.fields]
    return "\n".join(p.strip() for p in parts if p and p.strip())


class RulesBook:
    def __init__(self, store: CaseStore, channel_id: int | None = None):
        self._store = store
        self._channel_id = channel_id
        self._rules: dict[int, str] = store.load_rules()

    def get(self, guild_id: int | None) -> str:
        return self._rules.get(guild_id, "") if guild_id else ""

    def channel_for(self, guild: discord.Guild) -> discord.TextChannel | None:
        """RULES_CHANNEL_ID if it's in this server, else the Community rules
        channel, else a text channel named "rules"."""
        if self._channel_id:
            channel = guild.get_channel(self._channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        if isinstance(guild.rules_channel, discord.TextChannel):
            return guild.rules_channel
        return discord.utils.find(lambda c: "rules" in c.name.lower(), guild.text_channels)

    def is_rules_channel(self, guild: discord.Guild | None, channel_id: int) -> bool:
        if guild is None:
            return False
        channel = self.channel_for(guild)
        return channel is not None and channel.id == channel_id

    async def refresh(self, guild: discord.Guild) -> str:
        channel = self.channel_for(guild)
        if channel is None:
            log.info("No rules channel found in %s", guild.name)
            return ""
        try:
            messages = [m async for m in channel.history(limit=MAX_RULES_MESSAGES, oldest_first=True)]
        except discord.HTTPException:
            log.exception("Could not read #%s in %s", channel.name, guild.name)
            return self.get(guild.id)

        text = "\n\n".join(t for t in (message_text(m) for m in messages) if t)[:MAX_RULES_CHARS]
        if text != self._rules.get(guild.id):
            self._rules[guild.id] = text
            self._store.save_rules(guild.id, channel.id, text)
            log.info("Loaded rules for %s from #%s (%d characters)", guild.name, channel.name, len(text))
        return text

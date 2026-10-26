"""Turns discord.py messages into the Discord-free Post the Moderator reviews."""

import logging
from datetime import datetime, timezone

import discord

from ..ai import Image
from ..config import Settings
from .engine import Post
from .files import MAX_ZIP_BYTES, zip_member_names

log = logging.getLogger("bot.moderation")

# Image types every vision provider here accepts. 5 MB is Anthropic's per-image limit.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4


def is_exempt(message: discord.Message, settings: Settings) -> bool:
    author = message.author
    if author.bot or message.guild is None or not isinstance(author, discord.Member):
        return True
    if author.guild_permissions.manage_messages:
        return True  # moderators
    return any(role.id in settings.mod_exempt_role_ids for role in author.roles)


async def to_post(message: discord.Message, rules: str = "") -> Post:
    images, names = [], []
    for attachment in message.attachments:
        names.append(attachment.filename)
        media_type = (attachment.content_type or "").split(";")[0]
        try:
            if media_type in IMAGE_TYPES and attachment.size <= MAX_IMAGE_BYTES and len(images) < MAX_IMAGES:
                images.append(Image(await attachment.read(), media_type))
            elif attachment.filename.lower().endswith(".zip") and attachment.size <= MAX_ZIP_BYTES:
                # Look inside, so game files can't hide in an archive.
                names += zip_member_names(attachment.filename, await attachment.read())
        except discord.HTTPException:
            log.warning("Could not download attachment %s", attachment.id)

    now = datetime.now(timezone.utc)
    author = message.author
    joined = getattr(author, "joined_at", None)
    return Post(
        text=message.content,
        author_id=author.id,
        channel_id=message.channel.id,
        images=images,
        attachment_count=len(message.attachments),
        attachment_names=names,
        account_age_days=(now - author.created_at).total_seconds() / 86400,
        member_age_hours=(now - joined).total_seconds() / 3600 if joined else None,
        rules=rules,
    )

"""Connects the Discord-free Moderator to discord.py messages and actions."""

import io
import logging
from datetime import datetime, timedelta, timezone

import discord

from ..ai import Image
from ..config import Settings
from .engine import Post, Verdict

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


async def to_post(message: discord.Message) -> Post:
    images = []
    for attachment in message.attachments:
        media_type = (attachment.content_type or "").split(";")[0]
        if media_type in IMAGE_TYPES and attachment.size <= MAX_IMAGE_BYTES and len(images) < MAX_IMAGES:
            try:
                images.append(Image(await attachment.read(), media_type))
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
        account_age_days=(now - author.created_at).total_seconds() / 86400,
        member_age_hours=(now - joined).total_seconds() / 3600 if joined else None,
    )


async def enforce(message: discord.Message, post: Post, verdict: Verdict, settings: Settings) -> None:
    action = settings.mod_action if verdict.label == "scam" else settings.mod_piracy_action
    done = []

    if action == "delete":
        try:
            await message.delete()
            done.append("deleted the message")
        except discord.HTTPException as error:
            done.append(f"could not delete ({error.status})")

    if verdict.label == "scam" and settings.mod_timeout_minutes > 0 and isinstance(message.author, discord.Member):
        try:
            await message.author.timeout(
                timedelta(minutes=settings.mod_timeout_minutes),
                reason=f"Scam post: {'; '.join(verdict.reasons[:2])}"[:500],
            )
            done.append(f"timed out the author for {settings.mod_timeout_minutes} min")
        except discord.HTTPException as error:
            done.append(f"could not time out ({error.status})")

    log.info(
        "Flagged %s by %s in #%s: %s (%s, %.2f) -> %s",
        verdict.label, message.author, message.channel, "; ".join(verdict.reasons),
        verdict.source, verdict.confidence, ", ".join(done) or "flag only",
    )
    await _report(message, post, verdict, done, settings)


async def _report(message: discord.Message, post: Post, verdict: Verdict, done: list[str], settings: Settings) -> None:
    if not settings.mod_log_channel_id:
        return
    channel = message.guild.get_channel(settings.mod_log_channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        log.warning("MOD_LOG_CHANNEL_ID %s is not a text channel the bot can see", settings.mod_log_channel_id)
        return

    title = {"scam": "Possible scam", "piracy": "Possible piracy"}.get(verdict.label, verdict.label)
    embed = discord.Embed(
        title=f"{title} ({verdict.confidence:.0%} confident)",
        description=(message.content[:1500] or "*(no text)*"),
        color=discord.Color.red() if verdict.label == "scam" else discord.Color.orange(),
        timestamp=message.created_at,
    )
    embed.add_field(name="Author", value=f"{message.author.mention} `{message.author}` ({message.author.id})", inline=False)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    embed.add_field(name="Action", value=", ".join(done) or "none (flag only)", inline=True)
    if "deleted the message" not in done:
        embed.add_field(name="Message", value=f"[Jump to message]({message.jump_url})", inline=True)
    embed.add_field(name="Why", value="\n".join(f"• {r}" for r in verdict.reasons[:8])[:1024] or "-", inline=False)
    if verdict.urls:
        embed.add_field(name="Links", value="\n".join(f"`{u}`" for u in verdict.urls[:5])[:1024], inline=False)
    embed.set_footer(text=f"Decided by {verdict.source} · rule score {verdict.score}")

    # Re-upload the images: the originals vanish if the message was deleted.
    files = [
        discord.File(io.BytesIO(image.data), filename=f"evidence-{n}.{image.media_type.split('/')[1]}", spoiler=True)
        for n, image in enumerate(post.images, start=1)
    ]
    try:
        await channel.send(embed=embed, files=files)
    except discord.HTTPException:
        log.exception("Could not post to the mod log channel")

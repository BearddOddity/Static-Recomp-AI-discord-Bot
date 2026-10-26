import asyncio
import contextlib
import logging
import sys
import time

import discord
from discord import app_commands

from . import __version__
from .ai import AIProvider, FallbackProvider, Image, build_provider
from .assist import (
    AskContext,
    ConversationMemory,
    Turn,
    build_messages,
    conversation_for,
    find_excerpts,
    persona_prompt,
    split_reply,
    strip_mention,
)
from .config import Settings, load_settings
from .docs import DocsIndex, DocsRouter, GitHubDocsFetcher, load_docs, parse_repo_list
from .health import GitHubReporter, HealthMonitor
from .moderation import Classifier, LinkChecker, Moderator
from .moderation.cases import CaseStore
from .moderation.discord_glue import is_exempt, read_images, to_post
from .moderation.review import PREFIX as CASE_PREFIX
from .moderation.review import CaseManager
from .moderation.rules import RulesBook
from .updater import RESTART_EXIT_CODE, Updater

log = logging.getLogger("bot")

RENEW_TIMEOUTS_EVERY_SECONDS = 6 * 3600

# Everything the bot needs. Used for the invite link and /status.
REQUIRED_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    add_reactions=True,  # ⏳ when someone asks again too quickly
    manage_messages=True,  # delete flagged messages
    moderate_members=True,  # timeouts pending review
    ban_members=True,  # only when a moderator presses Ban
    manage_guild=True,  # read AutoMod rules and receive AutoMod events
)

class AssistantBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        # Privileged: enable "Message Content Intent" in the Developer Portal.
        intents.message_content = True
        # Lets the bot hear Discord AutoMod's slur filter (needs Manage Server).
        intents.auto_moderation_execution = True
        # AI output is untrusted, so the bot never pings @everyone, roles or users.
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())

        self.settings = settings
        self.exit_code = 0
        self.ai: AIProvider = build_provider(settings)

        self.docs = DocsIndex()
        self.docs_specs = parse_repo_list(settings.docs_repos)
        self.docs_router = DocsRouter.parse(settings.docs_routes)
        unknown = self.docs_router.repos - {s.base_name.lower() for s in self.docs_specs}
        if unknown:
            raise RuntimeError(f"DOCS_ROUTES names repos missing from DOCS_REPOS: {', '.join(sorted(unknown))}")
        self.docs_fetcher = GitHubDocsFetcher(token=settings.github_token)
        self._docs_task: asyncio.Task | None = None

        # Cases, warnings and the #rules copy live in SQLite and survive restarts.
        self.store = CaseStore(settings.mod_db_path)
        self.rules = RulesBook(self.store, settings.rules_channel_id)
        self.cases = CaseManager(self, self.store, settings)
        self._renew_task: asyncio.Task | None = None

        self.moderator: Moderator | None = None
        self._mod_ai: AIProvider | None = None
        if settings.mod_enabled:
            if settings.mod_ai_scan != "off":
                # A separate model (e.g. a vision model) can be used for moderation. With
                # OpenRouter picking free models itself, moderation gets its own, preferring
                # models that can read screenshots.
                auto_free = settings.ai_provider == "openrouter" and not settings.openai_compat_model
                self._mod_ai = (
                    build_provider(settings, model=settings.mod_ai_model, setting="MOD_AI_MODEL", needs_images=True)
                    if settings.mod_ai_model or auto_free else self.ai
                )
                if not self._mod_ai.supports_images:
                    log.warning("AI provider %s can't see images; image scams are judged by text only", self._mod_ai.name)
            self.moderator = Moderator(
                LinkChecker(settings.mod_blocked_domains, settings.mod_allowed_domains, settings.mod_piracy_domains),
                Classifier(self._mod_ai, community=settings.community_about) if self._mod_ai else None,
                ai_scan=settings.mod_ai_scan,
                min_confidence=settings.mod_min_confidence,
            )

        self.conversations = ConversationMemory()
        self._mention_asked: dict[int, float] = {}

        # When a model disappears or the key stops working: tell the moderators, and
        # propose the fix on GitHub (a pull request for a model, an issue for a key).
        reporter = None
        if settings.health_github_repo and settings.health_github_token:
            reporter = GitHubReporter(settings.health_github_repo, settings.health_github_token)
        self.health = HealthMonitor(self.notify_mods, reporter, settings.health_pr_file)
        for provider in {id(p): p for p in (self.ai, self._mod_ai) if p}.values():
            if isinstance(provider, FallbackProvider):
                provider.on_problem = self.health.report

        self.updater = Updater(branch=settings.update_branch)
        self._update_task: asyncio.Task | None = None

        self.tree = app_commands.CommandTree(self)
        self.tree.error(on_command_error)
        self.tree.add_command(ask)
        self.tree.add_command(reload_docs)
        self.tree.add_command(warn)
        self.tree.add_command(warnings)
        self.tree.add_command(status)

    async def setup_hook(self) -> None:
        if self.docs_specs:
            self._docs_task = asyncio.create_task(self._refresh_docs_forever())
        self._renew_task = asyncio.create_task(self._renew_timeouts_forever())
        if self.settings.auto_update and self.settings.update_check_minutes > 0:
            if self.updater.available:
                self._update_task = asyncio.create_task(self._update_forever())
            else:
                log.info("Auto-update is off: the bot isn't running from a git clone (see docs/HOSTING.md)")

        # Guild sync shows commands at once; global sync can take an hour to appear.
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def refresh_docs(self) -> int:
        documents = await load_docs(self.docs_fetcher, self.docs_specs)
        # Keep the old index if every repo failed, so a GitHub outage doesn't wipe it.
        if documents or not len(self.docs):
            self.docs.replace(documents)
        log.info("Docs index has %d chunks from %d files", len(self.docs), len(documents))
        return len(documents)

    async def _refresh_docs_forever(self) -> None:
        while True:
            try:
                await self.refresh_docs()
            except Exception:
                log.exception("Docs refresh failed")
            if self.settings.docs_refresh_minutes <= 0:
                return
            await asyncio.sleep(self.settings.docs_refresh_minutes * 60)

    async def _update_forever(self) -> None:
        await self.wait_until_ready()
        while True:
            await asyncio.sleep(self.settings.update_check_minutes * 60)
            try:
                update = await self.updater.check()
            except Exception:
                log.exception("Checking for updates failed")
                continue
            if update:
                log.info("Updated %s -> %s (%s); restarting", update.old, update.new, update.summary)
                await self.notify_mods(f"🔄 Updated to `{update.summary}`. Restarting, back in a moment.")
                # start.sh, start.bat, the service and Docker all start the bot again.
                self.exit_code = RESTART_EXIT_CODE
                asyncio.create_task(self.close())
                return

    async def notify_mods(self, text: str) -> None:
        channel = self.get_channel(self.settings.mod_log_channel_id) if self.settings.mod_log_channel_id else None
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(text[:2000])
            except discord.HTTPException:
                log.warning("Couldn't post to the mod log: %s", text)

    async def _renew_timeouts_forever(self) -> None:
        await self.wait_until_ready()
        while True:
            try:
                renewed = await self.cases.renew_review_timeouts()
                if renewed:
                    log.info("Renewed %d review timeouts", renewed)
            except Exception:
                log.exception("Renewing review timeouts failed")
            await asyncio.sleep(RENEW_TIMEOUTS_EVERY_SECONDS)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (v%s) using AI provider %s", self.user, __version__, self.ai.name)
        log.info("Invite link: %s", invite_url(self.application_id))
        if not self.settings.mod_log_channel_id:
            log.warning("MOD_LOG_CHANNEL_ID is not set: cases are saved but moderators get no reports or buttons")
        for guild in self.guilds:
            await self.rules.refresh(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.rules.refresh(guild)

    # The rules are re-read whenever anything in the rules channel changes.
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        await self._rules_changed(payload.guild_id, payload.channel_id)
        # Scammers sometimes post something harmless, then edit in the scam. Only real
        # edits count: Discord also sends "edits" when it adds link previews.
        if not self.moderator or not payload.guild_id or not payload.data.get("edited_timestamp"):
            return
        before = payload.cached_message
        if before is not None and before.content == payload.data.get("content", before.content):
            return
        channel = self.get_channel(payload.channel_id)
        if channel is None or self.rules.is_rules_channel(channel.guild, channel.id):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        await self._moderate(message)

    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        if not self.moderator:
            return
        try:
            await self.cases.open_from_automod(execution)
        except Exception:
            log.exception("Handling an AutoMod action failed")

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self._rules_changed(payload.guild_id, payload.channel_id)

    async def _rules_changed(self, guild_id: int | None, channel_id: int) -> None:
        guild = self.get_guild(guild_id) if guild_id else None
        if self.rules.is_rules_channel(guild, channel_id):
            await self.rules.refresh(guild)

    async def on_message(self, message: discord.Message) -> None:
        if self.rules.is_rules_channel(message.guild, message.channel.id):
            await self.rules.refresh(message.guild)
            return
        if await self._moderate(message):
            return
        await self._answer_mention(message)

    async def _moderate(self, message: discord.Message) -> bool:
        """Check a message; True if it was flagged."""
        if not self.moderator or is_exempt(message, self.settings):
            return False
        try:
            post = await to_post(message, rules=self.rules.get(message.guild.id))
            verdict = await self.moderator.review(post)
            if verdict.flagged:
                await self.cases.open_from_message(message, post, verdict)
                return True
        except Exception:
            log.exception("Moderation failed for message %s", message.id)
        return False

    async def answer(
        self, question: str, guild_id: int | None, history: list[Turn] = (),
        context: AskContext | None = None, images: list[Image] = (),
    ) -> tuple[list[str], Turn]:
        """Answer anything, using the docs as a reference when they're relevant. Returns
        the Discord message parts and the turn to remember for follow-ups. Raises if the
        AI fails."""
        context = context or AskContext(bot_name=self.user.name if self.user else "the assistant")
        excerpts, topic_repos = await find_excerpts(
            self.docs, self.docs_router, question, limit=self.settings.docs_excerpts,
            ai=self.ai, ai_query=self.settings.docs_ai_query, history=list(history),
        )
        reply = await self.ai.complete(
            build_messages(
                persona_prompt(self.settings.system_prompt, context, self.settings.community_about),
                question, excerpts, topic_repos,
                rules=self.rules.get(guild_id) if guild_id else "", history=list(history),
                context=context, images=images if self.ai.supports_images else (),
            )
        )
        return split_reply(reply, excerpts), Turn(question, reply.strip())

    async def context_for(self, guild: discord.Guild | None, channel, user: discord.abc.User,
                          before: discord.Message | None = None) -> AskContext:
        recent: list[str] = []
        if channel is not None and self.settings.chat_context_messages > 0:
            try:
                async for earlier in channel.history(limit=self.settings.chat_context_messages, before=before):
                    if earlier.content:
                        name = "you" if earlier.author == self.user else earlier.author.display_name
                        recent.append(f"{name}: {earlier.clean_content[:300]}")
            except discord.HTTPException:
                pass
        return AskContext(
            bot_name=guild.me.display_name if guild else self.user.name,
            server=guild.name if guild else "this",
            channel=getattr(channel, "name", "") or "",
            asker=getattr(user, "display_name", user.name),
            now=discord.utils.utcnow(),
            recent=tuple(reversed(recent)),
        )

    def _asked_bot(self, message: discord.Message) -> list[Turn] | None:
        if message.guild is None or message.author.bot:
            return None
        if message.channel.id in self.settings.assistant_channel_ids:
            # Assistant channels: the bot answers every message there.
            reference = message.reference.message_id if message.reference else None
            return self.conversations.get(reference) if reference else []
        if not self.settings.ask_on_mention:
            return None
        reference = message.reference.message_id if message.reference else None
        return conversation_for(message.content, reference, self.user.id, self.conversations)

    async def _answer_mention(self, message: discord.Message) -> None:
        history = self._asked_bot(message)
        if history is None:
            return
        # Strip the bot's mention so the question reads naturally.
        question = strip_mention(message.content, self.user.id)
        images = await read_images(message) if self.ai.supports_images else []
        if not question and not images:
            return
        now = time.monotonic()
        last = self._mention_asked.get(message.author.id, 0.0)
        if now - last < self.settings.ask_cooldown_seconds:
            await message.add_reaction("⏳")
            return
        self._mention_asked[message.author.id] = now
        try:
            async with message.channel.typing():
                context = await self.context_for(message.guild, message.channel, message.author, before=message)
                parts, turn = await self.answer(
                    question or "(see the attached image)", message.guild.id, history, context, images,
                )
        except Exception:
            log.exception("AI request failed")
            await message.reply("The AI provider failed. Check the bot logs.", mention_author=False)
            return
        for n, part in enumerate(parts):
            sent = await (message.reply(part, mention_author=False) if n == 0 else message.channel.send(part))
            # Every part counts, so a reply to any of them continues the conversation.
            self.conversations.remember(sent.id, history, turn)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # Case report buttons. Slash commands are handled by the command tree.
        if interaction.type == discord.InteractionType.component:
            custom_id = (interaction.data or {}).get("custom_id", "")
            if custom_id.startswith(CASE_PREFIX + ":"):
                try:
                    await self.cases.handle_button(interaction)
                except Exception:
                    log.exception("Case button %s failed", custom_id)

    async def close(self) -> None:
        for task in (self._docs_task, self._renew_task, self._update_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.docs_fetcher.aclose()
        if self._mod_ai and self._mod_ai is not self.ai:
            await self._mod_ai.aclose()
        await self.ai.aclose()
        await self.health.aclose()
        self.store.close()
        await super().close()


def _ask_cooldown(interaction: discord.Interaction) -> app_commands.Cooldown | None:
    # One /ask per user per ASK_COOLDOWN_SECONDS, so nobody can run up API costs.
    seconds = interaction.client.settings.ask_cooldown_seconds  # type: ignore[attr-defined]
    return app_commands.Cooldown(1, seconds) if seconds > 0 else None


async def on_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Slow down. Try again in {error.retry_after:.0f}s.", ephemeral=True
        )
        return
    log.error("Command error", exc_info=error)


@app_commands.command(name="ask", description="Ask the AI assistant anything")
@app_commands.describe(question="What do you want to know?")
@app_commands.checks.dynamic_cooldown(_ask_cooldown, key=lambda i: i.user.id)
async def ask(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer(thinking=True)
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    try:
        context = await bot.context_for(interaction.guild, interaction.channel, interaction.user)
        parts, turn = await bot.answer(question, interaction.guild_id, context=context)
    except Exception:
        log.exception("AI request failed")
        await interaction.followup.send("The AI provider failed. Check the bot logs.")
        return
    for part in parts:
        sent = await interaction.followup.send(part, wait=True)
        bot.conversations.remember(sent.id, [], turn)


@app_commands.command(name="reload-docs", description="Re-download the GitHub docs the bot answers from")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def reload_docs(interaction: discord.Interaction) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if not bot.docs_specs:
        await interaction.response.send_message("No DOCS_REPOS are configured.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    count = await bot.refresh_docs()
    await interaction.followup.send(
        f"Loaded {count} files ({len(bot.docs)} sections) from "
        f"{', '.join(s.full_name for s in bot.docs_specs)}.",
        ephemeral=True,
    )


@app_commands.command(name="warn", description="Warn a member (counts toward a timeout pending review)")
@app_commands.describe(member="Who to warn", reason="Why. The member sees this in a DM.")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if member.bot or member.guild_permissions.manage_messages:
        await interaction.response.send_message("Bots and moderators can't be warned.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    case = await bot.cases.open_manual(interaction.guild, member, interaction.user, reason)
    count = bot.cases.warning_count(interaction.guild.id, member.id)
    note = " They are now timed out until a moderator reviews the case." if case.status == "review" else ""
    await interaction.followup.send(
        f"Warned {member.mention} (case #{case.id}, {count}/{bot.settings.mod_warn_limit} warnings).{note}",
        ephemeral=True,
    )


@app_commands.command(name="warnings", description="Show a member's warnings and recent cases")
@app_commands.describe(member="Whose record to show")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def warnings(interaction: discord.Interaction, member: discord.Member) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    active = bot.store.active_warnings(interaction.guild.id, member.id, bot.settings.mod_warn_expiry_days)
    cases = bot.store.cases_for_user(interaction.guild.id, member.id)
    lines = [f"**{member}** has {len(active)}/{bot.settings.mod_warn_limit} active warnings."]
    lines += [f"• <t:{int(w.created_at)}:d> case #{w.case_id}: {w.reason}" for w in active]
    if cases:
        lines.append("\n**Recent cases**")
        lines += [
            f"• #{c.id} {c.label}: {c.status if c.is_open else c.decision} (<t:{int(c.created_at)}:R>)"
            for c in cases
        ]
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


def invite_url(application_id: int | None) -> str:
    return discord.utils.oauth_url(
        application_id or 0, permissions=REQUIRED_PERMISSIONS, scopes=("bot", "applications.commands")
    )


@app_commands.command(name="status", description="Check the bot's setup and permissions in this server")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def status(interaction: discord.Interaction) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    settings, guild = bot.settings, interaction.guild
    ok, bad = "✅", "⚠️"
    lines = [f"**{bot.user.name} status** (v{__version__})"]

    def model_of(provider: AIProvider) -> str:
        if isinstance(provider, FallbackProvider):
            return f"`{provider.name}` · `{provider.current or 'picks a free model on first use'}`"
        return f"`{provider.name}`"

    lines.append(f"{ok} AI for /ask: {model_of(bot.ai)}")
    for problem in bot.health.problems[-3:]:
        lines.append(f"{bad} AI {problem.kind.replace('_', ' ')} problem with `{problem.model}`"
                     + (f", now using `{problem.replacement}`" if problem.replacement else ""))
    if settings.auto_update and bot.updater.available:
        lines.append(f"{ok} Auto-update: checks GitHub every {settings.update_check_minutes:g} minutes")
    else:
        lines.append(f"{bad} Auto-update is off (run from a git clone with AUTO_UPDATE=true)")
    if not settings.mod_enabled:
        lines.append(f"{bad} Moderation is off (MOD_ENABLED=false)")
    elif bot._mod_ai is None:
        lines.append(f"{bad} Moderation AI is off (MOD_AI_SCAN=off): rules and AutoMod only")
    else:
        vision = bot._mod_ai.supports_images
        lines.append(
            f"{ok if vision else bad} Moderation AI: {model_of(bot._mod_ai)}, scan mode `{settings.mod_ai_scan}`, "
            + ("can see images" if vision else "can't see images, so image-only scams get through")
        )

    log_channel = guild.get_channel(settings.mod_log_channel_id) if settings.mod_log_channel_id else None
    lines.append(
        f"{ok} Case reports go to {log_channel.mention}" if log_channel
        else f"{bad} MOD_LOG_CHANNEL_ID is missing or not visible: moderators get no case reports"
    )
    rules_channel = bot.rules.channel_for(guild)
    rules_text = bot.rules.get(guild.id)
    lines.append(
        f"{ok} Rules: {len(rules_text)} characters read from {rules_channel.mention}" if rules_channel and rules_text
        else f"{bad} No rules loaded (set RULES_CHANNEL_ID or name a channel #rules)"
    )
    lines.append(
        f"{ok} Docs: {len(bot.docs)} sections from {len(bot.docs_specs)} sources" if len(bot.docs)
        else f"{bad} No docs loaded yet (DOCS_REPOS, or wait a minute after startup)"
    )
    lines.append(f"{ok} Warnings: timeout pending review at {settings.mod_warn_limit}; {bot.store.count_in_review(guild.id)} case(s) waiting for review")

    try:
        rules = await guild.fetch_automod_rules()
        slurs = any(r.enabled and r.trigger.presets and r.trigger.presets.slurs for r in rules)
        lines.append(
            f"{ok} AutoMod slur filter is on: hate speech gets a timeout and a case" if slurs
            else f"{bad} AutoMod slur filter is off: Server Settings → AutoMod → turn on \"Block words: Slurs\""
        )
    except discord.HTTPException:
        lines.append(f"{bad} Can't read AutoMod rules (the bot needs Manage Server)")

    missing = [name.replace("_", " ").title() for name, needed in REQUIRED_PERMISSIONS if needed
               and not getattr(guild.me.guild_permissions, name)]
    lines.append(f"{bad} Missing permissions: {', '.join(missing)}" if missing else f"{ok} All permissions granted")
    if guild.me.top_role.position <= 1:
        lines.append(f"{bad} Move the bot's role higher in Server Settings → Roles, above members it should moderate")

    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


# Exit codes the start scripts understand: 0 = stopped on purpose, 2 = a setting needs
# fixing (restarting won't help), 3 = updated, start again. Anything else: crashed.
CONFIG_EXIT_CODE = 2


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = load_settings()
        bot = AssistantBot(settings)
    except (RuntimeError, ValueError) as error:
        log.error("Setup problem: %s", error)
        sys.exit(CONFIG_EXIT_CODE)
    try:
        bot.run(settings.discord_token, log_handler=None)
    except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as error:
        log.error("Discord refused to log in: %s. Check DISCORD_TOKEN and the Message Content Intent.", error)
        sys.exit(CONFIG_EXIT_CODE)
    sys.exit(bot.exit_code)


if __name__ == "__main__":
    main()

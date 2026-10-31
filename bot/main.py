import asyncio
import contextlib
import json
import logging
import sys
import time
from collections import deque
from pathlib import Path

import discord
from discord import app_commands

from . import __version__
from .ai import AIProvider, FallbackProvider, Image, build_provider, models_from
from .assist import (
    AskContext,
    ConversationMemory,
    Turn,
    build_messages,
    conversation_for,
    find_excerpts,
    persona_prompt,
    screen_reply,
    split_reply,
    strip_mention,
    wants_sources,
)
from .config import Settings, load_settings
from .docs import DocsIndex, DocsRouter, GitHubDocsFetcher, load_docs, parse_repo_list
from .guardrails import HELD_BACK_REPLY, GuardrailsFile
from .health import GitHubReporter, HealthMonitor
from .moderation import Classifier, LinkChecker, Moderator
from .moderation.appeals import AppealManager
from .moderation.brake import SafetyBrake
from .moderation.cases import CaseStore, protect
from .moderation.discord_glue import is_exempt, read_images, to_post
from .moderation.review import APPEAL_PREFIX
from .moderation.review import PREFIX as CASE_PREFIX
from .moderation.review import CaseManager
from .moderation.rules import RulesBook
from . import community, runtime_settings, settings_ui
from .updater import RESTART_EXIT_CODE, Updater

log = logging.getLogger("bot")

RENEW_TIMEOUTS_EVERY_SECONDS = 6 * 3600
PAUSED_REPLY = "I'm paused by the moderators for the moment. Please ask again later, or ask a moderator."

# Permissions the bot never needs and that would let a stolen token or a bug do real
# damage (delete channels, hand out roles, impersonate people with webhooks...).
DANGEROUS_PERMISSIONS = (
    "administrator", "manage_roles", "manage_channels", "manage_webhooks", "mention_everyone",
    "kick_members", "manage_nicknames", "manage_expressions", "manage_events", "move_members",
    "mute_members", "deafen_members", "view_audit_log", "create_instant_invite",
)
# Roles the bot must stay below, so it can never time out or ban the people who run the server.
STAFF_PERMISSIONS = ("administrator", "manage_guild", "ban_members", "manage_roles")
# Forget members' last question times once this many are tracked, so memory stays flat.
MAX_TRACKED_ASKERS = 5000

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
    create_private_threads=True,  # /help threads
    manage_threads=True,  # close and archive /help threads
)

class HomeOnlyTree(app_commands.CommandTree):
    """Slash commands only work in the bot's own server. Settings, keys and AI quota are
    shared by every server the bot is in, so someone who invites it elsewhere must not be
    able to change them or spend them."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.client.is_home(interaction.guild):  # type: ignore[attr-defined]
            return True
        if interaction.type == discord.InteractionType.application_command:
            await interaction.response.send_message("This bot only works in the server it was set up for.", ephemeral=True)
        return False


class AssistantBot(discord.Client):
    def __init__(self, settings: Settings, members: bool | None = None):
        intents = discord.Intents.default()
        # Privileged: enable "Message Content Intent" in the Developer Portal.
        intents.message_content = True
        # Lets the bot hear Discord AutoMod's slur filter (needs Manage Server).
        intents.auto_moderation_execution = True
        # Privileged: only asked for when a welcome message is set up, and only if the
        # Developer Portal allows it (main() starts without it otherwise).
        intents.members = bool(settings.welcome_channel_id) and members is not False
        # AI output is untrusted, so the bot never pings @everyone, roles or users.
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())

        self.settings = settings
        self.exit_code = 0
        self.ai: AIProvider = build_provider(settings)
        if settings.ai_provider == "cli":
            log.warning("AI_PROVIDER=cli: command-line AI tools can run commands, and members can try to talk them "
                        "into it. Run the tool read-only or in a sandbox (see docs/SECURITY.md).")

        # /ai-mode: the chosen mode is saved next to the database so a restart keeps it.
        self._mode_file = Path(settings.mod_db_path).parent / "ai_mode.txt"
        self.ai_mode = "default"
        with contextlib.suppress(OSError):
            self.set_ai_mode(self._mode_file.read_text().strip(), save=False)

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
        # The safety brake: /brake on stops everything automatic; it also comes on by itself
        # when the AI suddenly flags many members (see moderation/brake.py).
        self.brake = SafetyBrake(Path(settings.mod_db_path).parent / "brake.json", settings.mod_brake_limit)
        self.cases = CaseManager(self, self.store, settings, self.brake)
        self.appeals = AppealManager(self, self.store, self.cases)
        self.last_ai_welcome = -1e9  # when the AI last wrote a welcome
        self._ticket_last: dict[int, int] = {}  # help thread -> the bot's latest answer there
        self.notice = ""  # shown to moderators once, after startup
        self._renew_task: asyncio.Task | None = None

        # The owners' guardrails (GUARDRAILS_FILE), re-read whenever the file changes.
        self.guardrails = GuardrailsFile(settings.guardrails_file, settings.guardrail_instructions)
        # The bot's own keys: an answer that contains one is never sent.
        self._secrets = tuple(k for k in (
            settings.discord_token, settings.anthropic_api_key, settings.openai_compat_api_key,
            settings.ai_backup_api_key, settings.github_token, settings.health_github_token,
        ) if k)
        # Also screens the assistant's own answers, so a tricked AI can't post piracy or scam links.
        self.links = LinkChecker(settings.mod_blocked_domains, settings.mod_allowed_domains, settings.mod_piracy_domains)
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
                self.links,
                Classifier(self._mod_ai, community=settings.community_about,
                           guidance=lambda: self.guardrails.current().moderation) if self._mod_ai else None,
                ai_scan=settings.mod_ai_scan,
                min_confidence=settings.mod_min_confidence,
                ai_per_minute=settings.mod_ai_max_per_minute,
            )

        # /tune: moderators change these two live; the values are saved so a restart keeps them.
        self.chat_context = settings.chat_context_messages
        self.settings_pending: set[str] = set()  # settings saved from Discord, waiting for a restart
        self._tuning_file = Path(settings.mod_db_path).parent / "tuning.json"
        with contextlib.suppress(OSError, ValueError, TypeError):
            saved = json.loads(self._tuning_file.read_text())
            self.set_tuning(saved.get("confidence"), saved.get("context"), save=False)

        self.conversations = ConversationMemory()
        self._mention_asked: dict[int, float] = {}  # member -> when the AI last answered them
        self._answers_this_minute: deque[float] = deque()  # every member, for ASK_MAX_PER_MINUTE

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

        self.tree = HomeOnlyTree(self)
        self.tree.error(on_command_error)
        self.tree.add_command(ask)
        self.tree.add_command(reload_docs)
        self.tree.add_command(warn)
        self.tree.add_command(warnings)
        self.tree.add_command(status)
        self.tree.add_command(ai_mode)
        self.tree.add_command(tune)
        self.tree.add_command(settings_command)
        self.tree.add_command(help_command)
        self.tree.add_command(feedback_command)
        self.tree.add_command(guardrails_command)
        self.tree.add_command(brake_command)

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

    def is_home(self, guild: discord.abc.Snowflake | None) -> bool:
        """Whether this is the server the bot was set up for. With DISCORD_GUILD_ID set, only
        that one; without it, the only server the bot is in (never one of several, since
        anyone could have invited it there)."""
        if guild is None:
            return False
        if self.settings.discord_guild_id:
            return guild.id == self.settings.discord_guild_id
        return len(self.guilds) <= 1

    def ai_wait(self, user_id: int) -> float:
        """Seconds until the AI may answer this member again (0 = now, and the answer is
        counted). Limits each member (ASK_COOLDOWN_SECONDS) and everyone together
        (ASK_MAX_PER_MINUTE), so nobody can run up the AI bill."""
        now = time.monotonic()
        wait = self._mention_asked.get(user_id, -1e9) + self.settings.ask_cooldown_seconds - now
        recent = self._answers_this_minute
        while recent and now - recent[0] >= 60:
            recent.popleft()
        limit = self.settings.ask_max_per_minute
        if limit > 0 and len(recent) >= limit:
            wait = max(wait, recent[0] + 60 - now)
        if wait > 0:
            return wait
        if len(self._mention_asked) > MAX_TRACKED_ASKERS:
            cutoff = now - self.settings.ask_cooldown_seconds
            self._mention_asked = {u: t for u, t in self._mention_asked.items() if t > cutoff}
        self._mention_asked[user_id] = now
        recent.append(now)
        return 0.0

    async def _leave_if_foreign(self, guild: discord.Guild, joined: bool = False) -> bool:
        """Leave a server the bot wasn't set up for. True if the bot left."""
        if self.settings.discord_guild_id:
            if guild.id == self.settings.discord_guild_id:
                return False
        elif not joined or len(self.guilds) <= 1:
            # Without DISCORD_GUILD_ID the first server is home; only a newcomer is left.
            return False
        log.warning("Leaving server %s (%s): the bot only runs in the server it was set up for", guild.name, guild.id)
        with contextlib.suppress(discord.HTTPException):
            await guild.leave()
        return True

    @property
    def min_confidence(self) -> float:
        return self.moderator.min_confidence if self.moderator else self.settings.mod_min_confidence

    def set_tuning(self, confidence: float | None = None, context: int | None = None, *, save: bool = True) -> None:
        """Change how sure the moderation AI must be, and how many channel messages the
        assistant reads. None leaves a value as it is."""
        if confidence is not None and self.moderator:
            self.moderator.min_confidence = min(0.99, max(0.5, float(confidence)))
        if context is not None:
            self.chat_context = min(20, max(0, int(context)))
        if save:
            self._tuning_file.parent.mkdir(parents=True, exist_ok=True)
            self._tuning_file.write_text(json.dumps({"confidence": self.min_confidence, "context": self.chat_context}))

    def set_ai_mode(self, mode: str, *, save: bool = True) -> bool:
        """Point /ask at a named model list from AI_MODES, or "default" for the
        OPENAI_COMPAT_MODEL list. False if there is no such mode."""
        mode = mode.lower()
        if not isinstance(self.ai, FallbackProvider):
            return False
        if mode == "default":
            models = self.settings.openai_compat_model
        elif mode in self.settings.ai_modes:
            models = self.settings.ai_modes[mode]
        else:
            return False
        self.ai.set_models(models_from(models))
        self.ai_mode = mode
        if save:
            self._mode_file.parent.mkdir(parents=True, exist_ok=True)
            self._mode_file.write_text(mode)
        return True

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
        for guild in list(self.guilds):
            if not await self._leave_if_foreign(guild):
                await self.rules.refresh(guild)
                await self._check_permissions(guild)
        if not self.settings.discord_guild_id and len(self.guilds) > 1:
            log.warning("The bot is in %d servers and DISCORD_GUILD_ID is not set, so it ignores all of them. "
                        "Set DISCORD_GUILD_ID to your server's ID.", len(self.guilds))
        if self.notice:
            await self.notify_mods(self.notice)
            self.notice = ""
        if runtime_settings.rejected_reason:
            await self.notify_mods(
                f"⚠️ The settings saved from Discord were rejected at startup ({runtime_settings.rejected_reason[:300]}). "
                "They were set aside in data/settings.rejected.json and the bot started from .env."
            )
            runtime_settings.rejected_reason = ""

    async def on_member_join(self, member: discord.Member) -> None:
        channel_id = self.settings.welcome_channel_id
        channel = member.guild.get_channel(channel_id) if channel_id else None
        if member.bot or not isinstance(channel, discord.abc.Messageable) or not self.is_home(member.guild):
            return
        rules = self.rules.channel_for(member.guild)
        rules_mention = rules.mention if rules else "the rules"
        text = None
        if self.settings.welcome_ai:  # off unless a moderator turned it on
            text = await community.ai_welcome(self, member, rules_mention)
        if text is None:
            text = community.render_welcome(self.settings.welcome_message, member.mention, member.guild.name, rules_mention)
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            log.warning("Could not post the welcome message in channel %s", channel_id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if not await self._leave_if_foreign(guild, joined=True):
            await self.rules.refresh(guild)
            await self._check_permissions(guild)

    async def _check_permissions(self, guild: discord.Guild) -> None:
        problems = safety_problems(guild)
        if problems:
            log.warning("Permission problems in %s: %s", guild.name, "; ".join(problems))
            await self.notify_mods("🔐 **Safety check:** " + " ".join(problems))

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
        if channel is None or not self.is_home(channel.guild) or self.rules.is_rules_channel(channel.guild, channel.id):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        await self._moderate(message)

    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        if not self.moderator or not self.is_home(execution.guild):
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
        if not self.is_home(message.guild):
            return  # DMs, and servers the bot wasn't set up for
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
        if self.brake.manual:
            return [PAUSED_REPLY], Turn(question, PAUSED_REPLY)
        rails = self.guardrails.current()
        decision = rails.check_question(question)
        if decision.reason:
            log.info("Guardrail for a question from %s: %s", context.asker or "someone", decision.reason)
        if decision.reply is not None:
            # A redirect or a refusal: the AI never sees the question.
            return split_reply(decision.reply, []), Turn(question, decision.reply)
        excerpts, topic_repos = await find_excerpts(
            self.docs, self.docs_router, question, limit=self.settings.docs_excerpts,
            ai=self.ai, ai_query=self.settings.docs_ai_query, history=list(history),
        )
        persona = persona_prompt(self.settings.system_prompt, context, self.settings.community_about)
        guardrails = [*rails.instructions, *decision.instructions]
        messages = build_messages(
            persona, question, excerpts, topic_repos,
            rules=self.rules.get(guild_id) if guild_id else "", history=list(history),
            context=context, images=images if self.ai.supports_images else (),
            docs_loaded=len(self.docs) > 0,
            guardrails=guardrails,
        )
        reply = await self.ai.complete(messages)
        # Only the private instructions count as a leak: quoting the docs or #rules is fine.
        problem = rails.check_answer(reply, "\n".join([persona, *guardrails]), self._secrets)
        if problem:
            log.warning("Held back an AI answer: %s", problem)
            reply = HELD_BACK_REPLY
        reply = screen_reply(reply, self.links)
        return split_reply(reply, excerpts if wants_sources(question) else []), Turn(question, reply.strip())

    async def context_for(self, guild: discord.Guild | None, channel, user: discord.abc.User,
                          before: discord.Message | None = None) -> AskContext:
        recent: list[str] = []
        if channel is not None and self.chat_context > 0:
            try:
                async for earlier in channel.history(limit=self.chat_context, before=before):
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
        if isinstance(message.channel, discord.Thread):
            ticket = self.store.get_ticket(message.channel.id)
            if ticket:  # a /help thread: answer the member who opened it, until a moderator joins
                if not community.bot_answers_in(ticket, message.author.id):
                    return None
                last = (message.reference.message_id if message.reference else None) or self._ticket_last.get(message.channel.id)
                return self.conversations.get(last) if last else []
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
        if self.ai_wait(message.author.id):
            with contextlib.suppress(discord.HTTPException):
                await message.add_reaction("⏳")
            return
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
            last = n == len(parts) - 1
            view = community.feedback_view() if last else discord.utils.MISSING
            sent = await (message.reply(part, mention_author=False, view=view) if n == 0
                          else message.channel.send(part, view=view))
            # Every part counts, so a reply to any of them continues the conversation.
            self.conversations.remember(sent.id, history, turn)
            if last:
                self.store.record_answer(sent.id, message.guild.id, message.channel.id, message.author.id, question, turn.answer)
                if isinstance(message.channel, discord.Thread):
                    self._ticket_last[message.channel.id] = sent.id

    async def answer_in_thread(self, thread: discord.Thread, member: discord.abc.User, question: str) -> None:
        """The first answer in a /help thread."""
        if self.ai_wait(member.id):
            await thread.send("I'm answering a lot of questions right now. Ask again here in a minute, "
                              "or press **Get a human** and a moderator will help.")
            return
        try:
            async with thread.typing():
                context = await self.context_for(thread.guild, thread, member)
                parts, turn = await self.answer(question, thread.guild.id, [], context)
        except Exception:
            log.exception("AI request failed in a help thread")
            await thread.send("The AI provider failed. Press **Get a human** and a moderator will help.")
            return
        for n, part in enumerate(parts):
            last = n == len(parts) - 1
            sent = await thread.send(part, view=community.feedback_view() if last else discord.utils.MISSING)
            self.conversations.remember(sent.id, [], turn)
            if last:
                self._ticket_last[thread.id] = sent.id
                self.store.record_answer(sent.id, thread.guild.id, thread.id, member.id, question, turn.answer)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # Buttons on case reports, appeals, answers and help threads. Slash commands
        # are handled by the command tree.
        if interaction.type != discord.InteractionType.component:
            return
        # Warning DMs carry the Appeal button, so appeals are the one button used outside the server.
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not self.is_home(interaction.guild) and not custom_id.startswith(APPEAL_PREFIX + ":"):
            return
        handlers = [
            (lambda c: c.startswith(CASE_PREFIX + ":"), self.cases.handle_button),
            (lambda c: c.startswith(APPEAL_PREFIX + ":"), self.appeals.handle_button),
            (lambda c: c in (community.FEEDBACK_UP, community.FEEDBACK_DOWN),
             lambda i: community.handle_feedback(self.store, i)),
            (lambda c: c.startswith(community.TICKET_PREFIX + ":"),
             lambda i: community.handle_ticket_button(self, i)),
        ]
        for matches, handle in handlers:
            if matches(custom_id):
                try:
                    await handle(interaction)
                except Exception:
                    log.exception("Button %s failed", custom_id)
                return

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


async def on_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Slow down. Try again in {error.retry_after:.0f}s.", ephemeral=True
        )
        return
    if isinstance(error, app_commands.MissingPermissions):
        needed = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"You need the {needed} permission to use this.", ephemeral=True)
        return
    if isinstance(error, app_commands.CheckFailure):
        return  # already answered (e.g. the bot isn't set up for this server)
    log.error("Command error", exc_info=error)


# Long enough for a question with an error log; caps what one /ask can cost.
MAX_QUESTION_CHARS = 1500


@app_commands.command(name="ask", description="Ask the AI assistant anything")
@app_commands.describe(question="What do you want to know?")
@app_commands.guild_only()
async def ask(interaction: discord.Interaction, question: app_commands.Range[str, 1, MAX_QUESTION_CHARS]) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    wait = bot.ai_wait(interaction.user.id)
    if wait:
        await interaction.response.send_message(f"Slow down. Try again in {wait:.0f}s.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        context = await bot.context_for(interaction.guild, interaction.channel, interaction.user)
        parts, turn = await bot.answer(question, interaction.guild_id, context=context)
    except Exception:
        log.exception("AI request failed")
        await interaction.followup.send("The AI provider failed. Check the bot logs.")
        return
    for n, part in enumerate(parts):
        last = n == len(parts) - 1
        sent = await interaction.followup.send(
            part, wait=True, view=community.feedback_view() if last else discord.utils.MISSING
        )
        bot.conversations.remember(sent.id, [], turn)
        if last:
            bot.store.record_answer(sent.id, interaction.guild_id, interaction.channel_id, interaction.user.id, question, turn.answer)


@app_commands.command(name="reload-docs", description="Re-download the GitHub docs the bot answers from")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
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
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.guild_only()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 500]) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if member.bot or member.guild_permissions.manage_messages:
        await interaction.response.send_message("Bots and moderators can't be warned.", ephemeral=True)
        return
    if not outranks(interaction.user, member):
        await interaction.response.send_message("You can only warn members whose top role is below yours.", ephemeral=True)
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
@app_commands.checks.has_permissions(moderate_members=True)
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


@app_commands.command(name="ai-mode", description="Switch which AI models answer /ask")
@app_commands.describe(mode="A mode from AI_MODES, or default. Leave empty to see the current one.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def ai_mode(interaction: discord.Interaction, mode: str | None = None) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    modes = ["default", *bot.settings.ai_modes]
    if mode is not None and not bot.set_ai_mode(mode):
        await interaction.response.send_message(f"Unknown mode. Choose one of: {', '.join(modes)}", ephemeral=True)
        return
    current = getattr(bot.ai, "current", "") or "picks a free model on first use"
    verb = "Switched to" if mode is not None else "Current mode:"
    await interaction.response.send_message(
        f"{verb} `{bot.ai_mode}` (model `{current}`). Modes: {', '.join(modes)}", ephemeral=True
    )


@app_commands.command(name="tune", description="Show or change how strict moderation is and how much chat the AI reads")
@app_commands.describe(
    confidence="How sure the moderation AI must be before flagging a post, 0.50 to 0.99 (higher = stricter)",
    context="How many recent channel messages the AI reads before answering, 0 to 20 (0 = none)",
)
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.guild_only()
async def tune(
    interaction: discord.Interaction,
    confidence: app_commands.Range[float, 0.5, 0.99] | None = None,
    context: app_commands.Range[int, 0, 20] | None = None,
) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if confidence is not None and not bot.moderator:
        await interaction.response.send_message("Moderation is off, so there is no confidence to set.", ephemeral=True)
        return
    before = (bot.min_confidence, bot.chat_context)
    if confidence is not None or context is not None:
        bot.set_tuning(confidence, context)
        if before != (bot.min_confidence, bot.chat_context):
            await bot.notify_mods(
                f"🎚️ {interaction.user.mention} changed the settings: moderation confidence "
                f"{before[0]:.2f} → {bot.min_confidence:.2f}, chat context {before[1]} → {bot.chat_context} messages."
            )
    verb = "Now" if confidence is not None or context is not None else "Current"
    await interaction.response.send_message(
        f"{verb}: moderation confidence **{bot.min_confidence:.2f}** (the AI must be at least this sure to flag a post; "
        f"higher = fewer false alarms, lower = catches more) · chat context **{bot.chat_context}** messages "
        f"(recent channel messages the AI reads before answering; 0 = none). Saved across restarts.",
        ephemeral=True,
    )


@app_commands.command(name="help", description="Open a private thread to get help")
@app_commands.describe(question="What do you need help with?")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
async def help_command(interaction: discord.Interaction, question: app_commands.Range[str, 3, 300]) -> None:
    await community.open_help_thread(interaction.client, interaction, question)


@app_commands.command(name="feedback", description="See the answers members marked as unhelpful")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.guild_only()
async def feedback_command(interaction: discord.Interaction) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("You need the Moderate Members permission.", ephemeral=True)
        return
    await interaction.response.send_message(embed=community.downvoted_embed(bot.store, interaction.guild_id), ephemeral=True)


@app_commands.command(name="brake", description="Emergency brake: stop every automatic punishment and AI answer")
@app_commands.describe(state="on stops everything automatic until you turn it off; off resumes; empty shows the state")
@app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.guild_only()
async def brake_command(interaction: discord.Interaction, state: str | None = None) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if state == "on":
        bot.brake.engage(str(interaction.user))
        await bot.notify_mods(f"🛑 {interaction.user.mention} pulled the **safety brake**: no automatic deletes, warnings, "
                              "DMs or timeouts, and no AI answers. Violations still get case reports. `/brake off` to resume.")
    elif state == "off":
        bot.brake.release()
        await bot.notify_mods(f"✅ {interaction.user.mention} released the safety brake. Automatic moderation and answers are back on.")
    await interaction.response.send_message(f"Safety brake: **{bot.brake.describe()}**.", ephemeral=True)


@app_commands.command(name="guardrails", description="See the AI's guardrails, or test what they do with a question")
@app_commands.describe(test="A question to try: shows whether it's answered, redirected or refused, and why")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
async def guardrails_command(interaction: discord.Interaction, test: app_commands.Range[str, 1, 500] | None = None) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    rails = bot.guardrails.current()
    path = bot.guardrails.path
    found = path is not None and path.is_file()
    lines = [
        f"**Guardrails** from `{path}`" if found else
        f"**Guardrails:** no file at `{path}` yet, so only the built-in ones apply. Copy `guardrails.example.txt` to start.",
        f"Loaded: {rails.summary()}. Edits to the file apply on the next question, no restart needed.",
        "Built in, always on: jailbreak attempts are refused, and answers that leak the bot's instructions, "
        "a key, a piracy link or a scam link are held back.",
    ]
    if rails.problems:
        lines.append("⚠️ Lines that couldn't be read: " + "; ".join(rails.problems[:5]))
    if test:
        decision = rails.check_question(test)
        if decision.reply is not None:
            lines.append(f"\n**Test:** answered without the AI ({decision.reason}):\n> {decision.reply[:500]}")
        elif decision.instructions:
            lines.append(f"\n**Test:** the AI answers, with extra instructions ({decision.reason}):\n"
                         + "\n".join(f"> {i[:300]}" for i in decision.instructions))
        else:
            lines.append("\n**Test:** no guardrail matches; the AI answers normally, following the instructions.")
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


@app_commands.command(name="settings", description="Open the private settings page (keys, channels, AI, moderation)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def settings_command(interaction: discord.Interaction) -> None:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    if not settings_ui.can_use(interaction):
        await interaction.response.send_message("You need the Manage Server permission to change settings.", ephemeral=True)
        return
    embed, view = settings_ui.page(bot, None)
    view.owner_id = interaction.user.id
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@ai_mode.autocomplete("mode")
async def _ai_mode_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot: AssistantBot = interaction.client  # type: ignore[assignment]
    return [app_commands.Choice(name=m, value=m) for m in ("default", *bot.settings.ai_modes) if current.lower() in m]


def safety_problems(guild: discord.Guild) -> list[str]:
    """What would let the bot do more harm than its job needs: permissions it shouldn't
    have, or a role above the server's staff."""
    me = guild.me
    if me is None:
        return []
    problems = []
    extra = [name for name in DANGEROUS_PERMISSIONS if getattr(me.guild_permissions, name, False)]
    if "administrator" in extra:
        problems.append("The bot has **Administrator**, which lets anyone with its token do anything to the server. "
                        "Remove it from the bot's role and re-invite with the link in SETUP.md.")
    elif extra:
        problems.append("The bot has permissions it never uses: " + ", ".join(p.replace("_", " ").title() for p in extra)
                        + ". Remove them from its role (Server Settings → Roles), so a stolen token can do less.")
    staff_below = [role.name for role in guild.roles if role < me.top_role and not role.is_default() and not role.managed
                   and any(getattr(role.permissions, p) for p in STAFF_PERMISSIONS)]
    if staff_below:
        problems.append("The bot's role is above staff roles (" + ", ".join(staff_below[:5]) + "), so it could time out "
                        "or ban staff. Drag it below them in Server Settings → Roles, keeping it above normal members.")
    return problems


def outranks(moderator: discord.abc.User, member: discord.Member) -> bool:
    """Whether a moderator may act on a member: never on themselves, and only on members
    whose top role is below theirs (the server owner may act on anyone)."""
    if moderator.id == member.id:
        return False
    if not isinstance(moderator, discord.Member):
        return False
    return moderator.id == member.guild.owner_id or moderator.top_role > member.top_role


def invite_url(application_id: int | None) -> str:
    return discord.utils.oauth_url(
        application_id or 0, permissions=REQUIRED_PERMISSIONS, scopes=("bot", "applications.commands")
    )


@app_commands.command(name="status", description="Check the bot's setup and permissions in this server")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
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

    lines.append(f"{bad if bot.brake.engaged else ok} Safety brake: {bot.brake.describe()}")
    for problem in safety_problems(guild):
        lines.append(f"{bad} {problem}")
    lines.append(f"{ok} Locked to this server (DISCORD_GUILD_ID)" if settings.discord_guild_id
                 else f"{bad} DISCORD_GUILD_ID is not set: set it so the bot can never be used from another server")
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
    on, off = ok, "➖"
    lines.append(f"{on if settings.appeals_enabled else off} Appeals mode: "
                 + ("on, warned members can appeal" if settings.appeals_enabled else "off (turn on in /settings → Community)"))
    lines.append(f"{on if settings.help_enabled else off} Help mode: "
                 + ("on, members can use /help" if settings.help_enabled else "off (turn on in /settings → Community)"))
    welcome = guild.get_channel(settings.welcome_channel_id) if settings.welcome_channel_id else None
    lines.append(f"{on if welcome else off} Welcome: "
                 + (f"{welcome.mention}, written by {'the AI' if settings.welcome_ai else 'your message'}" if welcome
                    else "off (pick a channel in /settings → Community)"))
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
    protect(Path(".env"), 0o600)  # the bot's token and keys: readable by its own user only
    for attempt in (1, 2):
        try:
            settings = load_settings()
            bot = AssistantBot(settings)
            break
        except (RuntimeError, ValueError) as error:
            # Settings saved from Discord that stop the bot starting are set aside once,
            # so a typo made in /settings can't lock everyone out.
            if attempt == 1 and runtime_settings.reject_overrides(str(error)):
                log.error("Saved Discord settings were rejected (%s); starting from .env instead", error)
                continue
            log.error("Setup problem: %s", error)
            sys.exit(CONFIG_EXIT_CODE)
    try:
        try:
            bot.run(settings.discord_token, log_handler=None)
        except discord.PrivilegedIntentsRequired:
            if not bot.intents.members:
                raise
            # Only the welcome message needs this intent: start without it instead of staying offline.
            log.warning("Server Members Intent is off in the Developer Portal; starting without welcome messages")
            bot = AssistantBot(settings, members=False)
            bot.notice = ("⚠️ Welcome messages are off: turn on **Server Members Intent** in the Developer Portal "
                          "(Bot page) and restart the bot.")
            bot.run(settings.discord_token, log_handler=None)
    except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as error:
        log.error("Discord refused to log in: %s. Check DISCORD_TOKEN and the Message Content Intent.", error)
        sys.exit(CONFIG_EXIT_CODE)
    sys.exit(bot.exit_code)


if __name__ == "__main__":
    main()

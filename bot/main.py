import logging

import discord
from discord import app_commands

from .ai import AIProvider, Message, build_provider
from .config import Settings, load_settings

log = logging.getLogger("bot")

DISCORD_MESSAGE_LIMIT = 2000


class AssistantBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.settings = settings
        self.ai: AIProvider = build_provider(settings)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(ask)

    async def setup_hook(self) -> None:
        # Guild sync shows commands at once; global sync can take an hour to appear.
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s using AI provider %s", self.user, self.ai.name)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # Moderation rules (piracy, scam) go here in the next step.

    async def close(self) -> None:
        await self.ai.aclose()
        await super().close()


@app_commands.command(name="ask", description="Ask the community AI assistant a question")
@app_commands.describe(question="What do you want to know?")
async def ask(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer(thinking=True)
    bot: AssistantBot = interaction.client  # type: ignore[assignment]

    try:
        answer = await bot.ai.complete(
            [
                Message("system", bot.settings.system_prompt),
                Message("user", question),
            ]
        )
    except Exception:
        log.exception("AI request failed")
        await interaction.followup.send("The AI provider failed. Check the bot logs.", ephemeral=True)
        return

    await interaction.followup.send(answer[:DISCORD_MESSAGE_LIMIT] or "(empty reply)")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    AssistantBot(settings).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

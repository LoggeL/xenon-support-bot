"""Discord bot with embeds, rate limiting, and image support."""

import asyncio
import base64
import time
from collections import defaultdict
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

from src.config import settings
from src.agent.runner import AgentRunner, AgentStep
from src.agent.client import OpenRouterClient
from src.docs.scraper import scrape_all_docs
from src.docs.search import doc_search
from src.docs.store import doc_store


# Discord embed limits
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000


class RateLimiter:
    """Simple per-user rate limiter."""

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.user_requests: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to make a request."""
        now = time.time()
        minute_ago = now - 60

        # Clean old requests
        self.user_requests[user_id] = [t for t in self.user_requests[user_id] if t > minute_ago]

        if len(self.user_requests[user_id]) >= self.requests_per_minute:
            return False

        self.user_requests[user_id].append(now)
        return True

    def time_until_allowed(self, user_id: int) -> float:
        """Get seconds until user can make another request."""
        if not self.user_requests[user_id]:
            return 0

        oldest = min(self.user_requests[user_id])
        return max(0, 60 - (time.time() - oldest))


class MessageHistory:
    """Tracks message history per channel."""

    def __init__(self, max_messages: int = 5):
        self.max_messages = max_messages
        self.history: dict[int, list[dict]] = defaultdict(list)

    def add(self, channel_id: int, role: str, content: str):
        """Add a message to history."""
        self.history[channel_id].append({"role": role, "content": content})
        # Keep only last N messages
        self.history[channel_id] = self.history[channel_id][-self.max_messages :]

    def get(self, channel_id: int) -> list[dict]:
        """Get history for a channel."""
        return self.history[channel_id].copy()

    def clear(self, channel_id: int):
        """Clear history for a channel."""
        self.history[channel_id] = []


def truncate_text(text: str, limit: int) -> str:
    """Truncate text to fit within limit."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def create_response_embed(
    content: str,
    steps: list[AgentStep],
    color: discord.Color = discord.Color.blue(),
) -> discord.Embed:
    """Create a response embed with proper length handling."""
    embed = discord.Embed(color=color)

    # Add steps as a field
    if steps:
        steps_text = "\n".join(
            f"{step.emoji} {step.description}" for step in steps if step.type == "tool_call"
        )
        if steps_text:
            embed.add_field(
                name="🔧 Steps Taken",
                value=truncate_text(steps_text, EMBED_FIELD_LIMIT),
                inline=False,
            )

    # Add response as description
    # Account for field length in total
    field_length = sum(len(f.name) + len(f.value) for f in embed.fields)
    available_for_description = min(
        EMBED_DESCRIPTION_LIMIT,
        EMBED_TOTAL_LIMIT - field_length - 100,  # Buffer for title etc
    )

    embed.description = truncate_text(content, available_for_description)

    return embed


def create_thinking_embed(steps: list[AgentStep]) -> discord.Embed:
    """Create an embed showing current thinking/tool steps."""
    embed = discord.Embed(
        title="🤖 Processing...",
        color=discord.Color.orange(),
    )

    if steps:
        steps_text = "\n".join(
            f"{step.emoji} {step.description}" for step in steps if step.type == "tool_call"
        )
        if steps_text:
            embed.description = steps_text
    else:
        embed.description = "🔄 Analyzing your question..."

    return embed


class XenonSupportBot(commands.Bot):
    """Xenon support bot."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
        self.message_history = MessageHistory(max_messages=5)
        self.openrouter_client = OpenRouterClient()
        self.agent_runner = AgentRunner(self.openrouter_client)

    async def setup_hook(self):
        """Set up slash commands."""
        self.tree.add_command(scrape_command)
        self.tree.add_command(clear_history_command)
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Listening in channel: {settings.discord_channel_id}")
        print(f"Admin users: {settings.admin_ids}")

        if not doc_store.is_initialized():
            print("⚠️  Documentation not scraped yet. Run /scrape command.")

    async def on_message(self, message: discord.Message):
        """Handle incoming messages."""
        # Ignore own messages
        if message.author == self.user:
            return

        # Ignore bots
        if message.author.bot:
            return

        # Only respond in configured channel
        if message.channel.id != settings.discord_channel_id:
            return

        # Check rate limit
        if not self.rate_limiter.is_allowed(message.author.id):
            wait_time = self.rate_limiter.time_until_allowed(message.author.id)
            await message.reply(
                embed=discord.Embed(
                    description=f"⏱️ Rate limited. Please wait {wait_time:.0f} seconds.",
                    color=discord.Color.red(),
                ),
                mention_author=False,
            )
            return

        # Check if docs are initialized
        if not doc_store.is_initialized():
            await message.reply(
                embed=discord.Embed(
                    description="📚 Documentation not loaded yet. An admin needs to run `/scrape` first.",
                    color=discord.Color.orange(),
                ),
                mention_author=False,
            )
            return

        # Get message content
        content = message.content.strip()
        if not content and not message.attachments:
            return

        # Extract images from attachments
        images: list[str] = []
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                try:
                    image_data = await attachment.read()
                    images.append(base64.b64encode(image_data).decode("utf-8"))
                except Exception as e:
                    print(f"Failed to read image attachment: {e}")

        # Get conversation history
        history = self.message_history.get(message.channel.id)

        # Send initial "thinking" message
        thinking_embed = create_thinking_embed([])
        reply = await message.reply(embed=thinking_embed, mention_author=False)

        # Run agent and collect steps
        steps: list[AgentStep] = []
        final_response: str | None = None
        is_irrelevant = False

        try:
            async for step in self.agent_runner.run(
                user_message=content,
                history=history,
                images=images if images else None,
            ):
                steps.append(step)

                if step.type == "tool_call":
                    # Update thinking embed with new step
                    thinking_embed = create_thinking_embed(steps)
                    await reply.edit(embed=thinking_embed)

                elif step.type == "irrelevant":
                    is_irrelevant = True
                    break

                elif step.type == "response":
                    final_response = step.response

        except Exception as e:
            print(f"Agent error: {e}")
            error_embed = discord.Embed(
                description="❌ Sorry, I encountered an error processing your request.",
                color=discord.Color.red(),
            )
            await reply.edit(embed=error_embed)
            return

        if is_irrelevant:
            # Delete the thinking message - don't respond to irrelevant questions
            await reply.delete()
            return

        if final_response:
            # Add to history
            self.message_history.add(message.channel.id, "user", content)
            self.message_history.add(message.channel.id, "assistant", final_response)

            # Create final response embed
            response_embed = create_response_embed(
                final_response,
                steps,
                color=discord.Color.green(),
            )
            await reply.edit(embed=response_embed)
        else:
            # No response generated
            await reply.delete()


# Slash commands
@app_commands.command(name="scrape", description="Scrape Xenon documentation (admin only)")
async def scrape_command(interaction: discord.Interaction):
    """Scrape documentation command."""
    if interaction.user.id not in settings.admin_ids:
        await interaction.response.send_message(
            "❌ You don't have permission to run this command.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("📚 Scraping Xenon documentation...", ephemeral=True)

    try:
        docs = await scrape_all_docs()
        section_count = doc_search.rebuild_index()

        await interaction.followup.send(
            f"✅ Scraped {len(docs)} documentation pages and indexed {section_count} sections.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Scraping failed: {e}",
            ephemeral=True,
        )


@app_commands.command(name="clear", description="Clear conversation history for this channel")
async def clear_history_command(interaction: discord.Interaction):
    """Clear conversation history."""
    bot: XenonSupportBot = interaction.client  # type: ignore
    bot.message_history.clear(interaction.channel_id)
    await interaction.response.send_message(
        "🗑️ Conversation history cleared.",
        ephemeral=True,
    )


# Create bot instance
bot = XenonSupportBot()

"""Discord bot with /support command, buttons, and ticket system."""

import asyncio
import base64
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

from src.config import settings
from src.server_config import server_config, ServerSettings
from src.agent.runner import AgentRunner, AgentStep, ButtonData
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
    field_length = sum(len(f.name) + len(str(f.value)) for f in embed.fields)
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


class SupportResponseView(discord.ui.View):
    """View with action buttons for support responses."""

    def __init__(
        self,
        *,
        question: str,
        response_text: str,
        server_settings: ServerSettings,
        link_buttons: list[ButtonData] | None = None,
    ):
        super().__init__(timeout=900)  # 15 minute timeout

        self.question = question
        self.response_text = response_text
        self.server_settings = server_settings

        # Add link buttons from agent (max 3)
        if link_buttons:
            for btn in link_buttons[:3]:
                if btn.type == "link" and btn.url:
                    self.add_item(
                        discord.ui.Button(
                            style=discord.ButtonStyle.link,
                            label=btn.label[:80],  # Discord limit
                            url=btn.url,
                        )
                    )

    @discord.ui.button(label="✅ Resolved", style=discord.ButtonStyle.success)
    async def resolved_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Mark the question as resolved."""
        # Add reaction to original message
        if interaction.message:
            await interaction.message.add_reaction("✅")

        # Disable all buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link:
                item.disabled = True

        # Update message with disabled buttons
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.set_footer(text=f"✅ Marked as resolved by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.primary)
    async def create_ticket_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Create a support ticket thread."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Tickets can only be created in servers.", ephemeral=True
            )
            return

        # Determine where to create the thread
        ticket_channel: discord.TextChannel | discord.Thread | None = None

        if self.server_settings.ticket_channel_id:
            channel = interaction.guild.get_channel(self.server_settings.ticket_channel_id)
            if isinstance(channel, discord.TextChannel):
                ticket_channel = channel

        if not ticket_channel and isinstance(interaction.channel, discord.TextChannel):
            ticket_channel = interaction.channel

        if not ticket_channel:
            await interaction.response.send_message(
                "❌ Could not find a valid channel to create the ticket.", ephemeral=True
            )
            return

        # Create thread
        thread_name = f"Support: {self.question[:50]}{'...' if len(self.question) > 50 else ''}"

        try:
            thread = await ticket_channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
                reason=f"Support ticket created by {interaction.user}",
            )

            # Build ticket message
            ticket_content_parts = [
                f"## 🎫 Support Ticket",
                f"**Created by:** {interaction.user.mention}",
                f"**Original channel:** {interaction.channel.mention if interaction.channel else 'Unknown'}",
                "",
                f"### Question:",
                f"> {self.question}",
                "",
                f"### Bot Response:",
                f"{self.response_text[:1500]}{'...' if len(self.response_text) > 1500 else ''}",
            ]

            # Ping support role if configured
            if self.server_settings.support_role_id:
                role = interaction.guild.get_role(self.server_settings.support_role_id)
                if role:
                    ticket_content_parts.insert(0, f"{role.mention}")

            await thread.send("\n".join(ticket_content_parts))

            # Disable buttons
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link:
                    item.disabled = True

            # Update original message
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            if embed:
                embed.set_footer(text=f"🎫 Ticket created: #{thread.name}")

            await interaction.response.edit_message(embed=embed, view=self)

            # Send confirmation
            await interaction.followup.send(
                f"✅ Created ticket: {thread.mention}", ephemeral=True
            )
            self.stop()

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create threads in the ticket channel.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to create ticket: {e}", ephemeral=True
            )


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
        self.tree.add_command(support_command)
        self.tree.add_command(support_config_group)
        self.tree.add_command(scrape_command)
        self.tree.add_command(clear_history_command)
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Admin users: {settings.admin_ids}")
        print("Bot is ready! Use /support to ask questions.")

        if not doc_store.is_initialized():
            print("⚠️  Documentation not scraped yet. Run /scrape command.")

    async def on_message(self, message: discord.Message):
        """Handle messages in support ticket threads."""
        # Ignore own messages
        if message.author == self.user:
            return

        # Ignore bots
        if message.author.bot:
            return

        # Only respond in threads that start with "Support:"
        if not isinstance(message.channel, discord.Thread):
            return

        if not message.channel.name.startswith("Support:"):
            return

        # Check if docs are initialized
        if not doc_store.is_initialized():
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

        content = message.content.strip()
        if not content:
            return

        # Send thinking message
        thinking_embed = create_thinking_embed([])
        reply = await message.reply(embed=thinking_embed, mention_author=False)

        # Fetch thread context (including bot responses)
        channel_context = await fetch_channel_context(
            message.channel,
            limit=15,
            bot_user=self.user,
        )

        # Get conversation history for this thread
        history = self.message_history.get(message.channel.id)

        # Run agent
        steps: list[AgentStep] = []
        final_response: str | None = None
        is_irrelevant = False

        try:
            async for step in self.agent_runner.run(
                user_message=content,
                history=history,
                channel_context=channel_context,
            ):
                steps.append(step)

                if step.type == "tool_call":
                    thinking_embed = create_thinking_embed(steps)
                    await reply.edit(embed=thinking_embed)

                elif step.type == "irrelevant":
                    is_irrelevant = True
                    break

                elif step.type == "response":
                    final_response = step.response

        except Exception as e:
            print(f"Agent error in thread: {e}")
            error_embed = discord.Embed(
                description="❌ Sorry, I encountered an error processing your request.",
                color=discord.Color.red(),
            )
            await reply.edit(embed=error_embed)
            return

        if is_irrelevant:
            await reply.delete()
            return

        if final_response:
            # Add to history
            self.message_history.add(message.channel.id, "user", content)
            self.message_history.add(message.channel.id, "assistant", final_response)

            # Create response embed (no buttons in thread replies)
            response_embed = create_response_embed(
                final_response,
                steps,
                color=discord.Color.green(),
            )
            await reply.edit(embed=response_embed)
        else:
            await reply.delete()


async def fetch_channel_context(
    channel: discord.TextChannel | discord.Thread | discord.DMChannel,
    limit: int = 10,
    bot_user: discord.User | discord.ClientUser | None = None,
) -> list[dict]:
    """Fetch recent messages from channel for context, including bot responses."""
    context: list[dict] = []

    try:
        async for msg in channel.history(limit=limit):
            if not msg.content:
                continue

            # Include bot's own messages as assistant responses
            if bot_user and msg.author.id == bot_user.id:
                # Extract text from embeds if message has no content
                content = msg.content
                if not content and msg.embeds:
                    # Get description from first embed
                    content = msg.embeds[0].description or ""
                if content:
                    context.append({
                        "author": "Assistant",
                        "content": content[:500],
                        "is_bot": True,
                    })
            elif not msg.author.bot:
                # Regular user message
                context.append({
                    "author": msg.author.display_name,
                    "content": msg.content[:500],
                    "is_bot": False,
                })
    except discord.Forbidden:
        pass  # Can't read history

    # Reverse to chronological order
    context.reverse()
    return context


# Slash commands
@app_commands.command(name="support", description="Ask a question about Xenon")
@app_commands.describe(question="Your question about Xenon bot (optional - uses channel context if not provided)")
async def support_command(interaction: discord.Interaction, question: str | None = None):
    """Handle support questions."""
    bot: XenonSupportBot = interaction.client  # type: ignore

    # Check rate limit
    if not bot.rate_limiter.is_allowed(interaction.user.id):
        wait_time = bot.rate_limiter.time_until_allowed(interaction.user.id)
        await interaction.response.send_message(
            f"⏱️ Rate limited. Please wait {wait_time:.0f} seconds.",
            ephemeral=True,
        )
        return

    # Check if docs are initialized
    if not doc_store.is_initialized():
        await interaction.response.send_message(
            "📚 Documentation not loaded yet. An admin needs to run `/scrape` first.",
            ephemeral=True,
        )
        return

    # Get server settings
    guild_id = interaction.guild_id or 0
    srv_settings = server_config.get(guild_id)

    # Determine if processing message should be ephemeral
    ephemeral = srv_settings.ephemeral_processing

    # Send initial thinking message
    await interaction.response.send_message(
        embed=create_thinking_embed([]),
        ephemeral=ephemeral,
    )

    # Fetch channel context
    channel_context: list[dict] = []
    if interaction.channel and hasattr(interaction.channel, "history"):
        channel_context = await fetch_channel_context(
            interaction.channel,  # type: ignore
            bot_user=bot.user,
        )

    # If no question provided, build one from context
    if not question:
        if not channel_context:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description="❌ No question provided and no recent messages to analyze. Please provide a question.",
                    color=discord.Color.red(),
                )
            )
            return
        # Use the last few messages as the question context
        question = "Based on the recent conversation above, please help answer any Xenon-related questions."

    # Get conversation history
    channel_id = interaction.channel_id or 0
    history = bot.message_history.get(channel_id)

    # Run agent and collect steps
    steps: list[AgentStep] = []
    final_response: str | None = None
    response_buttons: list[ButtonData] = []
    is_irrelevant = False

    try:
        async for step in bot.agent_runner.run(
            user_message=question,
            history=history,
            channel_context=channel_context,
        ):
            steps.append(step)

            if step.type == "tool_call":
                # Update thinking embed with new step
                thinking_embed = create_thinking_embed(steps)
                await interaction.edit_original_response(embed=thinking_embed)

            elif step.type == "irrelevant":
                is_irrelevant = True
                break

            elif step.type == "response":
                final_response = step.response
                response_buttons = step.buttons

    except Exception as e:
        print(f"Agent error: {e}")
        error_embed = discord.Embed(
            description="❌ Sorry, I encountered an error processing your request.",
            color=discord.Color.red(),
        )
        await interaction.edit_original_response(embed=error_embed)
        return

    if is_irrelevant:
        irrelevant_embed = discord.Embed(
            description="🤔 This question doesn't seem to be about Xenon. I can only help with Xenon-related questions.",
            color=discord.Color.greyple(),
        )
        await interaction.edit_original_response(embed=irrelevant_embed)
        return

    if final_response:
        # Add to history
        bot.message_history.add(channel_id, "user", question)
        bot.message_history.add(channel_id, "assistant", final_response)

        # Create final response embed
        response_embed = create_response_embed(
            final_response,
            steps,
            color=discord.Color.green(),
        )

        # Create view with buttons
        view = SupportResponseView(
            question=question,
            response_text=final_response,
            server_settings=srv_settings,
            link_buttons=response_buttons,
        )

        # If ephemeral, we need to send a new public message
        if ephemeral:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description="✅ Response posted below.",
                    color=discord.Color.green(),
                ),
                view=None,
            )
            await interaction.followup.send(
                embed=response_embed,
                view=view,
            )
        else:
            await interaction.edit_original_response(embed=response_embed, view=view)
    else:
        # No response generated
        no_response_embed = discord.Embed(
            description="🤔 I couldn't generate a response. Please try rephrasing your question.",
            color=discord.Color.orange(),
        )
        await interaction.edit_original_response(embed=no_response_embed)


# Config command group
support_config_group = app_commands.Group(
    name="support-config",
    description="Configure Xenon support bot settings for this server",
    default_permissions=discord.Permissions(manage_guild=True),
    guild_only=True,
)


@support_config_group.command(name="show", description="Show current server settings")
async def config_show(interaction: discord.Interaction):
    """Show current configuration."""
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command only works in servers.", ephemeral=True)
        return

    srv_settings = server_config.get(interaction.guild_id)

    embed = discord.Embed(
        title="⚙️ Support Bot Configuration",
        color=discord.Color.blue(),
    )

    # Support role
    if srv_settings.support_role_id:
        role = interaction.guild.get_role(srv_settings.support_role_id) if interaction.guild else None
        embed.add_field(
            name="Support Role",
            value=role.mention if role else f"ID: {srv_settings.support_role_id} (not found)",
            inline=True,
        )
    else:
        embed.add_field(name="Support Role", value="Not set", inline=True)

    # Ticket channel
    if srv_settings.ticket_channel_id:
        channel = interaction.guild.get_channel(srv_settings.ticket_channel_id) if interaction.guild else None
        embed.add_field(
            name="Ticket Channel",
            value=channel.mention if channel else f"ID: {srv_settings.ticket_channel_id} (not found)",
            inline=True,
        )
    else:
        embed.add_field(name="Ticket Channel", value="Current channel (default)", inline=True)

    # Processing visibility
    embed.add_field(
        name="Processing Messages",
        value="Ephemeral (private)" if srv_settings.ephemeral_processing else "Public (default)",
        inline=True,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@support_config_group.command(name="support-role", description="Set the role to ping for tickets")
@app_commands.describe(role="The role to ping when tickets are created")
async def config_support_role(interaction: discord.Interaction, role: discord.Role | None = None):
    """Set the support role."""
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command only works in servers.", ephemeral=True)
        return

    server_config.update(
        interaction.guild_id,
        support_role_id=role.id if role else None,
    )

    if role:
        await interaction.response.send_message(
            f"✅ Support role set to {role.mention}. This role will be pinged when tickets are created.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "✅ Support role cleared. No role will be pinged for tickets.",
            ephemeral=True,
        )


@support_config_group.command(name="ticket-channel", description="Set the channel for ticket threads")
@app_commands.describe(channel="The channel where ticket threads will be created")
async def config_ticket_channel(
    interaction: discord.Interaction, channel: discord.TextChannel | None = None
):
    """Set the ticket channel."""
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command only works in servers.", ephemeral=True)
        return

    server_config.update(
        interaction.guild_id,
        ticket_channel_id=channel.id if channel else None,
    )

    if channel:
        await interaction.response.send_message(
            f"✅ Ticket channel set to {channel.mention}. Ticket threads will be created there.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "✅ Ticket channel cleared. Tickets will be created in the channel where `/support` was used.",
            ephemeral=True,
        )


@support_config_group.command(name="processing-visibility", description="Set whether processing messages are public or private")
@app_commands.describe(ephemeral="True for private (only user sees), False for public (everyone sees)")
async def config_processing_visibility(interaction: discord.Interaction, ephemeral: bool):
    """Set processing message visibility."""
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command only works in servers.", ephemeral=True)
        return

    server_config.update(
        interaction.guild_id,
        ephemeral_processing=ephemeral,
    )

    if ephemeral:
        await interaction.response.send_message(
            "✅ Processing messages will now be **private** (only the user sees 'Processing...').",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "✅ Processing messages will now be **public** (everyone sees 'Processing...').",
            ephemeral=True,
        )


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
    channel_id = interaction.channel_id or 0
    bot.message_history.clear(channel_id)
    await interaction.response.send_message(
        "🗑️ Conversation history cleared.",
        ephemeral=True,
    )


# Create bot instance
bot = XenonSupportBot()

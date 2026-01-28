"""Discord bot with menu-based support system and analytics."""

import time
from collections import defaultdict
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from src.config import settings
from src.server_config import server_config
from src.agent.runner import AgentRunner, AgentStep, ButtonData
from src.agent.client import OpenRouterClient
from src.docs.scraper import scrape_all_docs
from src.docs.search import doc_search
from src.docs.store import doc_store
from src.analytics import analytics
from src.views.support_menu import (
    SupportMenuView,
    SupportResponseView,
    create_menu_embed,
)


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
        self.openrouter_client = OpenRouterClient()
        self.agent_runner = AgentRunner(self.openrouter_client)
        self.start_time = datetime.utcnow()

    async def setup_hook(self):
        """Set up slash commands and persistent views."""
        # Register persistent view
        self.add_view(SupportMenuView(on_question=self.handle_question))

        # Add commands
        self.tree.add_command(setup_support_menu_command)
        self.tree.add_command(support_analytics_command)
        self.tree.add_command(support_unanswered_command)
        self.tree.add_command(support_config_group)
        self.tree.add_command(scrape_command)
        self.tree.add_command(stats_command)
        self.tree.add_command(about_command)
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Admin users: {settings.admin_ids}")
        print("Bot is ready! Use /setup-support-menu to create a support channel.")

        if not doc_store.is_initialized():
            print("⚠️  Documentation not scraped yet. Run /scrape command.")

    async def rephrase_for_community(self, question: str) -> str:
        """Rephrase a question to be clearer for community support."""
        from src.agent.client import Message

        messages = [
            Message(
                role="system",
                content=(
                    "You are a helpful assistant. Rephrase the user's question to be clearer "
                    "and more concise for community support volunteers to understand. "
                    "Keep the core problem but make it easier to read. "
                    "Output ONLY the rephrased question, nothing else. "
                    "Keep it under 200 characters if possible."
                ),
            ),
            Message(role="user", content=question),
        ]

        try:
            response = await self.openrouter_client.chat(messages)
            if response.content:
                return response.content.strip()
        except Exception:
            pass

        return question  # Fall back to original

    async def handle_question(
        self,
        interaction: discord.Interaction,
        question: str,
    ) -> None:
        """Handle a support question from the menu modal."""
        # Check rate limit
        if not self.rate_limiter.is_allowed(interaction.user.id):
            wait_time = self.rate_limiter.time_until_allowed(interaction.user.id)
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

        # Send initial processing message (ephemeral)
        await interaction.response.send_message(
            embed=create_thinking_embed([]),
            ephemeral=True,
        )

        # Log question to analytics
        question_id = await analytics.log_question(
            guild_id=guild_id,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            question=question,
        )

        # Tool call callback for analytics
        async def on_tool_call(name: str, args: dict, result: dict) -> None:
            await analytics.log_tool_call(question_id, name, args, result)

        # Run agent
        steps: list[AgentStep] = []
        final_response: str | None = None
        response_buttons: list[ButtonData] = []
        is_irrelevant = False

        try:
            async for step in self.agent_runner.run(
                user_message=question,
                on_tool_call=on_tool_call,
            ):
                steps.append(step)

                if step.type == "tool_call":
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
            # Create response embed
            response_embed = create_response_embed(
                final_response,
                steps,
                color=discord.Color.green(),
            )

            # Rephrase callback for community support
            async def rephrase_question(q: str) -> str:
                return await self.rephrase_for_community(q)

            # Add link buttons from agent response
            view = SupportResponseView(
                question_id=question_id,
                original_question=question,
                bot_response=final_response,
                community_channel_id=srv_settings.community_support_channel_id,
                on_resolved=analytics.mark_answered,
                on_community_support=analytics.mark_community_support,
                on_rephrase=rephrase_question,
            )

            # Add link buttons from agent
            for btn in response_buttons[:3]:
                if btn.type == "link" and btn.url:
                    view.add_item(
                        discord.ui.Button(
                            style=discord.ButtonStyle.link,
                            label=btn.label[:80],
                            url=btn.url,
                        )
                    )

            await interaction.edit_original_response(embed=response_embed, view=view)
        else:
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

    # Support channel
    if srv_settings.support_channel_id:
        channel = interaction.guild.get_channel(srv_settings.support_channel_id) if interaction.guild else None
        embed.add_field(
            name="Support Channel",
            value=channel.mention if channel else f"ID: {srv_settings.support_channel_id} (not found)",
            inline=True,
        )
    else:
        embed.add_field(name="Support Channel", value="Not set", inline=True)

    # Menu message
    if srv_settings.menu_message_id:
        embed.add_field(
            name="Menu Message ID",
            value=str(srv_settings.menu_message_id),
            inline=True,
        )
    else:
        embed.add_field(name="Menu Message", value="Not set", inline=True)

    # Community support channel
    if srv_settings.community_support_channel_id:
        channel = interaction.guild.get_channel(srv_settings.community_support_channel_id) if interaction.guild else None
        embed.add_field(
            name="Community Support Channel",
            value=channel.mention if channel else f"ID: {srv_settings.community_support_channel_id} (not found)",
            inline=True,
        )
    else:
        embed.add_field(name="Community Support Channel", value="Not set", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


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


@app_commands.command(
    name="setup-support-menu",
    description="Set up the support menu in a channel (admin only)",
)
@app_commands.describe(
    channel="The channel to post the support menu in",
    community_channel="The channel to link for community support (optional)",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def setup_support_menu_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    community_channel: discord.TextChannel | None = None,
):
    """Set up the support menu."""
    bot: XenonSupportBot = interaction.client  # type: ignore

    if not interaction.guild_id:
        await interaction.response.send_message(
            "❌ This command only works in servers.",
            ephemeral=True,
        )
        return

    # Create menu embed and view
    embed = create_menu_embed()
    view = SupportMenuView(on_question=bot.handle_question)

    # Post menu message
    try:
        message = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ I don't have permission to send messages in {channel.mention}.",
            ephemeral=True,
        )
        return

    # Save config
    server_config.update(
        interaction.guild_id,
        support_channel_id=channel.id,
        menu_message_id=message.id,
        community_support_channel_id=community_channel.id if community_channel else None,
    )

    response_text = f"✅ Support menu posted in {channel.mention}."
    if community_channel:
        response_text += f"\n💬 Community support channel set to {community_channel.mention}."

    await interaction.response.send_message(response_text, ephemeral=True)


@app_commands.command(
    name="support-analytics",
    description="View support analytics (admin only)",
)
@app_commands.describe(days="Number of days to analyze (default: 7)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def support_analytics_command(
    interaction: discord.Interaction,
    days: int = 7,
):
    """Show support analytics."""
    if not interaction.guild_id:
        await interaction.response.send_message(
            "❌ This command only works in servers.",
            ephemeral=True,
        )
        return

    stats = await analytics.get_stats(interaction.guild_id, days=days)

    embed = discord.Embed(
        title=f"📊 Support Analytics (Last {days} Days)",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Total Questions", value=str(stats["total"]), inline=True)
    embed.add_field(name="Answered", value=str(stats["answered"]), inline=True)
    embed.add_field(name="Unanswered", value=str(stats["unanswered"]), inline=True)
    embed.add_field(
        name="Answer Rate",
        value=f"{stats['answer_rate']:.1f}%",
        inline=True,
    )
    embed.add_field(
        name="Community Support Clicks",
        value=str(stats["community_support_clicked"]),
        inline=True,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(
    name="support-unanswered",
    description="View recent unanswered questions (admin only)",
)
@app_commands.describe(
    days="Number of days to look back (default: 7)",
    limit="Maximum number of questions to show (default: 10)",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def support_unanswered_command(
    interaction: discord.Interaction,
    days: int = 7,
    limit: int = 10,
):
    """Show unanswered questions."""
    if not interaction.guild_id:
        await interaction.response.send_message(
            "❌ This command only works in servers.",
            ephemeral=True,
        )
        return

    questions = await analytics.get_unanswered(
        interaction.guild_id,
        days=days,
        limit=limit,
    )

    if not questions:
        await interaction.response.send_message(
            f"✅ No unanswered questions in the last {days} days!",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"❓ Unanswered Questions (Last {days} Days)",
        color=discord.Color.orange(),
    )

    for i, q in enumerate(questions[:10], 1):
        question_preview = q.question[:100] + "..." if len(q.question) > 100 else q.question
        community = " 💬" if q.community_support_clicked else ""
        embed.add_field(
            name=f"{i}. {q.created_at.strftime('%Y-%m-%d %H:%M')}{community}",
            value=question_preview,
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


def format_uptime(delta) -> str:
    """Format a timedelta as a human-readable string."""
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


@app_commands.command(
    name="stats",
    description="View bot statistics",
)
async def stats_command(interaction: discord.Interaction):
    """Show bot statistics."""
    bot: XenonSupportBot = interaction.client  # type: ignore

    # Get global analytics stats
    stats = await analytics.get_global_stats()

    # Calculate uptime
    uptime = datetime.utcnow() - bot.start_time
    uptime_str = format_uptime(uptime)

    # Get bot info
    guild_count = len(bot.guilds)
    user_count = sum(g.member_count or 0 for g in bot.guilds)

    # Create embed
    embed = discord.Embed(
        title="📊 Xenon Support Bot Stats",
        color=discord.Color.blue(),
    )

    # Bot stats
    embed.add_field(
        name="🤖 Bot",
        value=f"```\n"
              f"Servers:  {guild_count:,}\n"
              f"Users:    {user_count:,}\n"
              f"Uptime:   {uptime_str}\n"
              f"```",
        inline=True,
    )

    # Questions stats
    answer_rate = stats["answer_rate"]
    rate_bar = "█" * int(answer_rate / 10) + "░" * (10 - int(answer_rate / 10))
    embed.add_field(
        name="❓ Questions",
        value=f"```\n"
              f"Total:    {stats['total_questions']:,}\n"
              f"Today:    {stats['questions_today']:,}\n"
              f"Week:     {stats['questions_week']:,}\n"
              f"```",
        inline=True,
    )

    # Performance stats
    embed.add_field(
        name="✅ Performance",
        value=f"```\n"
              f"Answered: {stats['total_answered']:,}\n"
              f"Rate:     {answer_rate:.1f}%\n"
              f"[{rate_bar}]\n"
              f"```",
        inline=True,
    )

    # Usage stats
    embed.add_field(
        name="👥 Usage",
        value=f"```\n"
              f"Unique Users:   {stats['unique_users']:,}\n"
              f"Active Servers: {stats['unique_guilds']:,}\n"
              f"Tool Calls:     {stats['total_tool_calls']:,}\n"
              f"```",
        inline=False,
    )

    # Footer
    embed.set_footer(text="Powered by Xenon Support Bot • Made with ❤️")

    await interaction.response.send_message(embed=embed)


@app_commands.command(
    name="about",
    description="Learn about the Xenon Support Bot",
)
async def about_command(interaction: discord.Interaction):
    """Show information about the bot."""
    bot: XenonSupportBot = interaction.client  # type: ignore

    embed = discord.Embed(
        title="🤖 About Xenon Support Bot",
        description=(
            "An AI-powered support assistant for [Xenon](https://xenon.bot), "
            "the Discord backup & template bot.\n\n"
            "I use **agentic RAG** (Retrieval-Augmented Generation) to answer "
            "your questions based on the official Xenon documentation."
        ),
        color=discord.Color.blue(),
    )

    # Features
    embed.add_field(
        name="✨ Features",
        value=(
            "• AI-powered answers from official docs\n"
            "• Real-time document search\n"
            "• Analytics tracking\n"
            "• Community support fallback"
        ),
        inline=True,
    )

    # Commands
    embed.add_field(
        name="📜 Commands",
        value=(
            "`/stats` - View bot statistics\n"
            "`/about` - This message\n"
            "`/support-config show` - Server settings"
        ),
        inline=True,
    )

    # Admin commands
    embed.add_field(
        name="🔧 Admin Commands",
        value=(
            "`/setup-support-menu` - Create menu\n"
            "`/support-analytics` - View analytics\n"
            "`/support-unanswered` - Unanswered Q's\n"
            "`/scrape` - Update docs"
        ),
        inline=True,
    )

    # Links
    embed.add_field(
        name="🔗 Links",
        value=(
            "[Xenon Bot](https://xenon.bot) • "
            "[Documentation](https://wiki.xenon.bot) • "
            "[Support Server](https://xenon.bot/discord)"
        ),
        inline=False,
    )

    # Footer with version and branding
    embed.set_footer(
        text=f"Xenon Support Bot v1.0 • Serving {len(bot.guilds)} servers • Made by LMF",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )

    # Author with LMF branding
    embed.set_author(
        name="Made by LMF",
        url="https://lmf.logge.top/",
    )

    await interaction.response.send_message(embed=embed)


# Create bot instance
bot = XenonSupportBot()

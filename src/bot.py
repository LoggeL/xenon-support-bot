"""Discord bot with menu-based support system and analytics."""

import logging
import random
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.admin_store import AdminStore
from src.agent.client import OpenAIResponsesClient
from src.agent.runner import AgentRunner, AgentStep, ButtonData
from src.analytics import analytics
from src.config import Settings
from src.database import close_pool, database, init_schema
from src.docs.scraper import scrape_all_docs
from src.docs.search import doc_search
from src.docs.store import doc_store
from src.server_config import ServerSettings, server_config
from src.views.support_menu import (
    SupportMenuView,
    SupportResponseView,
    create_menu_embed,
)

logger = logging.getLogger(__name__)


# Discord embed limits
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024


def get_git_commit() -> str:
    """Get the current git commit hash (short)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# Cache commit at startup
GIT_COMMIT = get_git_commit()
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
    steps_summary: str | None = None,
    question: str | None = None,
    user: discord.User | discord.Member | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    """Create a response embed with proper length handling."""
    embed = discord.Embed(color=color or discord.Color.blue())

    # Add user's question as author
    if question and user:
        embed.set_author(
            name=f"{user.display_name} asked:",
            icon_url=user.display_avatar.url,
        )
        embed.title = truncate_text(question, 256)

    # Add steps summary as a field
    if steps_summary:
        embed.add_field(
            name="🔧 What I checked",
            value=truncate_text(steps_summary, EMBED_FIELD_LIMIT),
            inline=False,
        )

    # Add response as description
    # Account for field length in total
    field_length = sum(len(field.name or "") + len(str(field.value)) for field in embed.fields)
    title_length = len(embed.title or "")
    available_for_description = min(
        EMBED_DESCRIPTION_LIMIT,
        EMBED_TOTAL_LIMIT - field_length - title_length - 150,  # Buffer for author etc
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

    def __init__(self, config: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.config = config
        self.rate_limiter = RateLimiter(config.rate_limit_per_minute)
        self.model_client = OpenAIResponsesClient(config)
        self.agent_runner = AgentRunner(self.model_client)
        self.admin_store = AdminStore(config.parsed_admin_user_ids)
        self.start_time = datetime.now(UTC)

    async def setup_hook(self):
        """Set up slash commands and persistent views."""
        database.configure(self.config.database_url)
        await init_schema()

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
        logger.info("Logged in as %s", self.user)
        logger.info("Configured bot administrators: %s", sorted(self.admin_store.get_all()))

        if not await doc_store.is_initialized():
            logger.warning("Documentation is not loaded; run /scrape")

        # Start status rotation
        if not self.rotate_status.is_running():
            self.rotate_status.start()

    async def close(self) -> None:
        """Release external clients before Discord shuts down."""
        self.rotate_status.cancel()
        await self.model_client.close()
        await close_pool()
        await super().close()

    @tasks.loop(minutes=10)
    async def rotate_status(self):
        """Rotate bot status every 10 minutes."""
        statuses = [
            # Normal statuses
            (discord.ActivityType.watching, "for your questions"),
            (discord.ActivityType.listening, "Xenon support requests"),
            (discord.ActivityType.watching, "the docs so you don't have to"),
            (discord.ActivityType.playing, "with the Xenon API"),
            (discord.ActivityType.watching, f"{len(self.guilds)} servers"),
            (discord.ActivityType.listening, "/about for info"),
            (discord.ActivityType.watching, "backups being created"),
            (discord.ActivityType.playing, "template librarian"),
            (discord.ActivityType.listening, "your server needs"),
            (discord.ActivityType.watching, "templates being synced"),
            # Fun statuses
            (discord.ActivityType.playing, "with server backups"),
            (discord.ActivityType.watching, "Discord grow"),
            (discord.ActivityType.listening, "the sound of data"),
            (discord.ActivityType.playing, "backup roulette"),
            # Easter eggs
            (discord.ActivityType.playing, "sudo rm -rf / (jk)"),
            (discord.ActivityType.watching, "you read this"),
            (discord.ActivityType.listening, "never gonna give you up"),
            (discord.ActivityType.playing, "in 4K resolution"),
            (discord.ActivityType.watching, "the mass of backup data"),
            (discord.ActivityType.playing, "hide and seek with bugs"),
        ]

        activity_type, name = random.choice(statuses)
        activity = discord.Activity(type=activity_type, name=name)
        await self.change_presence(activity=activity, status=discord.Status.online)

    @rotate_status.before_loop
    async def before_rotate_status(self):
        """Wait until bot is ready before starting rotation."""
        await self.wait_until_ready()

    async def rephrase_for_community(self, question: str) -> str:
        """Rephrase a question to be clearer for community support."""
        try:
            return await self.model_client.generate_text(
                instructions=(
                    "Rewrite the question for community support volunteers. Preserve all facts, "
                    "remove ambiguity, use at most 200 characters, and output only the question."
                ),
                prompt=question,
            )
        except Exception:
            logger.exception("Could not rephrase a community-support question")

        return question

    async def summarize_steps(self, steps: list[AgentStep]) -> str | None:
        """Summarize the steps taken by the agent in a concise way."""
        pages = [
            str(step.tool_args.get("slug"))
            for step in steps
            if step.type == "tool_call"
            and step.tool_name == "get_doc"
            and step.tool_args
            and step.tool_args.get("slug")
        ]
        pages = list(dict.fromkeys(pages))
        if not pages:
            return None
        return f"Checked {', '.join(pages[:3])} documentation."

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

        # Auto-scrape if docs aren't initialized
        if not await doc_store.is_initialized():
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="📚 Documentation not loaded yet. Auto-scraping...",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )

            try:
                docs = await scrape_all_docs()
                await doc_search.rebuild_index()
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        description=(
                            f"✅ Scraped {len(docs)} documentation pages. "
                            "Processing your question..."
                        ),
                        color=discord.Color.green(),
                    )
                )
            except Exception as e:
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        description=(
                            f"❌ Auto-scrape failed: {e}. Please ask an admin to run `/scrape`."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return
        else:
            # Send initial processing message (ephemeral)
            await interaction.response.send_message(
                embed=create_thinking_embed([]),
                ephemeral=True,
            )

        # Get server settings
        guild_id = interaction.guild_id or 0
        srv_settings = await server_config.get(guild_id)

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

        except Exception:
            logger.exception("Agent failed while answering a question")
            error_embed = discord.Embed(
                description="❌ Sorry, I encountered an error processing your request.",
                color=discord.Color.red(),
            )
            await interaction.edit_original_response(embed=error_embed)
            return

        if is_irrelevant:
            irrelevant_embed = discord.Embed(
                description=(
                    "🤔 This question doesn't seem to be about Xenon. "
                    "I can only help with Xenon-related questions."
                ),
                color=discord.Color.greyple(),
            )
            await interaction.edit_original_response(embed=irrelevant_embed)
            return

        if final_response:
            # Summarize steps for display
            steps_summary = await self.summarize_steps(steps)

            # Create response embed with user's question
            response_embed = create_response_embed(
                final_response,
                steps_summary=steps_summary,
                question=question,
                user=interaction.user,
                color=discord.Color.green(),
            )

            # Rephrase callback for community support
            async def rephrase_question(q: str) -> str:
                return await self.rephrase_for_community(q)

            # Extract step descriptions for community support
            steps_taken = [
                step.description for step in steps if step.type == "tool_call" and step.description
            ]

            # Build conversation history for follow-ups
            conversation_history = [
                {"role": "user", "content": question},
                {"role": "assistant", "content": final_response},
            ]

            # Follow-up callback
            async def handle_followup(
                followup_interaction: discord.Interaction,
                followup_question: str,
                history: list[dict],
            ) -> None:
                await self.handle_followup_question(
                    followup_interaction,
                    followup_question,
                    history,
                    guild_id,
                    srv_settings,
                )

            # Add link buttons from agent response
            view = SupportResponseView(
                question_id=question_id,
                original_question=question,
                bot_response=final_response,
                steps_taken=steps_taken,
                conversation_history=conversation_history,
                community_channel_id=srv_settings.community_support_channel_id,
                on_resolved=analytics.mark_answered,
                on_community_support=analytics.mark_community_support,
                on_followup=handle_followup,
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
                description=(
                    "🤔 I couldn't generate a response. Please try rephrasing your question."
                ),
                color=discord.Color.orange(),
            )
            await interaction.edit_original_response(embed=no_response_embed)

    async def handle_followup_question(
        self,
        interaction: discord.Interaction,
        question: str,
        history: list[dict],
        guild_id: int,
        srv_settings: ServerSettings,
    ) -> None:
        """Handle a follow-up question with conversation history."""
        # Check rate limit
        if not self.rate_limiter.is_allowed(interaction.user.id):
            wait_time = self.rate_limiter.time_until_allowed(interaction.user.id)
            await interaction.response.send_message(
                f"⏱️ Rate limited. Please wait {wait_time:.0f} seconds.",
                ephemeral=True,
            )
            return

        # Send processing message
        await interaction.response.send_message(
            embed=create_thinking_embed([]),
            ephemeral=True,
        )

        # Log question to analytics
        question_id = await analytics.log_question(
            guild_id=guild_id,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            question=f"[Follow-up] {question}",
        )

        # Tool call callback for analytics
        async def on_tool_call(name: str, args: dict, result: dict) -> None:
            await analytics.log_tool_call(question_id, name, args, result)

        # Run agent with history
        steps: list[AgentStep] = []
        final_response: str | None = None
        response_buttons: list[ButtonData] = []
        is_irrelevant = False

        try:
            async for step in self.agent_runner.run(
                user_message=question,
                history=history,
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

        except Exception:
            logger.exception("Agent failed while answering a follow-up")
            error_embed = discord.Embed(
                description="❌ Sorry, I encountered an error processing your request.",
                color=discord.Color.red(),
            )
            await interaction.edit_original_response(embed=error_embed)
            return

        if is_irrelevant:
            irrelevant_embed = discord.Embed(
                description=(
                    "🤔 This question doesn't seem to be about Xenon. "
                    "I can only help with Xenon-related questions."
                ),
                color=discord.Color.greyple(),
            )
            await interaction.edit_original_response(embed=irrelevant_embed)
            return

        if final_response:
            # Summarize steps for display
            steps_summary = await self.summarize_steps(steps)

            # Create response embed with user's question
            response_embed = create_response_embed(
                final_response,
                steps_summary=steps_summary,
                question=question,
                user=interaction.user,
                color=discord.Color.green(),
            )

            # Rephrase callback for community support
            async def rephrase_question(q: str) -> str:
                return await self.rephrase_for_community(q)

            # Extract step descriptions for community support
            steps_taken = [
                step.description for step in steps if step.type == "tool_call" and step.description
            ]

            # Build updated conversation history
            new_history = [
                *history,
                {"role": "user", "content": question},
                {"role": "assistant", "content": final_response},
            ]

            # Follow-up callback
            async def handle_followup(
                followup_interaction: discord.Interaction,
                followup_question: str,
                hist: list[dict],
            ) -> None:
                await self.handle_followup_question(
                    followup_interaction,
                    followup_question,
                    hist,
                    guild_id,
                    srv_settings,
                )

            # Create view with buttons
            view = SupportResponseView(
                question_id=question_id,
                original_question=question,
                bot_response=final_response,
                steps_taken=steps_taken,
                conversation_history=new_history,
                community_channel_id=srv_settings.community_support_channel_id,
                on_resolved=analytics.mark_answered,
                on_community_support=analytics.mark_community_support,
                on_followup=handle_followup,
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
                description=(
                    "🤔 I couldn't generate a response. Please try rephrasing your question."
                ),
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
        await interaction.response.send_message(
            "❌ This command only works in servers.", ephemeral=True
        )
        return

    srv_settings = await server_config.get(interaction.guild_id)

    embed = discord.Embed(
        title="⚙️ Support Bot Configuration",
        color=discord.Color.blue(),
    )

    # Support channel
    if srv_settings.support_channel_id:
        channel = (
            interaction.guild.get_channel(srv_settings.support_channel_id)
            if interaction.guild
            else None
        )
        embed.add_field(
            name="Support Channel",
            value=channel.mention
            if channel
            else f"ID: {srv_settings.support_channel_id} (not found)",
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
        channel = (
            interaction.guild.get_channel(srv_settings.community_support_channel_id)
            if interaction.guild
            else None
        )
        embed.add_field(
            name="Community Support Channel",
            value=channel.mention
            if channel
            else f"ID: {srv_settings.community_support_channel_id} (not found)",
            inline=True,
        )
    else:
        embed.add_field(name="Community Support Channel", value="Not set", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="scrape", description="Scrape Xenon documentation (admin only)")
async def scrape_command(interaction: discord.Interaction):
    """Scrape documentation command."""
    bot = interaction.client
    if not isinstance(bot, XenonSupportBot):
        await interaction.response.send_message("❌ Bot is not ready.", ephemeral=True)
        return
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not bot.admin_store.is_admin_in_context(interaction.user.id, member):
        await interaction.response.send_message(
            "❌ You don't have permission to run this command.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("📚 Scraping Xenon documentation...", ephemeral=True)

    try:
        docs = await scrape_all_docs()
        section_count = await doc_search.rebuild_index()

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
    await server_config.update(
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
    uptime = datetime.now(UTC) - bot.start_time
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

    # Footer with LMF branding
    embed.set_footer(text="Xenon Support Bot • Made by LMF • lmf.logge.top")

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        name="🔗 Xenon Links",
        value=(
            "[Xenon Bot](https://xenon.bot) • "
            "[Documentation](https://wiki.xenon.bot) • "
            "[Support Server](https://xenon.bot/discord)"
        ),
        inline=False,
    )

    # Developer branding
    embed.add_field(
        name="👨‍💻 Developer",
        value=(
            "Created by **LMF** • [Portfolio](https://lmf.logge.top/)\n"
            "A Modern Frontier of Innovation"
        ),
        inline=False,
    )

    # Footer with version and commit
    embed.set_footer(
        text=f"Xenon Support Bot v1.0 ({GIT_COMMIT}) • Serving {len(bot.guilds)} servers",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )

    # Author with LMF branding
    embed.set_author(
        name="LMF",
        url="https://lmf.logge.top/",
    )

    await interaction.response.send_message(embed=embed)

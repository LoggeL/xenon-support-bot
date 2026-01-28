# Menu-Based Support System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `/support` command with channel-based menu button that opens a modal, logs all questions to SQLite, and provides analytics.

**Architecture:** Persistent menu view with button → modal → ephemeral processing → ephemeral response with buttons. SQLite tracks all questions and tool calls for analytics.

**Tech Stack:** discord.py 2.3+, aiosqlite (new dep), Python 3.11+

---

## Task 1: Add aiosqlite dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add dependency**

Edit `pyproject.toml` dependencies to add aiosqlite:

```toml
dependencies = [
    "discord.py>=2.3.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12.0",
    "lxml>=5.0.0",
    "whoosh>=2.7.4",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "python-dotenv>=1.0.0",
    "aiosqlite>=0.19.0",
]
```

**Step 2: Install dependencies**

Run: `cd ~/projects/xenon-support-bot && pip install -e .`

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add aiosqlite dependency for analytics"
```

---

## Task 2: Create analytics module

**Files:**
- Create: `src/analytics.py`

**Step 1: Create the analytics module**

```python
"""Analytics storage using SQLite for question tracking."""

import aiosqlite
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import settings


@dataclass
class QuestionRecord:
    """A logged question with metadata."""
    id: int
    guild_id: int
    user_id: int
    channel_id: int
    question: str
    answered: bool
    community_support_clicked: bool
    created_at: datetime


class Analytics:
    """SQLite-based analytics for question tracking."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (settings.data_dir / "analytics.db")
        self._initialized = False

    async def init(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answered INTEGER NOT NULL DEFAULT 0,
                    community_support_clicked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL REFERENCES questions(id),
                    tool_name TEXT NOT NULL,
                    arguments TEXT,
                    result TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_guild ON questions(guild_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_questions_answered ON questions(answered)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_calls_question ON tool_calls(question_id)"
            )
            await db.commit()

        self._initialized = True

    async def log_question(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        question: str,
    ) -> int:
        """Log a new question, returns question_id."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO questions (guild_id, user_id, channel_id, question)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, channel_id, question),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def log_tool_call(
        self,
        question_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Log a tool call for a question."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO tool_calls (question_id, tool_name, arguments, result)
                VALUES (?, ?, ?, ?)
                """,
                (question_id, tool_name, json.dumps(arguments), json.dumps(result)),
            )
            await db.commit()

    async def mark_answered(self, question_id: int) -> None:
        """Mark a question as answered."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE questions SET answered = 1 WHERE id = ?",
                (question_id,),
            )
            await db.commit()

    async def mark_community_support(self, question_id: int) -> None:
        """Mark that community support was clicked."""
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE questions SET community_support_clicked = 1 WHERE id = ?",
                (question_id,),
            )
            await db.commit()

    async def get_unanswered(
        self,
        guild_id: int,
        days: int = 7,
        limit: int = 10,
    ) -> list[QuestionRecord]:
        """Get recent unanswered questions."""
        await self.init()
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM questions
                WHERE guild_id = ? AND answered = 0 AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, cutoff.isoformat(), limit),
            )
            rows = await cursor.fetchall()
            return [
                QuestionRecord(
                    id=row["id"],
                    guild_id=row["guild_id"],
                    user_id=row["user_id"],
                    channel_id=row["channel_id"],
                    question=row["question"],
                    answered=bool(row["answered"]),
                    community_support_clicked=bool(row["community_support_clicked"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def get_stats(self, guild_id: int, days: int = 7) -> dict[str, Any]:
        """Get analytics stats for a guild."""
        await self.init()
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with aiosqlite.connect(self.db_path) as db:
            # Total questions
            cursor = await db.execute(
                "SELECT COUNT(*) FROM questions WHERE guild_id = ? AND created_at >= ?",
                (guild_id, cutoff.isoformat()),
            )
            total = (await cursor.fetchone())[0]

            # Answered questions
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = ? AND answered = 1 AND created_at >= ?
                """,
                (guild_id, cutoff.isoformat()),
            )
            answered = (await cursor.fetchone())[0]

            # Community support clicked
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM questions
                WHERE guild_id = ? AND community_support_clicked = 1 AND created_at >= ?
                """,
                (guild_id, cutoff.isoformat()),
            )
            community_clicked = (await cursor.fetchone())[0]

            return {
                "total": total,
                "answered": answered,
                "unanswered": total - answered,
                "answer_rate": (answered / total * 100) if total > 0 else 0,
                "community_support_clicked": community_clicked,
                "days": days,
            }


# Global instance
analytics = Analytics()
```

**Step 2: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.analytics import analytics; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add src/analytics.py
git commit -m "feat: add analytics module with SQLite storage"
```

---

## Task 3: Update server configuration

**Files:**
- Modify: `src/server_config.py`

**Step 1: Add new fields to ServerSettings**

Add these fields to the `ServerSettings` dataclass (after line 18):

```python
@dataclass
class ServerSettings:
    """Configuration for a single server."""

    guild_id: int
    support_role_id: int | None = None
    ticket_channel_id: int | None = None
    ephemeral_processing: bool = False
    # New fields for menu-based support
    support_channel_id: int | None = None
    menu_message_id: int | None = None
    community_support_channel_id: int | None = None
```

**Step 2: Update from_dict to handle new fields**

Update the `from_dict` method:

```python
@classmethod
def from_dict(cls, data: dict[str, Any]) -> "ServerSettings":
    return cls(
        guild_id=data.get("guild_id", 0),
        support_role_id=data.get("support_role_id"),
        ticket_channel_id=data.get("ticket_channel_id"),
        ephemeral_processing=data.get("ephemeral_processing", False),
        support_channel_id=data.get("support_channel_id"),
        menu_message_id=data.get("menu_message_id"),
        community_support_channel_id=data.get("community_support_channel_id"),
    )
```

**Step 3: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.server_config import ServerSettings; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add src/server_config.py
git commit -m "feat: add menu config fields to ServerSettings"
```

---

## Task 4: Create support menu views module

**Files:**
- Create: `src/views/__init__.py`
- Create: `src/views/support_menu.py`

**Step 1: Create views package**

Create `src/views/__init__.py`:

```python
"""Discord UI views for the support bot."""

from src.views.support_menu import (
    SupportMenuView,
    SupportQuestionModal,
    SupportResponseView,
)

__all__ = ["SupportMenuView", "SupportQuestionModal", "SupportResponseView"]
```

**Step 2: Create support menu views**

Create `src/views/support_menu.py`:

```python
"""Support menu views: persistent menu, question modal, response buttons."""

from typing import TYPE_CHECKING, Callable, Awaitable

import discord

if TYPE_CHECKING:
    from src.bot import XenonSupportBot


class SupportQuestionModal(discord.ui.Modal):
    """Modal for entering a support question."""

    question = discord.ui.TextInput(
        label="Your Question",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. How do I create a backup of my server?",
        max_length=1000,
        required=True,
    )

    def __init__(
        self,
        *,
        on_submit: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        super().__init__(title="Ask a Question")
        self.on_submit_callback = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission."""
        await self.on_submit_callback(interaction, self.question.value)


class SupportMenuView(discord.ui.View):
    """Persistent menu view with 'Ask a Question' button."""

    def __init__(
        self,
        *,
        on_question: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        super().__init__(timeout=None)  # Persistent view
        self.on_question = on_question

    @discord.ui.button(
        label="Ask a Question",
        style=discord.ButtonStyle.primary,
        emoji="❓",
        custom_id="support_menu:ask_question",
    )
    async def ask_question_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Open the question modal."""
        modal = SupportQuestionModal(on_submit=self.on_question)
        await interaction.response.send_modal(modal)


class SupportResponseView(discord.ui.View):
    """Response view with Resolved and Community Support buttons."""

    def __init__(
        self,
        *,
        question_id: int,
        community_channel_id: int | None = None,
        on_resolved: Callable[[int], Awaitable[None]],
        on_community_support: Callable[[int], Awaitable[None]],
    ):
        super().__init__(timeout=300)  # 5 minute timeout
        self.question_id = question_id
        self.community_channel_id = community_channel_id
        self.on_resolved = on_resolved
        self.on_community_support = on_community_support

    @discord.ui.button(
        label="Resolved",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def resolved_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Mark question as resolved."""
        await self.on_resolved(self.question_id)

        # Disable all buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # Update message
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed:
            embed.set_footer(text=f"✅ Marked as resolved by {interaction.user.display_name}")

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(
        label="Community Support",
        style=discord.ButtonStyle.secondary,
        emoji="💬",
    )
    async def community_support_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Link to community support channel."""
        await self.on_community_support(self.question_id)

        # Disable all buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        # Update message with footer
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed:
            embed.set_footer(text="💬 Redirected to community support")

        await interaction.response.edit_message(embed=embed, view=self)

        # Send link to community channel
        if self.community_channel_id and interaction.guild:
            channel = interaction.guild.get_channel(self.community_channel_id)
            if channel:
                await interaction.followup.send(
                    f"Please ask your question in {channel.mention} for community help!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Community support channel not found. Please contact a moderator.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                "No community support channel configured. Please contact a moderator.",
                ephemeral=True,
            )

        self.stop()


def create_menu_embed() -> discord.Embed:
    """Create the support menu embed."""
    return discord.Embed(
        title="🤖 Xenon Support",
        description=(
            "Have a question about Xenon?\n\n"
            "Click the button below and our AI will try to help "
            "based on the official documentation."
        ),
        color=discord.Color.blue(),
    )
```

**Step 3: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.views import SupportMenuView; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add src/views/
git commit -m "feat: add support menu views (menu, modal, response)"
```

---

## Task 5: Add tool call callback to agent runner

**Files:**
- Modify: `src/agent/runner.py`

**Step 1: Add callback parameter to run method**

Update the `run` method signature and add callback invocation. Change lines 142-148:

```python
async def run(
    self,
    user_message: str,
    history: list[dict] | None = None,
    images: list[str] | None = None,
    channel_context: list[dict] | None = None,
    on_tool_call: Callable[[str, dict, dict], Awaitable[None]] | None = None,
) -> AsyncIterator[AgentStep]:
```

Add import at top of file (after line 4):

```python
from typing import AsyncIterator, Callable, Awaitable
```

**Step 2: Call the callback after tool execution**

After line 210 (`result = execute_tool(tool_call.name, tool_call.arguments)`), add:

```python
# Call the callback if provided
if on_tool_call:
    await on_tool_call(tool_call.name, tool_call.arguments, result)
```

**Step 3: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.agent.runner import AgentRunner; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add src/agent/runner.py
git commit -m "feat: add on_tool_call callback to AgentRunner"
```

---

## Task 6: Update bot.py - Add new commands and menu handling

**Files:**
- Modify: `src/bot.py`

**Step 1: Add imports**

Add these imports at the top (after line 18):

```python
from src.analytics import analytics
from src.views.support_menu import (
    SupportMenuView,
    SupportResponseView,
    create_menu_embed,
)
```

**Step 2: Add question handler method to XenonSupportBot**

Add this method to the `XenonSupportBot` class (after line 309):

```python
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

        # Add link buttons from agent response
        view = SupportResponseView(
            question_id=question_id,
            community_channel_id=srv_settings.community_support_channel_id,
            on_resolved=analytics.mark_answered,
            on_community_support=analytics.mark_community_support,
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
```

**Step 3: Update setup_hook to register persistent view**

Update the `setup_hook` method (around line 295):

```python
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
    await self.tree.sync()
```

**Step 4: Add new commands after existing commands (around line 760)**

```python
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
```

**Step 5: Remove old commands from setup_hook**

Remove these lines from the original `setup_hook`:
- `self.tree.add_command(support_command)`
- `self.tree.add_command(clear_history_command)`

**Step 6: Delete the old support_command function and clear_history_command function**

Remove the entire `support_command` function (lines 457-603) and `clear_history_command` function (lines 765-774).

**Step 7: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.bot import bot; print('OK')"`

Expected: `OK`

**Step 8: Commit**

```bash
git add src/bot.py
git commit -m "feat: add menu-based support with analytics commands"
```

---

## Task 7: Update support-config show command

**Files:**
- Modify: `src/bot.py`

**Step 1: Update config_show to display new fields**

Update the `config_show` command to show new settings. Find the existing function and update it:

```python
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
```

**Step 2: Remove old config commands**

Remove these functions (no longer needed):
- `config_support_role`
- `config_ticket_channel`
- `config_processing_visibility`

Also remove them from the command group registration if they're explicitly added.

**Step 3: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.bot import bot; print('OK')"`

Expected: `OK`

**Step 4: Commit**

```bash
git add src/bot.py
git commit -m "feat: update support-config show for new settings"
```

---

## Task 8: Clean up old SupportResponseView

**Files:**
- Modify: `src/bot.py`

**Step 1: Remove old SupportResponseView class**

Remove the old `SupportResponseView` class (lines 138-274) from `bot.py` since we now use the one from `src/views/support_menu.py`.

**Step 2: Remove MessageHistory class if no longer used**

Check if `MessageHistory` is still used anywhere. If the `/support` command and thread responses are removed, this class can be deleted (lines 57-76).

**Step 3: Clean up unused imports**

Remove any imports that are no longer used.

**Step 4: Verify syntax**

Run: `cd ~/projects/xenon-support-bot && python -c "from src.bot import bot; print('OK')"`

Expected: `OK`

**Step 5: Commit**

```bash
git add src/bot.py
git commit -m "refactor: remove old SupportResponseView and unused code"
```

---

## Task 9: Test the complete flow manually

**Step 1: Run the bot locally**

```bash
cd ~/projects/xenon-support-bot && python -m src.main
```

**Step 2: Test /scrape command**

Run `/scrape` in Discord to ensure docs are loaded.

**Step 3: Test /setup-support-menu command**

Run `/setup-support-menu #test-channel #community-help` in Discord.

Verify:
- Menu embed appears in the channel
- Button is clickable
- Modal opens when button clicked

**Step 4: Test asking a question**

Click "Ask a Question" button, enter "How do I create a backup?", submit.

Verify:
- Ephemeral "Processing..." message appears
- Response with buttons appears (ephemeral)
- "Resolved" button works
- "Community Support" button works

**Step 5: Test analytics commands**

Run `/support-analytics` and `/support-unanswered`.

Verify:
- Stats are displayed correctly
- Questions appear in unanswered list

**Step 6: Commit any fixes**

If any issues found, fix and commit.

---

## Task 10: Final cleanup and documentation

**Files:**
- Modify: `README.md` (if it exists)

**Step 1: Update README if needed**

Update command documentation to reflect new commands:
- `/setup-support-menu <channel> [community_channel]`
- `/support-analytics [days]`
- `/support-unanswered [days] [limit]`
- `/support-config show`
- `/scrape`

**Step 2: Final commit**

```bash
git add -A
git commit -m "docs: update README for menu-based support"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add aiosqlite dependency | pyproject.toml |
| 2 | Create analytics module | src/analytics.py |
| 3 | Update server configuration | src/server_config.py |
| 4 | Create support menu views | src/views/*.py |
| 5 | Add tool call callback | src/agent/runner.py |
| 6 | Update bot with new commands | src/bot.py |
| 7 | Update config show command | src/bot.py |
| 8 | Clean up old code | src/bot.py |
| 9 | Manual testing | - |
| 10 | Documentation | README.md |

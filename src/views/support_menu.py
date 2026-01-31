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
        original_question: str,
        bot_response: str,
        steps_taken: list[str] | None = None,
        community_channel_id: int | None = None,
        on_resolved: Callable[[int], Awaitable[None]],
        on_community_support: Callable[[int], Awaitable[None]],
        on_rephrase: Callable[[str], Awaitable[str]] | None = None,
    ):
        super().__init__(timeout=300)  # 5 minute timeout
        self.question_id = question_id
        self.original_question = original_question
        self.bot_response = bot_response
        self.steps_taken = steps_taken or []
        self.community_channel_id = community_channel_id
        self.on_resolved = on_resolved
        self.on_community_support = on_community_support
        self.on_rephrase = on_rephrase

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

        # Disable only interactive buttons (not link buttons)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link:
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
        """Post question to community support channel."""
        await self.on_community_support(self.question_id)

        # Disable only interactive buttons (not link buttons)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link:
                item.disabled = True

        # Update message with footer
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
        if embed:
            embed.set_footer(text="💬 Redirected to community support")

        await interaction.response.edit_message(embed=embed, view=self)

        # Post to community channel
        if self.community_channel_id and interaction.guild:
            channel = interaction.guild.get_channel(self.community_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                # Rephrase the question if callback provided
                rephrased = self.original_question
                if self.on_rephrase:
                    try:
                        rephrased = await self.on_rephrase(self.original_question)
                    except Exception:
                        pass  # Fall back to original

                # Create help request embed
                help_embed = discord.Embed(
                    title="💬 Community Help Needed",
                    description=rephrased,
                    color=discord.Color.orange(),
                )
                help_embed.add_field(
                    name="👤 Asked by",
                    value=interaction.user.mention,
                    inline=True,
                )
                help_embed.add_field(
                    name="🤖 Bot couldn't help",
                    value="The AI support bot wasn't able to fully answer this question.",
                    inline=True,
                )
                # Add steps already tried by the bot
                if self.steps_taken:
                    steps_text = "\n".join(f"• {step}" for step in self.steps_taken[:5])
                    help_embed.add_field(
                        name="🔧 Already tried",
                        value=steps_text,
                        inline=False,
                    )
                help_embed.set_footer(text="Please help if you can!")

                try:
                    await channel.send(embed=help_embed)
                    await interaction.followup.send(
                        f"✅ Your question has been posted in {channel.mention}. "
                        "Someone from the community will help you soon!",
                        ephemeral=True,
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "❌ Couldn't post to the community channel. Please ask there manually.",
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
    embed = discord.Embed(
        title="🤖 Xenon Support",
        description=(
            "Have a question about Xenon?\n\n"
            "Click the button below and our AI will try to help "
            "based on the official documentation."
        ),
        color=discord.Color.blue(),
    )
    embed.set_footer(text="Powered by LMF • lmf.logge.top")
    return embed

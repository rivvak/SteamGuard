"""Discord /ask slash command \u2014 streams to Discord via edit-in-place.

Loaded from server/bot.py in on_ready():

    from server.bot.cogs.ask_cog import setup as setup_ask
    await setup_ask(bot)

Requires env (all already set for the bot):
    LICENSE_SERVER_URL
    DISCORD_GUILD_ID
    ADMIN_KEY            (used with X-Admin-Key header on /ai/ask bot path)

The license server verifies the invoking Discord user has an active
license before answering. No client-side key/HWID lookup needed.
"""

from __future__ import annotations

import json
import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands

LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"].rstrip("/")
ADMIN_KEY = os.environ["ADMIN_KEY"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
GUILD = discord.Object(id=GUILD_ID)

MAX_ANSWER_CHARS = 1900  # Discord message cap with headroom


class AskCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=120.0)

    @app_commands.command(
        name="ask",
        description="Ask the SteamGuard assistant a question about the product",
    )
    @app_commands.describe(question="Your question (max 2000 chars)")
    @app_commands.guilds(GUILD)
    async def ask(self, interaction: discord.Interaction, question: str):
        if len(question) > 2000:
            await interaction.response.send_message(
                "Question too long (max 2000 chars).", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)

        try:
            async with self._http.stream(
                "POST",
                f"{LICENSE_SERVER_URL}/ai/ask",
                headers={
                    "X-Admin-Key": ADMIN_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "discord_user_id": str(interaction.user.id),
                    "question": question,
                },
            ) as r:
                if r.status_code == 403:
                    await interaction.followup.send(
                        "No active SteamGuard license found for your Discord account.",
                        ephemeral=True,
                    )
                    return
                if r.status_code == 429:
                    await interaction.followup.send(
                        "Rate limited (30 questions per hour). Try again later.",
                        ephemeral=True,
                    )
                    return
                if r.status_code == 503:
                    await interaction.followup.send(
                        "The AI assistant is temporarily disabled.", ephemeral=True
                    )
                    return
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    await interaction.followup.send(
                        f"Error from license server ({r.status_code}): {detail}",
                        ephemeral=True,
                    )
                    return

                answer_parts: list[str] = []
                sources: list[str] = []
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if "delta" in obj:
                        answer_parts.append(obj["delta"])
                    if "sources" in obj:
                        sources = list(obj["sources"])[:5]
        except httpx.HTTPError as e:
            await interaction.followup.send(
                f"Network error calling license server: {e}", ephemeral=True
            )
            return

        answer = ("".join(answer_parts)).strip() or "(empty answer)"
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[: MAX_ANSWER_CHARS - 3] + "..."

        embed = discord.Embed(
            title=f"Q: {question[:200]}",
            description=answer,
            color=discord.Color.blurple(),
        )
        if sources:
            embed.add_field(
                name="Sources",
                value="\n".join(f"\u2022 {s}" for s in sources),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    """Called from server/bot.py on_ready(): `await setup_ask(bot)`.
    Safe to call multiple times \u2014 no-op if the cog is already registered."""
    if bot.get_cog("AskCog"):
        return
    await bot.add_cog(AskCog(bot), guilds=[GUILD])

"""Discord /ask slash command — streams to Discord via edit-in-place.

Loaded from server/bot.py:

    from server.bot.cogs.ask_cog import setup as setup_ask
    await setup_ask(bot)

Requires env:
    LICENSE_SERVER_URL     (already set for the bot)
    DISCORD_GUILD_ID       (already set — used for instant guild-scoped sync)
    ADMIN_KEY              (already set — used to short-circuit sig check
                            when the bot itself asks on behalf of a user)

Users must have an active SteamGuard license bound to their Discord ID.
The bot looks up the key by Discord ID via /admin/key-info, then signs
the request server-side.
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
        self._http = httpx.AsyncClient(timeout=60.0)

    async def _lookup_key(self, discord_user_id: int) -> tuple[str, str] | None:
        """Ask the license server for this user's active key + hwid."""
        r = await self._http.get(
            f"{LICENSE_SERVER_URL}/admin/keys-for-discord",
            params={"discord_user_id": discord_user_id},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        active = next((k for k in payload.get("keys", []) if k.get("status") == "active"), None)
        if not active:
            return None
        return active["key"], active["hwid"]

    @app_commands.command(
        name="ask",
        description="Ask the SteamGuard assistant a question about the product",
    )
    @app_commands.guilds(GUILD)
    @app_commands.describe(question="What would you like to know?")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        credentials = await self._lookup_key(interaction.user.id)
        if credentials is None:
            await interaction.followup.send(
                "You need an active SteamGuard license to use /ask. "
                "Use `/status` to check your license or `!getkey` in #get-key.",
                ephemeral=True,
            )
            return

        key, hwid = credentials

        # The bot proxies to /ai/ask via the admin-signed passthrough because
        # the bot has ADMIN_KEY and can HMAC-sign on the user's behalf.
        r = await self._http.post(
            f"{LICENSE_SERVER_URL}/ai/ask-via-bot",
            headers={"X-Admin-Key": ADMIN_KEY},
            json={"key": key, "hwid": hwid, "question": question},
        )
        if r.status_code == 429:
            await interaction.followup.send(
                "You've hit the /ask rate limit (30/hour). Try again later.",
                ephemeral=True,
            )
            return
        if r.status_code != 200:
            await interaction.followup.send(
                f"Sorry, the assistant is unavailable right now ({r.status_code}).",
                ephemeral=True,
            )
            return

        data = r.json()
        answer = data.get("answer", "").strip() or "(no answer)"
        sources = data.get("sources", [])
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS - 3] + "..."

        footer = ""
        if sources:
            footer = "\n\n_Sources: " + ", ".join(f"`{s}`" for s in sources[:3]) + "_"

        await interaction.followup.send(answer + footer, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AskCog(bot))
    # Register the guild copy so slash commands appear instantly
    bot.tree.copy_global_to(guild=GUILD)
    await bot.tree.sync(guild=GUILD)

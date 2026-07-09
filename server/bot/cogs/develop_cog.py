"""/develop slash command \u2014 dispatches a dev-agent session on sg-devbox.

Loaded from server/bot.py in on_ready():

    from server.bot.cogs.develop_cog import setup as setup_develop
    await setup_develop(bot)

Owner-only: the command is visible only in the configured guild, and the
handler ignores non-owner invocations with an ephemeral notice. The server
also enforces owner-vs-user rate limits.
"""

from __future__ import annotations

import os

import discord
import httpx
from discord import app_commands
from discord.ext import commands

LICENSE_SERVER_URL = os.environ["LICENSE_SERVER_URL"].rstrip("/")
ADMIN_KEY = os.environ["ADMIN_KEY"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
OWNER_ID = int(os.environ.get("DISCORD_OWNER_ID", "1513150836472021074"))
GUILD = discord.Object(id=GUILD_ID)


class DevelopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=1500.0)

    @app_commands.command(
        name="develop",
        description="Ask the AI dev agent to open a PR (or push a doc/test commit) that implements a task.",
    )
    @app_commands.describe(task="What you want the agent to do (max 6000 chars)")
    @app_commands.guilds(GUILD)
    async def develop(self, interaction: discord.Interaction, task: str):
        if interaction.user.id != OWNER_ID:
            # Non-owners get rate-limited server-side but we also give them a
            # visible cap here as a UX nicety.
            pass
        if len(task) > 6000:
            await interaction.response.send_message(
                "Task too long (max 6000 chars).", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)

        try:
            r = await self._http.post(
                f"{LICENSE_SERVER_URL}/internal/develop",
                headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                json={
                    "discord_user_id": str(interaction.user.id),
                    "task": task,
                    "thread_id": (str(interaction.channel_id) if interaction.channel_id else None),
                },
            )
        except httpx.HTTPError as e:
            await interaction.followup.send(f"Network error: {e}", ephemeral=True)
            return

        if r.status_code == 429:
            await interaction.followup.send(
                "You've hit the /develop rate limit (5 per hour).", ephemeral=True
            )
            return
        if r.status_code == 503:
            await interaction.followup.send(
                "The dev agent is disabled right now.", ephemeral=True
            )
            return
        if r.status_code != 200:
            await interaction.followup.send(
                f"Server error {r.status_code}: {r.text[:300]}", ephemeral=True
            )
            return

        data = r.json()
        status = data.get("status", "unknown")
        embed = self._format_result(task, status, data)
        await interaction.followup.send(embed=embed)

    def _format_result(self, task: str, status: str, data: dict) -> discord.Embed:
        title = f"/develop \u2014 {status}"
        color = {
            "committed": discord.Color.green(),
            "pr_opened": discord.Color.blurple(),
            "refused": discord.Color.red(),
            "empty": discord.Color.dark_gray(),
            "error": discord.Color.orange(),
        }.get(status, discord.Color.light_gray())

        emb = discord.Embed(title=title, description=f"**Task:** {task[:400]}", color=color)

        if status == "committed":
            emb.add_field(name="Commit", value=data.get("commit_url", data.get("commit_sha", "")), inline=False)
            if data.get("files"):
                emb.add_field(name="Files", value="\n".join(f"\u2022 {f}" for f in data["files"][:15]), inline=False)
        elif status == "pr_opened":
            emb.add_field(name="PR", value=data.get("pr_url", ""), inline=False)
            if data.get("files"):
                emb.add_field(name="Files", value="\n".join(f"\u2022 {f}" for f in data["files"][:15]), inline=False)
        elif status == "refused":
            emb.add_field(
                name="Denied files",
                value="\n".join(f"\u2022 {f}" for f in data.get("denied_files", [])[:15]) or "(none listed)",
                inline=False,
            )
        elif status == "error":
            emb.add_field(name="Error", value=data.get("error", "unknown"), inline=False)
            if data.get("log_tail"):
                emb.add_field(name="Log tail", value=f"```\n{data['log_tail'][-800:]}\n```", inline=False)

        emb.set_footer(text=f"session {data.get('session_id', '?')}")
        return emb

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    if bot.get_cog("DevelopCog"):
        return
    await bot.add_cog(DevelopCog(bot), guilds=[GUILD])

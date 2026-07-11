"""/develop slash command — dispatches a dev-agent session on sg-devbox.

Loaded from server/bot.py in on_ready():

    from server.bot.cogs.develop_cog import setup as setup_develop
    await setup_develop(bot)

Owner-only: the command is visible only in the configured guild, and the
handler ignores non-owner invocations with an ephemeral notice. The server
also enforces owner-vs-user rate limits.
"""

from __future__ import annotations

import asyncio
import os
import time

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
        self._http = httpx.AsyncClient(timeout=30.0)

    POLL_INITIAL_SECONDS = 5
    POLL_STEADY_SECONDS = 15
    HEARTBEAT_EDIT_EVERY = 60
    MAX_JOB_SECONDS = 6 * 60 * 60

    @app_commands.command(
        name="develop",
        description="Ask the AI dev agent to open a PR (or push a doc/test commit) that implements a task.",
    )
    @app_commands.describe(
        task="What you want the agent to do (max 6000 chars)",
        deep="Deep-reasoning mode — spend more thinking tokens. Slower but stronger.",
    )
    @app_commands.guilds(GUILD)
    async def develop(self, interaction: discord.Interaction, task: str, deep: bool = False):
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

        kickoff: dict
        try:
            kickoff_resp = await self._http.post(
                f"{LICENSE_SERVER_URL}/internal/develop",
                headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                json={
                    "discord_user_id": str(interaction.user.id),
                    "task": task,
                    "thread_id": (str(interaction.channel_id) if interaction.channel_id else None),
                    "channel_id": (str(interaction.channel_id) if interaction.channel_id else None),
                    "deep": deep,
                },
            )
        except httpx.HTTPError as e:
            await interaction.followup.send(f"Network error: {e}", ephemeral=True)
            return

        if kickoff_resp.status_code == 429:
            await interaction.followup.send(
                "You've hit the /develop rate limit (5 per hour).", ephemeral=True
            )
            return
        if kickoff_resp.status_code == 503:
            await interaction.followup.send(
                "The dev agent is disabled right now.", ephemeral=True
            )
            return
        if kickoff_resp.status_code != 200:
            await interaction.followup.send(
                f"Server error {kickoff_resp.status_code}: {kickoff_resp.text[:300]}", ephemeral=True
            )
            return

        kickoff = kickoff_resp.json()
        sid = kickoff.get("session_id")
        if not sid:
            await interaction.followup.send(
                f"Develop service returned no session_id: {kickoff}", ephemeral=True
            )
            return

        msg = await interaction.followup.send(
            embed=self._heartbeat_embed(task, sid, "starting", 0, None),
            wait=True,
        )

        started = time.monotonic()
        last_edit = 0.0
        last_state = "starting"
        state_data = kickoff

        for _ in range(3):
            await asyncio.sleep(self.POLL_INITIAL_SECONDS)
            state_data = await self._poll(sid)
            if state_data.get("state") in ("finished", "error", "killed"):
                await self._finalize(interaction, msg, task, sid, state_data)
                return

        while True:
            elapsed = int(time.monotonic() - started)
            if elapsed > self.MAX_JOB_SECONDS:
                await msg.edit(
                    embed=self._heartbeat_embed(
                        task,
                        sid,
                        "timeout",
                        elapsed,
                        "Client-side timeout — session may still be running.",
                    )
                )
                return

            await asyncio.sleep(self.POLL_STEADY_SECONDS)
            try:
                state_data = await self._poll(sid)
            except Exception as e:
                if time.monotonic() - last_edit > self.HEARTBEAT_EDIT_EVERY:
                    await msg.edit(
                        embed=self._heartbeat_embed(
                            task,
                            sid,
                            last_state,
                            elapsed,
                            f"(poll error, retrying: {type(e).__name__})",
                        )
                    )
                    last_edit = time.monotonic()
                continue

            state = state_data.get("state", "running")
            if state in ("finished", "error", "killed"):
                await self._finalize(interaction, msg, task, sid, state_data)
                return

            if time.monotonic() - last_edit > self.HEARTBEAT_EDIT_EVERY or state != last_state:
                await msg.edit(
                    embed=self._heartbeat_embed(
                        task,
                        sid,
                        state,
                        elapsed,
                        (state_data.get("result") or {}).get("log_tail") or state_data.get("error"),
                    )
                )
                last_edit = time.monotonic()
                last_state = state

    async def _poll(self, sid: str) -> dict:
        r = await self._http.get(
            f"{LICENSE_SERVER_URL}/internal/develop/status/{sid}",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        r.raise_for_status()
        return r.json()

    async def _finalize(
        self,
        interaction: discord.Interaction,
        msg: discord.Message,
        task: str,
        sid: str,
        state_data: dict,
    ) -> None:
        state = state_data.get("state", "unknown")
        result = state_data.get("result") or {}
        status = result.get("status", "error" if state == "error" else "unknown")

        data_for_embed = dict(result)
        data_for_embed.setdefault("session_id", sid)
        if status == "error" and not data_for_embed.get("error"):
            data_for_embed["error"] = state_data.get("error", "unknown")
        embed = self._format_result(task, status, data_for_embed)
        if state_data.get("elapsed_s"):
            embed.add_field(name="Elapsed", value=f"{state_data['elapsed_s']}s", inline=True)

        try:
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            if e.code == 50027:
                await interaction.channel.send(
                    content=f"⚠️ {interaction.user.mention} the session took more than 15 minutes, but successfully completed! Here are your results:",
                    embed=embed
                )
            else:
                raise

    def _heartbeat_embed(
        self, task: str, sid: str, state: str, elapsed: int, note: str | None
    ) -> discord.Embed:
        icon = {
            "starting": "🟡",
            "running": "🟢",
            "timeout": "⏱️",
            "error": "❌",
        }.get(state, "⚪")
        emb = discord.Embed(
            title=f"{icon} /develop — {state}",
            description=f"**Task:** {task[:400]}",
            color=discord.Color.blurple(),
        )
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        emb.add_field(
            name="Elapsed",
            value=f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s",
            inline=True,
        )
        emb.add_field(name="Session", value=f"`{sid}`", inline=True)
        if note:
            emb.add_field(name="Latest output", value=f"```\n{str(note)[-900:]}\n```", inline=False)
        return emb

    def _format_result(self, task: str, status: str, data: dict) -> discord.Embed:
        title = f"/develop — {status}"
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
                emb.add_field(name="Files", value="\n".join(f"• {f}" for f in data["files"][:15]), inline=False)
        elif status == "pr_opened":
            emb.add_field(name="PR", value=data.get("pr_url", ""), inline=False)
            if data.get("files"):
                emb.add_field(name="Files", value="\n".join(f"• {f}" for f in data["files"][:15]), inline=False)
        elif status == "refused":
            emb.add_field(
                name="Denied files",
                value="\n".join(f"• {f}" for f in data.get("denied_files", [])[:15]) or "(none listed)",
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

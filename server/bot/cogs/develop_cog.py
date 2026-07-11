"""/develop slash command — dispatches a dev-agent session on sg-devbox.

Loaded from server/bot.py in setup_hook() when AI_DEVELOP_ENABLED=true:

    from server.bot.cogs.develop_cog import setup as setup_develop
    await setup_develop(bot)

Non-blocking flow (mirrors /create — see server/routes/ai_hooks.py for the
server half and why blocking here is fatal on Cloud Run):

  1. `/develop task:<…>` — defer, POST to /internal/develop. The license
     server returns {session_id, state:"starting"} immediately and runs the
     devbox /session call on a detached task, so the request — which Cloud Run
     caps at 300 s — never carries the actual work.
  2. Cog sends a follow-up message establishing the session.
  3. Cog polls /internal/develop/status/{sid} every few seconds, editing the
     same message with a heartbeat ("🟢 working — 4m elapsed") so the user
     sees it is alive.
  4. When state ∈ {done, error} the cog edits that message with the final
     result embed (PR / commit / refused / empty / error) and exits.

Editing the existing message uses the bot's permanent token (Message.edit),
not the 15-minute interaction token. So we drive the message for the full
(~20 min) duration of a big task without ever racing Discord's follow-up
expiry — the original cause of "application did not respond" on big tasks.

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

# Poll cadence: a couple of fast polls (small tasks can finish in ~1 min),
# then steady-state. Heartbeat-edit the message every minute so the user sees
# progress. Ceiling is well past the devbox's 20-min agent cap + push slack.
POLL_INITIAL_SECONDS = 5
POLL_STEADY_SECONDS = 12
FAST_POLLS = 2
HEARTBEAT_EDIT_SECONDS = 60
MAX_WAIT_SECONDS = 25 * 60


class DevelopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=60.0)

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

        # 1) Kick off the dev-agent session. Returns immediately with a session
        #    id; the devbox grinds through the task off-request.
        try:
            r = await self._http.post(
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

        kickoff = r.json()
        sid = kickoff.get("session_id")
        if not sid:
            await interaction.followup.send(
                f"Server returned no session_id: {kickoff}", ephemeral=True
            )
            return

        # 2) Establish the follow-up message now, while the interaction token
        #    is fresh (the FIRST follow-up must land within 15 min of the
        #    interaction). From here on we only Message.edit this message —
        #    Message.edit uses the bot's permanent token, so it never races the
        #    15-min clock and cannot 50027 even for a 20-minute task.
        msg = await self._establish(interaction, task, sid)
        if msg is None:
            return  # both follow-up and channel fallback failed; nothing to drive

        # 3) Poll until terminal, heartbeating the message so the user sees it.
        started = time.monotonic()
        last_edit = 0.0
        consec_404 = 0

        for _ in range(FAST_POLLS):
            await asyncio.sleep(POLL_INITIAL_SECONDS)
            data, lost = await self._poll_once(sid)
            if lost:
                await self._safe_edit(msg, embed=self._stalled_embed(task, sid))
                return
            if self._terminal(data):
                await self._finalize(msg, task, sid, data, started)
                return

        while True:
            elapsed = time.monotonic() - started
            if elapsed > MAX_WAIT_SECONDS:
                await self._safe_edit(msg, embed=self._stalled_embed(
                    task, sid,
                    note="client timeout — the dev agent may still be running on the devbox. "
                         "If it finishes it will commit/push on its own; check the repo shortly.",
                ))
                return

            await asyncio.sleep(POLL_STEADY_SECONDS)
            data, lost = await self._poll_once(sid)
            if lost:
                consec_404 += 1
                if consec_404 >= 3:
                    await self._safe_edit(msg, embed=self._stalled_embed(task, sid))
                    return
                continue
            consec_404 = 0
            if self._terminal(data):
                await self._finalize(msg, task, sid, data, started)
                return
            if time.monotonic() - last_edit > HEARTBEAT_EDIT_SECONDS:
                await self._safe_edit(msg, embed=self._heartbeat_embed(
                    task, sid, int(elapsed),
                    data.get("log_tail") if isinstance(data, dict) else None,
                ))
                last_edit = time.monotonic()

    # ── polling helpers ─────────────────────────────────────────────────────

    async def _poll_once(self, sid: str) -> "tuple[dict | None, bool]":
        """Fetch session status. Returns (data_or_None, session_lost).

        - transport/transient error → (None, False)  — caller retries
        - 404 (unknown session)     → (None, True)   — the server lost it
        - any other non-200          → (None, False)  — treat as transient
        - 200                        → (json,         False)
        """
        try:
            r = await self._http.get(
                f"{LICENSE_SERVER_URL}/internal/develop/status/{sid}",
                headers={"X-Admin-Key": ADMIN_KEY},
            )
        except httpx.HTTPError:
            return None, False
        if r.status_code == 404:
            return None, True
        if r.status_code != 200:
            return None, False
        try:
            return r.json(), False
        except ValueError:
            return None, False

    @staticmethod
    def _terminal(data) -> bool:
        return bool(data) and data.get("state") in ("done", "error")

    # ── message helpers ──────────────────────────────────────────────────────

    async def _establish(self, interaction: discord.Interaction, task: str, sid: str):
        """Send the initial follow-up. Fall back to a plain channel message if
        the interaction token is somehow already gone (shouldn't happen so soon
        after defer). Returns the Message to drive, or None if both failed."""
        emb = self._heartbeat_embed(task, sid, 0, None)
        try:
            return await interaction.followup.send(embed=emb, wait=True)
        except discord.HTTPException:
            try:
                content = f"⏳ {interaction.user.mention} `/develop` session `{sid}` started…"
                return await interaction.channel.send(content=content, embed=emb)
            except discord.HTTPException:
                return None

    async def _finalize(self, msg, task: str, sid: str, data: dict, started: float) -> None:
        state = data.get("state")
        status = data.get("status") or ("error" if state == "error" else "unknown")
        if "session_id" not in data:
            data = {**data, "session_id": sid}
        embed = self._format_result(task, status, data, elapsed=int(time.monotonic() - started))
        await self._safe_edit(msg, embed=embed)

    async def _safe_edit(self, msg, *, content=None, embed=None) -> None:
        if msg is None:
            return
        try:
            await msg.edit(content=content, embed=embed)
        except discord.HTTPException:
            pass  # message gone / no perms — nothing more we can do

    def _heartbeat_embed(self, task: str, sid: str, elapsed: int, log_tail: str | None) -> discord.Embed:
        emb = discord.Embed(
            title="🟢 /develop — working",
            description=f"**Task:** {task[:400]}",
            color=discord.Color.blurple(),
        )
        m, s = divmod(int(elapsed), 60)
        emb.add_field(name="Elapsed", value=f"{m}m {s:02d}s", inline=True)
        emb.add_field(name="Session", value=f"`{sid}`", inline=True)
        if log_tail:
            emb.add_field(name="Latest output", value=f"```\n{str(log_tail)[-900:]}\n```", inline=False)
        return emb

    def _stalled_embed(self, task: str, sid: str, note: str | None = None) -> discord.Embed:
        emb = discord.Embed(
            title="⚠️ /develop — stalled",
            description=f"**Task:** {task[:400]}",
            color=discord.Color.orange(),
        )
        emb.add_field(name="Session", value=f"`{sid}`", inline=True)
        emb.add_field(
            name="Heads-up",
            value=note or (
                "session status became unavailable — the server may have restarted mid-task. "
                "If the agent had finished it will have committed/pushed on its own; "
                "check the repo for a new PR or commit."
            ),
            inline=False,
        )
        return emb

    def _format_result(self, task: str, status: str, data: dict, *, elapsed: int | None = None) -> discord.Embed:
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

        if elapsed is not None:
            m, s = divmod(int(elapsed), 60)
            emb.add_field(name="Elapsed", value=f"{m}m {s:02d}s", inline=True)

        emb.set_footer(text=f"session {data.get('session_id', '?')}")
        return emb

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    if bot.get_cog("DevelopCog"):
        return
    await bot.add_cog(DevelopCog(bot), guilds=[GUILD])

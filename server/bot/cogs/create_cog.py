"""/create slash command — dispatches a full-app build in an isolated sandbox
container on sg-devbox and returns the resulting .zip.

Loaded from server/bot.py in on_ready() when AI_CREATE_ENABLED=true.

Unlike /develop (which blocks on a Cloud Run request for up to 20 minutes and
returns a PR link), /create can run for hours. The flow is:

  1. `/create prompt:<...>` — defer interaction, POST to /internal/create.
  2. License server returns {session_id, state:"starting"} immediately.
  3. Cog enters a poll loop, editing the interaction follow-up every 60s with
     a heartbeat like `🟢 building — 12m elapsed`.
  4. When state ∈ {finished, error, killed}, cog fetches the artifact:
       • attachment kind → downloads via /internal/create/artifact/<sid> and
         posts as a Discord file attachment.
       • signed_url kind → posts the signed URL (valid 7 days).
       • too_large / error → posts the error + log_tail in an embed.

Discord interaction tokens are valid for 15 min for the FIRST edit, but the
follow-up webhook URL stays valid for 15 min *after each edit* — so as long as
we edit at least every ~14 min, we can drive the message forever.
"""

from __future__ import annotations

import asyncio
import io
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

POLL_INITIAL_SECONDS = 5
POLL_STEADY_SECONDS = 15
HEARTBEAT_EDIT_EVERY = 60
MAX_JOB_SECONDS = 6 * 60 * 60  # matches devbox hard cap


class CreateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=60.0)

    @app_commands.command(
        name="create",
        description="Ask the AI agent to build something from scratch and return a .zip.",
    )
    @app_commands.describe(
        prompt="What to build (max 32000 chars)",
        deep="Deep-reasoning mode — more thinking tokens + longer watchdog. Slower but stronger.",
        model="Optional model override (default: nvidia_nim/z-ai/glm-5.2)",
    )
    @app_commands.guilds(GUILD)
    async def create(
        self,
        interaction: discord.Interaction,
        prompt: str,
        deep: bool = False,
        model: str | None = None,
    ):
        if len(prompt) > 32000:
            await interaction.response.send_message(
                "Prompt too long (max 32000 chars).", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        # 1) kick off the sandbox
        try:
            r = await self._http.post(
                f"{LICENSE_SERVER_URL}/internal/create",
                headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                json={
                    "discord_user_id": str(interaction.user.id),
                    "channel_id": (str(interaction.channel_id) if interaction.channel_id else None),
                    "prompt": prompt,
                    "model": model,
                    "deep": deep,
                },
            )
        except httpx.HTTPError as e:
            await interaction.followup.send(f"Network error: {e}", ephemeral=True)
            return

        if r.status_code == 429:
            await interaction.followup.send(
                "You've hit the /create rate limit (20 per day).", ephemeral=True
            )
            return
        if r.status_code == 503:
            await interaction.followup.send(
                "/create is disabled right now.", ephemeral=True
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
                f"Devbox returned no session_id: {kickoff}", ephemeral=True
            )
            return

        # 2) initial message
        msg = await interaction.followup.send(
            embed=self._heartbeat_embed(prompt, sid, "starting", 0, None),
            wait=True,
        )

        # 3) poll loop
        started = time.monotonic()
        last_edit = 0.0
        last_state = "starting"
        state_data = kickoff

        # First few polls fast (job might already be done — hello-world builds
        # can finish in ~15s), then steady-state at 15s.
        for i in range(3):
            await asyncio.sleep(POLL_INITIAL_SECONDS)
            state_data = await self._poll(sid)
            if state_data.get("state") in ("finished", "error", "killed"):
                await self._finalise(interaction, msg, prompt, sid, state_data)
                return

        while True:
            if time.monotonic() - started > MAX_JOB_SECONDS:
                await msg.edit(
                    embed=self._heartbeat_embed(
                        prompt, sid, "timeout", int(time.monotonic() - started),
                        "Client-side timeout — sandbox may still be running. Try /create/status.",
                    )
                )
                return

            await asyncio.sleep(POLL_STEADY_SECONDS)
            try:
                state_data = await self._poll(sid)
            except Exception as e:
                # Transient poll error — keep going.
                if time.monotonic() - last_edit > HEARTBEAT_EDIT_EVERY:
                    await msg.edit(
                        embed=self._heartbeat_embed(
                            prompt, sid, last_state,
                            int(time.monotonic() - started),
                            f"(poll error, retrying: {type(e).__name__})",
                        )
                    )
                    last_edit = time.monotonic()
                continue

            state = state_data.get("state", "running")
            if state in ("finished", "error", "killed"):
                await self._finalise(interaction, msg, prompt, sid, state_data)
                return

            if time.monotonic() - last_edit > HEARTBEAT_EDIT_EVERY or state != last_state:
                await msg.edit(
                    embed=self._heartbeat_embed(
                        prompt, sid, state,
                        int(time.monotonic() - started),
                        state_data.get("log_tail"),
                    )
                )
                last_edit = time.monotonic()
                last_state = state

    async def _poll(self, sid: str) -> dict:
        r = await self._http.get(
            f"{LICENSE_SERVER_URL}/internal/create/status/{sid}",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        r.raise_for_status()
        return r.json()

    async def _finalise(
        self,
        interaction: discord.Interaction,
        msg: discord.Message,
        prompt: str,
        sid: str,
        data: dict,
    ) -> None:
        state = data.get("state", "unknown")
        artifact = data.get("artifact") or {}
        kind = artifact.get("kind")
        elapsed = data.get("elapsed_s", 0)

        # error / killed / no-artifact paths
        if state in ("error", "killed") and kind not in ("attachment", "signed_url"):
            emb = self._done_embed(
                prompt, sid, state, elapsed,
                error=data.get("error", "no error field"),
                log_tail=data.get("log_tail"),
            )
            await msg.edit(embed=emb)
            return

        if kind == "too_large":
            emb = self._done_embed(
                prompt, sid, "artifact_too_large", elapsed,
                error=f"Artifact {artifact.get('size_bytes', 0)} bytes exceeds cap.",
            )
            await msg.edit(embed=emb)
            return

        if kind == "signed_url":
            emb = self._done_embed(
                prompt, sid, state, elapsed,
                extra=(
                    f"**Artifact:** [{data.get('artifact_size_human', '?')}]"
                    f"({artifact['url']}) (7-day signed URL)"
                ),
                log_tail=data.get("log_tail"),
            )
            await msg.edit(embed=emb)
            return

        if kind == "attachment":
            # Stream the zip through the license server → attach to the message.
            emb = self._done_embed(
                prompt, sid, state, elapsed,
                extra=f"**Artifact:** {data.get('artifact_size_human', '?')} (attached)",
                log_tail=data.get("log_tail"),
            )
            try:
                async with httpx.AsyncClient(timeout=300.0) as c:
                    async with c.stream(
                        "GET",
                        f"{LICENSE_SERVER_URL}/internal/create/artifact/{sid}",
                        headers={"X-Admin-Key": ADMIN_KEY},
                    ) as resp:
                        resp.raise_for_status()
                        buf = io.BytesIO()
                        async for chunk in resp.aiter_bytes():
                            buf.write(chunk)
                        buf.seek(0)
                file = discord.File(buf, filename=f"sg-create-{sid}.zip")
                await msg.edit(embed=emb, attachments=[file])
            except Exception as e:
                emb.add_field(
                    name="Attachment error",
                    value=f"Failed to fetch artifact: {type(e).__name__}: {e}",
                    inline=False,
                )
                await msg.edit(embed=emb)
            return

        # fallback
        await msg.edit(embed=self._done_embed(prompt, sid, state, elapsed))

    def _heartbeat_embed(
        self, prompt: str, sid: str, state: str, elapsed: int, log_tail: str | None
    ) -> discord.Embed:
        icon = {
            "starting": "🟡",
            "running": "🟢",
            "uploading": "🔵",
            "timeout": "⏱️",
        }.get(state, "⚪")
        emb = discord.Embed(
            title=f"{icon} /create — {state}",
            description=f"**Prompt:** {prompt[:400]}",
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
        if log_tail:
            emb.add_field(
                name="Latest output",
                value=f"```\n{log_tail[-900:]}\n```",
                inline=False,
            )
        return emb

    def _done_embed(
        self,
        prompt: str,
        sid: str,
        state: str,
        elapsed: int,
        extra: str | None = None,
        error: str | None = None,
        log_tail: str | None = None,
    ) -> discord.Embed:
        color = {
            "finished": discord.Color.green(),
            "killed": discord.Color.orange(),
            "error": discord.Color.red(),
            "artifact_too_large": discord.Color.orange(),
        }.get(state, discord.Color.light_gray())
        icon = {
            "finished": "✅",
            "killed": "⚠️",
            "error": "❌",
            "artifact_too_large": "📦",
        }.get(state, "⚪")
        emb = discord.Embed(
            title=f"{icon} /create — {state}",
            description=f"**Prompt:** {prompt[:400]}",
            color=color,
        )
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        emb.add_field(
            name="Elapsed",
            value=f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s",
            inline=True,
        )
        emb.add_field(name="Session", value=f"`{sid}`", inline=True)
        if extra:
            emb.add_field(name="Result", value=extra, inline=False)
        if error:
            emb.add_field(name="Error", value=str(error)[:1000], inline=False)
        if log_tail:
            emb.add_field(
                name="Log tail",
                value=f"```\n{log_tail[-900:]}\n```",
                inline=False,
            )
        return emb

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    if bot.get_cog("CreateCog"):
        return
    await bot.add_cog(CreateCog(bot), guilds=[GUILD])

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


class _PlanPagerView(discord.ui.View):
    def __init__(
        self,
        cog: "DevelopCog",
        *,
        task: str,
        sid: str,
        chunks: list[str],
        owner_id: int,
        warning: str | None,
        proposed_files: list[str],
        attempts_text: str | None,
    ):
        super().__init__(timeout=1800)
        self._cog = cog
        self._task = task
        self._sid = sid
        self._chunks = chunks
        self._owner_id = owner_id
        self._warning = warning
        self._proposed_files = proposed_files
        self._attempts_text = attempts_text
        self._idx = 0
        self.message: discord.Message | None = None

    def _embed(self) -> discord.Embed:
        return self._cog._planned_embed_page(
            self._task,
            self._sid,
            self._chunks,
            self._idx,
            warning=self._warning,
            proposed_files=self._proposed_files,
            attempts_text=self._attempts_text,
        )

    @discord.ui.button(label="Next part ▶", style=discord.ButtonStyle.primary)
    async def next_part(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self._owner_id:
            await interaction.response.send_message(
                "Only the user who started this /develop run can page through this plan.",
                ephemeral=True,
            )
            return
        if self._idx >= len(self._chunks) - 1:
            button.disabled = True
            button.label = "End reached"
            await interaction.response.edit_message(view=self)
            return
        self._idx += 1
        if self._idx >= len(self._chunks) - 1:
            button.disabled = True
            button.label = "End reached"
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class DevelopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._http = httpx.AsyncClient(timeout=30.0)
        self._last_session_by_channel: dict[int, str] = {}

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
        session_id="Optional existing /develop session id to continue",
        plan_only="Plan mode only (no code edits or PR/commit).",
        continue_last="Continue the latest session in this channel when session_id is omitted.",
    )
    @app_commands.guilds(GUILD)
    async def develop(
        self,
        interaction: discord.Interaction,
        task: str,
        deep: bool = False,
        session_id: str | None = None,
        plan_only: bool = False,
        continue_last: bool = False,
    ):
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
            use_sid = session_id
            if not use_sid and continue_last and interaction.channel_id:
                use_sid = self._last_session_by_channel.get(int(interaction.channel_id))

            if use_sid:
                kickoff_resp = await self._http.post(
                    f"{LICENSE_SERVER_URL}/internal/develop/message/{use_sid}",
                    headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                    json={"message": task, "plan_only": plan_only},
                )
            else:
                kickoff_resp = await self._http.post(
                    f"{LICENSE_SERVER_URL}/internal/develop",
                    headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/json"},
                    json={
                        "discord_user_id": str(interaction.user.id),
                        "task": task,
                        "thread_id": (str(interaction.channel_id) if interaction.channel_id else None),
                        "channel_id": (str(interaction.channel_id) if interaction.channel_id else None),
                        "deep": deep,
                        "plan_only": plan_only,
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
            detail = ""
            try:
                detail = kickoff_resp.json().get("detail", "")
            except Exception:
                detail = kickoff_resp.text[:300]
            if "out of date" in detail.lower() or "missing async /develop" in detail.lower():
                await interaction.followup.send(
                    "Devbox needs upgrade for async /develop endpoints. Please deploy latest sg-devbox.",
                    ephemeral=True,
                )
                return
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
        if interaction.channel_id:
            self._last_session_by_channel[int(interaction.channel_id)] = sid

        msg = await interaction.followup.send(
            embed=self._heartbeat_embed(task, sid, "starting", 0, None, kickoff.get("progress")),
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
                msg = await self._safe_edit_or_repost(
                    interaction,
                    msg,
                    self._heartbeat_embed(
                        task,
                        sid,
                        "timeout",
                        elapsed,
                        "Client-side timeout — session may still be running.",
                        state_data.get("progress"),
                    ),
                    timeout_notice=True,
                )
                return

            await asyncio.sleep(self.POLL_STEADY_SECONDS)
            try:
                state_data = await self._poll(sid)
            except Exception as e:
                if time.monotonic() - last_edit > self.HEARTBEAT_EDIT_EVERY:
                    msg = await self._safe_edit_or_repost(
                        interaction,
                        msg,
                        self._heartbeat_embed(
                            task,
                            sid,
                            last_state,
                            elapsed,
                            f"(poll error, retrying: {type(e).__name__})",
                            state_data.get("progress"),
                        ),
                    )
                    last_edit = time.monotonic()
                continue

            state = state_data.get("state", "running")
            if state in ("finished", "error", "killed"):
                await self._finalize(interaction, msg, task, sid, state_data)
                return

            if time.monotonic() - last_edit > self.HEARTBEAT_EDIT_EVERY or state != last_state:
                msg = await self._safe_edit_or_repost(
                    interaction,
                    msg,
                    self._heartbeat_embed(
                        task,
                        sid,
                        state,
                        elapsed,
                        self._heartbeat_note(state_data),
                        state_data.get("progress"),
                    ),
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

    @app_commands.command(
        name="develop_history",
        description="Show recent conversation history for a /develop session.",
    )
    @app_commands.describe(session_id="Optional session id (defaults to this channel's latest)")
    @app_commands.guilds(GUILD)
    async def develop_history(self, interaction: discord.Interaction, session_id: str | None = None):
        sid = session_id
        if not sid and interaction.channel_id:
            sid = self._last_session_by_channel.get(int(interaction.channel_id))
        if not sid:
            await interaction.response.send_message("No session id found for this channel.", ephemeral=True)
            return

        try:
            r = await self._http.get(
                f"{LICENSE_SERVER_URL}/internal/develop/history/{sid}",
                headers={"X-Admin-Key": ADMIN_KEY},
            )
        except httpx.HTTPError as e:
            await interaction.response.send_message(f"Network error: {e}", ephemeral=True)
            return

        if r.status_code != 200:
            await interaction.response.send_message(
                f"Server error {r.status_code}: {r.text[:300]}", ephemeral=True
            )
            return
        data = r.json()
        messages = data.get("messages") or []
        snippet = []
        for m in messages[-10:]:
            role = (m.get("role") or "?").upper()
            content = str(m.get("content") or "")[:180]
            snippet.append(f"**{role}:** {content}")
        txt = "\n".join(snippet) if snippet else "(no history)"
        emb = discord.Embed(
            title=f"/develop history — {sid}",
            description=txt[:3900],
            color=discord.Color.blurple(),
        )
        if data.get("state"):
            emb.add_field(name="State", value=str(data["state"])[:200], inline=True)
        if data.get("progress"):
            emb.add_field(
                name="Progress",
                value=str((data["progress"] or {}).get("detail") or (data["progress"] or {}).get("stage") or "")[:1000],
                inline=False,
            )
        if data.get("active_objective"):
            emb.add_field(name="Objective", value=str(data["active_objective"])[:1000], inline=False)
        await interaction.response.send_message(embed=emb, ephemeral=True)

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

        await self._safe_edit_or_repost(interaction, msg, embed, timeout_notice=True)
        if status == "planned":
            plan_text = str(data_for_embed.get("plan") or "").strip()
            chunks = self._plan_chunks(plan_text)
            attempts_text = self._attempt_summary(data_for_embed.get("attempts") or [])
            warning = str(data_for_embed.get("warning") or "").strip() or None
            proposed_files = list(data_for_embed.get("proposed_files") or [])

            if len(chunks) > 1 and interaction.channel is not None:
                pager = _PlanPagerView(
                    self,
                    task=task,
                    sid=sid,
                    chunks=chunks,
                    owner_id=interaction.user.id,
                    warning=warning,
                    proposed_files=proposed_files,
                    attempts_text=attempts_text,
                )
                pager_msg = await interaction.channel.send(embed=pager._embed(), view=pager)
                pager.message = pager_msg

            if plan_text and len(plan_text) > 2800 and interaction.channel is not None:
                buf = io.BytesIO(plan_text.encode("utf-8"))
                await interaction.channel.send(
                    file=discord.File(buf, filename=f"develop-plan-{sid}.md")
                )

    async def _safe_edit_or_repost(
        self,
        interaction: discord.Interaction,
        msg: discord.Message,
        embed: discord.Embed,
        timeout_notice: bool = False,
    ) -> discord.Message:
        try:
            await msg.edit(embed=embed)
            return msg
        except discord.HTTPException as e:
            if e.code != 50027:
                raise
            content = None
            if timeout_notice:
                content = (
                    f"⚠️ {interaction.user.mention} interaction token expired; "
                    "switching to channel updates for this /develop session."
                )
            new_msg = await interaction.channel.send(content=content, embed=embed)
            return new_msg

    def _heartbeat_embed(
        self,
        task: str,
        sid: str,
        state: str,
        elapsed: int,
        note: str | None,
        progress: dict | None = None,
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
        if progress:
            detail = str(progress.get("detail") or progress.get("stage") or "")[:1000]
            if detail:
                emb.add_field(name="Progress", value=detail, inline=False)
            if progress.get("attempt") and progress.get("attempt_limit"):
                emb.add_field(
                    name="Attempt",
                    value=f"{progress['attempt']}/{progress['attempt_limit']}",
                    inline=True,
                )
            if progress.get("model"):
                emb.add_field(name="Model", value=str(progress["model"])[:200], inline=True)
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
            "planned": discord.Color.gold(),
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
        elif status == "planned":
            plan_text = str(data.get("plan", "No plan text"))
            chunks = self._plan_chunks(plan_text)
            emb.add_field(name="Plan", value=chunks[0], inline=False)
            if len(chunks) > 1:
                emb.add_field(
                    name="More",
                    value=f"Plan continues in {len(chunks) - 1} more part(s). Use **Next part ▶**.",
                    inline=False,
                )
            if data.get("warning"):
                emb.add_field(name="Warning", value=str(data["warning"])[:1000], inline=False)
            if data.get("proposed_files"):
                emb.add_field(
                    name="Proposed files",
                    value="\n".join(f"• {f}" for f in data["proposed_files"][:15]),
                    inline=False,
                )
        elif status == "empty":
            emb.add_field(
                name="No changes made",
                value=(
                    "The agent completed without producing a diff. "
                    "Try follow-up with a narrower task or run `plan_only:true` first."
                ),
                inline=False,
            )
        attempts = self._attempt_summary(data.get("attempts") or [])
        if attempts:
            emb.add_field(name="Attempts", value=attempts, inline=False)

        emb.set_footer(text=f"session {data.get('session_id', '?')}")
        return emb

    def _plan_chunks(self, plan_text: str) -> list[str]:
        txt = (plan_text or "").strip() or "No plan text was produced."
        size = 900
        return [txt[i : i + size] for i in range(0, len(txt), size)] or [txt]

    def _planned_embed_page(
        self,
        task: str,
        sid: str,
        chunks: list[str],
        idx: int,
        *,
        warning: str | None,
        proposed_files: list[str],
        attempts_text: str | None,
    ) -> discord.Embed:
        total = max(1, len(chunks))
        safe_idx = max(0, min(idx, total - 1))
        emb = discord.Embed(
            title=f"/develop — planned (part {safe_idx + 1}/{total})",
            description=f"**Task:** {task[:400]}",
            color=discord.Color.gold(),
        )
        emb.add_field(name="Plan", value=chunks[safe_idx], inline=False)
        if safe_idx == 0 and warning:
            emb.add_field(name="Warning", value=warning[:1000], inline=False)
        if safe_idx == 0 and proposed_files:
            emb.add_field(
                name="Proposed files",
                value="\n".join(f"• {f}" for f in proposed_files[:15]),
                inline=False,
            )
        if attempts_text:
            emb.add_field(name="Attempts", value=attempts_text, inline=False)
        emb.set_footer(text=f"session {sid}")
        return emb

    def _heartbeat_note(self, state_data: dict) -> str | None:
        progress = state_data.get("progress") or {}
        log_tail = state_data.get("log_tail")
        if not log_tail and state_data.get("state") not in ("starting", "running"):
            log_tail = (state_data.get("result") or {}).get("log_tail")
        if not log_tail:
            log_tail = state_data.get("error")
        detail = str(progress.get("detail") or "").strip()
        if detail and log_tail:
            return f"{detail}\n\n{str(log_tail)[-700:]}"
        if detail:
            return detail
        return str(log_tail)[-900:] if log_tail else None

    def _attempt_summary(self, attempts: list[dict]) -> str | None:
        if not attempts:
            return None
        lines: list[str] = []
        for attempt in attempts[-4:]:
            model = attempt.get("model") or "probe"
            auth = attempt.get("auth_slot") or "default"
            if attempt.get("returncode") == 0:
                outcome = "ok"
            elif attempt.get("timeout"):
                outcome = f"timeout {attempt['timeout']}s"
            elif attempt.get("ready") is False:
                outcome = f"gateway {attempt.get('error', 'not ready')}"
            elif attempt.get("error"):
                outcome = str(attempt["error"])[:80]
            else:
                outcome = str(attempt.get("failure_kind") or f"rc {attempt.get('returncode', '?')}")[:80]
            lines.append(f"• `{model}` / `{auth}` — {outcome}")
        return "\n".join(lines)[:1000]

    async def cog_unload(self) -> None:
        await self._http.aclose()


async def setup(bot: commands.Bot) -> None:
    if bot.get_cog("DevelopCog"):
        return
    await bot.add_cog(DevelopCog(bot), guilds=[GUILD])
